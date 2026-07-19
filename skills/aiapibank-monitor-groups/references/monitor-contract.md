# Sub2API Monitor Contract

The defaults below are verified for AIAPIBANK and PinAI. Verify each new sub2api deployment and record path or response-shape overrides in the site registry.

## Configuration

Use these environment variables for legacy single-site operation. For multiple sites, use the YAML registry and per-site secret files described in `multi-site-architecture.md`.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `AIAPIBANK_EMAIL` | Yes | - | Login email |
| `AIAPIBANK_PASSWORD` | Yes | - | Login password |
| `AIAPIBANK_BASE_URL` | No | `https://www.aiapibank.com` | Site base URL |
| `POLL_INTERVAL_SECONDS` | No | `300` | Successful polling interval |
| `DATA_DIR` | No | `data` | Snapshot/history directory |
| `TOKEN_STATE_FILE` | No | `data/token_state.json` | Private token cache |
| `LOG_LEVEL` | No | `INFO` | Console log level |

Keep the User-Agent stable between login and group requests. Do not put credentials directly in Python or systemd unit files.

## Request Lifecycle

For each cycle:

1. Re-login when no access token exists or JWT `exp` is within a safety margin.
2. Call `GET /api/v1/groups/available` with Bearer authentication.
3. On 401/403, log in once and retry once.
4. On connection/read timeout or 5xx, back off and retry without terminating the daemon.
5. Validate that `data` is a list.
6. Write full group data and print concise per-group output.
7. Wait until 300 seconds from the start of the successful cycle.

Use separate connect and read timeouts, for example `(10, 30)`.

## Idle Connection Requirement

Do not leave a pooled keep-alive connection idle for the full polling interval and then reuse it. A controlled test against this site produced:

```text
before_idle status=200 seconds=1.529
after_300s_idle ReadTimeout seconds=30.035
fresh_connection status=200 seconds=1.197
```

Use one of these patterns:

```python
response = session.get(
    url,
    headers={
        "Authorization": f"Bearer {token}",
        "Connection": "close",
    },
    timeout=(10, 30),
)
```

Or create and close a fresh Session for each polling cycle. Retain a stable User-Agent even when recreating the Session.

## Persistence

- Write `groups_latest.json` through a temporary file followed by atomic replacement.
- Append one JSON object per successful poll to `groups_history.jsonl`.
- Set token state permissions to `0600`.
- Exclude credential files, token state, data output, and Python cache files from Git.
- Add rotation or retention when history growth becomes material.

## Supervision

Use a systemd service with network-online ordering, a fixed working directory, `Restart=always`, a short restart delay, and no credentials embedded in `ExecStart`. Verify the unit with `systemd-analyze verify` before enabling it.
