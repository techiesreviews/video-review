"""tools/transcribe_local.py: the output files, without ffmpeg, stable-ts or a GPU."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import file_store  # noqa: E402
import transcribe_local  # noqa: E402

RAW_RESULT = '{"segments": [{"words": [{"word": "Hi.", "start": 0, "end": 0.5}]}]}'


class FakeResult:
    def save_as_json(self, path):
        if not path.lower().endswith('.json'):  # as stable-ts does
            path += '.json'
        Path(path).write_text(RAW_RESULT, encoding='utf-8')


def run_main(media, load_audio):
    """transcribe_local.main() on `media` with --device cpu and a fake stable-ts: (model mock, SystemExit or None)."""
    model = mock.Mock()
    model.transcribe.return_value = FakeResult()
    stable_whisper = mock.Mock(load_model=mock.Mock(return_value=model))
    with mock.patch.dict(sys.modules, {'stable_whisper': stable_whisper, 'torch': mock.Mock()}), \
            mock.patch.object(transcribe_local, 'load_audio', load_audio), \
            mock.patch.object(sys, 'argv', ['transcribe_local.py', str(media), '--device', 'cpu']), \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            transcribe_local.main()
        except SystemExit as stop:
            return model, stop
    return model, None


def files_in(folder):
    """The files a later run could find in its way. Lock files are left out: the system holds a lock only while
    its program runs, so a lock file never stops a later run."""
    return sorted(p.name for p in Path(folder).iterdir() if p.suffix != '.lock')


class Outputs(unittest.TestCase):
    def test_a_run_started_while_another_is_transcribing_stops_before_transcribing_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'replay.mkv'
            media.write_bytes(b'')
            raw = Path(folder) / 'replay.large-v3.json'
            # The other run holds the raw file's lock for as long as it transcribes.
            with file_store.locked(file_store.lock_for(raw)):
                model, stop = run_main(media, lambda *args: [0.0])
            self.assertIsNotNone(stop)
            self.assertIn('Another run', str(stop.code))
            model.transcribe.assert_not_called()
            self.assertEqual(files_in(folder), ['replay.mkv'])

    def test_a_run_finds_both_files_already_there_and_keeps_them(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'replay.mkv'
            media.write_bytes(b'')
            raw, words = Path(folder) / 'replay.large-v3.json', Path(folder) / 'replay.words.json'
            raw.write_text('earlier run', encoding='utf-8')
            words.write_text('earlier run', encoding='utf-8')
            model, stop = run_main(media, lambda *args: [0.0])
            self.assertIsNotNone(stop)
            model.transcribe.assert_not_called()
            self.assertEqual(raw.read_text(encoding='utf-8'), 'earlier run')
            self.assertEqual(words.read_text(encoding='utf-8'), 'earlier run')

    def test_a_run_that_fails_before_saving_leaves_no_empty_file_behind(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'replay.mkv'
            media.write_bytes(b'')

            def ffmpeg_fails(*args):
                sys.exit('ffmpeg could not read it')

            _, stop = run_main(media, ffmpeg_fails)
            self.assertIsNotNone(stop)
            self.assertEqual(files_in(folder), ['replay.mkv'])

    def test_a_run_that_fails_halfway_through_saving_leaves_nothing_in_the_way_of_a_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'replay.mkv'
            media.write_bytes(b'')

            def half_saved(result, path):
                Path(path).write_text('{"segm', encoding='utf-8')
                raise OSError(28, 'disk full')

            with mock.patch.object(FakeResult, 'save_as_json', half_saved), self.assertRaises(OSError):
                run_main(media, lambda *args: [0.0])
            self.assertEqual(files_in(folder), ['replay.mkv'])

    def test_a_killed_run_leaves_nothing_that_stops_the_next_run(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'replay.mkv'
            media.write_bytes(b'')
            # A killed program runs no clean-up code at all; os._exit in the middle of the GPU work does the same.
            killed = textwrap.dedent(f'''
                import os, sys
                from unittest import mock
                sys.path.insert(0, {str(ROOT / 'tools')!r})
                import transcribe_local
                sys.modules['stable_whisper'] = mock.Mock()
                sys.modules['torch'] = mock.Mock()
                transcribe_local.load_audio = lambda *args: os._exit(9)
                sys.argv = ['transcribe_local.py', {str(media)!r}, '--device', 'cpu']
                transcribe_local.main()
            ''')
            done = subprocess.run([sys.executable, '-c', killed], capture_output=True, timeout=60)
            self.assertEqual(done.returncode, 9, done.stderr)
            model, stop = run_main(media, lambda *args: [0.0])
            self.assertIsNone(stop)
            model.transcribe.assert_called_once()
            self.assertIn('"Hi."', (Path(folder) / 'replay.words.json').read_text(encoding='utf-8'))

    def test_a_rerun_after_the_words_save_failed_makes_them_from_the_raw_file_without_transcribing(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'replay.mkv'
            media.write_bytes(b'')
            raw, words = Path(folder) / 'replay.large-v3.json', Path(folder) / 'replay.words.json'
            with mock.patch.object(file_store, 'write_new_text', side_effect=OSError(28, 'disk full')), \
                    self.assertRaises(OSError):
                run_main(media, lambda *args: [0.0])
            self.assertEqual(files_in(folder), ['replay.large-v3.json', 'replay.mkv'])
            saved = raw.stat().st_mtime_ns, raw.read_bytes()

            model, stop = run_main(media, lambda *args: [0.0])
            self.assertIsNone(stop)
            model.transcribe.assert_not_called()
            self.assertEqual((raw.stat().st_mtime_ns, raw.read_bytes()), saved)
            self.assertIn('"Hi."', words.read_text(encoding='utf-8'))

    def test_a_cut_off_raw_file_from_an_older_run_is_named_and_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'replay.mkv'
            media.write_bytes(b'')
            raw = Path(folder) / 'replay.large-v3.json'
            raw.write_bytes(b'')  # the empty claim an older version left when it was killed
            model, stop = run_main(media, lambda *args: [0.0])
            self.assertIsNotNone(stop)
            self.assertIn('not a finished transcript', str(stop.code))
            model.transcribe.assert_not_called()
            self.assertEqual(raw.read_bytes(), b'')
            self.assertEqual(files_in(folder), ['replay.large-v3.json', 'replay.mkv'])

    def test_a_run_writes_both_files(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'replay.mkv'
            media.write_bytes(b'')
            _, stop = run_main(media, lambda *args: [0.0])
            self.assertIsNone(stop)
            self.assertIn('"Hi."', (Path(folder) / 'replay.words.json').read_text(encoding='utf-8'))
            self.assertIn('segments', (Path(folder) / 'replay.large-v3.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
