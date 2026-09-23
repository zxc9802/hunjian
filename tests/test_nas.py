import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'video-material-match'/'scripts'), str(ROOT/'video-material-match'/'deploy')]
from fastapi.testclient import TestClient
from nas_api import create_app, Jobs, generate
from migrate_catalog import migrate, source_relative, vector_digest
from matcher import Catalog, file_stamp
from api import Models


class NasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = create_app(self.root, 'x'*40, start_worker=False)
        self.client = TestClient(self.app)
        self.headers = {'Authorization': 'Bearer '+'x'*40, 'Idempotency-Key': 'nas-test-001'}

    def submit(self, **body):
        return self.client.post('/v1/jobs', headers=self.headers, json={'text': '这里可以游泳。', **body})

    def test_auth_validation_and_idempotency(self):
        self.assertIn('cover_editor', self.client.get('/health').json()['capabilities'])
        self.assertEqual(self.client.post('/v1/jobs', json={'text': '测试'}).status_code, 401)
        self.assertEqual(self.submit(emotion_alpha=.9).status_code, 400)
        self.assertEqual(self.submit(emotion_alpha=.81).status_code, 400)
        self.assertEqual(self.submit(width=9999).status_code, 400)
        self.assertEqual(self.submit(width=1080.0).status_code, 400)
        a, b = self.submit(), self.submit()
        self.assertEqual(a.status_code, 202)
        self.assertEqual(a.json()['id'], b.json()['id'])
        self.assertEqual(self.submit(text='不同文案').status_code, 409)
        job_id = a.json()['id']
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}').status_code, 401)
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}/video', headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post('/v1/jobs', headers={**self.headers,'Origin':'https://other.test'},
                                         json={'text':'测试'}).status_code, 403)

    def test_persistent_queue_and_interrupted_job_not_resubmitted(self):
        job_id = self.submit().json()['id']
        restarted = Jobs(self.root)
        self.assertEqual(restarted.get(job_id)['state'], 'queued')
        with restarted.connect() as db:
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
        recovered = Jobs(self.root)
        self.assertEqual(recovered.get(job_id)['state'], 'interrupted')
        self.assertFalse(recovered.run_one(lambda *_: self.fail('重复下单')))
        saved = self.root / 'outputs' / job_id / 'voice-001.wav'
        saved.parent.mkdir(parents=True)
        saved.write_bytes(b'paid-voice')
        response = self.client.post(f'/v1/jobs/{job_id}/resume', headers=self.headers)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['id'], job_id)
        self.assertEqual(response.json()['state'], 'queued')
        self.assertEqual(saved.read_bytes(), b'paid-voice')
        self.assertEqual(self.client.post(f'/v1/jobs/{job_id}/resume', headers=self.headers).status_code, 409)

    def test_only_reviewed_actual_artifact_delivered(self):
        job_id = self.submit().json()['id']

        def render(spec, folder, log):
            result = folder/'video-music.mp4'
            result.write_bytes(b'actual-rendered-by-test')
            (folder/'cuts.json').write_text('[{"scene":1}]')
            (folder/'quality-report.json').write_text(json.dumps({
                'passed': True, 'sha256': hashlib.sha256(result.read_bytes()).hexdigest()}))
            return result

        jobs = self.app.state.jobs
        self.assertTrue(jobs.run_one(render))
        self.assertEqual(jobs.get(job_id)['state'], 'done')
        response = self.client.get(f'/v1/jobs/{job_id}/video', headers=self.headers)
        self.assertEqual(response.content, b'actual-rendered-by-test')
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}/cuts', headers=self.headers).json(), [{'scene':1}])
        self.assertFalse(jobs.run_one(render))
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}/jobs.sqlite3', headers=self.headers).status_code, 404)

    def test_cover_edit_queue_reuses_original_and_delivers_only_reviewed_revision(self):
        import media
        self.assertEqual(self.client.post('/v1/jobs/missing/edit', headers=self.headers,
            json={'cover_index': 0}).status_code, 404)
        job_id = self.submit().json()['id']
        self.assertEqual(self.client.post(f'/v1/jobs/{job_id}/edit', headers=self.headers,
            json={'cover_index': 0}).status_code, 409)

        def original(spec, folder, log):
            clip = folder / 'clip-001-01.mp4'
            media.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=128x224:r=25',
                       '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)])
            (folder / 'plan.json').write_text(json.dumps({'fps': 25,
                'scenes': [{'text': '这里可以游泳。', 'start': 0, 'end': 1}]}), encoding='utf-8')
            (folder / 'cuts.json').write_text(json.dumps([{'scene': 1, 'shot': 1,
                'used_seconds': 1, 'freeze_seconds': 0}]), encoding='utf-8')
            (folder / 'quality-report.json').write_text(json.dumps({'passed': True,
                'video': str(clip.resolve()), 'sha256': hashlib.sha256(clip.read_bytes()).hexdigest()}))
            return clip

        self.assertTrue(self.app.state.jobs.run_one(original))
        original_video = self.client.get(f'/v1/jobs/{job_id}/video', headers=self.headers).content
        form = self.client.get(f'/v1/jobs/{job_id}/edit', headers=self.headers)
        self.assertEqual(form.status_code, 200)
        self.assertEqual(len(form.json()['covers']), 10)
        self.assertEqual(len(form.json()['shots']), 1)
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}/covers/0', headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}/covers/0').status_code, 401)
        valid = {'cover_index': 0, 'cover_text': '海南过冬',
                 'titles': [{'white': '带爸妈来海南', 'yellow': '住得很舒服'}]}
        url = f'/v1/jobs/{job_id}/edit'
        self.assertEqual(self.client.post(url, headers=self.headers, json={**valid, 'cover_index': 10}).status_code, 400)
        first = self.client.post(url, headers=self.headers, json=valid)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(self.client.post(url, headers=self.headers, json=valid).status_code, 202)
        self.assertEqual(self.client.post(url, headers=self.headers,
            json={**valid, 'cover_text': '另一张标题'}).status_code, 409)
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}/video', headers=self.headers).content, original_video)

        def revised(spec, folder, log):
            target = folder / 'edit-test.mp4'
            target.write_bytes(b'reviewed-edited-video')
            report_dir = folder / target.stem
            report_dir.mkdir()
            (report_dir / 'quality-report.json').write_text(json.dumps({'passed': True,
                'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))
            return target

        self.assertTrue(self.app.state.jobs.run_edit(revised))
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}/edit-status', headers=self.headers).json()['state'], 'done')
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}/video', headers=self.headers).content, b'reviewed-edited-video')
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}/report', headers=self.headers).json()['passed'], True)
        self.assertFalse(self.app.state.jobs.run_edit(revised))

    def test_mismatched_quality_report_fails(self):
        job_id = self.submit().json()['id']

        def render(spec, folder, log):
            result = folder/'video.mp4'
            result.write_bytes(b'changed')
            (folder/'quality-report.json').write_text('{"passed":true,"sha256":"old"}')
            return result

        self.app.state.jobs.run_one(render)
        self.assertEqual(self.app.state.jobs.get(job_id)['state'], 'failed')

    def test_operator_resume_reuses_saved_plan_and_paid_voice_cache(self):
        plan={'script':'原文案', 'scenes':[{'text':'原分段', 'match':{'selected':None}}]}
        (self.root/'plan.json').write_text(json.dumps(plan),encoding='utf-8')
        spec={'text':'原文案','emotion_alpha':.8,'width':1080,'height':1920}
        with patch('matcher.create_plan') as create, patch('voice.synthesize_plan') as synth, \
             patch('finish.deliver',return_value=self.root/'video.mp4') as deliver:
            generate(spec,self.root,lambda m:None)
            create.assert_not_called()
            self.assertEqual(synth.call_args.args[0],plan)
            self.assertEqual(synth.call_args.args[1],self.root)
            self.assertEqual(deliver.call_args.args[0],plan)
            with self.assertRaisesRegex(ValueError,'文案不一致'):
                generate({**spec,'text':'其他文案'},self.root,lambda m:None)
            self.assertEqual(synth.call_count,1)

    def test_migration_preserves_vectors_and_rejects_changed_original(self):
        seed, media, target = self.root/'seed', self.root/'media', self.root/'catalog'
        media.mkdir()
        source = media/'泳池.mp4'
        source.write_bytes(b'original source bytes')
        (seed/'proxies').mkdir(parents=True)
        (seed/'proxies'/'one.mp4').write_bytes(b'proxy')
        models = Models()
        catalog = Catalog(seed, {'embedding': models.embed_url, 'llm': models.llm_url,
                                'chunk_seconds':8, 'proxy_fps':1, 'schema':1})
        catalog.add({'path':r'Z:\泳池.mp4', 'stamp':file_stamp(source), 'start':0, 'end':8,
                     'proxy':r'D:\old\proxies\one.mp4', 'description':'游泳池'}, [1,0,0])
        before = vector_digest(catalog.db)
        catalog.close()
        result = migrate(seed, target, media, 'Z:\\')
        self.assertEqual(result['vector_sha256'], before)
        self.assertEqual(result['vectors'], 1)
        self.assertEqual(result['embeddings_recomputed'], 0)
        self.assertEqual(migrate(seed, target, media, 'Z:\\'), result)
        source.write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError, '已变化'):
            migrate(seed, target, media, 'Z:\\')
        with self.assertRaises(ValueError):
            source_relative(r'Z:\..\secret.mp4', 'Z:\\')
        with self.assertRaises(ValueError):
            source_relative(r'C:\secret.mp4', 'Z:\\')


if __name__ == '__main__':
    unittest.main()
