import asyncio
import json
import unittest
import server


class ServerIntegrationTest(unittest.TestCase):
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
        self.assertIn("gemini-3.8", status["default_model"])

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
        res = server.handle_large_output(long_text, "D:/antigravity-mcp", max_lines=20)
        self.assertIn("Full content saved to artifact", res)
        self.assertIn(".antigravity/artifacts", res)


if __name__ == "__main__":
    unittest.main()