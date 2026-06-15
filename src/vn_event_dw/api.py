from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

from .api_service import (
    fetch_event_coverage,
    fetch_event_detail,
    fetch_event_post_statistics,
    fetch_event_posts_light,
    fetch_event_search,
    fetch_event_summary,
    fetch_event_top_fb_posts,
    fetch_events,
    fetch_events_compact,
    fetch_games,
    fetch_post_detail,
    validate_event_lookup_params,
)
from .etl import open_connection

SourceType = Literal["fb_post", "st_app_update_event", "st_version_event"]


class EventResponseItem(BaseModel):
    unified_event_id: str
    canonical_event_name: str
    event_category: str
    estimated_start_date: str | None
    estimated_end_date: str | None
    canonical_event_description: str
    anchor_source_type: str
    merge_confidence: float
    month_bucket: str
    fb_post_count: int
    st_app_update_event_count: int
    st_version_event_count: int
    total_engagement_fb: int
    total_reaction_fb: int
    total_comment_fb: int
    total_share_fb: int
    total_view_fb: int
    social_score: int


class EventsByAppResponse(BaseModel):
    unified_app_id: str
    app_name: str
    events: list[EventResponseItem]


class EventsResponse(BaseModel):
    results: list[EventsByAppResponse]


class CompactEventResponseItem(BaseModel):
    unified_event_id: str
    canonical_event_name: str
    event_category: str


class CompactEventsByAppResponse(BaseModel):
    unified_app_id: str
    app_name: str
    events: list[CompactEventResponseItem]


class CompactEventsResponse(BaseModel):
    results: list[CompactEventsByAppResponse]


class GameLookupResponseItem(BaseModel):
    unified_app_id: str
    app_name: str


class GameLookupResponse(BaseModel):
    results: list[GameLookupResponseItem]


class TopSocialEventResponse(BaseModel):
    unified_event_id: str
    canonical_event_name: str
    event_category: str
    social_score: int
    total_engagement_fb: int
    total_reaction_fb: int
    total_comment_fb: int
    total_share_fb: int
    total_view_fb: int


class EventSummaryResponseItem(BaseModel):
    event_count_total: int
    event_count_st_app_update: int
    event_count_st_version: int
    event_count_fb: int
    total_engagement_fb: int
    total_reaction_fb: int
    total_comment_fb: int
    total_share_fb: int
    total_view_fb: int
    top_socially_active_events: list[TopSocialEventResponse]


class EventSummaryByAppResponse(BaseModel):
    unified_app_id: str
    app_name: str
    statistics: EventSummaryResponseItem


class EventSummaryResponse(BaseModel):
    results: list[EventSummaryByAppResponse]


class EventDetailResponse(BaseModel):
    unified_event_id: str
    unified_app_id: str
    app_name: str
    canonical_event_name: str
    event_category: str
    estimated_start_date: str | None
    estimated_end_date: str | None
    canonical_event_description: str
    anchor_source_type: str
    merge_confidence: float
    month_bucket: str
    fb_post_count: int
    st_app_update_event_count: int
    st_version_event_count: int
    total_engagement_fb: int
    total_reaction_fb: int
    total_comment_fb: int
    total_share_fb: int
    total_view_fb: int
    social_score: int


class EventPostStatisticsResponse(BaseModel):
    unified_event_id: str
    unified_app_id: str
    app_name: str
    canonical_event_name: str
    event_category: str
    estimated_start_date: str | None
    estimated_end_date: str | None
    fb_post_count: int
    total_engagement_fb: int
    total_reaction_fb: int
    total_comment_fb: int
    total_share_fb: int
    total_view_fb: int
    social_score: int


class EventPostCompactResponseItem(BaseModel):
    source_post_id: str
    publish_time: str
    engagement_num: int
    reaction_num: int
    comment_num: int
    share_num: int
    view_num: int
    social_score: int


class EventTopPostResponseItem(EventPostCompactResponseItem):
    link: str


class EventTopPostsResponse(BaseModel):
    unified_event_id: str
    unified_app_id: str
    app_name: str
    canonical_event_name: str
    posts: list[EventTopPostResponseItem]


class EventPostsLightResponse(BaseModel):
    unified_event_id: str
    unified_app_id: str
    app_name: str
    canonical_event_name: str
    posts: list[EventPostCompactResponseItem]


class PostDetailResponse(BaseModel):
    source_post_id: str
    unified_app_id: str
    app_name: str
    fb_page_id: str
    channel_id: str
    channel_name: str
    post_type: str
    post_description: str
    duration: str
    link: str
    publish_time: str
    hashtag: str
    engagement: str
    reaction: str
    comment: str
    share: str
    view: str
    source_file: str
    ingested_at: str
    engagement_num: int
    reaction_num: int
    comment_num: int
    share_num: int
    view_num: int
    social_score: int


class EventCoverageResponseItem(BaseModel):
    unified_app_id: str
    app_name: str
    min_month_bucket: str | None
    max_month_bucket: str | None
    months_available: int
    event_count: int
    fb_post_count: int
    latest_ingested_at: str | None


class EventCoverageResponse(BaseModel):
    results: list[EventCoverageResponseItem]


class EventSearchResponseItem(BaseModel):
    unified_event_id: str
    unified_app_id: str
    app_name: str
    canonical_event_name: str
    event_category: str
    canonical_event_description: str
    month_bucket: str
    social_score: int
    fb_post_count: int
    match_score: float
    match_scope: Literal["scoped_game", "cross_game_fallback"]


class EventSearchResponse(BaseModel):
    results: list[EventSearchResponseItem]


def _resolve_db_path(db_path: str | Path | None) -> Path:
    resolved = db_path or os.getenv("VN_EVENT_DW_DB_PATH", "").strip()
    if not resolved:
        raise RuntimeError("A database path is required. Pass --db or set VN_EVENT_DW_DB_PATH.")
    return Path(resolved)


def create_app(*, db_path: str | Path | None = None) -> FastAPI:
    resolved_db_path = _resolve_db_path(db_path)
    app = FastAPI(title="VN Event DW API", version="0.1.0")

    def _lookup_params(
        unified_app_id: Annotated[list[str], Query(..., min_length=1)],
        time_range_start: date,
        time_range_end: date,
    ) -> tuple[list[str], date, date]:
        try:
            validated = validate_event_lookup_params(
                unified_app_ids=unified_app_id,
                time_range_start=time_range_start,
                time_range_end=time_range_end,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return list(validated.unified_app_ids), validated.time_range_start, validated.time_range_end

    def _filters(
        event_category: Annotated[list[str] | None, Query()] = None,
        source_type: Annotated[list[SourceType] | None, Query()] = None,
        min_social_score: Annotated[int | None, Query(ge=0)] = None,
        has_fb_posts: bool | None = None,
    ) -> dict[str, object]:
        return {
            "event_categories": event_category,
            "source_types": source_type,
            "min_social_score": min_social_score,
            "has_fb_posts": has_fb_posts,
        }

    def _optional_date_scope(
        time_range_start: date | None = None,
        time_range_end: date | None = None,
    ) -> tuple[date | None, date | None]:
        if (time_range_start is None) != (time_range_end is None):
            raise HTTPException(
                status_code=400,
                detail="time_range_start and time_range_end must both be provided.",
            )
        if time_range_start is not None and time_range_end is not None and time_range_start > time_range_end:
            raise HTTPException(
                status_code=400,
                detail="time_range_start must be on or before time_range_end.",
            )
        return time_range_start, time_range_end

    @app.get("/api/games", response_model=GameLookupResponse)
    def get_games(
        q: Annotated[str | None, Query(min_length=1)] = None,
    ) -> GameLookupResponse:
        conn = open_connection(resolved_db_path)
        try:
            results = fetch_games(conn, query=q)
        finally:
            conn.close()
        return GameLookupResponse(results=results)

    @app.get("/api/events", response_model=EventsResponse)
    def get_events(
        lookup: tuple[list[str], date, date] = Depends(_lookup_params),
        filters: dict[str, object] = Depends(_filters),
        top: Annotated[int | None, Query(ge=1)] = None,
    ) -> EventsResponse:
        unified_app_ids, time_range_start, time_range_end = lookup
        conn = open_connection(resolved_db_path)
        try:
            results = fetch_events(
                conn,
                unified_app_ids=unified_app_ids,
                time_range_start=time_range_start,
                time_range_end=time_range_end,
                top=top,
                **filters,
            )
        finally:
            conn.close()
        return EventsResponse(results=results)

    @app.get("/api/events/compact", response_model=CompactEventsResponse)
    def get_events_compact(
        lookup: tuple[list[str], date, date] = Depends(_lookup_params),
        filters: dict[str, object] = Depends(_filters),
    ) -> CompactEventsResponse:
        unified_app_ids, time_range_start, time_range_end = lookup
        conn = open_connection(resolved_db_path)
        try:
            results = fetch_events_compact(
                conn,
                unified_app_ids=unified_app_ids,
                time_range_start=time_range_start,
                time_range_end=time_range_end,
                **filters,
            )
        finally:
            conn.close()
        return CompactEventsResponse(results=results)

    @app.get("/api/events/summary", response_model=EventSummaryResponse)
    def get_event_summary(
        lookup: tuple[list[str], date, date] = Depends(_lookup_params),
        filters: dict[str, object] = Depends(_filters),
    ) -> EventSummaryResponse:
        unified_app_ids, time_range_start, time_range_end = lookup
        conn = open_connection(resolved_db_path)
        try:
            results = fetch_event_summary(
                conn,
                unified_app_ids=unified_app_ids,
                time_range_start=time_range_start,
                time_range_end=time_range_end,
                **filters,
            )
        finally:
            conn.close()
        return EventSummaryResponse(results=results)

    @app.get("/api/events/coverage", response_model=EventCoverageResponse)
    def get_event_coverage(
        unified_app_id: Annotated[list[str] | None, Query(min_length=1)] = None,
        date_scope: tuple[date | None, date | None] = Depends(_optional_date_scope),
    ) -> EventCoverageResponse:
        time_range_start, time_range_end = date_scope
        conn = open_connection(resolved_db_path)
        try:
            results = fetch_event_coverage(
                conn,
                unified_app_ids=unified_app_id,
                time_range_start=time_range_start,
                time_range_end=time_range_end,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            conn.close()
        return EventCoverageResponse(results=results)

    @app.get("/api/events/search", response_model=EventSearchResponse)
    def get_event_search(
        q: Annotated[str, Query(min_length=1)],
        unified_app_id: Annotated[list[str] | None, Query(min_length=1)] = None,
        date_scope: tuple[date | None, date | None] = Depends(_optional_date_scope),
        top: Annotated[int, Query(ge=1)] = 10,
    ) -> EventSearchResponse:
        time_range_start, time_range_end = date_scope
        conn = open_connection(resolved_db_path)
        try:
            results = fetch_event_search(
                conn,
                q=q,
                unified_app_ids=unified_app_id,
                time_range_start=time_range_start,
                time_range_end=time_range_end,
                top=top,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            conn.close()
        return EventSearchResponse(results=results)

    @app.get("/api/events/{unified_event_id}/post-stats", response_model=EventPostStatisticsResponse)
    def get_event_post_stats(unified_event_id: str) -> EventPostStatisticsResponse:
        conn = open_connection(resolved_db_path)
        try:
            result = fetch_event_post_statistics(conn, unified_event_id=unified_event_id)
        finally:
            conn.close()
        if result is None:
            raise HTTPException(status_code=404, detail="Unified event not found.")
        return EventPostStatisticsResponse(**result)

    @app.get("/api/events/{unified_event_id}/top-posts", response_model=EventTopPostsResponse)
    def get_event_top_posts(
        unified_event_id: str,
        top: Annotated[int, Query(ge=1)] = 5,
    ) -> EventTopPostsResponse:
        conn = open_connection(resolved_db_path)
        try:
            result = fetch_event_top_fb_posts(conn, unified_event_id=unified_event_id, top=top)
        finally:
            conn.close()
        if result is None:
            raise HTTPException(status_code=404, detail="Unified event not found.")
        return EventTopPostsResponse(**result)

    @app.get("/api/events/{unified_event_id}/posts", response_model=EventPostsLightResponse)
    def get_event_posts(unified_event_id: str) -> EventPostsLightResponse:
        conn = open_connection(resolved_db_path)
        try:
            result = fetch_event_posts_light(conn, unified_event_id=unified_event_id)
        finally:
            conn.close()
        if result is None:
            raise HTTPException(status_code=404, detail="Unified event not found.")
        return EventPostsLightResponse(**result)

    @app.get("/api/events/{unified_event_id}", response_model=EventDetailResponse)
    def get_event_detail(unified_event_id: str) -> EventDetailResponse:
        conn = open_connection(resolved_db_path)
        try:
            result = fetch_event_detail(conn, unified_event_id=unified_event_id)
        finally:
            conn.close()
        if result is None:
            raise HTTPException(status_code=404, detail="Unified event not found.")
        return EventDetailResponse(**result)

    @app.get("/api/posts/{source_post_id}", response_model=PostDetailResponse)
    def get_post(source_post_id: str) -> PostDetailResponse:
        conn = open_connection(resolved_db_path)
        try:
            result = fetch_post_detail(conn, source_post_id=source_post_id)
        finally:
            conn.close()
        if result is None:
            raise HTTPException(status_code=404, detail="Post not found.")
        return PostDetailResponse(**result)

    return app
