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

Done when 1: met — `tests/test_scene_store.py` covers scene ids from headings, take names both ways, the scene list
saved once, a changed title flagged, star wins, newest by default, the clearly-worse case, and a half-written file
never read as a broken one (plus swapped headings, the lock and two programs writing at once).
Done when 2: met once `kit pr` lands it on `dev`; `bash scripts/agents/run-tests.sh` passes and the PR's checks
run the same script.
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
- A title edit that changes only capital letters, spacing or dash style is ignored on purpose; any other change
  (even symbols, such as "C++" to "C#") flags the scene. The one-hash title line of a cue file is never a scene.
- Other helpers: `load`, `save`, `add_take`, `set_star`, `set_favorite`, `find_scene`, take-name helpers
  (`take_file_name`, `parse_take_file_name`, `take_full_name`, `parse_take_full_name`, `scene_folder`).
- Limit: a program that calls `load` and `save` itself, outside `update`, can still overwrite another program's change.

### Follow-ups

- Standards (P3): f95e755:scripts/video/scene_store.py:80 — P3 fault (R7-1): `title_key` doesn't ignore some small edits that the id rules already treat as the same title, so `check_script` can flag a scene whose wording did not change. Two cases. First, `slugify` treats `'` and `’` the same, but `title_key` does not, so "WHAT'S NEXT" becoming "WHAT’S NEXT" (an editor turning on smart quotes) sets `script_changed`. Second, spacing around a dash counts: "AI - what changes" and "AI—what changes" give the same id (the test at tests/test_scene_store.py:86 says so), but their keys are "ai - what changes" and "ai-what changes", so the scene is flagged. The stored title is never updated, so the flag stays until the heading is changed back. The docs promise that "spacing or dash style" edits are ignored. Fix: in `title_key`, turn `’ ‘ \`` into `'`, then remove spaces next to a dash, e.g. `re.sub(r'\s*-\s*', '-', ...)` after the dash pass. Also add a line to the ticket rule at docs/.../01-scene-file.md:89 saying quote style is ignored.
- Standards (P3): f95e755:scripts/video/scene_store.py:76 — P3 fault (R7-2): vowel signs that are not "combining" in Unicode's sense still turn into dashes, so words in Thai, Hindi and other Indic scripts are split. `unicodedata.combining()` only skips marks with a non-zero combining class. Thai vowel signs such as U+0E31 (category Mn, class 0) and Devanagari vowel signs such as U+093F (category Mc, class 0) fail both `combining()` and `isalnum()`, so each becomes `-`. For example `'สวัสดี'` gives `'สว-สด'` and `'हिन्दी'` comes out with dashes in the middle of the word. This is a rare path, since the titles are mostly English. Fix: keep a character as part of the word when `unicodedata.category(char)` starts with `'M'` and `combining(char)` is 0, i.e. `char.isalnum() or unicodedata.category(char)[0] == 'M'`, and leave the existing skip for marks with a non-zero class (Latin accents) as it is.
- Validation gaps (gap): R7-1 has no test. Add to `ScriptChanges`: `self.assertEqual(self.flagged(CUE.replace("WHAT'S NEXT", "WHAT’S NEXT")), [])` (adjust the string to the fixture's actual heading). Add one for spacing around a dash: build a store from `'### 1 · AI - what changes\n'`, run `check_script` with `'### 1 · AI—what changes\n'`, and `assertFalse(store['scenes'][0]['script_changed'])`.
- Validation gaps (gap): R7-2 has no test. Add to `SceneIds`: `self.assertEqual(scene_store.scene_id(1, 'สวัสดี'), '01-สวัสดี')`.

