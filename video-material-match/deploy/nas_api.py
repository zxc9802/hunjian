"""Single-process NAS job queue; originals are mounted read-only by Compose."""
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import FileResponse

from api import clean_error
from local_ui import validate_request
from video_storage import upload_video, delete_videos


class JobPaused(BaseException):
    """Unwind at a checkpoint without treating pause as a provider failure."""


class Jobs:
    def __init__(self, root, video_publisher=upload_video):
        self.root = Path(root).resolve()
        self.video_publisher = video_publisher
        self.storage_lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'jobs.sqlite3'
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE, spec TEXT,
                state TEXT, created REAL, updated REAL, logs TEXT, result TEXT, error TEXT)''')
            if 'cos_key' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN cos_key TEXT')
            if 'source_cos_key' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN source_cos_key TEXT')
            db.execute('''CREATE TABLE IF NOT EXISTS edits (
                job_id TEXT PRIMARY KEY, spec TEXT NOT NULL, state TEXT NOT NULL,
                updated REAL NOT NULL, result TEXT, error TEXT)''')
            db.execute('CREATE TABLE IF NOT EXISTS deleted_music (key TEXT PRIMARY KEY)')
            db.execute("UPDATE jobs SET state='paused' WHERE state='pausing'")
            # Never blindly resubmit a possibly billed model request after a crash.
            db.execute("UPDATE jobs SET state='interrupted', error=?, updated=? WHERE state='running'",
                       ('服务重启中断任务；保留输出和服务商任务记录，核对后再恢复。', time.time()))
            db.execute("UPDATE edits SET state='interrupted', error=?, updated=? WHERE state='running'",
                       ('服务重启中断文字导出，请核对后重新提交同一设置。', time.time()))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, key, spec):
        encoded = json.dumps(spec, sort_keys=True, ensure_ascii=False)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE request_key=?', (key,)).fetchone()
            if row:
                if row['state'] in ('deleted', 'deleting'):
                    raise HTTPException(410, '这条记录已删除，请新建任务')
                if row['spec'] != encoded:
                    raise HTTPException(409, '同一 Idempotency-Key 不能提交不同文案或参数')
                return row['id']
            if spec.get('music_key') and db.execute('SELECT 1 FROM deleted_music WHERE key=?',
                                                    (spec['music_key'],)).fetchone():
                raise HTTPException(409, '所选音乐已删除，请重新选择音乐')
            if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= 20:
                raise HTTPException(429, '任务队列已满，请稍后再试')
            job_id = uuid.uuid4().hex
            now = time.time()
            db.execute('INSERT INTO jobs (id,request_key,spec,state,created,updated,logs,result,error) VALUES (?,?,?,?,?,?,?,?,?)',
                       (job_id, key, encoded, 'queued', now, now, '[]', None, None))
            return job_id

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None or row['state'] == 'deleted':
            raise HTTPException(404, '任务不存在')
        result = dict(row)
        result['logs'] = json.loads(result['logs'])
        result.pop('request_key')
        result.pop('spec')
        return result

    def checkpoint(self, job_id):
        folder = self.root / 'outputs' / job_id
        plan_path = folder / 'plan.json'
        if not plan_path.is_file():
            return '任务提交'
        try:
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
            scenes = plan.get('scenes', [])
            if not scenes or any('match' not in scene for scene in scenes):
                return '素材匹配'
            if not (folder / 'narration.wav').is_file():
                return '配音与剪辑'
            if any(not scene.get('shots') for scene in scenes):
                return '动态镜头补齐'
            video = 'video-music.mp4' if plan.get('music_settings', {}).get('path') else 'video.mp4'
            if not (folder / video).is_file():
                return '视频导出'
            return '成片检查'
        except (OSError, ValueError, TypeError):
            return '素材匹配'

    def resume(self, job_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise HTTPException(404, '任务不存在')
            if row['state'] not in ('failed', 'interrupted', 'paused'):
                raise HTTPException(409, '仅暂停、失败或中断的任务可以继续')
            db.execute("UPDATE jobs SET state='queued',error=NULL,updated=? WHERE id=?",
                       (time.time(), job_id))
        self.log(job_id, '从已保存的「' + self.checkpoint(job_id) + '」状态继续制作')
        return self.get(job_id)

    def pause(self, job_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row['state'] == 'deleted':
                raise HTTPException(404, '任务不存在')
            if row['state'] not in ('queued', 'running', 'pausing', 'paused'):
                raise HTTPException(409, '当前任务不能暂停，请刷新状态')
            state = 'paused' if row['state'] in ('queued', 'paused') else 'pausing'
            db.execute('UPDATE jobs SET state=?,updated=? WHERE id=?', (state, time.time(), job_id))
        return self.get(job_id)

    def check_pause(self, job_id):
        if self.get(job_id)['state'] == 'pausing':
            raise JobPaused()

    def progress(self, job_id, message):
        self.check_pause(job_id)
        self.log(job_id, message)

    def delete(self, job_id):
        with self.storage_lock:
            return self._delete(job_id)

    def _delete(self, job_id):
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise HTTPException(404, '任务不存在')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row['state'] == 'deleted':
                return
            edit = db.execute('SELECT state FROM edits WHERE job_id=?', (job_id,)).fetchone()
            if row['state'] in ('queued', 'running', 'pausing') or edit and edit['state'] in ('queued', 'running'):
                raise HTTPException(409, '任务仍在制作或导出，请先暂停制作或等待导出完成')
            db.execute("UPDATE jobs SET state='deleting',updated=? WHERE id=?", (time.time(), job_id))
        try:
            delete_videos(job_id)
            folder = (self.root / 'outputs' / job_id).resolve()
            if folder.parent != (self.root / 'outputs').resolve():
                raise ValueError('任务目录无效')
            if folder.exists():
                shutil.rmtree(folder)
        except Exception:
            raise HTTPException(503, '成片尚未全部删除，请重试删除；制作记录暂时保留') from None
        with self.connect() as db:
            db.execute('DELETE FROM edits WHERE job_id=?', (job_id,))
            db.execute("UPDATE jobs SET state='deleted',spec='{}',logs='[]',result=NULL,"
                       'cos_key=NULL,source_cos_key=NULL,error=NULL WHERE id=?', (job_id,))

    def delete_music(self, key):
        from cos_music import validate_key, delete
        try:
            key = validate_key(key)
            if not key:
                raise ValueError('请选择要删除的音乐')
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT spec FROM jobs WHERE state NOT IN ('done','deleted')"):
                if json.loads(row['spec']).get('music_key') == key:
                    raise HTTPException(409, '这首音乐仍被未完成的任务使用，请先完成或删除相关任务')
            try:
                delete(key)
            except Exception:
                raise HTTPException(503, '音乐删除未完成，请稍后重试') from None
            db.execute('INSERT OR IGNORE INTO deleted_music VALUES (?)', (key,))

    def log(self, job_id, message):
        message = clean_error(message)
        with self.connect() as db:
            logs = json.loads(db.execute('SELECT logs FROM jobs WHERE id=?', (job_id,)).fetchone()[0])
            db.execute('UPDATE jobs SET logs=?,updated=? WHERE id=?',
                       (json.dumps((logs + [message])[-100:], ensure_ascii=False), time.time(), job_id))
        print(f'[{job_id}] {message}', flush=True)

    def get_edit(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT spec,state,updated,result,error FROM edits WHERE job_id=?', (job_id,)).fetchone()
        return ({'state': row['state'], 'updated': row['updated'], 'result': row['result'],
                 'error': row['error'], 'spec': json.loads(row['spec'])} if row else {'state': 'not_started'})

    def submit_edit(self, job_id, spec):
        if self.get(job_id)['state'] != 'done':
            raise HTTPException(409, '需要先完成原视频制作')
        encoded = json.dumps(spec, sort_keys=True, ensure_ascii=False)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()['state'] != 'done':
                raise HTTPException(409, '任务状态已变化，请刷新后重试')
            previous = db.execute('SELECT state,spec FROM edits WHERE job_id=?', (job_id,)).fetchone()
            if previous and previous['state'] in ('queued', 'running') and previous['spec'] != encoded:
                raise HTTPException(409, '上一版文字仍在导出，请等待完成')
            if not previous or previous['spec'] != encoded or previous['state'] in ('failed', 'interrupted'):
                db.execute('INSERT INTO edits VALUES (?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET '
                           'spec=excluded.spec,state=excluded.state,updated=excluded.updated,'
                           'result=CASE WHEN edits.spec=excluded.spec THEN edits.result ELSE NULL END,error=NULL',
                           (job_id, encoded, 'queued', time.time(), None, None))
        return self.get_edit(job_id)

    def run_edit(self, runner):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT job_id,spec,result FROM edits WHERE state='queued' ORDER BY updated LIMIT 1").fetchone()
            if not row:
                return False
            db.execute("UPDATE edits SET state='running',updated=? WHERE job_id=?", (time.time(), row['job_id']))
        job_id = row['job_id']
        folder = self.root / 'outputs' / job_id
        try:
            saved = folder / row['result'] if row['result'] else None
            result = (saved if saved and saved.is_file() and
                      (folder / saved.stem / 'quality-report.json').is_file()
                      else Path(runner(json.loads(row['spec']), folder,
                                       lambda message: self.log(job_id, message)))).resolve()
            if not result.is_relative_to(folder) or not result.is_file():
                raise ValueError('修改后的视频路径无效')
            report = json.loads((folder / result.stem / 'quality-report.json').read_text(encoding='utf-8'))
            if not report.get('passed') or report.get('sha256') != hashlib.sha256(result.read_bytes()).hexdigest():
                raise ValueError('修改后的成片未通过实际视频与声音检查')
            with self.connect() as db:
                db.execute('UPDATE edits SET result=? WHERE job_id=?', (result.name, job_id))
            cos_key = self.video_publisher(result, job_id, report['sha256'])
            with self.connect() as db:
                db.execute('UPDATE jobs SET result=?,cos_key=?,updated=? WHERE id=?',
                           (result.name, cos_key, time.time(), job_id))
                db.execute("UPDATE edits SET state='done',result=?,error=NULL,updated=? WHERE job_id=?",
                           (result.name, time.time(), job_id))
        except Exception as exc:
            error = clean_error(exc)
            self.log(job_id, '封面或文字导出失败：' + error)
            with self.connect() as db:
                db.execute("UPDATE edits SET state='failed',error=?,updated=? WHERE job_id=?",
                           (error, time.time(), job_id))
        return True

    def run_one(self, runner):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return False
            db.execute("UPDATE jobs SET state='running',error=NULL,updated=? WHERE id=?", (time.time(), row['id']))
        job_id = row['id']
        folder = self.root / 'outputs' / job_id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            self.check_pause(job_id)
            saved = folder / row['result'] if row['result'] else None
            result = (saved if saved and saved.is_file() and (folder / 'quality-report.json').is_file()
                      else Path(runner(json.loads(row['spec']), folder,
                                       lambda msg: self.progress(job_id, msg)))).resolve()
            if not result.is_relative_to(folder) or not result.is_file():
                raise ValueError('任务输出路径无效')
            report = json.loads((folder/'quality-report.json').read_text(encoding='utf-8'))
            digest = hashlib.sha256(result.read_bytes()).hexdigest()
            if not report.get('passed') or report.get('sha256') != digest:
                raise ValueError('实际成片尚未通过检查或检查报告与成片不一致')
            with self.connect() as db:
                db.execute('UPDATE jobs SET result=? WHERE id=?', (result.name, job_id))
            self.check_pause(job_id)
            cos_key = self.video_publisher(result, job_id, digest)
            with self.connect() as db:
                db.execute("UPDATE jobs SET state=CASE WHEN state='pausing' THEN 'paused' ELSE 'done' END,"
                           'result=?,cos_key=?,source_cos_key=?,error=NULL,updated=? WHERE id=?',
                           (result.name, cos_key, cos_key, time.time(), job_id))
        except JobPaused:
            with self.connect() as db:
                db.execute("UPDATE jobs SET state='paused',error=NULL,updated=? WHERE id=?", (time.time(), job_id))
            self.log(job_id, '已暂停制作，已完成的进度保留，可继续制作')
        except Exception as exc:
            error = clean_error(exc)
            self.log(job_id, '失败：' + error)
            with self.connect() as db:
                db.execute("UPDATE jobs SET state=CASE WHEN state='pausing' THEN 'paused' ELSE 'failed' END,"
                           'error=?,updated=? WHERE id=?', (error, time.time(), job_id))
        return True

    def sync_video(self, job_id):
        with self.storage_lock:
            return self._sync_video(job_id)

    def _sync_video(self, job_id):
        job = self.get(job_id)
        if job['state'] != 'done':
            raise HTTPException(409, '成片尚未通过检查')
        if job['cos_key'] and job['source_cos_key']:
            return job
        folder = (self.root / 'outputs' / job_id).resolve()
        video = (folder / job['result']).resolve()
        if not video.is_relative_to(folder) or not video.is_file():
            raise HTTPException(404, '原成片不存在')
        report_name = (video.stem + '/quality-report.json') if video.name.startswith('edit-') else 'quality-report.json'
        report = json.loads((folder / report_name).read_text(encoding='utf-8'))
        digest = hashlib.sha256(video.read_bytes()).hexdigest()
        if not report.get('passed') or report.get('sha256') != digest:
            raise HTTPException(409, '原成片与质检报告不一致')
        cos_key = job['cos_key'] or self.video_publisher(video, job_id, digest)
        source_cos_key = job['source_cos_key']
        if not source_cos_key:
            source_report = json.loads((folder / 'quality-report.json').read_text(encoding='utf-8'))
            source = Path(source_report.get('video', video)).resolve()
            if not source.is_relative_to(folder) or not source.is_file():
                raise HTTPException(404, '原始成片不存在')
            source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if not source_report.get('passed') or source_report.get('sha256') != source_digest:
                raise HTTPException(409, '原始成片与质检报告不一致')
            source_cos_key = (cos_key if source == video else
                              self.video_publisher(source, job_id, source_digest))
        with self.connect() as db:
            db.execute('UPDATE jobs SET cos_key=?,source_cos_key=? WHERE id=?',
                       (cos_key, source_cos_key, job_id))
        return self.get(job_id)


def generate(spec, folder, log):
    from matcher import create_plan, resume_plan
    from voice import synthesize_plan
    from finish import deliver
    catalog = os.environ.get('CATALOG_DIR', '/data/catalog')
    saved = folder / 'plan.json'
    plan = json.loads(saved.read_text(encoding='utf-8')) if saved.exists() else create_plan(
        spec['text'], catalog, folder, log=log)
    if plan['script'] != spec['text']:
        raise ValueError('保存的计划与任务文案不一致，不能复用已付费任务')
    resume_plan(plan, catalog, folder, log=log)
    voiced = plan.get('voice_settings', {}).get('emotion_alpha') == spec['emotion_alpha']
    voiced = voiced and (folder / 'narration.wav').is_file() and all(
        scene.get('voice') and Path(scene['voice']).is_file() for scene in plan['scenes'])
    if voiced:
        log('复用已完成配音与时间轴')
    else:
        synthesize_plan(plan, folder, spec['emotion_alpha'], log=log)
    music_file = None
    if spec.get('music_key'):
        from cos_music import download
        from matcher import write_json
        music_file = download(spec['music_key'], folder)
        plan['music_settings'] = {'provider': 'tencent-cos', 'key': spec['music_key'],
                                  'path': str(music_file)}
        write_json(saved, plan)
        log('已读取音乐库曲目，混音时按视频时长裁切')
    return deliver(plan, folder, spec['width'], spec['height'], catalog=catalog,
                   music_file=music_file, log=log)


def render_edit(spec, folder, log):
    import editor
    import quality
    folder = Path(folder).resolve()
    plan = json.loads((folder / 'plan.json').read_text(encoding='utf-8'))
    original_report = json.loads((folder / 'quality-report.json').read_text(encoding='utf-8'))
    original = Path(original_report['video']).resolve()
    if not original.is_relative_to(folder) or not original.is_file():
        raise ValueError('原始成片不存在，不能修改封面')
    log('从已选镜头制作 0.5 秒封面和逐镜头文字')
    video = editor.render_overlay(folder, spec, original)
    review_folder = folder / video.stem
    review_folder.mkdir(exist_ok=True)
    review_plan = {**plan, 'cover_seconds': .5}
    log('检查修改后的实际画面、配音和音乐')
    report = quality.review(video, review_plan, review_folder, log=log)
    if not report['passed']:
        raise ValueError('修改后的成片检查未通过，请查看质量报告并调整文字')
    return video


preview_lock = threading.Lock()


def source_preview(source, folder):
    import media
    preview = folder / 'source-preview.mp4'
    if preview.is_file():
        return preview
    with preview_lock:
        if preview.is_file():
            return preview
        temporary = folder / ('source-preview-' + uuid.uuid4().hex + '.mp4')
        try:
            media.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(source),
                       '-vf', 'scale=360:-2,fps=12', '-c:v', 'libx264', '-preset', 'veryfast',
                       '-crf', '34', '-maxrate', '250k', '-bufsize', '500k', '-pix_fmt', 'yuv420p',
                       '-c:a', 'aac', '-b:a', '48k', '-ac', '1', '-movflags', '+faststart',
                       str(temporary)])
            temporary.replace(preview)
        finally:
            temporary.unlink(missing_ok=True)
    return preview


def create_app(root=None, token=None, runner=generate, start_worker=True,
               edit_runner=render_edit, video_publisher=upload_video):
    token = token or os.environ.get('MIXER_API_TOKEN', '')
    if len(token) < 32 or not token.isascii():
        raise ValueError('MIXER_API_TOKEN 必须是至少 32 字符的随机 ASCII 密钥')
    jobs = Jobs(root or os.environ.get('DATA_DIR', '/data'), video_publisher)
    stop = threading.Event()

    def work():
        while not stop.is_set():
            if not jobs.run_one(runner) and not jobs.run_edit(edit_runner):
                stop.wait(1)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            from auto_index import AutoIndexer
            threading.Thread(target=work, name='mixer-worker', daemon=True).start()
            threading.Thread(target=AutoIndexer(catalog=os.environ.get('CATALOG_DIR', '/data/catalog')).run,
                             args=(stop,), name='material-indexer', daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title='海南康养混剪 NAS API', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.jobs = jobs

    def authorize(request: Request):
        value = request.headers.get('authorization', '')
        if not hmac.compare_digest(value.encode(), ('Bearer ' + token).encode()):
            raise HTTPException(401, '需要有效访问密钥', headers={'WWW-Authenticate': 'Bearer'})
        if request.headers.get('origin'):
            raise HTTPException(403, '仅接受服务端或命令行 API 调用')

    @app.get('/health')
    def health():
        return {'status': 'ok', 'service': 'hainan-mixer', 'worker_concurrency': 1,
                'capabilities': ['cover_editor', 'auto_index', 'task_controls']}

    @app.post('/v1/jobs', status_code=202, dependencies=[Depends(authorize)])
    async def submit(request: Request):
        key = request.headers.get('idempotency-key', '')
        if not re.fullmatch(r'[A-Za-z0-9._-]{8,128}', key):
            raise HTTPException(400, '需要 8–128 字符的 Idempotency-Key')
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, '需要 application/json')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 50000:
                raise HTTPException(413, '请求过大')
        try:
            body = json.loads(data)
            text, alpha, action = validate_request(body)
            if action != 'video':
                raise ValueError('NAS 接口生成有声成片，action 必须为 video')
            width, height = body.get('width', 1080), body.get('height', 1920)
            if type(width) is not int or type(height) is not int or (width, height) not in (
                    (1080, 1920), (1920, 1080), (720, 1280), (1280, 720)):
                raise ValueError('仅支持 720p/1080p 横屏或竖屏')
            from cos_music import validate_key
            music_key = validate_key(body.get('music_key'))
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(400, clean_error(exc)) from None
        job_id = jobs.submit(key, {'text': text, 'emotion_alpha': alpha, 'width': width,
                                   'height': height, 'music_key': music_key})
        return {'id': job_id, 'state': jobs.get(job_id)['state'], 'status_url': f'/v1/jobs/{job_id}'}

    @app.get('/v1/jobs/{job_id}', dependencies=[Depends(authorize)])
    def status(job_id: str):
        result = jobs.get(job_id)
        result['checkpoint'] = jobs.checkpoint(job_id)
        if result['state'] == 'done':
            result['video_url'] = f'/v1/jobs/{job_id}/video'
        if (jobs.root / 'outputs' / job_id / 'quality-report.json').is_file():
            result['report_url'] = f'/v1/jobs/{job_id}/report'
        return result

    @app.post('/v1/jobs/{job_id}/sync-video', dependencies=[Depends(authorize)])
    def sync_video(job_id: str):
        return jobs.sync_video(job_id)

    @app.post('/v1/jobs/{job_id}/resume', status_code=202, dependencies=[Depends(authorize)])
    def resume(job_id: str):
        result = jobs.resume(job_id)
        result['checkpoint'] = jobs.checkpoint(job_id)
        return result

    @app.post('/v1/jobs/{job_id}/pause', status_code=202, dependencies=[Depends(authorize)])
    def pause(job_id: str):
        return jobs.pause(job_id)

    @app.delete('/v1/jobs/{job_id}', dependencies=[Depends(authorize)])
    def delete_job(job_id: str):
        jobs.delete(job_id)
        return {'deleted': True}

    @app.delete('/v1/music', dependencies=[Depends(authorize)])
    def delete_music(key: str = ''):
        jobs.delete_music(key)
        return {'deleted': True}

    @app.get('/v1/jobs/{job_id}/edit', dependencies=[Depends(authorize)])
    def edit_form(job_id: str):
        import editor
        if jobs.get(job_id)['state'] != 'done':
            raise HTTPException(409, '需要先完成原视频制作')
        folder = jobs.root / 'outputs' / job_id
        return {'covers': editor.cover_options(folder, job_id),
                'shots': editor.shot_list(folder), 'edit': jobs.get_edit(job_id)}

    @app.get('/v1/jobs/{job_id}/edit-status', dependencies=[Depends(authorize)])
    def edit_status(job_id: str):
        jobs.get(job_id)
        return jobs.get_edit(job_id)

    @app.post('/v1/jobs/{job_id}/edit', status_code=202, dependencies=[Depends(authorize)])
    async def submit_edit(job_id: str, request: Request):
        import editor
        if jobs.get(job_id)['state'] != 'done':
            raise HTTPException(409, '需要先完成原视频制作')
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, '需要 JSON 请求')
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 50000:
                raise HTTPException(413, '文字设置过长')
        try:
            spec = editor.validate_edit(json.loads(content), jobs.root / 'outputs' / job_id)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, clean_error(exc)) from None
        return jobs.submit_edit(job_id, spec)

    @app.get('/v1/jobs/{job_id}/covers/{index}', dependencies=[Depends(authorize)])
    def cover(job_id: str, index: int):
        import editor
        if jobs.get(job_id)['state'] != 'done':
            raise HTTPException(409, '原视频还没完成')
        options = editor.cover_options(jobs.root / 'outputs' / job_id, job_id)
        if not 0 <= index < len(options):
            raise HTTPException(404, '封面候选不存在')
        return FileResponse(jobs.root / 'outputs' / job_id / options[index]['thumbnail'], media_type='image/jpeg')

    @app.get('/v1/jobs/{job_id}/{artifact}', dependencies=[Depends(authorize)])
    def download(job_id: str, artifact: str):
        job = jobs.get(job_id)
        if artifact == 'report' and job['state'] != 'done':
            report = jobs.root / 'outputs' / job_id / 'quality-report.json'
            if not report.is_file():
                raise HTTPException(404, '检查报告尚未生成')
            return FileResponse(report, filename='quality-report.json')
        if job['state'] != 'done':
            raise HTTPException(409, '成片尚未通过检查')
        if artifact == 'cover':
            if not job['result'].startswith('edit-'):
                raise HTTPException(404, '封面尚未生成')
            import media
            folder = jobs.root / 'outputs' / job_id
            cover = folder / (Path(job['result']).stem + '-cover.png')
            if not cover.is_file():
                temporary = folder / (cover.stem + '-' + uuid.uuid4().hex + '.png')
                try:
                    media.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', '0.12',
                               '-i', job['result'], '-frames:v', '1', temporary.name], cwd=folder)
                    temporary.replace(cover)
                finally:
                    temporary.unlink(missing_ok=True)
            return FileResponse(cover, filename='cover.png', media_type='image/png')
        if artifact in ('source-video', 'source-preview'):
            folder = (jobs.root / 'outputs' / job_id).resolve()
            report = json.loads((folder / 'quality-report.json').read_text(encoding='utf-8'))
            source = Path(report['video']).resolve()
            if not report.get('passed') or not source.is_relative_to(folder) or not source.is_file():
                raise HTTPException(404, '原始成片不存在')
            if artifact == 'source-preview':
                source = source_preview(source, folder)
            return FileResponse(source, filename=artifact + '.mp4', media_type='video/mp4')
        report_name = (job['result'][:-4] + '/quality-report.json') if job['result'].startswith('edit-') else 'quality-report.json'
        name = {'video': job['result'], 'report': report_name, 'captions': 'captions.srt',
                'plan': 'plan.json', 'cuts': 'cuts.json'}.get(artifact)
        if not name:
            raise HTTPException(404, '文件不存在')
        return FileResponse(jobs.root/'outputs'/job_id/name, filename=name)

    return app
