"""Bounded read-only bridge to the existing C# ADS reader."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import struct
import fnmatch

from .values import decode_snapshot

DEMO_SYMBOLS = {"Demo.Counter": ("DINT", 42), "Demo.Ready": ("BOOL", True), "Demo.Value": ("LREAL", 1.25)}


def validate_symbols(symbols: list[str]) -> list[str]:
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 128:
        raise ValueError("Use 1..128 symbols")
    if any(not isinstance(s, str) or not s.strip() or len(s) > 512 or any(ord(c) < 32 for c in s) for s in symbols):
        raise ValueError("Invalid symbol name")
    if len({s.casefold() for s in symbols}) != len(symbols):
        raise ValueError("Duplicate symbol names")
    return list(symbols)


class Backend:
    def __init__(self, config: dict | None = None, *, demo: bool = False):
        self.demo = demo
        self._lock = threading.Lock()
        self.config = dict(config or {})
        if demo:
            self.symbols = list(DEMO_SYMBOLS)
            return
        allowed = {"enabled", "amsNetId", "adsPort", "timeoutMs", "symbols"}
        if not allowed <= set(self.config) or set(self.config) - allowed - {"allowSymbolBrowse"} or type(self.config["enabled"]) is not bool:
            raise ValueError("Config requires enabled, amsNetId, adsPort, timeoutMs, symbols; optional allowSymbolBrowse")
        if "allowSymbolBrowse" in self.config and type(self.config["allowSymbolBrowse"]) is not bool:
            raise ValueError("allowSymbolBrowse must be a boolean")
        self.symbols = validate_symbols(self.config["symbols"])
        port, timeout = self.config["adsPort"], self.config["timeoutMs"]
        if type(port) is not int or not 851 <= port <= 899:
            raise ValueError("Only PLC ADS ports 851..899 are supported")
        if type(timeout) is not int or not 100 <= timeout <= 5000:
            raise ValueError("Timeout must be 100..5000 ms")
        netid = self.config["amsNetId"]
        if not isinstance(netid, str):
            raise ValueError("Invalid AMS Net ID")
        parts = netid.split(".")
        if len(parts) != 6 or any(not p.isascii() or not p.isdecimal() or not 0 <= int(p) <= 255 for p in parts):
            raise ValueError("AMS Net ID must contain six decimal bytes")
        if self.config["enabled"] and not any(int(p) for p in parts):
            raise ValueError("Replace the placeholder AMS Net ID before enabling reads")

    def describe(self) -> dict:
        return {"mode": "demo" if self.demo else "live" if self.config["enabled"] else "disabled",
                "readOnly": True, "symbols": self.symbols, "maxSymbolsPerRead": 128,
                "captureTimeoutSeconds": 60, "atomicPlcCycleSnapshot": False,
                "allowSymbolBrowse": self.demo or self.config.get("allowSymbolBrowse", False),
                "maxBytesPerSymbol": 4096}

    def check_symbols(self, symbols: list[str]) -> list[str]:
        names = validate_symbols(symbols)
        if not self.demo and not self.config["enabled"]:
            raise ValueError("Live reads are disabled in the operator configuration")
        if not set(names) <= set(self.symbols):
            raise ValueError("Requested symbol is not in the configured allowlist (case-sensitive)")
        return names

    def read(self, symbols: list[str]) -> dict:
        names = self.check_symbols(symbols)
        if not self._lock.acquire(blocking=False):
            raise ValueError("Another capture is in progress")
        try:
            if self.demo:
                now = datetime.now(timezone.utc).isoformat()
                return {"demo": True, "readOnly": True, "StartedUtc": now, "FinishedUtc": now,
                        "VersionStable": True, "AdsState": 5, "DeviceState": 0, "Error": None,
                        "VersionBefore": 0, "VersionAfter": 0,
                        "Samples": [{"Name": n, "Type": DEMO_SYMBOLS[n][0], "Value": DEMO_SYMBOLS[n][1],
                                     "Status": "ok", "Error": None,
                                     "RawHex": struct.pack({"DINT": "<i", "BOOL": "<?", "LREAL": "<d"}[DEMO_SYMBOLS[n][0]], DEMO_SYMBOLS[n][1]).hex()} for n in names]}
            return self._capture(names)
        finally:
            self._lock.release()

    def read_typed(self, symbols: list[str], xml_text: str | None = None, string_encoding: str | None = None) -> dict:
        # Parse user metadata before contacting the target, so malformed input causes no reads.
        from .tmc import TmcCatalog
        catalog = TmcCatalog(xml_text) if xml_text is not None else None
        if string_encoding not in {None, "latin-1", "utf-8"}:
            raise ValueError("STRING encoding must be latin-1 or utf-8")
        return decode_snapshot(self.read(symbols), xml_text, string_encoding, catalog)

    def metadata(self, action: str, symbols: list[str] | None = None, name_filter: str = "*", offset: int = 0, limit: int = 100) -> dict:
        if action not in {"state", "describe", "browse"}:
            raise ValueError("Unsupported metadata action")
        if not self.demo and not self.config["enabled"]:
            raise ValueError("Live reads are disabled in the operator configuration")
        if action == "browse" and not self.demo and not self.config.get("allowSymbolBrowse", False):
            raise ValueError("Full symbol browsing requires operator allowSymbolBrowse=true; value reads still require the allowlist")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Use nonnegative offset and limit 1..500")
        if not isinstance(name_filter, str) or len(name_filter) > 512 or any(ord(c) < 32 for c in name_filter):
            raise ValueError("Invalid symbol filter")
        names = self.check_symbols(self.symbols if symbols is None else symbols) if action == "describe" else []
        if self.demo:
            matched = names if action == "describe" else [s for s in self.symbols if fnmatch.fnmatchcase(s.casefold(), name_filter.casefold())] if action == "browse" else []
            rows = [{"Name": s, "Type": DEMO_SYMBOLS[s][0], "Size": {"BOOL": 1, "DINT": 4, "LREAL": 8}[DEMO_SYMBOLS[s][0]],
                     "Comment": "Synthetic demo symbol", "Error": None} for s in matched]
            result = {"demo": True, "readOnly": True, "AdsState": 5, "DeviceState": 0, "VersionBefore": 0, "VersionAfter": 0,
                      "VersionStable": True, "Error": None, "Symbols": rows[offset:offset + limit] if action == "browse" else rows, "Total": len(rows)}
        else:
            with self._lock:
                result = self._capture(names, action=action, nameFilter=name_filter, offset=offset, limit=limit)
        if action == "browse":
            result["nextOffset"] = offset + limit if offset + limit < result.get("Total", 0) else None
        return result

    def _command(self):
        if os.name != "nt":
            raise RuntimeError("Live ADS reads require Windows and an installed TwinCAT ADS router")
        # The executable and script are fixed; tool input is JSON on stdin, never executable text.
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        script = Path(__file__).parent / "native/Read-Snapshot.ps1"
        return [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)]

    def _request(self, names, **options):
        return {"amsNetId": self.config["amsNetId"], "adsPort": self.config["adsPort"],
                "timeoutMs": self.config["timeoutMs"], "symbols": names, **options}

    def _capture(self, names: list[str], **options) -> dict:
        request = self._request(names, **options)
        try:
            result = subprocess.run(self._command(),
                                    input=json.dumps(request, ensure_ascii=True), capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=60, shell=False,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Read timed out after 60 seconds; bridge process was terminated") from None
        if result.returncode:
            raise RuntimeError("ADS bridge failed: " + (result.stderr or result.stdout).strip()[:2000])
        snapshot = json.loads(result.stdout.lstrip("\ufeff"))
        snapshot["demo"], snapshot["readOnly"] = False, True
        return snapshot

    def stream(self, symbols: list[str], duration_seconds: float, interval_ms: int, max_samples: int, stop: threading.Event):
        """One persistent worker/ADS connection per recording; stdout is bounded JSONL."""
        names = self.check_symbols(symbols)
        if type(duration_seconds) not in {int, float} or not 0.1 <= duration_seconds <= 3600 or type(interval_ms) is not int or not 20 <= interval_ms <= 60000 or type(max_samples) is not int or not 1 <= max_samples <= 100000:
            raise ValueError("Invalid bounded recording limits")
        if self.demo:
            started = time.monotonic()
            for sequence in range(max_samples):
                if stop.is_set() or time.monotonic() - started >= duration_seconds:
                    break
                yield self.read(names) | {"Sequence": sequence, "ElapsedSeconds": time.monotonic() - started}
                if stop.wait(interval_ms / 1000):
                    break
            return
        request = self._request(names, action="stream", durationSeconds=duration_seconds, intervalMs=interval_ms, maxSamples=max_samples)
        process = subprocess.Popen(self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="strict", shell=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        done, timed_out = threading.Event(), threading.Event()

        def watchdog():
            deadline = time.monotonic() + duration_seconds + 60
            while not done.wait(0.05):
                if stop.is_set() or time.monotonic() >= deadline:
                    if not stop.is_set():
                        timed_out.set()
                    if process.poll() is None:
                        try:
                            process.terminate()
                        except OSError:
                            pass
                    return

        watcher = threading.Thread(target=watchdog, daemon=True, name="ads-read-watchdog")
        watcher.start()
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=True))
            process.stdin.close()
            while True:
                line = process.stdout.readline(2 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 2 * 1024 * 1024:
                    raise RuntimeError("Recording worker exceeded its JSON line limit")
                snapshot = json.loads(line.lstrip("\ufeff"))
                snapshot.update(demo=False, readOnly=True)
                yield snapshot
            process.wait(timeout=3)
            if timed_out.is_set():
                raise RuntimeError("Recording worker exceeded its deadline and was terminated")
            if process.returncode and not stop.is_set():
                raise RuntimeError("ADS recording worker failed: " + process.stderr.read(2000))
        finally:
            done.set()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
            watcher.join(timeout=2)
