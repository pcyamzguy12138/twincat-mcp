# Engineering, Scope and read-only diagnostics expansion

User-selected scope: engineering, Scope and read-only diagnostics. No PLC value
writes, downloads, activation, deployment, runtime state changes or route changes.
The 0.1 release is the starting point, not the completion criterion.

## Required capabilities and acceptance evidence

| Capability | Required behavior | Acceptance evidence | Status |
| --- | --- | --- | --- |
| Project inspection | Inventory, source search, object/member inspection, project reference diagnostics; bounded access to explicitly configured roots | Synthetic multi-file project tests; traversal/link rejection; MCP round trip | Implemented; real Windows junction test passed |
| TMC symbols and types | Filter/paginate symbols, resolve aliases/enums/structures/arrays from exported layout; expose uncertainty | Synthetic layout fixtures, actual compiler-generated TMC, architecture-dependent offsets, bounds and malformed-input tests | Implemented and offline-qualified |
| Source editing | Methods, properties, actions and interfaces; batch change previews; identity/format preservation; candidate output with source conflict detection | Exact unchanged-byte checks and candidate bundle round trips, no original overwrite | Implemented and verified |
| Offline compilation | Environment diagnosis and bounded build of an explicitly configured disposable project; structured diagnostics | Real synthetic x64 PLC build and intentional failure; original hashes unchanged | Implemented; classic XAE DTE 15.0 qualified; other install layouts unqualified |
| Read-only runtime | Browse authorized symbol metadata, decode common complex types, state/connection diagnosis, bounded operations | Simulated ADS protocol tests, no write/control APIs; hardware qualification reported separately | Implemented; no live hardware qualification claimed |
| Recording | Start/status/stop, bounded storage, sample timestamps and errors, CSV export, basic timing/edge analysis | Deterministic replay, trigger/prebuffer, cancellation/resource-limit tests, MCP round trip | Implemented and offline-verified |
| Complete Scope generation | Symbol selection from TMC, acquisitions plus chart/axis/channel bindings, independent project artifacts | Vendor-backed schemas/templates, graph/reference checks and installed Scope reopening where available | Implemented; native API 4.48.117.0 reload passed for 11 primitive types; GUI/online acquisition untested |
| Delivery | Updated docs, demo, install/doctor workflow, compatibility and validation matrix, reviewed public release | Installed-package test, privacy allowlist, verified remote commit | 0.2.0 package validated; 46-file publication allowlist passed |

## Implementation rules

- Use only synthetic/public examples in this repository; do not include customer
  source, addresses, routes, recordings, machine configuration or local paths.
- Source/TMC evidence never proves the online program identity or accessibility.
- XML parsing or mocked tests never substitute for an XAE compiler or Scope loader.
- Unsupported types/layouts fail explicitly; no inferred packing or imaginary XML.
- Preserve fixed runtime targets and operator-defined read access.
- Keep the goal open until the full selected scope is implemented and audited;
  a completed milestone is not completion of this roadmap.
