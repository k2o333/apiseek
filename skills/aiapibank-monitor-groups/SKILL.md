---
name: aiapibank-monitor-groups
description: Build, run, verify, and operate resilient group monitors for AIAPIBANK and compatible sub2api deployments. Use when onboarding multiple sub2api websites, polling `/api/v1/groups/available`, maintaining access and refresh tokens without an LLM or browser, isolating per-site credentials and state, persisting group snapshots/history, configuring short polling intervals and retries, or supervising monitors with systemd.
---

# Monitor Sub2API Groups

Implement unattended polling with direct HTTP login, bounded retries, durable output, and process supervision.

## Scope

- Treat AIAPIBANK as the original verified deployment. Apply this skill to confirmed sub2api sites and compatible forks such as PinAI.
- Verify login, refresh, and group endpoints during onboarding. Keep per-site overrides when a deployment differs from the defaults.

## Workflow

1. Inspect existing project scripts, `sites.yaml`, and per-site configuration before creating or replacing files.
2. Store non-secret site configuration in YAML and load email/password from a separate mode-600 environment file or secret manager.
3. Log in through the JSON API and persist per-site token state with mode 600.
4. Check JWT expiry before each poll. Prefer the verified refresh endpoint near expiry; persist rotated refresh tokens atomically. Fall back to password login when refresh is unavailable or rejected.
5. Poll every 300 seconds and emit each group's ID, name, rate multiplier, and status to the terminal.
6. Write the complete latest response atomically. Append history when group content changes; add periodic heartbeats only when an audit trail requires them.
7. Avoid reusing a five-minute-idle HTTP connection. Send `Connection: close` for the groups request or create a fresh Session per polling cycle.
8. Retry transient network failures with bounded exponential backoff while preserving the normal five-minute success cadence.
9. Run each site as an isolated systemd template instance with restart-on-failure and graceful SIGINT/SIGTERM handling.
10. Verify with a test covering at least two polling intervals and one controlled failure path.

Read [references/monitor-contract.md](references/monitor-contract.md) before implementing or reviewing the monitor.
Read [references/multi-site-architecture.md](references/multi-site-architecture.md) when adding sites, selecting polling frequency, or changing token/group persistence.

## Completion Checks

- Confirm terminal output contains complete group summaries, not only a count.
- Confirm `groups_latest.json`, `groups_history.jsonl`, and private token state are updated.
- Confirm a 401 triggers re-login and a timeout triggers transport retry.
- Confirm no password or token appears in logs, Git-tracked files, or service definitions.
- Confirm each site has isolated credentials, token state, latest snapshot, and history.
- Confirm the process survives a transient timeout and exits cleanly on a stop signal.
