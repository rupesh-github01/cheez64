import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {Chess} from 'chess.js';
import {createHash} from 'node:crypto';
const base=new URL('../src/lib/',import.meta.url);
function module(file,imports={},globals={}){
 const exports={};const source=ts.transpileModule(fs.readFileSync(new URL(file,base),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 vm.runInNewContext(source,{exports,require:name=>{if(name==='chess.js')return {Chess};if(name in imports)return imports[name];throw new Error(name);},Map,Set,Promise,Error,setTimeout,clearTimeout,queueMicrotask,performance,fetch:()=>assert.fail('No backend/cloud request permitted'),...globals});return exports;
}
const model=module('browser-stockfish.ts');
const replaySource=ts.transpileModule(fs.readFileSync(new URL('../src/components/PlayerDashboard/pgn-context.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const pg={};vm.runInNewContext(replaySource,{exports:pg,require:()=>({Chess,DEFAULT_POSITION:new Chess().fen()}),TextEncoder});
const position=(ply=0)=>model.livePosition(pg.replayPGN('1. e4 e5 2. Nf3 Nc6 *'),ply,`p${ply}`);
const info=(rank=1,depth=4,pv='e2e4',score='cp 35')=>model.parseInfo(`info depth ${depth} multipv ${rank} score ${score} nodes 100 time 20 pv ${pv}`);
class FakeWorker{
 onmessage=null;onerror=null;onmessageerror=null;commands=[];terminated=0;
 postMessage(command){this.commands.push(command);}terminate(){this.terminated++;}
 emit(line){this.onmessage?.({data:line});}
}
function harness(extra={}){
 let time=0,id=0,releases=0;const timers=new Map(),workers=[],views=[];
 const set=(fn,delay)=>{const token=++id;timers.set(token,{fn,at:time+delay});return token;};
 const controller=module('browser-stockfish-controller.ts',{'./browser-stockfish':model,'./browser-engine-lease':{acquireBrowserEngine:()=>assert.fail('Injected admission only')}},{setTimeout:set,clearTimeout:token=>timers.delete(token)});
 const c=new controller.BrowserStockfishController(v=>views.push(v),{worker:()=>{const w=new FakeWorker();workers.push(w);return w;},lease:async()=>()=>releases++,now:()=>time,...extra});
 return {c,workers,views,get view(){return views.at(-1);},get releases(){return releases;},get timers(){return timers.size;},async start(p=position()){c.start(p);await Promise.resolve();await Promise.resolve();},advance(ms){time+=ms;for(const [key,t] of [...timers])if(t.at<=time){timers.delete(key);t.fn();}}};
}
function handshake(h,mp=3){const w=h.workers.at(-1);w.emit('id name Stockfish 19 Lite WASM');w.emit('option name Hash type spin default 16 min 1 max 1024');w.emit('option name Threads type spin default 1 min 1 max 1');w.emit(`option name MultiPV type spin default 1 min 1 max ${mp}`);w.emit('uciok');w.emit('readyok');w.emit('readyok');return w;}
test('full legal history is transmitted, including setup, castling, EP and promotion',()=>{
 assert.match(model.positionCommand(position(3)),/moves e2e4 e7e5 g1f3$/);
 for(const text of ['[SetUp "1"]\n[FEN "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"]\n\n1. O-O O-O-O *','[SetUp "1"]\n[FEN "k7/8/8/3pP3/8/8/8/4K3 w - d6 0 1"]\n\n1. exd6 *','[SetUp "1"]\n[FEN "7k/P7/8/8/8/8/8/4K3 w - - 0 1"]\n\n1. a8=Q+ *']){const r=pg.replayPGN(text);const p=model.livePosition(r,r.moves.length,'setup');assert.match(model.positionCommand(p),/position fen .* moves /);assert.equal(p.board.fen(),r.fens.at(-1));}
 assert.throws(()=>model.livePosition(pg.replayPGN('1. e4 *'),1001,'long'));
});
test('score and bound perspective invert for Black; mate stays typed',()=>{
 const p=position(1);let line=model.projectLiveLine(p,info(1,4,'e7e5','cp 125 lowerbound'));assert.equal(line.score.cp,-125);assert.equal(line.bound,'upper');assert.equal(line.sans[0],'e5');
 line=model.projectLiveLine(p,info(1,4,'e7e5','mate -3 upperbound'));assert.equal(line.score.mate,3);assert.equal(line.bound,'lower');assert.equal(p.board.fen(),p.fen);
});
test('reject malformed scores, illegal full tails and inconsistent mate records',()=>{
 for(const line of ['info depth -1 score cp 1 pv e2e4','info depth 15 score cp 1 pv e2e4','info depth 4 score mate 0 pv e2e4','info depth 4 score cp 1 lowerbound upperbound pv e2e4','info depth 4 multipv broken score cp 1 pv e2e4','info depth 4 score cp 1 score mate 1 pv e2e4'])assert.equal(model.parseInfo(line),undefined);
 const p=position();assert.throws(()=>model.projectLiveLine(p,info(1,4,'e2e4 e7e5 e4e8')));assert.equal(p.board.fen(),p.fen);
 const r=pg.replayPGN('1. f3 e5 2. g4 *');const mate=model.livePosition(r,3,'mate');assert.throws(()=>model.projectLiveLine(mate,info(1,4,'d8h4','mate -1')));assert.equal(model.projectLiveLine(mate,info(1,4,'d8h4','mate 1')).sans[0],'Qh4#');
});
test('cohorts never mix depths, repeat rank-one batches, bounds or duplicate roots',()=>{
 const c=new model.DepthCohort(3),p=position();const accept=(rank,depth,move,score)=>c.accept(model.projectLiveLine(p,info(rank,depth,move,score)));
 accept(1,4,'e2e4');accept(2,3,'d2d4');assert.equal(c.lines.length,1);accept(3,4,'g1f3');assert.equal(c.complete,false);accept(2,4,'d2d4');assert.equal(c.complete,true);
 accept(1,4,'c2c4');assert.equal(c.lines.length,1);accept(2,4,'c2c4');assert.equal(c.lines.length,1);accept(2,4,'d2d4');accept(3,4,'g1f3','cp 20 lowerbound');assert.equal(c.complete,false);
});
test('terminal mate/stalemate are deterministic and do not create workers',async()=>{
 for(const [fen,label] of [['7k/6Q1/5K2/8/8/8/8/8 b - - 0 1','Checkmate'],['7k/5Q2/6K1/8/8/8/8/8 b - - 0 1','Stalemate']]){
 const r={root_fen:fen,fens:[fen],moves:[],sans:[],root_history_complete:false};const p=model.livePosition(r,0,label),h=harness();await h.start(p);assert.equal(h.workers.length,0);assert.equal(h.view.status,'terminal');assert.equal(p.terminal,label);h.c.dispose();}
});
test('real controller contract initializes UCI options and bounded searches, never backend requests',async()=>{
 const h=harness();await h.start();const w=handshake(h);for(const cmd of ['uci','setoption name Hash value 16','setoption name Threads value 1','setoption name MultiPV value 3','ucinewgame','go depth 14 movetime 10000'])assert.ok(w.commands.includes(cmd),cmd);
 assert.equal(h.view.status,'searching');for(const [rank,move]of [[1,'e2e4'],[2,'d2d4'],[3,'g1f3']])w.emit(`info depth 5 multipv ${rank} score cp 35 nodes 100 time 10 pv ${move}`);assert.equal(h.view.complete,true);assert.equal(h.view.depth,5);w.emit('bestmove e2e4');assert.equal(h.view.status,'complete');h.c.stop();assert.equal(w.terminated,1);assert.equal(h.view.status,'stopped');assert.equal(h.view.lines.length,3);assert.equal(h.releases,1);assert.equal(h.timers,0);
 h.c.select(position(1));assert.equal(h.view.lines.length,0);assert.equal(h.workers.length,1);h.c.dispose();
});
test('navigation drains old output before restarting from the newest full history',async()=>{
 const h=harness();await h.start();const w=handshake(h);h.c.select(position(1));h.c.select(position(2));w.emit('info depth 8 multipv 1 score cp 999 pv e2e4');assert.equal(h.view.lines.length,0);w.emit('bestmove e2e4');w.emit('readyok');w.emit('readyok');assert.match(w.commands.at(-2),/moves e2e4 e7e5$/);assert.equal(h.view.key,'p2');w.emit('info depth 3 multipv 1 score cp 20 pv g1f3');assert.equal(h.view.lines[0].sans[0],'Nf3');assert.equal(h.workers.length,1);h.c.dispose();
});
test('bounded navigation replacement rejects callbacks from the terminated worker',async()=>{
 const h=harness();await h.start();const old=handshake(h),late=old.onmessage;h.c.select(position(1));h.advance(751);assert.equal(old.terminated,1);assert.equal(h.workers.length,2);late({data:'info depth 14 multipv 1 score cp 999 pv e2e4'});assert.equal(h.view.lines.length,0);handshake(h);assert.equal(h.view.key,'p1');h.c.dispose();assert.equal(h.timers,0);
});
test('crashes never auto-restart; explicit restart works and unmount releases ownership',async()=>{
 const h=harness();await h.start();const w=handshake(h);w.onerror({});assert.equal(w.terminated,1);assert.equal(h.view.status,'error');assert.equal(h.view.enabled,false);h.c.select(position(1));h.advance(20000);assert.equal(h.workers.length,1);await h.start(position(1));assert.equal(h.workers.length,2);h.c.dispose();assert.equal(h.workers[1].terminated,1);assert.equal(h.releases,2);
});
test('startup and search hard deadlines terminate owned workers',async()=>{
 const h=harness();await h.start();h.advance(15001);assert.equal(h.workers[0].terminated,1);assert.equal(h.view.status,'error');h.c.dispose();
 const s=harness();await s.start();const w=handshake(s);s.advance(10000);assert.equal(w.commands.at(-1),'stop');s.advance(751);assert.equal(w.terminated,1);assert.equal(s.view.status,'error');s.c.dispose();
});
test('engine capability limits are honored and unsafe Hash is refused',async()=>{
 const h=harness();await h.start();handshake(h,2);assert.equal(h.view.expected,2);assert.ok(h.workers[0].commands.includes('setoption name MultiPV value 2'));h.c.dispose();
 const bad=harness();await bad.start();const w=bad.workers[0];w.emit('id name Stockfish');w.emit('option name Hash type spin default 64 min 64 max 1024');w.emit('uciok');assert.equal(w.terminated,1);assert.equal(bad.view.status,'error');bad.c.dispose();
});
test('Web Locks coordinate tabs and fence native UI queuing; fallback requires explicit acknowledgment',async()=>{
 let held=false;const navigator={locks:{request:async(_name,_opts,callback)=>{if(held)return callback(null);held=true;try{return await callback({});}finally{held=false;}},query:async()=>({held:held?[{name:'pcis-browser-stockfish-v1'}]:[]})}};
 const a=module('browser-engine-lease.ts',{}, {navigator}),b=module('browser-engine-lease.ts',{}, {navigator});const release=await a.acquireBrowserEngine();await assert.rejects(b.acquireBrowserEngine(),/another tab/);await assert.rejects(b.assertNoBrowserEngine(),/Stop browser/);release();await Promise.resolve();const rb=await b.acquireBrowserEngine();rb();await Promise.resolve();
 const f=module('browser-engine-lease.ts');await assert.rejects(f.acquireBrowserEngine(),/unavailable/);const rf=await f.acquireBrowserEngine(true);await assert.rejects(f.acquireBrowserEngine(true),/this tab/);await assert.rejects(f.assertNoBrowserEngine(),/Stop browser/);rf();
});
test('distributed engine assets match the pinned hashes and package integrity',()=>{
 for(const [name,hash]of [['stockfish-19-lite-single.js',model.ENGINE_IDENTITY.js_sha256],['stockfish-19-lite-single.wasm',model.ENGINE_IDENTITY.wasm_sha256]])assert.equal(createHash('sha256').update(fs.readFileSync(new URL(`../public/engines/stockfish-19.0.1/${name}`,import.meta.url))).digest('hex'),hash);
 const pkg=JSON.parse(fs.readFileSync(new URL('../package-lock.json',import.meta.url),'utf8')).packages['node_modules/stockfish'];assert.equal(pkg.version,'19.0.1');assert.ok(pkg.integrity.startsWith('sha512-'));
});
test('a forced reply never reduces MultiPV capability for later positions',async()=>{
 const h=harness();const forced={...position(),legalCount:1};await h.start(forced);const w=handshake(h);assert.equal(h.view.expected,1);w.emit('bestmove e2e4');h.c.select(position(1));h.advance(121);w.emit('readyok');assert.equal(h.view.expected,3);assert.match(w.commands.at(-2),/moves e2e4$/);h.c.dispose();
});
test('Stop or dispose during asynchronous admission releases the late lease without a worker',async()=>{
 for(const action of ['stop','dispose']){
  let resolve,released=0,created=0;const states=[];const {BrowserStockfishController}=module('browser-stockfish-controller.ts',{'./browser-stockfish':model,'./browser-engine-lease':{}});const c=new BrowserStockfishController(s=>states.push(s),{lease:()=>new Promise(r=>resolve=r),worker:()=>{created++;return new FakeWorker();}});c.start(position());c[action]();resolve(()=>released++);await new Promise(setImmediate);assert.equal(created,0);assert.equal(released,1);c.dispose();
 }
});
test('separate evidence mode sends complete options/reset receipts and bounded depth16; live remains depth14',async()=>{
 const events=[];const h=harness({reasoningEvidence:true,observe:(kind,text)=>events.push({kind,text})});await h.start();const w=h.workers[0];w.emit('id name Stockfish 19 Lite WASM');
 for(const line of ['Threads type spin default 1 min 1 max 1','Hash type spin default 16 min 1 max 33554432','Clear Hash type button','Ponder type check default false','MultiPV type spin default 1 min 1 max 256','Skill Level type spin default 20 min 0 max 20','Move Overhead type spin default 10 min 0 max 5000','nodestime type spin default 0 min 0 max 10000','UCI_Chess960 type check default false','UCI_LimitStrength type check default false','UCI_Elo type spin default 1320 min 1320 max 3190','UCI_ShowWDL type check default false','EvalFile type string default nn-61e7af4bb97d.nnue'])w.emit('option name '+line);
 w.emit('uciok');w.emit('readyok');w.emit('readyok');assert.ok(w.commands.includes('setoption name Clear Hash'));assert.equal(w.commands.at(-1),'go depth 16 movetime 20000');assert.ok(w.commands.includes('setoption name UCI_Elo value 1320'));assert.ok(w.commands.includes('setoption name EvalFile value nn-61e7af4bb97d.nnue'));
 for(const [rank,move]of [[1,'e2e4'],[2,'d2d4'],[3,'g1f3']])w.emit(`info depth 16 multipv ${rank} score cp 35 pv ${move}`);w.emit('bestmove e2e4');assert.equal(h.view.depth,16);assert.equal(h.view.complete,true);h.c.dispose();assert.equal(events.at(-1).kind,'terminated');assert.equal(events[0].kind,'worker_started');assert.equal(events.filter(x=>x.kind==='send'&&x.text.startsWith('go ')).length,1);
 assert.equal(model.parseInfo('info depth 16 multipv 1 score cp 3 pv e2e4'),undefined);assert.ok(model.parseInfo('info depth 16 multipv 1 score cp 3 pv e2e4',16));
});
test('evidence observation failure terminates the worker without automatic restart',async()=>{
 const h=harness({reasoningEvidence:true,observe:()=>{throw Error('Transcript full');}});await h.start();await new Promise(setImmediate);assert.equal(h.view.status,'error');assert.equal(h.workers[0].terminated,1);assert.equal(h.releases,1);h.c.dispose();
});
test('evidence deadline terminates immediately at20s while live keeps its original stop grace',async()=>{
 const h=harness({reasoningEvidence:true});await h.start();h.workers[0].emit('option name Clear Hash type button');const w=handshake(h);h.advance(19999);assert.equal(w.terminated,0);h.advance(1);assert.equal(w.terminated,1);assert.equal(h.view.status,'error');assert.equal(h.releases,1);assert.equal(h.timers,0);h.c.dispose();
});
