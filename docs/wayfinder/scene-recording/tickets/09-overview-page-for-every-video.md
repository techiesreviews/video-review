---
title: An overview page for every video
type: prototype (HITL)
status: closed
assignee: Lex + claude
blocked_by: []
---

## Question

Lex wants a static page per video that shows all its scenes and takes at a glance, "like borumi" (a video editor that
records each scene separately, keeps every take and builds the timeline). A first mockup is
[here](../prototypes/video-overview-page-mockup.html) (reworked in Borumi's layout after Lex shared a screenshot: [final mockup](../prototypes/video-overview-page-borumi-style.html)): scenes in order, takes as cards with length and a short note,
a gold star for the rough cut, a hollow star for the computer's favorite, a "script changed" flag, and the rough cut
as a strip on top.

Open: is this page only a read-only overview that opens by double click (a file saved in the video's folder after
every take), or the working page where Lex also changes stars and sends the rough cut on (video-review)? Likely both
read the same small file. Lex decides.

## Resolution (2026-11-13)

Layout, from Lex's Borumi screenshot: one card per scene with its script text, a dashed "+ Record" tile that opens the
Prompter on that scene, the take in the rough cut in front with a gold star, older takes folded under "Archived (n)",
the computer's favorite as a hollow star, a "script changed" chip, and the rough-cut strip on top.
See the [final mockup](../prototypes/video-overview-page-borumi-style.html).

Options put to Lex: (1) a read-only overview that opens by double click, saved in the video's folder and updated after
every take (recommended), (2) the same page does everything. Lex: "1 it would be nice I am still able to pick things up
if needed, star different videos if needed".

Read as: option 1, plus starring. One page per video, one small file behind it:

- The page is saved in the video's folder, updated after every take, and opens by double click, also when nothing else
  is running (read-only then: stars are greyed out with a short note).
- When the video-review server is running on the same computer, the same page lets Lex pick up a different take as the
  best (the star). The change goes into the small per-video file, so the Prompter, the page and the rough cut agree.
- Sending the best takes on to the rough cut stays in video-review (see
  [How takes show up in video-review](07-how-takes-show-up-in-video-review.md)).

"Star different videos" was read as "star different takes". Lex can correct this.
