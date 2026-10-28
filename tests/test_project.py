import hashlib
from pathlib import Path
import tempfile
import unittest

from twincat_mcp.project import Project
from twincat_mcp.source import inspect_source, preview_source_edit, preview_source_batch

BASE = (Path(__file__).resolve().parents[1] / "examples/demo-pou.xml").read_text(encoding="utf-8")
MEMBERS = '''<Action Name="Init" Id="action"><Implementation><ST><![CDATA[counter := 0;]]></ST></Implementation></Action>
<Property Name="Value" Id="property"><Declaration><![CDATA[PROPERTY Value : DINT]]></Declaration>
<Get Name="Get" Id="get"><Declaration><![CDATA[VAR\nEND_VAR]]></Declaration><Implementation><ST><![CDATA[Value := counter;]]></ST></Implementation></Get>
<Set Name="Set" Id="set"><Declaration><![CDATA[VAR\nEND_VAR]]></Declaration><Implementation><ST><![CDATA[counter := Value;]]></ST></Implementation></Set></Property>'''


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "POUs").mkdir()
        (self.root / "POUs/Main.TcPOU").write_text(BASE, encoding="utf-8")
        self.project = Project(self.root)

    def test_inventory_source_members_and_binary_report(self):
        (self.root / "Locked.TcPOU").write_bytes(b'\xff\x00binary')
        rows = self.project.inventory()["files"]
        self.assertEqual(2, len(rows))
        self.assertEqual("unavailable", rows[0]["status"])
        self.assertEqual("POU", rows[1]["object"]["kind"])
        self.assertEqual("POU", self.project.source("POUs/Main.TcPOU")["object"]["kind"])

    def test_traversal_absolute_streams_and_wrong_extensions(self):
        for path in ["../secret.TcPOU", "C:\\secret.TcPOU", "\\\\host\\secret.TcPOU", "/secret.TcPOU", "POUs/Main.TcPOU:stream", "POUs/../POUs/Main.TcPOU", "POUs//Main.TcPOU", "POUs/Main.txt"]:
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.project.read(path)

    def test_generated_directory_excluded(self):
        (self.root / "Boot").mkdir()
        (self.root / "Boot/Secret.TcPOU").write_text(BASE)
        self.assertEqual(["POUs/Main.TcPOU"], self.project.paths())
        with self.assertRaises(ValueError):
            self.project.read("Boot/Secret.TcPOU")

    def test_symbolic_link_excluded_if_supported(self):
        link = self.root / "Link.TcPOU"
        try:
            link.symlink_to(self.root / "POUs/Main.TcPOU")
        except OSError:
            self.skipTest("OS user cannot create symbolic links")
        self.assertNotIn("Link.TcPOU", self.project.paths())
        with self.assertRaises(ValueError):
            self.project.read("Link.TcPOU")

    def test_search_identifiers_ignores_comments_strings_and_reports_section_lines(self):
        body = "// counter\n(* counter (* counter *) *)\nlabel := 'counter';\ncounter := counter + 1;"
        text = preview_source_edit(BASE, "implementation", body)["xml"]
        (self.root / "POUs/Main.TcPOU").write_text(text, encoding="utf-8")
        result = self.project.search("counter", identifier=True)
        impl = [m for m in result["matches"] if m["section"] == "implementation"]
        self.assertEqual(2, len(impl))
        self.assertEqual([4, 4], [m["line"] for m in impl])
        self.assertEqual("lexical_occurrences_not_semantic_binding", result["evidence"])
        self.assertGreater(self.project.search("counter")["total"], result["total"])

    def test_static_project_references(self):
        xml = '''<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003"><ItemGroup>
        <Compile Include="POUs\\Main.TcPOU"/><Compile Include="POUs\\Main.TcPOU"/>
        <Compile Include="Missing.TcPOU"/><Compile Include="../Outside.TcPOU"/>
        <Compile Include="$(Source)/*.TcPOU"/><PlaceholderReference Include="Tc2_Standard"/>
        </ItemGroup></Project>'''
        (self.root / "Demo.plcproj").write_text(xml)
        result = self.project.references("Demo.plcproj")
        self.assertEqual(["exists", "exists", "unresolved", "unresolved", "unresolved"], [r["status"] for r in result["references"]])
        self.assertTrue(result["references"][1]["duplicate"])
        self.assertEqual("Tc2_Standard", result["libraries"][0]["Include"])

    def test_multifile_preview_hash_conflict_and_input_preservation(self):
        path = self.root / "POUs/Main.TcPOU"
        before = path.read_bytes()
        sha = hashlib.sha256(before).hexdigest()
        change = {"path": "POUs/Main.TcPOU", "expected_sha256": sha,
                  "edits": [{"section": "implementation", "replacement": "counter := 20;"}]}
        result = self.project.preview([change])
        self.assertIn("counter := 20;", result["files"][0]["xml"])
        self.assertEqual(before, path.read_bytes())
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.project.preview([change | {"expected_sha256": "old"}])
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.project.preview([change, change])

    def test_property_action_edit_preserves_other_bytes(self):
        text = BASE.replace("</POU>", MEMBERS + "</POU>").replace("\n", "\r\n")
        rows = inspect_source(text)["sections"]
        self.assertIn("Property:Value/Get", [r["member"] for r in rows])
        result = preview_source_edit(text, "implementation", "Value := 10;", member="Property:Value/Get")
        self.assertEqual(text.replace("Value := counter;", "Value := 10;"), result["xml"])
        result = preview_source_edit(text, "implementation", "counter := -1;", member="Action:Init")
        self.assertEqual(text.replace("counter := 0;", "counter := -1;"), result["xml"])
        # A top-level edit must not accidentally match property/action bodies.
        result = preview_source_edit(text, "implementation", "counter := 3;")
        self.assertIn(MEMBERS.replace("\n", "\r\n"), result["xml"])

    def test_interface_and_batch_transaction(self):
        text = '<TcPlcObject><Itf Name="I_Demo" Id="interface"><Declaration><![CDATA[INTERFACE I_Demo]]></Declaration><Method Name="Run" Id="method"><Declaration><![CDATA[METHOD Run]]></Declaration></Method></Itf></TcPlcObject>'
        sha = inspect_source(text)["sourceSha256"]
        result = preview_source_batch(text, [{"section": "declaration", "replacement": "INTERFACE I_Demo EXTENDS I_Base"},
            {"section": "declaration", "method": "Run", "replacement": "METHOD Run : BOOL"}], sha)
        self.assertEqual(2, result["editCount"])
        self.assertIn("METHOD Run : BOOL", result["xml"])
        with self.assertRaises(ValueError):
            preview_source_batch(text, [{"section": "declaration", "replacement": "x"}] * 2, sha)
        with self.assertRaises(ValueError):
            preview_source_edit(text, "implementation", "x", method="Run")


if __name__ == "__main__":
    unittest.main()
