from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .etl import ensure_all_raw_fb_posts_mapped
from .fb_event_ai import (
    DEDUP_PROMPT_VERSION,
    DETECTION_PROMPT_VERSION,
    EXTRACTION_PROMPT_VERSION,
    LlmUsageRecord,
    MERGE_PROMPT_VERSION,
    REMAINING_FB_HARVEST_PROMPT_VERSION,
    UNIFIED_CONSOLIDATION_PROMPT_VERSION,
    UNIFIED_MERGE_PROMPT_VERSION,
    UNIFIED_EVENT_CATEGORIES,
    OpenAIFbEventClient,
)

RULE_MERGE_SCORE_THRESHOLD = 0.90
RULE_REJECT_SCORE_THRESHOLD = 0.55

try:  # pragma: no cover - optional dependency path
    from rapidfuzz import fuzz as rapidfuzz_fuzz
except ImportError:  # pragma: no cover - exercised when dependency missing
    rapidfuzz_fuzz = None


DETECTION_THRESHOLD = 0.60
PROGRESS_LOG_EVERY = 25
CHECKPOINT_COMMIT_EVERY = 25
UNIFIED_MERGE_SAFE_INPUT_TOKEN_TARGET = 180_000
UNIFIED_MERGE_REQUEST_TOKEN_OVERHEAD = 8_000
FB_POST_TITLE_MAX_LENGTH = 120
FB_POST_TEXT_MAX_LENGTH = 8_000
REMAINING_FB_HARVEST_LOG_EVERY = 10
UMBRELLA_PREBUCKET_CATEGORIES = {
    "Monetization",
    "Retention / Free Rewards",
    "Progression / Season Systems",
}
CAMPAIGN_ALIAS_PREBUCKET_CATEGORIES = {
    "Monetization",
    "Retention / Free Rewards",
    "Progression / Season Systems",
    "Gameplay / Content Activation",
    "Community Participation",
}
CAMPAIGN_NAME_STOPWORDS = {
    "su",
    "kien",
    "sự",
    "kiện",
    "ra",
    "mắt",
    "mat",
    "bat",
    "bắt",
    "nam",
    "nắm",
    "tron",
    "trọn",
    "dang",
    "đang",
    "da",
    "đã",
    "mo",
    "mở",
    "uu",
    "ưu",
    "dai",
    "đãi",
    "nhac",
    "nhắc",
    "ban",
    "bạn",
    "co",
    "cơ",
    "hoi",
    "hội",
    "cuoi",
    "cuối",
    "ngay",
    "ngày",
    "dem",
    "đếm",
    "nguoc",
    "ngược",
    "bắttrọn",
}
DESCRIPTION_MATCH_STOPWORDS = {
    "su",
    "sự",
    "kien",
    "kiện",
    "nguoi",
    "người",
    "choi",
    "chơi",
    "tham",
    "gia",
    "de",
    "để",
    "va",
    "và",
    "voi",
    "với",
    "trong",
    "tai",
    "tại",
    "cho",
    "co",
    "có",
    "the",
    "thể",
    "nhan",
    "nhận",
}

REMAINING_FB_CATEGORY_TO_UNIFIED_CATEGORY = {
    "monetization": "Monetization",
    "retention_free_rewards": "Retention / Free Rewards",
    "progression_season_systems": "Progression / Season Systems",
    "gameplay_content_activation": "Gameplay / Content Activation",
    "community_participation": "Community Participation",
    "media_awareness": "Media / Awareness",
    "release_update_rollout": "Release / Update Rollout",
    "unknown_event": "Other",
}


@dataclass(frozen=True, slots=True)
class FbEventDetectionStats:
    processed_posts: int
    detected_posts: int


@dataclass(frozen=True, slots=True)
class FbEventObjectStats:
    processed_posts: int
    extracted_objects: int


@dataclass(frozen=True, slots=True)
class FbRawEventBuildStats:
    detection_processed_posts: int
    detected_posts: int
    extraction_processed_posts: int
    extracted_objects: int


@dataclass(frozen=True, slots=True)
class FbLlmMergeStats:
    merge_groups: int
    merged_events: int
    source_objects: int


@dataclass(frozen=True, slots=True)
class FbEventBuildStats:
    candidate_pairs: int
    judged_pairs: int
    fb_events: int


@dataclass(frozen=True, slots=True)
class FbEventDecisionPreviewStats:
    candidate_pairs: int
    rule_merge_pairs: int
    rule_reject_pairs: int
    llm_judge_pairs: int


@dataclass(frozen=True, slots=True)
class UnifiedEventBuildStats:
    merge_scopes: int
    source_rows: int
    merged_events: int


@dataclass(frozen=True, slots=True)
class NormalizedFbPost:
    post_id: str
    unified_app_id: str
    fb_page_id: str
    channel_id: str
    page_name: str
    game_name: str
    post_text: str
    post_time: str
    hashtags: str
    link: str
    post_type: str
    engagement_raw: str
    reaction_count: int
    comment_count: int
    share_count: int
    view_count: int
    source_file: str
    ingested_at: str

    @property
    def post_text_hash(self) -> str:
        return _content_hash(self.post_text)

    @property
    def total_engagement(self) -> int:
        explicit = _parse_metric(self.engagement_raw)
        if explicit > 0:
            return explicit
        return self.reaction_count + self.comment_count + self.share_count + self.view_count


@dataclass(frozen=True, slots=True)
class EventObjectRow:
    event_object_id: str
    post_id: str
    unified_app_id: str
    fb_page_id: str
    page_name: str
    game_name: str
    post_time: str
    event_name: str
    estimated_start_date: str | None
    estimated_end_date: str | None
    event_description: str
    evidence_text: str
    extraction_confidence: float


@dataclass(frozen=True, slots=True)
class UnifiedMergeSourceRow:
    source_type: str
    source_id: str
    unified_app_id: str
    event_name: str
    estimated_start_date: str | None
    estimated_end_date: str | None
    event_description: str
    source_time: str
    page_name: str | None
    source_post_id: str | None
    post_text: str | None
    post_type: str | None
    hashtags: str | None
    link: str | None
    source_confidence: float
    source_authority_rank: int


@dataclass(frozen=True, slots=True)
class UnifiedMergedEventCandidate:
    candidate_id: str
    canonical_event_name: str
    event_category: str
    estimated_start_date: str | None
    estimated_end_date: str | None
    canonical_event_description: str
    anchor_source_type: str
    source_ids: list[str]
    merge_confidence: float


def _emit_progress(progress: Any, message: str) -> None:
    if progress is not None:
        progress(message)


def _format_duration_seconds(value: float) -> str:
    total_seconds = max(0, int(round(value)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def _progress_timing(processed: int, total: int, started_at: float) -> str:
    elapsed = max(0.0, time.monotonic() - started_at)
    if processed <= 0 or total <= 0:
        return f"elapsed={_format_duration_seconds(elapsed)}"
    rate = elapsed / processed
    remaining = max(0, total - processed)
    eta = remaining * rate
    return (
        f"elapsed={_format_duration_seconds(elapsed)} "
        f"eta={_format_duration_seconds(eta)}"
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(prefix: str, *parts: object) -> str:
    payload = json.dumps([prefix, *parts], ensure_ascii=True, default=str, sort_keys=False)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _truncate_text(value: str, limit: int) -> str:
    text = _normalize_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _ascii_fold_text(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )


def _campaign_name_tokens(value: str) -> list[str]:
    normalized = _ascii_fold_text(value).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    raw_tokens = [token for token in normalized.split() if token]
    return [token for token in raw_tokens if token not in CAMPAIGN_NAME_STOPWORDS]


def _campaign_name_key(value: str) -> str:
    return " ".join(_campaign_name_tokens(value))


def _description_match_key(value: str) -> str:
    normalized = _ascii_fold_text(value).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    raw_tokens = [token for token in normalized.split() if token]
    return " ".join(token for token in raw_tokens if token not in DESCRIPTION_MATCH_STOPWORDS)


def _content_hash(value: str) -> str:
    return hashlib.sha1(_normalize_text(value).lower().encode("utf-8")).hexdigest()


def _parse_metric(value: Any) -> int:
    digits = re.sub(r"\D+", "", str(value or ""))
    return int(digits) if digits else 0


def _parse_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
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


def _parse_date(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _normalize_llm_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() == "null":
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _json_array_text(values: list[Any]) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def _token_set_ratio(left: str, right: str) -> float:
    if rapidfuzz_fuzz is not None:
        return float(rapidfuzz_fuzz.token_set_ratio(left, right)) / 100.0

    left_tokens = set(_normalize_text(left).lower().split())
    right_tokens = set(_normalize_text(right).lower().split())
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return intersection / union if union else 0.0


def _date_similarity(
    start_a: str | None,
    end_a: str | None,
    start_b: str | None,
    end_b: str | None,
) -> float:
    a_start = _parse_date(start_a)
    a_end = _parse_date(end_a) or a_start
    b_start = _parse_date(start_b)
    b_end = _parse_date(end_b) or b_start

    if a_start is None or b_start is None:
        return 0.5

    if a_end is None:
        a_end = a_start
    if b_end is None:
        b_end = b_start

    if a_start <= b_end and b_start <= a_end:
        return 1.0

    if a_end < b_start:
        delta_days = (b_start - a_end).days
    else:
        delta_days = (a_start - b_end).days

    if delta_days <= 3:
        return 0.8
    if delta_days <= 14:
        return 0.5
    return 0.0


def _date_ranges_are_comparable(
    start_a: str | None,
    end_a: str | None,
    start_b: str | None,
    end_b: str | None,
) -> bool:
    if start_a is None or start_b is None:
        return True
    return _date_similarity(start_a, end_a, start_b, end_b) > 0.0


def _page_game_similarity(left: EventObjectRow, right: EventObjectRow) -> float:
    if left.game_name == right.game_name:
        return 1.0
    if left.page_name == right.page_name:
        return 0.8
    return 0.0


def _build_post_index(conn: sqlite3.Connection) -> dict[str, NormalizedFbPost]:
    rows = conn.execute(
        """
        SELECT
            raw_fb_posts.source_post_id,
            raw_fb_posts.unified_app_id,
            raw_fb_posts.fb_page_id,
            raw_fb_posts.channel_id,
            raw_fb_posts.channel_name,
            raw_fb_posts.post_type,
            raw_fb_posts.post_description,
            raw_fb_posts.link,
            raw_fb_posts.publish_time,
            raw_fb_posts.hashtag,
            raw_fb_posts.engagement,
            raw_fb_posts.reaction,
            raw_fb_posts.comment,
            raw_fb_posts.share,
            raw_fb_posts.view,
            raw_fb_posts.source_file,
            raw_fb_posts.ingested_at,
            (
                SELECT config_app_mapping.app_name
                FROM config_app_mapping
                WHERE config_app_mapping.unified_app_id = raw_fb_posts.unified_app_id
                  AND config_app_mapping.is_active = 1
                ORDER BY COALESCE(config_app_mapping.source_updated_at, '') DESC, config_app_mapping.app_name
                LIMIT 1
            ) AS mapped_game_name
        FROM raw_fb_posts
        ORDER BY raw_fb_posts.source_post_id
        """
    ).fetchall()

    posts: dict[str, NormalizedFbPost] = {}
    for row in rows:
        page_name = _normalize_text(row["channel_name"])
        posts[row["source_post_id"]] = NormalizedFbPost(
            post_id=row["source_post_id"],
            unified_app_id=_normalize_text(row["unified_app_id"]),
            fb_page_id=row["fb_page_id"],
            channel_id=row["channel_id"],
            page_name=page_name,
            game_name=_normalize_text(row["mapped_game_name"] or page_name),
            post_text=_normalize_text(row["post_description"]),
            post_time=row["publish_time"],
            hashtags=_normalize_text(row["hashtag"]),
            link=_normalize_text(row["link"]),
            post_type=_normalize_text(row["post_type"]),
            engagement_raw=_normalize_text(row["engagement"]),
            reaction_count=_parse_metric(row["reaction"]),
            comment_count=_parse_metric(row["comment"]),
            share_count=_parse_metric(row["share"]),
            view_count=_parse_metric(row["view"]),
            source_file=row["source_file"],
            ingested_at=row["ingested_at"],
        )
    return posts


def _ensure_scoped_fb_posts_mapped(
    conn: sqlite3.Connection,
    *,
    fb_page_id: str | None = None,
    game_name: str | None = None,
    page_name: str | None = None,
    unified_app_id: str | None = None,
    month: str | None = None,
    limit: int | None = None,
) -> None:
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
    conn.commit()
    posts = list(_build_post_index(conn).values())
    scoped_posts = [
        post
        for post in posts
        if _post_matches_scope(
            post,
            fb_page_id=fb_page_id,
            game_name=game_name,
            page_name=page_name,
            unified_app_id=unified_app_id,
            month=month,
        )
    ]
    if limit is not None:
        scoped_posts = scoped_posts[:limit]
    missing = [post for post in scoped_posts if not _normalize_text(post.unified_app_id)]
    if missing:
        examples = ", ".join(f"{post.post_id}:{post.fb_page_id}" for post in missing[:10])
        raise RuntimeError(
            "Some Facebook posts are missing unified_app_id mappings. "
            f"Example rows: {examples}. "
            "Every Facebook post must map to a unified_app_id before FB event processing can continue."
        )


def _build_detection_input(post: NormalizedFbPost) -> dict[str, Any]:
    return {
        "post_id": post.post_id,
        "page_name": post.page_name,
        "game_name": post.game_name,
        "post_time": post.post_time,
        "post_type": post.post_type,
        "post_text": post.post_text,
        "hashtags": post.hashtags,
        "link": post.link,
    }


def _build_extraction_input(post: NormalizedFbPost) -> dict[str, Any]:
    return _build_detection_input(post)


def _contains_facebook_event_link(post: NormalizedFbPost) -> bool:
    haystacks = [
        _normalize_text(post.link).lower(),
        _normalize_text(post.post_text).lower(),
    ]
    return any("facebook.com/events" in text or "fb.me/e/" in text for text in haystacks)


def _is_low_signal_extracted_event(
    *,
    post: NormalizedFbPost,
    event_name: str,
    event_description: str,
    evidence_text: str,
) -> bool:
    normalized_name = _normalize_text(event_name).lower()
    normalized_description = _normalize_text(event_description).lower()
    normalized_evidence = _normalize_text(evidence_text).lower()
    post_text = _normalize_text(post.post_text).lower()

    generic_name_patterns = (
        "facebook event",
        "facebook events",
        "su kien facebook",
        "sự kiện facebook",
    )
    if any(pattern in normalized_name for pattern in generic_name_patterns):
        return True

    low_signal_description_patterns = (
        "bài đăng chỉ",
        "chi chua lien ket",
        "chỉ chứa liên kết",
        "chi cung cap lien ket",
        "only links to",
        "generic cta",
        "không có nội dung",
        "khong co noi dung",
        "không có thông tin",
        "khong co thong tin",
    )
    if any(pattern in normalized_description for pattern in low_signal_description_patterns):
        return True

    if _contains_facebook_event_link(post) and not post_text:
        return True

    if _contains_facebook_event_link(post) and len(post_text) < 40 and (
        "facebook event" in normalized_name or "sự kiện" in normalized_name or "su kien" in normalized_name
    ):
        return True

    if normalized_evidence and normalized_evidence == normalized_name and (
        "facebook event" in normalized_name or "sự kiện facebook" in normalized_name or "su kien facebook" in normalized_name
    ):
        return True

    return False


def _post_matches_scope(
    post: NormalizedFbPost,
    *,
    fb_page_id: str | None = None,
    game_name: str | None = None,
    page_name: str | None = None,
    unified_app_id: str | None = None,
    month: str | None = None,
) -> bool:
    if fb_page_id and post.fb_page_id != fb_page_id:
        return False
    if game_name and post.game_name != game_name:
        return False
    if page_name and post.page_name != page_name:
        return False
    if unified_app_id and post.unified_app_id != unified_app_id:
        return False
    if month:
        source_row = UnifiedMergeSourceRow(
            source_type="fb_post",
            source_id=post.post_id,
            unified_app_id=post.unified_app_id,
            event_name=post.post_id,
            estimated_start_date=None,
            estimated_end_date=None,
            event_description=post.post_text,
            source_time=post.post_time,
            page_name=post.page_name,
            source_post_id=post.post_id,
            post_text=post.post_text,
            post_type=post.post_type,
            hashtags=post.hashtags,
            link=post.link,
            source_confidence=1.0,
            source_authority_rank=1,
        )
        if not _source_in_month_scope(source_row, month):
            return False
    return True


def _load_detection_rows(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM post_event_detection").fetchall()
    return {row["post_id"]: row for row in rows}


def _load_object_meta(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT
            post_id,
            MAX(processed_at) AS latest_processed_at,
            MIN(llm_model) AS llm_model,
            MIN(prompt_version) AS prompt_version
        FROM post_event_objects
        GROUP BY post_id
        """
    ).fetchall()
    return {row["post_id"]: row for row in rows}


def build_fb_event_detection(
    conn: sqlite3.Connection,
    *,
    client: OpenAIFbEventClient | None = None,
    fb_page_id: str | None = None,
    game_name: str | None = None,
    page_name: str | None = None,
    unified_app_id: str | None = None,
    month: str | None = None,
    limit: int | None = None,
    progress: Any | None = None,
    session_id: str | None = None,
) -> FbEventDetectionStats:
    _ensure_llm_usage_tables(conn)
    llm_client = client or OpenAIFbEventClient()
    _configure_llm_usage_recorder(conn, llm_client)
    usage_session_id = session_id or _stable_id(
        "llmsess",
        "fb-detect",
        unified_app_id or "",
        month or "",
        _utc_now_iso(),
    )
    _ensure_scoped_fb_posts_mapped(
        conn,
        fb_page_id=fb_page_id,
        game_name=game_name,
        page_name=page_name,
        unified_app_id=unified_app_id,
        month=month,
        limit=limit,
    )
    posts = _build_post_index(conn)
    existing_rows = _load_detection_rows(conn)

    processed_posts = 0
    detected_posts = 0
    processed_at = _utc_now_iso()

    scoped_posts = [
        post
        for post in posts.values()
        if _post_matches_scope(
            post,
            fb_page_id=fb_page_id,
            game_name=game_name,
            page_name=page_name,
            unified_app_id=unified_app_id,
            month=month,
        )
    ]
    if limit is not None:
        scoped_posts = scoped_posts[:limit]
    posts_to_process = [
        post
        for post in scoped_posts
        if not (
            existing_rows.get(post.post_id)
            and existing_rows[post.post_id]["post_text_hash"] == post.post_text_hash
            and existing_rows[post.post_id]["llm_model"] == llm_client.model
            and existing_rows[post.post_id]["prompt_version"] == DETECTION_PROMPT_VERSION
        )
    ]
    _emit_progress(
        progress,
        f"[fb-detect] scoped_posts={len(scoped_posts)} queued_posts={len(posts_to_process)} model={llm_client.model}",
    )

    skipped_posts = 0
    started_at = time.monotonic()
    for post in scoped_posts:
        existing = existing_rows.get(post.post_id)
        if (
            existing
            and existing["post_text_hash"] == post.post_text_hash
            and existing["llm_model"] == llm_client.model
            and existing["prompt_version"] == DETECTION_PROMPT_VERSION
        ):
            skipped_posts += 1
            continue

        with _llm_usage_context(
            llm_client,
            session_id=usage_session_id,
            unified_app_id=post.unified_app_id,
            month_bucket=month,
            stage="fb_detection",
            item_id=post.post_id,
        ):
            result = llm_client.detect_post_event(_build_detection_input(post))
        contains_event = bool(result.get("contains_event"))
        confidence = float(result.get("confidence", 0.0))
        reason = _normalize_text(result.get("reason"))
        event_signals = result.get("event_signals")
        if not isinstance(event_signals, list):
            event_signals = []

        conn.execute(
            """
            INSERT OR REPLACE INTO post_event_detection (
                post_id, unified_app_id, fb_page_id, channel_id, page_name, game_name, post_time,
                contains_event, detection_confidence, detection_reason, event_signals,
                post_text_hash, llm_model, prompt_version, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post.post_id,
                post.unified_app_id,
                post.fb_page_id,
                post.channel_id,
                post.page_name,
                post.game_name,
                post.post_time,
                1 if contains_event else 0,
                confidence,
                reason,
                _json_array_text([_normalize_text(item) for item in event_signals if _normalize_text(item)]),
                post.post_text_hash,
                llm_client.model,
                DETECTION_PROMPT_VERSION,
                processed_at,
            ),
        )
        processed_posts += 1
        if contains_event and confidence >= DETECTION_THRESHOLD:
            detected_posts += 1
        if processed_posts % CHECKPOINT_COMMIT_EVERY == 0:
            conn.commit()
        if processed_posts % PROGRESS_LOG_EVERY == 0:
            _emit_progress(
                progress,
                "[fb-detect] "
                f"processed={processed_posts}/{len(posts_to_process)} "
                f"detected={detected_posts} skipped={skipped_posts} "
                f"{_progress_timing(processed_posts, len(posts_to_process), started_at)}",
            )

    conn.commit()
    _emit_progress(
        progress,
        "[fb-detect] completed "
        f"processed={processed_posts}/{len(posts_to_process)} "
        f"detected={detected_posts} skipped={skipped_posts} "
        f"{_progress_timing(processed_posts, len(posts_to_process), started_at)}",
    )
    input_tokens, cached_input_tokens, output_tokens, total_cost_usd = _llm_usage_totals_for_session(conn, usage_session_id)
    if processed_posts or skipped_posts:
        _emit_progress(
            progress,
            "[fb-detect] usage "
            f"session_id={usage_session_id} input_tokens={input_tokens} "
            f"cached_input_tokens={cached_input_tokens} output_tokens={output_tokens} "
            f"estimated_cost_usd={total_cost_usd:.4f}",
        )
    return FbEventDetectionStats(processed_posts=processed_posts, detected_posts=detected_posts)


def _event_object_id(post_id: str, event_name: str, event_description: str) -> str:
    return _stable_id(
        "fbobj",
        post_id,
        _normalize_text(event_name).lower(),
        _content_hash(event_description),
    )


def build_fb_event_objects(
    conn: sqlite3.Connection,
    *,
    client: OpenAIFbEventClient | None = None,
    fb_page_id: str | None = None,
    game_name: str | None = None,
    page_name: str | None = None,
    limit: int | None = None,
    progress: Any | None = None,
    session_id: str | None = None,
) -> FbEventObjectStats:
    _ensure_llm_usage_tables(conn)
    llm_client = client or OpenAIFbEventClient()
    _configure_llm_usage_recorder(conn, llm_client)
    usage_session_id = session_id or _stable_id(
        "llmsess",
        "fb-extract",
        game_name or page_name or fb_page_id or "",
        _utc_now_iso(),
    )
    _ensure_scoped_fb_posts_mapped(
        conn,
        fb_page_id=fb_page_id,
        game_name=game_name,
        page_name=page_name,
        limit=limit,
    )
    posts = _build_post_index(conn)
    detection_rows = _load_detection_rows(conn)
    existing_meta = _load_object_meta(conn)
    processed_posts = 0
    extracted_objects = 0
    processed_at = _utc_now_iso()

    scoped_detection_rows: list[tuple[str, sqlite3.Row]] = []
    for post_id, detection in detection_rows.items():
        post = posts.get(post_id)
        if post is None:
            continue
        if not _post_matches_scope(post, fb_page_id=fb_page_id, game_name=game_name, page_name=page_name):
            continue
        scoped_detection_rows.append((post_id, detection))
    if limit is not None:
        scoped_detection_rows = scoped_detection_rows[:limit]
    eligible_detection_rows = [
        (post_id, detection)
        for post_id, detection in scoped_detection_rows
        if int(detection["contains_event"]) and float(detection["detection_confidence"]) >= DETECTION_THRESHOLD
    ]
    queued_detection_rows = []
    for post_id, detection in eligible_detection_rows:
        existing = existing_meta.get(post_id)
        if (
            existing
            and existing["llm_model"] == llm_client.model
            and existing["prompt_version"] == EXTRACTION_PROMPT_VERSION
            and str(existing["latest_processed_at"]) >= str(detection["processed_at"])
        ):
            continue
        queued_detection_rows.append((post_id, detection))
    _emit_progress(
        progress,
        f"[fb-extract] scoped_detection_rows={len(scoped_detection_rows)} "
        f"eligible_posts={len(eligible_detection_rows)} "
        f"queued_posts={len(queued_detection_rows)} model={llm_client.model}",
    )

    skipped_posts = 0
    started_at = time.monotonic()
    for post_id, detection in scoped_detection_rows:
        if not int(detection["contains_event"]) or float(detection["detection_confidence"]) < DETECTION_THRESHOLD:
            conn.execute("DELETE FROM post_event_objects WHERE post_id = ?", (post_id,))
            continue
        post = posts.get(post_id)
        if post is None:
            continue
        existing = existing_meta.get(post_id)
        if (
            existing
            and existing["llm_model"] == llm_client.model
            and existing["prompt_version"] == EXTRACTION_PROMPT_VERSION
            and str(existing["latest_processed_at"]) >= str(detection["processed_at"])
        ):
            skipped_posts += 1
            continue

        with _llm_usage_context(
            llm_client,
            session_id=usage_session_id,
            unified_app_id=post.unified_app_id,
            stage="fb_extraction",
            item_id=post.post_id,
        ):
            result = llm_client.extract_event_objects(_build_extraction_input(post))
        events = result.get("events")
        if not isinstance(events, list):
            events = []

        conn.execute("DELETE FROM post_event_objects WHERE post_id = ?", (post_id,))
        for item in events:
            if not isinstance(item, dict):
                continue
            event_name = _normalize_text(item.get("event_name"))
            event_description = _normalize_text(item.get("event_description"))
            evidence_text = _normalize_text(item.get("evidence_text"))
            if not event_name or not event_description or not evidence_text:
                continue
            if _is_low_signal_extracted_event(
                post=post,
                event_name=event_name,
                event_description=event_description,
                evidence_text=evidence_text,
            ):
                continue
            estimated_start_date = _normalize_llm_date(item.get("estimated_start_date"))
            estimated_end_date = _normalize_llm_date(item.get("estimated_end_date"))
            extraction_confidence = float(item.get("confidence", 0.0))
            event_object_id = _event_object_id(post_id, event_name, event_description)
            conn.execute(
                """
                INSERT OR REPLACE INTO post_event_objects (
                    event_object_id, post_id, unified_app_id, fb_page_id, page_name, game_name, post_time,
                    event_name, estimated_start_date, estimated_end_date, event_description,
                    evidence_text, extraction_confidence, llm_model, prompt_version, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_object_id,
                    post_id,
                    post.unified_app_id,
                    post.fb_page_id,
                    post.page_name,
                    post.game_name,
                    post.post_time,
                    event_name,
                    estimated_start_date,
                    estimated_end_date,
                    event_description,
                    evidence_text,
                    extraction_confidence,
                    llm_client.model,
                    EXTRACTION_PROMPT_VERSION,
                    processed_at,
                ),
            )
            extracted_objects += 1

        processed_posts += 1
        if processed_posts % CHECKPOINT_COMMIT_EVERY == 0:
            conn.commit()
        if processed_posts % PROGRESS_LOG_EVERY == 0:
            _emit_progress(
                progress,
                "[fb-extract] "
                f"processed_posts={processed_posts}/{len(queued_detection_rows)} "
                f"extracted_objects={extracted_objects} skipped={skipped_posts} "
                f"{_progress_timing(processed_posts, len(queued_detection_rows), started_at)}",
            )

    conn.commit()
    _emit_progress(
        progress,
        "[fb-extract] completed "
        f"processed_posts={processed_posts}/{len(queued_detection_rows)} "
        f"extracted_objects={extracted_objects} skipped={skipped_posts} "
        f"{_progress_timing(processed_posts, len(queued_detection_rows), started_at)}",
    )
    input_tokens, cached_input_tokens, output_tokens, total_cost_usd = _llm_usage_totals_for_session(conn, usage_session_id)
    if processed_posts or skipped_posts:
        _emit_progress(
            progress,
            "[fb-extract] usage "
            f"session_id={usage_session_id} input_tokens={input_tokens} "
            f"cached_input_tokens={cached_input_tokens} output_tokens={output_tokens} "
            f"estimated_cost_usd={total_cost_usd:.4f}",
        )
    return FbEventObjectStats(processed_posts=processed_posts, extracted_objects=extracted_objects)


def build_fb_raw_events(
    conn: sqlite3.Connection,
    *,
    client: OpenAIFbEventClient | None = None,
    fb_page_id: str | None = None,
    game_name: str | None = None,
    page_name: str | None = None,
    limit: int | None = None,
    progress: Any | None = None,
) -> FbRawEventBuildStats:
    llm_client = client or OpenAIFbEventClient()
    session_id = _stable_id("llmsess", "fb-raw-events", game_name or page_name or fb_page_id or "", _utc_now_iso())
    _emit_progress(progress, "[fb-raw-events] starting detection + extraction")
    detection_stats = build_fb_event_detection(
        conn,
        client=llm_client,
        fb_page_id=fb_page_id,
        game_name=game_name,
        page_name=page_name,
        limit=limit,
        progress=progress,
        session_id=session_id,
    )
    object_stats = build_fb_event_objects(
        conn,
        client=llm_client,
        fb_page_id=fb_page_id,
        game_name=game_name,
        page_name=page_name,
        limit=limit,
        progress=progress,
        session_id=session_id,
    )
    _emit_progress(
        progress,
        "[fb-raw-events] completed "
        f"detection_processed={detection_stats.processed_posts} "
        f"detected={detection_stats.detected_posts} "
        f"extraction_processed={object_stats.processed_posts} "
        f"extracted_objects={object_stats.extracted_objects}",
    )
    input_tokens, cached_input_tokens, output_tokens, total_cost_usd = _llm_usage_totals_for_session(conn, session_id)
    _emit_progress(
        progress,
        "[fb-raw-events] usage "
        f"session_id={session_id} input_tokens={input_tokens} "
        f"cached_input_tokens={cached_input_tokens} output_tokens={output_tokens} "
        f"estimated_cost_usd={total_cost_usd:.4f}",
    )
    return FbRawEventBuildStats(
        detection_processed_posts=detection_stats.processed_posts,
        detected_posts=detection_stats.detected_posts,
        extraction_processed_posts=object_stats.processed_posts,
        extracted_objects=object_stats.extracted_objects,
    )


def _load_event_objects(
    conn: sqlite3.Connection,
    *,
    fb_page_id: str | None = None,
    game_name: str | None = None,
    page_name: str | None = None,
) -> list[EventObjectRow]:
    rows = conn.execute(
        """
        SELECT
            event_object_id,
            post_id,
            unified_app_id,
            fb_page_id,
            page_name,
            game_name,
            post_time,
            event_name,
            estimated_start_date,
            estimated_end_date,
            event_description,
            evidence_text,
            extraction_confidence
        FROM post_event_objects
        ORDER BY post_time, event_object_id
        """
    ).fetchall()
    return [
        EventObjectRow(
            event_object_id=row["event_object_id"],
            post_id=row["post_id"],
            unified_app_id=row["unified_app_id"],
            fb_page_id=row["fb_page_id"],
            page_name=row["page_name"],
            game_name=row["game_name"],
            post_time=row["post_time"],
            event_name=row["event_name"],
            estimated_start_date=row["estimated_start_date"],
            estimated_end_date=row["estimated_end_date"],
            event_description=row["event_description"],
            evidence_text=row["evidence_text"],
            extraction_confidence=float(row["extraction_confidence"]),
        )
        for row in rows
        if (not fb_page_id or row["fb_page_id"] == fb_page_id)
        and (not game_name or row["game_name"] == game_name)
        and (not page_name or row["page_name"] == page_name)
    ]


def _group_event_objects_for_llm_merge(
    objects: list[EventObjectRow],
    *,
    max_objects_per_group: int = 40,
    max_window_days: int = 45,
) -> list[list[EventObjectRow]]:
    groups: list[list[EventObjectRow]] = []
    objects_by_game: dict[str, list[EventObjectRow]] = {}
    for row in objects:
        objects_by_game.setdefault(row.game_name, []).append(row)

    for rows in objects_by_game.values():
        rows_sorted = sorted(
            rows,
            key=lambda row: (_parse_datetime(row.post_time) or datetime.max.replace(tzinfo=timezone.utc), row.event_object_id),
        )
        current_group: list[EventObjectRow] = []
        current_group_start: datetime | None = None
        for row in rows_sorted:
            row_time = _parse_datetime(row.post_time)
            if row_time is None:
                row_time = datetime.max.replace(tzinfo=timezone.utc)
            if not current_group:
                current_group = [row]
                current_group_start = row_time
                continue
            window_days = abs((row_time - current_group_start).days) if current_group_start is not None else 0
            if len(current_group) >= max_objects_per_group or window_days > max_window_days:
                groups.append(current_group)
                current_group = [row]
                current_group_start = row_time
                continue
            current_group.append(row)
        if current_group:
            groups.append(current_group)
    return groups


def _llm_merge_event_id(
    *,
    game_name: str,
    canonical_event_name: str,
    estimated_start_date: str | None,
    first_seen_post_time: str,
) -> str:
    return _event_id(
        game_name=game_name,
        canonical_event_name=canonical_event_name,
        estimated_start_date=estimated_start_date,
        first_seen_post_time=first_seen_post_time,
    )


def build_fb_events_with_llm_merge(
    conn: sqlite3.Connection,
    *,
    client: OpenAIFbEventClient | None = None,
    fb_page_id: str | None = None,
    game_name: str | None = None,
    page_name: str | None = None,
    limit: int | None = None,
) -> FbLlmMergeStats:
    _ensure_llm_usage_tables(conn)
    llm_client = client or OpenAIFbEventClient()
    _configure_llm_usage_recorder(conn, llm_client)
    session_id = _stable_id("llmsess", "fb-legacy-merge", game_name or page_name or fb_page_id or "", _utc_now_iso())
    objects = _load_event_objects(conn, fb_page_id=fb_page_id, game_name=game_name, page_name=page_name)
    if limit is not None:
        objects = objects[:limit]
    posts = _build_post_index(conn)
    created_at = _utc_now_iso()

    selected_games = sorted({_normalize_text(row.game_name) for row in objects if _normalize_text(row.game_name)})
    if selected_games:
        placeholders = ", ".join("?" for _ in selected_games)
        conn.execute(f"DELETE FROM fb_events WHERE game_name IN ({placeholders})", tuple(selected_games))

    groups = _group_event_objects_for_llm_merge(objects)
    merged_events = 0

    for group_index, group in enumerate(groups, start=1):
        payload = {
            "merge_group_id": f"group_{group_index}",
            "game_name": group[0].game_name if group else "",
            "raw_event_objects": [
                {
                    "event_object_id": row.event_object_id,
                    "post_id": row.post_id,
                    "fb_page_id": row.fb_page_id,
                    "page_name": row.page_name,
                    "game_name": row.game_name,
                    "post_time": row.post_time,
                    "event_name": row.event_name,
                    "estimated_start_date": row.estimated_start_date,
                    "estimated_end_date": row.estimated_end_date,
                    "event_description": row.event_description,
                    "evidence_text": row.evidence_text,
                    "extraction_confidence": row.extraction_confidence,
                }
                for row in group
            ],
        }
        with _llm_usage_context(
            llm_client,
            session_id=session_id,
            stage="fb_legacy_merge",
            item_id=f"group_{group_index}",
        ):
            llm_result = llm_client.merge_event_objects(payload)
        returned_events = llm_result.get("events")
        if not isinstance(returned_events, list):
            returned_events = []

        rows_by_id = {row.event_object_id: row for row in group}
        assigned_ids: set[str] = set()

        for item in returned_events:
            if not isinstance(item, dict):
                continue
            source_ids = item.get("source_event_object_ids")
            if not isinstance(source_ids, list):
                continue
            normalized_source_ids: list[str] = []
            for source_id in source_ids:
                normalized = _normalize_text(source_id)
                if normalized and normalized in rows_by_id and normalized not in assigned_ids:
                    normalized_source_ids.append(normalized)
            if not normalized_source_ids:
                continue

            rows = [rows_by_id[source_id] for source_id in normalized_source_ids]
            rows_sorted = sorted(
                rows,
                key=lambda row: (_parse_datetime(row.post_time) or datetime.max.replace(tzinfo=timezone.utc), row.event_object_id),
            )
            canonical_event_name = _normalize_text(item.get("canonical_event_name")) or _canonical_event_name(rows_sorted)
            canonical_event_description = _normalize_text(item.get("canonical_event_description")) or _canonical_event_description(rows_sorted)
            estimated_start_date = _normalize_llm_date(item.get("estimated_start_date"))
            estimated_end_date = _normalize_llm_date(item.get("estimated_end_date"))
            if estimated_start_date is None:
                starts = [row.estimated_start_date for row in rows_sorted if row.estimated_start_date]
                estimated_start_date = min(starts) if starts else None
            if estimated_end_date is None:
                ends = [row.estimated_end_date for row in rows_sorted if row.estimated_end_date]
                estimated_end_date = max(ends) if ends else None
            first_post = rows_sorted[0]
            source_post_ids = sorted({row.post_id for row in rows_sorted})
            total_engagement = sum(posts[post_id].total_engagement for post_id in source_post_ids if post_id in posts)
            dedup_confidence = float(item.get("dedup_confidence", 0.0))
            event_id = _llm_merge_event_id(
                game_name=first_post.game_name,
                canonical_event_name=canonical_event_name,
                estimated_start_date=estimated_start_date,
                first_seen_post_time=first_post.post_time,
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO fb_events (
                    event_id, unified_app_id, canonical_event_name, estimated_start_date, estimated_end_date,
                    canonical_event_description, game_name, page_name, source_post_ids,
                    source_event_object_ids, first_seen_post_time, last_seen_post_time,
                    num_source_posts, total_engagement, dedup_confidence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    first_post.unified_app_id,
                    canonical_event_name,
                    estimated_start_date,
                    estimated_end_date,
                    canonical_event_description,
                    first_post.game_name,
                    first_post.page_name,
                    _json_array_text(source_post_ids),
                    _json_array_text(normalized_source_ids),
                    rows_sorted[0].post_time,
                    rows_sorted[-1].post_time,
                    len(source_post_ids),
                    total_engagement,
                    dedup_confidence,
                    created_at,
                    created_at,
                ),
            )
            merged_events += 1
            assigned_ids.update(normalized_source_ids)

        unassigned_rows = [rows_by_id[event_object_id] for event_object_id in rows_by_id if event_object_id not in assigned_ids]
        for row in unassigned_rows:
            event_id = _llm_merge_event_id(
                game_name=row.game_name,
                canonical_event_name=row.event_name,
                estimated_start_date=row.estimated_start_date,
                first_seen_post_time=row.post_time,
            )
            total_engagement = posts[row.post_id].total_engagement if row.post_id in posts else 0
            conn.execute(
                """
                INSERT OR REPLACE INTO fb_events (
                    event_id, unified_app_id, canonical_event_name, estimated_start_date, estimated_end_date,
                    canonical_event_description, game_name, page_name, source_post_ids,
                    source_event_object_ids, first_seen_post_time, last_seen_post_time,
                    num_source_posts, total_engagement, dedup_confidence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    row.unified_app_id,
                    row.event_name,
                    row.estimated_start_date,
                    row.estimated_end_date,
                    row.event_description,
                    row.game_name,
                    row.page_name,
                    _json_array_text([row.post_id]),
                    _json_array_text([row.event_object_id]),
                    row.post_time,
                    row.post_time,
                    1,
                    total_engagement,
                    1.0,
                    created_at,
                    created_at,
                ),
            )
            merged_events += 1

    conn.commit()
    return FbLlmMergeStats(
        merge_groups=len(groups),
        merged_events=merged_events,
        source_objects=len(objects),
    )


def _candidate_pairs(objects: list[EventObjectRow]) -> list[tuple[EventObjectRow, EventObjectRow]]:
    pairs: list[tuple[EventObjectRow, EventObjectRow]] = []
    for index, left in enumerate(objects):
        left_post_time = _parse_datetime(left.post_time)
        for right in objects[index + 1 :]:
            if left.game_name != right.game_name:
                continue
            right_post_time = _parse_datetime(right.post_time)
            if left_post_time is None or right_post_time is None:
                continue
            if abs((left_post_time - right_post_time).days) > 45:
                continue
            if not _date_ranges_are_comparable(
                left.estimated_start_date,
                left.estimated_end_date,
                right.estimated_start_date,
                right.estimated_end_date,
            ):
                continue
            pairs.append((left, right))
    return pairs


def _pair_id(left_event_object_id: str, right_event_object_id: str) -> str:
    ordered = sorted((left_event_object_id, right_event_object_id))
    return _stable_id("fbpair", ordered[0], ordered[1])


def _has_known_dates(left: EventObjectRow, right: EventObjectRow) -> bool:
    return _parse_date(left.estimated_start_date) is not None and _parse_date(right.estimated_start_date) is not None


def _rule_based_pair_decision(
    *,
    left: EventObjectRow,
    right: EventObjectRow,
    name_similarity: float,
    description_similarity: float,
    date_similarity: float,
    page_game_similarity: float,
    dedup_score: float,
) -> dict[str, Any] | None:
    left_name = _normalize_text(left.event_name).lower()
    right_name = _normalize_text(right.event_name).lower()
    same_game = left.game_name == right.game_name
    same_page = left.page_name == right.page_name
    exact_name_match = bool(left_name) and left_name == right_name

    if same_game and dedup_score >= RULE_MERGE_SCORE_THRESHOLD:
        return {
            "decision_source": "rule_merge",
            "same_event": True,
            "confidence": max(dedup_score, 0.95),
            "reason": "Deterministic merge: same game and very high weighted similarity.",
        }

    if exact_name_match and same_game and date_similarity >= 0.8:
        return {
            "decision_source": "rule_merge",
            "same_event": True,
            "confidence": max(dedup_score, 0.92),
            "reason": "Deterministic merge: exact event name match on the same game with close dates.",
        }

    if dedup_score < RULE_REJECT_SCORE_THRESHOLD:
        return {
            "decision_source": "rule_reject",
            "same_event": False,
            "confidence": max(0.90, 1.0 - dedup_score),
            "reason": "Deterministic reject: weighted similarity is below the safe merge threshold.",
        }

    if _has_known_dates(left, right) and date_similarity == 0.0 and name_similarity < 0.75:
        return {
            "decision_source": "rule_reject",
            "same_event": False,
            "confidence": 0.94,
            "reason": "Deterministic reject: dates conflict and event names are not similar enough.",
        }

    if same_page and page_game_similarity >= 0.8 and description_similarity < 0.2 and name_similarity < 0.45:
        return {
            "decision_source": "rule_reject",
            "same_event": False,
            "confidence": 0.90,
            "reason": "Deterministic reject: same page but weak name and description overlap.",
        }

    return None


def _load_cached_pair_decisions(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM fb_event_match_decisions").fetchall()
    return {row["pair_id"]: row for row in rows}


def _canonical_event_name(rows: list[EventObjectRow]) -> str:
    rows_sorted = sorted(
        rows,
        key=lambda row: (-row.extraction_confidence, len(_normalize_text(row.event_name)), _normalize_text(row.event_name).lower()),
    )
    return rows_sorted[0].event_name


def _canonical_event_description(rows: list[EventObjectRow]) -> str:
    rows_sorted = sorted(
        rows,
        key=lambda row: (-row.extraction_confidence, len(_normalize_text(row.event_description))),
    )
    return rows_sorted[0].event_description


def _event_id(
    *,
    game_name: str,
    canonical_event_name: str,
    estimated_start_date: str | None,
    first_seen_post_time: str,
) -> str:
    if estimated_start_date:
        return _stable_id("fbev", game_name, canonical_event_name, estimated_start_date)
    month_label = first_seen_post_time[:7] if len(first_seen_post_time) >= 7 else first_seen_post_time
    return _stable_id("fbev", game_name, canonical_event_name, month_label)


class _UnionFind:
    def __init__(self, nodes: list[str]) -> None:
        self.parent = {node: node for node in nodes}

    def find(self, node: str) -> str:
        parent = self.parent[node]
        if parent != node:
            self.parent[node] = self.find(parent)
        return self.parent[node]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def build_fb_events(
    conn: sqlite3.Connection,
    *,
    client: OpenAIFbEventClient | None = None,
    fb_page_id: str | None = None,
    game_name: str | None = None,
    page_name: str | None = None,
) -> FbEventBuildStats:
    _ensure_llm_usage_tables(conn)
    llm_client = client or OpenAIFbEventClient()
    _configure_llm_usage_recorder(conn, llm_client)
    session_id = _stable_id("llmsess", "fb-legacy-dedup", game_name or page_name or fb_page_id or "", _utc_now_iso())
    objects = _load_event_objects(conn, fb_page_id=fb_page_id, game_name=game_name, page_name=page_name)
    posts = _build_post_index(conn)
    cached_decisions = _load_cached_pair_decisions(conn)
    candidate_pairs = _candidate_pairs(objects)
    judged_pairs = 0
    processed_at = _utc_now_iso()
    pair_results: list[tuple[str, dict[str, Any], EventObjectRow, EventObjectRow]] = []

    for left, right in candidate_pairs:
        name_similarity = _token_set_ratio(left.event_name, right.event_name)
        description_similarity = _token_set_ratio(left.event_description, right.event_description)
        date_similarity = _date_similarity(
            left.estimated_start_date,
            left.estimated_end_date,
            right.estimated_start_date,
            right.estimated_end_date,
        )
        page_game_similarity = _page_game_similarity(left, right)
        dedup_score = (
            0.40 * name_similarity
            + 0.30 * description_similarity
            + 0.20 * date_similarity
            + 0.10 * page_game_similarity
        )

        pair_id = _pair_id(left.event_object_id, right.event_object_id)
        cached = cached_decisions.get(pair_id)
        rule_decision = _rule_based_pair_decision(
            left=left,
            right=right,
            name_similarity=name_similarity,
            description_similarity=description_similarity,
            date_similarity=date_similarity,
            page_game_similarity=page_game_similarity,
            dedup_score=dedup_score,
        )
        if rule_decision is not None:
            judge_payload = rule_decision
        elif (
            cached
            and (
                str(cached["decision_source"]) != "llm_judge"
                or (
                    cached["llm_model"] == llm_client.model
                    and cached["prompt_version"] == DEDUP_PROMPT_VERSION
                )
            )
        ):
            judge_payload = {
                "decision_source": str(cached["decision_source"]),
                "same_event": bool(int(cached["same_event"])),
                "confidence": float(cached["judge_confidence"]),
                "reason": str(cached["judge_reason"]),
            }
        else:
            with _llm_usage_context(
                llm_client,
                session_id=session_id,
                unified_app_id=left.unified_app_id,
                stage="fb_legacy_judge",
                item_id=pair_id,
            ):
                judge_payload = llm_client.judge_event_pair(
                    {
                        "event_a": {
                            "event_object_id": left.event_object_id,
                            "game_name": left.game_name,
                            "page_name": left.page_name,
                            "post_time": left.post_time,
                            "event_name": left.event_name,
                            "estimated_start_date": left.estimated_start_date,
                            "estimated_end_date": left.estimated_end_date,
                            "event_description": left.event_description,
                            "evidence_text": left.evidence_text,
                        },
                        "event_b": {
                            "event_object_id": right.event_object_id,
                            "game_name": right.game_name,
                            "page_name": right.page_name,
                            "post_time": right.post_time,
                            "event_name": right.event_name,
                            "estimated_start_date": right.estimated_start_date,
                            "estimated_end_date": right.estimated_end_date,
                            "event_description": right.event_description,
                            "evidence_text": right.evidence_text,
                        },
                        "rule_scores": {
                            "name_similarity": name_similarity,
                            "description_similarity": description_similarity,
                            "date_similarity": date_similarity,
                            "page_game_similarity": page_game_similarity,
                            "dedup_score": dedup_score,
                        },
                    }
                )
            judge_payload["decision_source"] = "llm_judge"
            judged_pairs += 1

        conn.execute(
            """
            INSERT OR REPLACE INTO fb_event_match_decisions (
                pair_id, left_event_object_id, right_event_object_id,
                name_similarity, description_similarity, date_similarity,
                page_game_similarity, dedup_score, decision_source, same_event,
                judge_confidence, judge_reason, llm_model, prompt_version, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pair_id,
                left.event_object_id,
                right.event_object_id,
                name_similarity,
                description_similarity,
                date_similarity,
                page_game_similarity,
                dedup_score,
                _normalize_text(judge_payload.get("decision_source")) or "llm_judge",
                1 if bool(judge_payload.get("same_event")) else 0,
                float(judge_payload.get("confidence", 0.0)),
                _normalize_text(judge_payload.get("reason")),
                llm_client.model if judge_payload.get("decision_source") == "llm_judge" else "",
                DEDUP_PROMPT_VERSION if judge_payload.get("decision_source") == "llm_judge" else "",
                processed_at,
            ),
        )
        pair_results.append((pair_id, judge_payload, left, right))

    conn.execute("DELETE FROM fb_events")

    if not objects:
        conn.commit()
        return FbEventBuildStats(candidate_pairs=0, judged_pairs=judged_pairs, fb_events=0)

    union_find = _UnionFind([row.event_object_id for row in objects])
    for _, judge_payload, left, right in pair_results:
        if bool(judge_payload.get("same_event")):
            union_find.union(left.event_object_id, right.event_object_id)

    clusters: dict[str, list[EventObjectRow]] = {}
    for row in objects:
        clusters.setdefault(union_find.find(row.event_object_id), []).append(row)

    positive_edges: dict[str, list[float]] = {}
    for _, judge_payload, left, right in pair_results:
        if not bool(judge_payload.get("same_event")):
            continue
        root = union_find.find(left.event_object_id)
        positive_edges.setdefault(root, []).append(float(judge_payload.get("confidence", 0.0)))

    created_at = _utc_now_iso()
    fb_events_created = 0
    for rows in clusters.values():
        rows_sorted = sorted(
            rows,
            key=lambda row: (_parse_datetime(row.post_time) or datetime.max.replace(tzinfo=timezone.utc), row.event_object_id),
        )
        canonical_event_name = _canonical_event_name(rows_sorted)
        canonical_event_description = _canonical_event_description(rows_sorted)
        nonnull_start_dates = [row.estimated_start_date for row in rows_sorted if row.estimated_start_date]
        nonnull_end_dates = [row.estimated_end_date for row in rows_sorted if row.estimated_end_date]
        estimated_start_date = min(nonnull_start_dates) if nonnull_start_dates else None
        estimated_end_date = max(nonnull_end_dates) if nonnull_end_dates else None
        first_post = rows_sorted[0]
        source_post_ids = sorted({row.post_id for row in rows_sorted})
        source_event_object_ids = sorted(row.event_object_id for row in rows_sorted)
        first_seen_post_time = rows_sorted[0].post_time
        last_seen_post_time = rows_sorted[-1].post_time
        total_engagement = sum(posts[post_id].total_engagement for post_id in source_post_ids if post_id in posts)
        cluster_confidences = positive_edges.get(union_find.find(first_post.event_object_id), [])
        dedup_confidence = (
            sum(cluster_confidences) / len(cluster_confidences)
            if cluster_confidences
            else 1.0
        )
        event_id = _event_id(
            game_name=first_post.game_name,
            canonical_event_name=canonical_event_name,
            estimated_start_date=estimated_start_date,
            first_seen_post_time=first_seen_post_time,
        )
        conn.execute(
            """
            INSERT INTO fb_events (
                event_id, unified_app_id, canonical_event_name, estimated_start_date, estimated_end_date,
                canonical_event_description, game_name, page_name, source_post_ids,
                source_event_object_ids, first_seen_post_time, last_seen_post_time,
                num_source_posts, total_engagement, dedup_confidence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                first_post.unified_app_id,
                canonical_event_name,
                estimated_start_date,
                estimated_end_date,
                canonical_event_description,
                first_post.game_name,
                first_post.page_name,
                _json_array_text(source_post_ids),
                _json_array_text(source_event_object_ids),
                first_seen_post_time,
                last_seen_post_time,
                len(source_post_ids),
                total_engagement,
                dedup_confidence,
                created_at,
                created_at,
            ),
        )
        fb_events_created += 1

    conn.commit()
    return FbEventBuildStats(
        candidate_pairs=len(candidate_pairs),
        judged_pairs=judged_pairs,
        fb_events=fb_events_created,
    )


def _month_bucket_from_values(*values: str | None) -> str:
    for value in values:
        if value and len(value) >= 7:
            return value[:7]
    return ""


def _source_time_sort_key(value: str) -> tuple[datetime, str]:
    parsed = _parse_datetime(value)
    if parsed is None:
        parsed = datetime.max.replace(tzinfo=timezone.utc)
    return parsed, value


def _month_bucket_for_source(row: UnifiedMergeSourceRow) -> str:
    return _month_bucket_from_values(row.estimated_start_date, row.estimated_end_date, row.source_time)


def _month_window(month_bucket: str) -> tuple[date, date]:
    start = date.fromisoformat(f"{month_bucket}-01")
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    return start, next_month - timedelta(days=1)


def _source_date_from_timestamp(value: str) -> date | None:
    parsed = _parse_datetime(value)
    return parsed.date() if parsed is not None else None


def _source_effective_date_range(row: UnifiedMergeSourceRow) -> tuple[date | None, date | None]:
    start_value = _parse_date(row.estimated_start_date)
    end_value = _parse_date(row.estimated_end_date)
    source_value = _source_date_from_timestamp(row.source_time)
    return start_value or source_value, end_value or start_value or source_value


def _source_in_month_scope(row: UnifiedMergeSourceRow, month_bucket: str) -> bool:
    month_start, month_end = _month_window(month_bucket)
    lookback_start = month_start - timedelta(days=14)
    source_start, source_end = _source_effective_date_range(row)
    source_time_date = _source_date_from_timestamp(row.source_time)

    if source_start and source_end and source_end >= month_start and source_start <= month_end:
        return True
    if source_time_date and lookback_start <= source_time_date < month_start:
        return True
    return False


def _derive_fb_post_event_name(post_text: str, page_name: str, post_time: str) -> str:
    raw_lines = [line.strip() for line in str(post_text or "").splitlines()]
    for line in raw_lines:
        cleaned = re.sub(r"^[^\w]+", "", line, flags=re.UNICODE).strip(" :-")
        if cleaned:
            return _truncate_text(cleaned, FB_POST_TITLE_MAX_LENGTH)
    fallback_date = post_time[:10] if len(post_time) >= 10 else "unknown-date"
    fallback_page = _normalize_text(page_name) or "Facebook"
    return _truncate_text(f"{fallback_page} post {fallback_date}", FB_POST_TITLE_MAX_LENGTH)


def _normalize_unified_event_category(value: Any) -> str:
    text = _normalize_text(value)
    return text if text in UNIFIED_EVENT_CATEGORIES else "Other"


def _normalize_remaining_fb_harvest_category(value: Any) -> str | None:
    text = _normalize_text(value)
    if not text:
        return None
    return REMAINING_FB_CATEGORY_TO_UNIFIED_CATEGORY.get(text, "Other")


def _normalize_anchor_source_type(value: Any) -> str:
    text = _normalize_text(value)
    if text in {"st_app_update_event", "st_version_event", "fb_post"}:
        return text
    return "fb_post"


def _source_authority_rank(source_type: str) -> int:
    if source_type == "st_app_update_event":
        return 3
    if source_type == "st_version_event":
        return 2
    return 1


def _merge_source_payload_item(row: UnifiedMergeSourceRow) -> dict[str, Any]:
    return {
        "source_type": row.source_type,
        "source_id": row.source_id,
        "unified_app_id": row.unified_app_id,
        "event_name": row.event_name,
        "estimated_start_date": row.estimated_start_date,
        "estimated_end_date": row.estimated_end_date,
        "event_description": row.event_description,
        "source_time": row.source_time,
        "page_name": row.page_name,
        "post_id": row.source_post_id,
        "post_text": row.post_text,
        "post_type": row.post_type,
        "hashtags": row.hashtags,
        "link": row.link,
        "source_confidence": row.source_confidence,
        "source_authority_rank": row.source_authority_rank,
    }


def _remaining_fb_harvest_payload_item(row: UnifiedMergeSourceRow) -> dict[str, Any]:
    return {
        "post_id": row.source_id,
        "publish_time": row.source_time,
        "page_name": row.page_name,
        "post_type": row.post_type,
        "post_text": row.post_text,
        "hashtags": row.hashtags,
        "link": row.link,
    }


def _candidate_payload_item(candidate: UnifiedMergedEventCandidate) -> dict[str, Any]:
    return {
        "source_type": candidate.anchor_source_type,
        "source_id": candidate.candidate_id,
        "event_name": candidate.canonical_event_name,
        "event_category": candidate.event_category,
        "estimated_start_date": candidate.estimated_start_date,
        "estimated_end_date": candidate.estimated_end_date,
        "event_description": candidate.canonical_event_description,
        "source_time": candidate.estimated_start_date or candidate.estimated_end_date or "",
        "source_confidence": candidate.merge_confidence,
        "source_authority_rank": _source_authority_rank(candidate.anchor_source_type),
        "source_ids": candidate.source_ids,
    }


def _estimate_payload_tokens(payload: dict[str, Any]) -> int:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return max(1, len(serialized) // 3)


def _chunk_unified_scope_rows(scope_items: list[UnifiedMergeSourceRow]) -> list[list[UnifiedMergeSourceRow]]:
    sorted_items = sorted(
        scope_items,
        key=lambda row: (_source_time_sort_key(row.source_time), -row.source_authority_rank, row.source_id),
    )
    chunks: list[list[UnifiedMergeSourceRow]] = []
    current_chunk: list[UnifiedMergeSourceRow] = []
    current_tokens = UNIFIED_MERGE_REQUEST_TOKEN_OVERHEAD

    for row in sorted_items:
        row_tokens = _estimate_payload_tokens(_merge_source_payload_item(row))
        if current_chunk and current_tokens + row_tokens > UNIFIED_MERGE_SAFE_INPUT_TOKEN_TARGET:
            chunks.append(current_chunk)
            current_chunk = []
            current_tokens = UNIFIED_MERGE_REQUEST_TOKEN_OVERHEAD
        current_chunk.append(row)
        current_tokens += row_tokens
    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def _unified_event_id(
    *,
    unified_app_id: str,
    canonical_event_name: str,
    estimated_start_date: str | None,
    month_bucket: str,
) -> str:
    if estimated_start_date:
        return _stable_id("uev", unified_app_id, canonical_event_name, estimated_start_date, month_bucket)
    return _stable_id("uev", unified_app_id, canonical_event_name, month_bucket)


def _unique_unified_event_id(
    *,
    used_ids: set[str],
    unified_app_id: str,
    canonical_event_name: str,
    estimated_start_date: str | None,
    month_bucket: str,
    source_ids: list[str],
) -> str:
    base_id = _unified_event_id(
        unified_app_id=unified_app_id,
        canonical_event_name=canonical_event_name,
        estimated_start_date=estimated_start_date,
        month_bucket=month_bucket,
    )
    if base_id not in used_ids:
        used_ids.add(base_id)
        return base_id

    collision_id = _stable_id(
        "uev",
        unified_app_id,
        canonical_event_name,
        estimated_start_date or month_bucket,
        sorted(source_ids),
    )
    if collision_id not in used_ids:
        used_ids.add(collision_id)
        return collision_id

    dedup_index = 2
    while True:
        fallback_id = _stable_id(
            "uev",
            unified_app_id,
            canonical_event_name,
            estimated_start_date or month_bucket,
            sorted(source_ids),
            dedup_index,
        )
        if fallback_id not in used_ids:
            used_ids.add(fallback_id)
            return fallback_id
        dedup_index += 1


def _anchor_source_type(rows: list[UnifiedMergeSourceRow]) -> str:
    source_types = {row.source_type for row in rows}
    if "st_app_update_event" in source_types:
        return "st_app_update_event"
    if "st_version_event" in source_types:
        return "st_version_event"
    return "fb_post"


def _delete_unified_scope(conn: sqlite3.Connection, *, unified_app_id: str, month_bucket: str) -> None:
    for table_name in (
        "unified_event_step3_candidate_sources",
        "unified_event_step4_harvest_candidate_sources",
        "unified_event_step5_final_candidate_sources",
    ):
        conn.execute(
            f"""
            DELETE FROM {table_name}
            WHERE run_id IN (
                SELECT run_id
                FROM unified_event_merge_runs
                WHERE unified_app_id = ?
                  AND month_bucket = ?
            )
            """,
            (unified_app_id, month_bucket),
        )
    for table_name in (
        "unified_event_step3_candidates",
        "unified_event_step4_harvest_candidates",
        "unified_event_step5_final_candidates",
    ):
        conn.execute(
            f"DELETE FROM {table_name} WHERE unified_app_id = ? AND month_bucket = ?",
            (unified_app_id, month_bucket),
        )
    conn.execute(
        """
        DELETE FROM unified_event_sources
        WHERE unified_event_id IN (
            SELECT unified_event_id
            FROM unified_events
            WHERE unified_app_id = ?
              AND month_bucket = ?
        )
        """,
        (unified_app_id, month_bucket),
    )
    conn.execute(
        "DELETE FROM unified_events WHERE unified_app_id = ? AND month_bucket = ?",
        (unified_app_id, month_bucket),
    )
    conn.execute(
        "DELETE FROM unified_event_merge_runs WHERE unified_app_id = ? AND month_bucket = ?",
        (unified_app_id, month_bucket),
    )


def _delete_unified_step5_outputs(conn: sqlite3.Connection, *, unified_app_id: str, month_bucket: str) -> None:
    conn.execute(
        """
        DELETE FROM unified_event_step5_final_candidate_sources
        WHERE run_id IN (
            SELECT run_id
            FROM unified_event_step5_final_candidates
            WHERE unified_app_id = ?
              AND month_bucket = ?
        )
        """,
        (unified_app_id, month_bucket),
    )
    conn.execute(
        "DELETE FROM unified_event_step5_final_candidates WHERE unified_app_id = ? AND month_bucket = ?",
        (unified_app_id, month_bucket),
    )
    conn.execute(
        """
        DELETE FROM unified_event_sources
        WHERE unified_event_id IN (
            SELECT unified_event_id
            FROM unified_events
            WHERE unified_app_id = ?
              AND month_bucket = ?
        )
        """,
        (unified_app_id, month_bucket),
    )
    conn.execute(
        "DELETE FROM unified_events WHERE unified_app_id = ? AND month_bucket = ?",
        (unified_app_id, month_bucket),
    )


def _ensure_unified_debug_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS unified_event_step3_candidates (
            run_id TEXT NOT NULL,
            unified_app_id TEXT NOT NULL,
            month_bucket TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            canonical_event_name TEXT NOT NULL,
            event_category TEXT NOT NULL,
            estimated_start_date TEXT,
            estimated_end_date TEXT,
            canonical_event_description TEXT NOT NULL,
            anchor_source_type TEXT NOT NULL,
            merge_confidence REAL NOT NULL,
            merge_model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, candidate_id)
        );
        CREATE INDEX IF NOT EXISTS idx_unified_event_step3_candidates_app_month
            ON unified_event_step3_candidates (unified_app_id, month_bucket);
        CREATE TABLE IF NOT EXISTS unified_event_step3_candidate_sources (
            run_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_time TEXT,
            source_post_id TEXT,
            source_confidence REAL,
            PRIMARY KEY (run_id, candidate_id, source_type, source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_unified_event_step3_candidate_sources_source
            ON unified_event_step3_candidate_sources (source_type, source_id);

        CREATE TABLE IF NOT EXISTS unified_event_step4_harvest_candidates (
            run_id TEXT NOT NULL,
            unified_app_id TEXT NOT NULL,
            month_bucket TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            canonical_event_name TEXT NOT NULL,
            event_category TEXT NOT NULL,
            estimated_start_date TEXT,
            estimated_end_date TEXT,
            canonical_event_description TEXT NOT NULL,
            anchor_source_type TEXT NOT NULL,
            merge_confidence REAL NOT NULL,
            merge_model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, candidate_id)
        );
        CREATE INDEX IF NOT EXISTS idx_unified_event_step4_harvest_candidates_app_month
            ON unified_event_step4_harvest_candidates (unified_app_id, month_bucket);
        CREATE TABLE IF NOT EXISTS unified_event_step4_harvest_candidate_sources (
            run_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_time TEXT,
            source_post_id TEXT,
            source_confidence REAL,
            PRIMARY KEY (run_id, candidate_id, source_type, source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_unified_event_step4_harvest_candidate_sources_source
            ON unified_event_step4_harvest_candidate_sources (source_type, source_id);

        CREATE TABLE IF NOT EXISTS unified_event_step5_final_candidates (
            run_id TEXT NOT NULL,
            unified_app_id TEXT NOT NULL,
            month_bucket TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            canonical_event_name TEXT NOT NULL,
            event_category TEXT NOT NULL,
            estimated_start_date TEXT,
            estimated_end_date TEXT,
            canonical_event_description TEXT NOT NULL,
            anchor_source_type TEXT NOT NULL,
            merge_confidence REAL NOT NULL,
            merge_model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, candidate_id)
        );
        CREATE INDEX IF NOT EXISTS idx_unified_event_step5_final_candidates_app_month
            ON unified_event_step5_final_candidates (unified_app_id, month_bucket);
        CREATE TABLE IF NOT EXISTS unified_event_step5_final_candidate_sources (
            run_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_time TEXT,
            source_post_id TEXT,
            source_confidence REAL,
            PRIMARY KEY (run_id, candidate_id, source_type, source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_unified_event_step5_final_candidate_sources_source
            ON unified_event_step5_final_candidate_sources (source_type, source_id);
        """
    )


def _ensure_unified_merge_run_columns(conn: sqlite3.Connection) -> None:
    existing_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(unified_event_merge_runs)").fetchall()
    }
    if "session_id" not in existing_columns:
        conn.execute("ALTER TABLE unified_event_merge_runs ADD COLUMN session_id TEXT")
    if "build_mode" not in existing_columns:
        conn.execute(
            "ALTER TABLE unified_event_merge_runs ADD COLUMN build_mode TEXT NOT NULL DEFAULT 'full'"
        )
    if "source_snapshot_run_id" not in existing_columns:
        conn.execute(
            "ALTER TABLE unified_event_merge_runs ADD COLUMN source_snapshot_run_id TEXT"
        )
    if "llm_input_tokens" not in existing_columns:
        conn.execute(
            "ALTER TABLE unified_event_merge_runs ADD COLUMN llm_input_tokens INTEGER NOT NULL DEFAULT 0"
        )
    if "llm_cached_input_tokens" not in existing_columns:
        conn.execute(
            "ALTER TABLE unified_event_merge_runs ADD COLUMN llm_cached_input_tokens INTEGER NOT NULL DEFAULT 0"
        )
    if "llm_output_tokens" not in existing_columns:
        conn.execute(
            "ALTER TABLE unified_event_merge_runs ADD COLUMN llm_output_tokens INTEGER NOT NULL DEFAULT 0"
        )
    if "llm_total_tokens" not in existing_columns:
        conn.execute(
            "ALTER TABLE unified_event_merge_runs ADD COLUMN llm_total_tokens INTEGER NOT NULL DEFAULT 0"
        )
    if "llm_total_cost_usd" not in existing_columns:
        conn.execute(
            "ALTER TABLE unified_event_merge_runs ADD COLUMN llm_total_cost_usd REAL NOT NULL DEFAULT 0"
        )


def _ensure_llm_usage_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS llm_usage_log (
            usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            run_id TEXT,
            unified_app_id TEXT,
            month_bucket TEXT,
            stage TEXT NOT NULL,
            item_id TEXT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            response_id TEXT,
            input_tokens INTEGER NOT NULL,
            cached_input_tokens INTEGER NOT NULL DEFAULT 0,
            uncached_input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL,
            input_cost_usd REAL NOT NULL DEFAULT 0,
            cached_input_cost_usd REAL NOT NULL DEFAULT 0,
            output_cost_usd REAL NOT NULL DEFAULT 0,
            total_cost_usd REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_llm_usage_log_session
            ON llm_usage_log (session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_llm_usage_log_run
            ON llm_usage_log (run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_llm_usage_log_scope
            ON llm_usage_log (unified_app_id, month_bucket, created_at);
        """
    )


def _record_llm_usage(conn: sqlite3.Connection, record: LlmUsageRecord) -> None:
    conn.execute(
        """
        INSERT INTO llm_usage_log (
            session_id, run_id, unified_app_id, month_bucket, stage, item_id,
            provider, model, prompt_version, response_id, input_tokens,
            cached_input_tokens, uncached_input_tokens, output_tokens, total_tokens,
            input_cost_usd, cached_input_cost_usd, output_cost_usd, total_cost_usd, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.session_id,
            record.run_id,
            record.unified_app_id,
            record.month_bucket,
            record.stage,
            record.item_id,
            record.provider,
            record.model,
            record.prompt_version,
            record.response_id,
            record.input_tokens,
            record.cached_input_tokens,
            record.uncached_input_tokens,
            record.output_tokens,
            record.total_tokens,
            record.input_cost_usd,
            record.cached_input_cost_usd,
            record.output_cost_usd,
            record.total_cost_usd,
            record.created_at,
        ),
    )
    if record.run_id:
        conn.execute(
            """
            UPDATE unified_event_merge_runs
            SET llm_input_tokens = llm_input_tokens + ?,
                llm_cached_input_tokens = llm_cached_input_tokens + ?,
                llm_output_tokens = llm_output_tokens + ?,
                llm_total_tokens = llm_total_tokens + ?,
                llm_total_cost_usd = llm_total_cost_usd + ?
            WHERE run_id = ?
            """,
            (
                record.input_tokens,
                record.cached_input_tokens,
                record.output_tokens,
                record.total_tokens,
                record.total_cost_usd,
                record.run_id,
            ),
        )


def _configure_llm_usage_recorder(conn: sqlite3.Connection, llm_client: OpenAIFbEventClient) -> None:
    if hasattr(llm_client, "set_usage_recorder"):
        llm_client.set_usage_recorder(lambda record: _record_llm_usage(conn, record))


@contextmanager
def _llm_usage_context(llm_client: Any, **kwargs: str | None) -> Any:
    usage_context = getattr(llm_client, "usage_context", None)
    if callable(usage_context):
        with usage_context(**kwargs):
            yield
        return
    yield


def _llm_usage_totals_for_session(conn: sqlite3.Connection, session_id: str) -> tuple[int, int, int, float]:
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(total_cost_usd), 0) AS total_cost_usd
        FROM llm_usage_log
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return 0, 0, 0, 0.0
    return int(row["input_tokens"]), int(row["cached_input_tokens"]), int(row["output_tokens"]), float(row["total_cost_usd"])


def _load_unified_merge_source_rows(
    conn: sqlite3.Connection,
    *,
    unified_app_id: str | None = None,
    month: str | None = None,
    limit_source_rows: int | None = None,
) -> list[UnifiedMergeSourceRow]:
    rows: list[UnifiedMergeSourceRow] = []

    fb_rows = conn.execute(
        """
        SELECT
            raw_fb_posts.source_post_id,
            raw_fb_posts.unified_app_id,
            raw_fb_posts.channel_name,
            raw_fb_posts.post_type,
            raw_fb_posts.post_description,
            raw_fb_posts.publish_time,
            raw_fb_posts.hashtag,
            raw_fb_posts.link
        FROM post_event_detection
        JOIN raw_fb_posts
          ON raw_fb_posts.source_post_id = post_event_detection.post_id
        WHERE COALESCE(raw_fb_posts.unified_app_id, '') <> ''
          AND post_event_detection.contains_event = 1
          AND post_event_detection.detection_confidence >= ?
        ORDER BY raw_fb_posts.unified_app_id, raw_fb_posts.publish_time, raw_fb_posts.source_post_id
        """
    , (DETECTION_THRESHOLD,)).fetchall()
    for row in fb_rows:
        post_text = _truncate_text(str(row["post_description"]), FB_POST_TEXT_MAX_LENGTH)
        page_name = _normalize_text(row["channel_name"])
        post_time = str(row["publish_time"])
        rows.append(
            UnifiedMergeSourceRow(
                source_type="fb_post",
                source_id=str(row["source_post_id"]),
                unified_app_id=str(row["unified_app_id"]),
                event_name=_derive_fb_post_event_name(post_text, page_name, post_time),
                estimated_start_date=None,
                estimated_end_date=None,
                event_description=post_text,
                source_time=post_time,
                page_name=page_name,
                source_post_id=str(row["source_post_id"]),
                post_text=post_text,
                post_type=str(row["post_type"]),
                hashtags=str(row["hashtag"]),
                link=str(row["link"]),
                source_confidence=1.0,
                source_authority_rank=1,
            )
        )

    st_update_rows = conn.execute(
        """
        SELECT
            st_app_update_events.st_update_event_id,
            st_app_update_events.unified_app_id,
            st_app_update_events.event_name,
            st_app_update_events.estimated_start_date,
            st_app_update_events.estimated_end_date,
            st_app_update_events.event_description,
            raw_st_app_update.update_time
        FROM st_app_update_events
        JOIN raw_st_app_update
          ON raw_st_app_update.source_update_id = st_app_update_events.source_row_id
        WHERE COALESCE(st_app_update_events.unified_app_id, '') <> ''
        ORDER BY st_app_update_events.unified_app_id, raw_st_app_update.update_time, st_app_update_events.st_update_event_id
        """
    ).fetchall()
    for row in st_update_rows:
        rows.append(
            UnifiedMergeSourceRow(
                source_type="st_app_update_event",
                source_id=str(row["st_update_event_id"]),
                unified_app_id=str(row["unified_app_id"]),
                event_name=str(row["event_name"]),
                estimated_start_date=row["estimated_start_date"],
                estimated_end_date=row["estimated_end_date"],
                event_description=_normalize_text(row["event_description"]),
                source_time=str(row["update_time"]),
                page_name=None,
                source_post_id=None,
                post_text=None,
                post_type=None,
                hashtags=None,
                link=None,
                source_confidence=1.0,
                source_authority_rank=3,
            )
        )

    st_version_rows = conn.execute(
        """
        SELECT
            st_version_events.st_version_event_id,
            st_version_events.unified_app_id,
            st_version_events.event_name,
            st_version_events.estimated_start_date,
            st_version_events.estimated_end_date,
            st_version_events.event_description,
            raw_st_version.version_time
        FROM st_version_events
        JOIN raw_st_version
          ON raw_st_version.source_version_id = st_version_events.source_row_id
        WHERE COALESCE(st_version_events.unified_app_id, '') <> ''
        ORDER BY st_version_events.unified_app_id, raw_st_version.version_time, st_version_events.st_version_event_id
        """
    ).fetchall()
    for row in st_version_rows:
        rows.append(
            UnifiedMergeSourceRow(
                source_type="st_version_event",
                source_id=str(row["st_version_event_id"]),
                unified_app_id=str(row["unified_app_id"]),
                event_name=str(row["event_name"]),
                estimated_start_date=row["estimated_start_date"],
                estimated_end_date=row["estimated_end_date"],
                event_description=_normalize_text(row["event_description"]),
                source_time=str(row["version_time"]),
                page_name=None,
                source_post_id=None,
                post_text=None,
                post_type=None,
                hashtags=None,
                link=None,
                source_confidence=1.0,
                source_authority_rank=2,
            )
        )

    filtered = [
        row
        for row in rows
        if (not unified_app_id or row.unified_app_id == unified_app_id)
        and (not month or _source_in_month_scope(row, month))
    ]
    filtered.sort(
        key=lambda row: (
            row.unified_app_id,
            _month_bucket_for_source(row),
            _source_time_sort_key(row.source_time),
            -row.source_authority_rank,
            row.source_id,
        )
    )
    if limit_source_rows is not None:
        filtered = filtered[:limit_source_rows]
    return filtered


def _query_rows_by_ids(
    conn: sqlite3.Connection,
    query_prefix: str,
    ids: list[str],
) -> list[sqlite3.Row]:
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    query = query_prefix.replace(":placeholders", placeholders)
    return conn.execute(query, ids).fetchall()


def _load_source_rows_by_referenced_ids(
    conn: sqlite3.Connection,
    *,
    unified_app_id: str,
    source_refs: dict[str, str],
) -> dict[str, UnifiedMergeSourceRow]:
    rows_by_id: dict[str, UnifiedMergeSourceRow] = {}

    fb_ids = sorted(source_id for source_id, source_type in source_refs.items() if source_type == "fb_post")
    for row in _query_rows_by_ids(
        conn,
        """
        SELECT
            source_post_id,
            unified_app_id,
            channel_name,
            post_type,
            post_description,
            publish_time,
            hashtag,
            link
        FROM raw_fb_posts
        WHERE source_post_id IN (:placeholders)
        """,
        fb_ids,
    ):
        post_text = _truncate_text(str(row["post_description"]), FB_POST_TEXT_MAX_LENGTH)
        page_name = _normalize_text(row["channel_name"])
        post_time = str(row["publish_time"])
        source_id = str(row["source_post_id"])
        rows_by_id[source_id] = UnifiedMergeSourceRow(
            source_type="fb_post",
            source_id=source_id,
            unified_app_id=str(row["unified_app_id"]),
            event_name=_derive_fb_post_event_name(post_text, page_name, post_time),
            estimated_start_date=None,
            estimated_end_date=None,
            event_description=post_text,
            source_time=post_time,
            page_name=page_name,
            source_post_id=source_id,
            post_text=post_text,
            post_type=str(row["post_type"]),
            hashtags=str(row["hashtag"]),
            link=str(row["link"]),
            source_confidence=1.0,
            source_authority_rank=1,
        )

    st_update_ids = sorted(
        source_id for source_id, source_type in source_refs.items() if source_type == "st_app_update_event"
    )
    for row in _query_rows_by_ids(
        conn,
        """
        SELECT
            st_app_update_events.st_update_event_id,
            st_app_update_events.unified_app_id,
            st_app_update_events.event_name,
            st_app_update_events.estimated_start_date,
            st_app_update_events.estimated_end_date,
            st_app_update_events.event_description,
            raw_st_app_update.update_time
        FROM st_app_update_events
        JOIN raw_st_app_update
          ON raw_st_app_update.source_update_id = st_app_update_events.source_row_id
        WHERE st_app_update_events.st_update_event_id IN (:placeholders)
        """,
        st_update_ids,
    ):
        source_id = str(row["st_update_event_id"])
        rows_by_id[source_id] = UnifiedMergeSourceRow(
            source_type="st_app_update_event",
            source_id=source_id,
            unified_app_id=str(row["unified_app_id"]),
            event_name=str(row["event_name"]),
            estimated_start_date=row["estimated_start_date"],
            estimated_end_date=row["estimated_end_date"],
            event_description=_normalize_text(row["event_description"]),
            source_time=str(row["update_time"]),
            page_name=None,
            source_post_id=None,
            post_text=None,
            post_type=None,
            hashtags=None,
            link=None,
            source_confidence=1.0,
            source_authority_rank=3,
        )

    st_version_ids = sorted(
        source_id for source_id, source_type in source_refs.items() if source_type == "st_version_event"
    )
    for row in _query_rows_by_ids(
        conn,
        """
        SELECT
            st_version_events.st_version_event_id,
            st_version_events.unified_app_id,
            st_version_events.event_name,
            st_version_events.estimated_start_date,
            st_version_events.estimated_end_date,
            st_version_events.event_description,
            raw_st_version.version_time
        FROM st_version_events
        JOIN raw_st_version
          ON raw_st_version.source_version_id = st_version_events.source_row_id
        WHERE st_version_events.st_version_event_id IN (:placeholders)
        """,
        st_version_ids,
    ):
        source_id = str(row["st_version_event_id"])
        rows_by_id[source_id] = UnifiedMergeSourceRow(
            source_type="st_version_event",
            source_id=source_id,
            unified_app_id=str(row["unified_app_id"]),
            event_name=str(row["event_name"]),
            estimated_start_date=row["estimated_start_date"],
            estimated_end_date=row["estimated_end_date"],
            event_description=_normalize_text(row["event_description"]),
            source_time=str(row["version_time"]),
            page_name=None,
            source_post_id=None,
            post_text=None,
            post_type=None,
            hashtags=None,
            link=None,
            source_confidence=1.0,
            source_authority_rank=2,
        )

    missing_source_ids = sorted(source_id for source_id in source_refs if source_id not in rows_by_id)
    if missing_source_ids:
        raise ValueError(
            "Missing persisted source rows for step 5 rerun: "
            + ", ".join(missing_source_ids[:10])
            + ("..." if len(missing_source_ids) > 10 else "")
        )

    for source_id, row in list(rows_by_id.items()):
        if row.unified_app_id != unified_app_id:
            raise ValueError(
                f"Persisted source row {source_id} does not belong to unified_app_id={unified_app_id}"
            )
    return rows_by_id


def _load_debug_candidates(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    unified_app_id: str,
    month_bucket: str,
    candidate_table: str,
    candidate_source_table: str,
) -> tuple[list[UnifiedMergedEventCandidate], dict[str, str]]:
    candidate_rows = conn.execute(
        f"""
        SELECT candidate_id, canonical_event_name, event_category, estimated_start_date,
               estimated_end_date, canonical_event_description, anchor_source_type, merge_confidence
        FROM {candidate_table}
        WHERE run_id = ?
          AND unified_app_id = ?
          AND month_bucket = ?
        ORDER BY candidate_id
        """,
        (run_id, unified_app_id, month_bucket),
    ).fetchall()
    if not candidate_rows:
        return [], {}

    source_rows = conn.execute(
        f"""
        SELECT candidate_id, source_type, source_id, source_time
        FROM {candidate_source_table}
        WHERE run_id = ?
        ORDER BY candidate_id, source_time, source_type, source_id
        """,
        (run_id,),
    ).fetchall()
    source_ids_by_candidate: dict[str, list[str]] = {}
    source_type_by_id: dict[str, str] = {}
    for row in source_rows:
        candidate_id = str(row["candidate_id"])
        source_id = str(row["source_id"])
        source_ids_by_candidate.setdefault(candidate_id, []).append(source_id)
        source_type_by_id[source_id] = str(row["source_type"])

    candidates: list[UnifiedMergedEventCandidate] = []
    for row in candidate_rows:
        candidate_id = str(row["candidate_id"])
        candidates.append(
            UnifiedMergedEventCandidate(
                candidate_id=candidate_id,
                canonical_event_name=str(row["canonical_event_name"]),
                event_category=str(row["event_category"]),
                estimated_start_date=row["estimated_start_date"],
                estimated_end_date=row["estimated_end_date"],
                canonical_event_description=str(row["canonical_event_description"]),
                anchor_source_type=str(row["anchor_source_type"]),
                source_ids=source_ids_by_candidate.get(candidate_id, []),
                merge_confidence=float(row["merge_confidence"]),
            )
        )
    return candidates, source_type_by_id


def _resolve_step5_source_snapshot_run_id(
    conn: sqlite3.Connection,
    *,
    unified_app_id: str,
    month_bucket: str,
    source_run_id: str | None,
) -> str:
    if source_run_id:
        exists = conn.execute(
            """
            SELECT 1
            FROM unified_event_step3_candidates
            WHERE run_id = ?
              AND unified_app_id = ?
              AND month_bucket = ?
            LIMIT 1
            """,
            (source_run_id, unified_app_id, month_bucket),
        ).fetchone()
        if exists is None:
            raise ValueError(
                f"No saved step 3 snapshot found for unified_app_id={unified_app_id} month={month_bucket} run_id={source_run_id}"
            )
        return source_run_id

    row = conn.execute(
        """
        SELECT c.run_id
        FROM unified_event_step3_candidates c
        LEFT JOIN unified_event_merge_runs r
          ON r.run_id = c.run_id
        WHERE c.unified_app_id = ?
          AND c.month_bucket = ?
        GROUP BY c.run_id
        ORDER BY MAX(COALESCE(r.started_at, c.created_at)) DESC, c.run_id DESC
        LIMIT 1
        """,
        (unified_app_id, month_bucket),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"No saved step 3 snapshot found for unified_app_id={unified_app_id} month={month_bucket}"
        )
    return str(row["run_id"])


def _parse_unified_llm_events(
    items: Any,
    rows_by_id: dict[str, UnifiedMergeSourceRow],
    discarded_items: Any = None,
) -> tuple[list[UnifiedMergedEventCandidate], set[str], set[str]]:
    if not isinstance(items, list):
        items = []

    parsed_events: list[UnifiedMergedEventCandidate] = []
    assigned_ids: set[str] = set()
    discarded_ids: set[str] = set()

    if isinstance(discarded_items, list):
        for source_id in discarded_items:
            normalized = _normalize_text(source_id)
            if normalized and normalized in rows_by_id:
                discarded_ids.add(normalized)

    for item in items:
        if not isinstance(item, dict):
            continue
        source_ids = item.get("source_ids")
        if not isinstance(source_ids, list):
            continue

        normalized_source_ids: list[str] = []
        seen_in_event: set[str] = set()
        for source_id in source_ids:
            normalized = _normalize_text(source_id)
            if (
                not normalized
                or normalized in seen_in_event
                or normalized in assigned_ids
                or normalized in discarded_ids
                or normalized not in rows_by_id
            ):
                continue
            seen_in_event.add(normalized)
            normalized_source_ids.append(normalized)
        if not normalized_source_ids:
            continue

        cluster_rows = [rows_by_id[source_id] for source_id in normalized_source_ids]
        cluster_rows.sort(key=lambda row: (_source_time_sort_key(row.source_time), -row.source_authority_rank, row.source_id))
        starts = [row.estimated_start_date for row in cluster_rows if row.estimated_start_date]
        ends = [row.estimated_end_date for row in cluster_rows if row.estimated_end_date]
        anchor_source_type = _normalize_anchor_source_type(item.get("anchor_source_type"))
        computed_anchor = _anchor_source_type(cluster_rows)
        if anchor_source_type != computed_anchor:
            anchor_source_type = computed_anchor

        parsed_events.append(
            UnifiedMergedEventCandidate(
                candidate_id=f"candidate_{len(parsed_events)}",
                canonical_event_name=_normalize_text(item.get("canonical_event_name")) or cluster_rows[0].event_name,
                event_category=_normalize_unified_event_category(item.get("event_category")),
                estimated_start_date=_normalize_llm_date(item.get("estimated_start_date")) or (min(starts) if starts else None),
                estimated_end_date=_normalize_llm_date(item.get("estimated_end_date")) or (max(ends) if ends else None),
                canonical_event_description=_normalize_text(item.get("canonical_event_description")) or cluster_rows[0].event_description,
                anchor_source_type=anchor_source_type,
                source_ids=normalized_source_ids,
                merge_confidence=float(item.get("merge_confidence", 0.0)),
            )
        )
        assigned_ids.update(normalized_source_ids)

    return parsed_events, assigned_ids, discarded_ids


def _fallback_unified_candidate(row: UnifiedMergeSourceRow, candidate_id: str) -> UnifiedMergedEventCandidate:
    return UnifiedMergedEventCandidate(
        candidate_id=candidate_id,
        canonical_event_name=row.event_name,
        event_category="Other",
        estimated_start_date=row.estimated_start_date,
        estimated_end_date=row.estimated_end_date,
        canonical_event_description=row.event_description,
        anchor_source_type=row.source_type,
        source_ids=[row.source_id],
        merge_confidence=row.source_confidence,
    )


def _merge_single_unified_chunk(
    llm_client: OpenAIFbEventClient,
    *,
    session_id: str,
    run_id: str,
    unified_app_id: str,
    month_bucket: str,
    chunk_index: int,
    scope_items: list[UnifiedMergeSourceRow],
) -> list[UnifiedMergedEventCandidate]:
    payload = {
        "unified_app_id": unified_app_id,
        "month_bucket": month_bucket,
        "source_events": [_merge_source_payload_item(row) for row in scope_items],
    }
    with _llm_usage_context(
        llm_client,
        session_id=session_id,
        run_id=run_id,
        unified_app_id=unified_app_id,
        month_bucket=month_bucket,
        stage="unified_step3_merge",
        item_id=f"chunk_{chunk_index}",
    ):
        llm_result = llm_client.merge_unified_event_sources(payload)
    rows_by_id = {row.source_id: row for row in scope_items}
    parsed_events, assigned_ids, discarded_ids = _parse_unified_llm_events(
        llm_result.get("events"),
        rows_by_id,
        llm_result.get("discarded_source_ids"),
    )

    next_index = len(parsed_events)
    for row in scope_items:
        if row.source_id in assigned_ids or row.source_id in discarded_ids:
            continue
        if row.source_type != "fb_post":
            parsed_events.append(_fallback_unified_candidate(row, f"candidate_{next_index}"))
            next_index += 1
    return parsed_events


def _consolidate_unified_candidates(
    llm_client: OpenAIFbEventClient,
    *,
    session_id: str,
    run_id: str | None,
    unified_app_id: str,
    month_bucket: str,
    candidates: list[UnifiedMergedEventCandidate],
    stage: str,
    item_id: str,
) -> list[UnifiedMergedEventCandidate]:
    payload = {
        "unified_app_id": unified_app_id,
        "month_bucket": month_bucket,
        "candidate_events": [_candidate_payload_item(candidate) for candidate in candidates],
    }
    with _llm_usage_context(
        llm_client,
        session_id=session_id,
        run_id=run_id,
        unified_app_id=unified_app_id,
        month_bucket=month_bucket,
        stage=stage,
        item_id=item_id,
    ):
        llm_result = llm_client.consolidate_unified_candidates(payload)
    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    returned_events = llm_result.get("events")
    if not isinstance(returned_events, list):
        returned_events = []

    parsed_events: list[UnifiedMergedEventCandidate] = []
    assigned_ids: set[str] = set()

    for item in returned_events:
        if not isinstance(item, dict):
            continue
        source_ids = item.get("source_ids")
        if not isinstance(source_ids, list):
            continue

        normalized_candidate_ids: list[str] = []
        seen_in_event: set[str] = set()
        for source_id in source_ids:
            normalized = _normalize_text(source_id)
            if not normalized or normalized in seen_in_event or normalized in assigned_ids or normalized not in candidates_by_id:
                continue
            seen_in_event.add(normalized)
            normalized_candidate_ids.append(normalized)
        if not normalized_candidate_ids:
            continue

        merged_candidates = [candidates_by_id[source_id] for source_id in normalized_candidate_ids]
        starts = [candidate.estimated_start_date for candidate in merged_candidates if candidate.estimated_start_date]
        ends = [candidate.estimated_end_date for candidate in merged_candidates if candidate.estimated_end_date]

        anchor_source_type = "fb_post"
        if any(candidate.anchor_source_type == "st_app_update_event" for candidate in merged_candidates):
            anchor_source_type = "st_app_update_event"
        elif any(candidate.anchor_source_type == "st_version_event" for candidate in merged_candidates):
            anchor_source_type = "st_version_event"

        flattened_source_ids: list[str] = []
        for candidate in merged_candidates:
            flattened_source_ids.extend(candidate.source_ids)

        parsed_events.append(
            UnifiedMergedEventCandidate(
                candidate_id=f"candidate_{len(parsed_events)}",
                canonical_event_name=_normalize_text(item.get("canonical_event_name")) or merged_candidates[0].canonical_event_name,
                event_category=_normalize_unified_event_category(item.get("event_category")),
                estimated_start_date=_normalize_llm_date(item.get("estimated_start_date")) or (min(starts) if starts else None),
                estimated_end_date=_normalize_llm_date(item.get("estimated_end_date")) or (max(ends) if ends else None),
                canonical_event_description=_normalize_text(item.get("canonical_event_description")) or merged_candidates[0].canonical_event_description,
                anchor_source_type=_normalize_anchor_source_type(item.get("anchor_source_type")) or anchor_source_type,
                source_ids=sorted(set(flattened_source_ids)),
                merge_confidence=float(item.get("merge_confidence", 0.0)),
            )
        )
        if parsed_events[-1].anchor_source_type != anchor_source_type:
            parsed_events[-1] = UnifiedMergedEventCandidate(
                candidate_id=parsed_events[-1].candidate_id,
                canonical_event_name=parsed_events[-1].canonical_event_name,
                event_category=parsed_events[-1].event_category,
                estimated_start_date=parsed_events[-1].estimated_start_date,
                estimated_end_date=parsed_events[-1].estimated_end_date,
                canonical_event_description=parsed_events[-1].canonical_event_description,
                anchor_source_type=anchor_source_type,
                source_ids=parsed_events[-1].source_ids,
                merge_confidence=parsed_events[-1].merge_confidence,
            )
        assigned_ids.update(normalized_candidate_ids)

    for candidate in candidates:
        if candidate.candidate_id in assigned_ids:
            continue
        parsed_events.append(
            UnifiedMergedEventCandidate(
                candidate_id=f"candidate_{len(parsed_events)}",
                canonical_event_name=candidate.canonical_event_name,
                event_category=candidate.event_category,
                estimated_start_date=candidate.estimated_start_date,
                estimated_end_date=candidate.estimated_end_date,
                canonical_event_description=candidate.canonical_event_description,
                anchor_source_type=candidate.anchor_source_type,
                source_ids=candidate.source_ids,
                merge_confidence=candidate.merge_confidence,
            )
        )

    return parsed_events


def _parse_remaining_fb_harvest_events(
    items: Any,
    *,
    row: UnifiedMergeSourceRow,
) -> list[UnifiedMergedEventCandidate]:
    if not isinstance(items, list):
        return []

    parsed_events: list[UnifiedMergedEventCandidate] = []
    seen_signatures: set[tuple[str, str | None, str | None, str]] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        canonical_event_name = _normalize_text(item.get("event_name"))
        if not canonical_event_name:
            continue
        event_category = _normalize_remaining_fb_harvest_category(item.get("category"))
        if event_category is None:
            continue
        estimated_start_date = _normalize_llm_date(item.get("estimated_start_date"))
        estimated_end_date = _normalize_llm_date(item.get("estimated_end_date"))
        canonical_event_description = _normalize_text(item.get("event_description")) or row.event_description
        signature = (
            canonical_event_name.lower(),
            estimated_start_date,
            estimated_end_date,
            event_category,
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        parsed_events.append(
            UnifiedMergedEventCandidate(
                candidate_id=f"candidate_{len(parsed_events)}",
                canonical_event_name=canonical_event_name,
                event_category=event_category,
                estimated_start_date=estimated_start_date,
                estimated_end_date=estimated_end_date,
                canonical_event_description=canonical_event_description,
                anchor_source_type="fb_post",
                source_ids=[row.source_id],
                merge_confidence=float(item.get("confidence", 0.0)),
            )
        )
    return parsed_events


def _harvest_remaining_fb_candidates(
    llm_client: OpenAIFbEventClient,
    *,
    session_id: str,
    run_id: str,
    unified_app_id: str,
    month_bucket: str,
    leftover_fb_rows: list[UnifiedMergeSourceRow],
    progress: Any | None = None,
) -> list[UnifiedMergedEventCandidate]:
    if not leftover_fb_rows:
        return []

    _emit_progress(
        progress,
        f"[unified-harvest] leftover_fb_posts={len(leftover_fb_rows)} model={llm_client.unified_merge_model}",
    )
    started_at = time.monotonic()
    harvested_candidates: list[UnifiedMergedEventCandidate] = []
    rescued_posts = 0

    for index, row in enumerate(leftover_fb_rows, start=1):
        with _llm_usage_context(
            llm_client,
            session_id=session_id,
            run_id=run_id,
            unified_app_id=unified_app_id,
            month_bucket=month_bucket,
            stage="unified_step4_harvest",
            item_id=row.source_id,
        ):
            llm_result = llm_client.harvest_remaining_fb_post_events(_remaining_fb_harvest_payload_item(row))
        parsed = _parse_remaining_fb_harvest_events(llm_result.get("events"), row=row)
        if parsed:
            rescued_posts += 1
        for candidate in parsed:
            harvested_candidates.append(
                UnifiedMergedEventCandidate(
                    candidate_id=f"candidate_{len(harvested_candidates)}",
                    canonical_event_name=candidate.canonical_event_name,
                    event_category=candidate.event_category,
                    estimated_start_date=candidate.estimated_start_date,
                    estimated_end_date=candidate.estimated_end_date,
                    canonical_event_description=candidate.canonical_event_description,
                    anchor_source_type=candidate.anchor_source_type,
                    source_ids=candidate.source_ids,
                    merge_confidence=candidate.merge_confidence,
                )
            )
        if index % REMAINING_FB_HARVEST_LOG_EVERY == 0 or index == len(leftover_fb_rows):
            _emit_progress(
                progress,
                "[unified-harvest] "
                f"processed_posts={index}/{len(leftover_fb_rows)} "
                f"rescued_posts={rescued_posts} rescued_candidates={len(harvested_candidates)} "
                f"{_progress_timing(index, len(leftover_fb_rows), started_at)}",
            )

    return harvested_candidates


def _insert_unified_event_candidates(
    conn: sqlite3.Connection,
    *,
    unified_app_id: str,
    month_bucket: str,
    processed_at: str,
    merge_model: str,
    prompt_version: str,
    candidates: list[UnifiedMergedEventCandidate],
    source_rows_by_id: dict[str, UnifiedMergeSourceRow],
    used_unified_event_ids: set[str],
) -> int:
    inserted = 0
    for candidate in candidates:
        cluster_rows = [source_rows_by_id[source_id] for source_id in candidate.source_ids if source_id in source_rows_by_id]
        starts = [row.estimated_start_date for row in cluster_rows if row.estimated_start_date]
        ends = [row.estimated_end_date for row in cluster_rows if row.estimated_end_date]
        estimated_start_date = candidate.estimated_start_date or (min(starts) if starts else None)
        estimated_end_date = candidate.estimated_end_date or (max(ends) if ends else None)
        anchor_source_type = _anchor_source_type(cluster_rows) if cluster_rows else candidate.anchor_source_type
        unified_event_id = _unique_unified_event_id(
            used_ids=used_unified_event_ids,
            unified_app_id=unified_app_id,
            canonical_event_name=candidate.canonical_event_name,
            estimated_start_date=estimated_start_date,
            month_bucket=month_bucket,
            source_ids=candidate.source_ids,
        )
        conn.execute(
            """
            INSERT INTO unified_events (
                unified_event_id, unified_app_id, month_bucket, canonical_event_name,
                event_category, estimated_start_date, estimated_end_date,
                canonical_event_description, anchor_source_type, merge_confidence,
                merge_model, prompt_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                unified_event_id,
                unified_app_id,
                month_bucket,
                candidate.canonical_event_name,
                candidate.event_category,
                estimated_start_date,
                estimated_end_date,
                candidate.canonical_event_description,
                anchor_source_type,
                candidate.merge_confidence,
                merge_model,
                prompt_version,
                processed_at,
                processed_at,
            ),
        )
        for row in cluster_rows:
            conn.execute(
                """
                INSERT INTO unified_event_sources (
                    unified_event_id, source_type, source_id, source_time, source_post_id, source_confidence
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    unified_event_id,
                    row.source_type,
                    row.source_id,
                    row.source_time,
                    row.source_post_id,
                    row.source_confidence,
                ),
            )
        inserted += 1
    return inserted


def _persist_unified_debug_candidates(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    unified_app_id: str,
    month_bucket: str,
    created_at: str,
    merge_model: str,
    prompt_version: str,
    candidates: list[UnifiedMergedEventCandidate],
    source_rows_by_id: dict[str, UnifiedMergeSourceRow],
    candidate_table: str,
    candidate_source_table: str,
) -> None:
    for candidate in candidates:
        conn.execute(
            f"""
            INSERT INTO {candidate_table} (
                run_id, unified_app_id, month_bucket, candidate_id, canonical_event_name,
                event_category, estimated_start_date, estimated_end_date,
                canonical_event_description, anchor_source_type, merge_confidence,
                merge_model, prompt_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                unified_app_id,
                month_bucket,
                candidate.candidate_id,
                candidate.canonical_event_name,
                candidate.event_category,
                candidate.estimated_start_date,
                candidate.estimated_end_date,
                candidate.canonical_event_description,
                candidate.anchor_source_type,
                candidate.merge_confidence,
                merge_model,
                prompt_version,
                created_at,
            ),
        )
        for source_id in candidate.source_ids:
            row = source_rows_by_id.get(source_id)
            if row is None:
                continue
            conn.execute(
                f"""
                INSERT INTO {candidate_source_table} (
                    run_id, candidate_id, source_type, source_id, source_time, source_post_id, source_confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    candidate.candidate_id,
                    row.source_type,
                    row.source_id,
                    row.source_time,
                    row.source_post_id,
                    row.source_confidence,
                ),
            )


def _insert_unified_event_merge_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    session_id: str | None,
    unified_app_id: str,
    month_bucket: str,
    source_row_count: int,
    model: str,
    prompt_version: str,
    started_at: str,
    build_mode: str,
    source_snapshot_run_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO unified_event_merge_runs (
            run_id, session_id, unified_app_id, month_bucket, source_row_count, merged_event_count,
            model, prompt_version, build_mode, source_snapshot_run_id,
            llm_input_tokens, llm_cached_input_tokens, llm_output_tokens, llm_total_tokens, llm_total_cost_usd,
            started_at, finished_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            session_id,
            unified_app_id,
            month_bucket,
            source_row_count,
            0,
            model,
            prompt_version,
            build_mode,
            source_snapshot_run_id,
            0,
            0,
            0,
            0,
            0.0,
            started_at,
            None,
            "running",
        ),
    )


def _finalize_unified_scope_candidates(
    conn: sqlite3.Connection,
    *,
    llm_client: OpenAIFbEventClient,
    session_id: str,
    run_id: str,
    unified_app_id: str,
    month_bucket: str,
    processed_at: str,
    candidates: list[UnifiedMergedEventCandidate],
    source_rows_by_id: dict[str, UnifiedMergeSourceRow],
    single_candidate_prompt_version: str | None = None,
    progress: Any | None = None,
) -> tuple[int, str]:
    prebucketing_input_count = len(candidates)
    final_candidates = _prebucket_unified_candidates(candidates)
    if len(final_candidates) != prebucketing_input_count:
        _emit_progress(
            progress,
            f"[unified-final-dedup] prebucket unified_app_id={unified_app_id} month={month_bucket} candidates={prebucketing_input_count}->{len(final_candidates)}",
        )

    final_prompt_version = UNIFIED_MERGE_PROMPT_VERSION
    if prebucketing_input_count > 1:
        final_prompt_version = UNIFIED_CONSOLIDATION_PROMPT_VERSION
    elif single_candidate_prompt_version:
        final_prompt_version = single_candidate_prompt_version

    if len(final_candidates) > 1:
        _emit_progress(
            progress,
            f"[unified-final-dedup] start unified_app_id={unified_app_id} month={month_bucket} candidates={len(final_candidates)}",
        )
        final_candidates = _consolidate_unified_candidates(
            llm_client,
            session_id=session_id,
            run_id=run_id,
            unified_app_id=unified_app_id,
            month_bucket=month_bucket,
            candidates=final_candidates,
            stage="unified_step5_consolidation",
            item_id="step5",
        )
        _emit_progress(
            progress,
            f"[unified-final-dedup] done unified_app_id={unified_app_id} month={month_bucket} merged_events={len(final_candidates)}",
        )

    _persist_unified_debug_candidates(
        conn,
        run_id=run_id,
        unified_app_id=unified_app_id,
        month_bucket=month_bucket,
        created_at=processed_at,
        merge_model=llm_client.unified_merge_model,
        prompt_version=final_prompt_version,
        candidates=final_candidates,
        source_rows_by_id=source_rows_by_id,
        candidate_table="unified_event_step5_final_candidates",
        candidate_source_table="unified_event_step5_final_candidate_sources",
    )

    scope_merged_events = _insert_unified_event_candidates(
        conn,
        unified_app_id=unified_app_id,
        month_bucket=month_bucket,
        processed_at=processed_at,
        merge_model=llm_client.unified_merge_model,
        prompt_version=final_prompt_version,
        candidates=final_candidates,
        source_rows_by_id=source_rows_by_id,
        used_unified_event_ids=set(),
    )
    return scope_merged_events, final_prompt_version


def _reindex_unified_candidates(candidates: list[UnifiedMergedEventCandidate]) -> list[UnifiedMergedEventCandidate]:
    return [
        UnifiedMergedEventCandidate(
            candidate_id=f"candidate_{index}",
            canonical_event_name=candidate.canonical_event_name,
            event_category=candidate.event_category,
            estimated_start_date=candidate.estimated_start_date,
            estimated_end_date=candidate.estimated_end_date,
            canonical_event_description=candidate.canonical_event_description,
            anchor_source_type=candidate.anchor_source_type,
            source_ids=candidate.source_ids,
            merge_confidence=candidate.merge_confidence,
        )
        for index, candidate in enumerate(candidates)
    ]


def _candidate_duration_score(candidate: UnifiedMergedEventCandidate) -> int:
    start_value = _parse_date(candidate.estimated_start_date)
    end_value = _parse_date(candidate.estimated_end_date) or start_value
    if start_value is None or end_value is None:
        return 0
    return max(0, (end_value - start_value).days)


def _candidate_anchor_rank(candidate: UnifiedMergedEventCandidate) -> int:
    return _source_authority_rank(candidate.anchor_source_type)


def _campaign_names_look_equivalent(left_name: str, right_name: str) -> bool:
    left_key = _campaign_name_key(left_name)
    right_key = _campaign_name_key(right_name)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    if left_key in right_key or right_key in left_key:
        return True
    similarity = _token_set_ratio(left_key, right_key)
    return similarity >= 0.82


def _campaign_name_similarity(left_name: str, right_name: str) -> float:
    left_key = _campaign_name_key(left_name)
    right_key = _campaign_name_key(right_name)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    if left_key in right_key or right_key in left_key:
        return 0.95
    return _token_set_ratio(left_key, right_key)


def _candidate_description_similarity(
    left: UnifiedMergedEventCandidate,
    right: UnifiedMergedEventCandidate,
) -> float:
    left_key = _description_match_key(left.canonical_event_description)
    right_key = _description_match_key(right.canonical_event_description)
    if not left_key or not right_key:
        return 0.0
    return _token_set_ratio(left_key, right_key)


def _should_prebucket_candidates(left: UnifiedMergedEventCandidate, right: UnifiedMergedEventCandidate) -> bool:
    if left.event_category != right.event_category:
        return False
    date_similarity = _date_similarity(
        left.estimated_start_date,
        left.estimated_end_date,
        right.estimated_start_date,
        right.estimated_end_date,
    )
    if date_similarity <= 0.0:
        return False
    if _normalize_text(left.canonical_event_name).lower() == _normalize_text(right.canonical_event_name).lower():
        return left.event_category in UMBRELLA_PREBUCKET_CATEGORIES
    if left.event_category not in CAMPAIGN_ALIAS_PREBUCKET_CATEGORIES:
        return False
    if _campaign_names_look_equivalent(left.canonical_event_name, right.canonical_event_name):
        return True

    name_similarity = _campaign_name_similarity(left.canonical_event_name, right.canonical_event_name)
    description_similarity = _candidate_description_similarity(left, right)
    if name_similarity < 0.45:
        return False
    if description_similarity >= 0.88:
        return True
    return (name_similarity + description_similarity) / 2.0 >= 0.78


def _merge_prebucket_group(candidates: list[UnifiedMergedEventCandidate]) -> UnifiedMergedEventCandidate:
    sorted_candidates = sorted(
        candidates,
        key=lambda candidate: (
            len(candidate.source_ids),
            _candidate_duration_score(candidate),
            len(_normalize_text(candidate.canonical_event_description)),
            _candidate_anchor_rank(candidate),
        ),
        reverse=True,
    )
    representative = sorted_candidates[0]
    starts = [candidate.estimated_start_date for candidate in candidates if candidate.estimated_start_date]
    ends = [candidate.estimated_end_date for candidate in candidates if candidate.estimated_end_date]
    anchor_source_type = "fb_post"
    if any(candidate.anchor_source_type == "st_app_update_event" for candidate in candidates):
        anchor_source_type = "st_app_update_event"
    elif any(candidate.anchor_source_type == "st_version_event" for candidate in candidates):
        anchor_source_type = "st_version_event"

    combined_source_ids: list[str] = []
    for candidate in candidates:
        combined_source_ids.extend(candidate.source_ids)

    return UnifiedMergedEventCandidate(
        candidate_id=representative.candidate_id,
        canonical_event_name=representative.canonical_event_name,
        event_category=representative.event_category,
        estimated_start_date=min(starts) if starts else representative.estimated_start_date,
        estimated_end_date=max(ends) if ends else representative.estimated_end_date,
        canonical_event_description=representative.canonical_event_description,
        anchor_source_type=anchor_source_type,
        source_ids=sorted(set(combined_source_ids)),
        merge_confidence=max(candidate.merge_confidence for candidate in candidates),
    )


def _prebucket_unified_candidates(candidates: list[UnifiedMergedEventCandidate]) -> list[UnifiedMergedEventCandidate]:
    if len(candidates) <= 1:
        return candidates

    buckets: list[list[UnifiedMergedEventCandidate]] = []
    for candidate in candidates:
        placed = False
        for bucket in buckets:
            if _should_prebucket_candidates(bucket[0], candidate):
                bucket.append(candidate)
                placed = True
                break
        if not placed:
            buckets.append([candidate])

    merged = [
        _merge_prebucket_group(bucket) if len(bucket) > 1 else bucket[0]
        for bucket in buckets
    ]
    return _reindex_unified_candidates(merged)


def _scope_months_for_rows(rows: list[UnifiedMergeSourceRow], requested_month: str | None) -> list[str]:
    if requested_month:
        return [requested_month]
    return sorted({_month_bucket_for_source(row) for row in rows if _month_bucket_for_source(row)})


def build_unified_events_with_llm_merge(
    conn: sqlite3.Connection,
    *,
    client: OpenAIFbEventClient | None = None,
    unified_app_id: str | None = None,
    month: str | None = None,
    limit_source_rows: int | None = None,
    progress: Any | None = None,
) -> UnifiedEventBuildStats:
    ensure_all_raw_fb_posts_mapped(conn)
    _ensure_unified_debug_tables(conn)
    _ensure_llm_usage_tables(conn)
    _ensure_unified_merge_run_columns(conn)
    llm_client = client or OpenAIFbEventClient()
    _configure_llm_usage_recorder(conn, llm_client)
    session_id = _stable_id("llmsess", "unified-full", unified_app_id or "", month or "", _utc_now_iso())
    detection_stats = build_fb_event_detection(
        conn,
        client=llm_client,
        unified_app_id=unified_app_id,
        month=month,
        progress=progress,
        session_id=session_id,
    )
    _emit_progress(
        progress,
        "[unified-merge] detection_ready "
        f"processed_posts={detection_stats.processed_posts} detected_posts={detection_stats.detected_posts}",
    )
    rows = _load_unified_merge_source_rows(
        conn,
        unified_app_id=unified_app_id,
        month=month,
        limit_source_rows=limit_source_rows,
    )
    if not rows:
        _emit_progress(progress, "[unified-merge] no source rows matched the requested scope")
        return UnifiedEventBuildStats(merge_scopes=0, source_rows=0, merged_events=0)

    scope_rows: dict[tuple[str, str], list[UnifiedMergeSourceRow]] = {}
    rows_by_app: dict[str, list[UnifiedMergeSourceRow]] = {}
    for row in rows:
        rows_by_app.setdefault(row.unified_app_id, []).append(row)
    for scope_unified_app_id, app_rows in rows_by_app.items():
        for month_bucket in _scope_months_for_rows(app_rows, month):
            scoped_items = [row for row in app_rows if _source_in_month_scope(row, month_bucket)]
            if scoped_items:
                scope_rows[(scope_unified_app_id, month_bucket)] = scoped_items
    if not scope_rows:
        _emit_progress(progress, "[unified-merge] no month scopes matched the requested scope")
        return UnifiedEventBuildStats(merge_scopes=0, source_rows=len(rows), merged_events=0)
    _emit_progress(
        progress,
        f"[unified-merge] scopes={len(scope_rows)} source_rows={len(rows)} model={llm_client.unified_merge_model}",
    )

    total_merged_events = 0

    for (scope_unified_app_id, month_bucket), scope_items in sorted(scope_rows.items()):
        processed_at = _utc_now_iso()
        _emit_progress(
            progress,
            f"[unified-merge] scope_start unified_app_id={scope_unified_app_id} month={month_bucket} source_rows={len(scope_items)}",
        )
        run_id = _stable_id("uemrun", scope_unified_app_id, month_bucket, processed_at)
        _delete_unified_scope(conn, unified_app_id=scope_unified_app_id, month_bucket=month_bucket)

        _insert_unified_event_merge_run(
            conn,
            run_id=run_id,
            session_id=session_id,
            unified_app_id=scope_unified_app_id,
            month_bucket=month_bucket,
            source_row_count=len(scope_items),
            model=llm_client.unified_merge_model,
            prompt_version=UNIFIED_MERGE_PROMPT_VERSION,
            started_at=processed_at,
            build_mode="full",
            source_snapshot_run_id=None,
        )
        try:
            chunks = _chunk_unified_scope_rows(scope_items)
            _emit_progress(
                progress,
                f"[unified-merge] scope_chunks unified_app_id={scope_unified_app_id} month={month_bucket} chunks={len(chunks)}",
            )
            chunk_candidates: list[UnifiedMergedEventCandidate] = []
            used_unified_event_ids: set[str] = set()
            for chunk_index, chunk in enumerate(chunks, start=1):
                _emit_progress(
                    progress,
                    f"[unified-merge] chunk_start unified_app_id={scope_unified_app_id} month={month_bucket} chunk={chunk_index}/{len(chunks)} source_rows={len(chunk)}",
                )
                merged_chunk = _merge_single_unified_chunk(
                    llm_client,
                    session_id=session_id,
                    run_id=run_id,
                    unified_app_id=scope_unified_app_id,
                    month_bucket=month_bucket,
                    chunk_index=chunk_index,
                    scope_items=chunk,
                )
                for candidate in merged_chunk:
                    chunk_candidates.append(
                        UnifiedMergedEventCandidate(
                            candidate_id=f"candidate_{len(chunk_candidates)}",
                            canonical_event_name=candidate.canonical_event_name,
                            event_category=candidate.event_category,
                            estimated_start_date=candidate.estimated_start_date,
                            estimated_end_date=candidate.estimated_end_date,
                            canonical_event_description=candidate.canonical_event_description,
                            anchor_source_type=candidate.anchor_source_type,
                            source_ids=candidate.source_ids,
                            merge_confidence=candidate.merge_confidence,
                        )
                    )
                _emit_progress(
                    progress,
                    f"[unified-merge] chunk_done unified_app_id={scope_unified_app_id} month={month_bucket} chunk={chunk_index}/{len(chunks)} merged_candidates={len(merged_chunk)}",
                )

            final_candidates = chunk_candidates
            if len(chunks) > 1:
                _emit_progress(
                    progress,
                    f"[unified-merge] consolidation_start unified_app_id={scope_unified_app_id} month={month_bucket} candidates={len(chunk_candidates)}",
                )
                final_candidates = _consolidate_unified_candidates(
                    llm_client,
                    session_id=session_id,
                    run_id=run_id,
                    unified_app_id=scope_unified_app_id,
                    month_bucket=month_bucket,
                    candidates=chunk_candidates,
                    stage="unified_step3_chunk_consolidation",
                    item_id="chunk_consolidation",
                )
                _emit_progress(
                    progress,
                    f"[unified-merge] consolidation_done unified_app_id={scope_unified_app_id} month={month_bucket} merged_events={len(final_candidates)}",
                )

            source_rows_by_id = {row.source_id: row for row in scope_items}
            _persist_unified_debug_candidates(
                conn,
                run_id=run_id,
                unified_app_id=scope_unified_app_id,
                month_bucket=month_bucket,
                created_at=processed_at,
                merge_model=llm_client.unified_merge_model,
                prompt_version=UNIFIED_MERGE_PROMPT_VERSION,
                candidates=final_candidates,
                source_rows_by_id=source_rows_by_id,
                candidate_table="unified_event_step3_candidates",
                candidate_source_table="unified_event_step3_candidate_sources",
            )
            assigned_fb_post_ids = {
                source_id
                for candidate in final_candidates
                for source_id in candidate.source_ids
                if source_rows_by_id.get(source_id) is not None and source_rows_by_id[source_id].source_type == "fb_post"
            }
            leftover_fb_rows = [
                row
                for row in scope_items
                if row.source_type == "fb_post" and row.source_id not in assigned_fb_post_ids
            ]
            harvested_candidates: list[UnifiedMergedEventCandidate] = []
            if leftover_fb_rows:
                _emit_progress(
                    progress,
                    f"[unified-harvest] scope_start unified_app_id={scope_unified_app_id} month={month_bucket} leftover_fb_posts={len(leftover_fb_rows)}",
                )
                harvested_candidates = _harvest_remaining_fb_candidates(
                    llm_client,
                    session_id=session_id,
                    run_id=run_id,
                    unified_app_id=scope_unified_app_id,
                    month_bucket=month_bucket,
                    leftover_fb_rows=leftover_fb_rows,
                    progress=progress,
                )
                _persist_unified_debug_candidates(
                    conn,
                    run_id=run_id,
                    unified_app_id=scope_unified_app_id,
                    month_bucket=month_bucket,
                    created_at=processed_at,
                    merge_model=llm_client.unified_merge_model,
                    prompt_version=REMAINING_FB_HARVEST_PROMPT_VERSION,
                    candidates=harvested_candidates,
                    source_rows_by_id=source_rows_by_id,
                    candidate_table="unified_event_step4_harvest_candidates",
                    candidate_source_table="unified_event_step4_harvest_candidate_sources",
                )
                _emit_progress(
                    progress,
                    f"[unified-harvest] scope_done unified_app_id={scope_unified_app_id} month={month_bucket} rescued_candidates={len(harvested_candidates)}",
                )

            all_candidates = _reindex_unified_candidates(final_candidates + harvested_candidates)
            scope_merged_events, final_prompt_version = _finalize_unified_scope_candidates(
                conn,
                llm_client=llm_client,
                session_id=session_id,
                run_id=run_id,
                unified_app_id=scope_unified_app_id,
                month_bucket=month_bucket,
                processed_at=processed_at,
                candidates=all_candidates,
                source_rows_by_id=source_rows_by_id,
                single_candidate_prompt_version=(
                    REMAINING_FB_HARVEST_PROMPT_VERSION
                    if harvested_candidates and len(all_candidates) <= 1
                    else None
                ),
                progress=progress,
            )

            conn.execute(
                """
                UPDATE unified_event_merge_runs
                SET merged_event_count = ?, prompt_version = ?, finished_at = ?, status = ?
                WHERE run_id = ?
                """,
                (scope_merged_events, final_prompt_version, _utc_now_iso(), "success", run_id),
            )
            total_merged_events += scope_merged_events
            _emit_progress(
                progress,
                f"[unified-merge] scope_done unified_app_id={scope_unified_app_id} month={month_bucket} merged_events={scope_merged_events}",
            )
        except Exception:
            conn.execute(
                """
                UPDATE unified_event_merge_runs
                SET finished_at = ?, status = ?
                WHERE run_id = ?
                """,
                (_utc_now_iso(), "failed", run_id),
            )
            conn.commit()
            raise

    conn.commit()
    _emit_progress(
        progress,
        f"[unified-merge] completed scopes={len(scope_rows)} source_rows={len(rows)} merged_events={total_merged_events}",
    )
    input_tokens, cached_input_tokens, output_tokens, total_cost_usd = _llm_usage_totals_for_session(conn, session_id)
    _emit_progress(
        progress,
        "[unified-merge] usage "
        f"session_id={session_id} input_tokens={input_tokens} "
        f"cached_input_tokens={cached_input_tokens} output_tokens={output_tokens} "
        f"estimated_cost_usd={total_cost_usd:.4f}",
    )
    return UnifiedEventBuildStats(
        merge_scopes=len(scope_rows),
        source_rows=len(rows),
        merged_events=total_merged_events,
    )


def rerun_unified_step5(
    conn: sqlite3.Connection,
    *,
    client: OpenAIFbEventClient | None = None,
    unified_app_id: str,
    month: str,
    source_run_id: str | None = None,
    progress: Any | None = None,
) -> UnifiedEventBuildStats:
    ensure_all_raw_fb_posts_mapped(conn)
    _ensure_unified_debug_tables(conn)
    _ensure_llm_usage_tables(conn)
    _ensure_unified_merge_run_columns(conn)
    llm_client = client or OpenAIFbEventClient()
    _configure_llm_usage_recorder(conn, llm_client)
    session_id = _stable_id("llmsess", "unified-step5", unified_app_id, month, _utc_now_iso())

    resolved_source_run_id = _resolve_step5_source_snapshot_run_id(
        conn,
        unified_app_id=unified_app_id,
        month_bucket=month,
        source_run_id=source_run_id,
    )
    step3_candidates, step3_source_refs = _load_debug_candidates(
        conn,
        run_id=resolved_source_run_id,
        unified_app_id=unified_app_id,
        month_bucket=month,
        candidate_table="unified_event_step3_candidates",
        candidate_source_table="unified_event_step3_candidate_sources",
    )
    if not step3_candidates:
        raise ValueError(
            f"No saved step 3 candidates found for unified_app_id={unified_app_id} month={month} run_id={resolved_source_run_id}"
        )
    step4_candidates, step4_source_refs = _load_debug_candidates(
        conn,
        run_id=resolved_source_run_id,
        unified_app_id=unified_app_id,
        month_bucket=month,
        candidate_table="unified_event_step4_harvest_candidates",
        candidate_source_table="unified_event_step4_harvest_candidate_sources",
    )
    combined_candidates = _reindex_unified_candidates(step3_candidates + step4_candidates)
    source_refs = {**step3_source_refs, **step4_source_refs}
    source_rows_by_id = _load_source_rows_by_referenced_ids(
        conn,
        unified_app_id=unified_app_id,
        source_refs=source_refs,
    )

    processed_at = _utc_now_iso()
    run_id = _stable_id("uemrun", unified_app_id, month, processed_at, "step5_only")
    _emit_progress(
        progress,
        f"[unified-step5] source_snapshot_run_id={resolved_source_run_id} unified_app_id={unified_app_id} month={month} candidates={len(combined_candidates)} source_rows={len(source_rows_by_id)}",
    )
    _delete_unified_step5_outputs(conn, unified_app_id=unified_app_id, month_bucket=month)
    _insert_unified_event_merge_run(
        conn,
        run_id=run_id,
        session_id=session_id,
        unified_app_id=unified_app_id,
        month_bucket=month,
        source_row_count=len(source_rows_by_id),
        model=llm_client.unified_merge_model,
        prompt_version=UNIFIED_CONSOLIDATION_PROMPT_VERSION,
        started_at=processed_at,
        build_mode="step5_only",
        source_snapshot_run_id=resolved_source_run_id,
    )

    try:
        merged_events, final_prompt_version = _finalize_unified_scope_candidates(
            conn,
            llm_client=llm_client,
            session_id=session_id,
            run_id=run_id,
            unified_app_id=unified_app_id,
            month_bucket=month,
            processed_at=processed_at,
            candidates=combined_candidates,
            source_rows_by_id=source_rows_by_id,
            progress=progress,
        )
        conn.execute(
            """
            UPDATE unified_event_merge_runs
            SET merged_event_count = ?, prompt_version = ?, finished_at = ?, status = ?
            WHERE run_id = ?
            """,
            (merged_events, final_prompt_version, _utc_now_iso(), "success", run_id),
        )
        conn.commit()
    except Exception:
        conn.execute(
            """
            UPDATE unified_event_merge_runs
            SET finished_at = ?, status = ?
            WHERE run_id = ?
            """,
            (_utc_now_iso(), "failed", run_id),
        )
        conn.commit()
        raise

    input_tokens, cached_input_tokens, output_tokens, total_cost_usd = _llm_usage_totals_for_session(conn, session_id)
    _emit_progress(
        progress,
        "[unified-step5] usage "
        f"session_id={session_id} input_tokens={input_tokens} "
        f"cached_input_tokens={cached_input_tokens} output_tokens={output_tokens} "
        f"estimated_cost_usd={total_cost_usd:.4f}",
    )

    return UnifiedEventBuildStats(merge_scopes=1, source_rows=len(source_rows_by_id), merged_events=merged_events)


def preview_fb_event_dedup(
    conn: sqlite3.Connection,
    *,
    fb_page_id: str | None = None,
    game_name: str | None = None,
    page_name: str | None = None,
) -> FbEventDecisionPreviewStats:
    objects = _load_event_objects(conn, fb_page_id=fb_page_id, game_name=game_name, page_name=page_name)
    candidate_pairs = _candidate_pairs(objects)
    rule_merge_pairs = 0
    rule_reject_pairs = 0
    llm_judge_pairs = 0

    for left, right in candidate_pairs:
        name_similarity = _token_set_ratio(left.event_name, right.event_name)
        description_similarity = _token_set_ratio(left.event_description, right.event_description)
        date_similarity = _date_similarity(
            left.estimated_start_date,
            left.estimated_end_date,
            right.estimated_start_date,
            right.estimated_end_date,
        )
        page_game_similarity = _page_game_similarity(left, right)
        dedup_score = (
            0.40 * name_similarity
            + 0.30 * description_similarity
            + 0.20 * date_similarity
            + 0.10 * page_game_similarity
        )
        decision = _rule_based_pair_decision(
            left=left,
            right=right,
            name_similarity=name_similarity,
            description_similarity=description_similarity,
            date_similarity=date_similarity,
            page_game_similarity=page_game_similarity,
            dedup_score=dedup_score,
        )
        if decision is None:
            llm_judge_pairs += 1
        elif bool(decision.get("same_event")):
            rule_merge_pairs += 1
        else:
            rule_reject_pairs += 1

    return FbEventDecisionPreviewStats(
        candidate_pairs=len(candidate_pairs),
        rule_merge_pairs=rule_merge_pairs,
        rule_reject_pairs=rule_reject_pairs,
        llm_judge_pairs=llm_judge_pairs,
    )
