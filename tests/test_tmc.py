import unittest

from twincat_mcp.tmc import TmcCatalog, inspect_tmc, inspect_tmc_type
from twincat_mcp.xmlutil import parse_xml

# Hand-authored synthetic layout; no machine source or compiled customer data.
TMC = '''<TcModuleClass><DataTypes>
<DataType><Name>Mode</Name><BitSize>16</BitSize><BaseType>INT</BaseType>
<EnumInfo><Text>Idle</Text><Enum>0</Enum></EnumInfo><EnumInfo><Text>Ready</Text><Enum>2</Enum></EnumInfo></DataType>
<DataType><Name>Counter</Name><BitSize>32</BitSize><BaseType>DINT</BaseType></DataType>
<DataType><Name>Pair</Name><BitSize>64</BitSize><BaseType>REAL</BaseType>
<ArrayInfo><LBound>-1</LBound><Elements>2</Elements></ArrayInfo></DataType>
<DataType><Name>State</Name><BitSize>96</BitSize>
<SubItem><Name>mode</Name><Type>Mode</Type><BitSize>16</BitSize><BitOffs>0</BitOffs></SubItem>
<SubItem><Name>count</Name><Type>Counter</Type><BitSize>32</BitSize><BitOffs>32</BitOffs></SubItem>
<SubItem><Name>ready</Name><Type>BOOL</Type><BitSize>8</BitSize><BitOffs>64</BitOffs></SubItem></DataType>
</DataTypes><Modules><Module><Name>Demo</Name><DataAreas><DataArea><Name>Internal</Name>
<Symbol><Name>MAIN.state</Name><BaseType>State</BaseType><BitSize>96</BitSize><BitOffs>0</BitOffs></Symbol>
<Symbol><Name>MAIN.values</Name><BaseType>Pair</BaseType><BitSize>64</BitSize><BitOffs>96</BitOffs></Symbol>
</DataArea></DataAreas></Module></Modules></TcModuleClass>'''


class TmcTest(unittest.TestCase):
    def test_page_filter_and_offline_evidence(self):
        result = inspect_tmc(TMC, limit=1)
        self.assertEqual(2, result["total"])
        self.assertEqual(1, result["nextOffset"])
        self.assertEqual("MAIN.values", inspect_tmc(TMC, offset=1)["symbols"][0]["name"])
        self.assertEqual("not_verified", result["symbols"][0]["onlineAvailability"])
        self.assertEqual(1, inspect_tmc(TMC, type_filter="p*")["total"])

    def test_struct_alias_enum_and_negative_array_bounds(self):
        rows = inspect_tmc(TMC, expand=True)["symbols"]
        self.assertEqual(["MAIN.state.mode", "MAIN.state.count", "MAIN.state.ready", "MAIN.values[-1]", "MAIN.values[0]"], [r["name"] for r in rows])
        self.assertEqual([0, 32, 64, 96, 128], [r["bitOffset"] for r in rows])
        self.assertEqual(["INT16", "INT32", "BIT", "REAL32", "REAL32"], [r["scopeType"] for r in rows])
        self.assertEqual(2, inspect_tmc_type(TMC, "Mode")["type"]["values"][1]["value"])

    def test_namespace_and_guid_resolution(self):
        text = TMC.replace('<Name>Counter</Name>', '<Name Namespace="DemoTypes" GUID="{abc}">Counter</Name>').replace('<Type>Counter</Type>', '<Type GUID="{abc}"/>')
        result = inspect_tmc_type(text, "DemoTypes.Counter")
        self.assertEqual("alias", result["type"]["kind"])
        self.assertEqual("DINT", inspect_tmc(text, expand=True)["symbols"][1]["type"])

    def test_multidimensional_member_array(self):
        text = TMC.replace('<Type>Counter</Type><BitSize>32</BitSize>', '<Type>INT</Type><ArrayInfo><LBound>1</LBound><Elements>2</Elements></ArrayInfo><ArrayInfo><LBound>4</LBound><Elements>2</Elements></ArrayInfo><BitSize>64</BitSize>')
        rows = inspect_tmc(text, expand=True)["symbols"]
        self.assertEqual(["MAIN.state.count[1,4]", "MAIN.state.count[1,5]", "MAIN.state.count[2,4]", "MAIN.state.count[2,5]"], [r["name"] for r in rows[1:5]])

    def test_unknown_recursive_and_indirect_types(self):
        self.assertEqual("unresolved", inspect_tmc_type(TMC, "Missing")["type"]["kind"])
        recursive = TMC.replace('<BaseType>DINT</BaseType>', '<BaseType>Counter</BaseType>')
        self.assertEqual("unresolved", inspect_tmc_type(recursive, "Counter")["type"]["base"]["kind"])
        indirect = TMC.replace('<Type>Counter</Type>', '<Type PointerTo="true">Counter</Type>')
        self.assertIsNone(inspect_tmc(indirect, expand=True)["symbols"][1]["scopeType"])

    def test_missing_offsets_not_assumed_zero(self):
        rows = inspect_tmc(TMC.replace('<BitOffs>32</BitOffs>', ''), expand=True)["symbols"]
        self.assertIsNone(rows[1]["bitOffset"])

    def test_array_limits_stride_and_invalid_bounds(self):
        for text in [TMC.replace('<Elements>2</Elements>', '<Elements>0</Elements>'),
                     TMC.replace('<Elements>2</Elements>', '<Elements>10001</Elements>'),
                     TMC.replace('<Name>Pair</Name><BitSize>64', '<Name>Pair</Name><BitSize>128')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                inspect_tmc(text, expand=True)

    def test_duplicate_symbols_and_types(self):
        for text in [TMC.replace('MAIN.values', 'MAIN.state'), TMC.replace('<Name>Pair</Name>', '<Name>State</Name>')]:
            with self.assertRaisesRegex(ValueError, "[Dd]uplicate"):
                TmcCatalog(text)

    def test_xml_attacks_and_limits(self):
        for text in ['<!DOCTYPE x [<!ENTITY x "abc">]><x>&x;</x>',
                     '<x>' * 65 + '</x>' * 65, '<?xml version="1.0" encoding="utf-16"?><x/>', '<x>\0</x>']:
            with self.assertRaises(ValueError):
                parse_xml(text)
        with self.assertRaises(ValueError):
            parse_xml('<x/>', max_bytes=3)
        with self.assertRaises(ValueError):
            inspect_tmc('<x/>')

    def test_invalid_pagination(self):
        for kwargs in [dict(offset=-1), dict(limit=501), dict(limit=True)]:
            with self.assertRaises(ValueError):
                inspect_tmc(TMC, **kwargs)


if __name__ == "__main__":
    unittest.main()
