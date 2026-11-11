"""YouTube chapters and clip candidates for a live tool lab replay.

Reads the <name>.words.json that tools/transcribe_local.py writes
({words:[{id,text,startMs,endMs}]}) and writes next to it:

  <name>.chapters.txt   YouTube chapter lines, 0:00 first, each at least 10 s
  <name>.clips.json     5 clip candidates of 20-60 s for Lex to approve

With --sections run-sheet.txt (one section title per line) each chapter goes
where the transcript first says words from that title. Without it the replay
is split on its longest silences. Existing outputs are never overwritten.
"""
import argparse
import json
import re
import sys
from pathlib import Path

MIN_CHAPTER_MS = 10_000
MAX_CHAPTERS = 15
CLIP_MIN_MS, CLIP_MAX_MS, CLIP_COUNT = 20_000, 60_000, 5
SENTENCE_GAP_MS = 700
HOOK_PHRASES = ('the verdict', 'surprised', 'surprising', 'the problem is', "here's why", 'here is why',
                'the catch', 'turns out', 'the trick', 'mistake', 'the winner', 'honestly')
STOPWORDS = set('a an and the of to in on for with is it this that round part section step vs'.split())


NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve'.split())}
NUMBER_WORDS.update(first='1', second='2', third='3', fourth='4', fifth='5')


def tokens(text):
    words = (NUMBER_WORDS.get(t, t) for t in re.findall(r"[a-z0-9']+", text.lower()))
    return [t for t in words if t not in STOPWORDS]


def sentences(words):
    """Group words into sentences: end on . ? ! or on a pause longer than SENTENCE_GAP_MS."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = nxt['startMs'] - w['endMs'] if nxt else 0
        if nxt is None or w['text'].rstrip().endswith(('.', '?', '!')) or gap > SENTENCE_GAP_MS:
            prev_end = out[-1]['endMs'] if out else cur[0]['startMs']
            out.append({'startMs': cur[0]['startMs'], 'endMs': cur[-1]['endMs'],
                        'text': ' '.join(x['text'].strip() for x in cur),
                        'pauseBeforeMs': cur[0]['startMs'] - prev_end,
                        'complete': cur[-1]['text'].rstrip().endswith(('.', '?', '!'))})
            cur = []
    return out


def stamp(ms):
    s = int(ms // 1000)
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def short_title(text, n=5):
    t = ' '.join(re.sub(r'[^\w\s\'-]', '', text).split()[:n])
    return t[:1].upper() + t[1:] if t else 'Untitled'


def chapters_from_sections(sents, titles, end_ms):
    chapters, after = [[0, titles[0]]], 0  # the stream opens with the first run-sheet line
    for title in titles[1:]:
        want = set(tokens(title))
        need = min(2, len(want)) or 1
        hit = None
        for i, s in enumerate(sents):
            if s['startMs'] < after + MIN_CHAPTER_MS or s['startMs'] > end_ms - MIN_CHAPTER_MS:
                continue
            window = set(tokens(' '.join(x['text'] for x in sents[i:i + 2])))
            if len(want & window) >= need:
                hit = s['startMs'] if want & set(tokens(s['text'])) else sents[i + 1]['startMs']
                break
        if hit is None:
            print(f'warning: no transcript match for section "{title}", skipped', file=sys.stderr)
            continue
        chapters.append([hit, title])
        after = hit
    return chapters[:MAX_CHAPTERS]


def chapters_from_silences(sents, end_ms):
    target = max(1, min(10, int(end_ms // (MIN_CHAPTER_MS * 3)) or 1))
    spacing = max(MIN_CHAPTER_MS, end_ms // (target * 3))
    cuts = []
    for s in sorted(sents[1:], key=lambda s: -s['pauseBeforeMs']):
        if len(cuts) >= target - 1:
            break
        t = s['startMs']
        if t < spacing or end_ms - t < MIN_CHAPTER_MS or any(abs(t - c) < spacing for c in cuts):
            continue
        cuts.append(t)
    starts = [0] + sorted(cuts)
    by_start = {s['startMs']: s for s in sents}
    return [[t, short_title(by_start[t]['text'] if t in by_start else sents[0]['text'])] for t in starts]


def clip_candidates(sents):
    cands = []
    for i, s in enumerate(sents):
        j, end = i, s['endMs']
        while j + 1 < len(sents) and sents[j + 1]['endMs'] - s['startMs'] <= CLIP_MAX_MS and \
                (end - s['startMs'] < CLIP_MIN_MS or not sents[j]['complete']):
            j += 1
            end = sents[j]['endMs']
        if not CLIP_MIN_MS <= end - s['startMs'] <= CLIP_MAX_MS:
            continue
        body = ' '.join(x['text'] for x in sents[i:j + 1]).lower()
        score, why = 0.0, []
        if s['pauseBeforeMs'] >= 1500:
            score += min(s['pauseBeforeMs'], 4000) / 1000
            why.append(f'starts after a {s["pauseBeforeMs"] / 1000:.1f} s pause')
        phrases = [p for p in HOOK_PHRASES if p in body]
        if phrases:
            score += 3 * len(phrases)
            why.append('says ' + ', '.join(f'"{p}"' for p in phrases))
        if re.search(r'\d', body):
            score += 1
            why.append('mentions a number')
        if sents[j]['complete'] and (i == 0 or sents[i - 1]['complete']):
            score += 1
            why.append('starts and ends on a full sentence')
        if score:
            cands.append({'startMs': s['startMs'], 'endMs': end, 'score': round(score, 2),
                          'hook': s['text'], 'reason': '; '.join(why)})
    picked = []
    for c in sorted(cands, key=lambda c: (-c['score'], c['startMs'])):
        if all(c['endMs'] <= p['startMs'] or c['startMs'] >= p['endMs'] for p in picked):
            picked.append(c)
        if len(picked) == CLIP_COUNT:
            break
    picked.sort(key=lambda c: c['startMs'])
    return [{'n': k + 1, 'status': 'candidate', 'start': stamp(c['startMs']), 'end': stamp(c['endMs']),
             'durationS': round((c['endMs'] - c['startMs']) / 1000, 1), **c} for k, c in enumerate(picked)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('words', type=Path, help='<name>.words.json from tools/transcribe-local')
    ap.add_argument('--sections', type=Path, help='run sheet: one section title per line')
    args = ap.parse_args()

    words = [w for w in json.loads(args.words.read_text(encoding='utf-8'))['words'] if w['text'].strip()]
    if not words:
        sys.exit('No words in transcript.')
    name = args.words.name.removesuffix('.json').removesuffix('.words')
    chapters_path = args.words.with_name(f'{name}.chapters.txt')
    clips_path = args.words.with_name(f'{name}.clips.json')
    for p in (chapters_path, clips_path):
        if p.exists():
            sys.exit(f'{p} already exists; move it away first (never overwritten).')

    sents = sentences(words)
    end_ms = words[-1]['endMs']
    if args.sections:
        titles = [t.strip() for t in args.sections.read_text(encoding='utf-8').splitlines() if t.strip()]
        chapters = chapters_from_sections(sents, titles, end_ms)
    else:
        chapters = chapters_from_silences(sents, end_ms)

    chapters_path.write_text(''.join(f'{stamp(t)} {title}\n' for t, title in chapters), encoding='utf-8')
    clips = clip_candidates(sents)
    clips_path.write_text(json.dumps({
        'source': args.words.name,
        'note': 'Candidates only. Lex approves each clip before anything is cut.',
        'clips': clips}, ensure_ascii=False, indent=1), encoding='utf-8')

    print(f'{len(chapters)} chapters -> {chapters_path}')
    if len(chapters) < 3:
        print('note: YouTube shows chapters only with 3 or more.')
    print(f'{len(clips)} clip candidates -> {clips_path}')


if __name__ == '__main__':
    main()
