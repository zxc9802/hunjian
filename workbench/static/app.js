'use strict';
const $ = id => document.getElementById(id);
const labels = {submitting:'正在提交', uncertain:'等待核对', rejected:'未提交成功', queued:'等待制作', running:'正在制作', done:'已完成', failed:'制作失败', interrupted:'制作中断'};
const activeStates = new Set(['queued','running','submitting','uncertain']);
const terminalStates = new Set(['done','failed','interrupted']);
const sample = '我认为全中国冬天最舒服的城市就是海南的三亚和陵水，这俩地方冬天气温25-28度，我每年都会带着爸妈来这里过冬，就住在三亚海棠湾的这家高端旅居基地。\n\n我比较喜欢这里的一点，就是爸妈住进来以后基本不用操什么心。住宿、吃饭、水电、网络这些都包含了，每天一日三餐都是自助餐，房间也会定期有人打扫。\n\n平时想活动一下，可以泡温泉、游泳、健身，园区里面每天也有不少同龄人一起散步、聊天、参加活动。这里还有医生全天在岗。\n\n如果你也想带爸妈来海南过冬，评论区扣1，我把价格和地址发给你看看。';
let jobs = [], selected = null, filter = 'all', pollTimer, refreshTimer, healthTimer, editTimer, toastTimer, submitting = false, authenticated = false, coverEditorAvailable = false, requestKey = null, pendingSpec = null, coverIndex = 0, musicSelection = '';
const voice = new Audio('/api/reference/voice');

function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4000); }
function storageGet(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } }
function storageSet(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } }
function stopTimers() { clearTimeout(pollTimer); clearTimeout(refreshTimer); clearTimeout(healthTimer); clearTimeout(editTimer); }
function showLogin() { authenticated = false; coverEditorAvailable = false; stopTimers(); pauseMusic(); voice.pause(); $('video').pause(); $('app').hidden = true; $('loading-view').hidden = true; $('login-view').hidden = false; }
async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, {credentials:'same-origin', ...options, headers:{'Content-Type':'application/json','X-Workbench-Request':'1', ...options.headers}, signal:AbortSignal.timeout(40000)}); }
  catch { throw new Error('工作台连接中断，请检查网络后重试。已提交的任务会继续制作。'); }
  let value;
  try { value = await response.json(); } catch { throw new Error('服务未返回有效结果，请稍后重试。'); }
  if (!response.ok) { if (response.status === 401 && path !== '/api/login') showLogin(); throw new Error(typeof value.detail === 'string' ? value.detail : '请求未完成，请重试。'); }
  return value;
}
function draftSpec() {
  return {text:$('editor').value, emotion_alpha:.8, width:1080, height:1920, music_key:musicSelection || null};
}
function setSpec(spec) {
  $('editor').value = spec.text || '';
  musicSelection = spec.music_key || '';
  $('music-select').value = musicSelection;
  updateControls(spec);
}
function pauseMusic(except = null) {
  $('music-list').querySelectorAll('audio').forEach(player => { if (player !== except) player.pause(); });
}
async function loadMusic() {
  try {
    const tracks = (await api('/api/music')).tracks;
    $('music-select').replaceChildren(new Option('不添加背景音乐', ''));
    pauseMusic(); $('music-list').replaceChildren();
    for (const track of tracks) {
      $('music-select').add(new Option(`${track.name} · ${(track.size / 1024 / 1024).toFixed(1)} MB`, track.key));
      const item = document.createElement('li');
      const name = document.createElement('span'); name.textContent = track.name;
      const player = document.createElement('audio'); player.controls = true; player.preload = 'none';
      player.setAttribute('aria-label', `试听 ${track.name}`);
      player.src = `/api/music/preview?key=${encodeURIComponent(track.key)}`;
      player.onplay = () => { pauseMusic(player); voice.pause(); $('video').pause(); };
      player.onerror = () => toast(`“${track.name}”暂时无法试听，请检查连接或音乐格式后重试。`);
      item.append(name, player); $('music-list').append(item);
    }
    $('music-list').hidden = tracks.length === 0;
    if (musicSelection && !tracks.some(track => track.key === musicSelection))
      $('music-select').add(new Option('已选曲目（当前未在列表中）', musicSelection));
    $('music-select').value = musicSelection;
    $('music-status').textContent = tracks.length ? `${tracks.length} 首已上传音乐，可逐首试听` : '音乐库暂无曲目，可上传后试听；也可直接制作无背景音乐的视频。';
  } catch (error) { $('music-status').textContent = error.message; }
}
function updateControls(spec = selected?.spec || draftSpec()) {
  $('counter').value = `${$('editor').value.length} / 6000`;
  const portrait = spec.width < spec.height;
  $('preview-format').textContent = `${portrait ? '9:16' : '16:9'} · ${Math.min(spec.width,spec.height)}p`;
  $('preview-frame').classList.toggle('landscape', !portrait);
}
function saveDraft() {
  if (selected || submitting) return;
  const spec = draftSpec();
  if (pendingSpec && JSON.stringify(spec) !== JSON.stringify(pendingSpec)) { requestKey = null; pendingSpec = null; }
  $('draft-label').textContent = storageSet('hainan-draft', {spec, requestKey, pendingSpec}) ? '草稿已保存' : '草稿未保存';
  updateControls();
}
function setReadOnly(readonly) {
  $('editor').readOnly = readonly;
  $('music-select').disabled = readonly;
  $('music-file').disabled = readonly;
  $('music-upload').disabled = readonly;
  $('example').hidden = readonly;
  $('create-actions').hidden = readonly;
  $('job-actions').hidden = !readonly;
}
function clearPreview() {
  $('video').pause(); $('video').removeAttribute('src'); $('video').load(); $('video').hidden = true;
  $('preview-empty').hidden = false; $('delivery').hidden = true; $('workflow-note').hidden = false;
  $('cover-editor').hidden = true; clearTimeout(editTimer);
  $('quality-summary').textContent = ''; $('video-error').textContent = '';
}
function newDraft(spec = null) {
  if (submitting) return;
  selected = null; clearTimeout(pollTimer); requestKey = null; pendingSpec = null;
  history.replaceState(null, '', '/');
  clearPreview(); setReadOnly(false); $('progress-section').hidden = true;
  $('page-title').textContent = '新建视频'; $('page-description').textContent = '写下想说的话，把其余的交给工作台。';
  $('preview-title').textContent = '下一支视频，从这里开始'; $('preview-subtitle').textContent = '提交文案后，工作台会匹配画面、生成配音，并检查最终成片。';
  $('form-error').textContent = ''; $('draft-label').textContent = '本机草稿';
  const draft = spec ? {spec} : storageGet('hainan-draft');
  setSpec(draft?.spec || {text:'', emotion_alpha:.8, width:1080, height:1920});
  requestKey = draft?.requestKey || null; pendingSpec = draft?.pendingSpec || null;
  if (spec) saveDraft();
  renderHistory();
}
function closeMobileHistory() { $('history-panel').classList.remove('open'); $('history-toggle').setAttribute('aria-expanded','false'); }
function renderHistory() {
  const visible = jobs.filter(job => filter === 'all' || (filter === 'active' ? activeStates.has(job.state) : job.state === 'done'));
  $('history').replaceChildren();
  if (!visible.length) { const p = document.createElement('p'); p.className = 'empty-history'; p.textContent = filter === 'all' ? '你的第一支视频，会留在这里。\n从新建视频开始吧。' : '暂无此类任务。'; $('history').append(p); return; }
  for (const job of visible) {
    const button = document.createElement('button'); button.className = 'history-item'; button.setAttribute('aria-current', String(selected?.id === job.id));
    const title = document.createElement('span'); title.className = 'history-title'; title.textContent = job.title; button.title = job.title;
    const meta = document.createElement('span'); meta.className = 'history-meta';
    const date = document.createElement('span'); date.textContent = new Date(job.created * 1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
    const state = document.createElement('span'); state.className = 'history-state'; state.dataset.state = job.state; state.textContent = labels[job.state] || job.state;
    meta.append(date,state); button.append(title,meta); button.onclick = () => { if (!submitting) { closeMobileHistory(); selectJob(job.id); } }; $('history').append(button);
  }
}
async function refreshHistory() {
  clearTimeout(refreshTimer);
  if (!authenticated) return;
  try {
    jobs = (await api('/api/jobs')).jobs; $('history-error').textContent = ''; renderHistory();
    const pending = jobs.filter(job => job.nas_id && !terminalStates.has(job.state) && job.id !== selected?.id);
    for (let offset = 0; offset < pending.length; offset += 4) {
      const results = await Promise.allSettled(pending.slice(offset,offset+4).map(job => api(`/api/jobs/${encodeURIComponent(job.id)}`)));
      for (const result of results) {
        if (result.status !== 'fulfilled') continue;
        const index = jobs.findIndex(job => job.id === result.value.id);
        if (index >= 0) jobs[index] = result.value;
      }
    }
    renderHistory();
  }
  catch (error) { $('history-error').textContent = error.message; }
  if (authenticated) refreshTimer = setTimeout(refreshHistory, 15000);
}
async function checkConnection() {
  clearTimeout(healthTimer); if (!authenticated) return;
  $('connection').disabled = true;
  try {
    const result = await api('/api/connection'); $('connection').dataset.state = 'online'; $('connection-text').textContent = '素材库已连接';
    $('connection-error').hidden = true; $('voice-preview').disabled = !result.voice_available;
    coverEditorAvailable = result.cover_editor_available === true;
    $('open-editor').hidden = !coverEditorAvailable;
    $('voice-preview').title = result.voice_available ? '试听你提供的主音色' : '此环境未配置试听音频';
  } catch (error) {
    $('connection').dataset.state = 'offline'; $('connection-text').textContent = '素材库连接异常';
    $('connection-error').textContent = error.message + ' 草稿仍可编辑，点击右上角重新检查。'; $('connection-error').hidden = false;
  } finally { $('connection').disabled = false; if (authenticated) healthTimer = setTimeout(checkConnection, 60000); }
}
function progressStage(job) {
  if (job.state === 'done') return 4;
  if (!job.nas_id || job.state === 'queued') return 0;
  const logs = job.snapshot?.logs || [];
  if (logs.some(log => /检查成片|复查成片/.test(log))) return 3;
  if (logs.some(log => /生成配音|音乐|导出画面|补充镜头/.test(log))) return 2;
  return 1;
}
function updateJobView(job) {
  selected = job;
  const index = jobs.findIndex(item => item.id === job.id); if (index < 0) jobs.unshift(job); else jobs[index] = job;
  renderHistory();
  $('job-status').textContent = labels[job.state] || job.state; $('job-status').dataset.state = job.state;
  const stage = progressStage(job);
  $('stages').querySelectorAll('li').forEach((li,index) => { li.classList.toggle('complete', index < stage); li.classList.toggle('active', index === stage); });
  const logs = job.snapshot?.logs || [];
  $('job-logs').textContent = logs.length ? logs.join('\n') : '任务已记录，等待 NAS 返回制作日志。';
  $('log-count').textContent = logs.length ? `(${logs.length})` : '';
  $('reconcile').hidden = !['uncertain','submitting','rejected'].includes(job.state);
  $('reconcile').textContent = job.state === 'rejected' ? '重新提交原任务' : '核对提交';
  $('resume').hidden = !['failed','interrupted'].includes(job.state);
  $('resume').textContent = `从${job.snapshot?.checkpoint || '保存进度'}继续`;
  $('failed-report').hidden = !(['failed','interrupted'].includes(job.state) && job.snapshot?.report_available);
  $('failed-report').href = `/api/jobs/${encodeURIComponent(job.id)}/artifacts/report`;
  $('job-message').textContent = job.state === 'interrupted'
    ? `${job.error ? job.error + ' ' : ''}已完成内容保留在 NAS。修正问题后，可点击续作。`
    : job.error || ({queued:'任务已进入 NAS 队列，轮到后会自动开始。',running:logs.at(-1) || '正在准备素材，请稍候。',done:'制作完成。成片已通过画面与声音检查，可以播放或下载。',failed:'任务未完成，请查看日志。已生成的中间文件保留在 NAS。'})[job.state] || '任务已记录，正在核对提交结果。';
  $('preview-title').textContent = {queued:'正在等待制作',running:'画面正在成形',failed:'这次制作没有完成',interrupted:'制作暂时中断',uncertain:'正在等待提交确认',rejected:'任务尚未开始'}[job.state] || '等待制作';
  $('preview-subtitle').textContent = terminalStates.has(job.state) && job.state !== 'done' ? '请查看左侧原因与制作日志。' : '可以离开页面，稍后回来查看。';
  if (job.state === 'done') showDelivery(job);
}
async function showDelivery(job) {
  const prefix = `/api/jobs/${encodeURIComponent(job.id)}/artifacts/`;
  $('preview-empty').hidden = true; $('workflow-note').hidden = true; $('video').hidden = false; $('delivery').hidden = false;
  $('open-editor').hidden = !coverEditorAvailable;
  $('download-cover').hidden = true;
  if ($('video').getAttribute('src') !== prefix + 'video') {
    $('video').src = prefix + 'video'; $('video-error').textContent = '';
    $('download-video').href = prefix + 'video?download=true'; $('download-captions').href = prefix + 'captions?download=true'; $('download-report').href = prefix + 'report';
    try {
      const report = await api(prefix + 'report');
      if (selected?.id !== job.id) return;
      $('quality-summary').textContent = report.passed ? '画面与声音检查通过。详细结论见检查报告。' : '检查报告需人工核对，请先查看报告。';
    } catch (error) { if (selected?.id === job.id) $('quality-summary').textContent = error.message; }
  }
  try {
    const edit = await api(`/api/jobs/${encodeURIComponent(job.id)}/edit-status`);
    if (selected?.id === job.id && edit.state === 'done') {
      $('download-cover').href = prefix + 'cover?download=true&v=' + encodeURIComponent(edit.result);
      $('download-cover').hidden = false;
    }
  } catch (_) { /* The original video remains available without an edited cover. */ }
}

function formatSecond(value) { const n = Math.floor(value); return `${Math.floor(n / 60).toString().padStart(2, '0')}:${(n % 60).toString().padStart(2, '0')}`; }
function chooseCover(index) {
  coverIndex = index;
  $('cover-grid').querySelectorAll('button').forEach(button => button.setAttribute('aria-checked', String(Number(button.dataset.index) === index)));
}
function renderCoverEditor(id, form) {
  const prefix = `/api/jobs/${encodeURIComponent(id)}/`;
  const saved = form.edit?.spec;
  $('cover-grid').replaceChildren();
  for (const option of form.covers) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'cover-choice';
    button.dataset.index = option.index; button.setAttribute('role', 'radio');
    button.setAttribute('aria-label', `封面候选 ${option.index + 1}，画面 ${formatSecond(option.time)}`);
    const img = document.createElement('img'); img.src = `${prefix}covers/${option.index}`; img.alt = ''; img.loading = 'lazy';
    const time = document.createElement('span'); time.textContent = formatSecond(option.time);
    button.append(img, time); button.onclick = () => chooseCover(option.index); $('cover-grid').append(button);
  }
  chooseCover(saved?.cover_index ?? 0);
  $('cover-text').value = saved?.cover_text || '';
  $('title-white').value = saved?.title?.white ?? saved?.titles?.[0]?.white ?? '';
  $('title-yellow').value = saved?.title?.yellow ?? saved?.titles?.[0]?.yellow ?? '';
}
async function pollEdit(id) {
  clearTimeout(editTimer);
  if (!authenticated || selected?.id !== id || $('cover-editor').hidden) return;
  try {
    const state = await api(`/api/jobs/${encodeURIComponent(id)}/edit-status`);
    if (selected?.id !== id) return;
    if (state.state === 'queued' || state.state === 'running') {
      $('edit-message').textContent = state.state === 'queued' ? '封面版已排队，正在等待导出。' : '正在叠加封面与标题，并检查成片。';
      $('save-edit').disabled = true;
      $('download-cover').hidden = true;
      editTimer = setTimeout(() => pollEdit(id), 4000);
    } else if (state.state === 'done') {
      $('save-edit').disabled = false;
      $('edit-message').textContent = '封面版已通过检查，可在右侧播放和下载。';
      const prefix = `/api/jobs/${encodeURIComponent(id)}/artifacts/`;
      const version = `?v=${encodeURIComponent(state.result || Date.now())}`;
      $('video').src = prefix + 'video' + version;
      $('download-video').href = prefix + 'video?download=true&v=' + encodeURIComponent(state.result || Date.now());
      $('download-cover').href = prefix + 'cover?download=true&v=' + encodeURIComponent(state.result || Date.now());
      $('download-cover').hidden = false;
      $('download-report').href = prefix + 'report' + version;
      $('quality-summary').textContent = '封面版画面与声音检查通过。';
    } else {
      $('save-edit').disabled = false;
      $('edit-message').textContent = state.error || '可以修改文字，再导出封面版。';
    }
  } catch (error) {
    if (selected?.id === id) { $('edit-message').textContent = error.message; editTimer = setTimeout(() => pollEdit(id), 10000); }
  }
}
async function openCoverEditor() {
  const id = selected?.id; if (!id || selected.state !== 'done') return;
  $('open-editor').disabled = true;
  $('cover-editor').hidden = false;
  $('edit-message').textContent = '正在从已选画面抽取封面候选…';
  try {
    const form = await api(`/api/jobs/${encodeURIComponent(id)}/edit`);
    if (selected?.id !== id) return;
    renderCoverEditor(id, form);
    $('edit-message').textContent = '选择封面画面，填写封面大黄字和整片固定标题。';
    $('cover-editor').scrollIntoView({behavior:'smooth',block:'start'});
    if (['queued','running','done'].includes(form.edit?.state)) pollEdit(id);
  } catch (error) { if (selected?.id === id) $('edit-message').textContent = error.message; }
  finally { $('open-editor').disabled = false; }
}
async function pollJob(id) {
  clearTimeout(pollTimer);
  if (!authenticated || selected?.id !== id) return;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
    if (selected?.id !== id) return;
    $('poll-error').textContent = ''; updateJobView(job);
    if (['queued','running'].includes(job.state)) pollTimer = setTimeout(() => pollJob(id), 3500);
  } catch (error) {
    if (selected?.id !== id) return;
    $('poll-error').textContent = error.message + ' 页面会继续尝试查询。';
    if (authenticated) pollTimer = setTimeout(() => pollJob(id), 10000);
  }
}
async function selectJob(id) {
  clearTimeout(pollTimer); clearPreview(); setReadOnly(true);
  selected = jobs.find(job => job.id === id) || {id};
  history.replaceState(null, '', `/?job=${encodeURIComponent(id)}`);
  $('page-title').textContent = '视频制作'; $('page-description').textContent = '从文案到成片，查看这次制作的每一步。';
  $('draft-label').textContent = '已保存的任务'; $('progress-section').hidden = false; $('form-error').textContent = ''; $('poll-error').textContent = '';
  if (selected.spec) { setSpec(selected.spec); updateJobView(selected); }
  else {
    try { const job = await api(`/api/jobs/${encodeURIComponent(id)}`); if (selected?.id !== id) return; setSpec(job.spec); updateJobView(job); }
    catch (error) { if (selected?.id === id) { newDraft(); toast(error.message); } return; }
  }
  await pollJob(id);
}
$('create-form').onsubmit = async event => {
  event.preventDefault(); if (submitting || selected) return;
  const spec = draftSpec(); if (!spec.text.trim()) { $('form-error').textContent = '先写下视频文案，再开始制作。'; $('editor').focus(); return; }
  if (!requestKey || JSON.stringify(spec) !== JSON.stringify(pendingSpec)) requestKey = crypto.randomUUID();
  pendingSpec = spec; storageSet('hainan-draft',{spec,requestKey,pendingSpec});
  submitting = true; $('generate').disabled = true; $('generate').firstChild.textContent = '正在提交… '; $('form-error').textContent = '';
  try {
    const job = await api('/api/jobs',{method:'POST',body:JSON.stringify({...spec,request_id:requestKey})});
    jobs.unshift(job); submitting = false; storageSet('hainan-draft',{spec,requestKey:null,pendingSpec:null});
    await selectJob(job.id); refreshHistory();
  } catch (error) { $('form-error').textContent = error.message + ' 再次点击会核对同一次提交。'; }
  finally { submitting = false; $('generate').disabled = false; $('generate').firstChild.textContent = '开始制作 '; }
};
$('reconcile').onclick = async () => {
  const id = selected?.id; if (!id) return;
  $('reconcile').disabled = true;
  try { const job = await api(`/api/jobs/${encodeURIComponent(id)}/reconcile`,{method:'POST'}); if (selected?.id === id) { updateJobView(job); pollJob(id); } }
  catch (error) { if (selected?.id === id) $('poll-error').textContent = error.message; }
  finally { $('reconcile').disabled = false; }
};
$('resume').onclick = async () => {
  const id = selected?.id; if (!id) return;
  $('resume').disabled = true;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}/resume`, {method:'POST'});
    if (selected?.id === id) { updateJobView(job); pollJob(id); }
  } catch (error) { if (selected?.id === id) $('poll-error').textContent = error.message; }
  finally { $('resume').disabled = false; }
};
$('music-select').onchange = () => { musicSelection = $('music-select').value; saveDraft(); };
$('music-upload').onclick = async () => {
  const file = $('music-file').files?.[0];
  if (!file) { $('music-status').textContent = '请先选择音乐文件'; return; }
  if (file.size > 100 * 1024 * 1024) { $('music-status').textContent = '音乐文件不能超过 100 MB'; return; }
  $('music-upload').disabled = true; $('music-status').textContent = '正在上传音乐到腾讯 COS…';
  try {
    const response = await fetch('/api/music', {method:'PUT', body:file, credentials:'same-origin',
      headers:{'Content-Type':'application/octet-stream','X-Workbench-Request':'1','X-Music-Name':encodeURIComponent(file.name)},
      signal:AbortSignal.timeout(180000)});
    const value = await response.json();
    if (!response.ok) throw new Error(value.detail || '上传失败');
    musicSelection = value.key; await loadMusic(); $('music-select').value = musicSelection;
    $('music-file').value = ''; saveDraft(); toast('音乐已上传并选中');
  } catch (error) { $('music-status').textContent = error.message || '上传失败，请重试'; }
  finally { $('music-upload').disabled = Boolean(selected); }
};
$('new-job').onclick = () => { newDraft(); closeMobileHistory(); $('editor').focus(); };
$('mobile-new-job').onclick = $('new-job').onclick;
$('reuse').onclick = () => { const spec = selected?.spec; if (spec) { newDraft(spec); $('editor').focus(); toast('已沿用文案，修改后可制作新视频。'); } };
$('open-editor').onclick = openCoverEditor;
$('save-edit').onclick = async () => {
  const id = selected?.id; if (!id) return;
  const spec = {cover_index:coverIndex, cover_text:$('cover-text').value.trim(),
    title:{white:$('title-white').value.trim(), yellow:$('title-yellow').value.trim()}};
  if (!spec.cover_text || !spec.title.white || !spec.title.yellow) {
    $('edit-message').textContent = '请填写封面大黄字和整片固定的白字、黄字。'; return;
  }
  $('save-edit').disabled = true; $('edit-message').textContent = '正在提交封面设置…';
  try { await api(`/api/jobs/${encodeURIComponent(id)}/edit`, {method:'POST',body:JSON.stringify(spec)}); if (selected?.id === id) pollEdit(id); }
  catch (error) { if (selected?.id === id) $('edit-message').textContent = error.message; $('save-edit').disabled = false; }
};
$('example').onclick = () => { if ($('editor').value.trim()) { toast('先清空文案，再填入示例，避免覆盖你的内容。'); return; } $('editor').value = sample; saveDraft(); $('editor').focus(); };
$('editor').addEventListener('input', saveDraft);
$('history-filters').onclick = event => { const button = event.target.closest('button[data-filter]'); if (!button) return; filter = button.dataset.filter; $('history-filters').querySelectorAll('button').forEach(el => el.setAttribute('aria-pressed', String(el === button))); renderHistory(); };
$('history-toggle').onclick = () => { const open = $('history-panel').classList.toggle('open'); $('history-toggle').setAttribute('aria-expanded', String(open)); };
$('refresh-history').onclick = refreshHistory; $('connection').onclick = checkConnection;
$('voice-preview').onclick = async () => { if (!voice.paused) { voice.pause(); return; } try { await voice.play(); } catch { toast('试听音频暂时无法播放，请稍后重试。'); } };
function voiceLabel() { $('voice-preview').querySelector('span').textContent = voice.paused ? '试听音色' : '停止试听'; }
voice.onplay = () => { pauseMusic(); $('video').pause(); voiceLabel(); }; voice.onpause = voiceLabel; voice.onended = voiceLabel;
$('video').addEventListener('error', () => { if ($('video').getAttribute('src')) $('video-error').textContent = '视频加载失败，请检查素材库连接后刷新此任务。'; });
$('video').addEventListener('play', () => { pauseMusic(); voice.pause(); });
$('login-form').onsubmit = async event => {
  event.preventDefault(); $('login-button').disabled = true; $('login-error').textContent = '';
  try { await api('/api/login',{method:'POST',body:JSON.stringify({password:$('password').value})}); $('password').value = ''; await boot(); }
  catch (error) { $('login-error').textContent = error.message; }
  finally { $('login-button').disabled = false; }
};
$('logout').onclick = async () => { try { await api('/api/logout',{method:'POST'}); showLogin(); } catch (error) { toast(error.message); } };
async function boot() {
  stopTimers();
  try {
    const session = await api('/api/session');
    if (!session.authenticated) { showLogin(); return; }
    authenticated = true; $('login-view').hidden = true; $('loading-view').hidden = true; $('app').hidden = false; $('logout').hidden = !session.login_required;
    const jobId = new URLSearchParams(location.search).get('job');
    newDraft(); await Promise.all([refreshHistory(), loadMusic()]); if (jobId) await selectJob(jobId); checkConnection();
  } catch (error) { $('loading-view').querySelector('p').textContent = error.message; $('reload-app').hidden = false; }
}
$('reload-app').onclick = boot;
document.addEventListener('visibilitychange', () => { if (!document.hidden && authenticated) { refreshHistory(); if (selected && !terminalStates.has(selected.state)) pollJob(selected.id); } });
boot();
