---
name: aiapibank-inspect-auth
description: Inspect and automate authentication for AIAPIBANK and compatible sub2api deployments. Use when onboarding a suspected sub2api website, verifying its implementation and API contract, replacing browser login with direct HTTP requests, reading login tokens, querying available groups, diagnosing 401/403 responses, or documenting authentication differences between deployments.
---

# Inspect Sub2API Authentication

Discover browser behavior once, then prefer the documented HTTP API for unattended automation.

## Scope

- Treat AIAPIBANK as the original verified deployment, not the only supported site.
- Use this skill for websites confirmed or suspected to run Wei-Shaw/sub2api or a compatible fork. PinAI is a verified compatible deployment.
- Verify the live contract before assuming endpoint paths, request fields, response envelopes, token storage keys, or refresh behavior. Stop using this workflow when the site is not sub2api-compatible.

## Workflow

1. Read local project notes, the site registry, and existing code before accessing the site.
2. Confirm sub2api compatibility from frontend assets, runtime version/config, `/api/v1` requests, or matching login/group response shapes.
3. Use Chrome DevTools only when browser behavior, consent dialogs, storage keys, or network requests must be rediscovered.
4. Inspect the login request and response without embedding credentials or tokens in source files.
5. Use direct HTTP login for Python automation when the JSON endpoint remains available.
6. Keep the same User-Agent across login and authenticated requests; treat tokens as potentially client-fingerprint-bound.
7. Classify HTTP 401/403 as authentication failure. Do not classify a `ReadTimeout` as token expiry.
8. Verify access with the available-groups endpoint and validate the JSON envelope before consuming data.

Read [references/api-contract.md](references/api-contract.md) for endpoints, storage keys, response shapes, and verified constraints.
For repository changes, also read [the formal provider and config contracts](../../docs/02%20specs/README.md); skill references cannot override their declared status.

## Security

- Load credentials from a mode-600 environment file or secret manager.
- Store each site's metadata and credentials in `sites/<site-id>.env` with mode `0600`; this repository does not use `sites.yaml`.
- Redact passwords, access tokens, and refresh tokens from logs and committed artifacts.
- Return a token verbatim only when the authorized user explicitly requests it.
- Avoid browser automation for long-running jobs unless CAPTCHA, browser attestation, or an equivalent control makes direct API login unavailable.
