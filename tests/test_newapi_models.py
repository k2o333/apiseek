"""Contract tests for New-API token coverage and model snapshots.

These tests drive the public functions in ``newapi_models``. Remote token and
model operations are replaced only at the injected callable boundary; coverage,
hydration, reconcile, refresh, and persistence decisions stay in shipped code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest
import requests

import newapi_models as m
from newapi_monitor import CollectError


def raw_token(
    token_id: int,
    group: str,
    *,
    name: str = "user-token",
    status: int = 1,
    expired_time: int = -1,
    model_limits_enabled: bool = False,
    allow_ips: str = "",
    unlimited_quota: bool = True,
    remain_quota: int = 0,
) -> dict[str, Any]:
    """Return a list-envelope token whose key is deliberately only a mask."""
    return {
        "id": token_id,
        "name": name,
        "group": group,
        "status": status,
        "expired_time": expired_time,
        "model_limits_enabled": model_limits_enabled,
        "model_limits": "" if not model_limits_enabled else "model-a",
        "allow_ips": allow_ips,
        "unlimited_quota": unlimited_quota,
        "remain_quota": remain_quota,
        "cross_group_retry": False,
        "key": "sk-abcd********wxyz",
    }


class FakeTokensBackend:
    """In-memory replacement for the four injected token API callables."""

    def __init__(self, tokens: list[dict[str, Any]] | None = None) -> None:
        self.tokens = [dict(token) for token in (tokens or [])]
        self.secrets: dict[str, str | BaseException] = {}
        self.list_calls = 0
        self.secret_calls: list[str] = []
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.create_effect: BaseException | None = None
        self.create_commits_before_error = False
        self.next_id = max((int(token["id"]) for token in self.tokens), default=0) + 1

    def list_tokens(self) -> tuple[list[dict[str, Any]], bool]:
        self.list_calls += 1
        return [dict(token) for token in self.tokens], True

    def get_secret(self, token_id: Any) -> str:
        normalized = str(token_id).strip()
        self.secret_calls.append(normalized)
        value = self.secrets[normalized]
        if isinstance(value, BaseException):
            raise value
        return value

    def _commit_create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        token_id = self.next_id
        self.next_id += 1
        created = raw_token(
            token_id,
            str(payload["group"]),
            name=str(payload["name"]),
            status=1,
            expired_time=-1,
            model_limits_enabled=False,
            allow_ips="",
        )
        self.tokens.append(created)
        self.secrets[str(token_id)] = f"plain-secret-{token_id}"
        return dict(created)

    def create_token(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        captured = dict(payload)
        self.create_calls.append(captured)
        created: dict[str, Any] | None = None
        if self.create_effect is None or self.create_commits_before_error:
            created = self._commit_create(captured)
        if self.create_effect is not None:
            raise self.create_effect
        assert created is not None
        return created

    def update_token(
        self,
        payload: Mapping[str, Any],
        *,
        status_only: bool = False,
    ) -> dict[str, Any]:
        captured = dict(payload)
        captured["_status_only"] = status_only
        self.update_calls.append(captured)
        token_id = str(captured["id"]).strip()
        for token in self.tokens:
            if str(token.get("id")).strip() != token_id:
                continue
            token.update({key: value for key, value in captured.items() if not key.startswith("_")})
            return dict(token)
        raise AssertionError(f"token {token_id} not found")


def hydrate(
    tokens: list[dict[str, Any]],
    secrets: Mapping[str, str],
) -> list[dict[str, Any]]:
    result = m.hydrate_tokens(
        tokens,
        get_token_secret_fn=lambda token_id: secrets[str(token_id).strip()],
    )
    return result.tokens


class TestPaginationFailClosed:
    def test_has_more_true_on_short_page_fetches_the_next_page(self) -> None:
        calls: list[tuple[int, int]] = []
        pages = {
            1: {
                "success": True,
                "data": {
                    "items": [raw_token(1, "a")],
                    "page": 1,
                    "page_size": 100,
                    "total": 2,
                    "has_more": True,
                },
            },
            2: {
                "success": True,
                "data": {
                    "items": [raw_token(2, "b")],
                    "page": 2,
                    "page_size": 100,
                    "total": 2,
                    "has_more": False,
                },
            },
        }

        def get_page(page: int, page_size: int) -> Mapping[str, Any]:
            calls.append((page, page_size))
            return pages[page]

        tokens, complete = m.list_tokens_all(get_page, page_size=100)

        assert complete is True
        assert [str(token["id"]) for token in tokens] == ["1", "2"]
        assert calls == [(1, 100), (2, 100)]

    def test_total_shortfall_is_incomplete_and_ensure_never_creates(self) -> None:
        page_calls: list[int] = []
        create_calls: list[Mapping[str, Any]] = []

        def get_page(page: int, page_size: int) -> Mapping[str, Any]:
            page_calls.append(page)
            return {
                "success": True,
                "data": {
                    "items": [raw_token(10, "other")],
                    "page": page,
                    "page_size": page_size,
                    "total": 9,
                    "has_more": False,
                },
            }

        def list_tokens() -> tuple[list[dict[str, Any]], bool]:
            return m.list_tokens_all(get_page, page_size=100)

        result = m.ensure_coverage(
            [{"id": "target", "name": "target"}],
            list_tokens_fn=list_tokens,
            get_token_secret_fn=lambda _token_id: "must-not-be-needed",
            create_token_fn=lambda payload: create_calls.append(payload) or {},
            update_token_fn=lambda _payload: pytest.fail("incomplete paging must not update"),
        )

        assert page_calls == [1]
        assert result.paging_incomplete is True
        assert result.created == 0
        assert create_calls == []

    def test_repeated_page_without_progress_hits_fail_closed_guard(self) -> None:
        calls: list[int] = []

        def repeated(page: int, _page_size: int) -> Mapping[str, Any]:
            calls.append(page)
            return {
                "success": True,
                "data": {
                    "items": [raw_token(1, "a"), raw_token(2, "b")],
                    "page": page,
                    "page_size": 2,
                    "total": 10,
                    "has_more": True,
                },
            }

        tokens, complete = m.list_tokens_all(repeated, page_size=2, max_pages=20)

        assert complete is False
        assert [str(token["id"]) for token in tokens] == ["1", "2"]
        assert len(calls) < 20

    def test_incomplete_relist_after_first_create_stops_later_creates(self) -> None:
        backend = FakeTokensBackend()
        list_calls = 0

        def list_tokens() -> tuple[list[dict[str, Any]], bool]:
            nonlocal list_calls
            list_calls += 1
            if list_calls == 1:
                return [], True
            return [dict(token) for token in backend.tokens], False

        result = m.ensure_coverage(
            [{"id": "first"}, {"id": "second"}],
            list_tokens_fn=list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert result.paging_incomplete is True
        assert [call["group"] for call in backend.create_calls] == ["first"]

    @pytest.mark.parametrize(
        "payload",
        [
            {"success": False, "message": "business failure", "data": {"items": []}},
            {"success": True, "data": {"items": [], "page": 1, "page_size": 100}},
            {"success": True, "data": {"items": {}, "page": 1, "page_size": 100, "total": 0}},
        ],
    )
    def test_invalid_or_business_failure_envelope_is_incomplete(
        self, payload: Mapping[str, Any]
    ) -> None:
        tokens, complete = m.list_tokens_all(lambda _page, _size: payload)

        assert tokens == []
        assert complete is False


class TestHydrationInventoryAndMissing:
    def test_masked_user_token_is_hydrated_before_missing_and_prevents_create(self) -> None:
        backend = FakeTokensBackend([raw_token(7, "  pro  ", name="hand-made")])
        backend.secrets["7"] = "plain-user-secret"

        result = m.ensure_coverage(
            [{"id": "pro", "name": "pro"}],
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert backend.secret_calls == ["7"]
        assert result.created == 0
        assert result.coverage_unknown == []
        assert m.token_secret(result.tokens[0]) == "plain-user-secret"
        assert backend.create_calls == []
        assert backend.update_calls == []

    def test_transient_secret_failure_marks_unknown_and_never_creates(self) -> None:
        backend = FakeTokensBackend([raw_token(8, "pro")])
        backend.secrets["8"] = requests.Timeout("secret read timed out")

        result = m.ensure_coverage(
            [{"id": "pro"}],
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert result.created == 0
        assert result.coverage_unknown == ["pro"]
        assert backend.create_calls == []
        assert backend.update_calls == []

    def test_secret_uncertainty_blocks_only_its_group(self) -> None:
        backend = FakeTokensBackend([raw_token(8, "unknown")])
        backend.secrets["8"] = requests.Timeout("secret read timed out")

        result = m.ensure_coverage(
            [{"id": "unknown"}, {"id": "missing"}],
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert result.coverage_unknown == ["unknown"]
        assert result.created == 1
        assert [call["group"] for call in backend.create_calls] == ["missing"]
        assert backend.update_calls == []
        assert m.inventory_suitable_tokens("missing", result.tokens)

    def test_only_inventory_suitable_tokens_are_candidates_and_sort_by_id(self) -> None:
        tokens = [
            raw_token(10, "g"),
            raw_token(2, "g"),
            raw_token(3, "g", status=2),
            raw_token(4, "g", expired_time=1),
            raw_token(5, "g", model_limits_enabled=True),
            raw_token(6, "g", allow_ips="192.0.2.4"),
            raw_token(7, "other"),
            raw_token(8, "g", unlimited_quota=False, remain_quota=0),
            raw_token(9, "g", unlimited_quota=False, remain_quota=1),
        ]
        hydrated = hydrate(tokens, {str(token["id"]): f"secret-{token['id']}" for token in tokens})

        candidates = m.inventory_suitable_tokens(" g ", hydrated, now=2)

        assert [str(token["id"]) for token in candidates] == ["2", "9", "10"]

    def test_managed_name_is_utf8_safe_deterministic_and_at_most_fifty_bytes(self) -> None:
        short = m.managed_token_name(" pro ")
        long_group = "超长分组" * 20
        first = m.managed_token_name(long_group)
        second = m.managed_token_name(long_group)

        assert short == "newapi-monitor:g:pro"
        assert first == second
        assert len(first.encode("utf-8")) <= 50
        assert first.startswith("newapi-monitor:g:")
        assert first != m.managed_token_name(long_group + "x")

    def test_limited_user_token_does_not_cover_group_or_get_updated(self) -> None:
        limited = raw_token(1, "g", name="user-owned", model_limits_enabled=True)
        backend = FakeTokensBackend([limited])
        backend.secrets["1"] = "limited-secret"

        result = m.ensure_coverage(
            [{"id": "g"}],
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert result.created == 1
        assert backend.update_calls == []
        assert len(backend.create_calls) == 1
        assert backend.create_calls[0]["group"] == "g"
        assert backend.create_calls[0]["name"] == m.managed_token_name("g")
        assert m.inventory_suitable_tokens("g", result.tokens)


class TestReconcile:
    def test_merely_disabled_managed_token_uses_status_only_put(self) -> None:
        managed = raw_token(
            4,
            "g",
            name=m.managed_token_name("g"),
            status=2,
        )
        backend = FakeTokensBackend([managed])
        backend.secrets["4"] = "managed-secret"
        hydrated_before = hydrate(backend.tokens, {"4": "managed-secret"})

        result = m.reconcile_token_for_group(
            "g",
            hydrated_before,
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert result.error is None
        assert result.updated is True
        assert backend.create_calls == []
        assert backend.update_calls == [
            {"id": 4, "status": 1, "_status_only": True},
        ]

    def test_duplicate_managed_names_repair_all_in_stable_id_order(self) -> None:
        name = m.managed_token_name("g")
        backend = FakeTokensBackend(
            [
                raw_token(10, "wrong", name=name),
                raw_token(2, "wrong", name=name),
            ]
        )
        backend.secrets.update({"10": "secret-10", "2": "secret-2"})
        hydrated_before = hydrate(backend.tokens, {"10": "secret-10", "2": "secret-2"})

        result = m.reconcile_token_for_group(
            "g",
            hydrated_before,
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert result.error is None
        assert backend.create_calls == []
        assert [call["id"] for call in backend.update_calls] == [2, 10]

    def test_disabled_wrong_group_managed_token_is_repaired_and_rehydrated(self) -> None:
        managed = raw_token(
            11,
            "wrong",
            name=m.managed_token_name("wanted"),
            status=2,
            expired_time=1,
            model_limits_enabled=True,
            allow_ips="203.0.113.10",
        )
        backend = FakeTokensBackend([managed])
        backend.secrets["11"] = "managed-secret"
        hydrated_before = hydrate(backend.tokens, {"11": "managed-secret"})

        result = m.reconcile_token_for_group(
            " wanted ",
            hydrated_before,
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert result.error is None
        assert result.created is False
        assert result.updated is True
        assert backend.create_calls == []
        assert len(backend.update_calls) == 2
        repair, enable = backend.update_calls
        assert repair["id"] == 11
        assert repair["group"] == "wanted"
        assert repair["expired_time"] == -1
        assert repair["remain_quota"] == 0
        assert repair["unlimited_quota"] is True
        assert repair["model_limits_enabled"] is False
        assert repair["model_limits"] == ""
        assert repair["allow_ips"] == ""
        assert repair["cross_group_retry"] is False
        assert repair["_status_only"] is False
        assert "status" not in repair
        assert enable == {"id": 11, "status": 1, "_status_only": True}
        assert m.token_secret(result.tokens_after[0]) == "managed-secret"
        assert m.inventory_suitable_tokens("wanted", result.tokens_after)

    def test_repair_fails_closed_when_rehydrated_secret_is_still_unreadable(self) -> None:
        managed = raw_token(
            12,
            "wrong",
            name=m.managed_token_name("wanted"),
            status=2,
        )
        backend = FakeTokensBackend([managed])
        backend.secrets["12"] = requests.Timeout("secret unavailable")
        hydrated_before = m.hydrate_tokens(
            backend.tokens,
            get_token_secret_fn=backend.get_secret,
        ).tokens

        result = m.reconcile_token_for_group(
            "wanted",
            hydrated_before,
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert result.created is False
        assert result.updated is True
        assert result.error is not None
        assert len(backend.create_calls) == 0
        assert len(backend.update_calls) == 2
        assert m.inventory_suitable_tokens("wanted", result.tokens_after) == []

    def test_unknown_post_outcome_relists_and_claims_without_second_post(self) -> None:
        backend = FakeTokensBackend()
        backend.create_effect = requests.Timeout("response lost")
        backend.create_commits_before_error = True

        result = m.reconcile_token_for_group(
            "g",
            [],
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert result.error is None
        assert result.created is False
        assert result.updated is False
        assert len(backend.create_calls) == 1
        assert backend.list_calls >= 1
        assert m.inventory_suitable_tokens("g", result.tokens_after)
        assert m.token_secret(result.tokens_after[0])

    def test_create_success_returns_relisted_hydrated_tokens_after(self) -> None:
        backend = FakeTokensBackend()

        result = m.reconcile_token_for_group(
            "g",
            [],
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert result.error is None
        assert result.created is True
        assert len(result.tokens_after) == 1
        assert m.token_secret(result.tokens_after[0]) == "plain-secret-1"
        assert m.inventory_suitable_tokens("g", result.tokens_after)

    def test_duplicate_exact_managed_tokens_are_all_repaired(self) -> None:
        first = raw_token(1, "g", name=m.managed_token_name("g"), status=2)
        second = raw_token(
            2,
            "wrong",
            name=m.managed_token_name("g"),
            model_limits_enabled=True,
        )
        backend = FakeTokensBackend([first, second])
        backend.secrets.update({"1": "secret-1", "2": "secret-2"})

        result = m.reconcile_token_for_group(
            "g",
            hydrate([first, second], backend.secrets),
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=backend.update_token,
        )

        assert result.error is None
        assert backend.create_calls == []
        assert {call["id"] for call in backend.update_calls} == {1, 2}
        assert len(m.inventory_suitable_tokens("g", result.tokens_after)) == 2

    def test_lost_put_response_converges_after_relist_verification(self) -> None:
        managed = raw_token(4, "g", name=m.managed_token_name("g"), status=2)
        backend = FakeTokensBackend([managed])
        backend.secrets["4"] = "managed-secret"

        def update_then_timeout(
            payload: Mapping[str, Any], *, status_only: bool = False
        ) -> Mapping[str, Any]:
            backend.update_token(payload, status_only=status_only)
            raise requests.Timeout("response lost after commit")

        result = m.reconcile_token_for_group(
            "g",
            hydrate([managed], {"4": "managed-secret"}),
            list_tokens_fn=backend.list_tokens,
            get_token_secret_fn=backend.get_secret,
            create_token_fn=backend.create_token,
            update_token_fn=update_then_timeout,
        )

        assert result.error is None
        assert result.updated is True
        assert m.inventory_suitable_tokens("g", result.tokens_after)


class TestModelsStore:
    def test_null_empty_and_failure_preserves_last_success(self, tmp_path: Path) -> None:
        store = m.ModelsStore(tmp_path, "torchai")

        assert store.get_group("g")["models"] is None
        store.apply_failure("g", "no_usable_key", error_kind="no_usable_key")
        assert store.get_group("g")["models"] is None

        store.apply_success("g", [], key_id=1, source="bootstrap")
        assert store.get_group("g")["models"] == []

        store.apply_success("g", [" z ", "a", "z", ""], key_id=2, source="refresh")
        successful = store.get_group("g")
        assert successful["models"] == ["a", "z"]
        success_fields = {
            field: successful[field]
            for field in ("models", "content_hash", "last_success_at", "key_id")
        }

        store.apply_failure("g", "HTTP 503", error_kind="server", source="refresh")
        failed = store.get_group("g")
        assert {field: failed[field] for field in success_fields} == success_fields
        assert failed["last_error"] == "HTTP 503"

    def test_each_group_is_checkpointed_and_only_two_models_files_exist(self, tmp_path: Path) -> None:
        store = m.ModelsStore(tmp_path, "torchai")
        store.apply_success("one", ["m1"], key_id=1, source="bootstrap")

        first_checkpoint = json.loads((tmp_path / "models_latest.json").read_text())
        assert first_checkpoint["models_by_group"]["one"]["models"] == ["m1"]

        store.apply_failure("two", "timeout", error_kind="timeout", source="bootstrap")
        second_checkpoint = json.loads((tmp_path / "models_latest.json").read_text())
        assert second_checkpoint["models_by_group"]["one"]["models"] == ["m1"]
        assert second_checkpoint["models_by_group"]["two"]["models"] is None
        assert {path.name for path in tmp_path.iterdir()} == {
            "models_latest.json",
            "models_events.jsonl",
        }

    def test_full_meta_includes_skipped_and_bootstrap_only_on_complete_success(
        self, tmp_path: Path
    ) -> None:
        store = m.ModelsStore(tmp_path, "torchai")
        store.update_full_meta(target=3, ok=1, failed=1, skipped=1, bootstrap=True)

        partial = store.load()
        assert partial["last_full_result"] == {
            "target": 3,
            "ok": 1,
            "failed": 1,
            "skipped": 1,
        }
        assert partial["bootstrap_completed_at"] is None
        assert partial["last_full_success_at"] is None

        store.update_full_meta(target=2, ok=2, failed=0, skipped=0, bootstrap=True)
        complete = store.load()
        assert complete["bootstrap_completed_at"] is not None
        assert complete["last_full_success_at"] is not None

    def test_snapshot_and_events_never_contain_hydrated_secret(self, tmp_path: Path) -> None:
        store = m.ModelsStore(tmp_path, "torchai")
        secret = "plain-secret-must-not-be-written"
        store.apply_success("g", ["model-a"], key_id=4, source="bootstrap")

        written = "\n".join(path.read_text() for path in tmp_path.iterdir())
        assert secret not in written

    @pytest.mark.parametrize(
        "record",
        [
            {"schema_version": 1, "site_id": "torchai", "bootstrap_completed_at": True},
            {
                "schema_version": 1,
                "site_id": "other-site",
                "bootstrap_completed_at": "2030-01-01T00:00:00Z",
            },
        ],
    )
    def test_invalid_bootstrap_metadata_is_not_authoritative(
        self, tmp_path: Path, record: Mapping[str, Any]
    ) -> None:
        (tmp_path / "models_latest.json").write_text(json.dumps(record), encoding="utf-8")
        store = m.ModelsStore(tmp_path, "torchai")

        assert store.load()["bootstrap_completed_at"] is None

    def test_should_attempt_respects_retry_cooldown(self) -> None:
        assert m.should_attempt_now({"next_retry_at": None}) is True
        assert m.should_attempt_now({"next_retry_at": "2000-01-01T00:00:00Z"}) is True
        assert m.should_attempt_now({"next_retry_at": "2099-01-01T00:00:00Z"}) is False


class TestModelsEnvelope:
    def test_empty_and_duplicate_model_ids_are_valid_and_normalized(self) -> None:
        assert m.parse_models_payload({"data": []}) == []
        assert m.parse_models_payload(
            {"data": [{"id": " z "}, {"id": "a"}, {"id": "z"}, {"id": ""}]}
        ) == ["a", "z"]

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {},
            {"data": {}},
            {"data": ["model-a"]},
            {"data": [{"name": "model-a"}]},
            {"data": [{"id": None}]},
        ],
    )
    def test_unrecognized_or_partial_envelope_is_contract_failure(self, payload: Any) -> None:
        with pytest.raises(ValueError):
            m.parse_models_payload(payload)

    def test_model_failure_text_cannot_persist_active_secret(self, tmp_path: Path) -> None:
        store = m.ModelsStore(tmp_path, "torchai")
        secret = "plain-secret-must-not-be-written"
        tokens = hydrate([raw_token(4, "g")], {"4": secret})

        result = m.refresh_models_for_groups(
            [{"id": "g"}],
            tokens,
            store,
            lambda value: (_ for _ in ()).throw(RuntimeError(f"failed bearer {value}")),
        )

        assert result.failed_count == 1
        written = "\n".join(path.read_text() for path in tmp_path.iterdir())
        assert secret not in written
        assert "[REDACTED]" in written


class TestRefreshModels:
    def test_blocked_group_records_precise_reason_without_models_call(
        self, tmp_path: Path
    ) -> None:
        store = m.ModelsStore(tmp_path, "torchai")
        calls: list[str] = []

        result = m.refresh_models_for_groups(
            [{"id": "g"}],
            [],
            store,
            lambda secret: calls.append(secret) or [],
            blocked_groups={"g": "coverage_unknown"},
        )

        assert result.failed_count == 1
        assert calls == []
        assert store.get_group("g")["last_error"] == "coverage_unknown"

    def test_limited_token_is_not_used_as_inventory_and_old_models_survive(self, tmp_path: Path) -> None:
        store = m.ModelsStore(tmp_path, "torchai")
        store.apply_success("g", ["old"], key_id=99, source="bootstrap")
        tokens = hydrate(
            [raw_token(1, "g", model_limits_enabled=True)],
            {"1": "limited-secret"},
        )
        model_calls: list[str] = []

        result = m.refresh_models_for_groups(
            [{"id": "g"}],
            tokens,
            store,
            lambda secret: model_calls.append(secret) or ["restricted-subset"],
            source="refresh",
        )

        assert result.ok_count == 0
        assert result.failed_count == 1
        assert model_calls == []
        assert store.get_group("g")["models"] == ["old"]
        assert store.get_group("g")["last_error"] == "no_usable_key"

    def test_key_auth_tries_next_suitable_token_and_checkpoints_success(self, tmp_path: Path) -> None:
        store = m.ModelsStore(tmp_path, "torchai")
        tokens = hydrate(
            [raw_token(10, "g"), raw_token(2, "g")],
            {"10": "good-secret", "2": "bad-secret"},
        )
        calls: list[str] = []

        def list_models(secret: str) -> list[str]:
            calls.append(secret)
            if secret == "bad-secret":
                raise CollectError("models HTTP 401", kind="key_auth", status_code=401)
            return [" z ", "a", "z"]

        result = m.refresh_models_for_groups(
            [{"id": "g"}],
            tokens,
            store,
            list_models,
            source="bootstrap",
        )

        assert result.ok_count == 1
        assert result.failed_count == 0
        assert calls == ["bad-secret", "good-secret"]
        assert store.get_group("g")["models"] == ["a", "z"]
        assert store.get_group("g")["key_id"] == 10
        checkpoint = json.loads((tmp_path / "models_latest.json").read_text())
        assert checkpoint["models_by_group"]["g"]["models"] == ["a", "z"]

    def test_deadline_stops_starting_groups_and_reports_all_as_skipped(self, tmp_path: Path) -> None:
        store = m.ModelsStore(tmp_path, "torchai")
        tokens = hydrate(
            [raw_token(1, "a"), raw_token(2, "b"), raw_token(3, "c")],
            {"1": "sa", "2": "sb", "3": "sc"},
        )
        model_calls: list[str] = []

        result = m.refresh_models_for_groups(
            [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            tokens,
            store,
            lambda secret: model_calls.append(secret) or ["model"],
            source="refresh",
            deadline=10.0,
            time_fn=lambda: 10.0,
        )

        assert result.ok_count == 0
        assert result.failed_count == 0
        assert result.skipped_count == 3
        assert result.target_count == 3
        assert model_calls == []
        assert not (tmp_path / "models_latest.json").exists()

    def test_rate_limit_preserves_retry_after_timestamp(self, tmp_path: Path) -> None:
        store = m.ModelsStore(tmp_path, "torchai")
        tokens = hydrate([raw_token(1, "g")], {"1": "secret"})
        retry_at = "2030-01-02T03:04:05Z"

        def limited(_secret: str) -> list[str]:
            raise CollectError(
                "models HTTP 429",
                kind="rate_limit",
                status_code=429,
                next_retry_at=retry_at,
            )

        result = m.refresh_models_for_groups(
            [{"id": "g"}], tokens, store, limited, source="refresh"
        )

        assert result.failed_count == 1
        assert store.get_group("g")["next_retry_at"] == retry_at
