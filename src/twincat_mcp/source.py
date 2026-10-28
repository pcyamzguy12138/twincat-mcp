"""Preserve TwinCAT XML bytes outside one selected ST CDATA section."""
from __future__ import annotations

import difflib
import hashlib
import re
from xml.parsers import expat

MAX_BYTES = 1024 * 1024


def _parse(xml_text: str) -> tuple[bytes, dict, list[dict]]:
    if not isinstance(xml_text, str):
        raise ValueError("Expected XML text, not a path or binary file")
    raw = xml_text.encode("utf-8")
    if not raw or len(raw) > MAX_BYTES or "\0" in xml_text:
        raise ValueError("Expected unprotected XML text of at most 1 MiB")
    declaration = re.match(r'^\ufeff?\s*<\?xml\s+[^?]*encoding\s*=\s*[\'"]([^\'"]+)', xml_text, re.I)
    if declaration and declaration[1].lower().replace("-", "") != "utf8":
        raise ValueError("Only UTF-8 XML declarations are supported; preserve other encodings outside this tool")
    parser = expat.ParserCreate()
    stack, objects, sections = [], [], []
    active_cdata = None

    def reject(*_):
        raise ValueError("DTD and entity declarations are not supported")

    def target():
        tags = [item[0] for item in stack]
        if not tags or tags[0] != "TcPlcObject" or len(tags) < 3 or tags[1] not in {"POU", "GVL", "DUT", "Itf"}:
            return None
        method, member, tail = None, None, tags[2:]
        if len(tags) >= 4 and tags[1] in {"POU", "Itf"} and tags[2] in {"Method", "Action", "Property"}:
            kind, name = tags[2], stack[2][1].get("Name")
            if not name or "/" in name:
                raise ValueError("Member has no valid name")
            member, tail = kind + ":" + name, tags[3:]
            if kind == "Method":
                method = name
            if kind == "Property" and tail and tail[0] in {"Get", "Set"}:
                member += "/" + tail[0]
                tail = tail[1:]
        if tail == ["Declaration"]:
            return {"section": "declaration", "method": method, "member": member}
        if tail == ["Implementation", "ST"] and tags[1] == "POU":
            return {"section": "implementation", "method": method, "member": member}
        return None

    def start(name, attributes):
        stack.append((name, attributes))
        if len(stack) > 32:
            raise ValueError("XML nesting limit exceeded")
        if len(stack) == 1 and name != "TcPlcObject":
            raise ValueError("Expected TcPlcObject root")
        if len(stack) == 2:
            if name not in {"POU", "GVL", "DUT", "Itf"}:
                raise ValueError("Only POU, GVL, DUT and Itf objects are supported")
            objects.append({"kind": name, "name": attributes.get("Name"), "id": attributes.get("Id")})

    def end(_):
        stack.pop()

    def start_cdata():
        nonlocal active_cdata
        active_cdata = (target(), parser.CurrentByteIndex + len(b"<![CDATA["))

    def end_cdata():
        nonlocal active_cdata
        selected, start_index = active_cdata
        if selected is not None:
            sections.append(selected | {"start": start_index, "end": parser.CurrentByteIndex})
        active_cdata = None

    def text(data):
        if active_cdata is None and target() is not None and data.strip():
            raise ValueError("Editable ST must be stored entirely in CDATA")

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.StartCdataSectionHandler, parser.EndCdataSectionHandler = start_cdata, end_cdata
    parser.CharacterDataHandler = text
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    try:
        parser.Parse(raw, True)
    except expat.ExpatError as exc:
        raise ValueError("Not supported unprotected TwinCAT XML: " + str(exc)) from None
    if len(objects) != 1 or not objects[0]["name"] or not objects[0]["id"]:
        raise ValueError("Expected exactly one named object with an existing Id")
    return raw, objects[0], sections


def inspect_source(xml_text: str) -> dict:
    """Inspect unprotected TwinCAT XML and list ST sections including methods, actions, property accessors and interfaces; no file or PLC access."""
    raw, obj, sections = _parse(xml_text)
    return {"object": obj, "sourceSha256": hashlib.sha256(raw).hexdigest(),
            "sections": [{"section": s["section"], "method": s["method"], "member": s["member"],
                          "text": raw[s["start"]:s["end"]].decode("utf-8")} for s in sections],
            "note": "XML structure only; not ST type checking or TwinCAT compilation"}


def preview_source_edit(xml_text: str, section: str, replacement: str, method: str | None = None,
                        member: str | None = None) -> dict:
    """Return a single-section ST edit as XML and diff, retaining object IDs and all bytes outside that CDATA body; never writes files."""
    raw, obj, sections = _parse(xml_text)
    if section not in {"declaration", "implementation"}:
        raise ValueError("Section must be declaration or implementation")
    if not isinstance(replacement, str) or "]]>" in replacement or "\0" in replacement:
        raise ValueError("Replacement must be ST text without NUL or the CDATA terminator")
    if method is not None and member is not None:
        raise ValueError("Use method or member, not both")
    selector = "Method:" + method if method is not None else member
    candidates = [s for s in sections if s["section"] == section and s["member"] == selector]
    if len(candidates) != 1:
        raise ValueError("Expected one matching CDATA section; missing, split or ambiguous sections are not edited")
    selected = candidates[0]
    newline = "\r\n" if "\r\n" in xml_text else "\n"
    replacement = replacement.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)
    updated = raw[:selected["start"]] + replacement.encode("utf-8") + raw[selected["end"]:]
    new_text = updated.decode("utf-8")
    _, after, _ = _parse(new_text)
    if after != obj:
        raise ValueError("Object identity unexpectedly changed")
    return {"object": obj, "writtenToDisk": False, "xml": new_text,
            "sourceSha256": hashlib.sha256(raw).hexdigest(), "resultSha256": hashlib.sha256(updated).hexdigest(),
            "diff": "".join(difflib.unified_diff(xml_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                                fromfile="before.xml", tofile="candidate.xml")),
            "note": "Preview only. Save to a separate copy and compile in TwinCAT before considering use."}


def preview_source_batch(xml_text: str, edits: list[dict], expected_sha256: str) -> dict:
    """Preview 1..128 distinct section edits together, requiring the exact source hash. No files are changed; a failed edit returns no candidate."""
    original = inspect_source(xml_text)
    if expected_sha256 != original["sourceSha256"]:
        raise ValueError("Source conflict: expected SHA256 does not match")
    if not isinstance(edits, list) or not 1 <= len(edits) <= 128:
        raise ValueError("Use 1..128 edits")
    seen = set()
    result = None
    current = xml_text
    for edit in edits:
        if not isinstance(edit, dict) or not {"section", "replacement"} <= set(edit) or set(edit) - {"section", "replacement", "method", "member"}:
            raise ValueError("Edit requires section/replacement and optional method or member")
        selector = edit.get("member") or ("Method:" + edit["method"] if edit.get("method") is not None else None)
        identity = (selector, edit["section"])
        if identity in seen:
            raise ValueError("Multiple edits select the same section")
        seen.add(identity)
        result = preview_source_edit(current, **edit)
        current = result["xml"]
    return result | {"sourceSha256": original["sourceSha256"], "editCount": len(edits),
        "diff": "".join(difflib.unified_diff(xml_text.splitlines(keepends=True), current.splitlines(keepends=True),
                                             fromfile="before.xml", tofile="candidate.xml"))}
