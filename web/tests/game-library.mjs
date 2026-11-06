import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import * as jsx from 'react/jsx-runtime';
import {renderToStaticMarkup} from 'react-dom/server';
const root=new URL('../src/',import.meta.url);
const style={default:new Proxy({},{get:(_,key)=>String(key)})};
function load(file,imports={}){const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL(file,root),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,{exports,require:n=>{assert.ok(n in imports,n);return imports[n];},Number,Error});return exports;}
const p=load('app/games/library-presentation.ts');
const ui=load('components/ui/Primitives.tsx',{'react/jsx-runtime':jsx,'./primitives.module.css':style});
const link={default:({prefetch,...props})=>{assert.equal(prefetch,false);return React.createElement('a',props);}};
const collection=load('app/games/GameCollection.tsx',{'react/jsx-runtime':jsx,'next/link':link,'@/components/ui/Primitives':ui,'./library-presentation':p,'./library.module.css':style});
const game={game_id:'fixture/a',white:'White',black:'Black',white_rating:0,black_rating:null,date:null,result:'*',analysis_status:'NOT_ANALYZED',opening:null,eco:null};
const render=node=>renderToStaticMarkup(node);
test('dates validate calendar days without inventing missing dates or changing timezones',()=>{
 assert.equal(p.gameDate('2024.02.29'),'2024-02-29');assert.equal(p.gameDate('2026-11-06T23:00:00-08:00'),'2026-11-06');
 for(const value of [null,'????.??.??','2025.02.29','2026.13.02','2026.04.31','0000.01.01'])assert.equal(p.gameDate(value),null);
});
test('saved analysis status remains distinct from position evidence and queued work',()=>{
 assert.equal(p.analysisLabel('NOT_ANALYZED').label,'Pending');assert.match(p.analysisLabel('NOT_ANALYZED').description,/does not mean.*queued/);
 assert.match(p.analysisLabel('ANALYZED').description,/does not certify every position/);
 assert.equal(p.analysisLabel('UNKNOWN').label,'Unavailable');assert.equal(p.analysisLabel('FAILED').tone,'danger');assert.equal(p.analysisLabel('ANALYZING').tone,'warning');
});
test('desktop table and responsive cards use native accessible links and honest metadata fallbacks',()=>{
 const html=render(React.createElement(collection.GameCollection,{games:[game]}));
 assert.match(html,/<table/);assert.equal((html.match(/scope="col"/g)||[]).length,7);assert.match(html,/<caption/);
 assert.equal((html.match(/href="\/games\/fixture%2Fa"/g)||[]).length,2);assert.match(html,/aria-label="Review White versus Black/);
 assert.match(html,/Date unavailable/);assert.match(html,/Opening unavailable/);assert.match(html,/No result/);assert.doesNotMatch(html,/<small>|onclick|accuracy|best move/i);
 assert.equal(p.gameOpening({...game,eco:'C50'}).name,null);assert.equal(p.gameOpening({...game,opening:{full_name:'Recorded opening'}}).name,'Recorded opening');
});
function hookHarness(){
 let state,dependencies,cleanup,pendingEffect;const requests=[];
 const react={useState:initial=>{state??=initial;return[state,value=>{state=typeof value==='function'?value(state):value;}];},useEffect:(effect,deps)=>{if(!dependencies||deps.some((v,i)=>v!==dependencies[i])){dependencies=deps;pendingEffect=effect;}}};
 const api={listGames:params=>new Promise((resolve,reject)=>requests.push({params,resolve,reject}))};
 const {useGameLibrary}=load('app/games/useGameLibrary.ts',{'react':react,'@/lib/api':api,'./library-presentation':p});
 function useRender(...args){return useGameLibrary(...args);}
 return {requests,render:useRender,commit:()=>{if(pendingEffect){cleanup?.();cleanup=pendingEffect();pendingEffect=null;}},unmount:()=>cleanup?.()};
}
const flush=async()=>{await Promise.resolve();await Promise.resolve();};
test('bounded exact filters and paging; obsolete responses cannot overwrite the latest selection',async()=>{
 const h=hookHarness();assert.equal(h.render('','',0,0).loading,true);h.commit();
 assert.deepEqual(JSON.parse(JSON.stringify(h.requests[0].params)),{limit:100,offset:0});
 h.requests[0].resolve({games:[game],total:1});await flush();assert.equal(h.render('','',0,0).games.length,1);
 assert.equal(h.render('ANALYZED','1-0',100,0).games.length,0);h.commit();
 h.render('NOT_ANALYZED','',0,0);h.commit();
 assert.deepEqual(JSON.parse(JSON.stringify(h.requests[1].params)),{limit:100,offset:100,analysis_status:'ANALYZED',result:'1-0'});
 h.requests[2].resolve({games:[{...game,game_id:'latest'}],total:1});await flush();
 h.requests[1].resolve({games:[{...game,game_id:'obsolete'}],total:20});await flush();
 assert.equal(h.render('NOT_ANALYZED','',0,0).games[0].game_id,'latest');
 h.unmount();
});
test('errors, retry, malformed responses and unmount remain safe',async()=>{
 const h=hookHarness();h.render('','',0,0);h.commit();h.requests[0].reject(new Error('Fixture failure'));await flush();assert.equal(h.render('','',0,0).error,'Fixture failure');
 assert.equal(h.render('','',0,1).loading,true);h.commit();h.requests[1].resolve({games:[],total:-1});await flush();assert.match(h.render('','',0,1).error,/unreadable/);
 h.render('','',0,2);h.commit();h.requests[2].resolve({games:[],total:0});await flush();assert.equal(h.render('','',0,2).total,0);
 h.render('','',0,3);h.commit();h.unmount();h.requests[3].resolve({games:[game],total:1});await flush();assert.equal(h.render('','',0,3).loading,true);
});
function page(snapshot,states=['','',0,0]){let i=0;const Page=load('app/games/page.tsx',{'react/jsx-runtime':jsx,'react':{useState:()=>[states[i++],()=>{}]},'next/link':link,'@/components/ui/Primitives':ui,'./GameCollection':collection,'./library-presentation':p,'./useGameLibrary':{useGameLibrary:()=>snapshot},'./library.module.css':style}).default;return render(React.createElement(Page));}
test('loading, error and empty states offer relevant recovery without invented rows',()=>{
 const base={games:[],total:0,loading:false,error:null};
 assert.match(page({...base,loading:true}),/aria-busy="true"/);assert.doesNotMatch(page({...base,loading:true}),/<table/);
 assert.match(page({...base,error:'Synthetic outage'}),/role="alert"/);assert.match(page({...base,error:'Synthetic outage'}),/Try again/);
 assert.match(page(base),/Import your first games/);assert.match(page(base,['ANALYZED','0-1',0,0]),/No games match these filters/);
 assert.match(page(base,['','',100,0]),/First page/);
});
test('page preserves exact filters, native destinations, bounded pagination and evidence limitations',()=>{
 const html=page({games:[game],total:103,loading:false,error:null});assert.match(html,/103 games in your library/);assert.match(html,/aria-label="Game pages"/);assert.match(html,/Previous/);assert.match(html,/Next/);
 assert.equal((html.match(/aria-pressed=/g)||[]).length,7);assert.match(html,/href="\/library\/manage"/);assert.match(html,/not position-by-position coverage/);assert.doesNotMatch(html,/tablist/);
});
