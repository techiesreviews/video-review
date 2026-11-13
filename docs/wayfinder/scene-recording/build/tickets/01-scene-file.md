---
title: The per-video scene file
type: task (AFK)
status: closed
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

## Done

Built `scripts/video/scene_store.py` with `tests/test_scene_store.py` and two fixtures
(`tests/fixtures/scene-cue-file.md`, a cue file in the real saved layout with invented words;
`tests/fixtures/scenes.sample.json`).

Fields of `scenes.json` (use exactly these names in tickets 02-04):

- File: `version` (1), `saved_at` (UTC time the scene list was saved), `scenes`, `markers` (empty list, for later).
- Scene: `id` (`02-what-it-really-costs`), `number`, `title` (as written in the heading), `script_changed`,
  `star` (take number or null, Lex's), `favorite` (take number or null, the computer's), `best` (take number or null),
  `flagged`, `takes`.
- Take: `number`, `file` (`take-03.mkv`), `name` (`scene-02-what-it-really-costs-take-03`), and when known
  `recorded_at`, `duration` (seconds), `sound`.
- `sound`: `ends_early` (check 1 failed: not a clean end), `length_off` (check 2 failed: length not close to the other
  takes), `long_pause` (check 3 failed: a long silence in the middle), each true or false, written by the sound check
  (ticket 02). A check missing from `sound` counts as not run. Only `ends_early` and `long_pause` decide "clearly
  worse" and the best take ("clean" = both false); `length_off` only feeds the computer's favorite.

Rules as built:

- Scene = numbered heading like `### **2 · TITLE · 02:00**` (bold marks and time are optional). PREP and other
  unnumbered headings are not scenes. Id: two-digit number, lower-case title, apostrophes dropped, other symbols
  become dashes.
- `ensure_scene_list(raw_dir, cue_text)` saves the list at the first take; later calls return the saved file as is.
  `check_script(store, cue_text)` sets `script_changed` on a saved scene whose id (number plus title) is no longer in
  the script (changed title, moved, or removed) and clears it when it matches again. Nothing is renamed or deleted;
  scenes only in the new script come back under `added` and are not added.
- Best take (`choose_best`): Lex's star; else the newest take (highest number); but if the newest ends early or has a
  long pause and an older take is clean, the newest clean older take is used and `flagged` is true. The favorite never
  decides. `save` refreshes `best` and `flagged` before writing, so readers always find them.
- Safe writes: temporary file in the same folder, then rename; leftover `.tmp` files are never read. A `scenes.json`
  that is not valid raises `SceneFileError` instead of looking empty.
- Every write goes through `update(raw_dir, change)`: it takes a lock file (`scenes.json.lock`, taken over when older
  than 30 seconds), loads, runs `change(store)`, saves and releases, so a star clicked during a take intake is not
  lost. Tickets 02-04 must write this way. `ensure_scene_list` takes the same lock.
- Edits to a script heading that keep the same id (only capital letters or punctuation) are ignored on purpose; the
  one-hash title line of a cue file is never a scene.
- Other helpers: `load`, `save`, `add_take`, `set_star`, `set_favorite`, `find_scene`, take-name helpers
  (`take_file_name`, `parse_take_file_name`, `take_full_name`, `parse_take_full_name`, `scene_folder`).
- Limit: a program that calls `load` and `save` itself, outside `update`, can still overwrite another program's change.
