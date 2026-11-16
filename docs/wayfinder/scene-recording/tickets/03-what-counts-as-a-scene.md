---
title: What counts as a scene
type: grilling (HITL)
status: closed
assignee: Lex + claude
blocked_by: []
---

## Question

Is a scene one numbered section of the script ("2 · Title"), an OBS scene, or something Lex names? Sections
have no permanent id today, so what keeps "scene 3" the same after a script edit? Does a screen share inside
a section make it its own scene?

## Resolution (2026-11-13)

Four questions put to Lex (with this ticket and [Who marks the best take, and when](05-who-marks-the-best-take.md)).
Lex: "1 yes, we can see if we need to break that up later / 2. yes, we need to adjust when it happens / 3. yes /
4. the computer can also help pick it's favorite".

- A scene is one numbered script section (for example "2 · What it really costs"). It is the section the Prompter
  is on, so the previous/next buttons move between scenes. Lex splits a section later only if it proves needed.
- At the first record of a video the Prompter saves a scene list (number and title). Files are named like
  `scene-02-what-it-really-costs-take-03`. Old takes stay with the scene they were recorded for.
- When the script changes after recording, the review page flags a scene that no longer matches, and Lex and the
  computer adjust it at that moment (no automatic re-linking).
- A long section, with a screen share or demo, stays one scene. To make it shorter Lex adds a heading in the script.
