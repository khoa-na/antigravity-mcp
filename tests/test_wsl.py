"""Offline Linux/WSL authentication and executable discovery regressions."""
import os
import unittest
from unittest import mock

import agy_agent
import agy_backend
import doctor


class WslTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_access_token_only_does_not_refresh(self):
        with mock.patch.dict(os.environ, {"AGY_ACCESS_TOKEN": "test-token"}), \
             mock.patch.object(agy_backend.requests, "post") as post:
            session = agy_backend.Session()
            session.ensure_fresh()
            self.assertEqual(session.token, "test-token")
            post.assert_not_called()

    def test_expired_access_token_fails_without_network(self):
        with mock.patch.dict(os.environ, {"AGY_ACCESS_TOKEN": "test-token",
                                         "AGY_TOKEN_EXPIRY": "2000-01-01T00:00:00+00:00"}), \
             mock.patch.object(agy_backend.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "expired"):
                agy_backend.Session().ensure_fresh()
            post.assert_not_called()

    def test_refresh_without_refresh_token_is_actionable(self):
        with mock.patch.dict(os.environ, {"AGY_ACCESS_TOKEN": "test-token"}), \
             mock.patch.object(agy_backend.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "AGY_REFRESH_TOKEN"):
                agy_backend.Session().refresh()
            post.assert_not_called()

    def test_refresh_is_reused_by_next_session(self):
        response = mock.Mock(ok=True)
        response.json.return_value = {"access_token": "refreshed", "expires_in": 3600}
        with mock.patch.dict(os.environ, {"AGY_ACCESS_TOKEN": "old", "AGY_REFRESH_TOKEN": "refresh"}), \
             mock.patch.object(agy_backend.requests, "post", return_value=response) as post, \
             mock.patch.object(agy_backend, "_advapi") as windows:
            agy_backend.Session().ensure_fresh()
            second = agy_backend.Session()
            second.ensure_fresh()
            self.assertEqual(second.token, "refreshed")
            post.assert_called_once()
            windows.CredWriteW.assert_not_called()

    def test_missing_linux_auth_explains_separate_backend(self):
        with mock.patch.object(agy_backend, "_advapi", None):
            with self.assertRaisesRegex(OSError, "native agy CLI login is separate"):
                agy_backend.Session()

    def test_linux_executable_discovery(self):
        with mock.patch.object(agy_agent.sys, "platform", "linux"), \
             mock.patch.object(agy_agent.shutil, "which", return_value="/usr/bin/agy"):
            self.assertEqual(agy_agent._default_agy_exe(), "/usr/bin/agy")

    def test_missing_linux_executable_has_no_windows_fallback(self):
        with mock.patch.object(agy_agent.sys, "platform", "linux"), \
             mock.patch.object(agy_agent.shutil, "which", return_value=None):
            self.assertEqual(agy_agent._default_agy_exe(), "agy")

    def test_wsl_ignores_inherited_windows_localappdata(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "/mnt/c/example"}), \
             mock.patch.object(agy_agent.sys, "platform", "linux"), \
             mock.patch.object(agy_agent.os.path, "exists", return_value=True), \
             mock.patch.object(agy_agent.shutil, "which", return_value="/usr/bin/agy"):
            self.assertEqual(agy_agent._default_agy_exe(), "/usr/bin/agy")

    def test_doctor_never_discloses_tokens(self):
        with mock.patch.dict(os.environ, {"AGY_ACCESS_TOKEN": "private-access-value",
                                         "AGY_REFRESH_TOKEN": "private-refresh-value"}):
            report = doctor.diagnose()
            self.assertEqual(report["backend_auth_source"], "environment")
            self.assertNotIn("private-access-value", str(report))
            self.assertNotIn("private-refresh-value", str(report))


if __name__ == "__main__":
    unittest.main()
