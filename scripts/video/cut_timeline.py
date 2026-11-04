"""Read word-timed transcripts and cut lists, and place the kept words on the edited timeline.

Shared by join-check.py and build-captions.py. Word times here are always seconds on the original
recording's clock. Nothing in this file reads or writes media.
"""
from dataclasses import dataclass
import math

# Transcript times carry about this much noise, so a cut this close to a word edge is not called a clip.
WORD_EDGE_TOLERANCE = 0.010


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Run:
    """One unbroken stretch of the recording on the timeline. Splits with continuous source are merged."""
    index: int
    segment_ids: tuple
    source_in: int
    source_out: int
    timeline_start_frame: int
    fps: float

    @property
    def source_start(self):
        return self.source_in / self.fps

    @property
    def source_end(self):
        return self.source_out / self.fps

    @property
    def timeline_start(self):
        return self.timeline_start_frame / self.fps

    @property
    def timeline_end_frame(self):
        return self.timeline_start_frame + self.source_out - self.source_in

    @property
    def timeline_end(self):
        return self.timeline_end_frame / self.fps

    @property
    def label(self):
        return '+'.join(self.segment_ids)


@dataclass(frozen=True)
class Timeline:
    fps: float
    runs: tuple
    continuous_splits: int

    @property
    def end(self):
        return self.runs[-1].timeline_end


@dataclass(frozen=True)
class Piece:
    """The part of a word that survives in one run, with the time cut off either side of it."""
    word: Word
    run_index: int
    timeline_start: float
    timeline_end: float
    clipped_start: float
    clipped_end: float

    @property
    def is_partial(self):
        return self.clipped_start > 0 or self.clipped_end > 0


def _from_seconds(text, start, end):
    return text, float(start), float(end)


def _from_object(item):
    if 'sourceStartMs' in item:
        return _from_seconds(item.get('text'), item['sourceStartMs'] / 1000, item['sourceEndMs'] / 1000)
    return _from_seconds(item.get('text'), item['startMs'] / 1000, item['endMs'] / 1000)


def _raw_words(data):
    if isinstance(data, dict):
        original = data.get('original')
        if isinstance(original, dict) and isinstance(original.get('words'), list):
            return [_from_object(item) for item in original['words']]
        if isinstance(data.get('words'), list):
            if data['words'] and all('sourceStartMs' in item for item in data['words']):
                return [_from_object(item) for item in data['words']]
            raise ValueError('These words have no source times and may be on the edited clock. '
                             'Use a transcript with original.words or sourceStartMs.')
        data = data.get('transcript', data.get('paragraphs'))
    if not isinstance(data, list) or not data:
        raise ValueError('No timed words found. Expected HappyScribe paragraphs, [text, start, end] '
                         'arrays, or word objects with startMs and endMs.')
    if isinstance(data[0], dict) and 'words' in data[0]:
        return [_from_seconds(item.get('text'), item['data_start'], item['data_end'])
                for paragraph in data for item in paragraph['words'] if item.get('type', 'word') == 'word']
    if isinstance(data[0], dict):
        return [_from_object(item) for item in data]
    return [_from_seconds(*item) for item in data]


def load_words(data):
    """Return the transcript's words on the source clock, refusing anything unsafe to cut against."""
    try:
        raw = _raw_words(data)
    except (KeyError, TypeError) as error:
        raise ValueError(f'A word is missing its text or times: {error!r}.') from error
    words = []
    for text, start, end in raw:
        text = ' '.join(str(text or '').split())
        if not text:
            continue
        if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end < start:
            raise ValueError(f'Invalid times on word "{text}".')
        if words and start < words[-1].end:
            raise ValueError(f'Word order runs backwards at "{text}". Review overlapping speakers first.')
        words.append(Word(text, start, end))
    if not words:
        raise ValueError('No timed words found.')
    return words


def _whole_frame(segment, key):
    value = segment.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f'Segment {segment.get("id", "?")}: {key} must be a whole frame number.')
    return value


def load_cutlist(data):
    """Return the cut list as merged runs in timeline order, refusing impossible or mixed-source lists."""
    fps = data.get('fps')
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or not math.isfinite(fps) or fps <= 0:
        raise ValueError('Cut list needs a positive fps.')
    segments = data.get('segments')
    if not isinstance(segments, list) or not segments:
        raise ValueError('Cut list has no segments.')
    if len({segment.get('sourcePath', data.get('sourcePath')) for segment in segments}) > 1:
        raise ValueError('Cut list mixes recordings. Each recording has its own clock; check them separately.')

    runs = []
    splits = 0
    previous_end = None
    for segment in sorted(segments, key=lambda item: item.get('timelineStartFrame', 0)):
        identity = segment.get('id', '?')
        source_in = _whole_frame(segment, 'sourceInFrame')
        source_out = _whole_frame(segment, 'sourceOutFrame')
        start = _whole_frame(segment, 'timelineStartFrame')
        if source_out <= source_in:
            raise ValueError(f'Segment {identity}: sourceOutFrame must be after sourceInFrame.')
        if segment.get('durationFrames', source_out - source_in) != source_out - source_in:
            raise ValueError(f'Segment {identity}: durationFrames does not match its source frames.')
        if previous_end is not None and start < previous_end:
            raise ValueError(f'Segment {identity} overlaps the segment before it on the timeline.')
        previous_end = start + source_out - source_in
        if runs and runs[-1].source_out == source_in and runs[-1].timeline_end_frame == start:
            last = runs[-1]
            runs[-1] = Run(last.index, last.segment_ids + (identity,), last.source_in, source_out,
                           last.timeline_start_frame, fps)
            splits += 1
        else:
            runs.append(Run(len(runs), (identity,), source_in, source_out, start, fps))
    return Timeline(fps, tuple(runs), splits)


def check_same_source(transcript, cutlist):
    """Refuse a transcript and cut list that both name their recording and name different ones."""
    original = transcript.get('original') if isinstance(transcript, dict) else None
    transcript_source = original.get('sourceId') if isinstance(original, dict) else None
    cut_source = cutlist.get('sourceSha256')
    if transcript_source and cut_source and transcript_source != cut_source:
        raise ValueError('The transcript and the cut list come from a different recording.')


def retained_words(words, timeline):
    """Return every kept word piece in timeline order. Slivers under the tolerance do not count as kept."""
    pieces = []
    for run in timeline.runs:
        for word in words:
            if word.end < run.source_start:
                continue
            if word.start >= run.source_end:
                break
            overlap = min(word.end, run.source_end) - max(word.start, run.source_start)
            kept = overlap > WORD_EDGE_TOLERANCE
            if word.end - word.start <= WORD_EDGE_TOLERANCE:
                kept = run.source_start <= word.start
            if not kept:
                continue
            start = max(word.start, run.source_start)
            end = min(word.end, run.source_end)
            lost_start = word.start < run.source_start - WORD_EDGE_TOLERANCE
            lost_end = word.end > run.source_end + WORD_EDGE_TOLERANCE
            pieces.append(Piece(word, run.index,
                                run.timeline_start + start - run.source_start,
                                run.timeline_start + end - run.source_start,
                                run.source_start - word.start if lost_start else 0.0,
                                word.end - run.source_end if lost_end else 0.0))
    return pieces
