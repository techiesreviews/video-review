"""The one way any file in a data folder or a video folder is locked and saved.

Two rules, for every program (the web server, the MCP server, the take intake and the scripts):

  locked(lock_file): one program or thread at a time, using a lock the operating system holds on a small lock file.
      The system lets go when the program ends, even after a crash, so a lock is never stale, never broken after a
      timeout, and no program can remove another's. Each data file has its own lock, so a slow job on one file does
      not hold up the others. Locks do not nest: a thread that already holds a lock must not ask for it again.
  write_text(path, text): a temporary file with its own name in the same folder, then a rename into place. A reader
      sees the old file or the new one, never half of it; a failed save leaves the old file and no temporary file;
      and on Windows a rename that a reader blocks for a moment is tried again.

Only the standard library, and locking that works on Linux, macOS and Windows.
"""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import time
import uuid

try:
    import fcntl
except ImportError:  # Windows (Git Bash)
    fcntl = None
    import msvcrt

LOCK_WAIT_SECONDS = 10  # how long locked() waits for another program before giving up
WINDOWS_RETRIES = 10
# What "someone else holds the lock" looks like: flock says EAGAIN or EWOULDBLOCK, Windows locking says EACCES
# or EDEADLOCK.
LOCK_BUSY_ERRNOS = {code for code in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES,
                                      getattr(errno, 'EDEADLK', None), getattr(errno, 'EDEADLOCK', None)) if code}


class Busy(ValueError):
    """Another program held the lock for longer than the wait."""


def retry_on_windows(action):
    """A reader holding the file open can make a rename or an open fail briefly on Windows; try again."""
    for attempt in range(WINDOWS_RETRIES):
        try:
            return action()
        except PermissionError:
            if attempt == WINDOWS_RETRIES - 1:
                raise
            time.sleep(0.05)


def _try_lock(handle):
    if fcntl:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)


def _unlock(handle):
    if fcntl:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def locked(lock_file, wait=None, busy=None, error=Busy):
    """Hold the lock on lock_file (made if missing; it stays in the folder) for the length of the with block.

    Waits up to `wait` seconds (LOCK_WAIT_SECONDS by default) for another program, then raises `error(busy)`. A real
    locking failure (for example a drive without locks) is raised as it is, not called busy."""
    lock_file = Path(lock_file)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + (LOCK_WAIT_SECONDS if wait is None else wait)
    with open(lock_file, 'a+b') as handle:
        while True:
            try:
                _try_lock(handle)
                break
            except OSError as failure:
                if failure.errno not in LOCK_BUSY_ERRNOS:
                    raise
                if time.monotonic() > deadline:
                    raise error(busy or f'{lock_file} is held by another program; try again in a moment.') from None
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)


def lock_for(path):
    """The lock file that guards one data file: its name with .lock added, next to it."""
    path = Path(path)
    return path.with_name(path.name + '.lock')


def write_text(path, text):
    """Save a whole text file safely (see the module notes)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp')
    try:
        with open(temporary, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        retry_on_windows(lambda: os.replace(temporary, path))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_json(path, data):
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False))


def read_text(path):
    """A whole text file; FileNotFoundError when there is none."""
    return retry_on_windows(lambda: Path(path).read_text(encoding='utf-8'))


def read_json(path, default):
    """A JSON file, or default when there is none. A file that is not valid JSON raises ValueError."""
    try:
        return json.loads(read_text(path))
    except FileNotFoundError:
        return default
