"""Lex's cut preferences live in one file (web/cut-preferences.json): every cut script reads it, and no script types
a cut number of its own.

The reading tests copy the scripts and the file into a temporary folder, change numbers in the copy and check that
the scripts follow the copy. The guard reads the source of every script and page module and fails on a cut number
typed outside the file.
"""
import ast
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREFERENCES = ROOT / 'web/cut-preferences.json'
SCRIPTS = sorted((ROOT / 'scripts/video').glob('*.py'))
PAGE_MODULES = sorted((ROOT / 'web').glob('*.js'))
# The join check's names for preferences the cutter calls roomMs: the same room before the first word and beside
# every cut.
ALIASES = {'leadInMs', 'speechEdgeMs'}
# The page's name for the fade.
PAGE_NAMES = {'JOIN_FADE_MS'}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[name]
    return module


def saved():
    return json.loads(PREFERENCES.read_text(encoding='utf-8'))


def value(entry):
    return entry['value'] if isinstance(entry, dict) else entry


def preference_names(data):
    return {name for section in ('lex', 'cutter', 'joinCheck') for name in data[section]}


def ranges(data):
    """Every two-number range in the file, as a tuple."""
    return {tuple(value(entry)) for section in ('lex', 'cutter', 'joinCheck') for entry in data[section].values()
            if isinstance(value(entry), list)}


class ChangedCopy:
    """The scripts and the preferences file in a temporary folder, with numbers changed in the file."""

    def __init__(self, change):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        shutil.copytree(ROOT / 'scripts/video', root / 'scripts/video')
        (root / 'web').mkdir()
        data = saved()
        change(data)
        (root / 'web/cut-preferences.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
        self.scripts = root / 'scripts/video'
        self.added = str(self.scripts)
        sys.path.insert(0, self.added)
        self.cached = {name: sys.modules.pop(name) for name in ('cut_preferences', 'cut_timeline', 'script_text')
                       if name in sys.modules}

    def load(self, name, file):
        return load(name, self.scripts / file)

    def close(self):
        sys.path.remove(self.added)
        for name in ('cut_preferences', 'cut_timeline', 'script_text'):
            sys.modules.pop(name, None)
        sys.modules.update(self.cached)
        self.temp.cleanup()


def change_numbers(data):
    data['lex']['roomMs']['value'] = [111, 161]
    data['lex']['linkedGapMs']['value'] = [222, 333]
    data['lex']['newThoughtGapMs']['value'] = [377, 555]
    data['lex']['audioFadeMs']['value'] = 17
    data['cutter']['breathMaxMs'] = 777
    data['joinCheck']['orphanPauseMs'] = 333


class EveryScriptReadsTheFile(unittest.TestCase):
    def setUp(self):
        self.copy = ChangedCopy(change_numbers)
        self.addCleanup(self.copy.close)

    def test_the_cutter_reads_the_file(self):
        cut_takes = self.copy.load('cut_takes_copy', 'cut-takes.py')
        self.assertEqual(cut_takes.SETTINGS['roomMs'], [111, 161])
        self.assertEqual(cut_takes.SETTINGS['linkedGapMs'], [222, 333])
        self.assertEqual(cut_takes.SETTINGS['newThoughtGapMs'], [377, 555])
        self.assertEqual(cut_takes.SETTINGS['audioFadeMs'], 17)
        self.assertEqual(cut_takes.SETTINGS['breathMaxMs'], 777)

    def test_the_join_check_reads_the_file(self):
        join_check = self.copy.load('join_check_copy', 'join-check.py')
        self.assertEqual(join_check.THRESHOLDS['leadInMs'], [111, 161])
        self.assertEqual(join_check.THRESHOLDS['speechEdgeMs'], [111, 161])
        self.assertEqual(join_check.THRESHOLDS['linkedGapMs'], [222, 333])
        self.assertEqual(join_check.THRESHOLDS['newThoughtGapMs'], [377, 555])
        self.assertEqual(join_check.THRESHOLDS['audioFadeMs'], 17)
        self.assertEqual(join_check.THRESHOLDS['orphanPauseMs'], 333)

    def test_the_cutter_and_the_join_check_share_the_room_at_a_new_thought(self):
        def change(data):
            data['cutter']['newThoughtRoomMaxMs'] = 222
        copy = ChangedCopy(change)
        self.addCleanup(copy.close)
        self.assertEqual(copy.load('cut_takes_room', 'cut-takes.py').SETTINGS['newThoughtRoomMaxMs'], 222)
        self.assertEqual(copy.load('join_check_room', 'join-check.py').THRESHOLDS['newThoughtRoomMaxMs'], 222)

    def test_a_cut_follows_the_changed_numbers(self):
        """The cutter's cut moves with the file: the room before the first word and the pause it aims for."""
        def change(data):
            change_numbers(data)
            data['lex']['linkedGapMs']['value'] = [270, 320]
            data['cutter']['linkedTargetMs'] = 300
        copy = ChangedCopy(change)
        self.addCleanup(copy.close)
        cut_takes = copy.load('cut_takes_cut', 'cut-takes.py')
        words = cut_takes.cut_timeline.load_words([['Intro', 0.5, 1.0], ['next', 2.0, 2.3], ['thing.', 2.34, 2.7]])
        result = cut_takes.plan(words, 30)
        join = result['paperEdit']['joins'][0]
        self.assertEqual(join['fadeMs'], 17)
        self.assertTrue(270 <= join['gapMs'] <= 320, join)
        lead_in = round((0.5 - result['cutlist']['segments'][0]['sourceInFrame'] / 30) * 1000)
        self.assertTrue(111 <= lead_in <= 161, lead_in)


class TheValuesOfToday(unittest.TestCase):
    """The numbers in the file are the ones Lex chose: moving them into the file changes no cut."""

    def test_lex_choices_have_their_reason_and_date(self):
        lex = saved()['lex']
        self.assertEqual({name: entry['value'] for name, entry in lex.items()},
                         {'roomMs': [100, 150], 'linkedGapMs': [200, 300], 'newThoughtGapMs': [350, 500],
                          'audioFadeMs': 15})
        for name, entry in lex.items():
            self.assertRegex(entry['why'], r'\d{4}-\d{2}-\d{2}', name)

    def test_cut_to_review_falls_back_to_the_file_fade(self):
        for folder in (str(ROOT / 'scripts/video'), str(ROOT)):
            sys.path.insert(0, folder)
            self.addCleanup(sys.path.remove, folder)
        cut_to_review = load('cut_to_review_fade', ROOT / 'scripts/video/cut-to-review.py')
        self.assertEqual(cut_to_review.cut_fade_ms({'segments': []}), saved()['lex']['audioFadeMs']['value'])
        self.assertEqual(cut_to_review.cut_fade_ms({'audioFadeMs': 20}), 20)


def python_offences(source, names, known_ranges):
    """Cut numbers typed in Python source: a preference name given a number, or one of the file's ranges."""
    offences = []

    def number(node):
        return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool)

    def numbers(node):
        return number(node) or isinstance(node, (ast.List, ast.Tuple)) and node.elts and all(map(number, node.elts))

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            for key, item in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in names and numbers(item):
                    offences.append(f'line {key.lineno}: {key.value!r} is given a number')
        elif isinstance(node, ast.keyword) and node.arg in names and numbers(node.value):
            offences.append(f'line {node.value.lineno}: {node.arg} is given a number')
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in names and node.value is not None \
                        and numbers(node.value):
                    offences.append(f'line {node.lineno}: {target.id} is given a number')
        if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) == 2 and all(map(number, node.elts)) \
                and tuple(item.value for item in node.elts) in known_ranges:
            offences.append(f'line {node.lineno}: the range {[item.value for item in node.elts]} is typed')
    return offences


def page_offences(source, names):
    """Cut numbers typed in page code: a preference name or the page's fade name given a number."""
    pattern = re.compile(r'\b(' + '|'.join(sorted(names | PAGE_NAMES)) + r')\s*[:=]\s*\[?\s*-?\d')
    return [f'line {source.count(chr(10), 0, match.start()) + 1}: {match.group(1)} is given a number'
            for match in pattern.finditer(source)]


SENTENCE_END_TEXT = r'''[.!?]["\')\]]*$'''


class NoCutNumberOutsideTheFile(unittest.TestCase):
    def setUp(self):
        data = saved()
        self.names = preference_names(data) | ALIASES
        self.ranges = ranges(data)

    def test_no_script_types_a_cut_number(self):
        found = {path.name: python_offences(path.read_text(encoding='utf-8'), self.names, self.ranges)
                 for path in SCRIPTS}
        self.assertEqual({name: offences for name, offences in found.items() if offences}, {})

    def test_no_page_module_types_a_cut_number(self):
        found = {path.name: page_offences(path.read_text(encoding='utf-8'), self.names) for path in PAGE_MODULES}
        self.assertEqual({name: offences for name, offences in found.items() if offences}, {})

    def test_what_ends_a_sentence_is_written_once(self):
        """The cutter, the join check and the captions read one sentence-end rule (cut_timeline.SENTENCE_END)."""
        holders = [path.name for path in SCRIPTS if SENTENCE_END_TEXT in path.read_text(encoding='utf-8')]
        self.assertEqual(holders, ['cut_timeline.py'])

    def test_the_guard_finds_a_typed_number(self):
        self.assertEqual(len(python_offences("SETTINGS = {'roomMs': [100, 150]}", self.names, self.ranges)), 2)
        self.assertEqual(len(python_offences("THRESHOLDS = {'speechEdgeMs': 120}", self.names, self.ranges)), 1)
        self.assertEqual(len(python_offences('limit = max(gap, 350, 500) if (350, 500) else 0', self.names,
                                             self.ranges)), 1)
        self.assertEqual(len(python_offences('plan(words, breathMaxMs=700)', self.names, self.ranges)), 1)
        self.assertEqual(len(page_offences('export const JOIN_FADE_MS=15;', self.names)), 1)
        self.assertEqual(len(page_offences('jumpPlan(ms,edits,{audioFadeMs: 20})', self.names)), 1)
        self.assertEqual(python_offences("SETTINGS = {'roomMs': PREFERENCES['roomMs']}", self.names, self.ranges),
                         [])
        self.assertEqual(page_offences('export const JOIN_FADE_MS=preferences.lex.audioFadeMs.value;', self.names),
                         [])


if __name__ == '__main__':
    unittest.main()
