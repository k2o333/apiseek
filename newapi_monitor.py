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
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import requests

from monitor_storage import (
    BACKEND_NEWAPI,
    InstanceLock,
    SnapshotStore,
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
DEFAULT_CONNECT = 5.0
DEFAULT_READ = 20.0
APP_DEADLINE_SECONDS = 170.0
TRANSIENT_BACKOFF = 5.0

PROJECT_ROOT = Path(__file__).resolve().parent


class ConfigError(ValueError):
    """Invalid configuration."""


class CollectError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "error", status_code: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


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
            state = load_auth_state(config.auth_state_file, config.host)
            has_auth = client.restore_auth(state)
            if not has_auth:
                client.login()

            groups: list[dict[str, Any]] | None = None
            last_err: CollectError | None = None
            auth_recovered = False
            transient_retried = False

            while True:
                try:
                    groups = client.fetch_groups_raw()
                    break
                except CollectError as exc:
                    last_err = exc
                    LOG.error(
                        "site=%s fetch kind=%s status=%s: %s",
                        config.site_id,
                        exc.kind,
                        exc.status_code,
                        exc,
                    )
                    if exc.kind == "auth" and not auth_recovered:
                        auth_recovered = True
                        clear_auth_state(config.auth_state_file)
                        client.session.cookies.clear()
                        client.user_id = None
                        try:
                            client.login()
                        except CollectError as login_exc:
                            LOG.error(
                                "site=%s re-login kind=%s: %s",
                                config.site_id,
                                login_exc.kind,
                                login_exc,
                            )
                            return 1
                        continue
                    if exc.kind in ("timeout", "server", "network", "rate_limit") and not transient_retried:
                        transient_retried = True
                        delay = TRANSIENT_BACKOFF
                        rem = deadline - mono()
                        if rem <= 0:
                            return 1
                        sleep(min(delay, rem))
                        continue
                    # captcha/region/contract/permanent/auth after recovery
                    return 1

            if groups is None:
                return 1

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
        return run_collect(config)
    except CollectError as exc:
        LOG.error("site=%s collect kind=%s: %s", config.site_id, exc.kind, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
