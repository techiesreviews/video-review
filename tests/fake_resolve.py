"""A stand-in for DaVinci Resolve's scripting objects that records every call, for tests without Resolve.

It follows the documented API names. Options let a test make Resolve misbehave: read endFrame as exclusive,
drop a frame on read-back, refuse fades, or fail a render.
"""
from pathlib import Path


class Recorder:
    def __init__(self):
        self.calls = []

    def note(self, *call):
        self.calls.append(call)

    def names(self):
        return [call[0] for call in self.calls]


class FakeClip:
    def __init__(self, log, kind, start, duration, left, item):
        self.log, self.kind, self.start, self.duration, self.left, self.item = log, kind, start, duration, left, item
        self.fades = None
        self.refuse_fades = False

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.start + self.duration

    def GetDuration(self):
        return self.duration

    def GetLeftOffset(self):
        return self.left

    def GetMediaPoolItem(self):
        return self.item

    def SetFades(self, fade_in, fade_out):
        self.log.note('SetFades', self.kind, fade_in, fade_out)
        if self.refuse_fades:
            return False
        self.fades = {'in': fade_in, 'out': fade_out}
        return True

    def GetFades(self):
        return self.fades


class FakeItem:
    def __init__(self, path, fps):
        self.path, self.fps = path, fps

    def GetClipProperty(self, key):
        return {'FPS': str(self.fps), 'File Path': self.path}.get(key)


class FakeTimeline:
    def __init__(self, log, name, options, start_frame=108000):
        self.log, self.name, self.options, self.start_frame = log, name, options, start_frame
        self.tracks = {'video': [], 'audio': []}
        self.markers = {}
        self.linked = []

    def GetName(self):
        return self.name

    def GetStartFrame(self):
        return self.start_frame

    def GetItemListInTrack(self, kind, index):
        return list(self.tracks[kind]) if index == 1 else []

    def DeleteClips(self, clips, ripple):
        self.log.note('DeleteClips', len(clips), ripple)
        for kind in self.tracks:
            self.tracks[kind] = [clip for clip in self.tracks[kind] if clip not in clips]
        return True

    def SetClipsLinked(self, clips, linked):
        self.log.note('SetClipsLinked', tuple(clip.kind for clip in clips), linked)
        self.linked.append(tuple(clips))
        return True

    def AddMarker(self, frame, color, name, note, duration):
        self.log.note('AddMarker', frame, color, name)
        self.markers[frame] = {'color': color, 'name': name, 'note': note, 'duration': duration}
        return True

    def GetMarkers(self):
        return dict(self.markers)

    def Export(self, path, kind):
        self.log.note('Export', Path(path).name, kind)
        Path(path).write_text('drt', encoding='utf-8')
        return True


class FakePool:
    def __init__(self, project):
        self.project, self.log, self.options = project, project.log, project.options

    def ImportMedia(self, paths):
        self.log.note('ImportMedia', tuple(paths))
        return [FakeItem(paths[0], self.options.get('itemFps', self.project.settings.get('timelineFrameRate')))]

    def CreateEmptyTimeline(self, name):
        self.log.note('CreateEmptyTimeline', name)
        timeline = FakeTimeline(self.log, name, self.options)
        self.project.timelines.append(timeline)
        return timeline

    def AppendToTimeline(self, infos):
        self.log.note('AppendToTimeline', len(infos))
        timeline = self.project.current
        placed = []
        for info in infos:
            duration = info['endFrame'] - info['startFrame'] + (0 if self.options.get('exclusiveEnd') else 1)
            if self.options.get('shortClipAt') == info['recordFrame'] - timeline.start_frame:
                duration -= 1
            kind = 'video' if info['mediaType'] == 1 else 'audio'
            clip = FakeClip(self.log, kind, info['recordFrame'], duration, info['startFrame'], info['mediaPoolItem'])
            clip.refuse_fades = self.options.get('refuseFades', False)
            timeline.tracks[kind].append(clip)
            placed.append(clip)
        return placed


class FakeProject:
    def __init__(self, log, name, options, timelines=()):
        self.log, self.name, self.options = log, name, options
        self.timelines = list(timelines)
        self.settings = {'timelineFrameRate': str(options.get('projectFps', '24'))}
        self.current = None
        self.render_settings = None
        self.jobs = {}
        self.polls = 0
        self.pool = FakePool(self)

    def GetName(self):
        return self.name

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def SetSetting(self, key, value):
        self.log.note('SetSetting', key, value)
        self.settings[key] = value
        return True

    def GetSetting(self, key):
        return self.settings.get(key)

    def GetMediaPool(self):
        return self.pool

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True

    def GetRenderCodecs(self, format_name):
        return {name: name for name in self.options.get('codecs', ('H264_NVIDIA', 'H264', 'H265'))}

    def SetCurrentRenderFormatAndCodec(self, format_name, codec):
        self.log.note('SetCurrentRenderFormatAndCodec', format_name, codec)
        return True

    def SetCurrentRenderMode(self, mode):
        return True

    def SetRenderSettings(self, settings):
        self.log.note('SetRenderSettings', dict(settings))
        self.render_settings = dict(settings)
        return True

    def AddRenderJob(self):
        job = f'job-{len(self.jobs) + 1}'
        self.jobs[job] = 'Ready'
        return job

    def StartRendering(self, jobs):
        self.log.note('StartRendering', tuple(jobs))
        return True

    def StopRendering(self):
        self.log.note('StopRendering')

    def GetRenderJobStatus(self, job):
        self.polls += 1
        outcome = self.options.get('renderOutcome', 'Complete')
        if outcome == 'Never' or self.polls < 3:
            return {'JobStatus': 'Rendering', 'CompletionPercentage': self.polls * 30}
        if outcome == 'Complete' and self.jobs[job] != 'Complete':
            self.jobs[job] = 'Complete'
            settings = self.render_settings
            write = self.options.get('writeRender', lambda path: path.write_bytes(b'rendered'))
            write(Path(settings['TargetDir']) / f'{settings["CustomName"]}.mp4')
        return {'JobStatus': outcome, 'Error': 'disk full' if outcome == 'Failed' else ''}


class FakeManager:
    def __init__(self, log, current, others, options):
        self.log, self.options = log, options
        self.current = current
        self.others = list(others)

    def GetCurrentProject(self):
        return self.current

    def GetProjectListInCurrentFolder(self):
        return list(self.others)

    def ExportProject(self, name, path):
        self.log.note('ExportProject', name, Path(path).name)
        Path(path).write_text('drp', encoding='utf-8')
        return True

    def LoadProject(self, name):
        self.log.note('LoadProject', name)
        self.current = FakeProject(self.log, name, self.options)
        return self.current

    def CreateProject(self, name):
        self.log.note('CreateProject', name)
        self.current = FakeProject(self.log, name, self.options)
        return self.current

    def SaveProject(self):
        self.log.note('SaveProject')
        return True


class FakeResolve:
    EXPORT_DRT = 'drt'

    def __init__(self, current='Untitled Project', others=(), product='DaVinci Resolve Studio', **options):
        self.log = Recorder()
        self.product = product
        project = None
        if current is not None:
            project = FakeProject(self.log, current, options, options.get('currentTimelines', ()))
        self.manager = FakeManager(self.log, project, others, options)

    def GetProductName(self):
        return self.product

    def GetVersionString(self):
        return '21.1.1'

    def GetProjectManager(self):
        return self.manager
