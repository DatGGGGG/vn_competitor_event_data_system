from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import AppMapping, PipelineConfig, load_pipeline_config
from .schema import SCHEMA_SQL
from .st_update_events import load_st_app_update_events
from .st_update_payload import (
    RAW_ST_APP_UPDATE_COLUMNS,
    RAW_ST_APP_UPDATE_REQUIRED_COLUMNS,
    build_raw_st_app_update_row,
)
from .st_version_events import load_st_version_events
from .st_version_payload import (
    RAW_ST_VERSION_COLUMNS,
    RAW_ST_VERSION_REQUIRED_COLUMNS,
    build_raw_st_version_row,
)


@dataclass(frozen=True)
class RunStats:
    raw_fb_posts: int = 0
    raw_app_updates: int = 0
    raw_versions: int = 0
    st_app_update_events_loaded: int = 0
    st_version_events_loaded: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest


def content_hash(value: str) -> str:
    normalized = " ".join(value.strip().split()).lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def open_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    _reset_incompatible_st_app_update_events_table(conn)
    _reset_incompatible_st_version_events_table(conn)
    conn.execute("DROP VIEW IF EXISTS fb_raw_events")
    _migrate_fb_related_unified_app_id_columns(conn)
    conn.executescript(SCHEMA_SQL)
    _drop_legacy_derived_tables(conn)
    _migrate_raw_fb_posts_table(conn)
    _migrate_raw_st_app_update_table(conn)
    _migrate_raw_st_version_table(conn)
    _migrate_st_app_update_events_table(conn)
    _migrate_st_version_events_table(conn)
    _migrate_fb_event_match_decisions_table(conn)
    _migrate_unified_events_table(conn)
    _migrate_fb_related_unified_app_id_columns(conn)
    conn.commit()


def _raw_fb_posts_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(raw_fb_posts)").fetchall()
    return {str(row["name"]) for row in rows}


def _migrate_raw_fb_posts_table(conn: sqlite3.Connection) -> None:
    columns = _raw_fb_posts_columns(conn)
    if not columns:
        return
    if {
        "channel_id",
        "channel_name",
        "post_type",
        "post_description",
        "duration",
        "link",
        "publish_time",
        "hashtag",
        "engagement",
        "reaction",
        "comment",
        "share",
        "view",
    }.issubset(columns) and "post_content" not in columns:
        return

    conn.execute("ALTER TABLE raw_fb_posts RENAME TO raw_fb_posts_legacy;")
    conn.execute(
        """
        CREATE TABLE raw_fb_posts (
            source_post_id TEXT PRIMARY KEY,
            unified_app_id TEXT NOT NULL DEFAULT '',
            fb_page_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            post_type TEXT NOT NULL,
            post_description TEXT NOT NULL,
            duration TEXT NOT NULL,
            link TEXT NOT NULL,
            publish_time TEXT NOT NULL,
            hashtag TEXT NOT NULL,
            engagement TEXT NOT NULL,
            reaction TEXT NOT NULL,
            comment TEXT NOT NULL,
            share TEXT NOT NULL,
            view TEXT NOT NULL,
            source_file TEXT NOT NULL,
            ingested_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO raw_fb_posts (
            source_post_id, unified_app_id, fb_page_id, channel_id, channel_name, post_type,
            post_description, duration, link, publish_time, hashtag,
            engagement, reaction, comment, share, view, source_file, ingested_at
        )
        SELECT
            source_post_id,
            '' AS unified_app_id,
            fb_page_id,
            fb_page_id AS channel_id,
            '' AS channel_name,
            '' AS post_type,
            post_content AS post_description,
            '' AS duration,
            '' AS link,
            post_time AS publish_time,
            '' AS hashtag,
            '' AS engagement,
            '' AS reaction,
            '' AS comment,
            '' AS share,
            '' AS view,
            source_file,
            ingested_at
        FROM raw_fb_posts_legacy
        """
    )
    conn.execute("DROP TABLE raw_fb_posts_legacy;")


def _raw_st_app_update_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(raw_st_app_update)").fetchall()
    return {str(row["name"]) for row in rows}


def _migrate_raw_st_app_update_table(conn: sqlite3.Connection) -> None:
    columns = _raw_st_app_update_columns(conn)
    if not columns:
        return
    if RAW_ST_APP_UPDATE_REQUIRED_COLUMNS.issubset(columns):
        return

    legacy_rows = conn.execute("SELECT * FROM raw_st_app_update").fetchall()

    conn.execute("ALTER TABLE raw_st_app_update RENAME TO raw_st_app_update_legacy;")
    conn.execute(
        """
        CREATE TABLE raw_st_app_update (
            source_update_id TEXT PRIMARY KEY,
            unified_app_id TEXT NOT NULL,
            os TEXT NOT NULL DEFAULT '',
            app_id TEXT NOT NULL DEFAULT '',
            country TEXT NOT NULL DEFAULT '',
            update_time TEXT NOT NULL,
            update_type TEXT NOT NULL,
            name TEXT,
            subtitle TEXT,
            short_description TEXT,
            description_text TEXT,
            description_before_text TEXT,
            description_after_text TEXT,
            description_diff_html TEXT,
            version_before TEXT,
            version_after TEXT,
            version_summary TEXT,
            events_json TEXT,
            channel_raw TEXT,
            notes_raw TEXT,
            advisory_raw TEXT,
            apple_watch_enabled_raw TEXT,
            apple_watch_icon_raw TEXT,
            apple_watch_screenshot_raw TEXT,
            category_raw TEXT,
            contains_ad_raw TEXT,
            content_rating_raw TEXT,
            country_raw TEXT,
            custom_product_pages_raw TEXT,
            description_raw TEXT,
            events_raw TEXT,
            feature_graphic_raw TEXT,
            featured_user_feedback_raw TEXT,
            file_size_raw TEXT,
            icon_raw TEXT,
            imessage_enabled_raw TEXT,
            imessage_icon_raw TEXT,
            imessage_screenshot_raw TEXT,
            install_range_raw TEXT,
            minimum_os_version_raw TEXT,
            name_raw TEXT,
            price_raw TEXT,
            promo_text_raw TEXT,
            publisher_id_raw TEXT,
            publisher_name_raw TEXT,
            related_app_raw TEXT,
            screenshot_raw TEXT,
            sdk_id_raw TEXT,
            short_description_raw TEXT,
            subtitle_raw TEXT,
            support_url_raw TEXT,
            supported_device_raw TEXT,
            supported_language_raw TEXT,
            top_in_app_purchase_raw TEXT,
            payload_unified_app_id_raw TEXT,
            version_raw TEXT,
            raw_payload TEXT,
            update_payload TEXT,
            source_file TEXT NOT NULL,
            ingested_at TEXT NOT NULL
        )
        """
    )

    for row in legacy_rows:
        row_values = build_raw_st_app_update_row(
            source_update_id=row["source_update_id"],
            unified_app_id=row["unified_app_id"],
            os_name=row["os"] if "os" in columns else "",
            app_id=row["app_id"] if "app_id" in columns else "",
            country=row["country"] if "country" in columns else "",
            update_time=row["update_time"],
            update_type=row["update_type"],
            payload_text=row["update_payload"],
            source_file=row["source_file"],
            ingested_at=row["ingested_at"],
        )
        conn.execute(
            f"""
            INSERT INTO raw_st_app_update ({", ".join(RAW_ST_APP_UPDATE_COLUMNS)})
            VALUES ({", ".join("?" for _ in RAW_ST_APP_UPDATE_COLUMNS)})
            """,
            tuple(row_values[column] for column in RAW_ST_APP_UPDATE_COLUMNS),
        )

    conn.execute("DROP TABLE raw_st_app_update_legacy;")


def _drop_legacy_derived_tables(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS fact_fb_posts;")
    conn.execute("DROP TABLE IF EXISTS fact_deterministic_events;")
    conn.execute("DROP TABLE IF EXISTS fact_rule_detected_events;")


def _st_app_update_event_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(st_app_update_events)").fetchall()
    return {str(row["name"]) for row in rows}


def _migrate_st_app_update_events_table(conn: sqlite3.Connection) -> None:
    columns = _st_app_update_event_columns(conn)
    if not columns:
        return
    if {"st_update_event_id", "event_id", "source_row_id", "unified_app_id", "event_name", "source_refs"}.issubset(columns):
        return

    conn.execute("DROP TABLE IF EXISTS st_app_update_events;")
    conn.execute(
        """
        CREATE TABLE st_app_update_events (
            st_update_event_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            source_row_id TEXT NOT NULL,
            unified_app_id TEXT NOT NULL,
            event_name TEXT NOT NULL,
            estimated_start_date TEXT,
            estimated_end_date TEXT,
            event_description TEXT,
            source_refs TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_st_app_update_events_app_time
            ON st_app_update_events (unified_app_id, estimated_start_date)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_st_app_update_events_event_id
            ON st_app_update_events (event_id)
        """
    )


def _reset_incompatible_st_app_update_events_table(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'st_app_update_events'
        """
    ).fetchone()
    if not table_exists:
        return

    columns = _st_app_update_event_columns(conn)
    if "event_id" in columns:
        return

    conn.execute("DROP INDEX IF EXISTS idx_st_app_update_events_app_time;")
    conn.execute("DROP INDEX IF EXISTS idx_st_app_update_events_event_id;")
    conn.execute("DROP TABLE IF EXISTS st_app_update_events;")


def _raw_st_version_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(raw_st_version)").fetchall()
    return {str(row["name"]) for row in rows}


def _migrate_raw_st_version_table(conn: sqlite3.Connection) -> None:
    columns = _raw_st_version_columns(conn)
    if not columns:
        return
    if RAW_ST_VERSION_REQUIRED_COLUMNS.issubset(columns):
        return

    legacy_rows = conn.execute("SELECT * FROM raw_st_version").fetchall()

    conn.execute("ALTER TABLE raw_st_version RENAME TO raw_st_version_legacy;")
    conn.execute(
        """
        CREATE TABLE raw_st_version (
            source_version_id TEXT PRIMARY KEY,
            unified_app_id TEXT NOT NULL,
            os TEXT NOT NULL DEFAULT '',
            app_id TEXT NOT NULL DEFAULT '',
            country TEXT NOT NULL DEFAULT '',
            version_time TEXT NOT NULL,
            version_name TEXT NOT NULL,
            before_version TEXT,
            after_version TEXT,
            version_summary TEXT,
            raw_payload TEXT,
            version_payload TEXT,
            source_file TEXT NOT NULL,
            ingested_at TEXT NOT NULL
        )
        """
    )

    for row in legacy_rows:
        row_values = build_raw_st_version_row(
            source_version_id=row["source_version_id"],
            unified_app_id=row["unified_app_id"],
            os_name=row["os"] if "os" in columns else "",
            app_id=row["app_id"] if "app_id" in columns else "",
            country=row["country"] if "country" in columns else "",
            version_time=row["version_time"],
            version_name=row["version_name"] if "version_name" in columns else None,
            payload_text=row["version_payload"] if "version_payload" in columns else None,
            source_file=row["source_file"],
            ingested_at=row["ingested_at"],
        )
        conn.execute(
            f"""
            INSERT INTO raw_st_version ({", ".join(RAW_ST_VERSION_COLUMNS)})
            VALUES ({", ".join("?" for _ in RAW_ST_VERSION_COLUMNS)})
            """,
            tuple(row_values[column] for column in RAW_ST_VERSION_COLUMNS),
        )

    conn.execute("DROP TABLE raw_st_version_legacy;")


def _st_version_event_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(st_version_events)").fetchall()
    return {str(row["name"]) for row in rows}


def _migrate_st_version_events_table(conn: sqlite3.Connection) -> None:
    columns = _st_version_event_columns(conn)
    if not columns:
        return
    if {"st_version_event_id", "source_row_id", "unified_app_id", "event_name", "source_refs"}.issubset(columns):
        return

    conn.execute("DROP TABLE IF EXISTS st_version_events;")
    conn.execute(
        """
        CREATE TABLE st_version_events (
            st_version_event_id TEXT PRIMARY KEY,
            source_row_id TEXT NOT NULL,
            unified_app_id TEXT NOT NULL,
            event_name TEXT NOT NULL,
            estimated_start_date TEXT,
            estimated_end_date TEXT,
            event_description TEXT,
            source_refs TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_st_version_events_app_time
            ON st_version_events (unified_app_id, estimated_start_date)
        """
    )


def _reset_incompatible_st_version_events_table(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'st_version_events'
        """
    ).fetchone()
    if not table_exists:
        return

    columns = _st_version_event_columns(conn)
    if {"st_version_event_id", "source_row_id", "unified_app_id", "event_name", "source_refs"}.issubset(columns):
        return

    conn.execute("DROP INDEX IF EXISTS idx_st_version_events_app_time;")
    conn.execute("DROP TABLE IF EXISTS st_version_events;")


def _fb_event_match_decisions_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(fb_event_match_decisions)").fetchall()
    return {str(row["name"]) for row in rows}


def _migrate_fb_event_match_decisions_table(conn: sqlite3.Connection) -> None:
    columns = _fb_event_match_decisions_columns(conn)
    if not columns or "decision_source" in columns:
        return
    conn.execute(
        """
        ALTER TABLE fb_event_match_decisions
        ADD COLUMN decision_source TEXT NOT NULL DEFAULT 'llm_judge'
        """
    )


def _unified_events_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(unified_events)").fetchall()
    return {str(row["name"]) for row in rows}


def _migrate_unified_events_table(conn: sqlite3.Connection) -> None:
    columns = _unified_events_columns(conn)
    if not columns or "event_category" in columns:
        return
    conn.execute(
        """
        ALTER TABLE unified_events
        ADD COLUMN event_category TEXT NOT NULL DEFAULT 'Other'
        """
    )


def _post_event_detection_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(post_event_detection)").fetchall()
    return {str(row["name"]) for row in rows}


def _post_event_objects_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(post_event_objects)").fetchall()
    return {str(row["name"]) for row in rows}


def _fb_events_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(fb_events)").fetchall()
    return {str(row["name"]) for row in rows}


def _migrate_fb_related_unified_app_id_columns(conn: sqlite3.Connection) -> None:
    raw_columns = _raw_fb_posts_columns(conn)
    if raw_columns and "unified_app_id" not in raw_columns:
        conn.execute("ALTER TABLE raw_fb_posts ADD COLUMN unified_app_id TEXT NOT NULL DEFAULT ''")

    detection_columns = _post_event_detection_columns(conn)
    if detection_columns and "unified_app_id" not in detection_columns:
        conn.execute("ALTER TABLE post_event_detection ADD COLUMN unified_app_id TEXT NOT NULL DEFAULT ''")

    object_columns = _post_event_objects_columns(conn)
    if object_columns and "unified_app_id" not in object_columns:
        conn.execute("ALTER TABLE post_event_objects ADD COLUMN unified_app_id TEXT NOT NULL DEFAULT ''")

    fb_event_columns = _fb_events_columns(conn)
    if fb_event_columns and "unified_app_id" not in fb_event_columns:
        conn.execute("ALTER TABLE fb_events ADD COLUMN unified_app_id TEXT NOT NULL DEFAULT ''")

    _backfill_fb_related_unified_app_ids(conn)


def _backfill_fb_related_unified_app_ids(conn: sqlite3.Connection) -> None:
    table_names = {
        row["name"]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    }
    if "raw_fb_posts" in table_names:
        conn.execute(
            """
            UPDATE raw_fb_posts
            SET unified_app_id = (
                SELECT config_app_mapping.unified_app_id
                FROM config_app_mapping
                WHERE config_app_mapping.fb_page_id = raw_fb_posts.fb_page_id
                  AND config_app_mapping.is_active = 1
                ORDER BY COALESCE(config_app_mapping.source_updated_at, '') DESC, config_app_mapping.unified_app_id
                LIMIT 1
            )
            WHERE COALESCE(unified_app_id, '') = ''
              AND EXISTS (
                SELECT 1
                FROM config_app_mapping
                WHERE config_app_mapping.fb_page_id = raw_fb_posts.fb_page_id
                  AND config_app_mapping.is_active = 1
              )
            """
        )
    if "post_event_detection" in table_names and "raw_fb_posts" in table_names:
        conn.execute(
            """
            UPDATE post_event_detection
            SET unified_app_id = (
                SELECT raw_fb_posts.unified_app_id
                FROM raw_fb_posts
                WHERE raw_fb_posts.source_post_id = post_event_detection.post_id
                LIMIT 1
            )
            WHERE COALESCE(unified_app_id, '') = ''
            """
        )
    if "post_event_objects" in table_names and "raw_fb_posts" in table_names:
        conn.execute(
            """
            UPDATE post_event_objects
            SET unified_app_id = (
                SELECT raw_fb_posts.unified_app_id
                FROM raw_fb_posts
                WHERE raw_fb_posts.source_post_id = post_event_objects.post_id
                LIMIT 1
            )
            WHERE COALESCE(unified_app_id, '') = ''
            """
        )
    if "fb_events" in table_names and "post_event_objects" in table_names:
        conn.execute(
            """
            UPDATE fb_events
            SET unified_app_id = COALESCE((
                SELECT post_event_objects.unified_app_id
                FROM post_event_objects
                WHERE post_event_objects.event_object_id IN (
                    SELECT value
                    FROM json_each(fb_events.source_event_object_ids)
                )
                  AND COALESCE(post_event_objects.unified_app_id, '') <> ''
                ORDER BY post_event_objects.unified_app_id
                LIMIT 1
            ), unified_app_id)
            WHERE COALESCE(unified_app_id, '') = ''
            """
        )


def ensure_all_raw_fb_posts_mapped(
    conn: sqlite3.Connection,
    *,
    fb_page_id: str | None = None,
) -> None:
    _backfill_fb_related_unified_app_ids(conn)
    if fb_page_id:
        rows = conn.execute(
            """
            SELECT source_post_id, fb_page_id
            FROM raw_fb_posts
            WHERE fb_page_id = ?
              AND COALESCE(unified_app_id, '') = ''
            ORDER BY source_post_id
            LIMIT 10
            """,
            (fb_page_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT source_post_id, fb_page_id
            FROM raw_fb_posts
            WHERE COALESCE(unified_app_id, '') = ''
            ORDER BY source_post_id
            LIMIT 10
            """
        ).fetchall()
    if rows:
        examples = ", ".join(f"{row['source_post_id']}:{row['fb_page_id']}" for row in rows)
        raise RuntimeError(
            "Some raw_fb_posts rows are missing unified_app_id mappings. "
            f"Example rows: {examples}. "
            "Every Facebook post must map to a unified_app_id before FB event processing can continue."
        )


def load_config(conn: sqlite3.Connection, config_path: Path) -> PipelineConfig:
    config = load_pipeline_config(config_path)

    conn.execute("DELETE FROM config_app_mapping;")
    for row in config.app_mappings:
        conn.execute(
            """
            INSERT INTO config_app_mapping (
                unified_app_id, fb_page_id, app_name, is_active,
                valid_from, valid_to, source_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.unified_app_id,
                row.fb_page_id,
                row.app_name,
                1 if row.is_active else 0,
                row.valid_from,
                row.valid_to,
                row.source_updated_at,
            ),
        )

    conn.commit()
    return config


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _discover_csv_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(candidate for candidate in path.rglob("*.csv") if candidate.is_file())


def _relative_source_file(path: Path, root: Path | None) -> str:
    if root is None:
        return path.name
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _active_fb_page_mapping(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT fb_page_id, unified_app_id
        FROM config_app_mapping
        WHERE is_active = 1
        ORDER BY COALESCE(source_updated_at, '') DESC, unified_app_id
        """
    ).fetchall()
    mapping: dict[str, str] = {}
    for row in rows:
        mapping.setdefault(str(row["fb_page_id"]), str(row["unified_app_id"]))
    return mapping


def _normalize_column_name(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "_" for character in value).strip("_")


def _row_lookup(row: dict[str, str]) -> dict[str, str]:
    return {_normalize_column_name(key): value for key, value in row.items()}


def _row_value(row: dict[str, str], *candidate_names: str) -> str:
    lookup = _row_lookup(row)
    for candidate_name in candidate_names:
        candidate = lookup.get(_normalize_column_name(candidate_name))
        if candidate is not None:
            text = str(candidate).strip()
            if text:
                return text
    return ""


def _row_text(row: dict[str, str], *candidate_names: str) -> str:
    lookup = _row_lookup(row)
    for candidate_name in candidate_names:
        normalized_name = _normalize_column_name(candidate_name)
        if normalized_name in lookup:
            value = lookup[normalized_name]
            return "" if value is None else str(value).strip()
    return ""


def ingest_raw_fb_posts(
    conn: sqlite3.Connection,
    path: Path,
    *,
    fb_page_mapping: dict[str, str],
    source_file: str | None = None,
) -> int:
    rows = _read_csv(path)
    ingested_at = utc_now_iso()
    source_label = source_file or path.name
    loaded = 0
    for row in rows:
        source_post_id = _row_value(row, "source_post_id", "Post id", "post_id", "Post ID")
        fb_page_id = _row_value(row, "fb_page_id", "Channel id", "channel_id", "Page id", "page_id")
        channel_id = _row_text(row, "Channel id", "channel_id", "fb_page_id", "Page id", "page_id") or fb_page_id
        channel_name = _row_text(row, "Channel name", "channel_name")
        post_type = _row_text(row, "Post type", "post_type")
        post_description = _row_text(row, "Post description", "post_description", "post_content", "content", "message")
        duration = _row_text(row, "Duration (second)", "duration")
        link = _row_text(row, "Link", "link", "URL", "url")
        publish_time = _row_value(row, "post_time", "Publish time", "publish_time", "Published time")
        hashtag = _row_text(row, "Hashtag", "hashtag")
        engagement = _row_text(row, "Engagement", "engagement")
        reaction = _row_text(row, "Reaction", "reaction")
        comment = _row_text(row, "Comment", "comment")
        share = _row_text(row, "Share", "share")
        view = _row_text(row, "View", "view")
        if not source_post_id or not fb_page_id or not publish_time:
            continue
        unified_app_id = fb_page_mapping.get(fb_page_id, "")
        if not unified_app_id:
            raise RuntimeError(
                f"Missing config_app_mapping for fb_page_id={fb_page_id} while loading {source_label}. "
                "Every Facebook post must map to a unified_app_id."
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO raw_fb_posts (
                source_post_id, unified_app_id, fb_page_id, channel_id, channel_name, post_type,
                post_description, duration, link, publish_time, hashtag,
                engagement, reaction, comment, share, view,
                source_file, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_post_id,
                unified_app_id,
                fb_page_id,
                channel_id,
                channel_name,
                post_type,
                post_description,
                duration,
                link,
                publish_time,
                hashtag,
                engagement,
                reaction,
                comment,
                share,
                view,
                source_label,
                ingested_at,
            ),
        )
        loaded += 1
    conn.commit()
    return loaded


def ingest_raw_fb_posts_from_source(
    conn: sqlite3.Connection,
    source: Path,
    *,
    fb_page_mapping: dict[str, str],
    source_root: Path | None = None,
) -> int:
    source = Path(source)
    if source.is_file():
        return ingest_raw_fb_posts(
            conn,
            source,
            fb_page_mapping=fb_page_mapping,
            source_file=_relative_source_file(source, source_root),
        )

    if not source.is_dir():
        return 0

    loaded = 0
    for csv_path in _discover_csv_files(source):
        loaded += ingest_raw_fb_posts(
            conn,
            csv_path,
            fb_page_mapping=fb_page_mapping,
            source_file=_relative_source_file(csv_path, source_root),
        )
    return loaded


def ingest_raw_app_updates(conn: sqlite3.Connection, path: Path) -> int:
    rows = _read_csv(path)
    ingested_at = utc_now_iso()
    for row in rows:
        row_values = build_raw_st_app_update_row(
            source_update_id=row["source_update_id"],
            unified_app_id=row["unified_app_id"],
            os_name=row.get("os", ""),
            app_id=row.get("app_id", ""),
            country=row.get("country", ""),
            update_time=row["update_time"],
            update_type=row["update_type"],
            payload_text=row.get("update_payload"),
            source_file=path.name,
            ingested_at=ingested_at,
        )
        conn.execute(
            f"""
            INSERT OR REPLACE INTO raw_st_app_update ({", ".join(RAW_ST_APP_UPDATE_COLUMNS)})
            VALUES ({", ".join("?" for _ in RAW_ST_APP_UPDATE_COLUMNS)})
            """,
            tuple(row_values[column] for column in RAW_ST_APP_UPDATE_COLUMNS),
        )
    conn.commit()
    return len(rows)


def ingest_raw_versions(conn: sqlite3.Connection, path: Path) -> int:
    rows = _read_csv(path)
    ingested_at = utc_now_iso()
    for row in rows:
        row_values = build_raw_st_version_row(
            source_version_id=row["source_version_id"],
            unified_app_id=row["unified_app_id"],
            os_name=row.get("os", ""),
            app_id=row.get("app_id", ""),
            country=row.get("country", ""),
            version_time=row["version_time"],
            version_name=row.get("version_name"),
            payload_text=row.get("version_payload"),
            source_file=path.name,
            ingested_at=ingested_at,
        )
        conn.execute(
            f"""
            INSERT OR REPLACE INTO raw_st_version ({", ".join(RAW_ST_VERSION_COLUMNS)})
            VALUES ({", ".join("?" for _ in RAW_ST_VERSION_COLUMNS)})
            """,
            tuple(row_values[column] for column in RAW_ST_VERSION_COLUMNS),
        )
    conn.commit()
    return len(rows)


def record_run(conn: sqlite3.Connection, status: str, details: dict[str, Any], run_id: str) -> None:
    finished_at = utc_now_iso()
    conn.execute(
        """
        INSERT OR REPLACE INTO etl_runs (
            run_id, started_at, finished_at, status, details_json
        ) VALUES (
            ?, COALESCE((SELECT started_at FROM etl_runs WHERE run_id = ?), ?),
            ?, ?, ?
        )
        """,
        (
            run_id,
            run_id,
            finished_at,
            finished_at,
            status,
            json.dumps(details, ensure_ascii=True, sort_keys=True),
        ),
    )
    conn.commit()


def run_etl(db_path: Path, config_path: Path, input_dir: Path) -> RunStats:
    conn = open_connection(db_path)
    try:
        init_db(conn)
        load_config(conn, config_path)
        fb_page_mapping = _active_fb_page_mapping(conn)

        fb_posts_source = input_dir / "fb_posts"
        if fb_posts_source.is_dir():
            raw_fb_posts = ingest_raw_fb_posts_from_source(
                conn,
                fb_posts_source,
                fb_page_mapping=fb_page_mapping,
                source_root=input_dir,
            )
        else:
            raw_fb_posts = ingest_raw_fb_posts_from_source(
                conn,
                input_dir / "fb_posts.csv",
                fb_page_mapping=fb_page_mapping,
                source_root=input_dir,
            )
        raw_app_updates = ingest_raw_app_updates(conn, input_dir / "st_app_update.csv")
        raw_versions = ingest_raw_versions(conn, input_dir / "st_version.csv")

        st_app_update_events_loaded = load_st_app_update_events(conn)
        st_version_events_loaded = load_st_version_events(conn)

        run_id = stable_id(db_path.as_posix(), input_dir.as_posix(), config_path.as_posix(), utc_now_iso())
        record_run(
            conn,
            "success",
            {
                "db_path": db_path.as_posix(),
                "config_path": config_path.as_posix(),
                "input_dir": input_dir.as_posix(),
                "raw_fb_posts": raw_fb_posts,
                "raw_app_updates": raw_app_updates,
                "raw_versions": raw_versions,
                "st_app_update_events_loaded": st_app_update_events_loaded,
                "st_version_events_loaded": st_version_events_loaded,
            },
            run_id,
        )

        return RunStats(
            raw_fb_posts=raw_fb_posts,
            raw_app_updates=raw_app_updates,
            raw_versions=raw_versions,
            st_app_update_events_loaded=st_app_update_events_loaded,
            st_version_events_loaded=st_version_events_loaded,
        )
    except Exception as exc:  # pragma: no cover - surfaced to CLI
        run_id = stable_id(db_path.as_posix(), input_dir.as_posix(), config_path.as_posix(), utc_now_iso())
        record_run(
            conn,
            "failed",
            {
                "db_path": db_path.as_posix(),
                "config_path": config_path.as_posix(),
                "input_dir": input_dir.as_posix(),
                "error": str(exc),
            },
            run_id,
        )
        raise
    finally:
        conn.close()


def reload_fb_posts(db_path: Path, config_path: Path, input_dir: Path) -> RunStats:
    conn = open_connection(db_path)
    try:
        init_db(conn)
        conn.execute("DELETE FROM raw_fb_posts;")
        load_config(conn, config_path)
        fb_page_mapping = _active_fb_page_mapping(conn)

        fb_posts_source = input_dir / "fb_posts"
        if fb_posts_source.is_dir():
            raw_fb_posts = ingest_raw_fb_posts_from_source(
                conn,
                fb_posts_source,
                fb_page_mapping=fb_page_mapping,
                source_root=input_dir,
            )
        else:
            raw_fb_posts = ingest_raw_fb_posts_from_source(
                conn,
                input_dir / "fb_posts.csv",
                fb_page_mapping=fb_page_mapping,
                source_root=input_dir,
            )

        run_id = stable_id(
            db_path.as_posix(),
            input_dir.as_posix(),
            config_path.as_posix(),
            utc_now_iso(),
            "reload_fb_posts",
        )
        record_run(
            conn,
            "success",
            {
                "db_path": db_path.as_posix(),
                "config_path": config_path.as_posix(),
                "input_dir": input_dir.as_posix(),
                "mode": "reload_fb_posts",
                "raw_fb_posts": raw_fb_posts,
            },
            run_id,
        )

        return RunStats(
            raw_fb_posts=raw_fb_posts,
            raw_app_updates=0,
            raw_versions=0,
            st_app_update_events_loaded=0,
            st_version_events_loaded=0,
        )
    except Exception as exc:  # pragma: no cover - surfaced to CLI
        run_id = stable_id(
            db_path.as_posix(),
            input_dir.as_posix(),
            config_path.as_posix(),
            utc_now_iso(),
            "reload_fb_posts",
        )
        record_run(
            conn,
            "failed",
            {
                "db_path": db_path.as_posix(),
                "config_path": config_path.as_posix(),
                "input_dir": input_dir.as_posix(),
                "mode": "reload_fb_posts",
                "error": str(exc),
            },
            run_id,
        )
        raise
    finally:
        conn.close()


def summarize_db(db_path: Path) -> dict[str, int]:
    conn = open_connection(db_path)
    try:
        table_names = {
            row["name"]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            ).fetchall()
        }

        def _count(table_name: str) -> int:
            if table_name not in table_names:
                return 0
            return conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

        return {
            "config_app_mapping": _count("config_app_mapping"),
            "raw_fb_posts": _count("raw_fb_posts"),
            "raw_st_app_update": _count("raw_st_app_update"),
            "raw_st_version": _count("raw_st_version"),
            "st_app_update_events": _count("st_app_update_events"),
            "st_version_events": _count("st_version_events"),
            "post_event_detection": _count("post_event_detection"),
            "post_event_objects": _count("post_event_objects"),
            "fb_event_match_decisions": _count("fb_event_match_decisions"),
            "fb_events": _count("fb_events"),
            "unified_events": _count("unified_events"),
            "unified_event_sources": _count("unified_event_sources"),
            "unified_event_merge_runs": _count("unified_event_merge_runs"),
            "etl_runs": _count("etl_runs"),
        }
    finally:
        conn.close()
