# Agent contract

## Reuse, do not rebuild

The working Socialdata path in this repo is already implemented in:

- `src/vn_event_dw/socialdata.py`
- `src/vn_event_dw/socialdata_sync.py`
- `src/vn_event_dw/cli.py`
- `scripts/socialdata_mint_google_access_token.mjs`

Prefer those entrypoints over custom one-off auth code.

## Required environment and inputs

Minimum inputs:

- repo root
- Google service-account JSON path
- Socialdata app slug, currently `srcvn`
- task objective: auth check, introspection, GraphQL test, or post sync

Useful environment variables:

```text
SOCIALDATA_BASE_URL=https://socialdata.garena.vn
SOCIALDATA_TIMEOUT_SECONDS=60
SOCIALDATA_APP_SLUG=srcvn
SOCIALDATA_GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/service-account.json
SOCIALDATA_GOOGLE_SCOPES=https://www.googleapis.com/auth/userinfo.email
SOCIALDATA_GOOGLE_ACCESS_TOKEN=
SOCIALDATA_USESSION=
```

## Required Google scope

Default Google scope must be:

```text
https://www.googleapis.com/auth/userinfo.email
```

Do not rely on `cloud-platform` alone. The callback may fail with `Invalid Email` because Socialdata needs to identify the granted service-account email.

## Preferred debug order

### 1. Auth check

```powershell
python -m vn_event_dw.cli socialdata-auth-check `
  --google-service-account-file C:\path\to\service-account.json
```

Expected success:

```json
{
  "data": {
    "__typename": "Query"
  }
}
```

### 2. Manual callback exchange

```powershell
$token = python -m vn_event_dw.cli socialdata-mint-google-access-token `
  --google-service-account-file C:\path\to\service-account.json `
  --token-only
$token = $token.Trim()
curl.exe -i --max-redirs 0 "https://socialdata.garena.vn/connect/google/callback?access_token=$token"
```

Expected success:

- HTTP `302`
- `Set-Cookie` includes `usession=...`

### 3. Schema introspection

```powershell
python -m vn_event_dw.cli socialdata-introspect `
  --google-service-account-file C:\path\to\service-account.json `
  --output tmp/socialdata_schema.json
```

### 4. GraphQL query experiments

```powershell
python -m vn_event_dw.cli socialdata-graphql `
  --google-service-account-file C:\path\to\service-account.json `
  --query "query { __typename }"
```

### 5. Incremental post sync

```powershell
python -m vn_event_dw.cli sync-socialdata-posts `
  --db data/warehouse.db `
  --config examples/config.json `
  --lookback-days 10 `
  --google-service-account-file C:\path\to\service-account.json
```

## Sync semantics

`sync-socialdata-posts`:

- resolves Socialdata app/team by slug
- lists channels
- matches Socialdata `channel.sub` to `config_app_mapping.fb_page_id`
- fetches recent posts and metrics
- upserts into `raw_fb_posts`
- stops when the configured cutoff is reached

Warehouse mapping notes:

- `source_post_id` comes from Socialdata `Post.sub`
- `source_file` is tracked like `socialdata/<app_slug>/channel_<channel_id>.json`
- a mojibake repair pass improves Vietnamese text readability

## Troubleshooting

### `Invalid Email`

Most likely:

- the service-account email was not added in Socialdata
- the Google token was minted with the wrong scope

### PowerShell `curl` errors

Use `curl.exe`, not `curl`.

### Token rotation

Google access tokens change each time they are minted. That is normal.

## Handoff rule

If the user asks for a teammate package, produce:

- a Vietnamese human guide
- a copy-paste prompt for Claude/Codex
- the exact repo file paths the other agent should reuse
