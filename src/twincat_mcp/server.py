"""Local MCP stdio entry point; no listener ports and no controller writes."""
import argparse
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .backend import Backend
from .source import inspect_source, preview_source_edit
from .scope import inspect_scope, create_scope_candidate


def build_server(backend: Backend) -> FastMCP:
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
    server.tool(annotations=hints)(inspect_scope)
    server.tool(annotations=hints)(create_scope_candidate)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="Synthetic values only; never loads ADS")
    mode.add_argument("--config", type=Path, help="Operator-controlled local JSON configuration")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config else None
        backend = Backend(config, demo=args.demo)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    build_server(backend).run(transport="stdio")


if __name__ == "__main__":
    main()
