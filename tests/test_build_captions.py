"""Offline checks for the caption builder: small invented word lists, no real footage."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/video/build-captions.py'
sys.path.insert(0, str(ROOT / 'scripts/video'))
spec = importlib.util.spec_from_file_location('build_captions', SCRIPT)
captions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(captions)
import cut_timeline  # noqa: E402

CUTLIST = {'fps': 30, 'durationFrames': 69, 'segments': [
    {'id': 'C01', 'sourceInFrame': 24, 'sourceOutFrame': 60, 'timelineStartFrame': 0, 'durationFrames': 36},
    {'id': 'C02', 'sourceInFrame': 117, 'sourceOutFrame': 150, 'timelineStartFrame': 36, 'durationFrames': 33},
]}
WORDS = [['hello', 1.0, 1.4], ['world.', 1.5, 1.9], ['um', 2.5, 2.7], ['next', 4.0, 4.4], ['thing', 4.5, 4.9]]


def pieces(words=WORDS, cutlist=CUTLIST):
    return cut_timeline.retained_words(cut_timeline.load_words(words), cut_timeline.load_cutlist(cutlist))


def timeline_end(cutlist=CUTLIST):
    return cut_timeline.load_cutlist(cutlist).end


def long_cutlist(seconds):
    frames = seconds * 30
    return {'fps': 30, 'durationFrames': frames, 'segments': [
        {'id': 'C01', 'sourceInFrame': 0, 'sourceOutFrame': frames, 'timelineStartFrame': 0, 'durationFrames': frames}]}


class WordCaptionTests(unittest.TestCase):
    def test_word_srt_has_one_cue_per_kept_word_on_the_edited_clock(self):
        text = captions.word_srt(pieces())
        self.assertEqual(text, (
            '1\n00:00:00,200 --> 00:00:00,600\nhello\n\n'
            '2\n00:00:00,700 --> 00:00:01,100\nworld.\n\n'
            '3\n00:00:01,300 --> 00:00:01,700\nnext\n\n'
            '4\n00:00:01,800 --> 00:00:02,200\nthing\n'))
        self.assertNotIn('um', text)

    def test_word_vtt_has_a_header_and_dots_in_times(self):
        text = captions.word_vtt(pieces())
        self.assertTrue(text.startswith('WEBVTT\n\n'))
        self.assertIn('00:00:01.300 --> 00:00:01.700\nnext', text)

    def test_timed_vtt_puts_each_later_word_at_its_exact_time(self):
        text = captions.timed_vtt(pieces(), timeline_end())
        self.assertIn('hello <00:00:00.700>world.', text)
        self.assertIn('next <00:00:01.800>thing', text)

    def test_timed_vtt_times_stay_strictly_inside_their_cue(self):
        words = [['a', 0.0, 0.0], ['b', 0.0, 0.2], ['c', 0.2, 0.2]]
        text = captions.timed_vtt(pieces(words, long_cutlist(2)), 2.0)
        cue_start, cue_end = re.search(r'(\S+) --> (\S+)', text).groups()
        inline = re.findall(r'<(\d\d:\d\d:\d\d\.\d{3})>', text)
        self.assertEqual(inline, sorted(set(inline)))
        for stamp in inline:
            self.assertTrue(cue_start < stamp < cue_end, stamp)

    def test_vtt_escapes_markup_but_srt_keeps_plain_text(self):
        words = [['R&D', 0.1, 0.5], ['<b>', 0.6, 0.9]]
        one = long_cutlist(2)
        self.assertIn('R&amp;D', captions.word_vtt(pieces(words, one)))
        self.assertIn('&lt;b&gt;', captions.phrase_vtt(pieces(words, one), 2.0))
        self.assertIn('R&D', captions.word_srt(pieces(words, one)))

    def test_zero_length_word_still_gets_a_valid_cue(self):
        text = captions.word_srt(pieces([['a', 0.5, 0.5]], long_cutlist(2)))
        self.assertIn('00:00:00,500 --> 00:00:00,501', text)


class PhraseCaptionTests(unittest.TestCase):
    def test_phrases_end_at_sentences_and_use_edited_times(self):
        text = captions.phrase_srt(pieces(), timeline_end())
        self.assertEqual(text, (
            '1\n00:00:00,200 --> 00:00:01,100\nhello world.\n\n'
            '2\n00:00:01,300 --> 00:00:02,200\nnext thing\n'))

    def test_phrase_vtt_matches_phrase_srt(self):
        text = captions.phrase_vtt(pieces(), timeline_end())
        self.assertTrue(text.startswith('WEBVTT\n\n'))
        self.assertIn('00:00:00.200 --> 00:00:01.100\nhello world.', text)

    def test_a_long_pause_starts_a_new_phrase(self):
        words = [['one', 0.0, 0.3], ['two', 1.0, 1.3]]
        self.assertEqual(len(captions.phrases(pieces(words, long_cutlist(3)))), 2)

    def test_a_short_join_does_not_split_a_phrase(self):
        words = [['one', 1.0, 1.4], ['two', 1.5, 1.9], ['three', 4.0, 4.4]]
        self.assertEqual(len(captions.phrases(pieces(words))), 1)

    def test_phrases_fit_two_short_lines(self):
        words = [[f'word{i:02}', i * 0.4, i * 0.4 + 0.3] for i in range(60)]
        cues = captions.phrases(pieces(words, long_cutlist(30)))
        self.assertGreater(len(cues), 1)
        for cue in cues:
            lines = captions.wrap([piece.word.text for piece in cue])
            self.assertLessEqual(len(lines), 2)
            self.assertTrue(all(len(line) <= captions.LINE_CHARS for line in lines))

    def test_two_lines_are_balanced_not_one_word_left_over(self):
        lines = captions.wrap('So last week I put my personal website live,'.split())
        self.assertEqual(lines, ['So last week I put my', 'personal website live,'])

    def test_phrases_have_a_length_limit_in_time(self):
        words = [['a', i * 0.5, i * 0.5 + 0.4] for i in range(40)]
        for cue in captions.phrases(pieces(words, long_cutlist(30))):
            self.assertLessEqual(cue[-1].timeline_end - cue[0].timeline_start, captions.MAX_PHRASE_SECONDS)

    def test_short_phrase_is_held_long_enough_to_read_without_overlapping(self):
        words = [['Hi.', 0.0, 0.1], ['Next', 0.5, 0.8], ['one.', 0.9, 1.2], ['End.', 5.0, 5.1]]
        text = captions.phrase_srt(pieces(words, long_cutlist(6)), 6.0)
        times = re.findall(r'(\S+) --> (\S+)', text)
        self.assertEqual(times[0], ('00:00:00,000', '00:00:00,500'))  # held until the next cue, no further
        self.assertEqual(times[2], ('00:00:05,000', '00:00:05,800'))  # held for the shortest reading time

    def test_last_phrase_is_not_held_past_the_end_of_the_timeline(self):
        text = captions.phrase_srt(pieces([['End.', 1.9, 1.95]], long_cutlist(2)), 2.0)
        self.assertIn('--> 00:00:02,000', text)


class MappingTests(unittest.TestCase):
    def test_partly_kept_word_uses_only_its_kept_time(self):
        one = {'fps': 30, 'durationFrames': 30, 'segments': [
            {'id': 'C01', 'sourceInFrame': 30, 'sourceOutFrame': 60, 'timelineStartFrame': 0, 'durationFrames': 30}]}
        text = captions.word_srt(pieces([['cut', 0.9, 1.3]], one))
        self.assertIn('00:00:00,000 --> 00:00:00,300', text)

    def test_split_across_continuous_source_stays_one_word(self):
        split = {'fps': 30, 'durationFrames': 30, 'segments': [
            {'id': 'C01', 'sourceInFrame': 0, 'sourceOutFrame': 15, 'timelineStartFrame': 0, 'durationFrames': 15},
            {'id': 'C02', 'sourceInFrame': 15, 'sourceOutFrame': 30, 'timelineStartFrame': 15, 'durationFrames': 15}]}
        self.assertEqual(captions.word_srt(pieces([['whole', 0.4, 0.6]], split)).count('whole'), 1)


class TimelineStartTests(unittest.TestCase):
    def test_times_count_from_the_first_frame_when_the_timeline_starts_late(self):
        late = {'fps': 30, 'segments': [dict(CUTLIST['segments'][0], timelineStartFrame=108000),
                                        dict(CUTLIST['segments'][1], timelineStartFrame=108036)]}
        timeline = cut_timeline.load_cutlist(late)
        moved = cut_timeline.from_first_frame(pieces(cutlist=late), timeline)
        self.assertEqual(captions.word_srt(moved), captions.word_srt(pieces()))
        self.assertEqual(captions.phrase_srt(moved, timeline.duration), captions.phrase_srt(pieces(), timeline_end()))
        self.assertTrue(captions.word_srt(moved).startswith('1\n00:00:00,200 -->'))


class CommandFailureTests(unittest.TestCase):
    def test_missing_file_and_bad_json_stop_with_a_plain_sentence(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            good = folder / 'cut.json'
            good.write_text(json.dumps(CUTLIST), encoding='utf-8')
            bad = folder / 'bad.json'
            bad.write_text('{not json', encoding='utf-8')
            for words in (folder / 'missing.json', bad):
                with self.subTest(words=words):
                    result = subprocess.run([sys.executable, str(SCRIPT), str(words), str(good), '--out-dir', str(folder / 'out')],
                                            capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Captions stopped', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)


class CommandTests(unittest.TestCase):
    def test_writes_separate_caption_files_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'words.json').write_text(json.dumps(WORDS), encoding='utf-8')
            (folder / 'cut.json').write_text(json.dumps(CUTLIST), encoding='utf-8')
            before = (folder / 'words.json').read_bytes(), (folder / 'cut.json').read_bytes()
            command = [sys.executable, str(SCRIPT), str(folder / 'words.json'), str(folder / 'cut.json'),
                       '--out-dir', str(folder / 'captions'), '--name', 'demo']
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(sorted(path.name for path in (folder / 'captions').iterdir()), [
                'demo.phrases.srt', 'demo.phrases.vtt', 'demo.timed-words.vtt', 'demo.words.srt', 'demo.words.vtt'])
            self.assertEqual(before, ((folder / 'words.json').read_bytes(), (folder / 'cut.json').read_bytes()))
            self.assertNotIn(b'\r', (folder / 'captions/demo.words.srt').read_bytes())
            self.assertIn('4 words', first.stdout)
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn('already exists', second.stderr)


if __name__ == '__main__':
    unittest.main()
