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
