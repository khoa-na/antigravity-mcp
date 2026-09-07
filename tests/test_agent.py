import unittest
import tempfile
import threading
from unittest import mock
import agy_agent


class AgentTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(agy_agent, "_HAS_ROTATION", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_partial_output_is_not_success(self):
        with mock.patch.object(agy_agent, "run_agent", return_value=("half done", "", "failed", 1)) as run:
            with self.assertRaises(RuntimeError):
                agy_agent.ask("edit code")
        run.assert_called_once()

    def test_success_preserves_text_api(self):
        with mock.patch.object(agy_agent, "run_agent", return_value=("done", "", "", 0)):
            self.assertEqual(agy_agent.ask("edit code"), "done")

    def test_profile_does_not_change_between_calls(self):
        with tempfile.TemporaryDirectory() as profile, mock.patch.object(agy_agent, "PROFILE", profile):
            first = agy_agent._env("worker1")
            second = agy_agent._env("worker2")
        self.assertEqual(first["USERPROFILE"], second["USERPROFILE"])

    def test_partial_error_preserves_diagnostics(self):
        with mock.patch.object(agy_agent, "run_agent", return_value=("half done", "", "failure", 2)):
            with self.assertRaises(agy_agent.AgentError) as caught:
                agy_agent.ask("task")
        self.assertEqual(caught.exception.status, "partial")
        self.assertEqual(caught.exception.output, "half done")
        self.assertEqual(caught.exception.exit_code, 2)

    def test_timeout_has_explicit_status(self):
        with mock.patch.object(agy_agent, "run_agent", return_value=("", "", "timeout", -1)):
            with self.assertRaises(agy_agent.AgentError) as caught:
                agy_agent.ask("task")
        self.assertEqual(caught.exception.status, "timed_out")

    def test_deadline_prevents_launch_after_preflight(self):
        with mock.patch.object(agy_agent.time, "monotonic", side_effect=[0, 11]), \
             mock.patch.object(agy_agent, "run_agent") as run:
            with self.assertRaises(agy_agent.AgentError) as caught:
                agy_agent.ask("task", timeout=10)
        self.assertEqual(caught.exception.status, "timed_out")
        run.assert_not_called()

    def test_retry_receives_remaining_budget(self):
        with mock.patch.object(agy_agent, "_HAS_ROTATION", True), \
             mock.patch.object(agy_agent, "load_config", return_value={"enabled": False}), \
             mock.patch.object(agy_agent, "swap_account", return_value=True), \
             mock.patch.object(agy_agent.time, "sleep"), \
             mock.patch.object(agy_agent.time, "monotonic", side_effect=[0, 2, 8]), \
             mock.patch.object(agy_agent, "run_agent", side_effect=[("", "", "429", 1), ("done", "", "", 0)]) as run:
            self.assertEqual(agy_agent.ask("task", timeout=10), "done")
        self.assertEqual([call.kwargs["timeout"] for call in run.call_args_list], [8, 2])

    def test_busy_task_rejected_and_lock_released_after_failure(self):
        entered, release = threading.Event(), threading.Event()
        def worker(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test worker not released")
            return "done", "", "", 0
        with mock.patch.object(agy_agent, "run_agent", side_effect=worker):
            thread = threading.Thread(target=agy_agent.ask, args=("first",))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(agy_agent.AgentError) as caught:
                    agy_agent.ask("second")
                self.assertEqual(caught.exception.status, "busy")
            finally:
                release.set()
                thread.join(5)
        with mock.patch.object(agy_agent, "run_agent", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                agy_agent.ask("fail")
        # A different thread must be able to acquire the lock after failure.
        available = []
        def check_lock():
            acquired = agy_agent._AGENT_LOCK.acquire(blocking=False)
            available.append(acquired)
            if acquired:
                agy_agent._AGENT_LOCK.release()
        checker = threading.Thread(target=check_lock)
        checker.start()
        checker.join(2)
        self.assertEqual(available, [True])

    def test_stdout_logs_are_not_an_answer(self):
        proc = mock.Mock(returncode=0, stdout=b"CLI initialized", stderr=b"")
        with mock.patch.object(agy_agent.subprocess, "run", return_value=proc):
            result, out, err, rc = agy_agent.run_agent("task")
        self.assertEqual(result, "")
        self.assertEqual(out, "CLI initialized")

    def test_cli_only_receives_existing_conversation_id(self):
        proc = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with mock.patch.object(agy_agent.subprocess, "run", return_value=proc) as run:
            agy_agent.run_agent("task", conversation_id="existing")
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--conversation") + 1], "existing")
        self.assertNotIn("--continue", args)

    def test_print_timeout_tracks_outer_budget_with_exit_margin(self):
        proc = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        for budget, expected in ((900, "890s"), (600, "590s"), (30, "28.5s"),
                                 (2, "1.9s"), (0.001, "0.00095s")):
            with self.subTest(budget=budget), mock.patch.object(agy_agent.subprocess, "run", return_value=proc) as run:
                agy_agent.run_agent("unused offline prompt", timeout=budget)
                args = run.call_args.args[0]
                self.assertEqual(args.count("--print-timeout"), 1)
                self.assertEqual(args[args.index("--print-timeout") + 1], expected)
                self.assertEqual(run.call_args.kwargs["timeout"], budget)

    def test_cli_timeout_does_not_fall_back_to_no_outfile_error(self):
        with mock.patch.object(agy_agent, "run_agent", return_value=("", "", "Error: timeout waiting for response", 1)):
            with self.assertRaises(agy_agent.AgentError) as caught:
                agy_agent.ask("unused", timeout=600)
        self.assertEqual(caught.exception.status, "timed_out")
        self.assertEqual(caught.exception.exit_code, 1)
        self.assertIn("CLI print deadline", str(caught.exception))

    def test_retry_print_timeout_uses_remaining_not_original_budget(self):
        calls = []
        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                return mock.Mock(returncode=1, stdout=b"", stderr=b"429 quota exhausted")
            from pathlib import Path
            Path(kwargs["cwd"], "out.txt").write_text("done", encoding="utf-8")
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with mock.patch.object(agy_agent, "_HAS_ROTATION", True), \
             mock.patch.object(agy_agent, "load_config", return_value={"enabled": False}), \
             mock.patch.object(agy_agent, "swap_account", return_value=True), \
             mock.patch.object(agy_agent.time, "sleep"), \
             mock.patch.object(agy_agent.time, "monotonic", side_effect=[0, 10, 500]), \
             mock.patch.object(agy_agent.subprocess, "run", side_effect=fake_run):
            self.assertEqual(agy_agent.ask("unused", timeout=600), "done")
        self.assertEqual([args[args.index("--print-timeout") + 1] for args, _ in calls], ["580s", "95s"])
        self.assertEqual([kw["timeout"] for _, kw in calls], [590, 100])


if __name__ == "__main__":
    unittest.main()
