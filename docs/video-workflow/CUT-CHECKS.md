# Take cutter, cut checks and captions

The take cutter reads a word-timed transcript (and, if given, the script) and proposes a cut. The join check and
the caption builder read a word-timed transcript and a cut list. None of them touches media or calls a paid
service, and none overwrites: point `--out-dir` at a new folder each time.

Use the raw HappyScribe export as the transcript when you have it. Word times must be on the original
recording's clock. `[text, start, end]` arrays are read as original-recording seconds, so only use them from a
raw export. A review transcript with `original.words` or `sourceStartMs` also works; word objects with only
`startMs` and `endMs` are refused, because they may be on the edited clock. When a transcript's `sourceId` is a
SHA-256 value, it must match the cut list's `sourceSha256`. The cut list needs `fps` and `segments` with `sourceInFrame`,
`sourceOutFrame` and `timelineStartFrame`; a segment's `durationFrames` is checked when it is there.

What each script checks: the comments and assertions in `tests/test_join_check.py` and
`tests/test_build_captions.py`.

## Take cutter: propose the cut, Lex approves the paper edit

```powershell
python scripts\video\cut-takes.py <words.json> --fps 30 --script <script.md> --demo 410-418 --out-dir <new-folder>
```

It reads the same transcripts as the two scripts below and writes three files into a new folder. It renders
nothing and touches no media.

| File | What it holds |
| --- | --- |
| `paper-edit.md` | For Lex: what was removed and why, what he must decide, the ad-libs, the timing used and every join |
| `cutlist.json` | The proposed cut in the usual cut-list shape (`fps`, `segments`), marked `"status": "proposal"` |
| `paper-edit.json` | The same paper edit for tools |

Lex reads and approves `paper-edit.md` before anyone builds a timeline or renders. After that, run the join check
on `cutlist.json` and listen.

What it does:

- **Retakes.** A quiet "again" with a pause around it marks a retake: the failed attempt and the word itself are
  removed. "Again and again", an "again" inside a sentence and an "again" the script itself says ("So, again,
  you see") stay. A sentence begun, dropped and restarted after a pause loses the dropped start; a dropped start
  of fewer than four words ("And then I—", "But I'm—") is only listed, as the Sunday paper edit asks.
- **The whole run of failed tries.** From the first failed start to just before the take that stays, as one
  passage, never into that take: a breath of under 1.5 s inside a take does not start a new one. A broken-off
  start ("If you could—") before a long pause goes when, with a script, the rest says the whole line. A finished
  first try that opens like the take ("Setting up the things.") goes too, unless the script has it as a line of
  its own.
- **Unfinished starts the transcript hides.** The transcript often drops a quiet "again", leaves the full stop
  off an unfinished start, or puts a stray word between two attempts, so a start can run into the take after it.
  Every attempt within 30 s before a complete take that opens with the take's first two words goes, part way
  through a phrase too ("So next, the..."), back to a sentence that says something else. With a script, what
  goes must belong to the take's own line; without one, a start with more than a stray word in it, or a
  shorter finished sentence that opens the same way, is listed instead of cut. A start cut off by a pause of
  1.5 s or more inside a kept take goes when the rest opens like another take of the line. A repeat after a
  comma ("The tool is fast, the tool is cheap.") is only cut when the script says that line once and the start
  belongs to it; otherwise it is listed.
- **Takes of one line.** The last complete take stays: the same restart, the same script sentence however far
  apart, or a near copy within a minute when there is no script. When the last take is clearly worse (more
  stumbles, or more of the script line missing), the better earlier take stays and the paper edit says so. A
  line the script itself says more than once is never treated as takes of one line. When no take of a line is
  complete, a take more than a minute from the others stays and is listed.
- **Timing (Lex, 2026-11-10).** 100-150 ms of room before and after speech at every cut, the start of the clip
  too; a whole silence of 150-250 ms between linked phrases (it aims for 200) and 250-450 ms before a new thought
  (it aims for 300); a breath inside a linked phrase stays; 15 ms fade at every cut. No cut lands inside a word.
- **Left in and listed for Lex:** anything inside a `--demo` range (start-end in source seconds, repeat it for
  more), a silence of 3 s or more (a screen demo or a pause?), a possible restart without a pause, a possible
  retake cue, and lines not in the script (ad-libs). An ad-lib is a line whose words are not in the script and
  that no take of the same line follows; a failed try is not an ad-lib. A removal that takes out a silence of
  3 s or more says so. Without `--script`, ad-libs are not checked.

The working values it uses beyond Lex's numbers are listed in each paper edit. The join check wants the same
100-150 ms before the first word (Lex, 2026-11-10), so the cutter's opening passes it.

## After every fix to a cut

After every fix to a cut, update EDITING-LESSONS.md with the preference or rule behind the fix, in one line,
so the same mistake does not come back. The file lives in that video's `acut/` folder.

## Join check: the AI reads first, Lex listens second

```powershell
python scripts\video\join-check.py <words.json> <cutlist.json> --out-dir <new-folder>
```

This writes `join-check.md`, for reading, and `join-check.json`, for tools. Run it after building or changing
a cut, fix what it shows, then listen. It reports:

- **Problems, listed first:** a cut inside a word, a clipped word start or end, a fade that touches a word,
  or a short word left on its own.
- **Checks, listed second:** a gap outside the preferences, a tight speech edge, a long or short lead-in, a
  kept "again" beside a join, or a join with no transcript words on one side.

The preferences come from `EDITING-LESSONS.md` and Lex's answers of 2026-11-10: 100-150 ms before the first
word, 150-250 ms between linked phrases, 250-450 ms for a new thought, at least 100 ms around speech edges, and
15 ms fades. A last word ending in `.`, `!` or `?` counts as a new thought. Those lessons call the numbers
suggestions to audition, so a flag on a gap is a question, not an order. The limits for a clipped edge and an
orphan word are this script's own working values, listed at the end of each report.

The script never counts as listening. Word times are approximate and a sound missing from the transcript cannot
show up, so no flag does not mean clean. You still need to listen to the whole cut at normal speed.

## Captions for the edited timeline

```powershell
python scripts\video\build-captions.py <words.json> <cutlist.json> --out-dir <new-folder> --name <name>
```

| File | What it holds |
| --- | --- |
| `<name>.phrases.srt`, `<name>.phrases.vtt` | Normal captions: up to two lines of 42 characters and 7 seconds |
| `<name>.words.srt`, `<name>.words.vtt` | One cue per word, with the word's exact kept time |
| `<name>.timed-words.vtt` | Phrase captions with a timestamp before each later word |

Removed words are left out and every time follows the cut list. Times count from the first frame of the edited
timeline. The files stay separate from the video; nothing is burned in. After any change to the cut, rebuild from
the new cut list, because the old times no longer match.

### Does the word-by-word file also help cutting?

Yes, as a reference. It shows each kept word and where it starts and ends on the edited timeline, so you can put
it on a spare subtitle track and see words against the waveform. Three limits:

- The times are the transcript's, so they are approximate. Check the waveform and listen before moving a cut.
- It uses edited-timeline times, not original-recording times.
- As far as we know it does not fill Resolve's own transcript panel, which Resolve makes with its own
  transcription. This has not been tried here.

It is a working file, not one to publish. Use the phrase files for viewers.

### Import the SRT into DaVinci Resolve Studio

These steps have not yet been tried on this PC, so check the first cue before relying on the result.

1. On the Edit page, choose **File > Import > Subtitle** and pick the `.srt`. It lands in the Media Pool.
2. Drag it to the first frame of the timeline. This adds a subtitle track and leaves the picture and sound alone.
3. Check the timing. The SRT counts from zero, and a Resolve timeline usually starts at 01:00:00:00, so play the
   first cue against the first spoken word. `build-captions.py` prints that word's time, which only shows where
   to look. If the whole track sits off, move it along the timeline by the difference between the cue and the
   word.
4. To keep captions out of the picture, on the Deliver page set the subtitle export to a separate file, or leave
   subtitle export off. Do not choose the burn-in option.
