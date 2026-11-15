"""The data folder's locks and saves: no lost changes, no frozen server, no failed saves on Windows.

Each test names the bug from the architecture review (TOOLS-268) it guards against. All of them use temporary
folders; none touches a real data folder.
"""
import json
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
import review_store as store  # noqa: E402

# A second program that saves a re-rendered video's keep ranges, with a slow hash of the video file.
SLOW_RECUT = (
    'import sys, time\n'
    'sys.path.insert(0, sys.argv[1]); import review_store\n'
    'real = review_store.fingerprint\n'
    'def slow(path):\n'
    '    print("hashing", flush=True); time.sleep(float(sys.argv[3])); return real(path)\n'
    'review_store.fingerprint = slow\n'
    'review_store.update_render(review_store.data_dir(sys.argv[2]), "cut-v1", None, [[0, 30]])\n')
# A second program that adds comments one after the other.
ADD_COMMENTS = (
    'import sys\n'
    'sys.path.insert(0, sys.argv[1]); import review_store\n'
    'data = review_store.data_dir(sys.argv[2])\n'
    'for n in range(int(sys.argv[4])):\n'
    '    review_store.apply(data, {"action": "add", "videoId": "cut-v1", "startMs": n, "text": sys.argv[3] + "-" + str(n)})\n')


class DataFolder(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = store.data_dir(self.temp.name)
        self.media = self.data / 'cut.mp4'
        self.media.write_bytes(bytes(range(256)))
        self.other = self.data / 'other.mp4'
        self.other.write_bytes(bytes(range(128)))
        store.write(self.data / 'manifest.json', [
            {'id': 'cut-v1', 'title': 'Cut', 'label': 'v1', 'path': str(self.media), 'fps': 30, 'durationMs': 2000,
             'fingerprint': 'x', 'keepRanges': [[0, 60]]},
            {'id': 'other-v1', 'title': 'Other', 'label': 'v1', 'path': str(self.other), 'fps': 30,
             'durationMs': 1000, 'fingerprint': 'y', 'keepRanges': [[0, 30]]}])

    def ids(self):
        return [item['id'] for item in store.videos(self.data)]


class LockIsNeverBroken(DataFolder):
    def test_b1_a_save_that_runs_past_30_seconds_keeps_both_changes(self):
        # B1: a lock older than 30 s used to count as dead. Saving a re-rendered video hashes the whole file;
        # on a slow disk that passes 30 s, the browser broke in, and one of the two changes was lost.
        recut = subprocess.Popen([sys.executable, '-I', '-c', SLOW_RECUT, str(ROOT), str(self.data), '2'],
                                 stdout=subprocess.PIPE, text=True)
        self.addCleanup(recut.stdout.close)
        self.addCleanup(recut.kill)
        self.assertEqual(recut.stdout.readline().strip(), 'hashing')
        old = time.time() - 60  # every lock file in the folder now looks older than 30 seconds
        for lock in self.data.glob('*.lock'):
            os.utime(lock, (old, old))
        store.archive_video(self.data, 'other-v1')
        self.assertEqual(recut.wait(timeout=30), 0)
        self.assertEqual(self.ids(), ['cut-v1'], 'the archive step was undone by the slower save')
        self.assertEqual(store.find_video(self.data, 'cut-v1')['keepRanges'], [[0, 30]])
        self.assertEqual([item['id'] for item in store.read(self.data / 'library-archive.json', [])], ['other-v1'])


class OneFileDoesNotWaitForAnother(DataFolder):
    def test_b2_a_comment_is_saved_while_a_long_video_is_being_hashed(self):
        # B2: one lock for every file in the server. While a re-rendered video was hashed, adding a comment hung.
        hashing, release = threading.Event(), threading.Event()
        real = store.fingerprint

        def slow(path):
            hashing.set()
            release.wait(10)
            return real(path)

        saved = threading.Event()
        with mock.patch.object(store, 'fingerprint', side_effect=slow):
            recut = threading.Thread(target=store.update_render, args=(self.data, 'cut-v1', None, [[0, 30]]))
            recut.start()
            try:
                self.assertTrue(hashing.wait(5))
                comment = threading.Thread(target=lambda: (store.apply(self.data, {
                    'action': 'add', 'videoId': 'other-v1', 'startMs': 10, 'text': 'Tighter'}), saved.set()))
                comment.start()
                comment.join(3)
                self.assertTrue(saved.is_set(), 'adding a comment waited for the video hash')
            finally:
                release.set()
                recut.join(10)
        self.assertEqual([c['text'] for c in store.comments(self.data)], ['Tighter'])
        self.assertEqual(store.find_video(self.data, 'cut-v1')['keepRanges'], [[0, 30]])


class FingerprintOfTheRightFile(DataFolder):
    def test_a_hash_of_a_file_the_entry_no_longer_points_to_is_not_saved(self):
        # The hash now runs before the lock; if the entry moved to another file meanwhile, the old hash is not kept.
        moved = self.data / 'moved.mp4'
        moved.write_bytes(b'moved')
        real = store.fingerprint

        def move_meanwhile(path):
            manifest = store.videos(self.data)
            manifest[0]['path'] = str(moved)
            store.write(self.data / 'manifest.json', manifest)
            return real(path)

        with mock.patch.object(store, 'fingerprint', side_effect=move_meanwhile):
            store.update_render(self.data, 'cut-v1', None, [[0, 30]])
        item = store.find_video(self.data, 'cut-v1')
        self.assertEqual((item['path'], item['fingerprint'], item['keepRanges']), (str(moved), 'x', [[0, 30]]))


class SavesSurviveWindows(DataFolder):
    def test_b3_a_save_tries_again_when_windows_holds_the_file_for_a_moment(self):
        # B3: on Windows a program reading the file makes the rename fail for a moment; the save gave up at once.
        real = os.replace
        calls = []

        def busy_once(source, target):
            calls.append(Path(source).name)
            if len(calls) == 1:
                raise PermissionError(13, 'The process cannot access the file', str(target))
            return real(source, target)

        with mock.patch('os.replace', side_effect=busy_once):
            store.set_view(self.data, {'videoId': 'cut-v1', 'playheadMs': 1200})
        self.assertEqual(store.view(self.data)['playheadMs'], 1200)
        self.assertEqual(len(calls), 2)
        self.assertEqual([p.name for p in self.data.iterdir() if '.tmp' in p.name], [])

    def test_b3_two_saves_of_one_file_at_once_use_their_own_temporary_files(self):
        # B3: every save of a file used the same temporary name, so two saves at once could take each other's.
        problems = []

        def save_playhead(start):
            for n in range(start, start + 40):
                try:
                    store.set_view(self.data, {'videoId': 'cut-v1', 'playheadMs': n})
                except OSError as error:
                    problems.append(error)

        threads = [threading.Thread(target=save_playhead, args=(n * 1000,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(problems, [])
        self.assertIsInstance(store.view(self.data)['playheadMs'], int)
        self.assertEqual([p.name for p in self.data.iterdir() if '.tmp' in p.name], [])


class AddingTheSameVideoTwice(DataFolder):
    def test_b4_two_quick_adds_of_one_file_make_one_entry(self):
        # B4: "Add video" looked for a duplicate before it took the lock, so two quick adds made two entries.
        new = self.data / 'new.mp4'
        new.write_bytes(bytes(range(64)))
        both_hashing = threading.Barrier(2, timeout=5)
        real = store.fingerprint

        def hash_together(path):
            try:
                both_hashing.wait()
            except threading.BrokenBarrierError:
                pass
            return real(path)

        probe = mock.Mock(stdout=json.dumps({'streams': [{'codec_type': 'video', 'avg_frame_rate': '30/1'}],
                                             'format': {'duration': '2.0'}}))
        results = []
        with mock.patch.object(store.shutil, 'which', return_value='ffprobe'), \
                mock.patch.object(store.subprocess, 'run', return_value=probe), \
                mock.patch.object(store, 'fingerprint', side_effect=hash_together):
            adds = [threading.Thread(target=lambda: results.append(store.add_local_video(self.data, str(new))))
                    for _ in range(2)]
            for add in adds:
                add.start()
            for add in adds:
                add.join(10)
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(1 for item in store.videos(self.data) if item['path'] == str(new.resolve())), 1)
        self.assertEqual(results[0]['id'], results[1]['id'])


class ManyWritersAtOnce(DataFolder):
    def test_separate_programs_adding_comments_at_once_lose_nothing(self):
        writers = [subprocess.Popen([sys.executable, '-I', '-c', ADD_COMMENTS, str(ROOT), str(self.data), name, '25'])
                   for name in ('browser', 'agent', 'third')]
        mine = []
        for n in range(25):  # and this program too, in its own threads' way
            mine.append(store.apply(self.data, {'action': 'add', 'videoId': 'cut-v1', 'startMs': n,
                                                'text': f'here-{n}'})[1]['id'])
        for writer in writers:
            self.assertEqual(writer.wait(timeout=60), 0)
        texts = sorted(c['text'] for c in store.comments(self.data))
        expected = sorted(f'{name}-{n}' for name in ('browser', 'agent', 'third', 'here') for n in range(25))
        self.assertEqual(texts, expected)
        self.assertEqual([p.name for p in self.data.iterdir() if '.tmp' in p.name], [])

    def test_threads_saving_comments_and_videos_at_once_lose_nothing(self):
        def comments(start):
            for n in range(start, start + 15):
                store.apply(self.data, {'action': 'add', 'videoId': 'cut-v1', 'startMs': n, 'text': f't-{n}'})

        def restore_and_archive():
            for _ in range(10):
                store.archive_video(self.data, 'other-v1')
                store.restore_video(self.data, 'other-v1')

        threads = [threading.Thread(target=comments, args=(n * 100,)) for n in range(4)]
        threads.append(threading.Thread(target=restore_and_archive))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(len(store.comments(self.data)), 60)
        self.assertEqual(sorted(self.ids()), ['cut-v1', 'other-v1'])


if __name__ == '__main__':
    unittest.main()
