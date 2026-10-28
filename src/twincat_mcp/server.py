"""Local MCP stdio entry point; no listener ports and no controller writes."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from functools import wraps
import json
from pathlib import Path
import threading
from typing import get_type_hints

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .backend import Backend
from .source import inspect_source, preview_source_edit, preview_source_batch
from .scope import inspect_scope, create_scope_candidate
from .project import Project
from .tmc import inspect_tmc, inspect_tmc_type
from .scope_project import generate_scope_project, plan_scope_from_tmc
from .values import decode_tmc_value
from .recording import Recorder
from .environment import diagnose_environment
from .compiler import Compiler


def build_server(backend: Backend, project: Project | None = None, recording_root: Path | None = None,
                 candidate_root: Path | None = None, compiler: Compiler | None = None) -> FastMCP:
    recorder = None
    recorder_lock = threading.Lock()

    def recordings():
        nonlocal recorder
        with recorder_lock:
            if recorder is None:
                recorder = Recorder(backend, recording_root)
            return recorder

    @asynccontextmanager
    async def lifespan(_server):
        try:
            yield {}
        finally:
            if recorder is not None:
                await asyncio.to_thread(recorder.close)

    server = FastMCP("twincat-mcp", lifespan=lifespan)
    hints = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    local_changes = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

    def tool(annotations=hints):
        def register(fn):
            @wraps(fn)
            async def threaded(*args, **kwargs):
                return await asyncio.to_thread(fn, *args, **kwargs)
            threaded.__annotations__ = get_type_hints(fn)
            return server.tool(annotations=annotations)(threaded)
        return register

    @tool()
    def describe_configuration() -> dict:
        """Inspect mode and allowed symbols without contacting a controller."""
        return backend.describe()

    @tool()
    def read_symbols(symbols: list[str]) -> dict:
        """Read one bounded ADS snapshot of operator-allowlisted symbols; demo mode returns synthetic data.

        Missing/unsupported symbols and unstable symbol versions remain explicit errors.
        Values are observations, never a guarantee of one atomic PLC scan.
        """
        return backend.read(symbols)

    for fn in (inspect_source, preview_source_edit, preview_source_batch, inspect_scope,
               create_scope_candidate, inspect_tmc, inspect_tmc_type, generate_scope_project,
               plan_scope_from_tmc, decode_tmc_value, diagnose_environment):
        tool()(fn)

    @tool()
    def read_typed_symbols(symbols: list[str], xml_text: str | None = None, string_encoding: str | None = None) -> dict:
        """Read allowlisted symbols and decode explicit TMC layouts, enums, arrays, strings and time types. Offline TMC identity is not verified against the running PLC."""
        return backend.read_typed(symbols, xml_text, string_encoding)

    @tool()
    def read_runtime_state() -> dict:
        """Read ADS/device state and symbol version from the fixed configured target; no state changes."""
        return backend.metadata("state")

    @tool()
    def describe_symbols(symbols: list[str]) -> dict:
        """Read type, size, address and comments for allowlisted symbols; unavailable entries remain explicit errors."""
        return backend.metadata("describe", symbols)

    @tool()
    def browse_runtime_symbols(name_filter: str = "*", offset: int = 0, limit: int = 100) -> dict:
        """Browse symbol metadata when the operator enabled allowSymbolBrowse; this never grants value-read access. A page fetch reads the bounded symbol table."""
        return backend.metadata("browse", name_filter=name_filter, offset=offset, limit=limit)

    @tool(annotations=local_changes)
    def start_recording(symbols: list[str], duration_seconds: float = 10, interval_ms: int = 100,
                        max_samples: int = 10000, trigger: dict | None = None, pretrigger_samples: int = 0) -> dict:
        """Start bounded read-only ADS polling into local JSONL. One active job; trigger is {symbol,operator,value}, with eq/gt/lt/rising/falling. Returns a session-owned recording id. No Scope recording or controller writes."""
        return recordings().start(symbols, duration_seconds, interval_ms, max_samples, trigger, pretrigger_samples)

    @tool()
    def recording_status(identifier: str) -> dict:
        """Inspect recording status, errors, storage and timing. Unfinished archives from another session do not prove owner liveness."""
        return recordings().status(identifier)

    @tool(annotations=local_changes)
    def stop_recording(identifier: str) -> dict:
        """Request cancellation of this session's owned polling worker. Poll status until terminal; retains local evidence files."""
        return recordings().stop(identifier)

    @tool()
    def read_recording_samples(identifier: str, offset: int = 0, limit: int = 100) -> dict:
        """Page stored JSON samples with timestamps and per-symbol errors; at most 500 frames and 4 MiB per response."""
        return recordings().samples(identifier, offset, limit)

    @tool()
    def export_recording_csv(identifier: str, offset: int = 0, limit: int = 100) -> dict:
        """Return a bounded CSV page, including header, timestamps and errors; follow nextOffset. Spreadsheet formula-like strings are escaped."""
        return recordings().export_csv(identifier, offset, limit)

    @tool()
    def analyze_recording(identifier: str) -> dict:
        """Analyze a finished archive: numeric min/max/mean and observed boolean edges/pulse widths. Polling can miss events and is not PLC-cycle timing."""
        return recordings().analyze(identifier)

    def workspace() -> Project:
        if project is None:
            raise ValueError("Project tools require --project-root selected by the operator at startup")
        return project

    @tool()
    def inspect_project(offset: int = 0, limit: int = 100) -> dict:
        """Inventory the configured project, with object/member names and source hashes. Generated directories and links are excluded."""
        return workspace().inventory(offset, limit)

    @tool()
    def read_project_source(path: str) -> dict:
        """Read ST sections of a project-relative TcPOU/TcGVL/TcDUT/TcIO file; binary/protected files are reported unsupported."""
        return workspace().source(path)

    @tool()
    def search_project(query: str, identifier: bool = False, path_filter: str = "*",
                       offset: int = 0, limit: int = 100) -> dict:
        """Search project source; identifier mode excludes strings/comments. Results are lexical occurrences with section-relative lines, not semantic bindings."""
        return workspace().search(query, identifier, path_filter, offset, limit)

    @tool()
    def inspect_project_references(path: str) -> dict:
        """Inspect static .plcproj source/library references for missing, outside-root or duplicate entries; does not evaluate MSBuild."""
        return workspace().references(path)

    @tool()
    def preview_project_edits(changes: list[dict]) -> dict:
        """Preview multiple project source edits against expected hashes; all-or-error, without changing original files."""
        return workspace().preview(changes)

    @tool(annotations=local_changes)
    def export_project_candidates(changes: list[dict]) -> dict:
        """Export a hash-checked source patch bundle into the configured candidate directory (or retained temp folder), including originals and manifest. Never overwrites source; not a full project or compiled artifact."""
        return workspace().export(changes, candidate_root)

    @tool(annotations=local_changes)
    def compile_plc_project(path: str, expected_sha256: str, platform: str = 'TwinCAT RT (x64)', timeout_seconds: int = 180) -> dict:
        """Compile an explicit PLC project in a disposable copy using a new hidden XAE instance. Requires operator --build-root. Returns compiler diagnostics; excludes system/NC/I/O validation and all controller operations. Custom build commands/imports are rejected."""
        if compiler is None:
            raise ValueError('Offline compilation requires --project-root and separate --build-root selected by the operator at startup')
        return compiler.build(path, expected_sha256, platform, timeout_seconds)

    @tool()
    def browse_project_symbols(path: str, name_filter: str = "*", type_filter: str = "*",
                               expand: bool = False, offset: int = 0, limit: int = 100) -> dict:
        """Browse a project-relative TMC file with type filters and optional structure/array expansion; does not contact PLC."""
        if Path(path).suffix.lower() != ".tmc":
            raise ValueError("Expected .tmc path")
        return inspect_tmc(workspace().read(path)[0], name_filter, type_filter, expand, offset, limit)

    @tool()
    def inspect_project_type(path: str, type_name: str) -> dict:
        """Inspect exported type layout from a project-relative TMC file, retaining explicit offsets, bounds and unresolved types."""
        if Path(path).suffix.lower() != ".tmc":
            raise ValueError("Expected .tmc path")
        return inspect_tmc_type(workspace().read(path)[0], type_name)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="Synthetic values only; never loads ADS")
    mode.add_argument("--config", type=Path, help="Operator-controlled local JSON configuration")
    mode.add_argument("--doctor", action="store_true", help="Print local installation diagnostics and exit; no controller access")
    parser.add_argument("--project-root", type=Path, help="Read-only engineering directory; tool calls cannot change it")
    parser.add_argument("--recording-root", type=Path, help="Local recording archive directory; otherwise a retained temporary directory is created on first use")
    parser.add_argument("--candidate-root", type=Path, help="Separate local directory for source candidate patch bundles")
    parser.add_argument("--build-root", type=Path, help="Opt in to isolated PLC compilation; separate directory for disposable build artifacts")
    args = parser.parse_args()
    if args.doctor:
        print(json.dumps(diagnose_environment(), ensure_ascii=False, indent=2))
        return
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config else None
        backend = Backend(config, demo=args.demo)
        project = Project(args.project_root) if args.project_root else None
        if args.build_root and project is None:
            raise ValueError('--build-root requires --project-root')
        compiler = Compiler(project, args.build_root) if args.build_root else None
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    build_server(backend, project, args.recording_root, args.candidate_root, compiler).run(transport="stdio")


if __name__ == "__main__":
    main()
