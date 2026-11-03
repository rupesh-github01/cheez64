import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
const root=new URL('../src/',import.meta.url);
function load(file,imports,globals={}){const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL(file,root),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText,{exports,require:name=>{if(name in imports)return imports[name];throw Error(name);},process:{env:{}},...globals});return exports;}
test('study transport keeps reads no-store and mutations explicit with local JSON action headers',async()=>{
 const calls=[];const api=load('lib/studies.ts',{'react':React},{fetch:async(url,init)=>{calls.push([url,init]);return {ok:true,json:async()=>({id:'fixture'})};}});
 await api.studyRequest('/status');await api.studyRequest('', 'POST',{title:"King's Gambit"});await api.studyRequest('/lines/fixture','PUT',{title:'Rename'});await api.studyRequest('/fixture','DELETE');
 assert.equal(calls[0][1].method,'GET');assert.equal(calls[0][1].cache,'no-store');assert.equal(calls[0][1].body,undefined);
 for(const [,options]of calls.slice(1)){assert.equal(options.headers['x-study-action'],'local-study');assert.equal(options.headers['content-type'],'application/json');}
 assert.equal(calls[2][1].method,'PUT');assert.equal(calls[3][1].method,'DELETE');assert.ok(calls.every(([url])=>url.startsWith('http://localhost:8000/api/studies')));
});
test('study transport exposes validation/read-only errors without false success',async()=>{
 const api=load('lib/studies.ts',{'react':React},{fetch:async()=>({ok:false,status:403,json:async()=>({detail:'Server is in read-only mode'})})});await assert.rejects(api.studyRequest('','POST',{title:'x'}),/read-only/);
 const invalid=load('lib/studies.ts',{'react':React},{fetch:async()=>({ok:false,status:422,json:async()=>({detail:[{}]})})});await assert.rejects(invalid.studyRequest(''),/Check the title/);
});
function workspace(status,paths={}){return load('components/Study/StudyWorkspace.tsx',{'react':React,'react/jsx-runtime':jsxRuntime,'next/link':{default:({children,...p})=>React.createElement('a',p,children)},'next/navigation':{useRouter:()=>({push:()=>assert.fail('No navigation on render')})},'@/lib/studies':{studyRequest:()=>assert.fail('No mutation on render'),useStudy:path=>({data:path==='/status'?status:paths[path],loading:false})},'./study.css':{}});}
test('study library renders hierarchy links and readonly controls without writes',()=>{
 const item={id:'a',title:"King's Gambit",description:'Preparation',created_at:'t',updated_at:'t'};const c=workspace({status:'AVAILABLE',writes_enabled:false},{'?limit=25&offset=0':{items:[item],total:1,limit:25,offset:0,next_offset:null}});
 const html=renderToStaticMarkup(React.createElement(c.StudyLibrary));assert.match(html,/Study Library/);assert.match(html,/href="\/study\/a"/);assert.match(html,/Read-only mode/);assert.match(html,/Create study/);assert.match(html,/disabled/);
});
test('uninitialized state is explicit and does not claim an empty initialized library',()=>{
 const c=workspace({status:'UNINITIALIZED',writes_enabled:true});const html=renderToStaticMarkup(React.createElement(c.StudyLibrary));assert.match(html,/storage needs setup/);assert.match(html,/src.study_store init/);assert.doesNotMatch(html,/library is empty/);
});
test('line detail intentionally offers metadata and six-field FEN, not a working board editor',()=>{
 const c=workspace({status:'AVAILABLE',writes_enabled:true},{'/lines/l':{id:'l',title:'Falkbeer',description:'',sub_study_id:'s',initial_fen:'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',created_at:'t',updated_at:'t'},'/sub-studies/s':{study_id:'a',title:'Video'}});
 const html=renderToStaticMarkup(React.createElement(c.StudyLineDetail,{id:'l'}));assert.match(html,/Line editor coming in Segment 20.8B/);assert.match(html,/w KQkq - 0 1/);assert.doesNotMatch(html,/chessboard/);
});
