---
title: Rough cut from the best takes
type: task (AFK)
status: open
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
