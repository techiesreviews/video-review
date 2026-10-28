# Review through the transcript

1. Open a video from your computer. In the library you can search titles and transcript words, add a video,
   or archive and restore a review entry. Archiving never deletes the recording.
2. Import an existing word-timed transcript, such as HappyScribe JSON. Import does not buy or start transcription.
   Paragraph-only timestamps cannot be used as invented word timings.
3. Click a word to seek. Drag across words, or Shift-click, to select a phrase. Selecting a pause stays paused.
   Follow playback keeps the current word in view; search finds words in the current transcript.
4. Choose a quick request such as clipped, more room, or remove. The request appears in the comment list
   immediately. Edit its note in the field below the options. Clicking a comment seeks the video and transcript.
5. Remove from cut (or Delete) makes a reversible preview decision. Undo restores it; Show removed exposes
   omitted words. This changes preview playback, not the original video file or a Resolve timeline.
6. Connect an editing agent using the private connection prompt. Keep the tab open. The agent can read comments,
   request the transcript, and apply approved changes to its local editable project. Export the cut list when needed.
7. For a replacement render, keep the same review entry and file name. Render to a temporary name, preserve
   the old review copy under a backup name, and only then rename the completed render into place. The agent
   announces actual duration and source ranges with `open_video`. It resolves notes only after checking the fix.

The hosted app remembers the selected video and agent connection on refresh. A changed render invalidates
the old browser copy. If the browser cannot reopen the file, choose the new copy in the waiting entry.

Download review backups regularly. They include notes and transcripts, not video bytes. Disconnect agents
revokes the session's tokens. Never commit connection tokens, footage, transcripts or private review backups.

## Listen and edit through the words

The app starts with the picture hidden, larger transcript text, and playback controls pinned below the words.
Use **Show video** to bring the picture back. The view is remembered after refresh. **Follow playback** keeps
the current word or long-pause marker vertically centred; switch it off to browse freely. Selecting words
pauses playback. The **Pauses ≥** setting chooses which approximate word-timing gaps to show.

When **Waveform** is enabled, it replaces the ordinary seek bar in the bottom playback controls. With no
selection it spans the whole video; selecting a passage opens a close-up and a small full-video overview.
Click the overview to jump elsewhere. Turning Waveform off brings the ordinary seek bar back. Comment
markers remain available on the active playback surface.

1. Press **Build waveform** to decode the loaded video's audio locally in the browser. No upload, paid service,
   or new transcription is involved. Use an MP4 review copy if the browser cannot decode the original format.
2. Select a word, phrase or pause. The waveform shows the proposed removal, including the normal breath margins
   for a pause. Change **View** to zoom the time window.
3. Drag the purple start or orange end handle, or enter the two times below the waveform. Arrow keys on a
   handle move one video frame; Shift+Arrow moves five. These adjustments replace the automatic margins.
4. **Hear cut** plays the surrounding audio and skips the proposed removal, without saving that audition.
5. **Remove from cut** or **Shorten pause** saves the preview decision and its review request immediately.
   Undo restores it. Export the cut list to apply those decisions to the same editable Resolve timeline.

Waveform heights are scaled for readability; this is not a loudness meter. Pauses are measured between
approximate transcript word times, so listen to the waveform boundaries before accepting a cut. This editor
does not destructively rewrite media or automatically change a Resolve project.

## See the original words and restore a passage

**Original transcript** is always available above the words. It shows review removals with strike-through text,
including their original pause lengths. Click **Restore** beside any individual removal; other cuts remain.
You can listen to these removed parts because they still exist in the loaded review file. **Edited transcript**
returns to preview playback with those removals skipped.

The attached transcript may also contain an `original` section with the full source recording's word timings,
duration and source identity. Its word IDs must match those in the derived review transcript. This section is
private review data and survives saving, backups and refresh; it is not a new transcription.

With that source attached, Original transcript also shows blocks labeled **Removed in Resolve**, including
missing speech and trimmed pauses. These blocks use the original recording's clock. **Restore in Resolve**
creates a timestamped restoration request with that source range. Click a missing word, then Shift-click another,
to request only that phrase. Search includes these original words while this view is open.

Already rendered-out material is absent from the current MP4: showing its text cannot play it or immediately
reinsert its sound. The agent restores it in the same Resolve timeline from the source recording, checks the
new joins, and replaces the review render. Review-clock positions and original-clock source anchors stay separate.
