"""CLI + orchestration tests for get-models (drives shipped sub2api_monitor entry)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import sub2api_monitor as mon
import sub2api_models as models


def write_env(path: Path, mapping: dict[str, str], mode: int = 0o600) -> Path:
    lines = [f"{k}={v}" for k, v in mapping.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def base_env(tmp: Path, site_id: str = "pinaic", **overrides: str) -> dict[str, str]:
    data = tmp / site_id
    data.mkdir(parents=True, exist_ok=True)
    cfg = {
        "MONITOR_SITE_ID": site_id,
        "MONITOR_SITE_NAME": "PinAI",
        "MONITOR_BASE_URL": "https://example.test",
        "MONITOR_USERNAME": "user@example.test",
        "MONITOR_PASSWORD": "secret",
        "MONITOR_LOGIN_PATH": "/api/v1/auth/login",
        "MONITOR_REFRESH_PATH": "/api/v1/auth/refresh",
        "MONITOR_GROUPS_PATH": "/api/v1/groups/available",
        "MONITOR_USERNAME_FIELD": "email",
        "POLL_INTERVAL_SECONDS": "300",
        "CONNECT_TIMEOUT_SECONDS": "10",
        "READ_TIMEOUT_SECONDS": "30",
        "REFRESH_MARGIN_SECONDS": "600",
        "REQUEST_JITTER_SECONDS": "0",
        "DATA_DIR": str(data),
        "TOKEN_STATE_FILE": str(data / "token.json"),
        "LOG_LEVEL": "WARNING",
    }
    cfg.update(overrides)
    return cfg


class FakeHTTP:
    """Minimal HTTP stub for keys/groups/models via AuthGroupClient session."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.proxies: dict[str, str] = {}
        self.posts: list[tuple[str, Any]] = []
        self.puts: list[tuple[str, Any]] = []
        self.gets: list[str] = []
        self.keys: list[dict[str, Any]] = []
        self.groups: list[dict[str, Any]] = [
            {"id": 1, "name": "g1", "rate_multiplier": 1, "status": "active"},
            {"id": 2, "name": "g2", "rate_multiplier": 1, "status": "active"},
        ]
        self.models_by_secret: dict[str, list[str]] = {
            "sk-g1": ["m1", "m2"],
            "sk-g2": ["m3"],
        }
        self.create_count = 0
        self.next_key_id = 100
        self.login_calls = 0
        self.models_401_secrets: set[str] = set()

    def _json_resp(self, status: int, body: Any) -> MagicMock:
        r = MagicMock()
        r.status_code = status
        r.headers = {"Content-Type": "application/json"}
        r.json.return_value = body
        r.text = json.dumps(body)
        return r

    def request(self, method: str, url: str, **kwargs: Any) -> MagicMock:
        method = method.upper()
        path = url.replace("https://example.test", "")
        if method == "POST" and path.endswith("/api/v1/auth/login"):
            self.login_calls += 1
            return self._json_resp(
                200,
                {"data": {"access_token": "jwt-access", "refresh_token": "jwt-refresh"}},
            )
        if method == "POST" and path.endswith("/api/v1/auth/refresh"):
            return self._json_resp(200, {"data": {"access_token": "jwt-access-2"}})
        if method == "GET" and "/api/v1/groups" in path:
            self.gets.append(path)
            return self._json_resp(200, {"data": self.groups})
        if method == "GET" and "/api/v1/keys" in path:
            self.gets.append(path)
            return self._json_resp(
                200,
                {
                    "data": {"items": list(self.keys), "total": len(self.keys)},
                    "page": 1,
                    "page_size": 100,
                    "has_more": False,
                },
            )
        if method == "POST" and path.rstrip("/").endswith("/api/v1/keys"):
            body = kwargs.get("json") or {}
            self.posts.append((path, body))
            self.create_count += 1
            kid = self.next_key_id
            self.next_key_id += 1
            # Derive secret from name for deterministic models
            name = body.get("name", "")
            gid = name.split(":")[-1] if ":" in name else str(kid)
            secret = f"sk-g{gid}"
            rec = {"id": kid, "name": name, "key": secret, "group_id": None}
            self.keys.append(rec)
            self.models_by_secret[secret] = self.models_by_secret.get(secret) or [f"model-{gid}"]
            return self._json_resp(201, {"data": rec})
        if method == "PUT" and "/api/v1/keys/" in path:
            body = kwargs.get("json") or {}
            self.puts.append((path, body))
            kid = int(path.rstrip("/").split("/")[-1])
            for k in self.keys:
                if k["id"] == kid:
                    k["group_id"] = body.get("group_id")
                    k["name"] = body.get("name", k.get("name"))
                    return self._json_resp(200, {"data": k})
            return self._json_resp(404, {"error": "not found"})
        if method == "GET" and path.endswith("/v1/models"):
            self.gets.append(path)
            auth = (kwargs.get("headers") or {}).get("Authorization") or ""
            secret = auth.replace("Bearer ", "").strip()
            if secret in self.models_401_secrets:
                return self._json_resp(401, {"error": "invalid key"})
            models_list = self.models_by_secret.get(secret, [])
            return self._json_resp(200, {"data": [{"id": m} for m in models_list]})
        return self._json_resp(404, {"error": f"no route {method} {path}"})

    def get(self, url: str, **kwargs: Any) -> MagicMock:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> MagicMock:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> MagicMock:
        return self.request("PUT", url, **kwargs)

    def close(self) -> None:
        pass


def patch_session(fake: FakeHTTP):
    """Patch requests.Session so AuthGroupClient uses FakeHTTP."""

    def factory() -> FakeHTTP:
        return fake

    return patch("sub2api_monitor.requests.Session", side_effect=factory)


class ConfigModelsDefaultsTests(unittest.TestCase):
    def test_incremental_default_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = write_env(root / "pinaic.env", base_env(root))
            cfg = mon.load_config(env, environ={})
            self.assertFalse(cfg.models_incremental_enable)
            self.assertEqual(cfg.keys_path, mon.DEFAULT_KEYS_PATH)
            self.assertEqual(cfg.models_path, mon.DEFAULT_MODELS_PATH)

    def test_incremental_enable_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            e = base_env(root, MONITOR_MODELS_INCREMENTAL_ENABLE="1")
            env = write_env(root / "pinaic.env", e)
            cfg = mon.load_config(env, environ={})
            self.assertTrue(cfg.models_incremental_enable)


class CliArgsTests(unittest.TestCase):
    def test_models_flags_parse(self) -> None:
        args = mon.parse_args(
            ["--env-file", "sites/x.env", "--models-preflight"]
        )
        self.assertTrue(args.models_preflight)
        self.assertFalse(args.models_bootstrap)


class PreflightCliTests(unittest.TestCase):
    def test_preflight_pass_no_create(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = write_env(root / "pinaic.env", base_env(root))
            fake = FakeHTTP()
            # Pre-seed usable keys so preflight can pass without create
            fake.keys = [
                {"id": 1, "name": "k1", "key": "sk-g1", "group_id": 1},
                {"id": 2, "name": "k2", "key": "sk-g2", "group_id": 2},
            ]
            with patch_session(fake):
                code = mon.main(["--env-file", str(env_path), "--models-preflight"])
            self.assertEqual(code, 0)
            self.assertEqual(fake.create_count, 0)
            self.assertEqual(fake.posts, [])

    def test_preflight_fail_no_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = write_env(root / "pinaic.env", base_env(root))
            fake = FakeHTTP()
            fake.keys = [{"id": 1, "name": "k1", "group_id": 1}]  # no secret
            with patch_session(fake):
                code = mon.main(["--env-file", str(env_path), "--models-preflight"])
            self.assertNotEqual(code, 0)
            self.assertEqual(fake.create_count, 0)


class OnceDefaultNoRemoteWriteTests(unittest.TestCase):
    def test_once_without_bootstrap_zero_create_zero_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = write_env(root / "pinaic.env", base_env(root))
            fake = FakeHTTP()
            # No keys at all — if --once wrongly ensures, would create
            fake.keys = []
            with patch_session(fake):
                code1 = mon.main(["--env-file", str(env_path), "--once"])
                code2 = mon.main(["--env-file", str(env_path), "--once"])
            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertEqual(fake.create_count, 0)
            models_gets = [g for g in fake.gets if g.endswith("/v1/models")]
            self.assertEqual(models_gets, [])
            # No models_latest created by cold once
            data = root / "pinaic"
            self.assertFalse((data / "models_latest.json").exists())


class BootstrapCliTests(unittest.TestCase):
    def test_bootstrap_twice_second_created_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = write_env(root / "pinaic.env", base_env(root))
            fake = FakeHTTP()
            # Seed one secret-bearing key for preflight secret_readable; bootstrap
            # will create managed keys for missing coverage.
            fake.keys = [
                {"id": 50, "name": "seed", "key": "sk-seed", "group_id": 1},
            ]
            fake.models_by_secret["sk-seed"] = ["seed-m"]
            fake.models_by_secret["sk-g1"] = ["m1"]
            fake.models_by_secret["sk-g2"] = ["m2"]

            with patch_session(fake):
                code1 = mon.main(["--env-file", str(env_path), "--models-bootstrap"])
            self.assertEqual(code1, 0, msg="first bootstrap should succeed")
            created_first = fake.create_count
            self.assertGreaterEqual(created_first, 1)

            data = root / "pinaic"
            latest = json.loads((data / "models_latest.json").read_text(encoding="utf-8"))
            self.assertIsNotNone(latest.get("bootstrap_completed_at"))
            self.assertIn("2", latest["models_by_group"])

            with patch_session(fake):
                code2 = mon.main(["--env-file", str(env_path), "--models-bootstrap"])
            self.assertEqual(code2, 0)
            # Second run: no extra creates
            self.assertEqual(fake.create_count, created_first)

            # Managed key count ≤1 per group
            managed = [
                k
                for k in fake.keys
                if str(k.get("name") or "").startswith("sub2api-monitor:g:")
            ]
            by_name: dict[str, int] = {}
            for k in managed:
                by_name[k["name"]] = by_name.get(k["name"], 0) + 1
            for name, n in by_name.items():
                self.assertEqual(n, 1, msg=f"duplicate managed key {name}")

    def test_bootstrap_partial_preserves_success_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = write_env(root / "pinaic.env", base_env(root))
            data = root / "pinaic"
            # Seed prior success for group 1
            store = models.ModelsStore(data, "pinaic")
            store.apply_success(1, ["old-a", "old-b"], key_id=9, source="bootstrap")

            fake = FakeHTTP()
            # Put working key first so preflight models probe succeeds; g1 key still 401 at refresh.
            fake.keys = [
                {"id": 2, "name": "k2", "key": "sk-g2", "group_id": 2},
                {"id": 1, "name": "k1", "key": "sk-g1", "group_id": 1},
            ]
            fake.models_401_secrets.add("sk-g1")
            fake.models_by_secret["sk-g2"] = ["new-g2"]

            with patch_session(fake):
                code = mon.main(["--env-file", str(env_path), "--models-bootstrap"])
            # Partial fail → non-zero
            self.assertNotEqual(code, 0)

            latest = json.loads((data / "models_latest.json").read_text(encoding="utf-8"))
            g1 = latest["models_by_group"]["1"]
            self.assertEqual(g1["models"], ["old-a", "old-b"])
            self.assertIsNotNone(g1["last_error"])
            self.assertIsNone(latest.get("bootstrap_completed_at"))
            self.assertIsNotNone(latest.get("last_full_attempt_at"))
            self.assertIsNone(latest.get("last_full_success_at"))


class ModelsAuthDomainTests(unittest.TestCase):
    def test_models_401_does_not_jwt_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = write_env(root / "pinaic.env", base_env(root))
            fake = FakeHTTP()
            fake.keys = [
                {"id": 1, "name": "k1", "key": "sk-g1", "group_id": 1},
                {"id": 2, "name": "k2", "key": "sk-g2", "group_id": 2},
            ]
            fake.models_401_secrets = {"sk-g1", "sk-g2"}
            with patch_session(fake):
                # bootstrap will login once for JWT then fail models
                mon.main(["--env-file", str(env_path), "--models-bootstrap"])
            # login should not be called again due to models 401
            # At most a few JWT logins for ensure_token/preflight, not per-model-key storm
            self.assertLessEqual(fake.login_calls, 3)


class LockWaitTests(unittest.TestCase):
    def test_acquire_wait_gets_lock_after_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitor.lock"
            holder = mon.InstanceLock(path)
            waiter = mon.InstanceLock(path)
            holder.acquire()
            result: list[Any] = []

            def wait_and_hold() -> None:
                try:
                    waited = waiter.acquire_wait(2.0, poll_interval=0.05)
                    result.append(waited)
                    waiter.release()
                except Exception as exc:
                    result.append(exc)

            t = threading.Thread(target=wait_and_hold)
            t.start()
            time.sleep(0.2)
            holder.release()
            t.join(timeout=3)
            self.assertEqual(len(result), 1)
            self.assertIsInstance(result[0], float)
            self.assertGreaterEqual(result[0], 0.0)


class StaticTriggerGuardsTests(unittest.TestCase):
    def test_source_has_incremental_default_zero(self) -> None:
        src = Path(mon.__file__).read_text(encoding="utf-8")
        self.assertIn('get("MONITOR_MODELS_INCREMENTAL_ENABLE", "0")', src)
        self.assertIn("models_incremental_enable", src)
        # No missing-snapshot full refresh in poll path
        self.assertNotIn("missing snapshot", src.lower())

    def test_daily_units_exist(self) -> None:
        root = Path(mon.__file__).resolve().parent
        timer = (root / "sub2api-models-daily@.timer").read_text(encoding="utf-8")
        service = (root / "sub2api-models-daily@.service").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* 00:00:00 Asia/Shanghai", timer)
        self.assertIn("RandomizedDelaySec=300", timer)
        self.assertIn("TimeoutStartSec=600", service)
        self.assertIn("--models-refresh", service)


if __name__ == "__main__":
    unittest.main()
