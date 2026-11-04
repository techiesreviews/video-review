# Cut checks and captions

Two scripts read a word-timed transcript and a cut list. They read only those two JSON files, touch no
media, call no paid service, and never overwrite: point `--out-dir` at a new folder each time.

Use the raw HappyScribe export as the transcript when you have it. Word times must be on the original
recording's clock. `[text, start, end]` arrays are read as original-recording seconds, so only use them from a
raw export. A review transcript with `original.words` or `sourceStartMs` also works; word objects with only
`startMs` and `endMs` are refused, because they may be on the edited clock. When a transcript's `sourceId` is a
SHA-256 value, it must match the cut list's `sourceSha256`. The cut list needs `fps` and `segments` with `sourceInFrame`,
`sourceOutFrame` and `timelineStartFrame`; a segment's `durationFrames` is checked when it is there.

What each script checks: the comments and assertions in `tests/test_join_check.py` and
`tests/test_build_captions.py`.

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

The preferences come from `EDITING-LESSONS.md`: 180-250 ms before the first word, 150-250 ms between linked
phrases, 250-450 ms for a new thought, at least 100 ms around speech edges, and 15 ms fades. A last word ending
in `.`, `!` or `?` counts as a new thought. Those lessons call the numbers suggestions to audition, so a flag on
a gap is a question, not an order. The limits for a clipped edge and an orphan word are this script's own working
values, listed at the end of each report.

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
   first cue against the first spoken word. `build-captions.py` prints that word's time. If the whole track sits
   early by that amount, move it along the timeline by that much.
4. To keep captions out of the picture, on the Deliver page set the subtitle export to a separate file, or leave
   subtitle export off. Do not choose the burn-in option.
