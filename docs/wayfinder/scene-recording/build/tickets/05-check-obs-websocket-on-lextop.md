---
title: Check OBS remote control on LexTop
type: task (HITL)
status: open
assignee: Lex
blocked_by: []
---

## What

Finds out whether the Prompter can start and stop OBS and tell it where to save each take. This unblocks the next
slice (the Prompter's record light). It is the open list in
[Check OBS on LexTop](../../tickets/04-check-obs-on-lextop.md). Needs LexTop, on, with OBS open.

Lex does the two settings himself (they hold a password); an agent on LexTop can run the rest:

1. In OBS: Tools, WebSocket Server Settings: switch the server on, note the port, set a password. The password stays
   with Lex and in his own browser; it is never written in a file, ticket or chat.
2. Check, with a throwaway script that is not committed, using the password Lex types in:
   - OBS answers on `127.0.0.1` at that port;
   - a page opened from the live Prompter (https) can connect to it (browsers sometimes block this);
   - OBS can set the recording folder and the file name before a take starts, and start and stop a recording;
   - how many audio tracks a recording has (and whether the mic is on its own track).
3. Write the answers (yes or no, with the OBS version) into this ticket's `## Done`.

## Done when

- The four answers above are in `## Done`, with no password or token anywhere.
- If an answer is "no", the ticket says what the fallback is (for example OBS saves to `D:\Stream` and a helper
  moves each file afterwards).
- The map's "Not yet specified" is updated and the Prompter tickets are written from the answers.
