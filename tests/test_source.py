from pathlib import Path
import unittest

from twincat_mcp.source import inspect_source, preview_source_edit

EXAMPLE = (Path(__file__).resolve().parents[1] / "examples/demo-pou.xml").read_text(encoding="utf-8")


class SourceTest(unittest.TestCase):
    def test_one_section_only_and_unicode(self):
        text = EXAMPLE.replace("\n", "\r\n")
        result = preview_source_edit(text, "implementation", "// 示例\ncounter := counter + 2;\n")
        expected = text.replace("counter := counter + 1;\r\n", "// 示例\r\ncounter := counter + 2;\r\n")
        self.assertEqual(expected, result["xml"])
        self.assertFalse(result["writtenToDisk"])
        self.assertEqual(inspect_source(text)["object"], inspect_source(result["xml"])["object"])

    def test_method_only(self):
        method = '<Method Name="Reset" Id="{22222222-2222-4222-8222-222222222222}"><Declaration><![CDATA[METHOD Reset]]></Declaration><Implementation><ST><![CDATA[counter := 0;]]></ST></Implementation></Method>'
        text = EXAMPLE.replace("</POU>", method + "</POU>")
        result = preview_source_edit(text, "implementation", "counter := 5;", "Reset")
        self.assertEqual(text.replace("counter := 0;", "counter := 5;"), result["xml"])

    def test_gvl_dut(self):
        for kind in ["GVL", "DUT"]:
            text = f'<TcPlcObject><{kind} Name="Demo" Id="demo"><Declaration><![CDATA[old]]></Declaration></{kind}></TcPlcObject>'
            self.assertIn("<![CDATA[new]]>", preview_source_edit(text, "declaration", "new")["xml"])
            with self.assertRaises(ValueError):
                preview_source_edit(text, "implementation", "new")

    def test_unsupported_and_entities(self):
        for text in ["binary\0data", "not xml", '<!DOCTYPE x [<!ENTITY a "boom">]><x/>',
                     EXAMPLE.replace("utf-8", "utf-16"), EXAMPLE.replace("<ST>", "<FBD>").replace("</ST>", "</FBD>")]:
            with self.subTest(text=text[:20]), self.assertRaises(ValueError):
                preview_source_edit(text, "implementation", "new")

    def test_delimiter_ambiguous_and_wrong_section(self):
        for replacement in ["]]>", "\0"]:
            with self.assertRaises(ValueError):
                preview_source_edit(EXAMPLE, "implementation", replacement)
        with self.assertRaises(ValueError):
            preview_source_edit(EXAMPLE, "bad", "new")
        with self.assertRaises(ValueError):
            preview_source_edit(EXAMPLE.replace("counter := counter + 1;", "a]]><![CDATA[b"), "implementation", "new")

    def test_bom_preserved(self):
        result = preview_source_edit("\ufeff" + EXAMPLE, "implementation", "counter := 2;")
        self.assertTrue(result["xml"].startswith("\ufeff"))


if __name__ == "__main__":
    unittest.main()
