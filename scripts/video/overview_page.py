#!/usr/bin/env python3
"""The overview page: one page per video, built from the video's scenes.json (see scene_store.py).

    python scripts/video/overview_page.py <video folder> [--cue <cue file>] [--data-dir <video-review data>]

It writes `overview.html` into the video folder: one self-contained file (styles and script inside) with relative
links to the take files and their thumbnails, so a double click opens it. It shows the scenes in script order,
each with its script text, the take in the rough cut in front (gold star), older takes under "Archived (n)", the
computer's favorite as a hollow star ("pick") and a strip of the rough cut on top.

The page is rewritten after every take registration (take_intake.py) and every star change (server.py). The stars
work only while video-review's server runs on this computer: the page asks it for the take list, and without an
answer the stars are greyed out with one line saying why. The server also serves the same page at
`/scenes/<video>`, where <video> is the key from video_key(); the server finds the folder in a small registry in
its data folder, which this command (and the take intake) fills.

The script text comes from the video's cue file, `prompter.md` in the video folder unless `--cue` names another
(the registry remembers it). It is only read, never changed. A scene the script no longer has is shown without text
and with the "Script changed since recording" chip, worked out from the cue file each time the page is made.

Thumbnails: one frame per take from ffmpeg, saved beside the take (take-03.mkv gets take-03.jpg). Without ffmpeg
the page is built with plain placeholders. Nothing here reads or writes scenes.json (except to load it); star
changes go through scene_store.update() in the server.
"""
import argparse
import copy
import hashlib
import html
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import quote
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import review_store  # noqa: E402
import scene_store  # noqa: E402

FILE_NAME = 'overview.html'
REGISTRY_NAME = 'scene-videos.json'
PORT_NAME = 'server-port.json'
DEFAULT_CUE_NAME = 'prompter.md'
DEFAULT_PORT = 8879
STARS_OFF_TEXT = 'Stars are off. Start video-review on this computer to change them.'
RECORD_TILE_TEXT = 'Coming with the Prompter'
OLDER_TAKE_TEXT = 'Older take in front: the newest has a sound problem'
THUMB_EXTENSION = '.jpg'
# A frame a second in: the very first frame of a recording is often black or mid-blink. A take shorter than two
# seconds is looked at halfway instead.
THUMB_SECONDS = 1.0
THUMB_WIDTH = 320
THUMB_TIMEOUT_SECONDS = 30
# Width of a rough-cut segment for a scene without a length or without a take, so it stays visible.
UNKNOWN_SEGMENT = 30


# The video key and the registry ----------------------------------------------------------------------------------

def video_key(folder):
    """The name of a video in the server's addresses: the folder's name and a short fingerprint of its full path,
    so two folders with the same name never share a key."""
    resolved = Path(folder).resolve()
    fingerprint = hashlib.sha1(str(resolved).encode('utf-8')).hexdigest()[:8]
    return f'{scene_store.slugify(resolved.name)[:40].strip("-") or "video"}-{fingerprint}'


def register(data_dir, folder, cue=None):
    """Tell the server's data folder where this video lives, so the server can serve its page and take stars.
    Returns the key. Nothing is written when the entry is already there. A cue file named earlier is kept when
    none is named now. A registry file that is not valid JSON raises ValueError."""
    path = Path(data_dir) / REGISTRY_NAME
    key = video_key(folder)
    with review_store.FileLock(Path(data_dir) / f'{REGISTRY_NAME}.lock'):
        registry = review_store.read(path, {})
        if not isinstance(registry, dict):
            raise ValueError(f'{path} is not a registry of videos.')
        old = registry.get(key) if isinstance(registry.get(key), dict) else {}
        entry = {'folder': str(Path(folder).resolve()),
                 'cue': str(Path(cue).resolve()) if cue else old.get('cue')}
        if registry.get(key) != entry:
            registry[key] = entry
            review_store.write(path, registry)
    return key


def lookup(data_dir, key):
    """(video folder, cue file or None) for a registered key, or None when the key is unknown or the folder has
    no scene file any more."""
    registry = review_store.read(Path(data_dir) / REGISTRY_NAME, {})
    entry = registry.get(key) if isinstance(registry, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get('folder'), str):
        return None
    folder = Path(entry['folder'])
    if not scene_store.scene_file_path(folder).is_file():
        return None
    cue = Path(entry['cue']) if isinstance(entry.get('cue'), str) else None
    return folder, cue


def remember_port(data_dir, port):
    """The server writes the port it really listens on, so pages written by other programs ask the right one."""
    review_store.write(Path(data_dir) / PORT_NAME, {'port': int(port)})


def server_address(data_dir=None):
    """Where a saved page asks for the take list: the port the server wrote in its data folder, else
    VIDEO_REVIEW_PORT, else the usual 8879."""
    port = None
    if data_dir is not None:
        try:
            port = review_store.read(Path(data_dir) / PORT_NAME, {}).get('port')
        except (ValueError, AttributeError, OSError):
            port = None  # no file, or one that is not readable: fall back
    if not isinstance(port, int) or isinstance(port, bool):
        port = os.environ.get('VIDEO_REVIEW_PORT') or DEFAULT_PORT
    return f'http://127.0.0.1:{port}'


# The script text -------------------------------------------------------------------------------------------------

ANY_HEADING = re.compile(r'^#{1,6}[ \t]')
LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')


def scene_texts(cue_text):
    """{scene id: spoken text} from a cue file. Stage directions (lines that start with a bracket), links and
    bold marks are left out. A heading that is not a scene ends the scene before it; for a heading that
    appears twice the first wins, as in scene_store.parse_cue_file."""
    texts = {}
    current = None
    for line in cue_text.splitlines():
        line = line.strip()
        if ANY_HEADING.match(line):
            match = scene_store.HEADING.match(line)
            key = scene_store.scene_id(match.group(1), match.group(2).strip()) if match else None
            current = key if key and key not in texts else None
            if current:
                texts[current] = []
            continue
        if current is None or not line or line.startswith('['):
            continue
        texts[current].append(' '.join(LINK.sub(r'\1', line).replace('**', '').split()))
    return {key: ' '.join(lines) for key, lines in texts.items() if lines}


# Thumbnails ------------------------------------------------------------------------------------------------------

def thumb_path(scene, take):
    """The thumbnail's path relative to the video folder, in URL form: scenes/02-x/take-03.jpg."""
    return f'scenes/{scene}/{Path(take["file"]).with_suffix(THUMB_EXTENSION).name}'


def _make_thumbnail(video, image, duration):
    """One frame of a take saved as a small picture, written to a temporary file first. True when it worked."""
    seek = min(THUMB_SECONDS, duration / 2) if duration else 0
    temporary = image.with_name(f'{image.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp{image.suffix}')
    command = ['ffmpeg', '-y', '-v', 'error', '-ss', f'{seek:g}', '-i', str(video), '-frames:v', '1',
               '-vf', f'scale={THUMB_WIDTH}:-2', '-q:v', '5', '-f', 'image2', '-update', '1', str(temporary)]
    try:
        done = subprocess.run(command, capture_output=True, timeout=THUMB_TIMEOUT_SECONDS)
        if done.returncode == 0 and temporary.is_file() and temporary.stat().st_size > 0:
            os.replace(temporary, image)
            return True
    except (OSError, subprocess.SubprocessError):
        pass  # no ffmpeg, an unreadable take or a hang: the page shows a placeholder instead
    temporary.unlink(missing_ok=True)
    return False


def ensure_thumbnails(raw_dir, store):
    """Make the thumbnail of every take whose file is there and whose picture is not yet. Returns the set of
    thumbnail paths (relative to the video folder) that exist now. Never raises for a missing or broken ffmpeg."""
    found = set()
    for scene in store['scenes']:
        for take in scene['takes']:
            relative = thumb_path(scene['id'], take)
            image = Path(raw_dir) / relative
            if image.is_file() and image.stat().st_size > 0:
                found.add(relative)
                continue
            video = Path(raw_dir) / 'scenes' / scene['id'] / take['file']
            if video.is_file() and _make_thumbnail(video, image, take.get('duration')):
                found.add(relative)
    return found


# The page --------------------------------------------------------------------------------------------------------

def _clock(seconds):
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'


def _url(prefix, relative):
    return prefix + '/'.join(quote(part, safe='') for part in relative.split('/'))


def _take_note(take):
    """What the sound checks found, in a few words; empty when the take was not checked."""
    sound = take.get('sound')
    if not isinstance(sound, dict):
        return ''
    notes = [text for flag, text in (('ends_early', 'ended early'), ('long_pause', 'long pause'),
                                     ('length_off', 'length off')) if sound.get(flag)]
    if notes:
        return ', '.join(notes)
    return 'clean end' if sound.get('ends_early') is False else ''


def _take_card(scene, take, best, thumbs, prefix):
    number = take['number']
    esc = html.escape
    label = f'Scene {scene["number"]} · {scene["title"]} · take {number}'
    thumb = thumb_path(scene['id'], take)
    picture = f'<img src="{esc(_url(prefix, thumb))}" alt="" loading="lazy">' if thumb in thumbs else ''
    length = f' ({_clock(take["duration"])})' if isinstance(take.get('duration'), (int, float)) else ''
    media = _url(prefix, f'scenes/{scene["id"]}/{take["file"]}')
    starred = scene.get('star') == number
    pick = ''
    if scene.get('favorite') == number and best != number:
        pick = '<span class="pick" title="Computer\'s favorite">☆ pick</span>'
    star_label = 'Take the star off' if starred else 'Put in rough cut'
    return (
        f'<div class="take" data-scene="{esc(scene["id"])}" data-take="{number}">'
        f'<button class="thumb" type="button" data-play="{esc(media)}" data-label="{esc(label)}"'
        f' aria-label="Play {esc(label)}">{picture}<span class="play">▶</span>'
        f'<span class="tag">Take {number}{esc(length)}</span></button>'
        f'{pick}'
        f'<button class="st" type="button" data-star data-scene="{esc(scene["id"])}" data-take="{number}"'
        f' data-on="{"true" if best == number else "false"}" data-starred="{"true" if starred else "false"}"'
        f' aria-label="{star_label}" title="{star_label}" disabled>{"★" if best == number else "☆"}</button>'
        f'<div class="note">{esc(_take_note(take))}</div></div>')


def _changed_in_script(store, cue_text):
    """Ids of the saved scenes whose heading no longer matches the script, found by comparing the scene list with
    the cue text now (scene_store.check_script on a copy, so nothing is saved). Empty when the cue text has no
    numbered sections at all: that is not a script, so it says nothing about the scenes."""
    if not scene_store.parse_cue_file(cue_text):
        return set()
    return set(scene_store.check_script(copy.deepcopy(store), cue_text)['changed'])


def _scene_section(scene, text, thumbs, prefix, changed):
    esc = html.escape
    best, older_in_front = scene_store.choose_best(scene)
    takes = sorted(scene['takes'], key=lambda t: t['number'])
    chips = ''
    if scene.get('script_changed') or scene['id'] in changed:
        chips += '<span class="chip warn">Script changed since recording</span>'
    if older_in_front:
        chips += f'<span class="chip warn">{esc(OLDER_TAKE_TEXT)}</span>'
    body = f'<p class="stext">{esc(text)}</p>' if text else ''
    cards = ''.join(_take_card(scene, take, best, thumbs, prefix) for take in takes if take['number'] == best)
    others = ''.join(_take_card(scene, take, best, thumbs, prefix) for take in takes if take['number'] != best)
    record = (f'<button class="rec" type="button" disabled>+ Record<small>{esc(RECORD_TILE_TEXT)}</small></button>')
    empty = '' if takes else '<div class="empty">Not recorded yet</div>'
    archive = (f'<details class="arch"><summary>Archived ({len(takes) - 1})</summary>'
               f'<div class="row">{others}</div></details>') if len(takes) > 1 else ''
    return (f'<section class="scene" data-scene="{esc(scene["id"])}">'
            f'<div class="sh"><span class="dotg{"" if takes else " off"}"></span>'
            f'<b class="stitle">{scene["number"]:02d} · {esc(scene["title"])}</b>{chips}</div>'
            f'{body}<div class="row">{record}{cards}{empty}</div>{archive}</section>')


def _cut_strip(store):
    """(strip markup, scenes recorded, takes, total seconds, whether every length is known)."""
    segments, recorded, takes, total, complete = [], 0, 0, 0.0, True
    for scene in store['scenes']:
        takes += len(scene['takes'])
        best = scene_store.choose_best(scene)[0]
        take = next((t for t in scene['takes'] if t['number'] == best), None)
        if take is None:
            segments.append(f'<div class="none" style="flex:{UNKNOWN_SEGMENT}"'
                            f' title="Scene {scene["number"]}: not recorded yet">{scene["number"]}</div>')
            continue
        recorded += 1
        duration = take.get('duration') if isinstance(take.get('duration'), (int, float)) else None
        if duration is None:
            complete = False
        else:
            total += duration
        flex = max(1, round(duration)) if duration is not None else UNKNOWN_SEGMENT
        segments.append(f'<div style="flex:{flex}" title="Scene {scene["number"]}, take {best}">'
                        f'{scene["number"]}</div>')
    return ''.join(segments), recorded, takes, total, complete


def render(store, name, cue_text=None, thumbs=(), server='', video='', link_prefix=''):
    """The page as text, for a scene file already loaded.

    thumbs: relative thumbnail paths that exist. server: where the stars send their clicks ('' for the page the
    server itself serves). video: the key the server knows this video by. link_prefix: put in front of every
    link to a take or thumbnail (the server's page needs one; the saved file uses relative links)."""
    esc = html.escape
    texts = scene_texts(cue_text or '')
    changed = _changed_in_script(store, cue_text or '')
    strip, recorded, takes, total, complete = _cut_strip(store)
    scenes = ''.join(_scene_section(scene, texts.get(scene['id']), thumbs, link_prefix, changed)
                     for scene in store['scenes'])
    summary = (f'<b>{recorded} of {len(store["scenes"])} scenes</b> recorded · {takes} take{"" if takes == 1 else "s"}'
               f' · rough cut <b>{_clock(total)}{"" if complete else "+"}</b>')
    return (PAGE_HEAD.replace('{title}', esc(name)) + PAGE_BODY.format(
        server=esc(server, quote=True), video=esc(video, quote=True), name=esc(name), summary=summary, strip=strip,
        off=esc(STARS_OFF_TEXT), scenes=scenes) + PAGE_SCRIPT)


def _cue_text(folder, cue):
    path = Path(cue) if cue else Path(folder) / DEFAULT_CUE_NAME
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''  # no cue file (or none readable): the scenes show without their text


def _page(raw_dir, store, cue, link_prefix, server, thumbs):
    return render(store, Path(raw_dir).resolve().name, cue_text=_cue_text(raw_dir, cue), thumbs=thumbs,
                  video=video_key(raw_dir), link_prefix=link_prefix,
                  server=server_address() if server is None else server)


def _load(raw_dir):
    store = scene_store.load(raw_dir)
    if store is None:
        raise scene_store.SceneFileError(
            f'No {scene_store.FILE_NAME} in {raw_dir}: the scene list is saved when the first take is recorded.')
    return store


def build_html(raw_dir, cue=None, link_prefix='', server=None):
    """The page for a video folder, as text. Makes missing thumbnails first. SceneFileError when the folder has no
    usable scene file."""
    store = _load(raw_dir)
    return _page(raw_dir, store, cue, link_prefix, server, ensure_thumbnails(raw_dir, store))


def build(raw_dir, cue=None, server=None):
    """Write overview.html into the video folder (a whole new file each time) and return its path.

    The slow part (thumbnails) is done first. The scene file is then read, the page made and written while the scene
    file's lock is held, so a page is never made from a scene file that another program is just changing, and two
    rewrites cannot finish in the wrong order and leave an older page behind."""
    # A take added after this read has no picture until the next rewrite; the intake rewrites the page right after
    # it adds takes, so that gap closes by itself.
    thumbs = ensure_thumbnails(raw_dir, _load(raw_dir))
    path = Path(raw_dir) / FILE_NAME
    temporary = path.with_name(f'{FILE_NAME}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp')
    try:
        with scene_store.locked(raw_dir):
            temporary.write_text(_page(raw_dir, _load(raw_dir), cue, '', server, thumbs), encoding='utf-8',
                                 newline='\n')
            for attempt in range(10):  # on Windows a program that has the page open can make the rename fail briefly
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.05)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def publish(raw_dir, cue=None, data_dir=None):
    """Register the video with the server's data folder, then write its page. Returns the page's path."""
    data_dir = review_store.data_dir(data_dir)
    key = register(data_dir, raw_dir, cue)
    registered = lookup(data_dir, key)
    return build(raw_dir, cue=cue or (registered[1] if registered else None), server=server_address(data_dir))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write a video's overview page and register it with video-review.")
    parser.add_argument('video_folder', help='the folder that holds scenes/ and scenes.json')
    parser.add_argument('--cue', help="the cue file with the script text (default: prompter.md in the video folder)")
    parser.add_argument('--data-dir', help="video-review's data folder (default: its usual one)")
    args = parser.parse_args(argv)
    folder = Path(args.video_folder)
    if not folder.is_dir():
        print(f'The video folder {folder} does not exist.', file=sys.stderr)
        return 2
    try:
        path = publish(folder, cue=args.cue, data_dir=args.data_dir)
    except (scene_store.SceneFileError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f'Overview page: {path}')
    return 0


# The page's own markup, styles and script ------------------------------------------------------------------------
# The look follows docs/wayfinder/scene-recording/prototypes/video-overview-page-borumi-style.html.

PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · overview</title>
<style>
  :root {
    color-scheme: dark;
    --bg: oklch(16% 0.06 282); --panel: oklch(24% 0.066 282); --panel-2: oklch(27% 0.069 282);
    --border: oklch(30% 0.06 282); --chip: oklch(33% 0.096 282); --text: oklch(97.5% 0.012 282);
    --muted: oklch(76.3% 0.027 282); --dim: oklch(64% 0.04 282);
    --accent: oklch(65% 0.227 32.3); --star: oklch(85% 0.15 85); --warn: oklch(80% 0.13 75);
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 500 14px/1.4 system-ui, sans-serif; }
  .page { max-width: 960px; margin: 0 auto; padding: 18px 16px 32px; }
  h1 { font: 700 20px/1.2 system-ui; margin: 0 0 2px; overflow-wrap: anywhere; }
  .sub { color: var(--muted); font-size: 13px; margin: 0 0 14px; }
  .sub b { color: var(--text); }
  .off { color: var(--warn); font-size: 13px; margin: 0 0 14px; }
  .cut { display: flex; gap: 3px; height: 34px; margin: 0 0 4px; }
  .cut div { display: grid; place-items: center; background: var(--chip); border-radius: 6px; font: 700 12px/1 system-ui; color: var(--text); min-width: 0; overflow: hidden; }
  .cut div.none { background: transparent; box-shadow: inset 0 0 0 1px var(--border); color: var(--dim); }
  .cutlabel { color: var(--dim); font-size: 12px; margin: 0 0 16px; }
  .scene { background: var(--panel); border-radius: 12px; padding: 12px; margin: 0 0 10px; }
  .sh { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; margin: 0 0 10px; }
  .stitle { font: 700 15px/1.2 system-ui; overflow-wrap: anywhere; }
  .chip { font: 600 11px/1 system-ui; padding: 4px 7px; border-radius: 6px; background: var(--chip); color: var(--muted); }
  .chip.warn { background: oklch(30% 0.07 75); color: var(--warn); }
  .dotg { width: 9px; height: 9px; border-radius: 50%; background: oklch(75% 0.17 150); align-self: center; flex: none; }
  .dotg.off { background: var(--dim); }
  .stext { color: var(--muted); font-size: 14px; margin: 0 0 10px; cursor: pointer; overflow: hidden; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; line-clamp: 3; overflow-wrap: anywhere; }
  .stext.open { display: block; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: stretch; }
  .rec { flex: 0 0 125px; min-height: 96px; border-radius: 10px; box-shadow: inset 0 0 0 1px var(--border); background: transparent; border: 0; color: var(--muted); font: 600 13px/1.3 system-ui; padding: 8px; cursor: not-allowed; opacity: .7; }
  .rec small { display: block; font-weight: 500; color: var(--dim); margin-top: 3px; }
  .take { flex: 0 0 125px; position: relative; }
  .thumb { display: block; width: 100%; height: 70px; border: 0; padding: 0; border-radius: 9px; position: relative; overflow: hidden; cursor: pointer; color: var(--muted); background: linear-gradient(135deg, oklch(30% 0.09 282), oklch(22% 0.07 282)); }
  .thumb img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
  .play { position: absolute; inset: 0; display: grid; place-items: center; font-size: 18px; text-shadow: 0 1px 4px #000; }
  .take[aria-current="true"] .thumb { box-shadow: 0 0 0 2px #fff; }
  .tag { position: absolute; left: 6px; bottom: 6px; font: 600 11px/1 system-ui; background: oklch(16% 0.06 282 / .78); border-radius: 5px; padding: 4px 6px; color: var(--text); }
  .note { margin: 3px 2px 0; color: var(--dim); font-size: 11px; min-height: 1.3em; }
  .st { position: absolute; top: 5px; right: 5px; border: 0; background: oklch(16% 0.06 282 / .7); border-radius: 6px; width: 26px; height: 26px; color: var(--dim); cursor: pointer; font-size: 15px; line-height: 1; }
  .st[data-on="true"] { color: var(--star); }
  .st:hover:not(:disabled) { background: var(--bg); }
  .st:disabled { cursor: not-allowed; opacity: .55; }
  .pick { position: absolute; top: 5px; left: 5px; font: 700 11px/1 system-ui; color: var(--muted); background: oklch(16% 0.06 282 / .7); border-radius: 6px; padding: 5px 6px; }
  .empty { color: var(--dim); font-size: 13px; padding: 12px; border-radius: 10px; box-shadow: inset 0 0 0 1px var(--border); display: grid; align-items: center; min-height: 96px; }
  .arch { margin: 10px 0 0; }
  .arch summary { cursor: pointer; color: var(--muted); font: 600 13px/1 system-ui; padding: 4px 0 8px; }
  .player { margin: 0 0 10px; }
  .player video { display: block; width: 100%; max-width: 560px; border-radius: 10px; background: #000; }
  .player .t { font: 700 14px/1.3 system-ui; margin-top: 6px; }
  .player .d { color: var(--muted); font-size: 13px; }
  .legend { color: var(--dim); font-size: 12px; margin: 10px 2px 0; }
  .legend b { color: var(--star); }
  button:focus-visible, summary:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
</style>
</head>
"""

PAGE_BODY = """<body data-server="{server}" data-video="{video}">
<div class="page">
  <h1>{name} · overview</h1>
  <p class="sub">{summary}</p>
  <p class="off" id="stars-off">{off}</p>
  <div class="cut" aria-label="Rough cut order">{strip}</div>
  <p class="cutlabel">Rough cut: the best take of every scene, in script order.</p>
  <div id="scenes">{scenes}</div>
  <div class="player" id="player" hidden><video controls playsinline preload="metadata"></video><div class="t"></div><div class="d"></div></div>
  <p class="legend"><b>★</b> in the rough cut (your star, or the newest take by default) &nbsp; ☆ computer's favorite, when it differs. Click a take to play it; click the star of an archived take to bring it to the front.</p>
</div>
"""

PAGE_SCRIPT = """<script>
(() => {
  const body = document.body, server = body.dataset.server, video = body.dataset.video;
  const stars = [...document.querySelectorAll('.st')], off = document.getElementById('stars-off'), offText = off.textContent;
  const player = document.getElementById('player'), clip = player.querySelector('video');
  const title = player.querySelector('.t'), detail = player.querySelector('.d');
  const setStars = on => { stars.forEach(s => { s.disabled = !on; }); off.hidden = on; };
  fetch(server + '/api/scenes?video=' + encodeURIComponent(video))
    .then(r => r.ok ? r.json() : Promise.reject(), () => Promise.reject())
    .then(() => setStars(true), () => setStars(false));
  document.addEventListener('click', e => {
    const star = e.target.closest('.st');
    if (star) {
      if (star.disabled) return;
      star.disabled = true;
      fetch(server + '/api/scenes/star', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video, scene: star.dataset.scene, take: star.dataset.starred === 'true' ? null : Number(star.dataset.take) })
      }).then(async r => {
        if (r.ok) return location.reload();
        // The server answered but could not do it: say what it said and keep the stars usable.
        const said = await r.json().then(j => j.error, () => '');
        off.textContent = said || offText; off.hidden = false; stars.forEach(s => { s.disabled = false; });
      }, () => { off.textContent = offText; setStars(false); });
      return;
    }
    const play = e.target.closest('[data-play]');
    if (play) {
      document.querySelectorAll('.take[aria-current]').forEach(t => t.removeAttribute('aria-current'));
      play.closest('.take').setAttribute('aria-current', 'true');
      play.closest('.scene').querySelector('.sh').after(player);
      player.hidden = false; detail.textContent = '';
      title.textContent = play.dataset.label;
      clip.src = play.dataset.play; clip.play().catch(() => {});
      return;
    }
    const text = e.target.closest('.stext');
    if (text) text.classList.toggle('open');
  });
  clip.addEventListener('error', () => { detail.textContent = 'This browser cannot play this file.'; });
})();
</script>
</body>
</html>
"""


if __name__ == '__main__':
    sys.exit(main())
