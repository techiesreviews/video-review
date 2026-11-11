"""Turn an approved cut list into a review video in one command, then stop and ask Lex before the upload.

Usage:
  python cut-to-review.py <cut-folder> --video <slug> [--version v1] [--project NAME] [--words words.json]
                          [--source PATH] [--renders-root DIR] [--dry-run]
  python cut-to-review.py --upload <run-folder> [--hosted] [--data-dir DIR]

The first form checks the cut (Lex's approval of this exact cut list, the recording, its hash when the cut list
has sourceSha256, and a constant frame rate), writes the join check and captions, builds one fresh "A-CUT — working" timeline in DaVinci Resolve
Studio, renders it under a temporary name, renames it, checks it with ffprobe and writes the review page data
into renders/<slug>/<version>/ without sending anything. Then it stops with exit code 3 and says what to listen
to first. --dry-run does the checks, the join check, captions and the review data only: no Resolve, no render.

The second form runs only after Lex says upload: it registers the video on the review page (the file stays on
this computer; nothing goes to YouTube), attaches the transcript and opens it. The local review page is the
default; --hosted uses video.techies.tools with the token in VIDEO_REVIEW_TOKEN, which is never printed.
"""
import argparse
from fractions import Fraction
import hashlib
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import cut_timeline  # noqa: E402
import resolve_build  # noqa: E402


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


join_check = _load('join_check', 'join-check.py')
build_captions = _load('build_captions', 'build-captions.py')

STOP_EXIT = 3
NAME = re.compile(r'[A-Za-z0-9._-]{1,48}')
DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
LOCAL_URL = 'http://127.0.0.1:8879'
HOSTED_URL = 'https://video.techies.tools'
UPLOAD_FILE = 'review-upload.json'
DONE_FILE = 'review-upload-record.json'
DEFAULT_RENDER_TIMEOUT_MINUTES = 180
HOSTED_RETRIES = 5
HOSTED_RETRY_SECONDS = 2


class Stop(Exception):
    """A check failed: the message says what and what to do. Nothing after the check has run."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        for chunk in iter(lambda: file.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(data):
    """The hash of a JSON object independent of spacing and key order: used for an approval block inside it."""
    text = json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def read_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        raise Stop(f'{what} not found: {path}.') from None
    except (OSError, ValueError) as error:
        raise Stop(f'{what} could not be read ({path}): {error}.') from None


# Step 1: the approval gate and the read-only checks.
def check_approval(cut_folder, cutlist_path, cutlist):
    """Lex's approval of this exact cut list: approval.json next to it, or an approval block inside it.

    approval.json: {"status": "approved", "approvedBy": "Lex", "words": "<what he said>", "date": "YYYY-MM-DD",
    "cutlistSha256": "<SHA-256 of cutlist.json's bytes>"}. A block inside the cut list has the same keys, and its
    cutlistSha256 is the canonical hash of the cut list without the block (canonical_sha256).
    """
    file = Path(cut_folder) / 'approval.json'
    if file.exists():
        approval, actual = read_json(file, 'The approval'), sha256_file(cutlist_path)
    elif isinstance(cutlist.get('approval'), dict):
        approval = cutlist['approval']
        actual = canonical_sha256({key: value for key, value in cutlist.items() if key != 'approval'})
    else:
        raise Stop(f'The cut list is a {cutlist.get("status", "cut list")} without Lex\'s approval. He approves the '
                   f'paper edit first; then write {file} (status, approvedBy, words, date, cutlistSha256).')
    if not isinstance(approval, dict) or approval.get('status') != 'approved':
        raise Stop(f'The approval says "{approval.get("status") if isinstance(approval, dict) else approval}", '
                   'not "approved".')
    for key in ('approvedBy', 'words'):
        if not isinstance(approval.get(key), str) or not approval[key].strip():
            raise Stop(f'The approval has no {key}.')
    if not isinstance(approval.get('date'), str) or not DATE.fullmatch(approval['date']):
        raise Stop('The approval needs its date as YYYY-MM-DD.')
    wanted = approval.get('cutlistSha256')
    if not isinstance(wanted, str) or not cut_timeline.SHA256.fullmatch(wanted):
        raise Stop('The approval needs the SHA-256 of the cut list Lex approved (cutlistSha256).')
    if wanted.lower() != actual:
        raise Stop('The cut list changed after Lex approved it. He approves the new one before anything is built.')
    return approval


def check_segments(cutlist, source_frames=None):
    """Whole-frame segments, back to back from frame 0 with no gap, adding up to durationFrames, inside the source."""
    if cutlist.get('schemaVersion') != 1:
        raise Stop(f'Cut list schemaVersion {cutlist.get("schemaVersion")!r}; this tool reads version 1.')
    try:
        timeline = cut_timeline.load_cutlist(cutlist)
    except ValueError as error:
        raise Stop(f'Cut list: {error}') from None
    segments = resolve_build.segments_from_cutlist(cutlist)
    position = 0
    for segment in segments:
        if segment.timeline_start != position:
            raise Stop(f'Segment {segment.id} starts at timeline frame {segment.timeline_start}, expected {position}: '
                       'segments must follow each other without gaps.')
        if source_frames is not None and segment.source_out > source_frames:
            raise Stop(f'Segment {segment.id} ends at source frame {segment.source_out}, after the recording\'s '
                       f'last frame ({source_frames}).')
        position += segment.duration
    if cutlist.get('durationFrames') != position:
        raise Stop(f'The segments add up to {position} frames; the cut list says durationFrames '
                   f'{cutlist.get("durationFrames")}.')
    return timeline, segments


def ffprobe_json(path, *arguments):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', *arguments, '-of', 'json', str(path)],
                                capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise Stop('ffprobe is not installed or not on PATH.') from None
    except subprocess.CalledProcessError as error:
        raise Stop(f'ffprobe could not read {path}: {error.stderr.strip()}') from None
    return json.loads(result.stdout)


def probe_source(path, fps):
    """Return width, height and frame count; refuse a variable frame rate or one that differs from the cut list."""
    data = ffprobe_json(path, '-select_streams', 'v:0', '-show_entries',
                        'stream=r_frame_rate,avg_frame_rate,width,height,nb_frames,duration:format=duration')
    streams = data.get('streams') or []
    if not streams:
        raise Stop(f'{path} has no video stream.')
    stream = streams[0]
    try:
        rate, average = Fraction(stream['r_frame_rate']), Fraction(stream['avg_frame_rate'])
    except (KeyError, ValueError, ZeroDivisionError):
        raise Stop(f'ffprobe gives no frame rate for {path}.') from None
    duration = float(stream.get('duration') or data.get('format', {}).get('duration') or 0)
    # A constant-rate file's average can differ by a rounding of its last frame (1800000/60001); a variable one
    # drifts by many frames over the file.
    drift = abs(float(rate) - float(average)) * duration if duration else (0 if rate == average else math.inf)
    if drift > 1.5:
        raise Stop(f'{path} has a variable frame rate ({float(rate):g} vs {float(average):g} fps average). '
                   'Convert it to a constant frame rate first.')
    if abs(float(rate) - fps) > 0.001:
        raise Stop(f'The recording runs at {float(rate):g} fps; the cut list says {fps:g}.')
    frames = stream.get('nb_frames')
    if not str(frames or '').isdigit():
        frames = round(duration * float(rate))
    return {'width': int(stream['width']), 'height': int(stream['height']), 'frames': int(frames),
            'fps': float(rate)}


def preflight(args):
    """Every read-only check, in order. Returns what the later steps need; touches nothing."""
    if not NAME.fullmatch(args.video) or not NAME.fullmatch(args.version):
        raise Stop('--video and --version take letters, digits, dot, dash or underscore (up to 48 each).')
    cut_folder = Path(args.cut_folder)
    cutlist_path = cut_folder / 'cutlist.json'
    cutlist = read_json(cutlist_path, 'The cut list')
    if not isinstance(cutlist, dict):
        raise Stop('The cut list must be a JSON object.')
    if cutlist.get('schemaVersion') != 1:
        raise Stop(f'Cut list schemaVersion {cutlist.get("schemaVersion")!r}; this tool reads version 1.')
    approval = check_approval(cut_folder, cutlist_path, cutlist)
    words_path = Path(args.words) if args.words else cut_folder / 'words.json'
    transcript = read_json(words_path, 'The word-timed transcript (--words)')
    try:
        cut_timeline.check_same_source(transcript, cutlist)
        words = cut_timeline.load_words(transcript)
    except ValueError as error:
        raise Stop(f'Transcript: {error}') from None
    named = args.source or cutlist.get('sourcePath')
    source = Path(named) if named else None
    if source is None or not source.is_file():
        raise Stop(f'The recording was not found ({source or "no sourcePath in the cut list"}). Pass --source.')
    fps = cutlist.get('fps')
    timeline, _ = check_segments(cutlist)
    media = probe_source(source, float(fps))
    check_segments(cutlist, media['frames'])
    wanted = cutlist.get('sourceSha256')
    if wanted and sha256_file(source) != str(wanted).lower():
        raise Stop(f'{source} is not the recording the cut list was made from (SHA-256 differs).')
    suffix = '-dry-run' if args.dry_run else ''
    run_dir = Path(args.renders_root or ROOT / 'renders') / args.video / f'{args.version}{suffix}'
    if run_dir.exists():
        raise Stop(f'{run_dir} already exists. Choose a new --version; older renders stay as they are.')
    return {'cutlist': cutlist, 'cutlistPath': cutlist_path, 'approval': approval, 'words': words,
            'wordsPath': words_path, 'source': source.resolve(), 'fps': fps, 'timeline': timeline,
            'segments': resolve_build.segments_from_runs(timeline), 'media': media, 'runDir': run_dir,
            'hashChecked': bool(wanted)}


# Step 2: the join check and captions, into the run folder.
def write_new(path, text):
    with open(path, 'x', encoding='utf-8', newline='\n') as file:
        file.write(text)


def write_checks(plan):
    run_dir, timeline, words = plan['runDir'], plan['timeline'], plan['words']
    report = join_check.check_joins(words, timeline)
    report['inputs'] = {'words': plan['wordsPath'].name, 'cutList': plan['cutlistPath'].name}
    write_new(run_dir / 'join-check.md', join_check.render_markdown(report))
    write_new(run_dir / 'join-check.json', json.dumps(report, ensure_ascii=False, indent=2))
    pieces = build_captions.one_per_word(cut_timeline.from_first_frame(cut_timeline.retained_words(words, timeline),
                                                                       timeline))
    files = {
        'captions.phrases.srt': build_captions.phrase_srt(pieces, timeline.duration),
        'captions.phrases.vtt': build_captions.phrase_vtt(pieces, timeline.duration),
        'captions.words.srt': build_captions.word_srt(pieces),
        'captions.words.vtt': build_captions.word_vtt(pieces),
        'captions.timed-words.vtt': build_captions.timed_vtt(pieces, timeline.duration),
    }
    for name, text in files.items():
        write_new(run_dir / name, text)
    return report


def red_joins(report):
    return [join for join in report['joins'] if any(item['severity'] == 'problem' for item in join['flags'])]


def markers(report):
    """One marker per join: red where the join check found a problem, yellow otherwise."""
    red = {join['index'] for join in red_joins(report)}
    return [resolve_build.Marker(join['timelineFrame'], 'Red' if join['index'] in red else 'Yellow',
                                 f'Join {join["index"]}',
                                 '; '.join(item['message'] for item in join['flags']) or 'Not listened')
            for join in report['joins']]


# Step 5: check the rendered file.
def verify_render(path, duration_frames, fps):
    data = ffprobe_json(path, '-show_entries', 'stream=codec_type,codec_tag_string:format=duration')
    streams = data.get('streams') or []
    # Resolve adds a timecode data stream (tag tmcd) to every MP4; it is not picture or sound.
    kinds = [stream.get('codec_type') for stream in streams
             if not (stream.get('codec_type') == 'data' and stream.get('codec_tag_string') == 'tmcd')]
    if sorted(kinds) != ['audio', 'video']:
        raise Stop(f'{path} has the streams {kinds}; expected one video and one audio stream.')
    duration = float(data.get('format', {}).get('duration') or 0)
    wanted = duration_frames / fps
    if abs(duration - wanted) > 1 / fps:
        raise Stop(f'{path} lasts {duration:.3f} s; the cut list says {wanted:.3f} s (more than a frame apart).')
    return duration


# Step 6: the review page data, written but not sent.
def keep_ranges(timeline):
    return [[run.source_in, run.source_out] for run in timeline.runs]


def transcript_payload(words, timeline, report, revision, label):
    """Kept words on the cut's clock (ms from its first frame) with their source times, and a cut per join."""
    pieces = build_captions.one_per_word(cut_timeline.from_first_frame(cut_timeline.retained_words(words, timeline),
                                                                       timeline))
    index = {word: number for number, word in enumerate(words)}
    seen = {}
    items = []
    for piece in pieces:
        base = f'w{index[piece.word]:05}'
        seen[base] = seen.get(base, 0) + 1
        items.append({'id': base if seen[base] == 1 else f'{base}-{seen[base]}', 'text': piece.word.text,
                      'startMs': round(piece.timeline_start * 1000), 'endMs': round(piece.timeline_end * 1000),
                      'sourceStartMs': round(piece.word.start * 1000), 'sourceEndMs': round(piece.word.end * 1000)})
    red = {join['index'] for join in red_joins(report)}
    first = timeline.runs[0].timeline_start_frame
    cuts = [{'id': f'join-{join["index"]}', 'atMs': round((join['timelineFrame'] - first) * 1000 / timeline.fps),
             'risk': join['index'] in red} for join in report['joins']]
    return {'words': items, 'revision': revision, 'label': label, 'cuts': cuts, 'keepRanges': keep_ranges(timeline),
            'fps': timeline.fps}


def upload_payload(plan, report, video_file=None, file_sha=None):
    cutlist, timeline = plan['cutlist'], plan['timeline']
    video_id = f'{plan["video"]}-{plan["version"]}'
    duration_ms = round(cutlist['durationFrames'] * 1000 / timeline.fps)
    label = f'{plan["video"]} · {plan["version"]}'
    return {
        'schemaVersion': 1,
        'kind': 'review-upload',
        'dryRun': video_file is None,
        'sent': False,
        'videoId': video_id,
        'file': {'name': video_file.name if video_file else None, 'sha256': file_sha},
        'register': {'id': video_id, 'title': plan['video'], 'label': label, 'fps': timeline.fps,
                     'durationMs': duration_ms, 'keepRanges': keep_ranges(timeline)},
        'transcript': transcript_payload(plan['words'], timeline, report, video_id, f'{label} transcript'),
        'cutlist': {'name': plan['cutlistPath'].name, 'sha256': sha256_file(plan['cutlistPath'])},
        'listening': 'Not listened',
    }


FACT_LABELS = (
    ('checkpoint', 'Checkpoint before the build'),
    ('endFrameInclusive', 'Resolve endFrame inclusive'),
    ('fadeFrames', 'Audio fades (frames each side)'),
    ('markers', 'Join markers'),
    ('codec', 'Render codec'),
)


def fact_lines(facts):
    return [f'- {text}: {facts[key]}' for key, text in FACT_LABELS if key in facts]


def failure_report(plan, report, facts, error, video_file=None):
    """The run report for a run that stopped in a late step: what was found so far, and the error."""
    summary = report['summary']
    lines = [
        f'# {plan["video"]}-{plan["version"]}: stopped, no review video', '',
        f'**Stopped:** {str(error) or type(error).__name__}', '',
        '**Not listened.** The join check reads word times; only listening clears a join.', '',
        f'- Cut list: {plan["cutlistPath"]}',
        f'- Recording: {plan["source"]}',
    ]
    if video_file is not None:
        lines.append(f'- Video file: {video_file.name} (rendered, not accepted)')
    lines.append(f'- Resolve: {facts.get("project", "not used (dry run)")}, timeline {facts.get("timeline", "none")}')
    lines += fact_lines(facts)
    lines += [f'- Joins: {summary["joins"]}; problems {summary["problems"]}, checks {summary["checks"]}', '',
              'Nothing was uploaded.', '']
    return '\n'.join(lines)


def run_report(plan, report, payload, facts):
    red = red_joins(report)
    summary = report['summary']
    lines = [
        f'# {payload["videoId"]}: review video, not uploaded', '',
        '**Not listened.** The join check reads word times; only listening clears a join.', '',
        f'- Cut list: {plan["cutlistPath"]} (approved by {plan["approval"]["approvedBy"]} on '
        f'{plan["approval"]["date"]}: "{plan["approval"]["words"]}")',
        f'- Recording: {plan["source"]} ' + ('(SHA-256 matches the cut list)' if plan['hashChecked']
                                             else '(hash not checked: the cut list has no sourceSha256)'),
        f'- Video file: {payload["file"]["name"] or "none (dry run)"}',
        f'- Resolve: {facts.get("project", "not used (dry run)")}, timeline {facts.get("timeline", "none")}',
    ]
    lines += fact_lines(facts)
    lines += [f'- Joins: {summary["joins"]}; problems {summary["problems"]}, checks {summary["checks"]}; '
              f'red joins {len(red)}', '', '## Listen to these joins first', '']
    lines += [f'- Join {join["index"]} at {join_check.clock(join["timelineSeconds"])}: '
              + '; '.join(item['message'] for item in join['flags'] if item['severity'] == 'problem')
              for join in red] or ['- No join has a problem flag. That does not mean clean: listen to the whole cut.']
    lines += ['', '## Upload', '',
              'Nothing was sent. After Lex says upload: '
              f'`python scripts/video/cut-to-review.py --upload {plan["runDir"]}`', '']
    return '\n'.join(lines)


def stop_message(plan, report, payload, facts):
    red = red_joins(report)
    lines = [f'Stopped before the upload. Video: {plan["runDir"] / (payload["file"]["name"] or "")}',
             f'Timeline: {facts.get("timeline", "none (dry run)")} in {facts.get("project", "no Resolve project")}.']
    if not plan['hashChecked']:
        lines.append(f'Recording hash not checked (the cut list has no sourceSha256): {plan["source"]}')
    if red:
        lines.append('Joins to hear first: ' + ', '.join(f'{join["index"]} at {join_check.clock(join["timelineSeconds"])}'
                                                        for join in red) + '.')
        lines.append('Recommend: listen to the red joins, then say upload.')
    else:
        lines.append('No red joins. Recommend: listen to the whole cut, then say upload.')
    lines.append(f'Upload when Lex says yes: python scripts/video/cut-to-review.py --upload {plan["runDir"]}')
    return '\n'.join(lines)


def write_failure_report(plan, report, facts, error, video_file=None):
    """Best effort: a report that cannot be written must not hide the error that stopped the run."""
    try:
        write_new(plan['runDir'] / 'run-report.md', failure_report(plan, report, facts, error, video_file))
    except OSError:
        pass


def build(args, connect=resolve_build.connect, log=print, sleep=None):
    """Steps 1-7. Returns the exit code: 3 at the stop point, 0 after a dry run."""
    plan = preflight(args)
    plan.update(video=args.video, version=args.version)
    resolve = None
    if not args.dry_run:
        try:
            resolve = connect()
            log(f'Resolve: {resolve_build.check_studio(resolve)}')
        except resolve_build.ResolveError as error:
            raise Stop(str(error)) from None
    run_dir = plan['runDir']
    run_dir.mkdir(parents=True)
    report = write_checks(plan)
    facts = {}
    video_file = file_sha = None
    if resolve is not None:
        builder = resolve_build.Builder(resolve, run_dir, log)
        media = plan['media']
        try:
            builder.open_project(args.project or args.video)
            builder.build_timeline(plan['source'], plan['fps'], media['width'], media['height'], plan['segments'],
                                   plan['cutlist'].get('audioFadeMs', join_check.THRESHOLDS['audioFadeMs']),
                                   markers(report))
            extra = {} if sleep is None else {'sleep': sleep}
            video_file = builder.render(f'{args.video}-{args.version}', plan['fps'], media['width'], media['height'],
                                        args.render_timeout * 60, **extra)
        except resolve_build.ResolveError as error:
            facts = builder.facts
            write_failure_report(plan, report, facts, error)
            raise Stop(f'{error} (Run folder: {run_dir})') from None
        except BaseException as error:
            facts = builder.facts
            write_failure_report(plan, report, facts, error)
            raise
        facts = builder.facts
    try:
        if video_file is not None:
            verify_render(video_file, plan['cutlist']['durationFrames'], float(plan['fps']))
            file_sha = sha256_file(video_file)
        payload = upload_payload(plan, report, video_file, file_sha)
        write_new(run_dir / UPLOAD_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
    except Stop as error:
        write_failure_report(plan, report, facts, error, video_file)
        raise Stop(f'{error} (Run folder: {run_dir})') from None
    except BaseException as error:
        write_failure_report(plan, report, facts, error, video_file)
        raise
    write_new(run_dir / 'run-report.md', run_report(plan, report, payload, facts))
    if args.dry_run:
        log(f'Dry run: checks, join check, captions and review data written to {run_dir}. Nothing built, '
            'rendered or sent.')
        return 0
    log(stop_message(plan, report, payload, facts))
    return STOP_EXIT


# Step 8: the upload, only on Lex's yes.
def post_json(url, message, token):
    request = urllib.request.Request(url, json.dumps(message).encode('utf-8'), method='POST', headers={
        'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b'null')
    except urllib.error.HTTPError as error:
        try:
            reply = json.loads(error.read() or b'null')
        except ValueError:
            reply = None
        finally:
            error.close()
        if isinstance(reply, dict) and isinstance(reply.get('error'), dict):
            return reply
        raise Stop(f'The review page answered HTTP {error.code}.') from None
    except (OSError, http.client.HTTPException) as error:
        raise Stop(f'The review page could not be reached: {error}.') from None
    except ValueError:
        raise Stop('The review page sent a reply that is not JSON.') from None


def hosted_caller(base_url, token, post=post_json, sleep=time.sleep):
    counter = iter(range(1, 1000))

    def rpc(method, params):
        reply = post(f'{base_url}/mcp', {'jsonrpc': '2.0', 'id': next(counter), 'method': method, 'params': params},
                     token)
        if not isinstance(reply, dict) or reply.get('error'):
            error = reply.get('error') if isinstance(reply, dict) else None
            raise Stop(f'The review page refused {method}: {error.get("message") if isinstance(error, dict) else reply}')
        return reply.get('result')

    rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                       'clientInfo': {'name': 'cut-to-review', 'version': '1'}})

    def call(name, arguments):
        for attempt in range(HOSTED_RETRIES + 1):
            result = rpc('tools/call', {'name': name, 'arguments': arguments}) or {}
            text = ''.join(item.get('text', '') for item in result.get('content', []))
            if not result.get('isError'):
                try:
                    pending = json.loads(text).get('status') == 'pending'
                except (ValueError, AttributeError):
                    pending = False
                if pending:
                    raise Stop(f'The reviewer\'s tab has not applied {name} yet. Bring the Video Review tab to the '
                               'front, then run the same --upload again.')
                return text
            # The reviewer's tab applies a new video a moment after register_video returns.
            if 'Unknown video version' not in text or attempt == HOSTED_RETRIES:
                raise Stop(f'The review page refused {name}: {text}')
            sleep(HOSTED_RETRY_SECONDS)
    return call


def local_caller(data_dir):
    import mcp_server
    mcp_server.DATA = mcp_server.store.data_dir(data_dir)

    def call(name, arguments):
        try:
            return mcp_server.call(name, arguments)
        except (ValueError, KeyError, TypeError) as error:
            raise Stop(f'The review page refused {name}: {error}') from None
    return call


def upload_destination(args, environ):
    """Which review page this upload goes to, without secrets: the local data folder, or the hosted session."""
    if args.hosted:
        token = environ.get('VIDEO_REVIEW_TOKEN', '').strip()
        session = token.split('_')[1] if token.count('_') >= 2 else token
        return f'{args.review_url or HOSTED_URL}|{hashlib.sha256(session.encode()).hexdigest()[:16]}'
    folder = args.data_dir or environ.get('VIDEO_REVIEW_DATA_DIR') or ROOT / 'data'
    return f'local {Path(folder).expanduser().resolve()}'


def upload(args, environ=os.environ, post=post_json, log=print):
    run_dir = Path(args.upload)
    payload = read_json(run_dir / UPLOAD_FILE, 'The review data')
    if payload.get('kind') != 'review-upload' or payload.get('schemaVersion') != 1:
        raise Stop(f'{run_dir / UPLOAD_FILE} is not review data from this tool.')
    if payload.get('dryRun'):
        raise Stop('This is a dry run: there is no video to upload.')
    done_file = run_dir / DONE_FILE
    progress = read_json(done_file, 'The upload record') if done_file.exists() else {'steps': []}
    if progress.get('complete'):
        raise Stop(f'{payload["videoId"]} was uploaded already; a video id is never reused. Render a new --version.')
    destination = upload_destination(args, environ)
    if progress.get('destination', destination) != destination:
        raise Stop(f'This upload was started on another review page ({progress["destination"].split("|")[0]}); '
                   'finish it there.')
    video = run_dir / payload['file']['name']
    if not video.is_file() or sha256_file(video) != payload['file']['sha256']:
        raise Stop(f'{video} is missing or changed since the render check. Render it again as a new version.')
    register = dict(payload['register'])
    if args.hosted:
        token = environ.get('VIDEO_REVIEW_TOKEN', '').strip()
        if not token:
            raise Stop('Set VIDEO_REVIEW_TOKEN (Connect agent in Video Review copies it) for --hosted.')
        call, page = hosted_caller(args.review_url or HOSTED_URL, token, post), args.review_url or HOSTED_URL
        register['fileName'] = video.name
    else:
        call, page = local_caller(args.data_dir), args.review_url or LOCAL_URL
        register.update(path=str(video.resolve()), fingerprint=payload['file']['sha256'])
    progress.update(videoId=payload['videoId'], reviewPage=page, hosted=bool(args.hosted), destination=destination)
    steps = (('register_video', register),
             ('set_transcript', {'videoId': payload['videoId'], 'transcript': payload['transcript']}),
             ('open_video', {'videoId': payload['videoId']}))
    # Each step is recorded as started and as finished, so a rerun after a failure carries on where it stopped.
    for name, arguments in steps:
        if name in progress['steps']:
            continue
        started = progress.get('started') == name
        progress['started'] = name
        done_file.write_text(json.dumps(progress, indent=2), encoding='utf-8')
        try:
            call(name, arguments)
        except Stop as error:
            refused = str(error).startswith('The review page refused')
            # The page registered the id last time but its answer was lost: this run folder owns that id.
            if not (started and name == 'register_video' and refused and 'already exists' in str(error)):
                if refused:
                    # A clear answer: nothing is in flight, so a later "already exists" is not ours.
                    progress.pop('started')
                    done_file.write_text(json.dumps(progress, indent=2), encoding='utf-8')
                raise
        progress['steps'].append(name)
        progress.pop('started')
        done_file.write_text(json.dumps(progress, indent=2), encoding='utf-8')
    progress['complete'] = True
    done_file.write_text(json.dumps(progress, indent=2), encoding='utf-8')
    log(f'Uploaded {payload["videoId"]} to the review page {page}. The file stays at {video}.')
    return 0


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('cut_folder', nargs='?', help='folder with cutlist.json and approval.json')
    parser.add_argument('--video', help='video slug; the review id becomes <slug>-<version>')
    parser.add_argument('--version', default='v1')
    parser.add_argument('--project', help='Resolve project (default: the video slug)')
    parser.add_argument('--words', help='word-timed transcript (default: words.json in the cut folder)')
    parser.add_argument('--source', help='the recording, when the cut list has no sourcePath')
    parser.add_argument('--renders-root', help='where renders/<slug>/<version> goes (default: the repo\'s renders/)')
    parser.add_argument('--render-timeout', type=int, default=DEFAULT_RENDER_TIMEOUT_MINUTES, metavar='MINUTES')
    parser.add_argument('--dry-run', action='store_true', help='checks, join check, captions and review data only')
    parser.add_argument('--upload', metavar='RUN_FOLDER', help='register a finished run on the review page')
    parser.add_argument('--hosted', action='store_true', help='upload to video.techies.tools instead of the local page')
    parser.add_argument('--review-url', help=argparse.SUPPRESS)
    parser.add_argument('--data-dir', help='local review data folder (default: the review server\'s)')
    args = parser.parse_args(argv)
    if args.upload:
        if args.cut_folder or args.dry_run or args.video or args.project or args.words or args.source:
            parser.error('--upload takes only the run folder (and --hosted or --data-dir).')
    elif not args.cut_folder or not args.video:
        parser.error('give the cut folder and --video, or --upload <run-folder>.')
    return args


def main(argv=None):
    args = parse(argv)
    try:
        return upload(args) if args.upload else build(args)
    except Stop as error:
        print(f'cut-to-review stopped: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
