from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class AppMapping:
    unified_app_id: str
    fb_page_id: str
    app_name: str
    is_active: bool = True
    valid_from: str | None = None
    valid_to: str | None = None
    source_updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class SensorTowerTarget:
    unified_app_id: str
    os: str
    app_id: str
    country: str


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    rule_keywords: tuple[str, ...]
    app_mappings: tuple[AppMapping, ...]
    sensortower_targets: tuple[SensorTowerTarget, ...]


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_bool(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    return default


def _parse_app_mappings(items: Iterable[dict[str, Any]]) -> tuple[AppMapping, ...]:
    parsed: list[AppMapping] = []
    for item in items:
        unified_app_id = _normalize_text(item.get("unified_app_id"))
        fb_page_id = _normalize_text(item.get("fb_page_id"))
        app_name = _normalize_text(item.get("app_name"))
        if not unified_app_id or not fb_page_id or not app_name:
            continue
        parsed.append(
            AppMapping(
                unified_app_id=unified_app_id,
                fb_page_id=fb_page_id,
                app_name=app_name,
                is_active=_normalize_bool(item.get("is_active"), default=True),
                valid_from=_normalize_text(item.get("valid_from")),
                valid_to=_normalize_text(item.get("valid_to")),
                source_updated_at=_normalize_text(item.get("source_updated_at")),
            )
        )
    return tuple(parsed)


def _parse_sensortower_targets(items: Iterable[dict[str, Any]]) -> tuple[SensorTowerTarget, ...]:
    parsed: list[SensorTowerTarget] = []
    for item in items:
        unified_app_id = _normalize_text(item.get("unified_app_id"))
        os_name = _normalize_text(item.get("os"))
        app_id = _normalize_text(item.get("app_id"))
        country = _normalize_text(item.get("country"))
        if not unified_app_id or not os_name or not app_id or not country:
            continue
        parsed.append(
            SensorTowerTarget(
                unified_app_id=unified_app_id,
                os=os_name,
                app_id=app_id,
                country=country,
            )
        )
    return tuple(parsed)


def load_pipeline_config(config_path: str | Path) -> PipelineConfig:
    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Configuration file must contain a JSON object.")

    rule_keywords = tuple(
        keyword
        for keyword in (_normalize_text(item) for item in payload.get("rule_keywords", []))
        if keyword
    )
    app_mappings = _parse_app_mappings(payload.get("app_mappings", []))
    sensortower_targets = _parse_sensortower_targets(payload.get("sensortower_targets", []))

    return PipelineConfig(
        rule_keywords=rule_keywords,
        app_mappings=app_mappings,
        sensortower_targets=sensortower_targets,
    )


def iter_sensor_tower_targets(
    config: PipelineConfig,
    unified_app_ids: Iterable[str] | None = None,
) -> tuple[SensorTowerTarget, ...]:
    if unified_app_ids is None:
        return config.sensortower_targets

    allowed_ids: list[str] = []
    seen_ids: set[str] = set()
    for unified_app_id in unified_app_ids:
        normalized = _normalize_text(unified_app_id)
        if not normalized or normalized in seen_ids:
            continue
        seen_ids.add(normalized)
        allowed_ids.append(normalized)

    filtered: list[SensorTowerTarget] = [
        target for target in config.sensortower_targets if target.unified_app_id in set(allowed_ids)
    ]
    return tuple(filtered)


def default_sensortower_raw_dir() -> Path:
    return Path("data_ingest") / "sensortower" / "raw"
