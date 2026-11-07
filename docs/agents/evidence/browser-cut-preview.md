# Browser cut preview: what to try and what was measured

## Try this, Lex (about five minutes)

The builder cannot listen, so these need your ears. Use a real review video that has a few removals, and make
the removals from the transcript first if it has none.

1. **Play the whole cut.** Press **Play whole cut**. It should start at the first kept moment and play straight
   through, skipping every removal. Space should pause and continue from the same place. Then click a word and
   press Space again: it should play from that word.
2. **Read the clocks.** While it plays, **Cut** counts up steadily to the cut's length, and **Source** jumps forward
   at each removal. The Cut length should be the file's length minus what you removed.
3. **Listen to the joins.** Find a join where the sound runs straight across the cut. Listen with **Join fade** on,
   then click it off and listen again. With it off you may hear a small click; with it on the click should be gone,
   and the words on each side should sound complete. Tell us if a word sounds clipped with the fade on but not off.
4. **Notice any silence at a join.** A short silence may be there because the browser is finding its place in the
   file. It is not in the final cut. If it is long enough to spoil the feel, tell us: see "Known limit" below.
5. **Switch to cut time.** Click **Source time** so it reads **Cut time**. The seek bar now covers only the cut. Drag
   it across a removal: the picture and words should never land in removed material. Comment times now show cut
   time too; hover one to see its source time.
6. **Ask the agent.** Select a few words, choose **Ask agent**, write a request and choose **Send to agent**. It
   should appear in the comment list marked **For agent**, with the quote and frames. Then ask your agent to read
   its open comments: it should repeat your request and name the same words.

## Measured without listening

`tests/cut-preview-browser.mjs` plays a made-up 8-second video with a steady tone through two 1-second removals and
records the sound sample by sample, before and after the fade. All numbers are from headless Chromium on Lex's
PC, on 2026-11-04.

| Measure | Fade on | Fade off |
| --- | --- | --- |
| Largest sample-to-sample step at a join (normal tone: 0.029, tone height: 0.5) | 0.029 at all 24 joins | about 0.5 at most joins, up to 0.96 |
| Jump lateness against the join (one frame is 33 ms) | at most 12 ms | at most 12 ms |

The lateness row covers the 12 repeated runs, so the fade is not delaying the jump. Each run also checks that the
sound returns to full volume after the last join.

The silence at a join depends on the file:

| Test file | Silence at a join |
| --- | --- |
| Small flat picture | usually under 30 ms, once 80 ms |
| Busy 1080p, keyframe every 15 frames | 25–55 ms |
| Busy 1080p, ordinary long keyframe gaps (a worst case) | 0.45–1 s |

On an 80-second copy of a real recording in headless Chrome (2026-11-05), the playback clock moved again 16–39 ms after
a jump with a keyframe every 15 frames, and 55–70 ms after a jump with one every 300 frames (three runs at two joins,
picture hidden). This times the clock, not the silence, so it is not directly comparable with the table above.

In a one-off check (three runs), at most 2.5 ms of the sound after the join point got through before the jump.
Once the sound returns, the fade takes about 12.5 ms to bring it back up, so it should not shave more than the
15 ms fade from the next word. Comparing that against a real word still needs your ears.

## Known limit

Playback still jumps by seeking within one video, as the brief asked. On a long-keyframe file, this can put up to
about a second of silence at a join, so the pacing is not yet exactly what the export will sound like. Two possible
next steps, neither built:

- Make review copies with a keyframe about every half second. In the test above this cut the silence to about 50 ms.
- For gapless joins, get the next passage ready in a second hidden video and switch to it at the join.

The picture can also show one or two removed frames at a join before the jump lands. The default transcript view
hides the picture, so this only shows with **Show video** on.

## Not verified

- How joins sound on real speech, and whether 15 ms feels right. Only a synthetic tone was measured.
- The actual silence at a join on real footage. Only the clock was timed there, and nobody has listened yet.
- The hosted app in a browser. Its code for change requests has unit tests, but `wrangler` is not installed here, so the
  live relay did not run. Its videos play from the same origin, so the fade should work there; try it once.
- Browsers other than Chromium.
