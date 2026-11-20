"""Local Whisper large-v3 on the GPU for one recording. No upload, no credits.

Run through tools/transcribe-local.ps1, which picks the venv that has stable-ts
and CUDA PyTorch (~/.cache/video-acut-venv, in the user folder).

Writes two files next to each other in --out-dir:
  <name>.large-v3.json   raw stable-ts result (same format as
                         input/2026-11-01-script-cut/large-v3-gpu-30s.json)
  <name>.words.json      word-timed transcript for Video Review set_transcript:
                         {words:[{id,text,startMs,endMs}],revision,label,cuts:[]}

Nothing already there is overwritten. A rerun that finds the raw file but no words file (the words save failed)
makes the words file from the raw file without transcribing again. A run holds the raw file's lock while it works,
so a second run for the same output stops at once, and a killed run leaves nothing in the way of the next one.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import file_store  # noqa: E402
import media_tools  # noqa: E402

SAMPLE_RATE = 16000
DECODE_TIMEOUT_S = 3600


def load_audio(path, start, duration):
    """Decode any media file to 16 kHz mono float32 with ffmpeg."""
    import numpy as np

    args = ['-nostdin', '-hide_banner', '-loglevel', 'error']
    if start:
        args += ['-ss', str(start)]
    args += ['-i', str(path)]
    if duration:
        args += ['-t', str(duration)]
    args += ['-vn', '-ac', '1', '-ar', str(SAMPLE_RATE), '-f', 's16le', '-']
    try:
        result = media_tools.run('ffmpeg', args, DECODE_TIMEOUT_S)
    except media_tools.MediaToolError as error:
        sys.exit(str(error))
    if result.returncode != 0:
        sys.exit(f'ffmpeg could not read {path}: {result.stderr.decode("utf-8", "replace").strip()}')
    raw = result.stdout
    if not raw:
        sys.exit(f'No audio decoded from {path}')
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def review_words(result, offset_s):
    """Flatten stable-ts segments to Video Review words in milliseconds."""
    words = []
    previous = -1
    for segment in result['segments']:
        for w in segment.get('words') or []:
            text = (w.get('word') or '').strip()
            if not text:
                continue
            start = round((w['start'] + offset_s) * 1000)
            end = round((w['end'] + offset_s) * 1000)
            start = max(start, previous, 0)
            end = max(end, start)
            words.append({'id': f'w{len(words) + 1:05d}', 'text': text, 'startMs': start, 'endMs': end})
            previous = start
    return words


def transcribe(args, raw_path):
    """Whisper on the recording; its raw result is saved at raw_path. The caller holds raw_path's lock.

    The result is saved under another name and renamed into place, so raw_path only ever holds a whole result: a save
    that fails halfway, or a run that is killed, leaves no file there. (A run killed during the save itself may leave
    its <name>.<process number>.saving.json behind; that file is in no later run's way.)"""
    import stable_whisper
    import torch

    if args.device == 'cuda' and not torch.cuda.is_available():
        sys.exit('CUDA is not available in this Python. Use the video-acut venv or pass --device cpu (slow).')
    t0 = time.time()
    audio = load_audio(args.media, args.start, args.duration)
    print(f'Audio: {len(audio) / SAMPLE_RATE:.1f} s decoded', flush=True)
    model = stable_whisper.load_model(args.model, device=args.device)
    print(f'Model {args.model} loaded on {args.device} ({time.time() - t0:.0f} s)', flush=True)
    result = model.transcribe(audio, language=args.language, word_timestamps=True,
                              verbose=False, fp16=args.device == 'cuda')
    saving = raw_path.with_name(f'{raw_path.stem}.{os.getpid()}.saving.json')  # stable-ts adds .json to other names
    try:
        result.save_as_json(str(saving))
        file_store.retry_on_windows(lambda: os.replace(saving, raw_path))
    except BaseException:
        saving.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('media', type=Path, help='recording (.mkv, .mp4, .wav, ...)')
    parser.add_argument('--out-dir', type=Path, help='default: next to the recording')
    parser.add_argument('--start', type=float, default=0.0, help='seconds into the recording to start')
    parser.add_argument('--duration', type=float, default=0.0, help='seconds to transcribe (0 = to the end)')
    parser.add_argument('--model', default='large-v3')
    parser.add_argument('--language', default='en')
    parser.add_argument('--device', default='cuda')
    args = parser.parse_args()

    if not args.media.is_file():
        sys.exit(f'Not a file: {args.media}')
    out_dir = args.out_dir or args.media.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.media.stem
    if args.start or args.duration:
        name += f'.{int(args.start)}s-{int(args.duration) if args.duration else "end"}'
    raw_path = out_dir / f'{name}.{args.model}.json'
    words_path = out_dir / f'{name}.words.json'
    t0 = time.time()
    # The system lets go of the lock when this program ends, even when it is killed, so it never blocks a later run.
    with file_store.locked(file_store.lock_for(raw_path), wait=0, error=SystemExit,
                           busy=f'Another run is writing {raw_path} now. Wait for it to finish; nothing was changed.'):
        if words_path.exists():
            sys.exit(f'{words_path} already exists; nothing is overwritten. Move both files for a new transcription, '
                     'or only the words file to make it again from the raw file, or pick another --out-dir.')
        if raw_path.exists():
            print(f'{raw_path} is there from an earlier run: making the words file from it, no new transcription.',
                  flush=True)
        else:
            transcribe(args, raw_path)
        try:
            words = review_words(json.loads(file_store.read_text(raw_path)), args.start)
        except (ValueError, KeyError, TypeError, AttributeError):
            sys.exit(f'{raw_path} is not a finished transcript (a run that stopped may have left it). '
                     'Move or delete it, then run again.')
        if not words:
            sys.exit(f'No words recognised. Raw result kept at {raw_path}')
        transcript = {
            'revision': f'local-{args.model}-{time.strftime("%Y%m%d-%H%M%S")}',
            'label': f'Local {args.model} (rough cut, not checked by ear)',
            'source': str(args.media),
            'offsetMs': round(args.start * 1000),
            'words': words,
            'cuts': [],
        }
        try:  # a words file made by hand meanwhile is kept
            file_store.write_new_text(words_path, json.dumps(transcript, ensure_ascii=False, indent=1))
        except FileExistsError:
            sys.exit(f'{words_path} was made meanwhile; it is kept. Raw result at {raw_path}')
    print(f'{len(words)} words in {time.time() - t0:.0f} s', flush=True)
    print(f'Raw:   {raw_path}')
    print(f'Words: {words_path}')


if __name__ == '__main__':
    main()
