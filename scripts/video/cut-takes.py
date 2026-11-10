"""Pick the takes in a word-timed transcript and propose a cut list and a paper edit for Lex to approve.

Usage: python cut-takes.py words.json --fps 30 --out-dir folder [--script script.md] [--demo 410-418] [--source PATH]
Writes cutlist.json, paper-edit.md and paper-edit.json into a folder; never overwrites, never touches media and
renders nothing. The cut list is a proposal: Lex approves the paper edit before any timeline is built or rendered.

What it does, in order:
1. A quiet "again" between pauses marks a retake: the failed attempt before it and the word itself are removed.
   "Again and again" and "again" inside a sentence are speech.
2. A sentence begun and dropped, then restarted after a pause with the same words, loses the dropped start.
3. Takes of the same line (a restart, the same script sentence however far apart, or a near copy within a minute
   when there is no script) keep the last complete take. When that take is clearly worse than an earlier one, the
   better take stays and Lex is told.
4. Unfinished starts that the transcript ran into other words (no full stop, a stray word between attempts, or a
   restart part way through a phrase) go too: every attempt before a complete take that opens with the take's
   first two words, back to a sentence that says something else.
5. Joins keep room around speech and a whole silence in Lex's ranges; long pauses are shortened, a breath inside a
   linked phrase stays, and a cut never lands inside a word.
A marked screen demo is never cut: what would have been removed there is listed for Lex instead.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cut_timeline  # noqa: E402

SETTINGS = {
    # Lex's answers of 2026-11-10 (room on both sides of speech, the whole silence at a join).
    'roomMs': [100, 150],
    'linkedGapMs': [150, 250],
    'newThoughtGapMs': [250, 450],
    'audioFadeMs': 15,
    # Where the cutter aims inside those ranges.
    'linkedTargetMs': 200,
    'newThoughtTargetMs': 300,
    # Working values of this cutter, not Lex's words; each is listed in the paper edit.
    'breathMaxMs': 700,
    'longSilenceMs': 3000,
    'cuePauseMs': 250,
    'restartPauseMs': 300,
    'attemptBreakMs': 1500,
    'attemptMaxSeconds': 30,
    'nearTakeSeconds': 60,
    'scriptMatch': 0.5,
    'nearTakeMatch': 0.6,
    'nearTakeRest': 0.5,
    'restartRepeat': 0.6,
    'completeCoverage': 0.8,
    'worseCoverageDrop': 0.15,
    'worseStumbles': 2,
    'worseKeptPoint': 0.6,
}
SENTENCE_END = re.compile(r'[.!?]["\')\]]*$')
TRAILING_MARK = re.compile(r'[,;:.!?–—-]["\')\]]*$')
FILLERS = {'um', 'uh', 'uhm', 'er', 'erm', 'ah', 'hmm', 'mm'}
REASONS = {
    'retake-cue': 'a quiet "again" marks a retake',
    'abandoned-start': 'a sentence begun, dropped and restarted',
    'earlier-take': 'an earlier take of the same line',
    'worse-later-take': 'a later take that is worse than the one kept',
}
TOLERANCE = cut_timeline.WORD_EDGE_TOLERANCE


def norm(text):
    return re.sub(r'[\W_]', '', text.lower())


def ends_sentence(word):
    return bool(SENTENCE_END.search(word.text))


def loose_equal(first, second):
    """Equal, or the same word with a short ending added, as in "I" and "I'm"."""
    short, long = sorted((first, second), key=len)
    return first == second or (bool(short) and long.startswith(short) and len(long) - len(short) <= 2)


def overlap(first, second):
    return sum((Counter(first) & Counter(second)).values())


def dice(first, second):
    return 2 * overlap(first, second) / (len(first) + len(second)) if first and second else 0.0


def script_sentences(text):
    lines = [line for line in text.splitlines() if not line.lstrip().startswith('#')]
    text = re.sub(r'\[[^\]]*\]', ' ', ' '.join(lines))
    sentences = [sentence.strip() for sentence in re.split(r'(?<=[.!?])\s+', text)]
    return [[norm(word) for word in sentence.split() if norm(word)] for sentence in sentences if sentence]


class Cutter:
    def __init__(self, words, fps, script_text=None, demos=()):
        self.words = words
        self.fps = fps
        self.norms = [norm(word.text) for word in words]
        self.keep = [True] * len(words)
        self.demos = [tuple(demo) for demo in demos]
        self.script = script_sentences(script_text) if script_text else None
        # Lines the script itself says more than once: never treated as takes of one line.
        self.repeated = {line for line, count in Counter(map(tuple, self.script or ())).items() if count > 1}
        self.removed = []
        self.flags = []

    # Small helpers.

    def gap_ms(self, before, after):
        return (self.words[after].start - self.words[before].end) * 1000

    def text(self, indices):
        return ' '.join(self.words[index].text for index in indices)

    def flag(self, code, first, last, message, start=None, end=None):
        """Record something for Lex; times default to the words' own, a silence passes its own span."""
        self.flags.append({'code': code,
                           'start': round(self.words[first].start if start is None else start, 3),
                           'end': round(self.words[last].end if end is None else end, 3),
                           'text': self.text(range(first, last + 1)), 'message': message})

    def demo_at(self, first, last):
        start, end = self.words[first].start, self.words[last].end
        return next((demo for demo in self.demos if start < demo[1] and end > demo[0]), None)

    def drop(self, indices, reason, keeps):
        """Remove these words, unless they touch a marked demo; then flag them for Lex instead."""
        indices = [index for index in indices if self.keep[index]]
        if not indices:
            return False
        demo = self.demo_at(indices[0], indices[-1])
        if demo:
            start, end = self.words[indices[0]].start, self.words[indices[-1]].end
            if any(item['code'] == 'demo-protected' and item['start'] < end and item['end'] > start
                   for item in self.flags):
                return False
            self.flag('demo-protected', indices[0], indices[-1],
                      f'Inside the marked screen demo ({demo[0]:.2f}-{demo[1]:.2f}): this would have been removed as '
                      f'{REASONS[reason]}. Left in; check the picture before cutting.')
            return False
        for index in indices:
            self.keep[index] = False
        # Words removed earlier can sit inside: list each unbroken stretch once, so nothing is counted twice.
        runs = [[indices[0]]]
        for index in indices[1:]:
            if index == runs[-1][-1] + 1:
                runs[-1].append(index)
            else:
                runs.append([index])
        self.removed += [{'first': run[0], 'last': run[-1], 'reason': reason, 'keeps': keeps} for run in runs]
        return True

    # 1. Quiet "again" retake cues.

    def find_attempt(self, cue, lower):
        """Return where the failed attempt before a cue starts, and whether the restart repeats its opening."""
        first = cue - 1
        limit = SETTINGS['attemptMaxSeconds']
        while first - 1 >= lower and not ends_sentence(self.words[first - 1]) \
                and self.gap_ms(first - 1, first) < SETTINGS['attemptBreakMs'] \
                and self.words[cue].start - self.words[first - 1].start <= limit:
            first -= 1
        restart = cue + 1
        key = self.norms[restart:restart + 1]
        if restart + 1 < len(self.words) and not ends_sentence(self.words[restart]) \
                and self.gap_ms(restart, restart + 1) < SETTINGS['restartPauseMs']:
            key = self.norms[restart:restart + 2]
        for start in range(first, cue - len(key) + 1):
            if self.norms[start:start + len(key)] == key:
                return start, True
        return first, False

    def possible_cue(self, index):
        self.flag('possible-retake-cue', index, index,
                  f'"{self.words[index].text}" after a pause may be a retake cue. Left in; listen.')

    def retake_cues(self):
        lower = 0
        for index, name in enumerate(self.norms[:-1]):
            if name != 'again' or not self.keep[index]:
                continue
            before = index == 0 or self.gap_ms(index - 1, index) >= SETTINGS['cuePauseMs'] \
                or bool(TRAILING_MARK.search(self.words[index - 1].text))
            after = self.gap_ms(index, index + 1) >= SETTINGS['cuePauseMs'] \
                or bool(TRAILING_MARK.search(self.words[index].text))
            natural = self.norms[index + 1:index + 3] == ['and', 'again'] \
                or (index > 0 and self.norms[index - 1] in {'and', 'once', 'over'})
            if natural or not (before or after):
                if natural and before and after:
                    self.possible_cue(index)
                continue
            start, matched = (index, False) if index == lower else self.find_attempt(index, lower)
            if not (matched or (before and after)):
                self.possible_cue(index)
                continue
            self.drop(range(start, index + 1), 'retake-cue', index + 1)
            lower = index + 1

    # 2 and 3. Restarts and takes of the same line.

    def phrases(self):
        """Kept words split at a sentence end, a pause long enough to restart in, or a removed stretch."""
        groups = []
        for index in (index for index, kept in enumerate(self.keep) if kept):
            previous = groups[-1][-1] if groups else None
            if previous is None or previous != index - 1 or ends_sentence(self.words[previous]) \
                    or self.gap_ms(previous, index) >= SETTINGS['restartPauseMs']:
                groups.append([index])
            else:
                groups[-1].append(index)
        return groups

    def sentences(self):
        groups = []
        for index in (index for index, kept in enumerate(self.keep) if kept):
            previous = groups[-1][-1] if groups else None
            if previous is None or previous != index - 1 or ends_sentence(self.words[previous]):
                groups.append([index])
            else:
                groups[-1].append(index)
        return groups

    def restarts(self):
        """Drop abandoned starts; return (earlier, later) first-word pairs where a complete sentence was redone."""
        pairs = []
        phrases = self.phrases()
        for first, second in zip(phrases, phrases[1:]):
            size = 2 if len(first) >= 2 and len(second) >= 2 else 1
            if len(first) > 1 and size == 1:
                continue
            if not all(loose_equal(self.norms[a], self.norms[b]) for a, b in zip(first[:size], second[:size])):
                continue
            if ends_sentence(self.words[first[-1]]):
                pairs.append((first[0], second[0]))
            elif not self.mostly_repeated(first, second):
                continue
            elif TRAILING_MARK.search(self.words[first[-1]].text) and not self.repeats_whole(first, second):
                # "The tool is fast, the tool is cheap." repeats on purpose; a dropped start usually just stops.
                self.flag('possible-restart', first[0], second[size - 1],
                          f'Possible restart: "{self.text(first)}" then "{self.text(second[:size])}". Left in; '
                          'listen and cut by hand if it is one.')
            else:
                self.drop(first, 'abandoned-start', second[0])
        # A stray word or two between a sentence and its redo ("So," or "and then") still makes a pair.
        for first, between, second in zip(phrases, phrases[1:], phrases[2:]):
            if ends_sentence(self.words[first[-1]]) and len(between) <= 2 and self.opens_alike(first, second):
                pairs.append((first[0], second[0]))
        return pairs

    def repeats_whole(self, first, second):
        """True when the later phrase starts with every word of the earlier one."""
        return len(second) >= len(first) and all(loose_equal(self.norms[a], self.norms[b])
                                                 for a, b in zip(first, second))

    def mostly_repeated(self, first, second):
        """True when the later phrase starts by repeating most of the earlier one, as a restart does."""
        matched = 0
        for a, b in zip(first, second):
            if not loose_equal(self.norms[a], self.norms[b]):
                break
            matched += 1
        return matched >= SETTINGS['restartRepeat'] * len(first)

    def stops_short(self, first, second):
        """True when the earlier sentence repeats most of the later one's opening and stops where the later goes on."""
        return self.mostly_repeated(first, second) and len(second) >= len(first) + 2

    def opens_alike(self, first, second):
        return len(first) >= 2 and len(second) >= 2 \
            and all(loose_equal(self.norms[a], self.norms[b]) for a, b in zip(first[:2], second[:2]))

    def same_line(self, first, second):
        """True when two sentences say the same line: the same script sentence, or a near copy when neither
        sentence is in the script (or there is no script)."""
        if self.script:
            (score, line), (other_score, other_line) = self.best_line(first), self.best_line(second)
            if max(score, other_score) >= SETTINGS['scriptMatch']:
                return line == other_line and min(score, other_score) >= SETTINGS['scriptMatch'] \
                    and tuple(self.script[line]) not in self.repeated
            # Neither is in the script (an ad-lib said twice): compare the two sentences themselves.
        names = [self.norms[index] for index in first]
        other = [self.norms[index] for index in second]
        if dice(names, other) < SETTINGS['nearTakeMatch']:
            return False
        # Sentences that only open alike ("This is the tool." / "This is the price.") are two lines: what follows
        # the shared opening must match too, unless one take simply stops where the other goes on.
        shared = 0
        while shared < min(len(names), len(other)) and loose_equal(names[shared], other[shared]):
            shared += 1
        rest, other_rest = names[shared:], other[shared:]
        return not rest or not other_rest or dice(rest, other_rest) >= SETTINGS['nearTakeRest']

    def stumbles(self, sentence):
        names = [self.norms[index] for index in sentence]
        return sum(name in FILLERS for name in names) + sum(a == b for a, b in zip(names, names[1:]))

    def coverage(self, sentence, line):
        return overlap([self.norms[index] for index in sentence], self.script[line]) / len(self.script[line])

    def best_line(self, sentence):
        names = [self.norms[index] for index in sentence]
        scores = [(dice(names, line), number) for number, line in enumerate(self.script)]
        return max(scores, default=(0.0, None))

    def worse(self, later, earlier, line):
        """Return why the later take is clearly worse than the earlier one, or an empty list."""
        reasons = []
        if line is not None:
            drop = self.coverage(earlier, line) - self.coverage(later, line)
            if drop > SETTINGS['worseCoverageDrop']:
                reasons.append(f'leaves out {round(drop * 100)}% more of the script line')
        extra = self.stumbles(later) - self.stumbles(earlier)
        if extra >= SETTINGS['worseStumbles']:
            reasons.append(f'has {extra} more stumbles (um, uh or a repeated word)')
        if line is None:
            point = [self.norms[index] for index in earlier if len(self.norms[index]) >= 4]
            kept = overlap(point, [self.norms[index] for index in later]) / len(point) if point else 1.0
            if kept < SETTINGS['worseKeptPoint']:
                reasons.append(f'keeps only {round(kept * 100)}% of the earlier take\'s main words')
        return reasons

    def takes(self, pairs):
        sentences = self.sentences()
        owner = {index: number for number, sentence in enumerate(sentences) for index in sentence}
        parent = list(range(len(sentences)))

        def root(number):
            while parent[number] != number:
                parent[number] = parent[parent[number]]
                number = parent[number]
            return number

        def join(first, second):
            parent[root(second)] = root(first)

        for earlier, later in pairs:
            if earlier in owner and later in owner \
                    and self.same_line(sentences[owner[earlier]], sentences[owner[later]]):
                join(owner[earlier], owner[later])
        lines = {}
        if self.script:
            for number, sentence in enumerate(sentences):
                score, line = self.best_line(sentence)
                # A short sentence, or one the script itself says twice, is not grouped with others.
                if score >= SETTINGS['scriptMatch'] and len(sentence) >= 3 \
                        and tuple(self.script[line]) not in self.repeated:
                    lines[number] = line
                    first = next((other for other, value in lines.items() if value == line and other != number), None)
                    if first is not None:
                        join(first, number)
        else:
            for number, sentence in enumerate(sentences):
                for other in range(number + 1, len(sentences)):
                    later = sentences[other]
                    if self.words[later[0]].start - self.words[sentence[-1]].end > SETTINGS['nearTakeSeconds']:
                        break
                    if min(len(sentence), len(later)) >= 4 \
                            and loose_equal(self.norms[sentence[0]], self.norms[later[0]]) \
                            and self.same_line(sentence, later):
                        join(number, other)
        groups = {}
        for number in range(len(sentences)):
            groups.setdefault(root(number), []).append(number)
        for members in groups.values():
            if len(members) > 1:
                self.resolve([sentences[number] for number in sorted(members)],
                             next((lines[number] for number in members if number in lines), None))

    def complete(self, sentence, line):
        if not ends_sentence(self.words[sentence[-1]]):
            return False
        return line is None or self.coverage(sentence, line) >= SETTINGS['completeCoverage']

    def resolve(self, takes, line):
        """Keep one take of a line: the last complete one, unless it is clearly worse than an earlier one."""
        demo = next((self.demo_at(take[0], take[-1]) for take in takes if self.demo_at(take[0], take[-1])), None)
        if demo:
            self.flag('demo-protected', takes[0][0], takes[-1][-1],
                      f'{len(takes)} takes of one line inside the marked screen demo ({demo[0]:.2f}-{demo[1]:.2f}). '
                      'All left in; pick one after checking the picture.')
            return
        complete = [number for number, take in enumerate(takes) if self.complete(take, line)]
        if not complete:
            chosen = len(takes) - 1
            self.flag('no-complete-take', takes[chosen][0], takes[chosen][-1],
                      f'None of the {len(takes)} takes of this line looks complete. Kept the last one; check it.')
        else:
            chosen = complete[-1]
            earlier = complete[:-1]
            if earlier:
                best = max(earlier, key=lambda number: (self.coverage(takes[number], line) if line is not None else 0,
                                                        -self.stumbles(takes[number]), number))
                reasons = self.worse(takes[chosen], takes[best], line)
                if reasons:
                    last = takes[chosen]
                    self.flag('kept-better-take', takes[best][0], takes[best][-1],
                              f'Kept the earlier take at {self.words[takes[best][0]].start:.2f}: the last take '
                              f'({self.words[last[0]].start:.2f}-{self.words[last[-1]].end:.2f}) '
                              f'{" and ".join(reasons)}. Say if you prefer the last take.')
                    chosen = best
        keeps = takes[chosen][0]
        start, rest = self.false_start(takes[chosen], takes, line)
        if start and self.drop(start, 'abandoned-start', rest[0]):
            keeps = rest[0]
        for number, take in enumerate(takes):
            if number == chosen:
                continue
            reason = 'earlier-take' if number < chosen else 'worse-later-take'
            if number > chosen and not self.complete(take, line):
                reason = 'abandoned-start'
            self.drop(take, reason, keeps)

    def false_start(self, take, takes, line):
        """Split a kept take at a long pause when what follows is itself a complete take that opens like another take
        of the line: the words before the pause are an unfinished start the transcript ran into the take."""
        for cut in range(len(take) - 1, 0, -1):
            if self.gap_ms(take[cut - 1], take[cut]) < SETTINGS['attemptBreakMs']:
                continue
            rest = take[cut:]
            others = [take[:cut]] + [other for other in takes if other is not take]
            if len(rest) >= 3 and self.complete(rest, line) and any(self.opens_alike(rest, other) for other in others):
                return take[:cut], rest
        return [], take

    # 4. Unfinished starts the transcript ran into other words.

    def take_at(self, first):
        """The kept words from here to the end of their sentence, or None when no sentence end follows."""
        take = []
        for index in range(first, len(self.words)):
            if not self.keep[index]:
                return None
            take.append(index)
            if ends_sentence(self.words[index]):
                return take
        return None

    def script_says_once(self, attempt, take):
        """True when a script line holds the take, the attempt belongs to that same line, and the line says the
        take's opening only once: then a repeat after a comma is a restart, not a repeat on purpose."""
        if not self.script:
            return False
        (score, line), (_, attempt_line) = self.best_line(take), self.best_line(attempt)
        if score < SETTINGS['scriptMatch'] or attempt_line != line:
            return False
        words, opening = self.script[line], self.norms[take[0]:take[0] + 2]
        return sum(all(loose_equal(a, b) for a, b in zip(words[at:at + 2], opening))
                   for at in range(len(words) - 1)) == 1

    def restarted_lines(self):
        """Remove the attempts before a complete take that open with the take's first two words, even when the
        transcript ran them into other words; stop at a sentence that says something else."""
        pieces, asks = self.phrases(), []
        for number in range(len(pieces) - 1, 0, -1):
            take = self.take_at(pieces[number][0]) if self.keep[pieces[number][0]] else None
            if not take or len(take) < 3:
                continue
            if self.script:
                score, line = self.best_line(take)
                if score >= SETTINGS['scriptMatch'] and tuple(self.script[line]) in self.repeated:
                    continue  # The script says this line twice on purpose.
            opening, start, passed, unsure = self.norms[take[0]:take[0] + 2], None, [], False
            for piece in reversed(pieces[:number]):
                kept = [index for index in piece if self.keep[index]]
                if not kept:
                    continue
                if self.words[take[0]].start - self.words[kept[0]].start > SETTINGS['attemptMaxSeconds']:
                    break
                at = next((at for at in range(len(kept) - 1) if all(
                    loose_equal(self.norms[a], b) for a, b in zip(kept[at:at + 2], opening))), None)
                finished = ends_sentence(self.words[kept[-1]])
                if at is None:
                    if finished and len(kept) >= 3:
                        break
                    passed.append(kept)
                    continue
                # A finished sentence before the take must be the same line said shorter, not a different line.
                # Without a script, one that only repeats the take's opening and stops is asked about, not cut.
                if finished and not self.same_line(kept[at:], take):
                    if self.script or not self.stops_short(kept[at:], take):
                        break
                    unsure = True
                # One word before the opening ("So next, the ...") belongs to the attempt.
                start = kept[0] if at <= 1 else kept[at]
            if start is None:
                continue
            attempt = [index for index in range(start, take[0]) if self.keep[index]]
            if TRAILING_MARK.search(self.words[attempt[-1]].text) \
                    and self.gap_ms(attempt[-1], take[0]) < SETTINGS['attemptBreakMs'] \
                    and not self.script_says_once(attempt, take):
                continue  # "The tool is fast, the tool is cheap." may repeat on purpose: restarts() flagged it.
            if self.script:
                # Everything removed must belong to the take's own script line, not to the talk around it.
                if self.best_line(attempt)[1] == self.best_line(take)[1]:
                    self.drop(attempt, 'abandoned-start', take[0])
            elif unsure or any(len(other) >= 3 and other[0] > start for other in passed):
                # Without a script, more than a stray word between the attempts may be real talk: ask, do not cut.
                asks.append((attempt, take[0]))
            else:
                self.drop(attempt, 'abandoned-start', take[0])
        # A restart flagged earlier whose words are now removed is no longer a question for Lex.
        removed = [self.words[index].start for index, kept in enumerate(self.keep) if not kept]
        self.flags = [item for item in self.flags if item['code'] not in ('possible-restart', 'possible-retake-cue')
                      or not any(item['start'] - TOLERANCE <= at <= item['end'] for at in removed)]
        for attempt, take in asks:
            self.flag('possible-restart', attempt[0], attempt[-1],
                      f'Possible unfinished start before the take at {self.words[take].start:.2f}: '
                      f'"{self.text(attempt)}". Left in; listen and cut by hand if it is one.')

    def possible_restarts(self):
        """Flag a two-word run repeated within a few words of a kept sentence: often a restart without a pause."""
        flagged = [(item['start'], item['end']) for item in self.flags
                   if item['code'] in ('possible-restart', 'demo-protected')]
        for sentence in self.sentences():
            span = self.words[sentence[0]].start, self.words[sentence[-1]].end
            if any(start < span[1] and end > span[0] for start, end in flagged):
                continue
            names = [self.norms[index] for index in sentence]
            for start in range(len(names) - 3):
                found = next((other for other in range(start + 2, min(start + 7, len(names) - 1))
                              if loose_equal(names[start], names[other])
                              and loose_equal(names[start + 1], names[other + 1])), None)
                if found is not None:
                    self.flag('possible-restart', sentence[start], sentence[found + 1],
                              f'Possible restart: "{self.text(sentence[start:found + 2])}". Left in; listen and cut '
                              'by hand if it is one.')
                    break

    def ad_libs(self):
        if not self.script:
            return []
        chosen = []
        for sentence in self.sentences():
            if len(sentence) >= 3 and self.best_line(sentence)[0] < SETTINGS['scriptMatch']:
                chosen.append({'start': round(self.words[sentence[0]].start, 3),
                               'end': round(self.words[sentence[-1]].end, 3), 'text': self.text(sentence)})
        return chosen

    # 5. Frames.

    def frames(self, at, low_room, high_room, direction, limit):
        """Frames that leave between low and high ms of room on one side of a word time, inside a limit."""
        fps = self.fps
        candidates = range(math.floor((at - 0.2) * fps), math.ceil((at + 0.2) * fps) + 1)
        chosen = []
        for frame in candidates:
            room = round((frame / fps - at) * 1000) * direction
            inside = frame / fps <= limit + TOLERANCE if direction > 0 else frame / fps >= limit - TOLERANCE
            if low_room <= room <= high_room and inside and frame >= 0:
                chosen.append(frame)
        return chosen

    def fallback(self, at, direction, limit, word_index, side):
        """No frame leaves the preferred room: take the roomiest one that stays off both words, and flag it."""
        fps = self.fps
        frame = math.floor((limit + TOLERANCE) * fps) if direction > 0 else math.ceil((limit - TOLERANCE) * fps)
        if (frame / fps - at) * direction < -TOLERANCE:
            frame = math.ceil((at - TOLERANCE) * fps) if direction > 0 else math.floor((at + TOLERANCE) * fps)
        frame = max(frame, 0)
        room = round((frame / fps - at) * 1000) * direction
        self.flag('tight-edge', word_index, word_index,
                  f'Only {room} ms of room {side} "{self.words[word_index].text}" (preferred '
                  f'{SETTINGS["roomMs"][0]}-{SETTINGS["roomMs"][1]} ms): the next sound is too close. Listen.')
        return frame

    def edge(self, index, direction, limit, target):
        at = self.words[index].end if direction > 0 else self.words[index].start
        low, high = SETTINGS['roomMs']
        options = self.frames(at, low, high, direction, limit)
        if not options:
            return self.fallback(at, direction, limit, index, 'after' if direction > 0 else 'before')
        return min(options, key=lambda frame: abs(round((frame / self.fps - at) * 1000) * direction - target))

    def join_frames(self, before, after, out_limit, in_limit):
        """Choose out and in frames for a join: room on both sides, the whole silence in Lex's range."""
        kind = 'new-thought' if ends_sentence(self.words[before]) else 'linked'
        low_gap, high_gap = SETTINGS['newThoughtGapMs' if kind == 'new-thought' else 'linkedGapMs']
        target = SETTINGS['newThoughtTargetMs' if kind == 'new-thought' else 'linkedTargetMs']
        end, start = self.words[before].end, self.words[after].start
        low, high = SETTINGS['roomMs']
        gap = lambda pair: room(pair[0], self.fps, end, 1) + room(pair[1], self.fps, start, -1)  # noqa: E731
        closest = None
        for stretch in (0, math.ceil(1000 / self.fps)):
            outs = self.frames(end, low, high + stretch, 1, out_limit)
            ins = self.frames(start, low, high + stretch, -1, in_limit)
            pairs = [(out, entry) for out in outs for entry in ins if entry > out]
            fitting = [pair for pair in pairs if low_gap <= gap(pair) <= high_gap]
            if fitting:
                note = f'one frame over {high} ms of room, to keep the silence in range' if stretch else None
                return min(fitting, key=lambda pair: abs(gap(pair) - target)) + (kind, note)
            if pairs and closest is None:
                closest = min(pairs, key=lambda pair: abs(gap(pair) - target))
        if closest:
            return closest + (kind, 'silence outside the range')
        outs = self.frames(end, low, high, 1, out_limit)
        ins = self.frames(start, low, high, -1, in_limit)
        out = outs[0] if outs else self.fallback(end, 1, out_limit, before, 'after')
        entry = ins[-1] if ins else self.fallback(start, -1, in_limit, after, 'before')
        return out, entry, kind, None

    def restore(self, before, after):
        """A removal too short to cut between two frames goes back in, and Lex is told."""
        for index in range(before + 1, after):
            self.keep[index] = True
        self.removed = [item for item in self.removed if not before < item['first'] < after]
        self.flag('too-short-to-cut', before + 1, after - 1,
                  'This would have been removed, but it is too short to cut cleanly at whole frames. Left in; listen.')

    def segments(self):
        kept = [index for index, value in enumerate(self.keep) if value]
        if not kept:
            raise ValueError('Every word was removed. Check the transcript and the retake marks.')
        first, last = kept[0], kept[-1]
        open_in = self.edge(first, -1, self.words[first - 1].end if first else 0.0, SETTINGS['roomMs'][1])
        runs, joins = [], []
        for before, after in zip(kept, kept[1:]):
            silence = self.gap_ms(before, after)
            removed = after != before + 1
            if not removed:
                if self.demo_at(before, after):
                    continue
                if silence >= SETTINGS['longSilenceMs']:
                    self.flag('long-silence', before, after,
                              f'{silence / 1000:.1f} s with no speech after "{self.words[before].text}": a screen '
                              'demo or a pause to remove? Left in.',
                              self.words[before].end, self.words[after].start)
                    continue
                limit = SETTINGS['newThoughtGapMs'][1] if ends_sentence(self.words[before]) \
                    else SETTINGS['breathMaxMs']
                if silence <= limit:
                    continue
            out_limit = self.words[before + 1].start
            in_limit = self.words[after - 1].end
            out, entry, kind, note = self.join_frames(before, after, out_limit, in_limit)
            if entry <= out:
                if removed:
                    self.restore(before, after)
                continue
            runs.append((open_in, out))
            open_in = entry
            joins.append({'before': before, 'after': after, 'out': out, 'in': entry, 'kind': kind, 'note': note,
                          'why': 'removed' if removed else 'pause'})
        close = self.edge(last, 1, self.words[last + 1].start if last + 1 < len(self.words) else math.inf,
                          SETTINGS['roomMs'][1])
        runs.append((open_in, close))
        return runs, joins

    def plan(self):
        self.retake_cues()
        self.takes(self.restarts())
        self.restarted_lines()
        self.possible_restarts()
        ad_libs = self.ad_libs()
        runs, joins = self.segments()
        return runs, joins, ad_libs


def room(frame, fps, at, direction):
    return round((frame / fps - at) * 1000) * direction


def plan(words, fps, script_text=None, demos=(), name='Take cut proposal', source_path=None, source_sha256=None):
    """Return {'cutlist': ..., 'paperEdit': ...} as plain data. Nothing is written."""
    if not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise ValueError('fps must be a positive number.')
    cutter = Cutter(words, fps, script_text, demos)
    runs, joins, ad_libs = cutter.plan()
    segments, start = [], 0
    for number, (source_in, source_out) in enumerate(runs, 1):
        segments.append({'id': f'C{number:02}', 'sourceInFrame': source_in, 'sourceOutFrame': source_out,
                         'timelineStartFrame': start, 'durationFrames': source_out - source_in})
        start += source_out - source_in
    cutlist = {'schemaVersion': 1, 'fps': fps, 'timelineName': name, 'durationFrames': start}
    if source_path:
        cutlist['sourcePath'] = source_path
    if source_sha256:
        cutlist['sourceSha256'] = source_sha256
    cutlist.update({'status': 'proposal', 'paperEdit': 'paper-edit.md', 'audioFadeMs': SETTINGS['audioFadeMs'],
                    'segments': segments})

    keep = cutter.keep
    removed = []
    for item in sorted(cutter.removed, key=lambda item: item['first']):
        keeps = next((index for index in range(item['keeps'], len(words)) if keep[index]), None)
        first, last = words[item['first']], words[item['last']]
        removed.append({'start': round(first.start, 3), 'end': round(last.end, 3),
                        'seconds': round(last.end - first.start, 2), 'reason': item['reason'],
                        'why': REASONS[item['reason']], 'text': cutter.text(range(item['first'], item['last'] + 1)),
                        'keeps': round(words[keeps].start, 3) if keeps is not None else None})
    join_entries = []
    for number, join in enumerate(joins, 1):
        before, after = words[join['before']], words[join['after']]
        tail = room(join['out'], fps, before.end, 1)
        head = room(join['in'], fps, after.start, -1)
        join_entries.append({
            'index': number, 'outgoing': segments[number - 1]['id'], 'incoming': segments[number]['id'],
            'sourceOutFrame': join['out'], 'sourceInFrame': join['in'],
            'sourceOutSeconds': round(join['out'] / fps, 3), 'sourceInSeconds': round(join['in'] / fps, 3),
            'why': join['why'], 'kind': join['kind'], 'lastWord': before.text, 'firstWord': after.text,
            'tailMs': tail, 'headMs': head, 'gapMs': tail + head, 'fadeMs': SETTINGS['audioFadeMs'],
            'note': join['note']})
    kept_words = [index for index, value in enumerate(keep) if value]
    source_seconds = sum(source_out - source_in for source_in, source_out in runs) / fps
    paper = {
        'schemaVersion': 1, 'kind': 'paper-edit', 'name': name,
        'status': 'proposal', 'approvedByLex': False, 'rendered': False,
        'hasScript': bool(cutter.script), 'demos': [list(demo) for demo in cutter.demos],
        'settings': SETTINGS,
        'summary': {'keptSeconds': round(source_seconds, 2),
                    'recordingSeconds': round(words[-1].end - words[0].start, 2),
                    'removedPassages': len(removed),
                    'removedSeconds': round(sum(item['seconds'] for item in removed), 2),
                    'joins': len(join_entries), 'flags': len(cutter.flags), 'adLibs': len(ad_libs)},
        'opening': {'leadInMs': room(runs[0][0], fps, words[kept_words[0]].start, -1)},
        'ending': {'tailMs': room(runs[-1][1], fps, words[kept_words[-1]].end, 1)},
        'removed': removed,
        'flags': sorted(cutter.flags, key=lambda item: item['start']),
        'adLibs': ad_libs,
        'joins': join_entries,
    }
    return {'cutlist': cutlist, 'paperEdit': paper}


def seconds(value):
    return f'{value:.2f}'


def render_paper_edit(paper):
    summary, limits = paper['summary'], paper['settings']
    lines = [
        f'# Paper edit: {paper["name"]}', '',
        '**Proposal. Not approved by Lex yet. Nothing is cut or rendered.** Read it, say what to change, then approve '
        'it before anyone builds the timeline or renders.', '',
        f'Kept {summary["keptSeconds"]} s of the {summary["recordingSeconds"]} s from the first word to the last. '
        f'Removed {summary["removedPassages"]} passages ({summary["removedSeconds"]} s of speech). '
        f'{summary["flags"]} things for Lex to decide. '
        f'{summary["adLibs"]} ad-libs kept. All times are seconds in the original recording.', '',
        '## Removed', '',
    ]
    for item in paper['removed']:
        keeps = f'; the take at {seconds(item["keeps"])} stays' if item['keeps'] is not None else ''
        lines.append(f'- **{seconds(item["start"])}-{seconds(item["end"])}** ({item["seconds"]} s), '
                     f'{item["why"]}{keeps}: "{item["text"]}"')
    if not paper['removed']:
        lines.append('- Nothing.')
    lines += ['', '## For Lex to decide', '']
    for item in paper['flags']:
        lines.append(f'- **{seconds(item["start"])}** {item["message"]}')
    if not paper['flags']:
        lines.append('- Nothing.')
    lines += ['', '## Ad-libs (kept, not in the script)', '']
    if not paper['hasScript']:
        lines.append('- No script given, so ad-libs were not checked.')
    for item in paper['adLibs']:
        lines.append(f'- **{seconds(item["start"])}-{seconds(item["end"])}** "{item["text"]}"')
    if paper['hasScript'] and not paper['adLibs']:
        lines.append('- None.')
    lines += [
        '', '## Timing used', '',
        f'- Room before and after speech at every cut: {limits["roomMs"][0]}-{limits["roomMs"][1]} ms '
        f'(opening {paper["opening"]["leadInMs"]} ms, ending {paper["ending"]["tailMs"]} ms).',
        f'- Whole silence at a join: about {limits["linkedTargetMs"]} ms between linked phrases (range '
        f'{limits["linkedGapMs"][0]}-{limits["linkedGapMs"][1]} ms), about {limits["newThoughtTargetMs"]} ms before a '
        f'new thought (range {limits["newThoughtGapMs"][0]}-{limits["newThoughtGapMs"][1]} ms). '
        'A word ending in . ! or ? counts as the end of a thought.',
        f'- A pause up to {limits["breathMaxMs"]} ms inside a linked phrase stays (a breath); a longer one, or a pause '
        f'over {limits["newThoughtGapMs"][1]} ms before a new thought, is shortened. A silence of '
        f'{limits["longSilenceMs"] // 1000} s or more stays and is listed above.',
        f'- {limits["audioFadeMs"]} ms audio fade at every cut.',
        f'- A quiet "again" counts as a retake cue when a pause of {limits["cuePauseMs"]} ms or more sits on both '
        'sides, or on one side when the restart repeats the attempt\'s opening words.',
        f'- A later take counts as clearly worse when it has {limits["worseStumbles"]} or more extra stumbles, '
        f'leaves out over {round(limits["worseCoverageDrop"] * 100)}% more of the script line, or (without a script) '
        f'keeps under {round(limits["worseKeptPoint"] * 100)}% of the earlier take\'s main words.',
        '- Word times are approximate. A clean paper edit is not a listened cut: listen to every join at normal speed.',
        '', '## Joins', '',
    ]
    for join in paper['joins']:
        note = f' ({join["note"]})' if join['note'] else ''
        why = 'after a removal' if join['why'] == 'removed' else 'shortened pause'
        lines.append(f'- Join {join["index"]} ({join["outgoing"]} to {join["incoming"]}), {why}, '
                     f'{seconds(join["sourceOutSeconds"])} to {seconds(join["sourceInSeconds"])}: '
                     f'"{join["lastWord"]}" | "{join["firstWord"]}", {join["kind"]} gap {join["gapMs"]} ms '
                     f'({join["tailMs"]} + {join["headMs"]}), {join["fadeMs"]} ms fade{note}.')
    if not paper['joins']:
        lines.append('- None.')
    lines.append('')
    return '\n'.join(lines)


def parse_demo(text):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*', text)
    if not match or float(match[1]) >= float(match[2]):
        raise ValueError(f'Bad --demo "{text}": use START-END in source seconds, start before end, like 410-418.')
    return float(match[1]), float(match[2])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('words', type=Path, help='word-timed transcript JSON on the original recording clock')
    parser.add_argument('--fps', required=True, type=float, help='frame rate of the original recording')
    parser.add_argument('--out-dir', required=True, type=Path, help='new folder for the cut list and paper edit')
    parser.add_argument('--script', type=Path, help='the script or prompter text, to match takes and find ad-libs')
    parser.add_argument('--demo', action='append', default=[], metavar='START-END',
                        help='a screen demo in source seconds that is never cut; repeat for more')
    parser.add_argument('--source', help='the recording path to write as sourcePath in the cut list')
    parser.add_argument('--name', help='timeline name (default: the transcript file name)')
    args = parser.parse_args()
    targets = [args.out_dir / name for name in ('paper-edit.md', 'paper-edit.json', 'cutlist.json')]
    for target in targets:
        if target.exists():
            sys.exit(f'{target} already exists. Choose a new folder; older proposals stay as they are.')
    try:
        demos = [parse_demo(text) for text in args.demo]
        transcript = json.loads(args.words.read_text(encoding='utf-8-sig'))
        script = args.script.read_text(encoding='utf-8-sig') if args.script else None
        original = transcript.get('original') if isinstance(transcript, dict) else None
        source_id = original.get('sourceId') if isinstance(original, dict) else None
        sha = source_id if isinstance(source_id, str) and cut_timeline.SHA256.fullmatch(source_id) else None
        fps = int(args.fps) if args.fps.is_integer() else args.fps
        result = plan(cut_timeline.load_words(transcript), fps, script, demos,
                      args.name or args.words.stem, args.source, sha)
    except (OSError, ValueError) as error:
        sys.exit(f'Cutter stopped: {error}')
    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        # The cut list goes last, so a failed write never leaves a cut list without its paper edit.
        with targets[0].open('x', encoding='utf-8', newline='\n') as output:
            output.write(render_paper_edit(result['paperEdit']))
        with targets[1].open('x', encoding='utf-8', newline='\n') as output:
            json.dump(result['paperEdit'], output, ensure_ascii=False, indent=2)
        with targets[2].open('x', encoding='utf-8', newline='\n') as output:
            json.dump(result['cutlist'], output, ensure_ascii=False, indent=2)
    except OSError as error:
        sys.exit(f'Cutter stopped: {error}')
    summary = result['paperEdit']['summary']
    print(f'Proposal only, nothing rendered: {summary["removedPassages"]} passages removed, {summary["flags"]} '
          f'things for Lex to decide. Lex approves {targets[0]} before anything is built or rendered.')


if __name__ == '__main__':
    main()
