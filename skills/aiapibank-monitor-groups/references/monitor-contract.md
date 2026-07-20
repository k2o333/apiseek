# Sub2API Monitor Contract

Defaults below are verified for AIAPIBANK and PinAI. Verify each new sub2api deployment and record path overrides in that site's `sites/<id>.env`.

## Configuration

One env file per site: `sites/<site-id>.env` (mode `0600`). Load via:

```bash
python3 sub2api_monitor.py --env-file sites/<site-id>.env [--validate|--once]
```

| Variable | Required | Default / notes |
|---|---|---|
| `MONITOR_SITE_ID` | Yes | lowercase alnum + hyphen |
| `MONITOR_SITE_NAME` | No | display name |
| `MONITOR_BASE_URL` | Yes | must be `https://` |
| `MONITOR_USERNAME` | Yes | login identity |
| `MONITOR_PASSWORD` | Yes | login password |
| `MONITOR_LOGIN_PATH` | No | `/api/v1/auth/login` (fixed relative path) |
| `MONITOR_REFRESH_PATH` | No | `/api/v1/auth/refresh` |
| `MONITOR_GROUPS_PATH` | No | `/api/v1/groups/available` |
| `MONITOR_USERNAME_FIELD` | No | `email` |
| `POLL_INTERVAL_SECONDS` | No | `300` (minimum 60) |
| `CONNECT_TIMEOUT_SECONDS` | No | `10` |
| `READ_TIMEOUT_SECONDS` | No | `30` (raise if site is slow) |
| `REFRESH_MARGIN_SECONDS` | No | `600` |
| `REQUEST_JITTER_SECONDS` | No | `10` |
| `DATA_DIR` | Yes | e.g. `.../data/<site-id>` |
| `TOKEN_STATE_FILE` | Yes | must be under `DATA_DIR`, e.g. `.../token.json` |
| `MONITOR_PROXY_URL` | No | optional; never log |
| `LOG_LEVEL` | No | `INFO` |

Legacy names `AIAPIBANK_EMAIL` / `AIAPIBANK_PASSWORD` / `AIAPIBANK_BASE_URL` are still accepted as fallbacks when `MONITOR_*` is unset.

Keep the User-Agent stable across login, refresh, and groups. Do not embed credentials in Python or unit files.

## Request Lifecycle

1. Ensure access token: password login if missing; refresh if near `exp`; else reuse memory/disk token.
2. `GET` groups with `Authorization: Bearer …` and `Connection: close`.
3. On 401 (or token-error 403 JSON): one refresh, then one password login, retry groups once.
4. On Cloudflare/geo HTML 403: treat as region/egress — do not login loop.
5. On 429: honor `Retry-After`. On timeout/5xx: keep token, backoff.
6. Validate `data` is a list; never treat error bodies as empty groups.
7. Write latest (+ event if hash changed); sleep `interval - elapsed + jitter`.

Timeouts: `timeout=(connect, read)`.

## Persistence

Per site under `data/<site-id>/`:

| File | Role |
|---|---|
| `token.json` | access/refresh/exp, mode `0600`, atomic |
| `groups_latest.json` | last successful full snapshot + `content_hash` |
| `groups_events.jsonl` | change events only (`initial` / `groups_changed`) |
| `monitor.lock` | single instance |

- Atomic write: temp + fsync + replace.
- Events: append + fsync before replacing latest; dedupe by `content_hash`.
- Default events retention: 180 days (pruned in process).
- No SQLite in first version.

## Supervision

Template unit `sub2api-monitor@.service`:

- Exec: project venv + `sub2api_monitor.py --env-file sites/%i.env`
- `Restart=always`, `RestartSec=10`, `After/Wants=network-online.target`
- Hardening: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ReadWritePaths=…/data`

```bash
systemd-analyze verify /etc/systemd/system/sub2api-monitor@.service
systemctl enable --now sub2api-monitor@pinaic
systemctl enable --now sub2api-monitor@aiapibank
```
