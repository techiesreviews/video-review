---
title: Check OBS on LexTop
type: task (HITL)
status: closed
assignee: unassigned
blocked_by: []
---

## Question

What is OBS on LexTop really set up as? Record: the OBS version, whether obs-websocket is on (port; the
password is never written down here), the recording folder and file name format, the file type, how many
audio tracks, and the list of scenes. Also whether a recording can be started, split and renamed by a script.
Resolved when the facts are written in the answer.

## Resolution (2026-11-13)

Answered by Lex from memory, no screenshots. Lex: "8. is on lexTop, this thing will also be open on lextop / 9. Latest
obs version, d:/stream".

- The Prompter and OBS run on the same computer, LexTop. A call from the Prompter page to OBS stays on that
  computer (the Prompter already calls a local program, CamFrame, on 127.0.0.1).
- OBS is the latest version (number not stated). Recordings go to `D:\Stream`. The one sample name,
  `2026-11-01 21-34-57.mkv`, looks like OBS's default name; not verified.
- Not yet known, and the first build ticket checks them on LexTop before anything else: whether OBS's WebSocket
  server is switched on and its port (the password stays in Lex's own browser, never in a file or chat), whether
  the OBS WebSocket can set the recording folder and file name before a take, whether a browser page on https can
  call it, and how many audio tracks are recorded.
