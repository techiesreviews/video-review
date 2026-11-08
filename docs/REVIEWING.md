# Review through the transcript

1. Open a video from your computer. In the library you can search titles and transcript words, add a video,
   or archive and restore a review entry. Archiving never deletes the recording.
2. Import an existing word-timed transcript, such as HappyScribe JSON. Import does not buy or start transcription.
   Paragraph-only timestamps cannot be used as invented word timings.
3. Click a word to seek. Drag across words, or Shift-click, to select a phrase. Selecting a pause stays paused.
   Follow playback keeps the current word in view; search finds words in the current transcript.
4. Selecting words opens a slim edit bar right next to them, with one text box, ready to type. **Delete** is the first
   button, then the text box and the small x. Typing shows a grey suggestion from your recent notes; Tab takes it and
   Enter saves the note for your agent. Each request appears in the comment list immediately. Clicking a comment seeks
   the video and transcript. Esc (or the small x) clears the selection.
5. **Delete** (or Backspace/Delete with the text box empty) makes a reversible preview decision. After a delete the next
   words are selected; Ctrl+Z undoes it and selects the words again. A note typed but not sent when Delete is clicked
   is saved with the cut, and Ctrl+Z puts it back in the box, unless a note has been started on another selection; that
   note and its selection are kept, and the cut's note stays in its comment under Resolved. Deleted words stay in the
   transcript, struck through. This changes preview playback, not the original video file or a Resolve timeline.
6. Connect an editing agent using the private connection prompt. Keep the tab open. The agent can read comments,
   request the transcript, and apply approved changes to its local editable project. Export the cut list when needed.
7. For a replacement render, keep the same review entry and file name. Render to a temporary name, preserve
   the old review copy under a backup name, and only then rename the completed render into place. The agent
   announces actual duration and source ranges with `open_video`. It resolves notes only after checking the fix.

The hosted app remembers the selected video and agent connection on refresh. A changed render invalidates
the old browser copy. If the browser cannot reopen the file, choose the new copy in the waiting entry.

Download review backups regularly. They include notes and transcripts, not video bytes. Disconnect agents
revokes the session's tokens. Never commit connection tokens, footage, transcripts or private review backups.

## The edit bar

- Type a note and press Enter: it is saved as a request for the agent and shown in the comment list. Grey text
  completes it from your recent notes and the quick phrases; **Tab** (or →) accepts it, so Tab never inserts anything.
- Backspace or Delete in an **empty** box removes the selected words from the cut, like a video editor. With text
  in the box they only edit the text. Space in an empty box plays and pauses. Esc clears the text, then the selection.
- The bar says in one line whether an agent is attached. With none, the note is still saved and waits for it.
  The offline app knows because `mcp_server.py` refreshes `agent-seen.json` in the data folder every 15 seconds while it runs,
  so after the agent stops the bar can still say it is attached for up to 45 seconds.

## Listen and edit through the words

The app starts with the picture hidden, larger transcript text, and playback controls pinned below the words.
Use **Show video** to bring the picture back. The view is remembered after refresh. **Follow playback** keeps
the current word or long-pause marker vertically centred; switch it off to browse freely. Selecting words
pauses playback. The **Pauses ≥** setting chooses which approximate word-timing gaps to show.

When **Waveform** is enabled, it replaces the ordinary seek bar in the bottom playback controls. With no
selection it spans the whole video; selecting a passage opens a close-up and a small full-video overview.
Click the overview to jump elsewhere. Turning Waveform off brings the ordinary seek bar back. Comment
markers remain available on the active playback surface.

Hold **Ctrl** and scroll over the waveform to zoom in or out around the playhead. Scrolling up zooms in;
scrolling down zooms out. This changes only the view, never the cut or playback position. **View → Fit video**
returns to the full recording. The overview stays available while zoomed in.

1. Press **Build waveform** to decode the loaded video's audio locally in the browser. No upload, paid service,
   or new transcription is involved. Use an MP4 review copy if the browser cannot decode the original format.
2. Select a word, phrase or pause. The waveform shows the proposed removal, including the normal breath margins
   for a pause. Change **View** to zoom the time window.
3. Drag the purple start or orange end handle, or enter the two times below the waveform. Arrow keys on a
   handle move one video frame; Shift+Arrow moves five. These adjustments replace the automatic margins.
4. **Hear cut** plays the surrounding audio and skips the proposed removal, without saving that audition.
5. **Delete** in the edit bar saves the preview decision (on a pause it shortens it) and its review request immediately.
   Undo restores it. Export the cut list to apply those decisions to the same editable Resolve timeline.

Waveform heights are scaled for readability; this is not a loudness meter. Pauses are measured between
approximate transcript word times, so listen to the waveform boundaries before accepting a cut. This editor
does not destructively rewrite media or automatically change a Resolve project.

## Experience the cut before exporting

Removals are still previews: playback jumps over them, and the original video file and any Resolve timeline are
not touched. These controls let you hear and feel the result first.

- **Play whole cut** plays from the first kept frame with every removal skipped. If you are in the Original
  transcript view, it switches back to the edited one. **Space** plays and pauses wherever you are, including
  after clicking a word. Enter selects a focused word, and Shift+Space extends a selection.
- **Cuts** is one on/off switch among the player controls. On (the default) plays the cut: removals are skipped.
  Off plays the original, straight through every removal. It reads **Cuts on** or **Cuts off** and is remembered.
  It works from the keyboard (Space or Enter) and takes effect while playing. **Play whole cut** switches it on.
  **Hear cut** always skips the range you are trying. The words you removed stay struck through in the transcript
  either way.
- The timecodes, seek bar, comment times and pins all use the review file's own clock, which is not the
  original recording's. Comments are always saved in that time.
- Every join is faded for 15 ms out and back in, like the native audio fades in the Resolve cut. It applies to
  every jump, including Hear cut. The fade starts early, so it does not hold back the jump, and it only hides
  clicks. It cannot repair a clipped word. It is fixed and has no control.

A short silence at a join is the browser finding its place in the file, and it is not in the final cut. The picture can
also show a removed frame or two just before the jump. Review copies with frequent keyframes cut that silence
a lot. The measurements are in [`docs/agents/evidence/browser-cut-preview.md`](agents/evidence/browser-cut-preview.md).

## Ask the agent to change something

Select words or a pause, choose **Ask agent**, write what should change, and choose **Send to agent**. For a single
moment or a range with no words selected, write in the comment box and choose **Ask agent** instead of Comment.
The request appears in the comment list marked **For agent**, with its quote and frames. You can edit, reply to or
delete it like any comment. Nothing changes until your connected agent acts on it.

### For agents

`list_comments`, `get_comment` and, on the hosted app, `wait_for_comments` return these requests with an
`agentRequest` field:

```json
{"type": "change", "instruction": "Tighten this and keep the laugh", "quote": "keep the laugh",
 "wordIds": ["W5", "W6", "W7"], "firstWordId": "W5", "lastWordId": "W7", "startFrame": 750, "endFrame": 780,
 "sourceSpans": [{"sourceStartFrame": 1050, "sourceEndFrame": 1080, "sourceStartMs": 35000, "sourceEndMs": 36000}],
 "transcriptRevision": "r1"}
```

`instruction` is always the comment's current text. `startFrame` and `endFrame` are frames in the review file
(`endFrame` is null for a single frame), and `sourceSpans` are frames in the original footage. `quote` is null and
`wordIds` is empty when the moment has no words. The stored `editRequest` has type `change` and keeps the quote, word
IDs and frames from when it was asked. Other request types are unchanged and have no `agentRequest`.

`get_transcript` returns the reviewer's current preview removals as `draftEdits`:
`{id, startMs, endMs, wordIds, quote, kind, commentId}`, in review-file milliseconds, with `kind` either `words` or
`pause`. They are not in the rendered file until an agent applies them. The hosted app reads them from the open tab.

## See the original words and restore a passage

The transcript always shows every original word. Words deleted in this review stay in place, struck through and dimmed,
and pauses keep their original length (a shortened pause shows both lengths). Click **Restore** beside any removal, or
select deleted words and press **Restore** in the edit bar (the first button reads Restore on words that are all deleted);
other cuts remain. Playback and the exported cut list still skip the deleted words.

The attached transcript may also contain an `original` section with the full source recording's word timings,
duration and source identity. Its word IDs must match those in the derived review transcript. This section is
private review data and survives saving, backups and refresh; it is not a new transcription.

With that source attached, the transcript also shows blocks labeled **Removed in Resolve**, including
missing speech and trimmed pauses. These blocks use the original recording's clock. **Restore in Resolve**
creates a timestamped restoration request with that source range. Click a missing word, then Shift-click another,
to request only that phrase. Search includes these original words.

Already rendered-out material is absent from the current MP4: showing its text cannot play it or immediately
reinsert its sound. The agent restores it in the same Resolve timeline from the source recording, checks the
new joins, and replaces the review render. Review-clock positions and original-clock source anchors stay separate.
