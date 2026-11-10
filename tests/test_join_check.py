"""Offline checks for the join check: small invented word lists, no real footage."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/video/join-check.py'
sys.path.insert(0, str(ROOT / 'scripts/video'))
spec = importlib.util.spec_from_file_location('join_check', SCRIPT)
join_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(join_check)
import cut_timeline  # noqa: E402

FPS = 30


def cutlist(*ranges, **extra):
    """Build a cut list like the real one from (sourceIn, sourceOut) frame pairs."""
    segments = []
    start = 0
    for index, (source_in, source_out) in enumerate(ranges, 1):
        duration = source_out - source_in
        segments.append({'id': f'C{index:02}', 'sourceInFrame': source_in, 'sourceOutFrame': source_out,
                         'timelineStartFrame': start, 'durationFrames': duration})
        start += duration
    return {'schemaVersion': 1, 'fps': FPS, 'durationFrames': start, 'segments': segments, **extra}


# hello world | um (removed) | next thing, with 100 ms of room either side of the join.
WORDS = [['hello', 1.0, 1.4], ['world', 1.5, 1.9], ['um', 2.5, 2.7], ['next', 4.0, 4.4], ['thing', 4.5, 4.9]]
CLEAN = ((26, 60), (117, 150))


def check(words, *ranges):
    return join_check.check_joins(cut_timeline.load_words(words), cut_timeline.load_cutlist(cutlist(*ranges)))


def codes(flagged):
    return [flag['code'] for flag in flagged['flags']]


class CleanJoinTests(unittest.TestCase):
    def test_join_inside_every_preference_has_no_flags(self):
        report = check(WORDS, *CLEAN)
        self.assertEqual(report['summary']['joins'], 1)
        self.assertEqual(report['summary']['problems'] + report['summary']['checks'], 0)
        join = report['joins'][0]
        self.assertEqual(join['timelineFrame'], 34)
        self.assertEqual(join['gapMs'], 200)
        self.assertEqual(join['gapKind'], 'linked')
        self.assertEqual((join['lastWord'], join['firstWord']), ('world', 'next'))
        self.assertEqual(join['removedWords'], ['um'])

    def test_a_split_with_continuous_source_is_not_a_join(self):
        # Resolve splits a clip for framing: same source frame on both sides, nothing was removed.
        report = check(WORDS, (24, 50), (50, 60), (117, 150))
        self.assertEqual(report['summary']['joins'], 1)
        self.assertEqual(report['summary']['continuousSplits'], 1)
        self.assertEqual(report['joins'][0]['flags'], [])


class WordCutTests(unittest.TestCase):
    def test_cut_through_the_middle_of_a_word(self):
        join = check(WORDS, (24, 51), (117, 150))['joins'][0]  # frame 51 = 1.7 s, world is 1.5-1.9
        flag = next(flag for flag in join['flags'] if flag['code'] == 'cut-inside-word')
        self.assertEqual((flag['severity'], flag['word'], flag['side']), ('problem', 'world', 'outgoing'))
        self.assertEqual(flag['ms'], 200)

    def test_incoming_cut_through_the_middle_of_a_word(self):
        join = check(WORDS, (24, 60), (126, 150))['joins'][0]  # frame 126 = 4.2 s, next is 4.0-4.4
        flag = next(flag for flag in join['flags'] if flag['code'] == 'cut-inside-word')
        self.assertEqual((flag['side'], flag['word']), ('incoming', 'next'))

    def test_clipped_word_end_and_start(self):
        join = check(WORDS, (24, 56), (121, 150))['joins'][0]  # 33 ms off world's end and next's start
        self.assertIn('clips-word-end', codes(join))
        self.assertIn('clips-word-start', codes(join))
        self.assertNotIn('cut-inside-word', codes(join))

    def test_cut_exactly_on_a_word_edge_is_not_clipped_but_the_fade_touches_it(self):
        join = check(WORDS, (24, 57), (117, 150))['joins'][0]  # frame 57 = 1.9 s, world's exact end
        self.assertNotIn('clips-word-end', codes(join))
        self.assertIn('fade-touches-word', codes(join))

    def test_tight_edge_is_a_check_not_a_problem(self):
        words = [['hello', 1.0, 1.4], ['world', 1.5, 1.95], ['next', 4.0, 4.4], ['thing', 4.5, 4.9]]
        join = check(words, *CLEAN)['joins'][0]  # 50 ms kept after world
        flag = next(flag for flag in join['flags'] if flag['code'] == 'tight-speech-edge')
        self.assertEqual((flag['severity'], flag['side'], flag['ms']), ('check', 'outgoing', 50))


class GapTests(unittest.TestCase):
    def join_with(self, last, gap_ms):
        words = [['hello', 1.0, 1.4], [last, 1.5, 1.9], ['next', 4.0, 4.4], ['thing', 4.5, 4.9]]
        # Keep 100 ms after the last word; the incoming cut supplies the rest of the wanted gap.
        source_in = 120 - round((gap_ms - 100) * FPS / 1000)
        return check(words, (24, 60), (source_in, 150))['joins'][0]

    def test_linked_gap_outside_preference(self):
        join = self.join_with('world', 400)
        self.assertEqual(join['gapMs'], 400)
        self.assertEqual(join['gapKind'], 'linked')
        self.assertIn('gap-too-long', codes(join))
        self.assertIn('hold', next(f for f in join['flags'] if f['code'] == 'gap-too-long')['message'])

    def test_linked_gap_too_short(self):
        words = [['hello', 1.0, 1.4], ['world', 1.5, 1.95], ['next', 3.95, 4.4], ['thing', 4.5, 4.9]]
        join = check(words, *CLEAN)['joins'][0]  # 50 ms + 50 ms
        self.assertEqual(join['gapMs'], 100)
        self.assertIn('gap-too-short', codes(join))

    def test_new_thought_uses_its_own_range(self):
        in_range = self.join_with('world.', 400)
        self.assertEqual(in_range['gapKind'], 'new-thought')
        self.assertNotIn('gap-too-long', codes(in_range))
        self.assertIn('gap-too-short', codes(self.join_with('world?', 200)))

    def test_range_edges_count_as_inside(self):
        words = [['hello', 1.0, 1.4], ['world', 1.5, 1.9], ['next', 4.05, 4.4], ['thing', 4.5, 4.9]]
        join = check(words, *CLEAN)['joins'][0]  # exactly 250 ms
        self.assertEqual(join['gapMs'], 250)
        self.assertNotIn('gap-too-long', codes(join))

    def test_segment_without_words_is_noted_and_gap_not_judged(self):
        report = check(WORDS, (24, 60), (90, 100), (117, 150))  # 3.0-3.33 s has no transcript words
        self.assertEqual(report['summary']['joins'], 2)
        for join in report['joins']:
            self.assertIsNone(join['gapMs'])
            self.assertIn('no-words-beside-join', codes(join))


class OrphanTests(unittest.TestCase):
    def test_single_short_word_alone_between_two_joins(self):
        words = [['hello', 1.0, 1.4], ['world', 1.5, 1.9], ['so', 3.0, 3.15], ['next', 4.0, 4.4], ['thing', 4.5, 4.9]]
        report = check(words, (24, 60), (87, 98), (117, 150))
        self.assertIn('orphan-word', codes(report['joins'][0]))

    def test_short_word_stranded_behind_a_long_pause(self):
        words = [['hello', 1.0, 1.4], ['world', 1.5, 1.9], ['and', 2.5, 2.65], ['next', 4.0, 4.4], ['thing', 4.5, 4.9]]
        join = check(words, (24, 83), (117, 150))['joins'][0]  # keeps "and" after 600 ms of silence
        flag = next(flag for flag in join['flags'] if flag['code'] == 'orphan-word')
        self.assertEqual((flag['word'], flag['side']), ('and', 'outgoing'))

    def test_word_inside_a_running_phrase_is_not_an_orphan(self):
        self.assertNotIn('orphan-word', codes(check(WORDS, *CLEAN)['joins'][0]))

    def test_long_single_word_is_not_an_orphan(self):
        words = [['hello', 1.0, 1.4], ['world', 1.5, 1.9], ['extraordinary', 2.5, 3.1], ['next', 4.0, 4.4]]
        join = check(words, (24, 96), (117, 150))['joins'][0]
        self.assertNotIn('orphan-word', codes(join))


class RetakeCueTests(unittest.TestCase):
    def test_kept_again_beside_a_join_is_flagged(self):
        words = [['hello', 1.0, 1.4], ['again', 1.5, 1.9], ['next', 4.0, 4.4], ['thing', 4.5, 4.9]]
        self.assertIn('retake-cue', codes(check(words, *CLEAN)['joins'][0]))

    def test_again_and_again_is_natural_speech(self):
        words = [['again', 0.3, 0.5], ['and', 0.6, 0.8], ['again', 1.5, 1.9], ['next', 4.0, 4.4], ['thing', 4.5, 4.9]]
        self.assertNotIn('retake-cue', codes(check(words, (0, 60), (117, 150))['joins'][0]))


class OpeningAndEndingTests(unittest.TestCase):
    def test_preferred_lead_in_and_ending(self):
        report = check(WORDS, *CLEAN)
        self.assertEqual(report['opening']['leadInMs'], 133)
        self.assertEqual(report['opening']['flags'], [])
        # Lex's choice of 2026-11-10: 100-150 ms before the first word, the same room the cutter leaves.
        for start_frame, lead_in in ((27, 100), (26, 133)):
            opening = check(WORDS, (start_frame, 60), (117, 150))['opening']
            self.assertEqual((opening['leadInMs'], opening['flags']), (lead_in, []))
        self.assertEqual(report['ending']['tailMs'], 100)
        self.assertEqual(report['ending']['flags'], [])

    def test_long_and_short_lead_in(self):
        self.assertEqual(check(WORDS, (0, 60), (117, 150))['opening']['flags'][0]['code'], 'lead-in-too-long')
        self.assertEqual(check(WORDS, (25, 60), (117, 150))['opening']['flags'][0]['code'], 'lead-in-too-long')
        self.assertEqual(check(WORDS, (28, 60), (117, 150))['opening']['flags'][0]['code'], 'lead-in-too-short')

    def test_short_ending_is_flagged(self):
        self.assertEqual(check(WORDS, (24, 60), (117, 148))['ending']['flags'][0]['code'], 'tight-speech-edge')


class OpeningFadeTests(unittest.TestCase):
    def test_a_cut_right_on_the_first_word_is_a_problem(self):
        report = check(WORDS, (30, 60), (117, 150))
        self.assertIn('fade-touches-word', codes(report['opening']))
        self.assertGreaterEqual(report['summary']['problems'], 1)


class WordlessEdgeTests(unittest.TestCase):
    CLIPS = {'cut-inside-word', 'clips-word-start', 'clips-word-end'}

    def clip_count(self, report):
        entries = [report['opening'], report['ending'], *report['joins']]
        return sum(flag['code'] in self.CLIPS for entry in entries for flag in entry['flags'])

    def test_clip_after_a_wordless_first_section_is_counted_once(self):
        self.assertEqual(self.clip_count(check([['next', 4.0, 4.4]], (0, 15), (123, 150))), 1)

    def test_clip_before_a_wordless_last_section_is_counted_once(self):
        self.assertEqual(self.clip_count(check([['hello', 1.0, 1.4]], (24, 39), (150, 165))), 1)


class ReportTests(unittest.TestCase):
    def test_report_tells_the_reader_it_is_not_a_listening_check(self):
        text = join_check.render_markdown(check(WORDS, (24, 51), (117, 150)))
        self.assertIn('Not listened', text)
        self.assertIn('approximate', text)
        self.assertIn('cut-inside-word', text)
        self.assertIn('00:00.900', text)  # timeline time of the join
        self.assertIn('Before the first word: 100-150 ms', text)

    def test_count_of_splits_reads_properly(self):
        text = join_check.render_markdown(check(WORDS, (24, 50), (50, 60), (117, 150)))
        self.assertIn('Clip splits with continuous source (not joins): 1.', text)

    def test_problems_are_listed_before_checks(self):
        words = [['hello', 1.0, 1.4], ['world', 1.5, 1.9], ['next', 4.0, 4.4], ['thing', 4.5, 4.9], ['more', 7.0, 7.4]]
        report = check(words, (24, 60), (114, 142), (205, 240))  # first join: long gap only; second: cut in a word
        text = join_check.render_markdown(report)
        self.assertLess(text.index('cut-inside-word'), text.index('gap-too-long'))

    def test_report_with_no_flags_never_claims_the_joins_are_clean(self):
        text = join_check.render_markdown(check(WORDS, *CLEAN))
        self.assertNotIn('clean', text.lower().replace('no flag does not mean clean', ''))

    def test_cli_writes_markdown_and_json_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'words.json').write_text(json.dumps(WORDS), encoding='utf-8')
            (folder / 'cut.json').write_text(json.dumps(cutlist(*CLEAN)), encoding='utf-8')
            command = [sys.executable, str(SCRIPT), str(folder / 'words.json'), str(folder / 'cut.json'),
                       '--out-dir', str(folder / 'report')]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            data = json.loads((folder / 'report/join-check.json').read_text(encoding='utf-8'))
            self.assertEqual(data['summary']['joins'], 1)
            self.assertEqual(data['thresholds']['linkedGapMs'], [150, 250])
            self.assertTrue((folder / 'report/join-check.md').exists())
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn('already exists', second.stderr)


class CommandFailureTests(unittest.TestCase):
    def test_output_folder_that_is_a_file_stops_with_a_plain_sentence(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            words, cut, taken = folder / 'words.json', folder / 'cut.json', folder / 'taken'
            words.write_text(json.dumps(WORDS), encoding='utf-8')
            cut.write_text(json.dumps(cutlist(*CLEAN)), encoding='utf-8')
            taken.write_text('', encoding='utf-8')
            result = subprocess.run([sys.executable, str(SCRIPT), str(words), str(cut), '--out-dir', str(taken)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Join check stopped', result.stderr)
            self.assertNotIn('Traceback', result.stderr)

    def test_a_segment_id_that_is_a_number_is_read(self):
        data = cutlist(*CLEAN)
        data['segments'][0]['id'] = 1
        report = join_check.check_joins(cut_timeline.load_words(WORDS), cut_timeline.load_cutlist(data))
        self.assertEqual(report['joins'][0]['outgoing'], '1')

    def test_missing_file_and_bad_json_stop_with_a_plain_sentence(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            good = folder / 'cut.json'
            good.write_text(json.dumps(cutlist(*CLEAN)), encoding='utf-8')
            bad = folder / 'bad.json'
            bad.write_text('{not json', encoding='utf-8')
            for words in (folder / 'missing.json', bad):
                with self.subTest(words=words):
                    result = subprocess.run([sys.executable, str(SCRIPT), str(words), str(good), '--out-dir', str(folder / 'out')],
                                            capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Join check stopped', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)


class InputTests(unittest.TestCase):
    def test_reads_every_project_word_format_on_the_source_clock(self):
        expected = [('So', 2.09, 2.25), ('last', 2.32, 2.48)]
        happyscribe = [{'words': [{'text': 'So', 'type': 'word', 'data_start': 2.09, 'data_end': 2.25},
                                  {'text': ' ', 'type': 'spacing', 'data_start': 2.25, 'data_end': 2.32},
                                  {'text': ' last', 'type': 'word', 'data_start': 2.32, 'data_end': 2.48}]}]
        arrays = [['So', 2.09, 2.25], ['last', 2.32, 2.48]]
        objects = [{'text': 'So', 'startMs': 2090, 'endMs': 2250}, {'text': 'last', 'startMs': 2320, 'endMs': 2480}]
        review = {'words': [{'text': 'So', 'startMs': 290, 'endMs': 450}], 'original': {'words': objects}}
        cut_clock = {'words': [{'text': 'So', 'startMs': 290, 'endMs': 450, 'sourceStartMs': 2090, 'sourceEndMs': 2250},
                               {'text': 'last', 'startMs': 520, 'endMs': 680, 'sourceStartMs': 2320, 'sourceEndMs': 2480}]}
        for data in (happyscribe, arrays, review, cut_clock):
            with self.subTest(data=data):
                words = cut_timeline.load_words(data)
                self.assertEqual([(w.text, round(w.start, 3), round(w.end, 3)) for w in words], expected)

    def test_refuses_words_that_may_be_on_the_edited_clock(self):
        objects = [{'text': 'So', 'startMs': 290, 'endMs': 450}]
        for data in ({'words': objects}, objects, {'transcript': objects}):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, 'clock'):
                cut_timeline.load_words(data)

    def test_tiny_word_overlap_is_noise_and_a_real_one_is_refused(self):
        words = cut_timeline.load_words([['a', 1.0, 2.0], ['b', 1.999, 3.0]])
        self.assertEqual(words[1].start, words[0].end)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            cut_timeline.load_words([['a', 1.0, 2.0], ['b', 1.5, 3.0]])

    def test_refuses_bad_word_times(self):
        for words in ([], [['a', 1, 0]], [['a', -1, 1]], [['a', 1, float('nan')]], [['a', 1, 2], ['b', 0.5, 1]],
                      [{'text': 'a'}], [['a', 1]]):
            with self.subTest(words=words), self.assertRaises(ValueError):
                cut_timeline.load_words(words)

    def test_refuses_impossible_cut_lists(self):
        good = cutlist(*CLEAN)

        def broken(change):
            data = json.loads(json.dumps(good))
            change(data)
            return data
        cases = [
            lambda d: d.update(fps=0),
            lambda d: d.update(segments=[]),
            lambda d: d['segments'][0].update(sourceOutFrame=10),
            lambda d: d['segments'][0].update(durationFrames=99),
            lambda d: d['segments'][0].update(sourceInFrame=24.5),
            lambda d: d['segments'][1].update(timelineStartFrame=5),
            lambda d: d['segments'][1].update(sourcePath='other.mkv'),
            lambda d: d['segments'][1].update(timelineStartFrame=None),
            lambda d: d['segments'][0].pop('timelineStartFrame'),
            lambda d: d['segments'].append('not a segment'),
        ]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                cut_timeline.load_cutlist(broken(change))

    def test_refuses_a_cut_list_that_is_not_an_object(self):
        with self.assertRaises(ValueError):
            cut_timeline.load_cutlist([])

    def test_refuses_a_transcript_of_a_different_recording(self):
        first, second = 'a' * 64, 'b' * 64
        review = {'original': {'sourceId': first, 'words': [{'text': 'a', 'startMs': 0, 'endMs': 100}]}}
        with self.assertRaisesRegex(ValueError, 'different recording'):
            cut_timeline.check_same_source(review, cutlist(*CLEAN, sourceSha256=second))
        cut_timeline.check_same_source(review, cutlist(*CLEAN, sourceSha256=first))
        cut_timeline.check_same_source(WORDS, cutlist(*CLEAN))

    def test_a_source_label_that_is_not_a_hash_is_not_compared(self):
        review = {'original': {'sourceId': 'raw', 'words': [{'text': 'a', 'startMs': 0, 'endMs': 100}]}}
        cut_timeline.check_same_source(review, cutlist(*CLEAN, sourceSha256='b' * 64))

    def test_reordered_sections_map_each_word_through_its_own_section(self):
        words = [['one', 1.0, 1.4], ['two', 4.0, 4.4]]
        timeline = cut_timeline.load_cutlist(cutlist((117, 150), (24, 60)))
        pieces = cut_timeline.retained_words(cut_timeline.load_words(words), timeline)
        self.assertEqual([p.word.text for p in pieces], ['two', 'one'])
        self.assertAlmostEqual(pieces[0].timeline_start, 0.1)
        self.assertAlmostEqual(pieces[1].timeline_start, 1.1 + 0.2)
        report = join_check.check_joins(cut_timeline.load_words(words), timeline)
        self.assertEqual(report['joins'][0]['removedWords'], [])


if __name__ == '__main__':
    unittest.main()
