"""Real MCP stdio exchange against the demo server, without any PLC access."""
import sys
import unittest
import json
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_tmc import TMC


class ProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_engineering_tools_over_real_stdio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xml = (Path(__file__).resolve().parents[1] / "examples/demo-pou.xml").read_text(encoding="utf-8")
            (root / "Demo.TcPOU").write_text(xml, encoding="utf-8")
            (root / "Demo.tmc").write_text(TMC, encoding="utf-8")
            params = StdioServerParameters(command=sys.executable, args=["-m", "twincat_mcp.server", "--demo", "--project-root", str(root)])
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    async def call(name, arguments):
                        result = await session.call_tool(name, arguments)
                        self.assertFalse(result.isError, result.content)
                        return json.loads(result.content[0].text)
                    inventory = await call("inspect_project", {})
                    self.assertEqual(2, inventory["total"])
                    source = await call("read_project_source", {"path": "Demo.TcPOU"})
                    edited = await call("preview_project_edits", {"changes": [{"path": "Demo.TcPOU", "expected_sha256": source["fileSha256"],
                        "edits": [{"section": "implementation", "replacement": "counter := 9;"}]}]})
                    self.assertFalse(edited["writtenToDisk"])
                    self.assertEqual(xml, (root / "Demo.TcPOU").read_text(encoding="utf-8"))
                    symbols = await call("browse_project_symbols", {"path": "Demo.tmc", "expand": True})
                    self.assertEqual(5, symbols["total"])
                    found = await call("search_project", {"query": "counter", "identifier": True})
                    self.assertGreater(found["total"], 0)
                    plan = await call("plan_scope_from_tmc", {"xml_text": TMC, "selections": [{"symbol": "MAIN.state.ready", "chart": "Status", "axis": "Bits"}],
                        "ams_net_id": "0.0.0.0.0.0", "ads_port": 851})
                    self.assertTrue(plan["ready"])
                    self.assertEqual("BIT", plan["signals"][0]["scope_type"])
                    denied = await session.call_tool("read_project_source", {"path": "../Outside.TcPOU"})
                    self.assertTrue(denied.isError)

    async def test_initialize_list_read_and_reject(self):
        params = StdioServerParameters(command=sys.executable, args=["-m", "twincat_mcp.server", "--demo"])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                self.assertEqual({"read_symbols", "describe_configuration", "inspect_source", "preview_source_edit", "inspect_scope", "create_scope_candidate",
                                  "preview_source_batch", "inspect_tmc", "inspect_tmc_type", "inspect_project", "read_project_source",
                                  "search_project", "inspect_project_references", "preview_project_edits", "browse_project_symbols", "inspect_project_type",
                                  "generate_scope_project", "plan_scope_from_tmc"}, {t.name for t in tools.tools})
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
