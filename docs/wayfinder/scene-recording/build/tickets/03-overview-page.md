---
title: The overview page
type: task (AFK, visible)
status: closed
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

## Done (2026-11-13)

Done when 1: met. `tests/test_overview_page.py` builds the page from a sample scene file (a scene with several
takes, a scene with none, a changed script, a favorite that differs from the star), and checks the strip, the
links, the thumbnails (ffmpeg replaced by a stand-in), the registry and the command line. `tests/test_server.py` covers
the star route writing `scenes.json` and rewriting the page, taking a star off, bad requests changing nothing, the
foreign-origin refusal, the two routes `GET /api/scenes?video=` and `/scenes/<video>`, and file serving. The greyed
state's text is a constant (`STARS_OFF_TEXT`) that a test compares and counts once on the page.
Done when 2: met, in a real Chrome (Playwright driving the installed Chrome), on a sample video folder with six
real clips and thumbnails made by ffmpeg (the clips are flat colours; no file committed):
with the server stopped, the page opened from the file (`file://`) shows every star greyed and the line "Stars are
off. Start video-review on this computer to change them." ([desktop](03-overview-page/server-stopped-desktop.png),
[phone](03-overview-page/server-stopped-phone.png)); with the server running, the stars are live, a click on an
archived take's star wrote `star: 1` into `scenes.json`, moved that take to the front, and the strip and the header
changed from 0:29 to 0:27 ([after the click](03-overview-page/server-running-after-star-click.png)). A click on a
take plays it in the page (the player showed 0:01 of 0:08 with no error), and the page the server
serves at `/scenes/<video>` has live stars too.
Done when 3: met. At 375 px wide the page has no sideways scroll, with the server stopped and running
([phone, server running](03-overview-page/server-running-phone.png)).
Done when 4: met once `kit pr` lands it on `dev`; `bash scripts/agents/run-tests.sh` passes.

Built `scripts/video/overview_page.py`, the two routes in `server.py`, and one line in `take_intake.py`'s command so
the page is rewritten after every registration run.

Rules as built (for ticket 04 and the Prompter slice):

- `python scripts/video/overview_page.py <video folder> [--cue FILE] [--data-dir DIR]` writes `overview.html` into
  the video folder and registers the video with video-review's data folder. `take_intake.py` does the same after
  every run (a page that cannot be written is said, and does not fail the run). Ticket 04 can call
  `overview_page.build(folder, cue=...)` after its own changes.
- The video's name in the server's addresses is its key, `overview_page.video_key(folder)`: the folder name plus
  eight characters of a fingerprint of the full path, so two folders with the same name never mix. The server finds
  the folder in `scene-videos.json` in its data folder (`overview_page.register` / `lookup`); a key nobody
  registered gets a 404.
- `GET /api/scenes?video=<key>` returns the saved scene list. `POST /api/scenes/star` takes
  `{"video": key, "scene": id, "take": number-or-null}` (null takes the star off), writes through
  `scene_store.update()`, then rewrites `overview.html`; a star that is saved while the page cannot be rewritten
  answers 500 with a message that says so. A take number that is not there, a scene that is not there and a
  request that is not whole numbers answer 400 and change nothing; a scene file that cannot be read answers 409.
- `GET /scenes/<key>` serves the same page with links through the server (`/scenes/<key>/scenes/<scene>/take-NN.mkv`
  and the thumbnail), with byte ranges. Only take files and thumbnails directly under the video's `scenes/` folder
  are served (not `scenes.json`, the cue file, other files, `..` or a link that leaves the folder).
- A page opened by double click calls the server from the browser origin `null`. The server accepts that origin only
  for the star route (with its preflight) and for reading the scene list; every other write still needs the server's
  own address. The limit: any page in a sandboxed frame can also send a star to a registered video, which can only
  change which take is in front and is undone by one click.
- Script text: read from the cue file (`prompter.md` in the video folder, or the file named with `--cue`, which the
  registry remembers), only read, never changed. Directions (lines that start with a bracket), links and bold marks
  are left out. A scene whose heading is no longer in the script (title or number changed) is shown without text,
  which matches the "Script changed since recording" chip.
- The best take in front is `scene_store.choose_best` (the star, else the newest unless clearly worse). The gold star
  marks it, whether it was starred or not; a click on a star sends that take, and a click on the star that is
  already Lex's takes it off. Notes under a take come from its sound checks ("ended early", "long pause", "length
  off", "clean end"); a take that was not checked has none.
- Thumbnails: one picture per take at 320 px wide, a second in, made once and saved beside the take (`take-03.jpg`).
  Without ffmpeg, or when ffmpeg cannot read a take, that take shows a plain placeholder and the page still builds.
- "+ Record" is disabled and says "Coming with the Prompter". The page's server address is `127.0.0.1` and the port
  from `VIDEO_REVIEW_PORT` (default 8879) when the file is written; the server writes its own port when it rewrites
  the page after a star. The strip's total shows a `+` when a best take has no known length.
- Only the page's wording is new; colours and layout follow the Borumi-style mock-up. A browser that cannot play
  `.mkv` (Firefox) shows "This browser cannot play this file." under the player.
