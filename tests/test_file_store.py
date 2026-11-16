"""Checks for file_store.py, the one lock and safe save for every data and video folder. Temporary folders only."""
import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import file_store  # noqa: E402

HOLD = (
    'import sys, time; sys.path.insert(0, sys.argv[1]); import file_store\n'
    'with file_store.locked(sys.argv[2]):\n'
    '    print("holding", flush=True)\n'
    '    time.sleep(float(sys.argv[3]))\n')


class Locked(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.lock = self.folder / 'data.json.lock'

    def hold_in_another_program(self, seconds):
        holder = subprocess.Popen([sys.executable, '-I', '-c', HOLD, str(ROOT), str(self.lock), str(seconds)],
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), 'holding')
        return holder

    def test_a_lock_is_never_broken_however_old_its_file_looks(self):
        self.hold_in_another_program(60)
        old = time.time() - 3600
        os.utime(self.lock, (old, old))
        with self.assertRaises(file_store.Busy):
            with file_store.locked(self.lock, wait=0.3):
                self.fail('took a lock another program holds')
        self.assertTrue(self.lock.exists(), 'the other program\'s lock file was removed')

    def test_busy_says_what_to_do_and_can_be_another_error(self):
        self.hold_in_another_program(60)
        with self.assertRaises(file_store.Busy) as caught:
            with file_store.locked(self.lock, wait=0.1, busy='Comments are busy; try again.'):
                pass
        self.assertEqual(str(caught.exception), 'Comments are busy; try again.')
        self.assertIsInstance(caught.exception, ValueError)

        class Mine(Exception):
            pass

        with self.assertRaises(Mine):
            with file_store.locked(self.lock, wait=0.1, error=Mine):
                pass

    def test_a_program_that_dies_holding_the_lock_does_not_block_the_next(self):
        holder = self.hold_in_another_program(60)
        holder.kill()
        holder.wait()
        with file_store.locked(self.lock, wait=1):
            pass

    def test_threads_of_one_program_take_turns(self):
        inside, most = [0], [0]
        guard = threading.Lock()

        def worker():
            for _ in range(20):
                with file_store.locked(self.lock):
                    with guard:
                        inside[0] += 1
                        most[0] = max(most[0], inside[0])
                    time.sleep(0.001)
                    with guard:
                        inside[0] -= 1

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(most[0], 1)

    def test_each_file_has_its_own_lock(self):
        with file_store.locked(file_store.lock_for(self.folder / 'manifest.json')):
            with file_store.locked(file_store.lock_for(self.folder / 'comments.json'), wait=0.2):
                pass
        self.assertEqual(file_store.lock_for(self.folder / 'comments.json').name, 'comments.json.lock')

    def test_a_real_locking_failure_is_not_called_a_busy_lock(self):
        failure = OSError(errno.ENOLCK, 'No locks available')
        with mock.patch.object(file_store, '_try_lock', side_effect=failure):
            with self.assertRaises(OSError) as caught:
                with file_store.locked(self.lock):
                    pass
        self.assertNotIsInstance(caught.exception, file_store.Busy)

    def test_the_windows_lock_calls_retry_and_work_from_the_start_of_the_file(self):
        calls = []

        class FakeMsvcrt:
            LK_NBLCK, LK_UNLCK = 'lock', 'unlock'

            @staticmethod
            def locking(descriptor, mode, size):
                calls.append((mode, os.lseek(descriptor, 0, os.SEEK_CUR), size))
                if len(calls) == 1:
                    raise OSError(errno.EACCES, 'busy')

        self.lock.write_bytes(b'x')  # the handle opens for appending, so it starts at the end
        with mock.patch.object(file_store, 'fcntl', None), \
                mock.patch.object(file_store, 'msvcrt', FakeMsvcrt, create=True):
            with file_store.locked(self.lock):
                pass
        self.assertEqual(calls, [('lock', 0, 1), ('lock', 0, 1), ('unlock', 0, 1)])


class WriteText(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'data.json'

    def leftovers(self):
        return [p.name for p in self.path.parent.iterdir() if '.tmp' in p.name]

    def test_a_save_replaces_the_whole_file_and_leaves_no_temporary_file(self):
        self.path.write_text('old', encoding='utf-8')
        file_store.write_json(self.path, {'a': 'é'})
        self.assertEqual(file_store.read_json(self.path, None), {'a': 'é'})
        self.assertEqual(self.leftovers(), [])

    def test_a_failed_save_keeps_the_old_file_and_no_temporary_file(self):
        self.path.write_text('old', encoding='utf-8')
        with mock.patch.object(file_store.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                file_store.write_text(self.path, 'new')
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'old')
        self.assertEqual(self.leftovers(), [])

    def test_a_windows_rename_that_stays_blocked_gives_up_after_a_while(self):
        with mock.patch.object(file_store.os, 'replace', side_effect=PermissionError(13, 'in use')) as replace, \
                mock.patch.object(file_store.time, 'sleep'):
            with self.assertRaises(PermissionError):
                file_store.write_text(self.path, 'new')
        self.assertEqual(replace.call_count, file_store.WINDOWS_RETRIES)
        self.assertEqual(self.leftovers(), [])

    def test_every_save_has_its_own_temporary_name(self):
        names = []
        real = os.replace

        def spy(source, target):
            names.append(Path(source).name)
            return real(source, target)

        with mock.patch.object(file_store.os, 'replace', side_effect=spy):
            file_store.write_text(self.path, 'one')
            file_store.write_text(self.path, 'two')
        self.assertEqual(len(set(names)), 2)

    def test_a_missing_file_reads_as_the_default_and_a_broken_one_raises(self):
        self.assertEqual(file_store.read_json(self.path, []), [])
        self.path.write_text('{nope', encoding='utf-8')
        with self.assertRaises(ValueError):
            file_store.read_json(self.path, [])


if __name__ == '__main__':
    unittest.main()
