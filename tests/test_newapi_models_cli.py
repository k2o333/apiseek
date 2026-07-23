"""CLI and deployment contracts for New-API model inventory.

HTTP is replaced only at the requests transport boundary.  The tests invoke
the shipped ``newapi_monitor.main`` entry point so paging, hydration, coverage,
reconciliation, model refresh, checkpointing, and exit-code decisions remain
production behavior.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import pytest
import requests

import newapi_models as models
import newapi_monitor as mon


PASSWORD_SENTINEL = "password-MUST-NOT-LEAK"
SESSION_SENTINEL = "session-MUST-NOT-LEAK"
SEED_SECRET = "seed-secret-MUST-NOT-LEAK"


def write_env(path: Path, *, incremental: bool = False) -> Path:
    path.write_text(
        "\n".join(
            [
                f"MONITOR_SITE_ID={path.stem}",
                "MONITOR_BASE_URL=https://example.test",
                "MONITOR_USERNAME=operator@example.test",
                f"MONITOR_PASSWORD={PASSWORD_SENTINEL}",
                "REQUIRE_NEW_API_USER_HEADER=1",
                f"MONITOR_MODELS_INCREMENTAL_ENABLE={int(incremental)}",
                "CONNECT_TIMEOUT_SECONDS=1",
                "READ_TIMEOUT_SECONDS=1",
                "LOG_LEVEL=INFO",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def token(
    token_id: int,
    group: str,
    *,
    name: str = "operator-seed",
    secret: str = SEED_SECRET,
) -> tuple[dict[str, Any], str]:
    return (
        {
            "id": token_id,
            "user_id": 77,
            "name": name,
            "key": "sk-abcd********wxyz",
            "status": 1,
            "created_time": 1,
            "accessed_time": 1,
            "expired_time": -1,
            "remain_quota": 0,
            "used_quota": 0,
            "unlimited_quota": True,
            "model_limits_enabled": False,
            "model_limits": "",
            "allow_ips": "",
            "group": group,
            "cross_group_retry": False,
            "DeletedAt": None,
        },
        secret,
    )


@dataclass(frozen=True)
class RequestCall:
    method: str
    path: str
    params: Mapping[str, Any]
    json_body: Any
    headers: Mapping[str, str]
    has_session_cookie: bool


class FakeNewApiSession:
    """Small stateful New-API server exposed as a requests Session."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.proxies: dict[str, str] = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.calls: list[RequestCall] = []
        self.login_count = 0
        self.groups: dict[str, dict[str, Any]] = {
            "alpha": {"ratio": 1.0, "desc": "Alpha"},
        }
        seed, seed_secret = token(1, "alpha")
        self.tokens: list[dict[str, Any]] = [seed]
        self.secrets: dict[str, str] = {"1": seed_secret}
        self.models_by_secret: dict[str, list[str]] = {
            seed_secret: ["model-a", "model-b"],
        }
        self.models_status_by_secret: dict[str, int] = {}
        self.models_headers_by_secret: dict[str, Mapping[str, str]] = {}
        self.token_list_failures: list[tuple[int, Mapping[str, Any]]] = []
        self.create_business_message: str | None = None
        self.created_models_status: int | None = None
        self.next_id = 2

    @staticmethod
    def _response(
        status: int,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> requests.Response:
        response = requests.Response()
        response.status_code = status
        response.headers.update({"Content-Type": "application/json", **dict(headers or {})})
        response._content = json.dumps(payload).encode("utf-8")
        response.encoding = "utf-8"
        return response

    def _has_session(self) -> bool:
        return any(cookie.name == "session" and bool(cookie.value) for cookie in self.cookies)

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        method = method.upper()
        path = urlsplit(url).path
        params = dict(kwargs.get("params") or {})
        body = kwargs.get("json")
        headers = {**self.headers, **dict(kwargs.get("headers") or {})}
        self.calls.append(
            RequestCall(method, path, params, body, headers, self._has_session())
        )

        if method == "POST" and path == "/api/user/login":
            self.login_count += 1
            self.cookies.clear()
            self.cookies.set("session", SESSION_SENTINEL, domain="example.test", path="/")
            return self._response(200, {"success": True, "data": {"id": 77}})

        if method == "GET" and path == "/api/user/self/groups":
            return self._response(200, {"success": True, "data": self.groups})

        if method == "GET" and path == "/api/token/":
            if self.token_list_failures:
                status, payload = self.token_list_failures.pop(0)
                return self._response(status, payload)
            page = int(params.get("p", 1))
            size = int(params.get("size", 100))
            start = (page - 1) * size
            items = [dict(item) for item in self.tokens[start : start + size]]
            return self._response(
                200,
                {
                    "success": True,
                    "data": {
                        "items": items,
                        "page": page,
                        "page_size": size,
                        "total": len(self.tokens),
                    },
                },
            )

        secret_match = re.fullmatch(r"/api/token/([^/]+)/key", path)
        if method == "POST" and secret_match:
            token_id = secret_match.group(1)
            if token_id not in self.secrets:
                return self._response(200, {"success": False, "message": "secret unavailable"})
            return self._response(
                200,
                {"success": True, "data": {"key": self.secrets[token_id]}},
            )

        if method == "POST" and path == "/api/token/":
            if self.create_business_message is not None:
                return self._response(
                    200,
                    {"success": False, "message": self.create_business_message},
                )
            assert isinstance(body, Mapping)
            token_id = self.next_id
            self.next_id += 1
            created, created_secret = token(
                token_id,
                str(body["group"]),
                name=str(body["name"]),
                secret=f"created-secret-{token_id}-MUST-NOT-LEAK",
            )
            self.tokens.append(created)
            self.secrets[str(token_id)] = created_secret
            self.models_by_secret[created_secret] = [f"model-{body['group']}"]
            if self.created_models_status is not None:
                self.models_status_by_secret[created_secret] = self.created_models_status
            return self._response(200, {"success": True, "data": {"id": token_id}})

        if method == "PUT" and path == "/api/token/":
            assert isinstance(body, Mapping)
            token_id = str(body["id"])
            for current in self.tokens:
                if str(current.get("id")) != token_id:
                    continue
                if str(params.get("status_only", "")).lower() == "true":
                    current["status"] = body["status"]
                else:
                    current.update(body)
                return self._response(200, {"success": True, "data": current})
            return self._response(200, {"success": False, "message": "not found"})

        if method == "GET" and path == "/v1/models":
            authorization = headers.get("Authorization", "")
            secret = authorization.removeprefix("Bearer ")
            status = self.models_status_by_secret.get(secret, 200)
            if status != 200:
                return self._response(
                    status,
                    {"error": {"message": f"HTTP {status}"}},
                    headers=self.models_headers_by_secret.get(secret),
                )
            model_ids = self.models_by_secret.get(secret, [])
            return self._response(
                200,
                {"object": "list", "data": [{"id": model_id} for model_id in model_ids]},
            )

        raise AssertionError(f"unexpected request: {method} {path} params={params!r}")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("PUT", url, **kwargs)

    def close(self) -> None:
        return None

    def matching(self, method: str, path: str) -> list[RequestCall]:
        return [call for call in self.calls if call.method == method and call.path == path]


def invoke(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    server: FakeNewApiSession,
    flag: str | None = None,
    *,
    incremental: bool = False,
) -> int:
    env_file = root / "torchai.env"
    if not env_file.exists():
        write_env(env_file, incremental=incremental)
    monkeypatch.setattr(mon, "PROJECT_ROOT", root)
    monkeypatch.setattr(mon.requests, "Session", lambda: server)
    argv = ["--env-file", str(env_file)]
    if flag:
        argv.append(flag)
    return mon.main(argv)


def site_data(root: Path) -> Path:
    return root / "data" / "torchai"


class TestCliFlags:
    @pytest.mark.parametrize(
        "flag,attribute",
        [
            ("--models-preflight", "models_preflight"),
            ("--models-bootstrap", "models_bootstrap"),
            ("--models-refresh", "models_refresh"),
        ],
    )
    def test_each_models_flag_is_exposed(self, flag: str, attribute: str) -> None:
        args = mon.parse_args(["--env-file", "torchai.env", flag])
        assert getattr(args, attribute) is True

    @pytest.mark.parametrize(
        "flags",
        [
            ("--models-preflight", "--models-bootstrap"),
            ("--models-preflight", "--models-refresh"),
            ("--models-bootstrap", "--models-refresh"),
            ("--models-preflight", "--models-bootstrap", "--models-refresh"),
        ],
    )
    def test_models_flags_are_parser_mutually_exclusive(
        self, flags: tuple[str, ...], capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit):
            mon.parse_args(["--env-file", "torchai.env", *flags])
        error = capsys.readouterr().err
        assert "not allowed with argument" in error

    def test_newapi_cli_does_not_grow_a_once_flag(self) -> None:
        with pytest.raises(SystemExit):
            mon.parse_args(["--env-file", "torchai.env", "--once"])


class TestPreflightAndColdGates:
    def test_preflight_is_read_only_and_creates_no_models_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()

        code = invoke(monkeypatch, tmp_path, server, "--models-preflight")

        assert code == 0
        assert server.matching("POST", "/api/token/") == []
        assert server.matching("PUT", "/api/token/") == []
        assert len(server.matching("POST", "/api/token/1/key")) == 1
        assert len(server.matching("GET", "/v1/models")) == 1
        assert not (site_data(tmp_path) / "models_latest.json").exists()
        assert not (site_data(tmp_path) / "models_events.jsonl").exists()

        management = [
            call
            for call in server.calls
            if call.path.startswith("/api/token/")
        ]
        assert management
        assert all(call.has_session_cookie for call in management)
        assert all(call.headers.get("new-api-user") == "77" for call in management)
        model_call = server.matching("GET", "/v1/models")[0]
        assert model_call.headers["Authorization"] == f"Bearer {SEED_SECRET}"
        assert "new-api-user" not in model_call.headers

    def test_zero_seed_fails_without_remote_or_local_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        server.tokens = []
        server.secrets = {}

        code = invoke(monkeypatch, tmp_path, server, "--models-preflight")

        assert code != 0
        assert server.matching("POST", "/api/token/") == []
        assert server.matching("PUT", "/api/token/") == []
        assert server.matching("GET", "/v1/models") == []
        assert not (site_data(tmp_path) / "models_latest.json").exists()
        assert not (site_data(tmp_path) / "models_events.jsonl").exists()

    def test_refresh_without_bootstrap_is_a_zero_write_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()

        code = invoke(monkeypatch, tmp_path, server, "--models-refresh")

        assert code != 0
        assert server.matching("GET", "/api/token/") == []
        assert server.matching("POST", "/api/token/") == []
        assert server.matching("PUT", "/api/token/") == []
        assert server.matching("GET", "/v1/models") == []
        assert not (site_data(tmp_path) / "models_latest.json").exists()
        assert not (site_data(tmp_path) / "models_events.jsonl").exists()

    def test_refresh_rejects_malformed_bootstrap_marker_without_remote_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        data_dir = site_data(tmp_path)
        data_dir.mkdir(parents=True)
        marker = data_dir / "models_latest.json"
        marker.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "site_id": "torchai",
                    "bootstrap_completed_at": True,
                    "models_by_group": {},
                }
            ),
            encoding="utf-8",
        )
        before = marker.read_bytes()

        assert invoke(monkeypatch, tmp_path, server, "--models-refresh") != 0
        assert marker.read_bytes() == before
        assert not any(call.path.startswith("/api/token/") for call in server.calls)
        assert server.matching("GET", "/v1/models") == []

    @pytest.mark.parametrize("incremental", [False, True])
    def test_cold_default_groups_never_touches_tokens_or_models(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        incremental: bool,
    ) -> None:
        server = FakeNewApiSession()

        code = invoke(monkeypatch, tmp_path, server, incremental=incremental)

        assert code == 0
        assert len(server.matching("GET", "/api/user/self/groups")) == 1
        assert not any(call.path.startswith("/api/token/") for call in server.calls)
        assert server.matching("GET", "/v1/models") == []
        assert (site_data(tmp_path) / "groups_latest.json").exists()
        assert not (site_data(tmp_path) / "models_latest.json").exists()


class TestBootstrapAndTransportErrors:
    def test_bootstrap_preflights_then_regets_groups_and_second_run_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        server.groups["beta"] = {"ratio": 2.0, "desc": "Beta"}

        first_code = invoke(monkeypatch, tmp_path, server, "--models-bootstrap")

        assert first_code == 0
        create_calls = server.matching("POST", "/api/token/")
        assert len(create_calls) == 1
        assert create_calls[0].json_body["group"] == "beta"
        assert create_calls[0].json_body["name"] == models.managed_token_name("beta")
        group_positions = [
            index
            for index, call in enumerate(server.calls)
            if call.method == "GET" and call.path == "/api/user/self/groups"
        ]
        create_position = server.calls.index(create_calls[0])
        assert len(group_positions) >= 2
        assert create_position > group_positions[1]

        first_create_count = len(create_calls)
        second_code = invoke(monkeypatch, tmp_path, server, "--models-bootstrap")

        assert second_code == 0
        assert len(server.matching("POST", "/api/token/")) == first_create_count
        latest = json.loads(
            (site_data(tmp_path) / "models_latest.json").read_text(encoding="utf-8")
        )
        assert latest["bootstrap_completed_at"] is not None
        assert latest["models_by_group"]["alpha"]["models"] is not None
        assert latest["models_by_group"]["beta"]["models"] is not None

    def test_models_401_is_key_domain_and_never_triggers_session_relogin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        server.models_status_by_secret[SEED_SECRET] = 401

        code = invoke(monkeypatch, tmp_path, server, "--models-preflight")

        assert code != 0
        assert len(server.matching("GET", "/v1/models")) == 1
        assert server.login_count == 1

    def test_models_429_uses_retry_after_without_session_relogin(self, tmp_path: Path) -> None:
        server = FakeNewApiSession()
        server.models_status_by_secret[SEED_SECRET] = 429
        server.models_headers_by_secret[SEED_SECRET] = {"Retry-After": "120"}
        config = mon.MonitorConfig(
            site_id="torchai",
            base_url="https://example.test",
            username="operator",
            password="unused",
            require_new_api_user_header=True,
            project_root=tmp_path,
        )
        client = mon.ModelsApiClient(config, session=server)
        before = datetime.now(timezone.utc)

        with pytest.raises(mon.CollectError) as captured:
            client.list_models(SEED_SECRET)

        assert captured.value.kind == "rate_limit"
        assert captured.value.next_retry_at is not None
        retry_at = datetime.fromisoformat(
            captured.value.next_retry_at.replace("Z", "+00:00")
        )
        assert 100 <= (retry_at - before).total_seconds() <= 130
        assert server.login_count == 0

    def test_management_auth_recovers_once_and_reuses_the_same_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        server.token_list_failures.append(
            (401, {"success": False, "message": "please login"})
        )

        code = invoke(monkeypatch, tmp_path, server, "--models-preflight")

        assert code == 0
        assert server.login_count == 2
        assert len(server.matching("GET", "/api/token/")) == 2

    def test_second_management_auth_failure_does_not_start_a_third_login(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        server.token_list_failures.extend(
            [
                (401, {"success": False, "message": "please login"}),
                (401, {"success": False, "message": "please login"}),
            ]
        )

        code = invoke(monkeypatch, tmp_path, server, "--models-preflight")

        assert code != 0
        assert server.login_count == 2
        assert len(server.matching("GET", "/api/token/")) == 2

    def test_management_http_200_success_false_is_failure_and_secrets_stay_absent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        server = FakeNewApiSession()
        server.groups["beta"] = {"ratio": 1.0, "desc": "Beta"}
        server.create_business_message = f"token={SEED_SECRET} refused"
        caplog.set_level(logging.DEBUG)

        code = invoke(monkeypatch, tmp_path, server, "--models-bootstrap")

        assert code != 0
        assert len(server.matching("POST", "/api/token/")) == 1
        assert server.matching("PUT", "/api/token/") == []
        latest_path = site_data(tmp_path) / "models_latest.json"
        if latest_path.exists():
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            assert latest.get("bootstrap_completed_at") is None

        captured = capsys.readouterr()
        observable = captured.out + captured.err + caplog.text
        for path in site_data(tmp_path).glob("*.json*"):
            if path.name != "auth_state.json":
                observable += path.read_text(encoding="utf-8")
        assert SEED_SECRET not in observable
        assert SESSION_SENTINEL not in observable
        assert PASSWORD_SENTINEL not in observable

    def test_refresh_reloads_models_state_after_lock_wait(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        data_dir = site_data(tmp_path)
        initial = models.ModelsStore(data_dir, "torchai")
        initial.apply_success("alpha", ["old-model"], key_id=1, source="bootstrap")
        initial.update_full_meta(target=1, ok=1, failed=0, skipped=0, bootstrap=True)
        server.models_status_by_secret[SEED_SECRET] = 503

        def acquire_after_external_checkpoint(lock: mon.InstanceLock, **_kwargs: Any) -> float:
            external = models.ModelsStore(data_dir, "torchai")
            external.apply_success(
                "alpha", ["fresh-model"], key_id=99, source="external"
            )
            lock.acquire()
            return 0.0

        monkeypatch.setattr(mon, "acquire_models_lock", acquire_after_external_checkpoint)

        assert invoke(monkeypatch, tmp_path, server, "--models-refresh") != 0
        latest = json.loads((data_dir / "models_latest.json").read_text(encoding="utf-8"))
        entry = latest["models_by_group"]["alpha"]
        assert entry["models"] == ["fresh-model"]
        assert entry["key_id"] == 99
        assert entry["last_error"] is not None


class TestIncrementalAndFullResult:
    def test_t_new_targets_only_true_added_group_and_failure_is_nonfatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        write_env(tmp_path / "torchai.env", incremental=True)
        assert invoke(monkeypatch, tmp_path, server, "--models-bootstrap", incremental=True) == 0

        server.calls.clear()
        server.groups = {
            "alpha": {"ratio": 9.0, "desc": "modified, not added"},
            "beta": {"ratio": 1.0, "desc": "actually added"},
        }
        server.created_models_status = 503

        code = invoke(monkeypatch, tmp_path, server, incremental=True)

        assert code == 0
        creates = server.matching("POST", "/api/token/")
        assert [call.json_body["group"] for call in creates] == ["beta"]
        model_calls = server.matching("GET", "/v1/models")
        assert len(model_calls) == 1
        assert "created-secret-" in model_calls[0].headers["Authorization"]
        assert SEED_SECRET not in model_calls[0].headers["Authorization"]
        latest = json.loads(
            (site_data(tmp_path) / "models_latest.json").read_text(encoding="utf-8")
        )
        assert latest["models_by_group"]["beta"]["last_error"] is not None

    def test_t_new_requires_a_valid_previous_groups_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        write_env(tmp_path / "torchai.env", incremental=True)
        assert invoke(monkeypatch, tmp_path, server, "--models-bootstrap", incremental=True) == 0
        (site_data(tmp_path) / "groups_latest.json").unlink()
        server.groups["beta"] = {"ratio": 1.0, "desc": "cannot prove this is new"}
        server.calls.clear()

        assert invoke(monkeypatch, tmp_path, server, incremental=True) == 0
        assert not any(call.path.startswith("/api/token/") for call in server.calls)
        assert server.matching("GET", "/v1/models") == []

    def test_t_new_rejects_structurally_invalid_previous_groups_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        write_env(tmp_path / "torchai.env", incremental=True)
        assert invoke(monkeypatch, tmp_path, server, "--models-bootstrap", incremental=True) == 0
        groups_path = site_data(tmp_path) / "groups_latest.json"
        groups_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "site_id": "torchai",
                    "backend": "newapi",
                    "count": 0,
                    "content_hash": "sha256:invalid",
                    "groups": [],
                }
            ),
            encoding="utf-8",
        )
        server.groups["beta"] = {"ratio": 1.0, "desc": "not provably new"}
        server.calls.clear()

        assert invoke(monkeypatch, tmp_path, server, incremental=True) == 0
        assert not any(call.path.startswith("/api/token/") for call in server.calls)
        assert server.matching("GET", "/v1/models") == []

    def test_full_failure_exits_nonzero_and_writes_exact_four_field_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        server.groups["beta"] = {"ratio": 1.0, "desc": "Beta"}
        beta, beta_secret = token(2, "beta", name="operator-beta", secret="beta-secret")
        server.tokens.append(beta)
        server.secrets["2"] = beta_secret
        server.models_by_secret[beta_secret] = ["model-beta"]
        server.models_status_by_secret[beta_secret] = 503
        server.next_id = 3

        code = invoke(monkeypatch, tmp_path, server, "--models-bootstrap")

        assert code != 0
        latest = json.loads(
            (site_data(tmp_path) / "models_latest.json").read_text(encoding="utf-8")
        )
        assert latest["last_full_result"] == {
            "target": 2,
            "ok": 1,
            "failed": 1,
            "skipped": 0,
        }
        assert latest.get("bootstrap_completed_at") is None

    def test_full_deadline_skip_exits_nonzero_and_is_not_counted_as_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeNewApiSession()
        server.groups["beta"] = {"ratio": 1.0, "desc": "Beta"}
        beta, beta_secret = token(2, "beta", name="operator-beta", secret="beta-secret")
        server.tokens.append(beta)
        server.secrets["2"] = beta_secret
        server.models_by_secret[beta_secret] = ["model-beta"]
        data_dir = site_data(tmp_path)
        store = models.ModelsStore(data_dir, "torchai")
        store.update_full_meta(target=1, ok=1, failed=0, skipped=0, bootstrap=True)
        monkeypatch.setattr(mon, "MODELS_DEADLINE_SECONDS", 0.0, raising=False)
        monkeypatch.setattr(mon, "DEFAULT_MODELS_DEADLINE_SECONDS", 0.0, raising=False)

        code = invoke(monkeypatch, tmp_path, server, "--models-refresh")

        assert code != 0
        latest = json.loads((data_dir / "models_latest.json").read_text(encoding="utf-8"))
        assert latest["last_full_result"] == {
            "target": 2,
            "ok": 0,
            "failed": 0,
            "skipped": 2,
        }


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class TestLockAndDeploymentContracts:
    def test_lock_wait_is_bounded_without_real_sleep(self, tmp_path: Path) -> None:
        holder = mon.InstanceLock(tmp_path / "monitor.lock")
        waiter = mon.InstanceLock(tmp_path / "monitor.lock")
        holder.acquire()
        clock = FakeClock()
        try:
            with pytest.raises(RuntimeError):
                waiter.acquire_wait(
                    0.2,
                    poll_interval=0.05,
                    time_fn=clock.monotonic,
                    sleep_fn=clock.sleep,
                )
            assert 0.2 <= clock.now <= 0.25
            assert 1 <= len(clock.sleeps) <= 5
        finally:
            holder.release()
            waiter.release()

        waiter.acquire()
        waiter.release()

    def test_daily_units_and_default_groups_unit_have_the_required_commands(self) -> None:
        root = Path(mon.__file__).resolve().parent
        daily_timer = (root / "newapi-models-daily@.timer").read_text(encoding="utf-8")
        daily_service = (root / "newapi-models-daily@.service").read_text(encoding="utf-8")
        groups_service = (root / "newapi-monitor-once@.service").read_text(encoding="utf-8")

        assert "OnCalendar=*-*-* 00:00:00 Asia/Shanghai" in daily_timer
        assert "RandomizedDelaySec=300" in daily_timer
        assert "TimeoutStartSec=600" in daily_service
        assert "--models-refresh" in daily_service
        assert "--models-" not in groups_service
        assert "--once" not in groups_service

    def test_installer_keeps_daily_timer_opt_in(self) -> None:
        installer = (
            Path(mon.__file__).resolve().parent / "install_newapi_service.sh"
        ).read_text(encoding="utf-8")

        assert "ENABLE_MODELS=0" in installer
        assert "--enable-models" in installer
        guarded_enable = re.compile(
            r'if \[\[ "\$ENABLE_MODELS" -eq 1 \]\]; then\s+'
            r'systemctl enable --now "newapi-models-daily@\$\{site\}\.timer"'
        )
        assert guarded_enable.search(installer)
        assert "successful models bootstrap" in installer
