---
title: Register takes and check their sound
type: task (AFK)
status: closed
assignee: unassigned
blocked_by: [01-scene-file]
---

## What

A command that looks at a video's folder and fills the scene file from the takes it finds.

- `python scripts/video/take_intake.py <video folder>`: scans `scenes/<scene id>/take-NN.mkv`, adds every take that
  is not yet in `scenes.json` (length from ffprobe, created time from the file), and leaves existing takes alone
  (only their length check is refreshed when a new take of the scene arrives, because it compares the takes).
- A take file that is still being written (size changing) is skipped and picked up next run.
- Free sound-only checks per take with ffmpeg, saved with the take:
  1. clean end: the take ends in a short stretch of silence, not mid-word;
  2. length close to the other takes of the same scene (within a set share of their middle length);
  3. no long pause in the middle (silence longer than a set number of seconds, default 2.5, after the first and before
     the last 2 seconds).
- The computer's favorite of a scene is the take that passes the most checks, newest on a tie. It is stored as the
  favorite and never as the star. A scene with one take has no favorite.
- No transcript, no paid service, no network.
- Thresholds are named constants at the top of the file, with the reason for each in a comment.

## Files

- `scripts/video/take_intake.py`
- `tests/test_take_intake.py`
- `tests/fixtures/` (tiny generated test clips, or a fake ffmpeg in the tests, whichever keeps the repo small)

## Done when

- Tests cover: a new take registered, an existing one untouched, a growing file skipped, each of the three checks
  passing and failing, favorite and tie rules, a scene with one take.
- One real run on a copy of a real recording (note which in `## Done`, no file committed) shows plausible checks.
- `bash scripts/agents/run-tests.sh` passes; landed on `dev` through `kit pr`.

## Done (2026-11-13)

Done when 1: met. `tests/test_take_intake.py` covers a new take registered, an existing one untouched, a growing
file skipped (and one that ffmpeg cannot read yet), each of the three checks passing and failing, the favorite and
tie rules, and a scene with one take. It also covers the write rules (one locked `update()`, slow work before it,
a take another program registered meanwhile is kept). Four tests make tiny sound clips with ffmpeg and are skipped
when ffmpeg is not installed; nothing is committed as a fixture.
Done when 2: met. One real run on copies of four finished spoken clips (32 to 54 seconds) from Lex's video folders,
laid out as two scenes (nothing copied into the repo): the clip that ends on sound failed check 1, the clip about
60% of the others' length failed check 2, the rest passed all three, the favorite was a passing take, and a second run
added nothing. The clips are edited, not raw takes, so a real OBS recording is the next thing to try.
Done when 3: met once `kit pr` lands it on `dev`; `bash scripts/agents/run-tests.sh` passes.

Built `scripts/video/take_intake.py` (`python scripts/video/take_intake.py <video folder>`).

Rules as built (so tickets 03 and 04 know what is in `scenes.json`):

- The video folder is the one with `scenes/` and `scenes.json`. It must already have a `scenes.json` (the scene list
  is saved at the first take, ticket 01); without it the command stops and says so. Take folders that are not in the
  scene list are reported and skipped. Only files named exactly `take-NN` with `.mkv`, `.mp4` or `.mov` count.
- A take already in `scenes.json` is not looked at again. A new take gets `duration` (ffprobe), `recorded_at` (the
  file's creation time on Windows and macOS, its last-written time elsewhere, UTC) and `sound`.
- A file whose size or modified time changes within 2 seconds, or that is empty, is skipped as still being written;
  so is one that changes while ffprobe and ffmpeg are working on it (its results would be stale). A file they cannot
  read yet is skipped too. All of these are picked up on the next run.
- `sound` holds `ends_early`, `length_off`, `long_pause` as in ticket 01. A take without a sound track is registered
  with no `sound` (not checked). `length_off` is compared with the middle length of the *other* takes of the scene, so
  a scene's first take has no `length_off` until a second take arrives; when new takes arrive, the `length_off` of the
  scene's older takes that already have `sound` is refreshed, and nothing else of an older take changes.
- Thresholds are named constants at the top of the file, each with its reason: silence is quieter than -35 dB for
  0.3 s; a clean end is a silence of at least 0.3 s that reaches the end (0.25 s slack); a take is length-off beyond
  30% of the others' middle length; a long pause is a silence over 2.5 s that starts after the first 2 s and ends
  before the last 2 s. Only the first sound track is listened to (OBS puts the full mix there).
- The favorite is the take with the most passed checks (a check that did not run is not a pass), the newest on a tie.
  It is only set on scenes that got a new take, only with two or more takes, saved as `favorite` and never as `star`.
- Not done on purpose: no transcript, no network, no OBS or Prompter link. ffmpeg and ffprobe must be installed; the
  command says so when they are not.
- For the OBS slice: only files named `take-NN.<ext>` are registered, while ticket 06 has OBS save the longer
  `scene-NN-title-take-NN` name. Files with the long name are skipped without a message, so that slice must save
  under the short name or extend this command (the scene file's `file` field would then hold the real name). A
  recording that OBS pauses for more than 2 seconds could also look finished while half written.

### Follow-ups

- Previous findings (P3): R3-1 still open: the "What" section of the ticket (line 14) still says the command "leaves existing takes alone". The "Rules as built" notes (lines 64–67) now describe how `length_off` is refreshed, but line 14 was not changed, so the ticket still contradicts itself.
- Spec (P3): 53fe46c:docs/wayfinder/scene-recording/build/tickets/02-register-takes-and-check-sound.md:14 — P3 ticket: (still open as R3-1) the "What" section says the command "leaves existing takes alone", but `_refresh_scene` (`scripts/video/take_intake.py:272`) rewrites `sound.length_off` on older takes whenever a new take arrives in their scene. Fix: change the line to something like "leaves existing takes alone apart from refreshing their `length_off` and the scene's favorite", to match lines 64–67.

