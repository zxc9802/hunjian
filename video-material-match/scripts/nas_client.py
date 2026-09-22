"""Submit and retrieve NAS jobs without copying original videos to the client."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, help='含 base_url 和 token 的私有 JSON 文件')
    sub = parser.add_subparsers(dest='command', required=True)
    submit = sub.add_parser('submit')
    submit.add_argument('--text-file', required=True)
    submit.add_argument('--request-id', required=True, help='本次任务固定 ID；网络超时后仍使用相同 ID')
    submit.add_argument('--emotion-alpha', type=float, default=.8)
    status = sub.add_parser('status')
    status.add_argument('job_id')
    download = sub.add_parser('download')
    download.add_argument('job_id')
    download.add_argument('--output', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    base = config['base_url'].rstrip('/')
    session = requests.Session()
    session.headers['Authorization'] = 'Bearer ' + config['token']

    def request(method, path, **kwargs):
        response = session.request(method, base+path, timeout=(10,120), allow_redirects=False, **kwargs)
        if not 200 <= response.status_code < 300:
            raise ValueError(f'NAS 返回 HTTP {response.status_code}: {response.text[:800]}')
        return response

    if args.command == 'submit':
        value = request('POST', '/v1/jobs', headers={'Idempotency-Key':args.request_id},
                        json={'text':Path(args.text_file).read_text(encoding='utf-8-sig').strip(),
                              'emotion_alpha':args.emotion_alpha}).json()
        print(json.dumps(value, ensure_ascii=False))
        return
    if not re.fullmatch('[a-f0-9]{32}', args.job_id):
        raise ValueError('任务 ID 无效')
    prefix = '/v1/jobs/' + args.job_id
    status = request('GET', prefix).json()
    if args.command == 'status':
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return
    if status['state'] != 'done':
        raise ValueError('任务尚未完成：' + status['state'])
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = request('GET', prefix+'/report').json()
    temporary = output/'video-music.download'
    digest = hashlib.sha256()
    with request('GET', prefix+'/video', stream=True) as response, temporary.open('wb') as file:
        for chunk in response.iter_content(1024*1024):
            file.write(chunk)
            digest.update(chunk)
    if not report.get('passed') or report.get('sha256') != digest.hexdigest():
        raise ValueError('下载成片与质量报告不一致')
    temporary.replace(output/'video-music.mp4')
    (output/'quality-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (output/'captions.srt').write_bytes(request('GET', prefix+'/captions').content)
    for artifact in ('plan', 'cuts'):
        (output/(artifact+'.json')).write_bytes(request('GET', prefix+'/'+artifact).content)
    print(str(output/'video-music.mp4'))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
