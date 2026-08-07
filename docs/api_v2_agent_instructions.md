# Agent Instructions For API v2

Use `/api/v2` for all new SeaTalk, Claude, Codex, and analyst-agent lookups.

The key rule:

- Event endpoints answer questions about `unified_events.month_bucket`.
- Post endpoints answer questions about actual Facebook `raw_fb_posts.publish_time`.

Do not use an event `month_bucket` as proof that a Facebook post was published in that month.

## When The User Asks About Events

Use event endpoints when the user asks about campaigns, events, event rankings, event categories, event coverage, or social score by event.

Examples:

- "Top events of MLBB in August"
- "List monetization events for PUBG"
- "Search for the Naruto event in MLBB"

Recommended endpoints:

- `GET /api/v2/games`
- `GET /api/v2/games/{unified_app_id}/events?month=YYYY-MM`
- `GET /api/v2/games/{unified_app_id}/events?start_month=YYYY-MM&end_month=YYYY-MM`
- `GET /api/v2/games/{unified_app_id}/events/summary?month=YYYY-MM`
- `GET /api/v2/games/{unified_app_id}/events/search?q=...`
- `GET /api/v2/events/{unified_event_id}`
- `GET /api/v2/events/{unified_event_id}/post-stats`
- `GET /api/v2/events/{unified_event_id}/posts`

State clearly that `month=YYYY-MM` filters event month buckets, not raw FB publish dates.

## When The User Asks About Facebook Posts

Use post endpoints when the user asks whether there are posts in a date range, asks for latest posts, asks for post text, or asks about actual publication dates.

Examples:

- "Are there any posts in August?"
- "Latest FB posts for LMHT: Toc Chien"
- "Posts published from 2026-08-01 to 2026-08-06"
- "Search posts containing sinh nhat"

Recommended endpoint:

- `GET /api/v2/games/{unified_app_id}/posts?publish_start=YYYY-MM-DD&publish_end=YYYY-MM-DD`

Optional params:

- `limit`, default `20`, max `100`
- `q`, searches post text

Raw post responses include:

- `publish_time`
- `engagement_count`
- `reaction_count`
- `comment_count`
- `share_count`
- `view_count`
- `social_score`
- `linked_events`

Use `linked_events` only as lineage. A post can exist even if it is not linked to an event yet.

## LMHT-Style Regression Rule

If a user asks:

```text
co post nao trong thang 8 nay khong?
```

Do this:

1. Resolve the game via `GET /api/v2/games`.
2. Query raw posts:

```text
GET /api/v2/games/{unified_app_id}/posts?publish_start=2026-08-01&publish_end=2026-08-31&limit=20
```

3. Answer based on `publish_time`.
4. Do not answer from `/events?month=2026-08`.

Correct reasoning:

- `/events?month=2026-08` means event records bucketed into August.
- `/posts?publish_start=2026-08-01&publish_end=2026-08-31` means FB posts actually published in August.

## Response Guidance

When answering users, prefer:

- game name
- endpoint semantics used
- actual dates when discussing posts
- `month_bucket` only when discussing events

Avoid saying "there are no posts in August" unless `/api/v2/games/{id}/posts` with August `publish_start` / `publish_end` returned no results.
