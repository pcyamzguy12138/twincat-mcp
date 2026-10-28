# Validation

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

Still pending: compilation integration, enhanced runtime diagnostics/recording,
candidate file export, final installed-wheel and release audit. Scope GUI opening
and live resolution/acquisition were not performed. This is milestone evidence,
not completion of the full development roadmap.

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
