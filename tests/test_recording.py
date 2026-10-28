import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from twincat_mcp.backend import Backend
from twincat_mcp.recording import Recorder, TERMINAL


def frames(values):
    backend = Backend(demo=True)
    result = []
    for i, value in enumerate(values):
        frame = backend.read(['Demo.Ready']) | {'Sequence': i, 'ElapsedSeconds': i * 0.1}
        frame['Samples'][0]['Value'] = value
        result.append(frame)
    return result


class Replay(Backend):
    def __init__(self, data, wait=False):
        super().__init__(demo=True)
        self.data, self.wait, self.closed = data, wait, threading.Event()

    def stream(self, symbols, duration_seconds, interval_ms, max_samples, stop):
        try:
            for frame in self.data:
                yield copy.deepcopy(frame)
            if self.wait:
                stop.wait(3)
        finally:
            self.closed.set()


class RecordingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.recorders = []

    def tearDown(self):
        for recorder in self.recorders:
            recorder.close()
        self.temp.cleanup()

    def recorder(self, data, wait=False):
        recorder = Recorder(Replay(data, wait), self.root)
        self.recorders.append(recorder)
        return recorder

    def complete(self, recorder, **kwargs):
        info = recorder.start(['Demo.Ready'], **kwargs)
        recorder._jobs[info['id']].thread.join(3)
        info = recorder.status(info['id'])
        self.assertIn(info['state'], TERMINAL, info)
        self.assertTrue(recorder.backend.closed.is_set())
        return info

    def test_replay_paging_csv_statistics_and_reopen(self):
        recorder = self.recorder(frames([False, True, True, False, True]))
        info = self.complete(recorder)
        self.assertEqual('completed', info['state'])
        self.assertEqual(5, info['storedSamples'])
        first = recorder.samples(info['id'], limit=2)
        self.assertEqual(2, first['nextOffset'])
        self.assertEqual(2, recorder.samples(info['id'], 2, 2)['frames'][0]['Sequence'])
        rows = list(csv.reader(io.StringIO(recorder.export_csv(info['id'])['csv'])))
        self.assertEqual(6, len(rows))
        self.assertEqual('elapsed_seconds', rows[0][1])
        analysis = recorder.analyze(info['id'])['symbols']['Demo.Ready']
        self.assertEqual(2, analysis['risingEdges'])
        self.assertEqual(1, analysis['fallingEdges'])
        self.assertAlmostEqual(0.2, analysis['completeHighPulseSeconds'][0])
        other = self.recorder([])
        self.assertEqual('completed', other.status(info['id'])['state'])
        self.assertEqual(5, other.samples(info['id'])['total'])
        with self.assertRaises(ValueError):
            other.stop(info['id'])

    def test_trigger_keeps_bounded_prebuffer(self):
        recorder = self.recorder(frames([False, False, False, True, False]))
        info = self.complete(recorder, trigger={'symbol': 'Demo.Ready', 'operator': 'rising', 'value': 0}, pretrigger_samples=2)
        self.assertTrue(info['triggerSeen'])
        self.assertEqual(1, info['discardedBeforeTrigger'])
        self.assertEqual([1, 2, 3, 4], [f['Sequence'] for f in recorder.samples(info['id'])['frames']])

    def test_no_trigger_no_samples_and_sample_limit(self):
        recorder = self.recorder(frames([False, False]))
        info = self.complete(recorder, trigger={'symbol': 'Demo.Ready', 'operator': 'eq', 'value': True}, pretrigger_samples=2)
        self.assertEqual('trigger_not_seen', info['reason'])
        self.assertEqual(0, info['storedSamples'])
        self.assertEqual(2, info['discardedBeforeTrigger'])
        recorder = self.recorder([])
        self.assertEqual('failed', self.complete(recorder)['state'])
        recorder = self.recorder(frames([False, True, False]))
        info = self.complete(recorder, max_samples=2)
        self.assertEqual('sample_limit', info['reason'])
        self.assertEqual(2, info['storedSamples'])

    def test_version_change_and_failed_capture_never_stored(self):
        for failure in ['change', 'transport', 'unstable', 'sequence', 'backwards']:
            data = frames([False, True])
            if failure == 'change':
                data[1].update(VersionBefore=1, VersionAfter=1)
            elif failure == 'transport':
                data[1]['Error'] = 'Disconnected'
            elif failure == 'unstable':
                data[1]['VersionStable'] = False
            elif failure == 'sequence':
                data[1]['Sequence'] = 9
            else:
                data[1]['ElapsedSeconds'] = -1
            with self.subTest(failure=failure):
                recorder = self.recorder(data)
                info = self.complete(recorder)
                self.assertEqual(1, info['storedSamples'])
                self.assertNotEqual('completed', info['state'])

    def test_error_and_time_gap_break_pulse_continuity(self):
        for failure in ['gap', 'error']:
            data = frames([False, True, True, False])
            if failure == 'gap':
                data[2]['ElapsedSeconds'] = 1
                data[3]['ElapsedSeconds'] = 1.1
            else:
                data[2]['Samples'][0].update(Status='error', Value=None, Error='ADS 1808')
            recorder = self.recorder(data)
            info = self.complete(recorder)
            analysis = recorder.analyze(info['id'])
            self.assertEqual([], analysis['symbols']['Demo.Ready']['completeHighPulseSeconds'])
            self.assertEqual(1 if failure == 'gap' else 0, analysis['gaps'])

    def test_mean_of_large_opposite_values_remains_finite(self):
        recorder = self.recorder(frames([1e308, -1e308]))
        info = self.complete(recorder)
        self.assertEqual(0, recorder.analyze(info['id'])['symbols']['Demo.Ready']['mean'])

    def test_stop_closes_owned_worker_and_rejects_second_start(self):
        recorder = self.recorder(frames([True]), wait=True)
        info = recorder.start(['Demo.Ready'])
        with self.assertRaises(ValueError):
            recorder.start(['Demo.Ready'])
        other = self.recorder([])
        self.assertEqual('owner_not_this_session', other.status(info['id'])['state'])
        with self.assertRaises(ValueError):
            other.samples(info['id'])
        recorder.stop(info['id'])
        recorder._jobs[info['id']].thread.join(2)
        self.assertTrue(recorder.backend.closed.is_set())
        self.assertEqual('user_stop', recorder.status(info['id'])['reason'])

    def test_limits_storage_corruption_and_formula_escaping(self):
        recorder = self.recorder(frames(['=1+1']))
        for kwargs in [{'interval_ms': 1}, {'duration_seconds': float('nan')}, {'max_samples': 0},
                       {'pretrigger_samples': 1}, {'trigger': {'symbol': 'Other'}}]:
            with self.assertRaises(ValueError):
                recorder.start(['Demo.Ready'], **kwargs)
        with self.assertRaises(ValueError):
            recorder.status('../outside')
        info = self.complete(recorder)
        self.assertIn("'=1+1", recorder.export_csv(info['id'])['csv'])
        (self.root / info['id'] / 'samples.jsonl').write_text('broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            recorder.samples(info['id'])
        recorder = self.recorder(frames([False, True]))
        with patch('twincat_mcp.recording.MAX_FILE_BYTES', 1):
            info = self.complete(recorder)
        self.assertEqual('storage_limit', info['reason'])
        self.assertEqual(0, info['storedSamples'])


if __name__ == '__main__':
    unittest.main()
