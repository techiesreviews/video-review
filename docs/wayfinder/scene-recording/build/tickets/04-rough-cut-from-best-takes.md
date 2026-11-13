---
title: Rough cut from the best takes
type: task (AFK)
status: closed
assignee: unassigned
blocked_by: [02-register-takes-and-check-sound, 03-overview-page]
---

## What

The rough cut is rebuilt on its own whenever a best take changes
([decision](../../tickets/07-how-takes-show-up-in-video-review.md)): after a star click, and after a registration
run brings a new take.

- `rebuild_rough_cut(video folder)` in `scripts/video/scene_store.py` (or a new small module): writes `rough-cut.json`
  next to `scenes.json`: the best take of each scene in script order, with file path, length, and the flags
  (`flagged`, `script_changed`, scene not recorded).
- Called from the star route and from the registration run, so there is no send button.
- Read `scripts/video/cut-to-review.py`, `cut_timeline.py` and `resolve_build.py`. Each take is already one finished
  scene, so nothing is cut inside it. If the existing review page and cutter can take a list of finished clips, feed
  `rough-cut.json` to them so the rough cut shows on the review page. If they cannot without a larger change, do not
  stretch this ticket: say so in `## Done` and write a follow-up ticket for it.
- Pushing the list into the Resolve timeline needs LexTop and is not part of this slice.
- The existing rule stays: Lex listens to every join before the cut goes further.

## Files

- `scripts/video/scene_store.py` (or the new module)
- `server.py` (call after the star)
- `scripts/video/cut-to-review.py` only if the check above says it can take the list
- `tests/test_scene_store.py`, `tests/test_server.py`

## Done when

- Tests cover: the file after a star change, after a new take, a scene with no take (listed as missing, not dropped),
  and a flagged scene.
- The overview page's strip and `rough-cut.json` agree after each change (shown in a test).
- `## Done` says plainly whether the review page shows the rough cut now, or what follow-up ticket covers it.
- `bash scripts/agents/run-tests.sh` passes; landed on `dev` through `kit pr`.

## Done (2026-11-13)

Done when 1: met. `tests/test_rough_cut.py` checks the file after a star change (star on, star off, a new take not
replacing a star), after a new take (in a scene that had takes and in an empty scene), a scene with no take (listed with
`recorded: false` and null take, file and length, keeping its place in script order), a flagged scene (an older take in
front because the newest has a sound problem; a star clears the flag), the changed-script flag (the saved flag, or a
heading that no longer matches the cue file) and a best take without a length. `tests/test_server.py` checks that a star
click rebuilds the file (on, to another take, off), that a bad star request leaves it alone, and that a failed rebuild
is said while the star is kept and the page is still written. `tests/test_take_intake.py` checks that a registration
run rebuilds it (with a new take, with nothing new, a failed rebuild said without failing the run or skipping the page).
Done when 2: met. `AgreesWithTheOverviewStrip` in `tests/test_rough_cut.py` builds the overview page from the same
scene file after each change (start, star on, star off, new take, a take in an empty scene, a flagged scene, a best
take without a length, no take at all) and compares the strip (scene and take of every segment, "not recorded yet" for
missing ones) and the header total with `rough-cut.json`; `tests/test_server.py` and `tests/test_take_intake.py` do the
same after a real star click and a real run.
Done when 3: **the review page does not show the rough cut yet.** The existing cutter takes one recording with its
word-timed transcript and a cut list Lex approved (`cut_timeline.load_cutlist` refuses a list that mixes recordings, and
`cut-to-review.py` checks one source file and its hash and renders the review video in DaVinci Resolve), so it cannot
take a list of finished clips without a larger change. The review page plays one registered video file. The follow-up is
[06 Show the rough cut on the review page](06-rough-cut-on-the-review-page.md): join the best takes into one file with
ffmpeg and register it with a marker at every join. Pushing the list into the Resolve timeline stays out (it needs LexTop).
Done when 4: `bash scripts/agents/run-tests.sh` passes (the unit tests and the two script suites; also with ffmpeg on the
path, so the four real-clip tests run too); the landing is this change's own pull request into `dev` through `kit pr`.

Rules as built (for ticket 06 and the Prompter slice):

- `scripts/video/rough_cut.py` (new): `rebuild_rough_cut(video folder, cue=None, data_dir=None)` writes `rough-cut.json`
  next to `scenes.json` and returns the list; the command `python scripts/video/rough_cut.py <video folder>` does the same
  by hand. It reads `scenes.json` and writes the list while holding the scene file's lock (`scene_store.locked`), writes
  through `scene_store.write_text_atomic` (new: the temporary-file-then-rename write that `save()` already used, now
  shared), and never changes `scenes.json`. A folder without a usable scene file raises `SceneFileError` and leaves the
  old list alone.
- The file: `version`, `total_seconds`, `lengths_known` (false when a recorded scene's best take has no length) and
  `scenes`, one per scene in script order: `scene`, `number`, `title`, `recorded`, `take`, `file` (relative to the video
  folder, such as `scenes/02-what-it-really-costs/take-03.mkv`), `duration`, `flagged`, `script_changed`. The best take is
  `scene_store.choose_best`, the same call the overview page's strip uses.
- `script_changed` is the saved flag, or the scene's heading no longer matching the cue file now: the rule of the overview
  page's chip (`overview_page.changed_in_script`, made public for this; so is `overview_page.read_cue_text`). The cue file
  is the one named, else the one the video was registered with (`data_dir`), else `prompter.md` in the video folder.
- Called from the star route in `server.py` (after the scene file is saved, before the page is rewritten) and from the
  command line of `take_intake.py` after every run, new takes or not. A failure in one is said and does not stop the
  other: the star route answers 500 "The star is saved, but the rough cut could not be rebuilt: ..." (and says so for the
  page too); the intake prints "rough cut not updated: ..." and still exits 0. The rebuild is its own locked step after
  the scene file's write, so a program that stops between the two leaves a list one change behind until the next star or
  run; every rebuild reads the newest scene file, so two at once end with the list of the latest one.
- The hand-run command only looks up the registry in the data folder (`--data-dir`, else `VIDEO_REVIEW_DATA_DIR`, else
  `data/`), never creates it, and says a failure in one line. A registry that cannot be read means no remembered cue file.
- Nothing is cut and no media is read; the Resolve timeline is not touched.

Demo for Lex (not in the repo): `bash .scratch/overview-sample/start-demo.sh` in the main checkout of video-review sets up
a fresh sample video, starts the server and opens the overview page and a self-reloading "Rough cut (live)" page; a star
click changes the live page within two seconds, and `bash .scratch/overview-sample/new-take.sh` brings a new take through
a real registration run. `stop-demo.sh` stops it.
