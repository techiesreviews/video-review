# Video Review

A local video review app for precise editing feedback. Play a video, step through frames, mark a millisecond timestamp or range, and save comments for a later editing pass.

The UI is buildless HTML, CSS and JavaScript. The server uses only Python's standard library. No database, npm install, account or Resolve installation is required.

## Run

Requires Python 3.11 or newer and a browser that supports the video's codec.

```sh
python server.py
```

Open **http://127.0.0.1:8879**. The server binds only to loopback.

On Windows, double-click **Open Video Review.cmd**. The launcher looks for an installed Python interpreter and can also use Resolve's bundled Python. Set `VIDEO_REVIEW_PYTHON` to an interpreter path to choose one explicitly.

For another port or data directory:

```sh
python server.py --port 8880 --data-dir /path/to/private-review-data
```

The Windows launcher uses `VIDEO_REVIEW_PORT` and `VIDEO_REVIEW_DATA_DIR` if set.

## Add your videos

The server creates an empty `data/` directory on first run. Configure `data/manifest.json` using [the example](examples/manifest.example.json). This directory is ignored by Git.

Each entry describes one exact video version:

- `id`: a unique stable ID, such as `episode-1-v3`. Use a new ID for a new edit.
- `title` and `label`: the display name and version label.
- `path`: an absolute path to a local video; Windows JSON paths need escaped backslashes, or use forward slashes.
- `fps` and `durationMs`: frame rate and duration in milliseconds.
- `fingerprint`: a version identity or content hash that travels with exported comments.
- `keepRanges`: retained `[startFrame, endFrame)` ranges from the original source, in edited playback order. For an uncut 60-second video at 30 fps, use `[[0, 1800]]`.

Videos stay in their original locations. Do not overwrite a video's media or mapping while reusing its ID: old comments must stay tied to the version they describe.

## Review controls

- Space / K: play or pause.
- Left / right, or comma / period: step one frame.
- J / L: move five seconds.
- N: pause and start a note at the current position.
- I / O: set the start and end of a range.
- Ctrl+Enter in the comment field: save the note.

Times accept `hh:mm:ss.mmm`, `mm:ss.mmm`, or seconds. Turn off **Snap to frame** to jump to an arbitrary millisecond. Comments preserve the entered milliseconds; picture precision remains limited to the video frame rate (about 33.3 ms at 30 fps).

## Use the comments for editing

Notes save immediately to `data/comments.json`. Ask your coding agent to read this file and process the open comments. Saving a note does not automatically change a video.

**Export comments** downloads JSON with the exact video version, timestamps, frame numbers and original-source ranges. A range that crosses a cut maps to multiple source spans.

To print all saved open notes with source mappings:

```sh
python export_saved_comments.py
```

Use **Mark done** after checking a correction. **Reopen** returns a note to the edit queue. Original-version comments keep their timebase when a newer cut is added.

## Repository contents

`server.py` serves the app, configured video files and comment API. `app.js`, `index.html` and `style.css` implement the review UI. `examples/` contains sanitized starter configuration. The Windows launcher is optional.

Local manifests, comments, logs, media and generated exports are excluded by `.gitignore`. Before pushing, review `git status` to check that only application source is staged.

The UI can use online web fonts, with system-font fallbacks. Video data and review comments are stored locally. This is a single-user local tool, not an authenticated internet service.

## Development checks

```sh
python tests/test_server.py
```

The tests cover local video range requests, comment persistence, timestamp validation and origin checks using a temporary data directory.
