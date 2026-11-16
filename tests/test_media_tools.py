"""Checks for media_tools.py, the one place that finds and runs ffmpeg and ffprobe.

Windows is tested on any computer with fakes: a made-up PATH, user PATH and install folders in a temporary folder,
holding empty files named like the real tools. Nothing here needs ffmpeg installed.
"""
import ast
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts/video'), str(ROOT)]
import media_tools  # noqa: E402

TOOLS = ('ffmpeg', 'ffprobe')


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoDirectCalls(unittest.TestCase):
    """Every script goes through media_tools; none starts ffmpeg or ffprobe itself."""

    def python_files(self):
        skip = {'tests', 'node_modules', '.git', '.scratch', '.venv', 'venv', '__pycache__'}
        for path in ROOT.rglob('*.py'):
            relative = path.relative_to(ROOT)
            if not skip & set(relative.parts) and relative != Path('media_tools.py'):
                yield relative

    @staticmethod
    def names_a_tool(node):
        return any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and Path(n.value.split()[0] if n.value.strip() else '_').stem.lower() in TOOLS
                   for n in ast.walk(node))

    def test_no_script_starts_ffmpeg_or_ffprobe_itself(self):
        found = []
        for relative in self.python_files():
            tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                # A command list that starts with the tool, wherever it is later passed.
                if isinstance(node, (ast.List, ast.Tuple)) and node.elts and self.names_a_tool(node.elts[0]):
                    found.append(f'{relative}:{node.lineno} command list')
                # A process call or a PATH lookup that names the tool anywhere in its arguments.
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and isinstance(node.func.value, ast.Name) \
                        and (node.func.value.id in ('subprocess', 'shutil', 'os')) \
                        and any(self.names_a_tool(arg) for arg in [*node.args, *node.keywords]):
                    found.append(f'{relative}:{node.lineno} {node.func.value.id}.{node.func.attr}')
        self.assertEqual(found, [], 'run ffmpeg and ffprobe through media_tools.run')

    def test_the_scripts_that_need_the_tools_use_the_module(self):
        for relative in ('review_store.py', 'scripts/video/take_intake.py', 'scripts/video/overview_page.py',
                         'scripts/video/cut-to-review.py'):
            tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
            imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            self.assertIn('media_tools', imported, relative)


class Folder(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)
        # The real own bin folder and fixed install folders of this computer stay out of every lookup here.
        for name, value in (('OWN_BIN', self.dir / 'video-review-bin'), ('POSIX_FOLDERS', ())):
            patcher = mock.patch.object(media_tools, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tool(self, *parts, executable=True):
        path = self.dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'')
        path.chmod(stat.S_IRWXU if executable else stat.S_IRUSR | stat.S_IWUSR)
        return path


class LinuxLookup(Folder):
    def find(self, tool, path):
        return media_tools.find(tool, environ={'PATH': path}, windows=False)

    def test_the_first_runnable_tool_on_the_path_is_found(self):
        first, second = self.tool('a', 'ffprobe'), self.tool('b', 'ffprobe')
        self.assertEqual(self.find('ffprobe', os.pathsep.join([str(first.parent), str(second.parent)])), str(first))

    def test_a_file_that_cannot_run_is_passed_over(self):
        self.tool('a', 'ffmpeg', executable=False)
        good = self.tool('b', 'ffmpeg')
        self.assertEqual(self.find('ffmpeg', os.pathsep.join([str(self.dir / 'a'), str(self.dir / 'b')])), str(good))

    def test_the_own_bin_folder_is_looked_in_after_the_path(self):
        own = self.tool('video-review-bin', 'ffmpeg')
        self.assertEqual(self.find('ffmpeg', str(self.dir / 'empty')), str(own))

    def test_the_usual_install_folders_are_looked_in_before_the_own_bin_folder(self):
        brew = self.tool('brew', 'ffmpeg')
        self.tool('video-review-bin', 'ffmpeg')
        with mock.patch.object(media_tools, 'POSIX_FOLDERS', (str(brew.parent),)):
            self.assertEqual(self.find('ffmpeg', str(self.dir / 'empty')), str(brew))

    def test_a_tool_that_is_nowhere_is_missing(self):
        with self.assertRaises(media_tools.ToolMissing) as caught:
            self.find('ffprobe', str(self.dir))
        self.assertEqual(caught.exception.tool, 'ffprobe')


class WindowsLookup(Folder):
    def find(self, tool, path='', user_path='', **environ):
        return media_tools.find(tool, environ={'PATH': path, **environ}, windows=True,
                                user_path=lambda: user_path)

    def test_the_path_is_split_on_semicolons_and_quotes_are_dropped(self):
        found = self.tool('Program Files', 'ffmpeg', 'bin', 'ffprobe.exe')
        path = f'{self.dir / "nothing"};"{found.parent}";'
        self.assertEqual(self.find('ffprobe', path), str(found))

    def test_pathext_decides_which_ending_wins(self):
        exe = self.tool('bin', 'ffmpeg.exe')
        self.tool('bin', 'ffmpeg.cmd')
        self.assertEqual(self.find('ffmpeg', str(exe.parent), PATHEXT='.COM;.EXE;.CMD'), str(exe))
        self.assertEqual(self.find('ffmpeg', str(exe.parent), PATHEXT='.CMD;.EXE'), str(exe.with_suffix('.cmd')))

    def test_without_pathext_the_usual_endings_are_tried(self):
        exe = self.tool('bin', 'ffmpeg.exe')
        self.assertEqual(self.find('ffmpeg', str(exe.parent)), str(exe))

    def test_a_file_without_an_ending_does_not_count(self):
        self.tool('bin', 'ffprobe')
        with self.assertRaises(media_tools.ToolMissing):
            self.find('ffprobe', str(self.dir / 'bin'))

    def test_the_user_path_from_the_registry_is_read_with_its_variables_filled_in(self):
        # winget adds its folder to the user PATH, which a window opened before the install does not have yet.
        found = self.tool('Users', 'lex', 'tools', 'ffprobe.exe')
        self.assertEqual(self.find('ffprobe', str(self.dir / 'old'), user_path=r'%HOMEDIR%\tools'.replace('\\', os.sep),
                                   HOMEDIR=str(self.dir / 'Users' / 'lex')), str(found))

    def test_the_winget_links_folder_is_found_without_the_path(self):
        found = self.tool('Local', 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe')
        self.assertEqual(self.find('ffmpeg', LOCALAPPDATA=str(self.dir / 'Local')), str(found))

    def test_the_winget_package_folder_is_found_without_the_path(self):
        found = self.tool('Local', 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe',
                          'ffmpeg-7.1-full_build', 'bin', 'ffprobe.exe')
        self.assertEqual(self.find('ffprobe', LOCALAPPDATA=str(self.dir / 'Local')), str(found))

    def test_the_newest_winget_package_version_wins(self):
        packages = ('Local', 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg_x')
        self.tool(*packages, 'ffmpeg-6.1-full_build', 'bin', 'ffmpeg.exe')
        newest = self.tool(*packages, 'ffmpeg-7.1-full_build', 'bin', 'ffmpeg.exe')
        self.assertEqual(self.find('ffmpeg', LOCALAPPDATA=str(self.dir / 'Local')), str(newest))

    def test_versions_are_compared_by_number_not_by_text(self):
        packages = ('Local', 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg_x')
        self.tool(*packages, 'ffmpeg-9.1-full_build', 'bin', 'ffmpeg.exe')
        newest = self.tool(*packages, 'ffmpeg-10.0-full_build', 'bin', 'ffmpeg.exe')
        self.assertEqual(self.find('ffmpeg', LOCALAPPDATA=str(self.dir / 'Local')), str(newest))

    def test_common_install_folders_are_found_without_the_path(self):
        cases = [(('Program Files', 'ffmpeg', 'bin'), {'ProgramFiles': str(self.dir / 'Program Files')}),
                 (('ProgramData', 'chocolatey', 'bin'), {'ProgramData': str(self.dir / 'ProgramData')}),
                 (('lex', 'scoop', 'shims'), {'USERPROFILE': str(self.dir / 'lex')}),
                 (('C', 'ffmpeg', 'bin'), {'SystemDrive': str(self.dir / 'C')})]
        for parts, environ in cases:
            with self.subTest(parts[-2]):
                found = self.tool(*parts, 'ffprobe.exe')
                self.assertEqual(self.find('ffprobe', **environ), str(found))
                found.unlink()

    def test_the_own_bin_folder_is_found_without_the_path(self):
        found = self.tool('video-review-bin', 'ffmpeg.exe')
        self.assertEqual(self.find('ffmpeg'), str(found))

    def test_the_path_comes_before_the_install_folders(self):
        on_path = self.tool('path', 'ffmpeg.exe')
        self.tool('Local', 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe')
        self.assertEqual(self.find('ffmpeg', str(on_path.parent), LOCALAPPDATA=str(self.dir / 'Local')), str(on_path))

    def test_a_registry_that_cannot_be_read_is_not_an_error(self):
        def broken():
            raise OSError('no user PATH value')
        found = self.tool('Local', 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe')
        self.assertEqual(media_tools.find('ffmpeg', environ={'LOCALAPPDATA': str(self.dir / 'Local')}, windows=True,
                                          user_path=broken), str(found))


class MissingMessage(unittest.TestCase):
    def test_the_message_names_the_tool_and_how_to_install_it_on_windows(self):
        text = str(media_tools.ToolMissing('ffprobe', windows=True))
        self.assertIn('ffprobe', text)
        self.assertIn('winget install Gyan.FFmpeg', text)

    def test_the_message_names_the_tool_and_how_to_install_it_elsewhere(self):
        for platform, command in (('darwin', 'brew install ffmpeg'), ('linux', 'sudo apt install ffmpeg')):
            with self.subTest(platform), mock.patch.object(media_tools.sys, 'platform', platform):
                text = str(media_tools.ToolMissing('ffmpeg', windows=False))
                self.assertIn('ffmpeg', text)
                self.assertIn(command, text)


class Running(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(media_tools, 'find', side_effect=lambda tool: f'/tools/{tool}')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_tool_found_is_run_with_the_arguments_and_the_time_limit(self):
        done = subprocess.CompletedProcess([], 0, b'out', b'')
        with mock.patch.object(media_tools.subprocess, 'run', return_value=done) as run:
            self.assertIs(media_tools.run('ffprobe', ['-v', 'error', 'x.mkv'], timeout=30), done)
        self.assertEqual(run.call_args.args[0], ['/tools/ffprobe', '-v', 'error', 'x.mkv'])
        self.assertEqual(run.call_args.kwargs['timeout'], 30)
        self.assertTrue(run.call_args.kwargs['capture_output'])
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_text_output_is_read_as_utf8_without_failing_on_odd_bytes(self):
        with mock.patch.object(media_tools.subprocess, 'run') as run:
            media_tools.run('ffmpeg', [], timeout=1, text=True)
        self.assertEqual((run.call_args.kwargs['encoding'], run.call_args.kwargs['errors']), ('utf-8', 'replace'))

    def test_a_tool_that_vanishes_before_it_starts_is_missing(self):
        with mock.patch.object(media_tools.subprocess, 'run', side_effect=FileNotFoundError), \
                self.assertRaises(media_tools.ToolMissing) as caught:
            media_tools.run('ffmpeg', [], timeout=1)
        self.assertEqual(caught.exception.tool, 'ffmpeg')

    def test_a_tool_that_is_there_but_cannot_start_is_named_with_its_path(self):
        with mock.patch.object(media_tools.subprocess, 'run', side_effect=OSError(193, 'not a valid application')), \
                self.assertRaisesRegex(media_tools.ToolMissing, r'ffprobe at /tools/ffprobe could not be started \(not a valid application\)'):
            media_tools.run('ffprobe', [], timeout=1)

    def test_a_tool_that_runs_too_long_is_stopped_with_a_clear_error(self):
        with mock.patch.object(media_tools.subprocess, 'run', side_effect=subprocess.TimeoutExpired('x', 5)), \
                self.assertRaisesRegex(media_tools.ToolTimedOut, 'ffprobe.*5'):
            media_tools.run('ffprobe', [], timeout=5)


class Finding(unittest.TestCase):
    def test_a_tool_found_once_is_not_looked_up_again(self):
        media_tools.forget()
        self.addCleanup(media_tools.forget)
        with mock.patch.object(media_tools, '_lookup', return_value='/x/ffmpeg') as lookup:
            self.assertEqual(media_tools.find('ffmpeg'), '/x/ffmpeg')
            self.assertEqual(media_tools.find('ffmpeg'), '/x/ffmpeg')
        self.assertEqual(lookup.call_count, 1)

    def test_a_missing_tool_is_looked_for_again_next_time(self):
        # Lex can install ffmpeg while the server runs; the next try finds it.
        media_tools.forget()
        self.addCleanup(media_tools.forget)
        with mock.patch.object(media_tools, '_lookup', side_effect=[None, '/x/ffmpeg']):
            with self.assertRaises(media_tools.ToolMissing):
                media_tools.find('ffmpeg')
            self.assertEqual(media_tools.find('ffmpeg'), '/x/ffmpeg')

    def test_a_remembered_tool_that_has_gone_is_looked_for_again(self):
        media_tools.forget()
        self.addCleanup(media_tools.forget)
        media_tools._found['ffmpeg'] = '/gone/ffmpeg'
        with mock.patch.object(media_tools.subprocess, 'run', side_effect=FileNotFoundError), \
                self.assertRaises(media_tools.ToolMissing):
            media_tools.run('ffmpeg', [], timeout=1)
        self.assertNotIn('ffmpeg', media_tools._found)


class CommandLine(unittest.TestCase):
    """python media_tools.py ffprobe, which the Windows launcher runs before it opens the page."""

    def answer(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = media_tools.main(argv)
        return code, out.getvalue().strip()

    def test_a_found_tool_prints_its_path(self):
        with mock.patch.object(media_tools, 'find', return_value='/tools/ffprobe') as find:
            self.assertEqual(self.answer(['ffprobe']), (0, '/tools/ffprobe'))
        find.assert_called_once_with('ffprobe')

    def test_a_missing_tool_prints_how_to_install_it_and_fails(self):
        with mock.patch.object(media_tools, 'find', side_effect=media_tools.ToolMissing('ffprobe', windows=True)):
            code, text = self.answer([])
        self.assertEqual(code, 1)
        self.assertIn('winget install Gyan.FFmpeg', text)

    def test_run_from_a_shell_a_missing_tool_exits_with_1(self):
        done = subprocess.run([sys.executable, str(ROOT / 'media_tools.py'), 'no-such-media-tool'],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn('no-such-media-tool is not installed', done.stdout)

    def test_the_launcher_asks_the_module(self):
        launcher = (ROOT / 'start-review.ps1').read_text(encoding='utf-8')
        self.assertIn("'media_tools.py'), 'ffprobe'", launcher)
        self.assertNotIn('ffprobe.exe', launcher)


class Callers(unittest.TestCase):
    """What each script says when the tool is missing or hangs."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)
        self.missing = mock.patch.object(media_tools, 'find', side_effect=lambda tool: (_ for _ in ()).throw(
            media_tools.ToolMissing(tool, windows=True)))

    def test_adding_a_video_without_ffprobe_says_how_to_install_it(self):
        import review_store
        video = self.dir / 'clip.mp4'
        video.write_bytes(b'x')
        with self.missing, self.assertRaisesRegex(ValueError, 'ffprobe.*winget install Gyan.FFmpeg'):
            review_store.add_local_video(self.dir, str(video))

    def test_a_video_ffprobe_fails_on_or_hangs_on_is_refused_plainly(self):
        import review_store
        video = self.dir / 'clip.mp4'
        video.write_bytes(b'x')
        for outcome in (subprocess.CompletedProcess([], 1, b'', b'bad'), media_tools.ToolTimedOut('ffprobe', 30)):
            with self.subTest(type(outcome).__name__):
                patch = mock.patch.object(media_tools, 'run', **(
                    {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}))
                with patch, self.assertRaisesRegex(ValueError, 'Could not read this video'):
                    review_store.add_local_video(self.dir, str(video))

    def test_the_take_intake_without_a_tool_says_how_to_install_it(self):
        import take_intake
        with self.missing, self.assertRaisesRegex(take_intake.ToolMissing, 'ffprobe.*winget install Gyan.FFmpeg'):
            take_intake.probe_media(self.dir / 'take-01.mkv')

    def test_a_take_the_tools_hang_on_is_left_for_the_next_run(self):
        import take_intake
        with mock.patch.object(media_tools, 'run', side_effect=media_tools.ToolTimedOut('ffprobe', 1)):
            self.assertIsNone(take_intake.probe_media(self.dir / 'take-01.mkv'))
            self.assertIsNone(take_intake.detect_silence(self.dir / 'take-01.mkv', 5.0))

    def test_cut_to_review_without_ffprobe_says_how_to_install_it(self):
        cut = _load('cut_to_review_media', 'scripts/video/cut-to-review.py')
        with self.missing, self.assertRaisesRegex(cut.Stop, 'ffprobe.*winget install Gyan.FFmpeg'):
            cut.ffprobe_json(self.dir / 'render.mp4', '-show_entries', 'format=duration')

    def test_cut_to_review_says_what_ffprobe_could_not_read(self):
        cut = _load('cut_to_review_media', 'scripts/video/cut-to-review.py')
        failed = subprocess.CompletedProcess([], 1, '', 'moov atom not found\n')
        with mock.patch.object(media_tools, 'run', return_value=failed), \
                self.assertRaisesRegex(cut.Stop, r'ffprobe could not read .*render.mp4: moov atom not found$'):
            cut.ffprobe_json(self.dir / 'render.mp4', '-show_entries', 'format=duration')

    def test_cut_to_review_names_a_hanging_ffprobe_and_its_file(self):
        cut = _load('cut_to_review_media', 'scripts/video/cut-to-review.py')
        with mock.patch.object(media_tools, 'run', side_effect=media_tools.ToolTimedOut('ffprobe', 120)), \
                self.assertRaisesRegex(cut.Stop, 'ffprobe did not finish within 120 seconds.*render.mp4'):
            cut.ffprobe_json(self.dir / 'render.mp4', '-show_entries', 'format=duration')


if __name__ == '__main__':
    unittest.main()
