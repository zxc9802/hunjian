import json
import sys
import tempfile
import time
import unittest
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'video-material-match' / 'scripts'))
import matcher
import media
from index_all import segments
from progress_ui import read_progress


class FullIndexTests(unittest.TestCase):
    def test_segments_cover_short_videos_and_absorb_tiny_tails(self):
        for duration in (.12, 7.9, 8, 8.1, 16.01, 24.5):
            clips = segments(duration)
            self.assertEqual(clips[0][0], 0)
            self.assertEqual(clips[-1][1], duration)
            self.assertTrue(all(end > start for start, end in clips))
            self.assertTrue(all(a[1] == b[0] for a, b in zip(clips, clips[1:])))
        self.assertEqual(segments(8.1), [(0., 8.1)])

    def test_atomic_progress_write_survives_temporary_windows_reader_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            matcher.write_json(path, {'completed': 7})
            original = Path.replace
            calls = []
            def replace(source, target):
                calls.append(1)
                if len(calls) < 3:
                    self.assertEqual(json.loads(path.read_text())['completed'], 7)
                    raise PermissionError('reader has the file open')
                return original(source, target)
            with patch.object(Path, 'replace', replace), patch.object(matcher.time, 'sleep'):
                matcher.write_json(path, {'completed': 8})
            self.assertEqual(json.loads(path.read_text())['completed'], 8)
            self.assertEqual(len(calls), 3)

    def test_active_full_index_is_used_and_model_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = SimpleNamespace(embed_url='embedding-model', llm_url='visual-model')
            signature = {'embedding': models.embed_url, 'llm': models.llm_url,
                         'chunk_seconds': 8, 'proxy_fps': 1, 'schema': 1}
            matcher.Catalog(root / 'full', signature).close()
            matcher.write_json(root / 'legacy' / 'active.json', {'catalog': str(root / 'full')})
            catalog = matcher.matching_catalog(root / 'legacy', models)
            self.assertEqual(catalog.folder, root / 'full')
            catalog.close()
            models.embed_url = 'different-model'
            with self.assertRaisesRegex(ValueError, '禁止混用'):
                matcher.matching_catalog(root / 'legacy', models)

    def test_stale_heartbeat_does_not_look_like_a_running_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_progress(tmp)['state'], 'waiting')
            db = sqlite3.connect(Path(tmp) / 'catalog.sqlite3')
            try:
                db.execute('CREATE TABLE index_progress (id INTEGER PRIMARY KEY, value TEXT)')
                for state, stale in [('running', True), ('complete', False)]:
                    db.execute('INSERT OR REPLACE INTO index_progress VALUES(1,?)',
                               (json.dumps({'state':state,'updated_at':time.time()-60}),))
                    db.commit()
                    self.assertEqual(read_progress(tmp)['stale'], stale)
            finally:
                db.close()

    def test_one_fps_proxy_keeps_a_frame_for_sub_half_second_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, proxy = Path(tmp) / 'source.mp4', Path(tmp) / 'proxy.mp4'
            media.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=64x64:r=25',
                       '-t','0.32','-c:v','libx264','-pix_fmt','yuv420p',str(source)])
            media.proxy(source,0,.32,proxy,fps=1)
            info=json.loads(media.run(['ffprobe','-v','error','-show_entries',
                                      'stream=nb_frames,r_frame_rate','-of','json',str(proxy)]))
            self.assertEqual(info['streams'][0]['r_frame_rate'],'1/1')
            self.assertEqual(int(info['streams'][0]['nb_frames']),1)


if __name__ == '__main__':
    unittest.main()
