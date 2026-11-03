import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { Chess, DEFAULT_POSITION } from 'chess.js';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
function load(file, imports) { const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../src/'+file,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText,{exports,require:name=>{if(name in imports)return imports[name];throw Error(name);}});return exports; }
const model=load('lib/study-line.ts',{'chess.js':{Chess,DEFAULT_POSITION}});
const ucis=['e2e4','e7e5','f2f4','d7d5','e4d5'];
test('legal line reconstructs every board with full EP targets and numbered SAN',()=>{
 const seq=model.replayLine(DEFAULT_POSITION,ucis);assert.equal(Array.from(seq.moves,m=>m.san).join(' '),'e4 e5 f4 d5 exd5');
 assert.equal(seq.moves[0].fen_after,'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1');
 const board=new Chess();for(const [i,move]of ucis.entries()){board.move(move);assert.equal(seq.moves[i].fen_after.split(' ').filter((_,n)=>n!==3).join(' '),board.fen().split(' ').filter((_,n)=>n!==3).join(' '));}
 assert.equal(model.moveLabel(DEFAULT_POSITION,5,'exd5'),'3. exd5');
 assert.equal(model.moveLabel('7k/8/8/8/8/8/7p/4K3 b - - 0 27',1,'h1=Q+'),'27… h1=Q+');
});
test('back/forward positions and exact next navigation are pure; divergence needs confirmation',()=>{
 const seq=model.replayLine(DEFAULT_POSITION,ucis),before=JSON.stringify(seq.moves);
 for(let n=0;n<=ucis.length;n++){const prefix=model.replayLine(DEFAULT_POSITION,ucis.slice(0,n));assert.equal(n?prefix.moves.at(-1).fen_after:prefix.initial_fen,n?seq.moves[n-1].fen_after:DEFAULT_POSITION);}
 assert.equal(model.proposeMove(seq,1,'e5').advance,true);
 const alt=model.proposeMove(seq,1,'c5');assert.equal(alt.replacement,true);assert.equal(alt.uci,'c7c5');
 assert.equal(JSON.stringify(seq.moves),before);assert.throws(()=>model.proposeMove(seq,2,'e8e1'));
});
test('special moves, custom FEN counters, checkmate and invalid positions',()=>{
 for(const [fen,moves,last] of [
  ['r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1',['e1g1','e8c8'],'O-O-O'],
  ['k7/8/8/3pP3/8/8/8/4K3 w - d6 0 20',['e5d6'],'exd6'],
  ['7k/P7/8/8/8/8/8/4K3 w - - 0 1',['a7a8q'],'a8=Q+']])assert.equal(model.replayLine(fen,moves).moves.at(-1).san,last);
 assert.throws(()=>model.replayLine('not a FEN',[]));
 assert.throws(()=>model.replayLine(DEFAULT_POSITION,['e2e5']));
 assert.throws(()=>model.replayLine(DEFAULT_POSITION,['f2f3','e7e5','g2g4','d8h4','b1c3']),/terminal/);
 assert.throws(()=>model.replayLine(DEFAULT_POSITION,Array(501).fill('e2e4')),/500/);
});
function component(data,writable=true){const c=load('components/Study/LineEditor.tsx',{'react':React,'react/jsx-runtime':jsxRuntime,'@/components/Practice/PuzzleBoard':{default:()=>React.createElement('div',{'data-board':'reused'},'board')},'@/lib/studies':{useStudy:()=>({data,loading:false}),studyRequest:()=>assert.fail('No writes on render')},'@/lib/study-line':model});return renderToStaticMarkup(React.createElement(c.default,{id:'l',writable,onDirty:()=>{}}));}
test('editor renders board, move controls and initial position actions without writing',()=>{
 const line={line_id:'l',updated_at:'t',...model.replayLine(DEFAULT_POSITION,ucis)};const html=component(line);
 for(const text of ['data-board="reused"','Save line','Previous move','Next move','Flip board','Custom starting FEN','Reset to standard','3. exd5','Latest','Black to move'])assert.ok(html.includes(text),text);
 assert.match(html,/aria-current="step"/);assert.doesNotMatch(html,/Stockfish|cloud|reasoning/i);
});
test('readonly editor permits viewing while disabling writes; missing sequence remains explicit',()=>{
 const html=component({line_id:'l',updated_at:'t',initial_fen:DEFAULT_POSITION,moves:[]},false);assert.match(html,/disabled=""/);assert.match(html,/No moves yet/);
 assert.match(component(undefined),/Saved line unavailable/);
});
