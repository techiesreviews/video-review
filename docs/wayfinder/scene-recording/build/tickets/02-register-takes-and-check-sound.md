---
title: Register takes and check their sound
type: task (AFK)
status: open
assignee: unassigned
blocked_by: [01-scene-file]
---

## What

A command that looks at a video's folder and fills the scene file from the takes it finds.

- `python scripts/video/take_intake.py <video folder>`: scans `scenes/<scene id>/take-NN.mkv`, adds every take that
  is not yet in `scenes.json` (length from ffprobe, created time from the file), and leaves existing takes alone.
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
