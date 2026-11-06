import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';
import {mergeGeometries} from 'three/addons/utils/BufferGeometryUtils.js';
import {Chess} from 'chess.js';
import React from 'react';
import * as jsx from 'react/jsx-runtime';
import {renderToStaticMarkup} from 'react-dom/server';
const base=new URL('../src/app/design-lab/cinematic/',import.meta.url);
function load(file,imports={}){const exports={};const source=ts.transpileModule(fs.readFileSync(new URL(file,base),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;vm.runInNewContext(source,{exports,require:n=>{if(n==='./chapters')return load('chapters.ts');if(n==='./illustration')return replay;assert.ok(n in imports,n);return imports[n];}});return exports;}
const fixture=load('fixtures.ts'),timeline=load('scene/timeline.ts'),lifetime=load('scene/lifetime.ts');
const replay=load('scene/illustration.ts',{'chess.js':{Chess},'../fixtures':fixture,'../chapters':load('chapters.ts'),'./timeline':timeline});
const geometry=load('scene/geometry.ts',{'three':THREE,'three/addons/geometries/RoundedBoxGeometry.js':{RoundedBoxGeometry},'three/addons/utils/BufferGeometryUtils.js':{mergeGeometries}});
test('full legal history matches scene FEN, all 32 square identities and correct world axes',()=>{
 const game=new Chess();fixture.HISTORY.forEach(san=>game.move(san));assert.equal(game.fen(),fixture.FEN);assert.equal(fixture.PIECES.length,32);assert.equal(new Set(fixture.PIECES.map(p=>p.square)).size,32);
 for(const p of fixture.PIECES){assert.equal(game.get(p.square).type,p.kind);assert.equal(game.get(p.square).color,p.color);}
 assert.deepEqual(Array.from(fixture.squarePosition('a1')),[-3.5,0,3.5]);assert.deepEqual(Array.from(fixture.squarePosition('h8')),[3.5,0,-3.5]);assert.equal(game.turn(),'w');
});
test('absolute timeline is reversible, settled before facts, never advances recorded history',()=>{
 const initial=JSON.stringify(fixture.PIECES);
 for(const tier of ['desktop','tablet','mobile']){
  const samples=Array.from({length:101},(_,i)=>JSON.stringify(timeline.sampleScene(i/100,tier)));
  for(let i=100;i>=0;i--)assert.equal(JSON.stringify(timeline.sampleScene(i/100,tier)),samples[i]);
  const hero=timeline.sampleScene(0,tier),end=timeline.sampleScene(1,tier);
  assert.equal(hero.knightLift,tier==='mobile'?1.65:2.65);assert.equal(hero.bishopLift,tier==='mobile'?.45:1.25);assert.equal(end.knightLift,0);assert.equal(end.bishopLift,0);assert.equal(end.settled,true);assert.equal(timeline.sampleScene(.21,tier).settled,false);assert.equal(timeline.sampleScene(.22,tier).settled,true);
 }
 assert.equal(JSON.stringify(fixture.PIECES),initial);assert.equal(timeline.sampleScene(-4,'desktop').p,0);assert.equal(timeline.sampleScene(7,'desktop').p,1);
});
test('DPR is bounded by device, quality tier and pixel budget',()=>{
 for(const [w,h,tier,cap] of [[1440,900,'desktop',3000000],[834,1112,'tablet',2000000],[390,844,'mobile',1000000],[7680,4320,'desktop',3000000]]){const scale=timeline.drawingScale(w,h,3,tier);assert.ok(w*h*scale*scale<=cap+1);assert.ok(scale<=(tier==='desktop'?1.5:1.25));}
 assert.equal(timeline.drawingScale(300,500,1,'mobile'),1);assert.equal(timeline.drawingScale(300,500,.25,'mobile'),.25);
});
test('original piece silhouettes are finite, grounded and within geometry budgets at both LODs',()=>{
 for(const low of [false,true]){let visible=0,bytes=0;for(const kind of ['p','r','n','b','q','k']){const g=geometry.pieceGeometry(kind,low);g.computeBoundingBox();const b=g.boundingBox;assert.ok(b.min.y>=-.001,kind);assert.ok(b.max.y<1.85,kind);assert.ok(b.max.x-b.min.x<1,kind);assert.ok(b.max.z-b.min.z<1.05,kind);assert.ok([...g.attributes.position.array].every(Number.isFinite));const count=kind==='p'?16:['r','n','b'].includes(kind)?4:2;visible+=(g.index?.count||g.attributes.position.count)/3*count;bytes+=Object.values(g.attributes).reduce((sum,a)=>sum+a.array.byteLength,0);g.dispose();}assert.ok(visible<(low?60000:150000),String(visible));assert.ok(bytes<(low?6:12)*1048576);}
});
test('RAF owner deduplicates wakes, rests, pauses and cannot resurrect after idempotent disposal',()=>{
 let next=0,renders=0,again=true;const callbacks=new Map();const owner=lifetime.frameOwner(fn=>{callbacks.set(++next,fn);return next;},id=>callbacks.delete(id),()=>{renders++;return again;});
 owner.wake();owner.wake();assert.equal(callbacks.size,1);let [id,fn]=callbacks.entries().next().value;callbacks.delete(id);fn(0);assert.equal(renders,1);assert.equal(callbacks.size,1);owner.pause();assert.equal(callbacks.size,0);owner.wake();[id,fn]=callbacks.entries().next().value;callbacks.delete(id);again=false;fn(16);assert.equal(callbacks.size,0);owner.wake();const late=callbacks.values().next().value;owner.dispose();owner.dispose();late(32);owner.wake();assert.equal(renders,2);assert.equal(callbacks.size,0);assert.equal(owner.pending,false);
});
test('pacing includes long active stalls; scheduler excludes idle before sampling',()=>{const p=timeline.pacing([16,16,16,1000]);assert.equal(p.samples,4);assert.equal(p.p95Ms,1000);assert.equal(p.medianMs,16);assert.equal(p.fps,62.5);assert.equal(timeline.pacing([]).fps,0);});
test('scene keeps a1 dark, no engine/API/worker imports, and no production route edits',()=>{
 const scene=fs.readFileSync(new URL('scene/create-scene.ts',base),'utf8');assert.match(scene,/parity\?0x665b5d:0xc7bdad/);
 for(const name of ['SceneCanvas.tsx','CinematicPrototype.tsx','fixtures.ts','scene/create-scene.ts']){const text=fs.readFileSync(new URL(name,base),'utf8');assert.doesNotMatch(text,/from ['"].*(?:\/lib\/|stockfish|reasoning)/i);assert.doesNotMatch(text,/fetch\(|new Worker\(/);}
 assert.match(fs.readFileSync(new URL('../src/app/page.tsx',import.meta.url),'utf8'),/redirect\("\/games"\)/);
});

test('server/static HTML keeps real headings, both CTAs and honest facts without a renderer',()=>{
 for(const query of ['view=static','motion=reduce']){const component=load('CinematicPrototype.tsx',{'react':React,'react/jsx-runtime':jsx,'next/link':{default:({prefetch,...props})=>{assert.equal(prefetch,false);return React.createElement('a',props);}},'next/navigation':{useSearchParams:()=>new URLSearchParams(query)},'./SceneCanvas':{default:()=>assert.fail('No renderer during static SSR')},'./scene/timeline':timeline,'./cinematic.module.css':{default:new Proxy({},{get:(_,name)=>String(name)})}}).default;const html=renderToStaticMarkup(React.createElement(component,{initialStatic:query.includes('view'),forceReduced:query.includes('motion')}));assert.match(html,/data-mode="static"/);assert.match(html,/See beyond/);assert.match(html,/Enter Cheez64/);assert.match(html,/18 legal plies/);assert.match(html,/Unavailable/);assert.doesNotMatch(html,/<canvas|score|centipawn/);if(query.includes('motion'))assert.match(html,/data-reduced="true"/);}
});

test('WebGL construction refusal propagates after safe partial-resource cleanup',()=>{
 const fault=load('scene/create-scene.ts',{'three':{...THREE,WebGLRenderer:class{constructor(){throw Error('WebGL2 refused');}}},'three/addons/geometries/RoundedBoxGeometry.js':{RoundedBoxGeometry},'../fixtures':fixture,'./geometry':geometry,'./timeline':timeline});
 assert.throws(()=>fault.createScene({append:()=>assert.fail('No canvas attached after constructor refusal')},'mobile'),/WebGL2 refused/);
});

test('four authored suspended pieces settle exactly and reverse without fixture mutation',()=>{
 for(const tier of ['desktop','tablet','mobile']){
  assert.equal(fixture.PIECES.filter(p=>timeline.pieceOffset(p.square,p.color,p.kind,0,tier).some(v=>v!==0)).length,4);
  for(const piece of fixture.PIECES){const samples=Array.from({length:101},(_,i)=>JSON.stringify(timeline.pieceOffset(piece.square,piece.color,piece.kind,i/100,tier)));for(let i=100;i>=0;i--)assert.equal(JSON.stringify(timeline.pieceOffset(piece.square,piece.color,piece.kind,i/100,tier)),samples[i]);assert.deepEqual(Array.from(timeline.pieceOffset(piece.square,piece.color,piece.kind,.6,tier)),[0,0,0]);}
 }
});
test('portrait chapters never visually overlap during the deterministic cross-chapter handoff',()=>{
 for(let i=0;i<=1000;i++){const state=timeline.chapterPresentation(i/1000);assert.ok(state.hero>=0&&state.hero<=1);assert.ok(state.analyze>=0&&state.analyze<=1);assert.equal(state.hero*state.analyze,0);}
 assert.equal(timeline.chapterPresentation(0).hero,1);assert.equal(timeline.chapterPresentation(.34).analyze,1);
});
test('route-owned prepaint gate selects stable layout before content, respecting reduction/data/static',async()=>{
 const Page=load('page.tsx',{'react/jsx-runtime':jsx,'./CinematicPrototype':{default:()=>null}}).default;
 for(const [params,reduced,saveData,expected] of [[{},false,false,'on'],[{},true,false,'off'],[{},false,true,'off'],[{view:'static'},false,false,'off'],[{motion:'reduce'},false,false,'off']]){
  const tree=await Page({searchParams:Promise.resolve(params)}),[script,content]=tree.props.children;
  assert.equal(script.type,'script');assert.ok(content);const dataset={};vm.runInNewContext(script.props.dangerouslySetInnerHTML.__html,{innerWidth:1440,innerHeight:900,document:{documentElement:{dataset}},matchMedia:()=>({matches:reduced}),navigator:{connection:{saveData}}});assert.equal(dataset.cheez64Cinematic,expected);
 }
 const component=load('CinematicPrototype.tsx',{'react':React,'react/jsx-runtime':jsx,'next/link':{default:({prefetch,...props})=>{assert.equal(prefetch,false);return React.createElement('a',props);}},'./SceneCanvas':{default:()=>assert.fail('SSR cannot start graphics')},'./scene/timeline':timeline,'./cinematic.module.css':{default:new Proxy({},{get:(_,name)=>String(name)})}}).default;
 const html=renderToStaticMarkup(React.createElement(component));assert.match(html,/data-pending="true"/);assert.match(html,/18 legal plies/);assert.doesNotMatch(html,/Prototype &amp; rendering details|Visible triangles|Render frames/);assert.equal((html.match(/<h[12] /g)||[]).length,4);for(const c of load('chapters.ts').CHAPTERS)assert.ok(html.includes(`id="${c.id}"`));
});

test('all four chapters have reversible exclusive progression and semantic anchors',()=>{
 const chapters=load('chapters.ts').CHAPTERS;assert.equal(chapters.length,4);
 for(const c of chapters){const state=timeline.chapterPresentation(c.progress);assert.equal(state[c.id],1);assert.equal(Object.values(state).filter(v=>v>0).length,1);}
 const samples=Array.from({length:1001},(_,i)=>JSON.stringify(timeline.chapterPresentation(i/1000)));for(let i=1000;i>=0;i--){const state=timeline.chapterPresentation(i/1000);assert.equal(JSON.stringify(state),samples[i]);assert.ok(Object.values(state).filter(v=>v>0).length<=1);}
});
test('illustrative exchange and understanding facts are legal, not an engine verdict',()=>{
 const board=new Chess(fixture.FEN);assert.equal(board.get('c3').type,'p');assert.equal(board.get('c3').color,'w');assert.ok(board.moves().includes('d4'));assert.ok(board.isAttacked('d4','w'));
 for(const san of load('chapters.ts').ILLUSTRATIVE_LINE)assert.ok(board.move(san));
 assert.equal(fixture.FEN,new Chess(fixture.FEN).fen());
});

test('scroll replay stable stops exactly match every legal FEN and captured piece identity',()=>{
 const original=JSON.stringify(fixture),board=new Chess(fixture.FEN);
 for(const [i,q] of [.365,.435,.505].entries()){board.move(load('chapters.ts').ILLUSTRATIVE_LINE[i]);const pose=replay.sampleIllustration(q);assert.equal(pose.fen,board.fen());assert.equal(pose.completed,i+1);assert.equal(pose.moving,false);const visible=pose.pieces.filter(p=>p.scale>0);assert.equal(visible.length,[32,31,30][i]);assert.equal(new Set(visible.map(p=>p.square)).size,visible.length);for(const p of visible){const identity=fixture.PIECES.find(x=>x.square===p.id);assert.equal(board.get(p.square).type,identity.kind);assert.equal(board.get(p.square).color,identity.color);assert.deepEqual(Array.from(p.position),Array.from(fixture.squarePosition(p.square)));}}
 assert.equal(replay.sampleIllustration(.505).pieces.find(p=>p.id==='c3').square,'d4');assert.equal(replay.sampleIllustration(1).pieces.find(p=>p.id==='d2').scale,0);assert.equal(replay.sampleIllustration(1).pieces.find(p=>p.id==='e5').scale,0);assert.equal(JSON.stringify(fixture),original);
});
test('all moving poses are bounded, explicitly unsettled and reverse exactly including captures',()=>{
 const samples=Array.from({length:1001},(_,i)=>JSON.stringify(replay.sampleIllustration(i/1000)));for(let i=1000;i>=0;i--){const pose=replay.sampleIllustration(i/1000);assert.equal(JSON.stringify(pose),samples[i]);for(const p of pose.pieces){assert.ok(p.position.every(Number.isFinite));assert.ok(p.scale>=0&&p.scale<=1);}if(pose.moving)assert.equal(pose.fen,null);}
 for(const q of [.33,.40,.47]){const p=replay.sampleIllustration(q);assert.equal(p.moving,true);assert.equal(p.fen,null);assert.ok(p.pieces.some(x=>x.position[1]>0));}
 assert.equal(replay.sampleIllustration(0).fen,fixture.FEN);assert.equal(replay.sampleIllustration(1).fen,replay.RESULT_FEN);
});
test('compact prepaint does not reserve a clipped live scene on short screens',async()=>{
 const Page=load('page.tsx',{'react/jsx-runtime':jsx,'./CinematicPrototype':{default:()=>null}}).default;const tree=await Page({searchParams:Promise.resolve({})});
 for(const [innerWidth,innerHeight] of [[390,667],[834,600],[1440,600]]){const dataset={};vm.runInNewContext(tree.props.children[0].props.dangerouslySetInnerHTML.__html,{innerWidth,innerHeight,document:{documentElement:{dataset}},matchMedia:()=>({matches:false}),navigator:{}});assert.equal(dataset.cheez64Cinematic,'off');}
});
