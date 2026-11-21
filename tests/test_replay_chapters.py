"""tools/replay_chapters.py: chapters and clip candidates from a replay's words.json."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import replay_chapters  # noqa: E402


def sentence(start_ms, end_ms, text, pause_ms=0):
    return {'startMs': start_ms, 'endMs': end_ms, 'text': text, 'pauseBeforeMs': pause_ms, 'complete': True}


def run_main(argv):
    """replay_chapters.main() with argv: (stdout, stderr). A SystemExit propagates."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, 'argv', ['replay_chapters.py'] + argv), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        replay_chapters.main()
    return out.getvalue(), err.getvalue()


class Sections(unittest.TestCase):
    def test_a_run_sheet_line_is_placed_where_it_is_first_said(self):
        sents = [sentence(0, 2000, 'Welcome everyone.'), sentence(30000, 33000, 'Now pricing plans, compared.'),
                 sentence(60000, 62000, 'Thanks.')]
        chapters = replay_chapters.chapters_from_sections(sents, ['Intro', 'Pricing plans'], 62000)
        self.assertEqual(chapters, [[0, 'Intro'], [30000, 'Pricing plans']])

    def test_a_match_in_the_next_sentence_never_makes_a_last_chapter_under_10_s(self):
        # Sentence 2 starts inside the window and the match is only in sentence 3, 5 s before the end.
        sents = [sentence(0, 2000, 'Welcome everyone.'), sentence(20000, 22000, 'Lots of chatter here.'),
                 sentence(39000, 41000, 'Now we look at something.'), sentence(45000, 50000, 'Pricing plans compared.')]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            chapters = replay_chapters.chapters_from_sections(sents, ['Intro', 'Pricing plans'], 50000)
        self.assertEqual(chapters, [[0, 'Intro']])
        self.assertIn('Pricing plans', err.getvalue())

    def test_sections_past_the_chapter_limit_are_named_not_dropped_silently(self):
        names = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot', 'golf', 'hotel', 'india', 'juliet',
                 'kilo', 'lima', 'mike', 'november', 'oscar', 'papa', 'quebec']
        sents = [sentence(k * 20000, k * 20000 + 2000, f'Now {name} topic.') for k, name in enumerate(names)]
        titles = [f'{name.title()} topic' for name in names]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            chapters = replay_chapters.chapters_from_sections(sents, titles, len(names) * 20000 + 30000)
        self.assertEqual(len(chapters), replay_chapters.MAX_CHAPTERS)
        self.assertIn('Papa topic', err.getvalue())
        self.assertIn('Quebec topic', err.getvalue())


class Main(unittest.TestCase):
    WORDS = {'words': [{'id': 'w00001', 'text': 'Hello.', 'startMs': 0, 'endMs': 500},
                       {'id': 'w00002', 'text': 'Bye.', 'startMs': 30000, 'endMs': 30500}]}

    def test_a_blank_run_sheet_is_refused_with_a_message(self):
        with tempfile.TemporaryDirectory() as folder:
            words = Path(folder) / 'replay.words.json'
            words.write_text(json.dumps(self.WORDS), encoding='utf-8')
            sections = Path(folder) / 'run-sheet.txt'
            sections.write_text('\n  \n', encoding='utf-8')
            with self.assertRaises(SystemExit) as caught:
                run_main([str(words), '--sections', str(sections)])
            self.assertIn('run sheet', str(caught.exception.code))
            self.assertFalse((Path(folder) / 'replay.chapters.txt').exists())

    def test_existing_chapters_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            words = Path(folder) / 'replay.words.json'
            words.write_text(json.dumps(self.WORDS), encoding='utf-8')
            chapters = Path(folder) / 'replay.chapters.txt'
            chapters.write_text('mine', encoding='utf-8')
            with self.assertRaises(SystemExit):
                run_main([str(words)])
            self.assertEqual(chapters.read_text(encoding='utf-8'), 'mine')

    def test_a_second_run_that_starts_at_the_same_time_never_replaces_the_first_runs_chapters(self):
        # The other run passes the "already exists" check too, then writes its chapters first.
        with tempfile.TemporaryDirectory() as folder:
            words = Path(folder) / 'replay.words.json'
            words.write_text(json.dumps(self.WORDS), encoding='utf-8')
            chapters = Path(folder) / 'replay.chapters.txt'
            real = replay_chapters.chapters_from_silences

            def other_run_writes_first(*args):
                chapters.write_text('other run', encoding='utf-8')
                return real(*args)

            with mock.patch.object(replay_chapters, 'chapters_from_silences', other_run_writes_first), \
                    self.assertRaises(SystemExit):
                run_main([str(words)])
            self.assertEqual(chapters.read_text(encoding='utf-8'), 'other run')

    def test_a_run_that_fails_while_writing_leaves_nothing_in_the_way_of_a_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            words = Path(folder) / 'replay.words.json'
            words.write_text(json.dumps(self.WORDS), encoding='utf-8')
            with mock.patch.object(replay_chapters.file_store.os, 'fsync',
                                   side_effect=OSError(28, 'disk full')), self.assertRaises(OSError):
                run_main([str(words)])
            self.assertFalse((Path(folder) / 'replay.chapters.txt').exists())
            run_main([str(words)])
            self.assertTrue((Path(folder) / 'replay.chapters.txt').read_text(encoding='utf-8').startswith('0:00'))
            self.assertTrue((Path(folder) / 'replay.clips.json').exists())


if __name__ == '__main__':
    unittest.main()
