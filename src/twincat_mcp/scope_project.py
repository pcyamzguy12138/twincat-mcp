"""Complete offline Scope projects created and reloaded by the installed vendor API."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid
from xml.etree import ElementTree as ET

from .scope import TYPES
from .xmlutil import parse_xml, value
from .tmc import TmcCatalog


def _label(text, field):
    if not isinstance(text, str) or not 1 <= len(text) <= 80 or text != text.strip() or any(ord(c) < 32 for c in text):
        raise ValueError(f"{field} must be 1..80 printable characters")
    return text


def validate_scope_request(name: str, signals: list[dict]) -> dict:
    _label(name, "name")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,79}", name) or name.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("Project name must be a portable file stem using ASCII letters, digits, spaces, _ or -")
    if not isinstance(signals, list) or not 1 <= len(signals) <= 128:
        raise ValueError("Use 1..128 Scope signals")
    normalized, seen = [], set()
    for signal in signals:
        if not isinstance(signal, dict) or set(signal) != {"symbol", "scope_type", "ams_net_id", "ads_port", "chart", "axis"}:
            raise ValueError("Each signal requires symbol, scope_type, ams_net_id, ads_port, chart and axis")
        s = dict(signal)
        if not isinstance(s["symbol"], str) or not 1 <= len(s["symbol"]) <= 512 or any(ord(c) < 32 for c in s["symbol"]) or not s["symbol"].strip():
            raise ValueError("Invalid symbol")
        if s["scope_type"] not in TYPES:
            raise ValueError("Unsupported Scope acquisition type")
        parts = s["ams_net_id"].split(".") if isinstance(s["ams_net_id"], str) else []
        if len(parts) != 6 or any(not p.isascii() or not p.isdecimal() or not 0 <= int(p) <= 255 for p in parts):
            raise ValueError("AMS Net ID must contain six decimal bytes; zero is allowed for an offline placeholder")
        s["ams_net_id"] = ".".join(str(int(p)) for p in parts)
        if type(s["ads_port"]) is not int or not 1 <= s["ads_port"] <= 65535:
            raise ValueError("ADS port must be 1..65535")
        _label(s["chart"], "chart")
        _label(s["axis"], "axis")
        identity = (s["ams_net_id"], s["ads_port"], s["symbol"].casefold())
        if identity in seen:
            raise ValueError("Duplicate symbol on the same target/port")
        seen.add(identity)
        normalized.append(s)
    return {"name": name, "signals": normalized}


def validate_scope_graph(xml_text: str, request: dict, group_map: list[dict] | None = None) -> dict:
    root = parse_xml(xml_text, max_bytes=8 * 1024 * 1024)
    if root.tag != "ScopeProject" or value(root, "Name") != request["name"]:
        raise ValueError("Generated Scope project identity mismatch")
    # The vendor serializes shared time axes/styles under multiple axis groups.
    # Identity uniqueness is required for binding-bearing objects, not those shared styles.
    identity_tags = {"ScopeProject", "YTChart", "AxisGroup", "Channel", "AdsAcquisition", "AcquisitionInterpreter"}
    guids = [uuid.UUID(value(n, "Guid")) for n in root.iter() if n.tag in identity_tags and value(n, "Guid")]
    if len(guids) != len(set(guids)):
        raise ValueError("Duplicate Scope object GUID")
    actual = {}
    for node in root.findall("./SubMember/DataPool/SubMember/AdsAcquisition"):
        key = (value(node, "AmsNetId"), int(value(node, "TargetPort")), value(node, "SymbolName"))
        if key in actual:
            raise ValueError("Duplicate generated acquisition")
        if value(node, "SymbolBased") != "true" or any(value(node, f) != "0" for f in ("IndexOffset", "FileHandle", "ServerHandle")):
            raise ValueError("Generated acquisition retains a runtime handle or is not symbolic")
        actual[key] = {"guid": value(node, "Guid"), "type": value(node, "DataType")}
    expected = {(s["ams_net_id"], s["ads_port"], s["symbol"]): s for s in request["signals"]}
    requested_groups = {(s["chart"], s["axis"]) for s in request["signals"]}
    mapped_groups = {k: k for k in requested_groups}
    if group_map is not None:
        mapped_groups = {(g["requestedChart"], g["requestedAxis"]): (g["chart"], g["axis"]) for g in group_map}
        if set(mapped_groups) != requested_groups or len(mapped_groups) != len(group_map) or len(set(mapped_groups.values())) != len(mapped_groups):
            raise ValueError("Generated chart/axis name map is incomplete or ambiguous")
    if set(actual) != set(expected) or any(actual[k]["type"] != expected[k]["scope_type"] for k in actual):
        raise ValueError("Generated acquisition set/types differ from requested signals")
    bindings = []
    groups = {}
    for chart in root.findall("./SubMember/YTChart"):
        for axis in chart.findall("./SubMember/AxisGroup"):
            for channel in axis.findall("./SubMember/Channel"):
                for interpreter in channel.findall("./SubMember/AcquisitionInterpreter"):
                    guid = value(interpreter, "AcquisitionGUID")
                    bindings.append(guid)
                    groups[guid] = (value(chart, "Name"), value(axis, "Name"))
    if sorted(bindings) != sorted(v["guid"] for v in actual.values()):
        raise ValueError("Each acquisition must be bound to exactly one chart channel")
    for key, item in actual.items():
        if groups[item["guid"]] != mapped_groups[(expected[key]["chart"], expected[key]["axis"])]:
            raise ValueError("Channel is bound to the wrong chart/axis group")
    return {"acquisitions": len(actual), "channelBindings": len(bindings), "uniqueBindingGuids": True,
            "charts": len(root.findall("./SubMember/YTChart"))}


def _native_generate(request):
    if os.name != "nt":
        raise RuntimeError("Full Scope generation requires Windows with the Beckhoff Scope API installed")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = Path(__file__).parent / "native/Generate-Scope.ps1"
    try:
        result = subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                                input=json.dumps(request, ensure_ascii=True), capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60, shell=False,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Offline Scope generation timed out; the worker was terminated") from None
    if result.returncode:
        raise RuntimeError("Scope API failed: " + (result.stderr or result.stdout).strip()[:2000])
    return json.loads(result.stdout.lstrip("\ufeff"))


def generate_scope_project(name: str, signals: list[dict]) -> dict:
    """Generate a full YT Scope .tcscopex and .tcmproj with chart/axis/channel bindings through the installed Windows Scope API. Returns XML text, never starts recording or connects a PLC."""
    request = validate_scope_request(name, signals)
    generated = _native_generate(request)
    if generated.get("nativeReloadVerified") is not True:
        raise RuntimeError("Scope native reload was not verified")
    graph = validate_scope_graph(generated["xml"], request, generated.get("groups"))
    scope_name = name + ".tcscopex"
    project = ET.Element("Project", {"ToolsVersion": "4.0", "DefaultTargets": "Build", "xmlns": "http://schemas.microsoft.com/developer/msbuild/2003"})
    group = ET.SubElement(project, "ItemGroup")
    content = ET.SubElement(group, "Content", {"Include": scope_name})
    ET.SubElement(content, "SubType").text = "Content"
    project_text = ET.tostring(project, encoding="unicode", xml_declaration=True)
    artifacts = []
    for filename, xml in ((scope_name, generated["xml"]), (name + ".tcmproj", project_text)):
        artifacts.append({"filename": filename, "xml": xml, "sha256": hashlib.sha256(xml.encode("utf-8")).hexdigest()})
    return {"artifacts": artifacts, "validation": graph, "nativeReloadVerified": True, "groups": generated.get("groups", []),
            "apiVersion": generated["apiVersion"], "writtenToDisk": False,
            "onlineAvailability": "not_verified", "sampling": "UseTaskSampleTime",
            "note": "Save both UTF-8 files in the same new directory. Native Scope model reload passed; online resolution and recording were not tested."}


def plan_scope_from_tmc(xml_text: str, selections: list[dict], ams_net_id: str, ads_port: int) -> dict:
    """Resolve explicitly selected TMC leaf symbols to a full-Scope generation request; report unsupported/ambiguous names without guessing types or target identity."""
    if not isinstance(selections, list) or not 1 <= len(selections) <= 128:
        raise ValueError("Use 1..128 explicitly selected symbols")
    for s in selections:
        if not isinstance(s, dict) or set(s) != {"symbol", "chart", "axis"}:
            raise ValueError("Each selection requires symbol, chart and axis")
        if not isinstance(s["symbol"], str):
            raise ValueError("Symbol must be text")
        _label(s["chart"], "chart")
        _label(s["axis"], "axis")
    if len({s["symbol"].casefold() for s in selections}) != len(selections):
        raise ValueError("Duplicate selected symbols")
    catalog = TmcCatalog(xml_text)
    wanted = {s["symbol"] for s in selections}
    found = {s: [] for s in wanted}
    for symbol in catalog.symbols:
        # Expand only relevant top-level symbols, so unrelated huge arrays do not block selection.
        if any(n == symbol["name"] or n.startswith(symbol["name"] + ".") or n.startswith(symbol["name"] + "[") for n in wanted):
            for leaf in catalog.leaves(symbol):
                if leaf["name"] in found:
                    found[leaf["name"]].append(leaf)
    signals, unresolved = [], []
    for selection in selections:
        matches = found[selection["symbol"]]
        if len(matches) != 1 or matches[0]["scopeType"] not in TYPES:
            unresolved.append({"symbol": selection["symbol"], "reason": "Missing, ambiguous or unsupported TMC leaf"})
        else:
            signals.append(selection | {"scope_type": matches[0]["scopeType"], "ams_net_id": ams_net_id, "ads_port": ads_port})
    if signals:
        signals = validate_scope_request("TmcScope", signals)["signals"]
    return {"signals": signals, "unresolved": unresolved, "sourceSha256": catalog.sha256,
            "ready": not unresolved, "onlineAvailability": "not_verified",
            "note": "Target is explicitly supplied, not inferred from the TMC. Review unresolved entries before generation."}
