# Engineering workspace and TMC catalog

Start with `twincat-mcp --demo --project-root <your-readable-project-copy>`.
For live values replace `--demo` with your fixed `--config`; the engineering
root is independent of the runtime target. Neither proves they contain the
same PLC program. The service cannot change this root through a tool call.

| Tool | Result |
| --- | --- |
| `inspect_project` | Paged file/object/member inventory with content hashes |
| `read_project_source` | ST declarations and implementations, including member selectors |
| `search_project` | Literal search or identifier occurrences excluding comments and strings |
| `inspect_project_references` | Static .plcproj compile references, duplicates, missing paths and libraries |
| `preview_project_edits` | Multiple hash-checked source changes; returns candidates without writing |
| `export_project_candidates` | Separate originals/candidates and hash manifest |
| `compile_plc_project` | Opt-in isolated PLC build and compiler diagnostics |
| `browse_project_symbols` | Filtered and paged TMC symbols, optionally expanded into primitive leaves |
| `inspect_project_type` | Exported layouts including member offsets and array bounds |

Source search reports a 1-based line **inside the indicated ST section**, not
the enclosing XML. Identifier results are lexical occurrences, not compiler
name resolution or a complete call graph. Conditions, imports, wildcards and
variables in MSBuild references need actual MSBuild/XAE evaluation.

Only recognized engineering extensions are read. Paths cannot escape the root,
use alternate streams, or traverse symbolic links/junctions. Generated folders
including Boot, .git, .venv, bin, obj and _Libraries are excluded. UTF-8/BOM
text is accepted; protected, binary and UTF-16 files are reported, not decoded
speculatively. Limits: 4000 engineering files, 10000 directories, 64 MiB searched
source, 10000 search matches. Search filters narrow the files read.

## TMC types

Text-only equivalents are `inspect_tmc` and `inspect_tmc_type`. No filesystem
root or installed TwinCAT is needed for these tools.

- Namespaced type names and GUID references are supported.
- Aliases, enum values, structure fields and explicit multidimensional array
  bounds are retained; arrays may have negative lower bounds.
- Missing offsets remain unknown. No packing/alignment is guessed.
- Pointers/references, missing definitions, recursive layouts and inherited
  structure expansion remain unresolved. Strings do not become Scope channels.
- Array expansion is bounded to 10000 nodes and requires consistent known
  element stride; larger catalogs can be browsed without expansion.
- Module and data-area labels are retained. A TcCOM data-area name is not
  automatically converted into a PLC ADS symbol path. Runtime accessibility,
  target identity and symbol export permissions remain unverified.

## Multi-file previews

Pass `changes` with one entry per distinct source file:

```json
{
  "path": "POUs/Demo.TcPOU",
  "expected_sha256": "hash returned by read_project_source",
  "edits": [
    {"member": "Property:Value/Get", "section": "implementation", "replacement": "Value := counter;"}
  ]
}
```

Every file is checked against its hash, and checked again before the complete
preview returns. A conflict or unsupported edit fails the whole preview; no
original files are written. This is not an atomic filesystem snapshot and does
not replace checking hashes again before applying candidates or compiling.

## Candidate export

`export_project_candidates` accepts the same changes. Set a separate
`--candidate-root <directory>` or use the retained temporary directory returned.
Each unique bundle includes `originals/`, `candidates/` and `manifest.json`.
BOM/line endings outside replacements remain byte for byte. Sources are checked
again before finalization; incomplete staging keeps a `.pending-` prefix.
No tool applies candidates to originals. This is a source patch bundle, not a
complete runnable project. Candidate content is bounded to 16 MiB per request.

## Environment and offline PLC compilation

`twincat-mcp --doctor` / `diagnose_environment` inspects local Python, ADS DLLs,
Scope API and DTE registration. Presence does not prove routes, licenses or
compiler health. No process or controller connection is started by doctor.

Set both `--project-root <readable-project>` and a separate
`--build-root <disposable-output-directory>` to enable compilation. Pass an
explicit `.plcproj`, its expected SHA-256, platform `TwinCAT RT (x64)` or
`TwinCAT RT (x86)`, and timeout 30..600 seconds. The service copies static
Compile references (up to 512 files/32 MiB), starts its own hidden
`TcXaeShell.DTE.15.0`, creates an empty system and imports the copy.
It never attaches to the user's XAE instance. A Windows job bounds the owned
instance lifetime, including bridge timeout; busy COM calls have bounded retries.

Results include LastBuildInfo, up to 500 diagnostics, generated TMC paths and
original source hashes. Success requires compiler success and nonempty generated
TMC. Artifacts remain in the reported directory; the source is not saved by XAE.
Custom MSBuild commands/imports/expressions, conditional projects, external source paths,
project references and protected/non-UTF-8 objects are rejected. Missing installed
libraries appear as compiler failures. Archive build folders after 32 runs.

This tests PLC type/syntax in an isolated system. It does not validate original
NC/I/O mapping, target licenses, runtime timing or online identity. No downloads
or activation occur. This adapter currently targets the classic 32-bit XAE
shell installation layout; other shells/4026 layouts can appear in doctor but
are not automatically used. x64 here describes the PLC target, not the IDE process.

Vendor references: [PLC import](https://infosys.beckhoff.com/content/1033/tc3_automationinterface/242730891.html),
[compilation](https://infosys.beckhoff.com/content/1033/tc3_automationinterface/21842451979.html),
[COM message filtering](https://infosys.beckhoff.com/content/1033/tc3_automationinterface/242727947.html).
