"""Fill real shot coverage, mix music, review the export, and gate delivery."""
import math
from pathlib import Path

from api import Models
from matcher import matching_catalog, judge_videos, write_json
import media
import music
import quality


def verified_options(scene):
    selected = scene.get('match', {}).get('selected')
    options = [selected] if selected else []
    seen = {c['id'] for c in options}
    # Contextual replacements were separately approved; do not revive earlier rejected strict matches.
    if scene.get('visual_usage') == 'contextual-b-roll':
        return options
    for attempt in scene.get('match', {}).get('attempts', []):
        clips = {c['id']: c for c in attempt['top3']}
        for accepted in attempt['verdict'].get('accepted', []):
            clip = clips.get(accepted['id'])
            if clip and clip['id'] not in seen:
                options.append({**clip, 'verified_start': accepted['start'],
                    'verified_end': min(accepted['end'], clip['source_end']-clip['source_start']),
                    'visual_score': accepted['score']})
                seen.add(clip['id'])
    return options


def allocate_shots(options, seconds, fps):
    remaining = round(seconds*fps)
    usable = [(c, math.floor((c['verified_end']-c['verified_start'])*fps+1e-6)) for c in options]
    for clip, frames in usable:
        if frames >= remaining:
            return [{'selected': clip, 'duration': remaining/fps}]
    if sum(max(0, f) for _, f in usable) < remaining:
        return None
    shots = []
    for clip, frames in usable:
        take = min(frames, remaining)
        if 0 < remaining-take < fps and take > fps:
            take = remaining-fps  # Avoid a tiny flash at the next cut.
        if take > 0:
            shots.append({'selected': clip, 'duration': take/fps})
            remaining -= take
        if not remaining:
            return shots
    return None


def prepare_shots(plan, catalog, models, log=print, replace=()):
    database, search = None, None
    try:
        for index, scene in enumerate(plan['scenes'], 1):
            if scene.get('shots') and index not in replace:
                continue
            options = verified_options(scene)
            rejected = {c['id'] for c in options}
            if index in replace:
                rejected.update(s['selected']['id'] for s in scene.get('shots', []))
                options = []
            length = scene['end']-scene['start']
            shots = allocate_shots(options, length, plan['fps'])
            if not shots:
                if database is None:
                    database = matching_catalog(catalog, models)
                    search = database.searcher()
                log(f'场景 {index} 补充动态素材，覆盖 {length:.2f} 秒配音')
                query = scene.get('query') or scene['text']
                # Retrieve once; each review sees a fresh group of three candidates.
                recalled = search(models.embed(text=query), 60)
                ranked = models.rerank(query, [c for c in recalled if c['id'] not in rejected])
                for start in range(0, min(len(ranked), 9), 3):
                    candidates = ranked[start:start+3]
                    visual_text = scene['text']
                    if scene.get('visual_note'):
                        visual_text += '；用户允许的环境替代范围：'+scene['visual_note']
                    if any(word in visual_text for word in ('评论区', '扣1', '地址发给')):
                        visual_text += '；这是咨询引导的旁白，允许主持人互动或该项目园区环境收尾，无须出现手机评论界面。'
                    verdict = judge_videos(models, visual_text, candidates)
                    attempt = {'query': query, 'recall_k': 60, 'top3': candidates, 'verdict': verdict}
                    scene.setdefault('coverage_attempts', []).append(attempt)
                    by_id = {c['id']: c for c in candidates}
                    for accepted in verdict.get('accepted', []):
                        c = by_id.get(accepted['id'])
                        a, b = float(accepted['start']), float(accepted['end'])
                        if not c or not 0 <= a < b <= c['source_end']-c['source_start']+.15:
                            raise ValueError('补充镜头核验返回非法区间')
                        options.append({**c, 'verified_start': a,
                            'verified_end': min(b, c['source_end']-c['source_start']), 'visual_score': accepted['score']})
                    shots = allocate_shots(options, length, plan['fps'])
                    if shots:
                        break
            if not shots:
                raise ValueError(f'场景 {index} 缺少足够的合格动态素材，请修订该段文案或补素材后重新配音；禁止长定格补时')
            scene['shots'] = shots
            scene['match']['selected'] = shots[0]['selected']
    finally:
        if database:
            database.close()


def deliver(plan, output, width=1920, height=1080, catalog='data/catalog', music_file=None, log=print):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan['output_color'] = 'bt709'
    models = Models()
    prepare_shots(plan, catalog, models, log)
    write_json(output/'plan.json', plan)
    if plan.get('narration'):
        saved_music = plan.get('music_settings', {}).get('path')
        if not music_file and saved_music and Path(saved_music).is_file():
            music_file = saved_music
        if not music_file:
            settings = plan.get('music_settings', {})
            if not settings.get('prompt'):
                settings = models.json('为以下混剪文案设计适合铺在口播下面的纯音乐。只返回 '
                    '{"prompt":"英文音乐描述，含情绪、乐器、节奏、no vocals/no humming/under narration"}。'
                    '不要生成歌词，不模仿具体艺人。文案：'+plan['script'])
                if not isinstance(settings.get('prompt'), str) or not settings['prompt'].strip():
                    raise ValueError('音乐提示词为空')
            settings['provider'] = 'suno_music_open'
            plan['music_settings'] = settings
            write_json(output/'plan.json', plan)
            music_file = music.generate(settings['prompt'], output, log)
        plan.setdefault('music_settings', {}).update({'path': str(Path(music_file).resolve()),
                                                     'narration_lufs': music.VOICE_LUFS,
                                                     'music_lufs': music.MUSIC_LUFS, 'ducking': True})
    for attempt in range(1, 3):
        write_json(output/'plan.json', plan)
        log('导出画面、配音与字幕…')
        video = media.render(plan, output, width, height)
        if plan.get('narration'):
            video = music.mix(video, plan['narration'], music_file, output, plan['scenes'][-1]['end'])
        report = quality.review(video, plan, output, models, log)
        write_json(output/f'quality-attempt-{attempt}.json', report)
        if report['passed']:
            log('Gemini 成片检查通过，音视频时间轴一致')
            return video
        errors = [i for r in report['segments'] for i in r['issues'] if i['severity'] == 'error']
        replace = {i.get('scene') for i in errors if i.get('type') in ('freeze', 'black_frame', 'visual_mismatch')}
        replace = {i for i in replace if type(i) is int and 1 <= i <= len(plan['scenes'])}
        if attempt == 2 or not replace:
            break
        log('检查发现画面问题，重新选择相关场景素材后复查')
        prepare_shots(plan, catalog, models, log, replace)
    raise ValueError('成片检查未通过。查看 quality-report.json，修正后重新 render；当前文件是待修订版本，不能标为完成')
