---
title: The overview page
type: task (AFK, visible)
status: open
assignee: unassigned
blocked_by: [01-scene-file]
---

## What

One page per video, built from `scenes.json`, in the layout Lex chose: the
[Borumi-style mockup](../../prototypes/video-overview-page-borumi-style.html). Decision and reasons:
[An overview page for every video](../../tickets/09-overview-page-for-every-video.md).

- A script writes `overview.html` into the video's folder: one self-contained file (styles and script inside), with
  relative links to the take files and thumbnails, so a double click works. It is rewritten after every
  registration run and every star change.
- Per scene: number, title, the scene's script text, the best take in front with a gold star, older takes under
  "Archived (n)", the computer's favorite as a hollow star ("pick") when it differs, a "Script changed since
  recording" chip, a "Not recorded yet" empty state, and the rough-cut strip on top (the best take of every scene in
  script order, with the total length).
- Thumbnails: one frame per take from ffmpeg, saved next to the take.
- Playing a take: click it, it plays in the page.
- "+ Record" tile: opens the Prompter on that scene on LexTop. Until the Prompter side exists (next slice) the tile is
  shown disabled with the text "Coming with the Prompter".
- Stars: when video-review's server is running on this computer, the page asks it for the take list and a star click
  is sent to it (`GET /api/scenes?video=…`, `POST /api/scenes/star` in `server.py`, which writes the scene file and
  rewrites the page). When the server is not reachable, the stars are greyed out with one short line saying why.
  The server also serves the same page at `/scenes/<video>`.
- Follow the mockup, not a new design. Keep it quiet: no extra panels.

## Files

- `scripts/video/overview_page.py` (builds the page)
- `server.py` (the two routes)
- `tests/test_overview_page.py`, `tests/test_server.py`

## Done when

- Tests cover: the page built from a sample `scenes.json` (scene with several takes, scene with none, changed
  script, favorite differing from star), the star route writing the file, and the greyed state's text.
- A real browser check on the local server: open the file by double click with the server stopped (stars greyed),
  then with it running (star click changes the file and the strip). Screenshots in `## Done`.
- Opens at phone width without sideways scrolling.
- `bash scripts/agents/run-tests.sh` passes; landed on `dev` through `kit pr`.
