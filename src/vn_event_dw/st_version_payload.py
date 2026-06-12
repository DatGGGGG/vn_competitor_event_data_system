from __future__ import annotations

import json
from typing import Any


RAW_ST_VERSION_COLUMNS = (
    "source_version_id",
    "unified_app_id",
    "os",
    "app_id",
    "country",
    "version_time",
    "version_name",
    "before_version",
    "after_version",
    "version_summary",
    "raw_payload",
    "version_payload",
    "source_file",
    "ingested_at",
)

RAW_ST_VERSION_REQUIRED_COLUMNS = set(RAW_ST_VERSION_COLUMNS)


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


def _read_payload_field(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        text = _normalize_text(value)
        if text:
            return text
    return None


def _coerce_payload(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_raw_st_version_row(
    *,
    source_version_id: str,
    unified_app_id: str,
    os_name: str,
    app_id: str,
    country: str,
    version_time: str,
    version_name: str | None,
    payload_text: str | None,
    source_file: str,
    ingested_at: str,
) -> dict[str, Any]:
    payload = _coerce_payload(payload_text)
    before_version = _read_payload_field(payload, "before", "before_version")
    after_version = _read_payload_field(payload, "after", "after_version", "version_name", "name")
    version_summary = _read_payload_field(payload, "version_summary", "summary", "notes")
    resolved_version_name = (
        _normalize_text(version_name)
        or after_version
        or version_summary
        or "version"
    )

    raw_payload = payload_text if payload_text else json.dumps(payload, ensure_ascii=False, sort_keys=True)

    return {
        "source_version_id": source_version_id,
        "unified_app_id": unified_app_id,
        "os": _normalize_text(os_name) or "",
        "app_id": _normalize_text(app_id) or "",
        "country": _normalize_text(country) or "",
        "version_time": version_time,
        "version_name": resolved_version_name,
        "before_version": before_version,
        "after_version": after_version,
        "version_summary": version_summary,
        "raw_payload": raw_payload,
        "version_payload": raw_payload,
        "source_file": source_file,
        "ingested_at": ingested_at,
    }
