"""The per-video scene file: scenes.json in a video's raw-files folder, kept only by video-review.

It holds the scene list, every take, Lex's stars, the computer's favorite and (later) markers. Every program in
the scene-recording slice reads and writes it through this module, so names and rules live in one place.

Fields (also listed in the build ticket, so the next tickets use the same names):

  file:   version, saved_at, scenes, markers
  scene:  id, number, title, script_changed, star, favorite, best, flagged, takes
  take:   number, file, name, recorded_at, duration, sound   (only number, file and name are always there)
  sound:  ends_early, length_off, long_pause                 (written by the sound check; each true or false)
          ends_early = check 1 failed (the take does not end in a clean stretch of silence),
          length_off = check 2 failed (length not close to the other takes of the scene),
          long_pause = check 3 failed (a long silence in the middle).
          Only ends_early and long_pause decide "clearly worse" and the best take; length_off is only for the
          computer's favorite. A check that is missing from sound counts as not run.

Nothing here reads or writes media, and nothing touches a script or prompter file: the cue text comes in as a
string.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import time
import unicodedata
import uuid

try:
    import fcntl
except ImportError:  # Windows (Git Bash)
    fcntl = None
    import msvcrt

FILE_NAME = 'scenes.json'
VERSION = 1
STORE_FIELDS = ('version', 'saved_at', 'scenes', 'markers')
SCENE_FIELDS = ('id', 'number', 'title', 'script_changed', 'star', 'favorite', 'best', 'flagged', 'takes')
TAKE_FIELDS = ('number', 'file', 'name', 'recorded_at', 'duration', 'sound')
OPTIONAL_TAKE_FIELDS = ('recorded_at', 'duration', 'sound')
DEFAULT_EXTENSION = '.mkv'

# A numbered section of a saved prompter cue file: "### **2 · WHAT IT REALLY COSTS · 02:00**". The time and the
# bold marks are optional. Headings without a number (PREP, the hook) are not scenes, and neither is the file's
# one-hash title line, even when it starts with a number.
HEADING = re.compile(
    r'^#{2,6}[ \t]+(?:\*\*)?[ \t]*(\d+)[ \t]*·[ \t]*(.+?)[ \t]*'
    r'(?:·[ \t]*\d{1,2}(?::\d{2}){1,2})?[ \t]*(?:\*\*)?[ \t]*$')
TAKE_FILE = re.compile(r'^take-(\d+)(\.[A-Za-z0-9]+)$')
TAKE_FULL = re.compile(r'^scene-(\d+-.+)-take-(\d+)(\.[A-Za-z0-9]+)?$')
WINDOWS_RETRIES = 10
LOCK_NAME = 'scenes.json.lock'
LOCK_WAIT_SECONDS = 10  # how long update() waits for another program's lock before giving up
# What "someone else holds the lock" looks like: flock says EAGAIN or EWOULDBLOCK, Windows locking says EACCES
# or EDEADLOCK.
LOCK_BUSY_ERRNOS = {code for code in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES,
                                      getattr(errno, 'EDEADLK', None), getattr(errno, 'EDEADLOCK', None)) if code}


class SceneFileError(Exception):
    """scenes.json exists but cannot be read as a scene file. Never replaced by an empty one."""


# Names ---------------------------------------------------------------------------------------------------------

def slugify(title):
    """Lower-case words joined by dashes: "What's next?" becomes "whats-next"."""
    letters = []
    for char in unicodedata.normalize('NFKD', title):
        if unicodedata.combining(char) or char in "'’`":
            continue  # accents and apostrophes vanish: "Café" is "cafe", "what's" is "whats"
        # Any other mark, dashes of every kind included, separates words.
        letters.append(char.lower() if char.isalnum() else '-')
    return re.sub(r'-+', '-', ''.join(letters)).strip('-') or 'untitled'


def title_key(title):
    """A title as compared between script versions: capitals, spacing and dash style do not count, symbols do."""
    text = unicodedata.normalize('NFKC', title).casefold()
    return ' '.join(re.sub(r'[\u2010-\u2015\u2212-]', '-', text).split())


def scene_id(number, title):
    return f'{int(number):02d}-{slugify(title)}'


def take_file_name(number, extension=DEFAULT_EXTENSION):
    """take-03.mkv, the name inside the scene's folder."""
    return f'take-{int(number):02d}{extension}'


def parse_take_file_name(name):
    """(3, '.mkv') for take-03.mkv, or None when the name is not a take file."""
    match = TAKE_FILE.match(name)
    return (int(match.group(1)), match.group(2)) if match else None


def take_full_name(scene, number):
    """scene-02-what-it-really-costs-take-03: says scene and take even for a file outside its folder."""
    return f'scene-{scene}-take-{int(number):02d}'


def parse_take_full_name(name):
    """(scene id, take number, extension) for scene-02-what-it-really-costs-take-03[.mkv], or None."""
    match = TAKE_FULL.match(name)
    return (match.group(1), int(match.group(2)), match.group(3) or '') if match else None


def scene_folder(raw_dir, scene):
    """<raw_dir>/scenes/02-what-it-really-costs, where the scene's take files sit."""
    return Path(raw_dir) / 'scenes' / scene


# The scene list ------------------------------------------------------------------------------------------------

def parse_cue_file(text):
    """Scenes (id, number, title) from the numbered headings of a cue file's text, in script order."""
    scenes = []
    seen = set()
    for line in text.splitlines():
        match = HEADING.match(line.strip())
        if not match:
            continue
        number, title = int(match.group(1)), match.group(2).strip()
        key = scene_id(number, title)
        if key not in seen:
            seen.add(key)
            scenes.append({'id': key, 'number': number, 'title': title})
    return scenes


def new_store(scenes):
    """A fresh scene file for a scene list from parse_cue_file."""
    return {
        'version': VERSION,
        'saved_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'scenes': [{
            'id': scene['id'], 'number': scene['number'], 'title': scene['title'],
            'script_changed': False, 'star': None, 'favorite': None, 'best': None, 'flagged': False,
            'takes': [],
        } for scene in scenes],
        'markers': [],
    }


def ensure_scene_list(raw_dir, cue_text):
    """The scene list is saved once, at the first take. Later calls return the saved file untouched."""
    with _locked(raw_dir):
        store = load(raw_dir)
        if store is not None:
            return store
        scenes = parse_cue_file(cue_text)
        if not scenes:
            raise ValueError('The cue file has no numbered sections (like "2 · Title"), so there is no scene list to save.')
        store = new_store(scenes)
        save(raw_dir, store)
        return store


def check_script(store, cue_text):
    """Compare the saved scene list with the script now. Flags changed scenes; renames and deletes nothing.

    A saved scene gets script_changed true when its id (number plus title) is no longer in the script (title
    changed, number changed, or removed), when its title differs in more than capitals, spacing or dash style
    (so "C++" becoming "C#" counts even though both make the same id), or when it kept its id but now sits in a
    different place among the scenes both lists still share (headings swapped or moved without renumbering). A
    scene that matches again loses the flag. Scenes that are only in the script now are reported under "added"
    and are not added to the file. Returns {"changed": [...], "added": [...]}.
    """
    now = parse_cue_file(cue_text)
    titles = {scene['id']: title_key(scene['title']) for scene in now}
    current = [scene['id'] for scene in now]
    saved = [scene['id'] for scene in store['scenes']]
    shared_now = [key for key in current if key in saved]
    shared_then = [key for key in saved if key in current]
    for scene in store['scenes']:
        key = scene['id']
        scene['script_changed'] = (key not in current or titles[key] != title_key(scene['title'])
                                   or shared_now.index(key) != shared_then.index(key))
    return {
        'changed': [scene['id'] for scene in store['scenes'] if scene['script_changed']],
        'added': [key for key in current if key not in saved],
    }


def find_scene(store, scene):
    for entry in store['scenes']:
        if entry['id'] == scene:
            return entry
    raise KeyError(f'No scene {scene!r} in the scene file.')


# Takes, stars, best take ---------------------------------------------------------------------------------------

def add_take(store, scene, number, extension=None, **fields):
    """Register a take (or update it when that number is already there). Takes stay in number order.

    Optional fields: recorded_at, duration, sound ({"ends_early": bool, "length_off": bool, "long_pause": bool}).
    None is skipped. extension (default .mkv) is only used to name the file; give it again to change it.
    """
    unknown = set(fields) - set(OPTIONAL_TAKE_FIELDS)
    if unknown:
        raise ValueError(f'Unknown take field(s): {", ".join(sorted(unknown))}')
    number = int(number)
    entry = find_scene(store, scene)
    take = next((t for t in entry['takes'] if t['number'] == number), None)
    if take is None:
        take = {'number': number, 'file': take_file_name(number, extension or DEFAULT_EXTENSION),
                'name': take_full_name(scene, number)}
        entry['takes'].append(take)
        entry['takes'].sort(key=lambda t: t['number'])
    elif extension:
        take['file'] = take_file_name(number, extension)
    take.update({key: value for key, value in fields.items() if value is not None})
    return take


def _require_take(entry, number):
    if number is not None and not any(t['number'] == number for t in entry['takes']):
        raise ValueError(f'Scene {entry["id"]} has no take {number}.')


def set_star(store, scene, number):
    """Lex's star: a take number, or None to take it off. The star always wins."""
    entry = find_scene(store, scene)
    _require_take(entry, number)
    entry['star'] = number


def set_favorite(store, scene, number):
    """The computer's favorite: a take number, or None. It is shown beside the star and never decides the best."""
    entry = find_scene(store, scene)
    _require_take(entry, number)
    entry['favorite'] = number


def _sound(take, flag):
    sound = take.get('sound')
    if not isinstance(sound, dict) or flag not in sound:
        return None
    return bool(sound[flag])


def _is_clean(take):
    return _sound(take, 'ends_early') is False and _sound(take, 'long_pause') is False


def _is_worse(take):
    return bool(_sound(take, 'ends_early') or _sound(take, 'long_pause'))


def choose_best(entry):
    """(best take number or None, flagged) for one scene.

    Lex's star if there is one. Otherwise the newest take (highest number), except that when the sound check says
    the newest ends early or has a long pause while an older take is clean, the newest clean older take is used
    and the scene is flagged for Lex. The computer's favorite is never used here.
    """
    takes = sorted(entry['takes'], key=lambda t: t['number'])
    if entry.get('star') is not None and any(t['number'] == entry['star'] for t in takes):
        return entry['star'], False
    if not takes:
        return None, False
    newest, older = takes[-1], takes[:-1]
    clean_older = [t for t in older if _is_clean(t)]
    if _is_worse(newest) and clean_older:
        return clean_older[-1]['number'], True
    return newest['number'], False


def apply_best(store):
    """Write best and flagged on every scene."""
    for entry in store['scenes']:
        entry['best'], entry['flagged'] = choose_best(entry)


# The file ------------------------------------------------------------------------------------------------------

def scene_file_path(raw_dir):
    return Path(raw_dir) / FILE_NAME


def _retry_on_windows(action):
    """A reader holding the file open can make rename or open fail briefly on Windows; try again."""
    for attempt in range(WINDOWS_RETRIES):
        try:
            return action()
        except PermissionError:
            if attempt == WINDOWS_RETRIES - 1:
                raise
            time.sleep(0.05)


def load(raw_dir):
    """The scene file as a dict, or None when there is none yet.

    Writes go to a temporary file and are renamed into place, so scenes.json is always a whole file; a leftover
    temporary file from a program that stopped half-way is never read. A scenes.json that is not valid raises
    SceneFileError instead of looking like an empty file.
    """
    path = scene_file_path(raw_dir)
    try:
        raw = _retry_on_windows(lambda: path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as error:
        raise SceneFileError(f'{path} is not valid UTF-8 text: {error}') from error
    try:
        store = json.loads(raw)
    except ValueError as error:
        raise SceneFileError(f'{path} is not valid JSON: {error}') from error
    if not isinstance(store, dict) or not isinstance(store.get('scenes'), list) or not all(
            isinstance(scene, dict) and 'id' in scene and isinstance(scene.get('takes'), list)
            for scene in store['scenes']):
        raise SceneFileError(f'{path} is not a scene file (no scene list, or a scene without an id or takes).')
    return store


def write_text_atomic(path, text):
    """Write a whole file safely: a temporary file in the same folder, then a rename into place. A reader sees the old
    file or the new one, never half of it, and a failed write leaves the old file and no temporary file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp')
    try:
        with open(temporary, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _retry_on_windows(lambda: os.replace(temporary, path))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def save(raw_dir, store):
    """Refresh best and flagged, then write the file safely (write_text_atomic)."""
    apply_best(store)
    write_text_atomic(scene_file_path(raw_dir), json.dumps(store, indent=2, ensure_ascii=False) + '\n')


def _try_lock(handle):
    if fcntl:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)


def _unlock(handle):
    if fcntl:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def _locked(raw_dir):
    """One program at a time between load and save, using a lock the operating system holds on scenes.json.lock.

    The system lets go of it when the program ends, even if it crashed, so there is no stale lock to clean up and
    no way for one program to remove another's. The small lock file itself stays in the folder.
    """
    folder = Path(raw_dir)
    folder.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    with open(folder / LOCK_NAME, 'a+b') as handle:
        while True:
            try:
                _try_lock(handle)
                break
            except OSError as error:
                if error.errno not in LOCK_BUSY_ERRNOS:
                    raise  # a real failure (for example no locking on this drive), not a busy lock
                if time.monotonic() > deadline:
                    raise SceneFileError(f'{handle.name} is held by another program; try again in a moment.') from None
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)


locked = _locked  # The same lock under a public name, for a short read-then-write job that is not a scene-file write (the overview page).


def update(raw_dir, change):
    """Load the scene file, run change(store), save it, all under the lock. Use this for every write, so a star
    clicked while the take intake runs is not lost. Returns the saved store; the file must already exist
    (ensure_scene_list makes it).

    Do slow work (ffprobe, ffmpeg, reading media) before calling update; change should only copy results into
    the store. Another program waits for the lock up to LOCK_WAIT_SECONDS, then gets a SceneFileError."""
    with _locked(raw_dir):
        store = load(raw_dir)
        if store is None:
            raise SceneFileError(f'No {FILE_NAME} in {raw_dir} yet; call ensure_scene_list first.')
        change(store)
        save(raw_dir, store)
        return store
