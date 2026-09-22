"""Suno task submission, resumable polling, and narration-first music mixing."""
import hashlib
import json
import os
import time
from pathlib import Path

import requests

from api import clean_error
from matcher import write_json
import media

BASE = 'https://api.openlux.ai'
VOICE_LUFS = -19
MUSIC_LUFS = -25


def generate(prompt, output, log=print):
    output = Path(output)
    payload = {'model': 'suno_music_open', 'mv': 'chirp-v4-5',
               'gpt_description_prompt': prompt, 'make_instrumental': True}
    signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    cache = output / f'suno-{signature}.json'
    audio = output / f'suno-{signature}.m4a'
    if audio.exists():
        media.duration(audio)
        return audio
    key = os.environ.get('SUNO_API_KEY')
    if not key:
        raise ValueError('请设置 SUNO_API_KEY；不能省略配乐步骤后报告成片完成')
    headers = {'Authorization': f'Bearer {key}'}
    state = json.loads(cache.read_text(encoding='utf-8')) if cache.exists() else {}
    if not state:
        # Persist before POST. A timeout may still have created a paid task.
        state = {'request': payload, 'submission': 'uncertain'}
        write_json(cache, state)
        try:
            response = requests.post(BASE+'/suno/submit/music', headers=headers, json=payload,
                                     timeout=(15, 90), allow_redirects=False)
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError('音乐提交结果不确定，请先查服务商任务，不能重复下单：'+clean_error(exc)) from None
        if not response.ok or data.get('code') != 'success' or not isinstance(data.get('data'), str):
            state.update({'submission': 'failed', 'error': clean_error(data)})
            write_json(cache, state)
            raise RuntimeError('音乐提交失败：'+clean_error(data))
        state.update({'submission': 'submitted', 'task_id': data['data']})
        write_json(cache, state)
    if not state.get('task_id'):
        raise RuntimeError('上次音乐提交未获得任务 ID，请检查服务商任务后再恢复，不能自动重复下单')
    log('Suno 音乐任务已提交，等待纯音乐完成…')
    deadline = time.monotonic()+600
    last = None
    while time.monotonic() < deadline:
        try:
            response = requests.get(BASE+'/suno/fetch/'+state['task_id'], headers=headers, timeout=(15, 45))
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            log('音乐查询暂未成功，将继续查询同一任务：'+clean_error(exc))
            time.sleep(10)
            continue
        if data.get('code') != 'success' or not isinstance(data.get('data'), dict):
            raise RuntimeError('音乐查询失败：'+clean_error(data))
        task = data['data']
        state['result'] = task
        write_json(cache, state)
        if task.get('status') in ('FAILURE', 'FAILED'):
            raise RuntimeError('音乐生成失败：'+clean_error(task.get('fail_reason')))
        if task.get('status') == 'SUCCESS':
            clips = task.get('data') or []
            if not clips:
                raise ValueError('音乐任务成功但没有音频')
            url = clips[0].get('cld2AudioUrl') or clips[0].get('audio_url')
            if not isinstance(url, str) or not url.startswith('https://'):
                raise ValueError('音乐任务没有可用 HTTPS 音频地址')
            result = requests.get(url, timeout=(15, 120))  # Do not forward the API key to the media host.
            result.raise_for_status()
            temporary = audio.with_suffix('.download.m4a')
            temporary.write_bytes(result.content)
            media.duration(temporary)
            temporary.replace(audio)
            return audio
        progress = task.get('progress')
        if progress != last:
            log(f'Suno 音乐生成进度：{progress}')
            last = progress
        time.sleep(10)
    raise TimeoutError('音乐尚未生成完；重跑会查询同一任务，不重复下单')


def normalize(source, target, seconds, lufs):
    # Measure via stderr; reset timestamps and trim by sample count to avoid loudnorm's PTS offset.
    import subprocess
    measured = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(source), '-t', str(seconds),
        '-af', f'loudnorm=I={lufs}:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
    stats = json.loads(measured.stderr[measured.stderr.rfind('{'):measured.stderr.rfind('}')+1])
    count = round(seconds*48000)
    af = (f'loudnorm=I={lufs}:TP=-1.5:LRA=11:measured_I={stats["input_i"]}'
          f':measured_TP={stats["input_tp"]}:measured_LRA={stats["input_lra"]}'
          f':measured_thresh={stats["input_thresh"]}:offset={stats["target_offset"]},'
          f'aresample=48000,asetpts=N/SR/TB,apad=whole_len={count},atrim=end_sample={count},asetpts=N/SR/TB')
    media.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-af', af,
               '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(target)])


def mix(video, narration, music, output, seconds):
    output = Path(output)
    if media.duration(music) < seconds:
        raise ValueError('音乐短于成片，需要生成更长配乐或剪辑衔接，不能静音补足')
    normalize(narration, output/'narration-normalized.wav', seconds, VOICE_LUFS)
    normalize(music, output/'music-bed.wav', seconds, MUSIC_LUFS)
    filters = (f'[0:a]asplit=2[voice][side];[1:a]afade=t=in:d=1.2,'
               f'afade=t=out:st={max(0,seconds-2)}:d=2[bed];'
               '[bed][side]sidechaincompress=threshold=0.06:ratio=2:attack=20:release=300[duck];'
               '[voice][duck]amix=inputs=2:duration=first:normalize=0,'
               'alimiter=limit=0.95:level=false:latency=true[mix]')
    media.run(['ffmpeg', '-v', 'error', '-y', '-i', str(output/'narration-normalized.wav'),
               '-i', str(output/'music-bed.wav'), '-filter_complex', filters, '-map', '[mix]',
               '-c:a', 'pcm_s16le', str(output/'mixed.wav')])
    target = output/'video-music.mp4'
    media.run(['ffmpeg', '-v', 'error', '-y', '-i', str(video), '-i', str(output/'mixed.wav'),
               '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
               '-movflags', '+faststart', str(target)])
    return target
