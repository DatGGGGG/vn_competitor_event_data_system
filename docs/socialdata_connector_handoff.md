# Socialdata Connector Handoff

This repo already contains a working Socialdata connector path for:

- authenticating with a Google service account
- exchanging that Google token for a Socialdata `usession`
- running arbitrary Socialdata GraphQL queries
- introspecting the GraphQL schema
- incrementally syncing Socialdata FB posts into `raw_fb_posts`

This document is the shortest practical handoff for another engineer or AI workflow builder to reuse that work.

## What Already Exists

Core implementation lives here:

- `src/vn_event_dw/socialdata.py`
- `src/vn_event_dw/socialdata_sync.py`
- `src/vn_event_dw/cli.py`
- `scripts/socialdata_mint_google_access_token.mjs`

High-level usage notes already exist in:

- `README.md`

## Required Authentication Model

The working Socialdata auth flow is:

1. Mint a Google access token from a Google service-account JSON file.
2. The Google token **must** include the scope:

```text
https://www.googleapis.com/auth/userinfo.email
```

3. Call:

```text
GET https://socialdata.garena.vn/connect/google/callback?access_token=<token>
```

4. Parse the `Set-Cookie` response header and extract `usession`.
5. Use `cookie: usession=<value>` for Socialdata GraphQL calls.

Important:

- A valid Google token with only `cloud-platform` scope is **not enough**.
- Socialdata needs the token to expose the granted service-account email.
- The repo default is already fixed to use `userinfo.email`.

## Environment Variables

The connector uses these variables:

```text
SOCIALDATA_BASE_URL=https://socialdata.garena.vn
SOCIALDATA_TIMEOUT_SECONDS=60
SOCIALDATA_APP_SLUG=srcvn

SOCIALDATA_USESSION=
SOCIALDATA_GOOGLE_ACCESS_TOKEN=
SOCIALDATA_GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/service-account.json
SOCIALDATA_GOOGLE_SCOPES=https://www.googleapis.com/auth/userinfo.email
```

Recommended unattended path:

- set `SOCIALDATA_GOOGLE_SERVICE_ACCOUNT_FILE`
- set `SOCIALDATA_GOOGLE_SCOPES=https://www.googleapis.com/auth/userinfo.email`
- do not rely on manually pasted `usession`

## CLI Surface

These commands already work in this repo:

```bash
python -m vn_event_dw.cli socialdata-mint-google-access-token \
  --google-service-account-file /path/to/service-account.json
```

```bash
python -m vn_event_dw.cli socialdata-mint-google-access-token \
  --google-service-account-file /path/to/service-account.json \
  --token-only
```

```bash
python -m vn_event_dw.cli socialdata-auth-check \
  --google-service-account-file /path/to/service-account.json
```

```bash
python -m vn_event_dw.cli socialdata-debug-token-exchange \
  --google-service-account-file /path/to/service-account.json
```

```bash
python -m vn_event_dw.cli socialdata-graphql \
  --usession <cookie> \
  --query "query { __typename }"
```

```bash
python -m vn_event_dw.cli socialdata-introspect \
  --google-service-account-file /path/to/service-account.json \
  --output tmp/socialdata_schema.json
```

```bash
python -m vn_event_dw.cli sync-socialdata-posts \
  --db data/warehouse.db \
  --config examples/config.json \
  --lookback-days 10
```

## What the Sync Actually Does

`sync-socialdata-posts` behavior:

- resolves the Socialdata app/team by slug, currently `srcvn`
- lists Socialdata channels
- matches Socialdata channel `sub` to `config_app_mapping.fb_page_id`
- lists recent posts per matched channel
- fetches metrics from Socialdata
- writes/upserts into `raw_fb_posts`
- stops scanning when it reaches the cutoff date

The main DB-facing behavior is:

- `source_post_id` comes from Socialdata `Post.sub`
- `source_file` is recorded like `socialdata/<app_slug>/channel_<channel_id>.json`
- a mojibake repair pass is applied to improve Vietnamese text readability

## Suggested Integration Contract For Another Agent

If another team wants to use Claude or another orchestrator, the cleanest contract is:

1. Reuse the existing Python connector directly from this repo.
2. Wrap the CLI commands or the `SocialDataClient` class as tools.
3. Keep auth, GraphQL, and incremental sync inside this repo.
4. Let the external workflow orchestrator call these tools instead of re-implementing the auth flow.

Recommended tool surface:

- `socialdata_auth_check`
- `socialdata_introspect`
- `socialdata_graphql`
- `socialdata_sync_posts`

If they want a thinner adapter, expose these operations only:

- `mint_google_access_token`
- `exchange_google_token_for_usession`
- `graphql(query, variables)`
- `sync_posts_since(date)`

## Minimal GraphQL Debug Sequence

If someone wants to debug the connector manually:

1. Mint token

```bash
python -m vn_event_dw.cli socialdata-mint-google-access-token \
  --google-service-account-file /path/to/service-account.json
```

2. Verify token exchange

```bash
python -m vn_event_dw.cli socialdata-debug-token-exchange \
  --google-service-account-file /path/to/service-account.json
```

Expected success pattern:

- HTTP `302`
- `Set-Cookie` contains `usession`

3. Verify GraphQL auth

```bash
python -m vn_event_dw.cli socialdata-auth-check \
  --google-service-account-file /path/to/service-account.json
```

Expected success:

```json
{
  "data": {
    "__typename": "Query"
  }
}
```

## Recommended Message To Another Engineer

If you need to explain the setup quickly:

```text
We already have a working Socialdata connector in vn_competitor_event_data_system.
The important part is Google service-account auth with the scope https://www.googleapis.com/auth/userinfo.email.
That token is exchanged for a Socialdata usession, then all GraphQL calls use the usession cookie.
Please reuse the existing Python connector and CLI instead of rebuilding the auth flow from scratch.
```
