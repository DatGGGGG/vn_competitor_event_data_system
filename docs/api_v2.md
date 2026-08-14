# Event Lookup API v2

`/api/v2` is the agent-friendly API surface for event and Facebook post lookup.
It keeps the old `/api/...` endpoints live while making one distinction explicit:

- event endpoints filter by `unified_events.month_bucket`
- raw post endpoints filter by `raw_fb_posts.publish_time`

Base URLs:

- Public: `https://april-refund-promoter.ngrok-free.dev`
- Local VM: `http://127.0.0.1:8765`

## Authentication

The API can be protected with a shared key by setting `VN_EVENT_DW_API_KEY` in the runtime environment.

When `VN_EVENT_DW_API_KEY` is blank, `/api/...` and `/api/v2/...` are public.
When it is set, every `/api/...` and `/api/v2/...` request must include one of:

```text
X-API-Key: your_api_key
```

or:

```text
Authorization: Bearer your_api_key
```

Example:

```bash
curl \
  -H "X-API-Key: your_api_key" \
  "https://april-refund-promoter.ngrok-free.dev/api/v2/games"
```

Admin pages under `/admin/...` use the separate `ADMIN_PASSWORD` login flow.

## Response Shape

Collection endpoints return:

```json
{
  "results": [],
  "meta": {
    "api_version": "v2",
    "generated_at": "2026-08-07T00:00:00+00:00",
    "scope": {},
    "semantics": "raw_fb_post_publish_time",
    "count": 0
  }
}
```

Single-resource endpoints return `result` plus the same `meta` block.

## Endpoints

### `GET /api/v2/health`

Lists available v2 endpoints.

### `GET /api/v2/games`

Lists tracked games.

Query params:

- `q`, optional search text

### `GET /api/v2/games/{unified_app_id}`

Returns one tracked game with its configured Facebook page IDs.

### `GET /api/v2/games/{unified_app_id}/events`

Lists events for one game.

Event time filters use event month buckets, not Facebook post dates.

Query params:

- `month=YYYY-MM`, repeatable
- or `start_month=YYYY-MM&end_month=YYYY-MM`
- `top`, optional, ranks by social score
- `event_category`, repeatable
- `source_type`, repeatable: `fb_post`, `st_app_update_event`, `st_version_event`
- `min_social_score`
- `has_fb_posts`

Example:

```bash
curl "https://april-refund-promoter.ngrok-free.dev/api/v2/games/5da680bb42fa0c4364eb64c8/events?month=2026-08"
```

### `GET /api/v2/games/{unified_app_id}/events/summary`

Aggregates event counts and FB metrics for one game.

Uses the same event month-bucket filters as `/events`.

### `GET /api/v2/games/{unified_app_id}/events/search`

Fuzzy event-name search scoped to one game, with cross-game fallback behavior inherited from the event search service.

Query params:

- `q`, required
- `month=YYYY-MM`, repeatable
- or `start_month=YYYY-MM&end_month=YYYY-MM`
- `top`, default `10`

### `GET /api/v2/games/{unified_app_id}/events/coverage`

Returns event coverage/freshness for one game.

Uses event month buckets when month filters are provided.

### `GET /api/v2/games/{unified_app_id}/posts`

Lists raw Facebook posts for one game, even if the posts are not linked to any event.

This is the endpoint to use for questions like:

- "Does this game have FB posts in August?"
- "What are the latest FB posts for this game?"

Query params:

- `publish_start=YYYY-MM-DD`, optional
- `publish_end=YYYY-MM-DD`, optional
- `limit`, default `20`, max `100`
- `q`, optional text search over post description

Post metric fields:

- `engagement_count`
- `reaction_count`
- `comment_count`
- `share_count`
- `view_count`
- `social_score`

Each post includes:

```json
"linked_events": [
  {
    "unified_event_id": "uev_...",
    "canonical_event_name": "Event name",
    "month_bucket": "2026-08"
  }
]
```

Example:

```bash
curl "https://april-refund-promoter.ngrok-free.dev/api/v2/games/5da680bb42fa0c4364eb64c8/posts?publish_start=2026-08-01&publish_end=2026-08-31"
```

### `GET /api/v2/events/{unified_event_id}`

Returns one event detail row.

### `GET /api/v2/events/{unified_event_id}/post-stats`

Returns FB post totals linked to one event.

### `GET /api/v2/events/{unified_event_id}/posts`

Returns posts linked to one event using v2 metric field names.

### `GET /api/v2/posts/{source_post_id}`

Returns one raw FB post with normalized metrics and linked event references.

## Agent Guidance

Use `/api/v2/games/{game_id}/posts` to answer raw post questions.
Do not infer post publish dates from event `month_bucket`.

Use `/api/v2/games/{game_id}/events` or `/events/summary` to answer event questions.
When reporting event results, describe `month_bucket` as the event bucket used by the warehouse, not as the actual date of every linked post.
