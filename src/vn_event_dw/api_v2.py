from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .api_service import (
    fetch_event_coverage_for_game_by_months,
    fetch_event_detail,
    fetch_event_post_statistics,
    fetch_event_posts_light,
    fetch_event_search,
    fetch_events_for_game_by_months,
    fetch_game_detail,
    fetch_games,
    fetch_post_detail_v2,
    fetch_posts_for_game,
)
from .etl import open_connection

API_VERSION = "v2"
MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")
SourceType = Literal["fb_post", "st_app_update_event", "st_version_event"]


class V2Meta(BaseModel):
    api_version: str
    generated_at: str
    scope: dict[str, Any]
    semantics: str
    count: int


class V2GameItem(BaseModel):
    unified_app_id: str
    app_name: str


class V2GameDetail(V2GameItem):
    fb_page_ids: list[str]
    is_active: bool


class V2GamesResponse(BaseModel):
    results: list[V2GameItem]
    meta: V2Meta


class V2GameDetailResponse(BaseModel):
    result: V2GameDetail
    meta: V2Meta


class V2EventItem(BaseModel):
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


class V2EventsResponse(BaseModel):
    results: list[V2EventItem]
    meta: V2Meta


class V2EventDetailResponse(BaseModel):
    result: V2EventItem
    meta: V2Meta


class V2TopSocialEvent(BaseModel):
    unified_event_id: str
    canonical_event_name: str
    event_category: str
    social_score: int
    total_engagement_fb: int
    total_reaction_fb: int
    total_comment_fb: int
    total_share_fb: int
    total_view_fb: int


class V2EventSummary(BaseModel):
    unified_app_id: str
    app_name: str
    event_count_total: int
    event_count_st_app_update: int
    event_count_st_version: int
    event_count_fb: int
    total_engagement_fb: int
    total_reaction_fb: int
    total_comment_fb: int
    total_share_fb: int
    total_view_fb: int
    top_socially_active_events: list[V2TopSocialEvent]


class V2EventSummaryResponse(BaseModel):
    result: V2EventSummary
    meta: V2Meta


class V2EventSearchItem(V2EventItem):
    unified_app_id: str
    app_name: str
    match_score: float
    match_scope: Literal["scoped_game", "cross_game_fallback"]


class V2EventSearchResponse(BaseModel):
    results: list[V2EventSearchItem]
    meta: V2Meta


class V2EventCoverageItem(BaseModel):
    unified_app_id: str
    app_name: str
    min_month_bucket: str | None
    max_month_bucket: str | None
    months_available: int
    event_count: int
    fb_post_count: int
    latest_ingested_at: str | None


class V2EventCoverageResponse(BaseModel):
    result: V2EventCoverageItem
    meta: V2Meta


class V2LinkedEvent(BaseModel):
    unified_event_id: str
    canonical_event_name: str
    month_bucket: str


class V2PostItem(BaseModel):
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
    engagement_count: int
    reaction_count: int
    comment_count: int
    share_count: int
    view_count: int
    social_score: int
    linked_events: list[V2LinkedEvent]


class V2EventLinkedPostItem(BaseModel):
    source_post_id: str
    publish_time: str
    engagement_count: int
    reaction_count: int
    comment_count: int
    share_count: int
    view_count: int
    social_score: int


class V2EventLinkedPostsResult(BaseModel):
    unified_event_id: str
    unified_app_id: str
    app_name: str
    canonical_event_name: str
    posts: list[V2EventLinkedPostItem]


class V2PostsResponse(BaseModel):
    results: list[V2PostItem]
    meta: V2Meta


class V2PostDetailResponse(BaseModel):
    result: V2PostItem
    meta: V2Meta


class V2EventPostStatsResponse(BaseModel):
    result: dict[str, Any]
    meta: V2Meta


class V2EventPostsResponse(BaseModel):
    result: V2EventLinkedPostsResult
    meta: V2Meta


class V2HealthResponse(BaseModel):
    result: dict[str, Any]
    meta: V2Meta


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meta(*, scope: dict[str, Any], semantics: str, count: int) -> V2Meta:
    return V2Meta(
        api_version=API_VERSION,
        generated_at=_generated_at(),
        scope=scope,
        semantics=semantics,
        count=count,
    )


def _validate_month(value: str) -> str:
    cleaned = value.strip()
    if not MONTH_PATTERN.match(cleaned):
        raise HTTPException(status_code=400, detail=f"Invalid month '{value}'. Expected YYYY-MM.")
    year_text, month_text = cleaned.split("-", 1)
    month_number = int(month_text)
    if month_number < 1 or month_number > 12:
        raise HTTPException(status_code=400, detail=f"Invalid month '{value}'. Month must be 01-12.")
    return f"{int(year_text):04d}-{month_number:02d}"


def _months_in_range(start_month: str, end_month: str) -> tuple[str, ...]:
    start = _validate_month(start_month)
    end = _validate_month(end_month)
    start_year, start_month_number = (int(part) for part in start.split("-", 1))
    end_year, end_month_number = (int(part) for part in end.split("-", 1))
    if (start_year, start_month_number) > (end_year, end_month_number):
        raise HTTPException(status_code=400, detail="start_month must be on or before end_month.")

    months: list[str] = []
    year = start_year
    month_number = start_month_number
    while (year, month_number) <= (end_year, end_month_number):
        months.append(f"{year:04d}-{month_number:02d}")
        if month_number == 12:
            year += 1
            month_number = 1
        else:
            month_number += 1
    return tuple(months)


def _month_scope(
    *,
    month: list[str] | None = None,
    start_month: str | None = None,
    end_month: str | None = None,
) -> tuple[str, ...] | None:
    if month and (start_month or end_month):
        raise HTTPException(status_code=400, detail="Use either repeated month or start_month/end_month, not both.")
    if (start_month is None) != (end_month is None):
        raise HTTPException(status_code=400, detail="start_month and end_month must both be provided.")
    if month:
        return tuple(dict.fromkeys(_validate_month(value) for value in month))
    if start_month and end_month:
        return _months_in_range(start_month, end_month)
    return None


def _date_scope(*, publish_start: date | None, publish_end: date | None) -> tuple[date | None, date | None]:
    if publish_start is not None and publish_end is not None and publish_start > publish_end:
        raise HTTPException(status_code=400, detail="publish_start must be on or before publish_end.")
    return publish_start, publish_end


def _month_dates(month_buckets: tuple[str, ...] | None) -> tuple[date | None, date | None]:
    if not month_buckets:
        return None, None
    start_month = min(month_buckets)
    end_month = max(month_buckets)
    start_year, start_month_number = (int(part) for part in start_month.split("-", 1))
    end_year, end_month_number = (int(part) for part in end_month.split("-", 1))
    start = date(start_year, start_month_number, 1)
    if end_month_number == 12:
        end = date(end_year + 1, 1, 1)
    else:
        end = date(end_year, end_month_number + 1, 1)
    return start, date.fromordinal(end.toordinal() - 1)


def _game_or_404(conn, unified_app_id: str) -> dict[str, Any]:
    game = fetch_game_detail(conn, unified_app_id=unified_app_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found.")
    return game


def _event_summary(*, game: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    top_events = sorted(
        events,
        key=lambda event: (
            -int(event["social_score"]),
            -int(event["total_engagement_fb"]),
            str(event["canonical_event_name"]),
        ),
    )[:5]
    return {
        "unified_app_id": game["unified_app_id"],
        "app_name": game["app_name"],
        "event_count_total": len(events),
        "event_count_st_app_update": sum(1 for event in events if int(event["st_app_update_event_count"]) > 0),
        "event_count_st_version": sum(1 for event in events if int(event["st_version_event_count"]) > 0),
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
            for event in top_events
        ],
    }


def create_v2_router(*, db_path: str | Path) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v2/health", response_model=V2HealthResponse)
    def get_health() -> V2HealthResponse:
        endpoints = [
            "GET /api/v2/health",
            "GET /api/v2/games",
            "GET /api/v2/games/{unified_app_id}",
            "GET /api/v2/games/{unified_app_id}/events",
            "GET /api/v2/games/{unified_app_id}/events/summary",
            "GET /api/v2/games/{unified_app_id}/events/search",
            "GET /api/v2/games/{unified_app_id}/events/coverage",
            "GET /api/v2/games/{unified_app_id}/posts",
            "GET /api/v2/events/{unified_event_id}",
            "GET /api/v2/events/{unified_event_id}/post-stats",
            "GET /api/v2/events/{unified_event_id}/posts",
            "GET /api/v2/posts/{source_post_id}",
        ]
        return V2HealthResponse(
            result={"status": "ok", "endpoints": endpoints},
            meta=_meta(scope={}, semantics="api_metadata", count=len(endpoints)),
        )

    @router.get("/api/v2/games", response_model=V2GamesResponse)
    def get_games(q: Annotated[str | None, Query(min_length=1)] = None) -> V2GamesResponse:
        conn = open_connection(db_path)
        try:
            results = fetch_games(conn, query=q)
        finally:
            conn.close()
        return V2GamesResponse(
            results=results,
            meta=_meta(scope={"q": q}, semantics="registered_games", count=len(results)),
        )

    @router.get("/api/v2/games/{unified_app_id}/events", response_model=V2EventsResponse)
    def get_game_events(
        unified_app_id: str,
        month: Annotated[list[str] | None, Query()] = None,
        start_month: str | None = None,
        end_month: str | None = None,
        top: Annotated[int | None, Query(ge=1)] = None,
        event_category: Annotated[list[str] | None, Query()] = None,
        source_type: Annotated[list[SourceType] | None, Query()] = None,
        min_social_score: Annotated[int | None, Query(ge=0)] = None,
        has_fb_posts: bool | None = None,
    ) -> V2EventsResponse:
        month_buckets = _month_scope(month=month, start_month=start_month, end_month=end_month)
        conn = open_connection(db_path)
        try:
            _game_or_404(conn, unified_app_id)
            results = fetch_events_for_game_by_months(
                conn,
                unified_app_id=unified_app_id,
                month_buckets=month_buckets,
                top=top,
                event_categories=event_category,
                source_types=source_type,
                min_social_score=min_social_score,
                has_fb_posts=has_fb_posts,
            )
        finally:
            conn.close()
        return V2EventsResponse(
            results=results,
            meta=_meta(
                scope={"unified_app_id": unified_app_id, "month_buckets": month_buckets},
                semantics="event_month_bucket",
                count=len(results),
            ),
        )

    @router.get("/api/v2/games/{unified_app_id}/events/summary", response_model=V2EventSummaryResponse)
    def get_game_event_summary(
        unified_app_id: str,
        month: Annotated[list[str] | None, Query()] = None,
        start_month: str | None = None,
        end_month: str | None = None,
        event_category: Annotated[list[str] | None, Query()] = None,
        source_type: Annotated[list[SourceType] | None, Query()] = None,
        min_social_score: Annotated[int | None, Query(ge=0)] = None,
        has_fb_posts: bool | None = None,
    ) -> V2EventSummaryResponse:
        month_buckets = _month_scope(month=month, start_month=start_month, end_month=end_month)
        conn = open_connection(db_path)
        try:
            game = _game_or_404(conn, unified_app_id)
            events = fetch_events_for_game_by_months(
                conn,
                unified_app_id=unified_app_id,
                month_buckets=month_buckets,
                event_categories=event_category,
                source_types=source_type,
                min_social_score=min_social_score,
                has_fb_posts=has_fb_posts,
            )
        finally:
            conn.close()
        result = _event_summary(game=game, events=events)
        return V2EventSummaryResponse(
            result=result,
            meta=_meta(
                scope={"unified_app_id": unified_app_id, "month_buckets": month_buckets},
                semantics="event_month_bucket",
                count=result["event_count_total"],
            ),
        )

    @router.get("/api/v2/games/{unified_app_id}/events/search", response_model=V2EventSearchResponse)
    def search_game_events(
        unified_app_id: str,
        q: Annotated[str, Query(min_length=1)],
        month: Annotated[list[str] | None, Query()] = None,
        start_month: str | None = None,
        end_month: str | None = None,
        top: Annotated[int, Query(ge=1)] = 10,
    ) -> V2EventSearchResponse:
        month_buckets = _month_scope(month=month, start_month=start_month, end_month=end_month)
        start_date, end_date = _month_dates(month_buckets)
        conn = open_connection(db_path)
        try:
            _game_or_404(conn, unified_app_id)
            results = fetch_event_search(
                conn,
                q=q,
                unified_app_ids=[unified_app_id],
                time_range_start=start_date,
                time_range_end=end_date,
                top=top,
            )
            if month_buckets is not None:
                results = [result for result in results if result["month_bucket"] in month_buckets]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            conn.close()
        return V2EventSearchResponse(
            results=results,
            meta=_meta(
                scope={"unified_app_id": unified_app_id, "q": q, "month_buckets": month_buckets},
                semantics="event_month_bucket_search",
                count=len(results),
            ),
        )

    @router.get("/api/v2/games/{unified_app_id}/events/coverage", response_model=V2EventCoverageResponse)
    def get_game_event_coverage(
        unified_app_id: str,
        month: Annotated[list[str] | None, Query()] = None,
        start_month: str | None = None,
        end_month: str | None = None,
    ) -> V2EventCoverageResponse:
        month_buckets = _month_scope(month=month, start_month=start_month, end_month=end_month)
        conn = open_connection(db_path)
        try:
            result = fetch_event_coverage_for_game_by_months(
                conn,
                unified_app_id=unified_app_id,
                month_buckets=month_buckets,
            )
        finally:
            conn.close()
        if result is None:
            raise HTTPException(status_code=404, detail="Game not found.")
        return V2EventCoverageResponse(
            result=result,
            meta=_meta(
                scope={"unified_app_id": unified_app_id, "month_buckets": month_buckets},
                semantics="event_month_bucket_coverage",
                count=1,
            ),
        )

    @router.get("/api/v2/games/{unified_app_id}/posts", response_model=V2PostsResponse)
    def get_game_posts(
        unified_app_id: str,
        publish_start: date | None = None,
        publish_end: date | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        q: Annotated[str | None, Query(min_length=1)] = None,
    ) -> V2PostsResponse:
        publish_start, publish_end = _date_scope(publish_start=publish_start, publish_end=publish_end)
        conn = open_connection(db_path)
        try:
            _game_or_404(conn, unified_app_id)
            results = fetch_posts_for_game(
                conn,
                unified_app_id=unified_app_id,
                publish_start=publish_start,
                publish_end=publish_end,
                limit=limit,
                q=q,
            )
        finally:
            conn.close()
        return V2PostsResponse(
            results=results,
            meta=_meta(
                scope={
                    "unified_app_id": unified_app_id,
                    "publish_start": publish_start.isoformat() if publish_start else None,
                    "publish_end": publish_end.isoformat() if publish_end else None,
                    "q": q,
                    "limit": limit,
                },
                semantics="raw_fb_post_publish_time",
                count=len(results),
            ),
        )

    @router.get("/api/v2/games/{unified_app_id}", response_model=V2GameDetailResponse)
    def get_game(unified_app_id: str) -> V2GameDetailResponse:
        conn = open_connection(db_path)
        try:
            result = _game_or_404(conn, unified_app_id)
        finally:
            conn.close()
        return V2GameDetailResponse(
            result=result,
            meta=_meta(scope={"unified_app_id": unified_app_id}, semantics="registered_game_detail", count=1),
        )

    @router.get("/api/v2/events/{unified_event_id}/post-stats", response_model=V2EventPostStatsResponse)
    def get_event_post_stats(unified_event_id: str) -> V2EventPostStatsResponse:
        conn = open_connection(db_path)
        try:
            result = fetch_event_post_statistics(conn, unified_event_id=unified_event_id)
        finally:
            conn.close()
        if result is None:
            raise HTTPException(status_code=404, detail="Unified event not found.")
        return V2EventPostStatsResponse(
            result=result,
            meta=_meta(scope={"unified_event_id": unified_event_id}, semantics="event_linked_fb_post_metrics", count=1),
        )

    @router.get("/api/v2/events/{unified_event_id}/posts", response_model=V2EventPostsResponse)
    def get_event_posts(unified_event_id: str) -> V2EventPostsResponse:
        conn = open_connection(db_path)
        try:
            result = fetch_event_posts_light(conn, unified_event_id=unified_event_id)
        finally:
            conn.close()
        if result is None:
            raise HTTPException(status_code=404, detail="Unified event not found.")
        result = {
            **result,
            "posts": [
                {
                    "source_post_id": post["source_post_id"],
                    "publish_time": post["publish_time"],
                    "engagement_count": post["engagement_num"],
                    "reaction_count": post["reaction_num"],
                    "comment_count": post["comment_num"],
                    "share_count": post["share_num"],
                    "view_count": post["view_num"],
                    "social_score": post["social_score"],
                }
                for post in result["posts"]
            ],
        }
        return V2EventPostsResponse(
            result=result,
            meta=_meta(scope={"unified_event_id": unified_event_id}, semantics="event_linked_posts", count=len(result["posts"])),
        )

    @router.get("/api/v2/events/{unified_event_id}", response_model=V2EventDetailResponse)
    def get_event(unified_event_id: str) -> V2EventDetailResponse:
        conn = open_connection(db_path)
        try:
            result = fetch_event_detail(conn, unified_event_id=unified_event_id)
        finally:
            conn.close()
        if result is None:
            raise HTTPException(status_code=404, detail="Unified event not found.")
        return V2EventDetailResponse(
            result=V2EventItem(**result),
            meta=_meta(scope={"unified_event_id": unified_event_id}, semantics="event_detail", count=1),
        )

    @router.get("/api/v2/posts/{source_post_id}", response_model=V2PostDetailResponse)
    def get_post(source_post_id: str) -> V2PostDetailResponse:
        conn = open_connection(db_path)
        try:
            result = fetch_post_detail_v2(conn, source_post_id=source_post_id)
        finally:
            conn.close()
        if result is None:
            raise HTTPException(status_code=404, detail="Post not found.")
        return V2PostDetailResponse(
            result=result,
            meta=_meta(scope={"source_post_id": source_post_id}, semantics="raw_fb_post_publish_time", count=1),
        )

    return router
