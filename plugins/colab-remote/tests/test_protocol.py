import json
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def _shipped_parameters(self):
        config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        launcher = config["mcpServers"]["colab-remote"]
        cwd = (ROOT / launcher["cwd"]).resolve()
        return StdioServerParameters(
            command=launcher["command"],
            args=launcher["args"],
            cwd=cwd,
        )

    async def _tools(self, parameters):
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
        return {tool.name: tool for tool in tools.tools}

    async def _list_tools(self, parameters):
        return set(await self._tools(parameters))

    async def test_stdio_handshake_and_tool_listing(self):
        parameters = self._shipped_parameters()
        names = await self._list_tools(parameters)
        self.assertIn("create_session", names)
        self.assertIn("credential_status", names)
        self.assertIn("start_job", names)

    async def test_llm_facing_schemas_are_constrained_and_documented(self):
        parameters = self._shipped_parameters()
        tools = await self._tools(parameters)
        create = tools["create_session"].inputSchema["properties"]
        accelerator_schema = create["accelerator"]["anyOf"][0]
        language_schema = create["language"]["anyOf"][0]
        self.assertEqual(
            accelerator_schema["enum"],
            ["cpu", "t4", "l4", "g4", "h100", "a100", "v5e-1", "v6e-1"],
        )
        self.assertEqual(language_schema["enum"], ["python", "r", "julia"])
        self.assertIn("description", create["session_name"])
        self.assertIn("description", create["high_ram"])
        self.assertNotIn("prefer_high_ram", create)

        config = tools["set_config"].inputSchema["properties"]
        self.assertNotIn("ssh_secret_name", config)
        notification_mode = next(
            item
            for item in config["notification_mode"]["anyOf"]
            if "enum" in item
        )
        self.assertEqual(
            notification_mode["enum"], ["off", "failures_only", "all"]
        )
        def integer_schema(name):
            return next(
                item
                for item in config[name]["anyOf"]
                if item.get("type") == "integer"
            )
        self.assertEqual(integer_schema("max_concurrent_sessions")["maximum"], 64)
        self.assertEqual(integer_schema("transfer_parallelism")["maximum"], 8)
        self.assertEqual(integer_schema("retry_attempts")["maximum"], 10)

        prepare = tools["prepare_language"].inputSchema["properties"]
        self.assertEqual(set(prepare), {"session_name", "language"})
        self.assertNotIn("acknowledge_external_download", prepare)

        transfer = tools["start_upload"].inputSchema["properties"]
        parallelism = next(
            item
            for item in transfer["parallelism"]["anyOf"]
            if item.get("type") == "integer"
        )
        self.assertEqual(parallelism["minimum"], 1)
        self.assertEqual(parallelism["maximum"], 8)
        self.assertTrue(all(item.get("description") for item in transfer.values()))

        for tool in tools.values():
            self.assertTrue(tool.description, f"{tool.name} needs a tool description")
            for name, schema in tool.inputSchema.get("properties", {}).items():
                described = bool(schema.get("description")) or any(
                    item.get("description") for item in schema.get("anyOf", [])
                )
                self.assertTrue(
                    described, f"{tool.name}.{name} needs a parameter description"
                )

    async def test_shipped_plugin_launcher(self):
        parameters = self._shipped_parameters()
        names = await self._list_tools(parameters)
        self.assertIn("create_session", names)
        self.assertIn("test_notification", names)


if __name__ == "__main__":
    unittest.main()
