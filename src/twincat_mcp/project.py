"""Read-only engineering workspace constrained to an operator-selected directory."""
from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path, PureWindowsPath
import re
import stat

from .source import inspect_source, preview_source_batch
from .xmlutil import parse_xml

SOURCE_EXTENSIONS = {".tcpou", ".tcgvl", ".tcdut", ".tcio"}
EXTENSIONS = SOURCE_EXTENSIONS | {".plcproj", ".tsproj", ".xti", ".tctto", ".tmc", ".sln", ".tcscopex", ".tcmproj"}
SKIP = {".git", ".venv", "venv", "node_modules", "boot", "_boot", "bin", "obj", "_compileinfo", "_libraries"}


def _is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _page(offset, limit):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Use nonnegative offset and limit 1..500")


def mask_st(text: str) -> str:
    """Blank comments/strings but preserve offsets/newlines for lexical references."""
    result = list(text)
    i = 0
    while i < len(text):
        start = i
        if text.startswith("//", i):
            i = text.find("\n", i)
            if i < 0:
                i = len(text)
        elif text.startswith("(*", i):
            depth, i = 1, i + 2
            while i < len(text) and depth:
                if text.startswith("(*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*)", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif text[i] in "\"'":
            quote, i = text[i], i + 1
            while i < len(text):
                if text[i] == "$":
                    i += 2
                elif text[i] == quote:
                    i += 1
                    if i < len(text) and text[i] == quote:
                        i += 1
                    else:
                        break
                else:
                    i += 1
        else:
            i += 1
            continue
        for j in range(start, min(i, len(text))):
            if text[j] not in "\r\n":
                result[j] = " "
    return "".join(result)


class Project:
    def __init__(self, root: Path | str):
        path = Path(root).absolute()
        for part in (path, *path.parents):
            if _is_link(part):
                raise ValueError("Project root must not contain links or reparse points")
        self.root = path.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Project root must be a directory")

    def file(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or len(relative) > 2048:
            raise ValueError("Expected a project-relative file path")
        normalized = relative.replace("\\", "/")
        if PureWindowsPath(relative).drive or normalized.startswith("/") or any(p in {"", ".", ".."} or ":" in p for p in normalized.split("/")):
            raise ValueError("Paths must stay inside the configured project root")
        current = self.root
        for part in normalized.split("/"):
            if part.casefold() in SKIP:
                raise ValueError("Generated/private directory is excluded")
            current /= part
            if _is_link(current):
                raise ValueError("Links and reparse points are not allowed")
        path = current.resolve(strict=True)
        if not path.is_relative_to(self.root) or not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            raise ValueError("Not a supported engineering file inside the project")
        return path

    def read(self, relative: str) -> tuple[str, str]:
        path = self.file(relative)
        maximum = 32 * 1024 * 1024 if path.suffix.lower() == ".tmc" else 2 * 1024 * 1024
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Engineering file exceeds its size limit")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("File is protected/binary or not UTF-8; export readable source in XAE") from None
        if "\0" in text:
            raise ValueError("File is protected/binary or unsupported UTF-16")
        return text, hashlib.sha256(data).hexdigest()

    def paths(self):
        found = []
        directories = 0
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            directories += 1
            if directories > 10000:
                raise ValueError("Project directory limit exceeded")
            dirs[:] = sorted(d for d in dirs if d.casefold() not in SKIP and not _is_link(Path(directory) / d))
            for name in sorted(files):
                path = Path(directory) / name
                if path.suffix.lower() in EXTENSIONS and not _is_link(path):
                    found.append(path.relative_to(self.root).as_posix())
                    if len(found) > 4000:
                        raise ValueError("More than 4000 engineering files; configure a smaller root")
        return sorted(found, key=str.casefold)

    def inventory(self, offset: int = 0, limit: int = 100) -> dict:
        _page(offset, limit)
        paths = self.paths()
        rows = []
        for path in paths[offset:offset + limit]:
            row = {"path": path, "extension": Path(path).suffix}
            try:
                text, sha = self.read(path)
                row.update(sha256=sha, bytes=len(text.encode("utf-8")))
                if Path(path).suffix.lower() in SOURCE_EXTENSIONS:
                    info = inspect_source(text)
                    row.update(object=info["object"], sections=[{k: v for k, v in s.items() if k != "text"} for s in info["sections"]])
                row["status"] = "readable"
            except (ValueError, OSError) as exc:
                row.update(status="unavailable", reason=str(exc))
            rows.append(row)
        return {"files": rows, "total": len(paths), "nextOffset": offset + limit if offset + limit < len(paths) else None,
                "readOnly": True, "excludedDirectories": sorted(SKIP)}

    def source(self, path: str) -> dict:
        if Path(path).suffix.lower() not in SOURCE_EXTENSIONS:
            raise ValueError("Expected a PLC source object")
        text, sha = self.read(path)
        return inspect_source(text) | {"path": path, "fileSha256": sha}

    def search(self, query: str, identifier: bool = False, path_filter: str = "*",
               offset: int = 0, limit: int = 100) -> dict:
        _page(offset, limit)
        if not isinstance(query, str) or not 1 <= len(query) <= 256 or "\n" in query or "\r" in query:
            raise ValueError("Query must be 1..256 characters on one line")
        if not isinstance(path_filter, str) or len(path_filter) > 512:
            raise ValueError("Invalid path filter")
        if identifier and not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", query):
            raise ValueError("Identifier must be a dotted ST identifier")
        pattern = re.compile(r"(?<![\w])" + re.escape(query) + r"(?![\w])", re.I) if identifier else re.compile(re.escape(query), re.I)
        hits, issues, consumed = [], [], 0
        for path in self.paths():
            if Path(path).suffix.lower() not in SOURCE_EXTENSIONS or not fnmatch.fnmatchcase(path.casefold(), path_filter.casefold()):
                continue
            try:
                text, _ = self.read(path)
                consumed += len(text.encode("utf-8"))
                if consumed > 64 * 1024 * 1024:
                    raise ValueError("Project search exceeds 64 MiB; narrow the path filter")
                info = inspect_source(text)
            except (ValueError, OSError) as exc:
                if consumed > 64 * 1024 * 1024:
                    raise
                issues.append({"path": path, "reason": str(exc)})
                continue
            for section in info["sections"]:
                body = section["text"]
                scan = mask_st(body) if identifier else body
                lines = body.splitlines()
                for match in pattern.finditer(scan):
                    line = scan.count("\n", 0, match.start()) + 1
                    column = match.start() - scan.rfind("\n", 0, match.start())
                    hits.append({"path": path, "object": info["object"]["name"],
                        "member": section["member"], "section": section["section"], "line": line, "column": column,
                        "text": lines[line - 1][:1000], "sourceSha256": info["sourceSha256"]})
                    if len(hits) > 10000:
                        raise ValueError("More than 10000 matches; narrow the query")
        return {"matches": hits[offset:offset + limit], "total": len(hits), "issues": issues,
                "nextOffset": offset + limit if offset + limit < len(hits) else None,
                "evidence": "lexical_occurrences_not_semantic_binding" if identifier else "literal_text",
                "lineBasis": "1-based within the indicated ST section, not the XML file"}

    def references(self, path: str) -> dict:
        if Path(path).suffix.lower() != ".plcproj":
            raise ValueError("Expected a .plcproj file")
        text, sha = self.read(path)
        root = parse_xml(text)
        if root.tag != "Project":
            raise ValueError("Expected MSBuild Project root")
        refs, seen = [], set()
        for item in root.findall(".//Compile"):
            include = item.get("Include", "")
            row = {"include": include, "condition": item.get("Condition")}
            key = include.replace("\\", "/").casefold()
            row["duplicate"] = key in seen
            seen.add(key)
            if not include or any(c in include for c in "$@*?;"):
                row.update(status="unresolved", reason="MSBuild expression/glob requires evaluation")
            else:
                relative = (Path(path).parent / include.replace("\\", "/")).as_posix()
                try:
                    target = self.file(relative)
                    row.update(status="exists", path=target.relative_to(self.root).as_posix())
                except (ValueError, OSError) as exc:
                    row.update(status="unresolved", reason=str(exc))
            refs.append(row)
        return {"path": path, "sourceSha256": sha, "references": refs,
                "libraries": [dict(i.attrib) for tag in ("PlaceholderReference", "LibraryReference") for i in root.iter(tag)],
                "note": "Static references only; MSBuild conditions/imports are not evaluated"}

    def preview(self, changes: list[dict]) -> dict:
        if not isinstance(changes, list) or not 1 <= len(changes) <= 64:
            raise ValueError("Use 1..64 source file changes")
        results, seen = [], set()
        for change in changes:
            if not isinstance(change, dict) or set(change) != {"path", "expected_sha256", "edits"}:
                raise ValueError("Each change requires path, expected_sha256 and edits")
            path = self.file(change["path"])
            if path.suffix.lower() not in SOURCE_EXTENSIONS or path in seen:
                raise ValueError("Expected distinct PLC source files")
            seen.add(path)
            text, sha = self.read(change["path"])
            result = preview_source_batch(text, change["edits"], change["expected_sha256"])
            results.append(result | {"path": path.relative_to(self.root).as_posix(), "sourceSha256": sha})
        # Detect changes occurring while the multi-file preview was generated.
        for result in results:
            if self.read(result["path"])[1] != result["sourceSha256"]:
                raise ValueError("Source changed during preview; regenerate against current files")
        return {"files": results, "writtenToDisk": False,
                "note": "All-or-error preview; no files saved. This is not a compiled project."}
