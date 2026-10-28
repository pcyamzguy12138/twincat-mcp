import json
import io
import threading
from pathlib import Path
import unittest
from unittest.mock import patch

from twincat_mcp.backend import Backend, validate_symbols


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((Path(__file__).resolve().parents[1] / "examples/target.example.json").read_text())

    def test_demo_never_starts_process(self):
        with patch("subprocess.run", side_effect=AssertionError("Must not start ADS")):
            result = Backend(demo=True).read(["Demo.Ready", "Demo.Value"])
        self.assertTrue(result["demo"])
        self.assertEqual([True, 1.25], [s["Value"] for s in result["Samples"]])

    def test_disabled_never_starts_process(self):
        with patch("subprocess.run", side_effect=AssertionError("Must not start ADS")):
            backend = Backend(self.config)
            self.assertEqual("disabled", backend.describe()["mode"])
            with self.assertRaisesRegex(ValueError, "disabled"):
                backend.read(["Demo.Value"])

    def test_allowlist_enforced_before_capture(self):
        self.config.update(enabled=True, amsNetId="127.0.0.1.1.1")
        backend = Backend(self.config)
        with patch.object(backend, "_capture", side_effect=AssertionError("No capture")):
            with self.assertRaisesRegex(ValueError, "allowlist"):
                backend.read(["Secret.Value"])

    def test_invalid_names_and_limits(self):
        for names in [[], ["x"] * 129, ["A", "a"], ["a\ncommand"], ["x\0"], [" "], [3]]:
            with self.subTest(names=names), self.assertRaises(ValueError):
                validate_symbols(names)

    def test_invalid_config(self):
        for updates in [dict(enabled="true"), dict(enabled=True), dict(adsPort=10000),
                        dict(timeoutMs=0), dict(amsNetId="999.0.0.1.1.1"), dict(unexpected="x")]:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                Backend(self.config | updates)

    def test_only_one_capture_at_a_time(self):
        backend = Backend(demo=True)
        backend._lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "in progress"):
                backend.read(["Demo.Value"])
        finally:
            backend._lock.release()

    def test_metadata_permissions_and_typed_input_before_native_read(self):
        self.config.update(enabled=True, amsNetId='127.0.0.1.1.1')
        backend = Backend(self.config)
        with patch.object(backend, '_capture', side_effect=AssertionError('No target access')):
            for action, kwargs in [('browse', {}), ('describe', {'symbols': []}), ('describe', {'symbols': ['Other']})]:
                with self.assertRaises(ValueError):
                    backend.metadata(action, **kwargs)
            with self.assertRaises(ValueError):
                backend.read_typed(['Demo.Value'], '<!DOCTYPE a><a/>')
            with self.assertRaises(ValueError):
                backend.read_typed(['Demo.Value'], string_encoding='guess')
        backend = Backend(self.config | {'allowSymbolBrowse': True})
        with patch.object(backend, '_capture', return_value={'Total': 3, 'Symbols': []}) as capture:
            self.assertEqual(2, backend.metadata('browse', offset=1, limit=1)['nextOffset'])
            self.assertEqual('browse', capture.call_args.kwargs['action'])
        with self.assertRaises(ValueError):
            backend.read(['Other'])

    def test_stream_process_closed_on_error_and_early_generator_close(self):
        self.config.update(enabled=True, amsNetId='127.0.0.1.1.1')
        backend = Backend(self.config)
        class Process:
            def __init__(self, output):
                self.stdin, self.stdout, self.stderr = io.StringIO(), io.StringIO(output), io.StringIO()
                self.returncode, self.terminated = None, False
            def poll(self): return self.returncode
            def terminate(self): self.returncode, self.terminated = -1, True
            def kill(self): self.terminate()
            def wait(self, timeout=None):
                self.returncode = self.returncode or 0
                return self.returncode
        for output, valid in [(json.dumps({'Sequence': 0}) + '\n', True), ('broken\n', False), ('x' * (2 * 1024 * 1024 + 1), False)]:
            process = Process(output)
            with patch.object(backend, '_command', return_value=['fixed-test-worker']), patch('subprocess.Popen', return_value=process):
                source = backend.stream(['Demo.Value'], 1, 100, 2, threading.Event())
                if valid:
                    self.assertEqual(0, next(source)['Sequence'])
                    source.close()
                else:
                    with self.assertRaises((ValueError, RuntimeError)):
                        next(source)
            self.assertTrue(process.terminated)
            self.assertTrue(all(p.closed for p in (process.stdin, process.stdout, process.stderr)))


if __name__ == "__main__":
    unittest.main()
