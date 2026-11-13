"""Checks for the per-video scene file (scripts/video/scene_store.py). Only small invented files, no real footage."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/video'))
import scene_store  # noqa: E402

FIXTURES = ROOT / 'tests/fixtures'
CUE = (FIXTURES / 'scene-cue-file.md').read_text(encoding='utf-8')
CLEAN = {'ends_early': False, 'long_pause': False}
SHORT = {'ends_early': True, 'long_pause': False}
PAUSY = {'ends_early': False, 'long_pause': True}


def store_with_takes(*takes, **scene_fields):
    """A one-scene store; each take is (number, sound-or-None)."""
    store = scene_store.new_store(scene_store.parse_cue_file(CUE))
    for number, sound in takes:
        scene_store.add_take(store, '02-what-it-really-costs', number, sound=sound)
    store['scenes'][1].update(scene_fields)
    return store


def best(store, scene_id='02-what-it-really-costs'):
    scene_store.apply_best(store)
    scene = scene_store.find_scene(store, scene_id)
    return scene['best'], scene['flagged']


class SceneIds(unittest.TestCase):
    def test_numbered_headings_become_scenes(self):
        scenes = scene_store.parse_cue_file(CUE)
        self.assertEqual([scene['id'] for scene in scenes], [
            '01-intro', '02-what-it-really-costs', '03-the-news-moves-fast',
            '04-room-1-what-changed', '05-whats-next'])
        self.assertEqual(scenes[1]['number'], 2)
        self.assertEqual(scenes[1]['title'], 'WHAT IT REALLY COSTS')

    def test_unnumbered_headings_are_not_scenes(self):
        titles = [scene['title'] for scene in scene_store.parse_cue_file(CUE)]
        self.assertNotIn('PREP', titles)
        self.assertNotIn('HOOK AND INTRO', titles)

    def test_time_suffix_and_plain_markdown_forms(self):
        text = '## 7 · Big Finish · 1:02:30\n### **8 · Short one**\nspoken words · 12:00\n'
        scenes = scene_store.parse_cue_file(text)
        self.assertEqual([(s['id'], s['title']) for s in scenes],
                         [('07-big-finish', 'Big Finish'), ('08-short-one', 'Short one')])

    def test_ids_are_two_digits_lowercase_with_dashes(self):
        self.assertEqual(scene_store.scene_id(2, 'What it really costs'), '02-what-it-really-costs')
        self.assertEqual(scene_store.scene_id(12, 'Café   & "Tea"!'), '12-cafe-tea')

    def test_empty_file_has_no_scenes(self):
        self.assertEqual(scene_store.parse_cue_file(''), [])


class TakeNames(unittest.TestCase):
    def test_file_name_both_ways(self):
        self.assertEqual(scene_store.take_file_name(3), 'take-03.mkv')
        self.assertEqual(scene_store.parse_take_file_name('take-03.mkv'), (3, '.mkv'))
        self.assertEqual(scene_store.parse_take_file_name('take-120.mp4'), (120, '.mp4'))

    def test_full_name_both_ways(self):
        name = scene_store.take_full_name('02-what-it-really-costs', 3)
        self.assertEqual(name, 'scene-02-what-it-really-costs-take-03')
        self.assertEqual(scene_store.parse_take_full_name(name), ('02-what-it-really-costs', 3, ''))
        self.assertEqual(scene_store.parse_take_full_name(name + '.mkv'),
                         ('02-what-it-really-costs', 3, '.mkv'))

    def test_a_title_that_says_take_still_reads_back(self):
        name = scene_store.take_full_name('04-take-your-time', 1)
        self.assertEqual(scene_store.parse_take_full_name(name), ('04-take-your-time', 1, ''))

    def test_other_names_are_not_takes(self):
        self.assertIsNone(scene_store.parse_take_file_name('notes.txt'))
        self.assertIsNone(scene_store.parse_take_file_name('take-x.mkv'))
        self.assertIsNone(scene_store.parse_take_full_name('scene-02-take-three.mkv'))

    def test_folder_and_file_path(self):
        folder = scene_store.scene_folder(Path('raw'), '02-what-it-really-costs')
        self.assertEqual(folder, Path('raw/scenes/02-what-it-really-costs'))


class SceneListSavedOnce(unittest.TestCase):
    def test_first_call_saves_and_second_keeps_the_saved_list(self):
        with tempfile.TemporaryDirectory() as raw:
            first = scene_store.ensure_scene_list(raw, CUE)
            self.assertTrue((Path(raw) / 'scenes.json').is_file())
            changed_script = CUE.replace('WHAT IT REALLY COSTS', 'WHAT IT COSTS')
            second = scene_store.ensure_scene_list(raw, changed_script)
            self.assertEqual(second, first)
            self.assertEqual(scene_store.load(raw)['scenes'][1]['id'], '02-what-it-really-costs')

    def test_starts_with_empty_fields(self):
        with tempfile.TemporaryDirectory() as raw:
            scene = scene_store.ensure_scene_list(raw, CUE)['scenes'][1]
        self.assertEqual((scene['takes'], scene['star'], scene['favorite'], scene['best']), ([], None, None, None))
        self.assertFalse(scene['flagged'] or scene['script_changed'])

    def test_a_cue_file_without_numbered_sections_saves_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(ValueError):
                scene_store.ensure_scene_list(raw, '### **PREP**\nwords\n')
            self.assertIsNone(scene_store.load(raw))

    def test_missing_file_loads_as_none(self):
        with tempfile.TemporaryDirectory() as raw:
            self.assertIsNone(scene_store.load(raw))


class ScriptChanges(unittest.TestCase):
    def setUp(self):
        self.store = scene_store.new_store(scene_store.parse_cue_file(CUE))
        self.before = json.dumps(self.store, sort_keys=True)

    def flagged(self, script):
        scene_store.check_script(self.store, script)
        return [s['id'] for s in self.store['scenes'] if s['script_changed']]

    def test_same_script_flags_nothing(self):
        self.assertEqual(self.flagged(CUE), [])

    def test_changed_title_is_flagged(self):
        self.assertEqual(self.flagged(CUE.replace('WHAT IT REALLY COSTS', 'WHAT IT COSTS')),
                         ['02-what-it-really-costs'])

    def test_moved_scene_is_flagged_without_renaming_anything(self):
        moved = CUE.replace('2 · WHAT IT REALLY COSTS', '9 · WHAT IT REALLY COSTS')
        self.assertEqual(self.flagged(moved), ['02-what-it-really-costs'])
        self.assertEqual([s['id'] for s in self.store['scenes']][1], '02-what-it-really-costs')

    def test_removed_scene_is_flagged_and_kept(self):
        without = CUE.replace('### **3 · THE NEWS MOVES FAST · 10:00**', '### not a heading')
        self.assertEqual(self.flagged(without), ['03-the-news-moves-fast'])
        self.assertEqual(len(self.store['scenes']), 5)

    def test_new_scene_in_script_is_reported_not_added(self):
        added = CUE + '### **6 · A LATE ADDITION**\nwords\n'
        report = scene_store.check_script(self.store, added)
        self.assertEqual(report['added'], ['06-a-late-addition'])
        self.assertEqual(len(self.store['scenes']), 5)

    def test_nothing_is_renamed_or_deleted(self):
        self.flagged(CUE.replace('WHAT IT REALLY COSTS', 'WHAT IT COSTS'))
        for scene in self.store['scenes']:
            scene['script_changed'] = False
        self.assertEqual(json.dumps(self.store, sort_keys=True), self.before)

    def test_flag_clears_when_the_script_matches_again(self):
        self.flagged(CUE.replace('WHAT IT REALLY COSTS', 'WHAT IT COSTS'))
        self.assertEqual(self.flagged(CUE), [])

    def test_takes_stay_with_the_saved_scene(self):
        scene_store.add_take(self.store, '02-what-it-really-costs', 1)
        self.flagged(CUE.replace('WHAT IT REALLY COSTS', 'WHAT IT COSTS'))
        self.assertEqual(len(scene_store.find_scene(self.store, '02-what-it-really-costs')['takes']), 1)


class Takes(unittest.TestCase):
    def test_add_take_builds_names_and_keeps_order(self):
        store = scene_store.new_store(scene_store.parse_cue_file(CUE))
        scene_store.add_take(store, '02-what-it-really-costs', 3, duration=12.5)
        scene_store.add_take(store, '02-what-it-really-costs', 1)
        takes = scene_store.find_scene(store, '02-what-it-really-costs')['takes']
        self.assertEqual([t['number'] for t in takes], [1, 3])
        self.assertEqual(takes[1]['file'], 'take-03.mkv')
        self.assertEqual(takes[1]['name'], 'scene-02-what-it-really-costs-take-03')
        self.assertEqual(takes[1]['duration'], 12.5)

    def test_same_take_twice_updates_it(self):
        store = scene_store.new_store(scene_store.parse_cue_file(CUE))
        scene_store.add_take(store, '01-intro', 1)
        scene_store.add_take(store, '01-intro', 1, duration=5)
        takes = scene_store.find_scene(store, '01-intro')['takes']
        self.assertEqual([(t['number'], t['duration']) for t in takes], [(1, 5)])

    def test_unknown_scene_or_field_is_refused(self):
        store = scene_store.new_store(scene_store.parse_cue_file(CUE))
        with self.assertRaises(KeyError):
            scene_store.add_take(store, '99-nothing', 1)
        with self.assertRaises(ValueError):
            scene_store.add_take(store, '01-intro', 1, colour='red')

    def test_star_needs_an_existing_take(self):
        store = store_with_takes((1, None))
        scene_store.set_star(store, '02-what-it-really-costs', 1)
        self.assertEqual(scene_store.find_scene(store, '02-what-it-really-costs')['star'], 1)
        with self.assertRaises(ValueError):
            scene_store.set_star(store, '02-what-it-really-costs', 7)
        scene_store.set_star(store, '02-what-it-really-costs', None)
        self.assertIsNone(scene_store.find_scene(store, '02-what-it-really-costs')['star'])


class BestTake(unittest.TestCase):
    def test_no_takes_no_best(self):
        self.assertEqual(best(store_with_takes()), (None, False))

    def test_newest_by_default(self):
        self.assertEqual(best(store_with_takes((1, None), (2, None), (3, None))), (3, False))

    def test_newest_means_highest_number_not_insertion_order(self):
        self.assertEqual(best(store_with_takes((3, None), (1, None), (2, None))), (3, False))

    def test_star_wins_over_newer_takes(self):
        store = store_with_takes((1, CLEAN), (2, CLEAN), (3, CLEAN), star=1)
        self.assertEqual(best(store), (1, False))

    def test_star_wins_even_when_that_take_sounds_worse(self):
        store = store_with_takes((1, CLEAN), (2, SHORT), star=2)
        self.assertEqual(best(store), (2, False))

    def test_computers_favorite_never_overrides_a_star(self):
        store = store_with_takes((1, CLEAN), (2, CLEAN), (3, CLEAN), star=2, favorite=3)
        self.assertEqual(best(store), (2, False))

    def test_computers_favorite_alone_does_not_beat_the_newest(self):
        store = store_with_takes((1, CLEAN), (2, CLEAN), favorite=1)
        self.assertEqual(best(store), (2, False))

    def test_a_star_on_a_missing_take_is_ignored(self):
        store = store_with_takes((1, None), (2, None), star=9)
        self.assertEqual(best(store), (2, False))

    def test_clearly_worse_newest_uses_older_clean_take_and_flags(self):
        store = store_with_takes((1, CLEAN), (2, CLEAN), (3, SHORT))
        self.assertEqual(best(store), (2, True))

    def test_long_pause_counts_as_clearly_worse(self):
        store = store_with_takes((1, CLEAN), (2, PAUSY))
        self.assertEqual(best(store), (1, True))

    def test_worse_newest_with_no_clean_older_take_stays_newest(self):
        store = store_with_takes((1, SHORT), (2, PAUSY))
        self.assertEqual(best(store), (2, False))

    def test_worse_newest_with_unchecked_older_takes_stays_newest(self):
        store = store_with_takes((1, None), (2, SHORT))
        self.assertEqual(best(store), (2, False))

    def test_clean_newest_is_not_flagged(self):
        store = store_with_takes((1, SHORT), (2, CLEAN))
        self.assertEqual(best(store), (2, False))

    def test_flag_clears_once_lex_stars_a_take(self):
        store = store_with_takes((1, CLEAN), (2, SHORT))
        self.assertEqual(best(store), (1, True))
        scene_store.set_star(store, '02-what-it-really-costs', 2)
        self.assertEqual(best(store), (2, False))


class SafeWrites(unittest.TestCase):
    def test_round_trip_keeps_everything(self):
        with tempfile.TemporaryDirectory() as raw:
            store = store_with_takes((1, CLEAN))
            scene_store.save(raw, store)
            self.assertEqual(scene_store.load(raw), store)

    def test_save_leaves_no_temporary_file_behind(self):
        with tempfile.TemporaryDirectory() as raw:
            scene_store.save(raw, store_with_takes())
            self.assertEqual(sorted(p.name for p in Path(raw).iterdir()), ['scenes.json'])

    def test_half_written_temporary_file_is_not_a_broken_file(self):
        with tempfile.TemporaryDirectory() as raw:
            good = store_with_takes((1, CLEAN))
            scene_store.save(raw, good)
            (Path(raw) / 'scenes.json.4242.abcd.tmp').write_text('{"version": 1, "scen', encoding='utf-8')
            self.assertEqual(scene_store.load(raw), good)

    def test_only_a_half_written_temporary_file_means_no_file_yet(self):
        with tempfile.TemporaryDirectory() as raw:
            (Path(raw) / 'scenes.json.4242.abcd.tmp').write_text('{"ver', encoding='utf-8')
            self.assertIsNone(scene_store.load(raw))

    def test_a_truly_broken_file_is_an_error_not_an_empty_store(self):
        with tempfile.TemporaryDirectory() as raw:
            (Path(raw) / 'scenes.json').write_text('{"ver', encoding='utf-8')
            with self.assertRaises(scene_store.SceneFileError):
                scene_store.load(raw)
            with self.assertRaises(scene_store.SceneFileError):
                scene_store.ensure_scene_list(raw, CUE)

    def test_a_reader_never_sees_a_half_written_file_while_another_program_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            scene_store.save(raw, store_with_takes())
            stop = threading.Event()
            errors = []

            def writer():
                number = 0
                while not stop.is_set():
                    number += 1
                    store = store_with_takes((number, CLEAN))
                    store['padding'] = 'x' * 200_000
                    scene_store.save(raw, store)

            thread = threading.Thread(target=writer)
            thread.start()
            try:
                for _ in range(300):
                    try:
                        self.assertIsNotNone(scene_store.load(raw))
                    except Exception as error:  # noqa: BLE001 - any failure is the bug under test
                        errors.append(repr(error))
                        break
            finally:
                stop.set()
                thread.join()
            self.assertEqual(errors, [])


class SampleFile(unittest.TestCase):
    def test_sample_scenes_json_uses_the_documented_fields(self):
        sample = json.loads((FIXTURES / 'scenes.sample.json').read_text(encoding='utf-8'))
        self.assertEqual(set(sample), set(scene_store.STORE_FIELDS))
        for scene in sample['scenes']:
            self.assertEqual(set(scene), set(scene_store.SCENE_FIELDS))
            for take in scene['takes']:
                self.assertLessEqual(set(take), set(scene_store.TAKE_FIELDS))
                self.assertEqual(take['name'], scene_store.take_full_name(scene['id'], take['number']))
                self.assertEqual(take['file'], scene_store.take_file_name(take['number']))

    def test_sample_best_matches_the_rules(self):
        sample = json.loads((FIXTURES / 'scenes.sample.json').read_text(encoding='utf-8'))
        expected = [(s['id'], s['best'], s['flagged']) for s in sample['scenes']]
        scene_store.apply_best(sample)
        self.assertEqual([(s['id'], s['best'], s['flagged']) for s in sample['scenes']], expected)


if __name__ == '__main__':
    unittest.main()
