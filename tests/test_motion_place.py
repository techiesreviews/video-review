"""tools/motion_place.py and motion/templates/verdict-card.setting, without Resolve.

The Resolve part (placing the card) was proved by hand on a throwaway project; see docs/motion-engine.md.
These checks keep the template and the script in step, and keep two faults found on the way from coming back.
"""
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import motion_place  # noqa: E402

TEMPLATE = (ROOT / 'motion' / 'templates' / 'verdict-card.setting').read_text(encoding='utf-8')


def tool_blocks(text):
    """{tool name: (tool type, block text)} for the tools inside the macro."""
    found = {}
    for match in re.finditer(r'^\t{4}(\w+) = (\w+) \{\n(.*?)^\t{4}\},', text, re.M | re.S):
        found[match.group(1)] = (match.group(2), match.group(3))
    return found


class Timecodes(unittest.TestCase):
    def test_non_drop_timecode_counts_from_midnight(self):
        self.assertEqual(motion_place.timecode_to_frame('01:00:00:00', 30), 108000)
        self.assertEqual(motion_place.timecode_to_frame('01:00:02:15', 30), 108075)
        self.assertEqual(motion_place.timecode_to_frame('00:00:01:00', 25), 25)

    def test_29_97_uses_30_frames_a_second_like_resolve(self):
        self.assertEqual(motion_place.timecode_to_frame('01:00:00:00', 29.97), 108000)

    def test_drop_frame_skips_two_frames_a_minute_except_every_tenth(self):
        self.assertEqual(motion_place.timecode_to_frame('00:01:00;02', 29.97), 1800)
        self.assertEqual(motion_place.timecode_to_frame('00:10:00;00', 29.97), 17982)

    def test_frame_back_to_timecode(self):
        self.assertEqual(motion_place.frame_to_timecode(108075, 30), '01:00:02:15')

    def test_a_frame_past_the_seconds_rate_is_refused(self):
        with self.assertRaises(motion_place.PlaceError):
            motion_place.timecode_to_frame('01:00:00:30', 30)

    def test_a_timecode_that_is_not_four_numbers_is_refused(self):
        with self.assertRaises(motion_place.PlaceError):
            motion_place.timecode_to_frame('1:00:00', 30)


class Install(unittest.TestCase):
    def test_copies_the_template_and_refreshes_a_changed_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            target = motion_place.install_template('verdict-card', Path(folder))
            self.assertEqual(target.name, 'Verdict Card.setting')
            self.assertEqual(target.read_text(encoding='utf-8'), TEMPLATE)
            target.write_text('old', encoding='utf-8')
            motion_place.install_template('verdict-card', Path(folder))
            self.assertEqual(target.read_text(encoding='utf-8'), TEMPLATE)


class Template(unittest.TestCase):
    def test_every_field_the_script_fills_is_a_published_control_on_a_real_tool(self):
        tools = tool_blocks(TEMPLATE)
        for control in motion_place.FIELDS['verdict-card']:
            match = re.search(rf'\b{control} = InstanceInput \{{\s*SourceOp = "(\w+)",\s*Source = "StyledText"',
                              TEMPLATE)
            self.assertIsNotNone(match, f'{control} is not published in the template')
            self.assertEqual(tools.get(match.group(1), ('',))[0], 'TextPlus', control)

    def test_the_macro_name_matches_the_script(self):
        self.assertIn(f'\t\t{motion_place.MACROS["verdict-card"]} = MacroOperator {{', TEMPLATE)

    def test_every_image_tool_follows_the_timeline_resolution(self):
        # Without this, a tool loaded from a .setting falls back to 320 x 240 and the card renders soft and
        # out of place (found on the first try in Resolve 21.2).
        for name, (kind, block) in tool_blocks(TEMPLATE).items():
            if kind in ('Background', 'TextPlus', 'RectangleMask'):
                self.assertIn('UseFrameFormatSettings = Input { Value = 1, }', block, name)

    def test_the_entrance_is_over_within_400_ms_even_at_24_fps(self):
        keys = [int(frame) for frame in re.findall(r'\[(\d+)\] = \{', TEMPLATE)]
        self.assertTrue(keys)
        self.assertLessEqual(max(keys) / 24, 0.4)

    def test_the_entrance_never_overshoots(self):
        # No bounce: every handle stays at or below the end value of its curve.
        for spline in re.findall(r'KeyFrames = \{(.*?)\n\t{5}\},', TEMPLATE, re.S):
            end = float(re.findall(r'\[\d+\] = \{ ([\d.]+)', spline)[-1])
            for handle in re.findall(r'[LR]H = \{ [\d.]+, ([\d.]+) \}', spline):
                self.assertLessEqual(float(handle), end)


if __name__ == '__main__':
    unittest.main()
