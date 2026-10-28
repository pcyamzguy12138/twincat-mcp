"""Opt-in local PLC compilation in a separate disposable directory."""
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

from .project import Project, SOURCE_EXTENSIONS, _is_link
from .xmlutil import parse_xml


class Compiler:
    def __init__(self, project: Project, output_root: Path):
        self.project = project
        root = output_root.absolute()
        for part in (root, *root.parents):
            if part.exists() and _is_link(part):
                raise ValueError('Build root must not contain links/reparse points')
        self.root = root.resolve()
        if self.root.is_relative_to(project.root) or project.root.is_relative_to(self.root):
            raise ValueError('Build root must be separate from the source project tree')
        self.lock = threading.Lock()

    def build(self, path: str, expected_sha256: str, platform: str = 'TwinCAT RT (x64)', timeout_seconds: int = 180) -> dict:
        if platform not in {'TwinCAT RT (x64)', 'TwinCAT RT (x86)'}:
            raise ValueError('Supported local build platforms are TwinCAT RT (x64) and TwinCAT RT (x86)')
        if type(timeout_seconds) is not int or not 30 <= timeout_seconds <= 600:
            raise ValueError('Build timeout must be 30..600 seconds')
        if Path(path).suffix.lower() != '.plcproj':
            raise ValueError('Select an explicit .plcproj; complete solution, NC and runtime deployment are outside this build tool')
        text, sha = self.project.read(path)
        if sha != expected_sha256:
            raise ValueError('PLC project hash conflict')
        xml = parse_xml(text)
        if xml.tag != 'Project':
            raise ValueError('Expected a PLC Project document')
        forbidden = {'Import', 'Target', 'UsingTask', 'Exec', 'ProjectReference', 'PreBuildEvent', 'PostBuildEvent'}
        if any(node.tag in forbidden or 'Condition' in node.attrib for node in xml.iter()):
            raise ValueError('Custom build commands, imports, project references and conditional projects are unsupported')
        if any('$(' in part or '@(' in part for node in xml.iter() for part in [node.text or '', *node.attrib.values()]):
            raise ValueError('MSBuild property/item expressions are unsupported in isolated PLC builds')
        rows = {self.project.file(path).relative_to(self.project.root).as_posix(): (text, sha)}
        for reference in self.project.references(path)['references']:
            if reference['status'] != 'exists' or reference['duplicate']:
                raise ValueError('Unresolved/duplicate Compile reference: ' + reference['include'])
            target = reference['path']
            if Path(target).suffix.lower() not in SOURCE_EXTENSIONS | {'.tctto'}:
                raise ValueError('Unsupported Compile object: ' + target)
            rows[target] = self.project.read(target)
        if len(rows) > 512 or sum(len(t.encode('utf-8')) for t, _ in rows.values()) > 32 * 1024 * 1024:
            raise ValueError('Build input exceeds 512 files or 32 MiB')
        if not self.lock.acquire(blocking=False):
            raise ValueError('An offline build is already in progress')
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if _is_link(self.root) or self.root.resolve() != self.root:
                raise ValueError('Build root changed since startup')
            if sum(1 for _ in self.root.iterdir()) >= 32:
                raise ValueError('Build root has 32 entries; archive old builds elsewhere')
            directory = self.root / uuid.uuid4().hex
            directory.mkdir()
            for relative, (content, _) in rows.items():
                destination = directory / 'Input' / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('xb') as stream:
                    stream.write(content.encode('utf-8'))
            for relative, (_, old_sha) in rows.items():
                if self.project.read(relative)[1] != old_sha:
                    raise ValueError('Source changed while preparing the isolated build')
            request = {'directory': str(directory), 'projectPath': str(directory / 'Input' / path), 'platform': platform}
            result = self._invoke(request, timeout_seconds)
            # The compiler can generate/normalize its copy; verify the source stayed unchanged.
            unchanged = all(self.project.read(relative)[1] == old_sha for relative, (_, old_sha) in rows.items())
            manifest = [{'path': relative, 'sourceSha256': old_sha} for relative, (_, old_sha) in rows.items()]
            result.update(directory=str(directory), sourceFiles=manifest, sourceUnchanged=unchanged,
                          note='Isolated PLC compiler result only; no original system/NC/I/O mapping, download, activation or online validation.')
            if not unchanged:
                result.update(compiled=False, sourceConflict=True)
            (directory / 'build-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            return result
        finally:
            self.lock.release()

    @staticmethod
    def _invoke(request, timeout):
        if os.name != 'nt':
            raise RuntimeError('Offline XAE compilation requires Windows and installed TcXaeShell.DTE.15.0')
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        command = [str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(Path(__file__).parent / 'native/Build-Offline.ps1')]
        try:
            result = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, encoding='utf-8',
                                    timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW, shell=False)
        except subprocess.TimeoutExpired:
            raise RuntimeError('Offline build timed out; bridge terminated and its owned XAE job closed. Build artifacts remain in ' + request['directory']) from None
        if result.returncode:
            raise RuntimeError('Offline build bridge failed: ' + result.stderr.strip()[:3000])
        output = json.loads(result.stdout.lstrip('\ufeff'))
        if type(output.get('compiled')) is not bool or type(output.get('lastBuildInfo')) is not int or output['compiled'] != (output['lastBuildInfo'] == 0 and bool(output.get('tmcArtifacts'))):
            raise RuntimeError('Missing/inconsistent compiler completion evidence')
        return output
