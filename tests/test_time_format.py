"""Every time the tool writes comes from one formatter per language: time_format.py and web/time-format.js.

The table tests run tests/fixtures/time-format-cases.json against time_format.py; tests/time-format.test.mjs runs the
same table against web/time-format.js, so a time reads the same in captions, paper edits, the overview page and the
review page. The guard reads every place that writes a time and fails on a time formatted there by hand.
"""
from pathlib import Path
import json
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import time_format  # noqa: E402

TABLE = json.loads((ROOT / 'tests/fixtures/time-format-cases.json').read_text(encoding='utf-8'))
OPTION_NAMES = {'places': 'places', 'nearest': 'nearest', 'hours': 'hours', 'padMinutes': 'pad_minutes'}

# The two homes of the time formats; every other file must call them.
HOMES = {'time_format.py', 'web/time-format.js'}
# Hand-made time formatting: milliseconds split into hours and minutes, seconds taken modulo 60, or a remainder of
# 1000 milliseconds written out. Each of the copies this replaced matches at least one of these.
HAND_MADE = {
    '.py': [re.compile(r'//\s*(60000|3600000)\b'), re.compile(r'%\s*60\b'),
            re.compile(r'divmod\([^)]*,\s*(60|3600)\s*\)'), re.compile(r'%\s*1000\s*:0?3\}')],
    '.js': [re.compile(r'/\s*(60000|3600000)\b'), re.compile(r'%\s*60\b'), re.compile(r'%\s*1000\b')],
}
HAND_MADE['.mjs'] = HAND_MADE['.js']


def writers():
    """Every source file of the tool (tests, docs and prototypes aside)."""
    files = [*ROOT.glob('*.py'), *ROOT.glob('scripts/video/*.py'), *ROOT.glob('scripts/video/*.mjs'),
             *ROOT.glob('web/*.js'), *ROOT.glob('cloud/*.js')]
    return sorted(path for path in files if path.relative_to(ROOT).as_posix() not in HOMES)


def hand_made(text, suffix):
    return [line.strip() for line in text.splitlines() if any(rule.search(line) for rule in HAND_MADE[suffix])]


class SharedTableTest(unittest.TestCase):
    def test_every_case_in_the_shared_table(self):
        for row in TABLE['cases']:
            with self.subTest(row['name']):
                args = list(row['args'])
                options = args.pop() if row['call'] == 'clock' and len(args) == 2 else {}
                call = getattr(time_format, row['call'])
                kwargs = {OPTION_NAMES[key]: value for key, value in options.items()}
                if row.get('error'):
                    with self.assertRaises(ValueError):
                        call(*args, **kwargs)
                else:
                    self.assertEqual(call(*args, **kwargs), row['returns'])

    def test_the_review_store_offers_the_shared_formats(self):
        import review_store
        self.assertIs(review_store.timecode, time_format.timecode)
        self.assertIs(review_store.frame_at, time_format.frame)


class NoHandMadeTimesTest(unittest.TestCase):
    def test_the_guard_catches_every_copy_it_replaced(self):
        copies = {
            '.py': ["return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'",
                    "hours, minutes = total // 3600000, total // 60000 % 60",
                    "text = f'{minutes:02}:{total // 1000 % 60:02}.{total % 1000:03}'",
                    "hours, rest = divmod(seconds, 3600)", "minutes, seconds = divmod(rest, 60)",
                    "return (f'{total // 3600000:02}:{total // 60000 % 60:02}:{total // 1000 % 60:02}'",
                    "f'{separator}{total % 1000:03}')"],
            '.js': ["const h=Math.floor(ms/3600000),m=Math.floor(ms/60000)%60,s=Math.floor(ms/1000)%60;",
                    "const stamp=ms=>`${Math.floor(ms/60000)}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}`;",
                    "const precise=ms=>short(ms)+'.'+String(Math.round(Math.max(0,ms))%1000).padStart(3,'0');",
                    "const clock=ms=>`${String(Math.round(ms)%1000).padStart(3,'0')}`;"],
        }
        for suffix, lines in copies.items():
            for line in lines:
                with self.subTest(line):
                    self.assertEqual(hand_made(line, suffix), [line])

    def test_no_place_formats_a_time_by_hand(self):
        self.assertGreater(len(writers()), 20)
        found = {}
        for path in writers():
            lines = hand_made(path.read_text(encoding='utf-8'), path.suffix)
            if lines:
                found[path.relative_to(ROOT).as_posix()] = lines
        self.assertEqual(found, {}, 'Write times with time_format.py or web/time-format.js, not by hand.')


if __name__ == '__main__':
    unittest.main()
