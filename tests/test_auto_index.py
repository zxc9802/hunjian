import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'video-material-match'/'scripts'), str(ROOT/'video-material-match'/'deploy')]

from api import Models
from auto_index import AutoIndexer
from matcher import Catalog, file_stamp, matching_catalog
from migrate_catalog import migrate


class AutoIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.media = self.root/'media'
        self.media.mkdir()
        self.folder = self.root/'catalog'
        seed = self.media/'seed.mp4'
        seed.write_bytes(b'original')
        proxy = self.folder/'proxies'/'seed.mp4'
        proxy.parent.mkdir(parents=True)
        proxy.write_bytes(b'proxy')
        models = Models()
        catalog = Catalog(self.folder, {'embedding': models.embed_url, 'llm': models.llm_url,
                                        'chunk_seconds': 8, 'proxy_fps': 1, 'schema': 1})
        catalog.add({'path': str(seed), 'stamp': file_stamp(seed), 'start': 0., 'end': 8.,
                     'proxy': str(proxy), 'description': '原素材'}, [1, 0, 0])
        catalog.searcher()
        catalog.close()
        self.indexer = AutoIndexer(self.media, self.folder, interval=60, stable_seconds=60)

    def rows(self, query):
        with closing(sqlite3.connect(self.folder/'catalog.sqlite3')) as db:
            return db.execute(query).fetchall()

    def fake_task(self, task, folder):
        proxy = Path(folder)/'proxies'/f"new-{task['start']}.mp4"
        proxy.write_bytes(b'proxy')
        return ({**task, 'proxy': str(proxy), 'description': '新素材'}, [0, 1, 0], 0)

    def test_new_file_is_indexed_after_stable_scan_and_not_repeated(self):
        self.indexer.scan(now=0)
        new = self.media/'new.mp4'
        new.write_bytes(b'new material')
        with patch('auto_index.media.duration', return_value=8), \
             patch('auto_index.index_task', side_effect=self.fake_task) as task:
            self.indexer.scan(now=10)
            self.indexer.scan(now=69)
            task.assert_not_called()
            self.indexer.scan(now=70)
            self.indexer.scan(now=130)
            self.assertEqual(task.call_count, 1)
        self.assertEqual(self.rows('SELECT count(*) FROM clips')[0][0], 2)
        self.assertEqual(self.rows("SELECT state FROM auto_index_files WHERE path LIKE '%new.mp4'")[0][0], 'complete')
        catalog = matching_catalog(self.folder, Models())
        try:
            self.assertEqual(catalog.searcher()(np.array([0, 1, 0]), 1)[0]['path'], str(new))
        finally:
            catalog.close()
        self.assertEqual(migrate(self.root/'unused-seed', self.folder, self.media, 'Z:\\')['vectors'], 2)

    def test_failure_resumes_only_missing_clip(self):
        new = self.media/'new.mp4'
        new.write_bytes(b'new material')
        starts = []
        fail = True

        def process(task, folder):
            nonlocal fail
            starts.append(task['start'])
            if task['start'] == 8 and fail:
                fail = False
                raise RuntimeError('temporary model failure')
            return self.fake_task(task, folder)

        with patch('auto_index.media.duration', return_value=16), \
             patch('auto_index.index_task', side_effect=process):
            self.indexer.scan(now=0)
            self.indexer.scan(now=60)
            self.assertEqual(self.rows('SELECT count(*) FROM clips')[0][0], 2)
            self.indexer.scan(now=120)
        self.assertEqual(starts, [0., 8., 8.])
        self.assertEqual(self.rows('SELECT count(*) FROM clips')[0][0], 3)
        self.assertEqual(self.rows("SELECT state FROM auto_index_files WHERE path LIKE '%new.mp4'")[0][0], 'complete')

    def test_upload_change_resets_stability_timer(self):
        new = self.media/'new.mp4'
        new.write_bytes(b'first part')
        with patch('auto_index.media.duration', return_value=8), \
             patch('auto_index.index_task', side_effect=self.fake_task) as task:
            self.indexer.scan(now=0)
            new.write_bytes(b'complete upload')
            self.indexer.scan(now=60)
            self.indexer.scan(now=119)
            task.assert_not_called()
            self.indexer.scan(now=120)
            self.assertEqual(task.call_count, 1)


if __name__ == '__main__':
    unittest.main()
