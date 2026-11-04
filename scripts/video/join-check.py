"""Read every join in a cut before anyone listens, and flag the ones worth hearing first.

Usage: python join-check.py words.json cutlist.json --out-dir folder
Writes join-check.md and join-check.json into a folder; never overwrites and never touches media.
Transcript times are approximate, so a flag says where to listen, not that a word is broken.
"""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cut_timeline  # noqa: E402

# Lex's recorded starting preferences: acut/sunday-update-2026-11-01/EDITING-LESSONS.md.
THRESHOLDS = {
    'leadInMs': [180, 250],
    'linkedGapMs': [150, 250],
    'newThoughtGapMs': [250, 450],
    'speechEdgeMs': [100, 150],
    'audioFadeMs': 15,
    # Working limits of this check, not recorded preferences.
    'clippedEdgeMaxMs': 80,
    'orphanWordMaxMs': 300,
    'orphanPauseMs': 300,
}
SENTENCE_END = re.compile(r'[.!?]["\')\]]*$')
CONTEXT_WORDS = 3


def ms(seconds):
    return round(seconds * 1000)


def flag(code, severity, message, side=None, word=None, value=None):
    return {'code': code, 'severity': severity, 'message': message, 'side': side, 'word': word, 'ms': value}


def clip_flags(piece, side):
    lost = ms(piece.clipped_end if side == 'outgoing' else piece.clipped_start)
    if not lost:
        return []
    part = 'end' if side == 'outgoing' else 'start'
    text = piece.word.text
    kept = ms(piece.timeline_end - piece.timeline_start)
    if lost <= THRESHOLDS['clippedEdgeMaxMs']:
        return [flag(f'clips-word-{part}', 'problem', f'The cut takes {lost} ms off the {part} of "{text}".',
                     side, text, lost)]
    return [flag('cut-inside-word', 'problem', f'The cut lands inside "{text}": {kept} ms kept, {lost} ms removed.',
                 side, text, lost)]


def edge_flags(room, piece, side):
    """Check the space kept between the cut and the nearest word, unless the cut already clips that word."""
    text = piece.word.text
    if piece.clipped_end if side == 'outgoing' else piece.clipped_start:
        return []
    if room < THRESHOLDS['audioFadeMs']:
        return [flag('fade-touches-word', 'problem',
                     f'Only {room} ms beside "{text}", so the {THRESHOLDS["audioFadeMs"]} ms fade touches the word.',
                     side, text, room)]
    if room < THRESHOLDS['speechEdgeMs'][0]:
        return [flag('tight-speech-edge', 'check',
                     f'{room} ms beside "{text}", under the preferred {THRESHOLDS["speechEdgeMs"][0]} ms.',
                     side, text, room)]
    return []


def gap_flags(gap, kind):
    low, high = THRESHOLDS['linkedGapMs' if kind == 'linked' else 'newThoughtGapMs']
    name = 'linked phrases' if kind == 'linked' else 'a new thought'
    if gap < low:
        return [flag('gap-too-short', 'check', f'{gap} ms gap for {name}; preferred {low}-{high} ms.', value=gap)]
    if gap > high:
        return [flag('gap-too-long', 'check',
                     f'{gap} ms gap for {name}; preferred {low}-{high} ms. Keep it if it is a deliberate hold.',
                     value=gap)]
    return []


def orphan_flags(run_pieces, side):
    """Flag a short word left alone at the join: nothing beside it in its section, or only after a long pause."""
    if not run_pieces:
        return []
    piece, neighbour = (run_pieces[-1], run_pieces[-2] if len(run_pieces) > 1 else None) if side == 'outgoing' \
        else (run_pieces[0], run_pieces[1] if len(run_pieces) > 1 else None)
    length = ms(piece.timeline_end - piece.timeline_start)
    if length > THRESHOLDS['orphanWordMaxMs']:
        return []
    if neighbour is not None:
        pause = neighbour.timeline_start - piece.timeline_end if side == 'incoming' \
            else piece.timeline_start - neighbour.timeline_end
        if ms(pause) < THRESHOLDS['orphanPauseMs']:
            return []
    text = piece.word.text
    return [flag('orphan-word', 'problem', f'This join leaves "{text}" ({length} ms) on its own.', side, text, length)]


def retake_flags(run_pieces, side):
    """Flag a kept "again" beside the join, since a quiet "again" can be a retake cue. "Again and again" is speech."""
    names = [re.sub(r'\W', '', piece.word.text).lower() for piece in run_pieces]
    span = range(max(0, len(names) - CONTEXT_WORDS), len(names)) if side == 'outgoing' \
        else range(min(CONTEXT_WORDS, len(names)))
    flags = []
    for index in span:
        natural = names[max(0, index - 2):index] == ['again', 'and'] or names[index + 1:index + 3] == ['and', 'again']
        if names[index] == 'again' and not natural:
            flags.append(flag('retake-cue', 'check',
                              f'"{run_pieces[index].word.text}" stays beside this join. Check it is not a retake cue.',
                              side, run_pieces[index].word.text))
    return flags


def context(run_pieces, side):
    chosen = run_pieces[-CONTEXT_WORDS:] if side == 'outgoing' else run_pieces[:CONTEXT_WORDS]
    return ' '.join(piece.word.text for piece in chosen)


def check_join(index, before, after, pieces_before, pieces_after, words):
    last = pieces_before[-1] if pieces_before else None
    first = pieces_after[0] if pieces_after else None
    moves_forward = after.source_start >= before.source_end
    removed = [word.text for word in words
               if word.start >= before.source_end - cut_timeline.WORD_EDGE_TOLERANCE
               and word.end <= after.source_start + cut_timeline.WORD_EDGE_TOLERANCE] if moves_forward else []
    flags = []
    tail = head = gap = kind = None
    if last:
        tail = ms(before.timeline_end - last.timeline_end)
        flags += clip_flags(last, 'outgoing') + edge_flags(tail, last, 'outgoing')
    if first:
        head = ms(first.timeline_start - after.timeline_start)
        flags += clip_flags(first, 'incoming') + edge_flags(head, first, 'incoming')
    if last and first:
        gap = ms(first.timeline_start - last.timeline_end)
        kind = 'new-thought' if SENTENCE_END.search(last.word.text) else 'linked'
        flags += gap_flags(gap, kind)
    else:
        flags.append(flag('no-words-beside-join', 'check',
                          'The transcript has no words on one side: a demo hold, or a sound it missed. Listen.'))
    for side, run_pieces in (('outgoing', pieces_before), ('incoming', pieces_after)):
        flags += orphan_flags(run_pieces, side) + retake_flags(run_pieces, side)
    return {
        'index': index,
        'outgoing': before.label,
        'incoming': after.label,
        'timelineFrame': after.timeline_start_frame,
        'timelineSeconds': round(after.timeline_start, 3),
        'sourceOutFrame': before.source_out,
        'sourceInFrame': after.source_in,
        'sourceMovesForward': moves_forward,
        'lastWord': last.word.text if last else None,
        'firstWord': first.word.text if first else None,
        'before': context(pieces_before, 'outgoing'),
        'after': context(pieces_after, 'incoming'),
        'removedWords': removed,
        'tailMs': tail,
        'headMs': head,
        'gapMs': gap,
        'gapKind': kind,
        'flags': flags,
    }


def check_opening(pieces, timeline):
    first = pieces[0]
    lead_in = ms(first.timeline_start - timeline.runs[0].timeline_start)
    low, high = THRESHOLDS['leadInMs']
    flags = clip_flags(first, 'incoming')
    if lead_in < low:
        flags.append(flag('lead-in-too-short', 'check', f'{lead_in} ms before "{first.word.text}"; preferred {low}-{high} ms.',
                          'incoming', first.word.text, lead_in))
    elif lead_in > high:
        flags.append(flag('lead-in-too-long', 'check', f'{lead_in} ms before "{first.word.text}"; preferred {low}-{high} ms.',
                          'incoming', first.word.text, lead_in))
    return {'leadInMs': lead_in, 'firstWord': first.word.text, 'flags': flags}


def check_ending(pieces, timeline):
    last = pieces[-1]
    tail = ms(timeline.end - last.timeline_end)
    return {'tailMs': tail, 'lastWord': last.word.text,
            'flags': clip_flags(last, 'outgoing') + edge_flags(tail, last, 'outgoing')}


def check_joins(words, timeline):
    """Return the full report as plain data."""
    pieces = cut_timeline.retained_words(words, timeline)
    if not pieces:
        raise ValueError('No transcript words fall inside the cut list. Check the clock and the recording.')
    by_run = {run.index: [] for run in timeline.runs}
    for piece in pieces:
        by_run[piece.run_index].append(piece)
    joins = [check_join(index + 1, before, after, by_run[before.index], by_run[after.index], words)
             for index, (before, after) in enumerate(zip(timeline.runs, timeline.runs[1:]))]
    opening = check_opening(pieces, timeline)
    ending = check_ending(pieces, timeline)
    flags = [item for entry in joins + [opening, ending] for item in entry['flags']]
    return {
        'schemaVersion': 1,
        'kind': 'join-check',
        'listening': 'Not listened',
        'fps': timeline.fps,
        'thresholds': THRESHOLDS,
        'summary': {
            'joins': len(joins),
            'continuousSplits': timeline.continuous_splits,
            'keptWords': len(pieces),
            'problems': sum(item['severity'] == 'problem' for item in flags),
            'checks': sum(item['severity'] == 'check' for item in flags),
            'joinsWithFlags': sum(bool(join['flags']) for join in joins),
        },
        'opening': opening,
        'ending': ending,
        'joins': joins,
    }


def clock(seconds):
    total = ms(seconds)
    hours, minutes = total // 3600000, total // 60000 % 60
    text = f'{minutes:02}:{total // 1000 % 60:02}.{total % 1000:03}'
    return f'{hours}:{text}' if hours else text


def flag_lines(flags):
    order = {'problem': 0, 'check': 1}
    return [f'  - `{item["code"]}`: {item["message"]}' for item in sorted(flags, key=lambda item: order[item['severity']])]


def join_entry(join):
    gap = f'{join["gapMs"]} ms {join["gapKind"]} gap' if join['gapMs'] is not None else 'gap not judged'
    removed = ' '.join(join['removedWords'][:12]) + (' …' if len(join['removedWords']) > 12 else '')
    lines = [f'- **Join {join["index"]} at {clock(join["timelineSeconds"])}** (frame {join["timelineFrame"]}, '
             f'{join["outgoing"]} to {join["incoming"]}), {gap}: "{join["before"]}" | "{join["after"]}"']
    if removed:
        lines.append(f'  - Removed here: "{removed}"')
    return lines + flag_lines(join['flags'])


def render_markdown(report):
    summary = report['summary']
    edges = [('Opening', report['opening']), ('Ending', report['ending'])]
    has = lambda entry, severity: any(item['severity'] == severity for item in entry['flags'])  # noqa: E731
    lines = [
        '# Join check', '',
        f'**{report["listening"]}.** Read this first, then listen. This is a reading of the word times, so it does '
        'not replace the normal-speed audition in the A-cut quality gate.', '',
        f'Joins: {summary["joins"]}. Kept words: {summary["keptWords"]}. Problems: {summary["problems"]}. '
        f'Checks: {summary["checks"]}. Joins with a flag: {summary["joinsWithFlags"]}. The counts of problems and '
        f'checks include the opening and ending. Clip splits with continuous source (not joins): '
        f'{summary["continuousSplits"]}.', '',
        'Transcript word times are approximate, so a flag says where to listen, not that a word is broken. '
        'No flag does not mean clean: a sound missing from the transcript cannot show up here.', '',
    ]
    for title, severity in (('Listen to these first', 'problem'), ('Then check these', 'check')):
        chosen = [join for join in report['joins'] if has(join, severity) and (severity == 'problem' or not has(join, 'problem'))]
        chosen_edges = [(name, entry) for name, entry in edges
                        if has(entry, severity) and (severity == 'problem' or not has(entry, 'problem'))]
        if not chosen and not chosen_edges:
            continue
        lines += [f'## {title}', '']
        for name, entry in chosen_edges:
            lines += [f'- **{name}**'] + flag_lines(entry['flags'])
        for join in chosen:
            lines += join_entry(join)
        lines.append('')
    limits = report['thresholds']
    lines += [
        '## Preferences used', '',
        f'- Before the first word: {limits["leadInMs"][0]}-{limits["leadInMs"][1]} ms.',
        f'- Gap between linked phrases: {limits["linkedGapMs"][0]}-{limits["linkedGapMs"][1]} ms. '
        'A last word ending in . ! or ? counts as a new thought; anything else as linked.',
        f'- Gap for a new thought: {limits["newThoughtGapMs"][0]}-{limits["newThoughtGapMs"][1]} ms.',
        f'- Space around speech edges: at least {limits["speechEdgeMs"][0]} ms where possible '
        f'(preference {limits["speechEdgeMs"][0]}-{limits["speechEdgeMs"][1]} ms; soft endings can need more).',
        f'- Audio fade: {limits["audioFadeMs"]} ms. A fade can hide a click, not repair a clipped word.',
        f'- Working limits of this check: a clip of up to {limits["clippedEdgeMaxMs"]} ms counts as clipping an edge; '
        f'an orphan is a word of up to {limits["orphanWordMaxMs"]} ms with at least {limits["orphanPauseMs"]} ms of '
        'silence or a cut on the other side.',
        '',
    ]
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('words', type=Path, help='word-timed transcript JSON')
    parser.add_argument('cutlist', type=Path, help='cut list JSON with source in/out frames and fps')
    parser.add_argument('--out-dir', required=True, type=Path, help='folder for join-check.md and join-check.json')
    args = parser.parse_args()
    targets = args.out_dir / 'join-check.md', args.out_dir / 'join-check.json'
    for target in targets:
        if target.exists():
            sys.exit(f'{target} already exists. Choose a new folder; older reports stay as they are.')
    transcript = json.loads(args.words.read_text(encoding='utf-8-sig'))
    cutlist = json.loads(args.cutlist.read_text(encoding='utf-8-sig'))
    try:
        cut_timeline.check_same_source(transcript, cutlist)
        report = check_joins(cut_timeline.load_words(transcript), cut_timeline.load_cutlist(cutlist))
    except ValueError as error:
        sys.exit(f'Join check stopped: {error}')
    report['inputs'] = {'words': args.words.name, 'cutList': args.cutlist.name}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with targets[0].open('x', encoding='utf-8', newline='\n') as output:
        output.write(render_markdown(report))
    with targets[1].open('x', encoding='utf-8', newline='\n') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    summary = report['summary']
    print(f'{summary["joins"]} joins read, not listened: {summary["problems"]} problems, {summary["checks"]} checks. '
          f'Read {targets[0]} first.')


if __name__ == '__main__':
    main()
