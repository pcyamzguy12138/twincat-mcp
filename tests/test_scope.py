from pathlib import Path
import unittest
from xml.dom import minidom

from twincat_mcp.scope import create_scope_candidate, inspect_scope

FIXTURE = (Path(__file__).resolve().parents[1] / "examples/scope-test-fixture.xml").read_text(encoding="utf-8")
ITEM = {"symbol": "Demo.NewValue", "data_type": "REAL64", "template_symbol": "Demo.TemplateValue"}


class ScopeTest(unittest.TestCase):
    def test_added_only_handles_cleared_and_idempotent(self):
        result = create_scope_candidate(FIXTURE, [ITEM])
        self.assertEqual(["Demo.NewValue"], result["added"])
        self.assertEqual([], result["removed"])
        self.assertEqual(2, inspect_scope(result["xml"])["count"])
        document = minidom.parseString(result["xml"])
        clone = document.getElementsByTagName("AdsAcquisition")[1]
        for name in ["IndexOffset", "FileHandle", "ServerHandle"]:
            self.assertEqual("0", clone.getElementsByTagName(name)[0].firstChild.data)
        self.assertEqual("true", clone.getElementsByTagName("SymbolBased")[0].firstChild.data)
        again = create_scope_candidate(result["xml"], [ITEM])
        self.assertEqual(result["xml"], again["xml"])
        self.assertEqual([], again["added"])

    def test_missing_type_preserves_supported_additions(self):
        result = create_scope_candidate(FIXTURE, [ITEM, ITEM | {"symbol": "Demo.Flag", "data_type": "BIT"}])
        self.assertEqual(["Demo.NewValue"], result["added"])
        self.assertEqual("Demo.Flag", result["unresolved"][0]["symbol"])

    def test_excluded_never_restored(self):
        result = create_scope_candidate(FIXTURE, [ITEM], ["Demo.NewValue"])
        self.assertEqual(FIXTURE, result["xml"])
        self.assertEqual([], result["added"])

    def test_incomplete_template_rejected(self):
        source = FIXTURE.replace("<FileHandle>456</FileHandle>", "")
        result = create_scope_candidate(source, [ITEM])
        self.assertEqual(source, result["xml"])
        self.assertEqual(1, len(result["unresolved"]))

    def test_duplicate_input_and_untrusted_xml(self):
        with self.assertRaises(ValueError):
            create_scope_candidate(FIXTURE, [ITEM, ITEM])
        with self.assertRaises(ValueError):
            inspect_scope('<!DOCTYPE ScopeProject [<!ENTITY x "a">]><ScopeProject/>')

    def test_guid_uniqueness_enforced(self):
        source = FIXTURE.replace("</SubMember>", "<AdsAcquisition><Guid>33333333-3333-4333-8333-333333333333</Guid><SymbolName>Demo.Other</SymbolName></AdsAcquisition></SubMember>", 1)
        with self.assertRaisesRegex(ValueError, "duplicate acquisition GUIDs"):
            inspect_scope(source)


if __name__ == "__main__":
    unittest.main()
