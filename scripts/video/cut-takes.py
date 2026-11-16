"""Pick the takes in a word-timed transcript and propose a cut list and a paper edit for Lex to approve.

Usage: python cut-takes.py words.json --fps 30 --out-dir folder [--script script.md] [--demo 410-418] [--source PATH]
Writes cutlist.json, paper-edit.md and paper-edit.json into a folder; never overwrites, never touches media and
renders nothing. The cut list is a proposal: Lex approves the paper edit before any timeline is built or rendered.

What it does, in order:
0. With a script, a pause where one script line ends and another begins ends a sentence, also when the transcript
   left the full stop off (this decides what one take is; timing still follows the transcript's own full stops).
1. A quiet "again" between pauses marks a retake: the failed attempt before it and the word itself are removed.
   "Again and again" and "again" inside a sentence are speech.
2. A sentence begun and dropped, then restarted after a pause with the same words, loses the dropped start.
3. Takes of the same line (a restart, the same script sentence however far apart, or a near copy within a minute
   when there is no script) keep the last complete take. When that take is clearly worse than an earlier one, the
   better take stays and Lex is told. Two sentences that say different parts of one script line are not takes of
   it, a take goes only when the take that stays says what it says, and a start the script has as words of its
   own, or as a line it says twice, is listed instead of cut.
4. Unfinished starts that the transcript ran into other words (no full stop, a stray word between attempts, or a
   restart part way through a phrase) go too: every attempt before a complete take that opens with the take's
   first two words, back to a sentence that says something else. The whole run of failed tries goes up to the take
   that stays, never into it; a breath inside a take does not start a new one. A dropped start of under four words
   is only listed, as the paper edit's "inspect" restarts are. An "again" the script itself says is speech.
   Then the last check on all of the above: a script line said word for word in the recording is still said in
   what stays, as often as the script says it; if not, its last word-for-word reading goes back in and Lex is told.
5. Joins keep room around speech and a whole silence in Lex's ranges; long pauses are shortened, a breath inside a
   linked phrase stays, and a cut never lands inside a word: a removal with no frame between it and the kept
   word beside it goes back in and is listed.
A marked screen demo is never cut: what would have been removed there is listed for Lex instead, and a demo with
no speech in it stays in the kept ranges, with the silence between it and the speech kept beside it.
"""
import argparse
from collections import Counter
from difflib import SequenceMatcher
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cut_timeline  # noqa: E402

SETTINGS = {
    # Lex's answers of 2026-11-10 (room on both sides of speech) and 2026-11-11 (the whole silence at a join:
    # he listened to the cutter's version next to the original and chose the longer pauses).
    'roomMs': [100, 150],
    'linkedGapMs': [200, 300],
    'newThoughtGapMs': [350, 500],
    'audioFadeMs': 15,
    # Where the cutter aims inside those ranges.
    'linkedTargetMs': 250,
    'newThoughtTargetMs': 400,
    # Room per side at a new-thought join may reach this (half the aimed silence): 100-150 ms on each side adds up to
    # 300 ms at most, so 350-500 ms of silence needs more. Every other cut stays at 100-150 ms.
    'newThoughtRoomMaxMs': 200,
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
    # Two sentences are takes of one script line only when this share of the line's words that the shorter one says
    # is said by the other too; less, and they are two parts of the line.
    'samePartOfLine': 0.6,
    'completeCoverage': 0.8,
    'worseCoverageDrop': 0.15,
    'worseStumbles': 2,
    'worseKeptPoint': 0.6,
    # A dropped start shorter than this is listed, not cut: the paper edit only asks to inspect "And then I—".
    'shortStartWords': 4,
    # A line is an ad-lib only when no more than this share of its main words is in its closest script line.
    'adLibMainWords': 0.5,
}
SENTENCE_END = re.compile(r'[.!?]["\')\]]*$')
TRAILING_MARK = re.compile(r'[,;:.!?–—-]["\')\]]*$')
# In a script: the end of a sentence with the quotes, brackets and bold marks that close after it, and a list item.
SCRIPT_STOP = re.compile(r'(?<=[.!?])["\'”’»)\]*_`]*\s+')
LIST_ITEM = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+')
BROKEN_OFF = re.compile(r'[–—-]["\')\]]*$')
FILLERS = {'um', 'uh', 'uhm', 'er', 'erm', 'ah', 'hmm', 'mm'}
REASONS = {
    'retake-cue': 'a quiet "again" marks a retake',
    'abandoned-start': 'a sentence begun, dropped and restarted',
    'earlier-take': 'an earlier take of the same line',
    'worse-later-take': 'a later take that is worse than the one kept',
    'retake-run': 'failed tries before the take that stays',
}
JOIN_WHY = {'removed': 'after a removal', 'pause': 'shortened pause', 'demo': 'beside the marked screen demo'}
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


def in_order(first, second):
    """How many words two word lists share in the same order."""
    return sum(block.size for block in SequenceMatcher(None, first, second, autojunk=False).get_matching_blocks())


def script_sentences(text):
    """The script as one list of words per sentence. A sentence ends at . ! or ? (also in front of a closing quote,
    bracket or bold mark), and at a heading, a blank line or a list item, so a line without a full stop does not
    run into the next one. Headings and [directions] are left out."""
    blocks = [[]]
    for line in text.splitlines():
        heading, item = line.lstrip().startswith('#'), LIST_ITEM.match(line)
        if not line.strip() or heading or item:
            blocks.append([])
        if line.strip() and not heading:
            blocks[-1].append(LIST_ITEM.sub('', line))
    sentences = []
    for block in blocks:
        for sentence in SCRIPT_STOP.split(re.sub(r'\[[^\]]*\]', ' ', ' '.join(block))):
            words = [norm(word) for word in sentence.split() if norm(word)]
            if words:
                sentences.append(words)
    return sentences


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
        self.script_words = [word for line in self.script or () for word in line]
        self.removed = []
        self.flags = []
        # Words that end a sentence although the transcript has no full stop there (script_stops).
        self.stops = set()
        # best_line's answers by the words asked about: the same sentence is asked about at every step.
        self.closest = {}
        # Set when the cut opens or ends on a marked demo that has no speech.
        self.demo_opens = self.demo_ends = False

    # Small helpers.

    def ends(self, index):
        """True when this word ends a sentence: by its full stop, or by the script (script_stops)."""
        return ends_sentence(self.words[index]) or index in self.stops

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
        if self.says_more(indices, keeps):
            # Whatever found this removal, it goes only when the take that stays says what it says.
            asked = ('possible-restart', 'take-says-more')
            start, end = self.words[indices[0]].start, self.words[indices[-1]].end
            if not any(item['code'] in asked and item['start'] < end and item['end'] > start for item in self.flags):
                self.flag('take-says-more', indices[0], indices[-1],
                          f'This would have been removed as {REASONS[reason]}, but the take kept at '
                          f'{self.words[keeps].start:.2f} does not say all of it. Left in; cut it by hand if it is '
                          'a failed try.')
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

    def says_more(self, indices, keeps):
        """True when these words say a script word that the kept take that starts at `keeps` does not: a take that
        says another part of the line, or more of it than a kept take that picks the line up half way."""
        if not self.script:
            return False
        take = []
        for index in range(keeps, len(self.words)):
            if not self.keep[index]:
                break
            take.append(index)
            if self.ends(index):
                break
        if not take:
            return False
        # Asked for the take's own script line, and for the line these words fit best when that is another one.
        lines = {line for score, line in (self.best_line(take), self.best_line(indices))
                 if score >= SETTINGS['scriptMatch']}
        return any(not self.says(take, indices, line) for line in lines)

    # 0. Sentence ends the transcript left out.

    def script_stops(self):
        """With a script, a pause where one script line ends and another begins ends a sentence, also when the
        transcript left the full stop off there: "The tool is fast | The tool is fast and easy to use." is two
        lines of the script, not a start and its restart."""
        if not self.script:
            return
        tails = {tuple(line[-2:]) for line in self.script if len(line) >= 2}
        heads = {tuple(line[:2]) for line in self.script if len(line) >= 2}
        for index in range(1, len(self.words)):
            last = index + 1 == len(self.words)
            if ends_sentence(self.words[index]) or tuple(self.norms[index - 1:index + 1]) not in tails:
                continue
            if last or (self.gap_ms(index, index + 1) >= SETTINGS['restartPauseMs']
                        and tuple(self.norms[index + 1:index + 3]) in heads):
                self.stops.add(index)

    # 1. Quiet "again" retake cues.

    def find_attempt(self, cue, lower):
        """Return where the failed attempt before a cue starts, and whether the restart repeats its opening."""
        first = cue - 1
        limit = SETTINGS['attemptMaxSeconds']
        while first - 1 >= lower and not self.ends(first - 1) \
                and self.gap_ms(first - 1, first) < SETTINGS['attemptBreakMs'] \
                and self.words[cue].start - self.words[first - 1].start <= limit:
            first -= 1
        restart = cue + 1
        key = self.norms[restart:restart + 1]
        if restart + 1 < len(self.words) and not self.ends(restart) \
                and self.gap_ms(restart, restart + 1) < SETTINGS['restartPauseMs']:
            key = self.norms[restart:restart + 2]
        for start in range(first, cue - len(key) + 1):
            if self.norms[start:start + len(key)] == key:
                return start, True
        return first, False

    def script_says(self, first, end):
        """True when the script has these words in a row ("So, again, you see" is speech, not a retake cue)."""
        names, words = self.norms[max(first, 0):end], self.script_words
        return bool(names) and any(words[at:at + len(names)] == names for at in range(len(words) - len(names) + 1))

    def possible_cue(self, index):
        self.flag('possible-retake-cue', index, index,
                  f'"{self.words[index].text}" after a pause may be a retake cue. Left in; listen.')

    def retake_cues(self):
        lower = 0
        for index, name in enumerate(self.norms[:-1]):
            if name != 'again' or not self.keep[index] or self.script_says(index - 1, index + 2):
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
            if previous is None or previous != index - 1 or self.ends(previous) \
                    or self.gap_ms(previous, index) >= SETTINGS['restartPauseMs']:
                groups.append([index])
            else:
                groups[-1].append(index)
        return groups

    def sentences(self):
        groups = []
        for index in (index for index, kept in enumerate(self.keep) if kept):
            previous = groups[-1][-1] if groups else None
            if previous is None or previous != index - 1 or self.ends(previous):
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
            if self.ends(first[-1]):
                pairs.append((first[0], second[0]))
            elif not self.mostly_repeated(first, second):
                continue
            elif TRAILING_MARK.search(self.words[first[-1]].text) and not self.repeats_whole(first, second):
                # "The tool is fast, the tool is cheap." repeats on purpose; a dropped start usually just stops.
                self.flag('possible-restart', first[0], second[size - 1],
                          f'Possible restart: "{self.text(first)}" then "{self.text(second[:size])}". Left in; '
                          'listen and cut by hand if it is one.')
            elif len(first) < SETTINGS['shortStartWords']:
                self.ask_restart(first, second[size - 1])
            elif why := self.script_keeps(first, second):
                # Often the transcript only missed a full stop: the script wants both, so Lex decides.
                self.flag('possible-restart', first[0], second[size - 1],
                          f'Possible restart: "{self.text(first)}" then "{self.text(second[:size])}". '
                          f'{why}, so left in; listen and cut by hand if it is one.')
            else:
                self.drop(first, 'abandoned-start', second[0])
        # A stray word or two between a sentence and its redo ("So," or "and then") still makes a pair.
        for first, between, second in zip(phrases, phrases[1:], phrases[2:]):
            if self.ends(first[-1]) and len(between) <= 2 and self.opens_alike(first, second):
                pairs.append((first[0], second[0]))
        return pairs

    def ask_restart(self, first, through):
        self.flag('possible-restart', first[0], through,
                  f'Possible restart: "{self.text(range(first[0], through + 1))}". A short start, so left in; listen '
                  'and cut by hand if it is one.')

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

    def near_copy(self, first, second):
        """True when the earlier sentence is a shorter or near copy of the later one."""
        names = [self.norms[index] for index in first]
        return self.stops_short(first, second) \
            or dice(names, [self.norms[index] for index in second]) >= SETTINGS['nearTakeMatch']

    def opens_alike(self, first, second):
        return len(first) >= 2 and len(second) >= 2 \
            and all(loose_equal(self.norms[a], self.norms[b]) for a, b in zip(first[:2], second[:2]))

    def said_twice(self, sentence):
        """True when the script itself says this sentence's line more than once: two readings of it are never takes
        of one line."""
        if not self.script:
            return False
        score, line = self.best_line(sentence)
        return score >= SETTINGS['scriptMatch'] and tuple(self.script[line]) in self.repeated

    def part_of_line(self, sentence, line):
        """The places in a script line that a sentence says, found by lining the two up."""
        names = [self.norms[index] for index in sentence]
        blocks = SequenceMatcher(None, names, self.script[line], autojunk=False).get_matching_blocks()
        return {block.b + offset for block in blocks for offset in range(block.size)}

    def says(self, take, other, line):
        """True when a take says every word of a script line that another sentence says: only then does the take
        make the other one a failed try. A take that says another part of the line, picks the line up half way or
        leaves out a script word the other one has ("easy to use" before "easy to carry", when the script says
        "easy to use and carry") does not."""
        return self.part_of_line(other, line) <= self.part_of_line(take, line)

    def same_part(self, first, second, line):
        """True when two sentences say the same part of a script line, as two takes of it do. The script may join
        two sentences into one line ("The camera is easy to use and the battery lasts all day."): said as two
        sentences, both match that line, but they are two parts of it and both stay."""
        part, other = self.part_of_line(first, line), self.part_of_line(second, line)
        return bool(part and other) and len(part & other) >= SETTINGS['samePartOfLine'] * min(len(part), len(other))

    def script_keeps(self, first, second):
        """Why the script wants the earlier words kept although the later ones open the same way, or None: the
        script says their line more than once, or it has them as a line or a part of a line of their own."""
        if self.said_twice(first) or self.said_twice(second):
            return 'The script says this line more than once'
        if not self.script or not self.other_line(first, second):
            return None
        # A start the later words repeat in full is a dropped start, unless it says a whole line of its own
        # ("The tool is fast" before "The tool is fast and easy to use.").
        whole = self.coverage(first, self.best_line(first)[1]) >= SETTINGS['completeCoverage']
        return 'The script has both' if whole or not self.repeats_whole(first, second) else None

    def same_line(self, first, second):
        """True when two sentences say the same line: the same part of the same script sentence, or a near copy
        when neither sentence is in the script (or there is no script)."""
        if self.script:
            (score, line), (other_score, other_line) = self.best_line(first), self.best_line(second)
            if max(score, other_score) >= SETTINGS['scriptMatch']:
                return line == other_line and min(score, other_score) >= SETTINGS['scriptMatch'] \
                    and tuple(self.script[line]) not in self.repeated and self.same_part(first, second, line)
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
        """(score, number) of the script line closest to a sentence. The score counts shared words in any order;
        among lines that score the same, the one with most of the words in the same order wins ("The camera beats
        the phone." is not "The phone beats the camera."), then the later one."""
        names = tuple(self.norms[index] for index in sentence)
        if names not in self.closest:
            scores = [(dice(names, line), number) for number, line in enumerate(self.script)]
            score = max(scores, default=(0.0, None))[0]
            tied = [number for value, number in scores if value == score]
            self.closest[names] = max(scores, default=(0.0, None)) if len(tied) < 2 or not score else \
                (score, max(tied, key=lambda number: (in_order(names, self.script[number]), number)))
        return self.closest[names]

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
            if earlier not in owner or later not in owner:
                continue
            first, second = sentences[owner[earlier]], sentences[owner[later]]
            # Only the script makes two sentences one line however far apart: a near copy has a minute.
            near = self.words[second[0]].start - self.words[first[-1]].end <= SETTINGS['nearTakeSeconds']
            scripted = bool(self.script) and self.best_line(first)[0] >= SETTINGS['scriptMatch']
            if (near or scripted) and self.same_line(first, second):
                join(owner[earlier], owner[later])
        lines = {}
        if self.script:
            for number, sentence in enumerate(sentences):
                score, line = self.best_line(sentence)
                # A short sentence, or one the script itself says twice, is not grouped with others; nor is one
                # that says another part of the line than the others do.
                if score >= SETTINGS['scriptMatch'] and len(sentence) >= 3 \
                        and tuple(self.script[line]) not in self.repeated:
                    for other, value in lines.items():
                        if value == line and self.same_part(sentences[other], sentence, line):
                            join(other, number)
                    lines[number] = line
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
        if not self.ends(sentence[-1]):
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
        # With none complete, takes more than a minute apart are not trusted as one line: each near group is
        # resolved on its own and a take far from the others stays.
        clusters = [[takes[0]]]
        for earlier, later in zip(takes, takes[1:]):
            if self.words[later[0]].start - self.words[earlier[-1]].end > SETTINGS['nearTakeSeconds']:
                clusters.append([])
            clusters[-1].append(later)
        if not complete and len(clusters) > 1:
            far = [cluster[0] for cluster in clusters if len(cluster) == 1]
            times = ', '.join(f'{self.words[take[0]].start:.2f}' for take in far)
            if far:
                self.flag('no-complete-take', far[0][0], far[0][-1],
                          f'{"The take at" if len(far) == 1 else "The takes at"} {times}: more than '
                          f'{SETTINGS["nearTakeSeconds"]} s from the other takes of this line, and none is complete. '
                          'Left in; cut one by hand if it is a retake.')
            for cluster in clusters:
                if len(cluster) > 1:
                    self.resolve(cluster, line)
            return
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
        if start and chosen == 0 and len(start) < SETTINGS['shortStartWords']:
            # A short start with no failed try before it is only listed, here as everywhere ("And then I—").
            if not any(item['code'] == 'possible-restart' and item['start'] <= self.words[start[-1]].end
                       and item['end'] >= self.words[start[0]].start for item in self.flags):
                self.ask_restart(start, rest[min(1, len(rest) - 1)])
        elif start and self.drop(start, 'abandoned-start', rest[0]):
            keeps = rest[0]
        # Takes are grouped pair by pair, so one that overlaps two parts of a script line brings both into its
        # group: drop() leaves in a take that says more than the take that stays.
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
            if len(rest) < 3 or not self.complete(rest, line):
                continue
            # With a script the rest already says the whole line, so a start broken off before the pause adds nothing.
            broken = line is not None and BROKEN_OFF.search(self.words[take[cut - 1]].text)
            if broken or any(self.opens_alike(rest, other) for other in others):
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
            if self.ends(index):
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

    def find_opening(self, indices, opening):
        """Where these words hold the take's first two words, or None."""
        indices = [index for index in indices if self.keep[index]]
        return next((at for at in range(len(indices) - 1) if all(
            loose_equal(self.norms[a], b) for a, b in zip(indices[at:at + 2], opening))), None)

    def carried_from(self, first):
        """The kept words this word carries on from: back over short breaths, to a sentence end, a removal or a
        long pause."""
        words = []
        while first > 0 and self.keep[first - 1] and not self.ends(first - 1) \
                and self.gap_ms(first - 1, first) < SETTINGS['attemptBreakMs']:
            first -= 1
            words.insert(0, first)
        return words

    def goes_on(self, first):
        """True when this word may carry on the speech before it after a short breath: no sentence end, broken-off
        word, comma or removal before it, and a pause under the attempt break."""
        before = first - 1
        return before >= 0 and self.keep[before] and not TRAILING_MARK.search(self.words[before].text) \
            and self.gap_ms(before, first) < SETTINGS['attemptBreakMs']

    def other_line(self, first, second):
        """True when the script has the first sentence as a line of its own, not the line of the second, or as
        another part of the second's line."""
        (score, line), (_, other) = self.best_line(first), self.best_line(second)
        if score < SETTINGS['scriptMatch']:
            return False
        if line == other:
            return not self.same_part(first, second, line)
        # A sentence that fits the second's line just as well ("The tool is quick" before "The tool is fast.") is
        # no line of its own.
        return score > dice([self.norms[index] for index in first], self.script[other])

    def restarted_lines(self):
        """Remove the attempts before a complete take that open with the take's first two words, even when the
        transcript ran them into other words; stop at a sentence that says something else."""
        pieces, asks = self.phrases(), []
        for number in range(len(pieces) - 1, 0, -1):
            take = self.take_at(pieces[number][0]) if self.keep[pieces[number][0]] else None
            if not take or len(take) < 3:
                continue
            opening = self.norms[take[0]:take[0] + 2]
            if self.find_opening(self.carried_from(take[0]), opening) is None and self.goes_on(take[0]):
                continue  # A breath inside a take ("I am | working on"), not a new take.
            if self.script:
                score, line = self.best_line(take)
                if score >= SETTINGS['scriptMatch'] and tuple(self.script[line]) in self.repeated:
                    continue  # The script says this line twice on purpose.
            start, passed, unsure = None, [], False
            for piece in reversed(pieces[:number]):
                kept = [index for index in piece if self.keep[index]]
                if not kept:
                    continue
                if self.words[take[0]].start - self.words[kept[0]].start > SETTINGS['attemptMaxSeconds']:
                    break
                at = self.find_opening(kept, opening)
                finished = self.ends(kept[-1])
                if at is None:
                    if finished and len(kept) >= 3:
                        break
                    passed.append(kept)
                    continue
                if finished and at > 1 and self.script and self.best_line(kept)[0] >= SETTINGS['scriptMatch'] \
                        and self.complete(kept, self.best_line(kept)[1]):
                    break  # A whole script line that ends with the take's opening ("... the phone. The phone ...").
                # A finished sentence before the take that opens the same way is a failed try, unless the script
                # has it as a different line. Without a script, one that only repeats the opening is asked about.
                if finished and not self.same_line(kept[at:], take):
                    if self.script:
                        # Outside the script it must be a near copy of the take, or it may be an ad-lib.
                        if self.other_line(kept[at:], take) or not self.near_copy(kept[at:], take):
                            break
                    elif self.stops_short(kept[at:], take):
                        unsure = True
                    else:
                        break
                # One word before the opening ("So next, the ...") belongs to the attempt.
                start = kept[0] if at <= 1 else kept[at]
            if start is None:
                continue
            attempt = [index for index in range(start, take[0]) if self.keep[index]]
            if len(attempt) < SETTINGS['shortStartWords'] and all(self.keep[max(start - 1, 0):take[0]]):
                # A short stutter with nothing removed around it: list it, like the paper edit's inspect items.
                if not any(item['code'] == 'possible-restart' and item['start'] <= self.words[attempt[-1]].end
                           and item['end'] >= self.words[attempt[0]].start for item in self.flags):
                    self.ask_restart(attempt, take[min(1, len(take) - 1)])
                continue
            if TRAILING_MARK.search(self.words[attempt[-1]].text) \
                    and self.gap_ms(attempt[-1], take[0]) < SETTINGS['attemptBreakMs'] \
                    and not self.script_says_once(attempt, take):
                continue  # "The tool is fast, the tool is cheap." may repeat on purpose: restarts() flagged it.
            if self.script:
                # Everything removed must belong to the take's own script line, not to the talk around it (and
                # drop() leaves in what the take does not say).
                if self.best_line(attempt)[1] == self.best_line(take)[1]:
                    self.drop(attempt, 'abandoned-start', take[0])
            elif unsure or any(len(other) >= 3 and other[0] > start for other in passed):
                # Without a script, more than a stray word between the attempts may be real talk: ask, do not cut.
                asks.append((attempt, take[0]))
            else:
                self.drop(attempt, 'abandoned-start', take[0])
        # A restart flagged earlier, or a take left in, whose words are now removed is no longer a question for Lex.
        removed = [self.words[index].start for index, kept in enumerate(self.keep) if not kept]
        asked = ('possible-restart', 'possible-retake-cue', 'take-says-more')
        self.flags = [item for item in self.flags if item['code'] not in asked
                      or not any(item['start'] - TOLERANCE <= at <= item['end'] for at in removed)]
        for attempt, take in asks:
            if not all(self.keep[index] for index in attempt):
                continue  # A later step removed part of it already.
            self.flag('possible-restart', attempt[0], attempt[-1],
                      f'Possible unfinished start before the take at {self.words[take].start:.2f}: '
                      f'"{self.text(attempt)}". Left in; listen and cut by hand if it is one.')

    def put_back(self, indices):
        """Words removed earlier stay after all; what is left of each removal around them is still removed."""
        for index in indices:
            self.keep[index] = True
        left = []
        for item in self.removed:
            run = []
            for index in range(item['first'], item['last'] + 2):
                if index <= item['last'] and not self.keep[index]:
                    run.append(index)
                elif run:
                    left.append({**item, 'first': run[0], 'last': run[-1]})
                    run = []
        self.removed = left

    def keep_script_lines(self):
        """The last check on the removals, whatever made them. A script line that is said word for word in the
        recording is still said in what stays, as often as the script says it: by the same words, or by a kept
        sentence that is a complete take of the line. When it is not, the last word-for-word reading goes back in
        and Lex is told. Longer lines are counted first and their words are not counted again for a shorter line
        ("The tool is fast." inside "The tool is fast and easy to use.")."""
        if not self.script:
            return
        spoken = [index for index, name in enumerate(self.norms) if name and name not in FILLERS]
        names = [self.norms[index] for index in spoken]
        starts = {}
        for at, name in enumerate(names):
            starts.setdefault(name, []).append(at)
        # A kept sentence counts as a take of its line when it says the line in order through to its last word.
        takes = {}
        for sentence in self.sentences():
            score, line = self.best_line(sentence)
            said = self.part_of_line(sentence, line) if score >= SETTINGS['scriptMatch'] else ()
            if len(said) >= SETTINGS['completeCoverage'] * len(self.script[line]) and len(self.script[line]) - 1 in said:
                takes.setdefault(tuple(self.script[line]), []).append(sentence)
        used = set()
        counts = Counter(map(tuple, self.script))
        for line in sorted(counts, key=len, reverse=True):
            found, free = [], 0
            for at in starts.get(line[0], ()):
                if at < free or tuple(names[at:at + len(line)]) != line:
                    continue
                # A line of one or two words is only itself as a sentence of its own ("Thanks again."), not as
                # the same words inside another sentence.
                own = (at == 0 or self.ends(spoken[at - 1])) and self.ends(spoken[at + len(line) - 1])
                if len(line) >= 3 or own:
                    found.append(spoken[at:at + len(line)])
                    free = at + len(line)
            found = [reading for reading in found if not used.intersection(reading)]
            used.update(index for reading in found for index in reading)
            kept = [reading for reading in found if all(self.keep[index] for index in reading)]
            gone = [reading for reading in found if reading not in kept]
            # A kept take that is not one of the word-for-word readings counts too, unless its words are counted
            # for a longer line already.
            others = [take for take in takes.get(line, ())
                      if not used.intersection(take) and not any(set(take) & set(reading) for reading in kept)]
            missing = min(counts[line], len(found)) - len(kept) - len(others)
            for reading in gone[len(gone) - missing:] if missing > 0 else ():
                reason = next((item['reason'] for item in self.removed
                               if item['first'] <= reading[0] <= item['last']), 'earlier-take')
                self.put_back(range(reading[0], reading[-1] + 1))
                self.flag('script-line-kept', reading[0], reading[-1],
                          f'This would have been removed as {REASONS[reason]}, but the script has this line and '
                          'nothing else that stays says it. Left in; cut it by hand if it is a failed try.')

    def possible_restarts(self):
        """Flag a two-word run repeated within a few words of a kept sentence: often a restart without a pause."""
        flagged = [(item['start'], item['end']) for item in self.flags
                   if item['code'] in ('possible-restart', 'demo-protected', 'take-says-more', 'script-line-kept')]
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

    def main_words_in_script(self, sentence):
        """True when most of the sentence's main words (four letters or more) are in its closest script line: a short
        take of a long line is the script, not an ad-lib."""
        main = [self.norms[index] for index in sentence if len(self.norms[index]) >= 4]
        line = self.best_line(sentence)[1]
        return bool(main) and overlap(main, self.script[line]) > SETTINGS['adLibMainWords'] * len(main)

    def take_follows(self, sentence, later):
        """True when a take of the same line follows soon: this one is a failed try, not an ad-lib."""
        end = self.words[sentence[-1]].end
        return any(self.opens_alike(sentence, other) and self.near_copy(sentence, other)
                   for other in later if self.words[other[0]].start - end <= SETTINGS['attemptMaxSeconds'])

    def ad_libs(self):
        if not self.script:
            return []
        chosen = []
        sentences = self.sentences()
        for number, sentence in enumerate(sentences):
            # Not in the script, and no take of the same line follows it (then it is a failed try).
            if len(sentence) >= 3 and self.best_line(sentence)[0] < SETTINGS['scriptMatch'] \
                    and not self.main_words_in_script(sentence) \
                    and not self.take_follows(sentence, sentences[number + 1:]):
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

    def clean_frame(self, at, direction, limit):
        """The roomiest frame on one side of a word time that is inside neither that word nor the sound at the limit,
        or None: two words that touch between two frames leave no frame to cut at."""
        fps = self.fps
        frame = math.floor((limit + TOLERANCE) * fps) if direction > 0 else math.ceil((limit - TOLERANCE) * fps)
        frame = max(frame, 0)
        return frame if (frame / fps - at) * direction >= -TOLERANCE else None

    def fallback(self, at, direction, limit, word_index, side):
        """No frame leaves the preferred room: take the roomiest one that stays off both words, and flag it. None
        when there is no such frame (clean_frame)."""
        fps = self.fps
        frame = self.clean_frame(at, direction, limit)
        if frame is None:
            return None
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
        wide = max(high, SETTINGS['newThoughtRoomMaxMs']) if kind == 'new-thought' else high
        gap = lambda pair: room(pair[0], self.fps, end, 1) + room(pair[1], self.fps, start, -1)  # noqa: E731
        closest = None
        for stretch in (0, math.ceil(1000 / self.fps)):
            outs = self.frames(end, low, wide + stretch, 1, out_limit)
            ins = self.frames(start, low, wide + stretch, -1, in_limit)
            pairs = [(out, entry) for out in outs for entry in ins if entry > out]
            fitting = [pair for pair in pairs if low_gap <= gap(pair) <= high_gap]
            if fitting:
                best = min(fitting, key=lambda pair: abs(gap(pair) - target))
                over = max(room(best[0], self.fps, end, 1), room(best[1], self.fps, start, -1))
                note = (f'{over} ms of room on one side, over {wide} ms, to keep the silence in range'
                        if over > wide else None)
                return best + (kind, note)
            if pairs and closest is None:
                closest = min(pairs, key=lambda pair: abs(gap(pair) - target))
        if closest:
            return closest + (kind, 'silence outside the range')
        outs = self.frames(end, low, wide, 1, out_limit)
        ins = self.frames(start, low, wide, -1, in_limit)
        out = outs[0] if outs else self.fallback(end, 1, out_limit, before, 'after')
        entry = ins[-1] if ins else self.fallback(start, -1, in_limit, after, 'before')
        return out, entry, kind, None

    def restore(self, before, after, touching=False):
        """A removal too short to cut between two frames goes back in, and Lex is told. So does one that touches
        the kept word beside it between two frames (touching): every frame there is inside one of the two words."""
        for index in range(before + 1, after):
            self.keep[index] = True
        self.removed = [item for item in self.removed if not before < item['first'] < after]
        if touching:
            self.flag('no-clean-frame', before + 1, after - 1,
                      'This would have been removed, but it touches the word kept beside it between two frames, so '
                      'no frame cuts cleanly there. Left in; cut it by hand.')
            return
        self.flag('too-short-to-cut', before + 1, after - 1,
                  'This would have been removed, but it is too short to cut cleanly at whole frames. Left in; listen.')

    def segments(self):
        kept = [index for index, value in enumerate(self.keep) if value]
        if not kept:
            raise ValueError('Every word was removed. Check the transcript and the retake marks.')
        first, last = kept[0], kept[-1]
        # A removed opening or ending that touches the first or last kept word between two frames goes back in.
        if first and self.clean_frame(self.words[first].start, -1, self.words[first - 1].end) is None:
            self.restore(-1, first, touching=True)
            return self.segments()
        if last + 1 < len(self.words) \
                and self.clean_frame(self.words[last].end, 1, self.words[last + 1].start) is None:
            self.restore(last, len(self.words), touching=True)
            return self.segments()
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
            if self.clean_frame(self.words[before].end, 1, out_limit) is None \
                    or self.clean_frame(self.words[after].start, -1, in_limit) is None:
                # Only a removal can touch a kept word: between two kept words there is a silence to cut in.
                if removed:
                    # A removal of under two frames is too short to cut anyway: say that instead.
                    self.restore(before, after, self.words[after].start - self.words[before].end >= 2 / self.fps)
                continue
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
        return self.keep_demos(runs, joins, kept)

    def demo_frames(self, demo):
        """The whole frames that hold a marked demo. An edge frame that would land inside a word moves one frame
        into the demo (under a frame of picture), so no cut lands inside a word; a demo shorter than that keeps
        its outer frames."""
        fps = self.fps
        start, end = max(math.floor(demo[0] * fps + 1e-6), 0), math.ceil(demo[1] * fps - 1e-6)

        def in_word(frame):
            return any(word.start + TOLERANCE < frame / fps < word.end - TOLERANCE for word in self.words)

        inner = start + in_word(start), end - in_word(end)
        return inner if inner[0] < inner[1] else (start, end)

    def removed_between(self, start, end):
        """True when a removed word lies between these two frames."""
        return any(not kept and word.start < end / self.fps - TOLERANCE and word.end > start / self.fps + TOLERANCE
                   for word, kept in zip(self.words, self.keep))

    def demo_edge(self, frame, direction, demo):
        """Tell Lex when a demo piece starts or ends closer to a removed word than the room kept at other cuts:
        the demo stays whole, so the cut cannot move away from the word."""
        at, (low, high) = frame / self.fps, SETTINGS['roomMs']
        for index, word in enumerate(self.words):
            beside = word.start < at if direction < 0 else word.end > at
            gap = round((at - word.end if direction < 0 else word.start - at) * 1000)
            if not self.keep[index] and beside and gap < low:
                self.flag('tight-edge', index, index,
                          f'Only {gap} ms between the removed "{word.text}" and the marked screen demo '
                          f'({demo[0]:.2f}-{demo[1]:.2f}) (preferred {low}-{high} ms): the demo stays whole, so the '
                          'cut sits that close. Listen.')

    def keep_demos(self, runs, joins, kept):
        """Put back the frames of a marked demo that a removal, the opening or the ending left out: a demo with no
        speech in it is still picture Lex marked. The silence between the demo and the speech kept beside it stays
        too, as it does where nothing is removed; a removed take beside the demo is still cut away, so a demo
        between two removals becomes a piece of its own. Returns the runs and joins with every demo in."""
        # Each piece knows its first and last kept word; a demo piece has none.
        pieces = [{'in': start, 'out': end, 'first': joins[number - 1]['after'] if number else kept[0],
                   'last': joins[number]['before'] if number < len(joins) else kept[-1]}
                  for number, (start, end) in enumerate(runs)]
        for demo in sorted(self.demos):
            start, end = self.demo_frames(demo)
            for run_in, run_out in runs + [(math.inf, math.inf)]:
                if start < min(run_in, end):
                    low, high = start, min(run_in, end)
                    # What ends before this part: a run, or a demo piece put back earlier.
                    before = max((piece['out'] for piece in pieces if piece['out'] <= low), default=None)
                    if before is not None and not self.removed_between(before, low):
                        low = before
                    else:
                        self.demo_edge(low, -1, demo)
                    if run_in != math.inf and not self.removed_between(high, run_in):
                        high = run_in
                    else:
                        self.demo_edge(high, 1, demo)
                    pieces.append({'in': low, 'out': high, 'first': None, 'last': None})
                    if run_in == math.inf and demo[1] > self.words[-1].end:
                        # The cut now ends later than the speech does, and the transcript cannot say what is there.
                        self.flags.append({
                            'code': 'demo-past-last-word', 'start': round(max(demo[0], self.words[-1].end), 3),
                            'end': round(demo[1], 3), 'text': '',
                            'message': f'The marked screen demo ({demo[0]:.2f}-{demo[1]:.2f}) runs past the last '
                                       f'word of the transcript. Kept to {demo[1]:.2f}; check the recording is '
                                       'that long.'})
                start = max(start, run_out)
        if len(pieces) == len(runs):
            return runs, joins
        merged = []
        for piece in sorted(pieces, key=lambda piece: piece['in']):
            if merged and piece['in'] <= merged[-1]['out']:
                if piece['out'] > merged[-1]['out']:
                    merged[-1].update(out=piece['out'], last=piece['last'])
            else:
                merged.append(piece)
        self.demo_opens, self.demo_ends = merged[0]['first'] is None, merged[-1]['last'] is None
        by_word = {join['before']: join for join in joins}
        # A join with speech on both sides is one the cutter made; beside a demo piece there is no word to measure.
        joins = [by_word[before['last']] if before['last'] is not None and after['first'] is not None
                 else {'before': before['last'], 'after': after['first'], 'out': before['out'], 'in': after['in'],
                       'kind': None, 'note': None, 'why': 'demo'}
                 for before, after in zip(merged, merged[1:])]
        return [(piece['in'], piece['out']) for piece in merged], joins

    def plan(self):
        self.script_stops()
        self.retake_cues()
        self.takes(self.restarts())
        self.restarted_lines()
        self.keep_script_lines()
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
    # Removals side by side that keep the same take are one passage: the whole run of failed tries before it.
    passages = []
    for item in sorted(cutter.removed, key=lambda item: item['first']):
        keeps = next((index for index in range(item['keeps'], len(words)) if keep[index]), None)
        previous = passages[-1] if passages else None
        if previous and previous['keeps'] == keeps and not any(keep[previous['last'] + 1:item['first']]) \
                and not cutter.demo_at(previous['last'], item['first']):
            previous['last'] = item['last']
            previous['reasons'].add(item['reason'])
        else:
            passages.append({'first': item['first'], 'last': item['last'], 'reasons': {item['reason']},
                             'keeps': keeps})
    removed = []
    for item in passages:
        first, last = words[item['first']], words[item['last']]
        reason = next(iter(item['reasons'])) if len(item['reasons']) == 1 else 'retake-run'
        gaps = [words[index + 1].start - words[index].end for index in range(item['first'], item['last'])]
        # The cut also takes the silence between the passage and the words on either side of it.
        beside = [words[index + 1].start - words[index].end for index in (item['first'] - 1, item['last'])
                  if 0 <= index < len(words) - 1]
        removed.append({'start': round(first.start, 3), 'end': round(last.end, 3),
                        'seconds': round(last.end - first.start, 2), 'reason': reason, 'why': REASONS[reason],
                        'text': cutter.text(range(item['first'], item['last'] + 1)),
                        'keeps': round(words[item['keeps']].start, 3) if item['keeps'] is not None else None,
                        'longestSilence': round(max(gaps, default=0.0), 2),
                        'silenceBeside': round(max(beside, default=0.0), 2)})
    join_entries = []
    for number, join in enumerate(joins, 1):
        # Beside a demo piece there is no word on that side, so no room to measure.
        before = words[join['before']] if join['before'] is not None else None
        after = words[join['after']] if join['after'] is not None else None
        tail = room(join['out'], fps, before.end, 1) if before else None
        head = room(join['in'], fps, after.start, -1) if after else None
        join_entries.append({
            'index': number, 'outgoing': segments[number - 1]['id'], 'incoming': segments[number]['id'],
            'sourceOutFrame': join['out'], 'sourceInFrame': join['in'],
            'sourceOutSeconds': round(join['out'] / fps, 3), 'sourceInSeconds': round(join['in'] / fps, 3),
            'why': join['why'], 'kind': join['kind'], 'lastWord': before.text if before else None,
            'firstWord': after.text if after else None, 'tailMs': tail, 'headMs': head,
            'gapMs': tail + head if before and after else None, 'fadeMs': SETTINGS['audioFadeMs'],
            'note': join['note']})
    kept_words = [index for index, value in enumerate(keep) if value]
    source_seconds = sum(source_out - source_in for source_in, source_out in runs) / fps
    # Counted on the cut's own clock, so a demo kept before the first word or after the last one is in it.
    first_word, last_word = words[kept_words[0]], words[kept_words[-1]]
    opens = next(item for item in segments if item['sourceOutFrame'] / fps > first_word.start)
    ends = next(item for item in reversed(segments) if item['sourceInFrame'] / fps < last_word.end)
    opening = {'leadInMs': round((first_word.start - (opens['sourceInFrame'] - opens['timelineStartFrame']) / fps)
                                 * 1000)}
    ending = {'tailMs': round(((cutlist['durationFrames'] - ends['timelineStartFrame'] + ends['sourceInFrame']) / fps
                               - last_word.end) * 1000)}
    if cutter.demo_opens:
        opening['demo'] = True
    if cutter.demo_ends:
        ending['demo'] = True
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
        'opening': opening,
        'ending': ending,
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
        f'Removed {summary["removedPassages"]} passages ({summary["removedSeconds"]} s). '
        f'{summary["flags"]} things for Lex to decide. '
        f'{summary["adLibs"]} ad-libs kept. All times are seconds in the original recording.', '',
        '## Removed', '',
    ]
    for item in paper['removed']:
        keeps = f'; the take at {seconds(item["keeps"])} stays' if item['keeps'] is not None else ''
        silence = f', includes a {item["longestSilence"]:.1f} s silence' \
            if item['longestSilence'] * 1000 >= limits['longSilenceMs'] else ''
        if item['silenceBeside'] * 1000 >= limits['longSilenceMs']:
            silence += f', takes out the {item["silenceBeside"]:.1f} s silence beside it'
        lines.append(f'- **{seconds(item["start"])}-{seconds(item["end"])}** ({item["seconds"]} s{silence}), '
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
    demo = ' with the marked screen demo'
    lines += [
        '', '## Timing used', '',
        f'- Room before and after speech at every cut: {limits["roomMs"][0]}-{limits["roomMs"][1]} ms '
        f'(opening {paper["opening"]["leadInMs"]} ms{demo if paper["opening"].get("demo") else ""}, '
        f'ending {paper["ending"]["tailMs"]} ms{demo if paper["ending"].get("demo") else ""}); before a new thought '
        f'up to {limits["newThoughtRoomMaxMs"]} ms on a side, so the silence can reach its range.',
        f'- Whole silence at a join: about {limits["linkedTargetMs"]} ms between linked phrases (range '
        f'{limits["linkedGapMs"][0]}-{limits["linkedGapMs"][1]} ms), about {limits["newThoughtTargetMs"]} ms before a '
        f'new thought (range {limits["newThoughtGapMs"][0]}-{limits["newThoughtGapMs"][1]} ms). '
        'A word ending in . ! or ? counts as the end of a thought.',
        f'- A pause up to {limits["breathMaxMs"]} ms inside a linked phrase stays (a breath); a longer one, or a pause '
        f'over {limits["newThoughtGapMs"][1]} ms before a new thought, is shortened. A silence of '
        f'{limits["longSilenceMs"] // 1000} s or more stays and is listed above.',
        f'- {limits["audioFadeMs"]} ms audio fade at every cut.',
        f'- A quiet "again" counts as a retake cue when a pause of {limits["cuePauseMs"]} ms or more sits on both '
        'sides, or on one side when the restart repeats the attempt\'s opening words; not when the script says the '
        'same words ("So, again, you see").',
        f'- A dropped start of fewer than {limits["shortStartWords"]} words ("And then I—") is listed, not cut.',
        f'- When no take of a line is complete, a take more than {limits["nearTakeSeconds"]} s from the others stays '
        'and is listed.',
        f'- An ad-lib: no more than {round(limits["adLibMainWords"] * 100)}% of its main words are in its closest '
        'script line, and no take of the same line follows it.',
        f'- A later take counts as clearly worse when it has {limits["worseStumbles"]} or more extra stumbles, '
        f'leaves out over {round(limits["worseCoverageDrop"] * 100)}% more of the script line, or (without a script) '
        f'keeps under {round(limits["worseKeptPoint"] * 100)}% of the earlier take\'s main words.',
        '- Word times are approximate. A clean paper edit is not a listened cut: listen to every join at normal speed.',
        '', '## Joins', '',
    ]
    for join in paper['joins']:
        note = f' ({join["note"]})' if join['note'] else ''
        where = (f'- Join {join["index"]} ({join["outgoing"]} to {join["incoming"]}), {JOIN_WHY[join["why"]]}, '
                 f'{seconds(join["sourceOutSeconds"])} to {seconds(join["sourceInSeconds"])}: ')
        if join['why'] == 'demo':
            # A demo piece has no speech: say which side it is on and the room left beside the word, if any.
            last = f'"{join["lastWord"]}" + {join["tailMs"]} ms' if join['lastWord'] else 'the demo (no speech)'
            first = f'{join["headMs"]} ms + "{join["firstWord"]}"' if join['firstWord'] else 'the demo (no speech)'
            lines.append(f'{where}{last} | {first}, {join["fadeMs"]} ms fade{note}.')
            continue
        lines.append(f'{where}"{join["lastWord"]}" | "{join["firstWord"]}", {join["kind"]} gap {join["gapMs"]} ms '
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
