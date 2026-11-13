"""Checks for the take intake (scripts/video/take_intake.py).

The checks are tested on small invented numbers and on canned ffmpeg text, and the intake itself with the media
tools replaced by stand-ins, so nothing here needs real footage. A few tests make tiny sound clips with ffmpeg in a
temporary folder; they are skipped when ffmpeg is not installed.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/video'))
import scene_store  # noqa: E402
import take_intake  # noqa: E402

CUE = (ROOT / 'tests/fixtures/scene-cue-file.md').read_text(encoding='utf-8')
HAVE_FFMPEG = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))


class EndCheck(unittest.TestCase):
    def test_a_take_that_ends_in_silence_passes(self):
        self.assertFalse(take_intake.ends_early(60.0, [(59.2, 60.0)]))

    def test_a_take_that_stops_while_someone_is_talking_fails(self):
        self.assertTrue(take_intake.ends_early(60.0, [(10.0, 11.0)]))

    def test_a_take_with_no_silence_at_all_fails(self):
        self.assertTrue(take_intake.ends_early(60.0, []))

    def test_a_silence_that_is_too_short_does_not_count_as_a_clean_end(self):
        self.assertTrue(take_intake.ends_early(60.0, [(59.9, 60.0)]))

    def test_a_silence_that_stops_a_little_before_the_end_still_counts(self):
        # Sound and picture rarely end on the same instant.
        self.assertFalse(take_intake.ends_early(60.0, [(59.0, 59.9)]))

    def test_a_silence_far_from_the_end_does_not_count(self):
        self.assertTrue(take_intake.ends_early(60.0, [(50.0, 53.0)]))


class PauseCheck(unittest.TestCase):
    def test_a_long_pause_in_the_middle_fails(self):
        self.assertTrue(take_intake.long_pause(60.0, [(20.0, 23.0)]))

    def test_a_short_pause_passes(self):
        self.assertFalse(take_intake.long_pause(60.0, [(20.0, 22.0)]))

    def test_the_limit_itself_passes_and_just_over_fails(self):
        limit = take_intake.LONG_PAUSE_SECONDS
        self.assertFalse(take_intake.long_pause(60.0, [(20.0, 20.0 + limit)]))
        self.assertTrue(take_intake.long_pause(60.0, [(20.0, 20.0 + limit + 0.1)]))

    def test_a_long_wait_at_the_start_is_not_a_pause_in_the_middle(self):
        self.assertFalse(take_intake.long_pause(60.0, [(0.0, 5.0)]))

    def test_a_long_silence_at_the_end_is_not_a_pause_in_the_middle(self):
        self.assertFalse(take_intake.long_pause(60.0, [(52.0, 60.0)]))

    def test_a_pause_that_runs_into_the_last_seconds_is_not_counted(self):
        self.assertFalse(take_intake.long_pause(60.0, [(54.0, 58.5)]))

    def test_one_long_pause_among_short_ones_fails(self):
        self.assertTrue(take_intake.long_pause(60.0, [(10.0, 11.0), (30.0, 34.0), (40.0, 41.0)]))


class LengthCheck(unittest.TestCase):
    def test_a_length_close_to_the_others_passes(self):
        self.assertFalse(take_intake.length_off(100.0, [95.0, 110.0, 104.0]))

    def test_a_much_shorter_take_fails(self):
        self.assertTrue(take_intake.length_off(40.0, [100.0, 105.0]))

    def test_a_much_longer_take_fails(self):
        self.assertTrue(take_intake.length_off(180.0, [100.0, 105.0]))

    def test_the_middle_length_of_the_others_is_used_not_the_average(self):
        # One very long take among the others must not drag the middle up: the middle of 100, 102, 400 is 102.
        self.assertFalse(take_intake.length_off(101.0, [100.0, 102.0, 400.0]))

    def test_with_no_other_take_the_check_does_not_run(self):
        self.assertIsNone(take_intake.length_off(100.0, []))


class Favorite(unittest.TestCase):
    @staticmethod
    def scene(*takes):
        """A scene dict; each take is (number, ends_early, length_off, long_pause) with None for a check not run."""
        entry = {'id': '01-intro', 'takes': []}
        for number, early, length, pause in takes:
            sound = {k: v for k, v in (('ends_early', early), ('length_off', length), ('long_pause', pause))
                     if v is not None}
            entry['takes'].append({'number': number, 'file': f'take-{number:02d}.mkv', 'name': f'x-{number}',
                                   'sound': sound})
        return entry

    def test_the_take_that_passes_the_most_checks_wins(self):
        entry = self.scene((1, False, False, False), (2, True, False, False), (3, True, True, False))
        self.assertEqual(take_intake.pick_favorite(entry), 1)

    def test_newest_wins_a_tie(self):
        entry = self.scene((1, False, False, False), (2, False, False, False), (3, True, False, False))
        self.assertEqual(take_intake.pick_favorite(entry), 2)

    def test_a_take_with_more_passes_beats_a_newer_one_with_fewer(self):
        entry = self.scene((1, False, False, False), (2, False, True, True))
        self.assertEqual(take_intake.pick_favorite(entry), 1)

    def test_a_scene_with_one_take_has_no_favorite(self):
        self.assertIsNone(take_intake.pick_favorite(self.scene((1, False, False, False))))

    def test_a_scene_with_no_takes_has_no_favorite(self):
        self.assertIsNone(take_intake.pick_favorite(self.scene()))

    def test_a_check_that_did_not_run_is_not_a_pass(self):
        # Take 2 never got its length compared, so it has two passes against take 1's three.
        entry = self.scene((1, False, False, False), (2, False, None, False))
        self.assertEqual(take_intake.pick_favorite(entry), 1)

    def test_takes_without_any_sound_data_are_left_out(self):
        entry = self.scene((1, False, False, False), (2, None, None, None))
        entry['takes'][1].pop('sound')
        self.assertEqual(take_intake.pick_favorite(entry), 1)


SILENCE_TEXT = """\
[silencedetect @ 0x55d1] silence_start: -0.00233333
[silencedetect @ 0x55d1] silence_end: 1.5 | silence_duration: 1.50233
size=N/A time=00:00:30.00 bitrate=N/A speed= 250x
[silencedetect @ 0x55d1] silence_start: 12.25
[silencedetect @ 0x55d1] silence_end: 15.75 | silence_duration: 3.5
[silencedetect @ 0x55d1] silence_start: 28.9
"""


class ParseSilence(unittest.TestCase):
    def test_pairs_become_intervals_and_an_open_silence_runs_to_the_end(self):
        self.assertEqual(take_intake.parse_silence(SILENCE_TEXT, 30.0), [(0.0, 1.5), (12.25, 15.75), (28.9, 30.0)])

    def test_text_without_silence_gives_nothing(self):
        self.assertEqual(take_intake.parse_silence('size=N/A time=00:00:30.00\n', 30.0), [])


class Finished:
    """What subprocess.run returns."""
    def __init__(self, stdout='', stderr='', returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class MediaTools(unittest.TestCase):
    def test_probe_reads_length_and_whether_there_is_sound(self):
        out = json.dumps({'format': {'duration': '61.250000'}, 'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}]})
        with mock.patch.object(take_intake.subprocess, 'run', return_value=Finished(stdout=out)) as run:
            self.assertEqual(take_intake.probe_media(Path('a.mkv')), (61.25, True))
        self.assertEqual(run.call_args.args[0][0], 'ffprobe')

    def test_probe_sees_a_take_without_sound(self):
        out = json.dumps({'format': {'duration': '5'}, 'streams': [{'codec_type': 'video'}]})
        with mock.patch.object(take_intake.subprocess, 'run', return_value=Finished(stdout=out)):
            self.assertEqual(take_intake.probe_media(Path('a.mkv')), (5.0, False))

    def test_probe_gives_none_for_a_file_it_cannot_read_yet(self):
        with mock.patch.object(take_intake.subprocess, 'run', return_value=Finished(returncode=1)):
            self.assertIsNone(take_intake.probe_media(Path('a.mkv')))
        for out in ('not json', json.dumps({'format': {}, 'streams': []}), json.dumps({'format': {'duration': 'N/A'}})):
            with mock.patch.object(take_intake.subprocess, 'run', return_value=Finished(stdout=out)):
                self.assertIsNone(take_intake.probe_media(Path('a.mkv')))

    def test_silence_check_listens_to_the_first_sound_track_only(self):
        with mock.patch.object(take_intake.subprocess, 'run', return_value=Finished(stderr=SILENCE_TEXT)) as run:
            self.assertEqual(take_intake.detect_silence(Path('a.mkv'), 30.0), [(0.0, 1.5), (12.25, 15.75), (28.9, 30.0)])
        command = run.call_args.args[0]
        self.assertEqual(command[0], 'ffmpeg')
        self.assertIn('-vn', command)
        self.assertEqual(command[command.index('-map') + 1], '0:a:0')
        self.assertIn(f'silencedetect=noise={take_intake.SILENCE_FLOOR_DB}dB:d={take_intake.SILENCE_MIN_SECONDS}', command)

    def test_a_failed_silence_check_is_none(self):
        with mock.patch.object(take_intake.subprocess, 'run', return_value=Finished(returncode=1)):
            self.assertIsNone(take_intake.detect_silence(Path('a.mkv'), 30.0))

    def test_missing_tools_are_said_plainly(self):
        with mock.patch.object(take_intake.subprocess, 'run', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(take_intake.ToolMissing, 'ffprobe'):
                take_intake.probe_media(Path('a.mkv'))
            with self.assertRaisesRegex(take_intake.ToolMissing, 'ffmpeg'):
                take_intake.detect_silence(Path('a.mkv'), 30.0)


class IntakeCase(unittest.TestCase):
    """A video folder with a saved scene list and a stand-in for the media tools."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.raw = Path(self._dir.name)
        scene_store.ensure_scene_list(self.raw, CUE)
        self.scenes = [s['id'] for s in scene_store.load(self.raw)['scenes']]
        self.media = {}      # file name -> (duration, has audio, silences)
        self.calls = []      # what the tools were asked, in order
        probe = mock.patch.object(take_intake, 'probe_media', side_effect=self._probe)
        silence = mock.patch.object(take_intake, 'detect_silence', side_effect=self._silence)
        probe.start()
        silence.start()
        self.addCleanup(probe.stop)
        self.addCleanup(silence.stop)

    def _probe(self, path):
        self.calls.append(('probe', path.name))
        entry = self.media.get(path.name)
        return None if entry is None else (entry[0], entry[1])

    def _silence(self, path, duration):
        self.calls.append(('silence', path.name))
        return self.media[path.name][2]

    def add_file(self, scene, number, duration, silences=((59.0, 60.0),), audio=True, name=None, size=1000):
        folder = scene_store.scene_folder(self.raw, scene)
        folder.mkdir(parents=True, exist_ok=True)
        name = name or scene_store.take_file_name(number)
        (folder / name).write_bytes(b'x' * size)
        self.media[name] = (duration, audio, list(silences))
        return folder / name

    def run_intake(self, **kwargs):
        kwargs.setdefault('settle_seconds', 0)
        kwargs.setdefault('sleep', lambda seconds: None)
        return take_intake.run(self.raw, **kwargs)

    def take(self, scene, number):
        entry = scene_store.find_scene(scene_store.load(self.raw), scene)
        return next((t for t in entry['takes'] if t['number'] == number), None)


class NewTakes(IntakeCase):
    def test_a_new_take_is_registered_with_length_time_and_sound_checks(self):
        scene = self.scenes[0]
        path = self.add_file(scene, 1, 60.0, [(59.0, 60.0)])
        os.utime(path, (1_700_000_000, 1_700_000_000))
        report = self.run_intake()
        take = self.take(scene, 1)
        self.assertEqual(report['added'], [(scene, 1)])
        self.assertEqual(take['file'], 'take-01.mkv')
        self.assertEqual(take['name'], scene_store.take_full_name(scene, 1))
        self.assertEqual(take['duration'], 60.0)
        if os.name != 'nt' and not hasattr(os.stat_result, 'st_birthtime'):
            self.assertEqual(take['recorded_at'], '2023-11-14T22:13:20Z')   # the file's last-written time
        self.assertRegex(take['recorded_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        # A lone take is never compared with another, so its length check did not run.
        self.assertEqual(take['sound'], {'ends_early': False, 'long_pause': False})

    def test_one_take_saved_in_two_formats_is_registered_once(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0)
        self.add_file(scene, 1, 60.0, name='take-01.mp4')
        report = self.run_intake()
        self.assertEqual(report['added'], [(scene, 1)])
        entry = scene_store.find_scene(scene_store.load(self.raw), scene)
        self.assertEqual([t['file'] for t in entry['takes']], ['take-01.mkv'])

    def test_an_upper_case_extension_is_a_take_and_keeps_its_spelling(self):
        self.add_file(self.scenes[0], 1, 60.0, name='take-01.MKV')
        self.run_intake()
        self.assertEqual(self.take(self.scenes[0], 1)['file'], 'take-01.MKV')

    def test_every_scene_folder_is_scanned(self):
        self.add_file(self.scenes[0], 1, 60.0)
        self.add_file(self.scenes[1], 1, 90.0)
        self.add_file(self.scenes[1], 2, 91.0)
        report = self.run_intake()
        self.assertEqual(sorted(report['added']), [(self.scenes[0], 1), (self.scenes[1], 1), (self.scenes[1], 2)])

    def test_the_file_extension_is_kept(self):
        self.add_file(self.scenes[0], 1, 60.0, name='take-01.mp4')
        self.run_intake()
        self.assertEqual(self.take(self.scenes[0], 1)['file'], 'take-01.mp4')

    def test_failed_checks_are_saved_with_the_take(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0, [(20.0, 24.0)])  # long pause in the middle, no silence at the end
        self.run_intake()
        self.assertEqual(self.take(scene, 1)['sound'], {'ends_early': True, 'long_pause': True})

    def test_a_take_without_sound_is_registered_and_its_sound_left_unchecked(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0, audio=False)
        self.run_intake()
        take = self.take(scene, 1)
        self.assertEqual(take['duration'], 60.0)
        self.assertNotIn('sound', take)
        self.assertNotIn(('silence', 'take-01.mkv'), self.calls)

    def test_names_that_are_not_take_files_are_left_alone(self):
        scene = self.scenes[0]
        folder = scene_store.scene_folder(self.raw, scene)
        folder.mkdir(parents=True)
        for name in ('notes.txt', 'take-1.mkv', 'take-01.mkv.part', 'take-02.txt', 'take-03.mkv.tmp'):
            (folder / name).write_bytes(b'x')
        report = self.run_intake()
        self.assertEqual(report['added'], [])
        self.assertEqual(self.calls, [])

    def test_a_folder_that_is_not_in_the_scene_list_is_reported_and_skipped(self):
        self.add_file('09-not-in-the-script', 1, 60.0)
        report = self.run_intake()
        self.assertEqual(report['added'], [])
        self.assertEqual(report['unknown_folders'], ['09-not-in-the-script'])
        self.assertEqual(self.calls, [])

    def test_a_folder_with_no_takes_in_it_is_not_reported(self):
        scene_store.scene_folder(self.raw, 'stray').mkdir(parents=True)
        self.assertEqual(self.run_intake()['unknown_folders'], [])

    def test_without_a_scenes_file_nothing_is_made_up(self):
        (self.raw / scene_store.FILE_NAME).unlink()
        with self.assertRaises(scene_store.SceneFileError):
            self.run_intake()
        self.assertFalse((self.raw / scene_store.FILE_NAME).exists())


class RecordedAt(unittest.TestCase):
    @staticmethod
    def fake_path(**times):
        path = mock.Mock()
        path.stat.return_value = mock.Mock(spec=list(times), **times)
        return path

    def test_birth_time_wins_where_python_has_one(self):
        path = self.fake_path(st_birthtime=1_600_000_000, st_ctime=1_650_000_000, st_mtime=1_700_000_000)
        self.assertEqual(take_intake.recorded_at(path), '2020-09-13T12:26:40Z')

    def test_on_windows_with_older_python_the_creation_time_is_ctime(self):
        path = self.fake_path(st_ctime=1_600_000_000, st_mtime=1_700_000_000)
        with mock.patch.object(take_intake.os, 'name', 'nt'):
            self.assertEqual(take_intake.recorded_at(path), '2020-09-13T12:26:40Z')

    def test_elsewhere_the_last_written_time_is_used(self):
        path = self.fake_path(st_ctime=1_650_000_000, st_mtime=1_700_000_000)
        with mock.patch.object(take_intake.os, 'name', 'posix'):
            self.assertEqual(take_intake.recorded_at(path), '2023-11-14T22:13:20Z')


class ExistingTakes(IntakeCase):
    def test_a_take_that_is_already_registered_is_not_touched_or_even_looked_at(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0)
        self.run_intake()
        # Lex starred it and the numbers were edited by hand; a new run must keep all of it.
        scene_store.update(self.raw, lambda store: (
            scene_store.set_star(store, scene, 1),
            scene_store.find_scene(store, scene)['takes'][0].update(
                duration=61.5, recorded_at='2026-01-01T00:00:00Z', sound={'ends_early': True, 'long_pause': True})))
        before = scene_store.load(self.raw)
        self.calls.clear()
        report = self.run_intake()
        self.assertEqual(report['added'], [])
        self.assertEqual(self.calls, [])
        self.assertEqual(scene_store.load(self.raw)['scenes'], before['scenes'])

    def test_a_new_take_leaves_the_older_takes_and_the_star_alone(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 100.0)
        self.run_intake()
        scene_store.update(self.raw, lambda store: scene_store.set_star(store, scene, 1))
        old = dict(self.take(scene, 1))
        self.add_file(scene, 2, 102.0)
        self.run_intake()
        new = self.take(scene, 1)
        # Only its length check may be refreshed, because it is judged against the other takes.
        self.assertEqual({k: v for k, v in new.items() if k != 'sound'}, {k: v for k, v in old.items() if k != 'sound'})
        self.assertEqual(new['sound']['ends_early'], old['sound']['ends_early'])
        self.assertEqual(new['sound']['long_pause'], old['sound']['long_pause'])
        self.assertEqual(scene_store.find_scene(scene_store.load(self.raw), scene)['star'], 1)

    def test_a_take_registered_without_sound_data_gets_none_added(self):
        scene = self.scenes[0]
        scene_store.update(self.raw, lambda store: scene_store.add_take(store, scene, 1, duration=100.0))
        self.add_file(scene, 2, 100.0)
        self.run_intake()
        self.assertNotIn('sound', self.take(scene, 1))


class GrowingFiles(IntakeCase):
    def test_a_file_that_is_still_growing_is_skipped_and_picked_up_next_run(self):
        scene = self.scenes[0]
        path = self.add_file(scene, 1, 60.0)

        def still_recording(seconds):
            with open(path, 'ab') as handle:
                handle.write(b'more sound')

        report = self.run_intake(sleep=still_recording, settle_seconds=2)
        self.assertEqual(report['added'], [])
        self.assertEqual(report['growing'], [(scene, 1)])
        self.assertIsNone(self.take(scene, 1))
        self.assertEqual(self.calls, [])
        self.run_intake()
        self.assertEqual(self.take(scene, 1)['duration'], 60.0)

    def test_an_empty_file_just_created_is_skipped(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0, size=0)
        report = self.run_intake()
        self.assertEqual(report['growing'], [(scene, 1)])
        self.assertIsNone(self.take(scene, 1))

    def test_a_steady_file_is_taken_and_a_growing_one_beside_it_is_skipped(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0)
        growing = self.add_file(scene, 2, 60.0)
        report = self.run_intake(sleep=lambda s: growing.write_bytes(b'y' * 5000), settle_seconds=2)
        self.assertEqual(report['added'], [(scene, 1)])
        self.assertEqual(report['growing'], [(scene, 2)])

    def test_a_file_that_grows_while_ffmpeg_is_listening_is_skipped_until_it_is_finished(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0)
        growing = self.add_file(scene, 2, 60.0)
        listen = self._silence
        grew = []

        def grow_during_listening(path, duration):
            if path == growing and not grew:
                grew.append(True)
                growing.write_bytes(b'z' * 5000)       # the recording went on after the steady look
            return listen(path, duration)

        with mock.patch.object(take_intake, 'detect_silence', side_effect=grow_during_listening):
            report = self.run_intake()
        self.assertEqual(report['added'], [(scene, 1)])
        self.assertEqual(report['growing'], [(scene, 2)])
        self.assertIsNone(self.take(scene, 2))
        self.media['take-02.mkv'] = (75.0, True, [(74.0, 75.0)])    # the finished recording is longer
        self.run_intake()
        self.assertEqual(self.take(scene, 2)['duration'], 75.0)

    def test_the_wait_is_skipped_when_there_is_nothing_new(self):
        waits = []
        self.run_intake(sleep=waits.append, settle_seconds=2)
        self.assertEqual(waits, [])

    def test_a_file_the_tools_cannot_read_yet_is_skipped_and_picked_up_next_run(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0)
        del self.media['take-01.mkv']          # the probe cannot read it
        report = self.run_intake()
        self.assertEqual(report['unreadable'], [(scene, 1)])
        self.assertIsNone(self.take(scene, 1))
        self.media['take-01.mkv'] = (60.0, True, [(59.0, 60.0)])
        self.run_intake()
        self.assertIsNotNone(self.take(scene, 1))

    def test_a_failed_silence_check_skips_the_take_for_this_run(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0)
        with mock.patch.object(take_intake, 'detect_silence', return_value=None):
            report = self.run_intake()
        self.assertEqual(report['unreadable'], [(scene, 1)])
        self.assertIsNone(self.take(scene, 1))


class LengthAcrossTakes(IntakeCase):
    def test_takes_of_one_scene_are_compared_with_each_other(self):
        scene = self.scenes[0]
        for number, length in ((1, 100.0), (2, 104.0), (3, 98.0), (4, 40.0)):
            self.add_file(scene, number, length)
        self.run_intake()
        self.assertEqual([self.take(scene, n)['sound']['length_off'] for n in (1, 2, 3, 4)],
                         [False, False, False, True])

    def test_other_scenes_are_not_part_of_the_comparison(self):
        self.add_file(self.scenes[0], 1, 100.0)
        self.add_file(self.scenes[1], 1, 20.0)
        self.run_intake()
        self.assertNotIn('length_off', self.take(self.scenes[0], 1)['sound'])
        self.assertNotIn('length_off', self.take(self.scenes[1], 1)['sound'])

    def test_a_later_take_refreshes_the_length_check_of_the_earlier_ones(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 100.0)
        self.run_intake()
        self.assertNotIn('length_off', self.take(scene, 1)['sound'])
        self.add_file(scene, 2, 40.0)
        self.run_intake()
        self.assertTrue(self.take(scene, 1)['sound']['length_off'])
        self.assertTrue(self.take(scene, 2)['sound']['length_off'])


class Favorites(IntakeCase):
    def favorite(self, scene):
        return scene_store.find_scene(scene_store.load(self.raw), scene)['favorite']

    def star(self, scene):
        return scene_store.find_scene(scene_store.load(self.raw), scene)['star']

    def test_a_scene_with_one_take_has_no_favorite(self):
        self.add_file(self.scenes[0], 1, 60.0)
        self.run_intake()
        self.assertIsNone(self.favorite(self.scenes[0]))

    def test_the_take_with_the_most_passed_checks_is_the_favorite_and_never_the_star(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 100.0, [(99.0, 100.0)])   # passes all three
        self.add_file(scene, 2, 100.0, [])                # ends early
        self.add_file(scene, 3, 100.0, [(40.0, 45.0)])    # ends early and long pause
        self.run_intake()
        self.assertEqual(self.favorite(scene), 1)
        self.assertIsNone(self.star(scene))

    def test_newest_wins_a_tie(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 100.0, [(99.0, 100.0)])
        self.add_file(scene, 2, 100.0, [(99.0, 100.0)])
        self.run_intake()
        self.assertEqual(self.favorite(scene), 2)

    def test_the_favorite_follows_new_takes_and_lexs_star_stays(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 100.0, [(99.0, 100.0)])
        self.add_file(scene, 2, 100.0, [])
        self.run_intake()
        scene_store.update(self.raw, lambda store: scene_store.set_star(store, scene, 2))
        self.assertEqual(self.favorite(scene), 1)
        self.add_file(scene, 3, 100.0, [(98.0, 100.0)])
        self.run_intake()
        self.assertEqual(self.favorite(scene), 3)
        self.assertEqual(self.star(scene), 2)

    def test_the_favorite_never_changes_which_take_is_best(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 100.0, [(99.0, 100.0)])
        self.add_file(scene, 2, 100.0, [(99.0, 100.0)])
        self.run_intake()
        entry = scene_store.find_scene(scene_store.load(self.raw), scene)
        self.assertEqual((entry['favorite'], entry['best']), (2, 2))

    def test_a_scene_that_got_no_new_take_keeps_its_favorite(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 100.0, [(99.0, 100.0)])
        self.add_file(scene, 2, 100.0, [])
        self.run_intake()
        scene_store.update(self.raw, lambda store: scene_store.set_favorite(store, scene, 2))
        self.add_file(self.scenes[1], 1, 50.0)
        self.run_intake()
        self.assertEqual(self.favorite(scene), 2)


class WritesGoThroughUpdate(IntakeCase):
    def test_the_slow_media_work_is_done_before_the_one_locked_write(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0)
        self.add_file(scene, 2, 60.0)
        events = []
        real_update = scene_store.update

        def spy(raw_dir, change):
            events.append(('update', len(self.calls)))
            return real_update(raw_dir, change)

        with mock.patch.object(scene_store, 'update', side_effect=spy), \
                mock.patch.object(scene_store, 'save', wraps=scene_store.save) as save:
            self.run_intake()
        # One locked write, after all four media calls (a probe and a silence check for each take).
        self.assertEqual(events, [('update', 4)])
        self.assertEqual(save.call_count, 1)

    def test_nothing_is_written_when_there_is_nothing_new(self):
        with mock.patch.object(scene_store, 'update') as update:
            self.run_intake()
        update.assert_not_called()

    def test_a_take_another_program_registered_meanwhile_is_not_overwritten(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0)
        real_update = scene_store.update

        def other_program_first(raw_dir, change):
            real_update(raw_dir, lambda store: scene_store.add_take(store, scene, 1, duration=77.0))
            return real_update(raw_dir, change)

        with mock.patch.object(scene_store, 'update', side_effect=other_program_first):
            report = self.run_intake()
        self.assertEqual(self.take(scene, 1)['duration'], 77.0)
        self.assertEqual(report['added'], [])


class CommandLine(IntakeCase):
    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = take_intake.main([str(a) for a in args])
        return code, out.getvalue()

    def test_a_run_says_what_it_added_and_skipped(self):
        scene = self.scenes[0]
        self.add_file(scene, 1, 60.0)
        self.add_file(scene, 2, 60.0, [(20.0, 24.0)])
        with mock.patch.object(take_intake, 'SETTLE_SECONDS', 0):
            code, text = self.run_main(self.raw)
        self.assertEqual(code, 0)
        self.assertIn('take 1', text)
        self.assertIn('take 2', text)
        self.assertIn('long pause', text)
        self.assertIn('2 new', text)

    def test_a_lone_take_does_not_claim_its_length_was_checked(self):
        self.add_file(self.scenes[0], 1, 60.0)
        with mock.patch.object(take_intake, 'SETTLE_SECONDS', 0):
            code, text = self.run_main(self.raw)
        self.assertIn('length not compared yet', text)
        self.assertNotIn('all checks passed', text)

    def test_nothing_new_is_a_normal_result(self):
        code, text = self.run_main(self.raw)
        self.assertEqual(code, 0)
        self.assertIn('0 new', text)

    def test_a_folder_without_a_scenes_file_is_an_error_that_says_so(self):
        (self.raw / scene_store.FILE_NAME).unlink()
        code, text = self.run_main(self.raw)
        self.assertEqual(code, 2)
        self.assertIn(scene_store.FILE_NAME, text)

    def test_a_missing_folder_is_an_error(self):
        code, text = self.run_main(self.raw / 'nope')
        self.assertEqual(code, 2)
        self.assertIn('folder', text)

    def test_missing_ffmpeg_is_an_error_that_names_it(self):
        self.add_file(self.scenes[0], 1, 60.0)
        with mock.patch.object(take_intake, 'SETTLE_SECONDS', 0), \
                mock.patch.object(take_intake, 'probe_media', side_effect=take_intake.ToolMissing('ffprobe is not installed')):
            code, text = self.run_main(self.raw)
        self.assertEqual(code, 2)
        self.assertIn('ffprobe', text)


def make_clip(path, parts):
    """A tiny sound-only file: parts is a list of ('tone' | 'quiet', seconds)."""
    inputs, labels = [], []
    for index, (kind, seconds) in enumerate(parts):
        source = 'sine=frequency=440:sample_rate=44100' if kind == 'tone' else 'anullsrc=r=44100:cl=mono'
        inputs += ['-f', 'lavfi', '-t', str(seconds), '-i', source]
        labels.append(f'[{index}:a]')
    graph = f'{"".join(labels)}concat=n={len(parts)}:v=0:a=1[out]'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *inputs, '-filter_complex', graph, '-map', '[out]',
                    '-ac', '1', str(path)], check=True)


@unittest.skipUnless(HAVE_FFMPEG, 'ffmpeg is not installed')
class RealClips(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)

    def analyse(self, parts):
        path = self.dir / 'take-01.mkv'
        make_clip(path, parts)
        duration, has_audio = take_intake.probe_media(path)
        self.assertTrue(has_audio)
        return duration, take_intake.detect_silence(path, duration)

    def test_a_clip_that_ends_in_quiet_with_no_long_pause_is_clean(self):
        duration, silences = self.analyse([('tone', 5), ('quiet', 1), ('tone', 5), ('quiet', 1)])
        self.assertAlmostEqual(duration, 12.0, delta=0.3)
        self.assertFalse(take_intake.ends_early(duration, silences))
        self.assertFalse(take_intake.long_pause(duration, silences))

    def test_a_clip_that_stops_while_the_tone_plays_ends_early(self):
        duration, silences = self.analyse([('tone', 5), ('quiet', 1), ('tone', 5)])
        self.assertTrue(take_intake.ends_early(duration, silences))

    def test_a_long_quiet_stretch_in_the_middle_is_a_long_pause(self):
        duration, silences = self.analyse([('tone', 5), ('quiet', 4), ('tone', 5), ('quiet', 1)])
        self.assertTrue(take_intake.long_pause(duration, silences))
        self.assertFalse(take_intake.ends_early(duration, silences))

    def test_a_clip_without_sound_is_seen_as_having_none(self):
        path = self.dir / 'take-01.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-t', '1', '-i', 'color=c=black:s=16x16',
                        str(path)], check=True)
        self.assertEqual(take_intake.probe_media(path)[1], False)


if __name__ == '__main__':
    unittest.main()
