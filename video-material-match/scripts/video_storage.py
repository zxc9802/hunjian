"""Publish reviewed videos to the private Singapore COS bucket."""
import os
import re
from pathlib import Path


BUCKET = 'hunjian-1410143389'
REGION = 'ap-singapore'


def video_key(job_id, digest):
    if not re.fullmatch(r'[a-f0-9]{32}', job_id) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('成片标识或校验值无效')
    return f'video-jobs/{job_id}/{digest}.mp4'


def upload_video(path, job_id, digest):
    path = Path(path)
    key = video_key(job_id, digest)
    secret_id, secret_key = os.environ.get('COS_SECRET_ID'), os.environ.get('COS_SECRET_KEY')
    if not secret_id or not secret_key:
        raise RuntimeError('NAS 尚未配置腾讯 COS 凭据')
    from qcloud_cos import CosConfig, CosS3Client
    cos = CosS3Client(CosConfig(Region=REGION, SecretId=secret_id,
                                SecretKey=secret_key, Scheme='https', Timeout=30))
    with path.open('rb') as video:
        cos.put_object(Bucket=BUCKET, Key=key, Body=video,
                       ContentType='video/mp4', EnableMD5=True)
    remote = cos.head_object(Bucket=BUCKET, Key=key)
    if int(remote['Content-Length']) != path.stat().st_size:
        raise RuntimeError('COS 成片大小与已检查文件不一致')
    return key


def delete_videos(job_id):
    prefix = video_key(job_id, '0' * 64).rsplit('/', 1)[0] + '/'
    from qcloud_cos import CosConfig, CosS3Client
    cos = CosS3Client(CosConfig(Region=REGION, SecretId=os.environ['COS_SECRET_ID'],
                                SecretKey=os.environ['COS_SECRET_KEY'], Scheme='https', Timeout=30))
    marker = ''
    while True:
        page = cos.list_objects(Bucket=BUCKET, Prefix=prefix, Marker=marker, MaxKeys=1000)
        objects = [{'Key': item['Key']} for item in page.get('Contents', [])]
        if any(not item['Key'].startswith(prefix) for item in objects):
            raise ValueError('COS 返回了非本任务的文件')
        if objects:
            result = cos.delete_objects(Bucket=BUCKET, Delete={'Object': objects, 'Quiet': 'true'})
            if result.get('Error'):
                raise RuntimeError('部分 COS 成片未能删除')
        if str(page.get('IsTruncated')).lower() != 'true':
            break
        marker = page.get('NextMarker') or objects[-1]['Key']
