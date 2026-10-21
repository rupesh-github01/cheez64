import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { webcrypto, createHash } from 'node:crypto';
import ts from 'typescript';
function helpers(fetch) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(new URL('../src/lib/practice.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, { exports, process: { env: {} }, fetch, crypto: webcrypto, TextEncoder, Uint8Array });
  return exports;
}
test('practice has no mount-time writes and uses explicit guarded JSON actions', async () => {
  const calls = []; const api = helpers(async (...args) => { calls.push(args); return { ok: true, json: async () => ({}) }; });
  assert.equal(calls.length, 0);
  await api.practiceWrite('/attempts/x/events', { kind: 'move', revision: 1, request_key: 'same', uci: 'a7a8n' });
  assert.equal(calls.length, 1); assert.equal(calls[0][1].method, 'POST');
  assert.equal(calls[0][1].headers['X-Practice-Action'], 'local-practice');
  assert.deepEqual(JSON.parse(calls[0][1].body), { kind: 'move', revision: 1, request_key: 'same', uci: 'a7a8n' });
});
test('question reads are no-store, cancellable and do not request legacy answers', async () => {
  const calls = []; const api = helpers(async (...args) => { calls.push(args); return { ok: true, json: async () => ({}) }; });
  const signal = new AbortController().signal;
  await api.practiceRead('/puzzles/x', signal);
  assert.equal(calls[0][0], 'http://localhost:8000/api/practice/puzzles/x');
  assert.equal(calls[0][1].signal, signal); assert.equal(calls[0][1].cache, 'no-store');
});
test('guided alternative labels make no incorrect or best-move claim', () => {
  const api = helpers();
  assert.equal(api.feedbackLabel('LEGAL_ALTERNATIVE_UNASSESSED'), 'Legal alternative · unassessed');
  assert.equal(api.feedbackLabel('OBJECTIVE_NOT_MET'), 'Not mate in one');
  assert.equal(api.feedbackLabel('DEMONSTRATED_TARGET'), 'Guided idea demonstrated');
});
test('source hash uses exact UTF-8 PGN including comments', async () => {
  const api = helpers(); const pgn = '[White "Å"]\n\n1. e4 {comment} *';
  assert.equal(await api.pgnHash(pgn), createHash('sha256').update(pgn).digest('hex'));
  assert.notEqual(await api.pgnHash(pgn), await api.pgnHash(pgn.replace('{comment}', '')));
});
test('HTTP failures reject as technical errors instead of chess verdicts', async () => {
  const api = helpers(async () => ({ ok: false, status: 409, json: async () => ({ detail: 'REVISION_CONFLICT' }) }));
  await assert.rejects(api.practiceWrite('/attempts/x/events', {}), /REVISION_CONFLICT/);
});
