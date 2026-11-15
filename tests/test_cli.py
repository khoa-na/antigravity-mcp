"""Offline tests for the skill/CLI entry point, the cross-process lock and the hooks."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agy_agent
import agy_hooks

ROOT = Path(__file__).resolve().parent.parent


def run_cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = agy_agent.main(list(argv))
    return code, out.getvalue()


class CliTests(unittest.TestCase):
    def test_json_success_uses_shared_contract(self):
        with mock.patch.object(agy_agent, "ask", return_value="done") as ask:
            code, out = run_cli("--json", "--timeout", "20", "--workspace", "none", "do it")
        result = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["verification"], "not_run_by_wrapper")
        self.assertEqual(result["response"], "done")
        self.assertIsNone(result["conversation_id"])
        self.assertEqual(ask.call_args.kwargs["timeout"], 20)
        self.assertIsNone(ask.call_args.kwargs["workspace"])
        # Same keys, in the same order, as the MCP tool's record.
        self.assertEqual(list(result), list(agy_agent.build_result(
            status="", task_id="", conversation_id=None, resume=False, workspace=None,
            model=None, exit_code=0, elapsed_seconds=0, response="", error=None)))

    def test_workspace_defaults_to_current_directory(self):
        with mock.patch.object(agy_agent, "ask", return_value="done") as ask:
            run_cli("--json", "task")
        self.assertEqual(ask.call_args.kwargs["workspace"], str(Path.cwd().resolve()))

    def test_failure_statuses_set_exit_code_and_keep_partial_output(self):
        for status in ("partial", "failed", "timed_out", "busy"):
            error = agy_agent.AgentError("boom", status=status, exit_code=3, output="half")
            with self.subTest(status=status), mock.patch.object(agy_agent, "ask", side_effect=error):
                code, out = run_cli("--json", "--workspace", "none", "task")
            result = json.loads(out)
            self.assertEqual(code, 1)
            self.assertEqual((result["status"], result["exit_code"], result["response"], result["error"]),
                             (status, 3, "half", "boom"))

    def test_unexpected_exception_is_a_failed_record(self):
        with mock.patch.object(agy_agent, "ask", side_effect=OSError("disk")):
            code, out = run_cli("--json", "--workspace", "none", "task")
        self.assertEqual((code, json.loads(out)["status"]), (1, "failed"))

    def test_text_mode_prints_answer_only(self):
        with mock.patch.object(agy_agent, "ask", return_value="plain answer"):
            code, out = run_cli("--workspace", "none", "task")
        self.assertEqual((code, out.strip()), (0, "plain answer"))

    def test_resume_and_conversation_are_forwarded_and_exclusive(self):
        with mock.patch.object(agy_agent, "ask", return_value="ok") as ask:
            run_cli("--resume", "--workspace", "none", "task")
            self.assertTrue(ask.call_args.kwargs["resume"])
            run_cli("--conversation", "abc", "--workspace", "none", "task")
            self.assertEqual(ask.call_args.kwargs["conversation_id"], "abc")
        with mock.patch.object(agy_agent, "ask") as ask, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                agy_agent.main(["--resume", "--conversation", "abc", "task"])
        self.assertEqual(caught.exception.code, 2)
        ask.assert_not_called()

    def test_invalid_input_exits_2_before_running(self):
        bad = [["--timeout", "0", "t"], ["--timeout", "nan", "t"], ["--timeout", "86401", "t"],
               ["--workspace", str(ROOT / "does-not-exist"), "t"], ["--prompt-file", str(ROOT / "nope.md")],
               ["  "], ["--prompt", "a", "b"]]
        for argv in bad:
            with self.subTest(argv=argv), mock.patch.object(agy_agent, "ask") as ask, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    agy_agent.main(argv)
                self.assertEqual(caught.exception.code, 2)
                ask.assert_not_called()

    def test_prompt_file_strips_bom(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(agy_agent, "ask", return_value="ok") as ask:
            path = Path(tmp, "p.md")
            path.write_text("﻿hello", encoding="utf-8")
            run_cli("--prompt-file", str(path), "--workspace", "none")
        self.assertEqual(ask.call_args.args[0], "hello")


class ProcessLockTests(unittest.TestCase):
    def test_second_process_is_rejected_with_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = os.path.join(tmp, "agent.lock")
            env = {**os.environ, "AGY_LOCK_FILE": lock, "PYTHONPATH": str(ROOT)}
            holder = subprocess.Popen(
                [sys.executable, "-c",
                 "import sys, agy_agent as a; h = a._acquire_process_lock(); "
                 "print('held' if h else 'none', flush=True); sys.stdin.read()"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env, cwd=tmp)
            try:
                self.assertEqual(holder.stdout.readline().strip(), "held")
                with mock.patch.dict(os.environ, {"AGY_LOCK_FILE": lock}):
                    with self.assertRaises(agy_agent.AgentError) as caught:
                        agy_agent._acquire_process_lock()
                self.assertEqual(caught.exception.status, "busy")
            finally:
                holder.stdin.close()
                holder.wait(10)
                holder.stdout.close()
            # Once the holder exits, the lock is free again.
            with mock.patch.dict(os.environ, {"AGY_LOCK_FILE": lock}):
                handle = agy_agent._acquire_process_lock()
                self.assertIsNotNone(handle)
                agy_agent._release_process_lock(handle)

    def test_nested_calls_reuse_the_lock_and_release_it(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"AGY_LOCK_FILE": os.path.join(tmp, "agent.lock")}), \
             mock.patch.object(agy_agent, "_HAS_ROTATION", False), \
             mock.patch.object(agy_agent, "_acquire_process_lock", wraps=agy_agent._acquire_process_lock) as acquire, \
             mock.patch.object(agy_agent, "run_agent", wraps=None, return_value=("done", "", "", 0)):
            self.assertEqual(agy_agent.ask("task"), "done")
            acquire.assert_called_once()
            self.assertEqual(agy_agent._PROC_LOCK_DEPTH, 0)
            self.assertIsNone(agy_agent._PROC_LOCK_HANDLE)


class HookTests(unittest.TestCase):
    @staticmethod
    def event(command, **extra):
        return {"tool_name": "Bash", "tool_input": {"command": command, **extra}, "cwd": str(ROOT)}

    def test_pre_blocks_calls_missing_timeout_or_json(self):
        for command in ("antigravity-agent 'x'", "antigravity-agent --json 'x'",
                        "antigravity-agent --timeout 60 'x'", "cd repo && antigravity-agent -w . x"):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(agy_hooks.pre(self.event(command)), 2)

    def test_pre_allows_complete_help_and_unrelated_calls(self):
        for command in ("antigravity-agent --timeout 600 --json --prompt-file p.md",
                        "antigravity-agent --help", "git status", "echo antigravity-agent-hook",
                        "pip install -e ."):
            with self.subTest(command=command):
                self.assertEqual(agy_hooks.pre(self.event(command)), 0)

    def test_post_background_run_does_not_claim_completion(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = agy_hooks.post(self.event("antigravity-agent --timeout 9 --json x", run_in_background=True))
        context = json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(code, 0)
        self.assertIn("NOT finished", context)

    def test_post_foreground_summarizes_tree_and_warns_unverified(self):
        out = io.StringIO()
        with mock.patch.object(agy_hooks, "_git", side_effect=[" M app.py", " app.py | 2 +-"]), \
             contextlib.redirect_stdout(out):
            agy_hooks.post(self.event("antigravity-agent --timeout 9 --json x"))
        payload = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(payload["hookEventName"], "PostToolUse")
        self.assertIn("M app.py", payload["additionalContext"])
        self.assertIn("NOT verification", payload["additionalContext"])

    def test_post_ignores_other_commands_and_main_rejects_bad_usage(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(agy_hooks.post(self.event("ls")), 0)
        self.assertEqual(out.getvalue(), "")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(agy_hooks.main(["bogus"]), 2)


if __name__ == "__main__":
    unittest.main()
