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

- **Reading the script.** A script sentence ends at a full stop, question mark or exclamation mark (also in
  front of a closing quote, bracket or bold mark), and at a heading, a blank line or a list item, so a line
  without a full stop does not run into the next one. Headings and `[directions]` are left out.
  A link is read as its text, or left out when it has a line to itself, like a direction; images and bare
  web addresses (`http://` or `https://`) are left out.
- **Retakes.** A quiet "again" with a pause around it marks a retake: the failed attempt and the word itself are
  removed. "Again and again", an "again" inside a sentence and an "again" the script itself says ("So, again,
  you see") stay. A sentence begun, dropped and restarted after a pause loses the dropped start; a dropped start
  of fewer than four words ("And then I—", "But I'm—") is only listed, as the Sunday paper edit asks, also in
  front of a take that stays while another take of the line is removed. It goes only as the tail of a run of
  failed tries: when the word before it is removed too.
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
  apart, or a near copy within a minute when there is no script (two sentences in a row count as a restart only
  within that minute too). When the last take is clearly worse (more
  stumbles, or more of the script line missing), the better earlier take stays and the paper edit says so. A
  line the script itself says more than once is never treated as takes of one line. When no take of a line is
  complete, a take more than a minute from the others stays and is listed.
- **What the script says stays.** Three rules, each a net under the one before:
  - *The script puts back a full stop the transcript left out.* A pause where one script line ends and another
    begins ends a sentence ("The tool is fast | The tool is fast and easy to use." is two lines of the script,
    not a start and its restart). A script line of one word ("Yes.") counts when the word is said on its own: at
    the start of a sentence, with no comma or dash on it, with the pause after it, and not as the opening of a
    longer script line. This only decides what one take is; the timing rules still follow the transcript's own
    full stops.
  - *A take goes only when the take that stays says what it says.* Two sentences that the script joins into one
    line ("The camera is easy to use and the battery lasts all day.") are two parts of that line, not two takes:
    both stay, and a whole take of the line still replaces them. A take that says a script word the kept take
    leaves out stays and is listed: another part of the line, more of it than a kept take that picks the line
    up half way or stops early, or one word ("easy to use" before "easy to carry", when the script says "easy
    to use and carry"). A quiet "again" is held to the same rule. A start is
    not cut when the script has its words as a part of a line of their own, or says the line more than once: it
    is listed as a possible restart. When two script lines have the same words ("The camera beats the phone." /
    "The phone beats the camera."), a sentence belongs to the one with its word order.
  - *The last check.* After every other step, a script line that is said word for word in the recording must
    still be said in what stays, as often as the script says it: by those words, or by a kept take that says the
    line through to its last word. If not, the last word-for-word reading goes back in and is listed. Longer
    lines are counted first, so "The tool is fast." inside "The tool is fast and easy to use." is not counted
    twice; a line of one or two words ("Thanks again.") counts only as a sentence of its own, and one word at the
    start of a sentence, with no comma or dash on it and a pause after it, is said on its own too, whatever
    follows the pause.
    `tests/test_cut_takes.py` runs this rule over 600 made-up recordings, one-word lines among them.
- **Timing (Lex, 2026-11-10 and 2026-11-11).** 100-150 ms of room before and after speech at every cut, the start
  of the clip too; a whole silence of 200-300 ms between linked phrases (it aims for 250) and 350-500 ms before a
  new thought (it aims for 400); Lex chose these longer pauses (version B) after listening to the cutter's version
  next to the original, so a silence of up to 500 ms before a new thought is left as it is. Two sides of 100-150 ms
  add up to 300 ms at most, so before a new thought the room may reach 200 ms on a side (a working value, listed in
  each paper edit). A breath inside a linked phrase stays; 15 ms fade at every cut. No cut lands inside a word:
  a removal that touches the kept word beside it between two frames has no frame to cut at, so it goes back in
  and is listed for Lex to cut by hand, as a removal too short for whole frames is.
- **Left in and listed for Lex:** anything inside a `--demo` range (start-end in source seconds, repeat it for
  more), a silence of 3 s or more (a screen demo or a pause?), a possible restart without a pause, a possible
  retake cue, and lines not in the script (ad-libs). An ad-lib is a line whose words are not in the script and
  that no take of the same line follows; a failed try is not an ad-lib. A removal that takes out a silence of
  3 s or more says so, whether the silence is inside the removed passage or between it and the words kept beside
  it (it may be a screen demo). The seconds are what the cut list really leaves out there: the room kept beside
  speech, a `--demo` range and the silence kept with it are not counted. Without `--script`, ad-libs are not
  checked.
- **A marked demo with no speech stays too.** A removal beside it, the opening and the ending never take a
  `--demo` range out of the kept ranges, and the silence between the demo and the speech kept beside it stays.
  A demo between two removed takes becomes a piece of its own; its join is listed as "beside the marked screen
  demo". A demo that runs past the last word of the transcript is kept and listed, because the cutter cannot see
  how long the recording is. A cut that opens or ends on a demo has more than 100-150 ms before its first word
  or after its last one on purpose; the paper edit says so, and the join check lists a long lead-in to confirm.
  A demo marked less than 100 ms from a removed word stays whole and is listed as a tight cut to listen to.

The working values it uses beyond Lex's numbers are listed in each paper edit. The join check wants the same
100-150 ms before the first word (Lex, 2026-11-10), so the cutter's opening passes it, unless the cut opens on a
marked demo (see above).

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

The preferences come from `EDITING-LESSONS.md` and Lex's answers of 2026-11-10 and 2026-11-11: 100-150 ms before
the first word, 200-300 ms between linked phrases, 350-500 ms for a new thought, at least 100 ms around speech
edges, and 15 ms fades. A last word ending in `.`, `!` or `?` counts as a new thought. Those lessons call the numbers
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

## Cut list to review video: one command, asks before the upload

```powershell
python scripts\video\cut-to-review.py <cut-folder> --video <slug> [--version v1] [--project "<name>"] [--words words.json]
```

It needs Lex's approval of this exact cut list: an `approval.json` in the cut folder with `status` `"approved"`,
`approvedBy`, his `words`, the `date` (YYYY-MM-DD) and `cutlistSha256`, the SHA-256 of `cutlist.json`'s bytes. A
cut list that changed after he approved it is refused. An `approval` block with the same keys inside
`cutlist.json` also works; its `cutlistSha256` is then the hash of the cut list without the block, written as
compact JSON with sorted keys. The transcript is `words.json` in the cut folder unless
`--words` names another; the recording is the cut list's `sourcePath` unless `--source` names it.

In order, it writes everything into a new `renders/<slug>/<version>/` folder (an existing one is refused):

1. Read-only checks: the approval, the recording and its hash when the cut list has `sourceSha256` (without
   it, the report and the stop message say the hash was not checked), a constant frame rate equal to
   the cut list's, segments back to back from frame 0 adding up to `durationFrames` and inside the recording,
   and DaVinci Resolve Studio reachable with external scripting set to Local. It never starts Resolve.
2. The join check and the captions.
3. In Resolve, the project named by `--project` (default: the slug). It only opens or creates that project
   when it is already open or when Resolve sits at an empty Untitled Project; it saves a `.drp` checkpoint of
   an existing project first, and refuses any other open project. One timeline `A-CUT — working` (refused
   when one with clips exists): linked picture and sound per unbroken run (a split where the source runs on
   is one clip), every clip read back, the cut list's
   audio fades, a marker per join (red where the join check found a problem, yellow otherwise), a `.drt`
   export and a save.
4. A render to `<slug>-<version>.rendering.mp4` (H.264 on the NVIDIA encoder when Resolve offers it, else
   H.264; AAC 48 kHz), renamed to `<slug>-<version>.mp4` once Resolve reports it complete.
5. ffprobe: one video and one audio stream (Resolve's timecode data stream, tag `tmcd`, is allowed; any other extra stream fails), and the length within a frame of the cut list.
6. `review-upload.json` (what the review page will get, with the file's SHA-256) and `run-report.md`. Nothing
   is sent. When any step from the Resolve build onward fails, `run-report.md` is still written, with what was
   found so far (endFrame inclusive or not, codec, fades, markers) and the error.
7. It stops with exit code 3 and names the joins to hear first.

Only after Lex says upload:

```powershell
python scripts\video\cut-to-review.py --upload renders\<slug>\<version>
```

This registers `<slug>-<version>` on the local review page (http://127.0.0.1:8879), attaches the transcript
and opens it. The file stays on this computer; nothing goes to YouTube. `--hosted` uses video.techies.tools
with the token in `VIDEO_REVIEW_TOKEN`. A changed file or an id uploaded before is refused. Each finished step
is recorded in `review-upload-record.json`, so after a failure the same command carries on where it stopped.
If the upload stopped while the video was being registered and the id is already on the page, it carries on
only when the page lists that id as this exact file (the hash of the whole file). For another file nothing on
the page is changed and the upload asks for a new `--version`; that also happens to a hosted upload an older
version of this tool left unfinished, because it was registered without that hash.

`--dry-run` does steps 1, 2 and 6 into `renders/<slug>/<version>-dry-run/` without Resolve, a render or the
review page. A render can take long: agents start the command with `scripts/agents/kit go`.
