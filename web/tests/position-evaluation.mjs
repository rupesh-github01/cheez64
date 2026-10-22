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
 return {pending,render(replay,ply=0,legacy){cursor=0;const tree=exports.Session({replay,ply,legacy});for(const f of nextEffects.splice(0))f();return JSON.stringify(tree);},unmount(){for(const e of effects)e?.cleanup?.();}};
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
test('legacy estimates are display-only cp with exact FEN/schema and mover perspective',()=>{
 const h=helpers(),fen=pgn.replayPGN('1. e4 *').fens[1];
 assert.equal(h.legacyEstimate({value:125,color:'Black',fen,schema:'1.1.0'},fen).cp,-125);
 assert.equal(h.legacyEstimate({value:0,color:'White',fen,schema:'1.1.0'},fen).cp,0);
 for(const patch of [{value:null},{value:100000},{value:-99990},{value:90000},{value:1.5},{color:'unknown'},{schema:'unknown'},{fen:'broken'}])assert.equal(h.legacyEstimate({value:1,color:'White',fen,schema:'1.1.0',...patch},fen),undefined);
 assert.equal(h.legacyEstimate({value:125,color:'White',fen,schema:'1.1.0'},pgn.replayPGN('1. d4 *').fens[1]),undefined);
});
test('coverage is an explicit bounded cache-only window with truthful one/two/three counts',async()=>{
 const calls=[];const replay=pgn.replayPGN('1. e4 e5 *');const h=load('../src/lib/position-evaluation.ts',async(url,options)=>{calls.push([url,options]);return {ok:true,json:async()=>({items:JSON.parse(options.body).items.map(q=>{
 const r=result(q.context.moves.length+1);r.evaluation.pvs=(q.context.moves.length===1?['e7e5','c7c5']:q.context.moves.length===2?['g1f3','f1c4','b1c3']:['e2e4']).map(m=>({kind:'cp',cp:10,moves:[m]}));return r;})})};});assert.equal(calls.length,0);
 const rows=await h.inspectCoverage(replay,[0,1,2],new AbortController().signal);const counts=h.coverageCounts(rows);assert.equal(counts.compatible,3);assert.equal(counts.one,1);assert.equal(counts.two,1);assert.equal(counts.three_plus,1);assert.equal(calls.length,1);assert.ok(calls[0][0].endsWith('/lookup-batch'));for(const item of JSON.parse(calls[0][1].body).items)assert.equal(item.allow_cloud,false);
});
test('diagnostics distinguish missing, incompatible, storage unavailable and history cap',async()=>{
 const h=helpers();const rows=['CACHE_MISS','INCOMPATIBLE','CONFIG_REQUIRED','UNAVAILABLE','HISTORY_LIMIT'].map((status,ply)=>({ply,lines:0,item:{status,evaluation:null}}));const c=h.coverageCounts(rows);assert.equal(c.missing,1);assert.equal(c.incompatible,2);assert.equal(c.unavailable,2);assert.equal(c.compatible,0);assert.match(h.unavailableReason(rows[2].item),/configurations/);assert.match(h.unavailableReason(rows[4].item),/1,000/);
});
test('coverage refuses bulk cloud/duplicate/invalid selections and opts into PV preference only explicitly',async()=>{
 const calls=[];const h=load('../src/lib/position-evaluation.ts',async(...args)=>{calls.push(args);const body=JSON.parse(args[1].body);return {ok:true,json:async()=>({items:body.items.map(()=>({status:'CLOUD_MISS',evaluation:null,available:[]}))})};});const replay=pgn.replayPGN('1. e4 e5 2. Nf3 Nc6 *');
 for(const plies of [[0,1,2,3],[0,0],[-1],[20]])await assert.rejects(h.inspectCoverage(replay,plies,new AbortController().signal,'ephemeral'));
 assert.equal(calls.length,0);await h.inspectCoverage(replay,[0,1,2],new AbortController().signal,'ephemeral');assert.ok(calls[0][0].endsWith('/resolve-batch'));assert.equal(calls[0][1].headers['X-Evaluation-Key'],'ephemeral');for(const q of JSON.parse(calls[0][1].body).items){assert.equal(q.prefer_cloud_pvs,true);assert.equal(q.cloud_multipv,3);assert.equal(q.source,'lichess_cloud');}
});
test('long-history inspection splits requests below body bounds without truncating prefixes',async()=>{
 const calls=[];const h=load('../src/lib/position-evaluation.ts',async(url,options)=>{calls.push(options.body);return {ok:true,json:async()=>({items:JSON.parse(options.body).items.map(()=>({status:'CACHE_MISS',evaluation:null,available:[]}))})};});
 const replay={root_fen:new Chess().fen(),root_history_complete:true,moves:Array.from({length:1001},(_,i)=>['g1f3','g8f6','f3g1','f6g8'][i%4]),fens:Array(1002).fill(new Chess().fen()),sans:[]};
 const rows=await h.inspectCoverage(replay,Array.from({length:20},(_,i)=>981+i),new AbortController().signal);assert.equal(rows.length,20);assert.equal(calls.length,2);for(const body of calls)assert.ok(new TextEncoder().encode(body).length<=96*1024);assert.equal(JSON.parse(calls.at(-1)).items.at(-1).context.moves.length,1000);
 const before=calls.length;const over=await h.inspectCoverage(replay,[1001],new AbortController().signal);assert.equal(calls.length,before);assert.equal(over[0].item.status,'HISTORY_LIMIT');
});
test('invalid cached PV cannot count as compatible coverage and abort cannot launch requests',async()=>{
 const h=load('../src/lib/position-evaluation.ts',async()=>({ok:true,json:async()=>{const r=result(1);r.evaluation.pvs[0].moves=['e2e5'];return {items:[r]};}}));const replay=pgn.replayPGN('1. e4 *');const rows=await h.inspectCoverage(replay,[0],new AbortController().signal);assert.equal(h.coverageCounts(rows).compatible,0);assert.equal(rows[0].item.status,'INVALID_EVIDENCE');const c=new AbortController();c.abort();await assert.rejects(h.inspectCoverage(replay,[0],c.signal),{name:'AbortError'});
});

test('actual session marks a legacy fallback and gives cached evidence precedence',async()=>{
 const replay=pgn.replayPGN('1. e4 *');const legacy={value:125,color:'White',fen:replay.fens[0],schema:'1.1.0'};
 for(const hit of [false,true]){const session=sessionHarness();session.render(replay,0,legacy);session.pending[0].resolve(hit?result(1):{status:'CACHE_MISS',context_compatibility:'UNAVAILABLE',evaluation:null,available:[]});await Promise.resolve();await Promise.resolve();const tree=session.render(replay,0,legacy);if(hit){assert.match(tree, /"score":\{"kind":"cp","cp":35/);assert.doesNotMatch(tree,/"tier":"LEGACY_ESTIMATE"/);}else{assert.match(tree,/"tier":"LEGACY_ESTIMATE"/);assert.match(tree,/"cp":125/);}session.unmount();}
});
test('setup diagnostics distinguish missing configuration from misses and database failures',()=>{
 const h=helpers();
 const checks={CACHE_NOT_CONFIGURED:/not configured.*CHESS_COACH_EVAL_CACHE_DB/i,CACHE_FILE_MISSING:/does not exist/i,CACHE_LIMITS_INVALID:/LIMITS.*invalid/i,CACHE_PATH_REFUSED:/path was refused/i,CACHE_SCHEMA_INVALID:/schema/i,CACHE_VERSION_INCOMPATIBLE:/version is incompatible/i,CACHE_BUSY:/temporarily locked/i,CACHE_DATABASE_ERROR:/database error/i,CACHE_IO_ERROR:/permissions/i,CACHE_DATA_INVALID:/record could not be decoded/i};
 for(const [reason,pattern] of Object.entries(checks))assert.match(h.unavailableReason({status:'UNAVAILABLE',reason,evaluation:null}),pattern);
 assert.doesNotMatch(h.unavailableReason({status:'CACHE_MISS',evaluation:null}),/initialize|not configured/);
});
test('cloud status distinguishes disabled, read-only and missing authorization without enabling access',()=>{
 const h=helpers();assert.match(h.cloudStatusExplanation(),/could not be established/);
 assert.match(h.cloudStatusExplanation({cloud_enabled:false}),/disabled/);
 assert.match(h.cloudStatusExplanation({cloud_enabled:true,read_only:true,cloud_operator_key_configured:true}),/read-only/);
 assert.match(h.cloudStatusExplanation({cloud_enabled:true,read_only:false,cloud_operator_key_configured:false}),/operator key is missing/);
 assert.match(h.cloudStatusExplanation({cloud_enabled:true,cloud_resolution_allowed:true,cloud_operator_key_configured:true}),/explicit request/);
});
test('actual evaluation bar renders a numeric cached score and a distinct unavailable state',async()=>{
 const {jsx,jsxs}=await import('react/jsx-runtime');const {renderToStaticMarkup}=await import('react-dom/server');
 const exports={};const code=ts.transpileModule(fs.readFileSync(new URL('../src/components/GameReview/EvaluationBar.tsx',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 vm.runInNewContext(code,{exports,require:n=>n==='react/jsx-runtime'?{jsx,jsxs}:helpers()});
 const html=renderToStaticMarkup(exports.default({score:{kind:'cp',cp:35}}));assert.match(html,/White-relative evaluation: \+0.35/);assert.match(html,/position-eval-fill/);
 const missing=renderToStaticMarkup(exports.default({}));assert.match(missing,/White-relative evaluation: Unavailable/);assert.match(missing,/unknown/);assert.doesNotMatch(missing,/\+0\.00/);
});
