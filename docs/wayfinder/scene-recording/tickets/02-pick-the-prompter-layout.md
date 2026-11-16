---
title: Pick the Prompter layout
type: prototype (HITL)
status: closed
assignee: Lex + claude
blocked_by: []
---

## Question

How should recording controls sit in the Prompter without stopping it from being a prompter? Five clickable
mockups in the Prompter's own look: [open them](../prototypes/prompter-recording-round1-five-layouts.html)
(throwaway, in-memory; each switches between "one file per take" and "one long recording + markers").

1. **In the bar:** Rec, scene and take, and a best star next to today's buttons.
2. **Read / Record switch:** Read is today's Prompter; Record adds the controls.
3. **Top strip:** controls in the already-read area near the camera.
4. **Scene rail:** a thin left strip of scenes with a dot per take, gold for best.
5. **Keys and lamp:** no buttons, a corner lamp and a scene/take tag; keys, clicker or foot pedal.

Lex picks one, a mix, or asks for changes. The answer also says which of these controls must work without
touching the screen.

## Resolution (2026-11-13)

Decided with Lex over four rounds. His words: "I like the idea of the top strip. and the scene rail. and 5, can we
iterate more. I would like it to be as simple as possible. while having the functionality. I don't want a lot of
extra distractions"; then "C is best, can we use the forward and backward button to skip through scenes, and
clicking the dim light to start recording, or create a new take when pressing it"; then "Only stops version is
the best. Keep it dim though, and add a star for the newest version remove saved, best etc as extra info";
then "That is nice".

The chosen layout is [the final mockup](../prototypes/prompter-recording-chosen.html) (throwaway, in-memory):

- A small dim light with "S2 · T3" at the top. The light is the button: click to start a take, click again to
  stop it. It only stops (it does not start a new take). It goes red and shows a timer while recording.
- A star next to the tag marks the best take. The newest take carries it by default. No "saved" or "best" text.
- The Prompter's previous/next buttons and arrow keys skip scenes (a take in progress is stopped and kept first).
  R does the same as clicking the light.
- A scene rail on the left edge is hidden until the mouse goes there. It shows the scenes, a dot per take, and
  lets Lex pick an older take and star it.
- The Prompter stays a prompter: nothing else is added on screen. Today's bar is unchanged.
- Works for both recording styles (one file per take, one long recording with markers): only the file side differs.

Assumption to confirm in [What counts as a scene](03-what-counts-as-a-scene.md): the scene is the section the
Prompter is on.
