"""Local Whisper large-v3 on the GPU for one recording. No upload, no credits.

Run through tools/transcribe-local.ps1, which picks the venv that has stable-ts
and CUDA PyTorch (~/.cache/video-acut-venv, in the user folder).

Writes two files next to each other in --out-dir:
  <name>.large-v3.json   raw stable-ts result (same format as
                         input/2026-11-01-script-cut/large-v3-gpu-30s.json)
  <name>.words.json      word-timed transcript for Video Review set_transcript:
                         {words:[{id,text,startMs,endMs}],revision,label,cuts:[]}
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('media', type=Path, help='recording (.mkv, .mp4, .wav, ...)')
    ap.add_argument('--out-dir', type=Path, help='default: next to the recording')
    ap.add_argument('--start', type=float, default=0.0, help='seconds into the recording to start')
    ap.add_argument('--duration', type=float, default=0.0, help='seconds to transcribe (0 = to the end)')
    ap.add_argument('--model', default='large-v3')
    ap.add_argument('--language', default='en')
    ap.add_argument('--device', default='cuda')
    args = ap.parse_args()

    if not args.media.is_file():
        sys.exit(f'Not a file: {args.media}')
    out_dir = args.out_dir or args.media.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.media.stem
    if args.start or args.duration:
        name += f'.{int(args.start)}s-{int(args.duration) if args.duration else "end"}'
    raw_path = out_dir / f'{name}.{args.model}.json'
    words_path = out_dir / f'{name}.words.json'
    for p in (raw_path, words_path):
        if p.exists():
            sys.exit(f'{p} already exists. Move it or pick another --out-dir; nothing is overwritten.')
    # Claim the raw file now (stable-ts saves over whatever is there): a run started meanwhile stops here.
    try:
        raw_path.open('x').close()
    except FileExistsError:
        sys.exit(f'{raw_path} was made by another run meanwhile; it is kept, nothing was overwritten.')
    saving = raw_path.with_name(f'{raw_path.name}.{os.getpid()}.tmp')

    try:
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
        # Saved beside the claim, then renamed over it: a save that fails halfway leaves no half file.
        result.save_as_json(str(saving))
        file_store.retry_on_windows(lambda: os.replace(saving, raw_path))
    except BaseException:
        saving.unlink(missing_ok=True)
        if raw_path.stat().st_size == 0:  # only the empty claim: a rerun must not find it in the way
            raw_path.unlink()
        raise

    raw = json.loads(raw_path.read_text(encoding='utf-8'))
    words = review_words(raw, args.start)
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
    try:  # another run that wrote the words meanwhile keeps its file
        file_store.write_new_text(words_path, json.dumps(transcript, ensure_ascii=False, indent=1))
    except FileExistsError:
        sys.exit(f'{words_path} was made by another run meanwhile; it is kept. Raw result at {raw_path}')
    print(f'{len(words)} words in {time.time() - t0:.0f} s', flush=True)
    print(f'Raw:   {raw_path}')
    print(f'Words: {words_path}')


if __name__ == '__main__':
    main()
