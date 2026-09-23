"""Continuously add stable new NAS videos to the existing 1 FPS catalog."""
import time
from pathlib import Path

from api import Models, clean_error
from index_all import index_task, segments
from matcher import file_stamp, inventory, matching_catalog
import media


class AutoIndexer:
    def __init__(self, source='/media', catalog='/data/catalog', interval=60, stable_seconds=300):
        self.source = Path(source)
        self.catalog = Path(catalog)
        self.interval = interval
        self.stable_seconds = stable_seconds

    @staticmethod
    def prepare(catalog):
        exists = catalog.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='auto_index_files'").fetchone()
        catalog.db.execute('''CREATE TABLE IF NOT EXISTS auto_index_files (
            path TEXT PRIMARY KEY, stamp TEXT NOT NULL, state TEXT NOT NULL,
            observed_at REAL NOT NULL, error TEXT)''')
        if not exists:
            # The migrated seed was verified before service startup.
            catalog.db.execute('''INSERT INTO auto_index_files
                SELECT path, stamp, 'complete', 0, NULL FROM clips GROUP BY path''')
        catalog.db.commit()

    def index_file(self, catalog, item):
        path, stamp = item['path'], item['stamp']
        for start, end in segments(media.duration(path)):
            if catalog.has(path, stamp, start):
                continue
            task = {'path': path, 'stamp': stamp, 'start': start, 'end': end}
            clip, vector, _ = index_task(task, catalog.folder)
            catalog.add(clip, vector)
        if file_stamp(path) != stamp:
            raise ValueError('入库期间源文件发生变化')
        catalog.searcher()

    def scan(self, now=None):
        """One scan; a new file must keep its stamp for the stability window."""
        now = time.time() if now is None else now
        items = inventory(self.source)
        models = Models()
        catalog = matching_catalog(self.catalog, models)
        try:
            self.prepare(catalog)
            states = {row[0]: row[1:] for row in catalog.db.execute(
                'SELECT path,stamp,state,observed_at FROM auto_index_files')}
            for item in items:
                path, stamp = item['path'], item['stamp']
                previous = states.get(path)
                if previous is None:
                    catalog.db.execute('INSERT INTO auto_index_files VALUES (?,?,?,?,NULL)',
                                       (path, stamp, 'pending', now))
                    catalog.db.commit()
                    continue
                old_stamp, state, observed_at = previous
                if state == 'complete':
                    if stamp != old_stamp:
                        print(f'自动入库跳过被覆盖的源文件，请使用新文件名：{path}', flush=True)
                    continue
                if stamp != old_stamp:
                    catalog.db.execute('DELETE FROM clips WHERE path=? AND stamp!=?', (path, stamp))
                    catalog.db.execute('UPDATE auto_index_files SET stamp=?,observed_at=?,error=NULL WHERE path=?',
                                       (stamp, now, path))
                    catalog.db.commit()
                    continue
                if now - observed_at < self.stable_seconds:
                    continue
                try:
                    print(f'自动入库开始：{path}', flush=True)
                    self.index_file(catalog, item)
                except Exception as exc:
                    error = clean_error(exc)
                    catalog.db.execute('UPDATE auto_index_files SET observed_at=?,error=? WHERE path=?',
                                       (now, error, path))
                    catalog.db.commit()
                    print(f'自动入库失败，下次扫描续作：{path}：{error}', flush=True)
                else:
                    catalog.db.execute("UPDATE auto_index_files SET state='complete',error=NULL WHERE path=?", (path,))
                    catalog.db.commit()
                    print(f'自动入库完成：{path}', flush=True)
        finally:
            catalog.close()

    def run(self, stop):
        while not stop.is_set():
            try:
                self.scan()
            except Exception as exc:
                print(f'自动入库扫描失败：{clean_error(exc)}', flush=True)
            stop.wait(self.interval)
