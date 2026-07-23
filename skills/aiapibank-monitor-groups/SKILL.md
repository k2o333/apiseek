---
name: aiapibank-monitor-groups
description: Build, run, verify, and operate resilient group monitors for AIAPIBANK and compatible sub2api deployments. Use when onboarding multiple sub2api websites, polling `/api/v1/groups/available`, maintaining access and refresh tokens without an LLM or browser, isolating per-site credentials and state, persisting group snapshots/events, configuring polling intervals and retries, or supervising monitors with systemd template instances.
---

# Monitor Sub2API Groups

Implement unattended polling with direct HTTP login, bounded retries, durable output, and process supervision.

## Scope

- Treat AIAPIBANK as the original verified deployment. Apply this skill to confirmed sub2api sites and compatible forks such as PinAI.
- Verify login, refresh, and group endpoints during onboarding. Keep per-site overrides when a deployment differs from the defaults.
- Current production shape: one public script (`sub2api_monitor.py`), one env file and data dir per site, and `sub2api-monitor-once@<id>.timer` + oneshot service. The old `sub2api-monitor@.service` is rollback-only. No `sites.yaml`, SQLite, Prometheus, or separate alert service.

## Workflow

1. Inspect `sub2api_monitor.py`, `sites/*.env`, `data/<site>/`, and `sub2api-monitor@.service` before creating or replacing files.
2. Store all site settings and credentials in `sites/<site-id>.env` with mode `0600` (directory `0700`). Do not commit real secrets.
3. Log in through the JSON API and persist per-site `data/<site>/token.json` with mode `0600` (atomic write).
4. Check JWT `exp` before each poll. Prefer refresh near expiry; persist rotated refresh tokens atomically. Fall back to password login when refresh is unavailable or rejected. Do not login/refresh on every groups poll when the access token is still valid.
5. Use `--once` for a bounded-retry polling round. Let the production timer control the long interval; use `POLL_INTERVAL_SECONDS` only for foreground loop debugging and installer consistency checks.
6. Write complete `groups_latest.json` atomically on success. Append `groups_events.jsonl` only when the content hash changes (or `initial` on first success). Never overwrite latest on failure.
7. Avoid reusing a long-idle HTTP keep-alive connection. Use `Connection: close` and a consistent User-Agent.
8. Classify errors: 401 → one refresh then one login; 403 geo/HTML → no login loop; 429 Retry-After; timeout/5xx keep token and back off.
9. Run each site through `sub2api-monitor-once@<site-id>.timer`. Never run the legacy simple service and the once timer for the same site at the same time.
10. Verify with unit tests (mock HTTP) plus real `--validate` / `--once` when credentials and network allow.

Read [the formal implementation design](../../docs/03%20designs/sub2api-monitor.md) before implementing or reviewing the monitor.
Read [the formal contract index](../../docs/02%20specs/README.md) before changing persistence, CLI behavior, remote mutation, or deployment. The local files under `references/` are historical summaries and cannot override formal docs.

## Completion Checks

- Terminal output contains complete group summaries, not only a count.
- `groups_latest.json`, `groups_events.jsonl`, and `token.json` (mode 0600) are updated for the site.
- 401 triggers recovery once; region 403 does not loop login; timeout does not clear token.
- No password or token appears in logs, Git-tracked files, or service definitions.
- Each site has isolated credentials, token state, latest snapshot, and events.
- Process survives transient timeout and exits cleanly on stop signal.
