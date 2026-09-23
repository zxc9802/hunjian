"""Single-workspace web gateway. NAS credentials never leave this process."""
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import music_library

ROOT = Path(__file__).resolve().parent
ARTIFACTS = {'video': ('mp4', 'video/mp4'), 'report': ('json', 'application/json'),
             'captions': ('srt', 'application/x-subrip'), 'plan': ('json', 'application/json'),
             'cuts': ('json', 'application/json')}
TERMINAL = {'done', 'failed', 'interrupted'}


@dataclass
class Settings:
    nas_url: str
    nas_token: str
    data_dir: Path
    public_url: str = 'http://127.0.0.1:8788'
    password: str = ''
    voice_file: Path = ROOT / 'reference/speaker-reference.mp3'

    def __post_init__(self):
        self.nas_url = self.nas_url.rstrip('/')
        self.public_url = self.public_url.rstrip('/')
        nas, public = urlsplit(self.nas_url), urlsplit(self.public_url)
        if nas.scheme not in ('http', 'https') or not nas.hostname or nas.username or nas.query or nas.fragment or nas.path:
            raise ValueError('NAS_URL 必须是固定 HTTP(S) 服务地址')
        if public.scheme not in ('http', 'https') or not public.hostname or public.username or public.path or public.query or public.fragment:
            raise ValueError('PUBLIC_URL 必须是工作台的完整来源地址')
        self.local = public.hostname in ('127.0.0.1', 'localhost', '::1')
        if not self.local and (public.scheme != 'https' or len(self.password) < 12):
            raise ValueError('远程部署需要 HTTPS PUBLIC_URL 和至少 12 位 WORKBENCH_PASSWORD')
        if self.password and len(self.password) < 12:
            raise ValueError('WORKBENCH_PASSWORD 至少 12 位')
        if len(self.nas_token) < 32 or not self.nas_token.isascii():
            raise ValueError('请在服务端设置有效的 NAS_TOKEN')
        self.data_dir = Path(self.data_dir)

    @classmethod
    def from_env(cls):
        config = {}
        if os.environ.get('NAS_CONFIG_FILE'):
            config = json.loads(Path(os.environ['NAS_CONFIG_FILE']).read_text(encoding='utf-8-sig'))
        return cls(os.environ.get('NAS_URL', config.get('base_url', '')),
                   os.environ.get('NAS_TOKEN', config.get('token', '')),
                   Path(os.environ.get('WORKBENCH_DATA', 'data/workbench')),
                   os.environ.get('PUBLIC_URL', 'http://127.0.0.1:8788'),
                   os.environ.get('WORKBENCH_PASSWORD', ''),
                   Path(os.environ.get('VOICE_FILE', str(cls.voice_file))))


class Store:
    def __init__(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / 'workbench.sqlite3'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, spec TEXT NOT NULL, nas_id TEXT,
                    state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    snapshot TEXT NOT NULL DEFAULT '{}', error TEXT);
                CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires REAL);
                CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT, created REAL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        value = dict(row)
        value['spec'] = json.loads(value['spec'])
        value['snapshot'] = json.loads(value['snapshot'])
        value['title'] = value['spec']['text'][:30]
        return value

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, '任务不存在')
        return self.decode(row)

    def reserve(self, job_id, spec):
        encoded = json.dumps(spec, ensure_ascii=False, sort_keys=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT spec FROM jobs WHERE id=?', (job_id,)).fetchone()
            if old and old['spec'] != encoded:
                raise HTTPException(409, '此提交标识已有不同文案，请新建任务')
            now = time.time()
            db.execute("INSERT OR IGNORE INTO jobs (id,spec,state,created,updated) VALUES (?,?,'submitting',?,?)",
                       (job_id, encoded, now, now))
        return self.get(job_id)

    def update(self, job_id, state, *, nas_id=None, snapshot=None, error=None):
        with self.connect() as db:
            db.execute('''UPDATE jobs SET state=?, nas_id=coalesce(?,nas_id),
                snapshot=coalesce(?,snapshot), error=?, updated=? WHERE id=?''',
                       (state, nas_id, json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None,
                        error, time.time(), job_id))
        return self.get(job_id)

    def list(self):
        with self.connect() as db:
            return [self.decode(row) for row in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 100')]


class Nas:
    def __init__(self, settings):
        self.settings = settings

    def request(self, method, path, **kwargs):
        # Private LAN/tailnet requests must not go through workstation HTTP proxies.
        with requests.Session() as session:
            session.trust_env = False
            headers = {'Authorization': 'Bearer ' + self.settings.nas_token, 'Accept-Encoding': 'identity', **kwargs.pop('headers', {})}
            return session.request(method, self.settings.nas_url + path, headers=headers,
                                   timeout=(5, 25), allow_redirects=False, **kwargs)


def validate_spec(data):
    text, alpha = data.get('text', ''), data.get('emotion_alpha', .8)
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 6000:
        raise HTTPException(400, '请输入 1–6000 字的文案')
    if type(alpha) not in (int, float) or not math.isfinite(alpha) or not .1 <= alpha <= .85 or abs(alpha * 20 - round(alpha * 20)) > 1e-7:
        raise HTTPException(400, '情绪强度需在 0.10–0.85 之间，步长为 0.05')
    width, height = data.get('width', 1080), data.get('height', 1920)
    if type(width) is not int or type(height) is not int or (width, height) not in ((1080, 1920), (1920, 1080), (720, 1280), (1280, 720)):
        raise HTTPException(400, '请选择 720p 或 1080p 横屏/竖屏')
    try:
        music_key = music_library.validate_key(data.get('music_key'))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {'text': text.strip(), 'emotion_alpha': alpha, 'width': width,
            'height': height, 'music_key': music_key}


def create_app(settings=None, nas=None):
    settings = settings or Settings.from_env()
    store, nas = Store(settings.data_dir), nas or Nas(settings)
    assets = [ROOT / 'static/style.css', ROOT / 'static/app.js']
    version = hashlib.sha256(b''.join(path.read_bytes() for path in assets)).hexdigest()[:12]
    page = (ROOT / 'static/index.html').read_text(encoding='utf-8')
    page = page.replace('/static/style.css', f'/static/style.css?v={version}')
    page = page.replace('/static/app.js', f'/static/app.js?v={version}')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.nas = store, nas
    hostname = urlsplit(settings.public_url).hostname
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[hostname])
    password_salt = secrets.token_bytes(16)
    password_hash = hashlib.scrypt(settings.password.encode(), salt=password_salt, n=16384, r=8, p=1)

    def clean(value):
        message = str(value).replace(settings.nas_token, '[已隐藏]')
        if settings.password:
            message = message.replace(settings.password, '[已隐藏]')
        return re.sub(r'sk-[A-Za-z0-9_-]+', '[已隐藏]', message)[:1800]

    def sanitized_snapshot(value):
        return {'state': value['state'], 'logs': [clean(x) for x in value.get('logs', [])][-100:],
                'error': clean(value['error']) if value.get('error') else None,
                'checkpoint': value.get('checkpoint'),
                'created': value.get('created'), 'updated': value.get('updated')}

    @app.middleware('http')
    async def protect(request, call_next):
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            if request.headers.get('origin') not in (None, settings.public_url) or request.headers.get('x-workbench-request') != '1':
                return JSONResponse({'detail': '请从工作台页面执行此操作'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def is_authenticated(request):
        if not settings.password:
            return settings.local
        digest = hashlib.sha256(request.cookies.get('workbench_session', '').encode()).hexdigest()
        with store.connect() as db:
            return db.execute('SELECT 1 FROM sessions WHERE digest=? AND expires>?', (digest, time.time())).fetchone() is not None

    def authorize(request: Request):
        if not is_authenticated(request):
            raise HTTPException(401, '请先登录工作台')

    async def read_json(request):
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, '需要 JSON 请求')
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 50000:
                raise HTTPException(413, '请求内容过长')
        try:
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, '请求格式不正确') from None

    def read_nas(path):
        try:
            with nas.request('GET', path) as response:
                if response.status_code == 401:
                    raise HTTPException(502, 'NAS 访问凭据无效，请检查服务端配置')
                if response.status_code != 200:
                    raise HTTPException(502, 'NAS 暂时未能返回任务信息，请稍后重试')
                return response.json()
        except (requests.RequestException, ValueError):
            raise HTTPException(503, '暂时连接不到 NAS，请检查 NAS 和私网连接后重试') from None

    def forward(job):
        if job['nas_id']:
            return job
        try:
            with nas.request('POST', '/v1/jobs', json=job['spec'], headers={'Idempotency-Key': job['id']}) as response:
                if response.status_code in (200, 202):
                    result = response.json()
                    if not re.fullmatch(r'[a-f0-9]{32}', result.get('id', '')):
                        raise ValueError('Invalid job id')
                    return store.update(job['id'], result['state'], nas_id=result['id'])
                if response.status_code in (400, 401, 403, 409, 413, 415, 429):
                    message = {401: 'NAS 访问凭据无效，请检查服务端配置', 403: 'NAS 拒绝访问，请检查服务端配置',
                               429: 'NAS 队列已满，可以稍后重新提交'}.get(response.status_code, 'NAS 未接受任务，请核对文案和参数')
                    return store.update(job['id'], 'rejected', error=message)
        except (requests.RequestException, ValueError, KeyError, TypeError):
            pass
        return store.update(job['id'], 'uncertain', error='还未确认 NAS 是否收到任务。点击“核对提交”会复用原标识，不重复下单。')

    app.state.read_nas = read_nas
    app.state.sanitized_snapshot = sanitized_snapshot

    def voice_path():
        uploaded = settings.data_dir / 'speaker-reference.mp3'
        return uploaded if uploaded.is_file() else settings.voice_file

    @app.get('/')
    def index():
        return HTMLResponse(page, headers={'Cache-Control': 'no-store'})

    @app.get('/health')
    def health():
        return {'status': 'ok', 'service': 'hainan-workbench'}

    @app.get('/api/session')
    def session(request: Request):
        return {'authenticated': is_authenticated(request), 'login_required': bool(settings.password)}

    @app.post('/api/login')
    async def login(request: Request):
        data = await read_json(request)
        ip, now = request.client.host, time.time()
        with store.connect() as db:
            db.execute('DELETE FROM login_attempts WHERE created<?', (now - 300,))
            if db.execute('SELECT count(*) FROM login_attempts WHERE ip=?', (ip,)).fetchone()[0] >= 5:
                raise HTTPException(429, '尝试次数过多，请 5 分钟后重试')
            db.execute('INSERT INTO login_attempts VALUES (?,?)', (ip, now))
        password = data.get('password', '')
        if not isinstance(password, str) or len(password) > 1024:
            raise HTTPException(400, '密码格式不正确')
        candidate = hashlib.scrypt(password.encode(), salt=password_salt, n=16384, r=8, p=1)
        if not settings.password or not hmac.compare_digest(candidate, password_hash):
            raise HTTPException(401, '密码不正确，请重试')
        session_token = secrets.token_urlsafe(32)
        with store.connect() as db:
            db.execute('DELETE FROM login_attempts WHERE ip=?', (ip,))
            db.execute('DELETE FROM sessions WHERE expires<?', (now,))
            db.execute('INSERT INTO sessions VALUES (?,?)', (hashlib.sha256(session_token.encode()).hexdigest(), now + 43200))
        response = JSONResponse({'authenticated': True})
        response.set_cookie('workbench_session', session_token, max_age=43200, httponly=True,
                            secure=settings.public_url.startswith('https:'), samesite='strict')
        return response

    @app.post('/api/logout')
    def logout(request: Request):
        with store.connect() as db:
            db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(request.cookies.get('workbench_session', '').encode()).hexdigest(),))
        response = JSONResponse({'ok': True})
        response.delete_cookie('workbench_session')
        return response

    @app.get('/api/connection', dependencies=[Depends(authorize)])
    def connection():
        value = read_nas('/health')
        if value.get('status') != 'ok' or value.get('service') != 'hainan-mixer':
            raise HTTPException(502, 'NAS 返回了非预期的服务，请检查地址')
        return {'connected': True, 'checked_at': time.time(), 'voice_available': voice_path().is_file(),
                'cover_editor_available': 'cover_editor' in value.get('capabilities', [])}

    @app.get('/api/jobs', dependencies=[Depends(authorize)])
    def list_jobs():
        return {'jobs': store.list()}

    @app.get('/api/music', dependencies=[Depends(authorize)])
    def list_music():
        try:
            return {'tracks': music_library.list_tracks()}
        except Exception:
            raise HTTPException(503, '音乐库暂时无法连接，请检查腾讯 COS 环境变量和存储桶权限') from None

    @app.put('/api/music', dependencies=[Depends(authorize)], status_code=201)
    async def upload_music(request: Request):
        try:
            key = music_library.new_key(request.headers.get('x-music-name', ''))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        temporary = settings.data_dir / ('.music-' + secrets.token_hex(16) + '.upload')
        size = 0
        try:
            with temporary.open('xb') as target:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 100 * 1024 * 1024:
                        raise HTTPException(413, '音乐文件不能超过 100 MB')
                    target.write(chunk)
            if size < 128:
                raise HTTPException(400, '音乐文件为空或过短')
            with temporary.open('rb') as source:
                header = source.read(16)
            suffix = Path(key).suffix.lower()
            valid = (suffix == '.mp3' and (header.startswith(b'ID3') or
                     header[:1] == b'\xff' and header[1] & 0xe0 == 0xe0)) or \
                    (suffix == '.wav' and header.startswith(b'RIFF') and header[8:12] == b'WAVE') or \
                    (suffix == '.m4a' and header[4:8] == b'ftyp')
            if not valid:
                raise HTTPException(400, '文件内容与音乐格式不符')
            try:
                await run_in_threadpool(music_library.upload, temporary, key)
            except Exception:
                raise HTTPException(503, '音乐上传到腾讯 COS 失败，请检查存储桶配置') from None
            return {'key': key, 'name': key.rsplit('/', 1)[-1], 'size': size}
        finally:
            temporary.unlink(missing_ok=True)

    @app.post('/api/jobs', dependencies=[Depends(authorize)], status_code=202)
    async def submit(request: Request):
        data = await read_json(request)
        key = data.get('request_id', '')
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9._-]{8,128}', key):
            raise HTTPException(400, '提交标识无效，请刷新页面后重试')
        job = store.reserve(key, validate_spec(data))
        return await run_in_threadpool(forward, job)

    @app.post('/api/jobs/{job_id}/reconcile', dependencies=[Depends(authorize)])
    def reconcile(job_id: str):
        return forward(store.get(job_id))

    @app.post('/api/jobs/{job_id}/resume', dependencies=[Depends(authorize)], status_code=202)
    def resume_job(job_id: str):
        job = store.get(job_id)
        if job['state'] not in ('failed', 'interrupted') or not job['nas_id']:
            raise HTTPException(409, '仅已失败或中断的 NAS 任务可以续作')
        try:
            with nas.request('POST', '/v1/jobs/' + job['nas_id'] + '/resume') as response:
                if response.status_code == 202:
                    value = response.json()
                    return store.update(job_id, value['state'], snapshot=sanitized_snapshot(value))
                if response.status_code in (404, 409):
                    raise HTTPException(response.status_code, 'NAS 任务状态已变化，请刷新后重试')
                raise HTTPException(502, 'NAS 暂时无法续作该任务')
        except (requests.RequestException, ValueError):
            raise HTTPException(503, '续作请求状态不确定，请刷新任务检查，避免重复点击') from None

    @app.get('/api/jobs/{job_id}', dependencies=[Depends(authorize)])
    def job_status(job_id: str):
        job = store.get(job_id)
        if job['nas_id'] and (job['state'] != 'done' or not job['snapshot']):
            value = read_nas('/v1/jobs/' + job['nas_id'])
            if value.get('state') not in {'queued', 'running', *TERMINAL}:
                raise HTTPException(502, 'NAS 返回了未知任务状态')
            job = store.update(job_id, value['state'], snapshot=sanitized_snapshot(value),
                               error=clean(value['error']) if value.get('error') else None)
        return job

    def finished_nas_id(job_id):
        job = store.get(job_id)
        if job['state'] != 'done' or not job['nas_id']:
            raise HTTPException(409, '请先等待原视频制作完成')
        return job['nas_id']

    @app.get('/api/jobs/{job_id}/edit', dependencies=[Depends(authorize)])
    def edit_form(job_id: str):
        return read_nas('/v1/jobs/' + finished_nas_id(job_id) + '/edit')

    @app.get('/api/jobs/{job_id}/edit-status', dependencies=[Depends(authorize)])
    def edit_status(job_id: str):
        return read_nas('/v1/jobs/' + finished_nas_id(job_id) + '/edit-status')

    @app.post('/api/jobs/{job_id}/edit', dependencies=[Depends(authorize)], status_code=202)
    async def save_edit(job_id: str, request: Request):
        nas_id = finished_nas_id(job_id)
        value = await read_json(request)
        try:
            with nas.request('POST', '/v1/jobs/' + nas_id + '/edit', json=value) as response:
                if response.status_code == 202:
                    return response.json()
                if response.status_code in (400, 409, 413, 415):
                    raise HTTPException(response.status_code, clean(response.json().get('detail', '文字设置未被接受')))
                if response.status_code == 401:
                    raise HTTPException(502, 'NAS 访问凭据无效，请检查服务端配置')
                raise HTTPException(502, 'NAS 暂时未能接收封面设置')
        except (requests.RequestException, ValueError):
            raise HTTPException(503, '无法确认封面设置是否收到，请用相同设置重试') from None

    @app.get('/api/jobs/{job_id}/covers/{index}', dependencies=[Depends(authorize)])
    def cover_image(job_id: str, index: int):
        nas_id = finished_nas_id(job_id)
        if not 0 <= index < 10:
            raise HTTPException(404, '封面候选不存在')
        try:
            upstream = nas.request('GET', f'/v1/jobs/{nas_id}/covers/{index}', stream=True)
        except requests.RequestException:
            raise HTTPException(503, '暂时无法读取封面候选') from None
        if upstream.status_code != 200:
            upstream.close()
            raise HTTPException(502, 'NAS 暂时无法提供封面候选')

        def chunks():
            try:
                yield from upstream.iter_content(chunk_size=64 * 1024)
            finally:
                upstream.close()
        return StreamingResponse(chunks(), media_type='image/jpeg')

    @app.get('/api/jobs/{job_id}/artifacts/{artifact}', dependencies=[Depends(authorize)])
    def artifact(job_id: str, artifact: str, request: Request, download: bool = False):
        if artifact not in ARTIFACTS:
            raise HTTPException(404, '文件不存在')
        job = store.get(job_id)
        if job['state'] != 'done' or not job['nas_id']:
            raise HTTPException(409, '成片尚未通过检查')
        headers = {}
        for header in ('Range', 'If-Range'):
            if request.headers.get(header):
                headers[header] = request.headers[header]
        try:
            upstream = nas.request('GET', '/v1/jobs/' + job['nas_id'] + '/' + artifact, headers=headers, stream=True)
        except requests.RequestException:
            raise HTTPException(503, '无法读取成片，请检查 NAS 连接后重试') from None
        if upstream.status_code not in (200, 206, 416):
            upstream.close()
            raise HTTPException(502, 'NAS 暂时无法提供此文件，请稍后重试')
        ext, media_type = ARTIFACTS[artifact]
        response_headers = {key: upstream.headers[key] for key in ('Content-Length', 'Content-Range', 'Accept-Ranges', 'ETag', 'Last-Modified') if key in upstream.headers}
        response_headers['Content-Disposition'] = f'{"attachment" if download else "inline"}; filename="hainan-{job_id[:12]}-{artifact}.{ext}"'

        def chunks():
            try:
                yield from upstream.iter_content(chunk_size=128 * 1024)
            finally:
                upstream.close()
        return StreamingResponse(chunks(), status_code=upstream.status_code, media_type=media_type, headers=response_headers)

    @app.get('/api/reference/voice', dependencies=[Depends(authorize)])
    def reference_voice():
        path = voice_path()
        if not path.is_file():
            raise HTTPException(404, '此环境未配置试听音频')
        return FileResponse(path, media_type='audio/mpeg')

    @app.put('/api/reference/voice', dependencies=[Depends(authorize)])
    async def upload_reference_voice(request: Request):
        if request.headers.get('content-type', '').split(';')[0] != 'audio/mpeg':
            raise HTTPException(415, '试听文件需要 MP3 格式')
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 5 * 1024 * 1024:
                raise HTTPException(413, '试听文件不能超过 5 MB')
        if len(content) < 10 or not (content[:3] == b'ID3' or content[0] == 0xff and content[1] & 0xe0 == 0xe0):
            raise HTTPException(400, '试听文件不是有效的 MP3')
        temporary = settings.data_dir / ('.voice-' + secrets.token_hex(16) + '.tmp')
        try:
            with temporary.open('xb') as file:
                file.write(content)
            temporary.replace(settings.data_dir / 'speaker-reference.mp3')
        finally:
            temporary.unlink(missing_ok=True)
        return {'voice_available': True}

    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    return app
