---
title: Show the rough cut on the review page
type: task (HITL until Lex answers the question below, then AFK)
status: open
assignee: unassigned
blocked_by: [04-rough-cut-from-best-takes]
---

## What

[Ticket 04](04-rough-cut-from-best-takes.md) keeps `rough-cut.json` up to date: the best take of every scene, in script
order, with its file, length and flags. The existing review page and cutter cannot take that list as it is (see
`## Done` of ticket 04), so the rough cut is not on the review page yet. This ticket makes it one playable video there.

Why the existing cutter does not fit: `cut-takes.py` and `cut-to-review.py` work on one recording with its
word-timed transcript and a cut list Lex approved, and `cut_timeline.load_cutlist` refuses a list that mixes
recordings. The review video is rendered by DaVinci Resolve. A rough cut is many finished clips, has no transcript yet,
and needs no cutting inside a take.

- A command joins the best takes named in `rough-cut.json` into one file in the video's folder (`rough-cut.mp4`), in
  script order, with ffmpeg (free, no Resolve, runs on this computer). Takes that share the same size, frame rate and
  codec (OBS saves them all with the same settings) are joined without re-encoding; if one differs, say which scene and
  re-encode only then.
- A scene with no take is skipped and listed in the command's output ("scene 3 not recorded yet"); a flagged scene is
  listed too.
- Register the file on the review page as a video named after the folder, with a marker at every join (scene number and
  title), so Lex can jump from join to join. The file stays on this computer; nothing goes to YouTube.
- The join check (`join-check.py`) listens to the join points; the existing rule stays: Lex listens to every join before
  the cut goes further.
- Run after the rough cut changes: the same moment `rebuild_rough_cut` runs, but joining video takes minutes for long
  takes, so it runs on its own (command, not on every star click) and says when the file is older than `rough-cut.json`.
  Lex decides in this ticket whether it should run by itself after a star (see the question below).

## Question for Lex

Joining the takes into one video takes a while for long takes. After a star change, should the video be re-joined at once
(slower star click answers, or a short wait before the page is current), or only when Lex asks for it? Recommended:
only when asked, with the page saying the video is older than the list.

## Files

- `scripts/video/rough_cut_video.py` (new)
- `tests/test_rough_cut_video.py`
- docs: one paragraph in `docs/` where the review page's registration is described

## Done when

- Lex's answer to the question is written into this ticket before the build starts.
- Tests (ffmpeg stood in for, plus one real two-clip join with generated clips): join order, a missing scene skipped, a
  flagged scene listed, markers at the joins, a differing take named and re-encoded, and a video older than the list
  said as such.
- The review page plays the rough cut and jumps between joins (real browser check, screenshots in `## Done`).
- `bash scripts/agents/run-tests.sh` passes; landed on `dev` through `kit pr`.
