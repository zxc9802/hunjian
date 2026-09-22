"""Read-only, loopback dashboard for the full-library indexing worker."""
import argparse
import json
import time
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def read_progress(folder):
    path = Path(folder) / 'catalog.sqlite3'
    if not path.exists():
        return {'state': 'waiting', 'stale': False, 'server_time': time.time()}
    with closing(sqlite3.connect(path, timeout=2)) as db:
        try:
            row = db.execute('SELECT value FROM index_progress WHERE id=1').fetchone()
        except sqlite3.OperationalError as exc:
            if 'no such table' not in str(exc):
                raise
            row = None
    if row is None:
        return {'state': 'waiting', 'stale': False, 'server_time': time.time()}
    data = json.loads(row[0])
    data['server_time'] = time.time()
    data['heartbeat_age'] = max(0, data['server_time'] - data['updated_at'])
    data['stale'] = data['state'] in ('scanning', 'running', 'verifying') and data['heartbeat_age'] > 20
    return data


def serve(port, folder):
    page = Path(__file__).resolve().parents[1] / 'assets' / 'progress.html'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/':
                body, mime, code = page.read_bytes(), 'text/html; charset=utf-8', 200
            elif self.path == '/api/progress':
                try:
                    body = json.dumps(read_progress(folder), ensure_ascii=False).encode('utf-8')
                    code = 200
                except (OSError, ValueError, KeyError, sqlite3.Error):
                    body = json.dumps({'error': '暂时无法读取进度，正在重试'}, ensure_ascii=False).encode('utf-8')
                    code = 503
                mime = 'application/json; charset=utf-8'
            else:
                body, mime, code = b'Not found', 'text/plain', 404
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    print(f'向量化进度：http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--catalog', default='data/catalog-full')
    args = parser.parse_args()
    serve(args.port, Path(args.catalog).resolve())
