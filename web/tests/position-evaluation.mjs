import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { Chess } from 'chess.js';
function load(file,fetch) {
 const exports={}; const code=ts.transpileModule(fs.readFileSync(new URL(file,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 vm.runInNewContext(code,{exports,require:n=>n==='chess.js'?{Chess}:assert.fail(n),fetch,process:{env:{}},DOMException,TextEncoder});return exports;
}
const pgn=load('../src/components/PlayerDashboard/pgn-context.ts');
const helpers=()=>load('../src/lib/position-evaluation.ts');
function result(count=3){return {status:'CACHE_HIT',context_compatibility:'TRUSTED_CONTEXT_MATCH',partial:false,available:[],evaluation:{perspective:'white',achieved_depth:20,returned_multipv:count,source:{kind:'local_stockfish'},config:{multipv:count},pvs:['e2e4','d2d4','g1f3'].slice(0,count).map((m,i)=>({kind:'cp',cp:35-i*10,moves:[m]}))}};}
for(const count of [1,2,3])test(`${count} recorded PVs remain ${count} legal SAN lines from one record`,()=>{
 const lines=helpers().legalLines(pgn.replayPGN('1. e4 e5 *'),0,result(count));assert.equal(lines.length,count);assert.deepEqual(Array.from(lines,l=>l.sans[0]),['e4','d4','Nf3'].slice(0,count));
});
test('White-relative zero, negative cp and both mate signs do not flip for Black to move',()=>{
 const h=helpers(); assert.equal(h.scoreText({kind:'cp',cp:0}),'+0.00');assert.equal(h.scoreText({kind:'cp',cp:-125}),'-1.25');assert.equal(h.whiteFill({kind:'cp',cp:0}),50);
 assert.equal(h.scoreText({kind:'mate',mate:-3}),'−M3');assert.equal(h.whiteFill({kind:'mate',mate:2}),100);assert.equal(h.whiteFill({kind:'mate',mate:-2}),0);assert.equal(h.whiteFill(),undefined);
 const r=result(1);r.evaluation.pvs=[{kind:'mate',mate:-3,moves:['e7e5']}]; const line=h.legalLines(pgn.replayPGN('1. e4 *'),1,r)[0];assert.equal(h.scoreText(line.score),'−M3');assert.equal(line.sans[0],'e5');
});
test('miss and incompatible responses cannot manufacture lines or scores',()=>{
 const h=helpers();const replay=pgn.replayPGN('1. e4 *');for(const status of ['CACHE_MISS','UNAVAILABLE','CLOUD_DISABLED','INCOMPATIBLE'])assert.equal(h.legalLines(replay,0,{status,evaluation:null}).length,0);
 const r=result(1);r.context_compatibility='INCOMPATIBLE';assert.throws(()=>h.legalLines(replay,0,r));
});
test('whole PV tail, duplicate roots, malformed score and terminal continuation are refused',()=>{
 const h=helpers(), replay=pgn.replayPGN('1. e4 *');
 for(const mutate of [r=>r.evaluation.pvs[0].moves.push('e7e5','e4e8'),r=>r.evaluation.pvs[1].moves=['e2e4'],r=>r.evaluation.pvs[0].cp=null,r=>r.evaluation.pvs[0]={kind:'mate',mate:0,moves:['e2e4']}]){const r=result();mutate(r);assert.throws(()=>h.legalLines(replay,0,r));}
 const r=result(1);r.evaluation.pvs[0].moves=['f2f3','e7e5','g2g4','d8h4','a2a3'];assert.throws(()=>h.legalLines(replay,0,r));
});
test('terminal score requires actual checkmate and correct winner',()=>{
 const h=helpers(), replay=pgn.replayPGN('1. f3 e5 2. g4 Qh4# 0-1');const r=result(1);r.evaluation.pvs=[{kind:'terminal_checkmate',mate:0,winner:'black',moves:[]}];assert.equal(h.legalLines(replay,4,r)[0].sans.length,0);r.evaluation.pvs[0].winner='white';assert.throws(()=>h.legalLines(replay,4,r));
});
test('default fetch is one bounded cache-only context; cloud needs explicit key and never carries unrelated config',async()=>{
 const calls=[];const h=load('../src/lib/position-evaluation.ts',async(...a)=>{calls.push(a);return {ok:true,json:async()=>a[0].endsWith('lookup-batch')?{items:[{status:'CACHE_MISS',available:[]}]}:{status:'CLOUD_DISABLED',available:[]}};});assert.equal(calls.length,0);
 const context=pgn.lookupContext(pgn.replayPGN('1. e4 e5 *'),1);await h.fetchEvaluation(context,new AbortController().signal,'a'.repeat(64));const body=JSON.parse(calls[0][1].body);assert.equal(body.items.length,1);assert.equal(body.items[0].allow_cloud,false);assert.equal(body.items[0].min_pvs,1);assert.equal(body.items[0].allow_partial,true);assert.deepEqual(body.items[0].context.moves,['e2e4']);assert.equal(calls[0][1].headers['X-Evaluation-Key'],undefined);
 await h.fetchEvaluation(context,new AbortController().signal,'a'.repeat(64),'temporary-operator');const cloud=JSON.parse(calls[1][1].body);assert.equal(cloud.allow_cloud,true);assert.equal(cloud.cloud_multipv,3);assert.equal(cloud.source,'lichess_cloud');assert.equal(cloud.config_hash,undefined);assert.equal(calls[1][1].headers['X-Evaluation-Key'],'temporary-operator');
});
test('cancelled requests never start and auth refusal is explicit',async()=>{
 let calls=0;const h=load('../src/lib/position-evaluation.ts',async()=>{calls++;return {ok:false,status:403};});const c=new AbortController();c.abort();await assert.rejects(h.fetchEvaluation({},c.signal),{name:'AbortError'});assert.equal(calls,0);await assert.rejects(h.fetchEvaluation({},new AbortController().signal,'','key'),/refused/);
});
// Exercise the actual React session's effect cleanup with deferred fetches.
function sessionHarness() {
 const states=[], effects=[], pending=[];let cursor=0;const nextEffects=[];
 const react={useState(initial){const i=cursor++;if(!(i in states))states[i]=initial;return [states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;}];},useMemo(fn){const i=cursor++;if(!states[i])states[i]=fn();return states[i];},useEffect(fn,deps){const i=cursor++;const prior=effects[i];if(!prior||deps.some((d,j)=>d!==prior.deps[j]))nextEffects.push(()=>{prior?.cleanup?.();effects[i]={deps,cleanup:fn()};});}};
 const exports={};const jsx=(type,props)=>({type,props});const helper=helpers();
 const fetches={...helper,evaluationStatus:async()=>({cloud_enabled:false}),fetchEvaluation:(_ctx,signal)=>new Promise(resolve=>pending.push({signal,resolve}))};
 const code=ts.transpileModule(fs.readFileSync(new URL('../src/components/GameReview/PositionEvaluation.tsx',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText+'\nexports.Session=EvaluationSession;';
 vm.runInNewContext(code,{exports,require:n=>n==='react'?react:n==='react/jsx-runtime'?{jsx,jsxs:jsx}:n==='@/lib/position-evaluation'?fetches:n.endsWith('pgn-context')?pgn:{},AbortController,setTimeout,clearTimeout});
 return {pending,render(replay,ply=0){cursor=0;const tree=exports.Session({replay,ply});for(const f of nextEffects.splice(0))f();return JSON.stringify(tree);},unmount(){for(const e of effects)e?.cleanup?.();}};
}
test('navigation aborts old sessions and late cache responses cannot replace a new position',async()=>{
 const old=sessionHarness(), replay=pgn.replayPGN('1. e4 *');old.render(replay);assert.equal(old.pending.length,1);old.unmount();assert.equal(old.pending[0].signal.aborted,true);
 const current=sessionHarness();current.render(replay,1);current.pending[0].resolve({status:'NEW_POSITION_MISS',evaluation:null,available:[]});await Promise.resolve();await Promise.resolve();assert.match(current.render(replay,1),/NEW_POSITION_MISS/);
 old.pending[0].resolve({status:'OBSOLETE',evaluation:null,available:[]});await Promise.resolve();assert.doesNotMatch(current.render(replay,1),/OBSOLETE/);current.unmount();
});
test('returning to the original position starts a fresh request rather than reviving aborted loading',()=>{
 const replay=pgn.replayPGN('1. e4 *'), old=sessionHarness();old.render(replay);old.unmount();const returned=sessionHarness();assert.match(returned.render(replay),/Looking up this position/);assert.equal(returned.pending.length,1);assert.equal(returned.pending[0].signal.aborted,false);returned.unmount();
});

test('cloud evidence retains advisory status and cannot masquerade as context verified',()=>{
 const h=helpers(),r=result(1),replay=pgn.replayPGN('1. e4 *');r.evaluation.source.kind='lichess_cloud';assert.throws(()=>h.legalLines(replay,0,r));r.context_compatibility='ADVISORY_CONTEXT_UNVERIFIED';assert.equal(h.legalLines(replay,0,r).length,1);
});
