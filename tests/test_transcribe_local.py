"""tools/transcribe_local.py: the output files, without ffmpeg, stable-ts or a GPU."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import transcribe_local  # noqa: E402


class FakeResult:
    def save_as_json(self, path):
        if not path.lower().endswith('.json'):  # as stable-ts does
            path += '.json'
        Path(path).write_text('{"segments": [{"words": [{"word": "Hi.", "start": 0, "end": 0.5}]}]}',
                              encoding='utf-8')


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


class Outputs(unittest.TestCase):
    def test_a_run_that_another_run_beat_to_the_files_stops_before_transcribing_and_keeps_them(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'replay.mkv'
            media.write_bytes(b'')
            raw, words = Path(folder) / 'replay.large-v3.json', Path(folder) / 'replay.words.json'
            # The other run made both files just after this run's "already exists" check.
            real_exists = Path.exists
            with mock.patch.object(Path, 'exists', lambda p: False if p in (raw, words) else real_exists(p)):
                raw.write_text('other run', encoding='utf-8')
                words.write_text('other run', encoding='utf-8')
                model, stop = run_main(media, lambda *args: [0.0])
            self.assertIsNotNone(stop)
            model.transcribe.assert_not_called()
            self.assertEqual(raw.read_text(encoding='utf-8'), 'other run')
            self.assertEqual(words.read_text(encoding='utf-8'), 'other run')

    def test_a_run_that_fails_before_saving_leaves_no_empty_file_behind(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'replay.mkv'
            media.write_bytes(b'')

            def ffmpeg_fails(*args):
                sys.exit('ffmpeg could not read it')

            _, stop = run_main(media, ffmpeg_fails)
            self.assertIsNotNone(stop)
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ['replay.mkv'])

    def test_a_run_that_fails_halfway_through_saving_leaves_nothing_in_the_way_of_a_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / 'replay.mkv'
            media.write_bytes(b'')

            def half_saved(result, path):
                Path(path).write_text('{"segm', encoding='utf-8')
                raise OSError(28, 'disk full')

            with mock.patch.object(FakeResult, 'save_as_json', half_saved), self.assertRaises(OSError):
                run_main(media, lambda *args: [0.0])
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ['replay.mkv'])

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
