"""How loud a video is, every 5 ms, measured once and kept, so the page can cut words at the real pauses (TOOLS-463).

ffmpeg decodes the sound as one 16 kHz channel and hands it over piece by piece, so a recording of hours never sits
in memory whole. Each 5 ms step gets the level of the 10 ms from it, in half dB below full scale, one byte (0 to 200),
as web/cut-boundary.js reads it (envelopeFromBytes). The result is kept in the data folder under the video's file
version, so a replaced file is measured again and an unchanged one is read back at once.
"""
import hashlib
import math
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media_tools  # noqa: E402

RATE = 16000
HOP = RATE * 5 // 1000          # 80 samples: one step
FULL = 32768.0 ** 2 * 2 * HOP   # a 10 ms window (two steps) at full scale
# Decoding takes far less than the recording's length; this only stops a stuck ffmpeg.
TIMEOUT_SECONDS = 3 * 3600
_locks = {}
_locks_guard = threading.Lock()
_sumprod = getattr(math, 'sumprod', None) or (lambda a, b: sum(x * y for x, y in zip(a, b)))


class NoSound(Exception):
    """The file has no sound to measure, or ffmpeg could not read it."""


def level_byte(energy, samples):
    """The level of `samples` 16-bit samples with this summed square, in half dB below full scale, 0 to 200."""
    if not samples:
        return 200
    db = 10 * math.log10(energy / (samples * 32768.0 ** 2) + 1e-12)
    return max(0, min(200, round(-db * 2)))


def measure(path):
    """The loudness bytes of the media file at path. Raises media_tools.ToolMissing or ToolTimedOut, or NoSound."""
    steps = []
    left = bytearray()

    def take(chunk):
        left.extend(chunk)
        whole = len(left) // (HOP * 2) * (HOP * 2)
        if not whole:
            return
        samples = memoryview(bytes(left[:whole])).cast('h')
        for at in range(0, len(samples), HOP):
            block = samples[at:at + HOP]
            steps.append(_sumprod(block, block))
        del left[:whole]

    code, errors = media_tools.stream('ffmpeg', ['-v', 'error', '-nostdin', '-i', str(path), '-vn', '-ac', '1',
                                                 '-ar', str(RATE), '-f', 's16le', '-acodec', 'pcm_s16le', '-'],
                                      TIMEOUT_SECONDS, take)
    if code != 0 or (not steps and not left):
        raise NoSound((errors.strip().splitlines() or ['no sound'])[-1])
    tail = memoryview(bytes(left[:len(left) // 2 * 2])).cast('h')
    if len(tail):
        steps.append(_sumprod(tail, tail))
    counts = [HOP] * len(steps)
    if len(tail):
        counts[-1] = len(tail)
    # Each step's window is that step and the next one, as the page measures decoded samples.
    return bytes(level_byte(steps[i] + (steps[i + 1] if i + 1 < len(steps) else 0),
                            counts[i] + (counts[i + 1] if i + 1 < len(steps) else 0)) for i in range(len(steps)))


def cached(data_dir, path, version):
    """The loudness bytes for the file at path in this file version, measured now or read back from the data folder.
    One measurement per file at a time: a second request waits for the first and reads its result."""
    folder = Path(data_dir) / 'loudness'
    name = hashlib.sha256(str(Path(path).resolve()).encode('utf-8')).hexdigest()[:24]
    target = folder / f'{name}-{hashlib.sha256(str(version).encode()).hexdigest()[:12]}.bin'
    with _locks_guard:
        lock = _locks.setdefault(name, threading.Lock())
    with lock:
        try:
            return target.read_bytes()
        except OSError:
            pass
        result = measure(path)
        folder.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=folder, prefix=name, suffix='.part')
        try:
            with os.fdopen(handle, 'wb') as out:
                out.write(result)
            os.replace(temp, target)
        except OSError:
            Path(temp).unlink(missing_ok=True)
            return result
        # An older version of the same file is no longer needed.
        for old in folder.glob(f'{name}-*.bin'):
            if old != target:
                old.unlink(missing_ok=True)
        return result
