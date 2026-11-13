# Scene recording: build

First slice of [the map](../map.md): takes that already sit in a video's folder get registered, checked for
sound, shown on one overview page per video, and the best take of each scene feeds the rough cut. Nothing here
needs the Prompter or OBS yet; those come in the next slice, after the OBS check.

## Rules

- Plain English in every file, comment, commit and PR text. This repo is public: no tokens, no personal data.
- Everything runs through `scripts/agents/kit` (`kit doctor` first, `kit slice`, `kit pr`). Checks:
  `bash scripts/agents/run-tests.sh` (Python and Node tests, no installs).
- Free tools only: ffmpeg and ffprobe for lengths, thumbnails and sound checks. No paid transcript
  (HappyScribe) runs unless Lex asks for it for one video.
- Scene and take names, folders and the star rules are decided; do not change them: see
  [What counts as a scene](../tickets/03-what-counts-as-a-scene.md),
  [Who marks the best take](../tickets/05-who-marks-the-best-take.md),
  [Where takes live](../tickets/06-where-takes-live-and-how-they-are-named.md).
- Never touch script, prompter or recording-note content. Tests use their own fixture files.
- The Prompter and the OBS control are in the next slice and live in the Prompter's own project.

## Waves

| Wave | Ticket | Who |
| --- | --- | --- |
| 1 | [The per-video scene file](tickets/01-scene-file.md) | builder |
| 1 | [Check OBS on LexTop](tickets/05-check-obs-websocket-on-lextop.md) | Lex, on LexTop (any time) |
| 2 | [Register takes and check their sound](tickets/02-register-takes-and-check-sound.md) | builder |
| 2 | [The overview page](tickets/03-overview-page.md) | builder |
| 3 | [Rough cut from the best takes](tickets/04-rough-cut-from-best-takes.md) | builder |

## Next slice (after the OBS check)

The Prompter's record light and OBS control, the scene rail and scene skipping, long-recording markers, moving
files from LexTop to this computer, and pushing the rough cut into the Resolve timeline. They become tickets
when the OBS check answers what is possible; see "Not yet specified" in the map.
