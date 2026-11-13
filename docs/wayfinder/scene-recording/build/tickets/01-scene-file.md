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

## Done (2026-11-13)

Done when 1: met — `tests/test_scene_store.py` covers scene ids from headings, take names both ways, the scene list saved
once, a changed title flagged, star wins, newest by default, the clearly-worse case, and a half-written file never read as
a broken one (plus swapped headings, the lock and two programs writing at once).
Done when 2: met once `kit pr` lands it on `dev` — `bash scripts/agents/run-tests.sh` passes (the PR's checks run the same script).
Done when 3: met — the file's fields are listed below.

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
  the script (changed title, changed number, or removed), or that kept its id but now sits in a different order among
  the scenes both lists share (headings swapped without renumbering), and clears it when it matches again. Nothing is
  renamed or deleted; scenes only in the new script come back under `added` and are not added.
- Best take (`choose_best`): Lex's star; else the newest take (highest number); but if the newest ends early or has a
  long pause and an older take is clean, the newest clean older take is used and `flagged` is true. The favorite never
  decides. `save` refreshes `best` and `flagged` before writing, so readers always find them.
- Safe writes: temporary file in the same folder, then rename; leftover `.tmp` files are never read. A `scenes.json`
  that is not valid raises `SceneFileError` instead of looking empty.
- Every write goes through `update(raw_dir, change)`: it takes a lock the operating system holds on
  `scenes.json.lock` (released by the system even when a program crashes, so nothing goes stale), loads, runs
  `change(store)`, saves and releases, so a star clicked during a take intake is not lost. Tickets 02-04 must write
  this way, and do slow work (ffprobe, ffmpeg) before calling `update`: `change` should only copy results into the
  store, because another program waits at most 10 seconds for the lock. `ensure_scene_list` takes the same lock.
  The small lock file stays in the video's folder beside `scenes.json`.
- Edits to a script heading that keep the same id (only capital letters or punctuation) are ignored on purpose; the
  one-hash title line of a cue file is never a scene.
- Other helpers: `load`, `save`, `add_take`, `set_star`, `set_favorite`, `find_scene`, take-name helpers
  (`take_file_name`, `parse_take_file_name`, `take_full_name`, `parse_take_full_name`, `scene_folder`).
- Limit: a program that calls `load` and `save` itself, outside `update`, can still overwrite another program's change.

### Follow-ups

- Spec (P3): a4f05d4:scripts/video/scene_store.py:39 — P3 fault: the new `slugify` turns every non-ASCII character that does not break down to plain ASCII into a dash, including letters in the middle of a word. So "Straße" becomes `strasse`… no: it becomes `stra-e`, and "Smørrebrød" becomes `sm-rrebr-d`. The ticket asks for "words joined by dashes", and this splits a single word. The old code dropped those letters (`strae`), which was also lossy but kept the word whole. Ids stay stable either way, so the only cost is ugly ids for titles with letters like ß, ø, æ or ł. Fix: treat letters and digits as part of the word even when they are not ASCII, and drop them instead of turning them into a dash, for example `if not char.isascii(): continue if char.isalnum() else '-'`. Better still, map the common ones first (ß→ss, ø→o, æ→ae).
- Standards (P3): a4f05d4:docs/wayfinder/scene-recording/build/tickets/01-scene-file.md:47 — P3 style: the rewritten "Done when 2" line runs to about 131 characters, while the lines around it wrap at about 121. This is the same kind of problem R4-3 raised. Fix: wrap after "passes" so the line matches the paragraph.
- Validation gaps (gap): There is no test for a title with a non-ASCII letter that has no ASCII form, so the behaviour above is untested. Add to `SceneIds` in tests/test_scene_store.py: `self.assertEqual(scene_store.scene_id(4, 'Straße'), '04-strasse')` (or whatever result the fix settles on), so the rule is pinned down.

