"""Real MCP stdio exchange against the demo server, without any PLC access."""
import sys
import asyncio
import os
import unittest
import json
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_tmc import TMC


def server_parameters(arguments):
    package_root = os.environ.get('TWINCAT_MCP_TEST_PACKAGE_ROOT')
    if package_root:
        # Assert inside the spawned server too: SDK stdio does not inherit PYTHONPATH.
        bootstrap = "import os,pathlib,runpy,twincat_mcp; assert pathlib.Path(os.environ['TWINCAT_MCP_TEST_PACKAGE_ROOT']).resolve() in pathlib.Path(twincat_mcp.__file__).resolve().parents; runpy.run_module('twincat_mcp.server',run_name='__main__')"
        return StdioServerParameters(command=sys.executable, args=['-c', bootstrap, *arguments],
                                     env={'PYTHONPATH': package_root, 'TWINCAT_MCP_TEST_PACKAGE_ROOT': package_root})
    return StdioServerParameters(command=sys.executable, args=['-m', 'twincat_mcp.server', *arguments])


class ProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_engineering_tools_over_real_stdio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xml = (Path(__file__).resolve().parents[1] / "examples/demo-pou.xml").read_text(encoding="utf-8")
            (root / "Demo.TcPOU").write_text(xml, encoding="utf-8")
            (root / "Demo.tmc").write_text(TMC, encoding="utf-8")
            params = server_parameters(["--demo", "--project-root", str(root)])
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
        params = server_parameters(["--demo"])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                self.assertEqual({"read_symbols", "describe_configuration", "inspect_source", "preview_source_edit", "inspect_scope", "create_scope_candidate",
                                  "preview_source_batch", "inspect_tmc", "inspect_tmc_type", "inspect_project", "read_project_source",
                                  "search_project", "inspect_project_references", "preview_project_edits", "browse_project_symbols", "inspect_project_type",
                                  "generate_scope_project", "plan_scope_from_tmc", "decode_tmc_value", "read_typed_symbols",
                                  "read_runtime_state", "describe_symbols", "browse_runtime_symbols", "start_recording",
                                  "recording_status", "stop_recording", "read_recording_samples", "export_recording_csv",
                                  "analyze_recording", "export_project_candidates", "diagnose_environment", "compile_plc_project"}, {t.name for t in tools.tools})
                self.assertTrue(all(t.annotations.readOnlyHint == (t.name not in {'start_recording', 'stop_recording', 'export_project_candidates', 'compile_plc_project'}) for t in tools.tools))
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

    async def test_read_only_runtime_and_recording_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            params = server_parameters(['--demo', '--recording-root', directory])
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    async def call(name, arguments=None):
                        result = await session.call_tool(name, arguments or {})
                        self.assertFalse(result.isError, result.content)
                        return json.loads(result.content[0].text)
                    self.assertEqual(5, (await call('read_runtime_state'))['AdsState'])
                    self.assertEqual(4, (await call('describe_symbols', {'symbols': ['Demo.Counter']}))['Symbols'][0]['Size'])
                    self.assertEqual(1, (await call('browse_runtime_symbols', {'limit': 1}))['nextOffset'])
                    self.assertEqual(42, (await call('read_typed_symbols', {'symbols': ['Demo.Counter']}))['Samples'][0]['Value'])
                    info = await call('start_recording', {'symbols': ['Demo.Ready'], 'duration_seconds': 0.2, 'interval_ms': 20})
                    arguments = {'identifier': info['id']}
                    for _ in range(30):
                        info = await call('recording_status', arguments)
                        if info['state'] != 'recording':
                            break
                        await asyncio.sleep(0.03)
                    self.assertEqual('completed', info['state'])
                    self.assertGreater(info['storedSamples'], 0)
                    self.assertGreater((await call('export_recording_csv', arguments))['rows'], 0)
                    self.assertEqual(0, (await call('analyze_recording', arguments))['symbols']['Demo.Ready']['risingEdges'])
                    running = await call('start_recording', {'symbols': ['Demo.Ready'], 'duration_seconds': 30})
                    await call('stop_recording', {'identifier': running['id']})
                    # Leave another job active: MCP lifespan must stop its owned worker on disconnect.
                    await asyncio.sleep(0.1)
                    last = await call('start_recording', {'symbols': ['Demo.Ready'], 'duration_seconds': 30})
            saved = json.loads((Path(directory) / last['id'] / 'metadata.json').read_text())
            self.assertEqual('stopped', saved['state'])


if __name__ == "__main__":
    unittest.main()
