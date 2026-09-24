"""Choose stills from the rendered shots and add the user's cover and shot titles."""
import hashlib
import json
import random
import re
from pathlib import Path

import media


def shot_list(folder):
    folder = Path(folder)
    plan = json.loads((folder / 'plan.json').read_text(encoding='utf-8'))
    cuts = json.loads((folder / 'cuts.json').read_text(encoding='utf-8'))
    shots, start = [], 0.0
    for cut in cuts:
        scene, number = cut['scene'], cut.get('shot', 1)
        length = cut['used_seconds'] + cut.get('freeze_seconds', 0)
        clip = f'clip-{scene:03}-{number:02}.mp4'
        if not (folder / clip).is_file() or length <= 0:
            raise ValueError('镜头文件或时间轴不完整，不能选封面')
        text = plan['scenes'][scene - 1]['text']
        chunks = [media.caption_text(part) for part in re.split(r'[，。！？；,.!?;]', text)]
        chunks = [part for part in chunks if part]
        if len(chunks) == 1:
            middle = max(1, len(chunks[0]) // 2)
            chunks = [chunks[0][:middle], chunks[0][middle:] or chunks[0]]
        shots.append({'scene': scene, 'shot': number, 'start': round(start, 3),
                      'end': round(start + length, 3), 'text': text, 'clip': clip,
                      'white': (chunks[0] if chunks else '')[:22],
                      'yellow': (chunks[1] if len(chunks) > 1 else '')[:22]})
        start += length
    if not shots:
        raise ValueError('尚无可供选择的成片镜头')
    return shots


def cover_options(folder, job_id):
    folder = Path(folder)
    cover_dir = folder / 'covers'
    manifest = cover_dir / 'options.json'
    if manifest.is_file():
        options = json.loads(manifest.read_text(encoding='utf-8'))
        if len(options) == 10 and all((folder / item['thumbnail']).is_file() for item in options):
            return options
    cover_dir.mkdir(exist_ok=True)
    shots = shot_list(folder)
    total = shots[-1]['end']
    rng = random.Random(job_id)
    options = []
    for index in range(10):
        second = total * (index + rng.uniform(.1, .9)) / 10
        shot = next((item for item in shots if second < item['end']), shots[-1])
        offset = min(max(second - shot['start'], .04), max(.04, shot['end'] - shot['start'] - .04))
        name = f'covers/{index:02}.jpg'
        media.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(offset),
                   '-i', shot['clip'], '-vf', 'scale=270:-2', '-frames:v', '1',
                   '-q:v', '3', name], cwd=folder)
        options.append({'index': index, 'time': round(second, 2), 'thumbnail': name,
                        'clip': shot['clip'], 'offset': round(offset, 3)})
    manifest.write_text(json.dumps(options, ensure_ascii=False), encoding='utf-8')
    return options


def validate_edit(value, folder):
    if not isinstance(value, dict):
        raise ValueError('封面设置格式无效')
    index, cover, titles = value.get('cover_index'), value.get('cover_text'), value.get('titles')
    options = cover_options(folder, Path(folder).name)
    if type(index) is not int or not 0 <= index < len(options):
        raise ValueError('请选择一张封面画面')

    def checked(text, limit):
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > limit:
            raise ValueError('文字不能为空，也不能过长')
        if any((ord(char) < 32 and char != '\n') or char in '{}\\' for char in text):
            raise ValueError('文字含有不支持的控制字符')
        return text.strip()

    cover = checked(cover, 28)
    if 'title' in value:
        title = value['title']
        if not isinstance(title, dict):
            raise ValueError('整片标题格式无效')
        return {'cover_index': index, 'cover_text': cover,
                'title': {'white': checked(title.get('white'), 28),
                          'yellow': checked(title.get('yellow'), 28)}}
    if not isinstance(titles, list) or len(titles) != len(shot_list(folder)):
        raise ValueError('每个镜头都需要一组白黄文字')
    result = []
    for title in titles:
        if not isinstance(title, dict):
            raise ValueError('镜头文字格式无效')
        result.append({'white': checked(title.get('white'), 28),
                       'yellow': checked(title.get('yellow'), 28)})
    return {'cover_index': index, 'cover_text': cover, 'titles': result}


def ass_time(seconds):
    centis = round(seconds * 100)
    return f'{centis // 360000:01}:{centis // 6000 % 60:02}:{centis // 100 % 60:02}.{centis % 100:02}'


def ass_line(text, wrap=14):
    cleaned = media.caption_text(text)
    return r'\N'.join(cleaned[i:i + wrap] for i in range(0, len(cleaned), wrap))


def render_overlay(folder, spec, base_video):
    folder, base_video = Path(folder), Path(base_video)
    options = cover_options(folder, folder.name)
    selected = options[spec['cover_index']]
    poster = folder / 'selected-cover.png'
    media.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(selected['offset']),
               '-i', selected['clip'], '-frames:v', '1', str(poster)], cwd=folder)
    info = json.loads(media.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,r_frame_rate', '-of', 'json', str(base_video)]))['streams'][0]
    width, height = info['width'], info['height']
    total = media.duration(base_video)
    size = round(min(width, height) * .074)
    styles = [
        'Style: White,Noto Sans CJK SC,{0},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,0,8,0,0,0,1'.format(size),
        'Style: Yellow,Noto Sans CJK SC,{0},&H0000E5FF,&H0000E5FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,0,8,0,0,0,1'.format(size),
        'Style: Cover,Noto Sans CJK SC,{0},&H0000E5FF,&H0000E5FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,0,5,0,0,0,1'.format(round(size * 1.1)),
    ]
    lines = [f'Dialogue: 1,{ass_time(0)},{ass_time(.5)},Cover,,0,0,0,,{{\\an5\\pos({width//2},{height//2})}}{ass_line(spec["cover_text"], 12)}']
    intervals = ([(.5, total, spec['title'])] if 'title' in spec else
                 [(max(.5, shot['start']), shot['end'], title)
                  for shot, title in zip(shot_list(folder), spec['titles'])])
    for start, end, title in intervals:
        if start >= end:
            continue
        white = ass_line(title['white'])
        yellow = ass_line(title['yellow'])
        first_y = round(height * .07)
        second_y = first_y + (white.count(r'\N') + 1) * round(size * 1.15)
        lines.extend([
            f'Dialogue: 0,{ass_time(start)},{ass_time(end)},White,,0,0,0,,{{\\an8\\pos({width//2},{first_y})}}{white}',
            f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Yellow,,0,0,0,,{{\\an8\\pos({width//2},{second_y})}}{yellow}',
        ])
    ass = folder / 'cover-titles.ass'
    ass.write_text('[Script Info]\nScriptType: v4.00+\nPlayResX: {0}\nPlayResY: {1}\n'
        '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n{2}\n'
        '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n{3}\n'
        .format(width, height, '\n'.join(styles), '\n'.join(lines)), encoding='utf-8')
    signature = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    target = folder / f'edit-{signature}.mp4'
    media.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', base_video.name,
               '-loop', '1', '-framerate', '25', '-i', poster.name,
               '-filter_complex', "[0:v][1:v]overlay=0:0:enable='lt(t,0.5)',subtitles=cover-titles.ass[v]",
               '-map', '[v]', '-map', '0:a:0?', '-t', str(total),
               '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
               '-c:a', 'copy', '-map_metadata', '-1', *media.SDR_FLAGS,
               '-movflags', '+faststart', target.name], cwd=folder)
    return target
