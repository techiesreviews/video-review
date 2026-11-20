"""tools/motion_place.py and motion/templates/verdict-card.setting, without Resolve.

The Resolve part (placing the card) was proved by hand on a throwaway project; see docs/motion-engine.md.
These checks keep the template and the script in step, and keep two faults found on the way from coming back.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_frame_back_to_drop_frame_timecode(self):
        self.assertEqual(motion_place.frame_to_timecode(1800, 29.97, drop=True), '00:01:00;02')
        self.assertEqual(motion_place.frame_to_timecode(17982, 29.97, drop=True), '00:10:00;00')
        for fps in (29.97, 59.94):
            for frame in range(0, 250000, 13):
                label = motion_place.frame_to_timecode(frame, fps, drop=True)
                self.assertEqual(motion_place.timecode_to_frame(label, fps), frame, (fps, label))

    def test_a_frame_past_the_seconds_rate_is_refused(self):
        with self.assertRaises(motion_place.PlaceError):
            motion_place.timecode_to_frame('01:00:00:30', 30)

    def test_drop_frame_is_refused_where_it_does_not_exist(self):
        for timecode, fps in (('01:00:00;00', 25), ('01:00:00;00', 24), ('01:00:00;00', 30),
                              ('01:00:00;00', 60), ('00:01:00;00', 29.97),
                              ('00:01:00;03', 59.94)):
            with self.subTest(timecode=timecode, fps=fps), self.assertRaises(motion_place.PlaceError):
                motion_place.timecode_to_frame(timecode, fps)
        self.assertEqual(motion_place.timecode_to_frame('00:10:00;00', 29.97), 17982)

    def test_minutes_or_seconds_past_59_are_refused(self):
        for timecode in ('00:60:00:00', '00:00:60:00', '00:00:60;00'):
            with self.subTest(timecode=timecode), self.assertRaises(motion_place.PlaceError):
                motion_place.timecode_to_frame(timecode, 29.97)

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
        # No bounce: every handle stays at or below the end value of its curve. Both curves rise today; a falling
        # curve (an exit fade, say) would need the opposite comparison.
        splines = re.findall(r'KeyFrames = \{(.*?)\n\t{5}\},', TEMPLATE, re.S)
        self.assertTrue(splines)
        for spline in splines:
            end = float(re.findall(r'\[\d+\] = \{ ([\d.]+)', spline)[-1])
            for handle in re.findall(r'[LR]H = \{ [\d.]+, ([\d.]+) \}', spline):
                self.assertLessEqual(float(handle), end)


class FakeClip:
    def __init__(self, name, track, start, duration, uid):
        self.name, self.track, self.start, self.duration, self.uid = name, track, start, duration, uid

    def GetName(self):
        return self.name

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.start + self.duration

    def GetDuration(self):
        return self.duration

    def GetUniqueId(self):
        return self.uid


class FakeMacro:
    def __init__(self):
        self.inputs = {}

    def SetInput(self, control, value):
        self.inputs[control] = value

    def GetInput(self, control):
        return self.inputs.get(control)


class FakeTitle(FakeClip):
    """A Fusion title as Resolve inserts it: 150 frames (5 s at 30 fps) at the playhead, on the top track."""

    def __init__(self, track, start):
        super().__init__('Verdict Card', track, start, 150, 'card')
        self.right = 0
        self.macro = FakeMacro()

    def GetRightOffset(self):
        return self.right

    def SetRightOffset(self, offset):
        self.duration -= offset - self.right
        self.right = offset
        return True

    def SetPosition(self, position):
        self.track, self.start = position['trackIndex'], position['startFrame']
        return True

    def GetTrackTypeAndIndex(self):
        return ['video', self.track]

    def GetFusionCompByIndex(self, index):
        return self

    def FindTool(self, name):
        return self.macro


class FakeTimeline:
    """One 10 s clip on V1 of a 30 fps timeline that starts at frame 0. push_on_insert makes the title insert
    push that clip along, as Resolve's insert edit does when the playhead is not past every clip."""

    def __init__(self, push_on_insert=0, frames_work=True, sheet_works=True):
        self.clips = [FakeClip('interview.mp4', 1, 0, 300, 'v1')]
        self.settings = {'timelineFrameRate': '30', 'timelineDropFrameTimecode': '0'}
        self.tracks = 1
        self.playhead = None
        self.push_on_insert, self.frames_work, self.sheet_works = push_on_insert, frames_work, sheet_works
        self.deleted = []

    def GetName(self):
        return 'Edit'

    def GetSetting(self, key):
        return self.settings[key]

    def GetStartFrame(self):
        return 0

    def GetStartTimecode(self):
        return '00:00:00:00'

    def GetEndFrame(self):
        return max(clip.GetEnd() for clip in self.clips)

    def SetCurrentTimecode(self, timecode):
        # A drop-frame timeline reads every label as drop-frame, whatever separator it has.
        if self.settings['timelineDropFrameTimecode'] == '1':
            timecode = timecode[:8] + ';' + timecode[9:]
        self.playhead = motion_place.timecode_to_frame(timecode, float(self.settings['timelineFrameRate']))
        return True

    def GetIsTrackEnabled(self, kind, track):
        return True

    def GetTrackCount(self, kind):
        return self.tracks if kind == 'video' else 0

    def AddTrack(self, kind):
        self.tracks += 1
        return True

    def DeleteTrack(self, kind, track):
        if kind != 'video' or track != self.tracks or self.GetItemListInTrack(kind, track):
            return False
        self.tracks -= 1
        return True

    def GetItemListInTrack(self, kind, track):
        return [clip for clip in self.clips if kind == 'video' and clip.track == track]

    def InsertFusionTitleIntoTimeline(self, name):
        for clip in self.clips:
            clip.start += self.push_on_insert
        title = FakeTitle(self.tracks, self.playhead)
        self.clips.append(title)
        return title

    def DeleteClips(self, items, ripple):
        self.deleted.append((tuple(item.uid for item in items), ripple))
        self.clips = [clip for clip in self.clips if clip not in items]
        return True

    def ExportFrame(self, frame, options):
        return {'success': self.frames_work, 'error': None if self.frames_work else 'render failed'}

    def ExportThumbnails(self, cells, options):
        return {'success': self.sheet_works, 'error': None if self.sheet_works else 'sheet render failed'}


class FakeResolve:
    def __init__(self, timeline):
        self.timeline = timeline

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self

    def GetName(self):
        return 'ZZ Motion proof - throwaway'

    def GetCurrentTimeline(self):
        return self.timeline

    def GetCurrentPage(self):
        return 'edit'


def run_main(argv, timeline):
    """main(argv) against a fake Resolve: (exit code, stdout, stderr, connect mock)."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(motion_place.resolve_build, 'connect', return_value=FakeResolve(timeline)) as connect, \
            mock.patch.object(motion_place.resolve_build, 'check_studio'), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = motion_place.main(argv)
    return code, out.getvalue(), err.getvalue(), connect


class Place(unittest.TestCase):
    VALUES = {'tool': 'Notion Calendar', 'score': '4', 'reason': 'Fast.', 'label': None}

    def test_places_the_card_on_a_new_track_without_moving_the_clip(self):
        timeline = FakeTimeline()
        report = motion_place.place(FakeResolve(timeline), 'verdict-card', self.VALUES, '00:00:01:00', 3)
        self.assertEqual((report['track'], report['frames']), ('V2', 90))
        self.assertEqual(timeline.clips[0].GetStart(), 0)
        self.assertEqual(timeline.deleted, [])

    def test_a_free_v2_is_reused_even_where_the_card_was_first_inserted(self):
        # The card goes in at the timeline's end on the top track; that spot must not make V2 look busy.
        timeline = FakeTimeline()
        timeline.tracks = 2
        report = motion_place.place(FakeResolve(timeline), 'verdict-card', self.VALUES, '00:00:10:00', 3)
        self.assertEqual((report['track'], timeline.tracks), ('V2', 2))

    def test_a_turned_off_v2_is_never_used_for_the_card(self):
        timeline = FakeTimeline()
        timeline.tracks = 2
        timeline.GetIsTrackEnabled = lambda kind, track: track != 2
        report = motion_place.place(FakeResolve(timeline), 'verdict-card', self.VALUES, '00:00:01:00', 3)
        self.assertEqual((report['track'], timeline.tracks), ('V3', 3))

    def test_a_drop_frame_start_is_reported_as_it_was_asked(self):
        timeline = FakeTimeline()
        timeline.settings = {'timelineFrameRate': '29.97', 'timelineDropFrameTimecode': '1'}
        report = motion_place.place(FakeResolve(timeline), 'verdict-card', self.VALUES, '00:01:00;02', 3)
        self.assertEqual(report['start'], '00:01:00;02')

    def test_on_a_drop_frame_timeline_the_card_goes_in_at_the_real_end(self):
        timeline = FakeTimeline()
        timeline.settings = {'timelineFrameRate': '29.97', 'timelineDropFrameTimecode': '1'}
        timeline.clips[0].duration = 20000  # 11 minutes, so non-drop and drop-frame labels differ by 20 frames
        report = motion_place.place(FakeResolve(timeline), 'verdict-card', self.VALUES, '00:01:00;02', 3)
        self.assertEqual(timeline.playhead, 20000)
        self.assertEqual((report['track'], timeline.clips[-1].GetStart()), ('V2', 1800))

    def test_on_a_non_drop_29_97_timeline_a_start_written_with_a_semicolon_is_refused(self):
        timeline = FakeTimeline()
        timeline.settings['timelineFrameRate'] = '29.97'
        with self.assertRaises(motion_place.PlaceError) as caught:
            motion_place.place(FakeResolve(timeline), 'verdict-card', self.VALUES, '00:10:00;00', 3)
        self.assertIn('00:10:00:00', str(caught.exception))
        self.assertEqual([clip.uid for clip in timeline.clips], ['v1'])

    def test_a_track_added_for_a_card_that_then_fails_is_removed_too(self):
        timeline = FakeTimeline()
        with mock.patch.object(FakeTitle, 'SetPosition', return_value=False), \
                self.assertRaises(motion_place.PlaceError):
            motion_place.place(FakeResolve(timeline), 'verdict-card', self.VALUES, '00:00:01:00', 3)
        self.assertEqual(([clip.uid for clip in timeline.clips], timeline.tracks), (['v1'], 1))

    def test_on_a_drop_frame_timeline_a_start_written_with_colons_is_refused(self):
        timeline = FakeTimeline()
        timeline.settings = {'timelineFrameRate': '29.97', 'timelineDropFrameTimecode': '1'}
        with self.assertRaises(motion_place.PlaceError) as caught:
            motion_place.place(FakeResolve(timeline), 'verdict-card', self.VALUES, '00:01:00:02', 3)
        self.assertIn(';', str(caught.exception))
        self.assertEqual([clip.uid for clip in timeline.clips], ['v1'])

    def test_a_card_that_pushed_clips_along_says_they_are_still_moved(self):
        timeline = FakeTimeline(push_on_insert=10)
        with self.assertRaises(motion_place.PlaceError) as caught:
            motion_place.place(FakeResolve(timeline), 'verdict-card', self.VALUES, '00:00:01:00', 3)
        self.assertEqual(timeline.deleted, [(('card',), False)])
        self.assertIn('still moved', str(caught.exception))
        self.assertIn('undo in Resolve', str(caught.exception))

    def test_a_card_resolve_will_not_delete_is_never_called_removed(self):
        timeline = FakeTimeline(push_on_insert=10)
        timeline.DeleteClips = lambda items, ripple: False
        with self.assertRaises(motion_place.PlaceError) as caught:
            motion_place.place(FakeResolve(timeline), 'verdict-card', self.VALUES, '00:00:01:00', 3)
        self.assertIn('could not be removed', str(caught.exception))
        self.assertNotIn('was removed', str(caught.exception))

    def test_a_failed_step_with_nothing_moved_removes_the_card_and_keeps_its_reason(self):
        timeline = FakeTimeline()
        with mock.patch.object(FakeMacro, 'GetInput', return_value='something else'), \
                self.assertRaises(motion_place.PlaceError) as caught:
            motion_place.place(FakeResolve(timeline), 'verdict-card', self.VALUES, '00:00:01:00', 3)
        self.assertEqual(timeline.deleted, [(('card',), False)])
        self.assertIn('Resolve kept', str(caught.exception))
        self.assertNotIn('still moved', str(caught.exception))


class Main(unittest.TestCase):
    PLACE = ['--tool', 'Notion Calendar', '--score', '4', '--reason', 'Fast.', '--at', '00:00:01:00']

    def test_a_template_that_cannot_be_installed_says_not_placed_without_a_traceback(self):
        with mock.patch.object(motion_place, 'install_template', side_effect=OSError('read-only')):
            code, out, err, connect = run_main(self.PLACE + ['--install'], FakeTimeline())
        self.assertEqual(code, 1)
        self.assertIn('Not placed', err)
        self.assertIn('read-only', err)
        connect.assert_not_called()

    def test_frames_that_fail_after_placing_still_say_the_card_is_placed(self):
        with tempfile.TemporaryDirectory() as folder:
            code, out, err, _ = run_main(self.PLACE + ['--frames', folder], FakeTimeline(frames_work=False))
        self.assertNotIn('Not placed', err)
        self.assertIn('Placed, but not all the frames could be saved', err)
        report = json.loads(out)
        self.assertEqual(report['track'], 'V2')
        self.assertIn('render failed', report['frames_error'])
        self.assertEqual(code, 0)

    def test_a_sheet_that_fails_after_the_frames_says_the_pictures_are_not_all_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            code, out, err, _ = run_main(self.PLACE + ['--frames', folder], FakeTimeline(sheet_works=False))
        self.assertIn('Placed, but not all the frames could be saved', err)
        report = json.loads(out)
        self.assertIn('sheet render failed', report['frames_error'])
        self.assertIn('the single frames are saved', report['frames_error'])
        self.assertEqual(code, 0)

    def test_a_windows_machine_without_appdata_gets_a_plain_message(self):
        with self.assertRaises(motion_place.PlaceError) as caught:
            motion_place.resolve_titles_folder('win32', {})
        self.assertIn('APPDATA is not set', str(caught.exception))
        with mock.patch.object(motion_place, 'resolve_titles_folder',
                               side_effect=motion_place.PlaceError('APPDATA is not set')):
            code, out, err, connect = run_main(self.PLACE + ['--install'], FakeTimeline())
        self.assertEqual(code, 1)
        self.assertIn('Not placed: APPDATA is not set', err)
        connect.assert_not_called()

    def test_a_bad_frames_option_is_refused_before_anything_is_placed(self):
        # parser.error exits before main connects to Resolve; a late ValueError would mean the card was placed.
        for option in (['--frames-crop', '0,0.6,x'], ['--frames-crop', '0,0.6,0.6'], ['--frames-crop', '0,0,2,1'],
                       ['--frame-offsets', '0,two']):
            with self.subTest(option=option), self.assertRaises(SystemExit):
                run_main(self.PLACE + ['--frames', 'out'] + option, FakeTimeline())

    def test_a_score_above_5_is_refused(self):
        with self.assertRaises(SystemExit):
            run_main(['--tool', 'X', '--score', '7', '--reason', 'Y', '--at', '00:00:01:00'], FakeTimeline())

    def test_text_too_long_for_the_card_is_refused_before_resolve_is_touched(self):
        # The text does not shrink to fit, so a card that would overflow is never placed and called "Placed".
        for option in (['--reason', 'x' * 51], ['--tool', 'Microsoft Power Automate Desktop']):
            with self.subTest(option=option), \
                    mock.patch.object(motion_place.resolve_build, 'connect') as connect, \
                    contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
                motion_place.main(self.PLACE + option)
            connect.assert_not_called()
            self.assertIn('characters', err.getvalue())
        code, out, err, _ = run_main(['--tool', 'x' * 26, '--score', '4.5', '--reason', 'y' * 50,
                                      '--at', '00:00:01:00'], FakeTimeline())
        self.assertEqual(code, 0)

    def test_a_length_that_is_not_above_zero_is_refused_before_resolve_is_touched(self):
        for seconds in ('0', '-3', 'nan', 'inf'):
            with self.subTest(seconds=seconds), \
                    mock.patch.object(motion_place.resolve_build, 'connect') as connect, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                motion_place.main(self.PLACE + ['--seconds', seconds])
            connect.assert_not_called()


class FakeProjectManager:
    """A Resolve whose open project is `current`, with `timelines` timelines; records project switches."""
    def __init__(self, current, timelines):
        self.current, self.timelines, self.switches = current, timelines, []

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self

    def GetName(self):
        return self.current

    def GetTimelineCount(self):
        return self.timelines

    def GetProjectListInCurrentFolder(self):
        return []

    def LoadProject(self, name):
        self.switches.append(('load', name))

    def CreateProject(self, name):
        self.switches.append(('create', name))


class OpenTestProject(unittest.TestCase):
    def test_an_untitled_project_with_a_timeline_is_never_switched_away_from(self):
        resolve = FakeProjectManager('Untitled Project', timelines=1)
        with self.assertRaises(motion_place.PlaceError) as caught:
            motion_place.open_test_project(resolve, 'ZZ Motion proof - throwaway')
        self.assertIn('Untitled Project', str(caught.exception))
        self.assertEqual(resolve.switches, [])

    def test_an_empty_untitled_project_may_be_switched_away_from(self):
        resolve = FakeProjectManager('Untitled Project', timelines=0)
        with self.assertRaises(motion_place.PlaceError):  # the fake creates nothing, so the next check stops it
            motion_place.open_test_project(resolve, 'ZZ Motion proof - throwaway')
        self.assertEqual(resolve.switches, [('create', 'ZZ Motion proof - throwaway')])


if __name__ == '__main__':
    unittest.main()
