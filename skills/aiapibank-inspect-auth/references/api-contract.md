# Sub2API Authentication Contract

AIAPIBANK and PinAI are verified deployments of this contract. Treat the paths and shapes below as sub2api defaults and verify them against each new deployment before unattended use.

## Endpoints

| Purpose | Method | Path | Authentication |
|---|---|---|---|
| Login | POST | `/api/v1/auth/login` | None |
| Refresh session | POST | `/api/v1/auth/refresh` | Refresh token in JSON body |
| Available groups | GET | `/api/v1/groups/available` | Bearer token |
| Group rates | GET | `/api/v1/groups/rates` | Bearer token |
| Available channels | GET | `/api/v1/channels/available` | Bearer token |
| User keys | GET | `/api/v1/keys` | Bearer token |

Use JSON for login:

```json
{"email":"user@example.com","password":"secret"}
```

Read `data.access_token` and `data.refresh_token` from a successful response. Send authenticated requests with:

```text
Authorization: Bearer <access_token>
```

Refresh near access-token expiry rather than on every poll:

```json
{"refresh_token":"rt_..."}
```

Read the new token values from `data` and atomically persist any rotated refresh token before the next request. Verify this endpoint for each deployment; fall back to password login when it is absent or rejected.

Expect API envelopes shaped like:

```json
{"code":0,"message":"success","data":[]}
```

## Browser Discovery

The login page is `/login`. The browser stores these values in `localStorage`:

- `auth_token`
- `refresh_token`
- `token_expires_at`

Use Chrome DevTools to inspect the form and network requests only when the contract may have changed. Consent dialogs can block the submit button, so inspect the live DOM rather than assuming selectors.

## Token Constraints

Decode the JWT payload only to inspect `exp`; do not treat payload decoding as signature verification. Use the verified refresh endpoint before expiry, or log in again when refresh is unavailable.

Keep the login and API User-Agent stable. Testing on 2026-07-19 showed one token returning HTTP 200 with the monitor User-Agent and HTTP 401 with a changed diagnostic User-Agent. Treat this as evidence of token/client binding unless a later test disproves it.

Interpret failures precisely:

- Missing/malformed Authorization header: immediate 401-style JSON response.
- Expired, rejected, or fingerprint-mismatched token: 401/403 response.
- `requests.exceptions.ReadTimeout`: transport/upstream response problem, not proof of authentication failure.

Never store the real account password or token in this skill.
