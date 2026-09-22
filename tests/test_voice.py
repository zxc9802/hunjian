import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'video-material-match' / 'scripts'))
import voice


class VoiceTests(unittest.TestCase):
    def test_emotion_default_and_boundaries(self):
        self.assertEqual(voice.validate_emotion(), .8)
        for value in (.1, .15, .8, .85):
            self.assertEqual(voice.validate_emotion(value), value)

    def test_server_rejects_out_of_range_nonfinite_and_wrong_step(self):
        for value in (0, .09, .86, 1, .82, float('nan'), float('inf'), True, None, 'oops'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                voice.validate_emotion(value)

    def test_invalid_emotion_cannot_trigger_network(self):
        with patch('voice.requests.post') as post:
            with self.assertRaises(ValueError):
                voice.synthesize_plan({'scenes': []}, 'unused', emotion_alpha=.9)
            post.assert_not_called()

    def test_main_voice_and_emotion_use_separate_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            speaker=Path(tmp)/'user-voice.mp3'
            emotion=Path(tmp)/'emotion.wav'
            plan={'fps':25,'scenes':[{'text':'测试文案','start':0,'end':1}]}
            def upload(path,folder,kind):
                return f'https://example.com/{kind}.wav',f'{kind}-hash'
            with patch('voice.upload_reference',side_effect=upload) as uploads, \
                 patch('voice.synthesize',return_value=Path(tmp)/'tts.wav') as synth, \
                 patch('voice.media.duration',return_value=1),patch('voice.media.run'):
                voice.synthesize_plan(plan,tmp,emotion_file=emotion,speaker_file=speaker,log=lambda m:None)
            self.assertEqual(synth.call_args.args[1:3],('https://example.com/speaker.wav','https://example.com/emotion.wav'))
            self.assertEqual(plan['voice_settings']['speaker_source'],str(speaker))
            self.assertEqual(plan['voice_settings']['speaker_sha256'],'speaker-hash')
            self.assertEqual(plan['voice_settings']['emotion_sha256'],'emotion-hash')

    def test_polling_server_error_retries_same_task_without_resubmitting(self):
        created=Mock(status_code=200)
        created.json.return_value={'task_id':'existing-job'}
        busy=Mock(status_code=500)
        success=Mock(status_code=200)
        success.json.return_value={'state':'SUCCESS','audio_url':'https://example.com/audio.wav'}
        download=Mock(status_code=200,content=b'test-audio')
        with tempfile.TemporaryDirectory() as tmp, \
             patch('voice.headers',return_value={}), \
             patch('voice.requests.post',return_value=created) as post, \
             patch('voice.requests.get',side_effect=[busy,success,download]) as get, \
             patch('voice.time.sleep'), patch('voice.media.duration',return_value=1):
            path=voice.synthesize('测试','https://example.com/speaker.wav',
                                  'https://example.com/emotion.wav','hash',.8,tmp,log=lambda m:None)
            self.assertEqual(path.read_bytes(),b'test-audio')
            self.assertEqual(post.call_count,1)
            self.assertEqual(get.call_args_list[0].kwargs['params'],{'task_id':'existing-job'})
            self.assertEqual(get.call_args_list[1].kwargs['params'],{'task_id':'existing-job'})

    def test_domestic_download_recovers_existing_paid_task(self):
        import requests
        created=Mock(status_code=200)
        created.json.return_value={'task_id':'already-paid'}
        success=Mock(status_code=200)
        success.json.return_value={'state':'SUCCESS','audio_url':'https://file.302.ai/gpt/audio/a.wav?x=1'}
        downloaded=Mock(status_code=200,content=b'recovered-audio')
        with tempfile.TemporaryDirectory() as tmp, \
             patch('voice.headers',return_value={}), \
             patch('voice.requests.post',return_value=created) as post, \
             patch('voice.time.sleep'), patch('voice.media.duration',return_value=1):
            args=('测试','https://example.com/speaker.wav','https://example.com/emotion.wav','hash',.8,tmp)
            with patch('voice.requests.get',side_effect=[success]+[requests.ConnectionError('offline')]*4):
                with self.assertRaisesRegex(RuntimeError,'已合成但下载'):
                    voice.synthesize(*args,log=lambda m:None)
            with patch('voice.TTS_BASE_URL','https://api.302ai.cn'), \
                 patch('voice.requests.get',side_effect=[success,downloaded]) as get:
                result=voice.synthesize(*args,log=lambda m:None)
                self.assertEqual(result.read_bytes(),b'recovered-audio')
                self.assertEqual(get.call_args_list[0].kwargs['params'],{'task_id':'already-paid'})
                self.assertEqual(get.call_args_list[1].args[0],'https://file.302ai.cn/gpt/audio/a.wav?x=1')
                self.assertNotIn('headers',get.call_args_list[1].kwargs)
            self.assertEqual(post.call_count,1)


if __name__ == '__main__':
    unittest.main()
