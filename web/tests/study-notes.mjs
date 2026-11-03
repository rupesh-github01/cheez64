import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import { Chess, DEFAULT_POSITION } from 'chess.js';
function load(file, imports){const exports={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL('../src/'+file,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText,{exports,require:name=>{if(name in imports)return imports[name];throw Error(name);}});return exports;}
const model=load('lib/study-line.ts',{'chess.js':{Chess,DEFAULT_POSITION}});
function fixture(){return {line_id:'line',initial_fen:DEFAULT_POSITION,updated_at:'r0',starting_note:'Root',notes_enabled:true,moves:[{id:'a',uci:'e2e4',comment:'e4 note'},{id:'b',uci:'e7e5',comment:'e5 note'}]};}
test('note drafts are keyed by stable move identity and survive navigation, edits and clears',()=>{
 const line=fixture(),text='  Counterattack ♞\n\nKeep <b>literal</b>.\n';let drafts=model.editNoteDraft(line,{},'a',text);drafts=model.editNoteDraft(line,drafts,'b','Second');
 assert.equal(drafts.a,text);assert.equal(drafts.b,'Second');assert.equal(model.noteText(line,'a'),'e4 note');
 drafts=model.editNoteDraft(line,drafts,'a','e4 note');assert.equal('a' in drafts,false);assert.equal(drafts.b,'Second');
 drafts=model.editNoteDraft(line,drafts,'root','');assert.equal(drafts.root,'');assert.equal(model.noteText(line,'root'),'Root');
 const replacement={...line,moves:[line.moves[0],{id:'new',uci:'c7c5',comment:''}]};assert.equal(model.noteText(replacement,'new'),'');assert.equal(model.noteText(replacement,'a'),'e4 note');
});
test('explicit multi-note save carries each accepted revision; no SAN/FEN or ply-based association',async()=>{
 const calls=[],accepted=[];const result=await model.persistNoteDrafts(fixture(),{a:'Changed',root:'Starting\nnote',b:''},async(path,body)=>{calls.push([path,body]);return {line_id:'line',move_id:path.endsWith('starting-note')?null:path.split('/')[4],comment:body.comment,updated_at:'r'+calls.length};},(seq,key)=>accepted.push([seq,key]));
 assert.deepEqual(calls.map(c=>c[1].expected_updated_at),['r0','r1','r2']);assert.equal(result.starting_note,'Starting\nnote');assert.equal(result.moves[0].comment,'Changed');assert.equal(result.moves[1].has_comment,false);assert.equal(accepted.length,3);assert.equal(fixture().moves[0].comment,'e4 note');
 assert.ok(calls.every(c=>Object.keys(c[1]).join(',')==='comment,expected_updated_at'));
});
test('failed later note save retains unaccepted drafts while keeping the accepted revision',async()=>{
 const drafts={a:'Saved',b:'Keep draft'},initial=fixture();let current=initial;
 await assert.rejects(model.persistNoteDrafts(initial,drafts,async(path,body)=>{if(path.includes('/b/'))throw Error('409 stale revision');return {line_id:'line',move_id:'a',comment:body.comment,updated_at:'r1'};},(seq,key)=>{current=seq;delete drafts[key];}),/stale/);
 assert.equal(current.updated_at,'r1');assert.deepEqual(drafts,{b:'Keep draft'});assert.equal(current.moves[1].comment,'e5 note');
});
test('missing or mismatched move identity never applies a response to another move',async()=>{
 await assert.rejects(model.persistNoteDrafts(fixture(),{deleted:'draft'},()=>assert.fail('No request for a deleted move'),()=>assert.fail('No acceptance')),/no longer/);
 await assert.rejects(model.persistNoteDrafts(fixture(),{a:'draft'},async()=>({line_id:'other',move_id:'a',comment:'bad',updated_at:'r1'}),()=>assert.fail('No acceptance')),/identity/);
});
test('notes panel escapes text, supports multiline and exposes save/clear/discard and readonly states',()=>{
 const Component=load('components/Study/StudyNotes.tsx',{'react/jsx-runtime':jsxRuntime}).default;
 const props={label:'Notes · 2… d5',text:'<script>evil()</script>\n中心 & ♞',enabled:true,currentDirty:true,count:2,busy:false,message:'',onChange:()=>{},onSave:()=>{},onSaveAll:()=>{},onDiscard:()=>{}};
 const html=renderToStaticMarkup(React.createElement(Component,props));assert.match(html,/Notes · 2… d5/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);assert.match(html,/2 unsaved notes/);for(const t of ['Save note','Save all notes','Clear comment','Discard note changes'])assert.ok(html.includes(t));
 const read=renderToStaticMarkup(React.createElement(Component,{...props,label:'Starting Position Note',enabled:false,currentDirty:false,count:0}));assert.match(read,/Starting Position Note/);assert.match(read,/disabled/);
});
test('a temporary root change cannot lose the original starting note when returning before save',()=>{
 const saved=fixture(),otherFen='k7/8/8/3pP3/8/8/8/4K3 w - d6 0 20';
 const changed={...saved,initial_fen:otherFen,moves:[],starting_note:model.initialNoteForFen(saved,saved,otherFen)};
 assert.equal(changed.starting_note,'');assert.equal(model.initialNoteForFen(saved,changed,saved.initial_fen),'Root');assert.equal(saved.starting_note,'Root');
});
