#!/usr/bin/env python3
"""New-API token coverage and per-group model snapshot primitives.

The monitor owns authenticated HTTP and injects token/model callables here.
Plaintext token secrets exist only in transient dictionaries returned by
``hydrate_tokens`` and are never included in snapshot or event records.

The managed create and repair payloads follow New-API rc.21 source behavior.
The deployed site's PUT and name-boundary behavior were not live-written during
the read-only contract probe, so callers must keep production writes behind the
explicit bootstrap/refresh gates described in the requirements.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import requests

from monitor_storage import append_jsonl_fsync, utc_now_iso, write_json_atomic


MANAGED_TOKEN_PREFIX = "newapi-monitor:g:"
MAX_TOKEN_NAME_BYTES = 50
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 500
DEFAULT_MODELS_PATH = "/v1/models"
DEFAULT_EVENT_MODELS_DIFF_CAP = 50

_SECRET_FIELD = "_newapi_hydrated_secret"
_SECRET_ERROR_FIELD = "_newapi_hydration_error"

ListTokensPageFn = Callable[[int, int], Mapping[str, Any]]
ListTokensAllFn = Callable[[], tuple[list[dict[str, Any]], bool]]
GetTokenSecretFn = Callable[[Any], str]
CreateTokenFn = Callable[[Mapping[str, Any]], Mapping[str, Any]]
UpdateTokenFn = Callable[..., Mapping[str, Any]]
ListModelsFn = Callable[[str], Sequence[str]]


# ---------------------------------------------------------------------------
# Pure token helpers
# ---------------------------------------------------------------------------


def norm_group(value: Any) -> str:
    return str(value).strip()


def norm_id(value: Any) -> str:
    return str(value).strip()


def _utf8_prefix(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    raw = value.encode("utf-8")[:max_bytes]
    while raw:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw[:-1]
    return ""


def managed_token_name(group: Any) -> str:
    """Return a deterministic managed name within New-API's 50-byte limit."""
    normalized = norm_group(group)
    direct = MANAGED_TOKEN_PREFIX + normalized
    if len(direct.encode("utf-8")) <= MAX_TOKEN_NAME_BYTES:
        return direct

    # Source-derived deployment assumption: 20 UTF-8-safe group bytes, a
    # separator, and 12 hex digest bytes keep the complete name at <= 50 bytes.
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    suffix_budget = MAX_TOKEN_NAME_BYTES - len(MANAGED_TOKEN_PREFIX.encode("ascii"))
    visible_budget = suffix_budget - 1 - len(digest)
    return f"{MANAGED_TOKEN_PREFIX}{_utf8_prefix(normalized, visible_budget)}:{digest}"


def is_managed_token_name(name: Any) -> bool:
    if not isinstance(name, str) or not name.startswith(MANAGED_TOKEN_PREFIX):
        return False
    return bool(name[len(MANAGED_TOKEN_PREFIX) :]) and len(name.encode("utf-8")) <= 50


def token_secret(token: Mapping[str, Any]) -> str | None:
    secret = token.get(_SECRET_FIELD)
    if isinstance(secret, str) and secret.strip():
        return secret.strip()
    return None


def _id_sort_key(token: Mapping[str, Any]) -> tuple[int, int | str]:
    value = norm_id(token.get("id", ""))
    if value.isdigit():
        return (0, int(value))
    return (1, value)


def _strict_int(value: Any) -> bool:
    return type(value) is int


def _inventory_fields_suitable(
    group: Any,
    token: Mapping[str, Any],
    *,
    now: float,
    require_secret: bool,
) -> bool:
    wanted = norm_group(group)
    token_group = token.get("group")
    if not isinstance(token_group, str) or norm_group(token_group) != wanted:
        return False
    if token.get("id") is None or not norm_id(token.get("id")):
        return False
    if not _strict_int(token.get("status")) or token.get("status") != 1:
        return False

    expired_time = token.get("expired_time")
    if not _strict_int(expired_time):
        return False
    if expired_time != -1 and expired_time <= now:
        return False

    if token.get("model_limits_enabled") is not False:
        return False
    allow_ips = token.get("allow_ips")
    if not isinstance(allow_ips, str) or "".join(allow_ips.split()):
        return False

    unlimited_quota = token.get("unlimited_quota")
    remain_quota = token.get("remain_quota")
    if not isinstance(unlimited_quota, bool) or not _strict_int(remain_quota):
        return False
    if not unlimited_quota and remain_quota <= 0:
        return False

    if require_secret and token_secret(token) is None:
        return False
    return True


def inventory_suitable_tokens(
    group: Any,
    tokens: Sequence[Mapping[str, Any]],
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    now_value = time.time() if now is None else now
    suitable = [
        dict(token)
        for token in tokens
        if isinstance(token, Mapping)
        and _inventory_fields_suitable(group, token, now=now_value, require_secret=True)
    ]
    suitable.sort(key=_id_sort_key)
    return suitable


def pick_inventory_token(
    group: Any,
    tokens: Sequence[Mapping[str, Any]],
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    suitable = inventory_suitable_tokens(group, tokens, now=now)
    return suitable[0] if suitable else None


# ---------------------------------------------------------------------------
# Token list pagination and hydration
# ---------------------------------------------------------------------------


def _parse_token_page(
    payload: Any,
    *,
    requested_page: int,
) -> tuple[list[dict[str, Any]], int, int, bool | None] | None:
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        return None
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    items = data.get("items")
    total = data.get("total")
    page = data.get("page")
    page_size = data.get("page_size")
    if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
        return None
    if not _strict_int(total) or total < 0:
        return None
    if not _strict_int(page) or page != requested_page:
        return None
    if not _strict_int(page_size) or page_size <= 0:
        return None
    has_more = data.get("has_more")
    if has_more is not None and not isinstance(has_more, bool):
        return None
    return [dict(item) for item in items], total, page_size, has_more


def list_tokens_all(
    get_page_fn: ListTokensPageFn,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch every token from the strict New-API envelope, failing closed."""
    if page_size < 1:
        page_size = DEFAULT_PAGE_SIZE
    if max_pages < 1:
        return [], False

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    expected_total: int | None = None
    seen_fingerprints: set[tuple[str, ...]] = set()

    for page in range(1, max_pages + 1):
        try:
            payload = get_page_fn(page, page_size)
        except Exception:
            return [merged[key] for key in order], False
        parsed = _parse_token_page(payload, requested_page=page)
        if parsed is None:
            return [merged[key] for key in order], False
        items, total, declared_size, has_more = parsed
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            return [merged[key] for key in order], False

        page_ids: list[str] = []
        progress = 0
        for item in items:
            if not _strict_int(item.get("id")) or item.get("id") <= 0:
                return [merged[key] for key in order], False
            token_id = norm_id(item.get("id"))
            if not token_id:
                return [merged[key] for key in order], False
            page_ids.append(token_id)
            if token_id not in merged:
                order.append(token_id)
                progress += 1
            merged[token_id] = item

        fingerprint = tuple(page_ids)
        if fingerprint in seen_fingerprints and (has_more is True or len(merged) < total):
            return [merged[key] for key in order], False
        seen_fingerprints.add(fingerprint)

        if len(merged) > total:
            return [merged[key] for key in order], False
        if has_more is True:
            if progress == 0:
                return [merged[key] for key in order], False
            continue
        if len(merged) >= total:
            return [merged[key] for key in order], True
        if has_more is False or not items or len(items) < declared_size or progress == 0:
            return [merged[key] for key in order], False

    return [merged[key] for key in order], False


@dataclass
class HydrationResult:
    tokens: list[dict[str, Any]]
    failures: dict[str, str] = field(default_factory=dict)


def hydrate_tokens(
    tokens: Sequence[Mapping[str, Any]],
    *,
    get_token_secret_fn: GetTokenSecretFn,
) -> HydrationResult:
    """Hydrate list tokens in memory; masked list keys are never accepted."""
    hydrated: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    for index, source in enumerate(tokens):
        if not isinstance(source, Mapping):
            continue
        token = dict(source)
        token.pop(_SECRET_FIELD, None)
        token.pop(_SECRET_ERROR_FIELD, None)
        token_id = token.get("id")
        failure_key = norm_id(token_id) if token_id is not None else f"index:{index}"
        if token_id is None or not norm_id(token_id):
            failures[failure_key] = "missing_token_id"
            token[_SECRET_ERROR_FIELD] = "missing_token_id"
            hydrated.append(token)
            continue
        try:
            secret = get_token_secret_fn(token_id)
            if not isinstance(secret, str) or not secret.strip():
                raise ValueError("empty token secret")
            token[_SECRET_FIELD] = secret.strip()
        except Exception as exc:
            kind = str(getattr(exc, "kind", "") or type(exc).__name__).lower()
            failures[failure_key] = kind
            token[_SECRET_ERROR_FIELD] = kind
        hydrated.append(token)
    return HydrationResult(tokens=hydrated, failures=failures)


def _group_from_record(group: Mapping[str, Any] | Any) -> str:
    if isinstance(group, Mapping):
        value = group.get("id")
        if value is None:
            value = group.get("name")
        return norm_group(value if value is not None else "")
    return norm_group(group)


def _otherwise_suitable_hydration_failure(
    group: str,
    token: Mapping[str, Any],
    *,
    now: float,
) -> bool:
    return token_secret(token) is None and _inventory_fields_suitable(
        group,
        token,
        now=now,
        require_secret=False,
    )


# ---------------------------------------------------------------------------
# Managed reconcile and coverage
# ---------------------------------------------------------------------------


def create_token_payload(group: Any) -> dict[str, Any]:
    normalized = norm_group(group)
    return {
        "name": managed_token_name(normalized),
        "remain_quota": 0,
        "expired_time": -1,
        "unlimited_quota": True,
        "model_limits_enabled": False,
        "model_limits": "",
        "allow_ips": "",
        "group": normalized,
        "cross_group_retry": False,
    }


def repair_token_payload(token_id: Any, group: Any) -> dict[str, Any]:
    normalized = norm_group(group)
    return {
        "id": token_id,
        "name": managed_token_name(normalized),
        "expired_time": -1,
        "remain_quota": 0,
        "unlimited_quota": True,
        "model_limits_enabled": False,
        "model_limits": "",
        "allow_ips": "",
        "group": normalized,
        "cross_group_retry": False,
    }


def _managed_full_repair_needed(token: Mapping[str, Any], group: str) -> bool:
    return any(
        (
            norm_group(token.get("group", "")) != group,
            token.get("name") != managed_token_name(group),
            token.get("expired_time") != -1,
            token.get("remain_quota") != 0,
            token.get("unlimited_quota") is not True,
            token.get("model_limits_enabled") is not False,
            token.get("model_limits") != "",
            token.get("allow_ips") != "",
            token.get("cross_group_retry") is not False,
            token.get("status") in (3, 4),
        )
    )


def _is_unknown_write_error(exc: BaseException) -> bool:
    if isinstance(exc, requests.RequestException):
        return True
    kind = str(getattr(exc, "kind", "") or "").lower()
    status = getattr(exc, "status_code", None)
    return kind in {"timeout", "network", "server"} or (
        _strict_int(status) and status >= 500
    )


def _relist_and_hydrate(
    *,
    list_tokens_fn: ListTokensAllFn,
    get_token_secret_fn: GetTokenSecretFn,
) -> tuple[list[dict[str, Any]], bool]:
    try:
        listed, complete = list_tokens_fn()
    except Exception:
        return [], False
    if not complete:
        return [dict(token) for token in listed], False
    return hydrate_tokens(listed, get_token_secret_fn=get_token_secret_fn).tokens, True


@dataclass
class ReconcileTokenResult:
    group: str
    token: dict[str, Any] | None
    created: bool
    updated: bool
    tokens_after: list[dict[str, Any]]
    error: str | None = None
    paging_incomplete: bool = False


def reconcile_token_for_group(
    group: Any,
    hydrated_tokens: Sequence[Mapping[str, Any]],
    *,
    list_tokens_fn: ListTokensAllFn,
    get_token_secret_fn: GetTokenSecretFn,
    create_token_fn: CreateTokenFn,
    update_token_fn: UpdateTokenFn,
) -> ReconcileTokenResult:
    """Repair an exact managed token or create one, then re-list and hydrate."""
    normalized = norm_group(group)
    current = [dict(token) for token in hydrated_tokens if isinstance(token, Mapping)]
    existing = pick_inventory_token(normalized, current)
    if existing is not None:
        return ReconcileTokenResult(normalized, existing, False, False, current)

    wanted_name = managed_token_name(normalized)
    managed = [token for token in current if token.get("name") == wanted_name]
    managed.sort(key=_id_sort_key)
    if managed:
        updated = False
        repair_errors: list[str] = []
        for selected in managed:
            token_id = selected.get("id")
            if token_id is None:
                repair_errors.append("managed token missing id")
                continue
            full_repair = _managed_full_repair_needed(selected, normalized)
            status_repair = selected.get("status") != 1
            full_repair_allows_status = True
            if full_repair:
                updated = True
                try:
                    update_token_fn(
                        repair_token_payload(token_id, normalized),
                        status_only=False,
                    )
                except Exception as exc:
                    repair_errors.append(
                        f"managed token full repair failed: {type(exc).__name__}"
                    )
                    full_repair_allows_status = _is_unknown_write_error(exc)
            if status_repair:
                updated = True
                if full_repair_allows_status:
                    try:
                        update_token_fn(
                            {"id": token_id, "status": 1},
                            status_only=True,
                        )
                    except Exception as exc:
                        repair_errors.append(
                            f"managed token status repair failed: {type(exc).__name__}"
                        )

        tokens_after, complete = _relist_and_hydrate(
            list_tokens_fn=list_tokens_fn,
            get_token_secret_fn=get_token_secret_fn,
        )
        if not complete:
            return ReconcileTokenResult(
                normalized,
                None,
                False,
                updated,
                tokens_after,
                "re-list after repair incomplete",
                paging_incomplete=True,
            )
        exact_after = [token for token in tokens_after if token.get("name") == wanted_name]
        unsuitable_after = [
            token
            for token in exact_after
            if not _inventory_fields_suitable(
                normalized,
                token,
                now=time.time(),
                require_secret=True,
            )
        ]
        repaired = pick_inventory_token(normalized, exact_after)
        if repaired is None or unsuitable_after:
            detail = repair_errors[0] if repair_errors else "verification failed"
            return ReconcileTokenResult(
                normalized,
                None,
                False,
                updated,
                tokens_after,
                f"managed token repair did not converge: {detail}",
            )
        return ReconcileTokenResult(normalized, repaired, False, updated, tokens_after)

    unknown_outcome = False
    try:
        create_token_fn(create_token_payload(normalized))
    except Exception as exc:
        if not _is_unknown_write_error(exc):
            return ReconcileTokenResult(
                normalized,
                None,
                False,
                False,
                current,
                f"managed token create failed: {type(exc).__name__}",
            )
        unknown_outcome = True

    tokens_after, complete = _relist_and_hydrate(
        list_tokens_fn=list_tokens_fn,
        get_token_secret_fn=get_token_secret_fn,
    )
    if not complete:
        return ReconcileTokenResult(
            normalized,
            None,
            not unknown_outcome,
            False,
            tokens_after,
            "re-list after create incomplete",
            paging_incomplete=True,
        )
    managed_after = [
        token
        for token in inventory_suitable_tokens(normalized, tokens_after)
        if token.get("name") == wanted_name
    ]
    if not managed_after:
        message = (
            "create outcome unknown and no matching managed token"
            if unknown_outcome
            else "created managed token not readable or inventory-suitable"
        )
        return ReconcileTokenResult(
            normalized, None, not unknown_outcome, False, tokens_after, message
        )
    return ReconcileTokenResult(
        normalized,
        managed_after[0],
        not unknown_outcome,
        False,
        tokens_after,
    )


@dataclass
class EnsureCoverageResult:
    tokens: list[dict[str, Any]]
    created: int = 0
    updated: int = 0
    paging_incomplete: bool = False
    coverage_unknown: list[str] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)


def ensure_coverage(
    groups: Sequence[Mapping[str, Any] | Any],
    *,
    list_tokens_fn: ListTokensAllFn,
    get_token_secret_fn: GetTokenSecretFn,
    create_token_fn: CreateTokenFn,
    update_token_fn: UpdateTokenFn,
) -> EnsureCoverageResult:
    """Hydrate before Missing; paging blocks all writes, uncertainty blocks one group."""
    group_names = [_group_from_record(group) for group in groups]
    group_names = [group for group in group_names if group]
    try:
        listed, complete = list_tokens_fn()
    except Exception as exc:
        return EnsureCoverageResult(
            tokens=[],
            paging_incomplete=True,
            failures=[{"group": "*", "error": f"list tokens failed: {type(exc).__name__}"}],
        )
    if not complete:
        return EnsureCoverageResult(tokens=[dict(token) for token in listed], paging_incomplete=True)

    hydration = hydrate_tokens(listed, get_token_secret_fn=get_token_secret_fn)
    current = hydration.tokens
    now = time.time()
    unknown: list[str] = []
    failures: list[dict[str, str]] = []
    created = 0
    updated = 0
    paging_incomplete = False

    for group in group_names:
        if pick_inventory_token(group, current, now=now) is not None:
            continue
        unknown_tokens = [
            token
            for token in current
            if _otherwise_suitable_hydration_failure(group, token, now=now)
        ]
        if unknown_tokens:
            unknown.append(group)
            # Prefer precise transient kind when secret hydrate failed as rate_limit
            # so next_retry_at uses minutes, not the 24h coverage_unknown window.
            if any(
                str(token.get(_SECRET_ERROR_FIELD) or "").lower() == "rate_limit"
                for token in unknown_tokens
            ):
                failures.append({"group": group, "error": "rate_limit"})
            else:
                failures.append({"group": group, "error": "coverage_unknown"})
            continue

        result = reconcile_token_for_group(
            group,
            current,
            list_tokens_fn=list_tokens_fn,
            get_token_secret_fn=get_token_secret_fn,
            create_token_fn=create_token_fn,
            update_token_fn=update_token_fn,
        )
        current = result.tokens_after
        if result.created:
            created += 1
        if result.updated:
            updated += 1
        if result.error is not None:
            failures.append({"group": group, "error": result.error})
        if result.paging_incomplete:
            paging_incomplete = True
            break

    return EnsureCoverageResult(
        tokens=current,
        created=created,
        updated=updated,
        paging_incomplete=paging_incomplete,
        coverage_unknown=unknown,
        failures=failures,
    )


# ---------------------------------------------------------------------------
# Model snapshot state
# ---------------------------------------------------------------------------


def normalize_model_ids(models: Sequence[Any]) -> list[str]:
    return sorted(
        {
            model.strip()
            for model in models
            if isinstance(model, str) and model.strip()
        }
    )


def parse_models_payload(payload: Any) -> list[str]:
    """Parse the strict OpenAI models envelope without silently dropping rows."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise ValueError("models response data must be a list")
    model_ids: list[str] = []
    for item in payload["data"]:
        if not isinstance(item, Mapping) or "id" not in item:
            raise ValueError("models response item missing id")
        raw_id = item.get("id")
        if raw_id is None or isinstance(raw_id, (bool, Mapping, list, tuple, set)):
            raise ValueError("models response item has invalid id")
        model_id = str(raw_id).strip()
        if model_id:
            model_ids.append(model_id)
    return normalize_model_ids(model_ids)


def content_hash_models(models: Sequence[str] | None) -> str | None:
    if models is None:
        return None
    normalized = normalize_model_ids(models)
    canonical = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def empty_models_record(site_id: str, *, models_path: str = DEFAULT_MODELS_PATH) -> dict[str, Any]:
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


def compute_next_retry_at(error_kind: str | None, *, now_iso: str | None = None) -> str:
    base = now_iso or utc_now_iso()
    try:
        now = datetime.fromisoformat(base.replace("Z", "+00:00"))
    except ValueError:
        now = datetime.now(timezone.utc)
    kind = (error_kind or "").lower()
    if kind in {"timeout", "server", "network"}:
        delta = timedelta(minutes=45)
    elif kind == "rate_limit":
        delta = timedelta(minutes=15)
    elif kind in {"contract", "no_usable_key", "key_auth", "coverage_unknown"}:
        delta = timedelta(hours=24)
    else:
        delta = timedelta(hours=6)
    return (now + delta).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def should_attempt_now(
    group_entry: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    if not group_entry:
        return True
    value = group_entry.get("next_retry_at")
    if value in (None, ""):
        return True
    try:
        retry_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return True
    current = now or datetime.now(timezone.utc)
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current >= retry_at


class ModelsStore:
    """Persist only models_latest.json and models_events.jsonl."""

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
        try:
            raw = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            raw = empty_models_record(self.site_id, models_path=self.models_path)
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != 1
            or raw.get("site_id") != self.site_id
        ):
            raw = empty_models_record(self.site_id, models_path=self.models_path)
        raw.setdefault("schema_version", 1)
        raw.setdefault("site_id", self.site_id)
        raw.setdefault("bootstrap_completed_at", None)
        raw.setdefault("last_full_attempt_at", None)
        raw.setdefault("last_full_success_at", None)
        raw.setdefault("last_full_result", None)
        raw.setdefault("last_incremental_at", None)
        raw.setdefault("models_path", self.models_path)
        bootstrap_at = raw.get("bootstrap_completed_at")
        if bootstrap_at is not None:
            try:
                parsed_bootstrap = datetime.fromisoformat(
                    str(bootstrap_at).replace("Z", "+00:00")
                )
                if not isinstance(bootstrap_at, str) or parsed_bootstrap.tzinfo is None:
                    raise ValueError("bootstrap timestamp must be timezone-aware text")
            except (TypeError, ValueError):
                raw["bootstrap_completed_at"] = None
        if not isinstance(raw.get("models_by_group"), dict):
            raw["models_by_group"] = {}
        self._record = raw
        return raw

    def save(self) -> None:
        record = self.load()
        record["updated_at"] = utc_now_iso()
        record["site_id"] = self.site_id
        self.data_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.latest_path, record, mode=0o644)

    def get_group(self, group: Any) -> dict[str, Any]:
        entry = self.load()["models_by_group"].get(norm_group(group))
        return dict(entry) if isinstance(entry, dict) else empty_group_entry()

    def apply_success(
        self,
        group: Any,
        models: Sequence[str],
        *,
        key_id: Any = None,
        source: str = "refresh",
        now: str | None = None,
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        record = self.load()
        group_name = norm_group(group)
        entries = record["models_by_group"]
        previous = dict(entries.get(group_name) or empty_group_entry())
        observed_at = now or utc_now_iso()
        normalized = normalize_model_ids(models)
        digest = content_hash_models(normalized)
        entry = {
            "key_id": key_id,
            "models": normalized,
            "content_hash": digest,
            "last_success_at": observed_at,
            "last_attempt_at": observed_at,
            "last_error": None,
            "next_retry_at": None,
            "source": source,
        }
        entries[group_name] = entry

        if digest != previous.get("content_hash"):
            previous_models = previous.get("models")
            old_set = set(previous_models) if isinstance(previous_models, list) else set()
            new_set = set(normalized)
            added = sorted(new_set - old_set)
            removed = sorted(old_set - new_set)
            truncated = False
            if len(added) > DEFAULT_EVENT_MODELS_DIFF_CAP:
                added = added[:DEFAULT_EVENT_MODELS_DIFF_CAP]
                truncated = True
            if len(removed) > DEFAULT_EVENT_MODELS_DIFF_CAP:
                removed = removed[:DEFAULT_EVENT_MODELS_DIFF_CAP]
                truncated = True
            append_jsonl_fsync(
                self.events_path,
                {
                    "site_id": self.site_id,
                    "observed_at": observed_at,
                    "event": "initial" if previous.get("content_hash") is None else "models_changed",
                    "group_id": group_name,
                    "key_id": key_id,
                    "model_count": len(normalized),
                    "content_hash": digest,
                    "source": source,
                    "added_models": added,
                    "removed_models": removed,
                    "truncated": truncated,
                },
            )
        if checkpoint:
            self.save()
        return dict(entry)

    def apply_failure(
        self,
        group: Any,
        error: str,
        *,
        source: str | None = None,
        next_retry_at: str | None = None,
        error_kind: str | None = None,
        now: str | None = None,
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        record = self.load()
        group_name = norm_group(group)
        entries = record["models_by_group"]
        previous = dict(entries.get(group_name) or empty_group_entry())
        attempted_at = now or utc_now_iso()
        retry_at = next_retry_at
        if retry_at is None and error_kind is not None:
            retry_at = compute_next_retry_at(error_kind, now_iso=attempted_at)
        entry = {
            "key_id": previous.get("key_id"),
            "models": previous.get("models"),
            "content_hash": previous.get("content_hash"),
            "last_success_at": previous.get("last_success_at"),
            "last_attempt_at": attempted_at,
            "last_error": str(error),
            "next_retry_at": retry_at if retry_at is not None else previous.get("next_retry_at"),
            "source": source if source is not None else previous.get("source"),
        }
        entries[group_name] = entry
        if checkpoint:
            self.save()
        return dict(entry)

    def update_full_meta(
        self,
        *,
        target: int,
        ok: int,
        failed: int,
        skipped: int,
        bootstrap: bool = False,
        now: str | None = None,
        checkpoint: bool = True,
    ) -> dict[str, Any]:
        if target != ok + failed + skipped:
            raise ValueError("target must equal ok + failed + skipped")
        record = self.load()
        attempted_at = now or utc_now_iso()
        record["last_full_attempt_at"] = attempted_at
        record["last_full_result"] = {
            "target": target,
            "ok": ok,
            "failed": failed,
            "skipped": skipped,
        }
        if target > 0 and ok == target and failed == 0 and skipped == 0:
            record["last_full_success_at"] = attempted_at
            if bootstrap:
                record["bootstrap_completed_at"] = attempted_at
        if checkpoint:
            self.save()
        return record

    def set_incremental_at(self, *, now: str | None = None, checkpoint: bool = True) -> None:
        self.load()["last_incremental_at"] = now or utc_now_iso()
        if checkpoint:
            self.save()


# ---------------------------------------------------------------------------
# Model refresh
# ---------------------------------------------------------------------------


@dataclass
class RefreshModelsResult:
    ok_count: int
    failed_count: int
    skipped_count: int
    target_count: int
    errors: list[dict[str, Any]] = field(default_factory=list)


def refresh_models_for_groups(
    groups: Sequence[Mapping[str, Any] | Any],
    hydrated_tokens: Sequence[Mapping[str, Any]],
    store: ModelsStore,
    list_models_fn: ListModelsFn,
    *,
    source: str = "refresh",
    blocked_groups: Mapping[str, str] | None = None,
    deadline: float | None = None,
    time_fn: Callable[[], float] | None = None,
) -> RefreshModelsResult:
    """Refresh serially using inventory tokens; key auth falls through candidates."""
    group_names = [_group_from_record(group) for group in groups]
    group_names = [group for group in group_names if group]
    target = len(group_names)
    now_fn = time_fn or time.time
    ok = 0
    failed = 0
    skipped = 0
    errors: list[dict[str, Any]] = []
    blocked = {
        norm_group(group): str(reason)
        for group, reason in (blocked_groups or {}).items()
    }

    for index, group in enumerate(group_names):
        if deadline is not None and now_fn() >= deadline:
            skipped = target - index
            break

        if group in blocked:
            reason = blocked[group]
            store.apply_failure(
                group,
                reason,
                source=source,
                error_kind=reason,
                checkpoint=True,
            )
            failed += 1
            errors.append({"group": group, "error": reason, "kind": reason})
            continue

        candidates = inventory_suitable_tokens(group, hydrated_tokens)
        if not candidates:
            store.apply_failure(
                group,
                "no_usable_key",
                source=source,
                error_kind="no_usable_key",
                checkpoint=True,
            )
            failed += 1
            errors.append({"group": group, "error": "no_usable_key"})
            continue

        last_error = "no_usable_key"
        last_kind = "no_usable_key"
        explicit_retry_at: str | None = None
        succeeded = False
        for token in candidates:
            secret = token_secret(token)
            if secret is None:
                continue
            try:
                models = list_models_fn(secret)
                store.apply_success(
                    group,
                    models,
                    key_id=token.get("id"),
                    source=source,
                    checkpoint=True,
                )
                ok += 1
                succeeded = True
                break
            except Exception as exc:
                last_error = str(exc) or type(exc).__name__
                if secret:
                    last_error = last_error.replace(secret, "[REDACTED]")
                last_kind = str(getattr(exc, "kind", "") or "error")
                explicit_retry_at = getattr(exc, "next_retry_at", None)
                if last_kind == "key_auth":
                    continue
                break
        if not succeeded:
            store.apply_failure(
                group,
                last_error,
                source=source,
                next_retry_at=explicit_retry_at,
                error_kind=last_kind,
                checkpoint=True,
            )
            failed += 1
            errors.append({"group": group, "error": last_error, "kind": last_kind})

    return RefreshModelsResult(
        ok_count=ok,
        failed_count=failed,
        skipped_count=skipped,
        target_count=target,
        errors=errors,
    )
