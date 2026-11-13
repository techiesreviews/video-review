---
title: How takes show up in video-review
type: prototype (HITL)
status: closed
assignee: unassigned
blocked_by: []
---

## Question

How does the review page show scenes with their takes, let Lex change the best one, and send the best takes to
the rough cut? Mockup first. Today the page lists videos flat, one entry per recording.

Lex chose (2026-11-13, see [How Lex checks earlier takes](08-how-lex-checks-earlier-takes.md)) that this page is where he first compares takes of a scene and picks the best.

Update (2026-11-13): the scene view is now the [overview page](09-overview-page-for-every-video.md) (final mockup:
[Borumi-style](../prototypes/video-overview-page-borumi-style.html)), which video-review serves and also saves into
the video's folder. Naming and best-take answers are in tickets 05 and 06. What is left to decide: how the best takes
go on to the rough cut (a button, or automatic after the last scene).

## Resolution (2026-11-13)

Options put to Lex: (1) a "Send to rough cut" button after he has looked over the stars (recommended), (2) automatic:
the rough cut is rebuilt each time he stars a take. Lex: "2".

- The rough cut is rebuilt on its own whenever the best take of a scene changes, and when a new take of an unstarred
  scene arrives (the newest counts by default, see [Who marks the best take](05-who-marks-the-best-take.md)).
- No send button. The overview page shows the rough-cut strip, so Lex sees the result at once.
- The cutter's existing rules still apply (Lex listens to every join before it goes further).
- Still to specify, because it is built on this: what the rebuild touches in the Resolve timeline, and what happens when
  the cut has already been edited by hand (see "Re-takes after the rough cut has started" in the map).
