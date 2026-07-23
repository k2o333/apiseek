#!/usr/bin/env python3
"""Fetch and persist per-site invite links (Sub2API + New-API).

Storage contract (draft): storage/invite-link-v1 → data/<site>/invite_latest.json

Refresh rules:
- base_url change → always remote re-fetch and rewrite invite_link
- same base_url → re-fetch at most every ttl_seconds (default 14 days)
- remote/contract failure must not blank an existing good latest
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import requests

from monitor_storage import utc_now_iso, write_json_atomic

LOG = logging.getLogger("invite-links")

SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 14 * 24 * 3600  # 14 days
INVITE_FILENAME = "invite_latest.json"
BACKENDS = frozenset({"sub2api", "newapi"})

SUB2API_AFF_PATH = "/api/v1/user/aff"
NEWAPI_SELF_PATH = "/api/user/self"
NEWAPI_AFF_PATH = "/api/user/aff"


class ConfigError(Exception):
    """Local configuration / env problem (CLI exit 2)."""


class InviteError(Exception):
    """Provider or contract failure (CLI exit 1)."""

    def __init__(self, message: str, *, kind: str = "contract") -> None:
        super().__init__(message)
        self.kind = kind


def _parse_rfc3339_z(value: str) -> datetime:
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_base_url(url: str) -> str:
    raw = (url or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigError(f"base_url must be HTTPS origin: {url!r}")
    if parsed.username or parsed.password:
        raise ConfigError("base_url must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ConfigError("base_url must not contain query or fragment")
    path = parsed.path or ""
    if path not in ("", "/"):
        raise ConfigError(f"base_url path must be empty or /: {url!r}")
    return f"https://{parsed.netloc.lower()}"


def build_invite_link(base_url: str, aff_code: str) -> str:
    base = normalize_base_url(base_url)
    code = (aff_code or "").strip()
    if not code or re.search(r"\s", code):
        raise InviteError("aff_code must be non-empty without whitespace", kind="contract")
    return f"{base}/register?aff={code}"


def invite_path(data_dir: Path) -> Path:
    return Path(data_dir) / INVITE_FILENAME


def validate_record(
    data: Mapping[str, Any],
    *,
    expected_site_id: str | None = None,
    expected_backend: str | None = None,
    expected_base_url: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InviteError("invite_latest root must be object", kind="contract")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise InviteError(f"unknown schema_version {version!r}", kind="contract")
    site_id = data.get("site_id")
    if not isinstance(site_id, str) or not site_id.strip():
        raise InviteError("site_id required", kind="contract")
    if expected_site_id is not None and site_id != expected_site_id:
        raise InviteError(
            f"site_id mismatch: file={site_id!r} expected={expected_site_id!r}",
            kind="contract",
        )
    backend = data.get("backend")
    if backend not in BACKENDS:
        raise InviteError(f"invalid backend {backend!r}", kind="contract")
    if expected_backend is not None and backend != expected_backend:
        raise InviteError(
            f"backend mismatch: file={backend!r} expected={expected_backend!r}",
            kind="contract",
        )
    try:
        base_url = normalize_base_url(str(data.get("base_url") or ""))
    except ConfigError as exc:
        raise InviteError(str(exc), kind="contract") from exc
    if expected_base_url is not None and base_url != normalize_base_url(expected_base_url):
        raise InviteError("base_url mismatch vs config", kind="contract")
    aff_code = data.get("aff_code")
    if not isinstance(aff_code, str) or not aff_code.strip() or re.search(r"\s", aff_code.strip()):
        raise InviteError("aff_code invalid", kind="contract")
    aff_code = aff_code.strip()
    invite_link = data.get("invite_link")
    expected_link = build_invite_link(base_url, aff_code)
    if invite_link != expected_link:
        raise InviteError(
            f"invite_link must equal {expected_link!r}",
            kind="contract",
        )
    for key in ("fetched_at", "checked_at"):
        val = data.get(key)
        if not isinstance(val, str) or not val.strip():
            raise InviteError(f"{key} required", kind="contract")
        try:
            _parse_rfc3339_z(val)
        except (TypeError, ValueError) as exc:
            raise InviteError(f"{key} not RFC3339: {val!r}", kind="contract") from exc
    ttl = data.get("ttl_seconds")
    try:
        ttl_i = int(ttl)
    except (TypeError, ValueError) as exc:
        raise InviteError("ttl_seconds must be positive int", kind="contract") from exc
    if ttl_i <= 0:
        raise InviteError("ttl_seconds must be positive", kind="contract")
    return {
        "schema_version": SCHEMA_VERSION,
        "site_id": site_id,
        "backend": backend,
        "base_url": base_url,
        "aff_code": aff_code,
        "invite_link": expected_link,
        "fetched_at": data["fetched_at"],
        "checked_at": data["checked_at"],
        "ttl_seconds": ttl_i,
    }


def load_invite_latest(
    path: Path,
    *,
    expected_site_id: str | None = None,
    expected_backend: str | None = None,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        LOG.warning("invite_latest unreadable (%s); will re-fetch", type(exc).__name__)
        return None
    try:
        return validate_record(
            raw,
            expected_site_id=expected_site_id,
            expected_backend=expected_backend,
        )
    except InviteError as exc:
        LOG.warning("invite_latest invalid (%s); will re-fetch", exc)
        return None


def save_invite_latest(path: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    clean = validate_record(record)
    write_json_atomic(path, clean, mode=0o644)
    return clean


def needs_remote_refresh(
    record: dict[str, Any] | None,
    *,
    base_url: str,
    force: bool = False,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Return (should_fetch, reason)."""
    if force:
        return True, "force"
    if record is None:
        return True, "missing_or_invalid"
    current = normalize_base_url(base_url)
    if record["base_url"] != current:
        return True, "base_url_changed"
    now_dt = now or datetime.now(timezone.utc)
    checked = _parse_rfc3339_z(record["checked_at"])
    age = (now_dt - checked).total_seconds()
    ttl = int(record["ttl_seconds"])
    if age >= ttl:
        return True, "ttl_expired"
    return False, "ttl_ok"


def detect_backend(env_file: Path) -> str:
    """Sub2API env has TOKEN_STATE_FILE and/or MONITOR_LOGIN_PATH."""
    from sub2api_monitor import parse_env_file

    vars_ = parse_env_file(env_file)
    if vars_.get("TOKEN_STATE_FILE") or vars_.get("MONITOR_LOGIN_PATH"):
        return "sub2api"
    return "newapi"


def _ttl_from_env(file_vars: Mapping[str, str], environ: Mapping[str, str]) -> int:
    raw = environ.get("INVITE_TTL_SECONDS")
    if raw is None or raw == "":
        raw = file_vars.get("INVITE_TTL_SECONDS", "")
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ConfigError(f"invalid INVITE_TTL_SECONDS: {raw!r}") from exc
    if value <= 0:
        raise ConfigError("INVITE_TTL_SECONDS must be positive")
    return value


@dataclass
class SiteContext:
    site_id: str
    backend: str
    base_url: str
    data_dir: Path
    ttl_seconds: int
    env_file: Path
    # backend-specific handles filled by runners
    fetch_aff_code: Callable[[], str] | None = None


def load_sub2api_context(
    env_file: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> SiteContext:
    import sub2api_monitor as mon

    env = environ if environ is not None else os.environ
    cfg = mon.load_config(env_file, environ=env, enforce_interval=False)
    base_url = normalize_base_url(cfg.base_url)
    file_vars = mon.parse_env_file(env_file)
    ttl = _ttl_from_env(file_vars, env)

    store = mon.TokenStore(cfg.token_state_file)
    client = mon.AuthGroupClient(cfg, store)

    def fetch() -> str:
        client.ensure_token()
        token = store.state.access_token
        if not token:
            raise InviteError("no access token after ensure_token", kind="auth")
        try:
            response = client.session.get(
                f"{base_url}{SUB2API_AFF_PATH}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "User-Agent": mon.USER_AGENT,
                    "Connection": "close",
                },
                timeout=cfg.timeout,
            )
        except requests.Timeout as exc:
            raise InviteError(f"aff timeout: {type(exc).__name__}", kind="timeout") from exc
        except requests.RequestException as exc:
            raise InviteError(f"aff network: {type(exc).__name__}", kind="network") from exc

        if response.status_code in (401, 403):
            # one recovery
            client.recover_auth()
            token = store.state.access_token
            if not token:
                raise InviteError("auth recovery produced no token", kind="auth")
            try:
                response = client.session.get(
                    f"{base_url}{SUB2API_AFF_PATH}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "User-Agent": mon.USER_AGENT,
                        "Connection": "close",
                    },
                    timeout=cfg.timeout,
                )
            except requests.RequestException as exc:
                raise InviteError(f"aff retry network: {type(exc).__name__}", kind="network") from exc

        if response.status_code != 200:
            kind = "auth" if response.status_code in (401, 403) else "server"
            if response.status_code == 429:
                kind = "rate_limit"
            raise InviteError(f"aff HTTP {response.status_code}", kind=kind)

        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise InviteError("aff response not JSON", kind="contract") from exc
        if not isinstance(payload, dict) or payload.get("code") not in (0, "0", None):
            # some deployments use code=0 only
            if isinstance(payload, dict) and payload.get("code") not in (0, "0"):
                raise InviteError(
                    f"aff envelope code={payload.get('code')!r}",
                    kind="contract",
                )
        data = payload.get("data") if isinstance(payload, dict) else None
        code: str | None = None
        if isinstance(data, dict):
            raw_code = data.get("aff_code") or data.get("code")
            if isinstance(raw_code, str):
                code = raw_code.strip()
        elif isinstance(data, str):
            code = data.strip()
        if not code:
            raise InviteError("aff response missing aff_code", kind="contract")
        return code

    return SiteContext(
        site_id=cfg.site_id,
        backend="sub2api",
        base_url=base_url,
        data_dir=cfg.data_dir,
        ttl_seconds=ttl,
        env_file=env_file,
        fetch_aff_code=fetch,
    )


def load_newapi_context(
    env_file: Path,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path | None = None,
) -> SiteContext:
    import newapi_monitor as mon

    env = environ if environ is not None else os.environ
    cfg = mon.load_config(env_file, environ=env, project_root=project_root)
    base_url = normalize_base_url(cfg.base_url)
    file_vars = mon.parse_env_file(env_file)
    ttl = _ttl_from_env(file_vars, env)
    client = mon.NewApiClient(cfg)

    def fetch() -> str:
        client.ensure_auth(require_user_id=cfg.require_new_api_user_header)
        headers = {
            "Accept": "application/json",
            "User-Agent": mon.USER_AGENT,
            "Connection": "close",
        }
        if cfg.require_new_api_user_header:
            if not client.user_id:
                raise InviteError("missing user_id for new-api-user", kind="auth")
            headers["new-api-user"] = str(client.user_id)
        elif client.user_id:
            headers["new-api-user"] = str(client.user_id)

        def _get(path: str) -> requests.Response:
            try:
                return client.session.get(
                    f"{base_url}{path}",
                    headers=headers,
                    timeout=client._timeout(),
                )
            except mon.CollectError:
                raise
            except requests.Timeout as exc:
                raise InviteError(f"aff timeout: {type(exc).__name__}", kind="timeout") from exc
            except requests.RequestException as exc:
                raise InviteError(f"aff network: {type(exc).__name__}", kind="network") from exc

        response = _get(NEWAPI_SELF_PATH)
        if response.status_code in (401, 403):
            client.recover_auth_once()
            if cfg.require_new_api_user_header and client.user_id:
                headers["new-api-user"] = str(client.user_id)
            elif client.user_id:
                headers["new-api-user"] = str(client.user_id)
            response = _get(NEWAPI_SELF_PATH)

        if response.status_code != 200:
            # fallback to /api/user/aff
            response = _get(NEWAPI_AFF_PATH)
            if response.status_code != 200:
                kind = "auth" if response.status_code in (401, 403) else "server"
                raise InviteError(f"user self/aff HTTP {response.status_code}", kind=kind)
            try:
                payload = response.json()
            except (ValueError, requests.JSONDecodeError) as exc:
                raise InviteError("aff response not JSON", kind="contract") from exc
            if not isinstance(payload, dict) or payload.get("success") is not True:
                raise InviteError("aff envelope success!=true", kind="contract")
            data = payload.get("data")
            if isinstance(data, str) and data.strip():
                return data.strip()
            raise InviteError("aff data missing code string", kind="contract")

        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise InviteError("self response not JSON", kind="contract") from exc
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise InviteError("self envelope success!=true", kind="contract")
        data = payload.get("data")
        if isinstance(data, dict):
            code = data.get("aff_code")
            if isinstance(code, str) and code.strip():
                return code.strip()
        raise InviteError("self data missing aff_code", kind="contract")

    return SiteContext(
        site_id=cfg.site_id,
        backend="newapi",
        base_url=base_url,
        data_dir=cfg.data_dir,
        ttl_seconds=ttl,
        env_file=env_file,
        fetch_aff_code=fetch,
    )


def load_site_context(
    env_file: Path,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path | None = None,
    backend: str | None = None,
) -> SiteContext:
    env_file = env_file.resolve()
    if not env_file.is_file():
        raise ConfigError(f"env file not found: {env_file}")
    kind = backend or detect_backend(env_file)
    if kind == "sub2api":
        return load_sub2api_context(env_file, environ=environ)
    if kind == "newapi":
        return load_newapi_context(env_file, environ=environ, project_root=project_root)
    raise ConfigError(f"unknown backend {kind!r}")


def run_once(
    ctx: SiteContext,
    *,
    force: bool = False,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Refresh invite_latest if needed. Returns action metadata."""
    path = invite_path(ctx.data_dir)
    existing = load_invite_latest(
        path,
        expected_site_id=ctx.site_id,
        expected_backend=ctx.backend,
    )
    should, reason = needs_remote_refresh(
        existing,
        base_url=ctx.base_url,
        force=force,
        now=now,
    )
    if not should:
        assert existing is not None
        LOG.info(
            "site=%s invite skip remote reason=%s link=%s",
            ctx.site_id,
            reason,
            existing["invite_link"],
        )
        return {"action": "skip", "reason": reason, "record": existing, "path": str(path)}

    if ctx.fetch_aff_code is None:
        raise InviteError("fetch_aff_code not configured", kind="contract")

    LOG.info("site=%s invite remote fetch reason=%s", ctx.site_id, reason)
    try:
        aff_code = ctx.fetch_aff_code()
    except InviteError:
        raise
    except Exception as exc:
        # Preserve last good on unexpected provider errors
        raise InviteError(f"fetch failed: {type(exc).__name__}: {exc}", kind="network") from exc

    ts = utc_now_iso()
    record = {
        "schema_version": SCHEMA_VERSION,
        "site_id": ctx.site_id,
        "backend": ctx.backend,
        "base_url": ctx.base_url,
        "aff_code": aff_code,
        "invite_link": build_invite_link(ctx.base_url, aff_code),
        "fetched_at": ts,
        "checked_at": ts,
        "ttl_seconds": ctx.ttl_seconds,
    }
    if dry_run:
        validate_record(record)
        return {"action": "dry_run", "reason": reason, "record": record, "path": str(path)}

    try:
        saved = save_invite_latest(path, record)
    except Exception as exc:
        raise InviteError(f"write failed: {type(exc).__name__}", kind="contract") from exc

    LOG.info("site=%s invite saved link=%s", ctx.site_id, saved["invite_link"])
    return {"action": "updated", "reason": reason, "record": saved, "path": str(path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch/store site invite links")
    parser.add_argument("--env-file", required=True, type=Path, help="sites/<id>.env")
    parser.add_argument(
        "--backend",
        choices=sorted(BACKENDS),
        default=None,
        help="override auto-detect (sub2api if TOKEN_STATE_FILE/LOGIN_PATH present)",
    )
    parser.add_argument("--force", action="store_true", help="ignore TTL")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="load config only; no provider call / write",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch but do not write (still hits provider unless TTL skip)",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        ctx = load_site_context(
            args.env_file,
            backend=args.backend,
        )
    except ConfigError as exc:
        LOG.error("config: %s", exc)
        return 2
    except Exception as exc:
        # load_config raises monitor ConfigError types
        msg = str(exc)
        name = type(exc).__name__
        if name in ("ConfigError",) or "ConfigError" in type(exc).__module__:
            LOG.error("config: %s", msg)
            return 2
        # import sub2api/newapi ConfigError by duck type
        try:
            import sub2api_monitor as s2
            import newapi_monitor as na

            if isinstance(exc, (s2.ConfigError, na.ConfigError)):
                LOG.error("config: %s", msg)
                return 2
        except Exception:
            pass
        LOG.error("config load failed: %s: %s", name, msg)
        return 2

    if args.validate:
        path = invite_path(ctx.data_dir)
        LOG.info(
            "validate ok site=%s backend=%s base_url=%s data_dir=%s invite_path=%s ttl=%s",
            ctx.site_id,
            ctx.backend,
            ctx.base_url,
            ctx.data_dir,
            path,
            ctx.ttl_seconds,
        )
        return 0

    try:
        result = run_once(ctx, force=args.force, dry_run=args.dry_run)
    except InviteError as exc:
        LOG.error("invite failed kind=%s: %s", exc.kind, exc)
        return 1
    except Exception as exc:
        LOG.error("invite failed: %s: %s", type(exc).__name__, exc)
        return 1

    rec = result.get("record") or {}
    print(
        json.dumps(
            {
                "action": result["action"],
                "reason": result["reason"],
                "site_id": ctx.site_id,
                "backend": ctx.backend,
                "invite_link": rec.get("invite_link"),
                "aff_code": rec.get("aff_code"),
                "path": result["path"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
