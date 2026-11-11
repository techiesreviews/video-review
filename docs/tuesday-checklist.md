# Tuesday recording checklist

Do this before you press record. Takes about 5 minutes.

## 1. Episode folder

Run once per episode:

```powershell
.\tools\new-episode.ps1 -Title "Video 02 - Who Changed Your WordPress Site"
```

It prints a path ending in `06 Source recordings\camera recordings`. Copy it.
Add `-WhatIf` first if you only want to see what it will make.

## 2. OBS

- Settings → Output → Recording Path: paste the path from step 1.
- Check the path in OBS is really the new episode, not last week's.

## 3. Disk space

- Check D: has room: about 1 GB per minute of recording, so keep at least 100 GB free.
- PowerShell: `Get-PSDrive D` (look at Free).

## 4. Mic

- Mic plugged in and picked in OBS (Audio Mixer moves when you talk).
- Say a sentence at normal volume: the level peaks in the yellow, not the red.
- Desktop audio muted unless you need it.

## 5. Camera

- Camera on and showing in OBS, in focus, face lit.
- Glasses clean, nothing odd in the background.

## 6. Prompter

- Prompter connected and showing the script for this episode.
- Scroll to the first line.

## 7. Take markers

- Before each retake, say a clear marker such as "again" or "take two", then pause for a second before you start.
- Do not start the new take in the middle of a sentence; go back to the start of the sentence.
- These markers help the cut find the take to keep.

## 8. Test clip

- Record 10 seconds, stop, play it back: sound and picture both there, file in the right folder.
- Then record for real.

## After recording

- Leave the .mkv files where OBS put them. They are the only copies.
- For a rough cut transcript without HappyScribe:

```powershell
.\tools\transcribe-local.ps1 "<path to the .mkv>"
```
