import { MicrophoneRecording, recordingStorage, stopOnPageHide, validateRecoveredRecording } from './microphone.mjs';

export const deletionMessage = kind => (kind === 'source' ? '이 전사 항목의 원본 음성만 삭제합니다. 전사 결과는 유지됩니다.' : '이 전사 항목의 원본과 전사 결과를 삭제합니다.') + ' 보관된 녹음 목록에 있는 별도 복사본은 유지됩니다. 삭제할까요?';
export const recordingLabel = status => ({recovery_pending:'복구 확인 필요', recovered:'복구됨', recovery_failed:'복구 실패', failed:'복구 실패', finished:'녹음 완료'}[status] || status);
export const canTranscribeRecording = status => ['recovered', 'finished'].includes(status);
export const activeJob = jobs => jobs.find(job => ['transcribing', 'paused', 'waiting_limit'].includes(job.status));
export const statusLabel = status => ({transcribing:'전사 중', paused:'일시 정지', waiting_limit:'한도 대기', completed:'전사 완료', stopped:'정지됨', failed:'실패', recovery_pending:'복구 확인 필요'}[status] || status);
export async function request(path, options = {}, fetcher = fetch) {
  const response = await fetcher(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `요청 실패 (${response.status})`);
  }
  return response.json();
}

if (typeof document !== 'undefined') start();
function start() {
  const root = document.querySelector('#app');
  let jobs = [], selected = null, busy = false, notice = '', recordings = [], systemRecordings = [], systemRecording = null, recording = null, paused = false, removePageHide = null;
  const el = (tag, text, cls) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; };
  const button = (text, action, cls) => { const node = el('button', text, cls); node.type = 'button'; node.onclick = action; return node; };
  const failedStarts = new Set(JSON.parse(sessionStorage.getItem('failed-transcription-starts') || '[]'));
  const json = body => ({method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  async function run(action) { if (busy) return; busy = true; notice = ''; render(); try { await action(); await refresh(); } catch (error) { notice = error.message; } finally { busy = false; render(); } }
  async function startJob(id) { try { await request(`/jobs/${encodeURIComponent(id)}/transcribe`, json({})); failedStarts.delete(id); } catch (error) { failedStarts.add(id); throw error; } finally { sessionStorage.setItem('failed-transcription-starts', JSON.stringify([...failedStarts])); } }
  async function refresh() { jobs = await request('/jobs'); recordings = await recordingStorage().list(); if (!systemRecording) systemRecordings = await request('/system-recordings').catch(() => []); if (selected) selected = jobs.find(job => job.id === selected.id) || null; }
  async function transcribeSource(path, name) { const {source} = await request(path); const bytes = Uint8Array.from(atob(source), char => char.charCodeAt(0)); const job = await request('/uploads', {method:'POST', headers:{'Content-Type':'application/octet-stream', 'X-Filename':encodeURIComponent(name)}, body:bytes}); selected = job; await startJob(job.id); }
  function home() { selected = null; render(); }
  function confirmAction(message, action) { if (window.confirm(message)) run(action); }
  function link(job, format) { const a = el('a', format.toUpperCase()); a.href = `/jobs/${encodeURIComponent(job.id)}/${format}`; a.download = ''; return a; }
  function render() {
    root.replaceChildren();
    const main = el('main', null, 'shell'); root.append(main);
    if (notice) { const alert = el('p', notice, 'notice'); alert.setAttribute('role', 'alert'); main.append(alert); }
    if (systemRecording) {
      main.append(el('h1', paused ? '시스템 소리 일시 정지' : '시스템 소리 녹음 중'));
      const controls = el('div', null, 'actions');
      controls.append(button(paused ? '계속' : '일시 정지', () => run(async () => { await request(`/system-recording/${paused ? 'resume' : 'pause'}`, json({})); paused = !paused; })));
      controls.append(button('녹음 종료', () => run(async () => { await request('/system-recording/stop', json({})); systemRecording = null; paused = false; }))); main.append(controls);
    } else if (recording) {
      main.append(el('h1', paused ? '마이크 일시 정지' : '마이크 녹음 중'));
      const controls = el('div', null, 'actions');
      controls.append(button(paused ? '계속' : '일시 정지', () => { paused ? recording.resume() : recording.pause(); paused = !paused; render(); }));
      controls.append(button('녹음 종료', () => run(async () => {
        const current = recording; try { await current.stop(); } finally { removePageHide?.(); recording = null; paused = false; }
        const {source} = await recordingStorage().source(current.id);
        const bytes = Uint8Array.from(atob(source), char => char.charCodeAt(0));
        const job = await request('/uploads', {method:'POST', headers:{'Content-Type':'application/octet-stream', 'X-Filename':encodeURIComponent('마이크 녹음.webm')}, body:bytes});
        selected = job; await startJob(job.id);
      }))); main.append(controls);
    } else if (!selected) {
      const active = activeJob(jobs);
      const cards = el('div', null, 'cards');
      const file = document.createElement('input'); file.type = 'file'; file.accept = 'audio/*,.mp4,.mpeg,.mpga'; file.hidden = true;
      file.onchange = () => { const chosen = file.files[0]; if (!chosen) return; run(async () => { const job = await request('/uploads', {method:'POST', headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(chosen.name)}, body:chosen}); selected = job; await startJob(job.id); }); file.value = ''; };
      const source = (title, description, action, available = true) => { const b = button('', action, 'card'); b.append(el('strong', title), el('small', description)); b.disabled = busy || !!active || !available; cards.append(b); };
      source('시스템 소리', 'Mac에서 재생되는 소리를 녹음합니다.', () => run(async () => { systemRecording = await request('/system-recording/start', json({})); }));
      source('마이크', '마이크로 들어오는 소리를 녹음합니다.', () => run(async () => { recording = await MicrophoneRecording.start({storage:recordingStorage()}); removePageHide = stopOnPageHide(recording); }));
      source('음성 파일', '기존 음성 파일 하나를 전사합니다.', () => file.click());
      main.append(cards, file);
      if (active) main.append(el('p', '진행 중인 전사를 완료하거나 정지한 뒤 새 작업을 시작할 수 있습니다.', 'hint'));
      const section = el('section'); section.append(el('h1', '전사 목록'));
      if (!jobs.length) section.append(el('p', '아직 전사한 항목이 없습니다.', 'hint'));
      for (const job of jobs) { const row = el('div', null, 'row'); const open = button('', () => { selected = job; render(); }, 'open'); open.append(el('strong', job.name), el('small', statusLabel(job.status))); row.append(open); if (job.status === 'completed') { row.append(link(job, 'md'), link(job, 'txt')); } section.append(row); }
      main.append(section);
      const recoveries = [...recordings.filter(item => ['recovery_pending','recovered','recovery_failed','finished'].includes(item.status)).map(item => ({...item, type:'mic'})), ...systemRecordings.map(item => ({...item, type:'system'}))];
      if (recoveries.length) { const recovery = el('section', null, 'recovery'); recovery.append(el('h2', '보관된 녹음')); for (const item of recoveries) { const row = el('div', null, 'row'); row.append(el('span', `${item.type === 'mic' ? '마이크' : '시스템 소리'} · ${recordingLabel(item.status)}`)); if (item.type === 'mic' && item.status === 'recovery_pending') row.append(button('복구 확인', () => run(async () => { const context = new AudioContext(); try { await validateRecoveredRecording(recordingStorage(), item.id, context); } finally { await context.close(); } }))); if (canTranscribeRecording(item.status)) { const b = button('전사', () => run(() => transcribeSource(`/${item.type === 'mic' ? 'recordings' : 'system-recordings'}/${encodeURIComponent(item.id)}/source`, item.type === 'mic' ? '마이크 녹음.webm' : '시스템 녹음.wav'))); b.disabled = !!active || busy; row.append(b); } recovery.append(row); } main.append(recovery); }

    } else {
      const job = selected, id = encodeURIComponent(job.id);
      main.append(button('← 뒤로 가기', home, 'back'));
      const title = el('h1', job.name); main.append(title, el('p', failedStarts.has(job.id) && job.status === 'transcribing' ? '전사 시작 실패' : statusLabel(job.status), 'hint'));
      if (job.status === 'completed') { const text = job.result?.text || job.result?.transcript || job.result?.txt; if (text) main.append(el('pre', text)); else main.append(el('p', '다운로드 파일에서 전사 내용을 확인하세요.', 'hint')); const downloads = el('div', null, 'actions'); downloads.append(link(job, 'md'), link(job, 'txt')); main.append(downloads); }
      if (['transcribing','paused','waiting_limit'].includes(job.status)) {
        main.append(el('p', failedStarts.has(job.id) && job.status === 'transcribing' ? '전사를 시작하지 못했습니다. 문제를 해결한 뒤 다시 시작하세요.' : '전사가 백그라운드에서 진행됩니다. 화면을 떠나도 정지되지 않습니다.', 'hint'));
        if (job.status === 'waiting_limit' && Number.isFinite(job.result?.wait_until)) main.append(el('p', `자동 재시도 예정: ${new Date(job.result.wait_until * 1000).toLocaleString()}`, 'hint'));
        if (job.result?.pause_requested) main.append(el('p', '현재 구간을 마친 뒤 일시 정지합니다.', 'hint'));
        if (job.result?.error) main.append(el('p', job.result.error, 'notice'));
        const controls = el('div', null, 'actions');
        if (job.status === 'paused') controls.append(button('계속', () => run(() => startJob(job.id))));
        else { const pause = button(job.result?.pause_requested ? '일시 정지 중' : '일시 정지', () => run(() => request(`/jobs/${id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({status:'paused'})}))); pause.disabled = !!job.result?.pause_requested || busy; controls.append(pause); }
        if (failedStarts.has(job.id) && job.status === 'transcribing') controls.append(button('전사 다시 시작', () => run(() => startJob(job.id))));
        controls.append(button('정지', () => confirmAction('전사를 정지하면 부분 전사는 삭제되고 원본 음성은 보관됩니다. 정지할까요?', async () => { await request(`/jobs/${id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({status:'stopped'})}); selected = null; })));
        main.append(controls);
      }
      if (job.status === 'stopped') { main.append(el('p', job.has_source ? '원본이 보관되어 있습니다.' : '원본이 삭제되었습니다.', 'hint')); if (job.has_source) { const retry = button('다시 전사', () => run(() => transcribeSource(`/jobs/${id}/source`, job.name))); retry.disabled = !!activeJob(jobs) || busy; main.append(retry); } }
      const actions = el('div', null, 'actions');
      actions.append(button('제목 수정', () => { const name = window.prompt('전사 제목', job.name); if (name?.trim() && name.trim() !== job.name) run(() => request(`/jobs/${id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:name.trim()})})); }));
      if (job.has_source && !['transcribing','paused','waiting_limit'].includes(job.status)) actions.append(button('원본 삭제', () => confirmAction(deletionMessage('source'), () => request(`/jobs/${id}/source`, {method:'DELETE'}))));
      if (!['transcribing','paused','waiting_limit'].includes(job.status)) actions.append(button('전체 삭제', () => confirmAction(deletionMessage('job'), async () => { await request(`/jobs/${id}`, {method:'DELETE'}); selected = null; })));
      main.append(actions);
    }
  }
  refresh().catch(error => { notice = error.message; }).finally(render);
  setInterval(() => { if (systemRecording) request('/system-recording/heartbeat', json({})).catch(error => { notice = error.message; systemRecording = null; render(); }); }, 5000);
  setInterval(async () => { if (busy || recording || systemRecording) return; const before = JSON.stringify(jobs); try { await refresh(); if (JSON.stringify(jobs) !== before) { const focused = document.activeElement; const controls = [...root.querySelectorAll('button,a')]; const index = controls.indexOf(focused); const label = focused?.textContent; render(); if (index >= 0) { const next = [...root.querySelectorAll('button,a')]; (next.find(node => node.textContent === label) || next[Math.min(index, next.length - 1)])?.focus(); } } } catch {} }, 3000);
}
