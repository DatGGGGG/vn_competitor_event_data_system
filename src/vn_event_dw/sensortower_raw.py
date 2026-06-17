from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import error, parse, request

from .config import (
    PipelineConfig,
    SensorTowerTarget,
    default_sensortower_raw_dir,
    iter_sensor_tower_targets,
    load_pipeline_config,
)
from .st_update_events import load_st_app_update_events
from .st_update_payload import RAW_ST_APP_UPDATE_COLUMNS, build_raw_st_app_update_row
from .st_version_events import load_st_version_events
from .st_version_payload import RAW_ST_VERSION_COLUMNS, build_raw_st_version_row

DEFAULT_SENSOR_TOWER_SINCE = date(2025, 1, 1)


@dataclass(frozen=True, slots=True)
class RawExtractionWindow:
    since: date
    until: date | None
    lookback_days: int | None

    def as_meta(self) -> dict[str, Any]:
        return {
            "since": self.since.isoformat(),
            "until": self.until.isoformat() if self.until else None,
            "lookback_days": self.lookback_days,
        }


@dataclass(frozen=True, slots=True)
class RawSnapshotSummary:
    run_id: str
    run_dir: str
    manifest_path: str
    snapshot_count: int


@dataclass(frozen=True, slots=True)
class RawLoadSummary:
    manifest_path: str
    loaded_snapshots: int
    skipped_snapshots: int
    update_rows: int
    version_rows: int
    st_update_event_rows: int
    st_version_event_rows: int


@dataclass(frozen=True, slots=True)
class SensorTowerClient:
    base_url: str
    auth_token: str
    timeout_seconds: int = 60
    max_retries: int = 5

    def _request(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        query = dict(params)
        query["auth_token"] = self.auth_token
        url = f"{self.base_url.rstrip('/')}{path}?{parse.urlencode(query)}"
        headers = {"Accept": "application/json"}

        for attempt in range(1, self.max_retries + 1):
            req = request.Request(url, headers=headers, method="GET")
            try:
                with request.urlopen(req, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise RuntimeError(f"Unexpected SensorTower response type: {type(payload)}")
                    return payload
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.max_retries:
                    raise RuntimeError(
                        f"SensorTower request failed for {path}: status={exc.code}, body={body[:1000]}"
                    ) from exc
            except error.URLError as exc:
                if attempt == self.max_retries:
                    raise RuntimeError(f"SensorTower request failed for {path}: {exc}") from exc

            time.sleep(min(2**attempt, 20))

        raise RuntimeError(f"SensorTower request failed for {path} after retries.")

    def get_app_update_history(self, *, os_name: str, app_id: str, country: str, date_limit: int) -> dict[str, Any]:
        return self._request(
            f"/v1/{os_name}/app_update/get_app_update_history",
            params={"app_id": app_id, "country": country, "date_limit": date_limit},
        )

    def get_app_version_history(self, *, os_name: str, app_id: str, country: str) -> dict[str, Any]:
        return self._request(
            f"/v1/{os_name}/apps/version_history",
            params={"app_id": app_id, "country": country},
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest


def _safe_component(value: str) -> str:
    return value.replace("/", "_").replace("\\", "_").replace(":", "_").strip() or "unknown"


def _short_component(value: str, *, limit: int = 48) -> str:
    cleaned = _safe_component(value)
    if len(cleaned) <= limit:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:12]
    head = cleaned[: max(8, limit - 15)]
    return f"{head}__{digest}"


def _target_key(target: SensorTowerTarget) -> str:
    return "__".join(
        (
            _short_component(target.unified_app_id, limit=24),
            _short_component(target.os, limit=16),
            _short_component(target.app_id, limit=48),
            _short_component(target.country, limit=16),
        )
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}, got {type(payload).__name__}")
    return payload


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stringify_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _normalize_text(value)


def _parse_record_date(value: str) -> date | None:
    raw = _normalize_text(value)
    if raw is None:
        return None
    normalized = raw.replace(" UTC", "+00:00")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            return None


def calculate_date_limit(start_date: date, today: date | None = None) -> int:
    current = today or date.today()
    delta = current - start_date
    return max(delta.days, 1)


def resolve_raw_window(
    *,
    since: date | None,
    until: date | None,
    lookback_days: int | None,
    default_since: date = DEFAULT_SENSOR_TOWER_SINCE,
    today: date | None = None,
) -> RawExtractionWindow:
    if since is not None and lookback_days is not None:
        raise ValueError("since and lookback_days are mutually exclusive.")

    current_day = today or date.today()
    resolved_since = since

    if resolved_since is None and lookback_days is not None:
        if lookback_days < 1:
            raise ValueError("lookback_days must be at least 1.")
        resolved_since = date.fromordinal(current_day.toordinal() - lookback_days)

    if resolved_since is None:
        resolved_since = default_since

    if until is not None and until < resolved_since:
        raise ValueError("until must be on or after since.")

    return RawExtractionWindow(
        since=resolved_since,
        until=until,
        lookback_days=lookback_days,
    )


def resolve_tracked_targets(
    config: PipelineConfig,
    unified_app_ids: Iterable[str] | None = None,
) -> tuple[SensorTowerTarget, ...]:
    return iter_sensor_tower_targets(config, unified_app_ids)


def _snapshot_payload(
    *,
    target: SensorTowerTarget,
    endpoint: str,
    window: RawExtractionWindow,
    fetched_at: str,
    payload: dict[str, Any],
    date_limit: int | None = None,
) -> dict[str, Any]:
    return {
        "meta": {
            "unified_app_id": target.unified_app_id,
            "os": target.os,
            "app_id": target.app_id,
            "country": target.country,
            "endpoint": endpoint,
            "fetched_at": fetched_at,
            "source_window": window.as_meta(),
            "date_limit": date_limit,
        },
        "payload": payload,
    }


def extract_sensortower_raw(
    *,
    client: SensorTowerClient,
    targets: Iterable[SensorTowerTarget],
    window: RawExtractionWindow,
    output_dir: Path | None = None,
) -> RawSnapshotSummary:
    tracked_targets = tuple(targets)
    if not tracked_targets:
        raise ValueError("No SensorTower targets were provided.")

    output_root = Path(output_dir) if output_dir is not None else default_sensortower_raw_dir()
    run_id = f"{_utc_now().strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    run_dir = output_root / "runs" / run_id
    fetched_at = _utc_now_iso()
    snapshots: list[dict[str, Any]] = []

    for target in tracked_targets:
        target_dir = run_dir / _target_key(target)
        date_limit = calculate_date_limit(window.since)

        update_payload = client.get_app_update_history(
            os_name=target.os,
            app_id=target.app_id,
            country=target.country,
            date_limit=date_limit,
        )
        update_snapshot_path = target_dir / "app_update_history.json"
        _atomic_write_json(
            update_snapshot_path,
            _snapshot_payload(
                target=target,
                endpoint="app_update_history",
                window=window,
                fetched_at=fetched_at,
                payload=update_payload,
                date_limit=date_limit,
            ),
        )
        snapshots.append(
            {
                "unified_app_id": target.unified_app_id,
                "os": target.os,
                "app_id": target.app_id,
                "country": target.country,
                "endpoint": "app_update_history",
                "path": str(update_snapshot_path.relative_to(run_dir)),
                "fetched_at": fetched_at,
                "source_window": window.as_meta(),
                "date_limit": date_limit,
            }
        )

        version_payload = client.get_app_version_history(
            os_name=target.os,
            app_id=target.app_id,
            country=target.country,
        )
        version_snapshot_path = target_dir / "version_history.json"
        _atomic_write_json(
            version_snapshot_path,
            _snapshot_payload(
                target=target,
                endpoint="version_history",
                window=window,
                fetched_at=fetched_at,
                payload=version_payload,
            ),
        )
        snapshots.append(
            {
                "unified_app_id": target.unified_app_id,
                "os": target.os,
                "app_id": target.app_id,
                "country": target.country,
                "endpoint": "version_history",
                "path": str(version_snapshot_path.relative_to(run_dir)),
                "fetched_at": fetched_at,
                "source_window": window.as_meta(),
                "date_limit": None,
            }
        )

    manifest = {
        "run_id": run_id,
        "created_at": fetched_at,
        "output_dir": str(output_root),
        "run_dir": str(run_dir),
        "targets": [asdict(target) for target in tracked_targets],
        "window": window.as_meta(),
        "snapshots": snapshots,
        "loaded_at": None,
        "loaded_summary": None,
    }
    manifest_path = run_dir / "manifest.json"
    _atomic_write_json(manifest_path, manifest)
    return RawSnapshotSummary(
        run_id=run_id,
        run_dir=str(run_dir),
        manifest_path=str(manifest_path),
        snapshot_count=len(snapshots),
    )


def discover_raw_manifests(raw_root: Path) -> list[Path]:
    if not raw_root.exists():
        return []
    return sorted(raw_root.glob("runs/*/manifest.json"))


def _row_in_window(row_date: date | None, since: date, until: date | None) -> bool:
    if row_date is None:
        return False
    if row_date < since:
        return False
    if until is not None and row_date > until:
        return False
    return True


def _store_update_rows(
    conn: sqlite3.Connection,
    *,
    target: SensorTowerTarget,
    snapshot_source: str,
    payload: dict[str, Any],
    since: date,
    until: date | None,
    ingested_at: str,
) -> int:
    rows = payload.get("update_data", [])
    if not isinstance(rows, list):
        return 0

    inserted = 0
    for index, item in enumerate(rows):
        if not isinstance(item, list) or len(item) != 2:
            continue
        record_date, record_payload = item
        if not isinstance(record_payload, dict):
            continue
        row_date = _parse_record_date(str(record_date))
        if not _row_in_window(row_date, since, until):
            continue

        update_type = _stringify_value(
            record_payload.get("update_type")
            or record_payload.get("type")
            or record_payload.get("version")
            or record_payload.get("name")
            or "app_update"
        )
        payload_json = json.dumps(record_payload, ensure_ascii=False, sort_keys=True)
        source_update_id = _stable_id(
            "st_update",
            target.unified_app_id,
            target.os,
            target.app_id,
            target.country,
            str(record_date),
            str(update_type or ""),
            str(index),
            payload_json,
        )
        row_values = build_raw_st_app_update_row(
            source_update_id=source_update_id,
            unified_app_id=target.unified_app_id,
            os_name=target.os,
            app_id=target.app_id,
            country=target.country,
            update_time=str(record_date),
            update_type=update_type or "app_update",
            payload_text=payload_json,
            source_file=snapshot_source,
            ingested_at=ingested_at,
        )
        conn.execute(
            f"""
            INSERT OR REPLACE INTO raw_st_app_update ({", ".join(RAW_ST_APP_UPDATE_COLUMNS)})
            VALUES ({", ".join("?" for _ in RAW_ST_APP_UPDATE_COLUMNS)})
            """,
            tuple(row_values[column] for column in RAW_ST_APP_UPDATE_COLUMNS),
        )
        inserted += 1

    return inserted


def _store_version_rows(
    conn: sqlite3.Connection,
    *,
    target: SensorTowerTarget,
    snapshot_source: str,
    payload: dict[str, Any],
    since: date,
    until: date | None,
    ingested_at: str,
) -> int:
    rows = payload.get("update_data", {})
    if not isinstance(rows, dict):
        return 0

    inserted = 0
    for index, (record_date, record_payload) in enumerate(rows.items()):
        if not isinstance(record_payload, dict):
            continue
        row_date = _parse_record_date(str(record_date))
        if not _row_in_window(row_date, since, until):
            continue

        version_name = _stringify_value(
            record_payload.get("after")
            or record_payload.get("version_name")
            or record_payload.get("name")
            or record_payload.get("version_summary")
            or "version"
        )
        payload_json = json.dumps(record_payload, ensure_ascii=False, sort_keys=True)
        source_version_id = _stable_id(
            "st_version",
            target.unified_app_id,
            target.os,
            target.app_id,
            target.country,
            str(record_date),
            str(version_name or ""),
            str(index),
            payload_json,
        )
        row_values = build_raw_st_version_row(
            source_version_id=source_version_id,
            unified_app_id=target.unified_app_id,
            os_name=target.os,
            app_id=target.app_id,
            country=target.country,
            version_time=str(record_date),
            version_name=version_name,
            payload_text=payload_json,
            source_file=snapshot_source,
            ingested_at=ingested_at,
        )
        conn.execute(
            f"""
            INSERT OR REPLACE INTO raw_st_version ({", ".join(RAW_ST_VERSION_COLUMNS)})
            VALUES ({", ".join("?" for _ in RAW_ST_VERSION_COLUMNS)})
            """,
            tuple(row_values[column] for column in RAW_ST_VERSION_COLUMNS),
        )
        inserted += 1

    return inserted


def _load_snapshot_into_db(
    conn: sqlite3.Connection,
    *,
    snapshot_path: Path,
    snapshot_meta: dict[str, Any],
) -> dict[str, int]:
    snapshot = _read_json(snapshot_path)
    meta = snapshot.get("meta")
    payload = snapshot.get("payload")
    if not isinstance(meta, dict) or not isinstance(payload, dict):
        raise ValueError(f"Invalid SensorTower snapshot at {snapshot_path}")

    target = SensorTowerTarget(
        unified_app_id=str(meta["unified_app_id"]),
        os=str(meta["os"]),
        app_id=str(meta["app_id"]),
        country=str(meta["country"]),
    )
    source_window = snapshot_meta.get("source_window") or meta.get("source_window") or {}
    since_value = source_window.get("since")
    if not since_value:
        raise ValueError(f"Snapshot missing source_window.since: {snapshot_path}")
    since = date.fromisoformat(str(since_value))
    until_value = source_window.get("until")
    until = date.fromisoformat(str(until_value)) if until_value else None
    ingested_at = _utc_now_iso()
    source_file = str(snapshot_meta.get("path") or snapshot_path.name)

    endpoint = str(meta.get("endpoint") or "")
    if endpoint == "app_update_history":
        return {
            "update_rows": _store_update_rows(
                conn,
                target=target,
                snapshot_source=source_file,
                payload=payload,
                since=since,
                until=until,
                ingested_at=ingested_at,
            ),
            "version_rows": 0,
        }

    if endpoint == "version_history":
        return {
            "update_rows": 0,
            "version_rows": _store_version_rows(
                conn,
                target=target,
                snapshot_source=source_file,
                payload=payload,
                since=since,
                until=until,
                ingested_at=ingested_at,
            ),
        }

    raise ValueError(f"Unsupported SensorTower endpoint in {snapshot_path}: {endpoint}")


def load_sensortower_raw_manifest(
    conn: sqlite3.Connection,
    *,
    manifest_path: Path,
    force: bool = False,
) -> RawLoadSummary:
    manifest = _read_json(manifest_path)
    loaded_at = manifest.get("loaded_at")
    if loaded_at and not force:
        summary = manifest.get("loaded_summary") or {}
        return RawLoadSummary(
            manifest_path=str(manifest_path),
            loaded_snapshots=int(summary.get("loaded_snapshots", 0)),
            skipped_snapshots=int(summary.get("skipped_snapshots", 0)),
            update_rows=int(summary.get("update_rows", 0)),
            version_rows=int(summary.get("version_rows", 0)),
            st_update_event_rows=int(summary.get("st_update_event_rows", 0)),
            st_version_event_rows=int(summary.get("st_version_event_rows", 0)),
        )

    snapshots = manifest.get("snapshots", [])
    if not isinstance(snapshots, list):
        raise ValueError(f"Invalid manifest snapshots list: {manifest_path}")

    loaded_snapshots = 0
    skipped_snapshots = 0
    update_rows = 0
    version_rows = 0
    st_update_event_rows = 0
    st_version_event_rows = 0
    skipped_paths: list[str] = []
    try:
        for snapshot_meta in snapshots:
            if not isinstance(snapshot_meta, dict):
                skipped_snapshots += 1
                continue
            relative_path = snapshot_meta.get("path")
            if not relative_path:
                skipped_snapshots += 1
                continue
            snapshot_path = manifest_path.parent / str(relative_path)
            if not snapshot_path.exists():
                skipped_snapshots += 1
                skipped_paths.append(str(relative_path))
                continue
            counts = _load_snapshot_into_db(
                conn,
                snapshot_path=snapshot_path,
                snapshot_meta=snapshot_meta,
            )
            loaded_snapshots += 1
            update_rows += counts["update_rows"]
            version_rows += counts["version_rows"]
        st_update_event_rows = load_st_app_update_events(conn)
        st_version_event_rows = load_st_version_events(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    manifest["loaded_at"] = _utc_now_iso()
    manifest["loaded_summary"] = {
        "loaded_snapshots": loaded_snapshots,
        "skipped_snapshots": skipped_snapshots,
        "skipped_paths": skipped_paths,
        "update_rows": update_rows,
        "version_rows": version_rows,
        "st_update_event_rows": st_update_event_rows,
        "st_version_event_rows": st_version_event_rows,
    }
    _atomic_write_json(manifest_path, manifest)

    return RawLoadSummary(
        manifest_path=str(manifest_path),
        loaded_snapshots=loaded_snapshots,
        skipped_snapshots=skipped_snapshots,
        update_rows=update_rows,
        version_rows=version_rows,
        st_update_event_rows=st_update_event_rows,
        st_version_event_rows=st_version_event_rows,
    )


def load_pending_sensortower_raw_manifests(
    conn: sqlite3.Connection,
    *,
    raw_root: Path | None = None,
    force: bool = False,
) -> list[RawLoadSummary]:
    raw_root = Path(raw_root) if raw_root is not None else default_sensortower_raw_dir()
    manifests = discover_raw_manifests(raw_root)
    summaries: list[RawLoadSummary] = []
    for manifest_path in manifests:
        summaries.append(load_sensortower_raw_manifest(conn, manifest_path=manifest_path, force=force))
    return summaries


def load_raw_config(config_path: str | Path) -> PipelineConfig:
    return load_pipeline_config(config_path)
