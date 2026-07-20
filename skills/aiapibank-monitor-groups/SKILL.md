---
name: aiapibank-monitor-groups
description: Build, run, verify, and operate resilient group monitors for AIAPIBANK and compatible sub2api deployments. Use when onboarding multiple sub2api websites, polling `/api/v1/groups/available`, maintaining access and refresh tokens without an LLM or browser, isolating per-site credentials and state, persisting group snapshots/events, configuring polling intervals and retries, or supervising monitors with systemd template instances.
---

# Monitor Sub2API Groups

Implement unattended polling with direct HTTP login, bounded retries, durable output, and process supervision.

## Scope

- Treat AIAPIBANK as the original verified deployment. Apply this skill to confirmed sub2api sites and compatible forks such as PinAI.
- Verify login, refresh, and group endpoints during onboarding. Keep per-site overrides when a deployment differs from the defaults.
- First version: one public script (`sub2api_monitor.py`), one env file per site (`sites/<id>.env`), one data dir per site, systemd template `sub2api-monitor@.service`. No `sites.yaml`, SQLite, Prometheus, or separate alert service.

## Workflow

1. Inspect `sub2api_monitor.py`, `sites/*.env`, `data/<site>/`, and `sub2api-monitor@.service` before creating or replacing files.
2. Store all site settings and credentials in `sites/<site-id>.env` with mode `0600` (directory `0700`). Do not commit real secrets.
3. Log in through the JSON API and persist per-site `data/<site>/token.json` with mode `0600` (atomic write).
4. Check JWT `exp` before each poll. Prefer refresh near expiry; persist rotated refresh tokens atomically. Fall back to password login when refresh is unavailable or rejected. Do not login/refresh on every groups poll when the access token is still valid.
5. Poll at least every 60 seconds (default 300). Print each group's ID, name, rate multiplier, and status.
6. Write complete `groups_latest.json` atomically on success. Append `groups_events.jsonl` only when the content hash changes (or `initial` on first success). Never overwrite latest on failure.
7. Avoid reusing a long-idle HTTP keep-alive connection. Use `Connection: close` and a consistent User-Agent.
8. Classify errors: 401 → one refresh then one login; 403 geo/HTML → no login loop; 429 Retry-After; timeout/5xx keep token and back off.
9. Run each site as `sub2api-monitor@<site-id>` with restart-on-failure and graceful SIGINT/SIGTERM.
10. Verify with unit tests (mock HTTP) plus real `--validate` / `--once` when credentials and network allow.

Read [references/monitor-contract.md](references/monitor-contract.md) before implementing or reviewing the monitor.
Read [references/multi-site-architecture.md](references/multi-site-architecture.md) when adding sites or changing persistence.

## Completion Checks

- Terminal output contains complete group summaries, not only a count.
- `groups_latest.json`, `groups_events.jsonl`, and `token.json` (mode 0600) are updated for the site.
- 401 triggers recovery once; region 403 does not loop login; timeout does not clear token.
- No password or token appears in logs, Git-tracked files, or service definitions.
- Each site has isolated credentials, token state, latest snapshot, and events.
- Process survives transient timeout and exits cleanly on stop signal.
