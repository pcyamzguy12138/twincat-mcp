"""Real MCP stdio exchange against the demo server, without any PLC access."""
import sys
import unittest
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_list_read_and_reject(self):
        params = StdioServerParameters(command=sys.executable, args=["-m", "twincat_mcp.server", "--demo"])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                self.assertEqual({"read_symbols", "describe_configuration", "inspect_source", "preview_source_edit", "inspect_scope", "create_scope_candidate"}, {t.name for t in tools.tools})
                self.assertTrue(all(t.annotations.readOnlyHint for t in tools.tools))
                result = await session.call_tool("read_symbols", {"symbols": ["Demo.Value"]})
                self.assertFalse(result.isError)
                payload = json.loads(result.content[0].text)
                self.assertTrue(payload["demo"])
                self.assertEqual(1.25, payload["Samples"][0]["Value"])
                rejected = await session.call_tool("read_symbols", {"symbols": ["Other.Value"]})
                self.assertTrue(rejected.isError)
                xml = (Path(__file__).resolve().parents[1] / "examples/demo-pou.xml").read_text(encoding="utf-8")
                edited = await session.call_tool("preview_source_edit", {"xml_text": xml, "section": "implementation", "replacement": "counter := 7;"})
                self.assertFalse(edited.isError)
                preview = json.loads(edited.content[0].text)
                self.assertFalse(preview["writtenToDisk"])
                self.assertIn("counter := 7;", preview["xml"])
                scope = (Path(__file__).resolve().parents[1] / "examples/scope-test-fixture.xml").read_text(encoding="utf-8")
                generated = await session.call_tool("create_scope_candidate", {"xml_text": scope, "additions": [{"symbol": "Demo.NewValue", "data_type": "REAL64", "template_symbol": "Demo.TemplateValue"}]})
                self.assertFalse(generated.isError)
                self.assertEqual(["Demo.NewValue"], json.loads(generated.content[0].text)["added"])


if __name__ == "__main__":
    unittest.main()
