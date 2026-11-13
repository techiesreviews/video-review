"""Checks for the rough cut list (scripts/video/rough_cut.py). Small invented scene files and cue text only; nothing
here needs a media tool."""
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts/video'))
import overview_page  # noqa: E402
import rough_cut  # noqa: E402
import scene_store  # noqa: E402

CUE = (ROOT / 'tests/fixtures/scene-cue-file.md').read_text(encoding='utf-8')
CLEAN = {'ends_early': False, 'long_pause': False, 'length_off': False}
SHORT = {'ends_early': True, 'long_pause': False, 'length_off': False}
COSTS = '02-what-it-really-costs'
ROOM = '04-room-1-what-changed'
INTRO = '01-intro'


class RoughCutCase(unittest.TestCase):
    """A video folder whose scene file holds: scene 2 with two clean takes, scene 4 with one take, the rest empty."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.raw = Path(self._dir.name)
        store = scene_store.new_store(scene_store.parse_cue_file(CUE))
        scene_store.add_take(store, COSTS, 1, duration=80.0, sound=CLEAN)
        scene_store.add_take(store, COSTS, 2, duration=118.0, sound=CLEAN)
        scene_store.add_take(store, ROOM, 1, duration=65.0, sound=CLEAN)
        scene_store.save(self.raw, store)

    def change(self, action):
        return scene_store.update(self.raw, action)

    def rebuild(self, **options):
        return rough_cut.rebuild_rough_cut(self.raw, **options)

    def saved(self):
        return json.loads((self.raw / rough_cut.FILE_NAME).read_text(encoding='utf-8'))

    def entry(self, scene, data=None):
        return next(item for item in (data or self.saved())['scenes'] if item['scene'] == scene)


class TheFile(RoughCutCase):
    def test_it_is_written_next_to_the_scene_file(self):
        self.rebuild()
        self.assertEqual(rough_cut.FILE_NAME, 'rough-cut.json')
        self.assertTrue((self.raw / 'rough-cut.json').is_file())
        self.assertTrue((self.raw / scene_store.FILE_NAME).is_file())

    def test_it_lists_the_best_take_of_every_scene_in_script_order(self):
        self.rebuild()
        scenes = self.saved()['scenes']
        self.assertEqual([item['scene'] for item in scenes],
                         [scene['id'] for scene in scene_store.load(self.raw)['scenes']])
        self.assertEqual([item['number'] for item in scenes], [1, 2, 3, 4, 5])
        costs = self.entry(COSTS)
        self.assertEqual((costs['title'], costs['take'], costs['duration']), ('WHAT IT REALLY COSTS', 2, 118.0))
        self.assertEqual(costs['file'], f'scenes/{COSTS}/take-02.mkv')

    def test_the_file_path_is_relative_to_the_video_folder_and_uses_the_takes_own_extension(self):
        def mp4(store):
            scene_store.add_take(store, ROOM, 2, extension='.mp4', duration=70.0, sound=CLEAN)
        self.change(mp4)
        self.rebuild()
        self.assertEqual(self.entry(ROOM)['file'], f'scenes/{ROOM}/take-02.mp4')

    def test_the_returned_list_is_the_one_saved(self):
        self.assertEqual(self.rebuild(), self.saved())

    def test_it_adds_up_the_lengths(self):
        self.rebuild()
        self.assertEqual(self.saved()['total_seconds'], 183.0)
        self.assertTrue(self.saved()['lengths_known'])

    def test_a_best_take_without_a_length_makes_the_total_incomplete(self):
        def no_length(store):
            scene_store.add_take(store, INTRO, 1, sound=CLEAN)
        self.change(no_length)
        self.rebuild()
        data = self.saved()
        self.assertIsNone(self.entry(INTRO, data)['duration'])
        self.assertEqual(data['total_seconds'], 183.0)
        self.assertFalse(data['lengths_known'])

    def test_it_changes_nothing_in_the_scene_file(self):
        before = (self.raw / scene_store.FILE_NAME).read_bytes()
        self.rebuild()
        self.assertEqual((self.raw / scene_store.FILE_NAME).read_bytes(), before)


class AfterEachChange(RoughCutCase):
    def test_a_star_change_moves_the_list_to_the_starred_take(self):
        self.rebuild()
        self.change(lambda store: scene_store.set_star(store, COSTS, 1))
        self.rebuild()
        costs = self.entry(COSTS)
        self.assertEqual((costs['take'], costs['duration'], costs['file']), (1, 80.0, f'scenes/{COSTS}/take-01.mkv'))
        self.assertEqual(self.saved()['total_seconds'], 145.0)

    def test_taking_the_star_off_goes_back_to_the_newest_take(self):
        self.change(lambda store: scene_store.set_star(store, COSTS, 1))
        self.rebuild()
        self.change(lambda store: scene_store.set_star(store, COSTS, None))
        self.rebuild()
        self.assertEqual(self.entry(COSTS)['take'], 2)

    def test_a_new_take_comes_into_the_list(self):
        self.rebuild()
        self.change(lambda store: scene_store.add_take(store, ROOM, 2, duration=70.0, sound=CLEAN))
        self.rebuild()
        self.assertEqual((self.entry(ROOM)['take'], self.entry(ROOM)['duration']), (2, 70.0))

    def test_the_first_take_of_an_empty_scene_comes_into_the_list(self):
        self.rebuild()
        self.assertFalse(self.entry(INTRO)['recorded'])
        self.change(lambda store: scene_store.add_take(store, INTRO, 1, duration=30.0, sound=CLEAN))
        self.rebuild()
        self.assertTrue(self.entry(INTRO)['recorded'])
        self.assertEqual(self.entry(INTRO)['take'], 1)

    def test_a_new_take_does_not_replace_a_star(self):
        self.change(lambda store: scene_store.set_star(store, COSTS, 1))
        self.rebuild()
        self.change(lambda store: scene_store.add_take(store, COSTS, 3, duration=90.0, sound=CLEAN))
        self.rebuild()
        self.assertEqual(self.entry(COSTS)['take'], 1)

    def test_every_rebuild_replaces_the_whole_file_and_leaves_no_temporary_file(self):
        (self.raw / rough_cut.FILE_NAME).write_text('old', encoding='utf-8')
        self.rebuild()
        self.rebuild()
        self.assertEqual(self.saved()['version'], rough_cut.VERSION)
        self.assertEqual([p.name for p in self.raw.iterdir() if p.name.endswith('.tmp')], [])


class MissingAndFlagged(RoughCutCase):
    def test_a_scene_with_no_take_is_listed_as_missing_not_dropped(self):
        self.rebuild()
        scenes = self.saved()['scenes']
        self.assertEqual(len(scenes), 5)
        for scene in (INTRO, '03-the-news-moves-fast', '05-whats-next'):
            item = self.entry(scene)
            self.assertEqual((item['recorded'], item['take'], item['file'], item['duration']),
                             (False, None, None, None))
        self.assertTrue(self.entry(COSTS)['recorded'])

    def test_a_missing_scene_keeps_its_place_between_recorded_ones(self):
        self.rebuild()
        self.assertEqual([item['recorded'] for item in self.saved()['scenes']], [False, True, False, True, False])

    def test_missing_scenes_add_no_length(self):
        self.rebuild()
        self.assertEqual(self.saved()['total_seconds'], 183.0)

    def test_a_scene_where_an_older_take_is_in_front_is_flagged(self):
        def worse(store):
            scene_store.add_take(store, COSTS, 3, duration=90.0, sound=SHORT)
        self.change(worse)
        self.rebuild()
        costs = self.entry(COSTS)
        self.assertEqual((costs['take'], costs['flagged']), (2, True))
        self.assertFalse(self.entry(ROOM)['flagged'])

    def test_a_star_clears_the_flag_because_lex_chose(self):
        def worse(store):
            scene_store.add_take(store, COSTS, 3, duration=90.0, sound=SHORT)
            scene_store.set_star(store, COSTS, 3)
        self.change(worse)
        self.rebuild()
        costs = self.entry(COSTS)
        self.assertEqual((costs['take'], costs['flagged']), (3, False))


class ScriptChanged(RoughCutCase):
    def test_the_saved_flag_is_carried_over(self):
        def flag(store):
            scene_store.find_scene(store, ROOM)['script_changed'] = True
        self.change(flag)
        self.rebuild()
        self.assertTrue(self.entry(ROOM)['script_changed'])
        self.assertFalse(self.entry(COSTS)['script_changed'])

    def test_a_heading_renamed_in_the_cue_file_flags_the_scene_even_when_nothing_saved_it(self):
        (self.raw / 'prompter.md').write_text(CUE.replace('WHAT IT REALLY COSTS', 'WHAT IT COSTS'), encoding='utf-8')
        self.rebuild()
        self.assertTrue(self.entry(COSTS)['script_changed'])
        self.assertFalse(self.entry(ROOM)['script_changed'])

    def test_an_unchanged_cue_file_flags_nothing(self):
        (self.raw / 'prompter.md').write_text(CUE, encoding='utf-8')
        self.rebuild()
        self.assertEqual([item['script_changed'] for item in self.saved()['scenes']], [False] * 5)

    def test_a_cue_file_without_numbered_sections_says_nothing(self):
        (self.raw / 'prompter.md').write_text('just words\n', encoding='utf-8')
        self.rebuild()
        self.assertEqual([item['script_changed'] for item in self.saved()['scenes']], [False] * 5)

    def test_without_a_cue_file_the_list_is_still_made(self):
        self.rebuild()
        self.assertEqual(len(self.saved()['scenes']), 5)

    def test_another_cue_file_can_be_named(self):
        other = self.raw / 'other-cues.md'
        other.write_text(CUE.replace('ROOM 1: WHAT CHANGED?', 'ROOM ONE'), encoding='utf-8')
        self.rebuild(cue=other)
        self.assertTrue(self.entry(ROOM)['script_changed'])

    def test_the_cue_file_the_overview_page_remembers_is_used_when_the_data_folder_is_given(self):
        data = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(data, True))
        other = self.raw / 'other-cues.md'
        other.write_text(CUE.replace('ROOM 1: WHAT CHANGED?', 'ROOM ONE'), encoding='utf-8')
        overview_page.register(data, self.raw, cue=other)
        self.rebuild(data_dir=data)
        self.assertTrue(self.entry(ROOM)['script_changed'])

    def test_the_cue_file_is_only_read(self):
        cue = self.raw / 'prompter.md'
        cue.write_text(CUE, encoding='utf-8')
        before = cue.read_bytes()
        self.rebuild()
        self.assertEqual(cue.read_bytes(), before)


class AgreesWithTheOverviewStrip(RoughCutCase):
    """The strip on top of the overview page and rough-cut.json are two views of the same list."""

    def strip(self):
        """[(scene number, take number or None)] from the page's strip, and its header total."""
        page = overview_page.render(scene_store.load(self.raw), 'Sample', cue_text=CUE)
        bar = re.search(r'<div class="cut"[^>]*>(.*?)</div><p class="cutlabel">', page, re.S).group(1)
        parts = re.findall(r'title="Scene (\d+)(?:, take (\d+)|: not recorded yet)"', bar)
        header = re.search(r'rough cut <b>([^<]+)</b>', page).group(1)
        return [(int(scene), int(take) if take else None) for scene, take in parts], header

    def assert_agree(self):
        self.rebuild()
        data = self.saved()
        segments, header = self.strip()
        self.assertEqual(segments, [(item['number'], item['take']) for item in data['scenes']])
        total = overview_page._clock(data['total_seconds']) + ('' if data['lengths_known'] else '+')
        self.assertEqual(header, total)

    def test_at_the_start(self):
        self.assert_agree()

    def test_after_a_star_change(self):
        self.change(lambda store: scene_store.set_star(store, COSTS, 1))
        self.assert_agree()

    def test_after_the_star_comes_off(self):
        self.change(lambda store: scene_store.set_star(store, COSTS, 1))
        self.change(lambda store: scene_store.set_star(store, COSTS, None))
        self.assert_agree()

    def test_after_a_new_take(self):
        self.change(lambda store: scene_store.add_take(store, ROOM, 2, duration=70.0, sound=CLEAN))
        self.assert_agree()

    def test_after_a_take_in_an_empty_scene(self):
        self.change(lambda store: scene_store.add_take(store, INTRO, 1, duration=30.0, sound=CLEAN))
        self.assert_agree()

    def test_with_a_flagged_scene(self):
        self.change(lambda store: scene_store.add_take(store, COSTS, 3, duration=90.0, sound=SHORT))
        self.assert_agree()

    def test_with_a_best_take_that_has_no_length(self):
        self.change(lambda store: scene_store.add_take(store, INTRO, 1, sound=CLEAN))
        self.assert_agree()

    def test_with_no_take_at_all(self):
        store = scene_store.new_store(scene_store.parse_cue_file(CUE))
        scene_store.save(self.raw, store)
        self.assert_agree()
        self.assertEqual([item['recorded'] for item in self.saved()['scenes']], [False] * 5)


class WhenItCannotBeMade(RoughCutCase):
    def test_a_folder_without_a_scene_file_is_an_error_that_says_so_and_makes_no_file(self):
        (self.raw / scene_store.FILE_NAME).unlink()
        with self.assertRaises(scene_store.SceneFileError) as caught:
            self.rebuild()
        self.assertIn(scene_store.FILE_NAME, str(caught.exception))
        self.assertFalse((self.raw / rough_cut.FILE_NAME).exists())

    def test_a_scene_file_that_cannot_be_read_leaves_the_old_list_alone(self):
        self.rebuild()
        before = (self.raw / rough_cut.FILE_NAME).read_bytes()
        (self.raw / scene_store.FILE_NAME).write_text('{nope', encoding='utf-8')
        with self.assertRaises(scene_store.SceneFileError):
            self.rebuild()
        self.assertEqual((self.raw / rough_cut.FILE_NAME).read_bytes(), before)

    def test_a_write_that_fails_leaves_the_old_list_and_no_temporary_file(self):
        self.rebuild()
        before = (self.raw / rough_cut.FILE_NAME).read_bytes()
        self.change(lambda store: scene_store.set_star(store, COSTS, 1))
        with mock.patch.object(scene_store.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.rebuild()
        self.assertEqual((self.raw / rough_cut.FILE_NAME).read_bytes(), before)
        self.assertEqual([p.name for p in self.raw.iterdir() if p.name.endswith('.tmp')], [])


class WhileAnotherProgramWrites(RoughCutCase):
    def test_the_list_is_made_from_a_scene_file_that_nobody_is_changing(self):
        """A rebuild waits for the scene file's lock, so it never lists a half-made change."""
        done = threading.Event()

        def run():
            self.rebuild()
            done.set()

        with scene_store.locked(self.raw):
            worker = threading.Thread(target=run)
            worker.start()
            time.sleep(0.3)
            self.assertFalse(done.is_set(), 'the rebuild did not wait for the lock')
        worker.join(5)
        self.assertTrue(done.is_set())

    def test_two_rebuilds_at_once_leave_the_list_of_the_latest_scene_file(self):
        for number in range(3, 9):
            self.change(lambda store, n=number: scene_store.add_take(store, ROOM, n, duration=60.0, sound=CLEAN))
            threads = [threading.Thread(target=self.rebuild) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(self.entry(ROOM)['take'], number)
        self.assertEqual([p.name for p in self.raw.iterdir() if p.name.endswith('.tmp')], [])


if __name__ == '__main__':
    unittest.main()
