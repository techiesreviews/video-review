"""Build one working timeline in DaVinci Resolve Studio from an approved cut list, and render it.

Used by cut-to-review.py. Talks to Resolve through its own Python scripting API (DaVinciResolveScript), so no
MCP time limit applies. Every step reads back what Resolve did and stops on the first difference. It never
launches Resolve, never switches away from a project it did not expect, and never saves over one.
"""
from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import re
import sys
import time

TIMELINE_NAME = 'A-CUT — working'
UNTITLED = re.compile(r'Untitled Project( \d+)?')
# Clips per AppendToTimeline call; a long cut (117 segments) goes in chunks.
CHUNK_SEGMENTS = 40
VIDEO, AUDIO = 1, 2
PREFERRED_CODECS = ('H264_NVIDIA', 'H264')
RENDER_POLL_SECONDS = 2.0
SCRIPT_PATHS = {
    'win32': (r'C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting',
              r'C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll'),
    'darwin': ('/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting',
               '/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so'),
    'linux': ('/opt/resolve/Developer/Scripting', '/opt/resolve/libs/Fusion/fusionscript.so'),
}


class ResolveError(Exception):
    """Resolve did something other than asked, or is in a state this tool must not touch."""


@dataclass(frozen=True)
class Segment:
    id: str
    source_in: int
    source_out: int
    timeline_start: int

    @property
    def duration(self):
        return self.source_out - self.source_in


@dataclass(frozen=True)
class Marker:
    frame: int
    color: str
    name: str
    note: str


def fade_frames(audio_fade_ms, fps):
    """Resolve fades are in timeline frames and may be fractional: 15 ms at 30 fps is 0.45 frames."""
    return audio_fade_ms * fps / 1000


def segments_from_cutlist(cutlist):
    return [Segment(str(item.get('id', index + 1)), item['sourceInFrame'], item['sourceOutFrame'],
                    item['timelineStartFrame'])
            for index, item in enumerate(sorted(cutlist['segments'], key=lambda item: item['timelineStartFrame']))]


def segments_from_runs(timeline):
    """One clip per unbroken run: a split where the source runs on is not a cut, so it gets no clip edge or fade."""
    return [Segment(run.label, run.source_in, run.source_out, run.timeline_start_frame) for run in timeline.runs]


def clip_infos(item, segment, timeline_start, end_inclusive=True):
    """The picture and the sound of one segment, both placed at the same record frame on track 1."""
    end = segment.source_out - 1 if end_inclusive else segment.source_out
    return [{'mediaPoolItem': item, 'startFrame': segment.source_in, 'endFrame': end, 'mediaType': kind,
             'trackIndex': 1, 'recordFrame': timeline_start + segment.timeline_start} for kind in (VIDEO, AUDIO)]


def connect(environ=os.environ, platform=sys.platform):
    """Return Resolve if it is running with external scripting set to Local. Never starts it."""
    api, lib = SCRIPT_PATHS.get('linux' if platform.startswith('linux') else platform, SCRIPT_PATHS['linux'])
    environ.setdefault('RESOLVE_SCRIPT_API', api)
    environ.setdefault('RESOLVE_SCRIPT_LIB', lib)
    modules = str(Path(environ['RESOLVE_SCRIPT_API']) / 'Modules')
    if modules not in sys.path:
        sys.path.append(modules)
    try:
        script = importlib.import_module('DaVinciResolveScript')
    except ImportError as error:
        raise ResolveError(f'Resolve scripting module not found under {modules}: {error}') from error
    resolve = script.scriptapp('Resolve')
    if resolve is None:
        raise ResolveError('Resolve is not reachable. Open DaVinci Resolve Studio yourself and set Preferences > '
                           'System > General > External scripting using to Local; this tool never starts it.')
    return resolve


def check_studio(resolve):
    product = resolve.GetProductName() or ''
    if 'Studio' not in product:
        raise ResolveError(f'This needs DaVinci Resolve Studio; Resolve says it is "{product}".')
    return f'{product} {resolve.GetVersionString() or ""}'.strip()


class Builder:
    """One build of the working timeline. Call open_project, build_timeline, render in that order."""

    def __init__(self, resolve, run_dir, log=print):
        self.resolve = resolve
        self.run_dir = Path(run_dir)
        self.log = log
        self.manager = resolve.GetProjectManager()
        self.project = None
        self.timeline = None
        self.facts = {}

    # Step 3a: the project guard.
    def open_project(self, name):
        current = self.manager.GetCurrentProject()
        current_name = current.GetName() if current else None
        if current_name == name:
            self._checkpoint(name)
            self.project = current
        elif current is None or (UNTITLED.fullmatch(current_name or '') and not current.GetTimelineCount()):
            if name in (self.manager.GetProjectListInCurrentFolder() or []):
                self._checkpoint(name)
                self.project = self.manager.LoadProject(name)
            else:
                self.project = self.manager.CreateProject(name)
                self.facts['projectCreated'] = True
            if not self.project or self.project.GetName() != name:
                raise ResolveError(f'Resolve did not open the project "{name}".')
        else:
            raise ResolveError(f'Resolve has the project "{current_name}" open, not "{name}". Nothing was changed: '
                               f'save and close it yourself, or pass --project "{current_name}".')
        self.facts['project'] = name
        return self.project

    def _checkpoint(self, name):
        target = self.run_dir / f'{name}-before.drp'
        if not self.manager.ExportProject(name, str(target)):
            raise ResolveError(f'Could not save a checkpoint of "{name}" to {target}; nothing was changed.')
        self.facts['checkpoint'] = target.name
        self.log(f'Checkpoint of the project saved: {target}')

    # Step 3b: the timeline.
    def build_timeline(self, source_path, fps, width, height, segments, audio_fade_ms, markers):
        project = self.project
        existing = self._timeline_named(TIMELINE_NAME)
        if existing and _has_clips(existing):
            raise ResolveError(f'The project already has a timeline "{TIMELINE_NAME}" with clips. Rename it in '
                               'Resolve first; this tool only builds one fresh working timeline.')
        if not project.GetTimelineCount():
            for key, value in (('timelineFrameRate', fps), ('timelineResolutionWidth', width),
                               ('timelineResolutionHeight', height)):
                project.SetSetting(key, str(value))
        if not _same_number(project.GetSetting('timelineFrameRate'), fps):
            raise ResolveError(f'The project runs at {project.GetSetting("timelineFrameRate")} fps, the cut at '
                               f'{fps}. Use a new project.')
        pool = project.GetMediaPool()
        imported = pool.ImportMedia([str(source_path)]) or []
        if len(imported) != 1:
            raise ResolveError(f'Resolve imported {len(imported)} items from {source_path}, expected one.')
        item = imported[0]
        if not _same_number(item.GetClipProperty('FPS'), fps):
            raise ResolveError(f'Resolve reads the recording at {item.GetClipProperty("FPS")} fps, the cut at {fps}.')
        timeline = existing or pool.CreateEmptyTimeline(TIMELINE_NAME)
        if not timeline or not project.SetCurrentTimeline(timeline):
            raise ResolveError(f'Resolve did not create the timeline "{TIMELINE_NAME}".')
        self.timeline = timeline
        self._append(pool, item, segments)
        pairs = self.read_back(segments)
        for video, audio in pairs:
            if not timeline.SetClipsLinked([video, audio], True):
                raise ResolveError('Resolve did not link a picture clip to its sound.')
        self._fades([audio for _, audio in pairs], fade_frames(audio_fade_ms, fps))
        self._markers(markers)
        drt = self.run_dir / f'{TIMELINE_NAME}.drt'
        if not timeline.Export(str(drt), self.resolve.EXPORT_DRT):
            raise ResolveError(f'Resolve did not export the timeline to {drt}.')
        if not self.manager.SaveProject():
            raise ResolveError('Resolve did not save the project.')
        self.facts.update({'timeline': TIMELINE_NAME, 'clips': len(pairs) * 2, 'drt': drt.name})
        return timeline

    def _timeline_named(self, name):
        project = self.project
        for index in range(1, (project.GetTimelineCount() or 0) + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline and timeline.GetName() == name:
                return timeline
        return None

    def _append(self, pool, item, segments):
        """Append every segment. The first one also settles whether Resolve reads endFrame as inclusive."""
        start = self.timeline.GetStartFrame()
        first, rest = segments[0], segments[1:]
        inclusive = True
        placed = pool.AppendToTimeline(clip_infos(item, first, start, inclusive)) or []
        if len(placed) == 2 and all(clip.GetDuration() == first.duration - 1 for clip in placed):
            # Resolve took endFrame as exclusive: put the first segment back with the full length.
            if not self.timeline.DeleteClips(placed, False):
                raise ResolveError('Could not remove the trial clip to correct its length.')
            inclusive = False
            placed = pool.AppendToTimeline(clip_infos(item, first, start, inclusive)) or []
        if len(placed) != 2:
            raise ResolveError(f'Resolve placed {len(placed)} clips for segment {first.id}, expected picture and sound.')
        self.facts['endFrameInclusive'] = inclusive
        for index in range(0, len(rest), CHUNK_SEGMENTS):
            chunk = rest[index:index + CHUNK_SEGMENTS]
            infos = [info for segment in chunk for info in clip_infos(item, segment, start, inclusive)]
            placed = pool.AppendToTimeline(infos) or []
            if len(placed) != len(infos):
                raise ResolveError(f'Resolve placed {len(placed)} of {len(infos)} clips from segment {chunk[0].id}.')

    def read_back(self, segments):
        """Check every clip on track 1 against the cut list: place, length and source frame."""
        start = self.timeline.GetStartFrame()
        tracks = {kind: sorted(self.timeline.GetItemListInTrack(kind, 1) or [], key=lambda clip: clip.GetStart())
                  for kind in ('video', 'audio')}
        for kind, clips in tracks.items():
            if len(clips) != len(segments):
                raise ResolveError(f'The timeline has {len(clips)} {kind} clips, the cut list {len(segments)} segments.')
            for clip, segment in zip(clips, segments):
                found = (clip.GetStart() - start, clip.GetDuration(), clip.GetLeftOffset())
                wanted = (segment.timeline_start, segment.duration, segment.source_in)
                if found != wanted:
                    raise ResolveError(f'Segment {segment.id} {kind} reads back as start {found[0]}, length {found[1]}, '
                                       f'source frame {found[2]}; the cut list says {wanted[0]}, {wanted[1]}, {wanted[2]}.')
        return list(zip(tracks['video'], tracks['audio']))

    def _fades(self, clips, frames):
        """Resolve 21's native fades: TimelineItem.SetFades({FadeIn, FadeOut}) in frames, read back with GetFades."""
        wanted = {'FadeIn': frames, 'FadeOut': frames}
        for clip in clips:
            if not clip.SetFades(dict(wanted)):
                raise ResolveError(f'Resolve did not set the {frames:g}-frame audio fades. The timeline is built '
                                   'but not rendered.')
            found = clip.GetFades()
            try:
                same = all(abs(float(found[key]) - frames) <= 1e-6 for key in wanted)
            except (KeyError, TypeError, ValueError):
                same = False
            if not same:
                raise ResolveError(f'Audio fades read back as {found!r}, expected {frames:g} frames each side.')
        self.facts['fadeFrames'] = frames

    def _markers(self, markers):
        for marker in markers:
            if not self.timeline.AddMarker(marker.frame, marker.color, marker.name, marker.note, 1):
                raise ResolveError(f'Resolve did not add the marker at frame {marker.frame}.')
        found = self.timeline.GetMarkers() or {}
        missing = [marker.frame for marker in markers if found.get(marker.frame, {}).get('color') != marker.color]
        if missing:
            raise ResolveError(f'Markers missing or the wrong colour at frames {missing}.')
        self.facts['markers'] = len(markers)

    # Step 4: render under a temporary name, wait, rename.
    def render(self, name, fps, width, height, timeout_seconds, sleep=time.sleep, clock=time.monotonic):
        project = self.project
        final = self.run_dir / f'{name}.mp4'
        temporary = self.run_dir / f'{name}.rendering.mp4'
        for path in (final, temporary):
            if path.exists():
                raise ResolveError(f'{path} already exists; nothing is overwritten.')
        codecs = set((project.GetRenderCodecs('mp4') or {}).values())
        codec = next((codec for codec in PREFERRED_CODECS if codec in codecs), None)
        if not codec or not project.SetCurrentRenderFormatAndCodec('mp4', codec):
            raise ResolveError(f'Resolve offers none of {", ".join(PREFERRED_CODECS)} for mp4.')
        self.facts['codec'] = codec
        project.SetCurrentRenderMode(1)
        settings = {'SelectAllFrames': True, 'TargetDir': str(self.run_dir), 'CustomName': temporary.stem,
                    'FormatWidth': width, 'FormatHeight': height, 'FrameRate': fps, 'ExportVideo': True,
                    'ExportAudio': True, 'AudioCodec': 'aac', 'AudioSampleRate': 48000}
        if not project.SetRenderSettings(settings):
            raise ResolveError('Resolve refused the render settings.')
        job = project.AddRenderJob()
        if not job or not project.StartRendering([job]):
            raise ResolveError('Resolve did not start the render.')
        self.facts['renderJob'] = job
        self.log(f'Rendering with {codec} to {temporary}')
        deadline = clock() + timeout_seconds
        while True:
            status = project.GetRenderJobStatus(job) or {}
            state = status.get('JobStatus')
            if state == 'Complete':
                break
            if state in ('Failed', 'Cancelled'):
                raise ResolveError(f'The render {state.lower()}: {status.get("Error") or "Resolve gave no reason"}.')
            if clock() > deadline:
                project.StopRendering()
                raise ResolveError(f'The render took longer than {timeout_seconds // 60} minutes and was stopped.')
            sleep(RENDER_POLL_SECONDS)
        if not temporary.is_file():
            raise ResolveError(f'Resolve says the render is complete but {temporary} is missing.')
        temporary.rename(final)
        return final


def _has_clips(timeline):
    """Any clip on any picture, sound or subtitle track."""
    return any(timeline.GetItemListInTrack(kind, index)
               for kind in ('video', 'audio', 'subtitle')
               for index in range(1, (timeline.GetTrackCount(kind) or 0) + 1))


def _same_number(value, number):
    try:
        return abs(float(value) - float(number)) < 0.001
    except (TypeError, ValueError):
        return False
