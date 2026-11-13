---
title: The per-video scene file
type: task (AFK)
status: open
assignee: unassigned
blocked_by: []
---

## What

One small file per video, `scenes.json` in the video's raw-files folder, kept only by video-review. It holds the
scene list, every take, Lex's stars, the computer's favorite and (later) markers. Everything else in this slice reads
and writes it through one module.

- New module `scripts/video/scene_store.py` (plain Python, no installs).
- Scene list: read the numbered section headings of the video's `prompter.md` cue file (like `2 · Title`). Build each
  scene's id as `02-what-it-really-costs` (two digits, lower-case title, words joined by dashes). Check the exact
  heading pattern against a real saved cue file copied into `tests/fixtures/` (strip anything personal first).
- Take naming: file `take-03.mkv` inside `scenes/02-what-it-really-costs/`; the full name is
  `scene-02-what-it-really-costs-take-03`. Helpers to build and read both forms.
- The scene list is saved once, at the first take. Later script changes are compared with the saved list: a scene
  whose title or position changed gets `script_changed: true`; nothing is renamed or deleted by itself.
- Best take of a scene, in this order: Lex's star if there is one; else the newest take; except an unstarred scene
  whose newest take the sound check marks "clearly worse" (clearly worse = it ends early or has a long pause while an
  older take is clean) uses the better older take and sets `flagged: true`. The computer's favorite never overrides
  a star.
- Reads and writes are safe if two programs touch the file: write to a temporary file, then rename.

## Files

- `scripts/video/scene_store.py`
- `tests/test_scene_store.py`
- `tests/fixtures/` (one cue file, one sample `scenes.json`)

## Done when

- Tests cover: scene ids from headings, take names both ways, scene list saved once, a changed title flagged, star
  wins, newest by default, the clearly-worse case, and a half-written file never read as a broken one.
- `bash scripts/agents/run-tests.sh` passes; landed on `dev` through `kit pr`.
- The builder lists the file's fields in the ticket's `## Done` so the next tickets use the same names.
