import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'video-material-match'/'scripts'))
import finish
import media
import music
import quality
from matcher import file_stamp


class FinishTests(unittest.TestCase):
    def test_short_shot_uses_additional_verified_footage_without_freeze(self):
        clips = [{'id': 1, 'verified_start': 0, 'verified_end': 1.5},
                 {'id': 2, 'verified_start': 0, 'verified_end': 2.5}]
        shots = finish.allocate_shots(clips,3.36,25)
        self.assertEqual(len(shots),2)
        self.assertAlmostEqual(sum(s['duration'] for s in shots),3.36)
        self.assertTrue(all(s['duration'] <= s['selected']['verified_end'] for s in shots))
        self.assertIsNone(finish.allocate_shots(clips[:1],3.36,25))

    def test_scene_uses_moving_filler_when_no_clip_matches_semantically(self):
        from unittest.mock import Mock
        clips = [{'id': i, 'source_start': 0, 'source_end': 5, 'path': f'{i}.mp4'} for i in range(3)]
        catalog = Mock()
        catalog.searcher.return_value = lambda _vector, _count: clips
        models = Mock()
        models.rerank.return_value = clips
        plan = {'fps': 25, 'scenes': [{'text': '园区步道', 'query': '园区步道',
                                     'start': 0, 'end': 12,
                                     'match': {'selected': None, 'attempts': []}}]}
        with patch('finish.matching_catalog', return_value=catalog), \
             patch('finish.judge_videos', return_value={'accepted': []}):
            finish.prepare_shots(plan, 'catalog', models, log=lambda _: None)
        scene = plan['scenes'][0]
        self.assertEqual(scene['visual_usage'], 'fallback-b-roll')
        self.assertAlmostEqual(sum(shot['duration'] for shot in scene['shots']), 12)
        self.assertTrue(all(shot['selected']['verified_end'] >= shot['duration'] for shot in scene['shots']))

    def test_error_overrides_model_pass_and_review_must_cover_end(self):
        result = {'passed':True,'audio_present':True,'watched_until':8,
                  'issues':[{'type':'audio_cutoff','severity':'error','problem':'Last word cut'}]}
        self.assertFalse(quality.validate_review(result,8,True)['passed'])
        with self.assertRaises(ValueError):
            quality.validate_review({**result,'watched_until':2},8,True)
        quiet = {'passed':True,'audio_present':True,'music_audible':False,
                 'speech_clear':True,'watched_until':8,'issues':[]}
        self.assertFalse(quality.validate_review(quiet,8,True,True)['passed'])

    def test_export_never_silently_truncates_narration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source.mp4'; audio=root/'speech.wav'
            media.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=64x64:r=25',
                       '-t','1','-c:v','libx264','-pix_fmt','yuv420p',str(source)])
            media.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=400:sample_rate=48000',
                       '-t','2','-c:a','pcm_s16le',str(audio)])
            plan={'fps':25,'seed':1,'narration':str(audio),'scenes':[{'text':'测试','start':0,'end':1,
                  'visual_usage':'contextual-b-roll','match':{'selected':{'path':str(source),'stamp':file_stamp(source),
                  'source_start':0,'verified_start':0,'verified_end':1}}}]}
            with self.assertRaisesRegex(ValueError,'配音时长'):
                media.render(plan,root/'out',64,64)
            self.assertNotIn('相关画面示意',(root/'out'/'display.srt').read_text(encoding='utf-8'))

    def test_mixed_audio_preserves_exact_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); voice=root/'speech.wav'; bgm=root/'music.wav'; video=root/'video.mp4'
            for target,frequency in ((voice,600),(bgm,200)):
                media.run(['ffmpeg','-v','error','-f','lavfi','-i',f'sine=frequency={frequency}:sample_rate=48000',
                           '-t',str(2 if target == voice else 1),'-c:a','pcm_s16le',str(target)])
            media.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=64x64:r=25',
                       '-t','2','-c:v','libx264',str(video)])
            result=music.mix(video,voice,bgm,root,2)
            self.assertAlmostEqual(media.duration(root/'mixed.wav'),2,places=3)
            plan={'fps':25,'narration':str(voice),'scenes':[{'end':2,'match':{'selected':{'id':1}}}]}
            self.assertEqual(quality.inspect_media(result,plan)['issues'],[])

    def test_uncertain_music_submission_is_not_resubmitted(self):
        import requests
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'SUNO_API_KEY':'test'}), \
             patch('music.requests.post',side_effect=requests.Timeout('timeout')) as post:
            with self.assertRaisesRegex(RuntimeError,'不确定'):
                music.generate('instrumental',tmp)
            with self.assertRaisesRegex(RuntimeError,'未获得任务 ID'):
                music.generate('instrumental',tmp)
            self.assertEqual(post.call_count,1)

    def test_final_mixed_video_is_reviewed_and_failure_blocks_delivery(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('finish.prepare_shots'), patch('finish.media.render',return_value=Path(tmp)/'video.mp4'), \
             patch('finish.music.mix',return_value=Path(tmp)/'video-music.mp4'), \
             patch('finish.quality.review',return_value={'passed':False,'segments':[{'issues':[
                 {'severity':'error','type':'audio_cutoff','problem':'Missing end','scene':1}]}]}) as reviewer:
            plan={'narration':'voice.wav','scenes':[{'end':2}]}
            with self.assertRaisesRegex(ValueError,'检查未通过'):
                finish.deliver(plan,tmp,music_file='music.wav',log=lambda _:None)
            self.assertEqual(reviewer.call_args.args[0],Path(tmp)/'video-music.mp4')

    def test_portrait_review_proxy_is_encodable_and_complete(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); video=root/'portrait.mp4'
            media.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=72x128:r=25',
                       '-t','1','-c:v','libx264',str(video)])
            plan={'fps':25,'scenes':[{'text':'test','start':0,'end':1,'match':{'selected':{'id':1}}}]}
            models=Mock()
            models.json.return_value={'passed':True,'audio_present':False,'watched_until':1,'issues':[]}
            result=quality.review(video,plan,root,models,log=lambda _:None)
            self.assertTrue(result['passed'])
            self.assertAlmostEqual(media.duration(root/'quality'/'review-001.mp4'),1,places=2)


if __name__=='__main__': unittest.main()
