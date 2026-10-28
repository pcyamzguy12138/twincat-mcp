import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from twincat_mcp.compiler import Compiler
from twincat_mcp.project import Project
from twincat_mcp.environment import diagnose_environment
from twincat_mcp.tmc import TmcCatalog

XML = '<Project><ItemGroup><Compile Include="Demo.TcPOU"/></ItemGroup></Project>'


class CompilerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'Source'
        source.mkdir()
        (source / 'Demo.plcproj').write_text(XML, encoding='utf-8')
        (source / 'Demo.TcPOU').write_text('<TcPlcObject/>', encoding='utf-8')
        self.project = Project(source)
        self.compiler = Compiler(self.project, self.root / 'Builds')

    def test_disposable_input_copy_and_source_hashes(self):
        before = {p.name: p.read_bytes() for p in self.project.root.iterdir()}
        def invoke(request, timeout):
            path = Path(request['projectPath'])
            self.assertTrue(path.is_relative_to(self.root / 'Builds'))
            self.assertEqual(XML, path.read_text())
            path.write_text('compiler normalized its copy')
            return {'compiled': True, 'lastBuildInfo': 0, 'diagnostics': []}
        with patch.object(self.compiler, '_invoke', side_effect=invoke):
            result = self.compiler.build('Demo.plcproj', self.project.read('Demo.plcproj')[1])
        self.assertTrue(result['sourceUnchanged'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.project.root.iterdir()})
        self.assertEqual(2, len(result['sourceFiles']))
        self.assertTrue((Path(result['directory']) / 'build-result.json').is_file())

    def test_reject_custom_commands_external_references_and_conflicts(self):
        for xml in [XML.replace('</Project>', '<Target Name="Bad"/></Project>'),
                    XML.replace('Demo.TcPOU', '../Outside.TcPOU'), XML.replace('<Compile', '<Compile Condition="x"'),
                    XML.replace('</Project>', '<Import Project="custom.targets"/></Project>'),
                    XML.replace('</Project>', '<PropertyGroup><Derived>$(Custom)</Derived></PropertyGroup></Project>')]:
            (self.project.root / 'Demo.plcproj').write_text(xml)
            with patch.object(self.compiler, '_invoke', side_effect=AssertionError('Must not run compiler')):
                with self.assertRaises(ValueError):
                    self.compiler.build('Demo.plcproj', self.project.read('Demo.plcproj')[1])
        with self.assertRaises(ValueError):
            self.compiler.build('Demo.plcproj', 'stale')
        with self.assertRaises(ValueError):
            Compiler(self.project, self.project.root / 'Builds')

    def test_failures_return_diagnostics_and_exclusive_build(self):
        failure = {'compiled': False, 'lastBuildInfo': 1, 'diagnostics': [{'description': 'Unknown type', 'line': 2}]}
        with patch.object(self.compiler, '_invoke', return_value=failure):
            result = self.compiler.build('Demo.plcproj', self.project.read('Demo.plcproj')[1])
        self.assertFalse(result['compiled'])
        self.assertEqual(2, result['diagnostics'][0]['line'])
        self.compiler.lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, 'in progress'):
                self.compiler.build('Demo.plcproj', self.project.read('Demo.plcproj')[1])
        finally:
            self.compiler.lock.release()

    def test_environment_check_never_starts_process(self):
        with patch('subprocess.run', side_effect=AssertionError('No process')), patch('subprocess.Popen', side_effect=AssertionError('No process')):
            result = diagnose_environment()
        self.assertFalse(result['runtimeConnectionTested'])
        self.assertFalse(result['compilerTested'])
        self.assertTrue(result['checks']['portableEngineering'])


@unittest.skipUnless(os.name == 'nt' and os.environ.get('TWINCAT_MCP_TEST_BUILD') == '1', 'Opt-in installed XAE isolated build; no PLC access')
class InstalledCompilerTest(unittest.TestCase):
    def test_success_generated_tmc_and_error_diagnostics(self):
        with tempfile.TemporaryDirectory(prefix='twincat-build-test-') as temp:
            root = Path(temp)
            source = root / 'Source'
            source.mkdir()
            guid = lambda: '{' + str(uuid.uuid4()) + '}'
            xml = '<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003"><PropertyGroup><FileVersion>1.0.0.0</FileVersion><SchemaVersion>2.0</SchemaVersion><ProjectGuid>' + guid() + '</ProjectGuid><Name>OfflineDemo</Name></PropertyGroup><ItemGroup><Compile Include="MAIN.TcPOU"/><Compile Include="DemoRecord.TcDUT"/><Compile Include="PlcTask.TcTTO"/></ItemGroup></Project>'
            libraries = '<ItemGroup>' + ''.join('<PlaceholderReference Include="' + name + '"><DefaultResolution>' + name + ', * (Beckhoff Automation GmbH)</DefaultResolution><Namespace>' + name + '</Namespace></PlaceholderReference>' for name in ['Tc2_Standard', 'Tc2_System', 'Tc3_Module']) + '</ItemGroup>'
            xml = xml.replace('</Project>', libraries + '</Project>')
            (source / 'OfflineDemo.plcproj').write_text(xml, encoding='utf-8')
            main = '<TcPlcObject Version="1.1.0.1" ProductVersion="3.1.4024.0"><POU Name="MAIN" Id="' + guid() + '" SpecialFunc="None"><Declaration><![CDATA[PROGRAM MAIN\nVAR\nrecord : DemoRecord;\nEND_VAR]]></Declaration><Implementation><ST><![CDATA[record.count := record.count + 1;]]></ST></Implementation></POU></TcPlcObject>'
            (source / 'MAIN.TcPOU').write_text(main, encoding='utf-8')
            dut = '<TcPlcObject Version="1.1.0.1" ProductVersion="3.1.4024.0"><DUT Name="DemoRecord" Id="' + guid() + '"><Declaration><![CDATA[TYPE DemoRecord :\nSTRUCT\ncount : DINT;\nvalues : ARRAY [-1..1] OF INT;\nready : BOOL;\nlabel : STRING(8);\nEND_STRUCT\nEND_TYPE]]></Declaration></DUT></TcPlcObject>'
            (source / 'DemoRecord.TcDUT').write_text(dut, encoding='utf-8')
            task = '<TcPlcObject Version="1.1.0.1"><Task Name="PlcTask" Id="' + guid() + '"><CycleTime>10000</CycleTime><Priority>20</Priority><PouCall><Name>MAIN</Name></PouCall>'
            task += ''.join('<' + tag + '>' + guid() + '</' + tag + '>' for tag in ['TaskFBGuid', 'Fb_init', 'Fb_exit', 'CycleUpdate', 'PostCycleUpdate'])
            (source / 'PlcTask.TcTTO').write_text(task + '<ObjectProperties/></Task></TcPlcObject>', encoding='utf-8')
            project = Project(source)
            compiler = Compiler(project, root / 'Builds')
            result = compiler.build('OfflineDemo.plcproj', project.read('OfflineDemo.plcproj')[1])
            self.assertTrue(result['compiled'], result)
            self.assertTrue(result['sourceUnchanged'])
            self.assertEqual(0, result['lastBuildInfo'])
            catalog = TmcCatalog(Path(result['tmcArtifacts'][0]).read_text(encoding='utf-8-sig'))
            # Real exported primitives/symbols, not a hand-authored XML round trip.
            self.assertGreater(len(catalog.symbols), 0)
            layout = catalog.resolve('DemoRecord')
            self.assertEqual('struct', layout['kind'])
            self.assertEqual(['count', 'values', 'ready', 'label'], [m['name'] for m in layout['members']])
            (source / 'MAIN.TcPOU').write_text(main.replace('record.count + 1', 'UnknownName + 1'), encoding='utf-8')
            failed = compiler.build('OfflineDemo.plcproj', project.read('OfflineDemo.plcproj')[1])
            self.assertFalse(failed['compiled'])
            self.assertNotEqual(0, failed['lastBuildInfo'])
            self.assertTrue(failed['sourceUnchanged'])
            self.assertTrue(any('UnknownName' in d['description'] and d['line'] > 0 for d in failed['diagnostics']), failed)


if __name__ == '__main__':
    unittest.main()
