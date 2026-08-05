from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_SENSOR_TOWER_OS = {"android", "ios"}


@dataclass(frozen=True, slots=True)
class AdminGameInput:
    app_name: str
    fb_page_id: str
    android_app_id: str
    ios_app_id: str
    country: str = "VN"
    unified_app_id: str | None = None


@dataclass(frozen=True, slots=True)
class AdminGamePreview:
    unified_app_id: str
    app_name: str
    fb_page_id: str
    android_app_id: str
    ios_app_id: str
    country: str
    app_mapping: dict[str, Any]
    sensortower_targets: tuple[dict[str, Any], dict[str, Any]]


class AdminConfigError(ValueError):
    pass


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _stable_unified_app_id(app_name: str, fb_page_id: str, existing_ids: set[str]) -> str:
    base = hashlib.sha1(f"{app_name}|{fb_page_id}".encode("utf-8")).hexdigest()
    for width in range(24, 41):
        candidate = base[:width]
        if candidate not in existing_ids:
            return candidate
    raise AdminConfigError("Could not generate a unique unified_app_id.")


def load_config_payload(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AdminConfigError("Config file must contain a JSON object.")
    payload.setdefault("rule_keywords", [])
    payload.setdefault("app_mappings", [])
    payload.setdefault("sensortower_targets", [])
    if not isinstance(payload["app_mappings"], list):
        raise AdminConfigError("Config field app_mappings must be a list.")
    if not isinstance(payload["sensortower_targets"], list):
        raise AdminConfigError("Config field sensortower_targets must be a list.")
    return payload


def build_game_preview(payload: dict[str, Any], game_input: AdminGameInput) -> AdminGamePreview:
    app_name = _clean_text(game_input.app_name)
    fb_page_id = _clean_text(game_input.fb_page_id)
    android_app_id = _clean_text(game_input.android_app_id)
    ios_app_id = _clean_text(game_input.ios_app_id)
    country = (_clean_text(game_input.country) or "VN").upper()
    requested_unified_app_id = _clean_text(game_input.unified_app_id)

    if not app_name:
        raise AdminConfigError("Game name is required.")
    if not fb_page_id:
        raise AdminConfigError("Facebook page ID is required.")
    if not android_app_id:
        raise AdminConfigError("Android SensorTower app/package ID is required.")
    if not ios_app_id:
        raise AdminConfigError("iOS SensorTower app ID is required.")
    if not country:
        raise AdminConfigError("Country is required.")

    app_mappings = payload.get("app_mappings", [])
    sensortower_targets = payload.get("sensortower_targets", [])

    existing_app_ids = {
        _clean_text(item.get("unified_app_id"))
        for item in app_mappings
        if isinstance(item, dict) and _clean_text(item.get("unified_app_id"))
    }
    existing_fb_page_ids = {
        _clean_text(item.get("fb_page_id"))
        for item in app_mappings
        if isinstance(item, dict) and _clean_text(item.get("fb_page_id"))
    }
    existing_st_targets = {
        (
            _clean_text(item.get("os")).lower(),
            _clean_text(item.get("app_id")),
            _clean_text(item.get("country")).upper(),
        )
        for item in sensortower_targets
        if isinstance(item, dict)
    }

    if requested_unified_app_id:
        unified_app_id = requested_unified_app_id
        if unified_app_id in existing_app_ids:
            raise AdminConfigError(f"unified_app_id already exists: {unified_app_id}")
    else:
        unified_app_id = _stable_unified_app_id(app_name, fb_page_id, existing_app_ids)

    if fb_page_id in existing_fb_page_ids:
        raise AdminConfigError(f"Facebook page ID already exists: {fb_page_id}")

    requested_targets = {
        ("android", android_app_id, country),
        ("ios", ios_app_id, country),
    }
    duplicate_targets = requested_targets & existing_st_targets
    if duplicate_targets:
        formatted = ", ".join(f"{os_name}:{app_id}:{target_country}" for os_name, app_id, target_country in sorted(duplicate_targets))
        raise AdminConfigError(f"SensorTower target already exists: {formatted}")

    app_mapping = {
        "unified_app_id": unified_app_id,
        "fb_page_id": fb_page_id,
        "app_name": app_name,
        "is_active": True,
    }
    targets = (
        {
            "unified_app_id": unified_app_id,
            "os": "android",
            "app_id": android_app_id,
            "country": country,
        },
        {
            "unified_app_id": unified_app_id,
            "os": "ios",
            "app_id": ios_app_id,
            "country": country,
        },
    )

    return AdminGamePreview(
        unified_app_id=unified_app_id,
        app_name=app_name,
        fb_page_id=fb_page_id,
        android_app_id=android_app_id,
        ios_app_id=ios_app_id,
        country=country,
        app_mapping=app_mapping,
        sensortower_targets=targets,
    )


def add_game_to_payload(payload: dict[str, Any], preview: AdminGamePreview) -> dict[str, Any]:
    updated = json.loads(json.dumps(payload, ensure_ascii=False))
    updated.setdefault("app_mappings", []).insert(0, preview.app_mapping)
    updated.setdefault("sensortower_targets", [])[0:0] = list(preview.sensortower_targets)
    return updated


def write_config_payload_atomic(config_path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        try:
            Path(tmp_name).replace(path)
        except OSError as exc:
            if exc.errno != 16:
                raise
            # Docker single-file bind mounts can reject atomic rename with EBUSY.
            # Keep a validated temp file, then copy its contents into the mounted file.
            with open(tmp_name, "r", encoding="utf-8") as source:
                json.load(source)
                source.seek(0)
                with path.open("w", encoding="utf-8") as target:
                    shutil.copyfileobj(source, target)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    Path(tmp_name).unlink(missing_ok=True)


def validate_payload_has_required_targets(payload: dict[str, Any], unified_app_id: str) -> None:
    targets = [
        item
        for item in payload.get("sensortower_targets", [])
        if isinstance(item, dict) and _clean_text(item.get("unified_app_id")) == unified_app_id
    ]
    os_names = {_clean_text(item.get("os")).lower() for item in targets}
    missing = SUPPORTED_SENSOR_TOWER_OS - os_names
    if missing:
        raise AdminConfigError(f"Missing SensorTower targets for: {', '.join(sorted(missing))}")
