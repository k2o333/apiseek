#!/usr/bin/env python3
"""Sub2API group models coverage: keys reconcile + models snapshot (risk surface isolation).

Does not wire CLI; import from sub2api_monitor or call programmatically.
Never deletes remote keys. JWT auth recovery is only for keys CRUD, never for /v1/models.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import requests

from sub2api_monitor import (
    ApiError,
    append_jsonl_fsync,
    utc_now_iso,
    write_json_atomic,
)

LOG = logging.getLogger("sub2api-models")

USER_AGENT = "sub2api-monitor/1.0.0"
MANAGED_KEY_PREFIX = "sub2api-monitor:g:"

DEFAULT_KEYS_PATH = "/api/v1/keys"
DEFAULT_MODELS_PATH = "/v1/models"
DEFAULT_PAGE_SIZE = 100
DEFAULT_EVENT_MODELS_DIFF_CAP = 50

# Injected callables
ListKeysPageFn = Callable[[int, int], Mapping[str, Any] | Sequence[Any]]
ListKeysAllFn = Callable[[], tuple[list[dict[str, Any]], bool]]
CreateKeyFn = Callable[[str], Mapping[str, Any]]
BindKeyFn = Callable[[Any, Any, str], Mapping[str, Any]]  # key_id, group_id, name
ListModelsFn = Callable[[str], list[str]]  # api_key secret -> model ids
RecoverAuthFn = Callable[[], None]
GetTokenFn = Callable[[], str | None]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def norm_id(x: Any) -> str:
    """Normalize group_id / key_id for set membership and map keys."""
    return str(x).strip()


def managed_key_name(group_id: Any) -> str:
    return MANAGED_KEY_PREFIX + norm_id(group_id)


def is_managed_key_name(name: Any) -> bool:
    if name is None:
        return False
    s = str(name)
    if not s.startswith(MANAGED_KEY_PREFIX):
        return False
    # Exact pattern: prefix + non-empty norm remainder, no extra path segments.
    rest = s[len(MANAGED_KEY_PREFIX) :]
    return rest != "" and rest == rest.strip() and "\n" not in rest


def key_secret(key: Mapping[str, Any]) -> str | None:
    """Return non-empty secret from key or api_key fields."""
    for field_name in ("key", "api_key"):
        val = key.get(field_name)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _truthy(val: Any) -> bool:
    if val is True:
        return True
    if val is False or val is None:
        return False
    if isinstance(val, (int, float)) and val != 0:
        return True
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def _key_is_disabled(key: Mapping[str, Any]) -> bool:
    """True if disabled when fields exist; missing fields → not filtered."""
    if "disabled" in key:
        return _truthy(key.get("disabled"))
    if "enabled" in key:
        return not _truthy(key.get("enabled"))
    status = key.get("status")
    if status is None:
        return False
    s = str(status).strip().lower()
    return s in ("disabled", "inactive", "revoked", "banned")


def _parse_expiry_ts(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        # Heuristic: ms timestamps
        ts = float(val)
        if ts > 1e12:
            ts = ts / 1000.0
        return ts
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
                return _parse_expiry_ts(int(s))
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError, OSError):
            return None
    return None


def _key_is_expired(key: Mapping[str, Any], *, now: float | None = None) -> bool:
    """True if expired when fields exist; missing fields → not filtered."""
    if "expired" in key:
        return _truthy(key.get("expired"))
    status = key.get("status")
    if status is not None and str(status).strip().lower() == "expired":
        return True
    for field_name in ("expires_at", "expire_at", "expired_at"):
        if field_name not in key:
            continue
        ts = _parse_expiry_ts(key.get(field_name))
        if ts is None:
            continue
        tnow = time.time() if now is None else now
        return ts <= tnow
    return False


def _id_sort_key(kid: str) -> tuple[int, int | str]:
    """Integer-first stable order: '10' > '2' for numeric ids."""
    try:
        return (0, int(kid))
    except (TypeError, ValueError):
        return (1, kid)


def usable_keys(group_id: Any, keys_all: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keys bound to group with secret, not disabled/expired; sorted int-first by key id."""
    gid = norm_id(group_id)
    out: list[dict[str, Any]] = []
    for raw in keys_all:
        if not isinstance(raw, Mapping):
            continue
        kg = raw.get("group_id")
        if kg is None or kg == "":
            continue
        if norm_id(kg) != gid:
            continue
        if key_secret(raw) is None:
            continue
        if _key_is_disabled(raw):
            continue
        if _key_is_expired(raw):
            continue
        out.append(dict(raw))

    def sort_key(k: Mapping[str, Any]) -> tuple[int, int | str]:
        kid = k.get("id")
        return _id_sort_key(norm_id(kid) if kid is not None else "")

    out.sort(key=sort_key)
    return out


def pick_key(group_id: Any, keys_all: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    keys = usable_keys(group_id, keys_all)
    return keys[0] if keys else None


def content_hash_models(models: Sequence[str] | None) -> str | None:
    if models is None:
        return None
    sorted_list = sorted(str(m) for m in models)
    canonical = json.dumps(sorted_list, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# Keys list parsing / pagination
# ---------------------------------------------------------------------------


def extract_keys_items(payload: Any) -> list[dict[str, Any]]:
    """Unwrap keys list from common envelopes."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, Mapping):
        for key in ("items", "list", "data", "keys"):
            items = data.get(key)
            if isinstance(items, list):
                return [x for x in items if isinstance(x, dict)]
    for key in ("items", "list", "keys"):
        items = payload.get(key)
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    return []


def _as_int(val: Any) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _as_bool(val: Any) -> bool | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no"):
            return False
    return None


def paging_meta(payload: Any) -> dict[str, Any]:
    """Extract paging fields from envelope (top-level or under data)."""
    meta: dict[str, Any] = {
        "total": None,
        "page": None,
        "page_size": None,
        "has_more": None,
    }
    if not isinstance(payload, Mapping):
        return meta
    sources: list[Mapping[str, Any]] = [payload]
    data = payload.get("data")
    if isinstance(data, Mapping):
        sources.append(data)
        for nested_name in ("pagination", "page_info", "meta"):
            nested = data.get(nested_name)
            if isinstance(nested, Mapping):
                sources.append(nested)
    for nested_name in ("pagination", "page_info", "meta"):
        nested = payload.get(nested_name)
        if isinstance(nested, Mapping):
            sources.append(nested)

    def pick(*names: str) -> Any:
        for src in sources:
            for n in names:
                if n in src and src[n] is not None:
                    return src[n]
        return None

    meta["total"] = _as_int(pick("total", "total_count", "count"))
    meta["page"] = _as_int(pick("page", "p", "current_page"))
    meta["page_size"] = _as_int(pick("page_size", "pageSize", "per_page", "limit", "size"))
    hm = pick("has_more", "hasMore", "more")
    if hm is not None:
        meta["has_more"] = _as_bool(hm)
    return meta


def list_keys_all(
    http_get_page_fn: ListKeysPageFn,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = 500,
) -> tuple[list[dict[str, Any]], bool]:
    """Paginate keys from p=1; merge + dedupe by norm key id.

    Returns (keys, paging_complete).
    paging_complete=False when we cannot prove the full set was retrieved.
    """
    if page_size < 1:
        page_size = DEFAULT_PAGE_SIZE

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    paging_complete = False
    page = 1
    seen_empty_after_full = False

    while page <= max_pages:
        raw = http_get_page_fn(page, page_size)
        items = extract_keys_items(raw)
        meta = paging_meta(raw)

        for item in items:
            kid = item.get("id")
            if kid is None:
                # Keep items without id under synthetic key to avoid silent drop of secrets-less junk.
                # They still won't be usable without id for bind, but preserve for completeness.
                sk = f"__noid_{len(order)}"
                if sk not in merged:
                    merged[sk] = dict(item)
                    order.append(sk)
                continue
            nk = norm_id(kid)
            if nk not in merged:
                order.append(nk)
            merged[nk] = dict(item)

        n = len(items)
        declared_size = meta["page_size"] if meta["page_size"] is not None else page_size
        has_more = meta["has_more"]
        total = meta["total"]

        # End conditions with proof. Order matters (P0-2 fail-closed):
        # 1) has_more=True → must continue (even if this page is short)
        # 2) total > merged → must continue or fail incomplete (never treat short page as done)
        # 3) only then short/empty page or has_more=False / total satisfied → complete

        if has_more is True:
            page += 1
            continue

        if total is not None and len(merged) < total:
            # Server claims more items exist than we have merged.
            if n == 0 or n < declared_size:
                # Short/empty page while total unsatisfied: cannot prove full set.
                paging_complete = False
                break
            page += 1
            continue

        if has_more is False:
            paging_complete = True
            break

        if total is not None and len(merged) >= total:
            paging_complete = True
            break

        if n == 0:
            # Empty page without has_more/total conflict: end of list.
            paging_complete = True
            break

        if n < declared_size:
            # Short page only trusted after has_more/total checks above.
            paging_complete = True
            break

        # Full page, no has_more, total absent or already satisfied: fetch next
        # (empty next page proves complete; endless full pages → incomplete at max_pages).
        page += 1
    else:
        # Hit max_pages without proof of end.
        paging_complete = False

    keys = [merged[k] for k in order]
    return keys, paging_complete


# ---------------------------------------------------------------------------
# HTTP client for keys + models
# ---------------------------------------------------------------------------


def _classify_http_error(status: int, *, for_api_key: bool = False) -> str:
    if status == 401:
        return "key_auth" if for_api_key else "auth"
    if status == 403:
        return "key_auth" if for_api_key else "auth"
    if status == 429:
        return "rate_limit"
    if status == 408:
        return "timeout"
    if status >= 500:
        return "server"
    return "error"


@dataclass
class KeysModelsClient:
    """Remote keys CRUD (JWT) and models list (API Key). Injectable session/token/recover."""

    base_url: str
    get_access_token: GetTokenFn
    session: requests.Session | None = None
    recover_auth: RecoverAuthFn | None = None
    keys_path: str = DEFAULT_KEYS_PATH
    models_path: str = DEFAULT_MODELS_PATH
    timeout: tuple[float, float] = (10.0, 30.0)
    time_fn: Callable[[], float] = field(default=time.time)
    _own_session: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
            self._own_session = True
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "Connection": "close",
            }
        )
        self.base_url = self.base_url.rstrip("/")

    def close(self) -> None:
        if self._own_session and self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _jwt_headers(self) -> dict[str, str]:
        token = self.get_access_token()
        if not token:
            raise ApiError("no access token", kind="auth")
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Connection": "close",
            "Content-Type": "application/json",
        }

    def _request_jwt(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        allow_recover: bool = True,
    ) -> requests.Response:
        assert self.session is not None
        tried_recover = False
        while True:
            try:
                response = self.session.request(
                    method,
                    self._url(path),
                    headers=self._jwt_headers(),
                    json=json_body,
                    params=params,
                    timeout=self.timeout,
                )
            except requests.Timeout as exc:
                raise ApiError(f"keys {method} timeout: {type(exc).__name__}", kind="timeout") from exc
            except requests.RequestException as exc:
                raise ApiError(
                    f"keys {method} network error: {type(exc).__name__}",
                    kind="network",
                ) from exc

            if response.status_code in (401, 403) and allow_recover and not tried_recover:
                if self.recover_auth is not None:
                    LOG.warning("keys HTTP %s; recovering JWT once", response.status_code)
                    self.recover_auth()
                    tried_recover = True
                    continue
            return response

    def _raise_keys_status(self, response: requests.Response, op: str) -> None:
        status = response.status_code
        if status in (200, 201):
            return
        kind = _classify_http_error(status, for_api_key=False)
        raise ApiError(f"{op} HTTP {status}", status_code=status, kind=kind)

    def get_keys_page(self, page: int, page_size: int) -> Any:
        response = self._request_jwt(
            "GET",
            self.keys_path,
            params={"p": page, "page": page, "page_size": page_size},
            allow_recover=True,
        )
        self._raise_keys_status(response, "list_keys")
        try:
            return response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise ApiError("keys response is not JSON", kind="contract") from exc

    def list_keys_all(
        self,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[list[dict[str, Any]], bool]:
        return list_keys_all(self.get_keys_page, page_size=page_size)

    def create_key(self, name: str) -> dict[str, Any]:
        response = self._request_jwt(
            "POST",
            self.keys_path,
            json_body={"name": name},
            allow_recover=True,
        )
        self._raise_keys_status(response, "create_key")
        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise ApiError("create_key response is not JSON", kind="contract") from exc
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if isinstance(data, Mapping):
            return dict(data)
        if isinstance(payload, Mapping) and payload.get("id") is not None:
            return dict(payload)
        raise ApiError("create_key response missing data", kind="contract")

    def bind_key(self, key_id: Any, group_id: Any, name: str) -> dict[str, Any]:
        path = f"{self.keys_path.rstrip('/')}/{key_id}"
        response = self._request_jwt(
            "PUT",
            path,
            json_body={"name": name, "group_id": group_id},
            allow_recover=True,
        )
        self._raise_keys_status(response, "bind_key")
        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise ApiError("bind_key response is not JSON", kind="contract") from exc
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if isinstance(data, Mapping):
            return dict(data)
        if isinstance(payload, Mapping):
            return dict(payload)
        raise ApiError("bind_key response structure error", kind="contract")

    def list_models(self, api_key: str) -> list[str]:
        """GET models with API Key Bearer. On 401/403 raise key_auth; NEVER recover JWT."""
        assert self.session is not None
        if not api_key or not str(api_key).strip():
            raise ApiError("empty api key", kind="key_auth")
        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Connection": "close",
        }
        try:
            response = self.session.get(
                self._url(self.models_path),
                headers=headers,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise ApiError(f"models timeout: {type(exc).__name__}", kind="timeout") from exc
        except requests.RequestException as exc:
            raise ApiError(f"models network error: {type(exc).__name__}", kind="network") from exc

        status = response.status_code
        if status in (401, 403):
            raise ApiError(
                f"models HTTP {status}",
                status_code=status,
                kind="key_auth",
            )
        if status == 429:
            raise ApiError("models HTTP 429", status_code=429, kind="rate_limit")
        if status == 408:
            raise ApiError("models HTTP 408", status_code=408, kind="timeout")
        if status >= 500:
            raise ApiError(f"models HTTP {status}", status_code=status, kind="server")
        if status != 200:
            raise ApiError(f"models HTTP {status}", status_code=status, kind="error")

        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise ApiError("models response is not JSON", kind="contract") from exc

        return parse_models_ids(payload)


def parse_models_ids(payload: Any) -> list[str]:
    """Parse models envelope to list of id strings."""
    items: list[Any] | None = None
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, Mapping):
        data = payload.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(data, Mapping):
            for key in ("data", "items", "list", "models"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
        if items is None:
            for key in ("items", "list", "models"):
                if isinstance(payload.get(key), list):
                    items = payload[key]
                    break
    if items is None:
        raise ApiError("models envelope not parseable", kind="contract")

    out: list[str] = []
    for m in items:
        if isinstance(m, dict):
            mid = m.get("id")
            if mid is None:
                mid = m.get("name") or m.get("model")
            if mid is not None and str(mid).strip():
                out.append(str(mid).strip())
        elif isinstance(m, str) and m.strip():
            out.append(m.strip())
    return out


# ---------------------------------------------------------------------------
# Reconcile / ensure coverage
# ---------------------------------------------------------------------------


def _group_id_unbound(key: Mapping[str, Any]) -> bool:
    gid = key.get("group_id")
    return gid is None or gid == ""


def _find_managed(
    keys_all: Sequence[Mapping[str, Any]],
    group_id: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (unbound_managed, bound_to_group_managed)."""
    want = managed_key_name(group_id)
    gid = norm_id(group_id)
    unbound: dict[str, Any] | None = None
    bound: dict[str, Any] | None = None
    for k in keys_all:
        if not isinstance(k, Mapping):
            continue
        if str(k.get("name") or "") != want:
            continue
        if _group_id_unbound(k):
            if unbound is None:
                unbound = dict(k)
        elif norm_id(k.get("group_id")) == gid:
            if bound is None:
                bound = dict(k)
    return unbound, bound


def _bind_group_id(group_id: Any) -> Any:
    """Prefer int when norm id is digits (remote APIs often expect int)."""
    gid = norm_id(group_id)
    if gid.isdigit():
        return int(gid)
    return group_id


def _claim_unbound_managed(
    unbound: Mapping[str, Any],
    group_id: Any,
    *,
    list_keys_fn: ListKeysAllFn,
    bind_fn: BindKeyFn,
    keys: list[dict[str, Any]],
    created: bool,
) -> ReconcileResult:
    gid = norm_id(group_id)
    kid = unbound.get("id")
    name = managed_key_name(gid)
    try:
        bind_fn(kid, _bind_group_id(group_id), name)
    except Exception as exc:
        return ReconcileResult(
            group_id=gid,
            key=None,
            created=created,
            keys_after=keys,
            error=f"bind failed: {exc}",
        )
    try:
        keys_after, _ = list_keys_fn()
    except Exception as exc:
        return ReconcileResult(
            group_id=gid,
            key=None,
            created=created,
            keys_after=keys,
            error=f"re-list after bind failed: {exc}",
        )
    picked = pick_key(gid, keys_after)
    if picked is not None:
        return ReconcileResult(
            group_id=gid,
            key=picked,
            created=created,
            keys_after=keys_after,
            bound=True,
        )
    return ReconcileResult(
        group_id=gid,
        key=None,
        created=created,
        keys_after=keys_after,
        error="bind verified but key not usable (missing secret?)",
    )


def _recover_after_unknown_create(
    group_id: Any,
    *,
    list_keys_fn: ListKeysAllFn,
    bind_fn: BindKeyFn,
    keys: list[dict[str, Any]],
    err_msg: str,
) -> ReconcileResult:
    """After timeout/5xx/network on POST: re-list and claim; never re-POST."""
    gid = norm_id(group_id)
    try:
        keys, _ = list_keys_fn()
    except Exception as list_exc:
        return ReconcileResult(
            group_id=gid,
            key=None,
            created=False,
            keys_after=keys,
            error=f"create unknown outcome and re-list failed: {list_exc}",
        )
    unbound, bound_managed = _find_managed(keys, gid)
    if bound_managed is not None:
        picked = pick_key(gid, keys)
        return ReconcileResult(
            group_id=gid,
            key=picked or bound_managed,
            created=False,
            keys_after=keys,
            bound=True,
        )
    if unbound is not None:
        return _claim_unbound_managed(
            unbound,
            group_id,
            list_keys_fn=list_keys_fn,
            bind_fn=bind_fn,
            keys=keys,
            created=False,
        )
    return ReconcileResult(
        group_id=gid,
        key=None,
        created=False,
        keys_after=keys,
        error=f"create unknown outcome, no managed key found: {err_msg}",
    )


@dataclass
class ReconcileResult:
    group_id: str
    key: dict[str, Any] | None
    created: bool
    keys_after: list[dict[str, Any]]
    error: str | None = None
    bound: bool = False


def reconcile_key_for_group(
    group_id: Any,
    keys_all: Sequence[Mapping[str, Any]],
    *,
    list_keys_fn: ListKeysAllFn,
    create_fn: CreateKeyFn,
    bind_fn: BindKeyFn,
) -> ReconcileResult:
    """Idempotent ensure one usable key for group (design §5.3)."""
    gid = norm_id(group_id)
    keys: list[dict[str, Any]] = [dict(k) for k in keys_all if isinstance(k, Mapping)]

    def _ok(key: dict[str, Any] | None, created: bool, keys_after: list[dict[str, Any]]) -> ReconcileResult:
        return ReconcileResult(
            group_id=gid,
            key=key,
            created=created,
            keys_after=keys_after,
            bound=key is not None,
        )

    # 1. usable non-empty → return existing
    existing = usable_keys(gid, keys)
    if existing:
        return _ok(existing[0], False, keys)

    # 2. claim unbound managed name → bind → re-list
    unbound, bound_managed = _find_managed(keys, gid)
    if unbound is not None:
        return _claim_unbound_managed(
            unbound,
            group_id,
            list_keys_fn=list_keys_fn,
            bind_fn=bind_fn,
            keys=keys,
            created=False,
        )

    # 3. already bound managed → return (even without secret — still no create)
    if bound_managed is not None:
        picked = pick_key(gid, keys)
        if picked is not None:
            return _ok(picked, False, keys)
        return _ok(bound_managed, False, keys)

    # 4. POST create; on unknown outcome re-list before re-POST
    name = managed_key_name(gid)
    created_key: Mapping[str, Any] | None = None
    try:
        created_key = create_fn(name)
    except ApiError as exc:
        if exc.kind in ("timeout", "server", "network") or (
            exc.status_code is not None and exc.status_code >= 500
        ):
            return _recover_after_unknown_create(
                group_id,
                list_keys_fn=list_keys_fn,
                bind_fn=bind_fn,
                keys=keys,
                err_msg=str(exc),
            )
        return ReconcileResult(
            group_id=gid,
            key=None,
            created=False,
            keys_after=keys,
            error=f"create failed: {exc}",
        )
    except (requests.Timeout, requests.RequestException) as exc:
        kind = "timeout" if isinstance(exc, requests.Timeout) else "network"
        return _recover_after_unknown_create(
            group_id,
            list_keys_fn=list_keys_fn,
            bind_fn=bind_fn,
            keys=keys,
            err_msg=f"{kind}: {exc}",
        )

    created_flag = True
    kid = created_key.get("id") if created_key is not None else None
    if kid is None:
        try:
            keys, _ = list_keys_fn()
        except Exception as exc:
            return ReconcileResult(
                group_id=gid,
                key=None,
                created=created_flag,
                keys_after=keys,
                error=f"re-list after create failed: {exc}",
            )
        unbound, bound_managed = _find_managed(keys, gid)
        if bound_managed is not None:
            picked = pick_key(gid, keys)
            return _ok(picked or bound_managed, created_flag, keys)
        if unbound is not None:
            return _claim_unbound_managed(
                unbound,
                group_id,
                list_keys_fn=list_keys_fn,
                bind_fn=bind_fn,
                keys=keys,
                created=created_flag,
            )
        return ReconcileResult(
            group_id=gid,
            key=None,
            created=created_flag,
            keys_after=keys,
            error="create succeeded but key not found on re-list",
        )

    # 5. bind; re-list verify usable
    try:
        bind_fn(kid, _bind_group_id(group_id), name)
    except Exception as exc:
        try:
            keys, _ = list_keys_fn()
        except Exception:
            pass
        return ReconcileResult(
            group_id=gid,
            key=None,
            created=created_flag,
            keys_after=keys,
            error=f"bind failed: {exc}",
        )

    try:
        keys, _ = list_keys_fn()
    except Exception as exc:
        return ReconcileResult(
            group_id=gid,
            key=None,
            created=created_flag,
            keys_after=keys,
            error=f"re-list after bind failed: {exc}",
        )

    picked = pick_key(gid, keys)
    if picked is None:
        return ReconcileResult(
            group_id=gid,
            key=None,
            created=created_flag,
            keys_after=keys,
            error="bind verified but key not usable (missing secret?)",
        )
    return _ok(picked, created_flag, keys)


@dataclass
class EnsureCoverageResult:
    keys: list[dict[str, Any]]
    created: int
    paging_incomplete: bool
    failures: list[dict[str, Any]] = field(default_factory=list)
    reconciled: list[str] = field(default_factory=list)


def ensure_coverage(
    groups: Sequence[Mapping[str, Any] | Any],
    *,
    list_keys_fn: ListKeysAllFn,
    create_fn: CreateKeyFn,
    bind_fn: BindKeyFn,
    keys_all: Sequence[Mapping[str, Any]] | None = None,
    paging_complete: bool | None = None,
) -> EnsureCoverageResult:
    """Ensure usable key for each group; fail closed on incomplete paging."""
    if keys_all is None or paging_complete is None:
        keys, complete = list_keys_fn()
    else:
        keys = [dict(k) for k in keys_all if isinstance(k, Mapping)]
        complete = paging_complete

    group_ids: list[Any] = []
    for g in groups:
        if isinstance(g, Mapping):
            if g.get("id") is None:
                continue
            group_ids.append(g.get("id"))
        else:
            group_ids.append(g)

    if not complete:
        return EnsureCoverageResult(
            keys=keys,
            created=0,
            paging_incomplete=True,
            failures=[{"error": "paging_incomplete", "message": "list_keys_all incomplete; create aborted"}],
        )

    created_count = 0
    failures: list[dict[str, Any]] = []
    reconciled: list[str] = []

    for gid in group_ids:
        if usable_keys(gid, keys):
            continue
        try:
            result = reconcile_key_for_group(
                gid,
                keys,
                list_keys_fn=list_keys_fn,
                create_fn=create_fn,
                bind_fn=bind_fn,
            )
            keys = result.keys_after
            if result.created:
                created_count += 1
            if result.error:
                failures.append({"group_id": norm_id(gid), "error": result.error})
            else:
                reconciled.append(norm_id(gid))
        except Exception as exc:
            failures.append({"group_id": norm_id(gid), "error": str(exc)})
            # Continue other groups; try re-list for freshness
            try:
                keys, _ = list_keys_fn()
            except Exception:
                pass

    return EnsureCoverageResult(
        keys=keys,
        created=created_count,
        paging_incomplete=False,
        failures=failures,
        reconciled=reconciled,
    )


# ---------------------------------------------------------------------------
# ModelsStore
# ---------------------------------------------------------------------------


def empty_models_record(
    site_id: str,
    *,
    models_path: str = DEFAULT_MODELS_PATH,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "site_id": site_id,
        "updated_at": utc_now_iso(),
        "bootstrap_completed_at": None,
        "last_full_attempt_at": None,
        "last_full_success_at": None,
        "last_full_result": None,
        "last_incremental_at": None,
        "models_path": models_path,
        "models_by_group": {},
    }


def empty_group_entry() -> dict[str, Any]:
    return {
        "key_id": None,
        "models": None,
        "content_hash": None,
        "last_success_at": None,
        "last_attempt_at": None,
        "last_error": None,
        "next_retry_at": None,
        "source": None,
    }


def should_attempt_now(
    group_entry: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Respect next_retry_at; missing/null means allowed."""
    if not group_entry:
        return True
    nra = group_entry.get("next_retry_at")
    if nra is None or nra == "":
        return True
    try:
        retry_at = datetime.fromisoformat(str(nra).replace("Z", "+00:00"))
    except ValueError:
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now >= retry_at


def compute_next_retry_at(
    error_kind: str | None,
    *,
    now_iso: str | None = None,
) -> str | None:
    """Heuristic cooldown for failures (design §4.5)."""
    from datetime import timedelta

    base = now_iso or utc_now_iso()
    try:
        now = datetime.fromisoformat(base.replace("Z", "+00:00"))
    except ValueError:
        now = datetime.now(timezone.utc)
    kind = (error_kind or "").lower()
    if kind in ("timeout", "server", "network"):
        delta = timedelta(minutes=45)
    elif kind in ("rate_limit",):
        delta = timedelta(minutes=15)
    elif kind in ("contract", "no_usable_key", "key_auth", "auth"):
        delta = timedelta(hours=24)
    else:
        delta = timedelta(hours=6)
    target = (now + delta).replace(microsecond=0)
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ModelsStore:
    """models_latest.json + models_events.jsonl with per-group checkpoint."""

    def __init__(
        self,
        data_dir: Path | str,
        site_id: str,
        *,
        models_path: str = DEFAULT_MODELS_PATH,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.site_id = site_id
        self.models_path = models_path
        self.latest_path = self.data_dir / "models_latest.json"
        self.events_path = self.data_dir / "models_events.jsonl"
        self._record: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        if self._record is not None:
            return self._record
        if not self.latest_path.exists():
            self._record = empty_models_record(self.site_id, models_path=self.models_path)
            return self._record
        try:
            raw = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._record = empty_models_record(self.site_id, models_path=self.models_path)
            return self._record
        if not isinstance(raw, dict):
            self._record = empty_models_record(self.site_id, models_path=self.models_path)
            return self._record
        if "models_by_group" not in raw or not isinstance(raw.get("models_by_group"), dict):
            raw["models_by_group"] = {}
        raw.setdefault("schema_version", 1)
        raw.setdefault("site_id", self.site_id)
        self._record = raw
        return self._record

    def save(self) -> None:
        rec = self.load()
        rec["updated_at"] = utc_now_iso()
        rec["site_id"] = self.site_id
        self.data_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.latest_path, rec, mode=0o644)
        self._record = rec

    def get_group(self, group_id: Any) -> dict[str, Any]:
        rec = self.load()
        mbg = rec["models_by_group"]
        gid = norm_id(group_id)
        entry = mbg.get(gid)
        if not isinstance(entry, dict):
            return empty_group_entry()
        return dict(entry)

    def apply_success(
        self,
        group_id: Any,
        models: Sequence[str],
        *,
        key_id: Any = None,
        source: str = "daily",
        now: str | None = None,
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        """Update success fields; emit event on content_hash change; optional checkpoint."""
        rec = self.load()
        gid = norm_id(group_id)
        mbg = rec.setdefault("models_by_group", {})
        prev = dict(mbg.get(gid) or empty_group_entry())
        now_s = now or utc_now_iso()
        models_list = [str(m) for m in models]
        new_hash = content_hash_models(models_list)
        old_hash = prev.get("content_hash")
        old_models = prev.get("models")

        entry = {
            "key_id": key_id,
            "models": models_list,
            "content_hash": new_hash,
            "last_success_at": now_s,
            "last_attempt_at": now_s,
            "last_error": None,
            "next_retry_at": None,
            "source": source,
        }
        mbg[gid] = entry

        if new_hash != old_hash:
            event_name = "initial" if old_hash is None else "models_changed"
            added: list[str] = []
            removed: list[str] = []
            truncated = False
            if isinstance(old_models, list):
                old_set = set(str(x) for x in old_models)
                new_set = set(models_list)
                added = sorted(new_set - old_set)
                removed = sorted(old_set - new_set)
            else:
                added = sorted(models_list)
            if len(added) > DEFAULT_EVENT_MODELS_DIFF_CAP:
                added = added[:DEFAULT_EVENT_MODELS_DIFF_CAP]
                truncated = True
            if len(removed) > DEFAULT_EVENT_MODELS_DIFF_CAP:
                removed = removed[:DEFAULT_EVENT_MODELS_DIFF_CAP]
                truncated = True
            event = {
                "site_id": self.site_id,
                "observed_at": now_s,
                "event": event_name,
                "group_id": gid,
                "key_id": key_id,
                "model_count": len(models_list),
                "content_hash": new_hash,
                "source": source,
                "added_models": added,
                "removed_models": removed,
                "truncated": truncated,
            }
            append_jsonl_fsync(self.events_path, event)

        if checkpoint:
            self.save()
        return entry

    def apply_failure(
        self,
        group_id: Any,
        error: str,
        *,
        source: str | None = None,
        next_retry_at: str | None = None,
        error_kind: str | None = None,
        now: str | None = None,
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        """Only update attempt/error/retry; NEVER clear success models/hash/key_id."""
        rec = self.load()
        gid = norm_id(group_id)
        mbg = rec.setdefault("models_by_group", {})
        prev = dict(mbg.get(gid) or empty_group_entry())
        now_s = now or utc_now_iso()
        nra = next_retry_at
        if nra is None and error_kind is not None:
            nra = compute_next_retry_at(error_kind, now_iso=now_s)
        entry = {
            "key_id": prev.get("key_id"),
            "models": prev.get("models"),  # may be null or list — preserve
            "content_hash": prev.get("content_hash"),
            "last_success_at": prev.get("last_success_at"),
            "last_attempt_at": now_s,
            "last_error": error,
            "next_retry_at": nra if nra is not None else prev.get("next_retry_at"),
            "source": source if source is not None else prev.get("source"),
        }
        mbg[gid] = entry
        if checkpoint:
            self.save()
        return entry

    def update_full_meta(
        self,
        *,
        target: int,
        ok: int,
        failed: int,
        bootstrap: bool = False,
        now: str | None = None,
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        """Update full-batch meta. last_full_success_at only when all ok."""
        rec = self.load()
        now_s = now or utc_now_iso()
        rec["last_full_attempt_at"] = now_s
        rec["last_full_result"] = {"target": target, "ok": ok, "failed": failed}
        if failed == 0 and target > 0 and ok == target:
            rec["last_full_success_at"] = now_s
            if bootstrap:
                rec["bootstrap_completed_at"] = now_s
        elif failed == 0 and target == 0 and bootstrap:
            # Empty site: still mark bootstrap complete if intentional full run with 0 groups
            rec["last_full_success_at"] = now_s
            rec["bootstrap_completed_at"] = now_s
        if checkpoint:
            self.save()
        return rec

    def mark_bootstrap_completed(self, *, now: str | None = None, checkpoint: bool = True) -> None:
        rec = self.load()
        rec["bootstrap_completed_at"] = now or utc_now_iso()
        if checkpoint:
            self.save()

    def set_incremental_at(self, *, now: str | None = None, checkpoint: bool = True) -> None:
        rec = self.load()
        rec["last_incremental_at"] = now or utc_now_iso()
        if checkpoint:
            self.save()


# ---------------------------------------------------------------------------
# refresh_models_for_groups
# ---------------------------------------------------------------------------


@dataclass
class RefreshModelsResult:
    ok_count: int
    failed_count: int
    skipped_deadline: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def refresh_models_for_groups(
    groups: Sequence[Mapping[str, Any] | Any],
    keys_all: Sequence[Mapping[str, Any]],
    store: ModelsStore,
    list_models_fn: ListModelsFn,
    *,
    source: str = "daily",
    deadline: float | None = None,
    time_fn: Callable[[], float] | None = None,
) -> RefreshModelsResult:
    """Serial per-group models refresh; key 401 → next key; never JWT."""
    tfn = time_fn or time.time
    ok = 0
    failed = 0
    skipped = 0
    errors: list[dict[str, Any]] = []

    group_ids: list[Any] = []
    for g in groups:
        if isinstance(g, Mapping):
            if g.get("id") is not None:
                group_ids.append(g.get("id"))
        else:
            group_ids.append(g)

    for gid in group_ids:
        if deadline is not None and tfn() >= deadline:
            skipped += 1
            # stop starting new groups
            remaining = len(group_ids) - (ok + failed + skipped) + 1
            # count this and rest as skipped? Design: stop starting new; current not started.
            skipped += max(0, remaining - 1)
            break

        candidates = usable_keys(gid, keys_all)
        if not candidates:
            store.apply_failure(
                gid,
                "no_usable_key",
                source=source,
                error_kind="no_usable_key",
                checkpoint=True,
            )
            failed += 1
            errors.append({"group_id": norm_id(gid), "error": "no_usable_key"})
            continue

        last_err: str | None = None
        last_kind: str | None = None
        success = False
        for key in candidates:
            secret = key_secret(key)
            if not secret:
                continue
            try:
                models = list_models_fn(secret)
                store.apply_success(
                    gid,
                    models,
                    key_id=key.get("id"),
                    source=source,
                    checkpoint=True,
                )
                ok += 1
                success = True
                break
            except ApiError as exc:
                last_err = str(exc)
                last_kind = exc.kind
                if exc.kind == "key_auth":
                    # try next candidate; never JWT
                    continue
                # non-auth key errors: stop trying other keys for this group
                break
            except Exception as exc:
                last_err = str(exc)
                last_kind = "error"
                break

        if not success:
            if last_kind == "key_auth":
                err_msg = f"key_auth: {last_err}" if last_err else "key_auth"
            else:
                err_msg = last_err or "no_usable_key"
            store.apply_failure(
                gid,
                err_msg,
                source=source,
                error_kind=last_kind or "error",
                checkpoint=True,
            )
            failed += 1
            errors.append({"group_id": norm_id(gid), "error": err_msg, "kind": last_kind})

    return RefreshModelsResult(
        ok_count=ok,
        failed_count=failed,
        skipped_deadline=skipped,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Preflight (read-only)
# ---------------------------------------------------------------------------


@dataclass
class PreflightResult:
    ok: bool
    checks: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": self.checks,
            "failures": list(self.failures),
        }


def preflight_checks(
    *,
    groups_fn: Callable[[], Sequence[Mapping[str, Any]]],
    list_keys_fn: ListKeysAllFn,
    list_models_fn: ListModelsFn | None = None,
) -> PreflightResult:
    """Read-only capability checks. Never creates keys."""
    checks: dict[str, Any] = {
        "groups_ok": False,
        "groups_count": 0,
        "paging_complete": False,
        "keys_count": 0,
        "secret_readable": False,
        "bound_with_secret": 0,
        "models_envelope_ok": None,
    }
    failures: list[str] = []

    try:
        groups = list(groups_fn())
        if not isinstance(groups, list) and not isinstance(groups, Sequence):
            failures.append("groups not a list")
        else:
            # ensure list-like of mappings
            ok_groups = [g for g in groups if isinstance(g, Mapping)]
            checks["groups_ok"] = True
            checks["groups_count"] = len(ok_groups)
            groups = ok_groups
    except Exception as exc:
        failures.append(f"groups failed: {exc}")
        groups = []

    keys: list[dict[str, Any]] = []
    complete = False
    try:
        keys, complete = list_keys_fn()
        checks["paging_complete"] = complete
        checks["keys_count"] = len(keys)
        if not complete:
            failures.append("paging_incomplete")
    except Exception as exc:
        failures.append(f"list_keys failed: {exc}")

    bound_secret = 0
    sample_secret: str | None = None
    for k in keys:
        if not isinstance(k, Mapping):
            continue
        gid = k.get("group_id")
        if gid is None or gid == "":
            continue
        sec = key_secret(k)
        if sec:
            bound_secret += 1
            if sample_secret is None:
                sample_secret = sec
    checks["bound_with_secret"] = bound_secret
    checks["secret_readable"] = bound_secret > 0
    if bound_secret == 0:
        failures.append("secret_not_readable")

    if sample_secret and list_models_fn is not None:
        try:
            models = list_models_fn(sample_secret)
            checks["models_envelope_ok"] = isinstance(models, list)
            if not isinstance(models, list):
                failures.append("models_envelope_unparseable")
        except Exception as exc:
            checks["models_envelope_ok"] = False
            failures.append(f"models envelope failed: {exc}")
    elif sample_secret and list_models_fn is None:
        checks["models_envelope_ok"] = None  # not probed
    else:
        # No usable key: cannot verify models envelope; document
        checks["models_envelope_ok"] = None

    ok = (
        checks["groups_ok"]
        and checks["paging_complete"]
        and checks["secret_readable"]
        and (
            checks["models_envelope_ok"] is True
            or (checks["models_envelope_ok"] is None and list_models_fn is None)
        )
    )
    # If we have secret and list_models_fn, require models_envelope_ok
    if sample_secret and list_models_fn is not None:
        ok = ok and checks["models_envelope_ok"] is True
    # Without secret, always fail
    if not checks["secret_readable"]:
        ok = False
    if not checks["groups_ok"] or not checks["paging_complete"]:
        ok = False

    return PreflightResult(ok=ok, checks=checks, failures=failures)


__all__ = [
    "DEFAULT_KEYS_PATH",
    "DEFAULT_MODELS_PATH",
    "EnsureCoverageResult",
    "KeysModelsClient",
    "ModelsStore",
    "PreflightResult",
    "ReconcileResult",
    "RefreshModelsResult",
    "content_hash_models",
    "ensure_coverage",
    "extract_keys_items",
    "is_managed_key_name",
    "key_secret",
    "list_keys_all",
    "managed_key_name",
    "norm_id",
    "parse_models_ids",
    "pick_key",
    "preflight_checks",
    "reconcile_key_for_group",
    "refresh_models_for_groups",
    "should_attempt_now",
    "usable_keys",
]
