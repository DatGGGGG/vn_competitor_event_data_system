---
name: socialdata-connector
description: Reuse the existing Socialdata connector in vn_competitor_event_data_system for Google service-account authentication, Socialdata usession exchange, GraphQL inspection, and incremental FB post sync. Use when Codex needs to connect to Socialdata, debug Socialdata auth, inspect the GraphQL schema, test post queries, or sync Socialdata posts into raw_fb_posts without reimplementing the auth flow.
---

# Socialdata Connector

Reuse the existing repo implementation instead of inventing a new connector.

Core code already exists here:

- `src/vn_event_dw/socialdata.py`
- `src/vn_event_dw/socialdata_sync.py`
- `src/vn_event_dw/cli.py`
- `scripts/socialdata_mint_google_access_token.mjs`

## Workflow

1. Read `references/agent-contract.md` for the working auth and command contract.
2. If the task includes guiding a non-technical teammate, read `references/nontech-setup.vi.md`.
3. Run auth validation before trying schema inspection or post sync.
4. Reuse existing CLI entrypoints whenever possible.

## Mandatory auth rule

Default Google scope must be:

```text
https://www.googleapis.com/auth/userinfo.email
```

Do not use `cloud-platform` as the only scope. Socialdata needs the Google token to expose the granted service-account email; otherwise the callback may fail with `Invalid Email`.

## Preferred command order

1. `socialdata-auth-check`
2. `socialdata-introspect` if schema discovery is needed
3. `socialdata-graphql` for query experiments
4. `sync-socialdata-posts` for warehouse loading

## Guardrails

- Do not ask the user to paste private key contents into chat if the JSON file already exists locally.
- Prefer the JSON key path via `--google-service-account-file`.
- Report commands, outputs, suspected cause, and next step when debugging fails.
- If the user already has a `usession`, it is acceptable to use it for quick GraphQL tests, but prefer the service-account flow for repeatable automation.

## Deliverables

When asked for a handoff package, produce:

- a non-technical setup guide in Vietnamese
- an agent-facing prompt or runbook
- exact commands for token minting, callback exchange, auth check, schema introspection, and post sync

## References

- `references/agent-contract.md`
- `references/nontech-setup.vi.md`
