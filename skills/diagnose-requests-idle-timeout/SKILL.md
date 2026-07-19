---
name: diagnose-requests-idle-timeout
description: Diagnose Python requests/urllib3 timeouts that occur after an idle interval, especially periodic jobs behind Cloudflare or another proxy. Use when the first request succeeds but a later pooled request hangs, when distinguishing token failure from transport failure, or when testing stale keep-alive connection reuse and selecting a safe mitigation.
---

# Diagnose Requests Idle Timeouts

Use controlled timing and fresh-connection comparisons to separate stale pooled connections from authentication, DNS, TLS, and upstream latency problems.

## Diagnostic Workflow

1. Capture the exact exception class, phase, timeout value, and timestamp.
2. Distinguish `ConnectTimeout` from `ReadTimeout`; do not infer token expiry from either.
3. Verify DNS, TCP/TLS, HTTP status, first-byte time, and total time independently.
4. Keep Authorization and User-Agent identical during comparisons because tokens may be client-bound.
5. Establish a baseline with several immediate same-Session and fresh-Session requests.
6. Reproduce the production idle interval with one Session: request, idle, request again.
7. Immediately repeat through a fresh connection after any timeout.
8. Attribute stale keep-alive reuse only when the idle pooled request fails and the fresh control succeeds.
9. Select the least invasive mitigation and rerun the original interval test.

Read [references/reproduction.md](references/reproduction.md) for the test matrix, interpretation rules, and mitigation options.

## Safety

- Use read-only endpoints and a low request count.
- Never print Bearer tokens, passwords, cookies, or complete sensitive response bodies.
- Do not alter production data to reproduce a transport issue.
- Stop any diagnostic process after collecting the required evidence.
