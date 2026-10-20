// Existing TypeScript compiler + Node test runner; no test dependency installation.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
function load(file, overrides = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src', file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, { exports, require, process, console, AbortController, DOMException, TextEncoder, setTimeout, clearTimeout, ...overrides });
  return exports;
}
const chess = load('components/PlayerDashboard/pgn-context.ts');
test('legal full history, setup roots, initial ply and malformed PGN', () => {
  const replay = chess.replayPGN('1. e4 e5 2. Nf3 Nc6 *');
  assert.equal(replay.root_history_complete, true);
  assert.equal(replay.moves.join(' '), 'e2e4 e7e5 g1f3 b8c6');
  assert.equal(chess.lookupContext(replay, 0).moves.length, 0);
  assert.equal(chess.lookupContext(replay, 2).fen, replay.fens[2]);
  assert.equal(chess.lookupContext(replay, 2).moves.join(' '), 'e2e4 e7e5');
  for (const bad of [-1, NaN, 1.5, 5]) assert.equal(chess.selectedPly(bad, 4), 0);
  assert.equal(chess.selectedPly(0, 4), 0);
  const setup = chess.replayPGN('[SetUp "1"]\n[FEN "4k3/8/8/8/8/8/8/4K3 w - - 0 1"]\n\n1. Kd2 *');
  assert.equal(setup.root_history_complete, false);
  assert.throws(() => chess.replayPGN('1. e5 *'));
  assert.throws(() => chess.replayPGN('[Variant \"Antichess\"]\n\n1. e4 *'), /standard chess/);
  assert.throws(() => chess.replayPGN('x'.repeat(2 * 1024 * 1024 + 1)), /2 MiB/);
  assert.throws(() => chess.lookupContext(replay, 1001), /1,000/);
});
test('null metrics remain unavailable and denominators are not invented', () => {
  const api = load('lib/player-dashboard.ts');
  assert.equal(api.metricText({ value: null }), 'Not enough data');
  assert.equal(api.metricText({ value: 0, unit: 'fraction' }), '0.0%');
  assert.equal(api.metricText({ value: .5, unit: 'fraction' }), '50.0%');
});
test('exact in-flight reuse, bounded response reuse and explicit local batch', async () => {
  let calls = [];
  const api = load('lib/player-dashboard.ts', { fetch: async (url, options) => { calls.push([url, options]); return { ok: true, json: async () => ({ count: calls.length }) }; } });
  const [a, b] = await Promise.all([api.readDashboard('/x?source=lichess&username=a'), api.readDashboard('/x?source=lichess&username=a')]);
  assert.equal(a.count, b.count); assert.equal(calls.length, 1);
  await api.readDashboard('/x?source=chess.com&username=a'); assert.equal(calls.length, 2);
  for (let n = 0; n < 9; n++) await api.readDashboard('/item/' + n);
  await api.readDashboard('/x?source=lichess&username=a'); assert.equal(calls.length, 12);
  api.clearDashboardCache(); await api.readDashboard('/item/8'); assert.equal(calls.length, 13);
  await api.readDashboard('/api/evaluations/lookup-batch', undefined, false, JSON.stringify({ items: [{ allow_cloud: false }] }));
  assert.equal(calls.at(-1)[1].method, 'POST');
  assert.equal(JSON.parse(calls.at(-1)[1].body).items[0].allow_cloud, false);
  assert.ok(calls.slice(0, -1).every(c => !c[1].method));
});
test('replay-backed requests serialize across selectors and aborted queued work never starts', async () => {
  let release; const started = [];
  const api = load('lib/player-dashboard.ts', { fetch: async url => { started.push(url); if (url.endsWith('/first')) await new Promise(r => { release = r; }); return { ok: true, json: async () => ({ ok: true }) }; } });
  const first = api.readDashboard('/first', undefined, true);
  const controller = new AbortController();
  const obsolete = api.readDashboard('/obsolete', controller.signal, true); controller.abort();
  const next = api.readDashboard('/next', undefined, true);
  await new Promise(r => setImmediate(r)); assert.equal(started.length, 1);
  release(); await first; await assert.rejects(obsolete, { name: 'AbortError' }); await next;
  assert.equal(started.length, 2); assert.ok(started[1].endsWith('/next'));
});

test('game/artifact drill-downs revalidate instead of retaining responses', async () => {
  let count = 0;
  const api = load('lib/player-dashboard.ts', { fetch: async () => ({ ok: true, json: async () => ({ count: ++count }) }) });
  for (const route of ['/api/games/a', '/api/progressive-analysis/games/a/progress', '/api/evaluations/lookup-batch']) {
    const first = await api.readDashboard(route); const second = await api.readDashboard(route);
    assert.notEqual(first.count, second.count);
  }
});
