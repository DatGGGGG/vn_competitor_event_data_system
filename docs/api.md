# Event Lookup API

This project exposes a small read-only HTTP API on top of the SQLite warehouse.

Source implementation:

- [api.py](C:/Users/VEE0634/Desktop/Coding/vn_competitor_event_data_system/src/vn_event_dw/api.py)
- [api_service.py](C:/Users/VEE0634/Desktop/Coding/vn_competitor_event_data_system/src/vn_event_dw/api_service.py)

## Run Locally

Start the API directly:

```bash
python -m vn_event_dw.cli serve-api --db data/warehouse.db
```

Default local URL:

- `http://127.0.0.1:8765`

To expose it through ngrok:

```bash
python -m vn_event_dw.cli serve-api-ngrok --db data/warehouse.db
```

## Base Concepts

- `unified_app_id` is the canonical game identifier used across the warehouse.
- `unified_event_id` is the canonical merged event identifier.
- `source_post_id` is the Facebook post identifier from `raw_fb_posts`.
- list/statistics endpoints now filter by warehouse `month_bucket`, not by estimated event-date overlap
- FB metrics are aggregated from all FB posts linked to an event.
- `social_score` is computed as:
  - `2 * reaction + 3 * comment + 5 * share + view`

## Endpoint Summary

- `GET /api/games`
- `GET /api/events`
- `GET /api/events-light`
- `GET /api/event-statistics`
- `GET /api/events/{unified_event_id}`
- `GET /api/events/{unified_event_id}/sources`
- `GET /api/events/{unified_event_id}/top-posts`
- `GET /api/events/{unified_event_id}/posts`
- `GET /api/posts/{source_post_id}`

## Endpoint: `GET /api/games`

Purpose:

- look up games and their `unified_app_id`
- use this before calling the event endpoints if you only know the game name

Query params:

- `q` optional
  - matches by `app_name`
  - matches by `unified_app_id`
  - supports acronym-style lookup such as `MLBB`

Example requests:

```text
/api/games
/api/games?q=MLBB
/api/games?q=PUBG
```

Example response:

```json
{
  "results": [
    {
      "unified_app_id": "57955d280211a6718a000002",
      "app_name": "Mobile Legends: Bang Bang"
    }
  ]
}
```

## Endpoint: `GET /api/events`

Purpose:

- return the detailed event list for one or more games in a requested date range
- optionally return only the top events by `social_score`

Required query params:

- `unified_app_id` repeatable
- `time_range_start` in `YYYY-MM-DD`
- `time_range_end` in `YYYY-MM-DD`

Optional query params:

- `top`
  - positive integer
  - when provided, each app block returns only the top N events ranked by:
    1. `social_score DESC`
    2. `total_engagement_fb DESC`
    3. `canonical_event_name`

Example requests:

```text
/api/events?unified_app_id=57955d280211a6718a000002&time_range_start=2026-05-01&time_range_end=2026-05-31
/api/events?unified_app_id=57955d280211a6718a000002&time_range_start=2026-05-01&time_range_end=2026-05-31&top=5
```

Response shape:

```json
{
  "results": [
    {
      "unified_app_id": "57955d280211a6718a000002",
      "app_name": "Mobile Legends: Bang Bang",
      "events": [
        {
          "unified_event_id": "...",
          "canonical_event_name": "...",
          "event_category": "...",
          "estimated_start_date": "2026-05-01",
          "estimated_end_date": "2026-05-31",
          "canonical_event_description": "...",
          "anchor_source_type": "fb_post",
          "merge_confidence": 0.91,
          "month_bucket": "2026-05",
          "fb_post_count": 3,
          "st_app_update_event_count": 1,
          "st_version_event_count": 0,
          "total_engagement_fb": 1200,
          "total_reaction_fb": 115,
          "total_comment_fb": 25,
          "total_share_fb": 6,
          "total_view_fb": 630,
          "social_score": 813
        }
      ]
    }
  ]
}
```

### Month-Bucket Inclusion Rules

For `/api/events`, `/api/events-light`, and `/api/event-statistics`, the requested date range is converted into one or more month buckets:

- `2026-04-01` to `2026-04-30` -> `2026-04`
- `2026-04-15` to `2026-05-02` -> `2026-04`, `2026-05`

An event is included when its stored `month_bucket` is one of the derived buckets.

This means:

- a mid-month query like `2026-05-15` to `2026-05-15` still returns events from `month_bucket = 2026-05`
- exact event start/end dates do not control inclusion for these list/stat endpoints anymore
- `estimated_start_date` and `estimated_end_date` are still returned as metadata when available

### Sorting

Without `top`, returned events are sorted by:

1. effective start date
2. canonical event name

With `top`, returned events are sorted by social rank as described above.

## Endpoint: `GET /api/events-light`

Purpose:

- return a compact event list for one or more games in a requested date range
- use this when an agent only needs IDs and names before drilling deeper

Required query params:

- `unified_app_id` repeatable
- `time_range_start` in `YYYY-MM-DD`
- `time_range_end` in `YYYY-MM-DD`

Example request:

```text
/api/events-light?unified_app_id=57955d280211a6718a000002&time_range_start=2026-05-01&time_range_end=2026-05-31
```

Response shape:

```json
{
  "results": [
    {
      "unified_app_id": "57955d280211a6718a000002",
      "app_name": "Mobile Legends: Bang Bang",
      "events": [
        {
          "unified_event_id": "...",
          "canonical_event_name": "...",
          "event_category": "..."
        }
      ]
    }
  ]
}
```

## Endpoint: `GET /api/event-statistics`

Purpose:

- return a summary view of events for one or more games in a requested date range

Required query params:

- `unified_app_id` repeatable
- `time_range_start` in `YYYY-MM-DD`
- `time_range_end` in `YYYY-MM-DD`

Example request:

```text
/api/event-statistics?unified_app_id=57955d280211a6718a000002&time_range_start=2026-05-01&time_range_end=2026-05-31
```

Response shape:

```json
{
  "results": [
    {
      "unified_app_id": "57955d280211a6718a000002",
      "app_name": "Mobile Legends: Bang Bang",
      "statistics": {
        "event_count_total": 12,
        "event_count_st_app_update": 4,
        "event_count_st_version": 2,
        "event_count_fb": 10,
        "total_engagement_fb": 8200,
        "total_reaction_fb": 740,
        "total_comment_fb": 180,
        "total_share_fb": 95,
        "total_view_fb": 3100,
        "top_socially_active_events": [
          {
            "unified_event_id": "...",
            "canonical_event_name": "...",
            "event_category": "...",
            "social_score": 4120,
            "total_engagement_fb": 2100,
            "total_reaction_fb": 220,
            "total_comment_fb": 55,
            "total_share_fb": 20,
            "total_view_fb": 3565
          }
        ]
      }
    }
  ]
}
```

### Statistics Semantics

- `event_count_total`: number of distinct events returned for that game
- `event_count_st_app_update`: number of returned events with at least one `st_app_update_event` source
- `event_count_st_version`: number of returned events with at least one `st_version_event` source
- `event_count_fb`: number of returned events with at least one `fb_post` source

These source-type counts are non-exclusive and may sum to more than `event_count_total`.

## Endpoint: `GET /api/events/{unified_event_id}`

Purpose:

- return the full detail for one unified event

Path params:

- `unified_event_id` required

Example request:

```text
/api/events/uev_example
```

Response shape:

```json
{
  "unified_event_id": "uev_example",
  "unified_app_id": "57955d280211a6718a000002",
  "app_name": "Mobile Legends: Bang Bang",
  "canonical_event_name": "...",
  "event_category": "...",
  "estimated_start_date": "2026-05-01",
  "estimated_end_date": "2026-05-31",
  "canonical_event_description": "...",
  "anchor_source_type": "fb_post",
  "merge_confidence": 0.91,
  "month_bucket": "2026-05",
  "fb_post_count": 3,
  "st_app_update_event_count": 1,
  "st_version_event_count": 0,
  "total_engagement_fb": 1200,
  "total_reaction_fb": 115,
  "total_comment_fb": 25,
  "total_share_fb": 6,
  "total_view_fb": 630,
  "social_score": 813
}
```

## Endpoint: `GET /api/events/{unified_event_id}/sources`

Purpose:

- return FB-post-only summary statistics for a single event
- this replaces the older raw lineage-style source listing

Path params:

- `unified_event_id` required

Example request:

```text
/api/events/uev_example/sources
```

Response shape:

```json
{
  "unified_event_id": "uev_example",
  "unified_app_id": "57955d280211a6718a000002",
  "app_name": "Mobile Legends: Bang Bang",
  "canonical_event_name": "...",
  "event_category": "...",
  "estimated_start_date": "2026-05-01",
  "estimated_end_date": "2026-05-31",
  "fb_post_count": 3,
  "total_engagement_fb": 1200,
  "total_reaction_fb": 115,
  "total_comment_fb": 25,
  "total_share_fb": 6,
  "total_view_fb": 630,
  "social_score": 813
}
```

## Endpoint: `GET /api/events/{unified_event_id}/top-posts`

Purpose:

- return the top FB posts linked to an event, ranked by `social_score`

Path params:

- `unified_event_id` required

Optional query params:

- `top`
  - positive integer
  - default `5`

Ranking:

1. `social_score DESC`
2. `engagement_num DESC`
3. `source_post_id`

Example request:

```text
/api/events/uev_example/top-posts?top=3
```

Response shape:

```json
{
  "unified_event_id": "uev_example",
  "unified_app_id": "57955d280211a6718a000002",
  "app_name": "Mobile Legends: Bang Bang",
  "canonical_event_name": "...",
  "posts": [
    {
      "source_post_id": "post_123",
      "publish_time": "2026-05-03T08:00:00Z",
      "link": "https://facebook.com/...",
      "engagement_num": 120,
      "reaction_num": 10,
      "comment_num": 2,
      "share_num": 1,
      "view_num": 100,
      "social_score": 131
    }
  ]
}
```

## Endpoint: `GET /api/events/{unified_event_id}/posts`

Purpose:

- return all linked FB posts for an event in compact form

Path params:

- `unified_event_id` required

Response shape:

```json
{
  "unified_event_id": "uev_example",
  "unified_app_id": "57955d280211a6718a000002",
  "app_name": "Mobile Legends: Bang Bang",
  "canonical_event_name": "...",
  "posts": [
    {
      "source_post_id": "post_123",
      "publish_time": "2026-05-03T08:00:00Z",
      "engagement_num": 120,
      "reaction_num": 10,
      "comment_num": 2,
      "share_num": 1,
      "view_num": 100,
      "social_score": 131
    }
  ]
}
```

## Endpoint: `GET /api/posts/{source_post_id}`

Purpose:

- return full detail for one FB post

Path params:

- `source_post_id` required

Response shape:

```json
{
  "source_post_id": "post_123",
  "unified_app_id": "57955d280211a6718a000002",
  "app_name": "Mobile Legends: Bang Bang",
  "fb_page_id": "...",
  "channel_id": "...",
  "channel_name": "...",
  "post_type": "photo",
  "post_description": "...",
  "duration": "",
  "link": "https://facebook.com/...",
  "publish_time": "2026-05-03T08:00:00Z",
  "hashtag": "",
  "engagement": "120",
  "reaction": "10",
  "comment": "2",
  "share": "1",
  "view": "100",
  "source_file": "seed.csv",
  "ingested_at": "2026-06-12T00:00:00Z",
  "engagement_num": 120,
  "reaction_num": 10,
  "comment_num": 2,
  "share_num": 1,
  "view_num": 100,
  "social_score": 131
}
```

## Validation Rules

For `/api/events`, `/api/events-light`, and `/api/event-statistics`:

- at least one `unified_app_id` is required
- `time_range_start` must be on or before `time_range_end`
- dates must be valid ISO dates in `YYYY-MM-DD`

Typical error behavior:

- missing required params -> `422`
- invalid date format -> `422`
- reversed date range -> `400`
- unknown `unified_event_id` -> `404`
- unknown `source_post_id` -> `404`

## Notes For Agents

Recommended lookup flow:

1. Call `/api/games?q=<game name or acronym>`
2. Resolve the correct `unified_app_id`
3. Call `/api/event-statistics` for the overview
4. Call `/api/events?top=N` when you want only the most important events
5. Call `/api/events-light` when you want a compact ID/name list
6. Call `/api/events/{unified_event_id}` for full detail on a chosen event
7. Call `/api/events/{unified_event_id}/top-posts` or `/api/events/{unified_event_id}/posts` for FB evidence
8. Call `/api/posts/{source_post_id}` for full post detail
