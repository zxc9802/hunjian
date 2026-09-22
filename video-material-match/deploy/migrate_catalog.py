"""Import a Windows catalog without recomputing its embeddings."""
import argparse
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path, PureWindowsPath


def source_relative(value, source_root):
    relative = PureWindowsPath(value).relative_to(PureWindowsPath(source_root))
    if '..' in relative.parts or not relative.parts:
        raise ValueError('索引文件不在指定素材目录内')
    return relative


def vector_digest(db):
    digest = hashlib.sha256()
    for row in db.execute('SELECT id,vector FROM clips ORDER BY id'):
        digest.update(str(row[0]).encode() + b':' + row[1])
    return digest.hexdigest()


def migrate(seed, target, media_root, source_root):
    seed, target, media_root = Path(seed).resolve(), Path(target).resolve(), Path(media_root).resolve()
    target.mkdir(parents=True, exist_ok=True)
    live = target/'catalog.sqlite3'
    fresh = not live.exists()
    temporary = target/'catalog.migrating.sqlite3'
    if fresh:
        with closing(sqlite3.connect(seed/'catalog.sqlite3')) as old, closing(sqlite3.connect(temporary)) as new:
            old.backup(new)
    database = temporary if fresh else live
    with closing(sqlite3.connect(database)) as db:
        before = vector_digest(db)
        rows = db.execute('SELECT id,path,stamp,proxy FROM clips').fetchall()
        if not rows:
            raise ValueError('源索引为空')
        stamps, paths, updates = {}, set(), []
        for clip_id, source, stamp, proxy in rows:
            relative = source_relative(source, source_root) if fresh else Path(source).relative_to(media_root)
            path = media_root.joinpath(*relative.parts).resolve()
            if not path.is_relative_to(media_root):
                raise ValueError('素材路径超出挂载目录')
            if path not in stamps:
                stat = path.stat()
                stamps[path] = (stat.st_size, stat.st_mtime_ns)
            size, mtime = stamps[path]
            old_size, old_mtime = map(int, stamp.split(':'))
            # SMB/Windows FILETIME has 100ns precision; never ignore larger changes.
            tolerance = 100 if fresh else 0
            if size != old_size or abs(mtime-old_mtime) > tolerance:
                raise ValueError(f'原素材已变化，停止迁移：{path}')
            paths.add(str(path))
            name = PureWindowsPath(proxy).name if fresh else Path(proxy).name
            dest_proxy = target/'proxies'/name
            if fresh:
                dest_proxy.parent.mkdir(exist_ok=True)
                shutil.copy2(seed/'proxies'/name, dest_proxy)
            if not dest_proxy.is_file():
                raise ValueError(f'缺少核验代理：{name}')
            if fresh:
                updates.append((str(path), f'{size}:{mtime}', str(dest_proxy), clip_id))
        if fresh:
            with db:
                db.executemany('UPDATE clips SET path=?,stamp=?,proxy=? WHERE id=?', updates)
        if vector_digest(db) != before:
            raise ValueError('迁移期间向量内容发生变化')
    if fresh:
        temporary.replace(live)
    from api import Models
    from matcher import matching_catalog
    import faiss
    catalog = matching_catalog(target, Models())
    try:
        catalog.searcher()
    finally:
        catalog.close()
    index = faiss.read_index(str(target/'vectors.faiss'))
    if index.ntotal != len(rows):
        raise ValueError('FAISS 数量与迁移记录不一致')
    result = {'videos': len(paths), 'clips': len(rows), 'vectors': index.ntotal,
              'dimension': index.d, 'vector_sha256': before, 'embeddings_recomputed': 0}
    (target/'migration-report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', default='/seed/catalog')
    parser.add_argument('--target', default='/data/catalog')
    parser.add_argument('--media', default='/media')
    parser.add_argument('--source-root', required=True)
    args = parser.parse_args()
    migrate(args.seed, args.target, args.media, args.source_root)
