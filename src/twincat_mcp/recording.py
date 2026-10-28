"""Bounded, file-backed read-only polling recordings with explicit evidence limits."""
from __future__ import annotations

from collections import deque
import csv
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path
import re
import tempfile
import threading
import uuid

from .project import _is_link

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_ROOT_BYTES = 256 * 1024 * 1024
TERMINAL = {"completed", "stopped", "failed"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def _trigger(spec, symbols):
    if spec is None:
        return None
    if not isinstance(spec, dict) or set(spec) != {"symbol", "operator", "value"} or spec["symbol"] not in symbols:
        raise ValueError("Trigger requires an acquired symbol, operator and numeric/boolean value")
    if spec["operator"] not in {"eq", "gt", "lt", "rising", "falling"} or type(spec["value"]) not in {int, float, bool} or not math.isfinite(spec["value"]):
        raise ValueError("Invalid trigger comparison")
    return dict(spec)


class _Job:
    def __init__(self, identifier, request, directory):
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.thread = None
        self.directory = directory
        self.summary = {"id": identifier, "state": "recording", "reason": None, "createdUtc": utc(),
            "finishedUtc": None, "request": request, "observedSamples": 0, "storedSamples": 0, "storedBytes": 0,
            "discardedBeforeTrigger": 0, "invalidSamples": 0, "sampleErrors": 0, "version": None,
            "triggerSeen": request["trigger"] is None, "triggerElapsedSeconds": None,
            "lastElapsedSeconds": None, "maxIntervalSeconds": 0.0, "intervalOverruns": 0, "error": None,
            "timeBasis": "engineering_pc_polling_not_plc_cycle", "atomicPlcCycleSnapshot": False}


class Recorder:
    def __init__(self, backend, root: Path | str | None = None):
        self.backend = backend
        self.temporary = root is None
        self.root = Path(root).absolute() if root is not None else Path(tempfile.mkdtemp(prefix="twincat-recordings-"))
        for p in (self.root, *self.root.parents):
            if p.exists() and _is_link(p):
                raise ValueError("Recording root must not contain links/reparse points")
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve(strict=True)
        self._jobs = {}
        self._lock = threading.Lock()
        self._closed = False

    def _directory(self, identifier):
        if not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{32}", identifier) is None:
            raise ValueError("Invalid recording id")
        path = self.root / identifier
        if _is_link(self.root) or _is_link(path) or path.resolve(strict=True).parent != self.root:
            raise ValueError("Recording directory escaped its configured root")
        return path

    def _file(self, identifier, name):
        path = self._directory(identifier) / name
        if path.exists() and _is_link(path):
            raise ValueError("Recording file must not be a link/reparse point")
        return path

    def _persist(self, job):
        temporary = self._file(job.summary["id"], "metadata.tmp")
        final = self._file(job.summary["id"], "metadata.json")
        temporary.write_text(json.dumps(job.summary, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        temporary.replace(final)

    def start(self, symbols: list[str], duration_seconds: float = 10, interval_ms: int = 100,
              max_samples: int = 10000, trigger: dict | None = None, pretrigger_samples: int = 0) -> dict:
        names = self.backend.check_symbols(symbols)
        if type(duration_seconds) not in {float, int} or not math.isfinite(duration_seconds) or not 0.1 <= duration_seconds <= 3600:
            raise ValueError("Duration must be 0.1..3600 seconds, including trigger waiting")
        if type(interval_ms) is not int or not 20 <= interval_ms <= 60000:
            raise ValueError("Polling interval must be 20..60000 ms")
        if type(max_samples) is not int or not 1 <= max_samples <= 100000 or max_samples * len(names) > 200000:
            raise ValueError("Use 1..100000 samples and at most 200000 symbol observations")
        if type(pretrigger_samples) is not int or not 0 <= pretrigger_samples <= 1000 or pretrigger_samples > max_samples:
            raise ValueError("Pretrigger buffer must be 0..1000 samples and no larger than max_samples")
        trigger = _trigger(trigger, names)
        if trigger is None and pretrigger_samples:
            raise ValueError("Pretrigger samples require a trigger")
        request = {"symbols": names, "durationSeconds": duration_seconds, "intervalMs": interval_ms,
                   "maxSamples": max_samples, "trigger": trigger, "pretriggerSamples": pretrigger_samples,
                   "demo": self.backend.demo}
        with self._lock:
            if self._closed:
                raise ValueError("Recorder is closed")
            if any(j.thread and j.thread.is_alive() for j in self._jobs.values()):
                raise ValueError("A recording is already active; stop it before starting another")
            if len(self._jobs) >= 32:
                raise ValueError("Session has 32 recordings; restart the service to retain archives and clear handles")
            paths = list(self.root.iterdir())
            if len(paths) > 1024:
                raise ValueError("Recording directory entry limit exceeded; archive old recordings outside this root")
            used = 0
            for p in paths:
                if p.is_dir() and not _is_link(p) and re.fullmatch(r"[0-9a-f]{32}", p.name):
                    for name in ("samples.jsonl", "metadata.json"):
                        f = p / name
                        if f.is_file() and not _is_link(f):
                            used += f.stat().st_size
            if used + MAX_FILE_BYTES > MAX_ROOT_BYTES:
                raise ValueError("Recording root disk budget exhausted; archive old recordings outside this root")
            identifier = uuid.uuid4().hex
            directory = self.root / identifier
            directory.mkdir()
            job = _Job(identifier, request, directory)
            self._persist(job)
            self._jobs[identifier] = job
            job.thread = threading.Thread(target=self._run, args=(job,), daemon=True, name="twincat-recording-" + identifier[:8])
            job.thread.start()
        return self.status(identifier)

    def _run(self, job):
        request = job.summary["request"]
        pending, pending_bytes, previous_trigger = deque(), 0, None
        reason, error, source = "duration_elapsed", None, None

        def store(stream, frame, encoded):
            if job.summary["storedBytes"] + len(encoded) > MAX_FILE_BYTES:
                raise OverflowError("Recording reached its 32 MiB storage bound")
            stream.write(encoded)
            stream.flush()
            job.summary["storedSamples"] += 1
            job.summary["storedBytes"] += len(encoded)

        try:
            with self._file(job.summary["id"], "samples.jsonl").open("xb") as stream:
                source = self.backend.stream(request["symbols"], request["durationSeconds"], request["intervalMs"], request["maxSamples"], job.stop)
                for frame in source:
                    with job.lock:
                        if job.stop.is_set():
                            reason = "user_stop"
                            break
                        count = job.summary["observedSamples"]
                        if count >= request["maxSamples"]:
                            reason = "sample_limit"
                            break
                        elapsed = frame.get("ElapsedSeconds")
                        last = job.summary["lastElapsedSeconds"]
                        if frame.get("Sequence") != count or type(elapsed) not in {int, float} or not math.isfinite(elapsed) or elapsed < 0 or (last is not None and elapsed < last):
                            raise ValueError("Worker sequence/time is missing or non-monotonic")
                        if frame.get("Error") or frame.get("VersionStable") is not True:
                            job.summary["invalidSamples"] += 1
                            reason = "capture_failed"
                            error = frame.get("Error") or "Unverified symbol version"
                            break
                        before, after = frame.get("VersionBefore"), frame.get("VersionAfter")
                        if type(before) is not int or not 0 <= before <= 0xFFFFFFFF or after != before:
                            raise ValueError("Invalid capture symbol version evidence")
                        if job.summary["version"] is None:
                            job.summary["version"] = before
                        elif before != job.summary["version"]:
                            job.summary["invalidSamples"] += 1
                            reason = "program_changed"
                            break
                        if not isinstance(frame.get("Samples"), list) or [s.get("Name") for s in frame["Samples"]] != request["symbols"]:
                            raise ValueError("Worker returned a different symbol list")
                        encoded = (json.dumps(frame, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
                        if len(encoded) > MAX_FRAME_BYTES:
                            raise ValueError("Worker frame exceeds 2 MiB")
                        job.summary["observedSamples"] += 1
                        job.summary["lastElapsedSeconds"] = elapsed
                        job.summary["sampleErrors"] += sum(s.get("Status") != "ok" for s in frame["Samples"])
                        if last is not None:
                            delta = elapsed - last
                            job.summary["maxIntervalSeconds"] = max(job.summary["maxIntervalSeconds"], delta)
                            if delta > request["intervalMs"] / 1000 * 1.5:
                                job.summary["intervalOverruns"] += 1
                        spec = request["trigger"]
                        if not job.summary["triggerSeen"]:
                            sample = next(s for s in frame["Samples"] if s["Name"] == spec["symbol"])
                            current = sample.get("Value")
                            valid = sample.get("Status") == "ok" and type(current) in {bool, int, float} and math.isfinite(current)
                            fired = False
                            if valid:
                                threshold, op = spec["value"], spec["operator"]
                                fired = (op == "eq" and current == threshold or op == "gt" and current > threshold or op == "lt" and current < threshold or
                                         op == "rising" and previous_trigger is not None and previous_trigger <= threshold < current or
                                         op == "falling" and previous_trigger is not None and previous_trigger >= threshold > current)
                            previous_trigger = current if valid else None
                            if fired:
                                job.summary["triggerSeen"] = True
                                job.summary["triggerElapsedSeconds"] = elapsed
                                for old_frame, old_bytes in pending:
                                    store(stream, old_frame, old_bytes)
                                pending.clear()
                                pending_bytes = 0
                            else:
                                pending.append((frame, encoded))
                                pending_bytes += len(encoded)
                                while len(pending) > request["pretriggerSamples"] or pending_bytes > 2 * 1024 * 1024:
                                    _, old_bytes = pending.popleft()
                                    pending_bytes -= len(old_bytes)
                                    job.summary["discardedBeforeTrigger"] += 1
                                continue
                        store(stream, frame, encoded)
                        if job.summary["observedSamples"] >= request["maxSamples"]:
                            reason = "sample_limit"
                            break
                    # Metadata is an occasional checkpoint; terminal metadata is always persisted.
                    if job.summary["observedSamples"] % 20 == 0:
                        with job.lock:
                            self._persist(job)
        except OverflowError as exc:
            reason, error = "storage_limit", str(exc)
        except Exception as exc:
            reason, error = "worker_error", f"{type(exc).__name__}: {exc}"
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception as exc:
                    reason, error = "worker_cleanup_error", str(exc)
            with job.lock:
                job.summary["discardedBeforeTrigger"] += len(pending)
                if job.stop.is_set() and reason == "duration_elapsed":
                    reason = "user_stop"
                if not job.summary["triggerSeen"] and reason in {"duration_elapsed", "sample_limit"}:
                    reason = "trigger_not_seen"
                if job.summary["observedSamples"] == 0 and reason == "duration_elapsed":
                    reason, error = "no_samples", "Worker completed without a verified sample"
                state = "failed" if error else "completed" if reason in {"duration_elapsed", "sample_limit"} else "stopped"
                job.summary.update(state=state, reason=reason, error=error, finishedUtc=utc())
                try:
                    self._persist(job)
                except Exception as exc:
                    job.summary.update(state="failed", reason="metadata_write_error", error=str(exc))

    def status(self, identifier: str) -> dict:
        job = self._jobs.get(identifier)
        if job is not None:
            with job.lock:
                result = json.loads(json.dumps(job.summary))
        else:
            path = self._file(identifier, "metadata.json")
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("Recording metadata is too large")
            result = json.loads(path.read_text(encoding="utf-8"))
            if result.get("id") != identifier:
                raise ValueError("Recording identity mismatch")
            if result.get("state") not in TERMINAL:
                # A file alone is not evidence its original owner stopped.
                result["state"] = "owner_not_this_session"
                result["note"] = "An unfinished recording belongs to another session; its liveness is not inferred and it is not replayed."
        return result | {"directory": str(self._directory(identifier)), "temporaryStorage": self.temporary}

    def stop(self, identifier: str) -> dict:
        job = self._jobs.get(identifier)
        if job is None:
            raise ValueError("Only this session's recordings can be stopped")
        job.stop.set()
        job.thread.join(timeout=0.1)
        return self.status(identifier)

    def samples(self, identifier: str, offset: int = 0, limit: int = 100) -> dict:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Use nonnegative offset and limit 1..500")
        state = self.status(identifier)
        if state["state"] == "owner_not_this_session":
            raise ValueError("Cannot read an unfinished archive owned by another session")
        count = state["storedSamples"]
        frames, used = [], 0
        path = self._file(identifier, "samples.jsonl")
        if not path.exists() and count == 0:
            return {"frames": [], "offset": offset, "total": 0, "nextOffset": None}
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Recording archive exceeds the storage limit")
        with path.open("rb") as stream:
            for index in range(min(count, offset + limit)):
                line = stream.readline(MAX_FRAME_BYTES + 1)
                if not line.endswith(b"\n") or len(line) > MAX_FRAME_BYTES:
                    raise ValueError("Recording archive is truncated or corrupt")
                if index >= offset:
                    if used + len(line) > 4 * 1024 * 1024:
                        break
                    frames.append(json.loads(line))
                    used += len(line)
        next_offset = offset + len(frames)
        return {"frames": frames, "offset": offset, "total": count,
                "nextOffset": next_offset if next_offset < count else None}

    def export_csv(self, identifier: str, offset: int = 0, limit: int = 100) -> dict:
        page = self.samples(identifier, offset, limit)
        symbols = self.status(identifier)["request"]["symbols"]
        def cell(value):
            text = json.dumps(value, ensure_ascii=False, allow_nan=False) if isinstance(value, (dict, list)) else "" if value is None else str(value)
            return "'" + text if isinstance(value, str) and text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["sequence", "elapsed_seconds", "started_utc", "finished_utc"] + [cell(s + suffix) for s in symbols for suffix in (".value", ".status", ".error")])
        for frame in page["frames"]:
            writer.writerow([frame["Sequence"], frame["ElapsedSeconds"], frame["StartedUtc"], frame["FinishedUtc"]] +
                            [cell(row.get(key)) for row in frame["Samples"] for key in ("Value", "Status", "Error")])
        return {"csv": output.getvalue(), "rows": len(page["frames"]), "total": page["total"], "nextOffset": page["nextOffset"],
                "headerIncluded": True, "spreadsheetStringsEscaped": True, "timeBasis": "engineering_pc_polling_not_plc_cycle"}

    def analyze(self, identifier: str) -> dict:
        state = self.status(identifier)
        if state["state"] not in TERMINAL:
            raise ValueError("Stop the recording before computing a full analysis")
        stats = {s: {"valid": 0, "invalid": 0, "min": None, "max": None, "mean": None,
                     "risingEdges": 0, "fallingEdges": 0, "completeHighPulseSeconds": []} for s in state["request"]["symbols"]}
        previous, high_start, offset, last_time, gaps = {}, {}, 0, None, 0
        gap_threshold = state["request"]["intervalMs"] / 1000 * 1.5
        while True:
            page = self.samples(identifier, offset, 500)
            for frame in page["frames"]:
                if last_time is not None and frame["ElapsedSeconds"] - last_time > gap_threshold:
                    gaps += 1
                    previous.clear()
                    high_start.clear()
                last_time = frame["ElapsedSeconds"]
                for row in frame["Samples"]:
                    name, value = row["Name"], row.get("Value")
                    if name not in stats:
                        raise ValueError("Archive symbol identity mismatch")
                    item = stats[name]
                    if row.get("Status") != "ok" or type(value) not in {bool, int, float} or not math.isfinite(value):
                        item["invalid"] += 1
                        previous.pop(name, None)
                        high_start.pop(name, None)
                        continue
                    item["valid"] += 1
                    item["min"] = value if item["min"] is None else min(item["min"], value)
                    item["max"] = value if item["max"] is None else max(item["max"], value)
                    item["mean"] = float(value) if item["mean"] is None else item["mean"] * (1 - 1 / item["valid"]) + value / item["valid"]
                    if type(value) is bool:
                        if previous.get(name) is False and value:
                            item["risingEdges"] += 1
                            high_start[name] = frame["ElapsedSeconds"]
                        elif previous.get(name) is True and not value:
                            item["fallingEdges"] += 1
                            if name in high_start:
                                duration = frame["ElapsedSeconds"] - high_start.pop(name)
                                item["completeHighPulseCount"] = item.get("completeHighPulseCount", 0) + 1
                                if len(item["completeHighPulseSeconds"]) < 1000:
                                    item["completeHighPulseSeconds"].append(duration)
                        previous[name] = value
                    else:
                        previous.pop(name, None)
                        high_start.pop(name, None)
            if page["nextOffset"] is None:
                break
            offset = page["nextOffset"]
        return {"symbols": stats, "recording": state, "gaps": gaps, "gapThresholdSeconds": gap_threshold,
                "pulseListLimit": 1000,
                "note": "Statistics use valid numeric samples only. Boolean pulse widths are polling estimates, omit incomplete/gapped pulses, and do not prove PLC-cycle timing or absence of missed edges."}

    def close(self):
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
            for job in jobs:
                job.stop.set()
        for job in jobs:
            if job.thread:
                job.thread.join(timeout=5)
