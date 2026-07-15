import sys
import shutil
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def _list_tools(self, parameters):
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
        return {tool.name for tool in tools.tools}

    async def test_stdio_handshake_and_tool_listing(self):
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "mcp" / "server.py")],
            cwd=ROOT,
        )
        names = await self._list_tools(parameters)
        self.assertIn("create_session", names)
        self.assertIn("credential_status", names)
        self.assertIn("start_job", names)

    async def test_plugin_portable_uv_launcher(self):
        uv = shutil.which("uv")
        self.assertIsNotNone(uv, "uv is required for the portable MCP launcher test")
        parameters = StdioServerParameters(
            command=str(uv),
            args=[
                "run",
                "--isolated",
                "--project",
                str(ROOT),
                "python",
                str(ROOT / "mcp" / "server.py"),
            ],
            cwd=ROOT,
        )
        names = await self._list_tools(parameters)
        self.assertIn("create_session", names)
        self.assertIn("test_notification", names)


if __name__ == "__main__":
    unittest.main()
