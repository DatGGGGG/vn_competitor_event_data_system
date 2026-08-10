from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import asdict, dataclass
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


@dataclass(frozen=True, slots=True)
class SocialDataDiagnosticFailure:
    code: str
    message: str


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
    for channel in matched_channels:
        _emit_progress(
            progress,
            (
                "socialdata_sync_matched_channel: "
                f"unified_app_id={active_mappings[channel.sub or ''].unified_app_id} "
                f"app_name={active_mappings[channel.sub or ''].app_name} "
                f"fb_page_id={channel.sub or ''} "
                f"channel_id={channel.id} "
                f"channel_name={_repair_text(channel.name)} "
                f"status={channel.status} "
                f"created_at={_normalize_text(channel.created_at)}"
            ),
        )

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

        latest_post = _latest_socialdata_post(posts)
        if latest_post is not None:
            _emit_progress(
                progress,
                (
                    "socialdata_sync_source_latest: "
                    f"channel_name={channel_name} "
                    f"channel_id={channel.id} "
                    f"source_post_id={_source_post_id(latest_post)} "
                    f"created_at={_normalize_text(latest_post.created_at)}"
                ),
            )

        batch_recent_posts = 0
        batch_older_posts = 0
        for post in posts:
            post_dt = _parse_datetime(post.created_at)
            if post_dt is None:
                continue
            if post_dt < cutoff:
                batch_older_posts += 1
                continue
            batch_recent_posts += 1
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

        if batch_recent_posts == 0 and batch_older_posts > 0:
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


def diagnose_socialdata_game(
    *,
    db_path: Path,
    config_path: Path,
    client: SocialDataClient,
    app_slug: str | None,
    unified_app_id: str,
    since: date | None = None,
    lookback_days: int | None = None,
    limit: int = 20,
    per_page: int = DEFAULT_SOCIALDATA_PAGE_SIZE,
) -> dict[str, Any]:
    conn = open_connection(db_path)
    try:
        init_db(conn)
        config = load_config(conn, config_path)
        resolved_slug = resolve_socialdata_app_slug(app_slug)
        app = client.app_by_slug(resolved_slug)
        cutoff = resolve_socialdata_cutoff(since=since, lookback_days=lookback_days)
        return diagnose_socialdata_game_into_connection(
            conn,
            config_app_mappings=config.app_mappings,
            client=client,
            app=app,
            unified_app_id=unified_app_id,
            cutoff=cutoff,
            limit=limit,
            per_page=per_page,
        )
    finally:
        conn.close()


def diagnose_socialdata_game_into_connection(
    conn: sqlite3.Connection,
    *,
    config_app_mappings: tuple[AppMapping, ...],
    client: SocialDataClient,
    app: SocialDataApp,
    unified_app_id: str,
    cutoff: datetime | None = None,
    limit: int = 20,
    per_page: int = DEFAULT_SOCIALDATA_PAGE_SIZE,
) -> dict[str, Any]:
    bounded_limit = max(1, min(limit, 100))
    active_mappings = _active_fb_page_mappings(config_app_mappings, unified_app_ids=[unified_app_id])
    if not active_mappings:
        raise RuntimeError(f"No active Facebook page mapping found for unified_app_id={unified_app_id}.")

    channels = list(client.iter_channels(app_id=app.id, per_page=per_page))
    matched_channels = _match_socialdata_channels(channels, active_mappings)
    matched_by_fb_page_id = {channel.sub or "": channel for channel in matched_channels}

    channel_diagnostics: list[dict[str, Any]] = []
    socialdata_posts: list[dict[str, Any]] = []
    for fb_page_id, mapping in sorted(active_mappings.items(), key=lambda item: item[1].app_name.lower()):
        channel = matched_by_fb_page_id.get(fb_page_id)
        if channel is None:
            channel_diagnostics.append(
                {
                    "fb_page_id": fb_page_id,
                    "app_name": mapping.app_name,
                    "matched": False,
                    "channel": None,
                    "latest_socialdata_posts": [],
                }
            )
            continue

        posts, reported_total = client.list_posts(
            app_id=app.id,
            page=0,
            per_page=bounded_limit,
            sort_field="createdAt",
            sort_order="DESC",
            filter={"channelId": channel.id},
        )
        latest_posts = [_socialdata_post_preview(post, channel=channel) for post in posts[:bounded_limit]]
        socialdata_posts.extend(latest_posts)
        channel_diagnostics.append(
            {
                "fb_page_id": fb_page_id,
                "app_name": mapping.app_name,
                "matched": True,
                "channel": {
                    "channel_id": channel.id,
                    "channel_name": _repair_text(channel.name),
                    "channel_sub": _normalize_text(channel.sub),
                    "status": channel.status,
                    "created_at": _normalize_text(channel.created_at),
                    "url": _normalize_text(channel.url),
                    "reported_post_total": reported_total,
                },
                "latest_socialdata_posts": latest_posts,
            }
        )

    socialdata_source_ids = [
        post["source_post_id"]
        for post in socialdata_posts
        if post["source_post_id"] and _post_is_in_scope(post.get("created_at"), cutoff)
    ]
    db_source_ids = _load_db_source_post_ids(conn, socialdata_source_ids)
    missing_source_post_ids = [source_id for source_id in socialdata_source_ids if source_id not in db_source_ids]
    db_posts = _latest_db_posts(conn, unified_app_id=unified_app_id, limit=bounded_limit)

    first_mapping = next(iter(active_mappings.values()))
    result = {
        "app_slug": app.slug,
        "app_id": app.id,
        "unified_app_id": unified_app_id,
        "app_name": first_mapping.app_name,
        "configured_fb_page_ids": sorted(active_mappings),
        "cutoff_iso": cutoff.isoformat() if cutoff else None,
        "matched_channels": len(matched_channels),
        "socialdata_latest_created_at": _latest_datetime_text(
            [post.get("created_at") for post in socialdata_posts]
        ),
        "db_latest_publish_time": _latest_datetime_text([post.get("publish_time") for post in db_posts]),
        "latest_db_posts": db_posts,
        "missing_source_post_ids": missing_source_post_ids,
        "channel_diagnostics": channel_diagnostics,
    }
    failures = socialdata_diagnostic_failures(result)
    result["ok"] = not failures
    result["failures"] = [asdict(failure) for failure in failures]
    return result


def socialdata_diagnostic_failures(diagnostic: dict[str, Any]) -> list[SocialDataDiagnosticFailure]:
    failures: list[SocialDataDiagnosticFailure] = []
    if int(diagnostic.get("matched_channels") or 0) == 0:
        failures.append(
            SocialDataDiagnosticFailure(
                code="no_matched_channel",
                message="No Socialdata channel matched the configured FB page ID for this game.",
            )
        )
    missing_source_post_ids = diagnostic.get("missing_source_post_ids") or []
    if missing_source_post_ids:
        failures.append(
            SocialDataDiagnosticFailure(
                code="socialdata_posts_missing_in_db",
                message=(
                    "Socialdata has recent source posts that are not present in raw_fb_posts: "
                    + ", ".join(str(item) for item in missing_source_post_ids[:10])
                ),
            )
        )
    return failures


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


def _source_post_id(post: SocialDataPost) -> str:
    return _normalize_text(post.sub) or str(post.id)


def _latest_socialdata_post(posts: list[SocialDataPost]) -> SocialDataPost | None:
    candidates = [post for post in posts if _parse_datetime(post.created_at) is not None]
    if not candidates:
        return posts[0] if posts else None
    return max(candidates, key=lambda post: _parse_datetime(post.created_at) or datetime.min.replace(tzinfo=timezone.utc))


def _socialdata_post_preview(post: SocialDataPost, *, channel: SocialDataChannel) -> dict[str, Any]:
    return {
        "source_post_id": _source_post_id(post),
        "socialdata_post_id": post.id,
        "channel_id": channel.id,
        "channel_name": _repair_text(channel.name),
        "fb_page_id": _normalize_text(channel.sub),
        "created_at": _normalize_text(post.created_at),
        "type": _socialdata_post_type_label(post.type),
        "name": _repair_text(post.name),
        "url": _normalize_text(post.url),
    }


def _latest_db_posts(
    conn: sqlite3.Connection,
    *,
    unified_app_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            source_post_id,
            unified_app_id,
            fb_page_id,
            channel_id,
            channel_name,
            post_type,
            publish_time,
            ingested_at,
            engagement,
            reaction,
            comment,
            share,
            view,
            link,
            substr(post_description, 1, 240) AS post_preview
        FROM raw_fb_posts
        WHERE unified_app_id = ?
        ORDER BY publish_time DESC
        LIMIT ?
        """,
        (unified_app_id, limit),
    ).fetchall()
    return [
        {
            "source_post_id": row["source_post_id"],
            "unified_app_id": row["unified_app_id"],
            "fb_page_id": row["fb_page_id"],
            "channel_id": row["channel_id"],
            "channel_name": row["channel_name"],
            "post_type": row["post_type"],
            "publish_time": row["publish_time"],
            "ingested_at": row["ingested_at"],
            "engagement": row["engagement"],
            "reaction": row["reaction"],
            "comment": row["comment"],
            "share": row["share"],
            "view": row["view"],
            "link": row["link"],
            "post_preview": row["post_preview"],
        }
        for row in rows
    ]


def _load_db_source_post_ids(conn: sqlite3.Connection, source_post_ids: list[str]) -> set[str]:
    unique_ids = list(dict.fromkeys(source_post_ids))
    if not unique_ids:
        return set()
    placeholders = ",".join("?" for _ in unique_ids)
    rows = conn.execute(
        f"SELECT source_post_id FROM raw_fb_posts WHERE source_post_id IN ({placeholders})",
        unique_ids,
    ).fetchall()
    return {row["source_post_id"] for row in rows}


def _post_is_in_scope(created_at: object, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    created_dt = _parse_datetime(str(created_at or ""))
    return created_dt is not None and created_dt >= cutoff


def _latest_datetime_text(values: list[object]) -> str | None:
    parsed_values: list[tuple[datetime, str]] = []
    for value in values:
        text = _normalize_text(value)
        parsed = _parse_datetime(text)
        if parsed is not None and text:
            parsed_values.append((parsed, text))
    if not parsed_values:
        return None
    return max(parsed_values, key=lambda item: item[0])[1]


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
