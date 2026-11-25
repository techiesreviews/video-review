"""The loudness the server measures once per video (loudness.py), read by web/cut-boundary.js to place word cuts."""
from pathlib import Path
import math
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import loudness  # noqa: E402
import media_tools  # noqa: E402


def tone_then_silence(seconds=0.5, amplitude=0.5):
    """16 kHz mono samples: a 440 Hz tone, then as long a silence."""
    count = int(loudness.RATE * seconds)
    return [round(amplitude * 32767 * math.sin(2 * math.pi * 440 * i / loudness.RATE)) for i in range(count)] + [0] * count


class Measure(unittest.TestCase):
    def test_a_level_is_half_db_steps_below_full_scale(self):
        self.assertEqual(loudness.level_byte(32768.0 ** 2 * 10, 10), 0)
        self.assertEqual(loudness.level_byte(32768.0 ** 2 * 10 / 100, 10), 40, '-20 dB')
        self.assertEqual(loudness.level_byte(0, 10), 200, 'silence is the floor')

    def test_the_decoded_sound_is_measured_in_5_ms_steps_whatever_size_its_pieces_come_in(self):
        samples = tone_then_silence()
        raw = struct.pack(f'<{len(samples)}h', *samples)

        def fake_stream(tool, arguments, timeout, on_chunk):
            self.assertEqual(tool, 'ffmpeg')
            for at in range(0, len(raw), 997):  # odd pieces, splitting samples and steps
                on_chunk(raw[at:at + 997])
            return 0, ''
        with mock.patch.object(media_tools, 'stream', fake_stream):
            levels = loudness.measure('clip.mp4')
        self.assertEqual(len(levels), 200, 'one byte per 5 ms of one second')
        self.assertAlmostEqual(levels[10] / 2, 9.0, delta=0.5, msg='a half-scale sine is about 9 dB below full scale')
        self.assertEqual(levels[150], 200)

    def test_a_file_ffmpeg_cannot_read_has_no_sound(self):
        with mock.patch.object(media_tools, 'stream', return_value=(1, 'Output file does not contain any stream\n')):
            with self.assertRaisesRegex(loudness.NoSound, 'does not contain any stream'):
                loudness.measure('still.png')

    def test_a_kept_result_is_read_back_and_an_older_version_is_dropped(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        with mock.patch.object(loudness, 'measure', side_effect=[b'\x01', b'\x02']) as measure:
            self.assertEqual(loudness.cached(folder, folder / 'a.mp4', '1-1'), b'\x01')
            self.assertEqual(loudness.cached(folder, folder / 'a.mp4', '1-1'), b'\x01')
            self.assertEqual(loudness.cached(folder, folder / 'a.mp4', '2-2'), b'\x02')
        self.assertEqual(measure.call_count, 2)
        self.assertEqual(len(list((folder / 'loudness').glob('*.bin'))), 1)

    def test_a_real_file_through_ffmpeg(self):
        try:
            media_tools.find('ffmpeg')
        except media_tools.ToolMissing:
            self.skipTest('ffmpeg is not installed here')
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        samples = tone_then_silence()
        with wave.open(str(folder / 'clip.wav'), 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(loudness.RATE)
            out.writeframes(struct.pack(f'<{len(samples)}h', *samples))
        levels = loudness.measure(folder / 'clip.wav')
        self.assertAlmostEqual(len(levels), 200, delta=1)
        self.assertAlmostEqual(levels[10] / 2, 9.0, delta=0.5)
        self.assertGreaterEqual(levels[150], 190)


if __name__ == '__main__':
    unittest.main()


class Stream(unittest.TestCase):
    """media_tools.stream stops a tool that runs too long, or whose reader fails, and leaves no process behind."""

    def test_a_tool_past_its_time_limit_is_stopped(self):
        processes = []
        real = media_tools.subprocess.Popen
        with mock.patch.object(media_tools, 'find', return_value=sys.executable), \
             mock.patch.object(media_tools.subprocess, 'Popen', side_effect=lambda *a, **k: processes.append(real(*a, **k)) or processes[-1]):
            with self.assertRaises(media_tools.ToolTimedOut):
                media_tools.stream('ffmpeg', ['-c', 'import sys\nwhile True: sys.stdout.write("x" * 4096)'], 1, lambda chunk: None)
            with self.assertRaises(ZeroDivisionError):
                media_tools.stream('ffmpeg', ['-c', 'import sys\nwhile True: sys.stdout.write("x" * 4096)'], 30, lambda chunk: 1 / 0)
        self.assertEqual([p.poll() is not None for p in processes], [True, True], 'no tool is left running')
