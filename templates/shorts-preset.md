> **Draft: waiting for Lex to confirm v5 is the template.**
> Taken from `renders/video01-shorts-test-v1` … `v5` (hidden links Short), EDITING-LESSONS.md "Shorts feedback (2026-11-08)" and RUNBOOK.md section 6. Nothing here is approved until Lex says v5 is the one.

# Shorts preset (9:16)

## Frame

- 1080 × 1920, 30 fps, H.264 (CRF 22), AAC, `+faststart`.
- Source: the finished long video master, not a new recording. No cloned voice.
- Crop from the 4K master: `crop=1215:2160:1320:0` then `scale=1080:1920` (v5; the presenter centred). Check the crop per Short: the face must stay in the middle third.
- The first frame is the cover: the opening hook card is on screen from frame 0.

## Length

- Under 60 seconds. v2–v5 are 44.7 s; aim for 40–55 s.
- Two or three continuous source spans at most. Each join gets 15 ms audio fades and the A-cut quality gate (normal-speed join audition, soft retake check) before it counts as clean.
- Remove dead pauses (Lex flagged one near 0:44 in v1) but keep natural breaths.

## Captions (v5 values, set by Lex's "too small on a phone" note on v1)

- Arial Bold **86 px** on a 1920 px tall frame (v1 used 55 px: too small).
- White text, centred at **y = 1247** (box top 1190, box 880 × 114, rounded 22 px corners, dark navy `#0B1026` at about 90 % opacity).
- At most 3 words and 25 characters per caption. A new caption starts after a gap over 0.6 s.
- A caption ends at the next caption's start or 60 ms after its last word, whichever is earlier. No overlaps.
- Captions come from the word-timed transcript of the master and must be checked against the audio.

## Hook and section cards

- White rounded card 860 × 204 at x 110, y 205 (top of frame), 150 ms fade in and out.
- Small label: 32 px, brand blue `#316BBD`, capitals (for example "THE CLUE").
- Main line: 66 px, dark navy, one or two lines.
- Show a card on its spoken cue and hold it through the sentence; 2.5–4.5 s each. Do not cover the mouth.

## Safe areas (keep text and faces inside)

- Top 200 px: YouTube's title and status bar. Cards start at y 205.
- Bottom 480 px (y above 1440 only): channel name, description, subscribe button. Captions sit at y 1190–1304.
- Right 140 px: like, comment and share buttons. Open point: the v5 caption box is 880 px wide with 100 px each side, so its right edge sits 40 px inside the button column. Check on a phone; if it clashes, use an 800 px box.

## Ending

- It must feel finished: end on a complete sentence, never mid-thought.
- Last 3 s: a closing card that states the outcome (v5: "THE LIMIT / Entry method unknown").
- Upload CTA: "Watch the full WordPress security lesson", with the YouTube Related Video set to the long video.

## Before showing Lex

- Play it on a phone, not only on the monitor.
- Listen to both ends and every join at normal speed.
- Version numbers are review names, not approval to publish.
