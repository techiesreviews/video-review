---
title: How a take starts and stops
type: grilling (HITL)
status: closed
assignee: Lex + claude
blocked_by: []
---

## Question

Who starts and stops a take, and what does a take become on disk? Options put to Lex: (1) one file per
take with an OBS helper that names and files it; (2) Prompter buttons that drive OBS; (3) one long
recording with markers for scene and take times.

## Resolution (2026-11-13)

Lex: "All three. For the prompter, I would like to prototype that so that I have a clear idea on how that
would look, let's create 5 prototypes. One in the bar directly, one for switching modes, and 3 others. The
problem is that the prompter also needs to stay a prompter. Also add the long recording with markers".

- All three ways are in scope: an OBS helper that files each take (the first slice), Prompter buttons, and one
  long recording with markers.
- The Prompter side is not designed yet: Lex wants five mockups first, and the Prompter must stay a
  prompter. See [Pick the Prompter layout](02-pick-the-prompter-layout.md).
