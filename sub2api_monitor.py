#!/usr/bin/env python3
"""Sub2API multi-site group monitor (one process per site).

Public entry for AIAPIBANK, PinAI, and compatible Sub2API sites.
Configuration is loaded from a single site env file; no sites.yaml.
"""

from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import hashlib
import json
import logging
import os
import random
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import requests

__version__ = "1.0.0"
USER_AGENT = f"sub2api-monitor/{__version__}"
LOG = logging.getLogger("sub2api-monitor")

# Default failure backoff ladder (seconds).
BACKOFF_SECONDS = (10, 30, 60, 120, 300)
# Default events retention in days.
EVENTS_RETENTION_DAYS = 180
# Site id: lowercase alphanumerics and hyphens only.
SITE_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
# Fixed relative API path: starts with /; no scheme, no .., no query/fragment abuse.
API_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")

STOP_REQUESTED = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    """Invalid configuration."""


class ApiError(RuntimeError):
    """Operational API / poll error with classification."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        kind: str = "error",
        retry_after: float | None = None,
        clear_token: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind  # auth, region, rate_limit, timeout, server, contract, network
        self.retry_after = retry_after
        self.clear_token = clear_token


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def jwt_expiry(token: str) -> int | None:
    """Read JWT exp claim only; no signature verification."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        exp = decoded.get("exp")
        return int(exp) if exp is not None else None
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Atomic file I/O
# ---------------------------------------------------------------------------


def write_bytes_atomic(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Write via temp file, fsync, atomic replace. Cleans up temp on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            mode,
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            # fd is closed by fdopen on success path; reopen risk only if open failed mid-way
            raise
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, data: Any, mode: int = 0o644) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_bytes_atomic(path, payload.encode("utf-8"), mode=mode)


def append_jsonl_fsync(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


# ---------------------------------------------------------------------------
# Env loading
# ---------------------------------------------------------------------------


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from an env file into a dict (does not mutate os.environ)."""
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


def resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    else:
        path = path.resolve()
    return path


def _is_fixed_api_path(path: str) -> bool:
    if not path or not path.startswith("/"):
        return False
    if ".." in path or "//" in path:
        return False
    if "://" in path or "?" in path or "#" in path or "\\" in path:
        return False
    if not API_PATH_RE.match(path):
        return False
    return True


def _check_credential_file_mode(path: Path) -> None:
    try:
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        raise ConfigError(f"cannot stat env file {path}: {exc}") from exc
    # Must not be group/other readable or writable.
    if mode & 0o077:
        raise ConfigError(
            f"credential file permissions too open for {path}: "
            f"{oct(mode)}; require 0600 (no group/other access)"
        )


@dataclass
class MonitorConfig:
    site_id: str
    site_name: str
    base_url: str
    username: str
    password: str
    login_path: str = "/api/v1/auth/login"
    refresh_path: str = "/api/v1/auth/refresh"
    groups_path: str = "/api/v1/groups/available"
    username_field: str = "email"
    poll_interval_seconds: int = 300
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 30.0
    refresh_margin_seconds: int = 600
    request_jitter_seconds: float = 10.0
    data_dir: Path = field(default_factory=lambda: Path("data"))
    token_state_file: Path = field(default_factory=lambda: Path("data/token.json"))
    proxy_url: str | None = None
    log_level: str = "INFO"
    events_retention_days: int = EVENTS_RETENTION_DAYS
    env_file: Path | None = None

    @property
    def timeout(self) -> tuple[float, float]:
        return (self.connect_timeout_seconds, self.read_timeout_seconds)

    @property
    def latest_file(self) -> Path:
        return self.data_dir / "groups_latest.json"

    @property
    def events_file(self) -> Path:
        return self.data_dir / "groups_events.jsonl"

    @property
    def lock_file(self) -> Path:
        return self.data_dir / "monitor.lock"


def validate_config(cfg: MonitorConfig, *, enforce_interval: bool = True) -> None:
    if not cfg.username:
        raise ConfigError("MONITOR_USERNAME is required")
    if not cfg.password:
        raise ConfigError("MONITOR_PASSWORD is required")

    if not cfg.site_id or not SITE_ID_RE.match(cfg.site_id):
        raise ConfigError(
            f"invalid MONITOR_SITE_ID {cfg.site_id!r}: "
            "use lowercase letters, digits, hyphens; no spaces, /, or .."
        )
    if any(ch in cfg.site_id for ch in ("/", "\\", " ", ".")) or ".." in cfg.site_id:
        raise ConfigError(f"illegal characters in MONITOR_SITE_ID: {cfg.site_id!r}")

    parsed = urlparse(cfg.base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigError(f"MONITOR_BASE_URL must be HTTPS with a host: {cfg.base_url!r}")

    for label, path in (
        ("MONITOR_LOGIN_PATH", cfg.login_path),
        ("MONITOR_REFRESH_PATH", cfg.refresh_path),
        ("MONITOR_GROUPS_PATH", cfg.groups_path),
    ):
        if not _is_fixed_api_path(path):
            raise ConfigError(f"{label} must be a fixed relative path starting with /: {path!r}")

    if enforce_interval and cfg.poll_interval_seconds < 60:
        raise ConfigError("POLL_INTERVAL_SECONDS must be at least 60")

    if cfg.connect_timeout_seconds <= 0 or cfg.read_timeout_seconds <= 0:
        raise ConfigError("timeouts must be positive")

    data_dir = cfg.data_dir
    token_file = cfg.token_state_file
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"DATA_DIR not creatable/writable: {data_dir}: {exc}") from exc
    if not os.access(data_dir, os.W_OK):
        raise ConfigError(f"DATA_DIR not writable: {data_dir}")

    # Token file must live under data_dir (consistent paths).
    try:
        token_resolved = token_file if token_file.is_absolute() else token_file
        data_resolved = data_dir.resolve()
        # Resolve parent even if token file does not exist yet.
        token_parent = token_file.parent
        token_parent.mkdir(parents=True, exist_ok=True)
        token_resolved = (token_parent.resolve() / token_file.name)
        try:
            token_resolved.relative_to(data_resolved)
        except ValueError as exc:
            raise ConfigError(
                f"TOKEN_STATE_FILE {token_file} is not under DATA_DIR {data_dir}"
            ) from exc
    except ConfigError:
        raise
    except OSError as exc:
        raise ConfigError(f"cannot resolve data/token paths: {exc}") from exc

    if cfg.env_file is not None and cfg.env_file.exists():
        _check_credential_file_mode(cfg.env_file)


def load_config(
    env_file: Path,
    *,
    environ: Mapping[str, str] | None = None,
    enforce_interval: bool = True,
) -> MonitorConfig:
    """Load and validate config from env file (+ optional process environ overrides)."""
    env_file = env_file.resolve()
    file_vars = parse_env_file(env_file)
    if environ is None:
        environ = os.environ
    # File values fill missing process env (setdefault). Explicit process env wins.
    # Tests may pass a closed mapping instead of os.environ.
    if environ is os.environ:
        for k, v in file_vars.items():
            os.environ.setdefault(k, v)

        def get(key: str, default: str = "") -> str:
            return os.environ.get(key, file_vars.get(key, default))
    else:
        effective = dict(file_vars)
        effective.update({k: v for k, v in environ.items() if v is not None})

        def get(key: str, default: str = "") -> str:
            return effective.get(key, default)

    base_dir = env_file.parent
    site_id = get("MONITOR_SITE_ID") or env_file.stem
    site_name = get("MONITOR_SITE_NAME") or site_id
    base_url = get("MONITOR_BASE_URL") or get("AIAPIBANK_BASE_URL") or ""
    username = get("MONITOR_USERNAME") or get("AIAPIBANK_EMAIL") or ""
    password = get("MONITOR_PASSWORD") or get("AIAPIBANK_PASSWORD") or ""

    data_dir_raw = get("DATA_DIR") or f"data/{site_id}"
    token_raw = get("TOKEN_STATE_FILE") or str(Path(data_dir_raw) / "token.json")
    data_dir = resolve_path(data_dir_raw, base_dir)
    token_file = resolve_path(token_raw, base_dir)

    proxy = get("MONITOR_PROXY_URL") or ""
    proxy_url = proxy.strip() or None

    try:
        poll_interval = int(get("POLL_INTERVAL_SECONDS", "300") or "300")
        connect_timeout = float(get("CONNECT_TIMEOUT_SECONDS", "10") or "10")
        read_timeout = float(get("READ_TIMEOUT_SECONDS", "30") or "30")
        refresh_margin = int(get("REFRESH_MARGIN_SECONDS", "600") or "600")
        jitter = float(get("REQUEST_JITTER_SECONDS", "10") or "10")
        retention = int(get("EVENTS_RETENTION_DAYS", str(EVENTS_RETENTION_DAYS)) or str(EVENTS_RETENTION_DAYS))
    except ValueError as exc:
        raise ConfigError(f"invalid numeric configuration: {exc}") from exc

    login_path = get("MONITOR_LOGIN_PATH", "/api/v1/auth/login") or "/api/v1/auth/login"
    refresh_path = get("MONITOR_REFRESH_PATH", "/api/v1/auth/refresh") or "/api/v1/auth/refresh"
    groups_path = get("MONITOR_GROUPS_PATH", "/api/v1/groups/available") or "/api/v1/groups/available"
    username_field = get("MONITOR_USERNAME_FIELD", "email") or "email"
    log_level = get("LOG_LEVEL", "INFO") or "INFO"
    # Keep paths exactly as configured; validation rejects non-absolute API paths.

    cfg = MonitorConfig(
        site_id=site_id.strip(),
        site_name=site_name.strip(),
        base_url=base_url.strip().rstrip("/"),
        username=username,
        password=password,
        login_path=login_path.strip(),
        refresh_path=refresh_path.strip(),
        groups_path=groups_path.strip(),
        username_field=username_field,
        poll_interval_seconds=poll_interval,
        connect_timeout_seconds=connect_timeout,
        read_timeout_seconds=read_timeout,
        refresh_margin_seconds=refresh_margin,
        request_jitter_seconds=jitter,
        data_dir=data_dir,
        token_state_file=token_file,
        proxy_url=proxy_url,
        log_level=log_level,
        events_retention_days=retention,
        env_file=env_file,
    )
    validate_config(cfg, enforce_interval=enforce_interval)
    return cfg


# ---------------------------------------------------------------------------
# Token store
# ---------------------------------------------------------------------------


@dataclass
class TokenState:
    access_token: str | None = None
    refresh_token: str | None = None
    access_expires_at: int | None = None
    saved_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_expires_at": self.access_expires_at,
            "saved_at": self.saved_at,
        }


class TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = TokenState()
        self.load()

    def load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("token state root is not an object")
            self.state = TokenState(
                access_token=data.get("access_token") or None,
                refresh_token=data.get("refresh_token") or None,
                access_expires_at=_maybe_int(data.get("access_expires_at")),
                saved_at=data.get("saved_at"),
            )
            # Backfill exp from JWT if missing.
            if self.state.access_token and self.state.access_expires_at is None:
                self.state.access_expires_at = jwt_expiry(self.state.access_token)
        except FileNotFoundError:
            self.state = TokenState()
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            LOG.warning("Ignoring unreadable token state (%s); will re-login", type(exc).__name__)
            self.state = TokenState()

    def save(self, state: TokenState) -> None:
        state.saved_at = utc_now_iso()
        if state.access_token and state.access_expires_at is None:
            state.access_expires_at = jwt_expiry(state.access_token)
        write_json_atomic(self.path, state.to_dict(), mode=0o600)
        self.state = state

    def clear_access(self) -> None:
        """Clear access only (rare); not used for timeout/5xx."""
        self.state.access_token = None
        self.state.access_expires_at = None
        self.save(self.state)


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Response classification helpers
# ---------------------------------------------------------------------------


def _response_snippet(response: requests.Response, limit: int = 200) -> str:
    try:
        text = response.text or ""
    except Exception:
        return ""
    text = text.replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    # Never include obvious bearer-like long tokens.
    text = re.sub(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[jwt]", text)
    text = re.sub(r"(?i)(access_token|refresh_token|password)\s*[\"':=]+\s*[\"']?[^\"'\\s,]+", r"\1=[redacted]", text)
    return text


def is_cloudflare_or_region_html(response: requests.Response) -> bool:
    ctype = (response.headers.get("Content-Type") or "").lower()
    body = ""
    try:
        body = (response.text or "")[:4000].lower()
    except Exception:
        body = ""
    if "text/html" in ctype or body.lstrip().startswith("<!doctype") or body.lstrip().startswith("<html"):
        markers = (
            "cloudflare",
            "cf-ray",
            "attention required",
            "access denied",
            "sorry, you have been blocked",
            "just a moment",
            "geo",
            "region",
            "not available in your",
            "country",
            "forbidden",
        )
        if any(m in body for m in markers) or "text/html" in ctype:
            return True
    return False


def is_token_error_json(response: requests.Response) -> bool:
    try:
        payload = response.json()
    except (ValueError, requests.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    blob = json.dumps(payload, ensure_ascii=False).lower()
    markers = (
        "invalid token",
        "token expired",
        "token is expired",
        "unauthorized",
        "unauthenticated",
        "not authenticated",
        "jwt",
        "access_token",
        "refresh_token",
        "authentication",
        "auth failed",
        "login required",
    )
    code = payload.get("code") or payload.get("error") or payload.get("message") or ""
    code_s = str(code).lower()
    return any(m in blob for m in markers) or any(m in code_s for m in ("auth", "token", "unauth"))


def parse_retry_after(response: requests.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Auth + Groups client
# ---------------------------------------------------------------------------


class AuthGroupClient:
    """Login / refresh / groups with classified errors. Does not log secrets."""

    def __init__(
        self,
        config: MonitorConfig,
        token_store: TokenStore,
        session: requests.Session | None = None,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.token_store = token_store
        self.time_fn = time_fn or time.time
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            }
        )
        # Avoid long-idle keep-alive reuse across poll cycles.
        self.session.headers["Connection"] = "close"
        if config.proxy_url:
            self.session.proxies.update({"http": config.proxy_url, "https": config.proxy_url})

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}{path}"

    def access_token(self) -> str | None:
        return self.token_store.state.access_token

    def token_needs_refresh(self) -> bool:
        state = self.token_store.state
        if not state.access_token:
            return True
        exp = state.access_expires_at
        if exp is None:
            exp = jwt_expiry(state.access_token)
            state.access_expires_at = exp
        if exp is None:
            # Opaque token without exp: treat as valid until 401.
            return False
        return exp <= self.time_fn() + self.config.refresh_margin_seconds

    def login(self) -> None:
        LOG.info("site=%s login starting (user=%s)", self.config.site_id, _mask_user(self.config.username))
        try:
            response = self.session.post(
                self._url(self.config.login_path),
                json={
                    self.config.username_field: self.config.username,
                    "password": self.config.password,
                },
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, "Connection": "close"},
                timeout=self.config.timeout,
            )
        except requests.Timeout as exc:
            raise ApiError(f"login timeout: {type(exc).__name__}", kind="timeout") from exc
        except requests.RequestException as exc:
            raise ApiError(f"login network error: {type(exc).__name__}", kind="network") from exc

        if response.status_code != 200:
            kind = self._classify_auth_failure(response)
            raise ApiError(
                f"login failed HTTP {response.status_code}",
                status_code=response.status_code,
                kind=kind,
            )
        try:
            payload = response.json()
            data = payload["data"]
            access = data["access_token"]
            if not isinstance(access, str) or not access:
                raise KeyError("access_token")
            refresh = data.get("refresh_token")
            if refresh is not None and not isinstance(refresh, str):
                refresh = None
        except (ValueError, KeyError, TypeError, requests.JSONDecodeError) as exc:
            raise ApiError("login response structure error", kind="contract") from exc

        new_state = TokenState(
            access_token=access,
            refresh_token=refresh if refresh else self.token_store.state.refresh_token,
            access_expires_at=jwt_expiry(access),
        )
        self.token_store.save(new_state)
        LOG.info("site=%s login ok", self.config.site_id)

    def refresh(self) -> None:
        rt = self.token_store.state.refresh_token
        if not rt:
            raise ApiError("no refresh_token available", kind="auth")
        LOG.info("site=%s refresh starting", self.config.site_id)
        try:
            response = self.session.post(
                self._url(self.config.refresh_path),
                json={"refresh_token": rt},
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, "Connection": "close"},
                timeout=self.config.timeout,
            )
        except requests.Timeout as exc:
            raise ApiError(f"refresh timeout: {type(exc).__name__}", kind="timeout") from exc
        except requests.RequestException as exc:
            raise ApiError(f"refresh network error: {type(exc).__name__}", kind="network") from exc

        if response.status_code != 200:
            kind = self._classify_auth_failure(response)
            raise ApiError(
                f"refresh failed HTTP {response.status_code}",
                status_code=response.status_code,
                kind=kind,
            )
        try:
            payload = response.json()
            data = payload.get("data", payload)
            access = data["access_token"]
            if not isinstance(access, str) or not access:
                raise KeyError("access_token")
            new_refresh = data.get("refresh_token")
        except (ValueError, KeyError, TypeError, requests.JSONDecodeError) as exc:
            raise ApiError("refresh response structure error", kind="contract") from exc

        # Keep old refresh if response omits a new one.
        if not new_refresh:
            new_refresh = rt
        new_state = TokenState(
            access_token=access,
            refresh_token=new_refresh,
            access_expires_at=jwt_expiry(access),
        )
        self.token_store.save(new_state)
        LOG.info("site=%s refresh ok", self.config.site_id)

    def ensure_token(self) -> None:
        """Ensure a usable access token: login or refresh only when needed."""
        if not self.token_store.state.access_token:
            self.login()
            return
        if not self.token_needs_refresh():
            return
        # Near expiry: prefer refresh, fall back to password login once.
        if self.token_store.state.refresh_token:
            try:
                self.refresh()
                return
            except ApiError as exc:
                LOG.warning("site=%s refresh failed (%s); trying password login", self.config.site_id, exc.kind)
        self.login()

    def recover_auth(self) -> None:
        """One refresh attempt, then one password login."""
        if self.token_store.state.refresh_token:
            try:
                self.refresh()
                return
            except ApiError as exc:
                LOG.warning(
                    "site=%s auth recovery refresh failed (%s); trying login",
                    self.config.site_id,
                    exc.kind,
                )
        self.login()

    def fetch_groups_raw(self) -> list[dict[str, Any]]:
        """GET groups once with current access token (no auth recovery)."""
        token = self.token_store.state.access_token
        if not token:
            raise ApiError("no access token", kind="auth")
        try:
            response = self.session.get(
                self._url(self.config.groups_path),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Connection": "close",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=self.config.timeout,
            )
        except requests.Timeout as exc:
            raise ApiError(f"groups timeout: {type(exc).__name__}", kind="timeout") from exc
        except requests.RequestException as exc:
            raise ApiError(f"groups network error: {type(exc).__name__}", kind="network") from exc

        return self._handle_groups_response(response)

    def get_groups(self) -> list[dict[str, Any]]:
        """Ensure token, fetch groups, recover once on auth failure."""
        self.ensure_token()
        try:
            return self.fetch_groups_raw()
        except ApiError as exc:
            if exc.kind == "auth":
                LOG.warning("site=%s groups auth failure; recovering once", self.config.site_id)
                self.recover_auth()
                return self.fetch_groups_raw()
            raise

    def _handle_groups_response(self, response: requests.Response) -> list[dict[str, Any]]:
        status = response.status_code
        if status == 401:
            raise ApiError("groups HTTP 401", status_code=401, kind="auth")
        if status == 403:
            if is_cloudflare_or_region_html(response):
                raise ApiError(
                    "groups HTTP 403 region/egress restriction",
                    status_code=403,
                    kind="region",
                )
            if is_token_error_json(response):
                raise ApiError("groups HTTP 403 token error", status_code=403, kind="auth")
            # Ambiguous 403: treat as region-like to avoid login loops.
            raise ApiError(
                "groups HTTP 403 (non-token)",
                status_code=403,
                kind="region",
            )
        if status == 429:
            raise ApiError(
                "groups HTTP 429",
                status_code=429,
                kind="rate_limit",
                retry_after=parse_retry_after(response),
            )
        if status == 408:
            raise ApiError("groups HTTP 408", status_code=408, kind="timeout")
        if status >= 500:
            raise ApiError(f"groups HTTP {status}", status_code=status, kind="server")
        if status != 200:
            raise ApiError(f"groups HTTP {status}", status_code=status, kind="error")

        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise ApiError("groups response is not JSON", kind="contract") from exc

        if not isinstance(payload, dict):
            raise ApiError("groups JSON root is not an object", kind="contract")
        if "data" not in payload:
            raise ApiError("groups JSON missing data", kind="contract")
        data = payload["data"]
        if not isinstance(data, list):
            raise ApiError("groups data is not a list", kind="contract")

        groups: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                raise ApiError("group item is not an object", kind="contract")
            # Safe field reads; keep full dict but ensure id/name/rate/status accessible.
            _ = item.get("id"), item.get("name"), item.get("rate_multiplier"), item.get("status")
            groups.append(item)
        return groups

    def _classify_auth_failure(self, response: requests.Response) -> str:
        if response.status_code == 403 and is_cloudflare_or_region_html(response):
            return "region"
        if response.status_code in (401, 403) and is_token_error_json(response):
            return "auth"
        if response.status_code == 429:
            return "rate_limit"
        if response.status_code >= 500:
            return "server"
        if response.status_code == 403 and is_cloudflare_or_region_html(response):
            return "region"
        return "auth" if response.status_code in (401, 403) else "error"

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass


def _mask_user(username: str) -> str:
    if "@" in username:
        local, domain = username.split("@", 1)
        if len(local) <= 2:
            return f"**@{domain}"
        return f"{local[:2]}***@{domain}"
    if len(username) <= 2:
        return "***"
    return f"{username[:2]}***"


# ---------------------------------------------------------------------------
# Groups canonicalize / hash / diff / writers
# ---------------------------------------------------------------------------


def group_stable_id(group: Mapping[str, Any]) -> str:
    gid = group.get("id")
    if gid is None:
        gid = group.get("name", "")
    return str(gid)


def canonicalize_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable sort by group id; each object uses sorted keys via dumps later."""
    return sorted(groups, key=lambda g: (group_stable_id(g), json.dumps(g, sort_keys=True, ensure_ascii=False)))


def content_hash_groups(groups: list[dict[str, Any]]) -> str:
    canonical = canonicalize_groups(groups)
    # Hash only group content; ignore fetched_at / site metadata.
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def summarize_group(group: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": group.get("id"),
        "name": group.get("name"),
        "rate_multiplier": group.get("rate_multiplier"),
        "status": group.get("status"),
    }


def diff_groups(
    old_groups: list[dict[str, Any]] | None,
    new_groups: list[dict[str, Any]],
) -> dict[str, list[Any]]:
    old_map = {group_stable_id(g): g for g in (old_groups or [])}
    new_map = {group_stable_id(g): g for g in new_groups}
    added = sorted(new_map.keys() - old_map.keys())
    removed = sorted(old_map.keys() - new_map.keys())
    modified: list[Any] = []
    for gid in sorted(old_map.keys() & new_map.keys()):
        old_c = json.dumps(old_map[gid], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        new_c = json.dumps(new_map[gid], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if old_c != new_c:
            modified.append(gid)
    # Prefer numeric ids when possible for readability.
    def coerce(ids: list[str]) -> list[Any]:
        out: list[Any] = []
        for i in ids:
            try:
                if i.isdigit() or (i.startswith("-") and i[1:].isdigit()):
                    out.append(int(i))
                else:
                    out.append(i)
            except Exception:
                out.append(i)
        return out

    return {
        "added": coerce(added),
        "removed": coerce(removed),
        "modified": coerce([str(m) for m in modified]),
    }


def load_latest(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        LOG.warning("Ignoring unreadable latest snapshot at %s", path)
    return None


def events_has_hash(path: Path, content_hash: str) -> bool:
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and rec.get("content_hash") == content_hash:
                    return True
    except OSError:
        return False
    return False


def prune_events(path: Path, retention_days: int, now: float | None = None) -> None:
    """Drop events older than retention_days. Rewrites file atomically if needed."""
    if not path.exists() or retention_days <= 0:
        return
    now_ts = now if now is not None else time.time()
    cutoff = now_ts - retention_days * 86400
    kept: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    kept.append(raw)
                    continue
                observed = rec.get("observed_at") or rec.get("fetched_at")
                if not observed:
                    kept.append(raw)
                    continue
                try:
                    # Support Z and +00:00
                    ts = datetime.fromisoformat(str(observed).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    kept.append(raw)
                    continue
                if ts >= cutoff:
                    kept.append(raw)
        # Only rewrite if we dropped something.
        original = path.read_text(encoding="utf-8")
        new_text = "".join(l + "\n" for l in kept)
        if new_text != original:
            write_bytes_atomic(path, new_text.encode("utf-8"), mode=0o644)
    except OSError as exc:
        LOG.warning("events prune skipped: %s", type(exc).__name__)


class SnapshotStore:
    """Crash-consistent latest + events writers."""

    def __init__(self, config: MonitorConfig) -> None:
        self.config = config
        self.latest_path = config.latest_file
        self.events_path = config.events_file

    def persist_success(self, groups: list[dict[str, Any]]) -> dict[str, Any]:
        canonical = canonicalize_groups(groups)
        digest = content_hash_groups(canonical)
        fetched_at = utc_now_iso()
        previous = load_latest(self.latest_path)
        prev_hash = previous.get("content_hash") if previous else None
        prev_groups = previous.get("groups") if previous else None
        if prev_groups is not None and not isinstance(prev_groups, list):
            prev_groups = None

        record = {
            "site_id": self.config.site_id,
            "fetched_at": fetched_at,
            "count": len(canonical),
            "content_hash": digest,
            "groups": canonical,
        }

        if prev_hash != digest:
            # Dedup: if event already recorded (crash after event, before latest), skip append.
            if not events_has_hash(self.events_path, digest):
                if previous is None or prev_hash is None:
                    # First tracked snapshot (including migration from pre-hash latest).
                    event_name = "initial"
                    diff = diff_groups(prev_groups, canonical) if prev_groups else {
                        "added": [],
                        "removed": [],
                        "modified": [],
                    }
                else:
                    event_name = "groups_changed"
                    diff = diff_groups(prev_groups, canonical)
                event = {
                    "site_id": self.config.site_id,
                    "observed_at": fetched_at,
                    "event": event_name,
                    "added": diff["added"],
                    "removed": diff["removed"],
                    "modified": diff["modified"],
                    "content_hash": digest,
                }
                # 1) append event + fsync  2) atomic replace latest
                append_jsonl_fsync(self.events_path, event)
            write_json_atomic(self.latest_path, record, mode=0o644)
        else:
            # Same content: still update latest fetched_at / count metadata atomically.
            write_json_atomic(self.latest_path, record, mode=0o644)

        prune_events(self.events_path, self.config.events_retention_days)
        return record


# ---------------------------------------------------------------------------
# Instance lock
# ---------------------------------------------------------------------------


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise ConfigError(f"another monitor instance holds the lock: {self.path}") from exc
            raise
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"{os.getpid()}\n")
        self._fh.flush()

    def release(self) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


# ---------------------------------------------------------------------------
# Monitor loop
# ---------------------------------------------------------------------------


class GroupMonitor:
    def __init__(
        self,
        config: MonitorConfig,
        client: AuthGroupClient,
        snapshots: SnapshotStore | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        stop_flag: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.snapshots = snapshots or SnapshotStore(config)
        self.sleep_fn = sleep_fn or time.sleep
        self.monotonic_fn = monotonic_fn or time.monotonic
        self.stop_flag = stop_flag or (lambda: STOP_REQUESTED)
        self.failures = 0

    def poll_once(self) -> dict[str, Any]:
        groups = self.client.get_groups()
        record = self.snapshots.persist_success(groups)
        self.failures = 0
        LOG.info(
            "site=%s fetched %d groups hash=%s",
            self.config.site_id,
            record["count"],
            record["content_hash"][:19] + "…",
        )
        for group in groups:
            LOG.info(
                "site=%s group id=%s name=%s rate=%sx status=%s",
                self.config.site_id,
                group.get("id", "-"),
                group.get("name", "-"),
                group.get("rate_multiplier", "-"),
                group.get("status", "-"),
            )
        return record

    def backoff_delay(self, exc: Exception | None = None) -> float:
        idx = min(max(self.failures - 1, 0), len(BACKOFF_SECONDS) - 1)
        delay = float(BACKOFF_SECONDS[idx])
        if isinstance(exc, ApiError) and exc.retry_after is not None:
            delay = max(delay, float(exc.retry_after))
        # Cap by poll interval so we don't sleep longer than the normal cycle by default.
        delay = min(delay, float(max(self.config.poll_interval_seconds, BACKOFF_SECONDS[-1])))
        return delay

    def success_delay(self, elapsed: float) -> float:
        jitter = 0.0
        if self.config.request_jitter_seconds > 0:
            jitter = random.uniform(0.0, float(self.config.request_jitter_seconds))
        return max(0.0, float(self.config.poll_interval_seconds) - elapsed + jitter)

    def interruptible_sleep(self, seconds: float) -> None:
        deadline = self.monotonic_fn() + max(0.0, seconds)
        while not self.stop_flag() and self.monotonic_fn() < deadline:
            remaining = deadline - self.monotonic_fn()
            self.sleep_fn(min(1.0, remaining))

    def run_loop(self) -> int:
        while not self.stop_flag():
            started = self.monotonic_fn()
            # Fresh connection each cycle: close pooled keep-alives.
            try:
                self.client.session.close()
            except Exception:
                pass
            try:
                self.poll_once()
                elapsed = self.monotonic_fn() - started
                delay = self.success_delay(elapsed)
            except ApiError as exc:
                self.failures += 1
                # Never clear token on timeout/5xx/network.
                if exc.kind in ("timeout", "server", "network", "region", "rate_limit", "contract"):
                    LOG.error(
                        "site=%s poll failed kind=%s status=%s failures=%d: %s",
                        self.config.site_id,
                        exc.kind,
                        exc.status_code,
                        self.failures,
                        exc,
                    )
                else:
                    LOG.error(
                        "site=%s poll failed kind=%s failures=%d: %s",
                        self.config.site_id,
                        exc.kind,
                        self.failures,
                        exc,
                    )
                delay = self.backoff_delay(exc)
            except OSError as exc:
                self.failures += 1
                LOG.error("site=%s poll OS error failures=%d: %s", self.config.site_id, self.failures, type(exc).__name__)
                delay = self.backoff_delay()
            self.interruptible_sleep(delay)
        LOG.info("site=%s stop requested; exiting loop", self.config.site_id)
        return 0


# ---------------------------------------------------------------------------
# Signal + CLI
# ---------------------------------------------------------------------------


def stop_handler(signum: int, _frame: Any) -> None:
    global STOP_REQUESTED
    LOG.info("received signal %s; stopping", signum)
    STOP_REQUESTED = True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sub2API multi-site group monitor")
    parser.add_argument("--env-file", type=Path, required=True, help="path to site env file")
    parser.add_argument("--once", action="store_true", help="fetch once and exit")
    parser.add_argument("--validate", action="store_true", help="validate config only")
    return parser.parse_args(argv)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def build_monitor(config: MonitorConfig) -> tuple[GroupMonitor, InstanceLock, AuthGroupClient]:
    token_store = TokenStore(config.token_state_file)
    client = AuthGroupClient(config, token_store)
    monitor = GroupMonitor(config, client)
    lock = InstanceLock(config.lock_file)
    return monitor, lock, client


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.env_file)
    except ConfigError as exc:
        # Logging may not be configured yet.
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    setup_logging(config.log_level)
    LOG.info(
        "site=%s name=%s base=%s data_dir=%s",
        config.site_id,
        config.site_name,
        config.base_url,
        config.data_dir,
    )

    if args.validate:
        LOG.info("site=%s configuration valid", config.site_id)
        return 0

    monitor, lock, client = build_monitor(config)
    try:
        lock.acquire()
    except ConfigError as exc:
        LOG.error("%s", exc)
        return 2

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    try:
        if args.once:
            try:
                monitor.poll_once()
                return 0
            except ApiError as exc:
                LOG.error("site=%s once failed kind=%s: %s", config.site_id, exc.kind, exc)
                return 1
            except OSError as exc:
                LOG.error("site=%s once OS error: %s", config.site_id, type(exc).__name__)
                return 1
        return monitor.run_loop()
    finally:
        client.close()
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
