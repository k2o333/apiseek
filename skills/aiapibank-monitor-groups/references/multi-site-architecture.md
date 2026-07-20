# Multi-Site Sub2API Architecture (v1)

## Configuration Boundaries

First version uses **one env file per site**, not `sites.yaml`:

```text
sites/<site-id>.env     # secrets + non-secrets, mode 0600
data/<site-id>/         # token, latest, events, lock
```

Example keys: see `sites/*.env.example` and `monitor-contract.md`.

Validate:

- HTTPS base URL only
- Site ID: lowercase letters, digits, hyphens
- Poll interval ≥ 60
- Token file under `DATA_DIR`
- Credential file not group/other-readable

## Token Lifecycle

- One `token.json` per site, mode `0600`, atomic replace
- Fields: `access_token`, `refresh_token`, `access_expires_at`, `saved_at`
- Do not refresh every groups poll; refresh near access expiry
- On refresh rotation, save new refresh token with access token
- 401 recovery: refresh once → password login once
- Timeout / 5xx / region 403: never clear stored token

## Group Polling and Persistence

- Default interval 300s; floor 60s; add jitter
- `groups_latest.json` on every **successful** poll only
- Canonical hash of sorted groups; `groups_events.jsonl` only when hash changes
- Crash order: append event + fsync, then atomic latest; restart dedupes by hash
- JSON + change-only JSONL is enough for a few sites

SQLite / Prometheus / central alert service are **non-goals** for v1. Revisit only if site count, query needs, or alert volume clearly outgrow files.

## Process Model

- One process per site (systemd template instance `%i`)
- Instance lock under `data/<site>/monitor.lock`
- Shared code: `sub2api_monitor.py` only — no per-site Python copies
- New site = new env + `systemctl enable --now sub2api-monitor@<id>`
