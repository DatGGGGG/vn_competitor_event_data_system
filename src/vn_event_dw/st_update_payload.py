from __future__ import annotations

import json
from typing import Any


RAW_ST_APP_UPDATE_COLUMNS = [
    "source_update_id",
    "unified_app_id",
    "os",
    "app_id",
    "country",
    "update_time",
    "update_type",
    "name",
    "subtitle",
    "short_description",
    "description_text",
    "description_before_text",
    "description_after_text",
    "description_diff_html",
    "version_before",
    "version_after",
    "version_summary",
    "events_json",
    "channel_raw",
    "notes_raw",
    "advisory_raw",
    "apple_watch_enabled_raw",
    "apple_watch_icon_raw",
    "apple_watch_screenshot_raw",
    "category_raw",
    "contains_ad_raw",
    "content_rating_raw",
    "country_raw",
    "custom_product_pages_raw",
    "description_raw",
    "events_raw",
    "feature_graphic_raw",
    "featured_user_feedback_raw",
    "file_size_raw",
    "icon_raw",
    "imessage_enabled_raw",
    "imessage_icon_raw",
    "imessage_screenshot_raw",
    "install_range_raw",
    "minimum_os_version_raw",
    "name_raw",
    "price_raw",
    "promo_text_raw",
    "publisher_id_raw",
    "publisher_name_raw",
    "related_app_raw",
    "screenshot_raw",
    "sdk_id_raw",
    "short_description_raw",
    "subtitle_raw",
    "support_url_raw",
    "supported_device_raw",
    "supported_language_raw",
    "top_in_app_purchase_raw",
    "payload_unified_app_id_raw",
    "version_raw",
    "raw_payload",
    "update_payload",
    "source_file",
    "ingested_at",
]


RAW_ST_APP_UPDATE_REQUIRED_COLUMNS = set(RAW_ST_APP_UPDATE_COLUMNS) - {
    "source_update_id",
    "unified_app_id",
    "update_time",
    "update_type",
    "source_file",
    "ingested_at",
}


def serialize_payload_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def extract_preferred_text(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("after", "before", "text", "description", "title", "name", "subtitle", "diff"):
            candidate = extract_preferred_text(value.get(key))
            if candidate:
                return candidate
        return None
    if isinstance(value, list):
        for item in value:
            candidate = extract_preferred_text(item)
            if candidate:
                return candidate
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_st_events_json(value: Any) -> str | None:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if not isinstance(value, dict):
        return None

    items: list[Any] = []
    seen_keys: set[str] = set()
    for bucket_name in ("after", "before"):
        bucket_items = value.get(bucket_name)
        if not isinstance(bucket_items, list):
            continue
        for item in bucket_items:
            if isinstance(item, dict):
                dedupe_key = str(item.get("event_id") or json.dumps(item, ensure_ascii=False, sort_keys=True))
            else:
                dedupe_key = str(item)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            items.append(item)
    return json.dumps(items, ensure_ascii=False, sort_keys=True) if items else None


def _parse_payload(payload_text: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if not payload_text:
        return None, None
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return None, payload_text
    if not isinstance(payload, dict):
        return None, payload_text
    return payload, json.dumps(payload, ensure_ascii=False, sort_keys=True)


def extract_update_payload_fields(payload_text: str | None) -> dict[str, Any]:
    payload, normalized_payload = _parse_payload(payload_text)
    if payload is None:
        return {
            "name": None,
            "subtitle": None,
            "short_description": None,
            "description_text": None,
            "description_before_text": None,
            "description_after_text": None,
            "description_diff_html": None,
            "version_before": None,
            "version_after": None,
            "version_summary": None,
            "events_json": None,
            "channel_raw": None,
            "notes_raw": None,
            "advisory_raw": None,
            "apple_watch_enabled_raw": None,
            "apple_watch_icon_raw": None,
            "apple_watch_screenshot_raw": None,
            "category_raw": None,
            "contains_ad_raw": None,
            "content_rating_raw": None,
            "country_raw": None,
            "custom_product_pages_raw": None,
            "description_raw": None,
            "events_raw": None,
            "feature_graphic_raw": None,
            "featured_user_feedback_raw": None,
            "file_size_raw": None,
            "icon_raw": None,
            "imessage_enabled_raw": None,
            "imessage_icon_raw": None,
            "imessage_screenshot_raw": None,
            "install_range_raw": None,
            "minimum_os_version_raw": None,
            "name_raw": None,
            "price_raw": None,
            "promo_text_raw": None,
            "publisher_id_raw": None,
            "publisher_name_raw": None,
            "related_app_raw": None,
            "screenshot_raw": None,
            "sdk_id_raw": None,
            "short_description_raw": None,
            "subtitle_raw": None,
            "support_url_raw": None,
            "supported_device_raw": None,
            "supported_language_raw": None,
            "top_in_app_purchase_raw": None,
            "payload_unified_app_id_raw": None,
            "version_raw": None,
            "raw_payload": payload_text,
        }

    description_value = payload.get("description")
    version_value = payload.get("version")
    return {
        "name": extract_preferred_text(payload.get("name")),
        "subtitle": extract_preferred_text(payload.get("subtitle")),
        "short_description": extract_preferred_text(payload.get("short_description")),
        "description_text": extract_preferred_text(description_value),
        "description_before_text": extract_preferred_text(description_value.get("before")) if isinstance(description_value, dict) else None,
        "description_after_text": extract_preferred_text(description_value.get("after")) if isinstance(description_value, dict) else None,
        "description_diff_html": extract_preferred_text(description_value.get("diff")) if isinstance(description_value, dict) else None,
        "version_before": extract_preferred_text(version_value.get("before")) if isinstance(version_value, dict) else None,
        "version_after": extract_preferred_text(version_value.get("after")) if isinstance(version_value, dict) else None,
        "version_summary": extract_preferred_text(version_value.get("version_summary")) if isinstance(version_value, dict) else None,
        "events_json": normalize_st_events_json(payload.get("events")),
        "channel_raw": serialize_payload_value(payload.get("channel")),
        "notes_raw": serialize_payload_value(payload.get("notes")),
        "advisory_raw": serialize_payload_value(payload.get("advisory")),
        "apple_watch_enabled_raw": serialize_payload_value(payload.get("apple_watch_enabled")),
        "apple_watch_icon_raw": serialize_payload_value(payload.get("apple_watch_icon")),
        "apple_watch_screenshot_raw": serialize_payload_value(payload.get("apple_watch_screenshot")),
        "category_raw": serialize_payload_value(payload.get("category")),
        "contains_ad_raw": serialize_payload_value(payload.get("contains_ad")),
        "content_rating_raw": serialize_payload_value(payload.get("content_rating")),
        "country_raw": serialize_payload_value(payload.get("country")),
        "custom_product_pages_raw": serialize_payload_value(payload.get("custom_product_pages")),
        "description_raw": serialize_payload_value(description_value),
        "events_raw": serialize_payload_value(payload.get("events")),
        "feature_graphic_raw": serialize_payload_value(payload.get("feature_graphic")),
        "featured_user_feedback_raw": serialize_payload_value(payload.get("featured_user_feedback")),
        "file_size_raw": serialize_payload_value(payload.get("file_size")),
        "icon_raw": serialize_payload_value(payload.get("icon")),
        "imessage_enabled_raw": serialize_payload_value(payload.get("imessage_enabled")),
        "imessage_icon_raw": serialize_payload_value(payload.get("imessage_icon")),
        "imessage_screenshot_raw": serialize_payload_value(payload.get("imessage_screenshot")),
        "install_range_raw": serialize_payload_value(payload.get("install_range")),
        "minimum_os_version_raw": serialize_payload_value(payload.get("minimum_os_version")),
        "name_raw": serialize_payload_value(payload.get("name")),
        "price_raw": serialize_payload_value(payload.get("price")),
        "promo_text_raw": serialize_payload_value(payload.get("promo_text")),
        "publisher_id_raw": serialize_payload_value(payload.get("publisher_id")),
        "publisher_name_raw": serialize_payload_value(payload.get("publisher_name")),
        "related_app_raw": serialize_payload_value(payload.get("related_app")),
        "screenshot_raw": serialize_payload_value(payload.get("screenshot")),
        "sdk_id_raw": serialize_payload_value(payload.get("sdk_id")),
        "short_description_raw": serialize_payload_value(payload.get("short_description")),
        "subtitle_raw": serialize_payload_value(payload.get("subtitle")),
        "support_url_raw": serialize_payload_value(payload.get("support_url")),
        "supported_device_raw": serialize_payload_value(payload.get("supported_device")),
        "supported_language_raw": serialize_payload_value(payload.get("supported_language")),
        "top_in_app_purchase_raw": serialize_payload_value(payload.get("top_in_app_purchase")),
        "payload_unified_app_id_raw": serialize_payload_value(payload.get("unified_app_id")),
        "version_raw": serialize_payload_value(version_value),
        "raw_payload": normalized_payload,
    }


def build_raw_st_app_update_row(
    *,
    source_update_id: str,
    unified_app_id: str,
    os_name: str,
    app_id: str,
    country: str,
    update_time: str,
    update_type: str,
    payload_text: str | None,
    source_file: str,
    ingested_at: str,
) -> dict[str, Any]:
    extracted = extract_update_payload_fields(payload_text)
    return {
        "source_update_id": source_update_id,
        "unified_app_id": unified_app_id,
        "os": os_name,
        "app_id": app_id,
        "country": country,
        "update_time": update_time,
        "update_type": update_type,
        **extracted,
        "update_payload": payload_text,
        "source_file": source_file,
        "ingested_at": ingested_at,
    }
