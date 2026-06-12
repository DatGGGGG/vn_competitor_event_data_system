from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime


def stable_id(prefix: str, *parts: object) -> str:
    payload = json.dumps([prefix, *parts], ensure_ascii=True, default=str, sort_keys=False)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _parse_date_like(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace(" UTC", "+00:00")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            return None


def _build_event_name(os_name: str | None, after_version: str | None, version_name: str | None) -> str:
    os_label = str(os_name or "").strip().lower()
    version_label = str(after_version or version_name or "").strip()
    prefix = f"{os_label} " if os_label else ""
    if version_label:
        return f"{prefix}Version Update {version_label}"
    return f"{prefix}Version Update".strip()


def _should_skip_row(row: sqlite3.Row) -> bool:
    os_name = str(row["os"] or "").strip()
    app_id = str(row["app_id"] or "").strip()
    country = str(row["country"] or "").strip()
    source_file = str(row["source_file"] or "").strip()
    return source_file == "st_version.csv" and not os_name and not app_id and not country


def load_st_version_events(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT
            source_version_id,
            unified_app_id,
            os,
            app_id,
            country,
            version_time,
            version_name,
            after_version,
            version_summary,
            source_file
        FROM raw_st_version
        ORDER BY source_version_id
        """
    ).fetchall()

    conn.execute("DELETE FROM st_version_events")

    inserted = 0
    for row in rows:
        if _should_skip_row(row):
            continue
        observed_date = _parse_date_like(row["version_time"])
        event_name = _build_event_name(row["os"], row["after_version"], row["version_name"])
        conn.execute(
            """
            INSERT INTO st_version_events (
                st_version_event_id, source_row_id, unified_app_id,
                event_name, estimated_start_date, estimated_end_date,
                event_description, source_refs
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("stver", row["source_version_id"], row["after_version"] or row["version_name"] or row["version_time"]),
                row["source_version_id"],
                row["unified_app_id"],
                event_name,
                observed_date.isoformat() if observed_date else None,
                observed_date.isoformat() if observed_date else None,
                row["version_summary"],
                json.dumps(
                    [{"source_table": "raw_st_version", "source_row_id": row["source_version_id"], "source_detail": None}],
                    ensure_ascii=False,
                ),
            ),
        )
        inserted += 1

    conn.commit()
    return inserted
