from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
import re
import sqlite3
from typing import Any

REACTION_WEIGHT = 2
COMMENT_WEIGHT = 3
SHARE_WEIGHT = 5
VIEW_WEIGHT = 1

SOURCE_TYPE_FB_POST = "fb_post"
SOURCE_TYPE_ST_APP_UPDATE = "st_app_update_event"
SOURCE_TYPE_ST_VERSION = "st_version_event"

SOURCE_TYPE_TO_COUNT_FIELD = {
    SOURCE_TYPE_FB_POST: "fb_post_count",
    SOURCE_TYPE_ST_APP_UPDATE: "st_app_update_event_count",
    SOURCE_TYPE_ST_VERSION: "st_version_event_count",
}

SEARCH_SCOPED_MATCH = "scoped_game"
SEARCH_FALLBACK_MATCH = "cross_game_fallback"
SEARCH_MIN_MATCH_SCORE = 25


def _metric_to_int_sql(column_name: str) -> str:
    return (
        "CAST("
        "COALESCE("
        f"NULLIF(REPLACE(REPLACE(REPLACE(REPLACE(TRIM({column_name}), ',', ''), '.', ''), ' ', ''), '+', ''), ''), "
        "'0'"
        ") AS INTEGER)"
    )


def _social_score_sql(
    *,
    reaction_expr: str,
    comment_expr: str,
    share_expr: str,
    view_expr: str,
) -> str:
    return (
        f"(({REACTION_WEIGHT} * COALESCE({reaction_expr}, 0)) + "
        f"({COMMENT_WEIGHT} * COALESCE({comment_expr}, 0)) + "
        f"({SHARE_WEIGHT} * COALESCE({share_expr}, 0)) + "
        f"({VIEW_WEIGHT} * COALESCE({view_expr}, 0)))"
    )


def _social_score_value(*, reaction: int, comment: int, share: int, view: int) -> int:
    return (
        (REACTION_WEIGHT * reaction)
        + (COMMENT_WEIGHT * comment)
        + (SHARE_WEIGHT * share)
        + (VIEW_WEIGHT * view)
    )


def _normalize_lookup_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _acronym_tokens(value: str) -> str:
    parts = re.findall(r"[a-z0-9]+", value.lower())
    return "".join(part[0] for part in parts if part)


def _text_tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", value.lower()))


def _fuzzy_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio() * 100.0


def _partial_ratio(needle: str, haystack: str) -> float:
    if not needle or not haystack:
        return 0.0
    if len(needle) > len(haystack):
        needle, haystack = haystack, needle
    window = len(needle)
    best = 0.0
    for index in range(0, len(haystack) - window + 1):
        best = max(best, _fuzzy_ratio(needle, haystack[index : index + window]))
    return best


@dataclass(frozen=True)
class EventLookupParams:
    unified_app_ids: tuple[str, ...]
    time_range_start: date
    time_range_end: date


@dataclass(frozen=True)
class CollectionFilters:
    event_categories: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    min_social_score: int | None = None
    has_fb_posts: bool | None = None


def _month_buckets_in_range(start_date: date, end_date: date) -> tuple[str, ...]:
    buckets: list[str] = []
    year = start_date.year
    month = start_date.month
    while (year, month) <= (end_date.year, end_date.month):
        buckets.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return tuple(buckets)


def _dedupe_cleaned(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not values:
        return ()
    return tuple(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _resolve_optional_month_buckets(
    *,
    time_range_start: date | None,
    time_range_end: date | None,
) -> tuple[str, ...] | None:
    if time_range_start is None and time_range_end is None:
        return None
    if time_range_start is None or time_range_end is None:
        raise ValueError("time_range_start and time_range_end must both be provided.")
    if time_range_start > time_range_end:
        raise ValueError("time_range_start must be on or before time_range_end.")
    return _month_buckets_in_range(time_range_start, time_range_end)


def fetch_games(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT
            unified_app_id,
            MIN(app_name) AS app_name
        FROM config_app_mapping
        GROUP BY unified_app_id
        ORDER BY LOWER(app_name), LOWER(unified_app_id)
        """,
    ).fetchall()
    results = [
        {
            "unified_app_id": str(row["unified_app_id"]),
            "app_name": str(row["app_name"] or ""),
        }
        for row in rows
    ]
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return results

    normalized_query = _normalize_lookup_text(cleaned_query)
    acronym_query = _acronym_tokens(cleaned_query)

    def _matches(result: dict[str, str]) -> bool:
        app_id = result["unified_app_id"]
        app_name = result["app_name"]
        normalized_app_id = _normalize_lookup_text(app_id)
        normalized_app_name = _normalize_lookup_text(app_name)
        acronym_app_name = _acronym_tokens(app_name)
        return any(
            candidate
            for candidate in (
                normalized_query and normalized_query in normalized_app_id,
                normalized_query and normalized_query in normalized_app_name,
                normalized_query and normalized_query == acronym_app_name,
                acronym_query and acronym_query == acronym_app_name,
            )
        )

    return [result for result in results if _matches(result)]


def validate_event_lookup_params(
    *,
    unified_app_ids: list[str],
    time_range_start: date,
    time_range_end: date,
) -> EventLookupParams:
    cleaned_ids = _dedupe_cleaned(unified_app_ids)
    if not cleaned_ids:
        raise ValueError("At least one unified_app_id is required.")
    if time_range_start > time_range_end:
        raise ValueError("time_range_start must be on or before time_range_end.")
    return EventLookupParams(
        unified_app_ids=cleaned_ids,
        time_range_start=time_range_start,
        time_range_end=time_range_end,
    )


def _validate_collection_filters(
    *,
    event_categories: list[str] | None = None,
    source_types: list[str] | None = None,
    min_social_score: int | None = None,
    has_fb_posts: bool | None = None,
) -> CollectionFilters:
    return CollectionFilters(
        event_categories=_dedupe_cleaned(event_categories),
        source_types=_dedupe_cleaned(source_types),
        min_social_score=min_social_score,
        has_fb_posts=has_fb_posts,
    )


def _load_app_names(conn: sqlite3.Connection, unified_app_ids: tuple[str, ...]) -> dict[str, str]:
    if not unified_app_ids:
        return {}
    placeholders = ", ".join("?" for _ in unified_app_ids)
    rows = conn.execute(
        f"""
        SELECT unified_app_id, MIN(app_name) AS app_name
        FROM config_app_mapping
        WHERE unified_app_id IN ({placeholders})
        GROUP BY unified_app_id
        """,
        unified_app_ids,
    ).fetchall()
    return {str(row["unified_app_id"]): str(row["app_name"] or "") for row in rows}


def _all_unified_app_ids(conn: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(result["unified_app_id"] for result in fetch_games(conn))


def _build_event_filter_clause(
    *,
    unified_app_ids: tuple[str, ...] | None = None,
    month_buckets: tuple[str, ...] | None = None,
    unified_event_id: str | None = None,
) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []

    if unified_event_id is not None:
        clauses.append("unified_event_id = ?")
        params.append(unified_event_id)

    if unified_app_ids:
        placeholders = ", ".join("?" for _ in unified_app_ids)
        clauses.append(f"unified_app_id IN ({placeholders})")
        params.extend(unified_app_ids)

    if month_buckets:
        placeholders = ", ".join("?" for _ in month_buckets)
        clauses.append(f"month_bucket IN ({placeholders})")
        params.extend(month_buckets)

    if not clauses:
        return "1 = 1", params
    return " AND ".join(clauses), params

def _event_row_query(filter_clause: str) -> str:
    return f"""
    WITH filtered_events AS (
        SELECT
            unified_event_id,
            unified_app_id,
            month_bucket,
            canonical_event_name,
            event_category,
            estimated_start_date,
            estimated_end_date,
            canonical_event_description,
            anchor_source_type,
            merge_confidence,
            COALESCE(
                estimated_start_date,
                estimated_end_date,
                month_bucket || '-01'
            ) AS effective_start_date,
            COALESCE(
                estimated_end_date,
                estimated_start_date,
                DATE(month_bucket || '-01', 'start of month', '+1 month', '-1 day')
            ) AS effective_end_date
        FROM unified_events
        WHERE {filter_clause}
    ),
    fb_metrics AS (
        SELECT
            source_post_id,
            {_metric_to_int_sql('engagement')} AS engagement_num,
            {_metric_to_int_sql('reaction')} AS reaction_num,
            {_metric_to_int_sql('comment')} AS comment_num,
            {_metric_to_int_sql('share')} AS share_num,
            {_metric_to_int_sql('view')} AS view_num
        FROM raw_fb_posts
    ),
    event_source_metrics AS (
        SELECT
            s.unified_event_id,
            COUNT(DISTINCT CASE WHEN s.source_type = 'fb_post' THEN s.source_id END) AS fb_post_count,
            COUNT(DISTINCT CASE WHEN s.source_type = 'st_app_update_event' THEN s.source_id END) AS st_app_update_event_count,
            COUNT(DISTINCT CASE WHEN s.source_type = 'st_version_event' THEN s.source_id END) AS st_version_event_count,
            COALESCE(SUM(CASE WHEN s.source_type = 'fb_post' THEN m.engagement_num ELSE 0 END), 0) AS total_engagement_fb,
            COALESCE(SUM(CASE WHEN s.source_type = 'fb_post' THEN m.reaction_num ELSE 0 END), 0) AS total_reaction_fb,
            COALESCE(SUM(CASE WHEN s.source_type = 'fb_post' THEN m.comment_num ELSE 0 END), 0) AS total_comment_fb,
            COALESCE(SUM(CASE WHEN s.source_type = 'fb_post' THEN m.share_num ELSE 0 END), 0) AS total_share_fb,
            COALESCE(SUM(CASE WHEN s.source_type = 'fb_post' THEN m.view_num ELSE 0 END), 0) AS total_view_fb
        FROM unified_event_sources s
        JOIN filtered_events e
          ON e.unified_event_id = s.unified_event_id
        LEFT JOIN fb_metrics m
          ON s.source_type = 'fb_post'
         AND s.source_id = m.source_post_id
        GROUP BY s.unified_event_id
    )
    SELECT
        e.unified_event_id,
        e.unified_app_id,
        e.month_bucket,
        e.canonical_event_name,
        e.event_category,
        e.estimated_start_date,
        e.estimated_end_date,
        e.canonical_event_description,
        e.anchor_source_type,
        e.merge_confidence,
        e.effective_start_date,
        e.effective_end_date,
        COALESCE(m.fb_post_count, 0) AS fb_post_count,
        COALESCE(m.st_app_update_event_count, 0) AS st_app_update_event_count,
        COALESCE(m.st_version_event_count, 0) AS st_version_event_count,
        COALESCE(m.total_engagement_fb, 0) AS total_engagement_fb,
        COALESCE(m.total_reaction_fb, 0) AS total_reaction_fb,
        COALESCE(m.total_comment_fb, 0) AS total_comment_fb,
        COALESCE(m.total_share_fb, 0) AS total_share_fb,
        COALESCE(m.total_view_fb, 0) AS total_view_fb,
        {_social_score_sql(
            reaction_expr='m.total_reaction_fb',
            comment_expr='m.total_comment_fb',
            share_expr='m.total_share_fb',
            view_expr='m.total_view_fb',
        )} AS social_score
    FROM filtered_events e
    LEFT JOIN event_source_metrics m
      ON m.unified_event_id = e.unified_event_id
    """


def _row_to_event_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "unified_event_id": str(row["unified_event_id"]),
        "canonical_event_name": str(row["canonical_event_name"]),
        "event_category": str(row["event_category"]),
        "estimated_start_date": row["estimated_start_date"],
        "estimated_end_date": row["estimated_end_date"],
        "canonical_event_description": str(row["canonical_event_description"]),
        "anchor_source_type": str(row["anchor_source_type"]),
        "merge_confidence": float(row["merge_confidence"]),
        "month_bucket": str(row["month_bucket"]),
        "fb_post_count": int(row["fb_post_count"]),
        "st_app_update_event_count": int(row["st_app_update_event_count"]),
        "st_version_event_count": int(row["st_version_event_count"]),
        "total_engagement_fb": int(row["total_engagement_fb"]),
        "total_reaction_fb": int(row["total_reaction_fb"]),
        "total_comment_fb": int(row["total_comment_fb"]),
        "total_share_fb": int(row["total_share_fb"]),
        "total_view_fb": int(row["total_view_fb"]),
        "social_score": int(row["social_score"]),
        "_effective_start_date": str(row["effective_start_date"]),
        "_effective_end_date": str(row["effective_end_date"]),
        "_unified_app_id": str(row["unified_app_id"]),
    }


def _sort_events_default(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda event: (
            event["_effective_start_date"],
            str(event["canonical_event_name"]),
        ),
    )


def _sort_events_top(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda event: (
            -int(event["social_score"]),
            -int(event["total_engagement_fb"]),
            str(event["canonical_event_name"]),
        ),
    )


def _strip_internal_event_fields(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if not key.startswith("_")}


def _load_event_rows(
    conn: sqlite3.Connection,
    *,
    unified_app_ids: tuple[str, ...] | None = None,
    month_buckets: tuple[str, ...] | None = None,
    unified_event_id: str | None = None,
) -> list[dict[str, Any]]:
    filter_clause, params = _build_event_filter_clause(
        unified_app_ids=unified_app_ids,
        month_buckets=month_buckets,
        unified_event_id=unified_event_id,
    )
    rows = conn.execute(
        _event_row_query(filter_clause)
        + """
        ORDER BY unified_app_id, effective_start_date, canonical_event_name
        """,
        params,
    ).fetchall()
    return [_row_to_event_dict(row) for row in rows]


def _event_matches_filters(event: dict[str, Any], filters: CollectionFilters) -> bool:
    if filters.event_categories and str(event["event_category"]) not in filters.event_categories:
        return False
    if filters.source_types and not any(
        int(event[SOURCE_TYPE_TO_COUNT_FIELD[source_type]]) > 0 for source_type in filters.source_types
    ):
        return False
    if filters.min_social_score is not None and int(event["social_score"]) < filters.min_social_score:
        return False
    if filters.has_fb_posts is not None and (int(event["fb_post_count"]) > 0) != filters.has_fb_posts:
        return False
    return True


def _apply_event_filters(events: list[dict[str, Any]], filters: CollectionFilters) -> list[dict[str, Any]]:
    if (
        not filters.event_categories
        and not filters.source_types
        and filters.min_social_score is None
        and filters.has_fb_posts is None
    ):
        return events
    return [event for event in events if _event_matches_filters(event, filters)]


def _load_scoped_events(
    conn: sqlite3.Connection,
    *,
    unified_app_ids: list[str],
    time_range_start: date,
    time_range_end: date,
    filters: CollectionFilters | None = None,
) -> tuple[EventLookupParams, dict[str, str], dict[str, list[dict[str, Any]]]]:
    params = validate_event_lookup_params(
        unified_app_ids=unified_app_ids,
        time_range_start=time_range_start,
        time_range_end=time_range_end,
    )
    month_buckets = _month_buckets_in_range(params.time_range_start, params.time_range_end)
    app_names = _load_app_names(conn, params.unified_app_ids)
    rows = _load_event_rows(conn, unified_app_ids=params.unified_app_ids, month_buckets=month_buckets)
    events_by_app: dict[str, list[dict[str, Any]]] = {app_id: [] for app_id in params.unified_app_ids}
    for event in _apply_event_filters(rows, filters or CollectionFilters()):
        app_id = str(event["_unified_app_id"])
        events_by_app.setdefault(app_id, []).append(event)
    return params, app_names, events_by_app

def fetch_events(
    conn: sqlite3.Connection,
    *,
    unified_app_ids: list[str],
    time_range_start: date,
    time_range_end: date,
    top: int | None = None,
    event_categories: list[str] | None = None,
    source_types: list[str] | None = None,
    min_social_score: int | None = None,
    has_fb_posts: bool | None = None,
) -> list[dict[str, Any]]:
    filters = _validate_collection_filters(
        event_categories=event_categories,
        source_types=source_types,
        min_social_score=min_social_score,
        has_fb_posts=has_fb_posts,
    )
    params, app_names, events_by_app = _load_scoped_events(
        conn,
        unified_app_ids=unified_app_ids,
        time_range_start=time_range_start,
        time_range_end=time_range_end,
        filters=filters,
    )
    results: list[dict[str, Any]] = []
    for app_id in params.unified_app_ids:
        events = events_by_app.get(app_id, [])
        if top is not None:
            selected = _sort_events_top(events)[:top]
        else:
            selected = _sort_events_default(events)
        results.append(
            {
                "unified_app_id": app_id,
                "app_name": app_names.get(app_id, ""),
                "events": [_strip_internal_event_fields(event) for event in selected],
            }
        )
    return results


def fetch_events_compact(
    conn: sqlite3.Connection,
    *,
    unified_app_ids: list[str],
    time_range_start: date,
    time_range_end: date,
    event_categories: list[str] | None = None,
    source_types: list[str] | None = None,
    min_social_score: int | None = None,
    has_fb_posts: bool | None = None,
) -> list[dict[str, Any]]:
    filters = _validate_collection_filters(
        event_categories=event_categories,
        source_types=source_types,
        min_social_score=min_social_score,
        has_fb_posts=has_fb_posts,
    )
    params, app_names, events_by_app = _load_scoped_events(
        conn,
        unified_app_ids=unified_app_ids,
        time_range_start=time_range_start,
        time_range_end=time_range_end,
        filters=filters,
    )
    results: list[dict[str, Any]] = []
    for app_id in params.unified_app_ids:
        selected = _sort_events_default(events_by_app.get(app_id, []))
        results.append(
            {
                "unified_app_id": app_id,
                "app_name": app_names.get(app_id, ""),
                "events": [
                    {
                        "unified_event_id": event["unified_event_id"],
                        "canonical_event_name": event["canonical_event_name"],
                        "event_category": event["event_category"],
                    }
                    for event in selected
                ],
            }
        )
    return results


def fetch_event_detail(
    conn: sqlite3.Connection,
    *,
    unified_event_id: str,
) -> dict[str, Any] | None:
    cleaned_event_id = unified_event_id.strip()
    if not cleaned_event_id:
        raise ValueError("unified_event_id is required.")

    rows = _load_event_rows(conn, unified_event_id=cleaned_event_id)
    if not rows:
        return None
    event = rows[0]
    app_name = conn.execute(
        """
        SELECT MIN(app_name) AS app_name
        FROM config_app_mapping
        WHERE unified_app_id = ?
        """,
        (event["_unified_app_id"],),
    ).fetchone()
    result = _strip_internal_event_fields(event)
    result["unified_app_id"] = str(event["_unified_app_id"])
    result["app_name"] = str((app_name["app_name"] if app_name else "") or "")
    return result


def fetch_event_post_statistics(
    conn: sqlite3.Connection,
    *,
    unified_event_id: str,
) -> dict[str, Any] | None:
    event = fetch_event_detail(conn, unified_event_id=unified_event_id)
    if event is None:
        return None
    return {
        "unified_event_id": event["unified_event_id"],
        "unified_app_id": event["unified_app_id"],
        "app_name": event["app_name"],
        "canonical_event_name": event["canonical_event_name"],
        "event_category": event["event_category"],
        "estimated_start_date": event["estimated_start_date"],
        "estimated_end_date": event["estimated_end_date"],
        "fb_post_count": event["fb_post_count"],
        "total_engagement_fb": event["total_engagement_fb"],
        "total_reaction_fb": event["total_reaction_fb"],
        "total_comment_fb": event["total_comment_fb"],
        "total_share_fb": event["total_share_fb"],
        "total_view_fb": event["total_view_fb"],
        "social_score": event["social_score"],
    }


def _fetch_event_fb_posts(
    conn: sqlite3.Connection,
    *,
    unified_event_id: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    event = fetch_event_detail(conn, unified_event_id=unified_event_id)
    if event is None:
        return None, []

    rows = conn.execute(
        f"""
        SELECT
            s.source_id AS source_post_id,
            s.source_time,
            s.source_confidence,
            fb.unified_app_id,
            fb.fb_page_id,
            fb.channel_id,
            fb.channel_name,
            fb.post_type,
            fb.post_description,
            fb.duration,
            fb.link,
            fb.publish_time,
            fb.hashtag,
            fb.engagement,
            fb.reaction,
            fb.comment,
            fb.share,
            fb.view,
            fb.source_file,
            fb.ingested_at,
            {_metric_to_int_sql('fb.engagement')} AS engagement_num,
            {_metric_to_int_sql('fb.reaction')} AS reaction_num,
            {_metric_to_int_sql('fb.comment')} AS comment_num,
            {_metric_to_int_sql('fb.share')} AS share_num,
            {_metric_to_int_sql('fb.view')} AS view_num
        FROM unified_event_sources s
        JOIN raw_fb_posts fb
          ON s.source_type = 'fb_post'
         AND s.source_id = fb.source_post_id
        WHERE s.unified_event_id = ?
        ORDER BY COALESCE(fb.publish_time, ''), s.source_id
        """,
        (unified_event_id.strip(),),
    ).fetchall()

    posts: list[dict[str, Any]] = []
    for row in rows:
        reaction_num = int(row["reaction_num"])
        comment_num = int(row["comment_num"])
        share_num = int(row["share_num"])
        view_num = int(row["view_num"])
        posts.append(
            {
                "source_post_id": str(row["source_post_id"]),
                "source_time": row["source_time"],
                "source_confidence": None if row["source_confidence"] is None else float(row["source_confidence"]),
                "unified_app_id": str(row["unified_app_id"]),
                "fb_page_id": str(row["fb_page_id"]),
                "channel_id": str(row["channel_id"]),
                "channel_name": str(row["channel_name"]),
                "post_type": str(row["post_type"]),
                "post_description": str(row["post_description"]),
                "duration": str(row["duration"]),
                "link": str(row["link"]),
                "publish_time": str(row["publish_time"]),
                "hashtag": str(row["hashtag"]),
                "engagement": str(row["engagement"]),
                "reaction": str(row["reaction"]),
                "comment": str(row["comment"]),
                "share": str(row["share"]),
                "view": str(row["view"]),
                "source_file": str(row["source_file"]),
                "ingested_at": str(row["ingested_at"]),
                "engagement_num": int(row["engagement_num"]),
                "reaction_num": reaction_num,
                "comment_num": comment_num,
                "share_num": share_num,
                "view_num": view_num,
                "social_score": _social_score_value(
                    reaction=reaction_num,
                    comment=comment_num,
                    share=share_num,
                    view=view_num,
                ),
            }
        )
    return event, posts


def fetch_event_top_fb_posts(
    conn: sqlite3.Connection,
    *,
    unified_event_id: str,
    top: int = 5,
) -> dict[str, Any] | None:
    event, posts = _fetch_event_fb_posts(conn, unified_event_id=unified_event_id)
    if event is None:
        return None
    ranked_posts = sorted(
        posts,
        key=lambda post: (
            -int(post["social_score"]),
            -int(post["engagement_num"]),
            str(post["source_post_id"]),
        ),
    )[:top]
    return {
        "unified_event_id": event["unified_event_id"],
        "unified_app_id": event["unified_app_id"],
        "app_name": event["app_name"],
        "canonical_event_name": event["canonical_event_name"],
        "posts": [
            {
                "source_post_id": post["source_post_id"],
                "publish_time": post["publish_time"],
                "link": post["link"],
                "engagement_num": post["engagement_num"],
                "reaction_num": post["reaction_num"],
                "comment_num": post["comment_num"],
                "share_num": post["share_num"],
                "view_num": post["view_num"],
                "social_score": post["social_score"],
            }
            for post in ranked_posts
        ],
    }


def fetch_event_posts_light(
    conn: sqlite3.Connection,
    *,
    unified_event_id: str,
) -> dict[str, Any] | None:
    event, posts = _fetch_event_fb_posts(conn, unified_event_id=unified_event_id)
    if event is None:
        return None
    return {
        "unified_event_id": event["unified_event_id"],
        "unified_app_id": event["unified_app_id"],
        "app_name": event["app_name"],
        "canonical_event_name": event["canonical_event_name"],
        "posts": [
            {
                "source_post_id": post["source_post_id"],
                "publish_time": post["publish_time"],
                "engagement_num": post["engagement_num"],
                "reaction_num": post["reaction_num"],
                "comment_num": post["comment_num"],
                "share_num": post["share_num"],
                "view_num": post["view_num"],
                "social_score": post["social_score"],
            }
            for post in posts
        ],
    }


def fetch_post_detail(
    conn: sqlite3.Connection,
    *,
    source_post_id: str,
) -> dict[str, Any] | None:
    cleaned_source_post_id = source_post_id.strip()
    if not cleaned_source_post_id:
        raise ValueError("source_post_id is required.")

    row = conn.execute(
        f"""
        SELECT
            fb.source_post_id,
            fb.unified_app_id,
            MIN(m.app_name) AS app_name,
            fb.fb_page_id,
            fb.channel_id,
            fb.channel_name,
            fb.post_type,
            fb.post_description,
            fb.duration,
            fb.link,
            fb.publish_time,
            fb.hashtag,
            fb.engagement,
            fb.reaction,
            fb.comment,
            fb.share,
            fb.view,
            fb.source_file,
            fb.ingested_at,
            {_metric_to_int_sql('fb.engagement')} AS engagement_num,
            {_metric_to_int_sql('fb.reaction')} AS reaction_num,
            {_metric_to_int_sql('fb.comment')} AS comment_num,
            {_metric_to_int_sql('fb.share')} AS share_num,
            {_metric_to_int_sql('fb.view')} AS view_num
        FROM raw_fb_posts fb
        LEFT JOIN config_app_mapping m
          ON m.unified_app_id = fb.unified_app_id
        WHERE fb.source_post_id = ?
        GROUP BY
            fb.source_post_id,
            fb.unified_app_id,
            fb.fb_page_id,
            fb.channel_id,
            fb.channel_name,
            fb.post_type,
            fb.post_description,
            fb.duration,
            fb.link,
            fb.publish_time,
            fb.hashtag,
            fb.engagement,
            fb.reaction,
            fb.comment,
            fb.share,
            fb.view,
            fb.source_file,
            fb.ingested_at
        """,
        (cleaned_source_post_id,),
    ).fetchone()

    if row is None:
        return None

    reaction_num = int(row["reaction_num"])
    comment_num = int(row["comment_num"])
    share_num = int(row["share_num"])
    view_num = int(row["view_num"])

    return {
        "source_post_id": str(row["source_post_id"]),
        "unified_app_id": str(row["unified_app_id"]),
        "app_name": str(row["app_name"] or ""),
        "fb_page_id": str(row["fb_page_id"]),
        "channel_id": str(row["channel_id"]),
        "channel_name": str(row["channel_name"]),
        "post_type": str(row["post_type"]),
        "post_description": str(row["post_description"]),
        "duration": str(row["duration"]),
        "link": str(row["link"]),
        "publish_time": str(row["publish_time"]),
        "hashtag": str(row["hashtag"]),
        "engagement": str(row["engagement"]),
        "reaction": str(row["reaction"]),
        "comment": str(row["comment"]),
        "share": str(row["share"]),
        "view": str(row["view"]),
        "source_file": str(row["source_file"]),
        "ingested_at": str(row["ingested_at"]),
        "engagement_num": int(row["engagement_num"]),
        "reaction_num": reaction_num,
        "comment_num": comment_num,
        "share_num": share_num,
        "view_num": view_num,
        "social_score": _social_score_value(
            reaction=reaction_num,
            comment=comment_num,
            share=share_num,
            view=view_num,
        ),
    }


def fetch_event_summary(
    conn: sqlite3.Connection,
    *,
    unified_app_ids: list[str],
    time_range_start: date,
    time_range_end: date,
    event_categories: list[str] | None = None,
    source_types: list[str] | None = None,
    min_social_score: int | None = None,
    has_fb_posts: bool | None = None,
) -> list[dict[str, Any]]:
    detailed_results = fetch_events(
        conn,
        unified_app_ids=unified_app_ids,
        time_range_start=time_range_start,
        time_range_end=time_range_end,
        event_categories=event_categories,
        source_types=source_types,
        min_social_score=min_social_score,
        has_fb_posts=has_fb_posts,
    )

    results: list[dict[str, Any]] = []
    for block in detailed_results:
        events = block["events"]
        top_social_events = sorted(
            events,
            key=lambda event: (
                -int(event["social_score"]),
                -int(event["total_engagement_fb"]),
                str(event["canonical_event_name"]),
            ),
        )[:5]
        results.append(
            {
                "unified_app_id": block["unified_app_id"],
                "app_name": block["app_name"],
                "statistics": {
                    "event_count_total": len(events),
                    "event_count_st_app_update": sum(
                        1 for event in events if int(event["st_app_update_event_count"]) > 0
                    ),
                    "event_count_st_version": sum(
                        1 for event in events if int(event["st_version_event_count"]) > 0
                    ),
                    "event_count_fb": sum(1 for event in events if int(event["fb_post_count"]) > 0),
                    "total_engagement_fb": sum(int(event["total_engagement_fb"]) for event in events),
                    "total_reaction_fb": sum(int(event["total_reaction_fb"]) for event in events),
                    "total_comment_fb": sum(int(event["total_comment_fb"]) for event in events),
                    "total_share_fb": sum(int(event["total_share_fb"]) for event in events),
                    "total_view_fb": sum(int(event["total_view_fb"]) for event in events),
                    "top_socially_active_events": [
                        {
                            "unified_event_id": event["unified_event_id"],
                            "canonical_event_name": event["canonical_event_name"],
                            "event_category": event["event_category"],
                            "social_score": int(event["social_score"]),
                            "total_engagement_fb": int(event["total_engagement_fb"]),
                            "total_reaction_fb": int(event["total_reaction_fb"]),
                            "total_comment_fb": int(event["total_comment_fb"]),
                            "total_share_fb": int(event["total_share_fb"]),
                            "total_view_fb": int(event["total_view_fb"]),
                        }
                        for event in top_social_events
                    ],
                },
            }
        )
    return results


def fetch_event_coverage(
    conn: sqlite3.Connection,
    *,
    unified_app_ids: list[str] | None = None,
    time_range_start: date | None = None,
    time_range_end: date | None = None,
) -> list[dict[str, Any]]:
    cleaned_app_ids = _dedupe_cleaned(unified_app_ids)
    scoped_app_ids = cleaned_app_ids or _all_unified_app_ids(conn)
    month_buckets = _resolve_optional_month_buckets(
        time_range_start=time_range_start,
        time_range_end=time_range_end,
    )
    app_names = _load_app_names(conn, scoped_app_ids)

    filter_clause, params = _build_event_filter_clause(
        unified_app_ids=scoped_app_ids,
        month_buckets=month_buckets,
    )
    placeholders = ", ".join("?" for _ in scoped_app_ids) if scoped_app_ids else "''"
    rows = conn.execute(
        f"""
        WITH filtered_events AS (
            SELECT
                unified_event_id,
                unified_app_id,
                month_bucket
            FROM unified_events
            WHERE {filter_clause}
        ),
        fb_linked_posts AS (
            SELECT
                e.unified_app_id,
                s.source_id AS source_post_id
            FROM filtered_events e
            JOIN unified_event_sources s
              ON s.unified_event_id = e.unified_event_id
             AND s.source_type = 'fb_post'
        )
        SELECT
            apps.unified_app_id AS unified_app_id,
            MIN(m.app_name) AS app_name,
            MIN(e.month_bucket) AS min_month_bucket,
            MAX(e.month_bucket) AS max_month_bucket,
            COUNT(DISTINCT e.month_bucket) AS months_available,
            COUNT(DISTINCT e.unified_event_id) AS event_count,
            COUNT(DISTINCT p.source_post_id) AS fb_post_count,
            MAX(fb.ingested_at) AS latest_ingested_at
        FROM (
            SELECT ? AS unified_app_id
            {''.join(' UNION ALL SELECT ?' for _ in scoped_app_ids[1:])}
        ) apps
        LEFT JOIN config_app_mapping m
          ON m.unified_app_id = apps.unified_app_id
        LEFT JOIN filtered_events e
          ON e.unified_app_id = apps.unified_app_id
        LEFT JOIN fb_linked_posts p
          ON p.unified_app_id = apps.unified_app_id
        LEFT JOIN raw_fb_posts fb
          ON fb.source_post_id = p.source_post_id
        GROUP BY apps.unified_app_id
        ORDER BY LOWER(COALESCE(MIN(m.app_name), apps.unified_app_id)), LOWER(apps.unified_app_id)
        """,
        [*params, *scoped_app_ids],
    ).fetchall()

    return [
        {
            "unified_app_id": str(row["unified_app_id"]),
            "app_name": str(row["app_name"] or app_names.get(str(row["unified_app_id"]), "")),
            "min_month_bucket": row["min_month_bucket"],
            "max_month_bucket": row["max_month_bucket"],
            "months_available": int(row["months_available"]),
            "event_count": int(row["event_count"]),
            "fb_post_count": int(row["fb_post_count"]),
            "latest_ingested_at": row["latest_ingested_at"],
        }
        for row in rows
    ]


def _event_search_score(query: str, event: dict[str, Any]) -> float:
    normalized_query = _normalize_lookup_text(query)
    query_tokens = set(_text_tokens(query))
    name = str(event["canonical_event_name"])
    description = str(event["canonical_event_description"])
    normalized_name = _normalize_lookup_text(name)
    normalized_description = _normalize_lookup_text(description)
    name_tokens = set(_text_tokens(name))
    description_tokens = set(_text_tokens(description))

    score = 0.0
    name_substring_hit = bool(normalized_query and normalized_query in normalized_name)
    description_substring_hit = bool(normalized_query and normalized_query in normalized_description)
    name_token_overlap = (len(query_tokens & name_tokens) / len(query_tokens)) if query_tokens else 0.0
    description_token_overlap = (len(query_tokens & description_tokens) / len(query_tokens)) if query_tokens else 0.0

    if name_substring_hit:
        score += 55.0
    if description_substring_hit:
        score += 35.0
    if name_token_overlap:
        score += 25.0 * name_token_overlap
    if description_token_overlap:
        score += 18.0 * description_token_overlap

    if normalized_query and normalized_name and (name_substring_hit or name_token_overlap > 0):
        score += 0.35 * _fuzzy_ratio(normalized_query, normalized_name)
        score += 0.20 * _partial_ratio(normalized_query, normalized_name)

    if normalized_query and normalized_description and (description_substring_hit or description_token_overlap > 0):
        score += 0.12 * _fuzzy_ratio(normalized_query, normalized_description)
        score += 0.08 * _partial_ratio(normalized_query, normalized_description)

    return round(min(score, 100.0), 2)


def _rank_event_search_results(
    events: list[dict[str, Any]],
    *,
    query: str,
    app_names: dict[str, str],
    match_scope: str,
    top: int,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for event in events:
        match_score = _event_search_score(query, event)
        if match_score < SEARCH_MIN_MATCH_SCORE:
            continue
        ranked.append(
            {
                **_strip_internal_event_fields(event),
                "unified_app_id": str(event["_unified_app_id"]),
                "app_name": app_names.get(str(event["_unified_app_id"]), ""),
                "match_score": match_score,
                "match_scope": match_scope,
            }
        )

    ranked.sort(
        key=lambda event: (
            -float(event["match_score"]),
            -int(event["social_score"]),
            -int(event["total_engagement_fb"]),
            str(event["canonical_event_name"]),
        )
    )
    return ranked[:top]


def fetch_event_search(
    conn: sqlite3.Connection,
    *,
    q: str,
    unified_app_ids: list[str] | None = None,
    time_range_start: date | None = None,
    time_range_end: date | None = None,
    top: int = 10,
) -> list[dict[str, Any]]:
    cleaned_query = q.strip()
    if not cleaned_query:
        raise ValueError("q is required.")

    month_buckets = _resolve_optional_month_buckets(
        time_range_start=time_range_start,
        time_range_end=time_range_end,
    )
    scoped_app_ids = _dedupe_cleaned(unified_app_ids)

    if scoped_app_ids:
        scoped_app_names = _load_app_names(conn, scoped_app_ids)
        scoped_events = _load_event_rows(
            conn,
            unified_app_ids=scoped_app_ids,
            month_buckets=month_buckets,
        )
        ranked_scoped = _rank_event_search_results(
            scoped_events,
            query=cleaned_query,
            app_names=scoped_app_names,
            match_scope=SEARCH_SCOPED_MATCH,
            top=top,
        )
        if ranked_scoped:
            return ranked_scoped

    fallback_app_ids = _all_unified_app_ids(conn)
    fallback_app_names = _load_app_names(conn, fallback_app_ids)
    fallback_events = _load_event_rows(
        conn,
        unified_app_ids=fallback_app_ids,
        month_buckets=month_buckets,
    )
    return _rank_event_search_results(
        fallback_events,
        query=cleaned_query,
        app_names=fallback_app_names,
        match_scope=SEARCH_FALLBACK_MATCH if scoped_app_ids else SEARCH_SCOPED_MATCH,
        top=top,
    )
