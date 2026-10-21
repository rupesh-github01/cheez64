import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { webcrypto, createHash } from 'node:crypto';
import ts from 'typescript';
function helpers(read) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(new URL('../src/lib/position-understanding.ts',import.meta.url),'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(code, { exports, require: () => ({ readDashboard: read }), TextEncoder, Uint8Array, URLSearchParams, DOMException, crypto: webcrypto });
  return exports;
}
test('understanding is explicit and uses exact PGN hash, ply zero and read-only GET path', async () => {
  const calls=[];const api=helpers(async (...args)=>{calls.push(args);return {ok:true};});
  assert.equal(calls.length,0);
  const pgn='1. e4 *'; await api.loadUnderstanding('game_a',pgn,0,new AbortController().signal);
  assert.equal(calls.length,1);
  const url=new URL('http://fixture'+calls[0][0]);
  assert.equal(url.pathname,'/api/games/game_a/understanding');
  assert.equal(url.searchParams.get('expected_pgn_sha256'),createHash('sha256').update(pgn).digest('hex'));
  assert.equal(url.searchParams.get('evidence'),'none');assert.equal(url.searchParams.get('ply'),'0');
  await api.loadUnderstanding('game_a',pgn,1,new AbortController().signal,true,'a'.repeat(64));
  assert.ok(calls[1][0].includes('evidence=accepted_local'));
});
test('navigation abort during hashing suppresses obsolete fetch and selection keys are isolated', async () => {
  const calls=[];const api=helpers(async path=>calls.push(path));const c=new AbortController();
  const request=api.loadUnderstanding('a','1. e4 *',0,c.signal);c.abort();
  await assert.rejects(request,{name:'AbortError'});assert.equal(calls.length,0);
  const key=api.understandingKey('a','1. e4 *',0,false);
  for(const args of [['a','1. e4 *',1,false],['b','1. e4 *',0,false],['a','1. d4 *',0,false],['a','1. e4 *',0,true]]) assert.notEqual(api.understandingKey(...args),key);
});
test('typed White scores preserve zero, null and signed mate', () => {
  const api=helpers();
  assert.equal(api.typedScore({kind:'cp',cp:0,mate:null,winner:null}),'+0.00 pawns (White)');
  assert.equal(api.typedScore({kind:'cp',cp:null,mate:null,winner:null}),'Score unavailable');
  assert.equal(api.typedScore({kind:'mate',cp:null,mate:-3,winner:null}),'Mate -3 (White perspective)');
  assert.equal(api.typedScore({kind:'terminal_checkmate',cp:null,mate:0,winner:'black'}),'Checkmate · black wins');
});
test('oversized PGN and invalid ply fail before any fetch', async () => {
  let calls=0;const api=helpers(async()=>{calls++;});
  await assert.rejects(api.loadUnderstanding('a',' '.repeat(2097153),0,new AbortController().signal),/2 MiB/);
  for(const ply of [-1,10001,1.2]) await assert.rejects(api.loadUnderstanding('a','*',ply,new AbortController().signal),/Invalid selected ply/);
  assert.equal(calls,0);
});
