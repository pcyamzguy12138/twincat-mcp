"""Bounded read-only bridge to the existing C# ADS reader."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import threading

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
        if set(self.config) != allowed or type(self.config["enabled"]) is not bool:
            raise ValueError("Config requires enabled, amsNetId, adsPort, timeoutMs and symbols only")
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
                "captureTimeoutSeconds": 60, "atomicPlcCycleSnapshot": False}

    def read(self, symbols: list[str]) -> dict:
        names = validate_symbols(symbols)
        if not self.demo and not self.config["enabled"]:
            raise ValueError("Live reads are disabled in the operator configuration")
        if not set(names) <= set(self.symbols):
            raise ValueError("Requested symbol is not in the configured allowlist (case-sensitive)")
        if not self._lock.acquire(blocking=False):
            raise ValueError("Another capture is in progress")
        try:
            if self.demo:
                now = datetime.now(timezone.utc).isoformat()
                return {"demo": True, "readOnly": True, "StartedUtc": now, "FinishedUtc": now,
                        "VersionStable": True, "AdsState": 5, "DeviceState": 0, "Error": None,
                        "Samples": [{"Name": n, "Type": DEMO_SYMBOLS[n][0], "Value": DEMO_SYMBOLS[n][1],
                                     "Status": "ok", "Error": None, "RawHex": None} for n in names]}
            return self._capture(names)
        finally:
            self._lock.release()

    def _capture(self, names: list[str]) -> dict:
        if os.name != "nt":
            raise RuntimeError("Live ADS reads require Windows and an installed TwinCAT ADS router")
        # The executable and script are fixed; tool input is JSON on stdin, never executable text.
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        script = Path(__file__).parent / "native/Read-Snapshot.ps1"
        request = {"amsNetId": self.config["amsNetId"], "adsPort": self.config["adsPort"],
                   "timeoutMs": self.config["timeoutMs"], "symbols": names}
        try:
            result = subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
                                    input=json.dumps(request, ensure_ascii=True), capture_output=True,
                                    text=True, encoding="utf-8-sig", timeout=60, shell=False,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Read timed out after 60 seconds; bridge process was terminated") from None
        if result.returncode:
            raise RuntimeError("ADS bridge failed: " + (result.stderr or result.stdout).strip()[:2000])
        snapshot = json.loads(result.stdout)
        snapshot["demo"], snapshot["readOnly"] = False, True
        return snapshot
