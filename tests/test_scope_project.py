import unittest
import os
from unittest.mock import patch

from twincat_mcp.scope_project import generate_scope_project, validate_scope_request, validate_scope_graph, plan_scope_from_tmc
from test_tmc import TMC

SIGNAL = dict(symbol="Demo.Value", scope_type="REAL64", ams_net_id="0.0.0.0.0.0", ads_port=851, chart="Chart", axis="Axis")
GRAPH = '''<ScopeProject><Name>Demo</Name><Guid>00000000-0000-4000-8000-000000000001</Guid><SubMember>
<DataPool><SubMember><AdsAcquisition><Guid>00000000-0000-4000-8000-000000000002</Guid><SymbolName>Demo.Value</SymbolName>
<AmsNetId>0.0.0.0.0.0</AmsNetId><TargetPort>851</TargetPort><DataType>REAL64</DataType>
<SymbolBased>true</SymbolBased><IndexOffset>0</IndexOffset><FileHandle>0</FileHandle><ServerHandle>0</ServerHandle>
</AdsAcquisition></SubMember></DataPool>
<YTChart><Name>Chart</Name><SubMember><AxisGroup><Name>Axis</Name><SubMember><Channel><SubMember><AcquisitionInterpreter>
<AcquisitionGUID>00000000-0000-4000-8000-000000000002</AcquisitionGUID>
</AcquisitionInterpreter></SubMember></Channel></SubMember></AxisGroup></SubMember></YTChart>
</SubMember></ScopeProject>'''


class FullScopeTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt" and os.environ.get("TWINCAT_MCP_TEST_SCOPE") == "1",
                         "Opt-in installed Scope API test; no PLC or recording")
    def test_installed_vendor_api_roundtrip_all_primitive_types(self):
        kinds = ["BIT", "INT8", "UINT8", "INT16", "UINT16", "INT32", "UINT32", "INT64", "UINT64", "REAL32", "REAL64"]
        signals = [SIGNAL | {"symbol": f"Demo.Signal{i}", "scope_type": kind,
                   "chart": "状态" if kind == "BIT" else "趋势", "axis": "数值" if i < 9 else "浮点"}
                   for i, kind in enumerate(kinds)]
        result = generate_scope_project("SyntheticScope", signals)
        self.assertTrue(result["nativeReloadVerified"])
        self.assertEqual(11, result["validation"]["channelBindings"])
        self.assertEqual(2, result["validation"]["charts"])
        self.assertEqual(3, len(result["groups"]))

    def test_tmc_selection_and_unresolved_types(self):
        selected = [dict(symbol="MAIN.state.ready", chart="Status", axis="Bits"),
                    dict(symbol="MAIN.values[-1]", chart="Values", axis="Position"),
                    dict(symbol="MAIN.unknown", chart="Values", axis="Position")]
        plan = plan_scope_from_tmc(TMC, selected, "0.0.0.0.0.0", 851)
        self.assertEqual(["BIT", "REAL32"], [s["scope_type"] for s in plan["signals"]])
        self.assertEqual(["MAIN.unknown"], [s["symbol"] for s in plan["unresolved"]])
        self.assertFalse(plan["ready"])

    def test_invalid_request_never_calls_native_api(self):
        with patch("twincat_mcp.scope_project._native_generate", side_effect=AssertionError("No native call")):
            for name, signals in [("../escape", [SIGNAL]), ("CON", [SIGNAL]), ("Demo", []),
                    ("Demo", [SIGNAL] * 2), ("Demo", [SIGNAL | {"ads_port": 0}]),
                    ("Demo", [SIGNAL | {"ams_net_id": "256.0.0.0.0.0"}]),
                    ("Demo", [SIGNAL | {"scope_type": "STRUCT"}])]:
                with self.subTest(name=name, signals=signals), self.assertRaises(ValueError):
                    generate_scope_project(name, signals)

    def test_target_identity_includes_port_and_netid(self):
        result = validate_scope_request("Demo", [SIGNAL, SIGNAL | {"ads_port": 852}])
        self.assertEqual(2, len(result["signals"]))

    def test_graph_rejects_dangling_wrong_group_and_runtime_handles(self):
        request = validate_scope_request("Demo", [SIGNAL])
        self.assertEqual(1, validate_scope_graph(GRAPH, request)["channelBindings"])
        for text in [GRAPH.replace('000000000002</AcquisitionGUID>', '000000000003</AcquisitionGUID>'),
                     GRAPH.replace('<Name>Axis</Name>', '<Name>Wrong</Name>'),
                     GRAPH.replace('<IndexOffset>0', '<IndexOffset>17'),
                     GRAPH.replace('<DataType>REAL64', '<DataType>REAL32')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_scope_graph(text, request)

    def test_vendor_axis_renaming_is_mapped_explicitly(self):
        request = validate_scope_request("Demo", [SIGNAL])
        text = GRAPH.replace('<Name>Axis</Name>', '<Name>Axis (1)</Name>')
        groups = [dict(requestedChart="Chart", requestedAxis="Axis", chart="Chart", axis="Axis (1)")]
        self.assertEqual(1, validate_scope_graph(text, request, groups)["channelBindings"])
        with self.assertRaises(ValueError):
            validate_scope_graph(text, request, [])

    def test_two_artifacts_and_validation_evidence(self):
        with patch("twincat_mcp.scope_project._native_generate", return_value={"xml": GRAPH, "apiVersion": "test", "nativeReloadVerified": True}):
            result = generate_scope_project("Demo", [SIGNAL])
        self.assertEqual(["Demo.tcscopex", "Demo.tcmproj"], [a["filename"] for a in result["artifacts"]])
        self.assertIn('Include="Demo.tcscopex"', result["artifacts"][1]["xml"])
        self.assertFalse(result["writtenToDisk"])
        self.assertEqual("not_verified", result["onlineAvailability"])

    def test_native_reload_is_required(self):
        with patch("twincat_mcp.scope_project._native_generate", return_value={"xml": GRAPH, "nativeReloadVerified": False}):
            with self.assertRaises(RuntimeError):
                generate_scope_project("Demo", [SIGNAL])


if __name__ == "__main__":
    unittest.main()
