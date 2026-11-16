"""The review-comment rules live in one file (web/review-rules.json) that the local app and the hosted site both read.

The table tests run tests/fixtures/review-rule-cases.json against the local app's rules (review_store.py, through its
real data-folder calls); tests/review-rules.test.mjs runs the same table against the browser and the hosted Worker
(web/review-core.js). The guard reads the source of every place that applies the rules and fails on a rule typed there
instead of read from the file.
"""
from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import review_store as store  # noqa: E402

RULES = json.loads((ROOT / 'web/review-rules.json').read_text(encoding='utf-8'))
TABLE = json.loads((ROOT / 'tests/fixtures/review-rule-cases.json').read_text(encoding='utf-8'))
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

# Every file that applies the comment rules, and the two that define them (only there are the limits searched for
# by value: elsewhere a number such as 10000 is just as likely a timeout).
RULE_FILES = ['review_store.py', 'server.py', 'mcp_server.py', 'export_saved_comments.py',
              'web/review-core.js', 'web/transcript-core.js', 'web/backend.js', 'cloud/worker.js']
RULE_HOMES = ['review_store.py', 'web/review-core.js']


def expand(value):
    """The table's shorthand: repeated text, long lists and the fixed clock."""
    if isinstance(value, dict):
        if set(value) == {'$repeat'}:
            text, count = value['$repeat']
            return text * count
        if set(value) == {'$list'}:
            item, count = value['$list']
            return [item] * count
        return {key: expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item) for item in value]
    if value == '<now>':
        return TABLE['now']
    return value


def plain(value):
    """An answer as the table writes it: new random ids as <uuid>."""
    if isinstance(value, dict):
        return {key: '<uuid>' if key == 'id' and isinstance(item, str) and UUID.match(item) else plain(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


class DataFolder:
    """A local data folder holding a case's videos and comments, with the clock fixed."""

    def __init__(self, state):
        self.temp = tempfile.TemporaryDirectory()
        self.path = store.data_dir(self.temp.name)
        missing = str(Path(self.temp.name) / 'not-rendered-yet.mp4')
        store.write(self.path / 'manifest.json', [{**video, 'path': missing} for video in state['videos']])
        store.write(self.path / 'comments.json', {'comments': state['comments']})
        self.clock = mock.patch.object(store, 'now', return_value=TABLE['now'])
        self.clock.start()

    def videos(self):
        return [{key: item for key, item in video.items() if key != 'path'} for video in store.videos(self.path)]

    def comments(self):
        return store.comments(self.path)

    def close(self):
        self.clock.stop()
        self.temp.cleanup()


def run_local(case):
    """What the local app answers for one case: {'returns': ...} or {'error': ...}, plus the saved state it touched."""
    call, args = case['call'], expand(case['args'])
    simple = {'parseTime': store.parse_time, 'timecode': store.timecode, 'sourceSpans': store.source_spans,
              'checkEditRequest': store.check_edit_request, 'describe': store.describe}
    if call in simple:
        return {'returns': simple[call](*args)}
    if call == 'checkRender':
        return {'returns': store.check_render(args[0].get('durationMs'), args[0].get('keepRanges'))}
    folder = DataFolder(args[0])
    calls = {'applyChange': lambda: store.apply(folder.path, args[1])[1],
             'applyRender': lambda: store.update_render(folder.path, args[1], args[2].get('durationMs'),
                                                        args[2].get('keepRanges')),
             'saveTranscript': lambda: store.save_transcript(folder.path, args[1], args[2])}
    if call not in calls:
        raise AssertionError(f'The table names a call the local runner does not know: {call}')
    try:
        try:
            answer = {'returns': calls[call]()}
        except ValueError as error:  # a refused change: the saved state shows what it left behind
            answer = {'error': error}
        return {**answer, 'comments': folder.comments(), 'videos': folder.videos()}
    finally:
        folder.close()


class TheLocalAppFollowsTheTable(unittest.TestCase):
    def test_every_case(self):
        self.assertGreater(len(TABLE['cases']), 40)
        for case in TABLE['cases']:
            with self.subTest(case['name']):
                try:
                    answer = run_local(case)
                except Exception as error:
                    answer = {'error': error}
                if 'error' in case or 'error' in answer:
                    self.assertEqual(str(answer.get('error')), RULES['messages'].get(case.get('error')))
                for key in ('returns', 'comments', 'videos'):
                    if key in case:
                        self.assertEqual(plain(answer[key]), expand(case[key]), key)


def copies(root):
    """Each rule typed in a rule file instead of read from web/review-rules.json, as 'file: what'."""
    found = []
    texts = {name: (root / name).read_text(encoding='utf-8') for name in RULE_FILES if (root / name).exists()}
    for name, text in texts.items():
        for key, message in RULES['messages'].items():
            # The message as a whole string in the code ('Invalid time', not the start of 'Invalid timed word').
            written = {message, message.replace('"', '\\"')}
            if any(re.search(rf'''['"`]{re.escape(form)}['"`]''', text) for form in written):
                found.append(f'{name}: message {key}')
        for key in ('authors', 'statuses', 'editRequestTypes', 'selectionFields'):
            first, second = RULES[key][:2]
            if re.search(rf'''['"]{re.escape(first)}['"]\s*,\s*['"]{re.escape(second)}['"]''', text):
                found.append(f'{name}: the list {key}')
        if RULES['patterns']['videoId'].strip('^$') in text:
            found.append(f'{name}: the video id pattern')
    for name in RULE_HOMES:
        if name not in texts:
            continue
        for key, number in RULES['limits'].items():
            digits = '[_,]?'.join(str(number))
            if number >= 1000 and re.search(rf'(?<![\w.]){digits}(?![\w.])', texts[name]):
                found.append(f'{name}: the limit {key}')
    return found


class TheExportUsesTheRules(unittest.TestCase):
    def test_open_comments_are_exported_with_the_original_frames_a_comment_shows(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            video = {'id': 'v', 'title': 'Cut', 'label': 'v1', 'path': str(folder / 'cut.mp4'), 'fps': 30,
                     'durationMs': 2000, 'fingerprint': 'x', 'keepRanges': [[0, 30], [60, 90]]}
            restore = {'type': 'restore', 'sourceSpans': [{'sourceStartFrame': 390, 'sourceEndFrame': 405,
                                                           'sourceStartMs': 13000, 'sourceEndMs': 13500}]}
            store.write(folder / 'manifest.json', [video])
            store.write(folder / 'comments.json', {'comments': [
                {'id': 'over-a-cut', 'videoId': 'v', 'startMs': 500, 'endMs': 1500, 'text': 'Tighter', 'status': 'open'},
                # Saved when it was added, under a mapping today's keep ranges cannot give: kept as saved.
                {'id': 'saved', 'videoId': 'v', 'startMs': 500, 'endMs': None, 'text': 'Here', 'status': 'open',
                 'sourceSpans': [{'sourceFrame': 900, 'sourceMs': 30000}]},
                {'id': 'restore', 'videoId': 'v', 'startMs': 500, 'endMs': None, 'text': 'Put it back', 'status': 'open',
                 'editRequest': restore},
                {'id': 'finished', 'videoId': 'v', 'startMs': 0, 'endMs': None, 'text': 'Done', 'status': 'done'}]})
            run = subprocess.run([sys.executable, '-I', str(ROOT / 'export_saved_comments.py'), '--data-dir', temp],
                                 capture_output=True, text=True, check=True)
            exported = {c['id']: c['sourceSpans'] for c in json.loads(run.stdout)['openComments']}
        self.assertEqual(exported, {'over-a-cut': store.source_spans(video, 500, 1500),
                                    'saved': [{'sourceFrame': 900, 'sourceMs': 30000}],
                                    'restore': restore['sourceSpans']})


class EachRuleIsTypedOnce(unittest.TestCase):
    def test_no_rule_file_types_a_rule_of_its_own(self):
        self.assertEqual(copies(ROOT), [])

    def test_both_sides_read_the_rules_file(self):
        for name in RULE_HOMES:
            with self.subTest(name):
                self.assertIn('review-rules.json', (ROOT / name).read_text(encoding='utf-8'))

    def test_the_guard_catches_a_copied_rule(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'web').mkdir()
            (root / 'review_store.py').write_text(
                "MAX=10_000\nif s not in ['open','done']:raise ValueError('Invalid status')\n", encoding='utf-8')
            (root / 'web/review-core.js').write_text(
                "const TYPES=['remove','restore'];const ID=/^[A-Za-z0-9._-]{1,100}$/;", encoding='utf-8')
            self.assertEqual(sorted(copies(root)), sorted([
                'review_store.py: message status', 'review_store.py: the list statuses',
                'review_store.py: the limit textLength', 'web/review-core.js: the list editRequestTypes',
                'web/review-core.js: the video id pattern']))


if __name__ == '__main__':
    unittest.main()
