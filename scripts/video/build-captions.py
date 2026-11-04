"""Build separate caption files for the edited timeline from a word-timed transcript and a cut list.

Usage: python build-captions.py words.json cutlist.json --out-dir folder [--name captions]
Writes <name>.phrases.srt/.vtt, <name>.words.srt/.vtt (one cue per word) and <name>.timed-words.vtt
(phrases with per-word timestamps). Times count from the timeline's first frame. Nothing is burned
into the video, no media is touched and no existing file is overwritten.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cut_timeline  # noqa: E402

LINE_CHARS = 42
MAX_LINES = 2
MAX_PHRASE_SECONDS = 7.0
MIN_PHRASE_SECONDS = 0.8
PHRASE_PAUSE_SECONDS = 0.55
SENTENCE_END = re.compile(r'[.!?]["\')\]]*$')
CLAUSE_END = re.compile(r'[,;:]["\')\]]*$')


def wrap(texts):
    """Fill lines greedily, then even out two lines so one word is not left alone on the second."""
    lines = []
    for text in texts:
        if lines and len(lines[-1]) + 1 + len(text) <= LINE_CHARS:
            lines[-1] += ' ' + text
        else:
            lines.append(text)
    if len(lines) != MAX_LINES:
        return lines
    splits = [(' '.join(texts[:index]), ' '.join(texts[index:])) for index in range(1, len(texts))]
    fitting = [pair for pair in splits if max(map(len, pair)) <= LINE_CHARS]
    return list(min(fitting, key=lambda pair: max(map(len, pair)))) if fitting else lines


def one_per_word(pieces):
    """Show a word kept on both sides of a cut once, from its first kept start to its last kept end."""
    merged = []
    for piece in pieces:
        if merged and merged[-1].word == piece.word:
            merged[-1] = replace(merged[-1], timeline_end=piece.timeline_end,
                                 clipped_end=merged[-1].clipped_end + piece.clipped_end)
        else:
            merged.append(piece)
    return merged


def phrases(pieces):
    """Group kept words into cues: end at a sentence, a long pause, the size limits or a clause once a line is full."""
    groups = []
    current = []
    for piece in pieces:
        if current:
            pause = piece.timeline_start - current[-1].timeline_end
            too_long = piece.timeline_end - current[0].timeline_start > MAX_PHRASE_SECONDS
            texts = [item.word.text for item in current] + [piece.word.text]
            if pause > PHRASE_PAUSE_SECONDS or too_long or len(wrap(texts)) > MAX_LINES:
                groups.append(current)
                current = []
        current.append(piece)
        text = piece.word.text
        length = len(' '.join(item.word.text for item in current))
        if SENTENCE_END.search(text) or (CLAUSE_END.search(text) and length >= LINE_CHARS):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def to_ms(seconds):
    return round(seconds * 1000)


def stamp(total, separator):
    return (f'{total // 3600000:02}:{total // 60000 % 60:02}:{total // 1000 % 60:02}'
            f'{separator}{total % 1000:03}')


def escape_vtt(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def word_cues(pieces):
    for piece in pieces:
        start = to_ms(piece.timeline_start)
        yield start, max(to_ms(piece.timeline_end), start + 1), piece.word.text


def phrase_cues(pieces, timeline_end):
    """Hold short phrases long enough to read, but never into the next phrase or past the timeline's end."""
    groups = phrases(pieces)
    limit = to_ms(timeline_end)
    for index, group in enumerate(groups):
        start = to_ms(group[0].timeline_start)
        following = to_ms(groups[index + 1][0].timeline_start) if index + 1 < len(groups) else limit
        end = max(to_ms(group[-1].timeline_end), min(start + to_ms(MIN_PHRASE_SECONDS), following, limit), start + 1)
        yield start, end, group


def write_srt(cues):
    return '\n'.join(f'{index}\n{stamp(start, ",")} --> {stamp(end, ",")}\n{text}\n'
                     for index, (start, end, text) in enumerate(cues, 1))


def write_vtt(cues):
    return 'WEBVTT\n\n' + '\n'.join(f'{stamp(start, ".")} --> {stamp(end, ".")}\n{text}\n' for start, end, text in cues)


def phrase_text(group):
    return '\n'.join(wrap([piece.word.text for piece in group]))


def phrase_srt(pieces, timeline_end):
    return write_srt((start, end, phrase_text(group)) for start, end, group in phrase_cues(pieces, timeline_end))


def phrase_vtt(pieces, timeline_end):
    return write_vtt((start, end, escape_vtt(phrase_text(group))) for start, end, group in phrase_cues(pieces, timeline_end))


def word_srt(pieces):
    return write_srt(word_cues(pieces))


def word_vtt(pieces):
    return write_vtt((start, end, escape_vtt(text)) for start, end, text in word_cues(pieces))


def timed_text(group, cue_start, cue_end):
    """WebVTT needs each inline time strictly inside its cue and rising, so a repeated time is left out."""
    parts = []
    latest = cue_start
    for piece in group:
        at = to_ms(piece.timeline_start)
        text = escape_vtt(piece.word.text)
        if latest < at < cue_end:
            parts.append(f'<{stamp(at, ".")}>{text}')
            latest = at
        else:
            parts.append(text)
    return ' '.join(parts)


def timed_vtt(pieces, timeline_end):
    return write_vtt((start, end, timed_text(group, start, end)) for start, end, group in phrase_cues(pieces, timeline_end))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('words', type=Path, help='word-timed transcript JSON')
    parser.add_argument('cutlist', type=Path, help='cut list JSON with source in/out frames and fps')
    parser.add_argument('--out-dir', required=True, type=Path, help='folder for the caption files')
    parser.add_argument('--name', default='captions', help='first part of each file name')
    args = parser.parse_args()
    if Path(args.name).name != args.name:
        sys.exit('--name is a file name, not a path.')
    try:
        transcript = json.loads(args.words.read_text(encoding='utf-8-sig'))
        cutlist = json.loads(args.cutlist.read_text(encoding='utf-8-sig'))
        cut_timeline.check_same_source(transcript, cutlist)
        timeline = cut_timeline.load_cutlist(cutlist)
        pieces = cut_timeline.retained_words(cut_timeline.load_words(transcript), timeline)
        pieces = one_per_word(cut_timeline.from_first_frame(pieces, timeline))
    except (OSError, ValueError) as error:
        sys.exit(f'Captions stopped: {error}')
    if not pieces:
        sys.exit('Captions stopped: no transcript words fall inside the cut list. Check the clock and the recording.')
    files = {
        f'{args.name}.phrases.srt': phrase_srt(pieces, timeline.duration),
        f'{args.name}.phrases.vtt': phrase_vtt(pieces, timeline.duration),
        f'{args.name}.words.srt': word_srt(pieces),
        f'{args.name}.words.vtt': word_vtt(pieces),
        f'{args.name}.timed-words.vtt': timed_vtt(pieces, timeline.duration),
    }
    for name in files:
        if (args.out_dir / name).exists():
            sys.exit(f'{args.out_dir / name} already exists. Choose a new folder; older captions stay as they are.')
    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            with (args.out_dir / name).open('x', encoding='utf-8', newline='\n') as output:
                output.write(text)
    except OSError as error:
        sys.exit(f'Captions stopped: {error}')
    partial = sum(piece.is_partial for piece in pieces)
    print(f'{len(pieces)} words in {len(phrases(pieces))} phrases on the edited timeline. '
          f'First word at {stamp(to_ms(pieces[0].timeline_start), ",")}. {partial} words are only partly kept. '
          f'Wrote {len(files)} files to {args.out_dir}.')


if __name__ == '__main__':
    main()
