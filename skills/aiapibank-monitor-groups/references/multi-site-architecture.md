# Multi-Site Sub2API Architecture

## Configuration Boundaries

Use YAML for operator-edited, non-secret site metadata. YAML supports comments and keeps per-site overrides readable. Do not put passwords or tokens in the YAML registry.

```yaml
sites:
  pinaic:
    name: PinAI
    base_url: https://app.pinaic.com
    login_path: /api/v1/auth/login
    refresh_path: /api/v1/auth/refresh
    groups_path: /api/v1/groups/available
    username_field: email
    poll_interval_seconds: 60
    credentials_file: secrets/pinaic.env
    data_dir: data/pinaic
```

Store credentials separately and set the file mode to `0600`:

```dotenv
MONITOR_USERNAME=user@example.com
MONITOR_PASSWORD=secret
```

Exclude `secrets/`, token state, databases, snapshots, and histories from version control. Validate site IDs and resolve all relative paths against the registry directory.

## Token Lifecycle

- Keep one private token-state file per site with mode `0600`.
- Store access token, refresh token, access expiry, and `saved_at`.
- Do not refresh on every group poll. Refresh shortly before access-token expiry.
- When the refresh endpoint rotates the refresh token, replace the token-state file atomically before the next request.
- On 401/403, attempt one refresh and retry once. Fall back to password login only when refresh is unavailable or rejected.
- Never store token state in `sites.yaml`, logs, service units, group snapshots, or shared history databases.

## Group Polling and Persistence

- Choose the shortest interval allowed by the site and operational need; default to 300 seconds and use 30-60 seconds only when changes must be detected quickly.
- Add small per-site jitter so multiple monitors do not poll simultaneously.
- Write `groups_latest.json` atomically on every successful poll.
- Compare a canonical content hash excluding fetch timestamps. Write history only when group content changes.
- Optionally write a lightweight heartbeat every 15-60 minutes to prove liveness without duplicating full payloads.
- For a few sites and modest history, JSON plus change-only JSONL is sufficient.
- For many sites, frequent polling, or querying change history, use SQLite in WAL mode. Keep current groups and change events in separate tables; store raw payload JSON when schema flexibility matters.

Suggested SQLite ownership:

```text
sites (site_id, display_name, base_url)
group_current (site_id, group_id, content_hash, payload_json, fetched_at)
group_events (site_id, group_id, event_type, content_hash, payload_json, observed_at)
poll_runs (site_id, started_at, finished_at, status, group_count, error_class)
```

Do not put passwords, access tokens, or refresh tokens in this database.

## Process Model

Prefer one systemd template instance per site, such as `sub2api-monitor@pinaic.service`. A failed or rate-limited site then cannot block other monitors. Each instance reads the shared YAML registry, selects one site ID, loads only that site's secret file, and writes only that site's runtime state.

Use bounded exponential backoff for transient failures while preserving the configured success cadence. Send `Connection: close` or create a fresh session when long idle intervals make pooled connections stale.
