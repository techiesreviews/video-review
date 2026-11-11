"""Offline checks for cut-to-review.py and resolve_build.py: a fake Resolve, a fake ffprobe, no network."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/video'))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
spec = importlib.util.spec_from_file_location('cut_to_review', ROOT / 'scripts/video/cut-to-review.py')
cut_to_review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cut_to_review)
import resolve_build  # noqa: E402
from fake_resolve import FakeResolve  # noqa: E402

FPS = 30
SOURCE_FRAMES = 200
# hello world | um (removed) | next thing: one join, at frame 34.
WORDS = [['hello', 1.0, 1.4], ['world', 1.5, 1.9], ['um', 2.5, 2.7], ['next', 4.0, 4.4], ['thing', 4.5, 4.9]]
RANGES = ((26, 60), (117, 150))


def make_cutlist(ranges=RANGES, **extra):
    segments, start = [], 0
    for index, (source_in, source_out) in enumerate(ranges, 1):
        segments.append({'id': f'C{index:02}', 'sourceInFrame': source_in, 'sourceOutFrame': source_out,
                         'timelineStartFrame': start, 'durationFrames': source_out - source_in})
        start += source_out - source_in
    return {'schemaVersion': 1, 'fps': FPS, 'timelineName': 'Test', 'durationFrames': start, 'status': 'proposal',
            'audioFadeMs': 15, 'segments': segments, **extra}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Workspace:
    """A cut folder, a recording and a renders folder in a temporary directory."""

    def __init__(self, cutlist=None, approve=True, approval=None):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cut = self.root / 'cut'
        self.cut.mkdir()
        self.source = self.root / 'recording.mov'
        self.source.write_bytes(b'recording')
        self.renders = self.root / 'renders'
        self.cutlist = cutlist or make_cutlist(sourcePath=str(self.source))
        self.write_cutlist(self.cutlist)
        (self.cut / 'words.json').write_text(json.dumps(WORDS), encoding='utf-8')
        if approve:
            self.approve(**(approval or {}))

    def write_cutlist(self, cutlist):
        (self.cut / 'cutlist.json').write_text(json.dumps(cutlist, indent=2), encoding='utf-8')

    def approve(self, **changes):
        approval = {'status': 'approved', 'approvedBy': 'Lex', 'words': 'Yes, build it.', 'date': '2026-11-11',
                    'cutlistSha256': sha((self.cut / 'cutlist.json').read_bytes()), **changes}
        (self.cut / 'approval.json').write_text(json.dumps(approval), encoding='utf-8')

    def args(self, *extra, dry_run=False):
        argv = [str(self.cut), '--video', 'test-video', '--renders-root', str(self.renders), *extra]
        return cut_to_review.parse(argv + (['--dry-run'] if dry_run else []))

    def close(self):
        self.temp.cleanup()


class FakeProbe:
    """ffprobe for the recording (constant 30 fps, 200 frames) and for the render (exact length)."""

    def __init__(self, rate='30/1', average='30/1', streams=('video', 'audio'), render_seconds=None):
        self.rate, self.average, self.streams, self.render_seconds = rate, average, streams, render_seconds
        self.calls = []

    def __call__(self, path, *arguments):
        self.calls.append(Path(path).name)
        if 'stream=codec_type:format=duration' in arguments:
            seconds = self.render_seconds if self.render_seconds is not None else 67 / FPS
            return {'streams': [{'codec_type': kind} for kind in self.streams], 'format': {'duration': str(seconds)}}
        return {'streams': [{'r_frame_rate': self.rate, 'avg_frame_rate': self.average, 'width': 1920,
                             'height': 1080, 'nb_frames': str(SOURCE_FRAMES)}], 'format': {}}


def no_network(*args, **kwargs):
    raise AssertionError('a network connection was attempted before --upload')


class Base(unittest.TestCase):
    def setUp(self):
        self.space = Workspace()
        self.probe = FakeProbe()
        patcher = mock.patch.object(cut_to_review, 'ffprobe_json', self.probe)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.space.close)

    def build(self, resolve=None, *extra, dry_run=False):
        self.resolve = resolve or FakeResolve()
        output = io.StringIO()
        with mock.patch.object(socket.socket, 'connect', no_network), contextlib.redirect_stdout(output):
            code = cut_to_review.build(self.space.args(*extra, dry_run=dry_run), connect=lambda: self.resolve,
                                       sleep=lambda seconds: None)
        self.output = output.getvalue()
        return code

    def refuses(self, pattern, resolve=None, *extra, dry_run=False):
        with self.assertRaisesRegex(cut_to_review.Stop, pattern):
            self.build(resolve, *extra, dry_run=dry_run)

    @property
    def run_dir(self):
        return self.space.renders / 'test-video' / 'v1'


class ApprovalGate(Base):
    def test_no_approval_refuses_the_proposal(self):
        (self.space.cut / 'approval.json').unlink()
        self.refuses('proposal without Lex\'s approval')

    def test_cut_list_changed_after_approval(self):
        changed = make_cutlist(((26, 60), (117, 151)), sourcePath=str(self.space.source))
        self.space.write_cutlist(changed)
        self.refuses('changed after Lex approved')

    def test_status_must_be_approved(self):
        self.space.approve(status='proposal')
        self.refuses('not "approved"')

    def test_needs_words_and_date(self):
        self.space.approve(words=' ')
        self.refuses('no words')
        self.space.approve(date='11/11/2026')
        self.refuses('YYYY-MM-DD')

    def test_approval_block_inside_the_cut_list(self):
        (self.space.cut / 'approval.json').unlink()
        cutlist = make_cutlist(sourcePath=str(self.space.source))
        block = {'status': 'approved', 'approvedBy': 'Lex', 'words': 'Go.', 'date': '2026-11-11',
                 'cutlistSha256': cut_to_review.canonical_sha256(cutlist)}
        self.space.write_cutlist({**cutlist, 'approval': block})
        self.assertEqual(self.build(dry_run=True), 0)
        self.space.write_cutlist({**cutlist, 'durationFrames': 1, 'approval': block})
        self.refuses('changed after Lex approved', dry_run=True)


class Preflight(Base):
    def test_schema_version(self):
        self.space.write_cutlist({**self.space.cutlist, 'schemaVersion': 2})
        self.space.approve()
        self.refuses('schemaVersion 2')

    def test_missing_source(self):
        self.space.source.unlink()
        self.refuses('recording was not found')

    def test_source_flag_replaces_a_missing_source_path(self):
        cutlist = make_cutlist()
        self.space.write_cutlist(cutlist)
        self.space.approve()
        self.refuses('no sourcePath', dry_run=True)
        self.assertEqual(self.build(None, '--source', str(self.space.source), dry_run=True), 0)

    def test_wrong_hash(self):
        self.space.write_cutlist({**self.space.cutlist, 'sourceSha256': sha(b'another recording')})
        self.space.approve()
        self.refuses('SHA-256 differs')

    def test_right_hash(self):
        self.space.write_cutlist({**self.space.cutlist, 'sourceSha256': sha(b'recording')})
        self.space.approve()
        self.assertEqual(self.build(dry_run=True), 0)

    def test_variable_frame_rate(self):
        self.probe.average = '2997/100'
        self.refuses('variable frame rate')

    def test_other_frame_rate(self):
        self.probe.rate = self.probe.average = '25/1'
        self.refuses('25 fps; the cut list says 30')

    def test_gap_between_segments(self):
        cutlist = make_cutlist(sourcePath=str(self.space.source))
        cutlist['segments'][1]['timelineStartFrame'] += 5
        cutlist['durationFrames'] += 5
        self.space.write_cutlist(cutlist)
        self.space.approve()
        self.refuses('without gaps')

    def test_duration_must_add_up(self):
        self.space.write_cutlist({**self.space.cutlist, 'durationFrames': 70})
        self.space.approve()
        self.refuses('add up to 67 frames')

    def test_segment_past_the_recording(self):
        cutlist = make_cutlist(((26, 60), (117, 250)), sourcePath=str(self.space.source))
        self.space.write_cutlist(cutlist)
        self.space.approve()
        self.refuses('after the recording\'s last frame')

    def test_never_overwrites_a_run(self):
        self.run_dir.mkdir(parents=True)
        self.refuses('already exists')

    def test_resolve_closed(self):
        def closed():
            raise resolve_build.ResolveError('Resolve is not reachable.')
        with self.assertRaisesRegex(cut_to_review.Stop, 'not reachable'):
            cut_to_review.build(self.space.args(), connect=closed)
        self.assertFalse(self.run_dir.exists())

    def test_needs_studio(self):
        self.refuses('needs DaVinci Resolve Studio', FakeResolve(product='DaVinci Resolve'))
        self.assertFalse(self.run_dir.exists())


class DryRun(Base):
    def test_writes_checks_and_review_data_without_resolve(self):
        def must_not_connect():
            raise AssertionError('dry run connected to Resolve')
        with mock.patch.object(socket.socket, 'connect', no_network), contextlib.redirect_stdout(io.StringIO()):
            code = cut_to_review.build(self.space.args(dry_run=True), connect=must_not_connect)
        self.assertEqual(code, 0)
        run_dir = self.space.renders / 'test-video' / 'v1-dry-run'
        names = sorted(path.name for path in run_dir.iterdir())
        self.assertIn('join-check.md', names)
        self.assertIn('captions.phrases.srt', names)
        self.assertIn('review-upload.json', names)
        self.assertFalse([name for name in names if name.endswith('.mp4')])
        payload = json.loads((run_dir / 'review-upload.json').read_text(encoding='utf-8'))
        self.assertTrue(payload['dryRun'])
        self.assertFalse(payload['sent'])


class ResolveSteps(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name)
        self.segments = resolve_build.segments_from_cutlist(make_cutlist())

    def builder(self, resolve):
        return resolve_build.Builder(resolve, self.run_dir, log=lambda text: None)

    def build(self, resolve, markers=()):
        builder = self.builder(resolve)
        builder.open_project('Test project')
        builder.build_timeline('/footage/recording.mov', FPS, 1920, 1080, self.segments, 15, list(markers))
        return builder

    def test_fade_frames(self):
        self.assertAlmostEqual(resolve_build.fade_frames(15, 30), 0.45)
        self.assertAlmostEqual(resolve_build.fade_frames(15, 25), 0.375)

    def test_clip_info_places_picture_and_sound_together(self):
        infos = resolve_build.clip_infos('item', self.segments[1], 108000)
        self.assertEqual([info['mediaType'] for info in infos], [1, 2])
        self.assertEqual({(info['startFrame'], info['endFrame'], info['recordFrame']) for info in infos},
                         {(117, 149, 108034)})
        self.assertEqual(resolve_build.clip_infos('item', self.segments[1], 0, False)[0]['endFrame'], 150)

    def test_builds_from_an_empty_untitled_project(self):
        resolve = FakeResolve(projectFps=24)
        builder = self.build(resolve, [resolve_build.Marker(34, 'Red', 'Join 1', 'problem')])
        names = resolve.log.names()
        self.assertIn(('CreateProject', 'Test project'), resolve.log.calls)
        self.assertIn(('SetSetting', 'timelineFrameRate', '30'), resolve.log.calls)
        self.assertNotIn('ExportProject', names)
        timeline = resolve.manager.current.current
        self.assertEqual(timeline.GetName(), resolve_build.TIMELINE_NAME)
        self.assertEqual([(clip.GetStart(), clip.GetDuration(), clip.GetLeftOffset())
                          for clip in timeline.tracks['audio']], [(108000, 34, 26), (108034, 33, 117)])
        self.assertEqual(len(timeline.linked), 2)
        self.assertEqual([call[2:] for call in resolve.log.calls if call[0] == 'SetFades'], [(0.45, 0.45)] * 2)
        self.assertEqual(timeline.markers[34]['color'], 'Red')
        self.assertTrue((self.run_dir / f'{resolve_build.TIMELINE_NAME}.drt').exists())
        self.assertEqual(names[-1], 'SaveProject')
        self.assertTrue(builder.facts['endFrameInclusive'])

    def test_corrects_an_exclusive_end_frame(self):
        resolve = FakeResolve(exclusiveEnd=True)
        builder = self.build(resolve)
        self.assertIn(('DeleteClips', 2, False), resolve.log.calls)
        self.assertFalse(builder.facts['endFrameInclusive'])
        clips = resolve.manager.current.current.tracks['video']
        self.assertEqual([clip.GetDuration() for clip in clips], [34, 33])

    def test_read_back_catches_a_wrong_clip(self):
        with self.assertRaisesRegex(resolve_build.ResolveError, 'Segment C02 video reads back as start 34, length 32'):
            self.build(FakeResolve(shortClipAt=34))

    def test_long_cut_goes_in_chunks(self):
        ranges = [(index * 10, index * 10 + 5) for index in range(95)]
        self.segments = resolve_build.segments_from_cutlist(make_cutlist(ranges))
        resolve = FakeResolve()
        self.build(resolve)
        self.assertEqual([call[1] for call in resolve.log.calls if call[0] == 'AppendToTimeline'], [2, 80, 80, 28])

    def test_refused_fades_stop_before_saving(self):
        resolve = FakeResolve(refuseFades=True)
        with self.assertRaisesRegex(resolve_build.ResolveError, 'audio fades'):
            self.build(resolve)
        self.assertNotIn('SaveProject', resolve.log.names())

    def test_never_touches_an_unexpected_project(self):
        resolve = FakeResolve(current='Client edit', others=['Test project'])
        with self.assertRaisesRegex(resolve_build.ResolveError, '"Client edit" open'):
            self.build(resolve)
        self.assertEqual(resolve.log.calls, [])

    def test_untitled_project_with_timelines_counts_as_unexpected(self):
        resolve = FakeResolve(currentTimelines=['something'])
        with self.assertRaisesRegex(resolve_build.ResolveError, 'Untitled Project'):
            self.build(resolve)
        self.assertEqual(resolve.log.calls, [])

    def test_checkpoint_before_using_an_existing_project(self):
        resolve = FakeResolve(current=None, others=['Test project'], projectFps=30)
        builder = self.build(resolve)
        self.assertEqual(resolve.log.calls[:2], [('ExportProject', 'Test project', 'Test project-before.drp'),
                                                 ('LoadProject', 'Test project')])
        self.assertEqual(builder.facts['checkpoint'], 'Test project-before.drp')

    def test_refuses_an_existing_working_timeline_with_clips(self):
        resolve = FakeResolve(current='Test project', projectFps=30)
        project = resolve.manager.current
        timeline = project.pool.CreateEmptyTimeline(resolve_build.TIMELINE_NAME)
        project.SetCurrentTimeline(timeline)
        project.pool.AppendToTimeline(resolve_build.clip_infos('item', self.segments[0], 108000))
        resolve.log.calls.clear()
        builder = self.builder(resolve)
        builder.open_project('Test project')
        with self.assertRaisesRegex(resolve_build.ResolveError, 'already has a timeline'):
            builder.build_timeline('/footage/recording.mov', FPS, 1920, 1080, self.segments, 15, [])
        self.assertNotIn('ImportMedia', resolve.log.names())

    def test_refuses_a_project_at_another_frame_rate(self):
        resolve = FakeResolve(current='Test project', currentTimelines=[], projectFps=25)
        project = resolve.manager.current
        project.timelines.append(project.pool.CreateEmptyTimeline('Other'))
        builder = self.builder(resolve)
        builder.open_project('Test project')
        with self.assertRaisesRegex(resolve_build.ResolveError, '25 fps'):
            builder.build_timeline('/footage/recording.mov', FPS, 1920, 1080, self.segments, 15, [])

    def render(self, resolve, timeout=600):
        builder = self.build(resolve)
        return builder, builder.render('test-video-v1', FPS, 1920, 1080, timeout, sleep=lambda seconds: None)

    def test_render_polls_then_renames(self):
        resolve = FakeResolve()
        builder, final = self.render(resolve)
        self.assertEqual(final.name, 'test-video-v1.mp4')
        self.assertTrue(final.is_file())
        self.assertFalse((self.run_dir / 'test-video-v1.rendering.mp4').exists())
        settings = resolve.manager.current.render_settings
        self.assertEqual(settings['CustomName'], 'test-video-v1.rendering')
        self.assertTrue(settings['SelectAllFrames'])
        self.assertEqual((settings['AudioCodec'], settings['AudioSampleRate']), ('aac', 48000))
        self.assertEqual(builder.facts['codec'], 'H264_NVIDIA')
        self.assertGreaterEqual(resolve.manager.current.polls, 3)

    def test_render_falls_back_to_h264(self):
        builder, _ = self.render(FakeResolve(codecs=('H264', 'H265')))
        self.assertEqual(builder.facts['codec'], 'H264')

    def test_failed_render(self):
        with self.assertRaisesRegex(resolve_build.ResolveError, 'render failed: disk full'):
            self.render(FakeResolve(renderOutcome='Failed'))
        self.assertFalse((self.run_dir / 'test-video-v1.mp4').exists())

    def test_render_timeout_stops_it(self):
        resolve = FakeResolve(renderOutcome='Never')
        ticks = iter(range(0, 100000, 100))
        builder = self.build(resolve)
        with self.assertRaisesRegex(resolve_build.ResolveError, 'longer than'):
            builder.render('test-video-v1', FPS, 1920, 1080, 600, sleep=lambda seconds: None,
                           clock=lambda: next(ticks))
        self.assertIn('StopRendering', resolve.log.names())


class FullRun(Base):
    def test_stops_and_asks_before_the_upload(self):
        self.assertEqual(self.build(), cut_to_review.STOP_EXIT)
        self.assertIn('Stopped before the upload', self.output)
        self.assertIn('--upload', self.output)
        video = self.run_dir / 'test-video-v1.mp4'
        payload = json.loads((self.run_dir / 'review-upload.json').read_text(encoding='utf-8'))
        self.assertFalse(payload['dryRun'])
        self.assertFalse(payload['sent'])
        self.assertEqual(payload['file'], {'name': video.name, 'sha256': sha(video.read_bytes())})
        self.assertEqual(payload['register']['id'], 'test-video-v1')
        self.assertEqual(payload['register']['keepRanges'], [[26, 60], [117, 150]])
        self.assertEqual(payload['register']['durationMs'], round(67 * 1000 / 30))
        words = payload['transcript']['words']
        self.assertEqual([word['text'] for word in words], ['hello', 'world', 'next', 'thing'])
        self.assertEqual(words[2]['startMs'], round((34 / 30 + 4.0 - 117 / 30) * 1000))
        self.assertEqual(payload['transcript']['cuts'], [{'id': 'join-1', 'atMs': 1133, 'risk': False}])
        report = (self.run_dir / 'run-report.md').read_text(encoding='utf-8')
        self.assertIn('Not listened', report)
        self.assertNotIn('upload-done', ' '.join(path.name for path in self.run_dir.iterdir()))

    def test_red_join_marker_and_message(self):
        cutlist = make_cutlist(((26, 55), (117, 150)), sourcePath=str(self.space.source))
        self.space.write_cutlist(cutlist)
        self.space.approve()
        self.probe.render_seconds = 62 / FPS
        self.assertEqual(self.build(), cut_to_review.STOP_EXIT)
        timeline = self.resolve.manager.current.current
        self.assertEqual(timeline.markers[29]['color'], 'Red')
        self.assertIn('Joins to hear first: 1 at', self.output)
        self.assertIn('Recommend: listen to the red joins, then say upload.', self.output)
        payload = json.loads((self.run_dir / 'review-upload.json').read_text(encoding='utf-8'))
        self.assertTrue(payload['transcript']['cuts'][0]['risk'])

    def test_render_with_the_wrong_length_is_refused(self):
        self.probe.render_seconds = 70 / FPS
        self.refuses('more than a frame apart')
        self.assertFalse((self.run_dir / 'review-upload.json').exists())

    def test_render_without_sound_is_refused(self):
        self.probe.streams = ('video',)
        self.refuses('one video and one audio stream')


class Upload(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.build(), cut_to_review.STOP_EXIT)
        self.data = self.space.root / 'review-data'

    def upload(self, *extra, environ=None, post=None):
        args = cut_to_review.parse(['--upload', str(self.run_dir), *extra])
        with contextlib.redirect_stdout(io.StringIO()):
            return cut_to_review.upload(args, environ=environ or {}, post=post or no_network)

    def test_local_upload_registers_transcript_and_opens(self):
        self.assertEqual(self.upload('--data-dir', str(self.data)), 0)
        manifest = json.loads((self.data / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual([item['id'] for item in manifest], ['test-video-v1'])
        self.assertEqual(manifest[0]['keepRanges'], [[26, 60], [117, 150]])
        self.assertEqual(manifest[0]['fingerprint'], sha((self.run_dir / 'test-video-v1.mp4').read_bytes()))
        transcripts = list((self.data).rglob('*.json'))
        self.assertTrue(any('transcript' in str(path) for path in transcripts))
        view = json.loads((self.data / 'view-request.json').read_text(encoding='utf-8'))
        self.assertEqual(view['videoId'], 'test-video-v1')

    def test_never_reuses_an_id(self):
        self.upload('--data-dir', str(self.data))
        with self.assertRaisesRegex(cut_to_review.Stop, 'never reused'):
            self.upload('--data-dir', str(self.data))

    def test_changed_file_is_refused(self):
        (self.run_dir / 'test-video-v1.mp4').write_bytes(b'something else')
        with self.assertRaisesRegex(cut_to_review.Stop, 'changed since the render check'):
            self.upload('--data-dir', str(self.data))
        self.assertFalse((self.data / 'manifest.json').exists())

    def test_hosted_needs_the_token(self):
        with self.assertRaisesRegex(cut_to_review.Stop, 'VIDEO_REVIEW_TOKEN'):
            self.upload('--hosted')

    def test_hosted_upload_sends_file_name_and_never_prints_the_token(self):
        sent = []

        def post(url, message, token):
            sent.append((url, message, token))
            if message['method'] == 'initialize':
                return {'jsonrpc': '2.0', 'id': message['id'], 'result': {}}
            return {'jsonrpc': '2.0', 'id': message['id'], 'result': {'content': [{'type': 'text', 'text': '{}'}]}}
        output = io.StringIO()
        args = cut_to_review.parse(['--upload', str(self.run_dir), '--hosted'])
        with contextlib.redirect_stdout(output):
            cut_to_review.upload(args, environ={'VIDEO_REVIEW_TOKEN': 'vr_secret'}, post=post)
        self.assertEqual({url for url, _, _ in sent}, {'https://video.techies.tools/mcp'})
        calls = [message['params']['name'] for _, message, _ in sent if message['method'] == 'tools/call']
        self.assertEqual(calls, ['register_video', 'set_transcript', 'open_video'])
        register = sent[1][1]['params']['arguments']
        self.assertEqual(register['fileName'], 'test-video-v1.mp4')
        self.assertNotIn('path', register)
        self.assertNotIn('vr_secret', output.getvalue())


class HostedRetry(unittest.TestCase):
    def test_waits_for_the_tab_to_list_a_new_video(self):
        replies = iter([{'result': {}}, {'result': {'content': [{'text': 'Unknown video version'}], 'isError': True}},
                        {'result': {'content': [{'text': '{}'}]}}])
        naps = []
        call = cut_to_review.hosted_caller('https://example.test', 'token', lambda url, message, token: next(replies),
                                           sleep=naps.append)
        call('set_transcript', {'videoId': 'x'})
        self.assertEqual(naps, [cut_to_review.HOSTED_RETRY_SECONDS])

    def test_other_refusals_stop_at_once(self):
        replies = iter([{'result': {}}, {'result': {'content': [{'text': 'That id already exists'}], 'isError': True}}])
        call = cut_to_review.hosted_caller('https://example.test', 'token', lambda url, message, token: next(replies),
                                           sleep=lambda seconds: self.fail('retried'))
        with self.assertRaisesRegex(cut_to_review.Stop, 'already exists'):
            call('register_video', {})


class DryRunUpload(Base):
    def test_a_dry_run_cannot_be_uploaded(self):
        self.assertEqual(self.build(dry_run=True), 0)
        args = cut_to_review.parse(['--upload', str(self.space.renders / 'test-video' / 'v1-dry-run')])
        with self.assertRaisesRegex(cut_to_review.Stop, 'dry run'):
            cut_to_review.upload(args, environ={}, post=no_network)


if __name__ == '__main__':
    unittest.main()
