"""Bounded little-endian decoding from explicit PLC type metadata."""
from __future__ import annotations

import math
import re
import struct

from .tmc import TmcCatalog

EMPTY_TMC = "<TcModuleClass><DataTypes/></TcModuleClass>"
SIGNED = {"SINT", "INT", "DINT", "LINT"}
TIME_UNITS = {"TIME": "milliseconds", "LTIME": "nanoseconds", "TOD": "milliseconds",
              "TIME_OF_DAY": "milliseconds", "LTOD": "nanoseconds", "DATE": "seconds",
              "DT": "seconds", "DATE_AND_TIME": "seconds", "LDATE": "nanoseconds", "LDT": "nanoseconds"}
EPOCH_TYPES = {"DATE", "DT", "DATE_AND_TIME", "LDATE", "LDT"}


def decode_value(data: bytes, layout: dict, string_encoding: str | None = None):
    """Decode one exact layout. Unknown padding/offsets, inheritance, pointers and unions fail."""
    if string_encoding not in {None, "latin-1", "utf-8"}:
        raise ValueError("STRING encoding must explicitly be latin-1 or utf-8")
    if not isinstance(data, bytes) or not 1 <= len(data) <= 65536:
        raise ValueError("Use 1..65536 raw bytes")
    nodes = 0

    def bits_of(spec, explicit=None):
        bits = spec.get("bitSize")
        if explicit is not None:
            if type(explicit) is not int or explicit <= 0:
                raise ValueError("Invalid explicit member size")
            packed_bool = spec["kind"] == "primitive" and spec["name"].upper() in {"BIT", "BOOL"} and explicit == 1
            if bits is not None and bits != explicit and not packed_bool:
                raise ValueError("Member size does not match its declared type")
            bits = explicit
        if type(bits) is not int or not 1 <= bits <= 65536 * 8:
            raise ValueError("Missing, invalid or oversized type bitSize")
        return bits

    root_bits = bits_of(layout)
    if (root_bits + 7) // 8 != len(data):
        raise ValueError("Raw byte length does not match the exported type layout")

    def unpack(spec, offset, explicit=None, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 10000 or depth > 24:
            raise ValueError("Decode expansion limit exceeded")
        bits = bits_of(spec, explicit)
        if type(offset) is not int or offset < 0 or offset + bits > root_bits:
            raise ValueError("Member lies outside its containing value")
        kind, name = spec["kind"], spec["name"].upper()
        if kind == "unresolved":
            raise ValueError(spec.get("reason", "Unresolved type"))
        if kind == "alias":
            return unpack(spec["base"], offset, bits, depth + 1)
        if kind == "enum":
            result = unpack(spec["base"], offset, bits, depth + 1)
            if type(result) is not int:
                raise ValueError("Enum base must be an integer")
            return {"value": result, "labels": [v["name"] for v in spec["values"] if v["value"] == result]}
        if kind == "array":
            dimensions = spec["dimensions"]
            length = math.prod(d["count"] for d in dimensions)
            element_bits = bits_of(spec["element"])
            if length > 10000 or length < 1 or length * element_bits != bits:
                raise ValueError("Array shape, stride or expansion limit is inconsistent")
            return {"lowerBounds": [d["lower"] for d in dimensions], "shape": [d["count"] for d in dimensions],
                    "values": [unpack(spec["element"], offset + i * element_bits, depth=depth + 1) for i in range(length)]}
        if kind == "struct":
            if spec.get("baseType"):
                raise ValueError("Inherited structures require a verified flattened layout")
            result, ranges = {}, []
            for member in spec["members"]:
                start = member["bitOffset"]
                size = bits_of(member["type"], member.get("bitSize"))
                if type(start) is not int or start < 0 or start + size > bits:
                    raise ValueError("Missing or out-of-range structure member offset")
                if any(start < end and start + size > begin for begin, end in ranges):
                    raise ValueError("Overlapping structure members need explicit union semantics")
                ranges.append((start, start + size))
                member_name = member["name"]
                if not member_name or member_name.casefold() in {n.casefold() for n in result}:
                    raise ValueError("Missing or duplicate structure member name")
                result[member_name] = unpack(member["type"], offset + start, size, depth + 1)
            return result
        if kind == "string":
            wide = name.startswith("WSTRING")
            unit = 16 if wide else 8
            if offset % 8 or bits % unit:
                raise ValueError("String layout must be byte/code-unit aligned")
            raw = data[offset // 8:(offset + bits) // 8]
            if wide:
                units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
                if 0 not in units:
                    raise ValueError("WSTRING has no terminator inside its declared capacity")
                units = units[:units.index(0)]
                if any(0xD800 <= n <= 0xDFFF for n in units):
                    raise ValueError("WSTRING UCS-2 does not support surrogate code units")
                return "".join(chr(n) for n in units)
            if string_encoding is None:
                raise ValueError("STRING requires an explicit latin-1 or utf-8 encoding")
            if b"\0" not in raw:
                raise ValueError("STRING has no terminator inside its declared capacity")
            return raw.split(b"\0", 1)[0].decode(string_encoding, errors="strict")
        if kind != "primitive":
            raise ValueError("Unsupported type layout")
        if name in {"REAL", "LREAL"}:
            if offset % 8 or bits not in {32, 64}:
                raise ValueError("Unsupported float alignment/size")
            result = struct.unpack("<f" if bits == 32 else "<d", data[offset // 8:(offset + bits) // 8])[0]
            if not math.isfinite(result):
                raise ValueError("Non-finite PLC floating-point value")
            return result
        first, last = offset // 8, (offset + bits + 7) // 8
        result = (int.from_bytes(data[first:last], "little") >> (offset % 8)) & ((1 << bits) - 1)
        if name in {"BOOL", "BIT"}:
            return bool(result)
        if name in SIGNED and result & (1 << (bits - 1)):
            result -= 1 << bits
        if name in TIME_UNITS:
            decoded = {"value": result, "unit": TIME_UNITS[name], "plcType": name}
            if name in EPOCH_TYPES:
                decoded["epoch"] = "1970-01-01 (no timezone conversion)"
            return decoded
        return result

    return unpack(layout, 0)


def decode_tmc_value(type_name: str, raw_hex: str, xml_text: str | None = None,
                     string_encoding: str | None = None) -> dict:
    """Decode supplied raw bytes using primitive types or exported TMC layouts. Offline layout consistency does not establish the online program identity."""
    catalog = TmcCatalog(xml_text if xml_text is not None else EMPTY_TMC)
    return _decode(catalog, type_name, raw_hex, xml_text is not None, string_encoding, {})


def _decode(catalog, type_name, raw_hex, has_tmc, string_encoding, layouts):
    if not isinstance(type_name, str) or not 1 <= len(type_name) <= 512:
        raise ValueError("Expected a PLC type name")
    if not isinstance(raw_hex, str) or not 2 <= len(raw_hex) <= 131072 or len(raw_hex) % 2 or re.fullmatch(r"[0-9A-Fa-f]+", raw_hex) is None:
        raise ValueError("Expected 1..65536 bytes of hexadecimal without separators")
    if type_name not in layouts:
        layouts[type_name] = catalog.resolve(type_name)
    layout = layouts[type_name]
    return {"type": type_name, "value": decode_value(bytes.fromhex(raw_hex), layout, string_encoding),
            "layoutSourceSha256": catalog.sha256 if has_tmc else None,
            "layoutVerification": "offline_tmc_not_verified_against_runtime" if has_tmc else "explicit_primitive_type",
            "byteLength": len(raw_hex) // 2, "stringEncoding": string_encoding}


def decode_snapshot(snapshot: dict, xml_text: str | None, string_encoding: str | None,
                    catalog=None, layouts=None) -> dict:
    """Preserve transport errors. A successful typed decode is separate from online identity verification."""
    result = dict(snapshot)
    result["Samples"] = [dict(row) for row in snapshot["Samples"]]
    if snapshot.get("VersionStable") is not True or snapshot.get("Error"):
        return result
    catalog = catalog or TmcCatalog(xml_text if xml_text is not None else EMPTY_TMC)
    layouts = layouts if layouts is not None else {}
    for row in result["Samples"]:
        if row.get("Status") not in {"ok", "unsupported"} or row.get("RawHex") is None:
            continue
        try:
            decoded = _decode(catalog, row["Type"], row["RawHex"], xml_text is not None, string_encoding, layouts)
            row.update(Value=decoded["value"], Status="ok", Error=None, Decode=decoded | {"value": None})
        except (ValueError, UnicodeError) as exc:
            row.update(Value=None, Status="unsupported", Error=str(exc))
    return result
