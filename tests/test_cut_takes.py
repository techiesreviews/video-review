"""Offline checks for the take cutter.

The Sunday fixture (tests/fixtures/sunday-retakes.words.json) is synthetic: the real Sunday transcript stays on the
editing PC. It follows the raw HappyScribe export shape and mirrors the Sunday paper edit's clear retake candidates
and "inspect before cutting" items at the same source seconds, plus a worse last take, an ad-lib, a demo, a long
pause before a new thought and a breath inside a linked phrase. No media is read.

The retakes copy the shapes that the real run on LexTop (2026-11-10) only flagged or cut in part: the transcript
has no quiet "again" there, an unfinished start runs into the next take without a full stop, a stray word sits
between two attempts, and a restart can begin part way through a phrase or after a comma.

The second real run (PR #17) showed more shapes, copied here at the same seconds with made-up words: a breath of
340 ms inside the preferred take at 603.68, unfinished pieces left between a removed take and the take it keeps,
a finished first try that opens like its take ("Setting up the things."), short stutters ("And then I—",
"But I'm—") that the paper edit only wants inspected, "So, again," that the script itself says, and a line
110 s away that only looks like an earlier take.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/video/cut-takes.py'
FIXTURES = ROOT / 'tests/fixtures'
sys.path.insert(0, str(ROOT / 'scripts/video'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cut_takes = load('cut_takes', SCRIPT)
join_check = load('join_check', ROOT / 'scripts/video/join-check.py')
import cut_timeline  # noqa: E402

FPS = 30
TRANSCRIPT = json.loads((FIXTURES / 'sunday-retakes.words.json').read_text(encoding='utf-8'))
SCRIPT_TEXT = (FIXTURES / 'sunday-retakes.script.md').read_text(encoding='utf-8')
WORDS = cut_timeline.load_words(TRANSCRIPT)
DEMO = [(410.0, 418.0)]

# The Sunday paper edit's clear retake candidates (source seconds) and where the take it prefers begins.
CANDIDATES = [((92.78, 98.42), 102.25), ((324.17, 326.37), 329.03), ((341.05, 349.23), 353.96),
              ((362.15, 370.21), 374.57), ((592.80, 601.86), 603.68), ((619.03, 621.10), 621.68)]
TOLERANCE = 0.011
# Lex's answers of 2026-11-10: room around speech and the whole silence at a join.
ROOM_MS = (100, 150)
GAP_MS = {'linked': (150, 250), 'new-thought': (250, 450)}


def plan(transcript=TRANSCRIPT, script=SCRIPT_TEXT, demos=DEMO):
    return cut_takes.plan(cut_timeline.load_words(transcript), FPS, script_text=script, demos=demos)


SUNDAY = plan()


def kept_starts(result):
    timeline = cut_timeline.load_cutlist(result['cutlist'])
    return {round(piece.word.start, 3) for piece in cut_timeline.retained_words(WORDS, timeline)
            if not piece.is_partial}


def words_between(start, end):
    return [word for word in WORDS if word.start >= start - TOLERANCE and word.end <= end + TOLERANCE]


def take_from(start):
    """The words of the sentence that starts at this source second."""
    chosen = []
    for word in WORDS:
        if word.start >= start - TOLERANCE:
            chosen.append(word)
            if word.text[-1] in '.!?':
                break
    return chosen


def removed_at(result, start):
    return next(item for item in result['paperEdit']['removed'] if abs(item['start'] - start) < TOLERANCE)


def flags(result, code):
    return [item for item in result['paperEdit']['flags'] if item['code'] == code]


def joins_from_frames(result, words=WORDS):
    """Measure every join from the cut list frames and the transcript, not from what the cutter reports."""
    timeline = cut_timeline.load_cutlist(result['cutlist'])
    by_run = {}
    for piece in cut_timeline.retained_words(words, timeline):
        by_run.setdefault(piece.run_index, []).append(piece)
    measured = []
    for before, after in zip(timeline.runs, timeline.runs[1:]):
        last, first = by_run[before.index][-1], by_run[after.index][0]
        tail = round((before.source_end - last.word.end) * 1000)
        head = round((first.word.start - after.source_start) * 1000)
        kind = 'new-thought' if last.word.text[-1] in '.!?' else 'linked'
        measured.append({'after': last.word, 'before': first.word, 'tail': tail, 'head': head,
                         'gap': tail + head, 'kind': kind})
    return measured


class SundayRetakeTests(unittest.TestCase):
    def test_every_clear_retake_candidate_is_removed_and_its_preferred_take_kept(self):
        kept = kept_starts(SUNDAY)
        for (start, end), take in CANDIDATES:
            attempt = words_between(start, end)
            self.assertTrue(attempt, f'fixture has words at {start}-{end}')
            self.assertFalse([word.text for word in attempt if round(word.start, 3) in kept],
                             f'the failed attempt at {start}-{end} is removed')
            whole_take = take_from(take)
            self.assertTrue(all(round(word.start, 3) in kept for word in whole_take),
                            f'the complete take at {take} is kept whole')

    def test_each_preferred_take_is_fully_inside_the_kept_ranges(self):
        timeline = cut_timeline.load_cutlist(SUNDAY['cutlist'])
        for _, take in CANDIDATES:
            for word in take_from(take):
                self.assertTrue(any(run.source_start <= word.start and word.end <= run.source_end
                                    for run in timeline.runs), f'"{word.text}" at {word.start} in the take at {take}')

    def test_each_attempt_run_is_one_removal_up_to_the_take_it_keeps(self):
        # One passage from the first failed start to just before the preferred take, not pieces.
        expected = {92.78: (98.42, 'retake-run', 102.25), 323.70: (326.37, 'abandoned-start', 329.03),
                    341.05: (349.23, 'retake-run', 353.96), 362.15: (370.21, 'earlier-take', 374.57),
                    592.80: (601.86, 'abandoned-start', 603.68), 619.03: (621.10, 'abandoned-start', 621.68)}
        for start, (end, reason, keeps) in expected.items():
            removal = removed_at(SUNDAY, start)
            self.assertEqual((round(removal['end'], 2), removal['reason'], removal['keeps']), (end, reason, keeps),
                             f'removal at {start}')
        self.assertEqual(len(SUNDAY['paperEdit']['removed']), len(expected) + 1)  # plus the worse take at 205

    def test_a_breath_inside_the_preferred_take_is_not_a_new_take(self):
        # 340 ms after "next week, I am" the take goes on: the removal stops before 603.68, not before 605.36.
        removal = removed_at(SUNDAY, 592.80)
        self.assertLess(removal['end'], 603.68)
        self.assertIn(603.68, kept_starts(SUNDAY))

    def test_a_removal_that_takes_out_a_long_silence_says_so(self):
        removal = removed_at(SUNDAY, 592.80)
        self.assertAlmostEqual(removal['longestSilence'], 3.43, 2)
        self.assertIn('3.4 s silence', cut_takes.render_paper_edit(SUNDAY['paperEdit']))

    def test_a_restart_part_way_through_a_phrase_keeps_the_take_and_the_line_before(self):
        self.assertAlmostEqual(removed_at(SUNDAY, 323.70)['end'], 326.37, 3)
        self.assertAlmostEqual(removed_at(SUNDAY, 619.03)['end'], 621.10, 3)

    def test_no_restart_flag_is_left_on_a_clear_retake(self):
        for (start, end), _ in CANDIDATES:
            self.assertFalse([item for item in SUNDAY['paperEdit']['flags'] if item['code'] == 'possible-restart'
                              and start - TOLERANCE <= item['start'] <= end], f'flag inside {start}-{end}')

    def test_the_introduction_before_the_closing_question_stays(self):
        kept = kept_starts(SUNDAY)
        self.assertTrue(all(round(word.start, 3) in kept for word in words_between(618.29, 618.92)))

    def test_again_and_again_and_look_again_are_speech(self):
        kept = kept_starts(SUNDAY)
        for start in (142.65, 560.00):
            self.assertTrue(all(round(word.start, 3) in kept for word in take_from(start)), f'line at {start}')
        self.assertFalse([item for item in SUNDAY['paperEdit']['removed'] if 139 < item['start'] < 146
                          or 559 < item['start'] < 562])

    def test_so_again_that_the_script_says_is_speech_not_a_retake_cue(self):
        kept = kept_starts(SUNDAY)
        self.assertTrue(all(round(word.start, 3) in kept for word in words_between(533.35, 537.03)))
        self.assertFalse([item for item in SUNDAY['paperEdit']['flags'] if 'again' in item['text']
                          and 533 < item['start'] < 535])
        # The 5.3 s silence before it now stays, so it is listed for Lex.
        self.assertTrue(any(abs(item['start'] - 528.09) < TOLERANCE for item in flags(SUNDAY, 'long-silence')))

    def test_a_line_far_away_that_only_looks_like_an_earlier_take_is_kept(self):
        kept = kept_starts(SUNDAY)
        self.assertTrue(all(round(word.start, 3) in kept for word in take_from(174.27)))
        self.assertTrue(all(round(word.start, 3) in kept for word in take_from(283.91)))
        flag, = flags(SUNDAY, 'no-complete-take')
        self.assertIn('174.27', flag['message'])

    def test_a_worse_last_take_keeps_the_better_one_and_flags_it(self):
        kept = kept_starts(SUNDAY)
        self.assertTrue(all(round(word.start, 3) in kept for word in take_from(200.00)))
        self.assertFalse([word for word in take_from(205.00) if round(word.start, 3) in kept])
        self.assertEqual(removed_at(SUNDAY, 205.00)['reason'], 'worse-later-take')
        flag, = flags(SUNDAY, 'kept-better-take')
        self.assertAlmostEqual(flag['start'], 200.00, 3)
        self.assertIn('205.00', flag['message'])

    def test_a_marked_screen_demo_is_flagged_instead_of_cut(self):
        kept = kept_starts(SUNDAY)
        self.assertTrue(all(round(word.start, 3) in kept for word in words_between(410.0, 418.0)))
        flag, = flags(SUNDAY, 'demo-protected')
        self.assertGreaterEqual(flag['start'], 410.0)

    def test_inspect_items_are_flagged_and_left_in(self):
        kept = kept_starts(SUNDAY)
        # The paper edit says inspect, not cut, at 63-70, 139-143, 410-418, 489-495 and 532-536.
        restarts = sorted(round(item['start']) for item in flags(SUNDAY, 'possible-restart'))
        self.assertEqual([start // 10 for start in restarts], [6, 14, 49])
        for start, end in ((63, 70), (139, 146), (410, 418), (489, 495), (532, 538)):
            self.assertTrue(all(round(word.start, 3) in kept for word in words_between(start, end)), f'{start}-{end}')
            self.assertFalse([item for item in SUNDAY['paperEdit']['removed'] if start <= item['start'] <= end])
        self.assertFalse([item for item in SUNDAY['paperEdit']['flags'] if 'short' in item['text']],
                         'repeated "short" is natural emphasis, not a flag')

    def test_ad_libs_are_kept_and_listed(self):
        # A failed try before a take of the same line (362.15, 592.80) is not an ad-lib, and neither is a short
        # take whose words are all in its script line (374.57).
        self.assertEqual([(item['start'], item['text']) for item in SUNDAY['paperEdit']['adLibs']],
                         [(450.0, 'By the way, this part was not planned at all.')])
        self.assertIn(450.0, kept_starts(SUNDAY))

    def test_a_long_silence_is_kept_and_flagged(self):
        silences = flags(SUNDAY, 'long-silence')
        self.assertTrue(any(abs(item['start'] - 3.40) < TOLERANCE for item in silences))
        timeline = cut_timeline.load_cutlist(SUNDAY['cutlist'])
        self.assertTrue(any(run.source_start < 4.0 and run.source_end > 63.0 for run in timeline.runs))


class TimingTests(unittest.TestCase):
    def test_no_cut_lands_inside_any_word(self):
        timeline = cut_timeline.load_cutlist(SUNDAY['cutlist'])
        for run in timeline.runs:
            for edge in (run.source_start, run.source_end):
                inside = [word.text for word in WORDS
                          if word.start + TOLERANCE < edge < word.end - TOLERANCE]
                self.assertEqual(inside, [], f'cut at {edge:.3f} s')

    def test_every_join_keeps_room_around_speech_and_a_whole_silence_in_range(self):
        measured = joins_from_frames(SUNDAY)
        self.assertEqual(len(measured), 10)
        for join in measured:
            where = f'join after "{join["after"].text}" at {join["after"].end}'
            self.assertTrue(ROOM_MS[0] <= join['tail'] <= ROOM_MS[1], f'{where}: tail {join["tail"]} ms')
            self.assertTrue(ROOM_MS[0] <= join['head'] <= ROOM_MS[1], f'{where}: head {join["head"]} ms')
            low, high = GAP_MS[join['kind']]
            self.assertTrue(low <= join['gap'] <= high, f'{where}: {join["kind"]} gap {join["gap"]} ms')

    def test_reported_joins_match_the_frames(self):
        reported = SUNDAY['paperEdit']['joins']
        measured = joins_from_frames(SUNDAY)
        self.assertEqual([(item['tailMs'], item['headMs'], item['gapMs'], item['kind']) for item in reported],
                         [(item['tail'], item['head'], item['gap'], item['kind']) for item in measured])

    def test_opening_and_ending_keep_room(self):
        timeline = cut_timeline.load_cutlist(SUNDAY['cutlist'])
        lead_in = round((WORDS[0].start - timeline.runs[0].source_start) * 1000)
        tail = round((timeline.runs[-1].source_end - WORDS[-1].end) * 1000)
        self.assertTrue(ROOM_MS[0] <= lead_in <= ROOM_MS[1], lead_in)
        self.assertTrue(ROOM_MS[0] <= tail <= ROOM_MS[1], tail)
        self.assertEqual(SUNDAY['paperEdit']['opening']['leadInMs'], lead_in)

    def test_a_breath_inside_a_linked_phrase_is_kept(self):
        timeline = cut_timeline.load_cutlist(SUNDAY['cutlist'])
        self.assertTrue(any(run.source_start < 564.6 and run.source_end > 565.15 for run in timeline.runs),
                        'the 550 ms breath after "better," is not cut')

    def test_a_long_pause_before_a_new_thought_is_tightened(self):
        join = next(item for item in joins_from_frames(SUNDAY) if item['after'].text == 'again.')
        self.assertEqual(join['kind'], 'new-thought')
        self.assertLess(join['gap'], 1200)

    def test_fades_are_noted(self):
        self.assertEqual(SUNDAY['cutlist']['audioFadeMs'], 15)
        self.assertTrue(all(item['fadeMs'] == 15 for item in SUNDAY['paperEdit']['joins']))
        self.assertIn('15 ms', cut_takes.render_paper_edit(SUNDAY['paperEdit']))

    def test_the_join_check_finds_no_problems(self):
        report = join_check.check_joins(WORDS, cut_timeline.load_cutlist(SUNDAY['cutlist']))
        problems = [flag for entry in report['joins'] + [report['opening'], report['ending']]
                    for flag in entry['flags'] if flag['severity'] == 'problem']
        self.assertEqual(problems, [])

    def test_the_join_check_accepts_the_cutters_opening(self):
        # Lex's choice of 2026-11-10: the join check wants 100-150 ms before the first word, as the cutter leaves.
        report = join_check.check_joins(WORDS, cut_timeline.load_cutlist(SUNDAY['cutlist']))
        self.assertEqual(report['opening']['flags'], [])
        self.assertEqual(join_check.THRESHOLDS['leadInMs'], list(ROOM_MS))


class OutputTests(unittest.TestCase):
    def test_cut_list_uses_the_repo_schema(self):
        cutlist = SUNDAY['cutlist']
        self.assertEqual((cutlist['schemaVersion'], cutlist['fps']), (1, FPS))
        cut_timeline.load_cutlist(cutlist)
        self.assertEqual(cutlist['durationFrames'], sum(item['durationFrames'] for item in cutlist['segments']))
        self.assertEqual(cutlist['segments'][0]['id'], 'C01')
        self.assertEqual(set(cutlist['segments'][0]),
                         {'id', 'sourceInFrame', 'sourceOutFrame', 'timelineStartFrame', 'durationFrames'})
        self.assertEqual(cutlist['status'], 'proposal')

    def test_paper_edit_waits_for_lex(self):
        paper = SUNDAY['paperEdit']
        self.assertEqual((paper['status'], paper['approvedByLex'], paper['rendered']), ('proposal', False, False))
        text = cut_takes.render_paper_edit(paper)
        self.assertTrue(text.startswith('# Paper edit'))
        self.assertIn('Not approved', text)
        self.assertIn('Nothing is cut or rendered', text)
        for needle in ('92.78', 'Kept the earlier take', 'By the way, this part was not planned at all.'):
            self.assertIn(needle, text)


class WithoutScriptTests(unittest.TestCase):
    def test_retakes_are_still_found_and_ad_libs_are_not_judged(self):
        result = plan(script=None, demos=())
        kept = kept_starts(result)
        timeline = cut_timeline.load_cutlist(result['cutlist'])
        for (start, end), take in CANDIDATES:
            for word in take_from(take):
                self.assertTrue(any(run.source_start <= word.start and word.end <= run.source_end
                                    for run in timeline.runs), f'"{word.text}" in the take at {take}')
            asked = [item for item in flags(result, 'possible-restart') if start - TOLERANCE <= item['end']
                     and item['start'] <= end + TOLERANCE]
            if start in (324.17, 362.15):
                self.assertFalse([word for word in words_between(start, end) if round(word.start, 3) in kept])
                self.assertEqual(asked, [], f'{start}-{end}')
            else:
                # Without a script nothing says these are one line said twice: exactly one flag, nothing cut.
                self.assertEqual(len(asked), 1, f'{start}-{end}')
                self.assertFalse([item for item in result['paperEdit']['removed'] if start <= item['start'] <= end])
        spans = sorted((item['start'], item['end']) for item in result['paperEdit']['removed'])
        self.assertFalse([pair for pair in zip(spans, spans[1:]) if pair[1][0] < pair[0][1]], 'removals overlap')
        self.assertEqual(result['paperEdit']['adLibs'], [])
        self.assertIn('No script', cut_takes.render_paper_edit(result['paperEdit']))
        self.assertEqual(removed_at(result, 205.00)['reason'], 'worse-later-take')


def plan_words(words, script=None, demos=(), fps=FPS):
    return cut_takes.plan(cut_timeline.load_words(words), fps, script_text=script, demos=demos)


def removed_texts(result):
    return [item['text'] for item in result['paperEdit']['removed']]


class CueTests(unittest.TestCase):
    def test_a_quiet_again_is_removed_with_its_failed_attempt(self):
        words = [['The', 92.78, 93.1], ['picture', 93.14, 93.6], ['from,', 93.64, 94.0], ['from', 94.04, 94.4],
                 ['start', 94.44, 94.9], ['again', 96.2, 96.5], ['The', 98.0, 98.2], ['picture', 98.24, 98.6],
                 ['shows', 98.64, 99.0], ['the', 99.04, 99.2], ['demo.', 99.24, 99.7]]
        removal, = plan_words(words)['paperEdit']['removed']
        self.assertEqual((removal['text'], removal['reason'], removal['keeps']),
                         ('The picture from, from start again', 'retake-cue', 98.0))

    def test_a_real_cue_is_still_a_cue_when_the_script_says_again_elsewhere(self):
        script = 'So, again, keep it short.'
        words = [['keep', 1.0, 1.2], ['it', 1.24, 1.4], ['long', 1.44, 1.8], ['again', 2.6, 2.9], ['keep', 3.6, 3.8],
                 ['it', 3.84, 4.0], ['short.', 4.04, 4.5]]
        self.assertEqual(removed_texts(plan_words(words, script)), ['keep it long again'])
        # An "again" the script says, even as the first word, is speech.
        words = [['Again,', 1.0, 1.3], ['keep', 1.9, 2.1], ['it', 2.14, 2.3], ['short.', 2.34, 2.8]]
        result = plan_words(words, script)
        self.assertEqual((removed_texts(result), result['paperEdit']['flags']), ([], []))

    def test_again_with_pauses_both_sides_is_a_retake_cue(self):
        words = [['Start', 1.0, 1.3], ['the', 1.34, 1.5], ['show', 1.54, 1.9], ['again', 2.6, 2.9],
                 ['Start', 3.6, 3.9], ['the', 3.94, 4.1], ['show', 4.14, 4.5], ['now.', 4.54, 4.9]]
        self.assertEqual(removed_texts(plan_words(words)), ['Start the show again'])

    def test_a_retake_inside_a_marked_demo_is_flagged_instead_of_removed(self):
        words = [['Start', 1.0, 1.3], ['the', 1.34, 1.5], ['show', 1.54, 1.9], ['again', 2.6, 2.9],
                 ['Start', 3.6, 3.9], ['the', 3.94, 4.1], ['show', 4.14, 4.5], ['now.', 4.54, 4.9]]
        result = plan_words(words, demos=[(0.5, 3.0)])
        self.assertEqual(removed_texts(result), [])
        flag, = result['paperEdit']['flags']
        self.assertEqual(flag['code'], 'demo-protected')
        self.assertIn('a quiet "again" marks a retake', flag['message'])

    def test_again_inside_normal_speech_is_kept(self):
        words = [['Say', 1.0, 1.3], ['it', 1.34, 1.5], ['again', 1.54, 1.9], ['louder.', 1.94, 2.4]]
        result = plan_words(words)
        self.assertEqual(removed_texts(result), [])
        self.assertEqual(result['paperEdit']['flags'], [])

    def test_again_and_again_is_speech_even_after_a_pause(self):
        words = [['Refine,', 1.0, 1.4], ['again', 1.9, 2.2], ['and', 2.24, 2.4], ['again.', 2.44, 2.8]]
        self.assertEqual(removed_texts(plan_words(words)), [])

    def test_an_again_after_a_pause_with_a_different_restart_is_flagged_not_cut(self):
        words = [['We', 1.0, 1.2], ['tried', 1.24, 1.6], ['it', 1.64, 1.8], ['again', 2.4, 2.7],
                 ['today.', 2.74, 3.2]]
        result = plan_words(words)
        self.assertEqual(removed_texts(result), [])
        self.assertEqual([item['code'] for item in result['paperEdit']['flags']], ['possible-retake-cue'])


class RestartTests(unittest.TestCase):
    def test_a_dropped_start_is_removed(self):
        words = [['So', 1.0, 1.2], ['the', 1.24, 1.4], ['new', 1.44, 1.6], ['tool', 1.64, 1.8], ['So', 2.6, 2.8],
                 ['the', 2.84, 3.0], ['new', 3.04, 3.2], ['tool', 3.24, 3.4], ['works.', 3.44, 3.9]]
        result = plan_words(words)
        self.assertEqual([(item['text'], item['reason']) for item in result['paperEdit']['removed']],
                         [('So the new tool', 'abandoned-start')])

    def test_a_dropped_start_of_under_four_words_is_flagged_not_cut(self):
        # The paper edit's inspect-only restarts ("And then I—", "But I'm—") are this short.
        for start in (['And', 'then', 'I—'], ["But", "I'm—"]):
            words = [[text, 1.0 + 0.3 * number, 1.2 + 0.3 * number] for number, text in enumerate(start)]
            words += [['and', 3.0, 3.2], ['then', 3.24, 3.4], ['I', 3.44, 3.5], ['look', 3.54, 3.8],
                      ['again.', 3.84, 4.2]] if len(start) == 3 else \
                [['but', 3.0, 3.2], ['I', 3.24, 3.3], ['managed', 3.34, 3.8], ['it.', 3.84, 4.0]]
            for script in (None, 'And then I look again. But I managed it.'):
                result = plan_words(words, script)
                self.assertEqual(removed_texts(result), [], (start, script))
                self.assertEqual([item['code'] for item in result['paperEdit']['flags']], ['possible-restart'],
                                 (start, script))

    def test_two_lines_that_only_open_alike_are_both_kept(self):
        words = [['This', 1.0, 1.2], ['is', 1.24, 1.4], ['the', 1.44, 1.6], ['tool.', 1.64, 2.0],
                 ['This', 2.6, 2.8], ['is', 2.84, 3.0], ['the', 3.04, 3.2], ['price.', 3.24, 3.6]]
        self.assertEqual(removed_texts(plan_words(words)), [])
        self.assertEqual(removed_texts(plan_words(words, 'This is the tool. This is the price.')), [])

    def test_an_ad_lib_said_twice_keeps_the_last_take_even_with_a_script(self):
        words = [['Quick', 1.0, 1.2], ['note', 1.24, 1.4], ['on', 1.44, 1.6], ['pricing.', 1.64, 2.0],
                 ['Quick', 2.6, 2.8], ['note', 2.84, 3.0], ['on', 3.04, 3.2], ['pricing.', 3.24, 3.6]]
        result = plan_words(words, 'This is the tool.')
        self.assertEqual([(item['text'], item['reason']) for item in result['paperEdit']['removed']],
                         [('Quick note on pricing.', 'earlier-take')])
        self.assertEqual([item['text'] for item in result['paperEdit']['adLibs']], ['Quick note on pricing.'])

    def test_a_restart_after_a_comma_is_cut_when_the_script_says_the_line_once(self):
        words = [['What', 619.03, 619.3], ['would', 619.34, 619.6], ['you', 619.64, 619.9], ['build', 619.94, 620.3],
                 ['if', 620.34, 620.6], ['you,', 620.64, 621.1], ['What', 621.68, 621.9], ['would', 621.94, 622.2],
                 ['you', 622.24, 622.5], ['build', 622.54, 622.9], ['with', 622.94, 623.2], ['this?', 623.24, 623.5]]
        result = plan_words(words, 'My question for you. What would you build with this?')
        self.assertEqual([(item['text'], item['reason']) for item in result['paperEdit']['removed']],
                         [('What would you build if you,', 'abandoned-start')])
        self.assertEqual(result['paperEdit']['flags'], [])
        # Without a script nothing says it is one line said twice: flag it, do not cut.
        result = plan_words(words)
        self.assertEqual(removed_texts(result), [])
        self.assertEqual([item['code'] for item in result['paperEdit']['flags']], ['possible-restart'])

    def test_a_repeat_after_a_comma_that_the_script_has_as_two_lines_is_flagged_not_cut(self):
        words = [['The', 1.0, 1.2], ['tool', 1.24, 1.5], ['is', 1.54, 1.7], ['fast,', 1.74, 2.1],
                 ['the', 2.6, 2.8], ['tool', 2.84, 3.1], ['is', 3.14, 3.3], ['cheap.', 3.34, 3.8]]
        for script in ('The tool is fast. The tool is cheap.', 'The tool is fast, the tool is cheap.'):
            result = plan_words(words, script)
            self.assertEqual(removed_texts(result), [], script)
            self.assertEqual([item['code'] for item in result['paperEdit']['flags']], ['possible-restart'], script)

    def test_a_different_sentence_between_two_openings_stops_the_restart(self):
        words = [['Next', 1.0, 1.2], ['week', 1.24, 1.5], ['I', 1.54, 1.6], ['will', 1.64, 1.8], ['show', 1.84, 2.1],
                 ['you', 2.14, 2.4], ['That', 4.0, 4.2], ['is', 4.24, 4.4], ['all', 4.44, 4.6], ['today.', 4.64, 5.0],
                 ['Next', 6.0, 6.2], ['week', 6.24, 6.5], ['I', 6.54, 6.6], ['will', 6.64, 6.8],
                 ['show', 6.84, 7.1], ['the', 7.14, 7.3], ['export.', 7.34, 7.8]]
        self.assertEqual(removed_texts(plan_words(words)), [])

    def test_two_script_lines_that_open_alike_are_both_kept(self):
        words = [['Here', 1.0, 1.2], ['is', 1.24, 1.4], ['the', 1.44, 1.6], ['plan.', 1.64, 2.0],
                 ['Here', 3.0, 3.2], ['is', 3.24, 3.4], ['the', 3.44, 3.6], ['plan', 3.64, 3.9], ['for', 3.94, 4.1],
                 ['the', 4.14, 4.3], ['whole', 4.34, 4.6], ['week.', 4.64, 5.0]]
        self.assertEqual(removed_texts(plan_words(words, 'Here is the plan. Here is the plan for the whole week.')),
                         [])

    def test_talk_between_two_openings_that_belongs_to_another_line_stays(self):
        script = 'I think this is great. The next thing is the board. I think this is the best part.'
        words = [['I', 1.0, 1.1], ['think', 1.14, 1.4], ['this', 1.44, 1.6], ['is', 1.64, 1.8], ['great', 1.84, 2.2],
                 ['and', 3.0, 3.2], ['the', 3.24, 3.4], ['next', 3.44, 3.7], ['thing', 3.74, 4.0], ['is', 4.04, 4.2],
                 ['the', 4.24, 4.4], ['board', 4.44, 4.8],
                 ['I', 6.0, 6.1], ['think', 6.14, 6.4], ['this', 6.44, 6.6], ['is', 6.64, 6.8], ['the', 6.84, 7.0],
                 ['best', 7.04, 7.3], ['part.', 7.34, 7.8]]
        self.assertEqual(removed_texts(plan_words(words, script)), [])
        result = plan_words(words)
        self.assertEqual(removed_texts(result), [])
        flag, = flags(result, 'possible-restart')
        self.assertIn('I think this is', flag['text'])

    def test_a_short_sentence_and_a_longer_different_one_that_open_alike_are_asked_about_not_cut(self):
        words = [['This', 1.0, 1.2], ['is', 1.24, 1.4], ['the', 1.44, 1.6], ['tool.', 1.64, 2.0], ['This', 3.8, 4.0],
                 ['is', 4.04, 4.2], ['the', 4.24, 4.4], ['price', 4.44, 4.7], ['we', 4.74, 4.9], ['pay', 4.94, 5.1],
                 ['for', 5.14, 5.3], ['it.', 5.34, 5.7]]
        result = plan_words(words)
        self.assertEqual(removed_texts(result), [])
        self.assertEqual([item['code'] for item in result['paperEdit']['flags']], ['possible-restart'])

    def test_a_line_the_script_says_twice_in_a_row_keeps_both(self):
        script = 'Thanks for watching this week. Thanks for watching this week.'
        words = [['Thanks', 1.0, 1.3], ['for', 1.34, 1.5], ['watching', 1.54, 1.9], ['this', 1.94, 2.1],
                 ['week.', 2.14, 2.5], ['Thanks', 4.5, 4.8], ['for', 4.84, 5.0], ['watching', 5.04, 5.4],
                 ['this', 5.44, 5.6], ['week.', 5.64, 6.0]]
        self.assertEqual(removed_texts(plan_words(words, script)), [])

    def test_an_unfinished_start_inside_a_marked_demo_is_flagged_once_instead_of_cut(self):
        words = [['So', 1.0, 1.2], ['next,', 1.3, 1.6], ['the', 1.64, 1.8], ['animation', 1.84, 2.3],
                 ['skills', 2.34, 2.7], ['Next,', 4.0, 4.3], ['the', 4.34, 4.5], ['animation', 4.54, 5.0],
                 ['skills', 5.04, 5.4], ['make', 5.44, 5.6], ['every', 5.64, 5.9], ['move', 5.94, 6.2],
                 ['feel', 6.24, 6.4], ['smooth.', 6.44, 6.9]]
        script = 'Next, the animation skills make every move feel smooth.'
        for text in (script, None):
            result = plan_words(words, text, demos=[(0.5, 7.0)])
            self.assertEqual(removed_texts(result), [], text)
            self.assertEqual([item['code'] for item in result['paperEdit']['flags']], ['demo-protected'], text)

    def test_an_unscripted_line_that_only_opens_like_the_take_is_an_ad_lib(self):
        script = 'Setting up the AI took three tries.'
        words = [['Setting', 1.0, 1.3], ['up', 1.34, 1.5], ['the', 1.54, 1.7], ['lights', 1.74, 2.1], ['was', 2.14, 2.3],
                 ['a', 2.34, 2.4], ['real', 2.44, 2.7], ['pain', 2.74, 3.0], ['today.', 3.04, 3.5],
                 ['Setting', 5.5, 5.8], ['up', 5.84, 6.0], ['the', 6.04, 6.2], ['AI', 6.24, 6.5], ['took', 6.54, 6.8],
                 ['three', 6.84, 7.1], ['tries.', 7.14, 7.6]]
        result = plan_words(words, script)
        self.assertEqual(removed_texts(result), [])
        self.assertEqual([item['text'] for item in result['paperEdit']['adLibs']],
                         ['Setting up the lights was a real pain today.'])

    def test_an_ad_lib_followed_by_a_different_line_that_opens_alike_is_still_listed(self):
        script = 'And then I look again at the timeline.'
        words = [['And', 1.0, 1.2], ['then', 1.24, 1.4], ['I', 1.44, 1.5], ['spilled', 1.54, 1.9], ['my', 1.94, 2.1],
                 ['coffee.', 2.14, 2.6], ['And', 4.0, 4.2], ['then', 4.24, 4.4], ['I', 4.44, 4.5], ['look', 4.54, 4.8],
                 ['again', 4.84, 5.1], ['at', 5.14, 5.2], ['the', 5.24, 5.4], ['timeline.', 5.44, 5.9]]
        result = plan_words(words, script)
        self.assertEqual(removed_texts(result), [])
        self.assertEqual([item['text'] for item in result['paperEdit']['adLibs']], ['And then I spilled my coffee.'])

    def test_failed_tries_for_different_takes_stay_separate_removals(self):
        words = [['So', 1.0, 1.2], ['the', 1.24, 1.4], ['new', 1.44, 1.6], ['tool', 1.64, 1.8], ['So', 2.6, 2.8],
                 ['the', 2.84, 3.0], ['new', 3.04, 3.2], ['tool', 3.24, 3.4], ['works.', 3.44, 3.9],
                 ['Then', 5.0, 5.2], ['the', 5.24, 5.4], ['robot', 5.44, 5.7], ['ships', 5.74, 6.0], ['Then', 7.0, 7.2],
                 ['the', 7.24, 7.4], ['robot', 7.44, 7.7], ['ships', 7.74, 8.0], ['it.', 8.04, 8.4]]
        result = plan_words(words)
        self.assertEqual([(item['text'], item['keeps']) for item in result['paperEdit']['removed']],
                         [('So the new tool', 2.6), ('Then the robot ships', 7.0)])

    def test_a_long_pause_inside_a_sentence_without_a_restart_stays(self):
        words = [['So', 1.0, 1.2], ['I', 1.24, 1.3], ['click', 1.34, 1.6], ['here', 1.64, 1.9], ['and', 4.0, 4.2],
                 ['the', 4.24, 4.4], ['page', 4.44, 4.7], ['opens.', 4.74, 5.2],
                 ['So', 6.0, 6.2], ['I', 6.24, 6.3], ['click', 6.34, 6.6], ['save', 6.64, 6.9], ['now.', 6.94, 7.3]]
        self.assertEqual(removed_texts(plan_words(words)), [])
        self.assertEqual(removed_texts(plan_words(words, 'So I click here and the page opens. So I click save now.')),
                         [])

    def test_repeating_on_purpose_after_a_comma_is_flagged_not_cut(self):
        words = [['The', 1.0, 1.2], ['tool', 1.24, 1.5], ['is', 1.54, 1.7], ['fast,', 1.74, 2.1],
                 ['the', 2.6, 2.8], ['tool', 2.84, 3.1], ['is', 3.14, 3.3], ['cheap.', 3.34, 3.8]]
        result = plan_words(words)
        self.assertEqual(removed_texts(result), [])
        self.assertEqual([item['code'] for item in result['paperEdit']['flags']], ['possible-restart'])

    def test_a_line_the_script_says_twice_is_not_treated_as_a_retake(self):
        script = 'Thanks for watching this week. Next up the demo. Thanks for watching this week.'
        words = [['Thanks', 1.0, 1.3], ['for', 1.34, 1.5], ['watching', 1.54, 1.9], ['this', 1.94, 2.1],
                 ['week.', 2.14, 2.5], ['Next', 3.0, 3.3], ['up', 3.34, 3.5], ['the', 3.54, 3.7], ['demo.', 3.74, 4.1],
                 ['Thanks', 4.6, 4.9], ['for', 4.94, 5.1], ['watching', 5.14, 5.5], ['this', 5.54, 5.7],
                 ['week.', 5.74, 6.1]]
        self.assertEqual(removed_texts(plan_words(words, script)), [])


class EdgeCaseTests(unittest.TestCase):
    def test_too_little_silence_is_flagged_and_no_word_is_cut(self):
        # The failed attempt starts 40 ms after "words.", so 100 ms of room cannot fit there.
        words = [['Intro', 0.5, 0.8], ['words.', 0.84, 1.2], ['Start', 1.24, 1.5], ['the', 1.54, 1.7],
                 ['show', 1.74, 2.0], ['again', 2.7, 3.0], ['Start', 3.7, 4.0], ['the', 4.04, 4.2],
                 ['show', 4.24, 4.5], ['now.', 4.54, 4.9]]
        result = plan_words(words)
        self.assertEqual(removed_texts(result), ['Start the show again'])
        self.assertIn('tight-edge', [item['code'] for item in result['paperEdit']['flags']])
        timeline = cut_timeline.load_cutlist(result['cutlist'])
        loaded = cut_timeline.load_words(words)
        for run in timeline.runs:
            for edge in (run.source_start, run.source_end):
                self.assertEqual([w.text for w in loaded if w.start + TOLERANCE < edge < w.end - TOLERANCE], [])

    def test_a_removal_too_short_for_whole_frames_goes_back_in(self):
        words = [['Okay.', 0.5, 1.0], ['Go,', 1.0, 1.01], ['again,', 1.01, 1.02], ['go', 1.02, 1.3],
                 ['now.', 1.34, 1.6]]
        result = plan_words(words)
        self.assertEqual(removed_texts(result), [])
        self.assertIn('too-short-to-cut', [item['code'] for item in result['paperEdit']['flags']])
        self.assertEqual(len(result['cutlist']['segments']), 1)

    def test_a_fractional_frame_rate_still_never_cuts_inside_a_word(self):
        result = cut_takes.plan(WORDS, 29.97, script_text=SCRIPT_TEXT, demos=DEMO)
        timeline = cut_timeline.load_cutlist(result['cutlist'])
        self.assertEqual(timeline.fps, 29.97)
        for run in timeline.runs:
            for edge in (run.source_start, run.source_end):
                self.assertEqual([w.text for w in WORDS if w.start + TOLERANCE < edge < w.end - TOLERANCE], [])
        self.assertEqual(kept_starts(result) & {round(w.start, 3) for w in words_between(92.78, 98.42)}, set())


class FarApartTests(unittest.TestCase):
    def test_a_retake_far_from_its_first_take_is_matched_by_the_script(self):
        script = 'The review page saves every comment. Other things happen. The end.'
        words = [['The', 10.0, 10.2], ['review', 10.24, 10.6], ['page', 10.64, 10.9], ['saves', 10.94, 11.2],
                 ['every', 11.24, 11.5], ['comment.', 11.54, 12.0],
                 ['Other', 13.0, 13.3], ['things', 13.34, 13.7], ['happen.', 13.74, 14.2],
                 ['The', 100.0, 100.2], ['review', 100.24, 100.6], ['page', 100.64, 100.9],
                 ['saves', 100.94, 101.2], ['every', 101.24, 101.5], ['comment.', 101.54, 102.0],
                 ['The', 103.0, 103.2], ['end.', 103.24, 103.6]]
        result = plan_words(words, script)
        removal, = result['paperEdit']['removed']
        self.assertEqual((removal['start'], removal['reason'], removal['keeps']), (10.0, 'earlier-take', 100.0))


class CommandTests(unittest.TestCase):
    def test_command_writes_the_three_files_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'proposal'
            command = [sys.executable, str(SCRIPT), str(FIXTURES / 'sunday-retakes.words.json'), '--fps', '30',
                       '--script', str(FIXTURES / 'sunday-retakes.script.md'), '--demo', '410-418',
                       '--out-dir', str(out)]
            done = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn('paper-edit.md', done.stdout)
            self.assertEqual(sorted(path.name for path in out.iterdir()),
                             ['cutlist.json', 'paper-edit.json', 'paper-edit.md'])
            cutlist = json.loads((out / 'cutlist.json').read_text(encoding='utf-8'))
            self.assertEqual(cutlist['segments'], SUNDAY['cutlist']['segments'])
            again = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertIn('already exists', again.stderr)

    def test_a_bad_demo_range_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([sys.executable, str(SCRIPT), str(FIXTURES / 'sunday-retakes.words.json'),
                                   '--fps', '30', '--demo', '418-410', '--out-dir', folder],
                                  capture_output=True, text=True)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn('demo', done.stderr.lower())


if __name__ == '__main__':
    unittest.main()
