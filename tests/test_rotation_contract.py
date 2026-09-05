import importlib.util
import unittest
from pathlib import Path
from unittest import mock


BACKEND = Path(__file__).resolve().parents[1] / "agy_backend.py"
SPEC = importlib.util.spec_from_file_location("agy_backend", BACKEND)
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


class BackendContractTests(unittest.TestCase):
    def test_swap_rejects_zero_rc_without_active_change(self):
        proc = mock.Mock(returncode=0, stdout="Switched", stderr="")
        with mock.patch.object(backend.os.path, "exists", return_value=True), \
             mock.patch.object(backend.subprocess, "run", return_value=proc), \
             mock.patch.object(backend, "_active_email", side_effect=["a@example.com", "a@example.com"]):
            self.assertFalse(backend.swap_account())

    def test_swap_accepts_zero_rc_with_active_change(self):
        proc = mock.Mock(returncode=0, stdout="Switched", stderr="")
        with mock.patch.object(backend.os.path, "exists", return_value=True), \
             mock.patch.object(backend.subprocess, "run", return_value=proc), \
             mock.patch.object(backend, "_active_email", side_effect=["a@example.com", "b@example.com"]):
            self.assertTrue(backend.swap_account())

    def test_swap_rejects_nonzero_rc(self):
        proc = mock.Mock(returncode=1, stdout="", stderr="failed")
        with mock.patch.object(backend.os.path, "exists", return_value=True), \
             mock.patch.object(backend.subprocess, "run", return_value=proc), \
             mock.patch.object(backend, "_active_email", return_value="a@example.com"):
            self.assertFalse(backend.swap_account())


if __name__ == "__main__":
    unittest.main()
