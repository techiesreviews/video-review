# Video Review

Review a video through its words: seek from the transcript, select a phrase, remove it from a reversible
preview cut, or leave a timestamped request for an editing agent. The final editable cut can stay in
DaVinci Resolve. [Reviewing and applying changes](docs/REVIEWING.md) explains the workflow.

The library searches video titles and transcript phrases. Removing an entry archives it without deleting
the recording. Comments, transcripts and recordings stay private; this repository contains the app code.
The optional production workflow and per-video assets on the creator's workstation are separate from this public app.

Leave precise, timestamped comments on a video edit, like Loom, and let an AI agent work through them with you. Pause anywhere, type, press Enter; the comment is pinned to that exact frame. The agent reads your comments with frame numbers and original-source mapping, replies in the thread, asks when something is unclear, resolves what it fixed and announces new cuts.

There are two ways to run it. They share the same interface.

## Online: video.techies.tools

Open **https://video.techies.tools** in Chrome or Edge. No account, nothing to install.

- **Add video** (or drop a file on the page). It plays locally and is never uploaded. The app reuses a remembered file handle, or a private browser copy when available, after refresh. Browser permissions and storage limits can still require you to choose the file again.
- Comments are stored in this browser. **⋯ → Download backup** saves them; **Import backup** restores them in another browser.
- **Connect agent** copies a prompt for Claude Code or Codex with a private connection token. Paste it into the agent. The button shows the agent once it connects. Keep the tab open while the agent works: its changes go through your tab, like the [Native Site Editor](https://github.com/techiesreviews/native-site-editor)'s agents.
- **Disconnect agents** revokes every token and deletes what was shared with them.

What leaves your computer: only while an agent is connected, your tab shares comments (including selected transcript quotes and edit requests) and video details (titles, file names, sizes, frame rates) with this session's Cloudflare Durable Object so the agent can read them. A connected agent can request an attached transcript through `get_transcript`; full transcripts are not part of the automatic context updates. The agent's replies pass through the session. Video files never leave your computer. The agent works on its own local copy of the video (the same file name), so it should run on the computer that has the files.

The transcript's **Import transcript** button accepts existing HappyScribe word-level JSON or this workflow's
derived word document. It never starts a transcription. `set_transcript` lets an agent attach those same
timings through MCP; `get_transcript` reads them back. Browser backups include transcripts and removed
review entries. Removing a video only archives its review entry; the recording is not deleted.

The agent's tools: `list_videos`, `list_comments`, `get_comment`, `wait_for_comments` (watch for new comments and answers), `reply_to_comment`, `resolve_comment`, `reopen_comment`, `add_comment`, `register_video` (announce a different video; you are asked to open the file. Renders of review fixes replace the reviewed file under the same name, and the old file is renamed) and `open_video` (switch your tab to a version, at a time or on a comment). When the agent replaces the open video's file, your tab loads the new render at the same moment, without a page refresh. `list_videos` also tells the agent which version you have open and where your playhead is.

### Deploy your own

The online app is a Cloudflare Worker (`cloud/worker.js`) that serves `web/` and hosts the MCP endpoint at `/mcp`, with one Durable Object per browser session. It runs on Cloudflare's free plan.

```sh
npx wrangler login
npx wrangler deploy
```

`wrangler.jsonc` routes `video.techies.tools`; change the `routes` pattern to a domain in your Cloudflare account. `npx wrangler dev` runs it locally at http://127.0.0.1:8787.

## Offline: on this computer

### Run

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

### Add your videos

The server creates an empty `data/` directory on first run. Configure `data/manifest.json` using [the example](examples/manifest.example.json). This directory is ignored by Git.

Each entry describes one exact video version:

- `id`: a unique stable ID, such as `episode-1-v3`. Use a new ID for a new edit.
- `title` and `label`: the display name and version label.
- `path`: an absolute path to a local video; Windows JSON paths need escaped backslashes, or use forward slashes.
- `fps` and `durationMs`: frame rate and duration in milliseconds.
- `fingerprint`: a version identity or content hash that travels with exported comments.
- `keepRanges`: retained `[startFrame, endFrame)` ranges from the original source, in edited playback order. For an uncut 60-second video at 30 fps, use `[[0, 1800]]`.

Videos stay in their original locations. A fix to the same reviewed video uses the same entry and file name:
render to a temporary file, rename the old review copy as a backup, then move the finished file into place.
Announce its actual duration and source keep ranges with `open_video`; comments follow their stored source
anchors. Register a new entry only for a genuinely different video. Never replace the original recording.

### Leave comments

Commenting works like Loom: pause anywhere, type in **Leave a comment…** and press Enter. The comment is pinned to the exact frame under the playhead; the button shows where it will land (**Comment at 0:12**). Comments appear as markers on the scrubber and in the list on the right. Click either to jump back to that moment.

- C (or N): comment on the current frame.
- Space / K: play or pause. Left / right, or comma / period: step one frame. J / L: move five seconds.
- I then O: mark a range, then type the comment. Esc clears it.
- Click the time chip in the comment box to type an exact start and end. Times accept `h:mm:ss.mmm`, `m:ss.mmm` or seconds.
- Enter posts; Shift+Enter adds a new line.

Comments keep millisecond timestamps; picture precision remains limited to the video frame rate (about 33.3 ms at 30 fps). Hover a comment to edit or delete it, and use the check circle to resolve or reopen it.

### Let your AI editor work through the comments

`mcp_server.py` is an MCP server that gives your coding agent direct access to the review. It reads the same `data/` folder as the app, so the AI's replies appear under your comments within a few seconds and a toast says what changed. You can answer in the same thread with **Reply**.

Register it once with Claude Code (user scope makes it available in every project):

```sh
claude mcp add video-review --scope user -- python /path/to/video-review/mcp_server.py
```

Use the full path to your Python if `python` is not on your PATH. Add `--data-dir /path/to/data` after `mcp_server.py` if you use a custom data directory, and set `VIDEO_REVIEW_PORT` if the app is not on 8879. Then ask the agent something like *"Work through my open video review comments."*

The agent gets these tools:

| Tool | What it does |
| --- | --- |
| `list_videos` | Versions in the library with file paths, fps, duration and comment counts. |
| `list_comments` | Open (or resolved, or all) comments with timecodes, frame numbers, original-source frames and reply threads. |
| `get_comment` | One comment and its thread. |
| `reply_to_comment` | Ask you a question or report progress; the comment stays open. |
| `resolve_comment` | Mark a comment done with a short summary of what changed. |
| `reopen_comment` | Reopen a resolved comment, optionally with a reason. |
| `add_comment` | Flag a moment or range for you to check. |
| `register_video` | Add a different video under a new id so you can review it. Renders of review fixes replace the reviewed file instead (the old file is renamed), so the comments stay on one video. |
| `open_video` | Switch the open review page to a version, optionally at a time or on a comment (within a few seconds). After re-rendering a file in place, the agent passes the new `durationMs` and `keepRanges`. |

Writes from the browser and the MCP server go through a shared lock, so neither overwrites the other.

**Export** downloads JSON with the exact video version, timestamps, frame numbers and original-source ranges. A range that crosses a cut maps to multiple source spans. `python export_saved_comments.py` prints the open comments with source mappings without the MCP server.

Original-version comments keep their timebase when a newer cut is added.

## Repository contents

`web/` is the interface used by both versions; `web/review-core.js` holds the comment rules they share. `cloud/worker.js` is the online version's Worker and MCP endpoint. `server.py` serves the offline app, configured video files and comment API. `mcp_server.py` exposes the same comments to an AI editor, and `review_store.py` holds the storage both share. `examples/` contains sanitized starter configuration. The Windows launcher is optional.

Local manifests, comments, logs, media and generated exports are excluded by `.gitignore`. Before pushing, review `git status` to check that only application source is staged.

The UI can use online web fonts, with system-font fallbacks. Video data and review comments are stored locally. This is a single-user local tool, not an authenticated internet service.

## Development checks

```sh
python tests/test_server.py
python tests/test_mcp_server.py
```

The tests cover local video range requests, comment persistence, timestamp validation, origin checks and the MCP tools, using temporary data directories.
