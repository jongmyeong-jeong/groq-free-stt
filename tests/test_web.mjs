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
