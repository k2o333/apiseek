#!/usr/bin/env python3
"""BotCF / TorchAI legacy session group collector (timer oneshot).

Default: one bounded poll then exit. Use --validate for config only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import requests

from monitor_storage import (
    BACKEND_NEWAPI,
    InstanceLock,
    SnapshotStore,
    content_hash_groups,
    load_latest,
    normalize_groups_dict,
    utc_now_iso,
    write_json_atomic,
)

__version__ = "1.0.0"
USER_AGENT = f"newapi-monitor/{__version__}"
LOG = logging.getLogger("newapi-monitor")

SITE_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
LOGIN_PATH = "/api/user/login"
GROUPS_PATH = "/api/user/self/groups"
TOKENS_PATH = "/api/token/"
MODELS_PATH = "/v1/models"
DEFAULT_CONNECT = 5.0
DEFAULT_READ = 20.0
APP_DEADLINE_SECONDS = 170.0
MODELS_DEADLINE_SECONDS = 540.0
MODELS_LOCK_WAIT_SECONDS = 30.0
MODELS_LOCK_POLL_SECONDS = 1.0
TRANSIENT_BACKOFF = 5.0
# Token secret (/api/token/{id}/key) is sensitive and frequently 429s without Retry-After.
SECRET_MIN_INTERVAL_SECONDS = 1.5
MANAGEMENT_429_MAX_RETRIES = 8
MANAGEMENT_429_DEFAULT_BACKOFF = 15.0
MANAGEMENT_429_MAX_BACKOFF = 90.0

PROJECT_ROOT = Path(__file__).resolve().parent


class ConfigError(ValueError):
    """Invalid configuration."""


class CollectError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "error",
        status_code: int | None = None,
        next_retry_at: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.next_retry_at = next_retry_at


# ---------------------------------------------------------------------------
# Env / config
# ---------------------------------------------------------------------------


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ConfigError(f"env file not found: {path}")
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _check_credential_file_mode(path: Path) -> None:
    try:
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        raise ConfigError(f"cannot stat env file {path}: {exc}") from exc
    if mode & 0o077:
        raise ConfigError(
            f"credential file permissions too open for {path}: "
            f"{oct(mode)}; require 0600 (no group/other access)"
        )


def _validate_origin(base_url: str) -> str:
    parsed = urlparse(base_url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ConfigError(f"MONITOR_BASE_URL must be HTTPS origin with host: {base_url!r}")
    if parsed.username or parsed.password:
        raise ConfigError("MONITOR_BASE_URL must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ConfigError("MONITOR_BASE_URL must not contain query or fragment")
    if parsed.path not in ("", "/"):
        raise ConfigError("MONITOR_BASE_URL path must be empty or /")
    # pure origin
    return f"https://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")


@dataclass
class MonitorConfig:
    site_id: str
    base_url: str
    username: str
    password: str
    require_new_api_user_header: bool = False
    connect_timeout_seconds: float = DEFAULT_CONNECT
    read_timeout_seconds: float = DEFAULT_READ
    proxy_url: str | None = None
    models_incremental_enable: bool = False
    log_level: str = "INFO"
    project_root: Path = PROJECT_ROOT
    env_file: Path | None = None

    @property
    def timeout(self) -> tuple[float, float]:
        return (self.connect_timeout_seconds, self.read_timeout_seconds)

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data" / self.site_id

    @property
    def auth_state_file(self) -> Path:
        return self.data_dir / "auth_state.json"

    @property
    def lock_file(self) -> Path:
        return self.data_dir / "monitor.lock"

    @property
    def host(self) -> str:
        return urlparse(self.base_url).hostname or ""


def load_config(
    env_file: Path,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path | None = None,
) -> MonitorConfig:
    """Load config from env file. Pure w.r.t. os.environ (never mutates)."""
    env_file = env_file.resolve()
    file_vars = parse_env_file(env_file)
    if environ is None:
        environ = os.environ

    def get(key: str, default: str = "") -> str:
        if key in environ:
            return environ[key]
        return file_vars.get(key, default)

    root = (project_root or PROJECT_ROOT).resolve()
    site_id = (get("MONITOR_SITE_ID") or env_file.stem).strip()
    if env_file.stem != site_id:
        raise ConfigError(
            f"env file stem {env_file.stem!r} must equal MONITOR_SITE_ID {site_id!r}"
        )
    if not site_id or not SITE_ID_RE.match(site_id):
        raise ConfigError(f"invalid MONITOR_SITE_ID {site_id!r}")

    base_url = _validate_origin(get("MONITOR_BASE_URL") or "")
    username = get("MONITOR_USERNAME") or ""
    password = get("MONITOR_PASSWORD") or ""
    if not username:
        raise ConfigError("MONITOR_USERNAME is required")
    if not password:
        raise ConfigError("MONITOR_PASSWORD is required")

    try:
        connect_timeout = float(get("CONNECT_TIMEOUT_SECONDS", str(DEFAULT_CONNECT)) or DEFAULT_CONNECT)
        read_timeout = float(get("READ_TIMEOUT_SECONDS", str(DEFAULT_READ)) or DEFAULT_READ)
    except ValueError as exc:
        raise ConfigError(f"invalid timeout: {exc}") from exc
    if connect_timeout <= 0 or read_timeout <= 0:
        raise ConfigError("timeouts must be positive")

    proxy = (get("MONITOR_PROXY_URL") or "").strip() or None
    require_header = _truthy(get("REQUIRE_NEW_API_USER_HEADER", "0") or "0")
    log_level = get("LOG_LEVEL", "INFO") or "INFO"
    models_incremental = _truthy(get("MONITOR_MODELS_INCREMENTAL_ENABLE", "0") or "0")

    if env_file.exists():
        _check_credential_file_mode(env_file)

    cfg = MonitorConfig(
        site_id=site_id,
        base_url=base_url,
        username=username,
        password=password,
        require_new_api_user_header=require_header,
        connect_timeout_seconds=connect_timeout,
        read_timeout_seconds=read_timeout,
        proxy_url=proxy,
        models_incremental_enable=models_incremental,
        log_level=log_level,
        project_root=root,
        env_file=env_file,
    )
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"DATA_DIR not creatable: {cfg.data_dir}: {exc}") from exc
    if not os.access(cfg.data_dir, os.W_OK):
        raise ConfigError(f"DATA_DIR not writable: {cfg.data_dir}")
    return cfg


# ---------------------------------------------------------------------------
# Auth state
# ---------------------------------------------------------------------------


def _check_auth_file_mode(path: Path) -> None:
    if not path.exists():
        return
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise CollectError(
            f"auth_state permissions too open: {oct(mode)}",
            kind="contract",
        )


def load_auth_state(path: Path, expected_host: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        _check_auth_file_mode(path)
        data = json.loads(path.read_text(encoding="utf-8"))
    except CollectError:
        raise
    except (OSError, json.JSONDecodeError, TypeError):
        LOG.warning("site auth_state unreadable; will re-login")
        return None
    if not isinstance(data, dict):
        return None
    session = data.get("session")
    if not isinstance(session, dict):
        return None
    value = session.get("value")
    domain = session.get("domain") or ""
    path_s = session.get("path") or "/"
    if not value or not isinstance(value, str):
        return None
    if domain != expected_host:
        LOG.warning("site auth_state domain mismatch; will re-login")
        return None
    if path_s not in ("/", ""):
        LOG.warning("site auth_state path not /; will re-login")
        return None
    user_id = data.get("user_id")
    if user_id is not None:
        try:
            user_id = int(user_id)
            if user_id <= 0:
                user_id = None
        except (TypeError, ValueError):
            user_id = None
    return {
        "user_id": user_id,
        "session": {
            "value": value,
            "domain": expected_host,
            "path": "/",
            "expires": session.get("expires"),
        },
    }


def save_auth_state(path: Path, *, session_value: str, domain: str, user_id: int | None) -> None:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "saved_at": utc_now_iso(),
        "session": {
            "value": session_value,
            "domain": domain,
            "path": "/",
            "expires": None,
        },
    }
    if user_id is not None:
        payload["user_id"] = user_id
    write_json_atomic(path, payload, mode=0o600)


def clear_auth_state(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _sanitize_message(text: str, limit: int = 160) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text or "")
    text = re.sub(r"(?i)(password|session|cookie|token)\s*[:=]\s*\S+", r"\1=[redacted]", text)
    text = text.strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _looks_like_html(response: requests.Response) -> bool:
    ctype = (response.headers.get("Content-Type") or "").lower()
    if "text/html" in ctype:
        return True
    try:
        body = (response.text or "")[:200].lower()
    except Exception:
        return False
    return body.lstrip().startswith("<!doctype") or body.lstrip().startswith("<html")


def _parse_retry_after(response: requests.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


def _json_payload(response: requests.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except (ValueError, requests.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _message_from_payload(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    for key in ("message", "msg", "error", "reason"):
        val = payload.get(key)
        if val is not None:
            return _sanitize_message(str(val))
    return ""


def _is_captcha_message(msg: str) -> bool:
    lower = msg.lower()
    markers = ("turnstile", "captcha", "验证码", "人机", "cloudflare")
    return any(m in lower for m in markers)


def _is_auth_business_failure(status: int, payload: dict[str, Any] | None) -> bool:
    if status == 401:
        return True
    msg = _message_from_payload(payload).lower()
    markers = (
        "未登录",
        "未提供 new-api-user",
        "new-api-user",
        "unauthorized",
        "无权",
        "access token",
        "token is invalid",
        "请先登录",
        "not login",
    )
    return any(m in msg for m in markers)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class NewApiClient:
    def __init__(
        self,
        config: MonitorConfig,
        *,
        session: requests.Session | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        deadline: float | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "Connection": "close",
            }
        )
        if config.proxy_url:
            self.session.proxies.update({"http": config.proxy_url, "https": config.proxy_url})
        self.monotonic_fn = monotonic_fn or time.monotonic
        self.sleep_fn = sleep_fn or time.sleep
        self.deadline = deadline
        self.user_id: int | None = None
        self._login_count = 0
        self._auth_recovery_used = False
        # In-memory only; never persisted. Avoids re-hitting /key for the same id
        # when bootstrap preflight + ensure_coverage + post-create re-list rehydrate.
        self._secret_cache: dict[str, str] = {}
        self._last_secret_fetch_at = 0.0

    def remaining(self) -> float:
        if self.deadline is None:
            return 3600.0
        return self.deadline - self.monotonic_fn()

    def _timeout(self) -> tuple[float, float]:
        rem = self.remaining()
        if rem <= 0:
            raise CollectError("deadline exhausted before request", kind="timeout")
        connect = min(self.config.connect_timeout_seconds, rem)
        read = min(self.config.read_timeout_seconds, max(0.1, rem - 0.05))
        if connect <= 0 or read <= 0:
            raise CollectError("deadline exhausted before request", kind="timeout")
        return (connect, read)

    def restore_auth(self, state: dict[str, Any] | None) -> bool:
        self.session.cookies.clear()
        self.user_id = None
        if not state:
            return False
        session = state.get("session") or {}
        value = session.get("value")
        if not value:
            return False
        self.session.cookies.set(
            "session",
            value,
            domain=self.config.host,
            path="/",
        )
        uid = state.get("user_id")
        if uid is not None:
            self.user_id = int(uid)
        if self.config.require_new_api_user_header and not self.user_id:
            return False
        return True

    def extract_session_cookie(self) -> str | None:
        for cookie in self.session.cookies:
            if cookie.name == "session":
                # domain may be with leading dot
                dom = (cookie.domain or "").lstrip(".")
                if dom and dom != self.config.host and not self.config.host.endswith("." + dom):
                    # allow exact host match primarily
                    if dom != self.config.host:
                        continue
                return cookie.value
        # fallback: jar without domain filter
        for cookie in self.session.cookies:
            if cookie.name == "session":
                return cookie.value
        return None

    def persist_auth(self) -> None:
        value = self.extract_session_cookie()
        if not value:
            raise CollectError("no session cookie to persist", kind="auth")
        if self.config.require_new_api_user_header and not self.user_id:
            raise CollectError("missing user_id for new-api-user header", kind="auth")
        save_auth_state(
            self.config.auth_state_file,
            session_value=value,
            domain=self.config.host,
            user_id=self.user_id if self.config.require_new_api_user_header else self.user_id,
        )

    def ensure_auth(self, *, require_user_id: bool = False) -> None:
        state = load_auth_state(self.config.auth_state_file, self.config.host)
        restored = self.restore_auth(state)
        if not restored or (require_user_id and not self.user_id):
            self.login()
        if require_user_id and not self.user_id:
            raise CollectError("token management login missing user_id", kind="auth")

    def recover_auth_once(self) -> None:
        if self._auth_recovery_used:
            raise CollectError("management authentication recovery exhausted", kind="auth")
        self._auth_recovery_used = True
        clear_auth_state(self.config.auth_state_file)
        self.session.cookies.clear()
        self.user_id = None
        self.login()

    def login(self) -> None:
        if self._login_count >= 2:
            raise CollectError("login budget exceeded", kind="auth")
        self._login_count += 1
        self.session.cookies.clear()
        url = f"{self.config.base_url}{LOGIN_PATH}"
        try:
            response = self.session.post(
                url,
                params={"turnstile": ""},
                json={"username": self.config.username, "password": self.config.password},
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                timeout=self._timeout(),
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise CollectError(f"login timeout: {type(exc).__name__}", kind="timeout") from exc
        except requests.RequestException as exc:
            raise CollectError(f"login network: {type(exc).__name__}", kind="network") from exc

        if response.status_code in (301, 302, 303, 307, 308):
            raise CollectError(
                f"login redirect not followed status={response.status_code}",
                kind="contract",
                status_code=response.status_code,
            )

        if _looks_like_html(response) and response.status_code == 403:
            raise CollectError("login blocked (region/cf html)", kind="region", status_code=403)

        payload = _json_payload(response)
        msg = _message_from_payload(payload)

        if response.status_code != 200:
            if _is_captcha_message(msg):
                raise CollectError(f"login captcha: {msg}", kind="captcha", status_code=response.status_code)
            if response.status_code >= 500 or response.status_code == 408:
                raise CollectError(
                    f"login server HTTP {response.status_code}",
                    kind="server",
                    status_code=response.status_code,
                )
            if response.status_code == 429:
                raise CollectError("login rate limited", kind="rate_limit", status_code=429)
            raise CollectError(
                f"login HTTP {response.status_code}: {msg}",
                kind="auth",
                status_code=response.status_code,
            )

        if not payload or payload.get("success") is not True:
            if _is_captcha_message(msg):
                raise CollectError(f"login captcha: {msg}", kind="captcha", status_code=200)
            raise CollectError(f"login business failure: {msg or 'success!=true'}", kind="auth", status_code=200)

        data = payload.get("data")
        user_id: int | None = None
        if isinstance(data, dict) and data.get("id") is not None:
            try:
                user_id = int(data["id"])
            except (TypeError, ValueError):
                user_id = None
        if self.config.require_new_api_user_header:
            if user_id is None or user_id <= 0:
                raise CollectError("login missing positive data.id", kind="auth", status_code=200)
            self.user_id = user_id
        elif user_id is not None and user_id > 0:
            self.user_id = user_id

        if not self.extract_session_cookie():
            raise CollectError("login ok but no session cookie", kind="auth", status_code=200)

        self.persist_auth()
        LOG.info("site=%s login ok", self.config.site_id)

    def _groups_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Connection": "close",
        }
        if self.config.require_new_api_user_header:
            if not self.user_id:
                raise CollectError("new-api-user required but user_id missing", kind="auth")
            headers["new-api-user"] = str(self.user_id)
        return headers

    def _token_headers(self, *, json_body: bool = False) -> dict[str, str]:
        if not self.user_id:
            raise CollectError("token management requires positive user_id", kind="auth")
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Connection": "close",
            "new-api-user": str(self.user_id),
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _management_request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        operation: str,
        retry_429: bool = True,
    ) -> dict[str, Any]:
        rate_limit_attempts = 0
        while True:
            try:
                response = self.session.request(
                    method,
                    f"{self.config.base_url}{path}",
                    params=dict(params or {}),
                    json=dict(json_body) if json_body is not None else None,
                    headers=self._token_headers(json_body=json_body is not None),
                    timeout=self._timeout(),
                    allow_redirects=False,
                )
            except requests.Timeout as exc:
                raise CollectError(f"{operation} timeout", kind="timeout") from exc
            except requests.RequestException as exc:
                raise CollectError(f"{operation} network failure", kind="network") from exc

            if response.status_code in (301, 302, 303, 307, 308):
                raise CollectError(
                    f"{operation} redirect",
                    kind="contract",
                    status_code=response.status_code,
                )
            payload = _json_payload(response)
            if response.status_code == 401 or _is_auth_business_failure(
                response.status_code, payload
            ):
                if not self._auth_recovery_used:
                    self.recover_auth_once()
                    continue
                raise CollectError(
                    f"{operation} authentication failed after recovery",
                    kind="auth",
                    status_code=response.status_code,
                )
            if response.status_code == 429:
                if not retry_429 or rate_limit_attempts >= MANAGEMENT_429_MAX_RETRIES:
                    raise CollectError(
                        operation + " rate limited",
                        kind="rate_limit",
                        status_code=429,
                    )
                rate_limit_attempts += 1
                wait = _parse_retry_after(response)
                if wait is None:
                    wait = min(
                        MANAGEMENT_429_DEFAULT_BACKOFF
                        * (2 ** max(0, rate_limit_attempts - 1)),
                        MANAGEMENT_429_MAX_BACKOFF,
                    )
                remaining = self.remaining()
                if remaining <= 0.5:
                    raise CollectError(
                        operation + " rate limited",
                        kind="rate_limit",
                        status_code=429,
                    )
                wait = min(float(wait), max(0.1, remaining - 0.5))
                LOG.warning(
                    "site=%s %s rate limited; sleep=%.1fs attempt=%s/%s",
                    self.config.site_id,
                    operation,
                    wait,
                    rate_limit_attempts,
                    MANAGEMENT_429_MAX_RETRIES,
                )
                self.sleep_fn(wait)
                continue
            if response.status_code == 408:
                raise CollectError(operation + " timeout", kind="timeout", status_code=408)
            if response.status_code >= 500:
                raise CollectError(
                    operation + " server failure",
                    kind="server",
                    status_code=response.status_code,
                )
            if response.status_code not in (200, 201):
                raise CollectError(
                    f"{operation} HTTP {response.status_code}",
                    kind="error",
                    status_code=response.status_code,
                )
            if not payload or payload.get("success") is not True:
                # Remote business messages may echo keys or request fields.
                raise CollectError(operation + " business failure", kind="contract", status_code=200)
            if self.extract_session_cookie():
                try:
                    self.persist_auth()
                except CollectError:
                    pass
            return payload

    def list_tokens_page(self, page: int, page_size: int) -> dict[str, Any]:
        return self._management_request(
            "GET",
            TOKENS_PATH,
            params={"p": page, "size": page_size},
            operation="token list",
        )

    def get_token_secret(self, token_id: Any) -> str:
        cache_key = str(token_id).strip()
        if not cache_key:
            raise CollectError("token secret missing id", kind="contract", status_code=200)
        cached = self._secret_cache.get(cache_key)
        if cached is not None:
            return cached

        # Pace plaintext key reads; TorchAI returns 429 without Retry-After when burst.
        if self._last_secret_fetch_at > 0:
            elapsed = self.monotonic_fn() - self._last_secret_fetch_at
            if elapsed < SECRET_MIN_INTERVAL_SECONDS:
                pause = SECRET_MIN_INTERVAL_SECONDS - elapsed
                remaining = self.remaining()
                if remaining > 0.2:
                    self.sleep_fn(min(pause, max(0.0, remaining - 0.1)))

        payload = self._management_request(
            "POST",
            f"/api/token/{cache_key}/key",
            operation="token secret",
        )
        self._last_secret_fetch_at = self.monotonic_fn()
        data = payload.get("data")
        secret = data.get("key") if isinstance(data, Mapping) else None
        if not isinstance(secret, str) or not secret.strip():
            raise CollectError("token secret envelope invalid", kind="contract", status_code=200)
        secret = secret.strip()
        self._secret_cache[cache_key] = secret
        return secret

    def create_token(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = self._management_request(
            "POST",
            TOKENS_PATH,
            json_body=payload,
            operation="token create",
        )
        data = result.get("data")
        return dict(data) if isinstance(data, Mapping) else {}

    def update_token(
        self,
        payload: Mapping[str, Any],
        *,
        status_only: bool = False,
    ) -> dict[str, Any]:
        result = self._management_request(
            "PUT",
            TOKENS_PATH,
            params={"status_only": "true"} if status_only else None,
            json_body=payload,
            operation="token update",
        )
        data = result.get("data")
        return dict(data) if isinstance(data, Mapping) else {}

    def fetch_groups_raw(self) -> list[dict[str, Any]]:
        url = f"{self.config.base_url}{GROUPS_PATH}"
        try:
            response = self.session.get(
                url,
                headers=self._groups_headers(),
                timeout=self._timeout(),
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise CollectError(f"groups timeout: {type(exc).__name__}", kind="timeout") from exc
        except requests.RequestException as exc:
            raise CollectError(f"groups network: {type(exc).__name__}", kind="network") from exc

        if response.status_code in (301, 302, 303, 307, 308):
            raise CollectError(
                f"groups redirect status={response.status_code}",
                kind="contract",
                status_code=response.status_code,
            )

        # session rotation
        if self.extract_session_cookie():
            try:
                self.persist_auth()
            except CollectError:
                pass

        if response.status_code == 403 and _looks_like_html(response):
            raise CollectError("groups region/cf html", kind="region", status_code=403)

        payload = _json_payload(response)
        msg = _message_from_payload(payload)

        if response.status_code == 429:
            raise CollectError("groups rate limited", kind="rate_limit", status_code=429)
        if response.status_code == 408 or response.status_code >= 500:
            raise CollectError(
                f"groups server HTTP {response.status_code}",
                kind="server",
                status_code=response.status_code,
            )
        if response.status_code == 401 or _is_auth_business_failure(response.status_code, payload):
            raise CollectError(
                f"groups auth: {msg or response.status_code}",
                kind="auth",
                status_code=response.status_code,
            )
        if response.status_code != 200:
            raise CollectError(
                f"groups HTTP {response.status_code}: {msg}",
                kind="error",
                status_code=response.status_code,
            )

        if not payload or payload.get("success") is not True:
            if _is_auth_business_failure(200, payload):
                raise CollectError(f"groups auth business: {msg}", kind="auth", status_code=200)
            raise CollectError(
                f"groups contract: {msg or 'success!=true'}",
                kind="contract",
                status_code=200,
            )

        data = payload.get("data")
        if not isinstance(data, dict) or not data:
            raise CollectError("groups data missing or empty", kind="contract", status_code=200)

        try:
            return normalize_groups_dict(data)
        except ValueError as exc:
            raise CollectError(f"groups normalize: {exc}", kind="contract", status_code=200) from exc

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass


class ModelsApiClient:
    """Cookie-independent API-key transport; it has no session-login capability."""

    def __init__(
        self,
        config: MonitorConfig,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "Connection": "close",
            }
        )
        if config.proxy_url:
            self.session.proxies.update({"http": config.proxy_url, "https": config.proxy_url})

    def list_models(self, secret: str) -> list[str]:
        try:
            response = self.session.get(
                f"{self.config.base_url}{MODELS_PATH}",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {secret}",
                    "User-Agent": USER_AGENT,
                    "Connection": "close",
                },
                timeout=self.config.timeout,
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise CollectError("models timeout", kind="timeout") from exc
        except requests.RequestException as exc:
            raise CollectError("models network failure", kind="network") from exc

        status = response.status_code
        if status in (401, 403):
            raise CollectError(f"models HTTP {status}", kind="key_auth", status_code=status)
        if status == 429:
            retry_seconds = _parse_retry_after(response)
            retry_at = None
            if retry_seconds is not None:
                retry_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=retry_seconds)
                ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            raise CollectError(
                "models HTTP 429",
                kind="rate_limit",
                status_code=429,
                next_retry_at=retry_at,
            )
        if status == 408:
            raise CollectError("models HTTP 408", kind="timeout", status_code=408)
        if status >= 500:
            raise CollectError(f"models HTTP {status}", kind="server", status_code=status)
        if status != 200:
            raise CollectError(f"models HTTP {status}", kind="error", status_code=status)

        payload = _json_payload(response)
        try:
            import newapi_models as models_mod

            return models_mod.parse_models_payload(payload)
        except (TypeError, ValueError) as exc:
            raise CollectError("models response contract invalid", kind="contract", status_code=200) from exc

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass


def fetch_groups_with_recovery(
    client: NewApiClient,
    *,
    deadline: float,
    monotonic_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> list[dict[str, Any]]:
    transient_retried = False
    while True:
        try:
            return client.fetch_groups_raw()
        except CollectError as exc:
            if exc.kind == "auth" and not client._auth_recovery_used:
                client.recover_auth_once()
                continue
            if exc.kind in ("timeout", "server", "network", "rate_limit") and not transient_retried:
                transient_retried = True
                remaining = deadline - monotonic_fn()
                if remaining <= 0:
                    raise
                sleep_fn(min(TRANSIENT_BACKOFF, remaining))
                continue
            raise


def _list_tokens_all(client: NewApiClient) -> tuple[list[dict[str, Any]], bool]:
    import newapi_models as models_mod

    return models_mod.list_tokens_all(client.list_tokens_page)


def _models_preflight(
    client: NewApiClient,
    models_client: ModelsApiClient,
    *,
    deadline: float,
    monotonic_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> tuple[bool, dict[str, Any]]:
    import newapi_models as models_mod

    checks: dict[str, Any] = {
        "groups_ok": False,
        "groups_count": 0,
        "paging_complete": False,
        "tokens_count": 0,
        "seed_secret_readable": False,
        "models_envelope_ok": False,
    }
    failures: list[str] = []
    try:
        groups = fetch_groups_with_recovery(
            client,
            deadline=deadline,
            monotonic_fn=monotonic_fn,
            sleep_fn=sleep_fn,
        )
        checks["groups_ok"] = bool(groups)
        checks["groups_count"] = len(groups)
    except CollectError as exc:
        failures.append(f"groups_{exc.kind}")
        return False, {"ok": False, "checks": checks, "failures": failures}

    tokens, complete = _list_tokens_all(client)
    checks["paging_complete"] = complete
    checks["tokens_count"] = len(tokens)
    if not complete:
        failures.append("token_paging_incomplete")
    if not tokens:
        failures.append("seed_token_missing")
    if failures:
        return False, {"ok": False, "checks": checks, "failures": failures}

    # Only need one readable seed secret for envelope check; stop early to
    # avoid burning the provider's /key rate limit before bootstrap ensure.
    seed_secret: str | None = None
    last_secret_kind: str | None = None
    for token in sorted(tokens, key=lambda item: str(item.get("id", ""))):
        token_id = token.get("id")
        if token_id is None or not models_mod.norm_id(token_id):
            continue
        try:
            candidate = client.get_token_secret(token_id)
        except CollectError as exc:
            last_secret_kind = exc.kind
            continue
        except Exception as exc:
            last_secret_kind = type(exc).__name__.lower()
            continue
        if isinstance(candidate, str) and candidate.strip():
            seed_secret = candidate.strip()
            break
    if seed_secret is None:
        failures.append(
            f"seed_secret_unreadable:{last_secret_kind}"
            if last_secret_kind
            else "seed_secret_unreadable"
        )
        return False, {"ok": False, "checks": checks, "failures": failures}
    checks["seed_secret_readable"] = True

    try:
        model_ids = models_client.list_models(seed_secret)
        checks["models_envelope_ok"] = isinstance(model_ids, list)
        checks["seed_model_count"] = len(model_ids)
    except CollectError as exc:
        failures.append(f"models_{exc.kind}")
    ok = not failures and checks["models_envelope_ok"] is True
    return ok, {"ok": ok, "checks": checks, "failures": failures}


def acquire_models_lock(
    lock: InstanceLock,
    *,
    wait_seconds: float = MODELS_LOCK_WAIT_SECONDS,
    retries: int = 2,
) -> float:
    total_wait = 0.0
    last_error: RuntimeError | None = None
    attempts = max(1, retries)
    per_attempt = max(0.0, wait_seconds) / attempts
    for _attempt in range(attempts):
        try:
            total_wait += lock.acquire_wait(
                per_attempt,
                poll_interval=MODELS_LOCK_POLL_SECONDS,
            )
            return total_wait
        except RuntimeError as exc:
            last_error = exc
    raise last_error or RuntimeError("models lock unavailable")


def run_models_preflight(
    config: MonitorConfig,
    *,
    monotonic_fn: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> int:
    mono = monotonic_fn or time.monotonic
    sleep = sleep_fn or time.sleep
    deadline = mono() + APP_DEADLINE_SECONDS
    lock = InstanceLock(config.lock_file)
    client = NewApiClient(
        config,
        monotonic_fn=mono,
        sleep_fn=sleep,
        deadline=deadline,
    )
    models_client = ModelsApiClient(config)
    try:
        try:
            lock.acquire()
        except RuntimeError as exc:
            LOG.error("site=%s preflight lock unavailable: %s", config.site_id, exc)
            return 2
        try:
            client.ensure_auth(require_user_id=True)
            ok, result = _models_preflight(
                client,
                models_client,
                deadline=deadline,
                monotonic_fn=mono,
                sleep_fn=sleep,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if ok else 1
        except CollectError as exc:
            LOG.error("site=%s preflight kind=%s: %s", config.site_id, exc.kind, exc)
            return 1
        finally:
            lock.release()
    finally:
        models_client.close()
        client.close()


def run_models_full(
    config: MonitorConfig,
    *,
    bootstrap: bool,
    monotonic_fn: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> int:
    import newapi_models as models_mod

    mono = monotonic_fn or time.monotonic
    sleep = sleep_fn or time.sleep
    gate_store = models_mod.ModelsStore(config.data_dir, config.site_id, models_path=MODELS_PATH)
    if not bootstrap and not gate_store.load().get("bootstrap_completed_at"):
        LOG.error("site=%s refresh requires a completed bootstrap", config.site_id)
        return 1

    lock = InstanceLock(config.lock_file)
    try:
        acquire_models_lock(lock)
    except RuntimeError as exc:
        LOG.error("site=%s models lock unavailable: %s", config.site_id, exc)
        return 2

    store = models_mod.ModelsStore(config.data_dir, config.site_id, models_path=MODELS_PATH)
    if not bootstrap and not store.load().get("bootstrap_completed_at"):
        LOG.error("site=%s refresh bootstrap state changed while waiting for lock", config.site_id)
        lock.release()
        return 1

    started = mono()
    deadline = started + MODELS_DEADLINE_SECONDS
    client = NewApiClient(
        config,
        monotonic_fn=mono,
        sleep_fn=sleep,
        deadline=started + max(APP_DEADLINE_SECONDS, MODELS_DEADLINE_SECONDS),
    )
    models_client = ModelsApiClient(config)
    phase = "bootstrap" if bootstrap else "refresh"
    try:
        try:
            client.ensure_auth(require_user_id=True)
            if bootstrap:
                ok, preflight = _models_preflight(
                    client,
                    models_client,
                    deadline=deadline,
                    monotonic_fn=mono,
                    sleep_fn=sleep,
                )
                if not ok:
                    print(json.dumps(preflight, ensure_ascii=False, indent=2, sort_keys=True))
                    return 1

            groups = fetch_groups_with_recovery(
                client,
                deadline=deadline,
                monotonic_fn=mono,
                sleep_fn=sleep,
            )
            SnapshotStore(
                config.data_dir,
                config.site_id,
                backend=BACKEND_NEWAPI,
            ).persist_success(groups)

            ensure_result = models_mod.ensure_coverage(
                groups,
                list_tokens_fn=lambda: _list_tokens_all(client),
                get_token_secret_fn=client.get_token_secret,
                create_token_fn=client.create_token,
                update_token_fn=client.update_token,
            )
            blocked_groups = {
                models_mod.norm_group(group): "coverage_unknown"
                for group in ensure_result.coverage_unknown
            }
            blocked_groups.update(
                {
                    models_mod.norm_group(failure["group"]): failure["error"]
                    for failure in ensure_result.failures
                    if failure.get("group") not in (None, "*")
                }
            )
            if ensure_result.paging_incomplete:
                blocked_groups.update(
                    {
                        models_mod.norm_group(group.get("id")): "paging_incomplete"
                        for group in groups
                    }
                )
            refresh_result = models_mod.refresh_models_for_groups(
                groups,
                ensure_result.tokens,
                store,
                models_client.list_models,
                source=phase,
                blocked_groups=blocked_groups,
                deadline=deadline,
                time_fn=mono,
            )
            store.update_full_meta(
                target=refresh_result.target_count,
                ok=refresh_result.ok_count,
                failed=refresh_result.failed_count,
                skipped=refresh_result.skipped_count,
                bootstrap=(
                    bootstrap
                    and not ensure_result.failures
                    and not ensure_result.paging_incomplete
                ),
            )
            summary = {
                "site_id": config.site_id,
                "phase": phase,
                "created": ensure_result.created,
                "updated": ensure_result.updated,
                "paging_incomplete": ensure_result.paging_incomplete,
                "coverage_unknown": list(ensure_result.coverage_unknown),
                "target": refresh_result.target_count,
                "ok": refresh_result.ok_count,
                "failed": refresh_result.failed_count,
                "skipped": refresh_result.skipped_count,
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            if (
                ensure_result.paging_incomplete
                or ensure_result.failures
                or refresh_result.failed_count > 0
                or refresh_result.skipped_count > 0
            ):
                return 1
            return 0
        except (CollectError, ValueError) as exc:
            kind = getattr(exc, "kind", "contract")
            LOG.error("site=%s phase=%s kind=%s: %s", config.site_id, phase, kind, exc)
            return 1
    finally:
        models_client.close()
        client.close()
        lock.release()


def _run_incremental_models(
    config: MonitorConfig,
    client: NewApiClient,
    groups: list[dict[str, Any]],
    previous_groups: Mapping[str, Any] | None,
) -> None:
    import newapi_models as models_mod

    if not config.models_incremental_enable:
        return
    models_store = models_mod.ModelsStore(config.data_dir, config.site_id, models_path=MODELS_PATH)
    if not models_store.load().get("bootstrap_completed_at"):
        return

    prior = previous_groups.get("groups") if isinstance(previous_groups, Mapping) else None
    previous_valid = (
        isinstance(previous_groups, Mapping)
        and previous_groups.get("schema_version") == 1
        and previous_groups.get("site_id") == config.site_id
        and previous_groups.get("backend") == BACKEND_NEWAPI
        and isinstance(prior, list)
        and bool(prior)
        and type(previous_groups.get("count")) is int
        and previous_groups.get("count") == len(prior)
        and isinstance(previous_groups.get("content_hash"), str)
    )
    if previous_valid:
        try:
            previous_valid = previous_groups.get("content_hash") == content_hash_groups(prior)
        except (KeyError, TypeError, ValueError):
            previous_valid = False
    if not previous_valid:
        LOG.warning(
            "site=%s phase=incremental skipped: no valid previous groups snapshot",
            config.site_id,
        )
        return
    assert isinstance(prior, list)
    prior_list = prior
    old_ids = {
        models_mod.norm_group(group.get("id"))
        for group in prior_list
        if isinstance(group, Mapping) and group.get("id") is not None
    }
    added = [
        group
        for group in groups
        if models_mod.norm_group(group.get("id")) not in old_ids
    ]
    refresh_set = [
        group
        for group in added
        if models_mod.should_attempt_now(models_store.get_group(group.get("id")))
    ]
    if not refresh_set:
        return

    models_client = ModelsApiClient(config)
    try:
        ensure_result = models_mod.ensure_coverage(
            refresh_set,
            list_tokens_fn=lambda: _list_tokens_all(client),
            get_token_secret_fn=client.get_token_secret,
            create_token_fn=client.create_token,
            update_token_fn=client.update_token,
        )
        blocked_groups = {
            models_mod.norm_group(group): "coverage_unknown"
            for group in ensure_result.coverage_unknown
        }
        blocked_groups.update(
            {
                models_mod.norm_group(failure["group"]): failure["error"]
                for failure in ensure_result.failures
                if failure.get("group") not in (None, "*")
            }
        )
        if ensure_result.paging_incomplete:
            blocked_groups.update(
                {
                    models_mod.norm_group(group.get("id")): "paging_incomplete"
                    for group in refresh_set
                }
            )
        refresh_result = models_mod.refresh_models_for_groups(
            refresh_set,
            ensure_result.tokens,
            models_store,
            models_client.list_models,
            source="incremental",
            blocked_groups=blocked_groups,
        )
        models_store.set_incremental_at()
        LOG.info(
            "site=%s phase=incremental target=%d ok=%d failed=%d created=%d",
            config.site_id,
            refresh_result.target_count,
            refresh_result.ok_count,
            refresh_result.failed_count,
            ensure_result.created,
        )
    except Exception as exc:
        LOG.error(
            "site=%s phase=incremental failed kind=%s",
            config.site_id,
            getattr(exc, "kind", type(exc).__name__),
        )
    finally:
        models_client.close()


# ---------------------------------------------------------------------------
# Collect once
# ---------------------------------------------------------------------------


def run_collect(
    config: MonitorConfig,
    *,
    monotonic_fn: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    session: requests.Session | None = None,
) -> int:
    mono = monotonic_fn or time.monotonic
    sleep = sleep_fn or time.sleep
    deadline = mono() + APP_DEADLINE_SECONDS
    client = NewApiClient(
        config,
        session=session,
        monotonic_fn=mono,
        sleep_fn=sleep,
        deadline=deadline,
    )
    store = SnapshotStore(config.data_dir, config.site_id, backend=BACKEND_NEWAPI)
    lock = InstanceLock(config.lock_file)

    try:
        lock.acquire()
    except RuntimeError as exc:
        LOG.error("site=%s %s", config.site_id, exc)
        return 2

    try:
        try:
            client.ensure_auth()
            groups = fetch_groups_with_recovery(
                client,
                deadline=deadline,
                monotonic_fn=mono,
                sleep_fn=sleep,
            )
            previous_latest = load_latest(store.latest_path)

            try:
                result = store.persist_success(groups)
            except ValueError as exc:
                LOG.error("site=%s persist contract: %s", config.site_id, exc)
                return 1

            if result.changed:
                outcome = "changed"
            else:
                outcome = "unchanged"
            LOG.info(
                "site=%s result=%s count=%d added=%d removed=%d modified=%d hash=%s",
                config.site_id,
                outcome,
                result.count,
                result.added,
                result.removed,
                result.modified,
                result.content_hash[:19] + "…",
            )
            _run_incremental_models(config, client, groups, previous_latest)
            return 0
        except CollectError as exc:
            LOG.error(
                "site=%s collect kind=%s status=%s: %s",
                config.site_id,
                exc.kind,
                exc.status_code,
                exc,
            )
            return 1
    finally:
        client.close()
        lock.release()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="New-API legacy session group collector")
    parser.add_argument("--env-file", type=Path, required=True, help="path to site env file")
    parser.add_argument("--validate", action="store_true", help="validate config only")
    models = parser.add_mutually_exclusive_group()
    models.add_argument(
        "--models-preflight",
        action="store_true",
        help="read-only token and models contract check",
    )
    models.add_argument(
        "--models-bootstrap",
        action="store_true",
        help="explicitly ensure token coverage and initialize model snapshots",
    )
    models.add_argument(
        "--models-refresh",
        action="store_true",
        help="refresh model snapshots after bootstrap",
    )
    return parser.parse_args(argv)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.env_file)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    setup_logging(config.log_level)
    LOG.info("site=%s base=%s data_dir=%s", config.site_id, config.base_url, config.data_dir)

    if args.validate:
        LOG.info("site=%s configuration valid", config.site_id)
        return 0

    try:
        if args.models_preflight:
            return run_models_preflight(config)
        if args.models_bootstrap:
            return run_models_full(config, bootstrap=True)
        if args.models_refresh:
            return run_models_full(config, bootstrap=False)
        return run_collect(config)
    except CollectError as exc:
        LOG.error("site=%s collect kind=%s: %s", config.site_id, exc.kind, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
