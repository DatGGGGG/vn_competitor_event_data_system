from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import date, datetime
from typing import Any


def _fold_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().lower()


def _first_description_fragment(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.split(r"[.;\n]", text, maxsplit=1)[0].strip()


def _coerce_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("after", "before", "text", "description", "title", "name", "subtitle", "diff"):
            candidate = value.get(key)
            if candidate is None:
                continue
            text = _coerce_text(candidate)
            if text:
                return text
        return ""
    if isinstance(value, list):
        for item in value:
            text = _coerce_text(item)
            if text:
                return text
        return ""

    text = str(value or "").strip()
    if not text:
        return ""
    if text[0] in "[{":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        return _coerce_text(parsed) or text
    return text


def _is_generic_st_update_name(value: str | None) -> bool:
    lowered = _fold_text(value)
    return lowered in {
        "",
        "cap nhat",
        "cap nhat moi",
        "update",
        "update moi",
        "new update",
        "version update",
    }


def preferred_update_item_name(item: dict[str, Any]) -> str | None:
    name = _coerce_text(item.get("name"))
    subtitle = _coerce_text(item.get("subtitle"))
    description = _coerce_text(item.get("description"))
    if name and not _is_generic_st_update_name(name):
        return name
    if subtitle and not _is_generic_st_update_name(subtitle):
        return subtitle
    if name:
        return name
    return subtitle or _first_description_fragment(description) or None


def merge_descriptions(*values: str | None) -> str | None:
    seen: list[str] = []
    for value in values:
        cleaned = _coerce_text(value)
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    if not seen:
        return None
    return "\n\n".join(seen)


def _parse_date_like(value: Any) -> date | None:
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


def _parse_events_raw(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, dict):
        return []

    items: list[Any] = []
    for bucket in ("before", "after"):
        bucket_items = parsed.get(bucket)
        if isinstance(bucket_items, list):
            items.extend(bucket_items)
    return items


def load_st_app_update_events(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT
            source_update_id,
            unified_app_id,
            update_time,
            description_text,
            events_raw
        FROM raw_st_app_update
        ORDER BY source_update_id
        """
    ).fetchall()

    conn.execute("DELETE FROM st_app_update_events")

    inserted = 0
    seen_event_ids: set[str] = set()
    for row in rows:
        items = _parse_events_raw(row["events_raw"])
        if not items:
            continue

        observed_date = _parse_date_like(row["update_time"])
        for item in items:
            payload = item if isinstance(item, dict) else {"name": str(item)}
            raw_event_id = payload.get("event_id")
            event_id = str(raw_event_id).strip() if raw_event_id is not None else ""
            if not event_id or event_id in seen_event_ids:
                continue
            event_name = preferred_update_item_name(payload)
            if not event_name:
                continue
            seen_event_ids.add(event_id)
            estimated_start = _parse_date_like(payload.get("event_start_date")) or observed_date
            estimated_end = _parse_date_like(payload.get("event_end_date")) or estimated_start or observed_date
            conn.execute(
                """
                INSERT INTO st_app_update_events (
                    st_update_event_id, event_id, source_row_id, unified_app_id,
                    event_name, estimated_start_date, estimated_end_date,
                    event_description, source_refs
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"stupd_{event_id}",
                    event_id,
                    row["source_update_id"],
                    row["unified_app_id"],
                    event_name,
                    estimated_start.isoformat() if estimated_start else None,
                    estimated_end.isoformat() if estimated_end else None,
                    merge_descriptions(payload.get("description"), row["description_text"]),
                    json.dumps(
                        [
                            {
                                "source_table": "raw_st_app_update",
                                "source_row_id": row["source_update_id"],
                                "source_detail": event_id,
                            }
                        ],
                        ensure_ascii=False,
                    ),
                ),
            )
            inserted += 1

    conn.commit()
    return inserted
