import csv
import importlib.util
import io
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

from analyzer import SessionAnalyzer
from capture_timing import FirstFrameClock
from review import review_events, review_events_page

ROOT = Path(__file__).resolve().parents[1]


def isolated_import(filename, dependencies):
    spec = importlib.util.spec_from_file_location('_test_' + filename, ROOT / (filename + '.py'))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(module)
    return module


class SessionFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def metadata(self, **data):
        (self.folder / 'session_metadata.json').write_text(json.dumps(data))

    def events(self, rows):
        with (self.folder / 'events.csv').open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['timestamp_s', 'event_type', 'detail'])
            for ts, kind, detail in rows:
                writer.writerow([ts, kind, json.dumps(detail)])

    def test_idle_tail_counts_and_setup_cleanup_events_do_not(self):
        self.metadata(task_start_timestamp_s=5, task_end_timestamp_s=65)
        self.events([(2, 'mouse_click', {'pressed': True}),
                     (40, 'mouse_click', {'pressed': True}),
                     (66, 'mouse_click', {'pressed': True})])
        result = SessionAnalyzer(self.folder).analyze()
        self.assertEqual(result['task_duration_seconds'], 60)
        self.assertEqual(result['total_clicks'], 1)
        self.assertEqual(result['clicks_per_minute'], 1)
        self.assertEqual(result['duration_source'], 'monotonic_task_boundaries')

    def test_completely_idle_task(self):
        self.metadata(task_start_timestamp_s=5, task_end_timestamp_s=65)
        self.events([])
        self.assertEqual(SessionAnalyzer(self.folder).analyze()['task_duration_seconds'], 60)

    def test_legacy_wallclock_is_labeled_estimate(self):
        self.metadata(session_start_wall_clock='2026-01-01T00:00:00',
                      session_end_wall_clock='2026-01-01T00:01:00')
        self.events([(40, 'key_press', {})])
        result = SessionAnalyzer(self.folder).analyze()
        self.assertEqual(result['task_duration_seconds'], 60)
        self.assertEqual(result['duration_source'], 'legacy_wall_clock_estimate')

    def test_all_pages_include_tail_and_no_key_content(self):
        self.metadata(screen_first_frame_timestamp_s=1, screen_capture_backend='ffmpeg')
        self.events([(0.5, 'mouse_click', {'pressed': True})] +
                    [(i + 1, 'key_press', {'key': 'private'}) for i in range(4501)])
        events, cursor = [], 0
        while cursor is not None:
            page = review_events_page(self.folder, cursor)
            events.extend(page['events'])
            cursor = page['next_cursor']
        self.assertEqual(len(events), 4501)
        self.assertEqual(events[-1]['time'], 4500)
        self.assertNotIn('private', json.dumps(events))
        self.assertEqual(events, review_events(self.folder))

    def test_exact_page_and_invalid_rows(self):
        self.metadata()
        self.events([(0, 'key_press', []), (float('nan'), 'key_press', {}),
                     (1, 'mouse_click', {'pressed': False})] +
                    [(i, 'key_press', {}) for i in range(2000)])
        page = review_events_page(self.folder)
        self.assertEqual(len(page['events']), 2000)
        self.assertIsNone(page['next_cursor'])
        with self.assertRaises(ValueError):
            review_events_page(self.folder, -1)

    def test_stop_boundary_saved_before_slow_cleanup_and_analysis(self):
        dependencies = {name: Mock() for name in
                        ('input_logger', 'screen_recorder', 'pupil_trigger', 'window_tracker', 'web_tracker')}
        module = isolated_import('session_controller', dependencies)
        controller = module.SessionController(self.folder)
        controller.session_dir = self.folder
        controller.t0 = 100
        controller._session_active = True
        controller.metadata = {'task_start_timestamp_s': 5}
        controller.input_logger = Mock()
        self.events([(40, 'key_press', {})])
        with patch.object(module.time, 'monotonic', return_value=165), \
             patch.object(module, 'batch_analyze_sessions'):
            result = controller.stop_session()
        self.assertEqual(result['task_duration_seconds'], 60)
        self.assertEqual(result['analysis_metrics']['task_duration_seconds'], 60)
        self.assertEqual(json.loads((self.folder / 'session_metadata.json').read_text())['task_end_timestamp_s'], 65)


class CaptureTests(unittest.TestCase):
    def test_integer_pts_not_rounded_epoch_or_message_delivery(self):
        clock = FirstFrameClock(100 - 1700000000, 100)
        clock.consume('[Parsed_showinfo_0] config in time_base: 1/1000000, frame_rate: 30/1')
        offset = clock.consume('[Parsed_showinfo_0] n: 0 pts:1700000000123456 pts_time:1.7e+09')
        self.assertAlmostEqual(offset, .123456, places=5)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg not installed')
    def test_real_ffmpeg_pts_and_zero_based_output(self):
        before, wall = time.monotonic(), time.time()
        clock = FirstFrameClock(before - wall, before)
        command = ['ffmpeg', '-hide_banner', '-loglevel', 'info', '-copyts',
                   '-use_wallclock_as_timestamps', '1', '-f', 'lavfi', '-i',
                   'testsrc2=size=64x64:rate=1000', '-vf', 'showinfo,setpts=PTS-STARTPTS',
                   '-fps_mode', 'cfr', '-r', '1000', '-frames:v', '1', '-f', 'null', '-']
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
        offsets = [value for line in result.stderr.splitlines() if (value := clock.consume(line)) is not None]
        self.assertEqual(len(offsets), 1)
        self.assertGreaterEqual(offsets[0], -.002)
        self.assertLessEqual(offsets[0], time.monotonic() - before)
        self.assertIn('time=00:00:00.00', result.stderr)

    def test_slow_fallback_preserves_elapsed_time_and_thread_ownership(self):
        module = isolated_import('screen_recorder', {'pynput': Mock(), 'pynput.mouse': Mock()})
        clock = [100.0]
        event = threading.Event()
        writer = Mock()
        frames = []
        writer.write.side_effect = frames.append
        capture = Mock()
        capture.monitors = [{}, {'width': 2, 'height': 2}]
        calls = [0]
        def grab(_):
            calls[0] += 1
            clock[0] += .25  # Only 4 actual captures/s at requested 10 FPS.
            if calls[0] == 3:
                event.set()
            return [[calls[0]]]
        capture.grab.side_effect = grab
        cv = Mock()
        cv.VideoWriter.return_value = writer
        cv.cvtColor.side_effect = lambda frame, _: frame
        recorder = module.ScreenRecorder.__new__(module.ScreenRecorder)
        recorder.output_path = Path('unused.mp4')
        recorder.fps = 10
        recorder.t0 = 100
        recorder._stop_event = event
        recorder._first_frame_event = threading.Event()
        recorder._writer = recorder._mss = None
        recorder._capture_error = None
        recorder.logger = logging.getLogger('test')
        recorder._draw_cursor_overlay = lambda frame: frame
        with patch.object(module, 'mss', Mock(MSS=Mock(return_value=capture))), \
             patch.object(module, 'cv2', cv), patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]):
            recorder._fallback_loop()
        self.assertIsNone(recorder._capture_error)
        self.assertEqual(len(frames), 8)  # ceil(0.75 seconds * 10 FPS), not 3 sped-up frames.
        self.assertEqual(recorder._first_frame_timestamp_s, 0)
        capture.close.assert_called_once()
        writer.release.assert_called_once()


if __name__ == '__main__':
    unittest.main()
