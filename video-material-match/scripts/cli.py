"""Entry point for the video-material-match skill."""
import argparse
import json
import random
import sys
from pathlib import Path

from api import clean_error
from matcher import create_plan, index_videos, inventory, write_json, validate_scenes
import media
import voice


def main():
    parser = argparse.ArgumentParser(description='文案 → 真实素材匹配 → IndexTTS2 配音 → 字幕成片')
    sub = parser.add_subparsers(dest='command',required=True)
    scan = sub.add_parser('scan',help='只读盘点，不调用收费模型')
    scan.add_argument('--root',default='Z:/')
    scan.add_argument('--output',default='data/inventory.json')
    index = sub.add_parser('index',help='增量向量索引；limit 限制本次新增片段数')
    index.add_argument('--root',default='Z:/')
    index.add_argument('--catalog',default='data/catalog')
    index.add_argument('--chunk-seconds',type=float,default=8)
    index.add_argument('--limit',type=int)
    index.add_argument('--paths-file',help='可选 JSON 文件，包含待索引视频绝对路径数组')
    match = sub.add_parser('match',help='切分文案、检索、精排、视频核验，保存 plan.json')
    match.add_argument('--text-file',required=True)
    match.add_argument('--catalog',default='data/catalog')
    match.add_argument('--output',required=True)
    match.add_argument('--chunk-seconds',type=float,default=8)
    match.add_argument('--seed',type=int)
    tts = sub.add_parser('voice',help='按场景配音，并用实际时长替换估算时间轴')
    tts.add_argument('--plan',required=True)
    tts.add_argument('--emotion-alpha',type=voice.validate_emotion,default=.8)
    tts.add_argument('--emotion-file',default=str(voice.EMOTION_FILE))
    speaker = tts.add_mutually_exclusive_group()
    speaker.add_argument('--speaker-url',help='覆盖本地主音色的 HTTPS 音频地址')
    speaker.add_argument('--speaker-file',default=str(voice.SPEAKER_FILE),help='本地主音色参考音频')
    render = sub.add_parser('render',help='从 plan.json 裁切源素材，导出字幕预览或有声成片')
    render.add_argument('--plan',required=True)
    render.add_argument('--width',type=int,default=1920)
    render.add_argument('--height',type=int,default=1080)
    render.add_argument('--catalog',default='data/catalog')
    render.add_argument('--music-file',help='使用用户提供的本地音乐；不指定则不添加背景音乐')
    ui = sub.add_parser('ui',help='本地操作页面，含情绪滑块')
    ui.add_argument('--port',type=int,default=8767)
    ui.add_argument('--catalog',default='data/catalog')
    ui.add_argument('--output-root',default='outputs')
    args = parser.parse_args()
    if args.command == 'scan':
        items = inventory(args.root)
        write_json(args.output,items)
        print(json.dumps({'videos':len(items),'GB':round(sum(x['bytes'] for x in items)/1024**3,2)},ensure_ascii=False))
    elif args.command == 'index':
        if args.limit is not None and args.limit < 1:
            parser.error('--limit 必须至少为 1')
        paths = json.loads(Path(args.paths_file).read_text(encoding='utf-8-sig')) if args.paths_file else None
        print(index_videos(args.root,args.catalog,args.chunk_seconds,args.limit,paths))
    elif args.command == 'match':
        text = Path(args.text_file).read_text(encoding='utf-8-sig').strip()
        if not text or len(text) > 6000:
            parser.error('文案须为 1–6000 字符')
        plan = create_plan(text,args.catalog,args.output,args.chunk_seconds,args.seed)
        print(json.dumps({'plan':str(Path(args.output).resolve()/'plan.json'),
                          'missing':sum(not s['match']['selected'] for s in plan['scenes'])},ensure_ascii=False))
    elif args.command in ('voice','render'):
        path = Path(args.plan).resolve()
        plan = json.loads(path.read_text(encoding='utf-8'))
        validate_scenes(plan['script'],plan['scenes'])
        if any('match' not in s for s in plan['scenes']):
            raise ValueError('匹配尚未完成，请先完成 match')
        if args.command == 'voice':
            voice.synthesize_plan(plan,path.parent,args.emotion_alpha,args.emotion_file,args.speaker_url,
                                  speaker_file=args.speaker_file)
            print(str(path.parent/'narration.wav'))
        else:
            if args.width < 2 or args.height < 2 or args.width % 2 or args.height % 2:
                parser.error('宽高必须为正偶数')
            from finish import deliver
            print(deliver(plan,path.parent,args.width,args.height,args.catalog,args.music_file))
    elif args.command == 'ui':
        from local_ui import serve
        serve(args.port,args.catalog,args.output_root)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(clean_error(exc),file=sys.stderr)
        sys.exit(1)
