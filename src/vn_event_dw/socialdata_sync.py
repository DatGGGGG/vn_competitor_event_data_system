from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .config import AppMapping
from .etl import init_db, load_config, open_connection, record_run, stable_id, utc_now_iso
from .socialdata import SocialDataApp, SocialDataChannel, SocialDataClient, SocialDataPost


DEFAULT_SOCIALDATA_APP_SLUG = "srcvn"
DEFAULT_SOCIALDATA_LOOKBACK_DAYS = 10
DEFAULT_SOCIALDATA_PAGE_SIZE = 100
SOCIALDATA_PROGRESS_EVERY = 25
SOCIALDATA_POST_TYPE_LABELS = {
    1: "STATUS",
    2: "PHOTO",
    3: "ALBUM",
    4: "LINK",
    5: "LIVE",
    6: "VIDEO",
    7: "REEL",
}
MOJIBAKE_HINTS = ("Ã", "Ä", "Å", "Æ", "áº", "á»", "ðŸ")


@dataclass(frozen=True, slots=True)
class SocialDataChannelSyncStats:
    channel_id: int
    fb_page_id: str
    channel_name: str
    listed_posts: int
    upserted_posts: int
    stopped_on_cutoff: bool


@dataclass(frozen=True, slots=True)
class SocialDataSyncStats:
    app_slug: str
    app_id: int
    cutoff_iso: str
    matched_channels: int
    listed_posts: int
    upserted_posts: int
    channel_stats: tuple[SocialDataChannelSyncStats, ...]


def resolve_socialdata_app_slug(app_slug: str | None) -> str:
    resolved = (app_slug or os.getenv("SOCIALDATA_APP_SLUG") or DEFAULT_SOCIALDATA_APP_SLUG).strip()
    if not resolved:
        raise RuntimeError("A Socialdata app slug is required. Pass --app-slug or set SOCIALDATA_APP_SLUG.")
    return resolved


def resolve_socialdata_cutoff(*, since: date | None, lookback_days: int | None) -> datetime:
    if since is not None:
        return datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
    days = DEFAULT_SOCIALDATA_LOOKBACK_DAYS if lookback_days is None else max(0, lookback_days)
    return datetime.now(timezone.utc) - timedelta(days=days)


def sync_socialdata_posts(
    *,
    db_path: Path,
    config_path: Path,
    client: SocialDataClient,
    app_slug: str | None,
    since: date | None = None,
    lookback_days: int | None = None,
    unified_app_ids: list[str] | None = None,
    per_page: int = DEFAULT_SOCIALDATA_PAGE_SIZE,
    progress: Callable[[str], None] | None = None,
) -> SocialDataSyncStats:
    conn = open_connection(db_path)
    try:
        init_db(conn)
        config = load_config(conn, config_path)
        resolved_slug = resolve_socialdata_app_slug(app_slug)
        app = client.app_by_slug(resolved_slug)
        cutoff = resolve_socialdata_cutoff(since=since, lookback_days=lookback_days)
        stats = sync_socialdata_posts_into_connection(
            conn,
            config_app_mappings=config.app_mappings,
            client=client,
            app=app,
            cutoff=cutoff,
            unified_app_ids=unified_app_ids,
            per_page=per_page,
            progress=progress,
        )
        run_id = stable_id(
            db_path.as_posix(),
            config_path.as_posix(),
            app.slug,
            cutoff.isoformat(),
            "sync_socialdata_posts",
            utc_now_iso(),
        )
        record_run(
            conn,
            "success",
            {
                "db_path": db_path.as_posix(),
                "config_path": config_path.as_posix(),
                "mode": "sync_socialdata_posts",
                "app_slug": stats.app_slug,
                "app_id": stats.app_id,
                "cutoff_iso": stats.cutoff_iso,
                "matched_channels": stats.matched_channels,
                "listed_posts": stats.listed_posts,
                "upserted_posts": stats.upserted_posts,
            },
            run_id,
        )
        return stats
    except Exception as exc:  # pragma: no cover - surfaced to CLI
        run_id = stable_id(
            db_path.as_posix(),
            config_path.as_posix(),
            resolve_socialdata_app_slug(app_slug),
            "sync_socialdata_posts",
            utc_now_iso(),
        )
        record_run(
            conn,
            "failed",
            {
                "db_path": db_path.as_posix(),
                "config_path": config_path.as_posix(),
                "mode": "sync_socialdata_posts",
                "app_slug": resolve_socialdata_app_slug(app_slug),
                "error": str(exc),
            },
            run_id,
        )
        raise
    finally:
        conn.close()


def sync_socialdata_posts_into_connection(
    conn: sqlite3.Connection,
    *,
    config_app_mappings: tuple[AppMapping, ...],
    client: SocialDataClient,
    app: SocialDataApp,
    cutoff: datetime,
    unified_app_ids: list[str] | None = None,
    per_page: int = DEFAULT_SOCIALDATA_PAGE_SIZE,
    progress: Callable[[str], None] | None = None,
) -> SocialDataSyncStats:
    active_mappings = _active_fb_page_mappings(config_app_mappings, unified_app_ids=unified_app_ids)
    matched_channels = _match_socialdata_channels(
        client.iter_channels(app_id=app.id, per_page=per_page),
        active_mappings,
    )
    _emit_progress(progress, f"socialdata_sync: app={app.slug} matched_channels={len(matched_channels)} cutoff={cutoff.isoformat()}")

    total_listed_posts = 0
    total_upserted_posts = 0
    channel_stats: list[SocialDataChannelSyncStats] = []
    for channel in matched_channels:
        listed_posts, upserted_posts, stopped_on_cutoff = _sync_channel_posts(
            conn,
            client=client,
            app=app,
            channel=channel,
            mapping=active_mappings[channel.sub or ""],
            cutoff=cutoff,
            per_page=per_page,
            progress=progress,
        )
        total_listed_posts += listed_posts
        total_upserted_posts += upserted_posts
        channel_stats.append(
            SocialDataChannelSyncStats(
                channel_id=channel.id,
                fb_page_id=channel.sub or "",
                channel_name=_repair_text(channel.name),
                listed_posts=listed_posts,
                upserted_posts=upserted_posts,
                stopped_on_cutoff=stopped_on_cutoff,
            )
        )
        _emit_progress(
            progress,
            (
                "socialdata_sync_channel: "
                f"fb_page_id={channel.sub or ''} "
                f"channel_id={channel.id} "
                f"listed_posts={listed_posts} "
                f"upserted_posts={upserted_posts} "
                f"stopped_on_cutoff={stopped_on_cutoff}"
            ),
        )

    conn.commit()
    return SocialDataSyncStats(
        app_slug=app.slug,
        app_id=app.id,
        cutoff_iso=cutoff.isoformat(),
        matched_channels=len(matched_channels),
        listed_posts=total_listed_posts,
        upserted_posts=total_upserted_posts,
        channel_stats=tuple(channel_stats),
    )


def _sync_channel_posts(
    conn: sqlite3.Connection,
    *,
    client: SocialDataClient,
    app: SocialDataApp,
    channel: SocialDataChannel,
    mapping: AppMapping,
    cutoff: datetime,
    per_page: int,
    progress: Callable[[str], None] | None,
) -> tuple[int, int, bool]:
    listed_posts = 0
    upserted_posts = 0
    page = 0
    stopped_on_cutoff = False
    channel_name = _repair_text(channel.name)
    _emit_progress(
        progress,
        (
            "socialdata_sync_channel_start: "
            f"channel_name={channel_name} "
            f"fb_page_id={channel.sub or ''} "
            f"channel_id={channel.id} "
            f"cutoff={cutoff.isoformat()}"
        ),
    )
    while True:
        posts, total = client.list_posts(
            app_id=app.id,
            page=page,
            per_page=per_page,
            sort_field="createdAt",
            sort_order="DESC",
            filter={"channelId": channel.id},
        )
        _emit_progress(
            progress,
            (
                "socialdata_sync_page: "
                f"channel_name={channel_name} "
                f"channel_id={channel.id} "
                f"page={page + 1} "
                f"page_size={len(posts)} "
                f"reported_total={total}"
            ),
        )
        if not posts:
            break

        batch_has_older_posts = False
        for post in posts:
            post_dt = _parse_datetime(post.created_at)
            if post_dt is None or post_dt < cutoff:
                batch_has_older_posts = True
                continue
            listed_posts += 1
            detailed_post = client.get_post(app_id=app.id, post_id=post.id, with_metrics=True)
            _upsert_socialdata_post(
                conn,
                mapping=mapping,
                channel=channel,
                post=detailed_post,
                source_file=f"socialdata/{app.slug}/channel_{channel.id}.json",
            )
            upserted_posts += 1
            if upserted_posts == 1 or upserted_posts % SOCIALDATA_PROGRESS_EVERY == 0:
                _emit_progress(
                    progress,
                    (
                        "socialdata_sync_posts: "
                        f"channel_name={channel_name} "
                        f"channel_id={channel.id} "
                        f"upserted_posts={upserted_posts} "
                        f"latest_source_post_id={_normalize_text(detailed_post.sub) or detailed_post.id} "
                        f"latest_created_at={_normalize_text(detailed_post.created_at)}"
                    ),
                )

        if batch_has_older_posts:
            stopped_on_cutoff = True
            _emit_progress(
                progress,
                (
                    "socialdata_sync_cutoff_reached: "
                    f"channel_name={channel_name} "
                    f"channel_id={channel.id} "
                    f"page={page + 1} "
                    f"cutoff={cutoff.isoformat()}"
                ),
            )
            break
        if (page + 1) * per_page >= total:
            break
        page += 1

    return listed_posts, upserted_posts, stopped_on_cutoff


def _upsert_socialdata_post(
    conn: sqlite3.Connection,
    *,
    mapping: AppMapping,
    channel: SocialDataChannel,
    post: SocialDataPost,
    source_file: str,
) -> None:
    metrics = post.metrics or {}
    source_post_id = _normalize_text(post.sub) or str(post.id)
    channel_name = _repair_text(channel.name)
    post_description = _repair_text(post.name)
    hashtag = _repair_text(post.tags)
    publish_time = _normalize_text(post.created_at)
    if not source_post_id or not publish_time:
        return

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
            mapping.unified_app_id,
            mapping.fb_page_id,
            str(channel.id),
            channel_name,
            _socialdata_post_type_label(post.type),
            post_description,
            _metric_text(metrics, "m61"),
            _normalize_text(post.url),
            publish_time,
            hashtag,
            _metric_text(metrics, "m0"),
            _metric_text(metrics, "m1"),
            _metric_text(metrics, "m2"),
            _metric_text(metrics, "m3"),
            _metric_text(metrics, "m4"),
            source_file,
            utc_now_iso(),
        ),
    )


def _active_fb_page_mappings(
    app_mappings: tuple[AppMapping, ...],
    *,
    unified_app_ids: list[str] | None,
) -> dict[str, AppMapping]:
    allowed_ids = {item.strip() for item in unified_app_ids or [] if str(item).strip()}
    selected: dict[str, AppMapping] = {}
    for mapping in app_mappings:
        if not mapping.is_active:
            continue
        if allowed_ids and mapping.unified_app_id not in allowed_ids:
            continue
        selected.setdefault(mapping.fb_page_id, mapping)
    return selected


def _match_socialdata_channels(
    channels: list[SocialDataChannel],
    active_mappings: dict[str, AppMapping],
) -> list[SocialDataChannel]:
    grouped: dict[str, list[SocialDataChannel]] = {}
    for channel in channels:
        fb_page_id = _normalize_text(channel.sub)
        if not fb_page_id or fb_page_id not in active_mappings:
            continue
        grouped.setdefault(fb_page_id, []).append(channel)

    matched: list[SocialDataChannel] = []
    for fb_page_id, candidates in grouped.items():
        matched.append(sorted(candidates, key=_channel_sort_key, reverse=True)[0])
    matched.sort(key=lambda item: (_repair_text(item.name).lower(), item.id))
    return matched


def _channel_sort_key(channel: SocialDataChannel) -> tuple[int, datetime, int]:
    created_at = _parse_datetime(channel.created_at) or datetime.min.replace(tzinfo=timezone.utc)
    status = channel.status or 0
    return (status, created_at, channel.id)


def _socialdata_post_type_label(post_type: int | None) -> str:
    if post_type is None:
        return ""
    return SOCIALDATA_POST_TYPE_LABELS.get(post_type, str(post_type))


def _metric_text(metrics: dict[str, Any], key: str) -> str:
    value = metrics.get(key)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _parse_datetime(value: str | None) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    normalized = text.replace(" UTC", "+00:00")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _repair_text(value: Any) -> str:
    text = _normalize_text(value)
    if not text or not any(hint in text for hint in MOJIBAKE_HINTS):
        return text
    candidates = [text]
    for source_encoding in ("latin1", "cp1252"):
        try:
            candidates.append(text.encode(source_encoding).decode("utf-8"))
        except UnicodeError:
            continue
    return min(candidates, key=_repair_sort_key)


def _mojibake_score(value: str) -> int:
    hint_count = sum(value.count(hint) for hint in MOJIBAKE_HINTS)
    replacement_count = len(re.findall(r"[ÃÄÅÆ][^\s]", value))
    return hint_count + replacement_count


def _repair_sort_key(value: str) -> tuple[int, int, int]:
    return (_mojibake_score(value), -_vietnamese_char_score(value), len(value))


def _vietnamese_char_score(value: str) -> int:
    return sum(1 for character in value if ord(character) > 127)


def _emit_progress(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)
