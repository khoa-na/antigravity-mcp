import asyncio
import json
import unittest
import tempfile
from pathlib import Path
from contextlib import ExitStack
from unittest import mock
import server


class ServerIntegrationTest(unittest.TestCase):
    def setUp(self):
        # No credentials, network, or real agent processes in the offline suite.
        patches = ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(mock.patch.object(server.agy_agent, "_HAS_ROTATION", False))
        patches.enter_context(mock.patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP forbidden in offline tests")))
        patches.enter_context(mock.patch.object(server.agy_backend, "Session"))
        patches.enter_context(mock.patch.object(server.agy_backend, "_retrieve_quota", return_value={"gemini-3.7-flash-tiered": 0.8}))
        patches.enter_context(mock.patch.object(server.agy_agent, "run_agent", return_value=("PONG", "", "", 0)))

    def test_list_models(self):
        models = server.list_models()
        self.assertIn("gemini-3.8-flash-high", models)
        self.assertIn("gemini-3.7-flash-high", models)
        self.assertIn("gemini-3.1-pro-high", models)

    def test_registered_tools(self):
        tools = [t.name for t in server.mcp._tool_manager.list_tools()]
        expected = [
            "ask-antigravity", "ask-gemini", "review-diff",
            "check-quota", "generate-tests", "ping", "list-models"
        ]
        for exp in expected:
            self.assertIn(exp, tools)

    def test_registered_resources(self):
        resources = [str(r.uri) for r in server.mcp._resource_manager.list_resources()]
        self.assertIn("antigravity://quota", resources)
        self.assertIn("antigravity://models", resources)
        self.assertIn("antigravity://status", resources)

    def test_registered_prompts(self):
        prompts = [p.name for p in server.mcp._prompt_manager.list_prompts()]
        self.assertIn("code-review", prompts)
        self.assertIn("tdd-feature", prompts)
        self.assertIn("security-audit", prompts)

    def test_resource_calls(self):
        # Models resource
        models_json = server.models_resource()
        models = json.loads(models_json)
        self.assertIsInstance(models, list)
        self.assertIn("gemini-3.8-flash-high", models)

        # Status resource
        status_json = server.status_resource()
        status = json.loads(status_json)
        self.assertEqual(status["status"], "ready")
        self.assertEqual(server.agy_agent.DEFAULT_MODEL, status["default_model"])

        # Quota resource
        quota_md = server.quota_resource()
        self.assertIn("Google Antigravity Live Model Quotas", quota_md)

    def test_prompt_generation(self):
        cr = server.code_review_prompt("diff --git a/foo.py", focus="security")
        self.assertIn("senior principal engineer", cr)
        self.assertIn("security", cr)
        self.assertIn("diff --git a/foo.py", cr)

        tdd = server.tdd_feature_prompt("Add JWT authentication", target_file="auth.py")
        self.assertIn("Red-Green-Refactor", tdd)
        self.assertIn("Add JWT authentication", tdd)
        self.assertIn("auth.py", tdd)

        sec = server.security_audit_prompt("def exec_code(code): eval(code)")
        self.assertIn("OWASP Top 10", sec)
        self.assertIn("eval(code)", sec)

    def test_ping(self):
        async def _run():
            return await server.ping()
        res = asyncio.run(_run())
        self.assertTrue(len(res) > 0)

    def test_check_quota(self):
        res = server.check_quota()
        self.assertIn("Google Antigravity Live Model Quotas", res)
        self.assertIn("gemini-3.7-flash-tiered", res)

    def test_handle_large_output(self):
        short_text = "This is a short output."
        self.assertEqual(server.handle_large_output(short_text, None, max_lines=10), short_text)

        long_text = "\n".join([f"Line {i}" for i in range(100)])
        with tempfile.TemporaryDirectory() as workspace:
            res = server.handle_large_output(long_text, workspace, max_lines=20)
        self.assertIn("Full content saved to artifact", res)
        self.assertIn(".antigravity/artifacts", res)

    def test_ping_rejects_failure_and_empty_success(self):
        for result in [("", "", "cannot launch", -1), ("partial", "", "failed", 1), ("", "", "", 0)]:
            with self.subTest(result=result), mock.patch.object(server.agy_agent, "run_agent", return_value=result):
                with self.assertRaises(RuntimeError):
                    asyncio.run(server.ping())

    def test_default_workspace_opt_out(self):
        with mock.patch.object(server, "DEFAULT_WORKSPACE", "none"):
            self.assertIsNone(server.resolve_workspace())
            self.assertIsNone(server.resolve_workspace(" "))

    def test_workspace_validation(self):
        self.assertEqual(server.resolve_workspace("."), str(Path.cwd().resolve()))
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(ValueError):
                server.resolve_workspace(str(Path(workspace) / "missing"))

    def test_staged_review_never_falls_back_to_unstaged(self):
        with mock.patch.object(server.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run:
            result = asyncio.run(server.review_diff(staged=True))
        self.assertIn("No staged", result)
        run.assert_called_once()

    def test_review_reports_git_failure(self):
        with mock.patch.object(server.subprocess, "run", return_value=mock.Mock(returncode=128, stdout="", stderr="not a git repository")):
            result = asyncio.run(server.review_diff())
        self.assertIn("not a git repository", result)
        self.assertNotIn("No uncommitted", result)

    def test_new_call_does_not_invent_conversation(self):
        with mock.patch.object(server.agy_agent, "ask", return_value="done") as ask:
            asyncio.run(server.ask_antigravity("task", workspace="none"))
        self.assertIsNone(ask.call_args.kwargs["conversation_id"])

    def test_resume_and_conversation_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            asyncio.run(server.ask_antigravity("task", resume=True, conversation_id="existing"))

    def test_artifact_preview_is_bounded(self):
        with tempfile.TemporaryDirectory() as workspace:
            result = server.handle_large_output("x" * 20000, workspace, max_chars=100)
        self.assertLess(len(result), 1000)
        self.assertNotIn("-39 lines", result)

    def test_json_success_is_explicitly_unverified(self):
        with mock.patch.object(server.agy_agent, "ask", return_value="done") as ask:
            result = json.loads(asyncio.run(server.ask_antigravity("task", conversation_id="real-id", result_format="json", timeout=20)))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["conversation_id"], "real-id")
        self.assertEqual(result["verification"], "not_run_by_wrapper")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(ask.call_args.kwargs["timeout"], 20)

    def test_json_errors_preserve_status_output_and_exit_code(self):
        for status in ["partial", "failed", "timed_out", "busy"]:
            error = server.agy_agent.AgentError("failure", status=status, exit_code=2, output="partial text")
            with self.subTest(status=status), mock.patch.object(server.agy_agent, "ask", side_effect=error):
                result = json.loads(asyncio.run(server.ask_gemini("task", result_format="json")))
            self.assertEqual(result["status"], status)
            self.assertEqual(result["response"], "partial text")
            self.assertEqual(result["exit_code"], 2)
            self.assertIsNone(result["conversation_id"])

    def test_text_errors_still_raise(self):
        with mock.patch.object(server.agy_agent, "ask", side_effect=server.agy_agent.AgentError("failure")):
            with self.assertRaises(RuntimeError):
                asyncio.run(server.ask_antigravity("task"))

    def test_review_never_launches_agent(self):
        with mock.patch.object(server.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="diff", stderr="")), \
             mock.patch.object(server.agy_backend, "ask", return_value="review") as backend, \
             mock.patch.object(server.agy_agent, "ask") as agent:
            result = asyncio.run(server.review_diff())
        self.assertIn("review", result)
        backend.assert_called_once()
        agent.assert_not_called()

    def test_invalid_options_rejected_before_execution(self):
        for kwargs in [{"result_format": "xml"}, {"timeout": 0}, {"timeout": float("nan")}, {"timeout": float("inf")}]:
            with self.subTest(kwargs=kwargs), mock.patch.object(server.agy_agent, "ask") as ask:
                with self.assertRaises(ValueError):
                    asyncio.run(server.ask_antigravity("task", **kwargs))
                ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
