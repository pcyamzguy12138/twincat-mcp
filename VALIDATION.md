# Validation

## Version 0.2.0, 2026-10-28

Environment: Windows 11, Python 3.12.14, MCP SDK 1.30.0, classic XAE shell DTE
15.0 and Scope API 4.48.117.0. No controller was contacted during these tests.

| Check | Evidence |
| --- | --- |
| Ordinary Python suite | 73 cases: 70 passed, 3 skipped (symlink privilege, opt-in Scope API and opt-in XAE build) |
| Windows path containment | Real junction rejection passed; symbolic-link creation unavailable to the test user |
| Native read-only protocol | 13 C# simulated transport/codec checks, plus bridge parser/forbidden API checks |
| Installed Scope API | All 8 Scope tests passed, including native save/reload of 11 primitive types, 2 charts, 3 axes and Chinese labels |
| Installed XAE compiler | All 5 compiler tests passed with integration enabled: task-linked synthetic structure/array project compiled and exported TMC; deliberately undefined identifier failed with file/line diagnostics; source hashes unchanged |
| Package installation | Wheel 0.2.0 includes all four native assets; MCP stdio tests run against installed package with a child-process import-path assertion |
| Recording | Deterministic frames, trigger/prebuffer, archive reopening, CSV escaping, errors/gaps, version change, stop, process cleanup and storage bounds |

To run vendor integration checks separately (Windows, installed dependencies):

```powershell
$env:TWINCAT_MCP_TEST_SCOPE = '1'
python -m unittest discover -s tests -p test_scope_project.py -v
$env:TWINCAT_MCP_TEST_BUILD = '1'
python -m unittest discover -s tests -p test_compiler.py -v
```

Qualification limits: no real PLC reads, live metadata browsing, runtime symbol
resolution or Scope acquisition; no GUI opening; no online write/download/activation.
Real x64 PLC compilation passed; x86 target, alternate IDEs and 4026 install
layouts remain unqualified. The compiler uses an empty system around a copied
PLC project, so original NC/I/O mappings and machine execution are not validated.
Recordings are PC polling, not PLC-cycle or high-speed Scope-server acquisition.

The remaining sections preserve earlier milestone evidence.

## Development expansion, 2026-10-28

- 47 Python cases: 45 passed in the ordinary offline run; two opt-in/platform
  cases skipped (symbolic-link privilege and installed Scope API).
- The separate installed Scope API run passed all 8 Scope-project cases,
  including the opt-in native integration case. It creates/reloads 11 synthetic
  primitive acquisitions, 2 charts and 3 axis groups with Chinese labels.
  API assembly version: 4.48.117.0. No recording/ADS connection is started.
- Real MCP SDK stdio exchanges exercise project inventory/source search,
  hash-checked candidate previews, TMC browsing and Scope planning.
- Source tests cover actions, properties, getters/setters and interface methods;
  unaffected source bytes and original files remain unchanged.
- TMC tests cover namespace/GUID references, enum/alias/structure layouts,
  negative and multidimensional bounds, missing offsets, unsupported pointers,
  inconsistent stride, expansion limits and hostile XML.
- Native serialization exposed and led to fixes for the stdin UTF-8 BOM issue,
  shared Scope time axes and automatic axis name suffixes. The returned group
  map records the actual vendor-assigned labels.

At this earlier milestone, compilation, enhanced diagnostics/recording and
candidate export were still pending; the 0.2 section above supersedes that status.

## Initial release

Version 0.1.0, 2026-10-27, Windows / Python 3.12 / MCP Python SDK 1.30.0.

- 19 Python unittest cases passed: operator configuration, symbol allowlist,
  disabled/demo behavior, XML ST edits and Scope generation.
- A real MCP SDK stdio client initialized the server, listed all six tools,
  read synthetic values, rejected a disallowed symbol, previewed an ST edit,
  and generated a Scope candidate from a synthetic fixture.
- 11 inherited C# simulated-transport checks passed, plus bridge PowerShell
  parsing and forbidden ADS API checks. No native ADS DLL was loaded.
- ST edits preserve the object identity and surrounding XML; tests cover
  methods, GVL/DUT, Unicode, CRLF, BOM, invalid/protected-style input and DTD rejection.
- Scope checks cover idempotency, unique GUIDs, cleared runtime handles,
  explicit exclusions, missing type templates and exact additions.
- Published source is an explicit allowlist of generic code and synthetic examples.

Not performed: live PLC/ADS communication through the new bridge, TwinCAT type
compilation, Scope reopening/online symbol resolution or actual recording.
The minimal Scope fixture is test data, not a complete runnable Scope project.
Offline validation does not establish machine safety or production readiness.
