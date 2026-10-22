import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { webcrypto, createHash } from 'node:crypto';
import ts from 'typescript';
function api(fetch) {
 const exports={};const code=ts.transpileModule(fs.readFileSync(new URL('../src/lib/position-reasoning.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 vm.runInNewContext(code,{exports,fetch,process:{env:{}},TextEncoder,Uint8Array,URLSearchParams,DOMException,crypto:webcrypto});return exports;
}
test('reasoning is explicit GET-only and binds exact root/hash/alternative/bundle',async()=>{
 const calls=[];const helper=api(async(...args)=>{calls.push(args);return {ok:true,json:async()=>({})};});assert.equal(calls.length,0);
 const pgn='1. e4 e5 *';await helper.loadReasoning('a/b',pgn,2,new AbortController().signal,{alternative:'g1f3',bundle:'a'.repeat(64)});
 const url=new URL(calls[0][0]);assert.equal(url.pathname,'/api/games/a%2Fb/reasoning');assert.equal(url.searchParams.get('ply'),'2');
 assert.equal(url.searchParams.get('expected_pgn_sha256'),createHash('sha256').update(pgn).digest('hex'));assert.equal(url.searchParams.get('alternative_uci'),'g1f3');
 assert.equal(calls[0][1].method,undefined);assert.equal(calls[0][1].cache,'no-store');
});
test('stale abort during hashing cannot send a request and request keys separate all contexts',async()=>{
 const calls=[];const helper=api(async(...a)=>calls.push(a));const c=new AbortController();const promise=helper.loadReasoning('g','1. e4 *',0,c.signal);c.abort();await assert.rejects(promise,{name:'AbortError'});assert.equal(calls.length,0);
 const key=helper.reasoningKey('g','pgn',0,'g1f3','b','p',false);
 for(const row of [['g','pgn',1,'g1f3','b','p',false],['g','pgn',0,'b1c3','b','p',false],['g','new',0,'g1f3','b','p',false],['g','pgn',0,'g1f3','other','p',false],['g','pgn',0,'g1f3','b','p',true]]) assert.notEqual(helper.reasoningKey(...row),key);
});
test('typed scores preserve zero, missing scores, signed mate and terminal outcome',()=>{
 const helper=api();assert.equal(helper.reasoningScore(null),'Unavailable · not zero');assert.equal(helper.reasoningScore({kind:'cp',cp:0}),'+0.00 pawns (White)');assert.equal(helper.reasoningScore({kind:'cp',cp:null}),'Score unavailable');
 assert.equal(helper.reasoningScore({kind:'mate',mate:-3}),'Reported mate -3 (White-relative)');assert.equal(helper.reasoningScore({kind:'terminal_checkmate',winner:'black'}),'Terminal checkmate · black wins');
});
test('malformed alternatives and oversize source fail before fetch',async()=>{
 const helper=api(()=>assert.fail('Unexpected fetch'));
 await assert.rejects(helper.loadReasoning('g','1. e4 *',0,new AbortController().signal,{alternative:'e9e4'}));
 await assert.rejects(helper.loadReasoning('g',' '.repeat(2*1024*1024+1),0,new AbortController().signal));
});
test('returning to the same position cannot revive an aborted loading/result view',()=>{
 const helper=api();const key=helper.reasoningKey('g','pgn',2,'g1f3','b','p',false);
 assert.equal(helper.currentReasoningView(key,key,false),true);
 assert.equal(helper.currentReasoningView(key,'different position',false),false);
 assert.equal(helper.currentReasoningView(key,key,true),false);
});
