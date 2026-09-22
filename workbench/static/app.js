'use strict';
const $ = id => document.getElementById(id);
const labels = {submitting:'正在提交', uncertain:'等待核对', rejected:'未提交成功', queued:'等待制作', running:'正在制作', done:'已完成', failed:'制作失败', interrupted:'制作中断'};
const activeStates = new Set(['queued','running','submitting','uncertain']);
const terminalStates = new Set(['done','failed','interrupted']);
const sample = '我认为全中国冬天最舒服的城市就是海南的三亚和陵水，这俩地方冬天气温25-28度，我每年都会带着爸妈来这里过冬，就住在三亚海棠湾的这家高端旅居基地。\n\n我比较喜欢这里的一点，就是爸妈住进来以后基本不用操什么心。住宿、吃饭、水电、网络这些都包含了，每天一日三餐都是自助餐，房间也会定期有人打扫。\n\n平时想活动一下，可以泡温泉、游泳、健身，园区里面每天也有不少同龄人一起散步、聊天、参加活动。这里还有医生全天在岗。\n\n如果你也想带爸妈来海南过冬，评论区扣1，我把价格和地址发给你看看。';
let jobs = [], selected = null, filter = 'all', pollTimer, refreshTimer, healthTimer, toastTimer, submitting = false, authenticated = false, requestKey = null, pendingSpec = null;
const voice = new Audio('/api/reference/voice');

function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4000); }
function storageGet(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } }
function storageSet(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } }
function stopTimers() { clearTimeout(pollTimer); clearTimeout(refreshTimer); clearTimeout(healthTimer); }
function showLogin() { authenticated = false; stopTimers(); voice.pause(); $('video').pause(); $('app').hidden = true; $('loading-view').hidden = true; $('login-view').hidden = false; }
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
  const portrait = document.querySelector('input[name=orientation]:checked').value === 'portrait';
  const short = Number($('resolution').value), long = short === 1080 ? 1920 : 1280;
  return {text:$('editor').value, emotion_alpha:Number($('emotion').value), width:portrait ? short : long, height:portrait ? long : short};
}
function setSpec(spec) {
  $('editor').value = spec.text || '';
  $('emotion').value = spec.emotion_alpha ?? .8;
  document.querySelector(`input[name=orientation][value=${(spec.width || 1080) < (spec.height || 1920) ? 'portrait' : 'landscape'}]`).checked = true;
  $('resolution').value = Math.min(spec.width || 1080, spec.height || 1920) === 720 ? '720' : '1080';
  updateControls();
}
function updateControls() {
  $('counter').value = `${$('editor').value.length} / 6000`;
  $('emotion-value').value = Number($('emotion').value).toFixed(2);
  const spec = draftSpec(), portrait = spec.width < spec.height;
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
  ['emotion','resolution'].forEach(id => $(id).disabled = readonly);
  document.querySelectorAll('input[name=orientation]').forEach(el => el.disabled = readonly);
  $('example').hidden = readonly;
  $('create-actions').hidden = readonly;
  $('job-actions').hidden = !readonly;
}
function clearPreview() {
  $('video').pause(); $('video').removeAttribute('src'); $('video').load(); $('video').hidden = true;
  $('preview-empty').hidden = false; $('delivery').hidden = true; $('workflow-note').hidden = false;
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
  $('job-message').textContent = job.state === 'interrupted'
    ? `${job.error ? job.error + ' ' : ''}本页面暂不支持中断续作。请查看制作日志，核对已生成的内容后，可“沿用文案新建”，这会创建新任务。`
    : job.error || ({queued:'任务已进入 NAS 队列，轮到后会自动开始。',running:logs.at(-1) || '正在准备素材，请稍候。',done:'制作完成。成片已通过画面与声音检查，可以播放或下载。',failed:'任务未完成，请查看日志。已生成的中间文件保留在 NAS。'})[job.state] || '任务已记录，正在核对提交结果。';
  $('preview-title').textContent = {queued:'正在等待制作',running:'画面正在成形',failed:'这次制作没有完成',interrupted:'制作暂时中断',uncertain:'正在等待提交确认',rejected:'任务尚未开始'}[job.state] || '等待制作';
  $('preview-subtitle').textContent = terminalStates.has(job.state) && job.state !== 'done' ? '请查看左侧原因与制作日志。' : '可以离开页面，稍后回来查看。';
  if (job.state === 'done') showDelivery(job);
}
async function showDelivery(job) {
  const prefix = `/api/jobs/${encodeURIComponent(job.id)}/artifacts/`;
  $('preview-empty').hidden = true; $('workflow-note').hidden = true; $('video').hidden = false; $('delivery').hidden = false;
  if ($('video').getAttribute('src') !== prefix + 'video') {
    $('video').src = prefix + 'video'; $('video-error').textContent = '';
    $('download-video').href = prefix + 'video?download=true'; $('download-captions').href = prefix + 'captions?download=true'; $('download-report').href = prefix + 'report';
    try {
      const report = await api(prefix + 'report');
      if (selected?.id !== job.id) return;
      $('quality-summary').textContent = report.passed ? '画面与声音检查通过。详细结论见检查报告。' : '检查报告需人工核对，请先查看报告。';
    } catch (error) { if (selected?.id === job.id) $('quality-summary').textContent = error.message; }
  }
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
$('new-job').onclick = () => { newDraft(); closeMobileHistory(); $('editor').focus(); };
$('mobile-new-job').onclick = $('new-job').onclick;
$('reuse').onclick = () => { const spec = selected?.spec; if (spec) { newDraft(spec); $('editor').focus(); toast('已沿用文案与设置，修改后可制作新视频。'); } };
$('example').onclick = () => { if ($('editor').value.trim()) { toast('先清空文案，再填入示例，避免覆盖你的内容。'); return; } $('editor').value = sample; saveDraft(); $('editor').focus(); };
$('editor').addEventListener('input', saveDraft); $('emotion').addEventListener('input', saveDraft);
$('resolution').addEventListener('change', saveDraft); document.querySelectorAll('input[name=orientation]').forEach(el => el.addEventListener('change', saveDraft));
$('history-filters').onclick = event => { const button = event.target.closest('button[data-filter]'); if (!button) return; filter = button.dataset.filter; $('history-filters').querySelectorAll('button').forEach(el => el.setAttribute('aria-pressed', String(el === button))); renderHistory(); };
$('history-toggle').onclick = () => { const open = $('history-panel').classList.toggle('open'); $('history-toggle').setAttribute('aria-expanded', String(open)); };
$('refresh-history').onclick = refreshHistory; $('connection').onclick = checkConnection;
$('voice-preview').onclick = async () => { if (!voice.paused) { voice.pause(); return; } try { await voice.play(); } catch { toast('试听音频暂时无法播放，请稍后重试。'); } };
function voiceLabel() { $('voice-preview').querySelector('span').textContent = voice.paused ? '试听音色' : '停止试听'; }
voice.onplay = voiceLabel; voice.onpause = voiceLabel; voice.onended = voiceLabel;
$('video').addEventListener('error', () => { if ($('video').getAttribute('src')) $('video-error').textContent = '视频加载失败，请检查素材库连接后刷新此任务。'; });
$('video').addEventListener('play', () => voice.pause());
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
    newDraft(); await refreshHistory(); if (jobId) await selectJob(jobId); checkConnection();
  } catch (error) { $('loading-view').querySelector('p').textContent = error.message; $('reload-app').hidden = false; }
}
$('reload-app').onclick = boot;
document.addEventListener('visibilitychange', () => { if (!document.hidden && authenticated) { refreshHistory(); if (selected && !terminalStates.has(selected.state)) pollJob(selected.id); } });
boot();
