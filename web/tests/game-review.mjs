import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import * as jsx from 'react/jsx-runtime';
import {renderToStaticMarkup} from 'react-dom/server';
import {Chess} from 'chess.js';
const root=new URL('../src/',import.meta.url),style={default:new Proxy({},{get:(_,key)=>String(key)})};
function load(file,imports={},globals={}){const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL(file,root),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,{exports,require:n=>{assert.ok(n in imports,n);return imports[n];},...globals});return exports;}
const render=node=>renderToStaticMarkup(node),element=(component,props)=>React.createElement(component,props);
const sheet=load('components/GameReview/ScoreSheet.tsx',{'react':React,'react/jsx-runtime':jsx,'./review.module.css':style});
class FakeElement{constructor(typing){this.typing=typing;}closest(){return this.typing?this:null;}}
const navigation=load('components/GameReview/review-navigation.ts',{}, {Element:FakeElement});
const replay=load('components/PlayerDashboard/pgn-context.ts',{'chess.js':{Chess}},{TextEncoder});
test('score sheet pairs legal SAN by real move number including Black-to-move setup roots',()=>{
 const ordinary=navigation.replayEntries(replay.replayPGN('1. e4 e5 2. Nf3 Nc6 *'));
 assert.deepEqual(JSON.parse(JSON.stringify(ordinary)).map(m=>[m.number,m.color,m.san]),[[1,'White','e4'],[1,'Black','e5'],[2,'White','Nf3'],[2,'Black','Nc6']]);
 const setup=navigation.replayEntries(replay.replayPGN('[SetUp "1"]\n[FEN "4k3/8/8/8/8/8/8/4K3 b - - 0 17"]\n\n17... Kd7 18. Kd2 *'));
 assert.equal(setup[0].number,17);assert.equal(setup[0].color,'Black');assert.equal(setup[1].number,18);assert.equal(setup[1].color,'White');
});
test('score selection is native keyboard accessible with one current move and stable callback plies',()=>{
 const entries=navigation.replayEntries(replay.replayPGN('1. e4 e5 *'));
 const html=render(element(sheet.default,{entries,currentPly:2,onSelectMove:()=>{}}));
 assert.match(html,/aria-label="1… e5"/);assert.match(html,/type="button"[^>]*aria-current="step"/);assert.equal((html.match(/aria-current="step"/g)||[]).length,1);
 assert.match(html,/aria-label="1\. e4"/);assert.doesNotMatch(html,/onclick=|best move|accuracy/i);
 const events=[],fake=load('components/GameReview/ScoreSheet.tsx',{'react':{useRef:()=>({current:null}),useEffect:()=>{}},'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})},'./review.module.css':style});
 const tree=fake.default({entries,currentPly:0,onSelectMove:p=>events.push(p)});
 function visit(node){if(!node)return;if(Array.isArray(node)){node.forEach(visit);return;}if(node.type==='button')node.props.onClick();visit(node.props?.children);}
 visit(tree);assert.deepEqual(events,[1,2]);
});
test('legacy move metadata retains its classification and critical/episode annotations',()=>{
 const move=load('components/GameReview/MoveList.tsx',{'react/jsx-runtime':jsx,'@/lib/types':{classificationColor:()=> '#abcdef'},'./ScoreSheet':sheet});
 const html=render(element(move.default,{moves:[{ply:9,move_number:5,color:'White',played_move:'O-O',classification:'Existing label',centipawn_loss:12,is_critical:true,episode_index:3}],currentPly:9,onSelectMove:()=>{},highlightEpisode:3}));
 assert.match(html,/Existing label \(12 cp\)/);assert.match(html,/data-critical="true" data-episode="true"/);assert.match(html,/aria-label="5\. O-O"/);
});
test('board preserves read-only rendering, recorded last-move highlighting and accessible boundaries',()=>{
 let options;const board=load('components/GameReview/ChessboardPane.tsx',{'react/jsx-runtime':jsx,'react-chessboard':{Chessboard:props=>{options=props.options;return React.createElement('div',{'data-board':true});}},'./review.module.css':style});
 const html=render(element(board.default,{fen:new Chess().fen(),currentMove:null,lastMove:'e2e4',currentPly:0,maxPly:10,evaluationRailRef:()=>{},onStart:()=>{},onPrev:()=>{},onNext:()=>{},onEnd:()=>{}}));
 assert.equal(options.allowDragging,false);assert.ok(options.squareStyles.e2);assert.ok(options.squareStyles.e4);assert.match(html,/aria-label="Move navigation"/);assert.match(html,/disabled=""[^>]*aria-label="Start"/);assert.match(html,/Ply 0 \/ 10/);
});
test('evaluation bar placement preserves score, tier, bounds and cache/live channel without a second evaluation',()=>{
 const helpers=load('lib/position-evaluation.ts',{'chess.js':{Chess}},{process:{env:{}}});
 let portal;const bar=load('components/GameReview/EvaluationBar.tsx',{'react/jsx-runtime':jsx,'react-dom':{createPortal:(node,target)=>{portal={node,target};return node;}},'@/lib/position-evaluation':helpers});
 const target={};const html=render(bar.default({score:{kind:'cp',cp:-125},tier:'LIVE_ENGINE_UNVERIFIED',bound:'upper',channel:'live',target}));
 assert.equal(portal.target,target);assert.match(html,/data-eval-channel="live"/);assert.match(html,/LIVE_ENGINE_UNVERIFIED · White-relative evaluation: ≤ -1.25/);assert.equal((html.match(/role="img"/g)||[]).length,1);
 const absent=render(bar.default({}));assert.match(absent,/White-relative evaluation: Unavailable/);assert.doesNotMatch(absent,/position-eval-fill/);
});
test('keyboard navigation respects text editing and header does not fabricate ratings/opening',()=>{
 const presentation=load('app/games/library-presentation.ts');const ui=load('components/ui/Primitives.tsx',{'react/jsx-runtime':jsx,'./primitives.module.css':style});
 const chrome=load('components/GameReview/ReviewChrome.tsx',{'react/jsx-runtime':jsx,'next/link':{default:({prefetch,...props})=>{assert.equal(prefetch,false);return React.createElement('a',props);}},'@/components/ui/Primitives':ui,'@/app/games/library-presentation':presentation,'./review.module.css':style},{Element:FakeElement});
 assert.equal(navigation.isReviewTypingTarget(null),false);assert.equal(navigation.isReviewTypingTarget(new FakeElement(true)),true);assert.equal(navigation.isReviewTypingTarget(new FakeElement(false)),false);
 const html=render(element(chrome.ReviewHeader,{game:{white:'Fixture A',black:'Fixture B',white_rating:null,black_rating:0,date:null,result:'*',analysis_status:'UNKNOWN',opening:null,eco:null}}));
 assert.match(html,/Opening unavailable/);assert.match(html,/Unavailable/);assert.doesNotMatch(html,/Black · 0|accuracy|Stockfish/i);
});
test('review styles contain portal override, responsive board bounds and reduced-motion handling',()=>{
 const css=fs.readFileSync(new URL('components/GameReview/review.module.css',root),'utf8');assert.match(css,/data-eval-channel=cache/);assert.match(css,/live-evaluation\[data-override=true\]/);assert.match(css,/max-width:719px/);assert.match(css,/prefers-reduced-motion/);assert.match(css,/grid-template-columns:20px minmax\(0,1fr\)/);
});
