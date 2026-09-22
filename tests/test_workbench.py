import json
import sys
import tempfile
import unittest
from pathlib import Path

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

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.offline:
            raise requests.ConnectionError('private token must never leak')
        response = requests.Response()
        response.status_code = 200
        response.headers['Content-Type'] = 'application/json'
        value = {}
        if path == '/health':
            value = {'status': 'ok', 'service': 'hainan-mixer'}
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
        self.assertEqual(self.client.get('/').status_code, 200)
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
