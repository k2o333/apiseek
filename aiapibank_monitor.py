#!/usr/bin/env python3
"""Poll groups from a configurable authenticated API."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


LOG = logging.getLogger("group-monitor")
STOP_REQUESTED = False


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jwt_expiry(token: str) -> int | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        return int(decoded["exp"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def write_json_atomic(path: Path, data: Any, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(mode)
    temporary.replace(path)


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GroupApiClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        state_file: Path,
        login_path: str = "/api/v1/auth/login",
        groups_path: str = "/api/v1/groups/available",
        username_field: str = "email",
        site_name: str = "AIAPIBANK",
        proxy_url: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.state_file = state_file
        self.login_path = "/" + login_path.lstrip("/")
        self.groups_path = "/" + groups_path.lstrip("/")
        self.username_field = username_field
        self.site_name = site_name
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "group-monitor/2.0",
            }
        )
        if proxy_url:
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self._load_state()

    def _load_state(self) -> None:
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.access_token = state.get("access_token")
            self.refresh_token = state.get("refresh_token")
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("Ignoring unreadable token state: %s", exc)

    def _save_state(self) -> None:
        write_json_atomic(
            self.state_file,
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "saved_at": utc_now(),
            },
            mode=0o600,
        )

    def token_needs_refresh(self, margin_seconds: int = 600) -> bool:
        if not self.access_token:
            return True
        expiry = jwt_expiry(self.access_token)
        return expiry is not None and expiry <= time.time() + margin_seconds

    def login(self) -> None:
        LOG.info("Logging in to %s as %s", self.site_name, self.username)
        try:
            response = self.session.post(
                f"{self.base_url}{self.login_path}",
                json={self.username_field: self.username, "password": self.password},
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            raise ApiError(f"login request failed: {exc}") from exc

        if response.status_code != 200:
            raise ApiError(
                f"login failed with HTTP {response.status_code}",
                response.status_code,
            )
        try:
            payload = response.json()
            data = payload["data"]
            self.access_token = data["access_token"]
            self.refresh_token = data.get("refresh_token")
        except (requests.JSONDecodeError, KeyError, TypeError) as exc:
            raise ApiError("login response did not contain access_token") from exc
        self._save_state()

    def get_groups(self) -> list[dict[str, Any]]:
        if self.token_needs_refresh():
            self.login()

        for attempt in range(2):
            try:
                response = self.session.get(
                    f"{self.base_url}{self.groups_path}",
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Connection": "close",
                    },
                    timeout=(10, 30),
                )
            except requests.RequestException as exc:
                raise ApiError(f"groups request failed: {exc}") from exc

            if response.status_code in (401, 403) and attempt == 0:
                LOG.warning("Session rejected with HTTP %s; logging in again", response.status_code)
                self.login()
                continue
            if response.status_code != 200:
                raise ApiError(
                    f"groups request failed with HTTP {response.status_code}",
                    response.status_code,
                )
            try:
                payload = response.json()
                groups = payload["data"]
            except (requests.JSONDecodeError, KeyError, TypeError) as exc:
                raise ApiError("groups response did not contain data") from exc
            if not isinstance(groups, list):
                raise ApiError("groups response data was not a list")
            return groups

        raise ApiError("authentication failed after re-login")


class GroupMonitor:
    def __init__(self, client: GroupApiClient, output_dir: Path) -> None:
        self.client = client
        self.output_dir = output_dir
        self.latest_file = output_dir / "groups_latest.json"
        self.history_file = output_dir / "groups_history.jsonl"

    def poll(self) -> int:
        groups = self.client.get_groups()
        record = {"fetched_at": utc_now(), "count": len(groups), "groups": groups}
        write_json_atomic(self.latest_file, record)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.history_file.open("a", encoding="utf-8") as history:
            history.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        LOG.info("Fetched %d groups", len(groups))
        for group in groups:
            LOG.info(
                "Group id=%s name=%s rate=%sx status=%s",
                group.get("id", "-"),
                group.get("name", "-"),
                group.get("rate_multiplier", "-"),
                group.get("status", "-"),
            )
        return len(groups)


def stop_handler(signum: int, _frame: Any) -> None:
    global STOP_REQUESTED
    LOG.info("Received signal %s; stopping", signum)
    STOP_REQUESTED = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor authenticated API groups")
    parser.add_argument("--once", action="store_true", help="fetch once and exit")
    parser.add_argument("--env-file", type=Path, default=Path("config.env"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    username = os.environ.get("MONITOR_USERNAME") or os.environ.get("AIAPIBANK_EMAIL")
    password = os.environ.get("MONITOR_PASSWORD") or os.environ.get("AIAPIBANK_PASSWORD")
    if not username or not password:
        LOG.error("MONITOR_USERNAME and MONITOR_PASSWORD are required")
        return 2

    interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))
    if interval < 60:
        LOG.error("POLL_INTERVAL_SECONDS must be at least 60")
        return 2
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    state_file = Path(os.environ.get("TOKEN_STATE_FILE", str(data_dir / "token_state.json")))
    client = GroupApiClient(
        os.environ.get(
            "MONITOR_BASE_URL",
            os.environ.get("AIAPIBANK_BASE_URL", "https://www.aiapibank.com"),
        ),
        username,
        password,
        state_file,
        login_path=os.environ.get("MONITOR_LOGIN_PATH", "/api/v1/auth/login"),
        groups_path=os.environ.get("MONITOR_GROUPS_PATH", "/api/v1/groups/available"),
        username_field=os.environ.get("MONITOR_USERNAME_FIELD", "email"),
        site_name=os.environ.get("MONITOR_SITE_NAME", "AIAPIBANK"),
        proxy_url=os.environ.get("MONITOR_PROXY_URL"),
    )
    monitor = GroupMonitor(client, data_dir)

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    failures = 0
    while not STOP_REQUESTED:
        started = time.monotonic()
        try:
            monitor.poll()
            failures = 0
        except (ApiError, OSError) as exc:
            failures += 1
            LOG.error("Poll failed: %s", exc)
            if args.once:
                return 1

        if args.once:
            return 0

        normal_delay = max(0.0, interval - (time.monotonic() - started))
        retry_delay = min(60 * (2 ** (failures - 1)), interval) if failures else normal_delay
        deadline = time.monotonic() + retry_delay
        while not STOP_REQUESTED and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
