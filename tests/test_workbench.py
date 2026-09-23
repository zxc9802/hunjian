import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.server import Settings, Store, create_app


class FakeNas:
    """An idempotent upstream that can accept a task before its reply is lost."""
    def __init__(self):
        self.tasks = {}
        self.calls = []
        self.drop_reply = False
        self.offline = False
        self.state = 'queued'
        self.capabilities = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.offline:
            raise requests.ConnectionError('private token must never leak')
        response = requests.Response()
        response.status_code = 200
        response.headers['Content-Type'] = 'application/json'
        value = {}
        if path == '/health':
            value = {'status': 'ok', 'service': 'hainan-mixer', 'capabilities': self.capabilities}
        elif path.endswith('/edit') and method == 'GET':
            value = {'covers': [{'index': i, 'time': i + .3} for i in range(10)],
                     'shots': [{'scene': 1, 'shot': 1, 'start': 0, 'end': 3,
                                'white': '带爸妈过冬', 'yellow': '住得舒服'}],
                     'edit': {'state': 'not_started'}}
        elif path.endswith('/edit') and method == 'POST':
            value = {'state': 'queued', 'spec': kwargs['json']}
            response.status_code = 202
        elif path.endswith('/edit-status'):
            value = {'state': 'done', 'result': 'edit-test.mp4'}
        elif path.endswith('/resume') and method == 'POST':
            self.state = 'queued'
            value = {'id': 'a' * 32, 'state': 'queued', 'logs': ['从保存进度继续'],
                     'error': None, 'checkpoint': '动态镜头补齐'}
            response.status_code = 202
        elif '/covers/' in path:
            response.headers['Content-Type'] = 'image/jpeg'
            response._content = b'\xff\xd8\xff\xd9'
            response._content_consumed = True
            return response
        elif method == 'POST':
            key = kwargs['headers']['Idempotency-Key']
            self.tasks.setdefault(key, {'id': 'a' * 32, 'state': self.state})
            value = self.tasks[key]
            response.status_code = 202
            if self.drop_reply:
                self.drop_reply = False
                raise requests.Timeout('upstream accepted but reply lost')
        elif path.endswith('/video'):
            response.status_code = 206
            response.headers.update({'Content-Type': 'video/mp4', 'Content-Range': 'bytes 0-3/12', 'Content-Length': '4', 'Accept-Ranges': 'bytes'})
            response._content = b'test'
            response._content_consumed = True
            return response
        else:
            value = {'id': 'a' * 32, 'state': self.state, 'logs': ['匹配 1/2：游泳'], 'error': None}
        response._content = json.dumps(value).encode()
        response._content_consumed = True
        return response


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.settings = Settings('http://nas.local:8780', 'n' * 40, self.path)
        self.nas = FakeNas()
        self.app = create_app(self.settings, self.nas)
        self.client = TestClient(self.app, base_url='http://127.0.0.1:8788')
        self.headers = {'X-Workbench-Request': '1', 'Origin': 'http://127.0.0.1:8788'}
        self.spec = {'text':'平时可以游泳、健身。', 'emotion_alpha':.8, 'width':1080, 'height':1920, 'request_id':'test-request-001'}

    def submit(self, **kwargs):
        return self.client.post('/api/jobs', json={**self.spec, **kwargs}, headers=self.headers)

    def test_real_page_and_no_credentials_in_public_assets(self):
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertRegex(page.text, r'/static/app.js\?v=[a-f0-9]{12}')
        self.assertRegex(page.text, r'/static/style.css\?v=[a-f0-9]{12}')
        for path in ('/', '/static/app.js', '/api/session', '/api/connection'):
            response = self.client.get(path)
            self.assertNotIn(self.settings.nas_token, response.text)
            self.assertNotIn('nas.local', response.text)
        self.assertIn("frame-ancestors 'none'", self.client.get('/').headers['content-security-policy'])

    def test_validation_and_cross_site_rejection_before_nas(self):
        for value in (True, .9, .81, '0.8', None):
            self.assertEqual(self.submit(emotion_alpha=value).status_code, 400)
        for value in ('', 'a' * 6001, 5):
            self.assertEqual(self.submit(text=value).status_code, 400)
        self.assertEqual(self.submit(width=1080.0).status_code, 400)
        self.assertEqual(self.submit(request_id='../unsafe').status_code, 400)
        self.assertEqual(self.client.post('/api/jobs',json=self.spec).status_code,403)
        self.assertEqual(self.client.post('/api/jobs',json=self.spec,headers={**self.headers,'Origin':'https://other.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/jobs',headers={'Host':'evil.example'}).status_code,400)
        self.assertEqual(len(self.nas.calls),0)

    def test_music_upload_selection_and_failed_job_resume(self):
        key = 'music-library/' + 'a' * 32 + '/海边.mp3'
        self.assertEqual(self.submit(music_key='../private').status_code, 400)
        with patch('workbench.music_library.upload') as upload, \
             patch('workbench.music_library.list_tracks', return_value=[{'key':key,'name':'海边.mp3','size':256}]):
            self.assertEqual(self.client.get('/api/music').json()['tracks'][0]['key'], key)
            sent = self.client.put('/api/music', content=b'ID3' + b'0' * 253,
                headers={**self.headers, 'X-Music-Name': '%E6%B5%B7%E8%BE%B9.mp3',
                         'Content-Type': 'application/octet-stream'})
            self.assertEqual(sent.status_code, 201)
            self.assertIn('/海边.mp3', sent.json()['key'])
            upload.assert_called_once()
        job = self.submit(music_key=key).json()
        self.assertEqual(job['spec']['music_key'], key)
        self.nas.state = 'failed'
        self.assertEqual(self.client.get('/api/jobs/test-request-001').json()['state'], 'failed')
        resumed = self.client.post('/api/jobs/test-request-001/resume', headers=self.headers)
        self.assertEqual(resumed.status_code, 202)
        self.assertEqual(resumed.json()['state'], 'queued')
        self.assertEqual(resumed.json()['snapshot']['checkpoint'], '动态镜头补齐')

    def test_music_preview_streams_each_format_and_byte_ranges(self):
        for suffix, mime in (('mp3', 'audio/mpeg'), ('wav', 'audio/wav'), ('m4a', 'audio/mp4')):
            key = 'music-library/' + 'a' * 32 + '/海边.' + suffix
            for partial in (False, True):
                with self.subTest(suffix=suffix, partial=partial):
                    stream = io.BytesIO(b'ID3' if partial else b'ID3audio')
                    upstream = {'Body': Mock(get_raw_stream=Mock(return_value=stream)),
                                'Content-Length': str(len(stream.getvalue()))}
                    if partial:
                        upstream['Content-Range'] = 'bytes 0-2/8'
                    with patch('workbench.music_library.client') as cos:
                        cos.return_value.get_object.return_value = upstream
                        response = self.client.get('/api/music/preview', params={'key': key},
                            headers={'Range': 'bytes=0-2'} if partial else {})
                        self.assertEqual(response.status_code, 206 if partial else 200)
                        self.assertEqual(response.content, b'ID3' if partial else b'ID3audio')
                        self.assertEqual(response.headers['content-type'], mime)
                        self.assertEqual(response.headers['accept-ranges'], 'bytes')
                        self.assertEqual(response.headers['cache-control'], 'no-store')
                        if partial:
                            self.assertEqual(response.headers['content-range'], 'bytes 0-2/8')
                        kwargs = cos.return_value.get_object.call_args.kwargs
                        self.assertEqual(kwargs['Key'], key)
                        self.assertEqual(kwargs.get('Range'), 'bytes=0-2' if partial else None)
                    self.assertTrue(stream.closed)

    def test_music_preview_rejects_invalid_input_and_hides_provider_errors(self):
        from qcloud_cos.cos_exception import CosServiceError
        key = 'music-library/' + 'a' * 32 + '/海边.mp3'
        with patch('workbench.music_library.client') as cos:
            for invalid in ('', '../private', 'other/file.mp3'):
                self.assertEqual(self.client.get('/api/music/preview', params={'key': invalid}).status_code, 400)
            for invalid in ('bytes=', 'bytes=2-1', 'bytes=0-1,4-5'):
                self.assertEqual(self.client.get('/api/music/preview', params={'key': key},
                    headers={'Range': invalid}).status_code, 416)
            cos.assert_not_called()
            for status, expected in ((404, 404), (416, 416), (403, 503)):
                cos.return_value.get_object.side_effect = CosServiceError('GET', 'private provider detail', status)
                response = self.client.get('/api/music/preview', params={'key': key})
                self.assertEqual(response.status_code, expected)
                self.assertNotIn('private provider detail', response.text)

    def test_music_preview_requires_login(self):
        settings = Settings('http://nas.local:8780', 'n'*40, self.path,
                            'https://video.example.com', 'a-strong-test-password')
        client = TestClient(create_app(settings, self.nas), base_url=settings.public_url)
        with patch('workbench.music_library.client') as cos:
            self.assertEqual(client.get('/api/music/preview', params={
                'key': 'music-library/' + 'a'*32 + '/海边.mp3'}).status_code, 401)
            cos.assert_not_called()

    def test_lost_submission_recovers_with_same_key_even_after_gateway_restart(self):
        self.nas.drop_reply = True
        first = self.submit()
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()['state'],'uncertain')
        self.assertEqual(len(self.nas.tasks),1)
        self.client = TestClient(create_app(self.settings,self.nas),base_url='http://127.0.0.1:8788')
        recovered = self.client.post('/api/jobs/test-request-001/reconcile',headers=self.headers)
        self.assertEqual(recovered.json()['state'],'queued')
        self.assertEqual(len(self.nas.tasks),1)
        self.assertEqual(self.submit().json()['nas_id'], 'a'*32)
        self.assertEqual(self.submit(text='另一个任务').status_code,409)
        self.assertEqual(len(self.nas.tasks),1)

    def test_pending_and_failed_artifacts_are_blocked_and_completed_range_is_streamed(self):
        self.submit()
        url = '/api/jobs/test-request-001/artifacts/video'
        self.assertEqual(self.client.get(url).status_code,409)
        self.nas.state = 'done'
        job = self.client.get('/api/jobs/test-request-001').json()
        self.assertEqual(job['state'],'done')
        response = self.client.get(url,headers={'Range':'bytes=0-3'})
        self.assertEqual(response.status_code,206)
        self.assertEqual(response.content,b'test')
        self.assertEqual(response.headers['content-range'],'bytes 0-3/12')
        self.assertEqual(self.nas.calls[-1][2]['headers']['Range'],'bytes=0-3')
        self.assertEqual(self.client.get('/api/jobs/test-request-001/artifacts/private.env').status_code,404)
        self.assertEqual(self.client.get('/api/jobs/unknown/artifacts/video').status_code,404)

    def test_offline_nas_keeps_task_and_does_not_resubmit_or_leak_exception(self):
        self.submit()
        self.nas.offline = True
        response = self.client.get('/api/jobs/test-request-001')
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private token',response.text)
        history = self.client.get('/api/jobs').json()['jobs']
        self.assertEqual(history[0]['state'],'queued')
        self.assertEqual(len(self.nas.tasks),1)

    def test_auth_cookie_logout_and_rate_limit(self):
        settings = Settings('http://nas.local:8780','n'*40,self.path,'https://video.example.com','a-strong-test-password')
        client = TestClient(create_app(settings,self.nas),base_url=settings.public_url)
        headers = {'X-Workbench-Request':'1','Origin':settings.public_url}
        self.assertEqual(client.get('/api/jobs').status_code,401)
        response = client.post('/api/login',json={'password':settings.password},headers=headers)
        self.assertEqual(response.status_code,200)
        for flag in ('HttpOnly','Secure','SameSite=strict'):
            self.assertIn(flag,response.headers['set-cookie'])
        self.assertEqual(client.get('/api/jobs').status_code,200)
        self.assertEqual(client.post('/api/logout',headers=headers).status_code,200)
        self.assertEqual(client.get('/api/jobs').status_code,401)
        for _ in range(5):
            self.assertEqual(client.post('/api/login',json={'password':'wrong'},headers=headers).status_code,401)
        self.assertEqual(client.post('/api/login',json={'password':settings.password},headers=headers).status_code,429)

    def test_public_deployment_refuses_missing_auth_or_plain_http(self):
        for url, password in [('http://video.example.com','strong-password'), ('https://video.example.com','')]:
            with self.assertRaises(ValueError):
                Settings('http://nas.local:8780','n'*40,self.path,url,password)

    def test_job_history_survives_restart_without_nas_calls(self):
        self.submit()
        self.app.state.store.update('test-request-001','running',nas_id='a'*32)
        self.assertEqual(Store(self.path).get('test-request-001')['state'],'running')
        self.assertEqual(len(self.nas.tasks),1)

    def test_history_expires_after_three_days_without_removing_active_jobs_or_files(self):
        now, retention = 2_000_000_000, 3 * 86400
        store = self.app.state.store
        voice = self.path / 'speaker-reference.mp3'
        voice.write_bytes(b'keep this file')
        with patch('workbench.server.time.time', return_value=now - retention):
            for state in ('done', 'failed', 'interrupted', 'rejected', 'queued', 'running', 'submitting', 'uncertain'):
                store.reserve('old-' + state, self.spec)
                store.update('old-' + state, state)
        with patch('workbench.server.time.time', return_value=now - retention + 1):
            store.reserve('recent-done', self.spec)
            store.update('recent-done', 'done')
        with patch('workbench.server.time.time', return_value=now):
            response = self.client.get('/api/jobs')
        self.assertEqual(response.status_code, 200)
        expected = {'recent-done', 'old-queued', 'old-running', 'old-submitting', 'old-uncertain'}
        self.assertEqual({job['id'] for job in response.json()['jobs']}, expected)
        with store.connect() as db:
            self.assertEqual({row['id'] for row in db.execute('SELECT id FROM jobs')}, expected)
        self.assertEqual(voice.read_bytes(), b'keep this file')
        self.assertEqual(self.nas.calls, [])

    def test_expired_history_cannot_be_opened_directly(self):
        with patch('workbench.server.time.time', return_value=2_000_000_000):
            self.submit()
            self.app.state.store.update('test-request-001', 'done')
        with patch('workbench.server.time.time', return_value=2_000_000_000 + 3 * 86400):
            self.assertEqual(self.client.get('/api/jobs/test-request-001').status_code, 404)
            self.assertEqual(self.client.get('/api/jobs/test-request-001/artifacts/video').status_code, 404)

    def test_startup_cleans_expired_history(self):
        with patch('workbench.server.time.time', return_value=2_000_000_000):
            self.submit()
            self.app.state.store.update('test-request-001', 'done')
        with patch('workbench.server.time.time', return_value=2_000_000_000 + 3 * 86400):
            restarted = Store(self.path)
        with restarted.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)

    def test_health_check_cleans_expired_history_without_nas_access(self):
        with patch('workbench.server.time.time', return_value=2_000_000_000):
            self.app.state.store.reserve('expired-record', self.spec)
            self.app.state.store.update('expired-record', 'done')
        with patch('workbench.server.time.time', return_value=2_000_000_000 + 3 * 86400):
            self.assertEqual(self.client.get('/health').status_code, 200)
        with self.app.state.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)
        self.assertEqual(self.nas.calls, [])

    def test_status_refresh_does_not_extend_retention_but_resuming_does(self):
        store = self.app.state.store
        with patch('workbench.server.time.time', return_value=2_000_000_000):
            self.submit()
            self.nas.state = 'failed'
            first = self.client.get('/api/jobs/test-request-001').json()
        with patch('workbench.server.time.time', return_value=2_000_000_100):
            refreshed = self.client.get('/api/jobs/test-request-001').json()
            self.assertEqual(refreshed['updated'], first['updated'])
            resumed = self.client.post('/api/jobs/test-request-001/resume', headers=self.headers)
            self.assertEqual(resumed.status_code, 202)
        with patch('workbench.server.time.time', return_value=2_000_000_200):
            store.update('test-request-001', 'done')
        with patch('workbench.server.time.time', return_value=2_000_000_000 + 3 * 86400):
            self.assertEqual(self.client.get('/api/jobs/test-request-001').json()['state'], 'done')
        with patch('workbench.server.time.time', return_value=2_000_000_200 + 3 * 86400):
            self.assertEqual(self.client.get('/api/jobs/test-request-001').status_code, 404)

    def test_uploaded_voice_enables_cloud_preview_and_survives_restart(self):
        self.settings.voice_file = self.path / 'missing-image-voice.mp3'
        audio = b'ID3\x04\x00\x00\x00\x00\x00\x00preview-fixture'
        self.assertFalse(self.client.get('/api/connection').json()['voice_available'])
        self.assertEqual(self.client.get('/api/reference/voice').status_code, 404)
        uploaded = self.client.put('/api/reference/voice', content=audio,
                                   headers={**self.headers, 'Content-Type': 'audio/mpeg'})
        self.assertEqual(uploaded.status_code, 200)
        client = TestClient(create_app(self.settings, self.nas), base_url=self.settings.public_url)
        self.assertTrue(client.get('/api/connection').json()['voice_available'])
        self.assertEqual(client.get('/api/reference/voice').content, audio)
        partial = client.get('/api/reference/voice', headers={'Range': 'bytes=0-2'})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b'ID3')
        self.assertEqual(partial.headers['content-range'], f'bytes 0-2/{len(audio)}')
        self.assertEqual(partial.headers['cache-control'], 'no-store')

    def test_cover_editor_proxies_only_for_completed_authenticated_jobs(self):
        self.assertFalse(self.client.get('/api/connection').json()['cover_editor_available'])
        self.nas.capabilities = ['cover_editor']
        self.assertTrue(self.client.get('/api/connection').json()['cover_editor_available'])
        url = '/api/jobs/test-request-001/edit'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.submit()
        self.assertEqual(self.client.get(url).status_code, 409)
        self.nas.state = 'done'
        self.client.get('/api/jobs/test-request-001')
        self.assertEqual(len(self.client.get(url).json()['covers']), 10)
        body = {'cover_index': 0, 'cover_text': '海南过冬',
                'titles': [{'white': '带爸妈过冬', 'yellow': '住得舒服'}]}
        self.assertEqual(self.client.post(url, json=body, headers=self.headers).status_code, 202)
        self.assertEqual(self.nas.calls[-1][2]['json'], body)
        self.assertEqual(self.client.get(url + '-status').json()['state'], 'done')
        image = self.client.get('/api/jobs/test-request-001/covers/0')
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.content, b'\xff\xd8\xff\xd9')
        self.assertEqual(image.headers['content-type'], 'image/jpeg')
        self.assertEqual(self.client.get('/api/jobs/test-request-001/covers/10').status_code, 404)
        settings = Settings('http://nas.local:8780', 'n'*40, self.path,
                            'https://video.example.com', 'a-strong-test-password')
        private = TestClient(create_app(settings, self.nas), base_url=settings.public_url)
        self.assertEqual(private.get(url).status_code, 401)
        self.assertEqual(private.get('/api/jobs/test-request-001/covers/0').status_code, 401)

    def test_voice_upload_requires_login_and_same_origin(self):
        settings = Settings('http://nas.local:8780', 'n'*40, self.path,
                            'https://video.example.com', 'a-strong-test-password')
        client = TestClient(create_app(settings, self.nas), base_url=settings.public_url)
        headers = {'X-Workbench-Request': '1', 'Origin': settings.public_url, 'Content-Type': 'audio/mpeg'}
        self.assertEqual(client.put('/api/reference/voice', content=b'ID3test', headers=headers).status_code, 401)
        self.assertEqual(client.get('/api/reference/voice').status_code, 401)
        self.assertEqual(client.put('/api/reference/voice', content=b'ID3test',
                                   headers={**headers, 'Origin': 'https://other.example'}).status_code, 403)
        self.assertFalse((self.path / 'speaker-reference.mp3').exists())

    def test_invalid_voice_upload_preserves_previous_preview(self):
        self.settings.voice_file = self.path / 'missing-image-voice.mp3'
        audio = b'ID3\x04\x00\x00\x00\x00\x00\x00preview-fixture'
        headers = {**self.headers, 'Content-Type': 'audio/mpeg'}
        self.assertEqual(self.client.put('/api/reference/voice', content=audio, headers=headers).status_code, 200)
        for data, content_type, status in ((audio, 'text/plain', 415), (b'', 'audio/mpeg', 400),
                                           (b'<html>not audio</html>', 'audio/mpeg', 400),
                                           (b'ID3' + b'x' * (5 * 1024 * 1024), 'audio/mpeg', 413)):
            response = self.client.put('/api/reference/voice', content=data,
                                       headers={**headers, 'Content-Type': content_type})
            self.assertEqual(response.status_code, status)
            self.assertEqual(self.client.get('/api/reference/voice').content, audio)


if __name__ == '__main__':
    unittest.main()
