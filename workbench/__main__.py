import argparse
import os

import uvicorn

from .server import Settings, create_app, validate_spec


def main():
    parser = argparse.ArgumentParser(description='海南康养视频工作台')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8788)
    parser.add_argument('--nas-config', help='本机 NAS 私有连接配置路径')
    parser.add_argument('--import-job', help='导入一条已完成 NAS 任务，不重新生成')
    args = parser.parse_args()
    if args.nas_config:
        os.environ['NAS_CONFIG_FILE'] = args.nas_config
    settings = Settings.from_env()
    if not args.import_job and not settings.password and args.host not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('无登录密码时只允许监听本机地址')
    app = create_app(settings)
    if args.import_job:
        import re
        if not re.fullmatch(r'[a-f0-9]{32}', args.import_job):
            parser.error('NAS 任务 ID 格式无效')
        status = app.state.read_nas('/v1/jobs/' + args.import_job)
        if status['state'] != 'done':
            parser.error('只能导入已完成的真实任务')
        plan = app.state.read_nas('/v1/jobs/' + args.import_job + '/plan')
        # NAS plans do not record every rendering option; use its verified report.
        report = app.state.read_nas('/v1/jobs/' + args.import_job + '/report')
        if not report.get('passed'):
            parser.error('此任务的成片检查未通过')
        spec = validate_spec({'text': plan['script'], 'emotion_alpha': plan.get('voice', {}).get('emotion_alpha', .8)})
        key = 'import-' + args.import_job
        app.state.store.reserve(key, spec)
        app.state.store.update(key, 'done', nas_id=args.import_job, snapshot=app.state.sanitized_snapshot(status))
        print('已导入完成任务：' + key + '；没有提交新的生成请求。')
        return
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False)


if __name__ == '__main__':
    main()
