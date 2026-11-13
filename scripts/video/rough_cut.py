#!/usr/bin/env python3
"""The rough cut list: `rough-cut.json` next to a video's `scenes.json`, rebuilt on its own whenever a best take changes.

    python scripts/video/rough_cut.py <video folder> [--cue <cue file>] [--data-dir <video-review data>]

The list holds one entry per scene, in script order: the best take of the scene (Lex's star, else the newest take
unless its sound is clearly worse, see scene_store.choose_best), with its file, length and flags. It is the same
list as the strip on top of the overview page (overview_page.py), made from the same scene file.

  file:   version, total_seconds, lengths_known, scenes
  scene:  scene, number, title, recorded, take, file, duration, flagged, script_changed
          recorded         false for a scene with no take; it stays in the list, with take, file and duration null,
                           so a gap is visible and never dropped
          take, file       the best take's number, and its file relative to the video folder
                           (scenes/02-what-it-really-costs/take-03.mkv), so the folder can be moved
          duration         seconds, or null when the take's length is not known
          flagged          an older take is in front because the newest has a sound problem
          script_changed   the saved flag, or the scene's heading no longer matches the cue file now (the same rule as
                           the overview page's chip)
  total_seconds   the known lengths added up; lengths_known is false when a recorded scene has no length

It is rebuilt after every star change (server.py) and every registration run (take_intake.py), so there is no send
button. It only reads scenes.json (under its lock) and the cue file, and writes rough-cut.json; nothing is cut and
no media is read. The existing cutter (cut-takes.py, cut-to-review.py) takes one recording and a word-timed
transcript, so it cannot take this list; see the ticket "Rough cut from the best takes".
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import overview_page  # noqa: E402
import review_store  # noqa: E402
import scene_store  # noqa: E402

FILE_NAME = 'rough-cut.json'
VERSION = 1


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def build_cut(store, changed=()):
    """The rough cut list for a scene file already loaded. changed: ids of scenes whose heading no longer matches
    the cue file (overview_page.changed_in_script)."""
    scenes, total, known = [], 0.0, True
    for scene in store['scenes']:
        best, flagged = scene_store.choose_best(scene)
        take = next((t for t in scene['takes'] if t['number'] == best), None)
        duration = _number(take.get('duration')) if take else None
        if take is not None:
            if duration is None:
                known = False
            else:
                total += duration
        scenes.append({
            'scene': scene['id'], 'number': scene['number'], 'title': scene['title'],
            'recorded': take is not None,
            'take': take['number'] if take else None,
            'file': f'scenes/{scene["id"]}/{take["file"]}' if take else None,
            'duration': duration,
            'flagged': flagged,
            'script_changed': bool(scene.get('script_changed')) or scene['id'] in changed,
        })
    return {'version': VERSION, 'total_seconds': round(total, 3), 'lengths_known': known, 'scenes': scenes}


def _cue_file(data_dir, folder, cue):
    """The cue file to compare with: the one named, else the one the video was registered with, else None (the
    folder's own prompter.md). A registry that cannot be read is no reason to stop: it only means no remembered cue."""
    if cue is None and data_dir is not None:
        try:
            found = overview_page.lookup(data_dir, overview_page.video_key(folder))
        except (ValueError, OSError):
            return None
        cue = found[1] if found else None
    return cue


def rebuild_rough_cut(raw_dir, cue=None, data_dir=None):
    """Write rough-cut.json for a video folder from its scene file now, and return the list.

    The scene file is read and the list written while the scene file's lock is held, so the list is never made from
    a scene file another program is just changing and two rebuilds cannot finish in the wrong order. cue: the cue file
    to compare headings with; without one, the cue file the video was registered with in data_dir, else
    prompter.md in the video folder. SceneFileError when the folder has no usable scene file."""
    cue_text = overview_page.read_cue_text(raw_dir, _cue_file(data_dir, raw_dir, cue))
    with scene_store.locked(raw_dir):
        store = scene_store.load(raw_dir)
        if store is None:
            raise scene_store.SceneFileError(
                f'No {scene_store.FILE_NAME} in {raw_dir}: the scene list is saved when the first take is recorded.')
        cut = build_cut(store, overview_page.changed_in_script(store, cue_text))
        scene_store.write_text_atomic(Path(raw_dir) / FILE_NAME, json.dumps(cut, indent=2, ensure_ascii=False) + '\n')
    return cut


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rebuild a video's rough cut list from its best takes.")
    parser.add_argument('video_folder', help='the folder that holds scenes/ and scenes.json')
    parser.add_argument('--cue', help='the cue file with the script (default: the one the video was registered with, '
                                      'else prompter.md in the video folder)')
    parser.add_argument('--data-dir', help="video-review's data folder (default: its usual one)")
    args = parser.parse_args(argv)
    folder = Path(args.video_folder)
    if not folder.is_dir():
        print(f'The video folder {folder} does not exist.', file=sys.stderr)
        return 2
    # Only looked up, never made: this command writes nothing outside the video folder.
    data_dir = args.data_dir or os.environ.get('VIDEO_REVIEW_DATA_DIR') or review_store.ROOT / 'data'
    try:
        cut = rebuild_rough_cut(folder, cue=args.cue, data_dir=Path(data_dir).expanduser())
    except (scene_store.SceneFileError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    recorded = sum(1 for item in cut['scenes'] if item['recorded'])
    print(f'Rough cut: {recorded} of {len(cut["scenes"])} scenes, {cut["total_seconds"]:g} s'
          f'{"" if cut["lengths_known"] else "+"}: {folder / FILE_NAME}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
