import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'video-material-match/scripts'))
from video_storage import delete_videos


class VideoDeletionTests(unittest.TestCase):
    def test_deletes_all_revisions_in_only_the_jobs_prefix(self):
        jid = 'a' * 32
        prefix = 'video-jobs/' + jid + '/'
        cos = Mock()
        cos.list_objects.side_effect = [
            {'Contents': [{'Key': prefix + 'first.mp4'}], 'IsTruncated': 'true', 'NextMarker': 'next'},
            {'Contents': [{'Key': prefix + 'second.mp4'}], 'IsTruncated': 'false'}]
        cos.delete_objects.return_value = {}
        with patch.dict('os.environ', {'COS_SECRET_ID': 'test', 'COS_SECRET_KEY': 'test'}), \
             patch('qcloud_cos.CosS3Client', return_value=cos):
            delete_videos(jid)
        self.assertEqual(cos.list_objects.call_count, 2)
        self.assertEqual(cos.list_objects.call_args.kwargs['Prefix'], prefix)
        self.assertEqual(cos.list_objects.call_args.kwargs['Marker'], 'next')
        self.assertEqual(cos.delete_objects.call_count, 2)

    def test_partial_failure_is_not_reported_as_deleted(self):
        cos = Mock()
        cos.list_objects.return_value = {'Contents': [{'Key': 'video-jobs/' + 'a' * 32 + '/video.mp4'}]}
        cos.delete_objects.return_value = {'Error': [{'Code': 'AccessDenied'}]}
        with patch.dict('os.environ', {'COS_SECRET_ID': 'test', 'COS_SECRET_KEY': 'test'}), \
             patch('qcloud_cos.CosS3Client', return_value=cos):
            with self.assertRaises(RuntimeError):
                delete_videos('a' * 32)
            with self.assertRaises(ValueError):
                delete_videos('../other')
