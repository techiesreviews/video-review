---
label: wayfinder:map
title: Scene-by-scene recording — every take kept, tagged and the best one in the rough cut
charted: 2026-11-13
tracker: local markdown (tickets in tickets/); board card TOOLS-226
---

## Destination

Lex records a video scene by scene. Every take is saved, named by scene and take, and tagged
(scene, take, best). Lex marks the best take of each scene. The best takes go into the rough cut on
the video-review page. This works with the script editor and the Prompter, records with OBS, and is
as automatic as possible, using the tools Lex already has. The map is done when the way is clear and
the first slice has build tickets.

## Notes

- Lex's words (2026-11-13): "a scene by scene recording automation that makes things structured works
  with the scripter and prompter and allow me to organise recordings with the tools that I already
  have. ... keep video's per scene together and tagged correctly, which scene, which take, and which
  one was best. The best one will get put in the video for rough cutting. I would like to automate
  this as much as possible, together with obs for recording."
- The Prompter must stay a prompter first.
- Read before any script, prompter or recording-note work:
  `chat/youtube/AGENTS.md` and the playbook in `chat/youtube/shared/taste/scripts.md`.
- Cutter rules that already exist (last complete take wins, pauses, Lex listens to every join):
  `fleet/inbox/video-cutter-rules-2026-11-10.md`.
- LexTop (Windows, runs OBS and Resolve) is often offline. A ticket that needs it says so.

## What exists today (2026-11-13, read only)

- **Script editor:** saves `script.md`, `prompter.md` (the cue file, copy `recording-notes.md`), B-roll
  files and a prompter link into the video folder. A section is a numbered heading ("2 · Title"); it has
  no permanent id.
- **Prompter:** scrolls the cues by section, has keys and a show clock, and already sends "face big/small"
  signals to a local camera tool (CamFrame). No record button, no takes, no OBS link.
- **OBS (LexTop):** one sample file `D:\Stream\2026-11-01 21-34-57.mkv`. No notes on scenes, file names,
  or remote control. Not yet checked on the machine.
- **video-review:** one recording, one transcript, one cut list, then the review page and a Resolve
  timeline. The cutter picks the last complete take inside one long recording. Nothing knows scenes,
  per-scene folders or "best take".

## Decisions so far

- [How a take starts and stops](tickets/01-how-a-take-starts-and-stops.md): all three ways are in: an OBS
  helper that files each take, Prompter buttons, and one long recording with markers.

- [Pick the Prompter layout](tickets/02-pick-the-prompter-layout.md): a dim light that is the record button, a
  star on the best take, previous/next skip scenes, a hidden scene rail; the Prompter stays a prompter. The
  [final mockup](prototypes/prompter-recording-chosen.html) shows it.
- [How Lex checks earlier takes](tickets/08-how-lex-checks-earlier-takes.md): both ways, the video-review page
  first, then playing a take on the main screen.

- [What counts as a scene](tickets/03-what-counts-as-a-scene.md): a scene is a numbered script section; a scene
  list saved at the first record; files named scene-NN-title-take-NN; script changes adjusted when they happen.
- [Who marks the best take, and when](tickets/05-who-marks-the-best-take.md): Lex decides, newest starred by
  default; the computer suggests a favorite from free sound checks, shown only on the review page; unstarred
  scenes use the newest unless it is clearly worse.

- [Check OBS on LexTop](tickets/04-check-obs-on-lextop.md): the Prompter and OBS run on LexTop, recordings go to
  `D:\Stream`, OBS is current; the remaining OBS facts are checked in the first build step.
- [Where takes live and how they are named](tickets/06-where-takes-live-and-how-they-are-named.md): one folder per
  scene under the video's raw-files folder, OBS saves straight there with the right name, tags in one small file
  per video, kept by video-review.

- [An overview page for every video](tickets/09-overview-page-for-every-video.md): one Borumi-style page per video
  ([mockup](prototypes/video-overview-page-borumi-style.html)): scenes with their takes, the best in front, older
  takes archived. A saved file that opens by double click; the star can be changed from it while video-review runs.

## Open tickets (the frontier)

- [How takes show up in video-review](tickets/07-how-takes-show-up-in-video-review.md): the scene view is decided
  (the overview page); what is left is how the best takes go on to the rough cut.

## Not yet specified

- How the long-recording mode cuts a scene out of the markers (marker file, who cuts, when).
- How files travel from LexTop to ubulex, and where the helper runs.
- Transcripts per take (HappyScribe cost) or only for the best take.
- The handoff of best takes to the existing cutter and the Resolve timeline.
- Paid transcript comparison with the script for the computer's favorite (only when Lex asks for a video).
- Re-takes of one scene after the rough cut has started.
- Playing an older take on the main screen from the Prompter rail (decided, built after the review page).

## Out of scope

- Editing beyond the rough cut, colour, music, and publishing.
