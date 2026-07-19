import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests

from aiapibank_monitor import GroupApiClient, GroupMonitor


class GroupApiClientTests(unittest.TestCase):
    def make_client(self) -> GroupApiClient:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return GroupApiClient(
            "https://example.test",
            "user@example.test",
            "secret",
            Path(temp_dir.name) / "token.json",
            site_name="TEST",
        )

    def test_login_and_groups_use_configured_contract(self) -> None:
        client = self.make_client()
        login_response = Mock(status_code=200)
        login_response.json.return_value = {
            "data": {"access_token": "opaque-token", "refresh_token": "refresh"}
        }
        groups_response = Mock(status_code=200)
        groups_response.json.return_value = {"data": [{"id": 1, "name": "group"}]}
        client.session.post = Mock(return_value=login_response)
        client.session.get = Mock(return_value=groups_response)

        self.assertEqual(client.get_groups(), [{"id": 1, "name": "group"}])
        client.session.get.assert_called_once_with(
            "https://example.test/api/v1/groups/available",
            headers={"Authorization": "Bearer opaque-token", "Connection": "close"},
            timeout=(10, 30),
        )
        state = json.loads(client.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["access_token"], "opaque-token")
        self.assertEqual(client.state_file.stat().st_mode & 0o777, 0o600)

    def test_unauthorized_response_triggers_one_relogin(self) -> None:
        client = self.make_client()
        client.access_token = "old-token"
        rejected = Mock(status_code=401)
        accepted = Mock(status_code=200)
        accepted.json.return_value = {"data": []}
        client.session.get = Mock(side_effect=[rejected, accepted])
        client.login = Mock(side_effect=lambda: setattr(client, "access_token", "new-token"))

        self.assertEqual(client.get_groups(), [])
        client.login.assert_called_once_with()

    def test_timeout_is_reported_without_relogin(self) -> None:
        client = self.make_client()
        client.access_token = "opaque-token"
        client.session.get = Mock(side_effect=requests.ReadTimeout("controlled timeout"))
        client.login = Mock()

        with self.assertRaisesRegex(RuntimeError, "groups request failed"):
            client.get_groups()
        client.login.assert_not_called()


class GroupMonitorTests(unittest.TestCase):
    def test_two_polls_update_latest_and_append_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = Mock()
            client.get_groups.side_effect = [
                [{"id": 1, "name": "first"}],
                [{"id": 2, "name": "second"}],
            ]
            monitor = GroupMonitor(client, Path(temp_dir))

            monitor.poll()
            monitor.poll()

            latest = json.loads(monitor.latest_file.read_text(encoding="utf-8"))
            history = monitor.history_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(latest["groups"], [{"id": 2, "name": "second"}])
            self.assertEqual(len(history), 2)


if __name__ == "__main__":
    unittest.main()
