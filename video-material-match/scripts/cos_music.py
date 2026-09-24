"""Download an approved user music track from private Tencent COS."""
import os
import re
from pathlib import Path


BUCKET = 'hunjian-1410143389'
KEY_PATTERN = re.compile(r'music-library/[a-f0-9]{32}/[^/]{1,100}\.(?:mp3|wav|m4a)', re.I)


def validate_key(key):
    if key in (None, ''):
        return None
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
        raise ValueError('音乐库曲目无效')
    return key


def download(key, folder):
    key = validate_key(key)
    if not key:
        return None
    secret_id, secret_key = os.environ.get('COS_SECRET_ID'), os.environ.get('COS_SECRET_KEY')
    if not secret_id or not secret_key:
        raise RuntimeError('NAS 尚未配置腾讯 COS 凭据')
    from qcloud_cos import CosConfig, CosS3Client
    target = Path(folder) / ('selected-music' + Path(key).suffix.lower())
    if target.is_file() and target.stat().st_size > 128:
        return target
    temporary = target.with_suffix(target.suffix + '.part')
    try:
        cos = CosS3Client(CosConfig(Region='ap-singapore', SecretId=secret_id,
                                    SecretKey=secret_key, Scheme='https'))
        cos.download_file(Bucket=BUCKET, Key=key, DestFilePath=str(temporary))
        if temporary.stat().st_size < 128:
            raise ValueError('音乐库曲目为空或过短')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def delete(key):
    key = validate_key(key)
    if not key:
        raise ValueError('请选择要删除的音乐')
    from qcloud_cos import CosConfig, CosS3Client
    cos = CosS3Client(CosConfig(Region='ap-singapore', SecretId=os.environ['COS_SECRET_ID'],
                                SecretKey=os.environ['COS_SECRET_KEY'], Scheme='https'))
    cos.delete_object(Bucket=BUCKET, Key=key)
