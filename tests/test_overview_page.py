"""Checks for the overview page (scripts/video/overview_page.py). Small invented scene files and cue text only;
ffmpeg is replaced by a stand-in, so nothing here needs real footage or a media tool."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts/video'))
import overview_page  # noqa: E402
import scene_store  # noqa: E402

CUE = (ROOT / 'tests/fixtures/scene-cue-file.md').read_text(encoding='utf-8')
CLEAN = {'ends_early': False, 'long_pause': False, 'length_off': False}
SHORT = {'ends_early': True, 'long_pause': False, 'length_off': False}
PAUSY = {'ends_early': False, 'long_pause': True, 'length_off': False}
COSTS = '02-what-it-really-costs'


def sample_store():
    """Five scenes: 1 has no take, 2 has three takes (star on 2, favorite 3), 3 has a changed script,
    4 has one take, 5 has no take."""
    store = scene_store.new_store(scene_store.parse_cue_file(CUE))
    scene_store.add_take(store, COSTS, 1, duration=80.0, sound=SHORT)
    scene_store.add_take(store, COSTS, 2, duration=118.0, sound=CLEAN)
    scene_store.add_take(store, COSTS, 3, duration=64.0, sound=PAUSY)
    scene_store.set_star(store, COSTS, 2)
    scene_store.set_favorite(store, COSTS, 3)
    scene_store.add_take(store, '04-room-1-what-changed', 1, duration=65.0, sound=CLEAN)
    scene_store.find_scene(store, '03-the-news-moves-fast')['script_changed'] = True
    scene_store.apply_best(store)
    return store


def scene_html(page, scene_id):
    """The part of the page that belongs to one scene."""
    match = re.search(rf'<section class="scene" data-scene="{re.escape(scene_id)}".*?</section>', page, re.S)
    assert match, f'no section for {scene_id}'
    return match.group(0)


def render(store=None, **options):
    return overview_page.render(store or sample_store(), 'Weekly update 03', cue_text=CUE, **options)


class SceneCards(unittest.TestCase):
    def test_scenes_come_in_script_order_with_number_and_title(self):
        page = render()
        titles = re.findall(r'<b class="stitle">(.*?)</b>', page)
        self.assertEqual(titles, ['01 · INTRO', '02 · WHAT IT REALLY COSTS', '03 · THE NEWS MOVES FAST',
                                  '04 · ROOM 1: WHAT CHANGED?', '05 · WHAT&#x27;S NEXT'])

    def test_the_scenes_script_text_is_shown_without_directions(self):
        costs = scene_html(render(), COSTS)
        self.assertIn('Two out of three had a problem.', costs)
        self.assertNotIn('Face small', costs)
        self.assertNotIn('The survey', costs)
        intro = scene_html(render(), '01-intro')
        self.assertIn('Picture this. An email from a client: &quot;Is our website safe?&quot; You don&#x27;t know yet.',
                      intro)

    def test_the_best_take_is_in_front_and_the_others_are_archived(self):
        costs = scene_html(render(), COSTS)
        front, archived = costs.split('<details class="arch">')
        self.assertIn('data-take="2"', front)
        self.assertNotIn('data-take="1"', front)
        self.assertNotIn('data-take="3"', front)
        self.assertIn('<summary>Archived (2)</summary>', archived)
        self.assertIn('data-take="1"', archived)
        self.assertIn('data-take="3"', archived)

    def test_a_scene_with_one_take_has_no_archive(self):
        room = scene_html(render(), '04-room-1-what-changed')
        self.assertIn('data-take="1"', room)
        self.assertNotIn('Archived', room)

    def test_the_best_take_has_a_gold_star_and_the_others_a_hollow_one(self):
        costs = scene_html(render(), COSTS)
        stars = dict(re.findall(r'data-star data-scene="[^"]*" data-take="(\d)" data-on="(\w+)"', costs))
        self.assertEqual(stars, {'1': 'false', '2': 'true', '3': 'false'})
        gold = re.search(r'data-take="2" data-on="true"[^>]*>(.)</button>', costs)
        self.assertEqual(gold.group(1), '★')
        hollow = re.search(r'data-take="1" data-on="false"[^>]*>(.)</button>', costs)
        self.assertEqual(hollow.group(1), '☆')

    def test_the_best_by_default_take_is_gold_even_without_a_star(self):
        store = sample_store()
        scene_store.set_star(store, COSTS, None)
        scene_store.find_scene(store, COSTS)['takes'][2]['sound'] = CLEAN  # the newest take is fine
        scene_store.apply_best(store)
        costs = scene_html(render(store), COSTS)
        self.assertIn('data-take="3" data-on="true" data-starred="false"', costs)
        self.assertIn('data-take="2" data-on="false"', costs)

    def test_the_computers_favorite_shows_as_a_pick_when_it_differs(self):
        costs = scene_html(render(), COSTS)
        pick = re.findall(r'<span class="pick"[^>]*>☆ pick</span>', costs)
        self.assertEqual(len(pick), 1)
        # take 3 is the favorite and sits in the archive
        archived = costs.split('<details class="arch">')[1]
        self.assertIn('☆ pick', archived.split('data-take="3"')[1])
        self.assertNotIn('☆ pick', archived.split('data-take="3"')[0])

    def test_no_pick_when_the_favorite_is_the_best_take(self):
        store = sample_store()
        scene_store.set_favorite(store, COSTS, 2)
        self.assertNotIn('pick', scene_html(render(store), COSTS).replace('data-pick', ''))

    def test_a_changed_script_gets_a_chip_and_an_unchanged_one_does_not(self):
        page = render()
        self.assertIn('Script changed since recording', scene_html(page, '03-the-news-moves-fast'))
        self.assertNotIn('Script changed since recording', scene_html(page, COSTS))

    def test_a_scene_without_takes_says_not_recorded_yet(self):
        intro = scene_html(render(), '01-intro')
        self.assertIn('Not recorded yet', intro)
        self.assertNotIn('data-take', intro)
        self.assertNotIn('Archived', intro)
        self.assertNotIn('Not recorded yet', scene_html(render(), COSTS))

    def test_each_take_says_how_its_sound_checks_went(self):
        costs = scene_html(render(), COSTS)
        self.assertIn('ended early', costs)
        self.assertIn('long pause', costs)
        self.assertIn('clean end', costs)

    def test_a_take_without_sound_data_says_nothing(self):
        store = scene_store.new_store(scene_store.parse_cue_file(CUE))
        scene_store.add_take(store, '01-intro', 1, duration=10.0)
        scene_store.apply_best(store)
        self.assertEqual(re.findall(r'<div class="note">(.*?)</div>', scene_html(render(store), '01-intro')), [''])

    def test_a_take_shows_its_number_and_length(self):
        self.assertIn('Take 2 (1:58)', scene_html(render(), COSTS))
        self.assertIn('Take 3 (1:04)', scene_html(render(), COSTS))

    def test_a_take_without_a_length_shows_only_its_number(self):
        store = scene_store.new_store(scene_store.parse_cue_file(CUE))
        scene_store.add_take(store, '01-intro', 1)
        scene_store.apply_best(store)
        intro = scene_html(render(store), '01-intro')
        self.assertIn('Take 1<', intro)
        self.assertNotIn('Take 1 (', intro)

    def test_the_record_tile_is_disabled_until_the_prompter_exists(self):
        for scene_id in ('01-intro', COSTS):
            tile = re.search(r'<button class="rec"[^>]*>.*?</button>', scene_html(render(), scene_id), re.S).group(0)
            self.assertIn('disabled', tile)
            self.assertIn('Coming with the Prompter', tile)

    def test_a_scene_missing_from_the_script_has_no_text_but_still_shows(self):
        page = overview_page.render(sample_store(), 'Weekly update 03', cue_text='')
        self.assertIn('02 · WHAT IT REALLY COSTS', page)
        self.assertNotIn('Two out of three', page)
        self.assertNotIn('class="stext"', scene_html(page, COSTS))

    def test_titles_script_text_and_names_are_escaped(self):
        store = scene_store.new_store([{'id': '01-x', 'number': 1, 'title': '<script>alert(1)</script>'}])
        scene_store.add_take(store, '01-x', 1, duration=5.0)
        scene_store.apply_best(store)
        cue = '### 1 · <script>alert(1)</script>\n<img src=x onerror=alert(2)>\n'
        page = overview_page.render(store, '<b>Name</b>', cue_text=cue)
        self.assertNotIn('<script>alert', page)
        self.assertNotIn('<img src=x', page)
        self.assertNotIn('<b>Name</b>', page)
        self.assertIn('&lt;b&gt;Name&lt;/b&gt;', page)


class CutStrip(unittest.TestCase):
    def strip(self, page):
        block = re.search(r'<div class="cut".*?</div>\s*<p class="cutlabel"', page, re.S).group(0)
        return re.findall(r'<div( class="none")?[^>]*>(\d+)</div>', block)

    def test_the_strip_has_one_segment_per_scene_in_script_order(self):
        self.assertEqual(self.strip(render()), [(' class="none"', '1'), ('', '2'), (' class="none"', '3'),
                                                ('', '4'), (' class="none"', '5')])

    def test_a_segment_is_as_long_as_the_best_take(self):
        page = render()
        self.assertIn('style="flex:118" title="Scene 2, take 2"', page)
        self.assertIn('style="flex:65" title="Scene 4, take 1"', page)

    def test_the_header_counts_scenes_takes_and_the_total_length(self):
        page = render()
        self.assertIn('<b>2 of 5 scenes</b> recorded', page)
        self.assertIn('4 takes', page)
        self.assertIn('rough cut <b>3:03</b>', page)

    def test_a_best_take_without_a_length_is_marked_in_the_total(self):
        store = scene_store.new_store(scene_store.parse_cue_file(CUE))
        scene_store.add_take(store, '01-intro', 1, duration=60.0)
        scene_store.add_take(store, COSTS, 1)
        scene_store.apply_best(store)
        self.assertIn('rough cut <b>1:00+</b>', render(store))

    def test_the_strip_follows_a_star_change(self):
        store = sample_store()
        scene_store.set_star(store, COSTS, 1)
        scene_store.apply_best(store)
        page = render(store)
        self.assertIn('style="flex:80" title="Scene 2, take 1"', page)
        self.assertIn('rough cut <b>2:25</b>', page)


class Links(unittest.TestCase):
    def test_takes_link_to_their_files_by_relative_path(self):
        page = render()
        self.assertIn('data-play="scenes/02-what-it-really-costs/take-02.mkv"', page)

    def test_a_link_prefix_is_put_in_front_for_the_served_page(self):
        page = render(link_prefix='/scenes/video-abc/')
        self.assertIn('data-play="/scenes/video-abc/scenes/02-what-it-really-costs/take-02.mkv"', page)

    def test_odd_characters_in_a_file_name_are_encoded(self):
        store = sample_store()
        scene_store.find_scene(store, COSTS)['takes'][0]['file'] = 'take 01 #1.mkv'
        self.assertIn('scenes/02-what-it-really-costs/take%2001%20%231.mkv', render(store))

    def test_a_thumbnail_is_used_only_when_it_exists(self):
        thumb = 'scenes/02-what-it-really-costs/take-02.jpg'
        page = render(thumbs={thumb})
        costs = scene_html(page, COSTS)
        self.assertIn(f'<img src="{thumb}"', costs)
        self.assertEqual(costs.count('<img'), 1)

    def test_the_page_is_one_self_contained_file(self):
        page = render()
        self.assertNotRegex(page, r'<link[^>]+(stylesheet|href)')
        self.assertNotRegex(page, r'<script[^>]+src=')
        self.assertIn('<style>', page)
        self.assertIn('<script>', page)
        self.assertIn('name="viewport"', page)


class StarsOffline(unittest.TestCase):
    def test_the_stars_start_greyed_with_one_short_line_saying_why(self):
        page = render(server='http://127.0.0.1:8879', video='weekly-1234')
        self.assertEqual(overview_page.STARS_OFF_TEXT, 'Stars are off. Start video-review on this computer to change them.')
        self.assertEqual(page.count(overview_page.STARS_OFF_TEXT), 1)
        buttons = re.findall(r'<button class="st"[^>]*>', page)
        self.assertEqual(len(buttons), 4)
        self.assertTrue(all(' disabled' in button for button in buttons))

    def test_the_page_knows_where_to_ask(self):
        page = render(server='http://127.0.0.1:8879', video='weekly-1234')
        self.assertIn('data-server="http://127.0.0.1:8879"', page)
        self.assertIn('data-video="weekly-1234"', page)


class ScriptText(unittest.TestCase):
    def test_text_is_collected_per_scene_without_directions_marks_or_links(self):
        cue = ('# Title\n### **PREP**\nnot a scene\n### **1 · A · 00:00**\n[Face full]\n**Bold** words.\n'
               'See [this page](https://example.com) now.\n\n### **HOOK**\nnot in scene 1\n### 2 · B\nTwo.\n')
        self.assertEqual(overview_page.scene_texts(cue), {'01-a': 'Bold words. See this page now.', '02-b': 'Two.'})

    def test_the_first_of_two_same_headings_wins(self):
        self.assertEqual(overview_page.scene_texts('### 1 · A\nfirst\n### 1 · A\nsecond\n'), {'01-a': 'first'})


class Thumbnails(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = Path(self.temp.name)
        self.store = sample_store()
        folder = self.raw / 'scenes' / COSTS
        folder.mkdir(parents=True)
        for number in (1, 2, 3):
            (folder / f'take-0{number}.mkv').write_bytes(b'x')
        self.calls = []

    def fake_ffmpeg(self, command, **kwargs):
        self.calls.append(command)
        Path(command[-1]).write_bytes(b'jpeg')
        return subprocess.CompletedProcess(command, 0, '', '')

    def make(self):
        with mock.patch.object(overview_page.subprocess, 'run', side_effect=self.fake_ffmpeg):
            return overview_page.ensure_thumbnails(self.raw, self.store)

    def test_one_frame_per_take_is_saved_next_to_the_take(self):
        found = self.make()
        self.assertEqual(found, {f'scenes/{COSTS}/take-0{n}.jpg' for n in (1, 2, 3)})
        for number in (1, 2, 3):
            self.assertTrue((self.raw / 'scenes' / COSTS / f'take-0{number}.jpg').is_file())
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[0][0], 'ffmpeg')
        self.assertIn(str(self.raw / 'scenes' / COSTS / 'take-01.mkv'), self.calls[0])
        self.assertEqual(sorted(p.name for p in (self.raw / 'scenes' / COSTS).iterdir() if p.suffix != '.mkv'),
                         ['take-01.jpg', 'take-02.jpg', 'take-03.jpg'])

    def test_a_frame_that_exists_is_not_made_again(self):
        self.make()
        self.calls.clear()
        self.make()
        self.assertEqual(self.calls, [])

    def test_a_take_file_that_is_not_there_is_skipped(self):
        (self.raw / 'scenes' / COSTS / 'take-03.mkv').unlink()
        self.assertEqual(self.make(), {f'scenes/{COSTS}/take-0{n}.jpg' for n in (1, 2)})

    def test_a_short_take_is_looked_at_before_its_end(self):
        scene_store.find_scene(self.store, COSTS)['takes'][0]['duration'] = 0.5
        self.make()
        seek = self.calls[0][self.calls[0].index('-ss') + 1]
        self.assertEqual(float(seek), 0.25)

    def test_a_failed_frame_leaves_no_file_and_the_others_still_get_made(self):
        def flaky(command, **kwargs):
            if 'take-01.mkv' in ' '.join(command):
                return subprocess.CompletedProcess(command, 1, '', 'broken')
            return self.fake_ffmpeg(command)
        with mock.patch.object(overview_page.subprocess, 'run', side_effect=flaky):
            found = overview_page.ensure_thumbnails(self.raw, self.store)
        self.assertEqual(found, {f'scenes/{COSTS}/take-0{n}.jpg' for n in (2, 3)})
        self.assertFalse(any('.tmp' in p.name for p in (self.raw / 'scenes' / COSTS).iterdir()))

    def test_without_ffmpeg_there_are_no_frames_and_nothing_breaks(self):
        with mock.patch.object(overview_page.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertEqual(overview_page.ensure_thumbnails(self.raw, self.store), set())

    def test_an_ffmpeg_that_hangs_is_given_up_on(self):
        with mock.patch.object(overview_page.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired('ffmpeg', 1)):
            self.assertEqual(overview_page.ensure_thumbnails(self.raw, self.store), set())


class WritingThePage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = Path(self.temp.name) / 'Weekly update 03'
        self.raw.mkdir()
        self.data = Path(self.temp.name) / 'data'
        scene_store.save(self.raw, sample_store())
        (self.raw / 'prompter.md').write_text(CUE, encoding='utf-8')
        patcher = mock.patch.object(overview_page.subprocess, 'run', side_effect=FileNotFoundError)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_build_writes_overview_html_into_the_video_folder(self):
        path = overview_page.build(self.raw)
        self.assertEqual(path, self.raw / 'overview.html')
        page = path.read_text(encoding='utf-8')
        self.assertIn('Weekly update 03', page)
        self.assertIn('Two out of three had a problem.', page)

    def test_the_cue_file_in_the_video_folder_is_read_but_never_changed(self):
        before = (self.raw / 'prompter.md').read_bytes()
        overview_page.build(self.raw)
        self.assertEqual((self.raw / 'prompter.md').read_bytes(), before)

    def test_another_cue_file_can_be_named(self):
        other = Path(self.temp.name) / 'cues.md'
        other.write_text('### 2 · WHAT IT REALLY COSTS\nOther words.\n', encoding='utf-8')
        page = overview_page.build(self.raw, cue=other).read_text(encoding='utf-8')
        self.assertIn('Other words.', page)
        self.assertNotIn('Two out of three', page)

    def test_without_a_cue_file_the_page_still_builds(self):
        (self.raw / 'prompter.md').unlink()
        page = overview_page.build(self.raw).read_text(encoding='utf-8')
        self.assertIn('02 · WHAT IT REALLY COSTS', page)

    def test_a_second_build_replaces_the_page_and_leaves_no_temporary_file(self):
        overview_page.build(self.raw)
        scene_store.update(self.raw, lambda store: scene_store.set_star(store, COSTS, 1))
        overview_page.build(self.raw)
        self.assertIn('style="flex:80" title="Scene 2, take 1"', (self.raw / 'overview.html').read_text('utf-8'))
        self.assertEqual(sorted(p.name for p in self.raw.iterdir() if p.name != scene_store.LOCK_NAME),
                         ['overview.html', 'prompter.md', 'scenes.json'])

    def test_a_folder_without_a_scene_file_is_an_error_that_says_so(self):
        (self.raw / 'scenes.json').unlink()
        with self.assertRaises(scene_store.SceneFileError) as caught:
            overview_page.build(self.raw)
        self.assertIn('scenes.json', str(caught.exception))

    def test_the_page_built_for_the_server_uses_its_prefix_and_writes_no_file(self):
        page = overview_page.build_html(self.raw, link_prefix='/scenes/key/')
        self.assertIn('data-play="/scenes/key/scenes/', page)
        self.assertFalse((self.raw / 'overview.html').exists())

    def test_the_server_address_comes_from_the_port_setting(self):
        with mock.patch.dict(os.environ, {'VIDEO_REVIEW_PORT': '9123'}):
            page = overview_page.build(self.raw).read_text(encoding='utf-8')
        self.assertIn('data-server="http://127.0.0.1:9123"', page)
        page = overview_page.build(self.raw).read_text(encoding='utf-8')
        self.assertIn('data-server="http://127.0.0.1:8879"', page)


class KeysAndRegistry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()

    def video(self, name):
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        scene_store.save(folder, sample_store())
        return folder

    def test_a_key_is_stable_and_safe_in_an_address(self):
        folder = self.video('Weekly update #3 café')
        key = overview_page.video_key(folder)
        self.assertEqual(key, overview_page.video_key(folder))
        self.assertRegex(key, r'^[a-z0-9-]+$')
        self.assertTrue(key.startswith('weekly-update-3-cafe-'))

    def test_two_folders_with_the_same_name_get_different_keys(self):
        one = self.video('a/Weekly')
        two = self.video('b/Weekly')
        self.assertNotEqual(overview_page.video_key(one), overview_page.video_key(two))

    def test_a_registered_video_is_found_by_its_key(self):
        folder = self.video('Weekly')
        key = overview_page.register(self.data, folder)
        self.assertEqual(overview_page.lookup(self.data, key), (folder.resolve(), None))

    def test_a_cue_file_is_remembered_with_the_video(self):
        folder = self.video('Weekly')
        cue = self.root / 'cues.md'
        key = overview_page.register(self.data, folder, cue)
        self.assertEqual(overview_page.lookup(self.data, key), (folder.resolve(), cue.resolve()))
        overview_page.register(self.data, folder)  # registering again without a cue keeps the named one
        self.assertEqual(overview_page.lookup(self.data, key)[1], cue.resolve())

    def test_a_key_nobody_registered_is_not_found(self):
        self.assertIsNone(overview_page.lookup(self.data, 'nope-12345678'))
        self.assertIsNone(overview_page.lookup(self.data, '../etc'))

    def test_a_video_whose_folder_is_gone_is_not_found(self):
        folder = self.video('Weekly')
        key = overview_page.register(self.data, folder)
        (folder / 'scenes.json').unlink()
        self.assertIsNone(overview_page.lookup(self.data, key))

    def test_registering_twice_does_not_rewrite_the_registry(self):
        folder = self.video('Weekly')
        overview_page.register(self.data, folder)
        before = (self.data / overview_page.REGISTRY_NAME).stat().st_mtime_ns
        overview_page.register(self.data, folder)
        self.assertEqual((self.data / overview_page.REGISTRY_NAME).stat().st_mtime_ns, before)

    def test_a_broken_registry_file_is_an_error_not_an_empty_registry(self):
        (self.data / overview_page.REGISTRY_NAME).write_text('{nope', encoding='utf-8')
        with self.assertRaises(ValueError):
            overview_page.register(self.data, self.video('Weekly'))


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = Path(self.temp.name) / 'Weekly'
        self.raw.mkdir()
        scene_store.save(self.raw, sample_store())
        self.data = Path(self.temp.name) / 'data'
        patcher = mock.patch.object(overview_page.subprocess, 'run', side_effect=FileNotFoundError)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = overview_page.main([str(a) for a in args])
        return code, out.getvalue()

    def test_it_writes_the_page_and_registers_the_video_for_the_server(self):
        code, text = self.run_main(self.raw, '--data-dir', self.data)
        self.assertEqual(code, 0)
        self.assertTrue((self.raw / 'overview.html').is_file())
        key = overview_page.video_key(self.raw)
        self.assertEqual(overview_page.lookup(self.data, key)[0], self.raw.resolve())
        self.assertIn('overview.html', text)

    def test_a_missing_folder_is_an_error(self):
        code, text = self.run_main(self.raw / 'nope', '--data-dir', self.data)
        self.assertEqual(code, 2)
        self.assertIn('folder', text)

    def test_a_folder_without_a_scene_file_is_an_error_that_says_so(self):
        (self.raw / 'scenes.json').unlink()
        code, text = self.run_main(self.raw, '--data-dir', self.data)
        self.assertEqual(code, 2)
        self.assertIn('scenes.json', text)
        self.assertFalse((self.raw / 'overview.html').exists())

    def test_publish_registers_and_builds_in_one_step(self):
        path = overview_page.publish(self.raw, data_dir=self.data)
        self.assertEqual(path, self.raw / 'overview.html')
        self.assertIsNotNone(overview_page.lookup(self.data, overview_page.video_key(self.raw)))


if __name__ == '__main__':
    unittest.main()
