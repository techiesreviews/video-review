"""Place a Techies motion template (a Fusion title) on the current Resolve timeline and fill in its text.

Talks to DaVinci Resolve Studio through its own Python scripting API (DaVinciResolveScript). Resolve must be
running with Preferences > System > General > External scripting using set to Local; this script never starts it.

  python tools/motion_place.py --tool "Notion Calendar" --score 4 --reason "Fast, but no tasks." --at 01:00:12:00

What it does, in order:
  1. Copies motion/templates/<template>.setting into Resolve's Titles folder when that copy is missing or different.
  2. Checks the current project and timeline (--project makes it refuse any other project).
  3. Picks a video track that is empty for the card's whole time range (the top one if it is above V1, else a new
     track on top).
  4. Inserts the title at the very end of the timeline, where Resolve's insert pushes nothing along, trims it to
     --seconds, then moves it to the chosen track and time.
  5. Sets the text through the template's own controls, so it stays editable in Resolve's Inspector.
  6. Compares every other clip on the timeline before and after; if anything else moved, it removes the card
     again and stops, and says so when the other clips are still moved (undo in Resolve puts them back).

--test-project NAME creates (or opens) a throwaway project for trying templates. The name must contain
"throwaway", so it can never open one of Lex's real projects.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts' / 'video'))
import resolve_build  # noqa: E402

TEMPLATES = ROOT / 'motion' / 'templates'
# The template's published controls (InstanceInput names in the .setting) and the option that fills each.
FIELDS = {'verdict-card': {'ToolName': 'tool', 'Score': 'score', 'Reason': 'reason', 'Label': 'label'}}
TITLE_NAMES = {'verdict-card': 'Verdict Card'}
# The MacroOperator's name in the .setting: the tool that carries the published controls.
MACROS = {'verdict-card': 'VerdictCard'}
TITLE_FOLDER = 'Techies'


class PlaceError(Exception):
    """Something Resolve did, or a state of the timeline, that the script will not work around."""


def resolve_titles_folder(platform=sys.platform, environ=os.environ):
    """Resolve's per-user folder for Fusion title templates (Edit page > Effects > Titles)."""
    if platform == 'win32':
        if not environ.get('APPDATA'):
            raise PlaceError('APPDATA is not set, so the Resolve Titles folder cannot be found')
        base = Path(environ['APPDATA']) / 'Blackmagic Design' / 'DaVinci Resolve' / 'Support' / 'Fusion'
    elif platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support' / 'Blackmagic Design' / 'DaVinci Resolve' / 'Fusion'
    else:
        base = Path.home() / '.local' / 'share' / 'DaVinciResolve' / 'Fusion'
    return base / 'Templates' / 'Edit' / 'Titles' / TITLE_FOLDER


def install_template(template, folder=None):
    """Copy the repo's .setting into Resolve's Titles folder when the installed copy differs. Returns its path."""
    source = TEMPLATES / f'{template}.setting'
    if not source.is_file():
        raise PlaceError(f'No template at {source}')
    target = (folder or resolve_titles_folder()) / f'{TITLE_NAMES[template]}.setting'
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()  # noqa: E731
    if not target.is_file() or digest(target) != digest(source):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return target


def timecode_to_frame(timecode, fps):
    """HH:MM:SS:FF to an absolute frame number. A ';' before the frames means drop-frame (29.97 or 59.94)."""
    drop = ';' in timecode
    parts = timecode.replace(';', ':').split(':')
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        raise PlaceError(f'Timecode "{timecode}" is not HH:MM:SS:FF')
    hours, minutes, seconds, frames = map(int, parts)
    if minutes > 59 or seconds > 59:
        raise PlaceError(f'Timecode "{timecode}" has more than 59 minutes or seconds')
    base = round(fps)
    if frames >= base:
        raise PlaceError(f'Timecode "{timecode}" has frame {frames}, but the timeline has {base} per second')
    total = ((hours * 60 + minutes) * 60 + seconds) * base + frames
    if drop:
        if base not in (30, 60) or abs(fps - base * 1000 / 1001) > 0.01:
            raise PlaceError(f'Timecode "{timecode}" is drop-frame, which only exists at 29.97 or 59.94 fps')
        dropped = 2 * base // 30
        if seconds == 0 and frames < dropped and minutes % 10:
            raise PlaceError(f'Timecode "{timecode}" is a label drop-frame skips; use ;{dropped:02d} instead')
        all_minutes = hours * 60 + minutes
        total -= dropped * (all_minutes - all_minutes // 10)
    return total


def frame_to_timecode(frame, fps, drop=False):
    """An absolute frame number to HH:MM:SS:FF, or to HH:MM:SS;FF on a drop-frame timeline, as Resolve's
    SetCurrentTimecode reads it there."""
    base = round(fps)
    frame = int(frame)
    if drop:
        dropped = 2 * base // 30
        per_minute, per_ten_minutes = base * 60 - dropped, base * 600 - dropped * 9
        tens, rest = divmod(frame, per_ten_minutes)
        frame += dropped * 9 * tens + (dropped * ((rest - dropped) // per_minute) if rest > dropped else 0)
    seconds, frames = divmod(frame, base)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}{";" if drop else ":"}{frames:02d}'


def snapshot(timeline):
    """Every item on every video and audio track, as comparable tuples."""
    items = set()
    for kind in ('video', 'audio'):
        for track in range(1, (timeline.GetTrackCount(kind) or 0) + 1):
            for item in timeline.GetItemListInTrack(kind, track) or []:
                items.add((kind, track, item.GetName(), item.GetStart(), item.GetEnd(), item.GetUniqueId()))
    return items


def free_track(timeline, start, end, card):
    """The top video track, if turned on and with nothing but the card in [start, end), or a new track added on top."""
    count = timeline.GetTrackCount('video') or 0
    if count >= 2 and timeline.GetIsTrackEnabled('video', count):
        busy = [item for item in timeline.GetItemListInTrack('video', count) or []
                if item.GetUniqueId() != card.GetUniqueId() and item.GetStart() < end and item.GetEnd() > start]
        if not busy:
            return count
    if not timeline.AddTrack('video'):
        raise PlaceError('Resolve would not add a video track for the card')
    return timeline.GetTrackCount('video')


def open_test_project(resolve, name, media=None, media_from=0.0):
    """Create or open a throwaway project with one 1080p 30 fps timeline, optionally with 20 s of a clip on V1."""
    if 'throwaway' not in name.lower():
        raise PlaceError('A test project name must contain "throwaway", so a real project is never opened')
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject()
    if project is None or project.GetName() != name:
        current = project.GetName() if project else ''
        # An Untitled project with a timeline may hold unsaved work, so it counts as real (as in resolve_build).
        empty_untitled = resolve_build.UNTITLED.fullmatch(current) and not project.GetTimelineCount()
        if current and not empty_untitled and 'throwaway' not in current.lower():
            raise PlaceError(f'"{current}" is open in Resolve. Close it yourself first; this script never '
                             'switches away from a real project.')
        project = (manager.LoadProject(name) if name in (manager.GetProjectListInCurrentFolder() or []) else None) \
            or manager.CreateProject(name)
    if project is None:
        raise PlaceError(f'Resolve would not create or open the project "{name}"')
    if project.GetTimelineCount() == 0:
        for key, value in (('timelineFrameRate', '30'), ('timelineResolutionWidth', '1920'),
                           ('timelineResolutionHeight', '1080')):
            project.SetSetting(key, value)
        pool = project.GetMediaPool()
        timeline = pool.CreateEmptyTimeline('Motion test')
        if media:
            clips = pool.ImportMedia([str(media)]) or []
            if not clips:
                raise PlaceError(f'Resolve would not import {media}')
            first = round(media_from * 30)
            pool.AppendToTimeline([{'mediaPoolItem': clips[0], 'startFrame': first, 'endFrame': first + 30 * 20,
                                    'mediaType': 1, 'trackIndex': 1, 'recordFrame': timeline.GetStartFrame()}])
    return project


def place(resolve, template, values, at, seconds, project_name=None):
    """Insert the template at timecode `at` for `seconds` and fill its controls. Returns a report dict."""
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise PlaceError('No project is open in Resolve')
    if project_name and project.GetName() != project_name:
        raise PlaceError(f'The open project is "{project.GetName()}", not "{project_name}"; nothing was changed')
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise PlaceError(f'Project "{project.GetName()}" has no timeline open')
    fps = float(timeline.GetSetting('timelineFrameRate'))
    drop = str(timeline.GetSetting('timelineDropFrameTimecode')).lower() in ('1', 'true')
    if drop != (';' in at):
        kind, mark = ('drop-frame', ';') if drop else ('non-drop timecode', ':')
        raise PlaceError(f'Timeline "{timeline.GetName()}" counts {kind}; write {at} as Resolve shows it, with '
                         f'"{mark}" before the frames (for example {at[:8]}{mark}{at[9:]})')
    start = timecode_to_frame(at, fps)
    if start < timeline.GetStartFrame():
        raise PlaceError(f'{at} is before the timeline starts ({timeline.GetStartTimecode()})')
    frames = max(1, round(seconds * fps))
    before = snapshot(timeline)

    if resolve.GetCurrentPage() not in ('edit', 'cut'):
        resolve.OpenPage('edit')
    # Insert where nothing follows: Resolve's title insert is an insert edit that pushes later clips along.
    end_of_timeline = timeline.GetEndFrame()
    if not timeline.SetCurrentTimecode(frame_to_timecode(end_of_timeline, fps, drop)):
        raise PlaceError('Resolve would not move the playhead to the end of the timeline')
    item = timeline.InsertFusionTitleIntoTimeline(TITLE_NAMES[template])
    if item is None:
        raise PlaceError(f'Resolve has no title called "{TITLE_NAMES[template]}". Run once with --install; '
                         'if it is still missing after that, restart Resolve.')
    added_track = None
    try:
        inserted = item.GetDuration()
        if frames > inserted:
            raise PlaceError(f'Resolve inserts this title for {inserted} frames at most (its standard generator '
                             f'duration); asked for {frames}. Drag it longer in Resolve or ask for less.')
        if frames < inserted and not item.SetRightOffset(item.GetRightOffset() + inserted - frames):
            raise PlaceError('Resolve would not trim the card to its length')
        tracks_before = timeline.GetTrackCount('video')
        track = free_track(timeline, start, start + frames, item)
        added_track = track if track > tracks_before else None
        if not item.SetPosition({'trackIndex': track, 'startFrame': start}):
            raise PlaceError(f'Resolve would not move the card to V{track} at {at}')
        if item.GetStart() != start or item.GetTrackTypeAndIndex() != ['video', track]:
            raise PlaceError(f'The card landed at V{item.GetTrackTypeAndIndex()[1]} frame {item.GetStart()}, '
                             f'not V{track} frame {start}')

        comp = item.GetFusionCompByIndex(1)
        macro = comp and comp.FindTool(MACROS[template])
        if macro is None:
            raise PlaceError('The card has no Fusion composition to fill in')
        filled = {}
        for control, option in FIELDS[template].items():
            value = values.get(option)
            if value is None:
                continue
            macro.SetInput(control, value)
            filled[control] = macro.GetInput(control)
            if filled[control] != value:
                raise PlaceError(f'Resolve kept "{filled[control]}" in {control}, not "{value}"')

        after = {entry for entry in snapshot(timeline) if entry[5] != item.GetUniqueId()}
        moved = before.symmetric_difference(after)
        if moved:
            raise PlaceError(f'Other clips changed while placing the card: {sorted(moved)[:4]}')
    except Exception as error:
        reason = str(error).rstrip('.')
        if not timeline.DeleteClips([item], False):
            raise PlaceError(f'{reason}. The card could not be removed; delete it in Resolve.') from error
        if snapshot(timeline) != before:
            raise PlaceError(f'{reason}. The card was removed, but other clips are still moved: undo in Resolve '
                             'until they are back, then delete the card again.') from error
        if added_track and not timeline.DeleteTrack('video', added_track):
            raise PlaceError(f'{reason}. The card was removed; the empty track V{added_track} made for it is '
                             'still there, delete it in Resolve.') from error
        raise
    return {'project': project.GetName(), 'timeline': timeline.GetName(), 'track': f'V{track}',
            'start': at, 'frames': item.GetDuration(), 'fps': fps,
            'controls': filled, 'item': item.GetUniqueId(), '_item': item, '_timeline': timeline}


def export_frames(timeline, item, folder, offsets, roi=None):
    """Save the whole timeline picture (video and card together) at frames counted from the card's start, as PNG
    files, plus one sheet of all of them. roi crops to (x, y, width, height) fractions of the frame."""
    folder.mkdir(parents=True, exist_ok=True)
    first = item.GetStart() - timeline.GetStartFrame()
    crop = dict(zip(('roiX', 'roiY', 'roiWidth', 'roiHeight'), roi)) if roi else {}
    saved = []
    for offset in offsets:
        path = folder / f'frame-{offset:03d}.png'
        result = timeline.ExportFrame(first + offset, {'outputType': 'file', 'filePath': str(path), **crop})
        if not result or not result.get('success'):
            raise PlaceError(f'Resolve could not export frame {offset}: {result and result.get("error")}')
        saved.append(str(path))
    sheet = folder / 'frames-sheet.jpg'
    cells = [{'frameOffset': first + offset, 'label': f'+{offset} frames'} for offset in offsets]
    result = timeline.ExportThumbnails(cells, {'outputType': 'file', 'filePath': str(sheet), 'maxDimension': 1600,
                                               'quality': 85})
    if not result or not result.get('success'):
        raise PlaceError(f'Resolve could not export the frames sheet: {result and result.get("error")}')
    saved.append(str(sheet))
    return saved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--template', default='verdict-card', choices=sorted(FIELDS))
    parser.add_argument('--tool', help='Tool name, for example "Notion Calendar"')
    parser.add_argument('--score', help='Score out of 5, for example 4 or 4.5')
    parser.add_argument('--reason', help='One short line on why')
    parser.add_argument('--label', help='Small label above the tool name (the template says VERDICT)')
    parser.add_argument('--at', required=True, help='Timeline timecode for the card to start, HH:MM:SS:FF '
                        '(HH:MM:SS;FF on a drop-frame timeline, as Resolve shows it)')
    parser.add_argument('--seconds', type=float, default=5.0, help='How long the card stays (default 5)')
    parser.add_argument('--project', help='Refuse to change anything unless this project is the open one')
    parser.add_argument('--install', action='store_true', help='Copy the template into Resolve first')
    parser.add_argument('--test-project', help='Create or open this throwaway project first ("throwaway" in it)')
    parser.add_argument('--test-media', help='With --test-project: a video to put on V1 of a new test timeline')
    parser.add_argument('--test-media-from', type=float, default=0.0, help='Start that video this many seconds in')
    parser.add_argument('--frames', help='Save PNG frames of the placed card into this folder')
    parser.add_argument('--frame-offsets', default='0,2,4,6,8,30', help='Which frames from the card start')
    parser.add_argument('--frames-crop', help='Crop the saved frames: x,y,width,height as fractions, e.g. 0,0.6,0.6,0.4')
    args = parser.parse_args(argv)
    if args.score is not None and not (args.score.replace('.', '', 1).isdigit() and float(args.score) <= 5):
        parser.error('--score takes a number from 0 to 5, for example 4 or 4.5')
    if not (math.isfinite(args.seconds) and args.seconds > 0):
        parser.error('--seconds takes a length above 0, for example 5')
    try:
        offsets = [int(x) for x in args.frame_offsets.split(',') if x.strip()]
        crop = [float(x) for x in args.frames_crop.split(',')] if args.frames_crop else None
        crop_ok = crop is None or (len(crop) == 4 and all(0 <= x <= 1 for x in crop))
    except ValueError:
        crop_ok = False
    if not crop_ok:
        parser.error('--frame-offsets takes whole numbers and --frames-crop four fractions, both comma-separated')
    try:
        installed = install_template(args.template) if args.install else None
        resolve = resolve_build.connect()
        resolve_build.check_studio(resolve)
        if args.test_project:
            open_test_project(resolve, args.test_project, args.test_media, args.test_media_from)
        values = {'tool': args.tool, 'score': args.score, 'reason': args.reason, 'label': args.label}
        report = place(resolve, args.template, values, args.at, args.seconds,
                       args.project or args.test_project)
        item, timeline = report.pop('_item'), report.pop('_timeline')
        if installed:
            report['installed'] = str(installed)
    except (PlaceError, resolve_build.ResolveError, OSError) as error:  # OSError: --install
        print(f'Not placed: {error}', file=sys.stderr)
        return 1
    if args.frames:
        # The card is on the timeline by now, so a failure here must not read as "Not placed".
        try:
            report['frames_saved'] = export_frames(timeline, item, Path(args.frames), offsets, crop)
        except (PlaceError, OSError) as error:
            report['frames_error'] = str(error)
            print(f'Placed, but the frames could not be saved: {error}', file=sys.stderr)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
