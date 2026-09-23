"""Private Tencent COS music library for the authenticated workbench."""
import os
import re
import uuid
from pathlib import Path
from urllib.parse import unquote


PREFIX = 'music-library/'
BUCKET = 'hunjian-1410143389'
REGION = 'ap-singapore'
KEY_PATTERN = re.compile(r'music-library/[a-f0-9]{32}/[^/]{1,100}\.(?:mp3|wav|m4a)', re.I)


def validate_key(key):
    if key in (None, ''):
        return None
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
        raise ValueError('音乐库曲目无效')
    return key


def client():
    secret_id, secret_key = os.environ.get('COS_SECRET_ID'), os.environ.get('COS_SECRET_KEY')
    if not secret_id or not secret_key:
        raise RuntimeError('音乐库尚未配置腾讯 COS 凭据')
    from qcloud_cos import CosConfig, CosS3Client
    return CosS3Client(CosConfig(Region=REGION, SecretId=secret_id,
                                 SecretKey=secret_key, Scheme='https'))


def new_key(filename):
    filename = Path(unquote(filename).replace('\\', '/')).name
    filename = re.sub(r'[^\w\u4e00-\u9fff .-]', '_', filename).strip(' .')
    if len(filename) > 100 or not re.search(r'\.(mp3|wav|m4a)$', filename, re.I):
        raise ValueError('请选择文件名不超过 100 字符的 MP3、WAV 或 M4A 音乐')
    return f'{PREFIX}{uuid.uuid4().hex}/{filename}'


def list_tracks():
    cos = client()
    result, marker = [], ''
    while True:
        page = cos.list_objects(Bucket=BUCKET, Prefix=PREFIX, Marker=marker, MaxKeys=1000)
        for item in page.get('Contents', []):
            key = item.get('Key', '')
            if KEY_PATTERN.fullmatch(key):
                result.append({'key': key, 'name': key.rsplit('/', 1)[-1],
                               'size': int(item.get('Size', 0))})
        if str(page.get('IsTruncated')).lower() != 'true':
            break
        marker = page.get('NextMarker') or page['Contents'][-1]['Key']
    return result


def upload(path, key):
    client().upload_file(Bucket=BUCKET, Key=validate_key(key), LocalFilePath=str(path))
