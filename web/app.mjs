import { MicrophoneRecording, recordingStorage, stopOnPageHide } from './microphone.mjs';

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
  let jobs = [], selected = null, busy = false, notice = '', recording = null, paused = false, removePageHide = null;
  const el = (tag, text, cls) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; };
  const button = (text, action, cls) => { const node = el('button', text, cls); node.type = 'button'; node.onclick = action; return node; };
  const json = body => ({method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  async function run(action) { if (busy) return; busy = true; notice = ''; render(); try { await action(); await refresh(); } catch (error) { notice = error.message; } finally { busy = false; render(); } }
  async function refresh() { jobs = await request('/jobs'); if (selected) selected = jobs.find(job => job.id === selected.id) || null; }
  function home() { selected = null; render(); }
  function confirmAction(message, action) { if (window.confirm(message)) run(action); }
  function link(job, format) { const a = el('a', format.toUpperCase()); a.href = `/jobs/${encodeURIComponent(job.id)}/download/${format}`; a.download = ''; return a; }
  function render() {
    root.replaceChildren();
    const main = el('main', null, 'shell'); root.append(main);
    if (notice) { const alert = el('p', notice, 'notice'); alert.setAttribute('role', 'alert'); main.append(alert); }
    if (recording) {
      main.append(el('h1', paused ? '마이크 일시 정지' : '마이크 녹음 중'));
      const controls = el('div', null, 'actions');
      controls.append(button(paused ? '계속' : '일시 정지', () => { paused ? recording.resume() : recording.pause(); paused = !paused; render(); }));
      controls.append(button('녹음 종료', () => run(async () => {
        const current = recording; await current.stop(); removePageHide?.(); recording = null; paused = false;
        const {source} = await recordingStorage().source(current.id);
        const bytes = Uint8Array.from(atob(source), char => char.charCodeAt(0));
        const job = await request('/uploads', {method:'POST', headers:{'Content-Type':'application/octet-stream', 'X-Filename':encodeURIComponent('마이크 녹음.webm')}, body:bytes});
        selected = job; await request(`/jobs/${encodeURIComponent(job.id)}/transcribe`, json({}));
      }))); main.append(controls);
    } else if (!selected) {
      const active = activeJob(jobs);
      const cards = el('div', null, 'cards');
      const file = document.createElement('input'); file.type = 'file'; file.accept = 'audio/*,.mp4,.mpeg,.mpga'; file.hidden = true;
      file.onchange = () => { const chosen = file.files[0]; if (!chosen) return; run(async () => { const job = await request('/uploads', {method:'POST', headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(chosen.name)}, body:chosen}); selected = job; await request(`/jobs/${encodeURIComponent(job.id)}/transcribe`, json({})); }); file.value = ''; };
      const source = (title, description, action, available = true) => { const b = button('', action, 'card'); b.append(el('strong', title), el('small', description)); b.disabled = busy || !!active || !available; cards.append(b); };
      source('시스템 소리', 'Mac에서 재생되는 소리를 녹음합니다. 준비 중', () => {}, false);
      source('마이크', '마이크로 들어오는 소리를 녹음합니다.', () => run(async () => { recording = await MicrophoneRecording.start({storage:recordingStorage()}); removePageHide = stopOnPageHide(recording); }));
      source('음성 파일', '기존 음성 파일 하나를 전사합니다.', () => file.click());
      main.append(cards, file);
      if (active) main.append(el('p', '진행 중인 전사를 완료하거나 정지한 뒤 새 작업을 시작할 수 있습니다.', 'hint'));
      const section = el('section'); section.append(el('h1', '전사 목록'));
      if (!jobs.length) section.append(el('p', '아직 전사한 항목이 없습니다.', 'hint'));
      for (const job of jobs) { const row = el('div', null, 'row'); const open = button('', () => { selected = job; render(); }, 'open'); open.append(el('strong', job.name), el('small', statusLabel(job.status))); row.append(open); if (job.status === 'completed') { row.append(link(job, 'md'), link(job, 'txt')); } section.append(row); }
      main.append(section);
    } else {
      const job = selected, id = encodeURIComponent(job.id);
      main.append(button('← 뒤로 가기', home, 'back'));
      const title = el('h1', job.name); main.append(title, el('p', statusLabel(job.status), 'hint'));
      if (job.status === 'completed') { const text = job.result?.text || job.result?.transcript; if (text) main.append(el('pre', text)); else main.append(el('p', '다운로드 파일에서 전사 내용을 확인하세요.', 'hint')); const downloads = el('div', null, 'actions'); downloads.append(link(job, 'md'), link(job, 'txt')); main.append(downloads); }
      if (['transcribing','paused','waiting_limit'].includes(job.status)) main.append(el('p', '전사가 백그라운드에서 진행됩니다. 화면을 떠나도 정지되지 않습니다.', 'hint'));
      if (job.status === 'stopped') main.append(el('p', job.has_source ? '원본이 보관되어 있습니다. 재전사는 준비 중입니다.' : '원본이 삭제되었습니다.', 'hint'));
      const actions = el('div', null, 'actions');
      actions.append(button('제목 수정', () => { const name = window.prompt('전사 제목', job.name); if (name?.trim() && name.trim() !== job.name) run(() => request(`/jobs/${id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:name.trim()})})); }));
      if (job.has_source) actions.append(button('원본 삭제', () => confirmAction('원본 음성만 삭제합니다. 전사 결과는 유지됩니다. 삭제할까요?', () => request(`/jobs/${id}/source`, {method:'DELETE'}))));
      actions.append(button('전체 삭제', () => confirmAction('원본과 전사 결과를 모두 삭제합니다. 삭제할까요?', async () => { await request(`/jobs/${id}`, {method:'DELETE'}); selected = null; })));
      main.append(actions);
    }
  }
  refresh().catch(error => { notice = error.message; }).finally(render);
  setInterval(async () => { if (busy || recording || document.activeElement?.closest('main')) return; const before = JSON.stringify(jobs); try { await refresh(); if (JSON.stringify(jobs) !== before) render(); } catch {} }, 3000);
}
