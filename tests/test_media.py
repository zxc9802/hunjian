import json
import sys
import tempfile
import unittest
import subprocess
import numpy as np
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'video-material-match'/'scripts'))
import media
from matcher import file_stamp


class MediaTimingTests(unittest.TestCase):
    def test_mixed_hdr_sdr_export_is_709_and_sdr_pixels_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            hdr=root/'hdr.mkv'; sdr=root/'sdr.mp4'
            for target,primaries,transfer,matrix,codec in (
                (hdr,'bt2020','arib-std-b67','bt2020nc','ffv1'),
                (sdr,'bt709','bt709','bt709','libx264')):
                media.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=0xd29a72:s=64x64:r=25',
                    '-t','1','-c:v',codec,'-pix_fmt','yuv420p10le' if target==hdr else 'yuv420p',
                    '-color_primaries',primaries,'-color_trc',transfer,'-colorspace',matrix,
                    '-color_range','tv',str(target)])
            plan={'fps':25,'seed':1,'scenes':[]}
            for i,source in enumerate((hdr,sdr)):
                plan['scenes'].append({'text':'','start':i,'end':i+1,'match':{'selected':{
                    'path':str(source),'stamp':file_stamp(source),'source_start':0,
                    'verified_start':0,'verified_end':1}}})
            target=media.render(plan,root/'out',64,64)
            info=json.loads(media.run(['ffprobe','-v','error','-select_streams','v:0',
                '-show_entries','stream=color_primaries,color_transfer,color_space,color_range',
                '-of','json',str(target)]))['streams'][0]
            self.assertEqual(info,{'color_range':'tv','color_space':'bt709','color_transfer':'bt709','color_primaries':'bt709'})
            def pixels(path,seek):
                b=subprocess.check_output(['ffmpeg','-v','error','-ss',str(seek),'-i',str(path),
                    '-vf','crop=32:32:16:0,format=rgb24','-frames:v','1','-f','rawvideo','-'])
                return np.frombuffer(b,dtype=np.uint8).astype(float)
            self.assertLess(np.abs(pixels(sdr,.4)-pixels(target,1.4)).mean(),3)

    def test_clip_fills_exact_scene_duration_when_decoder_ends_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'short.mp4'
            media.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=64x64:r=25',
                       '-t','0.4','-c:v','libx264','-pix_fmt','yuv420p',str(source)])
            plan={'fps':25,'seed':1,'scenes':[{'text':'测试','start':0,'end':1.2,
                  'match':{'selected':{'path':str(source),'stamp':file_stamp(source),
                  'source_start':0,'verified_start':0,'verified_end':.44}}}]}
            target=media.render(plan,root/'out',width=64,height=64)
            info=json.loads(media.run(['ffprobe','-v','error','-show_entries','stream=nb_frames',
                                      '-of','json',str(target)]))
            self.assertEqual(int(info['streams'][0]['nb_frames']),30)
            self.assertAlmostEqual(media.duration(target),1.2,places=3)


if __name__=='__main__':unittest.main()
