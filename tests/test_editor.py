import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'video-material-match' / 'scripts'))
import editor
import media


class CoverEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        for i, color in enumerate(('red', 'blue'), 1):
            media.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s=128x224:r=25',
                       '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                       str(self.folder / f'clip-{i:03}-01.mp4')])
        (self.folder / 'cuts.json').write_text(json.dumps([
            {'scene': 1, 'shot': 1, 'used_seconds': 1, 'freeze_seconds': 0},
            {'scene': 2, 'shot': 1, 'used_seconds': 1, 'freeze_seconds': 0}]), encoding='utf-8')
        (self.folder / 'plan.json').write_text(json.dumps({'fps': 25, 'scenes': [
            {'text': '带爸妈来海南过冬，住得舒服！', 'start': 0, 'end': 1},
            {'text': '这里可以散步，聊聊天。', 'start': 1, 'end': 2}]}), encoding='utf-8')
        (self.folder / 'concat.txt').write_text("file 'clip-001-01.mp4'\nfile 'clip-002-01.mp4'\n", encoding='utf-8')
        media.run(['ffmpeg', '-v', 'error', '-f', 'concat', '-safe', '1', '-i', 'concat.txt',
                   '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '2',
                   '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                   '-c:a', 'aac', 'video-music.mp4'], cwd=self.folder)

    def test_ten_cover_choices_span_real_uncaptioned_shots(self):
        options = editor.cover_options(self.folder, 'fixed-job-id')
        self.assertEqual(len(options), 10)
        self.assertTrue(all((self.folder / item['thumbnail']).is_file() for item in options))
        self.assertLess(options[0]['time'], 1)
        self.assertGreater(options[-1]['time'], 1)
        self.assertEqual(len(editor.shot_list(self.folder)), 2)
        plan = json.loads((self.folder / 'plan.json').read_text(encoding='utf-8'))
        plan['scenes'][1]['text'] = '每天都能散步聊天'
        (self.folder / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        self.assertTrue(editor.shot_list(self.folder)[1]['yellow'])

    def test_edit_validation_rejects_wrong_shots_and_control_text(self):
        editor.cover_options(self.folder, 'fixed-job-id')
        valid = {'cover_index': 8, 'cover_text': '带爸妈来海南过冬', 'titles': [
            {'white': '带爸妈过冬', 'yellow': '住得舒服'},
            {'white': '散步聊天', 'yellow': '每天都开心'}]}
        self.assertEqual(editor.validate_edit(valid, self.folder)['cover_index'], 8)
        for wrong in ({**valid, 'cover_index': 10}, {**valid, 'titles': valid['titles'][:1]},
                      {**valid, 'cover_text': 'x' * 100},
                      {**valid, 'titles': [{'white': '{\\move(0,0)}', 'yellow': 'A'}, valid['titles'][1]]},
                      {'cover_index': 0, 'cover_text': '海南过冬',
                       'title': {'white': '固定白字', 'yellow': ''}}):
            with self.assertRaises(ValueError):
                editor.validate_edit(wrong, self.folder)

    def test_selected_cover_is_first_half_second_and_audio_duration_is_unchanged(self):
        options = editor.cover_options(self.folder, 'fixed-job-id')
        blue = next(i for i, item in enumerate(options) if item['time'] > 1)
        spec = editor.validate_edit({'cover_index': blue, 'cover_text': '海南过冬',
            'titles': [{'white': '带爸妈过冬', 'yellow': '住得舒服'},
                       {'white': '每天散步', 'yellow': '真开心'}]}, self.folder)
        target = editor.render_overlay(self.folder, spec, self.folder / 'video-music.mp4')
        self.assertAlmostEqual(media.duration(target), 2, delta=.05)
        self.assertIn('audio', subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
            'stream=codec_type', '-of', 'csv=p=0', str(target)], text=True))
        def pixel(at):
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(target),
                '-vf', 'crop=1:1:10:100,format=rgb24', '-frames:v', '1', '-f', 'rawvideo', '-'])
            return tuple(raw[:3])
        first, second = pixel(.2), pixel(.7)
        self.assertGreater(first[2], first[0] + 80)
        self.assertGreater(second[0], second[2] + 80)

    def test_one_title_stays_on_screen_across_all_shots(self):
        editor.cover_options(self.folder, 'fixed-job-id')
        spec = editor.validate_edit({'cover_index': 0, 'cover_text': '海南过冬',
            'title': {'white': '三亚海棠湾康养旅居', 'yellow': '夫妻连续三年过冬'}}, self.folder)
        editor.render_overlay(self.folder, spec, self.folder / 'video-music.mp4')
        events = (self.folder / 'cover-titles.ass').read_text(encoding='utf-8')
        self.assertEqual(events.count(',White,,'), 1)
        self.assertEqual(events.count(',Yellow,,'), 1)
        self.assertIn('Dialogue: 0,0:00:00.50,0:00:02.00,White', events)
        self.assertIn('Dialogue: 0,0:00:00.50,0:00:02.00,Yellow', events)


if __name__ == '__main__':
    unittest.main()
