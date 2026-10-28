"""Local installation diagnostics. Never starts XAE or contacts a runtime."""
import importlib.metadata
import os
from pathlib import Path
import sys


def diagnose_environment() -> dict:
    """Inspect local Python, Windows, ADS DLL, Scope API and XAE registration without opening a controller connection."""
    windows = os.name == 'nt'
    candidates = {
        'ads64': [r'C:\TwinCAT\Common64\TcAdsDll.dll', r'C:\TwinCAT\AdsApi\TcAdsDll\x64\TcAdsDll.dll'],
        'ads32': [r'C:\TwinCAT\Common32\TcAdsDll.dll', r'C:\TwinCAT\AdsApi\TcAdsDll\TcAdsDll.dll'],
        'scopeApi': [r'C:\TwinCAT\Functions\TE130x-Scope-View\Redist\TwinCAT.Measurement.Scope.API.dll'],
        'powershell': [str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')],
    }
    installed = {name: [p for p in paths if windows and Path(p).is_file()] for name, paths in candidates.items()}
    xae = []
    if windows:
        import winreg
        for progid in ['TcXaeShell.DTE.15.0', 'TcXaeShell.DTE.17.0', 'VisualStudio.DTE.17.0', 'VisualStudio.DTE.16.0', 'VisualStudio.DTE.15.0']:
            for view in [winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY]:
                try:
                    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r'\CLSID', 0, winreg.KEY_READ | view) as key:
                        clsid = winreg.QueryValueEx(key, None)[0]
                    xae.append({'progId': progid, 'clsid': clsid, 'registryView': 32 if view == winreg.KEY_WOW64_32KEY else 64})
                except OSError:
                    continue
    try:
        mcp_version = importlib.metadata.version('mcp')
    except importlib.metadata.PackageNotFoundError:
        mcp_version = None
    os_version = 'Windows ' + '.'.join(str(n) for n in sys.getwindowsversion()[:3]) if windows else sys.platform
    return {'platform': os_version, 'python': sys.version.split()[0], 'pythonBits': 64 if sys.maxsize > 2**32 else 32,
            'mcpVersion': mcp_version, 'installedFiles': installed, 'registeredDte': xae,
            'checks': {'portableEngineering': True, 'demoAvailable': True,
                       'adsPrerequisitesFound': bool(installed['ads64' if sys.maxsize > 2**32 else 'ads32'] and installed['powershell']),
                       'scopeApiFound': bool(installed['scopeApi']), 'dteRegistrationFound': bool(xae)},
            'runtimeConnectionTested': False, 'compilerTested': False,
            'note': 'File/registry presence only. Router state, routes, licenses, execution policy, XAE PLC support and compiler operation are not established by these checks.'}
