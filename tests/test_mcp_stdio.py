"""Offline protocol smoke test: no authenticated agent/backend calls."""
import asyncio
import sys
import unittest
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class StdioTests(unittest.TestCase):
    def test_registration_and_validation_over_stdio(self):
        async def check():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-B", str(Path(__file__).resolve().parents[1] / "server.py")],
                env={"AGY_PROACTIVE": "0", "AGY_DEFAULT_WORKSPACE": "none"},
            )
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self.assertEqual(len(listing.tools), 7)
                    tool = next(t for t in listing.tools if t.name == "ask-antigravity")
                    properties = tool.model_dump(by_alias=True)["inputSchema"]["properties"]
                    self.assertIn("result_format", properties)
                    self.assertIn("timeout", properties)
                    self.assertNotIn("ctx", properties)
                    result = await session.call_tool("list-models", {})
                    self.assertFalse(result.model_dump(by_alias=True).get("isError", False))
                    rejected = await session.call_tool("ask-antigravity", {
                        "prompt": "unused", "resume": True, "conversation_id": "invalid",
                    })
                    self.assertTrue(rejected.model_dump(by_alias=True)["isError"])
        asyncio.run(asyncio.wait_for(check(), timeout=20))


if __name__ == "__main__":
    unittest.main()
