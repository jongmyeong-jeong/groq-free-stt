import test from 'node:test';
import assert from 'node:assert/strict';
import { activeJob, statusLabel, request } from '../web/app.mjs';

test('unfinished work blocks a new transcription', () => {
  assert.equal(activeJob([{status:'completed'}, {id:'x', status:'waiting_limit'}]).id, 'x');
  assert.equal(activeJob([{status:'stopped'}]), undefined);
});

test('job states are labelled for the user', () => {
  assert.equal(statusLabel('waiting_limit'), '한도 대기');
  assert.equal(statusLabel('stopped'), '정지됨');
});

test('failed requests expose the server error', async () => {
  await assert.rejects(request('/jobs', {}, async () => ({ok:false, status:409, json:async () => ({error:'이미 진행 중'})})), /이미 진행 중/);
});

test('recording recovery states explain available actions', async () => {
  const { recordingLabel, canTranscribeRecording } = await import('../web/app.mjs');
  assert.equal(recordingLabel('recovery_failed'), '복구 실패');
  assert.equal(canTranscribeRecording('recovery_failed'), false);
  assert.equal(canTranscribeRecording('recovered'), true);
});

test('deletion explains retained recording copies', async () => {
  const { deletionMessage } = await import('../web/app.mjs');
  assert.match(deletionMessage('source'), /보관된 녹음.*별도/);
  assert.match(deletionMessage('job'), /보관된 녹음.*별도/);
});

test('system recording creates a job without transferring audio through the browser', async () => {
  const { createSystemJob } = await import('../web/app.mjs');
  let sent;
  const job = await createSystemJob('recording-id', async (path, options) => { sent = {path, options}; return {ok:true, json:async () => ({id:'job-id'})}; });
  assert.equal(sent.path, '/system-recordings/recording-id/create-job');
  assert.equal(sent.options.method, 'POST');
  assert.equal(sent.options.body, '{}');
  assert.equal(job.id, 'job-id');
});

test('failed system capture is usable only when a partial source was recovered', async () => {
  const { canTranscribeRecovery } = await import('../web/app.mjs');
  assert.equal(canTranscribeRecovery({type:'system', status:'failed', source:'abc.wav'}), true);
  assert.equal(canTranscribeRecovery({type:'system', status:'failed', source:null}), false);
  assert.equal(canTranscribeRecovery({type:'mic', status:'recovery_failed', source:'abc'}), false);
});

test('microphone recordings and retries create jobs on the server', async () => {
  const { createMicrophoneJob, retryJob } = await import('../web/app.mjs');
  const calls = [];
  const fetcher = async (path, options) => { calls.push({path, options}); return {ok:true, json:async () => ({id:'job'})}; };
  await createMicrophoneJob('mic', '녹음.webm', fetcher);
  await retryJob('old', fetcher);
  assert.equal(calls[0].path, '/recordings/mic/jobs');
  assert.deepEqual(JSON.parse(calls[0].options.body), {name:'녹음.webm'});
  assert.equal(calls[1].path, '/jobs/old/retry');
});
