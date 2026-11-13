---
title: Where takes live and how they are named
type: grilling (HITL)
status: closed
assignee: unassigned
blocked_by: [03-what-counts-as-a-scene, 04-check-obs-on-lextop]
---

## Question

One folder per video with one folder per scene, or a flat folder with tagged names? How are scene, take and
best written (names, a small data file)? Where do raw OBS files go, and how does the video folder in the chat
repo, `D:\Ready for Edit\<slug>` and video-review point at the same takes?

## Resolution (2026-11-13)

Lex: "10. yes / 11. yes, if that can be automated, and also make sure that also happens when recording. / 12. yes,
perhaps create a static page for it for each video as an overview? Like borumi".

- Each video's takes sit under its raw-files folder on LexTop, one folder per scene:
  `D:\Ready for Edit\<video>\scenes\02-what-it-really-costs\take-03.mkv`. The file name also says scene and take
  (`scene-02-what-it-really-costs-take-03.mkv`) so a file outside its folder still says what it is.
- OBS saves straight into the scene folder under that name. The Prompter sets the folder and name in OBS before each
  take, so no file is moved afterwards. It has to happen by itself at record time, in the one-file-per-take style
  and the long-recording style (where the one long file goes in the video's folder with its markers beside it).
  `D:\Stream` stays for everything that is not a take.
- Scene list, star, the computer's favorite and the markers live in one small file per video next to the takes,
  kept up to date by video-review (it already runs on LexTop). No new program.
- Lex also asked for a static overview page for every video, "like borumi". See
  [An overview page for every video](09-overview-page-for-every-video.md).
