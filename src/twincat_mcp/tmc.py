"""TMC symbol/type inspection. Layout comes from metadata, never guessed packing."""
from __future__ import annotations

import fnmatch
import hashlib
import itertools
import math
import re

from .xmlutil import parse_xml, value, number

# bit size, Scope acquisition type. Time/date values retain their PLC type.
PRIMITIVES = {
    "BOOL": (8, "BIT"), "BIT": (1, "BIT"),
    "SINT": (8, "INT8"), "USINT": (8, "UINT8"), "BYTE": (8, "UINT8"),
    "INT": (16, "INT16"), "UINT": (16, "UINT16"), "WORD": (16, "UINT16"),
    "DINT": (32, "INT32"), "UDINT": (32, "UINT32"), "DWORD": (32, "UINT32"),
    "LINT": (64, "INT64"), "ULINT": (64, "UINT64"), "LWORD": (64, "UINT64"),
    "REAL": (32, "REAL32"), "LREAL": (64, "REAL64"),
    "TIME": (32, "UINT32"), "TOD": (32, "UINT32"), "TIME_OF_DAY": (32, "UINT32"),
    "DATE": (32, "UINT32"), "DT": (32, "UINT32"), "DATE_AND_TIME": (32, "UINT32"),
    "LTIME": (64, "UINT64"), "LDATE": (64, "UINT64"), "LDT": (64, "UINT64"), "LTOD": (64, "UINT64"),
}
MAX_LEAVES = 10000


def _ref(node):
    if node is None:
        return {"name": "", "guid": None}
    name = (node.text or "").strip()
    namespace = node.get("Namespace")
    if namespace and name:
        name = namespace + "." + name
    indirect = any(node.get(k, "false").lower() not in {"false", "0", ""} for k in ("PointerTo", "ReferenceTo"))
    return {"name": name, "guid": (node.get("GUID") or "").casefold() or None, "indirect": indirect}


def _dimensions(node):
    dimensions = []
    for item in node.findall("ArrayInfo"):
        lower, count = number(value(item, "LBound")), number(value(item, "Elements"))
        if lower is None or count is None or not 1 <= count <= 2**31:
            raise ValueError("Invalid TMC array bounds")
        dimensions.append({"lower": lower, "count": count})
    if len(dimensions) > 8:
        raise ValueError("TMC array dimension limit exceeded")
    return dimensions


class TmcCatalog:
    def __init__(self, xml_text: str):
        root = parse_xml(xml_text)
        if root.tag != "TcModuleClass":
            raise ValueError("Expected TcModuleClass TMC root")
        self.sha256 = hashlib.sha256(xml_text.encode("utf-8")).hexdigest()
        self.types, self.guids, self.shortnames = {}, {}, {}
        for node in root.findall("./DataTypes/DataType"):
            ref = _ref(node.find("Name"))
            name = ref["name"]
            if not name or name.casefold() in self.types:
                raise ValueError("Missing or duplicate TMC type name")
            self.types[name.casefold()] = node
            self.shortnames.setdefault(name.rsplit(".", 1)[-1].casefold(), []).append(node)
            if ref["guid"]:
                if ref["guid"] in self.guids:
                    raise ValueError("Duplicate TMC type GUID")
                self.guids[ref["guid"]] = node
        self.symbols = []
        for module_index, module in enumerate(root.findall("./Modules/Module")):
            module_name = value(module, "Name", f"module-{module_index}")
            for area in module.findall("./DataAreas/DataArea"):
                for symbol in area.findall("Symbol"):
                    name = value(symbol, "Name")
                    if not name:
                        raise ValueError("TMC symbol has no name")
                    reference = _ref(symbol.find("BaseType"))
                    if not reference["name"] and not reference["guid"]:
                        reference = _ref(symbol.find("Type"))
                    self.symbols.append({"name": name, "module": module_name,
                        "area": value(area, "Name"), "bitSize": number(value(symbol, "BitSize")),
                        "bitOffset": number(value(symbol, "BitOffs")),
                        "comment": value(symbol, "Comment", ""), "reference": reference,
                        "dimensions": _dimensions(symbol)})
        identities = [(s["module"], s["area"], s["name"].casefold()) for s in self.symbols]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate TMC symbol in one module/data area")

    def _lookup(self, ref):
        if ref.get("guid") in self.guids:
            return self.guids[ref["guid"]]
        name = ref["name"].casefold()
        if name in self.types:
            return self.types[name]
        matches = self.shortnames.get(name, [])
        if len(matches) > 1:
            raise ValueError("Ambiguous TMC type; a namespace or GUID is required")
        return matches[0] if matches else None

    def resolve(self, reference: str | dict, *, _seen=(), _budget=None):
        ref = {"name": reference, "guid": None} if isinstance(reference, str) else reference
        budget = [10000] if _budget is None else _budget
        budget[0] -= 1
        if budget[0] < 0 or len(_seen) >= 24:
            raise ValueError("Type layout expansion limit exceeded")
        name = ref["name"]
        if ref.get("indirect"):
            return {"kind": "unresolved", "name": name, "bitSize": None, "scopeType": None,
                    "reason": "Pointer/reference target layout is not inline data"}
        upper = name.upper()
        if upper in PRIMITIVES:
            bits, scope = PRIMITIVES[upper]
            return {"kind": "primitive", "name": name, "bitSize": bits, "scopeType": scope}
        string = re.fullmatch(r"(W?STRING)(?:\s*\(\s*(\d+)\s*\))?", upper)
        if string:
            # Unspecified length is unresolved; do not assume a compiler default.
            capacity = int(string[2]) if string[2] else None
            return {"kind": "string", "name": name, "bitSize":
                    (capacity + 1) * (16 if string[1] == "WSTRING" else 8) if capacity is not None else None,
                    "encoding": "utf-16-le" if string[1] == "WSTRING" else "unspecified", "scopeType": None}
        node = self._lookup(ref)
        if node is None:
            return {"kind": "unresolved", "name": name, "bitSize": None, "scopeType": None,
                    "reason": "Type definition is absent from this TMC"}
        canonical = _ref(node.find("Name"))["name"]
        if canonical.casefold() in _seen:
            return {"kind": "unresolved", "name": canonical, "bitSize": None,
                    "scopeType": None, "reason": "Recursive type layout"}
        seen = _seen + (canonical.casefold(),)
        recurse = lambda reference: self.resolve(reference, _seen=seen, _budget=budget)
        bits = number(value(node, "BitSize"))
        base = _ref(node.find("BaseType"))
        dims = _dimensions(node)
        result = {"name": canonical, "bitSize": bits, "scopeType": None}
        if node.get("Union", "false").lower() == "true" or node.find("Union") is not None:
            return result | {"kind": "unresolved", "reason": "Union active member cannot be inferred"}
        if dims:
            return result | {"kind": "array", "dimensions": dims, "element": recurse(base)}
        enum = node.findall("EnumInfo")
        if enum:
            layout = recurse(base)
            return result | {"kind": "enum", "base": layout,
                "scopeType": layout["scopeType"], "values": [
                    {"name": value(e, "Text"), "value": number(value(e, "Enum"))} for e in enum]}
        members = node.findall("SubItem")
        if members:
            items = []
            for member in members:
                layout = recurse(_ref(member.find("Type")))
                dimensions = _dimensions(member)
                member_bits = number(value(member, "BitSize"))
                if dimensions:
                    layout = {"kind": "array", "name": layout["name"], "bitSize": member_bits,
                              "scopeType": None, "element": layout, "dimensions": dimensions}
                items.append({"name": value(member, "Name"), "bitOffset": number(value(member, "BitOffs")),
                              "bitSize": member_bits, "type": layout})
            # Inherited layouts need explicit offsets too; do not invent base-class packing.
            return result | {"kind": "struct", "members": items,
                             "baseType": base["name"] or None}
        if base["name"] or base["guid"]:
            layout = recurse(base)
            return result | {"kind": "alias", "base": layout,
                             "scopeType": layout["scopeType"]}
        return result | {"kind": "unresolved", "reason": "No supported data layout"}

    def symbol_layout(self, symbol):
        layout = self.resolve(symbol["reference"])
        if symbol["dimensions"]:
            return {"kind": "array", "name": layout["name"], "bitSize": symbol["bitSize"],
                    "scopeType": None, "dimensions": symbol["dimensions"], "element": layout}
        return layout

    def leaves(self, symbol):
        count = 0

        def visit(name, layout, offset, depth=0):
            nonlocal count
            count += 1
            if count > MAX_LEAVES or depth > 24:
                raise ValueError("Symbol expansion exceeds 10000 nodes or 24 levels; select a smaller TMC")
            kind = layout["kind"]
            if kind == "alias":
                yield from visit(name, layout["base"], offset, depth + 1)
            elif kind == "struct" and not layout.get("baseType"):
                for member in layout["members"]:
                    if not member["name"]:
                        raise ValueError("TMC structure member has no name")
                    member_offset = member["bitOffset"]
                    absolute = offset + member_offset if offset is not None and member_offset is not None else None
                    yield from visit(name + "." + member["name"], member["type"], absolute, depth + 1)
            elif kind == "array":
                dims = layout["dimensions"]
                size = layout["element"]["bitSize"]
                length = math.prod(d["count"] for d in dims)
                if length > MAX_LEAVES:
                    raise ValueError("Array expansion exceeds 10000 elements")
                if layout["bitSize"] is not None and size is not None and layout["bitSize"] != length * size:
                    raise ValueError("Array size/stride mismatch; packing cannot be inferred")
                for i, indices in enumerate(itertools.product(*(range(d["lower"], d["lower"] + d["count"]) for d in dims))):
                    absolute = offset + i * size if offset is not None and size is not None else None
                    yield from visit(name + "[" + ",".join(map(str, indices)) + "]", layout["element"], absolute, depth + 1)
            else:
                yield {"name": name, "type": layout["name"], "kind": kind, "bitSize": layout["bitSize"],
                       "bitOffset": offset, "scopeType": layout["scopeType"],
                       "module": symbol["module"], "area": symbol["area"], "comment": symbol["comment"],
                       "onlineAvailability": "not_verified"}
        yield from visit(symbol["name"], self.symbol_layout(symbol), symbol["bitOffset"])


def inspect_tmc(xml_text: str, name_filter: str = "*", type_filter: str = "*",
                expand: bool = False, offset: int = 0, limit: int = 100) -> dict:
    """Browse exported TMC symbols with wildcard filtering and pagination. Expanded member names are offline evidence, not online discovery."""
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Use nonnegative offset and limit 1..500")
    if any(not isinstance(s, str) or len(s) > 512 for s in (name_filter, type_filter)):
        raise ValueError("Invalid filter")
    catalog = TmcCatalog(xml_text)
    rows = []
    for symbol in catalog.symbols:
        if expand:
            entries = catalog.leaves(symbol)
        else:
            layout = catalog.symbol_layout(symbol)
            entries = [dict(name=symbol["name"], type=layout["name"], kind=layout["kind"],
                bitSize=symbol["bitSize"], bitOffset=symbol["bitOffset"], scopeType=layout["scopeType"],
                module=symbol["module"], area=symbol["area"], comment=symbol["comment"], onlineAvailability="not_verified")]
        for row in entries:
            if fnmatch.fnmatchcase(row["name"].casefold(), name_filter.casefold()) and fnmatch.fnmatchcase(row["type"].casefold(), type_filter.casefold()):
                rows.append(row)
            if len(rows) > MAX_LEAVES:
                raise ValueError("Result exceeds 10000 symbols; narrow filters or disable expansion")
    return {"sourceSha256": catalog.sha256, "symbols": rows[offset:offset + limit], "total": len(rows),
            "nextOffset": offset + limit if offset + limit < len(rows) else None,
            "typeCount": len(catalog.types), "onlineAvailability": "not_verified"}


def inspect_tmc_type(xml_text: str, type_name: str) -> dict:
    """Resolve one exported type including explicit member offsets and array bounds; unsupported layouts remain explicit."""
    if not isinstance(type_name, str) or not type_name or len(type_name) > 512:
        raise ValueError("Expected a type name of at most 512 characters")
    catalog = TmcCatalog(xml_text)
    return {"sourceSha256": catalog.sha256, "type": catalog.resolve(type_name), "onlineAvailability": "not_verified"}
