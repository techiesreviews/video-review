"""The one place that finds and runs the media tools, ffmpeg and ffprobe, for every script.

Finding a tool: the PATH first, then (on Windows) the user PATH saved in the registry, which a window opened before
the install does not have yet, then the folders the usual installs use, then video-review's own bin folder. On
Windows a tool counts with one of the PATHEXT endings (.exe in practice); elsewhere it must be runnable. A found
tool is remembered for the rest of the run; a missing one is looked for again next time, so installing ffmpeg
while the server runs is enough.

From a shell, `python media_tools.py ffprobe` prints the tool's path, or the missing-tool message with exit code 1
(the Windows launcher asks this way).

Running a tool: run() takes the tool's name, its arguments and a time limit, captures what it prints and never
opens a console window on Windows. A missing tool raises ToolMissing, whose message names the tool and the command
that installs it; a tool that runs past its limit raises ToolTimedOut. A tool that runs and fails is not an error
here: the caller reads the return code and what it printed.
"""
import glob
import os
from pathlib import Path
import re
import subprocess
import sys

TOOLS = ('ffmpeg', 'ffprobe')
OWN_BIN = Path(__file__).resolve().parent / 'bin'
# Where installs put the tools outside the PATH, on a Mac or Linux. A program started from a desktop shortcut
# may not have the shell's PATH.
POSIX_FOLDERS = ('/opt/homebrew/bin', '/usr/local/bin', '/usr/bin')
# Windows folders, with %NAME% filled in from the environment. winget links the tools into Links, or leaves them in
# its package folder (Gyan.FFmpeg_...\ffmpeg-7.1-full_build\bin); Chocolatey and Scoop have their own folders.
WINDOWS_FOLDERS = (r'%LOCALAPPDATA%\Microsoft\WinGet\Links', r'%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*\*\bin',
                   r'%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*\bin', r'%ProgramFiles%\ffmpeg\bin',
                   r'%ProgramData%\chocolatey\bin', r'%USERPROFILE%\scoop\shims', r'%SystemDrive%\ffmpeg\bin')
DEFAULT_PATHEXT = '.COM;.EXE;.BAT;.CMD'

_found = {}


class MediaToolError(Exception):
    """A media tool could not be used: missing, or it ran too long."""


class ToolMissing(MediaToolError):
    """The tool is not installed, or this computer cannot find it."""

    def __init__(self, tool, windows=None, found=None, reason=None):
        self.tool = tool
        because = f' ({reason})' if reason else ''
        problem = f'{tool} at {found} could not be started{because}' if found else \
            f'{tool} is not installed (or this computer cannot find it)'
        super().__init__(f'{problem}. {install_hint(windows)}')


class ToolTimedOut(MediaToolError):
    def __init__(self, tool, seconds):
        self.tool = tool
        super().__init__(f'{tool} did not finish within {seconds:g} seconds and was stopped.')


def install_hint(windows=None):
    """How to install ffmpeg (which brings ffprobe) on this kind of computer."""
    windows = os.name == 'nt' if windows is None else windows
    if windows:
        return ('Install FFmpeg, which includes ffmpeg and ffprobe: open PowerShell, run "winget install Gyan.FFmpeg", '
                'then try again.')
    if sys.platform == 'darwin':
        return 'Install FFmpeg, which includes ffmpeg and ffprobe: run "brew install ffmpeg" in Terminal.'
    return 'Install FFmpeg, which includes ffmpeg and ffprobe: run "sudo apt install ffmpeg" (or your system\'s installer).'


def _registry_user_path():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
        return winreg.QueryValueEx(key, 'Path')[0]


def _expand(text, environ):
    """%NAME% filled in from environ (any case, as on Windows); a name it does not have is left as it is."""
    names = {key.upper(): value for key, value in environ.items()}
    return re.sub(r'%([^%]+)%', lambda m: names.get(m.group(1).upper(), m.group(0)), text)


def _windows_folders(environ, user_path):
    folders = environ.get('PATH', '').split(';')
    try:
        folders += _expand(user_path() or '', environ).split(';')
    except (OSError, ImportError):
        pass  # no user PATH saved, or no registry: the PATH and the install folders still count
    for pattern in WINDOWS_FOLDERS:
        filled = _expand(pattern, environ)
        if '%' in filled:
            continue  # the variable is not set on this computer
        filled = filled.replace('\\', os.sep)
        folders += sorted(glob.glob(filled), key=_newest_first) if '*' in filled else [filled]
    return folders


def _newest_first(folder):
    """Sort key that puts the highest version first: ffmpeg-10.0 before ffmpeg-9.1, by number, not by text."""
    return [-int(n) for n in re.findall(r'\d+', folder)] + [0]


def _lookup(tool, environ, windows, user_path):
    if windows:
        endings = [e for e in (environ.get('PATHEXT') or DEFAULT_PATHEXT).split(';') if e]
        folders = _windows_folders(environ, user_path or _registry_user_path) + [str(OWN_BIN)]
        # Windows ignores case; the lower-case ending is also tried so a copy on a case-sensitive disk is found too.
        names = list(dict.fromkeys(tool + spelling for ending in endings for spelling in (ending, ending.lower())))
    else:
        folders = environ.get('PATH', '').split(os.pathsep) + [*POSIX_FOLDERS, str(OWN_BIN)]
        names = [tool]
    for folder in folders:
        folder = folder.strip().strip('"')
        if not folder:
            continue
        for name in names:
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate) and (windows or os.access(candidate, os.X_OK)):
                return candidate
    return None


def find(tool, environ=None, windows=None, user_path=None):
    """The full path of the tool, or ToolMissing. environ, windows and user_path (a function that returns the
    registry's user PATH) stand in for this computer's in tests; a lookup with none of them is remembered."""
    remember = environ is None and windows is None and user_path is None
    if remember and tool in _found:
        return _found[tool]
    windows = os.name == 'nt' if windows is None else windows
    path = _lookup(tool, os.environ if environ is None else environ, windows, user_path)
    if path is None:
        raise ToolMissing(tool, windows)
    if remember:
        _found[tool] = path
    return path


def forget():
    """Drop the remembered paths, so the next find looks again."""
    _found.clear()


def run(tool, arguments, timeout, text=False):
    """Run ffmpeg or ffprobe with the arguments and capture what it prints. text=True reads the output as UTF-8
    (an odd byte becomes a replacement mark, never an error). Raises ToolMissing or ToolTimedOut."""
    command = [find(tool), *arguments]
    options = {'encoding': 'utf-8', 'errors': 'replace'} if text else {}
    try:
        return subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), **options)
    except FileNotFoundError:
        forget()
        raise ToolMissing(tool) from None
    except subprocess.TimeoutExpired:
        raise ToolTimedOut(tool, timeout) from None
    except OSError as error:
        # There, but it cannot start (a damaged download, a file Windows will not run, or no handles left).
        forget()
        # Windows leaves its own placeholder in the text: "%1 is not a valid Win32 application."
        reason = str(error.strerror or error).replace('%1', os.path.basename(command[0])).rstrip('.')
        raise ToolMissing(tool, found=command[0], reason=reason) from None


def main(argv):
    """Print the path of the tool named first in argv (ffprobe when none is), or why it cannot be found."""
    try:
        print(find(argv[0] if argv else 'ffprobe'))
    except ToolMissing as error:
        print(error)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
