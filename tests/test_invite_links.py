"""Tests for invite_links storage + TTL / base_url refresh rules."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import invite_links as inv


def write_env(path: Path, mapping: dict[str, str], mode: int = 0o600) -> Path:
    path.write_text("\n".join(f"{k}={v}" for k, v in mapping.items()) + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def sample_record(**overrides: object) -> dict:
    base = {
        "schema_version": 1,
        "site_id": "pinaic",
        "backend": "sub2api",
        "base_url": "https://example.test",
        "aff_code": "ABC123",
        "invite_link": "https://example.test/register?aff=ABC123",
        "fetched_at": "2026-07-01T00:00:00Z",
        "checked_at": "2026-07-01T00:00:00Z",
        "ttl_seconds": inv.DEFAULT_TTL_SECONDS,
    }
    base.update(overrides)
    return base


class NormalizeAndBuildTests(unittest.TestCase):
    def test_normalize_strips_slash_and_lowercases_host(self) -> None:
        self.assertEqual(
            inv.normalize_base_url("https://Example.TEST/"),
            "https://example.test",
        )

    def test_normalize_rejects_path(self) -> None:
        with self.assertRaises(inv.ConfigError):
            inv.normalize_base_url("https://example.test/app")

    def test_build_invite_link(self) -> None:
        self.assertEqual(
            inv.build_invite_link("https://example.test", "XYZ"),
            "https://example.test/register?aff=XYZ",
        )

    def test_build_rejects_whitespace_code(self) -> None:
        with self.assertRaises(inv.InviteError):
            inv.build_invite_link("https://example.test", "A B")


class ValidateRecordTests(unittest.TestCase):
    def test_ok(self) -> None:
        rec = inv.validate_record(sample_record())
        self.assertEqual(rec["aff_code"], "ABC123")

    def test_rejects_wrong_invite_link(self) -> None:
        bad = sample_record(invite_link="https://evil.test/register?aff=ABC123")
        with self.assertRaises(inv.InviteError):
            inv.validate_record(bad)

    def test_rejects_site_mismatch(self) -> None:
        with self.assertRaises(inv.InviteError):
            inv.validate_record(sample_record(), expected_site_id="other")

    def test_rejects_unknown_schema(self) -> None:
        with self.assertRaises(inv.InviteError):
            inv.validate_record(sample_record(schema_version=99))


class NeedsRefreshTests(unittest.TestCase):
    def test_missing_fetches(self) -> None:
        should, reason = inv.needs_remote_refresh(None, base_url="https://example.test")
        self.assertTrue(should)
        self.assertEqual(reason, "missing_or_invalid")

    def test_ttl_ok_skips(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        rec = sample_record(checked_at="2026-07-09T00:00:00Z")
        should, reason = inv.needs_remote_refresh(
            inv.validate_record(rec),
            base_url="https://example.test",
            now=now,
        )
        self.assertFalse(should)
        self.assertEqual(reason, "ttl_ok")

    def test_ttl_expired_fetches(self) -> None:
        now = datetime(2026, 7, 20, tzinfo=timezone.utc)
        rec = sample_record(checked_at="2026-07-01T00:00:00Z")
        should, reason = inv.needs_remote_refresh(
            inv.validate_record(rec),
            base_url="https://example.test",
            now=now,
        )
        self.assertTrue(should)
        self.assertEqual(reason, "ttl_expired")

    def test_base_url_change_fetches(self) -> None:
        now = datetime(2026, 7, 2, tzinfo=timezone.utc)
        rec = sample_record(checked_at="2026-07-01T00:00:00Z")
        should, reason = inv.needs_remote_refresh(
            inv.validate_record(rec),
            base_url="https://other.test",
            now=now,
        )
        self.assertTrue(should)
        self.assertEqual(reason, "base_url_changed")

    def test_force_fetches(self) -> None:
        now = datetime(2026, 7, 2, tzinfo=timezone.utc)
        rec = sample_record(checked_at="2026-07-01T00:00:00Z")
        should, reason = inv.needs_remote_refresh(
            inv.validate_record(rec),
            base_url="https://example.test",
            force=True,
            now=now,
        )
        self.assertTrue(should)
        self.assertEqual(reason, "force")


class RunOnceTests(unittest.TestCase):
    def test_skip_when_ttl_ok_no_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            checked = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0)
            rec = sample_record(
                checked_at=checked.isoformat().replace("+00:00", "Z"),
                fetched_at=checked.isoformat().replace("+00:00", "Z"),
            )
            path = inv.invite_path(data)
            path.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")

            fetch = MagicMock(return_value="SHOULD_NOT_CALL")
            ctx = inv.SiteContext(
                site_id="pinaic",
                backend="sub2api",
                base_url="https://example.test",
                data_dir=data,
                ttl_seconds=inv.DEFAULT_TTL_SECONDS,
                env_file=Path("dummy.env"),
                fetch_aff_code=fetch,
            )
            result = inv.run_once(ctx, force=False)
            self.assertEqual(result["action"], "skip")
            fetch.assert_not_called()

    def test_base_url_change_updates_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            checked = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0)
            rec = sample_record(
                base_url="https://old.example.test",
                invite_link="https://old.example.test/register?aff=ABC123",
                checked_at=checked.isoformat().replace("+00:00", "Z"),
                fetched_at=checked.isoformat().replace("+00:00", "Z"),
            )
            inv.invite_path(data).write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")

            fetch = MagicMock(return_value="NEWCODE")
            ctx = inv.SiteContext(
                site_id="pinaic",
                backend="sub2api",
                base_url="https://example.test",
                data_dir=data,
                ttl_seconds=inv.DEFAULT_TTL_SECONDS,
                env_file=Path("dummy.env"),
                fetch_aff_code=fetch,
            )
            result = inv.run_once(ctx, force=False)
            self.assertEqual(result["action"], "updated")
            self.assertEqual(result["reason"], "base_url_changed")
            fetch.assert_called_once()
            saved = json.loads(inv.invite_path(data).read_text(encoding="utf-8"))
            self.assertEqual(saved["base_url"], "https://example.test")
            self.assertEqual(saved["aff_code"], "NEWCODE")
            self.assertEqual(saved["invite_link"], "https://example.test/register?aff=NEWCODE")

    def test_remote_failure_preserves_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            checked = datetime(2020, 1, 1, tzinfo=timezone.utc)  # expired
            rec = sample_record(
                checked_at=checked.isoformat().replace("+00:00", "Z"),
                fetched_at=checked.isoformat().replace("+00:00", "Z"),
            )
            path = inv.invite_path(data)
            path.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
            before = path.read_text(encoding="utf-8")

            def boom() -> str:
                raise inv.InviteError("upstream down", kind="server")

            ctx = inv.SiteContext(
                site_id="pinaic",
                backend="sub2api",
                base_url="https://example.test",
                data_dir=data,
                ttl_seconds=inv.DEFAULT_TTL_SECONDS,
                env_file=Path("dummy.env"),
                fetch_aff_code=boom,
            )
            with self.assertRaises(inv.InviteError):
                inv.run_once(ctx, force=True)
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_force_writes_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            fetch = MagicMock(return_value="ZZ99")
            ctx = inv.SiteContext(
                site_id="pinaic",
                backend="sub2api",
                base_url="https://example.test",
                data_dir=data,
                ttl_seconds=inv.DEFAULT_TTL_SECONDS,
                env_file=Path("dummy.env"),
                fetch_aff_code=fetch,
            )
            result = inv.run_once(ctx, force=True)
            self.assertEqual(result["action"], "updated")
            saved = json.loads(inv.invite_path(data).read_text(encoding="utf-8"))
            self.assertEqual(saved["invite_link"], "https://example.test/register?aff=ZZ99")
            mode = inv.invite_path(data).stat().st_mode & 0o777
            self.assertEqual(mode, 0o644)


class DetectBackendTests(unittest.TestCase):
    def test_sub2api_when_token_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = write_env(
                root / "pinaic.env",
                {
                    "MONITOR_SITE_ID": "pinaic",
                    "TOKEN_STATE_FILE": str(root / "token.json"),
                    "MONITOR_BASE_URL": "https://example.test",
                    "MONITOR_USERNAME": "u",
                    "MONITOR_PASSWORD": "p",
                },
            )
            self.assertEqual(inv.detect_backend(env), "sub2api")

    def test_newapi_when_no_token_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = write_env(
                root / "botcf.env",
                {
                    "MONITOR_SITE_ID": "botcf",
                    "MONITOR_BASE_URL": "https://botcf.example",
                    "MONITOR_USERNAME": "u",
                    "MONITOR_PASSWORD": "p",
                    "REQUIRE_NEW_API_USER_HEADER": "1",
                },
            )
            self.assertEqual(inv.detect_backend(env), "newapi")


class ParseAffHelpers(unittest.TestCase):
    """Exercise fetch helpers via run_once with mocked session paths."""

    def test_cli_validate_exit_2_on_missing_env(self) -> None:
        code = inv.main(["--env-file", "/no/such/env.file", "--validate"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
