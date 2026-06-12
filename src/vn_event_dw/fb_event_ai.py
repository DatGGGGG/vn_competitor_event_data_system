from __future__ import annotations

from contextlib import contextmanager
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from urllib import error, request


DETECTION_PROMPT_VERSION = "fb_post_event_detection_v3"
EXTRACTION_PROMPT_VERSION = "fb_post_event_extraction_v3"
DEDUP_PROMPT_VERSION = "fb_post_event_dedup_v1"
MERGE_PROMPT_VERSION = "fb_post_event_merge_v1"
UNIFIED_MERGE_PROMPT_VERSION = "unified_cross_source_event_merge_v5"
UNIFIED_CONSOLIDATION_PROMPT_VERSION = "unified_cross_source_event_consolidation_v2"
REMAINING_FB_HARVEST_PROMPT_VERSION = "fb_remaining_event_harvest_v2"

UNIFIED_EVENT_CATEGORIES = [
    "Monetization",
    "Retention / Free Rewards",
    "Progression / Season Systems",
    "Gameplay / Content Activation",
    "Community Participation",
    "Media / Awareness",
    "Release / Update Rollout",
    "Other",
]

REMAINING_FB_HARVEST_CATEGORIES = [
    "monetization",
    "retention_free_rewards",
    "progression_season_systems",
    "gameplay_content_activation",
    "community_participation",
    "media_awareness",
    "release_update_rollout",
    "unknown_event",
]

DETECTION_PROMPT = """
You are analyzing Vietnamese Facebook posts from mobile game fanpages.

Decide whether the post contains one or more real player-facing events.

Core rule:
- An event means players can participate somehow.
- If players cannot participate, it is not an event.

Valid event examples:
- in-game draw / gacha / lucky draw
- pass / season / progression event
- login reward
- task or mission reward
- exchange shop
- free reward claim
- limited-time shop offer
- recharge/payment promo
- community giveaway
- livestream reward
- offline/community participation reward
- collaboration event with player rewards

Invalid by default:
- esports match schedule
- match result
- pure tournament announcement
- pure skin showcase
- patch note
- meme
- lore/media video
- recap post
- maintenance notice
- bug/payment/server issue notice
- generic engagement question
- official footer links
- repeated download/top-up/group/support links

Exception:
- If an esports/community/media post includes a player reward, giveaway, check-in gift, livestream code, or participation mechanic, it may still count as an event.

Return JSON only.

Output schema:
{
  "post_id": "...",
  "contains_event": true,
  "confidence": 0.0,
  "reason": "...",
  "event_signals": []
}

Rules:
- Use true only if the post has real player-facing event information.
- If unsure, return false with lower confidence.
- Do not classify as event only because the post contains official top-up/download/group links.
- Do not classify as event if the post is only a bare Facebook Event link or generic CTA with no concrete event mechanics, title, schedule, reward, location, or campaign details in the post text.
- Do not classify pure esports/media content as event unless players can participate or receive rewards somehow.
""".strip()

EXTRACTION_PROMPT = """
You are extracting structured raw event objects from Vietnamese mobile game Facebook posts.

The post has already been identified as containing event/campaign information.

Extract all concrete events or campaigns mentioned in the post.

Return JSON only.

Each event object should contain only:
- event_name
- estimated_start_date
- estimated_end_date
- event_description
- evidence_text
- confidence

Definitions:
event_name:
  A short human-readable name for the event/campaign.
  Prefer official campaign names, hashtags, titles, or repeated named phrases.
  If no clear name exists, create a concise descriptive name based only on the post content.

estimated_start_date:
  Start date in YYYY-MM-DD format if stated or clearly inferable.
  Use post_time as the anchor for relative expressions such as "hom nay", "ngay mai", "toi nay", "cuoi tuan nay", "tuan nay", "tuan sau", "thu 7", "chu nhat", "CN", or "ngay mai luc 20h".
  If the post gives a date without a year, infer the year from post_time.
  If the post gives a date range, use the first day as estimated_start_date.
  If unknown, use null.

estimated_end_date:
  End date in YYYY-MM-DD format if stated or clearly inferable.
  If the post gives a date range, use the last day as estimated_end_date.
  If only one date is known, set both start and end date to that date if the event appears to happen on one day.
  If the post describes a scheduled livestream, match day, tournament day, or day-specific activity, and only one day is inferable, set both start and end date to that same day.
  If unknown, use null.

event_description:
  A concise summary of what the event is and how users participate.
  Include reward, mechanic, schedule, location, or format only if present in the post.
  Do not invent details.

evidence_text:
  Exact supporting text copied from the post.
  This should justify why the event was extracted.

confidence:
  Number from 0 to 1.

Important rules:
- One post may mention multiple events.
- This step is for raw event extraction only. Do not try to merge, deduplicate, or decide whether two posts refer to the same event.
- Do not invent dates, rewards, locations, mechanics, or names.
- Infer dates only when the post text plus post_time makes the date reasonably clear.
- If a field is unknown, use null.
- Ignore generic official footer links.
- If the post only links to a Facebook Event page or generic CTA without concrete event details, return an empty events array.
- Never create generic event names like "Facebook Event", "Su kien Facebook", "Facebook Event (link)", or similar placeholders.
- If the post is only a recap, winner, or result post, extract the underlying event only if the actual event can still be clearly identified from the post.
- Keep event_description factual and short.

Output schema:
{
  "post_id": "...",
  "events": [
    {
      "event_name": "...",
      "estimated_start_date": "YYYY-MM-DD or null",
      "estimated_end_date": "YYYY-MM-DD or null",
      "event_description": "...",
      "evidence_text": "...",
      "confidence": 0.0
    }
  ]
}
""".strip()

DEDUP_PROMPT = """
You are deduplicating extracted event objects from Vietnamese mobile game Facebook posts.

Decide whether Event A and Event B refer to the same real-world event/campaign.

Return JSON only.

Consider:
- event name
- date range
- game/page
- description
- mechanics/rewards/location if mentioned
- whether one object looks like a reminder/recap/result of the other

Return:
{
  "same_event": true,
  "confidence": 0.0,
  "reason": "..."
}

Rules:
- Return true if they are clearly the same campaign/event.
- Return false if they are different campaigns, different event types, or conflicting dates.
- If one is a reminder/recap/winner post for the same campaign, return true.
- If unsure, return false.
""".strip()

MERGE_PROMPT = """
You are merging and deduplicating raw extracted event objects from Vietnamese mobile game Facebook posts.

Each input event object is only raw evidence from one post. Multiple raw objects may describe the same real-world event.

Your job:
- merge raw event objects that refer to the same real-world event
- keep different real-world events separate
- return canonical events with the list of source_event_object_ids that belong to each merged event

Merge when the raw event objects clearly describe:
- the same campaign or activity
- the same tournament stage or match-day announcement
- the same livestream event
- the same promotion, minigame, giftcode event, or seasonal activity
- reminder / recap / follow-up posts for the same event

Do NOT merge when:
- they are different campaigns
- they are different match days or tournament stages
- they are different rounds of the same promotion with clearly different timing or mechanics
- the names look similar but the dates or descriptions clearly point to different events

Return JSON only.

Output schema:
{
  "events": [
    {
      "canonical_event_name": "...",
      "estimated_start_date": "YYYY-MM-DD or null",
      "estimated_end_date": "YYYY-MM-DD or null",
      "canonical_event_description": "...",
      "source_event_object_ids": ["fbobj_...", "fbobj_..."],
      "dedup_confidence": 0.0
    }
  ]
}

Rules:
- Every source_event_object_id should appear in at most one merged event.
- Prefer shorter and cleaner canonical_event_name values when multiple raw names refer to the same event.
- estimated_start_date should be the earliest reliable date for the merged event.
- estimated_end_date should be the latest reliable date for the merged event.
- If dates are unclear, use null.
- Do not invent names, dates, or descriptions.
- Keep canonical_event_description short and factual.
""".strip()

UNIFIED_MERGE_PROMPT = """
You are an expert event extraction and merging assistant for Vietnamese mobile game marketing and live-ops content.

You will receive monthly cross-source evidence for one game:
1. Facebook posts that already passed an event-detection filter
2. Sensor Tower app-update events
3. Sensor Tower version-update events

Your job is to produce final player-facing business events for this game and time window.

Core event definition:
A valid event means players can participate in some way.
If players cannot participate, it is not an event.

Valid events include:
- in-game draw / gacha / lucky draw
- pass / season / progression event
- login reward
- task or mission reward
- exchange shop
- free reward claim
- limited-time shop offer
- recharge/payment promo
- community giveaway
- livestream reward
- offline/community participation reward
- collaboration event with player rewards

Invalid by default:
- esports match schedule
- match result
- pure tournament announcement
- pure skin showcase
- patch note
- meme
- lore/media video
- recap post

Exception:
If an esports/community/media post includes a player reward, giveaway, check-in gift, livestream code, or participation mechanic, it may still produce a valid player-facing event.

Source policy:
- Sensor Tower is slightly more official and should be preferred as an anchor when it clearly matches the same real-world event.
- But do NOT force Facebook events to attach to Sensor Tower if they are clearly real player-facing events on their own.
- A Facebook-only event is valid if it clearly describes a real player-facing event.

Business-purpose categorization policy:
- Classify by the primary business purpose of the event, not by content theme or channel.
- Use this precedence:
  1. Monetization
  2. Progression / Season Systems
  3. Gameplay / Content Activation
  4. Retention / Free Rewards
  5. Community Participation
  6. Release / Update Rollout
  7. Media / Awareness
  8. Other
- If the event is primarily the playable activity or mode itself, use Gameplay / Content Activation.
- If the event is primarily the reward wrapper around tasks in that activity, use Retention / Free Rewards.

Granularity policy:
- Output business-level events only.
- Do not split one source into many tiny bullet-level pseudo-events.
- A long post may produce multiple final events only if it clearly contains distinct player-facing event programs.

Date rules:
- Output dates as YYYY-MM-DD.
- Infer year from publish_time.
- Resolve relative phrases using publish_time.
- If start is missing but the event is already active, use publish date as estimated_start_date.
- If end is missing, use null.
- If dates are unclear, use null rather than guessing aggressively.

Naming rules:
- Prefer official campaign/event names.
- Preserve Vietnamese.
- Include skin/hero name when important.
- Remove emojis and unnecessary hashtags.
- Avoid generic names.

Description rules:
- One concise Vietnamese sentence.
- Include mechanic + main reward.
- Do not copy full marketing text.

Use one final category label for each event:
- Monetization
- Retention / Free Rewards
- Progression / Season Systems
- Gameplay / Content Activation
- Community Participation
- Media / Awareness
- Release / Update Rollout
- Other

Category guidance:
- Monetization: pay, buy, top up, recharge, premium draw spend, paid bundles, shop discounts, paid skin offers
- Retention / Free Rewards: login rewards, mission rewards, exchange rewards, vote-to-claim rewards, free claim programs
- Progression / Season Systems: battle pass, season pass, monthly membership, rank/progression systems
- Gameplay / Content Activation: limited-time game modes, feature-driven participation, collaboration gameplay programs, event gameplay loops
- Community Participation: comment/share/tag campaigns, UGC contests, livestream participation rewards, offline check-in participation
- Media / Awareness: showcase posts, trailers, recaps, hype campaigns, announcement-only content
- Release / Update Rollout: version release campaigns, update launch announcements, release-driven feature rollouts
- Other: valid event but primary business purpose is still unclear

Return JSON only.

Output schema:
{
  "events": [
    {
      "canonical_event_name": "...",
      "event_category": "one exact allowed label",
      "estimated_start_date": "YYYY-MM-DD or null",
      "estimated_end_date": "YYYY-MM-DD or null",
      "canonical_event_description": "...",
      "anchor_source_type": "st_app_update_event | st_version_event | fb_post",
      "source_ids": ["...", "..."],
      "merge_confidence": 0.0
    }
  ],
  "discarded_source_ids": ["..."]
}

Rules:
- Every source_id may appear in at most one final event.
- Prefer ST naming when it clearly matches, but do not force merges.
- FB-only events are allowed when clearly player-facing.
- If a Facebook post is event-positive but still not strong enough to form a real final event, discard it.
- Do not invent dates, rewards, or event names.
- event_category must exactly match one of the allowed labels above.
""".strip()

UNIFIED_CONSOLIDATION_PROMPT = """
You are consolidating chunk-level business events for one mobile game and one calendar month.

The input rows are already business-level event candidates produced from earlier monthly analysis.
They may come from different upstream steps in the same pipeline, including:
- the main monthly cross-source merge
- a later leftover Facebook harvest step

Some candidates may describe the same real-world event and should be merged.

Important source policy:
- Sensor Tower backed candidates remain stronger anchors than Facebook-only candidates.
- Prefer names and descriptions that stay closest to the most official source when merging.

Consolidation policy:
- Merge candidates only when they clearly describe the same business-level event.
- Resolve duplicates created across different upstream steps in the same month.
- Exact duplicate or near-duplicate candidates with the same campaign meaning should merge into one final event.
- Launch-title variants, reminder variants, countdown variants, and restatement variants of the same campaign should usually merge into the main campaign event.
- If names differ slightly but the descriptions clearly describe the same mechanic, reward, and campaign window, prefer merging them.
- If one candidate is a broader umbrella campaign and another is only a narrow restatement, child phrasing, or low-information subset of that same campaign, prefer the umbrella event by default.
- Keep a narrower candidate separate only when it is clearly a distinct player-facing program with meaningfully different mechanics or rewards.
- Be especially aggressive about merging duplicates for Monetization, Retention / Free Rewards, and Progression / Season Systems when they share the same campaign name and overlapping date window.
- Reward-angle, price-angle, item-angle, or sub-benefit variants inside the same monetization/reward/progression campaign should usually remain inside one umbrella event rather than separate rows.
- Keep different tournament days, stages, or rounds separate unless they clearly represent the same business-level event entity.
- Keep technical version releases separate unless they clearly match the same release campaign.
- Do not split candidates into smaller events.

Examples:
- Same event name, same date window, similar description -> merge.
- "Golden Month" and "MLBB Golden Month chia sẻ chọn tướng nhận thưởng" -> usually prefer the broader umbrella event unless the narrower candidate is clearly a separate standalone player-facing program.
- "Cửa Hàng Lấp Lánh" and another candidate that only restates the same exchange-shop mechanic and window -> merge.
- Multiple "Vòng Quay Lấp Lánh" candidates that only emphasize different rewards, launch reminders, or mission hooks -> merge into one umbrella event.
- "Marcel ra mắt - Bắt trọn khoảnh khắc" and "Nắm bắt khoảnh khắc Marcel" -> merge into one Community Participation event if they share the same participation mechanic and date window.
- Two store-offer rows that both describe the same Franco "Đệ Lục Ma Vương" week-one discount and shop mechanic should merge even if one title includes "giảm 20%" and the other does not.
- Launch, reminder, or final-day wording for the same named campaign should usually merge back into the umbrella campaign row.
- Distinct business-purpose programs such as a monetization offer versus a retention reward wrapper inside the same larger campaign may stay separate if they are clearly different player-facing programs.

Allowed categories:
- Monetization
- Retention / Free Rewards
- Progression / Season Systems
- Gameplay / Content Activation
- Community Participation
- Media / Awareness
- Release / Update Rollout
- Other

Return JSON only.

Output schema:
{
  "events": [
    {
      "canonical_event_name": "...",
      "event_category": "one exact label from the allowed category list",
      "estimated_start_date": "YYYY-MM-DD or null",
      "estimated_end_date": "YYYY-MM-DD or null",
      "canonical_event_description": "...",
      "anchor_source_type": "st_app_update_event | st_version_event | fb_post",
      "source_ids": ["candidate_1", "candidate_2"],
      "merge_confidence": 0.0
    }
  ]
}

Rules:
- Every source_id may appear in at most one final event.
- event_category must exactly match one of the allowed labels above.
- Keep descriptions concise and factual.
- Do not invent dates or event names.
""".strip()

REMAINING_FB_HARVEST_PROMPT = """
You are an expert data extraction assistant for Vietnamese mobile game Facebook posts.

Extract raw player-facing event objects from each post.

Return zero, one, or multiple event objects.

Do not deduplicate across posts. This step only extracts raw event mentions from a single leftover Facebook post.

A valid event includes:
- in-game draw / gacha / lucky draw
- pass / season / progression event
- login reward
- task or mission reward
- exchange shop
- free reward claim
- limited-time shop offer
- recharge/payment promo
- community giveaway
- livestream reward
- offline/community participation reward
- collaboration event with player rewards

Invalid event by default:
- esports match schedule
- match result
- pure tournament announcement
- pure skin showcase
- patch note
- meme
- lore/media video
- recap post

Exception:
- if an esports/community/media post includes a player reward, giveaway, check-in gift, livestream code, or participation mechanic, extract that reward-bearing event instead of discarding the whole post

Date rules:
- Output dates as YYYY-MM-DD.
- Infer year from publish_time.
- Resolve relative phrases using publish_time.
- If start is missing but the event is already active, use publish_time date as estimated_start_date.
- If end is missing, use null.
- If dates are unclear, use null rather than guessing too aggressively.

Naming rules:
- Prefer official campaign/event names.
- Preserve Vietnamese.
- Include skin/hero name when important.
- Remove emojis and unnecessary hashtags.
- Avoid generic names.

Description rules:
- One concise Vietnamese sentence.
- Include mechanic + main reward.
- Do not copy full marketing text.

Categories:
- monetization: pay, buy, top up, recharge, premium draw spend, paid bundles, shop discounts
- retention_free_rewards: login rewards, mission rewards, exchange rewards, vote-to-claim rewards, free claims
- progression_season_systems: pass, season, monthly membership, rank/progression systems
- gameplay_content_activation: playable modes, feature-driven participation, collaboration gameplay programs
- community_participation: livestream participation rewards, comment/share/tag activities, UGC contests, offline participation
- media_awareness: showcase, trailer, recap, hype, announcement-only content
- release_update_rollout: version release, update launch, feature rollout
- unknown_event: valid event but primary business purpose is still unclear

Return JSON only.

Output schema:
{
  "post_id": "...",
  "events": [
    {
      "event_name": "...",
      "estimated_start_date": "YYYY-MM-DD or null",
      "estimated_end_date": "YYYY-MM-DD or null",
      "event_description": "...",
      "category": "one exact category label from the allowed list",
      "confidence": 0.0,
      "evidence": "..."
    }
  ]
}

Rules:
- Extract only real player-facing events.
- Do not invent dates, rewards, or event names.
- If the post still is not strong enough to support a real player-facing event, return an empty events array.
- event_description must be concise Vietnamese, not copied marketing text.
- Classify by primary business purpose, not by content theme.
""".strip()


def _extract_response_text(response_json: dict[str, Any]) -> str:
    output_text = response_json.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    parts: list[str] = []
    for item in response_json.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    if not parts:
        raise RuntimeError(f"OpenAI response did not contain text output: {response_json}")
    return "\n".join(parts)


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True, slots=True)
class ModelPricing:
    input_per_1m: float
    cached_input_per_1m: float
    output_per_1m: float


@dataclass(frozen=True, slots=True)
class ModelPricingBands:
    short: ModelPricing
    long: ModelPricing
    long_context_threshold_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LlmUsageRecord:
    session_id: str | None
    run_id: str | None
    unified_app_id: str | None
    month_bucket: str | None
    stage: str
    item_id: str | None
    provider: str
    model: str
    prompt_version: str
    response_id: str | None
    input_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    output_tokens: int
    total_tokens: int
    input_cost_usd: float
    cached_input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    created_at: str


LONG_CONTEXT_THRESHOLD_TOKENS = 272_000


DEFAULT_MODEL_PRICING: dict[str, ModelPricingBands] = {
    "gpt-5.3-codex": ModelPricingBands(
        short=ModelPricing(input_per_1m=1.75, cached_input_per_1m=0.175, output_per_1m=14.00),
        long=ModelPricing(input_per_1m=1.75, cached_input_per_1m=0.175, output_per_1m=14.00),
        long_context_threshold_tokens=None,
    ),
    "gpt-5.5": ModelPricingBands(
        short=ModelPricing(input_per_1m=5.00, cached_input_per_1m=0.50, output_per_1m=30.00),
        long=ModelPricing(input_per_1m=10.00, cached_input_per_1m=1.00, output_per_1m=45.00),
        long_context_threshold_tokens=LONG_CONTEXT_THRESHOLD_TOKENS,
    ),
    "gpt-5.4": ModelPricingBands(
        short=ModelPricing(input_per_1m=2.50, cached_input_per_1m=0.25, output_per_1m=15.00),
        long=ModelPricing(input_per_1m=5.00, cached_input_per_1m=0.05, output_per_1m=22.50),
        long_context_threshold_tokens=LONG_CONTEXT_THRESHOLD_TOKENS,
    ),
    "gpt-5.4-mini": ModelPricingBands(
        short=ModelPricing(input_per_1m=0.75, cached_input_per_1m=0.075, output_per_1m=4.50),
        long=ModelPricing(input_per_1m=0.75, cached_input_per_1m=0.075, output_per_1m=4.50),
        long_context_threshold_tokens=None,
    ),
    "gpt-5.4-nano": ModelPricingBands(
        short=ModelPricing(input_per_1m=0.20, cached_input_per_1m=0.02, output_per_1m=1.25),
        long=ModelPricing(input_per_1m=0.20, cached_input_per_1m=0.02, output_per_1m=1.25),
        long_context_threshold_tokens=None,
    ),
}


def _model_pricing_env_prefix(model: str) -> str:
    sanitized = []
    for char in model:
        if char.isalnum():
            sanitized.append(char.upper())
        else:
            sanitized.append("_")
    return "OPENAI_PRICE_" + "".join(sanitized).strip("_")


def _resolve_model_pricing(model: str, *, input_tokens: int) -> ModelPricing:
    matched_key = next((key for key in DEFAULT_MODEL_PRICING if model == key or model.startswith(f"{key}-")), None)
    default_bands = DEFAULT_MODEL_PRICING.get(
        matched_key or "",
        ModelPricingBands(
            short=ModelPricing(0.0, 0.0, 0.0),
            long=ModelPricing(0.0, 0.0, 0.0),
            long_context_threshold_tokens=None,
        ),
    )
    env_prefix = _model_pricing_env_prefix(matched_key or model)

    def _float_env(suffix: str, fallback: float) -> float:
        raw = os.getenv(f"{env_prefix}_{suffix}", "").strip()
        if not raw:
            return fallback
        try:
            return max(0.0, float(raw))
        except ValueError:
            return fallback

    threshold_raw = os.getenv(f"{env_prefix}_LONG_CONTEXT_THRESHOLD_TOKENS", "").strip()
    threshold = default_bands.long_context_threshold_tokens
    if threshold_raw:
        try:
            threshold = max(0, int(threshold_raw))
        except ValueError:
            threshold = default_bands.long_context_threshold_tokens

    short = ModelPricing(
        input_per_1m=_float_env("SHORT_INPUT_PER_1M", _float_env("INPUT_PER_1M", default_bands.short.input_per_1m)),
        cached_input_per_1m=_float_env(
            "SHORT_CACHED_INPUT_PER_1M",
            _float_env("CACHED_INPUT_PER_1M", default_bands.short.cached_input_per_1m),
        ),
        output_per_1m=_float_env("SHORT_OUTPUT_PER_1M", _float_env("OUTPUT_PER_1M", default_bands.short.output_per_1m)),
    )
    long = ModelPricing(
        input_per_1m=_float_env("LONG_INPUT_PER_1M", short.input_per_1m if threshold is None else default_bands.long.input_per_1m),
        cached_input_per_1m=_float_env(
            "LONG_CACHED_INPUT_PER_1M",
            short.cached_input_per_1m if threshold is None else default_bands.long.cached_input_per_1m,
        ),
        output_per_1m=_float_env("LONG_OUTPUT_PER_1M", short.output_per_1m if threshold is None else default_bands.long.output_per_1m),
    )
    if threshold is not None and input_tokens > threshold:
        return long
    return short


def _extract_response_usage(response_json: dict[str, Any]) -> tuple[int, int, int, int, int]:
    usage = response_json.get("usage")
    if not isinstance(usage, dict):
        return 0, 0, 0, 0, 0
    input_tokens = _safe_int(usage.get("input_tokens"))
    output_tokens = _safe_int(usage.get("output_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens"))
    input_details = usage.get("input_tokens_details")
    cached_input_tokens = 0
    if isinstance(input_details, dict):
        cached_input_tokens = _safe_int(input_details.get("cached_tokens"))
    uncached_input_tokens = max(0, input_tokens - cached_input_tokens)
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    return input_tokens, cached_input_tokens, uncached_input_tokens, max(0, output_tokens), total_tokens


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class FbEventLlmConfig:
    api_key: str
    base_url: str
    provider: str
    model: str
    fb_merge_model: str
    unified_merge_model: str
    timeout_seconds: int
    max_retries: int


def _normalize_responses_base_url(value: str) -> str:
    text = value.strip().rstrip("/")
    if text.endswith("/responses"):
        return text[: -len("/responses")]
    return text


def load_fb_event_llm_config() -> FbEventLlmConfig:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing.")
    return FbEventLlmConfig(
        api_key=api_key,
        base_url=_normalize_responses_base_url(
            os.getenv("OPENAI_BASE_URL", "https://compass.llm.shopee.io/compass-api/v1")
        ),
        provider=os.getenv("OPENAI_PROVIDER", "OpenAI").strip() or "OpenAI",
        model=os.getenv("OPENAI_MODEL", "gpt-5.4-nano").strip() or "gpt-5.4-nano",
        fb_merge_model=os.getenv("OPENAI_FB_MERGE_MODEL", "gpt-5.4-mini").strip() or "gpt-5.4-mini",
        unified_merge_model=os.getenv("OPENAI_UNIFIED_EVENT_MERGE_MODEL", "gpt-5.4-mini").strip() or "gpt-5.4-mini",
        timeout_seconds=max(30, int(os.getenv("OPENAI_TIMEOUT_SECONDS", "300").strip() or "300")),
        max_retries=max(1, int(os.getenv("OPENAI_MAX_RETRIES", "3").strip() or "3")),
    )


class OpenAIFbEventClient:
    def __init__(self, config: FbEventLlmConfig | None = None) -> None:
        self._config = config or load_fb_event_llm_config()
        self._usage_recorder: Callable[[LlmUsageRecord], None] | None = None
        self._usage_context_stack: list[dict[str, str | None]] = []

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def merge_model(self) -> str:
        return self._config.fb_merge_model

    @property
    def unified_merge_model(self) -> str:
        return self._config.unified_merge_model

    def set_usage_recorder(self, recorder: Callable[[LlmUsageRecord], None] | None) -> None:
        self._usage_recorder = recorder

    @contextmanager
    def usage_context(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        unified_app_id: str | None = None,
        month_bucket: str | None = None,
        stage: str | None = None,
        item_id: str | None = None,
    ) -> Iterator[None]:
        self._usage_context_stack.append(
            {
                "session_id": session_id,
                "run_id": run_id,
                "unified_app_id": unified_app_id,
                "month_bucket": month_bucket,
                "stage": stage,
                "item_id": item_id,
            }
        )
        try:
            yield
        finally:
            self._usage_context_stack.pop()

    def _responses_create(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
        model_override: str | None = None,
        prompt_version: str,
    ) -> dict[str, Any]:
        req = request.Request(
            f"{self._config.base_url}/responses",
            method="POST",
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
                "Provider": self._config.provider,
            },
            data=json.dumps(
                {
                    "model": model_override or self._config.model,
                    "input": [
                        {
                            "role": "system",
                            "content": [{"type": "input_text", "text": system_prompt}],
                        },
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": json.dumps(user_payload, ensure_ascii=False)}],
                        },
                    ],
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": schema_name,
                            "strict": True,
                            "schema": schema,
                        }
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )
        last_error: Exception | None = None
        for attempt in range(1, self._config.max_retries + 1):
            try:
                with request.urlopen(req, timeout=self._config.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except error.HTTPError as exc:  # pragma: no cover - network path
                body = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
                raise RuntimeError(f"OpenAI request failed: status={exc.code}, body={body[:1000]}") from exc
            except TimeoutError as exc:  # pragma: no cover - network path
                last_error = exc
            except error.URLError as exc:  # pragma: no cover - network path
                last_error = exc

            if attempt < self._config.max_retries:
                time.sleep(min(2 ** (attempt - 1), 8))
            else:
                assert last_error is not None
                raise RuntimeError(
                    "OpenAI request failed after retries: "
                    f"timeout={self._config.timeout_seconds}s retries={self._config.max_retries} error={last_error}"
                ) from last_error
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected OpenAI response type: {type(payload)}")
        self._record_usage(
            response_json=payload,
            model=model_override or self._config.model,
            prompt_version=prompt_version,
        )
        return payload

    def _record_usage(self, *, response_json: dict[str, Any], model: str, prompt_version: str) -> None:
        if self._usage_recorder is None:
            return
        input_tokens, cached_input_tokens, uncached_input_tokens, output_tokens, total_tokens = _extract_response_usage(
            response_json
        )
        pricing = _resolve_model_pricing(model, input_tokens=input_tokens)
        input_cost_usd = (uncached_input_tokens / 1_000_000.0) * pricing.input_per_1m
        cached_input_cost_usd = (cached_input_tokens / 1_000_000.0) * pricing.cached_input_per_1m
        output_cost_usd = (output_tokens / 1_000_000.0) * pricing.output_per_1m
        context = self._usage_context_stack[-1] if self._usage_context_stack else {}
        self._usage_recorder(
            LlmUsageRecord(
                session_id=context.get("session_id"),
                run_id=context.get("run_id"),
                unified_app_id=context.get("unified_app_id"),
                month_bucket=context.get("month_bucket"),
                stage=context.get("stage") or "unknown",
                item_id=context.get("item_id"),
                provider=self._config.provider,
                model=model,
                prompt_version=prompt_version,
                response_id=str(response_json.get("id")) if response_json.get("id") else None,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                uncached_input_tokens=uncached_input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                input_cost_usd=input_cost_usd,
                cached_input_cost_usd=cached_input_cost_usd,
                output_cost_usd=output_cost_usd,
                total_cost_usd=input_cost_usd + cached_input_cost_usd + output_cost_usd,
                created_at=_utc_now_iso(),
            )
        )

    def merge_event_objects(self, merge_payload: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "canonical_event_name": {"type": "string"},
                            "estimated_start_date": {"type": ["string", "null"]},
                            "estimated_end_date": {"type": ["string", "null"]},
                            "canonical_event_description": {"type": "string"},
                            "source_event_object_ids": {"type": "array", "items": {"type": "string"}},
                            "dedup_confidence": {"type": "number"},
                        },
                        "required": [
                            "canonical_event_name",
                            "estimated_start_date",
                            "estimated_end_date",
                            "canonical_event_description",
                            "source_event_object_ids",
                            "dedup_confidence",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["events"],
            "additionalProperties": False,
        }
        response_json = self._responses_create(
            system_prompt=MERGE_PROMPT,
            user_payload=merge_payload,
            schema_name="fb_event_merge",
            schema=schema,
            model_override=self._config.fb_merge_model,
            prompt_version=MERGE_PROMPT_VERSION,
        )
        payload = json.loads(_extract_response_text(response_json))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid merge payload: {payload}")
        return payload

    def merge_unified_event_sources(self, merge_payload: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "canonical_event_name": {"type": "string"},
                            "event_category": {"type": "string", "enum": UNIFIED_EVENT_CATEGORIES},
                            "estimated_start_date": {"type": ["string", "null"]},
                            "estimated_end_date": {"type": ["string", "null"]},
                            "canonical_event_description": {"type": "string"},
                            "anchor_source_type": {
                                "type": "string",
                                "enum": ["st_app_update_event", "st_version_event", "fb_post"],
                            },
                            "source_ids": {"type": "array", "items": {"type": "string"}},
                            "merge_confidence": {"type": "number"},
                        },
                        "required": [
                            "canonical_event_name",
                            "event_category",
                            "estimated_start_date",
                            "estimated_end_date",
                            "canonical_event_description",
                            "anchor_source_type",
                            "source_ids",
                            "merge_confidence",
                        ],
                        "additionalProperties": False,
                    },
                },
                "discarded_source_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["events", "discarded_source_ids"],
            "additionalProperties": False,
        }
        response_json = self._responses_create(
            system_prompt=UNIFIED_MERGE_PROMPT,
            user_payload=merge_payload,
            schema_name="unified_cross_source_event_merge",
            schema=schema,
            model_override=self._config.unified_merge_model,
            prompt_version=UNIFIED_MERGE_PROMPT_VERSION,
        )
        payload = json.loads(_extract_response_text(response_json))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid unified merge payload: {payload}")
        return payload

    def consolidate_unified_candidates(self, merge_payload: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "canonical_event_name": {"type": "string"},
                            "event_category": {"type": "string", "enum": UNIFIED_EVENT_CATEGORIES},
                            "estimated_start_date": {"type": ["string", "null"]},
                            "estimated_end_date": {"type": ["string", "null"]},
                            "canonical_event_description": {"type": "string"},
                            "anchor_source_type": {
                                "type": "string",
                                "enum": ["st_app_update_event", "st_version_event", "fb_post"],
                            },
                            "source_ids": {"type": "array", "items": {"type": "string"}},
                            "merge_confidence": {"type": "number"},
                        },
                        "required": [
                            "canonical_event_name",
                            "event_category",
                            "estimated_start_date",
                            "estimated_end_date",
                            "canonical_event_description",
                            "anchor_source_type",
                            "source_ids",
                            "merge_confidence",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["events"],
            "additionalProperties": False,
        }
        response_json = self._responses_create(
            system_prompt=UNIFIED_CONSOLIDATION_PROMPT,
            user_payload=merge_payload,
            schema_name="unified_cross_source_event_consolidation",
            schema=schema,
            model_override=self._config.unified_merge_model,
            prompt_version=UNIFIED_CONSOLIDATION_PROMPT_VERSION,
        )
        payload = json.loads(_extract_response_text(response_json))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid unified consolidation payload: {payload}")
        return payload

    def harvest_remaining_fb_post_events(self, post_payload: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "event_name": {"type": "string"},
                            "estimated_start_date": {"type": ["string", "null"]},
                            "estimated_end_date": {"type": ["string", "null"]},
                            "event_description": {"type": "string"},
                            "category": {"type": "string", "enum": REMAINING_FB_HARVEST_CATEGORIES},
                            "confidence": {"type": "number"},
                            "evidence": {"type": "string"},
                        },
                        "required": [
                            "event_name",
                            "estimated_start_date",
                            "estimated_end_date",
                            "event_description",
                            "category",
                            "confidence",
                            "evidence",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["post_id", "events"],
            "additionalProperties": False,
        }
        response_json = self._responses_create(
            system_prompt=REMAINING_FB_HARVEST_PROMPT,
            user_payload=post_payload,
            schema_name="fb_remaining_event_harvest",
            schema=schema,
            model_override=self._config.unified_merge_model,
            prompt_version=REMAINING_FB_HARVEST_PROMPT_VERSION,
        )
        payload = json.loads(_extract_response_text(response_json))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid remaining FB harvest payload: {payload}")
        return payload

    def detect_post_event(self, post_payload: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "contains_event": {"type": "boolean"},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
                "event_signals": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["post_id", "contains_event", "confidence", "reason", "event_signals"],
            "additionalProperties": False,
        }
        response_json = self._responses_create(
            system_prompt=DETECTION_PROMPT,
            user_payload=post_payload,
            schema_name="fb_event_detection",
            schema=schema,
            prompt_version=DETECTION_PROMPT_VERSION,
        )
        payload = json.loads(_extract_response_text(response_json))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid detection payload: {payload}")
        return payload

    def extract_event_objects(self, post_payload: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "event_name": {"type": "string"},
                            "estimated_start_date": {"type": ["string", "null"]},
                            "estimated_end_date": {"type": ["string", "null"]},
                            "event_description": {"type": "string"},
                            "evidence_text": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": [
                            "event_name",
                            "estimated_start_date",
                            "estimated_end_date",
                            "event_description",
                            "evidence_text",
                            "confidence",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["post_id", "events"],
            "additionalProperties": False,
        }
        response_json = self._responses_create(
            system_prompt=EXTRACTION_PROMPT,
            user_payload=post_payload,
            schema_name="fb_event_extraction",
            schema=schema,
            prompt_version=EXTRACTION_PROMPT_VERSION,
        )
        payload = json.loads(_extract_response_text(response_json))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid extraction payload: {payload}")
        return payload

    def judge_event_pair(self, pair_payload: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "same_event": {"type": "boolean"},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["same_event", "confidence", "reason"],
            "additionalProperties": False,
        }
        response_json = self._responses_create(
            system_prompt=DEDUP_PROMPT,
            user_payload=pair_payload,
            schema_name="fb_event_dedup_judge",
            schema=schema,
            prompt_version=DEDUP_PROMPT_VERSION,
        )
        payload = json.loads(_extract_response_text(response_json))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Invalid dedup payload: {payload}")
        return payload
