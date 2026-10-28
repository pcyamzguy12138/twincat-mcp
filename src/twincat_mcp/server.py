"""Local MCP stdio entry point; no listener ports and no controller writes."""
import argparse
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .backend import Backend
from .source import inspect_source, preview_source_edit, preview_source_batch
from .scope import inspect_scope, create_scope_candidate
from .project import Project
from .tmc import inspect_tmc, inspect_tmc_type
from .scope_project import generate_scope_project, plan_scope_from_tmc


def build_server(backend: Backend, project: Project | None = None) -> FastMCP:
    server = FastMCP("twincat-mcp")
    hints = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=hints)
    def describe_configuration() -> dict:
        """Inspect mode and allowed symbols without contacting a controller."""
        return backend.describe()

    @server.tool(annotations=hints)
    def read_symbols(symbols: list[str]) -> dict:
        """Read one bounded ADS snapshot of operator-allowlisted symbols; demo mode returns synthetic data.

        Missing/unsupported symbols and unstable symbol versions remain explicit errors.
        Values are observations, never a guarantee of one atomic PLC scan.
        """
        return backend.read(symbols)

    server.tool(annotations=hints)(inspect_source)
    server.tool(annotations=hints)(preview_source_edit)
    server.tool(annotations=hints)(preview_source_batch)
    server.tool(annotations=hints)(inspect_scope)
    server.tool(annotations=hints)(create_scope_candidate)
    server.tool(annotations=hints)(inspect_tmc)
    server.tool(annotations=hints)(inspect_tmc_type)
    server.tool(annotations=hints)(generate_scope_project)
    server.tool(annotations=hints)(plan_scope_from_tmc)

    def workspace() -> Project:
        if project is None:
            raise ValueError("Project tools require --project-root selected by the operator at startup")
        return project

    @server.tool(annotations=hints)
    def inspect_project(offset: int = 0, limit: int = 100) -> dict:
        """Inventory the configured project, with object/member names and source hashes. Generated directories and links are excluded."""
        return workspace().inventory(offset, limit)

    @server.tool(annotations=hints)
    def read_project_source(path: str) -> dict:
        """Read ST sections of a project-relative TcPOU/TcGVL/TcDUT/TcIO file; binary/protected files are reported unsupported."""
        return workspace().source(path)

    @server.tool(annotations=hints)
    def search_project(query: str, identifier: bool = False, path_filter: str = "*",
                       offset: int = 0, limit: int = 100) -> dict:
        """Search project source; identifier mode excludes strings/comments. Results are lexical occurrences with section-relative lines, not semantic bindings."""
        return workspace().search(query, identifier, path_filter, offset, limit)

    @server.tool(annotations=hints)
    def inspect_project_references(path: str) -> dict:
        """Inspect static .plcproj source/library references for missing, outside-root or duplicate entries; does not evaluate MSBuild."""
        return workspace().references(path)

    @server.tool(annotations=hints)
    def preview_project_edits(changes: list[dict]) -> dict:
        """Preview multiple project source edits against expected hashes; all-or-error, without changing original files."""
        return workspace().preview(changes)

    @server.tool(annotations=hints)
    def browse_project_symbols(path: str, name_filter: str = "*", type_filter: str = "*",
                               expand: bool = False, offset: int = 0, limit: int = 100) -> dict:
        """Browse a project-relative TMC file with type filters and optional structure/array expansion; does not contact PLC."""
        if Path(path).suffix.lower() != ".tmc":
            raise ValueError("Expected .tmc path")
        return inspect_tmc(workspace().read(path)[0], name_filter, type_filter, expand, offset, limit)

    @server.tool(annotations=hints)
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
    parser.add_argument("--project-root", type=Path, help="Read-only engineering directory; tool calls cannot change it")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config else None
        backend = Backend(config, demo=args.demo)
        project = Project(args.project_root) if args.project_root else None
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    build_server(backend, project).run(transport="stdio")


if __name__ == "__main__":
    main()
