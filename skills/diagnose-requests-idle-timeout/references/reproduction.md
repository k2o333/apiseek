# Idle Timeout Reproduction

## Test Matrix

Run these controls with the same URL, token, headers, and timeout:

| Test | Connection behavior | Purpose |
|---|---|---|
| Immediate pooled requests | One Session, no long idle | Establish normal API latency |
| Immediate fresh requests | New connection each request | Establish connection setup cost |
| Idle pooled request | One Session, production idle interval | Reproduce stale connection behavior |
| Fresh request after failure | New connection immediately | Separate stale socket from server outage |

Record status code, elapsed seconds, exception type, response server header, and whether expected JSON data was present. Redact secrets.

## Interpretation

- `ConnectTimeout`: DNS resolution, routing, TCP, proxy, or TLS establishment may be blocked or slow.
- `ReadTimeout`: a connection was established, but no response data completed within the read timeout.
- Immediate 401/403: authentication or client-binding issue, not a network timeout.
- Idle pooled timeout followed by immediate fresh success: strong evidence of a silently closed or unusable keep-alive connection.
- Both pooled and fresh requests timing out: investigate service health, proxy, routing, or rate limiting.
- Slow but successful fresh and pooled requests: investigate upstream latency before changing connection reuse.

## Verified AIAPIBANK Case

On 2026-07-19, AIAPIBANK resolved to Cloudflare addresses and returned `server: cloudflare`. A controlled request succeeded, the same `requests.Session` idled for 300 seconds, and the next request raised `ReadTimeout` at 30.035 seconds. A fresh connection immediately succeeded in 1.197 seconds.

This reproduces the monitor's earlier second-request timeout and rules out token expiry for that event.

## Mitigations

Prefer one of these approaches for low-frequency polling:

1. Send `Connection: close` and establish a fresh connection each cycle.
2. Create and close a Session per cycle while keeping headers and User-Agent stable.
3. Configure retries for idempotent GET requests, including read failures, with bounded backoff.

For a five-minute interval, connection reuse saves little compared with the risk and 30-second penalty of a stale socket. Keep explicit transport retries even after disabling long-idle reuse because independent network failures remain possible.

Verify the chosen mitigation by repeating at least two full production intervals. Do not declare the issue fixed from immediate back-to-back requests alone.
