import struct
import unittest
from unittest.mock import patch

from test_tmc import TMC
from twincat_mcp.backend import Backend
from twincat_mcp.tmc import TmcCatalog
from twincat_mcp.values import decode_tmc_value, decode_value, decode_snapshot


class ValueTest(unittest.TestCase):
    def test_struct_padding_alias_enum(self):
        raw = struct.pack('<h2xiB3x', 2, -123, 1)
        result = decode_tmc_value('State', raw.hex(), TMC)
        self.assertEqual({'mode': {'value': 2, 'labels': ['Ready']}, 'count': -123, 'ready': True}, result['value'])
        self.assertEqual('offline_tmc_not_verified_against_runtime', result['layoutVerification'])
        self.assertEqual(64, len(result['layoutSourceSha256']))

    def test_arrays_bounds_and_inline_ads_names(self):
        result = decode_tmc_value('Pair', struct.pack('<ff', 1.25, -2.5).hex(), TMC)['value']
        self.assertEqual({'lowerBounds': [-1], 'shape': [2], 'values': [1.25, -2.5]}, result)
        result = decode_tmc_value('ARRAY [-1..0, 3..4] OF INT', struct.pack('<hhhh', -1, 2, 3, -4).hex())['value']
        self.assertEqual([2, 2], result['shape'])
        self.assertEqual([-1, 3], result['lowerBounds'])
        self.assertEqual([-1, 2, 3, -4], result['values'])

    def test_strings_require_encoding_capacity_and_ucs2(self):
        self.assertEqual('é', decode_tmc_value('STRING(3)', 'c3a90000', string_encoding='utf-8')['value'])
        self.assertEqual('é', decode_tmc_value('STRING(1)', 'e900', string_encoding='latin-1')['value'])
        self.assertEqual('汉', decode_tmc_value('WSTRING(1)', '496c0000')['value'])
        for name, raw, encoding in [('STRING(1)', 'e900', None), ('STRING(1)', 'e900', 'utf-8'),
                                    ('STRING(1)', '0102', 'latin-1'), ('WSTRING(1)', '00d80000', None)]:
            with self.subTest(name=name, raw=raw, encoding=encoding), self.assertRaises(ValueError):
                decode_tmc_value(name, raw, string_encoding=encoding)

    def test_time_units_signedness_and_float_errors(self):
        self.assertEqual(-2, decode_tmc_value('LINT', struct.pack('<q', -2).hex())['value'])
        self.assertEqual({'value': 1500, 'unit': 'milliseconds', 'plcType': 'TIME'},
                         decode_tmc_value('TIME', struct.pack('<I', 1500).hex())['value'])
        self.assertEqual('nanoseconds', decode_tmc_value('LTIME', '0100000000000000')['value']['unit'])
        self.assertIn('epoch', decode_tmc_value('DATE', '01000000')['value'])
        for raw in [struct.pack('<d', float('nan')).hex(), struct.pack('<d', float('inf')).hex(), '00']:
            with self.assertRaises(ValueError):
                decode_tmc_value('LREAL', raw)

    def test_bad_layouts_never_guessed(self):
        catalog = TmcCatalog(TMC)
        for mutate in [lambda s: s['members'][1].update(bitOffset=0),
                       lambda s: s['members'][1].update(bitOffset=None),
                       lambda s: s.update(baseType='Base'),
                       lambda s: s['members'][1].update(bitSize=16)]:
            spec = catalog.resolve('State')
            mutate(spec)
            with self.assertRaises(ValueError):
                decode_value(bytes(12), spec)
        for name, raw in [('POINTER TO INT', '00000000'), ('Mystery', '00'), ('INT', '0x01'),
                          ('ARRAY [0..999999] OF BYTE', '00')]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                decode_tmc_value(name, raw)

    def test_packed_bits(self):
        layout = {'name': 'Bits', 'kind': 'struct', 'bitSize': 8, 'members': [
            {'name': 'flag', 'bitSize': 1, 'bitOffset': 3,
             'type': {'name': 'BOOL', 'kind': 'primitive', 'bitSize': 8}}]}
        self.assertEqual({'flag': True}, decode_value(b'\x08', layout))

    def test_snapshot_preserves_transport_errors_and_parses_tmc_once(self):
        frame = Backend(demo=True).read(['Demo.Counter', 'Demo.Value'])
        frame['Samples'][0].update(Status='error', Error='ADS 1808', Value=None)
        with patch('twincat_mcp.values.TmcCatalog', wraps=TmcCatalog) as catalog:
            result = decode_snapshot(frame, TMC, None)
            self.assertEqual(1, catalog.call_count)
        self.assertEqual('ADS 1808', result['Samples'][0]['Error'])
        self.assertEqual(1.25, result['Samples'][1]['Value'])
        frame['VersionStable'] = False
        self.assertEqual(frame, decode_snapshot(frame, TMC, None))


if __name__ == '__main__':
    unittest.main()
