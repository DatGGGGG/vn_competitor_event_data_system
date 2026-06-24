---
name: socialdata-connector
description: Use this skill to access Socialdata from a standalone handoff folder without needing the main warehouse repo. It covers Google service-account token minting, Socialdata usession exchange, GraphQL connectivity tests, schema inspection, and agent handoff guidance.
---

# Socialdata Connector

Use the files inside this handoff package only. Do not assume the main project repo exists.

## Files to reuse

- `../../socialdata_mint_google_access_token.mjs`
- `../socialdata-connector/references/agent-workflow.vi.md`
- `../socialdata-connector/references/nontech-handoff.vi.md`

## Slug rule

Do not hard-code `srcvn`.

Always derive the Socialdata slug from the user's URL.

Example:

```text
https://socialdata.garena.vn/srcvn/member/channel
```

Slug:

```text
srcvn
```

In general, the slug is the first path segment after `socialdata.garena.vn/`.

## Required auth rule

Mint the Google access token with this default scope:

```text
https://www.googleapis.com/auth/userinfo.email
```

Do not switch to `cloud-platform` as the only scope. Socialdata needs the token to expose the granted service-account email.

## Working flow

1. Confirm the JSON key path.
2. Mint a Google access token with `socialdata_mint_google_access_token.mjs`.
3. Call:
   `GET https://socialdata.garena.vn/connect/google/callback?access_token=<token>`
4. Parse `usession` from `Set-Cookie`.
5. Use `cookie: usession=<value>` for GraphQL requests.

## First test

Run a GraphQL health query:

```text
query { __typename }
```

Expected success:

```json
{
  "data": {
    "__typename": "Query"
  }
}
```

## If the user is non-technical

Read `references/nontech-handoff.vi.md` and respond in simple Vietnamese with copy-paste commands.

## If debugging fails

Always report:

- command run
- exact output
- most likely cause
- next step
