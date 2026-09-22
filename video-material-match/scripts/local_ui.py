"""Loopback-only control page; all model calls and validation stay on the server."""
import json
import mimetypes
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from api import clean_error
from matcher import create_plan, index_videos
import media
import voice


def validate_request(data):
    alpha = voice.validate_emotion(data.get('emotion_alpha', .8))
    text = data.get('text', '')
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 6000:
        raise ValueError('请输入 1–6000 字的文案')
    action = data.get('action', 'video')
    if action not in ('match', 'video'):
        raise ValueError('未知操作')
    return text.strip(), alpha, action


def serve(port, catalog, output_root):
    catalog, output_root = Path(catalog).resolve(), Path(output_root).resolve()
    output_root.mkdir(parents=True,exist_ok=True)
    status, lock = {'state':'idle','logs':[]}, threading.Lock()
    page = Path(__file__).resolve().parents[1] / 'assets' / 'control.html'

    def log(message):
        with lock:
            status['logs'] = (status['logs'] + [clean_error(message)])[-80:]
        print(clean_error(message),flush=True)

    def generate(text,alpha,action,run_id):
        folder = output_root / run_id
        try:
            plan = create_plan(text,catalog,folder,log=log)
            if action == 'video':
                voice.synthesize_plan(plan,folder,alpha,log=log)
            from finish import deliver
            video = deliver(plan,folder,catalog=catalog,log=log)
            with lock:
                status.update({'state':'done','url':f'/results/{run_id}/{video.name}',
                               'plan':str(folder/'plan.json'),
                               'missing':sum(not s['match']['selected'] for s in plan['scenes'])})
        except Exception as exc:
            log(f'失败：{clean_error(exc)}')
            with lock:
                status.update({'state':'failed','plan':str(folder/'plan.json')})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def respond(self,code,value):
            body=json.dumps(value,ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            host=self.headers.get('Host','')
            if host not in (f'127.0.0.1:{port}',f'localhost:{port}'):
                return self.respond(403,{'error':'仅允许本机访问'})
            if self.headers.get('Origin') not in (None,f'http://{host}'):
                return self.respond(403,{'error':'不接受跨站请求'})
            if self.path != '/api/generate' or self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                return self.respond(400,{'error':'请求格式错误'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 50000:
                    raise ValueError('请求大小超限')
                text,alpha,action=validate_request(json.loads(self.rfile.read(size)))
                with lock:
                    if status['state']=='running':
                        return self.respond(409,{'error':'已有任务在运行'})
                    status.clear()
                    status.update({'state':'running','logs':[]})
                run_id=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
                threading.Thread(target=generate,args=(text,alpha,action,run_id),daemon=True).start()
                self.respond(202,{'state':'running'})
            except (ValueError,TypeError,AttributeError) as exc:
                self.respond(400,{'error':clean_error(exc)})

        def do_GET(self):
            if self.path == '/api/status':
                with lock:
                    return self.respond(200,dict(status))
            if self.path == '/':
                path=page
            elif self.path.startswith('/results/'):
                path=(output_root/unquote(urlparse(self.path).path[len('/results/'):])).resolve()
                if not path.is_relative_to(output_root) or path.suffix.lower() not in ('.mp4','.wav','.mp3','.m4a','.srt','.json'):
                    return self.respond(404,{'error':'文件不存在'})
            else:
                return self.respond(404,{'error':'页面不存在'})
            if not path.is_file():
                return self.respond(404,{'error':'文件不存在'})
            size=path.stat().st_size
            start,end,code=0,size-1,200
            requested=self.headers.get('Range')
            if requested:
                match=re.fullmatch(r'bytes=(\d+)-(\d*)',requested)
                if not match:
                    return self.respond(416,{'error':'无效字节范围'})
                start=int(match[1]); end=min(int(match[2]) if match[2] else size-1,size-1); code=206
                if start>end:
                    return self.respond(416,{'error':'无效字节范围'})
            self.send_response(code)
            self.send_header('Content-Type',mimetypes.guess_type(path)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(end-start+1))
            self.send_header('Accept-Ranges','bytes')
            if code==206:
                self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            try:
                with path.open('rb') as file:
                    file.seek(start)
                    remaining=end-start+1
                    while remaining:
                        chunk=file.read(min(65536,remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining-=len(chunk)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                pass

    print(f'素材匹配页面：http://127.0.0.1:{port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
