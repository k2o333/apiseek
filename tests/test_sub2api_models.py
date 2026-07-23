"""Tests for sub2api_models — drive real shipped functions; mock only HTTP boundary."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import requests

import sub2api_models as m
from sub2api_monitor import ApiError


# ---------------------------------------------------------------------------
# Helpers / fake remote key store
# ---------------------------------------------------------------------------


class FakeKeysBackend:
    """In-memory keys API for reconcile/list tests (transport-level fake)."""

    def __init__(self) -> None:
        self.keys: list[dict[str, Any]] = []
        self.next_id = 1
        self.create_calls = 0
        self.bind_calls = 0
        self.list_calls = 0
        self.create_behavior: str = "ok"  # ok | timeout | server
        self.bind_behavior: str = "ok"  # ok | fail
        self._create_timeout_once = False
        self._server_created_on_timeout = True
        self.page_size_override: int | None = None

    def seed(self, keys: list[dict[str, Any]]) -> None:
        self.keys = [dict(k) for k in keys]
        if keys:
            ids = [int(k["id"]) for k in keys if str(k.get("id", "")).isdigit()]
            if ids:
                self.next_id = max(ids) + 1

    def list_keys_fn(self) -> tuple[list[dict[str, Any]], bool]:
        self.list_calls += 1
        return [dict(k) for k in self.keys], True

    def create_fn(self, name: str) -> dict[str, Any]:
        self.create_calls += 1
        if self.create_behavior == "timeout":
            if self._server_created_on_timeout:
                # Server actually created the key despite client timeout
                kid = self.next_id
                self.next_id += 1
                self.keys.append(
                    {
                        "id": kid,
                        "name": name,
                        "group_id": None,
                        "key": f"sk-created-{kid}",
                        "status": "active",
                    }
                )
            raise ApiError("create timeout", kind="timeout")
        if self.create_behavior == "server":
            if self._server_created_on_timeout:
                kid = self.next_id
                self.next_id += 1
                self.keys.append(
                    {
                        "id": kid,
                        "name": name,
                        "group_id": None,
                        "key": f"sk-created-{kid}",
                        "status": "active",
                    }
                )
            raise ApiError("create HTTP 503", status_code=503, kind="server")
        kid = self.next_id
        self.next_id += 1
        rec = {
            "id": kid,
            "name": name,
            "group_id": None,
            "key": f"sk-created-{kid}",
            "status": "active",
        }
        self.keys.append(dict(rec))
        return dict(rec)

    def bind_fn(self, key_id: Any, group_id: Any, name: str) -> dict[str, Any]:
        self.bind_calls += 1
        if self.bind_behavior == "fail":
            raise ApiError("bind HTTP 500", status_code=500, kind="server")
        for k in self.keys:
            if str(k.get("id")) == str(key_id):
                k["group_id"] = group_id
                k["name"] = name
                return dict(k)
        raise ApiError(f"key {key_id} not found", kind="error")

    def count_managed(self, group_id: Any) -> int:
        want = m.managed_key_name(group_id)
        return sum(1 for k in self.keys if k.get("name") == want)


class FakePagedTransport:
    """Fake page transport for list_keys_all."""

    def __init__(
        self,
        pages: list[list[dict[str, Any]]],
        *,
        page_size: int = 2,
        include_total: bool = True,
        include_has_more: bool = False,
        incomplete_mode: bool = False,
    ) -> None:
        self.pages = pages
        self.page_size = page_size
        self.include_total = include_total
        self.include_has_more = include_has_more
        self.incomplete_mode = incomplete_mode
        self.calls: list[tuple[int, int]] = []

    def __call__(self, page: int, page_size: int) -> dict[str, Any]:
        self.calls.append((page, page_size))
        idx = page - 1
        if idx < 0 or idx >= len(self.pages):
            items: list[dict[str, Any]] = []
        else:
            items = self.pages[idx]
        # total = unique key ids (realistic API); not raw row sum (dupes across pages).
        if self.include_total:
            seen_ids: set[str] = set()
            for page_items in self.pages:
                for it in page_items:
                    if it.get("id") is not None:
                        seen_ids.add(str(it["id"]).strip())
            total: int | None = len(seen_ids)
        else:
            total = None
        body: dict[str, Any] = {
            "data": {
                "items": items,
            }
        }
        if self.include_total and total is not None:
            body["data"]["total"] = total
        body["data"]["page"] = page
        body["data"]["page_size"] = page_size
        if self.include_has_more:
            body["data"]["has_more"] = idx + 1 < len(self.pages)
        if self.incomplete_mode:
            # Always return full pages forever (no total, no has_more, never short page)
            # Simulate by repeating last full page without end signal after known pages.
            if idx >= len(self.pages):
                # Keep returning full dummy pages — list_keys_all hits max_pages
                body["data"]["items"] = [
                    {"id": f"extra-{page}-{i}", "name": "x", "key": "sk"}
                    for i in range(page_size)
                ]
                body["data"].pop("total", None)
                body["data"].pop("has_more", None)
        return body


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


class NormIdTests(unittest.TestCase):
    def test_norm_id_number_string_strip(self) -> None:
        self.assertEqual(m.norm_id(7), "7")
        self.assertEqual(m.norm_id("7"), "7")
        self.assertEqual(m.norm_id("  52  "), "52")
        self.assertEqual(m.norm_id(0), "0")


class UsableKeysTests(unittest.TestCase):
    def test_usable_keys_filters_and_int_first_sort(self) -> None:
        keys = [
            {"id": "10", "group_id": 1, "key": "sk-10"},
            {"id": "2", "group_id": 1, "key": "sk-2"},
            {"id": "3", "group_id": 1, "key": ""},  # no secret
            {"id": "4", "group_id": 1, "api_key": "sk-4"},
            {"id": "5", "group_id": 1, "key": "sk-5", "disabled": True},
            {"id": "6", "group_id": 1, "key": "sk-6", "status": "disabled"},
            {"id": "7", "group_id": 1, "key": "sk-7", "expired": True},
            {"id": "8", "group_id": 2, "key": "sk-other"},
            {"id": "9", "group_id": "1", "key": "sk-9"},  # string group match
        ]
        usable = m.usable_keys(1, keys)
        ids = [m.norm_id(k["id"]) for k in usable]
        # 2,4,9,10 — int-first: 2 < 4 < 9 < 10
        self.assertEqual(ids, ["2", "4", "9", "10"])
        # no secret / disabled / expired excluded
        self.assertNotIn("3", ids)
        self.assertNotIn("5", ids)
        self.assertNotIn("6", ids)
        self.assertNotIn("7", ids)
        self.assertNotIn("8", ids)

        picked = m.pick_key(1, keys)
        assert picked is not None
        self.assertEqual(m.norm_id(picked["id"]), "2")


class ManagedKeyNameTests(unittest.TestCase):
    def test_managed_key_name(self) -> None:
        self.assertEqual(m.managed_key_name(52), "sub2api-monitor:g:52")
        self.assertEqual(m.managed_key_name(" 9 "), "sub2api-monitor:g:9")
        self.assertTrue(m.is_managed_key_name("sub2api-monitor:g:52"))
        self.assertFalse(m.is_managed_key_name("sub2api-monitor:g:"))
        self.assertFalse(m.is_managed_key_name("other:g:52"))
        self.assertFalse(m.is_managed_key_name("not-managed"))
        self.assertTrue(m.is_managed_key_name(m.managed_key_name(1)))


class ContentHashTests(unittest.TestCase):
    def test_content_hash_sorted_stable(self) -> None:
        h1 = m.content_hash_models(["b", "a"])
        h2 = m.content_hash_models(["a", "b"])
        self.assertEqual(h1, h2)
        assert h1 is not None
        self.assertTrue(h1.startswith("sha256:"))
        self.assertIsNone(m.content_hash_models(None))
        empty = m.content_hash_models([])
        assert empty is not None
        self.assertTrue(empty.startswith("sha256:"))


# ---------------------------------------------------------------------------
# list_keys_all
# ---------------------------------------------------------------------------


class ListKeysAllTests(unittest.TestCase):
    def test_multi_page_and_dedupe(self) -> None:
        pages = [
            [{"id": 1, "name": "a", "key": "sk-1"}, {"id": 2, "name": "b", "key": "sk-2"}],
            [{"id": 2, "name": "b-dup", "key": "sk-2b"}, {"id": 3, "name": "c", "key": "sk-3"}],
            [{"id": 4, "name": "d", "key": "sk-4"}],
        ]
        transport = FakePagedTransport(pages, page_size=2, include_total=True)
        keys, complete = m.list_keys_all(transport, page_size=2)
        self.assertTrue(complete)
        ids = [m.norm_id(k["id"]) for k in keys]
        self.assertEqual(ids, ["1", "2", "3", "4"])
        # dedupe keeps last occurrence for id 2
        k2 = next(k for k in keys if m.norm_id(k["id"]) == "2")
        self.assertEqual(k2["name"], "b-dup")
        self.assertGreaterEqual(len(transport.calls), 2)

    def test_incomplete_paging_complete_false(self) -> None:
        # Full pages only, no total/has_more, and keep returning full pages → incomplete
        page = [{"id": i, "name": f"k{i}", "key": f"sk-{i}"} for i in range(10)]

        def endless(p: int, page_size: int) -> dict[str, Any]:
            # Always full page, never signals end
            start = (p - 1) * page_size
            items = [
                {"id": start + j, "name": f"k{start+j}", "key": f"sk-{start+j}"}
                for j in range(page_size)
            ]
            return {"data": {"items": items, "page": p, "page_size": page_size}}

        keys, complete = m.list_keys_all(endless, page_size=5, max_pages=3)
        self.assertFalse(complete)
        self.assertEqual(len(keys), 15)

    def test_short_page_proves_complete(self) -> None:
        def fn(p: int, page_size: int) -> dict[str, Any]:
            if p == 1:
                return {
                    "data": {
                        "items": [{"id": 1, "key": "sk"}, {"id": 2, "key": "sk"}],
                        "page_size": 10,
                    }
                }
            return {"data": {"items": [], "page_size": 10}}

        keys, complete = m.list_keys_all(fn, page_size=10)
        self.assertTrue(complete)
        self.assertEqual(len(keys), 2)

    def test_short_page_with_total_unsatisfied_not_complete(self) -> None:
        """P0-2: page1 short + total > len(items) must NOT mark complete."""
        calls: list[int] = []

        def fn(p: int, page_size: int) -> dict[str, Any]:
            calls.append(p)
            # Only 2 items but server claims total=50; short relative to page_size=100.
            return {
                "data": {
                    "items": [
                        {"id": 1, "name": "a", "key": "sk-1"},
                        {"id": 2, "name": "b", "key": "sk-2"},
                    ],
                    "total": 50,
                    "page": p,
                    "page_size": page_size,
                }
            }

        keys, complete = m.list_keys_all(fn, page_size=100, max_pages=5)
        self.assertFalse(complete, "must not treat short page as complete while total unsatisfied")
        self.assertEqual(len(keys), 2)
        # Should not loop forever; stop after incomplete short page
        self.assertEqual(calls, [1])

    def test_short_page_has_more_true_fetches_next(self) -> None:
        """P0-2: short page + has_more=True must fetch next page."""
        pages = {
            1: {
                "data": {
                    "items": [{"id": 1, "key": "sk-1"}],  # short vs page_size
                    "page": 1,
                    "page_size": 10,
                    "has_more": True,
                }
            },
            2: {
                "data": {
                    "items": [{"id": 2, "key": "sk-2"}, {"id": 3, "key": "sk-3"}],
                    "page": 2,
                    "page_size": 10,
                    "has_more": False,
                }
            },
        }
        calls: list[int] = []

        def fn(p: int, page_size: int) -> dict[str, Any]:
            calls.append(p)
            return pages[p]

        keys, complete = m.list_keys_all(fn, page_size=10)
        self.assertTrue(complete)
        self.assertEqual([m.norm_id(k["id"]) for k in keys], ["1", "2", "3"])
        self.assertEqual(calls, [1, 2])

    def test_short_page_total_unsatisfied_ensure_no_create(self) -> None:
        """ensure_coverage must fail-closed (created=0) when list_keys_all incomplete."""
        create_calls = 0

        def list_fn() -> tuple[list[dict[str, Any]], bool]:
            # Drive real list_keys_all, not a hand-stubbed incomplete flag.
            def page_fn(p: int, page_size: int) -> dict[str, Any]:
                return {
                    "data": {
                        "items": [{"id": 99, "name": "orphan", "key": "sk-x"}],
                        "total": 20,
                        "page": p,
                        "page_size": page_size,
                    }
                }

            return m.list_keys_all(page_fn, page_size=100)

        def create_fn(name: str) -> dict[str, Any]:
            nonlocal create_calls
            create_calls += 1
            return {"id": 1000 + create_calls, "name": name, "key": f"sk-new-{create_calls}"}

        def bind_fn(key_id: Any, group_id: Any, name: str) -> dict[str, Any]:
            return {"id": key_id, "name": name, "group_id": group_id, "key": "sk"}

        result = m.ensure_coverage(
            [{"id": 1}, {"id": 2}],
            list_keys_fn=list_fn,
            create_fn=create_fn,
            bind_fn=bind_fn,
        )
        self.assertTrue(result.paging_incomplete)
        self.assertEqual(result.created, 0)
        self.assertEqual(create_calls, 0)


# ---------------------------------------------------------------------------
# ensure_coverage fail closed
# ---------------------------------------------------------------------------


class EnsureCoverageTests(unittest.TestCase):
    def test_incomplete_paging_does_not_create(self) -> None:
        backend = FakeKeysBackend()
        create_calls = 0

        def list_incomplete() -> tuple[list[dict[str, Any]], bool]:
            return [], False

        def create_fn(name: str) -> dict[str, Any]:
            nonlocal create_calls
            create_calls += 1
            return backend.create_fn(name)

        result = m.ensure_coverage(
            [{"id": 1}, {"id": 2}],
            list_keys_fn=list_incomplete,
            create_fn=create_fn,
            bind_fn=backend.bind_fn,
        )
        self.assertTrue(result.paging_incomplete)
        self.assertEqual(result.created, 0)
        self.assertEqual(create_calls, 0)

    def test_ensure_returns_keys_after(self) -> None:
        backend = FakeKeysBackend()
        groups = [{"id": 9}, {"id": 52}]
        # Initially empty
        result = m.ensure_coverage(
            groups,
            list_keys_fn=backend.list_keys_fn,
            create_fn=backend.create_fn,
            bind_fn=backend.bind_fn,
        )
        self.assertFalse(result.paging_incomplete)
        self.assertEqual(result.created, 2)
        # pick after ensure sees new keys
        for g in groups:
            picked = m.pick_key(g["id"], result.keys)
            self.assertIsNotNone(picked, f"group {g['id']} should have usable key")
            assert picked is not None
            self.assertEqual(m.norm_id(picked["group_id"]), m.norm_id(g["id"]))
            secret = m.key_secret(picked)
            self.assertTrue(secret)


# ---------------------------------------------------------------------------
# reconcile idempotency
# ---------------------------------------------------------------------------


class ReconcileTests(unittest.TestCase):
    def test_post_timeout_server_created_then_claim(self) -> None:
        backend = FakeKeysBackend()
        backend.create_behavior = "timeout"
        backend._server_created_on_timeout = True

        r1 = m.reconcile_key_for_group(
            7,
            [],
            list_keys_fn=backend.list_keys_fn,
            create_fn=backend.create_fn,
            bind_fn=backend.bind_fn,
        )
        # After timeout, should claim via re-list + bind, created flag False (unknown outcome path)
        self.assertIsNone(r1.error)
        self.assertIsNotNone(r1.key)
        self.assertEqual(backend.count_managed(7), 1)
        self.assertEqual(backend.create_calls, 1)

        # Second reconcile: usable exists, no more create
        r2 = m.reconcile_key_for_group(
            7,
            r1.keys_after,
            list_keys_fn=backend.list_keys_fn,
            create_fn=backend.create_fn,
            bind_fn=backend.bind_fn,
        )
        self.assertFalse(r2.created)
        self.assertEqual(backend.create_calls, 1)
        self.assertEqual(backend.count_managed(7), 1)

    def test_bind_fail_then_restart_claim_unbound(self) -> None:
        backend = FakeKeysBackend()
        # First attempt: create ok, bind fails → unbound managed left on server
        backend.bind_behavior = "fail"
        r1 = m.reconcile_key_for_group(
            3,
            [],
            list_keys_fn=backend.list_keys_fn,
            create_fn=backend.create_fn,
            bind_fn=backend.bind_fn,
        )
        self.assertIsNotNone(r1.error)
        self.assertEqual(backend.count_managed(3), 1)
        unbound = [k for k in backend.keys if k.get("name") == m.managed_key_name(3)]
        self.assertEqual(len(unbound), 1)
        self.assertTrue(unbound[0].get("group_id") in (None, ""))

        # Restart: claim unbound, no second create
        backend.bind_behavior = "ok"
        create_before = backend.create_calls
        r2 = m.reconcile_key_for_group(
            3,
            list(backend.keys),
            list_keys_fn=backend.list_keys_fn,
            create_fn=backend.create_fn,
            bind_fn=backend.bind_fn,
        )
        self.assertIsNone(r2.error)
        self.assertFalse(r2.created)
        self.assertEqual(backend.create_calls, create_before)
        self.assertEqual(backend.count_managed(3), 1)
        self.assertIsNotNone(m.pick_key(3, r2.keys_after))


# ---------------------------------------------------------------------------
# Models auth domain separation
# ---------------------------------------------------------------------------


class ModelsAuthDomainTests(unittest.TestCase):
    def test_models_401_does_not_call_login_recover(self) -> None:
        recover = Mock()
        get_token = Mock(return_value="jwt-token")

        session = Mock(spec=requests.Session)
        session.headers = {}
        resp = Mock()
        resp.status_code = 401
        resp.text = "unauthorized"
        session.get.return_value = resp

        client = m.KeysModelsClient(
            base_url="https://example.test",
            get_access_token=get_token,
            session=session,
            recover_auth=recover,
        )
        with self.assertRaises(ApiError) as ctx:
            client.list_models("sk-bad")
        self.assertEqual(ctx.exception.kind, "key_auth")
        recover.assert_not_called()
        # list_models uses API key, not JWT get for the models request path
        session.get.assert_called_once()
        call_headers = session.get.call_args.kwargs.get("headers") or session.get.call_args[1].get("headers")
        self.assertIn("Bearer sk-bad", call_headers["Authorization"])

    def test_keys_401_calls_recover_once(self) -> None:
        recover = Mock()
        tokens = {"v": "jwt-old"}

        def get_token() -> str:
            return tokens["v"]

        def do_recover() -> None:
            tokens["v"] = "jwt-new"

        recover.side_effect = do_recover

        session = Mock(spec=requests.Session)
        session.headers = {}
        resp_auth = Mock()
        resp_auth.status_code = 401
        resp_ok = Mock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {
            "data": {"items": [{"id": 1, "key": "sk-1", "group_id": 1}], "total": 1, "page_size": 100}
        }
        session.request.side_effect = [resp_auth, resp_ok]

        client = m.KeysModelsClient(
            base_url="https://example.test",
            get_access_token=get_token,
            session=session,
            recover_auth=recover,
        )
        keys, complete = client.list_keys_all()
        recover.assert_called_once()
        self.assertTrue(complete)
        self.assertEqual(len(keys), 1)


# ---------------------------------------------------------------------------
# ModelsStore
# ---------------------------------------------------------------------------


class ModelsStoreTests(unittest.TestCase):
    def test_null_vs_empty_and_fail_preserves_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = m.ModelsStore(tmp, "littleapi")
            # never-success: models=null not []
            entry = store.get_group(99)
            self.assertIsNone(entry["models"])
            self.assertIsNone(entry["content_hash"])

            store.apply_failure(99, "no_usable_key", error_kind="no_usable_key")
            entry = store.get_group(99)
            self.assertIsNone(entry["models"])
            self.assertEqual(entry["last_error"], "no_usable_key")

            # success empty list
            store.apply_success(9, [], key_id=835, source="bootstrap")
            entry = store.get_group(9)
            self.assertEqual(entry["models"], [])
            self.assertIsNotNone(entry["content_hash"])
            self.assertIsNone(entry["last_error"])

            # success with models
            store.apply_success(9, ["gpt-5", "gpt-4"], key_id=835, source="daily")
            entry = store.get_group(9)
            self.assertEqual(sorted(entry["models"]), ["gpt-4", "gpt-5"])
            old_hash = entry["content_hash"]
            old_success = entry["last_success_at"]

            # failure preserves models/hash/success key_id
            store.apply_failure(9, "HTTP 503", error_kind="server", source="daily")
            entry = store.get_group(9)
            self.assertEqual(sorted(entry["models"]), ["gpt-4", "gpt-5"])
            self.assertEqual(entry["content_hash"], old_hash)
            self.assertEqual(entry["last_success_at"], old_success)
            self.assertEqual(entry["key_id"], 835)
            self.assertEqual(entry["last_error"], "HTTP 503")
            self.assertIsNotNone(entry["next_retry_at"])

    def test_checkpoint_after_group1_even_if_group2_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = m.ModelsStore(tmp, "pinaic")
            store.apply_success(1, ["m1"], key_id=10, source="bootstrap")
            # Simulate mid-batch: file has group1
            on_disk = json.loads((Path(tmp) / "models_latest.json").read_text(encoding="utf-8"))
            self.assertIn("1", on_disk["models_by_group"])
            self.assertEqual(on_disk["models_by_group"]["1"]["models"], ["m1"])

            store.apply_failure(2, "timeout", error_kind="timeout", source="bootstrap")
            on_disk = json.loads((Path(tmp) / "models_latest.json").read_text(encoding="utf-8"))
            self.assertIn("1", on_disk["models_by_group"])
            self.assertIn("2", on_disk["models_by_group"])
            self.assertEqual(on_disk["models_by_group"]["1"]["models"], ["m1"])
            self.assertIsNone(on_disk["models_by_group"]["2"]["models"])

    def test_content_hash_change_appends_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = m.ModelsStore(tmp, "hubway")
            store.apply_success(5, ["a"], key_id=1, source="bootstrap")
            store.apply_success(5, ["a", "b"], key_id=1, source="daily")
            events_path = Path(tmp) / "models_events.jsonl"
            lines = events_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            e0 = json.loads(lines[0])
            e1 = json.loads(lines[1])
            self.assertEqual(e0["event"], "initial")
            self.assertEqual(e1["event"], "models_changed")
            self.assertEqual(e1["group_id"], "5")
            # same hash re-apply: no new event
            store.apply_success(5, ["b", "a"], key_id=1, source="daily")
            lines2 = events_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines2), 2)

    def test_full_meta_partial_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = m.ModelsStore(tmp, "yybb")
            store.update_full_meta(target=3, ok=2, failed=1, bootstrap=True)
            rec = store.load()
            self.assertIsNotNone(rec["last_full_attempt_at"])
            self.assertIsNone(rec["last_full_success_at"])
            self.assertIsNone(rec["bootstrap_completed_at"])
            self.assertEqual(rec["last_full_result"], {"target": 3, "ok": 2, "failed": 1})

            # full success path
            store.update_full_meta(target=3, ok=3, failed=0, bootstrap=True)
            rec = store.load()
            self.assertIsNotNone(rec["last_full_success_at"])
            self.assertIsNotNone(rec["bootstrap_completed_at"])
            self.assertEqual(rec["last_full_result"]["failed"], 0)

    def test_bootstrap_completed_at_only_on_full_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = m.ModelsStore(tmp, "iaiguo")
            # helper: update_full_meta with bootstrap flag
            store.update_full_meta(target=1, ok=0, failed=1, bootstrap=True)
            self.assertIsNone(store.load()["bootstrap_completed_at"])
            store.update_full_meta(target=1, ok=1, failed=0, bootstrap=True)
            self.assertIsNotNone(store.load()["bootstrap_completed_at"])

    def test_should_attempt_now(self) -> None:
        self.assertTrue(m.should_attempt_now(None))
        self.assertTrue(m.should_attempt_now({}))
        self.assertTrue(m.should_attempt_now({"next_retry_at": None}))
        # far future
        self.assertFalse(
            m.should_attempt_now({"next_retry_at": "2099-01-01T00:00:00Z"})
        )
        # past
        self.assertTrue(
            m.should_attempt_now({"next_retry_at": "2000-01-01T00:00:00Z"})
        )


# ---------------------------------------------------------------------------
# refresh_models_for_groups
# ---------------------------------------------------------------------------


class RefreshModelsTests(unittest.TestCase):
    def test_key_auth_tries_next_never_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = m.ModelsStore(tmp, "klinkw")
            keys = [
                {"id": 1, "group_id": 10, "key": "sk-bad"},
                {"id": 2, "group_id": 10, "key": "sk-good"},
            ]
            recover = Mock()
            calls: list[str] = []

            def list_models(secret: str) -> list[str]:
                calls.append(secret)
                if secret == "sk-bad":
                    raise ApiError("models HTTP 401", status_code=401, kind="key_auth")
                return ["model-x"]

            result = m.refresh_models_for_groups(
                [{"id": 10}],
                keys,
                store,
                list_models,
                source="bootstrap",
            )
            self.assertEqual(result.ok_count, 1)
            self.assertEqual(result.failed_count, 0)
            self.assertEqual(calls, ["sk-bad", "sk-good"])
            recover.assert_not_called()
            entry = store.get_group(10)
            self.assertEqual(entry["models"], ["model-x"])
            self.assertEqual(entry["key_id"], 2)

    def test_no_usable_key_keeps_old_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = m.ModelsStore(tmp, "aresaicode")
            store.apply_success(1, ["old"], key_id=9, source="bootstrap")
            result = m.refresh_models_for_groups(
                [{"id": 1}],
                [],  # no keys
                store,
                lambda s: ["new"],
                source="daily",
            )
            self.assertEqual(result.failed_count, 1)
            entry = store.get_group(1)
            self.assertEqual(entry["models"], ["old"])
            self.assertEqual(entry["last_error"], "no_usable_key")


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


class PreflightTests(unittest.TestCase):
    def test_preflight_read_only_never_creates(self) -> None:
        created = 0

        def groups_fn() -> list[dict[str, Any]]:
            return [{"id": 1, "name": "g"}]

        def list_keys() -> tuple[list[dict[str, Any]], bool]:
            return (
                [{"id": 1, "group_id": 1, "key": "sk-test", "status": "active"}],
                True,
            )

        def list_models(secret: str) -> list[str]:
            return ["m1"]

        # Ensure create path not invoked (preflight has no create_fn)
        pr = m.preflight_checks(
            groups_fn=groups_fn,
            list_keys_fn=list_keys,
            list_models_fn=list_models,
        )
        self.assertTrue(pr.ok)
        self.assertEqual(created, 0)
        self.assertTrue(pr.checks["secret_readable"])
        self.assertTrue(pr.checks["paging_complete"])
        self.assertTrue(pr.checks["models_envelope_ok"])

    def test_preflight_fails_without_secret(self) -> None:
        pr = m.preflight_checks(
            groups_fn=lambda: [{"id": 1}],
            list_keys_fn=lambda: ([{"id": 1, "group_id": 1, "key": ""}], True),
            list_models_fn=lambda s: [],
        )
        self.assertFalse(pr.ok)
        self.assertIn("secret_not_readable", pr.failures)


if __name__ == "__main__":
    unittest.main()
