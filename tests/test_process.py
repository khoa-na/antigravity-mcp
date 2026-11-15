"""Process-tree containment and conversation-ID discovery (real processes, no agy needed)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import agy_agent

ROOT = Path(__file__).resolve().parent.parent

# A "CLI" that starts a long-lived grandchild and records its PID.
TREE = (
    "import subprocess, sys, time\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "open(sys.argv[1], 'w').write(str(g.pid))\n"
    "time.sleep(120)\n"
)


def pid_alive(pid):
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return f" {pid} " in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def read_pid(path, seconds=15):
    assert wait_for(lambda: os.path.exists(path) and os.path.getsize(path) > 0, seconds), "grandchild never started"
    return int(Path(path).read_text())


class ConversationIdTests(unittest.TestCase):
    def test_parse_cli_json(self):
        cases = {
            '{"conversation_id":"abc-1","status":"SUCCESS"}': "abc-1",
            'log line\n{"conversation_id": " x "}\n': "x",
            '{\n  "conversation_id": "pretty"\n}': "pretty",
            "CLI initialized": None, "": None, None: None, "[1,2]": None,
            '{"conversation_id": 5}': None, '{"conversation_id": ""}': None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(agy_agent._cli_conversation_id(text), expected)

    def test_run_agent_requests_json_output(self):
        proc = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with mock.patch.object(agy_agent, "_run_cli", return_value=proc) as run:
            agy_agent.run_agent("task")
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--output-format") + 1], "json")

    def test_ask_returns_text_with_conversation_id(self):
        out = '{"conversation_id":"conv-9","status":"SUCCESS"}'
        with mock.patch.object(agy_agent, "_HAS_ROTATION", False), \
             mock.patch.object(agy_agent, "run_agent", return_value=("done", out, "", 0)):
            result = agy_agent.ask("task")
        self.assertEqual(result, "done")                 # still a plain-str answer
        self.assertEqual(result.conversation_id, "conv-9")

    def test_errors_keep_conversation_id(self):
        out = '{"conversation_id":"conv-7"}'
        cases = [("half", out, "boom", 2, "partial"), ("", out, "", 1, "failed"),
                 ("", out, "timeout waiting for response", 1, "timed_out"),
                 ("", out, "please login", 1, "failed")]
        for result, stdout, stderr, rc, status in cases:
            with self.subTest(status=status, stderr=stderr), \
                 mock.patch.object(agy_agent, "_HAS_ROTATION", False), \
                 mock.patch.object(agy_agent, "run_agent", return_value=(result, stdout, stderr, rc)):
                with self.assertRaises(agy_agent.AgentError) as caught:
                    agy_agent.ask("task")
            self.assertEqual((caught.exception.status, caught.exception.conversation_id), (status, "conv-7"))

    def test_cli_reports_discovered_conversation_id(self):
        found = agy_agent.AgentResult("done", "conv-3")
        out = io.StringIO()
        with mock.patch.object(agy_agent, "ask", return_value=found), contextlib.redirect_stdout(out):
            agy_agent.main(["--json", "--resume", "--workspace", "none", "task"])
        record = json.loads(out.getvalue())
        self.assertEqual((record["conversation_id"], record["resume_latest"]), ("conv-3", True))

    def test_explicit_conversation_id_is_echoed_when_cli_reports_none(self):
        out = io.StringIO()
        with mock.patch.object(agy_agent, "ask", return_value="done"), contextlib.redirect_stdout(out):
            agy_agent.main(["--json", "--conversation", "mine", "--workspace", "none", "task"])
        self.assertEqual(json.loads(out.getvalue())["conversation_id"], "mine")


class ProcessTreeTests(unittest.TestCase):
    def test_timeout_kills_grandchildren(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "pid")
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                agy_agent._run_cli([sys.executable, "-c", TREE, pidfile], timeout=5)
            self.assertLess(time.monotonic() - started, 40)
            pid = read_pid(pidfile)
            self.assertTrue(wait_for(lambda: not pid_alive(pid), 10), "grandchild survived the timeout")

    def test_clean_exit_returns_output_and_leaves_survivors_alone(self):
        script = (
            "import subprocess, sys\n"
            "D = subprocess.DEVNULL   # a detached helper: must not hold our stdout/stderr pipes\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], stdin=D, stdout=D, stderr=D)\n"
            "open(sys.argv[1], 'w').write(str(g.pid))\n"
            "print('hello')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "pid")
            done = agy_agent._run_cli([sys.executable, "-c", script, pidfile], input=b"", timeout=30)
            pid = read_pid(pidfile)
            try:
                self.assertEqual((done.returncode, done.stdout.strip()), (0, b"hello"))
                time.sleep(0.5)
                self.assertTrue(pid_alive(pid), "a helper the CLI left running on purpose was killed")
            finally:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"] if sys.platform == "win32"
                               else ["kill", "-9", str(pid)], capture_output=True)

    @unittest.skipUnless(sys.platform == "win32", "Job Object semantics are Windows-specific")
    def test_killing_the_parent_kills_the_tree(self):
        driver = (
            "import sys; sys.path.insert(0, sys.argv[2]); import agy_agent\n"
            "agy_agent._run_cli([sys.executable, '-c', sys.argv[3], sys.argv[1]], timeout=120)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = os.path.join(tmp, "pid")
            parent = subprocess.Popen([sys.executable, "-c", driver, pidfile, str(ROOT), TREE])
            try:
                pid = read_pid(pidfile)
                self.assertTrue(pid_alive(pid))
                parent.kill()                       # hard kill: no finally/except blocks run
                parent.wait(10)
                self.assertTrue(wait_for(lambda: not pid_alive(pid), 10), "tree outlived its killed parent")
            finally:
                parent.kill()

    def test_sigterm_handler_installed_by_cli(self):
        if not hasattr(agy_agent.signal, "SIGTERM"):
            self.skipTest("no SIGTERM")
        previous = agy_agent.signal.getsignal(agy_agent.signal.SIGTERM)
        try:
            with mock.patch.object(agy_agent, "ask", return_value="ok"), contextlib.redirect_stdout(io.StringIO()):
                agy_agent.main(["--workspace", "none", "task"])
            handler = agy_agent.signal.getsignal(agy_agent.signal.SIGTERM)
            with self.assertRaises(SystemExit):
                handler(agy_agent.signal.SIGTERM, None)
        finally:
            agy_agent.signal.signal(agy_agent.signal.SIGTERM, previous)


if __name__ == "__main__":
    unittest.main()
