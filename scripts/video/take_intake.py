#!/usr/bin/env python3
"""Take intake: fill a video's scene file from the take files that sit in its scene folders.

    python scripts/video/take_intake.py <video folder>

The video folder is the one that holds `scenes/<scene id>/take-NN.mkv` and `scenes.json` (see scene_store.py).
Every take file that is not yet in `scenes.json` is added with its length, the time it was recorded and three free
sound checks. Takes that are already in the file are left alone, except that when new takes of a scene arrive the
length check of that scene's older takes is refreshed (it compares the takes with each other). A file that is still being written, or that
ffmpeg cannot read yet, is skipped and picked up on the next run.

The sound checks use only ffmpeg and ffprobe on this computer: no transcript, no paid service, no network.
They are saved with each take under `sound` (names as in scene_store.py):

  ends_early   check 1 failed: the take does not end in a short stretch of silence (it stops mid-word),
  length_off   check 2 failed: the length is not close to the other takes of the same scene,
  long_pause   check 3 failed: a long silence in the middle of the take.

The computer's favorite of a scene is the take that passes the most checks, the newest on a tie. It is saved as
`favorite`, never as `star` (that is Lex's), and a scene with one take has none.

All writes go through scene_store.update(); the slow work (ffprobe, ffmpeg) is done before it, so the lock is held
only while results are copied into the file.

After every run the command rewrites the video's overview page (overview_page.py) and registers the video with
video-review's data folder, so the page's stars can reach the server. Give --data-dir the same folder the server is
started with (it defaults to VIDEO_REVIEW_DATA_DIR, else the usual one), or the server will not find the video.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import overview_page  # noqa: E402
import scene_store  # noqa: E402

# Thresholds. Each has its reason; change them here, nowhere else.

# How long a file must hold the same size and modified time before it counts as finished. OBS writes a recording
# in steady chunks, so two looks this far apart catch a take that is still running.
SETTLE_SECONDS = 2.0
# Take files this program registers. The name is always take-NN, so only the ending varies; OBS can save .mkv,
# .mp4 or .mov.
MEDIA_EXTENSIONS = ('.mkv', '.mp4', '.mov')
# What counts as silence: quieter than this for at least SILENCE_MIN_SECONDS. A desk microphone's room noise sits
# far below speech; -35 dB is above room noise and well below a voice, and the first real recording will show if
# it needs moving.
SILENCE_FLOOR_DB = -35
SILENCE_MIN_SECONDS = 0.3
# Check 1: the take must end in silence at least this long ("a short stretch", not a half-second click) ...
END_SILENCE_MIN_SECONDS = 0.3
# ... and that silence must reach the end of the file within this slack, because sound and picture rarely stop on
# the same instant.
END_SLACK_SECONDS = 0.25
# Check 2: a take is "off" when its length differs from the middle length of the other takes of the scene by more
# than this share. Retakes of one scene read the same words, so a take far shorter or longer was cut short or ran
# on; 30% leaves room for a slower read.
LENGTH_SHARE = 0.30
# Check 3: a silence longer than this, strictly inside the take, is a long pause. Natural breaths and beats stay
# under it; a stall does not.
LONG_PAUSE_SECONDS = 2.5
# Check 3 ignores silences that touch the first or last seconds: waiting to start and waiting to stop are not
# pauses in the middle (the end is check 1's job).
EDGE_SECONDS = 2.0


class ToolMissing(Exception):
    """ffmpeg or ffprobe is not installed (or not on the path)."""


# The three checks ----------------------------------------------------------------------------------------------
# silences is a list of (start, end) in seconds, as detect_silence gives it.

def ends_early(duration, silences):
    """Check 1 failed: the take does not finish in a short stretch of silence."""
    return not any(end >= duration - END_SLACK_SECONDS and end - start >= END_SILENCE_MIN_SECONDS
                   for start, end in silences)


def long_pause(duration, silences):
    """Check 3 failed: a silence longer than LONG_PAUSE_SECONDS that starts after the first EDGE_SECONDS and ends
    before the last EDGE_SECONDS."""
    return any(start >= EDGE_SECONDS and end <= duration - EDGE_SECONDS and end - start > LONG_PAUSE_SECONDS
               for start, end in silences)


def length_off(duration, other_durations):
    """Check 2 failed: length differs from the middle length of the other takes by more than LENGTH_SHARE.
    None when there is no other take to compare with (the check did not run)."""
    if not other_durations:
        return None
    middle = statistics.median(other_durations)
    return abs(duration - middle) > LENGTH_SHARE * middle


def passed_checks(take):
    """How many of the three checks the take passed. A check that did not run is not a pass."""
    sound = take.get('sound')
    if not isinstance(sound, dict):
        return 0
    return sum(1 for flag in ('ends_early', 'length_off', 'long_pause') if sound.get(flag) is False)


def pick_favorite(entry):
    """The favorite take number of one scene dict: most checks passed, newest on a tie; None for a scene with
    fewer than two takes or with no sound data."""
    takes = [t for t in entry['takes'] if isinstance(t.get('sound'), dict)]
    if len(entry['takes']) < 2 or not takes:
        return None
    return max(takes, key=lambda t: (passed_checks(t), t['number']))['number']


# The media tools -----------------------------------------------------------------------------------------------

def _run(command, tool):
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    except FileNotFoundError:
        raise ToolMissing(f'{tool} is not installed (or not on the path); the sound checks need it.') from None


def probe_media(path):
    """(length in seconds, has sound) from ffprobe, or None when the file cannot be read (yet)."""
    result = _run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type',
                   '-of', 'json', str(path)], 'ffprobe')
    if result.returncode != 0:
        return None
    try:
        info = json.loads(result.stdout)
        duration = float(info['format']['duration'])
    except (ValueError, KeyError, TypeError):
        return None
    if not duration > 0:
        return None
    has_audio = any(s.get('codec_type') == 'audio' for s in info.get('streams') or [])
    return duration, has_audio


# A number as ffmpeg prints it: older versions write a very small one like 2.26757e-05.
NUMBER = r'-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?'


def parse_silence(text, duration):
    """(start, end) pairs from ffmpeg's silencedetect lines. A silence still open at the end runs to the end."""
    silences, start = [], None
    for line in text.splitlines():
        found = re.search(rf'silence_start: ({NUMBER})', line)
        if found:
            start = max(0.0, float(found.group(1)))
            continue
        found = re.search(rf'silence_end: ({NUMBER})', line)
        if found and start is not None:
            silences.append((start, float(found.group(1))))
            start = None
    if start is not None:
        silences.append((start, duration))
    return silences


def detect_silence(path, duration):
    """Silent stretches of the take's first sound track, or None when ffmpeg could not read it.

    Only the first sound track is listened to: OBS puts the full mix there. The picture is not decoded."""
    result = _run(['ffmpeg', '-hide_banner', '-nostats', '-nostdin', '-i', str(path), '-vn', '-map', '0:a:0',
                   '-af', f'silencedetect=noise={SILENCE_FLOOR_DB}dB:d={SILENCE_MIN_SECONDS}',
                   '-f', 'null', '-'], 'ffmpeg')
    if result.returncode != 0:
        return None
    return parse_silence(result.stderr, duration)


# Finding new takes ---------------------------------------------------------------------------------------------

def find_new_takes(raw_dir, store):
    """Take files in the scene folders that are not in the scene file yet.

    Returns (candidates, unknown_folders): candidates are (scene id, take number, extension, path) in scene and
    take order. Folders that are not in the scene list are reported and skipped; files whose names are not
    exactly take-NN plus a media extension are ignored."""
    known = {scene['id']: {t['number'] for t in scene['takes']} for scene in store['scenes']}
    root = Path(raw_dir) / 'scenes'
    candidates, unknown = [], []
    if not root.is_dir():
        return candidates, unknown
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        files = []
        for path in sorted(folder.iterdir()):
            parsed = scene_store.parse_take_file_name(path.name)
            if not parsed or not path.is_file() or parsed[1].lower() not in MEDIA_EXTENSIONS:
                continue
            if scene_store.take_file_name(*parsed) != path.name:
                continue  # take-3.mkv: not the name the scene file would give it
            files.append((parsed[0], parsed[1], path))
        if not files:
            continue
        if folder.name not in known:
            unknown.append(folder.name)
            continue
        seen = set()
        for number, extension, path in files:
            if number in known[folder.name] or number in seen:
                continue  # already registered, or the same take saved in two formats
            seen.add(number)
            candidates.append((folder.name, number, extension, path))
    return candidates, unknown


def _fingerprint(path):
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


def settle(candidates, seconds, sleep):
    """Split candidates into (steady, growing, prints): a file whose size or modified time changed after the wait,
    that vanished, or that is still empty is growing. One wait covers all files. prints holds each steady file's
    size and modified time, so run() can check again after the slow media work that nothing changed meanwhile."""
    if not candidates:
        return [], [], {}
    before = {c[3]: _fingerprint(c[3]) for c in candidates}
    sleep(seconds)
    steady, growing, prints = [], [], {}
    for candidate in candidates:
        now = _fingerprint(candidate[3])
        if now and now == before[candidate[3]] and now[0] > 0:
            steady.append(candidate)
            prints[candidate[3]] = now
        else:
            growing.append(candidate)
    return steady, growing, prints


def recorded_at(path):
    """When the file was created, as UTC text: its birth time where Python gives one (macOS, Windows with Python
    3.12 or newer), its creation time on older Python on Windows (st_ctime means creation there), and elsewhere,
    where no creation time exists, the time it was last written."""
    info = path.stat()
    seconds = getattr(info, 'st_birthtime', None)
    if not seconds:
        seconds = info.st_ctime if os.name == 'nt' else info.st_mtime
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# The run -------------------------------------------------------------------------------------------------------

def analyse(scene, number, extension, path):
    """Everything about one take that needs the media tools, or None when ffmpeg cannot read it yet.

    The result has duration, recorded_at and sound (ends_early and long_pause; None when the file has no sound
    track). The length check needs the other takes, so it is added under the lock."""
    probed = probe_media(path)
    if probed is None:
        return None
    duration, has_audio = probed
    sound = None
    if has_audio:
        silences = detect_silence(path, duration)
        if silences is None:
            return None
        sound = {'ends_early': ends_early(duration, silences), 'long_pause': long_pause(duration, silences)}
    try:
        created = recorded_at(path)
    except OSError:
        return None  # the file vanished or was renamed meanwhile
    return {'scene': scene, 'number': number, 'extension': extension, 'duration': round(duration, 3),
            'recorded_at': created, 'sound': sound}


def _refresh_scene(store, scene):
    """After new takes arrive: refresh the length check of every take that has sound data (the middle length of
    the others changed) and the computer's favorite. Nothing else of an older take is touched."""
    entry = scene_store.find_scene(store, scene)
    for take in entry['takes']:
        sound = take.get('sound')
        if not isinstance(sound, dict) or not isinstance(take.get('duration'), (int, float)):
            continue
        others = [t['duration'] for t in entry['takes']
                  if t is not take and isinstance(t.get('duration'), (int, float))]
        flag = length_off(take['duration'], others)
        if flag is not None:
            sound['length_off'] = flag
    scene_store.set_favorite(store, scene, pick_favorite(entry))


def run(raw_dir, settle_seconds=None, sleep=time.sleep):
    """Register the new takes of one video folder. Returns a report of what happened:

    added (scene, number), growing (still being written), unreadable (ffmpeg could not read them yet) and
    unknown_folders (scene folders that are not in the scene list)."""
    store = scene_store.load(raw_dir)
    if store is None:
        raise scene_store.SceneFileError(
            f'No {scene_store.FILE_NAME} in {raw_dir}: the scene list is saved when the first take is recorded.')
    candidates, unknown = find_new_takes(raw_dir, store)
    steady, growing, prints = settle(candidates, SETTLE_SECONDS if settle_seconds is None else settle_seconds, sleep)
    report = {'added': [], 'growing': [(c[0], c[1]) for c in growing], 'unreadable': [],
              'unknown_folders': unknown, 'details': {}}
    results = []
    for scene, number, extension, path in steady:
        found = analyse(scene, number, extension, path)
        if found is None:
            report['unreadable'].append((scene, number))
        else:
            found['path'] = path
            results.append(found)
    if not results:
        return report

    def change(current):
        touched = []
        for item in results:
            entry = scene_store.find_scene(current, item['scene'])
            if any(t['number'] == item['number'] for t in entry['takes']):
                continue  # another program registered it while we were listening
            if _fingerprint(item['path']) != prints[item['path']]:
                # It grew (or vanished) while this or another take was being analysed, so its length and sound
                # results are stale. Left for the next run; checked here, just before saving, to keep the window small.
                report['growing'].append((item['scene'], item['number']))
                continue
            scene_store.add_take(current, item['scene'], item['number'], extension=item['extension'],
                                 duration=item['duration'], recorded_at=item['recorded_at'], sound=item['sound'])
            report['added'].append((item['scene'], item['number']))
            report['details'][(item['scene'], item['number'])] = item
            if item['scene'] not in touched:
                touched.append(item['scene'])
        for scene in touched:
            _refresh_scene(current, scene)

    saved = scene_store.update(raw_dir, change)
    for (scene, number), item in report['details'].items():
        item['saved'] = next(t for t in scene_store.find_scene(saved, scene)['takes'] if t['number'] == number)
    return report


# The command line ----------------------------------------------------------------------------------------------

def _describe(take):
    sound = take.get('sound')
    if not isinstance(sound, dict):
        return 'no sound track, not checked'
    notes = []
    if sound.get('ends_early'):
        notes.append('ends early')
    if sound.get('length_off'):
        notes.append('length off')
    if sound.get('long_pause'):
        notes.append('long pause')
    if notes:
        return ', '.join(notes)
    return 'all checks passed' if 'length_off' in sound else 'sound checks passed, length not compared yet'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Register the takes in a video folder and check their sound.')
    parser.add_argument('video_folder', help='the folder that holds scenes/ and scenes.json')
    parser.add_argument('--data-dir', help="video-review's data folder, the one its server is started with "
                                           "(default: VIDEO_REVIEW_DATA_DIR, else the usual one)")
    args = parser.parse_args(argv)
    folder = Path(args.video_folder)
    if not folder.is_dir():
        print(f'The video folder {folder} does not exist.', file=sys.stderr)
        return 2
    try:
        report = run(folder)
    except (scene_store.SceneFileError, ToolMissing) as error:
        print(str(error), file=sys.stderr)
        return 2
    for (scene, number), item in sorted(report['details'].items()):
        take = item['saved']
        print(f'  {scene} take {number}: {take["duration"]:.1f} s, {_describe(take)}')
    for scene, number in report['growing']:
        print(f'  {scene} take {number}: still being written, left for the next run')
    for scene, number in report['unreadable']:
        print(f'  {scene} take {number}: could not be read yet, left for the next run')
    for name in report['unknown_folders']:
        print(f'  scenes/{name}: not in the scene list, skipped')
    waiting = len(report['growing']) + len(report['unreadable'])
    print(f'{len(report["added"])} new take(s) registered, {waiting} left for the next run.')
    # The video's overview page shows what is registered, so it is rewritten after every run. The takes are
    # already saved, so a page that cannot be written is said and does not fail the run.
    try:
        print(f'Overview page: {overview_page.publish(folder, data_dir=args.data_dir)}')
    except (scene_store.SceneFileError, ValueError, OSError) as error:
        print(f'  overview page not updated: {error}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
