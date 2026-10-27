"""Template-based Scope acquisition generation, without inventing Scope schemas."""
from __future__ import annotations

import hashlib
import re
import uuid
from xml.dom import minidom
from xml.parsers import expat

from .backend import validate_symbols

TYPES = {"BIT", "INT8", "UINT8", "INT16", "UINT16", "INT32", "UINT32", "INT64", "UINT64", "REAL32", "REAL64"}


def _child(node, name):
    matches = [n for n in node.childNodes if n.nodeType == n.ELEMENT_NODE and n.tagName == name]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {name} child")
    return matches[0]


def _text(node, name):
    child = _child(node, name)
    if any(n.nodeType == n.ELEMENT_NODE for n in child.childNodes):
        raise ValueError(f"Unexpected nested value in {name}")
    return "".join(n.data for n in child.childNodes if n.nodeType in (n.TEXT_NODE, n.CDATA_SECTION_NODE))


def _set(node, name, value):
    child = _child(node, name)
    for item in list(child.childNodes):
        child.removeChild(item)
    child.appendChild(node.ownerDocument.createTextNode(str(value)))


def _parse(xml_text):
    if not isinstance(xml_text, str) or not xml_text or len(xml_text.encode("utf-8")) > 2 * 1024 * 1024 or "\0" in xml_text:
        raise ValueError("Expected Scope XML text of at most 2 MiB")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", xml_text, re.I):
        raise ValueError("DTD and entity declarations are not supported")
    parser = expat.ParserCreate()
    depth = 0

    def start(*_):
        nonlocal depth
        depth += 1
        if depth > 64:
            raise ValueError("XML nesting limit exceeded")

    def end(*_):
        nonlocal depth
        depth -= 1

    parser.StartElementHandler, parser.EndElementHandler = start, end
    try:
        parser.Parse(xml_text, True)
        document = minidom.parseString(xml_text)
    except expat.ExpatError as exc:
        raise ValueError("Invalid Scope XML: " + str(exc)) from None
    if document.documentElement.tagName != "ScopeProject":
        raise ValueError("Expected ScopeProject root; this Scope format is not supported")
    pool = _child(_child(_child(document.documentElement, "SubMember"), "DataPool"), "SubMember")
    nodes = list(document.getElementsByTagName("AdsAcquisition"))
    symbols = [_text(n, "SymbolName") for n in nodes]
    guids = [_text(n, "Guid") for n in nodes]
    if any(not s.strip() for s in symbols) or len({s.casefold() for s in symbols}) != len(symbols):
        raise ValueError("Source contains missing or duplicate acquisition symbols")
    try:
        parsed_guids = [uuid.UUID(g) for g in guids]
    except ValueError:
        raise ValueError("Source contains an invalid acquisition GUID") from None
    if len(set(parsed_guids)) != len(parsed_guids):
        raise ValueError("Source contains duplicate acquisition GUIDs")
    return document, pool, nodes


def inspect_scope(xml_text: str) -> dict:
    """List symbols and data types in supplied Scope XML without loading Scope or contacting ADS."""
    document, _, nodes = _parse(xml_text)
    try:
        return {"acquisitions": [{"symbol": _text(n, "SymbolName"), "data_type": _text(n, "DataType"),
                                  "guid": _text(n, "Guid")} for n in nodes],
                "count": len(nodes), "onlineAvailability": "not_verified"}
    finally:
        document.unlink()


def create_scope_candidate(xml_text: str, additions: list[dict], excluded_symbols: list[str] | None = None) -> dict:
    """Generate Scope XML by cloning explicit same-type acquisition templates. Return candidate and exact added/skipped/unresolved sets; never write files or connect to ADS."""
    document, pool, nodes = _parse(xml_text)
    try:
        if not isinstance(additions, list) or not 1 <= len(additions) <= 128:
            raise ValueError("Use 1..128 explicit additions")
        for item in additions:
            if not isinstance(item, dict) or not {"symbol", "data_type", "template_symbol"} <= set(item) or set(item) - {"symbol", "data_type", "template_symbol", "comment"}:
                raise ValueError("Each addition requires symbol, data_type, template_symbol, and optional comment")
            validate_symbols([item["symbol"]])
            validate_symbols([item["template_symbol"]])
            if not isinstance(item["data_type"], str) or item["data_type"].upper() not in TYPES:
                raise ValueError("Unsupported requested data type")
            comment = item.get("comment", "")
            if not isinstance(comment, str) or len(comment) > 4096 or any(ord(c) < 32 and c not in "\t\n\r" for c in comment):
                raise ValueError("Invalid comment")
        validate_symbols([i["symbol"] for i in additions])
        if excluded_symbols:
            validate_symbols(excluded_symbols)
        excluded = {s.casefold() for s in excluded_symbols or []}
        original = {_text(n, "SymbolName").casefold(): n for n in nodes}
        existing = dict(original)
        added, skipped, unresolved = [], [], []
        priorities = [int(_text(n, "SortPriority")) for n in nodes]
        priority = max(priorities, default=0)
        for item in additions:
            symbol, kind = item["symbol"], item["data_type"].upper()
            reason = None
            if symbol.casefold() in excluded:
                reason = "symbol is explicitly excluded"
            elif symbol.casefold() in existing:
                if _text(existing[symbol.casefold()], "DataType").upper() != kind:
                    reason = "existing symbol has a different data type"
                else:
                    skipped.append(symbol)
                    continue
            template = original.get(item["template_symbol"].casefold())
            if reason is None and template is None:
                reason = "explicit template symbol was not found in the source"
            if reason is None and _text(template, "DataType").upper() != kind:
                reason = "template data type does not match requested data type"
            if reason is not None:
                unresolved.append({"symbol": symbol, "reason": reason})
                continue
            clone = template.cloneNode(True)
            priority += 1
            changes = {"Guid": str(uuid.uuid4()), "Name": symbol, "SymbolName": symbol,
                       "Comment": item.get("comment", ""), "SortPriority": priority,
                       "Title": f"AdsAcquisition_Generated_{priority}", "SymbolBased": "true",
                       "IndexOffset": "0", "FileHandle": "0", "ServerHandle": "0"}
            try:
                for name, value in changes.items():
                    _set(clone, name, value)
            except ValueError as exc:
                unresolved.append({"symbol": symbol, "reason": "unsupported template: " + str(exc)})
                clone.unlink()
                continue
            pool.appendChild(clone)
            existing[symbol.casefold()] = clone
            added.append(symbol)
        # No-op returns the original text, so repeated requests are exactly idempotent.
        candidate = document.toxml() if added else xml_text
        checked, _, final_nodes = _parse(candidate)
        try:
            final = {_text(n, "SymbolName").casefold(): n for n in final_nodes}
            if set(final) != set(original) | {s.casefold() for s in added}:
                raise ValueError("Unexpected acquisition difference")
            for name, node in original.items():
                if node.toxml() != final[name].toxml():
                    raise ValueError("An original acquisition changed")
        finally:
            checked.unlink()
        return {"xml": candidate, "writtenToDisk": False, "added": added, "skippedExisting": skipped,
                "unresolved": unresolved, "removed": [], "originalCount": len(nodes), "finalCount": len(existing),
                "xmlValid": True, "uniqueGuids": True, "sourceSha256": hashlib.sha256(xml_text.encode("utf-8")).hexdigest(),
                "resultSha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
                "onlineAvailability": "not_verified",
                "note": "DataPool acquisitions only; save a separate candidate and verify it in your Scope version. Chart channels are not generated."}
    finally:
        document.unlink()
