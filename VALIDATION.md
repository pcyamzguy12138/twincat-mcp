# Validation

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
