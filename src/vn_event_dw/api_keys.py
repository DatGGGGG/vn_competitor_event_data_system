from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KEY_PREFIX = "vnedw_"
HASH_PREFIX = "sha256:"


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    name: str
    key: str
    key_prefix: str
    created_at: str
    keys_file: Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_api_key_value() -> str:
    return f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"


def api_key_hash(api_key: str) -> str:
    return f"{HASH_PREFIX}{hashlib.sha256(api_key.encode('utf-8')).hexdigest()}"


def key_display_prefix(api_key: str) -> str:
    return api_key[:14]


def load_key_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "keys": []}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("API key store must be a JSON object.")
    keys = payload.get("keys", [])
    if not isinstance(keys, list):
        raise ValueError("API key store field 'keys' must be a list.")
    return {"version": int(payload.get("version", 1)), "keys": keys}


def write_key_store(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def add_api_key(path: Path, *, name: str) -> GeneratedApiKey:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("API key name is required.")

    payload = load_key_store(path)
    for item in payload["keys"]:
        if str(item.get("name", "")).strip() == cleaned_name and not item.get("revoked_at"):
            raise ValueError(f"Active API key already exists for name: {cleaned_name}")

    key = generate_api_key_value()
    created_at = utc_now_iso()
    payload["keys"].append(
        {
            "name": cleaned_name,
            "key_hash": api_key_hash(key),
            "key_prefix": key_display_prefix(key),
            "created_at": created_at,
            "revoked_at": None,
        }
    )
    write_key_store(path, payload)
    return GeneratedApiKey(
        name=cleaned_name,
        key=key,
        key_prefix=key_display_prefix(key),
        created_at=created_at,
        keys_file=path,
    )


def list_api_keys(path: Path) -> list[dict[str, Any]]:
    payload = load_key_store(path)
    return [
        {
            "name": str(item.get("name", "")),
            "key_prefix": str(item.get("key_prefix", "")),
            "created_at": item.get("created_at"),
            "revoked_at": item.get("revoked_at"),
            "active": not bool(item.get("revoked_at")),
        }
        for item in payload["keys"]
    ]


def revoke_api_key(path: Path, *, name: str) -> dict[str, Any]:
    cleaned_name = name.strip()
    payload = load_key_store(path)
    for item in payload["keys"]:
        if str(item.get("name", "")).strip() == cleaned_name and not item.get("revoked_at"):
            item["revoked_at"] = utc_now_iso()
            write_key_store(path, payload)
            return {
                "name": cleaned_name,
                "key_prefix": str(item.get("key_prefix", "")),
                "revoked_at": item["revoked_at"],
            }
    raise ValueError(f"No active API key found for name: {cleaned_name}")


def key_matches_store(path: Path, *, api_key: str) -> bool:
    provided_hash = api_key_hash(api_key)
    payload = load_key_store(path)
    for item in payload["keys"]:
        if item.get("revoked_at"):
            continue
        if secrets.compare_digest(str(item.get("key_hash", "")), provided_hash):
            return True
    return False
