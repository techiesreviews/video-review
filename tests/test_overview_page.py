"""Checks for the overview page (scripts/video/overview_page.py). Small invented scene files and cue text only;
ffmpeg is replaced by a stand-in, so nothing here needs real footage or a media tool."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
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
        titles = re.findall(r'<h2 class="stitle">(.*?)</h2>', page)
        self.assertEqual(titles, ['01 · INTRO', '02 · WHAT IT REALLY COSTS', '03 · THE NEWS MOVES FAST',
                                  '04 · ROOM 1: WHAT CHANGED?', '05 · WHAT&#x27;S NEXT'])

    def test_each_scene_title_is_a_heading_one_level_under_the_pages(self):
        # A screen reader jumps from scene to scene by heading; the page's own heading is the only level one.
        page = render()
        self.assertEqual(len(re.findall(r'<h1[ >]', page)), 1)
        self.assertEqual(len(re.findall(r'<h2[ >]', page)), len(sample_store()['scenes']))
        for section in re.findall(r'<section class="scene".*?</section>', page, re.S):
            self.assertEqual(len(re.findall(r'<h2 class="stitle">', section)), 1)
        self.assertNotIn('<b class="stitle">', page)
        # A heading brings its own space above and below; the title keeps the look it had.
        self.assertIn('.stitle { font: 700 15px/1.2 system-ui; margin: 0; overflow-wrap: anywhere; }', page)

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
        # The gold take is already in the rough cut: its button keeps it there rather than putting it in.
        self.assertIn('data-take="3" data-on="true" data-starred="false"'
                      ' aria-label="Keep scene 2, take 3 in rough cut (star it)"', costs)
        self.assertIn('data-take="2" data-on="false" data-starred="false" aria-label="Put scene 2, take 2 in rough cut"',
                      costs)

    def test_every_stars_name_says_its_scene_its_take_and_what_it_does(self):
        # A screen reader's list of buttons shows only the names: each one says which recording it is about.
        stars = re.findall(r'<button class="st"[^>]*data-scene="([^"]*)" data-take="(\d+)"[^>]*aria-label="([^"]*)"',
                           render())
        self.assertEqual(stars, [(COSTS, '2', 'Take the star off scene 2, take 2'),
                                 (COSTS, '1', 'Put scene 2, take 1 in rough cut'),
                                 (COSTS, '3', 'Put scene 2, take 3 in rough cut'),
                                 ('04-room-1-what-changed', '1', 'Keep scene 4, take 1 in rough cut (star it)')])
        self.assertEqual(len({name for _, _, name in stars}), len(stars))

    def test_the_script_text_opens_from_the_keyboard_too(self):
        text = re.search(r'<p class="stext"[^>]*>', scene_html(render(), COSTS)).group(0)
        self.assertEqual(text, '<p class="stext" tabindex="0" role="button" aria-expanded="false">')

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

    def test_the_legend_names_the_pick_badge_as_the_computers_favorite(self):
        legend = re.search(r'<p class="legend">(.*?)</p>', render(), re.S).group(1)
        # A bare hollow star is also the star button of every archived take, so the legend names the badge.
        self.assertIn("☆ pick: the computer's favorite, when it differs.", legend)
        self.assertNotIn("☆ computer's favorite", legend)

    def test_a_changed_script_gets_a_chip_and_an_unchanged_one_does_not(self):
        page = render()
        self.assertIn('Script changed since recording', scene_html(page, '03-the-news-moves-fast'))
        self.assertNotIn('Script changed since recording', scene_html(page, COSTS))

    def test_a_heading_renamed_in_the_script_shows_the_chip_even_when_nothing_flagged_it(self):
        store = sample_store()
        scene_store.find_scene(store, '03-the-news-moves-fast')['script_changed'] = False
        scene_store.find_scene(store, COSTS)['script_changed'] = False
        cue = CUE.replace('WHAT IT REALLY COSTS', 'WHAT IT REALLY COSTS YOU')
        page = overview_page.render(store, 'Weekly update 03', cue_text=cue)
        self.assertIn('Script changed since recording', scene_html(page, COSTS))
        self.assertNotIn('Script changed since recording', scene_html(page, '01-intro'))
        self.assertNotIn('Script changed since recording', scene_html(page, '03-the-news-moves-fast'))

    def test_a_cue_file_without_numbered_sections_flags_nothing(self):
        store = sample_store()
        scene_store.find_scene(store, '03-the-news-moves-fast')['script_changed'] = False
        page = overview_page.render(store, 'Weekly update 03', cue_text='Just some notes.\n')
        self.assertNotIn('Script changed since recording', page)

    def test_an_older_take_in_front_because_the_newest_is_worse_says_so_beside_the_title(self):
        store = scene_store.new_store(scene_store.parse_cue_file(CUE))
        scene_store.add_take(store, COSTS, 1, duration=60.0, sound=CLEAN)
        scene_store.add_take(store, COSTS, 2, duration=40.0, sound=SHORT)
        scene_store.apply_best(store)
        costs = scene_html(render(store), COSTS)
        head = costs.split('<div class="row">')[0]
        self.assertIn(overview_page.OLDER_TAKE_TEXT, head)
        self.assertIn('data-take="1" data-on="true"', costs)
        self.assertNotIn(overview_page.OLDER_TAKE_TEXT, scene_html(render(), COSTS))  # a starred scene is not flagged

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


def css(page, selector, media=None):
    """The declarations of one rule in the page's own styles, as {property: value}. media: the rule sits in this
    @media block. tests/overview-browser.mjs measures the same rules in a real browser, by hand."""
    styles = re.search(r'<style>(.*?)</style>', page, re.S).group(1)
    if media:
        block = re.search(rf'@media {re.escape(media)} \{{((?:[^{{}}]*\{{[^{{}}]*\}})*)\s*\}}', styles)
        assert block, f'no @media {media} block'
        styles = block.group(1)
    rule = re.search(rf'(?:^|[}}\n])\s*{re.escape(selector)}\s*\{{([^}}]*)\}}', styles)
    assert rule, f'no rule for {selector}'
    return {name.strip(): value.strip()
            for name, value in (part.split(':', 1) for part in rule.group(1).split(';') if part.strip())}


def pixels(value):
    return float(re.fullmatch(r'(-?[\d.]+)px', value).group(1))


class NarrowScreens(unittest.TestCase):
    """A phone shows every scene, keeps the page as wide as the screen and gives a finger room to hit a star."""

    def test_a_strip_too_long_for_the_screen_scrolls_instead_of_hiding_its_last_scenes(self):
        page = render()
        strip = css(page, '.cut')
        self.assertEqual(strip.get('overflow-x'), 'auto')
        self.assertNotIn('overflow', strip, 'hidden overflow cuts the last scenes off')
        # The height sits on the segments, so a scroll bar goes under them instead of over them.
        self.assertNotIn('height', strip)
        self.assertEqual(css(page, '.cut div')['height'], '34px')
        # Scrolling from the keyboard needs the strip to take focus, and focus must show.
        self.assertIn('<div class="cut" tabindex="0" role="group" aria-label="Rough cut order">', page)
        self.assertRegex(page, r'\.cut:focus-visible[^{]*\{ outline:')

    def test_a_strip_segment_keeps_room_for_its_scene_number(self):
        # Two digits at 12 px need about 15 px; the approved sketch gave a segment 18 px.
        self.assertGreaterEqual(pixels(css(render(), '.cut div')['min-width']), 18)

    def test_a_three_digit_scene_number_gets_room_for_all_its_digits(self):
        # 100 scenes on a phone squeeze every segment to its smallest width, and "100" at 12 px is about 21 px
        # wide: more than the 18 px that holds two digits. 1ch is the width of one digit.
        scenes = [{'id': f'{n:04d}-scene', 'number': n, 'title': f'Scene {n}'} for n in (9, 99, 100, 1000)]
        store = scene_store.new_store(scenes)
        scene_store.add_take(store, '0099-scene', 1, duration=5.0)
        scene_store.add_take(store, '0100-scene', 1, duration=5.0)
        scene_store.apply_best(store)
        page = overview_page.render(store, 'Long video')
        block = re.search(r'<div class="cut".*?</div>\s*<p class="cutlabel"', page, re.S).group(0)
        styles = {int(number): style for style, number in re.findall(r'<div[^>]*? style="([^"]*)"[^>]*>(\d+)</div>', block)}
        self.assertEqual(sorted(styles), [9, 99, 100, 1000])
        # One and two digits keep the look they had: the 18 px every segment gets, and nothing of their own.
        for number in (9, 99):
            self.assertNotIn('min-width', styles[number], f'scene {number}')
        # Three digits and more get their own smallest width, recorded or not: every digit plus some air.
        for number, digits in ((100, 3), (1000, 4)):
            room = re.search(r'min-width:calc\((\d+)ch \+ (\d+)px\)', styles[number])
            self.assertTrue(room, f'scene {number} has no smallest width of its own: {styles[number]!r}')
            self.assertEqual(int(room.group(1)), digits, f'scene {number}')
            self.assertGreaterEqual(int(room.group(2)), 4, f'scene {number} has no air around its number')

    def test_a_long_unbroken_title_wraps_in_the_player_instead_of_widening_the_page(self):
        self.assertEqual(css(render(), '.player .t').get('overflow-wrap'), 'anywhere')

    def test_a_long_server_error_wraps_in_the_stars_line_instead_of_widening_the_page(self):
        # A scene-file error names the whole folder path, which has no spaces to break at.
        self.assertEqual(css(render(), '.off').get('overflow-wrap'), 'anywhere')

    def test_a_star_has_a_44_px_target_on_touch_and_keeps_its_look(self):
        page = render()
        star = css(page, '.st')
        self.assertEqual((star['width'], star['height']), ('26px', '26px'), 'the visible star stays as approved')
        # Only a star that can be used: while the stars are off, a touch beside one still plays the take.
        target = css(page, '.st:not(:disabled)::after', media='(any-pointer: coarse)')
        self.assertEqual((target.get('content'), target.get('position')), ("''", 'absolute'))
        self.assertGreaterEqual(pixels(star['width']) - 2 * pixels(target['inset']), 44)


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

    def test_the_line_waits_for_the_server_answer_instead_of_flashing_on_every_load(self):
        page = render(server='http://127.0.0.1:8879', video='weekly-1234')
        line = re.search(r'<p class="off" id="stars-off"[^>]*>(.*?)</p>', page)
        self.assertEqual(line.group(1), '', 'the line is empty until the server answered or did not')

    def test_the_line_is_read_out_by_a_screen_reader_when_it_changes(self):
        # A live region only speaks when it is on the page before its text changes: never hidden, empty instead.
        page = render(server='http://127.0.0.1:8879', video='weekly-1234')
        line = re.search(r'<p class="off" id="stars-off"[^>]*>', page).group(0)
        self.assertIn(' role="status"', line)
        self.assertNotIn('hidden', line)
        self.assertNotIn('display', css(page, '.off'))

    def test_the_page_knows_where_to_ask(self):
        page = render(server='http://127.0.0.1:8879', video='weekly-1234')
        self.assertIn('data-server="http://127.0.0.1:8879"', page)
        self.assertIn('data-video="weekly-1234"', page)


class ScriptText(unittest.TestCase):
    def test_text_is_collected_per_scene_without_directions_marks_or_links(self):
        cue = ('# Title\n### **PREP**\nnot a scene\n### **1 · A · 00:00**\n[Face full]\n**Bold** words.\n'
               'See [this page](https://example.com) now.\n\n### **HOOK**\nnot in scene 1\n### 2 · B\nTwo.\n')
        self.assertEqual(overview_page.scene_texts(cue), {'01-a': 'Bold words. See this page now.', '02-b': 'Two.'})

    def test_a_spoken_line_that_starts_with_a_link_keeps_its_words(self):
        # Second look on the release, 2026-11-16: the line was taken for a direction and left out whole.
        cue = '### 1 · A\n[This camera](https://example.com) records in 4K.\n'
        self.assertEqual(overview_page.scene_texts(cue), {'01-a': 'This camera records in 4K.'})

    def test_a_link_with_a_line_to_itself_is_left_out_like_a_direction(self):
        cue = '### 1 · A\nOne.\n[The survey: 300 responses.](https://example.com/survey)\nTwo.\n'
        self.assertEqual(overview_page.scene_texts(cue), {'01-a': 'One. Two.'})

    def test_a_direction_that_runs_over_two_lines_is_left_out_whole(self):
        # Second look on the release, 2026-11-16: the second line of the direction was shown as speech.
        cue = '### 1 · A\nOne.\n[Show the camera\nfrom the side.]\nTwo.\n### 2 · B\n[Face full]\nThree.\n'
        self.assertEqual(overview_page.scene_texts(cue), {'01-a': 'One. Two.', '02-b': 'Three.'})

    def test_a_direction_inside_a_spoken_line_is_left_out(self):
        cue = '### 1 · A\nLook here [point at the screen] and there.\n'
        self.assertEqual(overview_page.scene_texts(cue), {'01-a': 'Look here and there.'})

    def test_a_direction_with_a_link_inside_it_is_left_out_whole(self):
        # Review round 1: the link was read from the direction's own bracket, so its first words were shown.
        cue = '### 1 · A\nOne.\n[Show [the page](https://example.com) and zoom in]\nTwo.\n'
        self.assertEqual(overview_page.scene_texts(cue), {'01-a': 'One. Two.'})

    def test_a_link_alone_on_a_list_item_is_left_out_as_the_cutter_does(self):
        cue = '### 1 · A\nOne.\n- [The survey](https://example.com/survey)\nTwo.\n'
        self.assertEqual(overview_page.scene_texts(cue), {'01-a': 'One. Two.'})

    def test_links_are_read_as_the_cutter_reads_them(self):
        # Second look on PR #46: the page's own simpler link reader got these wrong, so it now uses the cutter's.
        texts = overview_page.scene_texts
        self.assertEqual(texts('### 1 · A\nOne.\n[The page](https://example.com/Guide_(2026))\nTwo.\n'), {'01-a': 'One. Two.'})
        self.assertEqual(texts('### 1 · A\nRead [the page](https://example.com/Guide_(2026)) first.\n'),
                         {'01-a': 'Read the page first.'})
        self.assertEqual(texts('### 1 · A\nRead [the [new] guide](docs/guide.md) first.\n'), {'01-a': 'Read the guide first.'})
        self.assertEqual(texts('### 1 · A\n[The [new] guide](docs/guide.md) is out.\n'), {'01-a': 'The guide is out.'})

    def test_an_image_is_left_out(self):
        cue = '### 1 · A\nOne.\n![chart](chart.png)\nSee ![chart](c.png) here.\n'
        self.assertEqual(overview_page.scene_texts(cue), {'01-a': 'One. See here.'})

    def test_a_line_that_opens_a_bracket_it_never_closes_stays_out(self):
        cue = '### 1 · A\n[Face full\nOne.\n'
        self.assertEqual(overview_page.scene_texts(cue), {'01-a': 'One.'})

    def test_a_bracket_that_is_never_closed_hides_no_later_line(self):
        # Second look on PR #46, round 2: the open bracket ran on to the next direction's closing one.
        texts = overview_page.scene_texts
        self.assertEqual(texts('### 1 · A\n[Face full\nOne.\n[Show the camera]\nTwo.\n'), {'01-a': 'One. Two.'})
        self.assertEqual(texts('### 1 · A\n[Face full\nOne.\nTwo [pause] three.\n'), {'01-a': 'One. Two three.'})
        self.assertEqual(texts('### 1 · A\nLook here [point at\nthe screen.\n[Face full]\nTwo.\n'),
                         {'01-a': 'Look here [point at the screen. Two.'})

    def test_a_direction_with_brackets_inside_it_is_left_out_whole(self):
        self.assertEqual(overview_page.scene_texts('### 1 · A\n[Show the [new] logo]\nOne.\n'), {'01-a': 'One.'})

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

    def test_listing_the_frames_that_are_there_makes_none_and_skips_an_empty_one(self):
        folder = self.raw / 'scenes' / COSTS
        (folder / 'take-01.jpg').write_bytes(b'')
        (folder / 'take-02.jpg').write_bytes(b'jpeg')
        with mock.patch.object(overview_page.subprocess, 'run') as ffmpeg:
            found = overview_page.existing_thumbnails(self.raw, self.store)
        self.assertEqual(found, {f'scenes/{COSTS}/take-02.jpg'})
        ffmpeg.assert_not_called()

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

    def test_a_page_is_never_built_from_a_scene_file_another_program_is_changing(self):
        import threading, time
        started = threading.Event()
        def other_program():
            with scene_store.locked(self.raw):
                started.set()
                time.sleep(0.4)
        thread = threading.Thread(target=other_program)
        thread.start()
        self.assertTrue(started.wait(5))
        begun = time.monotonic()
        overview_page.build(self.raw)
        thread.join()
        self.assertGreaterEqual(time.monotonic() - begun, 0.3)

    def test_an_older_rewrite_that_finishes_last_keeps_the_newest_takes_picture(self):
        folder = self.raw / 'scenes' / COSTS
        folder.mkdir(parents=True)
        (folder / 'take-01.mkv').write_bytes(b'x')
        newest = f'<img src="scenes/{COSTS}/take-04.jpg"'
        newer_pages = []

        def ffmpeg(command, **kwargs):
            if not newer_pages:
                # The older rewrite is still at its first picture: a take comes in and its own rewrite finishes first.
                newer_pages.append('')
                (folder / 'take-04.mkv').write_bytes(b'x')
                scene_store.update(self.raw, lambda store: scene_store.add_take(store, COSTS, 4, duration=70.0))
                newer_pages[0] = overview_page.build(self.raw).read_text(encoding='utf-8')
            Path(command[-1]).write_bytes(b'jpeg')
            return subprocess.CompletedProcess(command, 0, '', '')

        with mock.patch.object(overview_page.subprocess, 'run', side_effect=ffmpeg):
            page = overview_page.build(self.raw).read_text(encoding='utf-8')
        self.assertIn(newest, newer_pages[0], "the newer rewrite shows the new take's picture")
        self.assertIn(newest, page, "the older rewrite, written last, still shows the new take's picture")

    def test_the_page_and_thumbnail_temporary_names_differ_between_programs_and_threads(self):
        names = set()
        real_replace = os.replace
        def spy(source, target):
            names.add(Path(source).name)
            return real_replace(source, target)
        with mock.patch.object(overview_page.os, 'replace', side_effect=spy):
            overview_page.build(self.raw)
            overview_page.build(self.raw)
        self.assertEqual(len(names), 2)

    def test_a_cue_file_named_once_is_used_by_later_rebuilds_that_do_not_name_it(self):
        other = Path(self.temp.name) / 'cues.md'
        other.write_text('### 2 · WHAT IT REALLY COSTS\nOther words.\n', encoding='utf-8')
        overview_page.publish(self.raw, cue=other, data_dir=self.data)
        page = overview_page.publish(self.raw, data_dir=self.data).read_text(encoding='utf-8')
        self.assertIn('Other words.', page)
        self.assertNotIn('Two out of three', page)

    def test_the_server_address_comes_from_the_port_setting(self):
        with mock.patch.dict(os.environ, {'VIDEO_REVIEW_PORT': '9123'}):
            page = overview_page.build(self.raw).read_text(encoding='utf-8')
        self.assertIn('data-server="http://127.0.0.1:9123"', page)
        page = overview_page.build(self.raw).read_text(encoding='utf-8')
        self.assertIn('data-server="http://127.0.0.1:8879"', page)


class ServerAddress(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def test_the_port_the_server_wrote_comes_first(self):
        overview_page.remember_port(self.data, 8881)
        with mock.patch.dict(os.environ, {'VIDEO_REVIEW_PORT': '9123'}):
            self.assertEqual(overview_page.server_address(self.data), 'http://127.0.0.1:8881')

    def test_without_one_the_port_setting_then_the_usual_port_is_used(self):
        with mock.patch.dict(os.environ, {'VIDEO_REVIEW_PORT': '9123'}):
            self.assertEqual(overview_page.server_address(self.data), 'http://127.0.0.1:9123')
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('VIDEO_REVIEW_PORT', None)
            self.assertEqual(overview_page.server_address(self.data), 'http://127.0.0.1:8879')

    def test_an_unreadable_port_file_is_ignored(self):
        (self.data / overview_page.PORT_NAME).write_text('{nope', encoding='utf-8')
        with mock.patch.dict(os.environ, {'VIDEO_REVIEW_PORT': '9123'}):
            self.assertEqual(overview_page.server_address(self.data), 'http://127.0.0.1:9123')

    def test_publish_writes_the_port_the_server_wrote(self):
        raw = self.data / 'Weekly'
        raw.mkdir()
        scene_store.save(raw, sample_store())
        overview_page.remember_port(self.data, 8881)
        with mock.patch.object(overview_page.subprocess, 'run', side_effect=FileNotFoundError):
            page = overview_page.publish(raw, data_dir=self.data).read_text(encoding='utf-8')
        self.assertIn('data-server="http://127.0.0.1:8881"', page)


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

    def test_the_saved_page_carries_its_video_s_page_code_and_keeps_it(self):
        page = overview_page.publish(self.raw, data_dir=self.data).read_text(encoding='utf-8')
        key = overview_page.video_key(self.raw)
        code = overview_page.page_code(self.data, key)
        self.assertGreaterEqual(len(code), 32)
        self.assertIn(f'data-code="{code}"', page)
        overview_page.publish(self.raw, data_dir=self.data)
        self.assertEqual(overview_page.page_code(self.data, key), code)
        self.assertTrue(overview_page.code_matches(self.data, key, code))
        for wrong in (None, '', code[:-1], code + 'x', 'é' * 32, 42):
            self.assertFalse(overview_page.code_matches(self.data, key, wrong), wrong)
        self.assertFalse(overview_page.code_matches(self.data, 'nope-12345678', code))

    def test_the_saved_page_shows_why_the_server_refused_its_take_list(self):
        # A page that is out of date gets a 403 with a reason; the page must show that, not "start video-review".
        script = overview_page.PAGE_SCRIPT
        self.assertIn("r.json().then(j => Promise.reject(j.error)", script)
        self.assertIn("said => show(false, reason(said))", script)
        self.assertIn("'&code=' + encodeURIComponent(code)", script)
        self.assertIn('JSON.stringify({ video, code,', script)

    def test_two_videos_get_different_page_codes(self):
        other = Path(self.temp.name) / 'Other'
        other.mkdir()
        scene_store.save(other, sample_store())
        one = overview_page.page_code(self.data, overview_page.register(self.data, self.raw))
        two = overview_page.page_code(self.data, overview_page.register(self.data, other))
        self.assertNotEqual(one, two)

    def test_a_page_saved_before_page_codes_gets_one_when_the_server_starts(self):
        overview_page.publish(self.raw, data_dir=self.data)
        registry_path = self.data / overview_page.REGISTRY_NAME
        registry = json.loads(registry_path.read_text(encoding='utf-8'))
        key = overview_page.video_key(self.raw)
        del registry[key]['code']
        gone = Path(self.temp.name) / 'Gone'
        registry['gone-12345678'] = {'folder': str(gone), 'cue': None}
        registry_path.write_text(json.dumps(registry), encoding='utf-8')
        (self.raw / 'overview.html').write_text('old page without a code', encoding='utf-8')
        self.assertIsNone(overview_page.page_code(self.data, key))
        self.assertEqual(overview_page.give_old_pages_a_code(self.data), [key])
        code = overview_page.page_code(self.data, key)
        self.assertIn(f'data-code="{code}"', (self.raw / 'overview.html').read_text(encoding='utf-8'))
        self.assertIsNotNone(overview_page.page_code(self.data, 'gone-12345678'))
        self.assertFalse(gone.exists())
        self.assertEqual(overview_page.give_old_pages_a_code(self.data), [])  # once is enough
        self.assertEqual(overview_page.page_code(self.data, key), code)

    def test_a_folder_that_may_not_be_read_does_not_stop_the_old_pages_after_it_getting_their_code(self):
        # Sixth whole-file look, 2026-11-16: the codes are saved before the pages are written, so a page skipped
        # here is not looked at again on the next start and its stars would stay off.
        locked = Path(self.temp.name) / 'A locked folder'
        locked.mkdir()
        scene_store.save(locked, sample_store())
        overview_page.publish(locked, data_dir=self.data)  # registered first, so it is looked at first
        page = overview_page.publish(self.raw, data_dir=self.data)
        key = overview_page.video_key(self.raw)
        registry_path = self.data / overview_page.REGISTRY_NAME
        registry = json.loads(registry_path.read_text(encoding='utf-8'))
        for entry in registry.values():
            del entry['code']
        registry_path.write_text(json.dumps(registry), encoding='utf-8')
        page.write_text('old page without a code', encoding='utf-8')
        real_lookup = overview_page.lookup

        def lookup(data_dir, asked):
            if asked != key:
                raise PermissionError(13, 'Permission denied', str(locked))
            return real_lookup(data_dir, asked)
        with mock.patch.object(overview_page, 'lookup', side_effect=lookup):
            self.assertEqual(overview_page.give_old_pages_a_code(self.data), [key])
        self.assertIn(f'data-code="{overview_page.page_code(self.data, key)}"', page.read_text(encoding='utf-8'))

    def test_a_page_that_asks_another_port_is_written_again_when_the_server_starts(self):
        overview_page.remember_port(self.data, 8879)
        page = overview_page.publish(self.raw, data_dir=self.data)
        key = overview_page.video_key(self.raw)
        code = overview_page.page_code(self.data, key)
        self.assertEqual(overview_page.point_saved_pages_at_the_server(self.data), [])  # same port: left alone
        overview_page.remember_port(self.data, 8880)  # 8879 was taken, the server moved on
        self.assertEqual(overview_page.point_saved_pages_at_the_server(self.data), [key])
        text = page.read_text(encoding='utf-8')
        self.assertIn('<body data-server="http://127.0.0.1:8880"', text)
        self.assertIn(f'data-code="{code}"', text)  # the page keeps its code
        self.assertEqual(overview_page.point_saved_pages_at_the_server(self.data), [])  # once is enough

    def test_a_page_that_asks_the_right_port_is_not_touched(self):
        overview_page.remember_port(self.data, 8880)
        page = overview_page.publish(self.raw, data_dir=self.data)
        with mock.patch.object(overview_page, 'build', side_effect=AssertionError('rewritten')):
            self.assertEqual(overview_page.point_saved_pages_at_the_server(self.data), [])
        self.assertIn('<body data-server="http://127.0.0.1:8880"', page.read_text(encoding='utf-8'))

    def test_pointing_pages_at_the_server_skips_what_it_cannot_fix(self):
        overview_page.remember_port(self.data, 8879)
        overview_page.publish(self.raw, data_dir=self.data)
        key = overview_page.video_key(self.raw)
        other = Path(self.temp.name) / 'No page yet'
        other.mkdir()
        scene_store.save(other, sample_store())
        overview_page.register(self.data, other)
        registry_path = self.data / overview_page.REGISTRY_NAME
        registry = json.loads(registry_path.read_text(encoding='utf-8'))
        gone = Path(self.temp.name) / 'Gone'
        registry['gone-12345678'] = {'folder': str(gone), 'cue': None, 'code': 'x' * 32}
        registry['broken-12345678'] = 'not an entry'
        registry_path.write_text(json.dumps(registry), encoding='utf-8')
        overview_page.remember_port(self.data, 8880)
        self.assertEqual(overview_page.point_saved_pages_at_the_server(self.data), [key])
        self.assertFalse((other / 'overview.html').exists())  # a video without a saved page does not get one
        self.assertFalse(gone.exists())

    def test_a_folder_that_may_not_be_read_does_not_stop_the_pages_after_it(self):
        locked = Path(self.temp.name) / 'A locked folder'
        locked.mkdir()
        scene_store.save(locked, sample_store())
        overview_page.remember_port(self.data, 8879)
        overview_page.publish(locked, data_dir=self.data)  # registered first, so it is looked at first
        page = overview_page.publish(self.raw, data_dir=self.data)
        key = overview_page.video_key(self.raw)
        code = overview_page.page_code(self.data, key)
        overview_page.remember_port(self.data, 8880)
        real_lookup = overview_page.lookup

        def lookup(data_dir, asked):
            # Before Python 3.14 the check for the scene file raises for a folder the user may not read.
            if asked != key:
                raise PermissionError(13, 'Permission denied', str(locked))
            return real_lookup(data_dir, asked)
        with mock.patch.object(overview_page, 'lookup', side_effect=lookup):
            self.assertEqual(overview_page.point_saved_pages_at_the_server(self.data), [key])
        text = page.read_text(encoding='utf-8')
        self.assertIn('<body data-server="http://127.0.0.1:8880"', text)
        self.assertIn(f'data-code="{code}"', text)

    def test_a_scene_title_cannot_pass_for_the_page_address(self):
        store = sample_store()
        store['scenes'][0]['title'] = '<body data-server="http://127.0.0.1:8880"'
        scene_store.save(self.raw, store)
        overview_page.remember_port(self.data, 8879)
        page = overview_page.publish(self.raw, data_dir=self.data)
        overview_page.remember_port(self.data, 8880)
        self.assertEqual(overview_page.point_saved_pages_at_the_server(self.data), [overview_page.video_key(self.raw)])
        self.assertIn('<body data-server="http://127.0.0.1:8880"', page.read_text(encoding='utf-8'))


class SavedPageScript(unittest.TestCase):
    """The saved page's own script, run in Node with a fake page (skipped without Node)."""
    def run_page_script(self, answer, click=False, later=(200, '{}'), carried=None, starred=False, storage=True,
                        hold=False, place=None, away=False, pointer=False, keeps=False):
        """Run the saved page's script in Node with a fake page and a fake fetch; return what the stars line (the
        page's live region, empty when there is nothing to say) says, whether the stars are on, how often the page
        loaded itself again and what it kept for after that. answer: what fetch does ('network', or [status, body
        text]): for the take list when the page opens, or with click=True for the star click (the take list then
        answers 200 first and `later` when it is asked again). carried: what an earlier load of the page kept.
        starred: the one star on the fake page (scene 01-intro, take 2) shows as starred and in the rough cut.
        storage=False: the browser gives the page nothing to keep things in. hold=True: the star click gets no
        answer yet, and the other star on the page (take 1) is clicked while it waits. place: the scene an earlier
        load kept for the focus. away=True: the user moves the focus to something else while the star is saved;
        away='open': while the page opens (before the take list answers). keeps=True: the browser leaves the focus
        on a button that is switched off, as Firefox does (the fake page otherwise drops it, as Chrome does).
        pointer=True: the star is clicked with a mouse or a finger, not from the keyboard. 'focus' in the result
        names what has the focus at the end ('star 2', 'play 2', 'elsewhere' or 'page' for nothing), 'ring' whether
        the page let its focus ring show, 'place' what the page kept for the focus, 'order' what happened in turn
        after the page opened or the star was clicked ('focus' for a move of the focus, 'say' for a line put in
        the live region). A click on a star puts the focus on it."""
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not installed')
        harness = textwrap.dedent('''
            const { answer, later, click, carried, starred, storage, usual, stale, hold, place, away, pointer, keeps } = JSON.parse(process.argv[1]);
            let order = [], line = '';
            const off = { dataset: { text: usual, stale }, get textContent() { return line; },
              set textContent(text) { line = text; if (text) order.push('say'); } };
            const page = { name: 'page', dataset: { server: 'http://127.0.0.1:8879', video: 'v-1', code: 'the-code' } };
            const elsewhere = { name: 'elsewhere' };
            let focused = page, ring = null;
            const take = (what, how) => { focused = what; ring = !(how && how.focusVisible === false); order.push('focus'); };
            const button = (number, on) => {
              const play = { name: 'play ' + number, focus: how => take(play, how) };
              let isOff = true;
              const made = { name: 'star ' + number, focus: how => { if (!isOff) take(made, how); },
                get disabled() { return isOff; },
                set disabled(value) { isOff = value; if (isOff && focused === made && !keeps) focused = page; },
                closest: asked => asked === '.take' ? { querySelector: what => what === '.thumb' ? play : null } : null,
                dataset: { scene: '01-intro', take: String(number), starred: String(on), on: String(on) } };
              return made;
            };
            const star = button(2, starred), other = button(1, false);
            globalThis.document = {
              body: page,
              get activeElement() { return focused; },
              querySelectorAll: () => [star, other],
              getElementById: () => off,
              addEventListener: (type, handler) => { if (type === 'click') globalThis.onClick = handler; },
            };
            const player = { querySelector: () => ({ addEventListener: () => {} }) };
            const realGet = document.getElementById;
            document.getElementById = id => id === 'player' ? player : realGet(id);
            const kept = new Map(carried === null ? [] : [['overview-said:v-1', carried]]);
            if (place !== null) kept.set('overview-focus:v-1', place);
            const broken = () => { throw new Error('no storage'); };
            globalThis.sessionStorage = storage
              ? { getItem: key => kept.get(key) ?? null, setItem: (key, value) => { kept.set(key, String(value)); }, removeItem: key => { kept.delete(key); } }
              : { getItem: broken, setItem: broken, removeItem: broken };
            let asked = '', lists = 0, reloads = 0;
            const sends = [];
            globalThis.location = { reload: () => { reloads += 1; } };
            const reply = said => {
              if (said === 'network') return Promise.reject(new TypeError('Failed to fetch'));
              const [status, text] = said;
              return Promise.resolve({ ok: status < 300, json: () => Promise.resolve().then(() => JSON.parse(text)) });
            };
            globalThis.fetch = (url, options) => {
              if (options) {
                sends.push(JSON.parse(options.body));
                if (away === true) focused = elsewhere;
                return hold ? new Promise(() => {}) : reply(answer);
              }
              asked = url; lists += 1;
              if (away === 'open' && lists === 1) focused = elsewhere;
              return reply(!click ? answer : lists === 1 ? [200, '{}'] : later);
            };
            ''') + overview_page.PAGE_SCRIPT.split('<script>')[1].split('</script>')[0] + textwrap.dedent('''
            // A click from the keyboard has detail 0; a mouse or a finger counts its clicks from 1.
            const tap = which => {
              which.focus(); ring = null; order = [];
              globalThis.onClick({ detail: pointer ? 1 : 0, target: { closest: s => s === '.st' ? which : null } });
            };
            if (click) setTimeout(() => tap(star), 20);
            if (hold) setTimeout(() => tap(other), 40);
            setTimeout(() => { console.log(JSON.stringify({ said: off.textContent, on: !star.disabled, asked, reloads,
              sent: sends.at(-1) ?? null, sends, off: [star, other].filter(s => s.disabled).length,
              kept: kept.get('overview-said:v-1') ?? null, place: kept.get('overview-focus:v-1') ?? null,
              focus: document.activeElement.name, ring, order })); process.exit(0); }, 90);
            ''')
        setup = {'answer': answer, 'later': later, 'click': click, 'carried': carried, 'starred': starred,
                 'storage': storage, 'usual': overview_page.STARS_OFF_TEXT, 'stale': overview_page.STALE_PAGE_TEXT,
                 'hold': hold, 'place': place, 'away': away, 'pointer': pointer, 'keeps': keeps}
        done = subprocess.run([node, '-e', harness, json.dumps(setup)], capture_output=True, text=True, timeout=30,
                              encoding='utf-8')
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    PROBLEM = 'The star is saved, but the rough cut could not be rebuilt: no cue file.'
    # What the server has saved once the star on the fake page (scene 01-intro, take 2) is set.
    SAVED = json.dumps({'scenes': [{'id': '01-intro', 'star': 2, 'best': 2}]})

    @staticmethod
    def place(scene='01-intro', quiet=False):
        """What the page keeps over a reload for the focus: the clicked star's scene, and whether the star was
        pressed with a mouse or a finger (quiet: no focus ring)."""
        return json.dumps({'scene': scene, 'quiet': quiet})

    def test_a_star_click_sends_the_code_and_shows_a_refusal(self):
        seen = self.run_page_script([200, '{}'], click=True)
        self.assertEqual(seen['sent'], {'video': 'v-1', 'code': 'the-code', 'scene': '01-intro', 'take': 2})
        self.assertEqual((seen['reloads'], seen['kept']), (1, None))
        seen = self.run_page_script([403, json.dumps({'error': overview_page.STALE_PAGE_TEXT})], click=True)
        self.assertEqual((seen['said'], seen['on'], seen['reloads']), (overview_page.STALE_PAGE_TEXT, True, 0))
        seen = self.run_page_script('network', click=True)
        self.assertEqual((seen['said'], seen['on']), (overview_page.STARS_OFF_TEXT, False))

    def test_every_star_is_off_while_one_is_being_saved(self):
        # Fourth whole-file look, 2026-11-16: two quick clicks on different takes sent two saves that could land in
        # the wrong order. The click handler switches every star off before it sends anything.
        script = overview_page.PAGE_SCRIPT
        click = script[script.index("const star = e.target.closest('.st');"):script.index("fetch(server + '/api/scenes/star'")]
        self.assertIn('stars.forEach(s => { s.disabled = true; });', click)
        self.assertNotIn('star.disabled = true;', click)
        # Run for real: the first save gets no answer yet and the other star is clicked. Nothing more is sent.
        seen = self.run_page_script([200, '{}'], click=True, hold=True)
        self.assertEqual(seen['sends'], [{'video': 'v-1', 'code': 'the-code', 'scene': '01-intro', 'take': 2}])
        self.assertEqual((seen['off'], seen['reloads']), (2, 0))

    def test_a_failed_star_click_with_an_odd_answer_shows_the_usual_line_and_keeps_the_stars(self):
        for answer in ([500, json.dumps({'error': {'x': 1}})], [500, '{"error": 7}'], [500, 'null'], [500, 'not json']):
            seen = self.run_page_script(answer, click=True)
            self.assertEqual((seen['said'], seen['on'], seen['reloads']), (overview_page.STARS_OFF_TEXT, True, 0),
                             answer)

    def test_a_star_that_was_saved_although_the_server_reports_a_problem_loads_the_page_again(self):
        # The server saves the star first; when the rough cut or the page cannot follow it answers 500. The page
        # then shows the old star, front take, strip and total: it loads itself again to show the saved ones and
        # keeps what the server said for after that.
        seen = self.run_page_script([500, json.dumps({'error': self.PROBLEM})], click=True, later=[200, self.SAVED])
        self.assertEqual((seen['reloads'], seen['kept'], seen['on']), (1, self.PROBLEM, False))

    def test_after_loading_again_the_page_says_what_the_server_said(self):
        seen = self.run_page_script([200, self.SAVED], carried=self.PROBLEM, starred=True)
        self.assertEqual((seen['said'], seen['on'], seen['kept']), (self.PROBLEM, True, None))
        # Once: the next load says nothing.
        seen = self.run_page_script([200, self.SAVED], starred=True)
        self.assertEqual((seen['said'], seen['on']), ('', True))

    def test_a_page_that_shows_other_stars_than_the_saved_ones_says_so_and_turns_its_stars_off(self):
        # The page could not be written again (so loading it again changed nothing): a click would switch the
        # wrong way, and the front take, strip and total are old too.
        seen = self.run_page_script([200, self.SAVED])
        self.assertEqual((seen['said'], seen['on']), (overview_page.STALE_PAGE_TEXT, False))
        problem = 'The star is saved, but the page could not be rewritten: no room.'
        seen = self.run_page_script([200, self.SAVED], carried=problem)
        self.assertEqual((seen['said'], seen['on']), (f'{problem} {overview_page.STALE_PAGE_TEXT}', False))
        # A scene file from before "best" was saved says nothing about the rough cut: only the star is compared.
        seen = self.run_page_script([200, json.dumps({'scenes': [{'id': '01-intro', 'star': None}]})])
        self.assertEqual((seen['said'], seen['on']), ('', True))
        seen = self.run_page_script([200, json.dumps({'scenes': [{'id': '09-other', 'star': 2, 'best': 2}]})])
        self.assertEqual((seen['said'], seen['on']), ('', True))

    def test_a_failed_star_click_leaves_the_stars_off_when_the_page_cannot_show_the_saved_stars(self):
        # The saved stars cannot be read, or the browser keeps nothing over a reload: the line says what the server
        # said and the stars stay off rather than switch the wrong way.
        answer = [500, json.dumps({'error': self.PROBLEM})]
        for later in ('network', [409, '{"error": "The scene file is not valid."}']):
            seen = self.run_page_script(answer, click=True, later=later)
            self.assertEqual((seen['said'], seen['on'], seen['reloads']), (self.PROBLEM, False, 0), later)
        seen = self.run_page_script(answer, click=True, later=[200, self.SAVED], storage=False)
        self.assertEqual((seen['said'], seen['on'], seen['reloads']), (self.PROBLEM, False, 0))
        # Without storage the page still opens and its stars still work.
        seen = self.run_page_script([200, self.SAVED], starred=True, storage=False)
        self.assertEqual((seen['said'], seen['on']), ('', True))

    def test_a_refused_star_click_gives_the_focus_back_to_the_clicked_star(self):
        # Release review R1-1, 2026-11-16: every star is off while one is saved, and a button that is switched off
        # drops the focus. A keyboard or screen-reader user must be back on the star once the stars are on again.
        for status in (400, 409):
            seen = self.run_page_script([status, '{"error": "The scene file is not valid."}'], click=True)
            self.assertEqual((seen['focus'], seen['on'], seen['reloads']), ('star 2', True, 0), status)
            self.assertTrue(seen['ring'])
        # Not when the user went somewhere else in the meantime.
        seen = self.run_page_script([409, '{"error": "The scene file is not valid."}'], click=True, away=True)
        self.assertEqual((seen['focus'], seen['on']), ('elsewhere', True))
        # A browser that leaves the focus on the star while it is off: the star still has it afterwards.
        seen = self.run_page_script([409, '{"error": "The scene file is not valid."}'], click=True, keeps=True)
        self.assertEqual((seen['focus'], seen['on']), ('star 2', True))

    def test_the_focus_moves_before_the_line_is_said(self):
        # Review of the focus fix, 2026-11-16: a screen reader may stop what it is saying when the focus moves, so
        # a line put in the live region just before the move can be cut off. The move comes first, on every path.
        problem = [500, json.dumps({'error': self.PROBLEM})]
        for name, seen in (
                ('refused', self.run_page_script([409, '{"error": "The scene file is not valid."}'], click=True)),
                ('no answer', self.run_page_script('network', click=True)),
                ('saved stars not read', self.run_page_script(problem, click=True, later='network')),
                ('nothing kept', self.run_page_script(problem, click=True, later=[200, self.SAVED], storage=False)),
                ('loaded again', self.run_page_script([200, self.SAVED], place=self.place(), carried=self.PROBLEM,
                                                      starred=True)),
                ('stars off', self.run_page_script('network', place=self.place(), starred=True))):
            self.assertEqual(seen['order'], ['focus', 'say'], name)
            self.assertTrue(seen['said'], name)

    def test_a_star_click_that_leaves_the_stars_off_puts_the_focus_on_the_take_s_play_button(self):
        # A star that stays off cannot hold the focus: the take's play button keeps the user's place.
        seen = self.run_page_script('network', click=True)
        self.assertEqual((seen['focus'], seen['on']), ('play 2', False))
        answer = [500, json.dumps({'error': self.PROBLEM})]
        seen = self.run_page_script(answer, click=True, later='network')
        self.assertEqual((seen['focus'], seen['on'], seen['reloads']), ('play 2', False, 0))
        seen = self.run_page_script(answer, click=True, later=[200, self.SAVED], storage=False)
        self.assertEqual((seen['focus'], seen['on'], seen['reloads']), ('play 2', False, 0))
        seen = self.run_page_script('network', click=True, away=True)
        self.assertEqual(seen['focus'], 'elsewhere')
        seen = self.run_page_script('network', click=True, keeps=True)
        self.assertEqual((seen['focus'], seen['on']), ('play 2', False))

    def test_a_star_pressed_with_a_mouse_or_a_finger_gets_the_focus_back_without_its_ring(self):
        # A button clicked with a mouse shows no focus ring, so giving the focus back must not make one appear:
        # the page looks the same as before to someone who does not use the keyboard.
        seen = self.run_page_script([409, '{"error": "The scene file is not valid."}'], click=True, pointer=True)
        self.assertEqual((seen['focus'], seen['ring']), ('star 2', False))
        seen = self.run_page_script('network', click=True, pointer=True)
        self.assertEqual((seen['focus'], seen['ring']), ('play 2', False))
        seen = self.run_page_script([200, '{}'], click=True, pointer=True)
        self.assertEqual((seen['reloads'], json.loads(seen['place'])), (1, {'scene': '01-intro', 'quiet': True}))
        seen = self.run_page_script([200, self.SAVED], place=self.place(quiet=True), starred=True)
        self.assertEqual((seen['focus'], seen['ring'], seen['place']), ('star 2', False, None))

    def test_a_star_click_keeps_its_scene_over_the_reload(self):
        # Release review R1-1: a reload puts the focus back at the top of the page. The page keeps the scene of the
        # clicked star, in this tab only, for the load that follows.
        seen = self.run_page_script([200, '{}'], click=True)
        self.assertEqual((seen['reloads'], json.loads(seen['place'])), (1, {'scene': '01-intro', 'quiet': False}))
        seen = self.run_page_script([500, json.dumps({'error': self.PROBLEM})], click=True, later=[200, self.SAVED])
        self.assertEqual((seen['reloads'], json.loads(seen['place']), seen['kept']),
                         (1, {'scene': '01-intro', 'quiet': False}, self.PROBLEM))
        # A browser that keeps nothing still loads the page again after a saved star.
        seen = self.run_page_script([200, '{}'], click=True, storage=False)
        self.assertEqual(seen['reloads'], 1)

    def test_after_the_reload_the_focus_is_on_the_star_of_the_scene_s_take_in_front(self):
        seen = self.run_page_script([200, self.SAVED], place=self.place(), starred=True)
        self.assertEqual((seen['focus'], seen['ring'], seen['on'], seen['place']), ('star 2', True, True, None))
        # Once, and only for a scene that is on the page with a take in front.
        seen = self.run_page_script([200, self.SAVED], starred=True)
        self.assertEqual(seen['focus'], 'page')
        seen = self.run_page_script([200, self.SAVED], place=self.place('09-other'), starred=True)
        self.assertEqual((seen['focus'], seen['place']), ('page', None))
        # Not when the user has already put the focus somewhere else while the page opened.
        seen = self.run_page_script([200, self.SAVED], place=self.place(), starred=True, away='open')
        self.assertEqual((seen['focus'], seen['place']), ('elsewhere', None))
        # Something else under that name does not stop the page, and is cleared away.
        for odd in ('not json', '7', 'null', '"01-intro"', '{}'):
            seen = self.run_page_script([200, self.SAVED], place=odd, starred=True)
            self.assertEqual((seen['focus'], seen['on'], seen['place']), ('page', True, None), odd)
        # With the stars off (no server, or a page that is out of date) the take's play button gets it.
        seen = self.run_page_script('network', place=self.place(), starred=True)
        self.assertEqual((seen['focus'], seen['on']), ('play 2', False))
        stale = json.dumps({'scenes': [{'id': '01-intro', 'star': 1, 'best': 1}]})
        seen = self.run_page_script([200, stale], place=self.place(), starred=True)
        self.assertEqual((seen['focus'], seen['on'], seen['said']), ('play 2', False, overview_page.STALE_PAGE_TEXT))

    def test_the_saved_page_shows_the_server_s_reason_or_the_usual_line(self):
        seen = self.run_page_script([200, '{"scenes": []}'])
        self.assertEqual((seen['on'], seen['said']), (True, ''))
        self.assertTrue(seen['asked'].endswith('/api/scenes?video=v-1&code=the-code'), seen['asked'])
        seen = self.run_page_script([403, json.dumps({'error': overview_page.STALE_PAGE_TEXT})])
        self.assertEqual((seen['on'], seen['said']), (False, overview_page.STALE_PAGE_TEXT))
        for answer in ('network', [403, 'not json'], [500, 'null'], [500, '{"error": 7}']):
            seen = self.run_page_script(answer)
            self.assertEqual((seen['on'], seen['said']), (False, overview_page.STARS_OFF_TEXT), answer)


if __name__ == '__main__':
    unittest.main()
