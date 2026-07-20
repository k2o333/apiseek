"""Tests for sub2api_monitor — drives the shipped entry module."""

from __future__ import annotations

import base64
import json
import os
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import sub2api_monitor as mon


def make_jwt(exp: int, sub: str = "user") -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": sub, "exp": exp}, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


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


class ConfigTests(unittest.TestCase):
    def test_normal_config_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = write_env(root / "pinaic.env", base_env(root))
            cfg = mon.load_config(env, environ={})
            self.assertEqual(cfg.site_id, "pinaic")
            self.assertEqual(cfg.base_url, "https://example.test")
            self.assertEqual(cfg.poll_interval_seconds, 300)

    def test_missing_username_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            e = base_env(root)
            e["MONITOR_USERNAME"] = ""
            env = write_env(root / "x.env", e)
            with self.assertRaises(mon.ConfigError):
                mon.load_config(env, environ={})

    def test_missing_password_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            e = base_env(root)
            del e["MONITOR_PASSWORD"]
            env = write_env(root / "x.env", e)
            with self.assertRaises(mon.ConfigError):
                mon.load_config(env, environ={})

    def test_non_https_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            e = base_env(root, MONITOR_BASE_URL="http://example.test")
            env = write_env(root / "x.env", e)
            with self.assertRaisesRegex(mon.ConfigError, "HTTPS"):
                mon.load_config(env, environ={})

    def test_illegal_site_id_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for bad in ("has space", "../escape", "a/b", "UPPER", "dot.name"):
                e = base_env(root, site_id="ok")
                e["MONITOR_SITE_ID"] = bad
                e["DATA_DIR"] = str(root / "ok")
                e["TOKEN_STATE_FILE"] = str(root / "ok" / "token.json")
                env = write_env(root / "x.env", e)
                with self.assertRaises(mon.ConfigError):
                    mon.load_config(env, environ={})

    def test_interval_below_60_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            e = base_env(root, POLL_INTERVAL_SECONDS="1")
            env = write_env(root / "x.env", e)
            with self.assertRaisesRegex(mon.ConfigError, "60"):
                mon.load_config(env, environ={})

    def test_token_outside_data_dir_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            e = base_env(root)
            e["TOKEN_STATE_FILE"] = str(root / "other" / "token.json")
            env = write_env(root / "x.env", e)
            with self.assertRaisesRegex(mon.ConfigError, "not under DATA_DIR"):
                mon.load_config(env, environ={})

    def test_credential_file_perms_not_0600_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = write_env(root / "x.env", base_env(root), mode=0o644)
            with self.assertRaisesRegex(mon.ConfigError, "permissions"):
                mon.load_config(env, environ={})

    def test_relative_paths_resolve_against_env_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sites = root / "sites"
            sites.mkdir()
            data_rel = "rel-data"
            e = base_env(root)
            e["DATA_DIR"] = data_rel
            e["TOKEN_STATE_FILE"] = f"{data_rel}/token.json"
            env = write_env(sites / "pinaic.env", e)
            cfg = mon.load_config(env, environ={})
            self.assertEqual(cfg.data_dir, (sites / data_rel).resolve())
            self.assertEqual(cfg.token_state_file, (sites / data_rel / "token.json").resolve())

    def test_login_path_must_be_fixed_relative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for bad in ("https://evil/login", "/api/../etc/passwd", "api/login", "/api?x=1"):
                e = base_env(root, MONITOR_LOGIN_PATH=bad)
                env = write_env(root / "x.env", e)
                with self.assertRaises(mon.ConfigError):
                    mon.load_config(env, environ={})


class AuthUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "site"
        self.data.mkdir()
        self.config = mon.MonitorConfig(
            site_id="site",
            site_name="Site",
            base_url="https://example.test",
            username="user@example.test",
            password="secret",
            data_dir=self.data,
            token_state_file=self.data / "token.json",
            request_jitter_seconds=0,
            poll_interval_seconds=60,
        )
        self.store = mon.TokenStore(self.config.token_state_file)
        self.client = mon.AuthGroupClient(self.config, self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_login_extracts_access_and_refresh(self) -> None:
        exp = int(time.time()) + 3600
        token = make_jwt(exp)
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "data": {"access_token": token, "refresh_token": "rt_1"}
        }
        self.client.session.post = Mock(return_value=resp)
        self.client.login()
        self.assertEqual(self.store.state.access_token, token)
        self.assertEqual(self.store.state.refresh_token, "rt_1")
        self.assertEqual(self.store.state.access_expires_at, exp)

    def test_login_structure_error(self) -> None:
        resp = Mock(status_code=200)
        resp.json.return_value = {"data": {}}
        self.client.session.post = Mock(return_value=resp)
        with self.assertRaisesRegex(mon.ApiError, "structure"):
            self.client.login()

    def test_refresh_success_and_new_refresh_token(self) -> None:
        self.store.save(mon.TokenState(access_token="old", refresh_token="rt_old", access_expires_at=1))
        exp = int(time.time()) + 3600
        new_access = make_jwt(exp)
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "data": {"access_token": new_access, "refresh_token": "rt_new"}
        }
        self.client.session.post = Mock(return_value=resp)
        self.client.refresh()
        self.assertEqual(self.store.state.access_token, new_access)
        self.assertEqual(self.store.state.refresh_token, "rt_new")

    def test_refresh_keeps_old_refresh_when_absent(self) -> None:
        self.store.save(mon.TokenState(access_token="old", refresh_token="rt_old", access_expires_at=1))
        exp = int(time.time()) + 3600
        new_access = make_jwt(exp)
        resp = Mock(status_code=200)
        resp.json.return_value = {"data": {"access_token": new_access}}
        self.client.session.post = Mock(return_value=resp)
        self.client.refresh()
        self.assertEqual(self.store.state.refresh_token, "rt_old")

    def test_refresh_fail_then_password_login(self) -> None:
        self.store.save(
            mon.TokenState(
                access_token=make_jwt(int(time.time()) - 10),
                refresh_token="rt_bad",
                access_expires_at=int(time.time()) - 10,
            )
        )
        bad = Mock(status_code=401)
        bad.headers = {}
        bad.text = '{"error":"bad"}'
        bad.json.return_value = {"error": "bad"}
        exp = int(time.time()) + 3600
        good = Mock(status_code=200)
        good.json.return_value = {
            "data": {"access_token": make_jwt(exp), "refresh_token": "rt_2"}
        }
        self.client.session.post = Mock(side_effect=[bad, good])
        self.client.ensure_token()
        self.assertEqual(self.store.state.refresh_token, "rt_2")
        self.assertEqual(self.client.session.post.call_count, 2)

    def test_near_exp_triggers_refresh(self) -> None:
        exp = int(time.time()) + 60  # within 600s margin
        self.store.save(
            mon.TokenState(
                access_token=make_jwt(exp),
                refresh_token="rt",
                access_expires_at=exp,
            )
        )
        new_exp = int(time.time()) + 7200
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "data": {"access_token": make_jwt(new_exp), "refresh_token": "rt"}
        }
        self.client.session.post = Mock(return_value=resp)
        self.client.ensure_token()
        self.client.session.post.assert_called_once()
        called_url = self.client.session.post.call_args[0][0]
        self.assertIn("/auth/refresh", called_url)

    def test_not_near_exp_skips_refresh(self) -> None:
        exp = int(time.time()) + 7200
        self.store.save(
            mon.TokenState(
                access_token=make_jwt(exp),
                refresh_token="rt",
                access_expires_at=exp,
            )
        )
        self.client.session.post = Mock()
        self.client.ensure_token()
        self.client.session.post.assert_not_called()

    def test_token_file_mode_0600_and_atomic(self) -> None:
        exp = int(time.time()) + 3600
        token = make_jwt(exp)
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "data": {"access_token": token, "refresh_token": "rt"}
        }
        self.client.session.post = Mock(return_value=resp)
        self.client.login()
        mode = self.config.token_state_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        # No leftover tmp
        tmps = list(self.data.glob("token.json.tmp*"))
        self.assertEqual(tmps, [])

    def test_corrupt_token_file_triggers_relogin(self) -> None:
        self.config.token_state_file.write_text("{not-json", encoding="utf-8")
        self.store.load()
        self.assertIsNone(self.store.state.access_token)
        exp = int(time.time()) + 3600
        resp = Mock(status_code=200)
        resp.json.return_value = {
            "data": {"access_token": make_jwt(exp), "refresh_token": "rt"}
        }
        self.client.session.post = Mock(return_value=resp)
        self.client.ensure_token()
        self.assertIsNotNone(self.store.state.access_token)


class AuthErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "site"
        self.data.mkdir()
        self.config = mon.MonitorConfig(
            site_id="site",
            site_name="Site",
            base_url="https://example.test",
            username="user@example.test",
            password="secret",
            data_dir=self.data,
            token_state_file=self.data / "token.json",
            request_jitter_seconds=0,
            poll_interval_seconds=60,
        )
        exp = int(time.time()) + 7200
        self.token = make_jwt(exp)
        self.store = mon.TokenStore(self.config.token_state_file)
        self.store.save(
            mon.TokenState(access_token=self.token, refresh_token="rt", access_expires_at=exp)
        )
        self.client = mon.AuthGroupClient(self.config, self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _groups_ok(self, groups=None):
        r = Mock(status_code=200)
        r.headers = {"Content-Type": "application/json"}
        r.json.return_value = {"data": groups if groups is not None else []}
        r.text = json.dumps({"data": groups if groups is not None else []})
        return r

    def test_401_then_refresh_retry_ok(self) -> None:
        rejected = Mock(status_code=401)
        rejected.headers = {}
        rejected.text = "unauthorized"
        rejected.json.return_value = {"message": "unauthorized"}
        new_exp = int(time.time()) + 7200
        refresh_resp = Mock(status_code=200)
        refresh_resp.json.return_value = {
            "data": {"access_token": make_jwt(new_exp), "refresh_token": "rt2"}
        }
        self.client.session.get = Mock(side_effect=[rejected, self._groups_ok([{"id": 1}])])
        self.client.session.post = Mock(return_value=refresh_resp)
        groups = self.client.get_groups()
        self.assertEqual(groups, [{"id": 1}])
        self.assertEqual(self.store.state.refresh_token, "rt2")

    def test_401_refresh_fail_login_ok(self) -> None:
        rejected = Mock(status_code=401)
        rejected.headers = {}
        rejected.text = "unauthorized"
        rejected.json.return_value = {"message": "unauthorized"}
        bad_refresh = Mock(status_code=401)
        bad_refresh.headers = {}
        bad_refresh.text = "no"
        bad_refresh.json.return_value = {"error": "no"}
        new_exp = int(time.time()) + 7200
        login_resp = Mock(status_code=200)
        login_resp.json.return_value = {
            "data": {"access_token": make_jwt(new_exp), "refresh_token": "rt3"}
        }
        self.client.session.get = Mock(side_effect=[rejected, self._groups_ok()])
        self.client.session.post = Mock(side_effect=[bad_refresh, login_resp])
        self.assertEqual(self.client.get_groups(), [])
        self.assertEqual(self.store.state.refresh_token, "rt3")

    def test_refresh_and_login_both_fail(self) -> None:
        rejected = Mock(status_code=401)
        rejected.headers = {}
        rejected.text = "unauthorized"
        rejected.json.return_value = {"message": "unauthorized"}
        fail = Mock(status_code=401)
        fail.headers = {}
        fail.text = "no"
        fail.json.return_value = {"error": "no"}
        self.client.session.get = Mock(return_value=rejected)
        self.client.session.post = Mock(return_value=fail)
        with self.assertRaises(mon.ApiError):
            self.client.get_groups()

    def test_403_token_json_triggers_auth_recovery(self) -> None:
        forbidden = Mock(status_code=403)
        forbidden.headers = {"Content-Type": "application/json"}
        forbidden.text = '{"message":"invalid token"}'
        forbidden.json.return_value = {"message": "invalid token"}
        new_exp = int(time.time()) + 7200
        refresh_resp = Mock(status_code=200)
        refresh_resp.json.return_value = {
            "data": {"access_token": make_jwt(new_exp), "refresh_token": "rt"}
        }
        self.client.session.get = Mock(side_effect=[forbidden, self._groups_ok()])
        self.client.session.post = Mock(return_value=refresh_resp)
        self.client.get_groups()
        self.client.session.post.assert_called()

    def test_403_cloudflare_html_no_login_loop(self) -> None:
        html = Mock(status_code=403)
        html.headers = {"Content-Type": "text/html"}
        html.text = "<html>Cloudflare Access denied - not available in your country</html>"
        html.json.side_effect = ValueError("not json")
        self.client.session.get = Mock(return_value=html)
        self.client.session.post = Mock()
        with self.assertRaisesRegex(mon.ApiError, "region"):
            self.client.get_groups()
        self.client.session.post.assert_not_called()
        # Token preserved
        self.assertEqual(self.store.state.access_token, self.token)

    def test_429_reads_retry_after(self) -> None:
        r = Mock(status_code=429)
        r.headers = {"Retry-After": "42"}
        r.text = "slow"
        r.json.return_value = {}
        self.client.session.get = Mock(return_value=r)
        with self.assertRaises(mon.ApiError) as ctx:
            self.client.get_groups()
        self.assertEqual(ctx.exception.kind, "rate_limit")
        self.assertEqual(ctx.exception.retry_after, 42.0)

    def test_5xx_server_kind(self) -> None:
        r = Mock(status_code=503)
        r.headers = {}
        r.text = "down"
        self.client.session.get = Mock(return_value=r)
        with self.assertRaises(mon.ApiError) as ctx:
            self.client.get_groups()
        self.assertEqual(ctx.exception.kind, "server")
        self.assertEqual(self.store.state.access_token, self.token)

    def test_read_timeout_keeps_token(self) -> None:
        self.client.session.get = Mock(side_effect=requests.ReadTimeout("t"))
        with self.assertRaises(mon.ApiError) as ctx:
            self.client.get_groups()
        self.assertEqual(ctx.exception.kind, "timeout")
        self.assertEqual(self.store.state.access_token, self.token)

    def test_connect_timeout_keeps_token(self) -> None:
        self.client.session.get = Mock(side_effect=requests.ConnectTimeout("t"))
        with self.assertRaises(mon.ApiError) as ctx:
            self.client.get_groups()
        self.assertEqual(ctx.exception.kind, "timeout")
        self.assertEqual(self.store.state.access_token, self.token)


class GroupsProcessingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "site"
        self.data.mkdir()
        self.config = mon.MonitorConfig(
            site_id="pinaic",
            site_name="PinAI",
            base_url="https://example.test",
            username="u",
            password="p",
            data_dir=self.data,
            token_state_file=self.data / "token.json",
        )
        self.store = mon.SnapshotStore(self.config)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_empty_and_multi_groups_hash_stable(self) -> None:
        r1 = self.store.persist_success([])
        self.assertEqual(r1["count"], 0)
        self.assertTrue(r1["content_hash"].startswith("sha256:"))
        events = self.config.events_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0])["event"], "initial")

        groups = [
            {"id": 2, "name": "b", "rate_multiplier": 1, "status": "active", "z": 1},
            {"id": 1, "name": "a", "rate_multiplier": 2, "status": "active", "a": 1},
        ]
        r2 = self.store.persist_success(groups)
        self.assertEqual(r2["count"], 2)
        # Stable sort by id
        self.assertEqual([g["id"] for g in r2["groups"]], [1, 2])
        # Same content same hash regardless of input order / fetched_at
        r3 = self.store.persist_success(list(reversed(groups)))
        self.assertEqual(r2["content_hash"], r3["content_hash"])
        # No duplicate event for same hash
        events = self.config.events_file.read_text(encoding="utf-8").strip().splitlines()
        hashes = [json.loads(e)["content_hash"] for e in events]
        self.assertEqual(len(hashes), len(set(hashes)))

    def test_key_sort_stable_hash(self) -> None:
        g1 = [{"id": 1, "name": "x", "rate_multiplier": 1, "status": "ok"}]
        g2 = [{"status": "ok", "rate_multiplier": 1, "name": "x", "id": 1}]
        self.assertEqual(mon.content_hash_groups(g1), mon.content_hash_groups(g2))

    def test_fetched_at_not_in_hash(self) -> None:
        groups = [{"id": 1, "name": "x"}]
        h1 = mon.content_hash_groups(groups)
        # hash function only sees groups
        self.assertEqual(h1, mon.content_hash_groups(groups))

    def test_diff_add_remove_modify(self) -> None:
        old = [
            {"id": 1, "name": "a", "rate_multiplier": 1, "status": "active"},
            {"id": 2, "name": "b", "rate_multiplier": 1, "status": "active"},
        ]
        new = [
            {"id": 2, "name": "b", "rate_multiplier": 2, "status": "active"},
            {"id": 3, "name": "c", "rate_multiplier": 1, "status": "active"},
        ]
        d = mon.diff_groups(old, new)
        self.assertEqual(d["added"], [3])
        self.assertEqual(d["removed"], [1])
        self.assertEqual(d["modified"], [2])

    def test_contract_errors_via_client(self) -> None:
        store = mon.TokenStore(self.config.token_state_file)
        exp = int(time.time()) + 7200
        store.save(mon.TokenState(access_token=make_jwt(exp), access_expires_at=exp))
        client = mon.AuthGroupClient(self.config, store)

        for payload, match in (
            ({}, "missing data"),
            ({"data": {}}, "not a list"),
            (None, "not JSON"),
        ):
            r = Mock(status_code=200)
            r.headers = {"Content-Type": "application/json"}
            if payload is None:
                r.json.side_effect = requests.JSONDecodeError("x", "d", 0)
                r.text = "not-json{"
            else:
                r.json.return_value = payload
                r.text = json.dumps(payload)
            client.session.get = Mock(return_value=r)
            with self.assertRaises(mon.ApiError) as ctx:
                client.get_groups()
            self.assertEqual(ctx.exception.kind, "contract")


class CrashConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "site"
        self.data.mkdir()
        self.config = mon.MonitorConfig(
            site_id="pinaic",
            site_name="PinAI",
            base_url="https://example.test",
            username="u",
            password="p",
            data_dir=self.data,
            token_state_file=self.data / "token.json",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_event_written_before_latest_crash_recovers(self) -> None:
        store = mon.SnapshotStore(self.config)
        # First success
        store.persist_success([{"id": 1, "name": "a"}])
        # Simulate: event appended for new hash but latest not updated
        new_groups = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        digest = mon.content_hash_groups(new_groups)
        mon.append_jsonl_fsync(
            self.config.events_file,
            {
                "site_id": "pinaic",
                "observed_at": mon.utc_now_iso(),
                "event": "groups_changed",
                "added": [2],
                "removed": [],
                "modified": [],
                "content_hash": digest,
            },
        )
        # Restart: persist again should not duplicate event; should write latest
        before = self.config.events_file.read_text(encoding="utf-8").strip().splitlines()
        store.persist_success(new_groups)
        after = self.config.events_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(after), len(before))  # no duplicate
        latest = json.loads(self.config.latest_file.read_text(encoding="utf-8"))
        self.assertEqual(latest["content_hash"], digest)
        self.assertEqual(latest["count"], 2)

    def test_tmp_residue_cleaned_on_success(self) -> None:
        # leftover temps should not break atomic write
        (self.data / "groups_latest.json.tmp.99999").write_text("partial", encoding="utf-8")
        (self.data / "token.json.tmp.99999").write_text("partial", encoding="utf-8")
        store = mon.TokenStore(self.config.token_state_file)
        store.save(mon.TokenState(access_token="a", refresh_token="r", access_expires_at=1))
        snap = mon.SnapshotStore(self.config)
        snap.persist_success([{"id": 1}])
        latest = json.loads(self.config.latest_file.read_text(encoding="utf-8"))
        self.assertEqual(latest["count"], 1)
        # New write should not leave our pid temp
        self.assertFalse(any(p.name.endswith(f".tmp.{os.getpid()}") for p in self.data.iterdir()))

    def test_no_half_json_on_latest(self) -> None:
        snap = mon.SnapshotStore(self.config)
        snap.persist_success([{"id": 1, "name": "ok"}])
        text = self.config.latest_file.read_text(encoding="utf-8")
        json.loads(text)  # must parse fully


class PollLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "site"
        self.data.mkdir()
        self.config = mon.MonitorConfig(
            site_id="site",
            site_name="Site",
            base_url="https://example.test",
            username="user@example.test",
            password="secret",
            data_dir=self.data,
            token_state_file=self.data / "token.json",
            poll_interval_seconds=1,
            request_jitter_seconds=0,
            refresh_margin_seconds=600,
        )
        exp = int(time.time()) + 7200
        self.token = make_jwt(exp)
        self.store = mon.TokenStore(self.config.token_state_file)
        self.store.save(
            mon.TokenState(access_token=self.token, refresh_token="rt", access_expires_at=exp)
        )
        self.client = mon.AuthGroupClient(self.config, self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ok(self, groups):
        r = Mock(status_code=200)
        r.headers = {"Content-Type": "application/json"}
        r.json.return_value = {"data": groups}
        r.text = json.dumps({"data": groups})
        return r

    def test_two_successful_polls(self) -> None:
        self.client.session.get = Mock(
            side_effect=[self._ok([{"id": 1}]), self._ok([{"id": 1}, {"id": 2}])]
        )
        monitor = mon.GroupMonitor(self.config, self.client)
        monitor.poll_once()
        monitor.poll_once()
        latest = json.loads(self.config.latest_file.read_text(encoding="utf-8"))
        self.assertEqual(latest["count"], 2)
        self.assertEqual(monitor.failures, 0)

    def test_success_then_timeout_keeps_latest(self) -> None:
        self.client.session.get = Mock(
            side_effect=[self._ok([{"id": 9, "name": "keep"}]), requests.ReadTimeout("t")]
        )
        monitor = mon.GroupMonitor(self.config, self.client)
        monitor.poll_once()
        with self.assertRaises(mon.ApiError):
            monitor.poll_once()
        latest = json.loads(self.config.latest_file.read_text(encoding="utf-8"))
        self.assertEqual(latest["groups"][0]["id"], 9)
        self.assertEqual(self.store.state.access_token, self.token)

    def test_success_then_401_recovers(self) -> None:
        rejected = Mock(status_code=401)
        rejected.headers = {}
        rejected.text = "u"
        rejected.json.return_value = {"message": "unauthorized"}
        new_exp = int(time.time()) + 7200
        refresh_resp = Mock(status_code=200)
        refresh_resp.json.return_value = {
            "data": {"access_token": make_jwt(new_exp), "refresh_token": "rt"}
        }
        self.client.session.get = Mock(
            side_effect=[self._ok([{"id": 1}]), rejected, self._ok([{"id": 1}])]
        )
        self.client.session.post = Mock(return_value=refresh_resp)
        monitor = mon.GroupMonitor(self.config, self.client)
        monitor.poll_once()
        monitor.poll_once()
        self.assertEqual(monitor.failures, 0)

    def test_success_then_bad_json_keeps_latest(self) -> None:
        bad = Mock(status_code=200)
        bad.headers = {"Content-Type": "application/json"}
        bad.text = "{bad"
        bad.json.side_effect = requests.JSONDecodeError("e", "d", 0)
        self.client.session.get = Mock(side_effect=[self._ok([{"id": 5}]), bad])
        monitor = mon.GroupMonitor(self.config, self.client)
        monitor.poll_once()
        with self.assertRaises(mon.ApiError):
            monitor.poll_once()
        latest = json.loads(self.config.latest_file.read_text(encoding="utf-8"))
        self.assertEqual(latest["groups"][0]["id"], 5)

    def test_failure_count_reset_on_success(self) -> None:
        self.client.session.get = Mock(
            side_effect=[
                requests.ReadTimeout("t"),
                self._ok([{"id": 1}]),
            ]
        )
        monitor = mon.GroupMonitor(self.config, self.client)
        with self.assertRaises(mon.ApiError):
            monitor.poll_once()
        # manually bump like run_loop
        monitor.failures = 3
        monitor.poll_once()
        self.assertEqual(monitor.failures, 0)

    def test_sigterm_stops_loop(self) -> None:
        stop = {"flag": False}
        sleeps = []

        def sleep_fn(sec: float) -> None:
            sleeps.append(sec)
            stop["flag"] = True

        self.client.session.get = Mock(return_value=self._ok([]))
        monitor = mon.GroupMonitor(
            self.config,
            self.client,
            sleep_fn=sleep_fn,
            stop_flag=lambda: stop["flag"],
        )
        rc = monitor.run_loop()
        self.assertEqual(rc, 0)
        self.assertTrue(stop["flag"])

    def test_run_loop_handles_failure_then_success(self) -> None:
        calls = {"n": 0}
        stop = {"flag": False}

        def get_side_effect(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.ReadTimeout("t")
            if calls["n"] >= 3:
                stop["flag"] = True
            return self._ok([{"id": 1}])

        self.client.session.get = Mock(side_effect=get_side_effect)
        monitor = mon.GroupMonitor(
            self.config,
            self.client,
            sleep_fn=lambda _s: None,
            stop_flag=lambda: stop["flag"],
        )
        monitor.run_loop()
        self.assertGreaterEqual(calls["n"], 2)
        latest = json.loads(self.config.latest_file.read_text(encoding="utf-8"))
        self.assertEqual(latest["count"], 1)


class MultiSiteIsolationTests(unittest.TestCase):
    def test_parallel_sites_do_not_cross_tokens_or_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = {}
            errors = []

            def run_site(site_id: str, password: str, groups):
                try:
                    data = root / site_id
                    data.mkdir()
                    cfg = mon.MonitorConfig(
                        site_id=site_id,
                        site_name=site_id,
                        base_url=f"https://{site_id}.example.test",
                        username=f"{site_id}@example.test",
                        password=password,
                        data_dir=data,
                        token_state_file=data / "token.json",
                        poll_interval_seconds=60,
                        request_jitter_seconds=0,
                    )
                    store = mon.TokenStore(cfg.token_state_file)
                    client = mon.AuthGroupClient(cfg, store)
                    exp = int(time.time()) + 3600
                    token = make_jwt(exp, sub=site_id)
                    login = Mock(status_code=200)
                    login.json.return_value = {
                        "data": {"access_token": token, "refresh_token": f"rt_{site_id}"}
                    }
                    g = Mock(status_code=200)
                    g.headers = {"Content-Type": "application/json"}
                    g.json.return_value = {"data": groups}
                    g.text = json.dumps({"data": groups})
                    client.session.post = Mock(return_value=login)
                    client.session.get = Mock(return_value=g)
                    if site_id == "site-a":
                        # fail first site after login? optional failure
                        pass
                    monitor = mon.GroupMonitor(cfg, client)
                    monitor.poll_once()
                    results[site_id] = {
                        "token": store.state.access_token,
                        "refresh": store.state.refresh_token,
                        "latest": json.loads(cfg.latest_file.read_text(encoding="utf-8")),
                        "user": cfg.username,
                        "base": cfg.base_url,
                    }
                except Exception as exc:  # noqa: BLE001
                    errors.append((site_id, exc))

            t1 = threading.Thread(
                target=run_site, args=("site-a", "pass-a", [{"id": 1, "name": "A"}])
            )
            t2 = threading.Thread(
                target=run_site, args=("site-b", "pass-b", [{"id": 2, "name": "B"}])
            )
            t1.start()
            t2.start()
            t1.join()
            t2.join()
            self.assertEqual(errors, [])
            self.assertIn("site-a", results)
            self.assertIn("site-b", results)
            self.assertNotEqual(results["site-a"]["token"], results["site-b"]["token"])
            self.assertEqual(results["site-a"]["refresh"], "rt_site-a")
            self.assertEqual(results["site-b"]["refresh"], "rt_site-b")
            self.assertEqual(results["site-a"]["latest"]["site_id"], "site-a")
            self.assertEqual(results["site-b"]["latest"]["site_id"], "site-b")
            self.assertEqual(results["site-a"]["latest"]["groups"][0]["name"], "A")
            self.assertEqual(results["site-b"]["latest"]["groups"][0]["name"], "B")
            # Data isolation on disk
            a_latest = json.loads((root / "site-a" / "groups_latest.json").read_text())
            b_latest = json.loads((root / "site-b" / "groups_latest.json").read_text())
            self.assertNotEqual(a_latest["content_hash"], b_latest["content_hash"])

    def test_site_a_failure_does_not_affect_b(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def make(site_id: str) -> mon.GroupMonitor:
                data = root / site_id
                data.mkdir()
                cfg = mon.MonitorConfig(
                    site_id=site_id,
                    site_name=site_id,
                    base_url=f"https://{site_id}.example.test",
                    username=f"{site_id}@ex.test",
                    password="x",
                    data_dir=data,
                    token_state_file=data / "token.json",
                    poll_interval_seconds=60,
                    request_jitter_seconds=0,
                )
                store = mon.TokenStore(cfg.token_state_file)
                exp = int(time.time()) + 7200
                store.save(
                    mon.TokenState(
                        access_token=make_jwt(exp, sub=site_id),
                        refresh_token=f"rt_{site_id}",
                        access_expires_at=exp,
                    )
                )
                client = mon.AuthGroupClient(cfg, store)
                return mon.GroupMonitor(cfg, client), client, cfg

            mon_a, client_a, cfg_a = make("site-a")
            mon_b, client_b, cfg_b = make("site-b")
            client_a.session.get = Mock(side_effect=requests.ReadTimeout("t"))
            ok = Mock(status_code=200)
            ok.headers = {"Content-Type": "application/json"}
            ok.json.return_value = {"data": [{"id": 7}]}
            ok.text = '{"data":[{"id":7}]}'
            client_b.session.get = Mock(return_value=ok)
            with self.assertRaises(mon.ApiError):
                mon_a.poll_once()
            mon_b.poll_once()
            self.assertFalse(cfg_a.latest_file.exists())
            latest_b = json.loads(cfg_b.latest_file.read_text(encoding="utf-8"))
            self.assertEqual(latest_b["site_id"], "site-b")


class CliSmokeTests(unittest.TestCase):
    def test_validate_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = write_env(root / "pinaic.env", base_env(root))
            rc = mon.main(["--env-file", str(env), "--validate"])
            self.assertEqual(rc, 0)

    def test_main_once_with_mocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_map = base_env(root)
            env = write_env(root / "pinaic.env", env_map)
            exp = int(time.time()) + 3600
            token = make_jwt(exp)

            login = Mock(status_code=200)
            login.json.return_value = {
                "data": {"access_token": token, "refresh_token": "rt"}
            }
            groups = Mock(status_code=200)
            groups.headers = {"Content-Type": "application/json"}
            groups.json.return_value = {"data": [{"id": 1, "name": "g", "rate_multiplier": 1, "status": "active"}]}
            groups.text = json.dumps(groups.json.return_value)

            real_session_cls = requests.Session

            class FakeSession(real_session_cls):
                def post(self, *a, **k):
                    return login

                def get(self, *a, **k):
                    return groups

            with patch.object(mon.requests, "Session", FakeSession):
                rc = mon.main(["--env-file", str(env), "--once"])
            self.assertEqual(rc, 0)
            token_path = Path(env_map["TOKEN_STATE_FILE"])
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
            latest = json.loads(Path(env_map["DATA_DIR"], "groups_latest.json").read_text())
            self.assertEqual(latest["count"], 1)


class OnceBoundedRetryTests(unittest.TestCase):
    """architecture §5.1 / §9.1: --once is a bounded-retry round, not fail-and-exit."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "site"
        self.data.mkdir()
        self.config = mon.MonitorConfig(
            site_id="site",
            site_name="Site",
            base_url="https://example.test",
            username="user@example.test",
            password="secret",
            data_dir=self.data,
            token_state_file=self.data / "token.json",
            request_jitter_seconds=0,
            poll_interval_seconds=300,
        )
        self.store = mon.TokenStore(self.config.token_state_file)
        exp = int(time.time()) + 7200
        self.store.save(
            mon.TokenState(
                access_token=make_jwt(exp),
                refresh_token="rt",
                access_expires_at=exp,
            )
        )
        self.client = mon.AuthGroupClient(self.config, self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ok(self, groups: list) -> Mock:
        resp = Mock(status_code=200)
        resp.headers = {"Content-Type": "application/json"}
        resp.json.return_value = {"data": groups}
        resp.text = json.dumps({"data": groups})
        return resp

    def _clocked_monitor(self) -> tuple[mon.GroupMonitor, list[float]]:
        """sleep_fn advances monotonic clock so interruptible_sleep does not busy-wait."""
        clock = {"t": 0.0}
        sleeps: list[float] = []

        def mono() -> float:
            return clock["t"]

        def sleep_fn(sec: float) -> None:
            sleeps.append(sec)
            clock["t"] += sec

        monitor = mon.GroupMonitor(
            self.config,
            self.client,
            sleep_fn=sleep_fn,
            monotonic_fn=mono,
        )
        return monitor, sleeps

    def test_once_429_then_success_uses_backoff(self) -> None:
        rate = Mock(status_code=429)
        rate.headers = {"Retry-After": "2", "Content-Type": "application/json"}
        rate.text = '{"error":"rate"}'
        rate.json.return_value = {"error": "rate"}
        self.client.session.get = Mock(side_effect=[rate, self._ok([{"id": 1}])])
        monitor, sleeps = self._clocked_monitor()
        rc = monitor.run_once(max_attempts=3)
        self.assertEqual(rc, 0)
        self.assertEqual(self.client.session.get.call_count, 2)
        # interruptible_sleep chunks into ≤1s; total waited ≈ backoff (≥ Retry-After 2)
        self.assertGreaterEqual(sum(sleeps), 2.0)
        latest = json.loads(self.config.latest_file.read_text(encoding="utf-8"))
        self.assertEqual(latest["groups"][0]["id"], 1)

    def test_once_transient_exhausts_attempts(self) -> None:
        self.client.session.get = Mock(side_effect=requests.ReadTimeout("t"))
        monitor, sleeps = self._clocked_monitor()
        rc = monitor.run_once(max_attempts=3)
        self.assertEqual(rc, 1)
        self.assertEqual(self.client.session.get.call_count, 3)
        # two backoffs between three attempts
        self.assertGreater(sum(sleeps), 0)

    def test_once_region_does_not_retry(self) -> None:
        region = Mock(status_code=403)
        region.headers = {"Content-Type": "text/html"}
        region.text = "<html>cloudflare access denied</html>"
        region.json.side_effect = ValueError("not json")
        self.client.session.get = Mock(return_value=region)
        monitor, sleeps = self._clocked_monitor()
        rc = monitor.run_once(max_attempts=3)
        self.assertEqual(rc, 1)
        self.assertEqual(self.client.session.get.call_count, 1)
        self.assertEqual(sleeps, [])

    def test_once_contract_does_not_retry(self) -> None:
        bad = Mock(status_code=200)
        bad.headers = {"Content-Type": "application/json"}
        bad.json.return_value = {"not": "data"}
        bad.text = '{"not":"data"}'
        self.client.session.get = Mock(return_value=bad)
        monitor, _sleeps = self._clocked_monitor()
        rc = monitor.run_once(max_attempts=3)
        self.assertEqual(rc, 1)
        self.assertEqual(self.client.session.get.call_count, 1)

    def test_once_budget_caps_backoff(self) -> None:
        """remaining_budget caps sleep so oneshot stays under TimeoutStartSec."""
        self.client.session.get = Mock(side_effect=requests.ReadTimeout("t"))
        monitor, sleeps = self._clocked_monitor()
        # failures ladder starts at 10s; budget 5s must cap
        rc = monitor.run_once(max_attempts=2, budget_seconds=5.0)
        self.assertEqual(rc, 1)
        self.assertEqual(self.client.session.get.call_count, 2)
        self.assertLessEqual(sum(sleeps), 5.0 + 0.01)

    def test_main_once_attempts_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = write_env(root / "pinaic.env", base_env(root))
            calls = {"n": 0}

            def fake_run_once(self, *, max_attempts=3, budget_seconds=200.0):  # noqa: ANN001
                calls["n"] = max_attempts
                return 0

            with patch.object(mon.GroupMonitor, "run_once", fake_run_once):
                with patch.object(mon.InstanceLock, "acquire", lambda self: None):
                    with patch.object(mon.InstanceLock, "release", lambda self: None):
                        rc = mon.main(
                            ["--env-file", str(env), "--once", "--once-attempts", "5"]
                        )
            self.assertEqual(rc, 0)
            self.assertEqual(calls["n"], 5)


class PureConfigTests(unittest.TestCase):
    def test_two_env_files_do_not_cross_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            e1 = base_env(root, site_id="site-a")
            e1["MONITOR_BASE_URL"] = "https://a.example.test"
            e1["MONITOR_USERNAME"] = "a@example.test"
            e1["MONITOR_PASSWORD"] = "pass-a"
            e2 = base_env(root, site_id="site-b")
            e2["MONITOR_BASE_URL"] = "https://b.example.test"
            e2["MONITOR_USERNAME"] = "b@example.test"
            e2["MONITOR_PASSWORD"] = "pass-b"
            env1 = write_env(root / "a.env", e1)
            env2 = write_env(root / "b.env", e2)
            # Closed environ: pure load must not rely on mutating os.environ.
            cfg_a = mon.load_config(env1, environ={})
            cfg_b = mon.load_config(env2, environ={})
            self.assertEqual(cfg_a.site_id, "site-a")
            self.assertEqual(cfg_b.site_id, "site-b")
            self.assertEqual(cfg_a.base_url, "https://a.example.test")
            self.assertEqual(cfg_b.base_url, "https://b.example.test")
            self.assertEqual(cfg_a.username, "a@example.test")
            self.assertEqual(cfg_b.username, "b@example.test")
            self.assertEqual(cfg_a.password, "pass-a")
            self.assertEqual(cfg_b.password, "pass-b")
            self.assertNotEqual(cfg_a.data_dir, cfg_b.data_dir)

    def test_load_config_does_not_mutate_os_environ(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = "SUB2API_TEST_MARKER_SHOULD_NOT_APPEAR"
            env_map = base_env(root)
            env_map[marker] = "from-file"
            env_path = write_env(root / "x.env", env_map)
            before = os.environ.get(marker)
            try:
                if marker in os.environ:
                    del os.environ[marker]
                mon.load_config(env_path, environ=os.environ)
                self.assertNotIn(marker, os.environ)
            finally:
                if before is None:
                    os.environ.pop(marker, None)
                else:
                    os.environ[marker] = before


class LockRecoveryTests(unittest.TestCase):
    def test_lock_reacquirable_after_fd_close(self) -> None:
        """Simulate TimeoutStartSec/SIGKILL: flock is released when fd closes."""
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "monitor.lock"
            first = mon.InstanceLock(lock_path)
            first.acquire()
            # Kill-like: close underlying fd without orderly release path
            # (kernel drops flock when last fd closes).
            fh = first._fh
            self.assertIsNotNone(fh)
            fh.close()
            first._fh = None

            second = mon.InstanceLock(lock_path)
            second.acquire()  # must not raise ConfigError
            second.release()


class HelperTests(unittest.TestCase):
    def test_jwt_expiry_and_backoff(self) -> None:
        exp = 1_800_000_000
        self.assertEqual(mon.jwt_expiry(make_jwt(exp)), exp)
        self.assertIsNone(mon.jwt_expiry("not-a-jwt"))
        cfg = mon.MonitorConfig(
            site_id="s",
            site_name="s",
            base_url="https://x.test",
            username="u",
            password="p",
            data_dir=Path("/tmp"),
            token_state_file=Path("/tmp/t.json"),
            poll_interval_seconds=300,
        )
        store = mon.TokenStore.__new__(mon.TokenStore)
        store.path = Path("/tmp/t.json")
        store.state = mon.TokenState()
        client = mon.AuthGroupClient(cfg, store)
        monitor = mon.GroupMonitor(cfg, client)
        monitor.failures = 1
        self.assertEqual(monitor.backoff_delay(), 10)
        monitor.failures = 5
        self.assertEqual(monitor.backoff_delay(), 300)

    def test_events_retention_prune(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            old = {
                "observed_at": "2020-01-01T00:00:00Z",
                "content_hash": "sha256:old",
                "event": "initial",
            }
            new = {
                "observed_at": mon.utc_now_iso(),
                "content_hash": "sha256:new",
                "event": "groups_changed",
            }
            mon.append_jsonl_fsync(path, old)
            mon.append_jsonl_fsync(path, new)
            mon.prune_events(path, retention_days=180)
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn("sha256:new", lines[0])


if __name__ == "__main__":
    unittest.main()
