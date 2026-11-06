import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import * as jsx from 'react/jsx-runtime';
import {renderToStaticMarkup} from 'react-dom/server';
const root=new URL('../src/components/',import.meta.url);
const style={default:new Proxy({},{get:(_,key)=>String(key)})};
function load(file,imports={}){const exports={};const code=ts.transpileModule(fs.readFileSync(new URL(file,root),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;vm.runInNewContext(code,{exports,require:n=>{assert.ok(n in imports,n);return imports[n];}});return exports;}
const routes=load('AppShell/routes.ts');
const ui=load('ui/Primitives.tsx',{'react/jsx-runtime':jsx,'./primitives.module.css':style});
let pathname='/games';
const Shell=load('AppShell/AppShell.tsx',{'react/jsx-runtime':jsx,'next/link':{default:({prefetch,...props})=>{assert.equal(prefetch,false);return React.createElement('a',props);}},'next/navigation':{usePathname:()=>pathname},'../ui/Primitives':ui,'../ui/workspace-tokens.css':{},'./routes':routes,'./shell.module.css':style}).default;
const render=component=>renderToStaticMarkup(component);
test('homepage and design-lab have no workspace shell, tokens, nav or extra wrapper',()=>{
 for(pathname of ['/','/design-lab','/design-lab/cinematic'])assert.equal(render(React.createElement(Shell,null,'unchanged content')),'<main>unchanged content</main>');
});
test('all workspace routes and deep links have one main, correct active link, real destinations and skip link',()=>{
 for(const [path,href] of [['/games','/games'],['/games/test','/games'],['/dashboard','/dashboard'],['/practice','/practice'],['/practice/test','/practice'],['/study/lines/fixture','/study'],['/library/manage','/library/manage']]){
  pathname=path;const html=render(React.createElement(Shell,null,'existing page'));
  assert.equal((html.match(/<main /g)||[]).length,1);assert.equal((html.match(/aria-current="page"/g)||[]).length,1);
  assert.match(html,new RegExp(`href="${href}"[^>]*aria-current="page"`));assert.match(html,/href="#workspace-content"/);assert.match(html,/id="workspace-content" tabindex="-1"/);
  for(const target of ['/','/games','/dashboard','/practice','/study','/library/manage'])assert.ok(html.includes(`href="${target}"`));
 }
 assert.equal(routes.activeRoute('/games-unrelated','/games'),false);assert.equal(routes.immersiveRoute('/design-lab-unrelated'),false);
 assert.equal(routes.workspaceSection('/games/test'),'Game review');assert.equal(routes.workspaceSection('/practice/test'),'Practice session');
});
test('buttons preserve events and native props; busy submission is disabled without invented status text',()=>{
 const click=()=>{};const tree=ui.Button({children:'Save',onClick:click,busy:true,type:'submit','aria-label':'Save study'});assert.equal(tree.props.onClick,click);assert.equal(tree.props.type,'submit');assert.equal(tree.props.disabled,true);assert.equal(tree.props['aria-busy'],true);
 assert.match(render(tree),/aria-label="Save study"/);assert.equal(ui.Button({children:'Retry'}).props.type,'button');
});
test('states and headings remain semantic and retain explicit caller labels',()=>{
 assert.match(render(React.createElement(ui.LoadingState,{label:'Loading section'})),/role="status" aria-label="Loading section"/);
 assert.match(render(React.createElement(ui.EmptyState,{role:'alert'},'Unavailable')),/role="alert"/);
 assert.match(render(React.createElement(ui.PageHeading,{description:'Your imported games'},'Games')),/<h1>Games<\/h1><p>Your imported games<\/p>/);
 assert.match(render(React.createElement(ui.SectionHeading,null,'Coverage')),/<h2/);
 const tabs=render(React.createElement(ui.Tabs,{label:'Sections'},React.createElement('a',{href:'/games'},'Games')));assert.match(tabs,/nav aria-label="Sections"/);assert.doesNotMatch(tabs,/tablist/);
 assert.doesNotMatch(render(React.createElement(ui.Status,null,'Personal workspace')),/role="status"|aria-live|verified/i);
});
test('existing dashboard Panel and State delegate presentation while preserving evidence and retry',()=>{
 const p=load('PlayerDashboard/Primitives.tsx',{'react/jsx-runtime':jsx,'@/components/ui/Primitives':ui,'@/lib/player-dashboard':{metricText:()=>''}});
 assert.match(render(React.createElement(p.Panel,{title:'Coverage',id:'coverage'},'facts')),/<section[^>]*id="coverage"/);
 assert.match(render(React.createElement(p.State,{loading:true})),/Loading section/);
 assert.match(render(React.createElement(p.State,{error:'Unavailable',retry:()=>{}})),/role="alert"/);
 assert.match(render(React.createElement(p.State,{empty:'No usable games'})),/No usable games/);
 assert.match(render(React.createElement(p.Evidence,{value:'HEURISTIC'})),/pd-heuristic/);
});
function luminance(hex){const values=hex.match(/\w\w/g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return values[0]*.2126+values[1]*.7152+values[2]*.0722;}
function contrast(a,b){const values=[luminance(a),luminance(b)].sort((x,y)=>y-x);return(values[0]+.05)/(values[1]+.05);}
test('workspace text, primary action and focus colors meet measured contrast targets',()=>{
 for(const text of ['f4e9ed','d0bac4','b49fa8'])for(const bg of ['110c10','1a1219','211820','3a2330'])assert.ok(contrast(text,bg)>=4.5,`${text} / ${bg}`);
 assert.ok(contrast('fff5f8','a53650')>=4.5);assert.ok(contrast('f3be94','211820')>=3);
 const css=fs.readFileSync(new URL('ui/workspace-tokens.css',root),'utf8');assert.match(css,/^\[data-workspace-shell\]/m);assert.doesNotMatch(css,/--eval-|--accent-blue\s*:|--accent-green\s*:/);assert.match(css,/prefers-reduced-motion/);
});
test('shell has no data fetches, workers, engine imports or animations',()=>{
 const source=fs.readFileSync(new URL('AppShell/AppShell.tsx',root),'utf8');assert.doesNotMatch(source,/fetch\(|new Worker|stockfish|from ['"].*\/lib\//i);
 const css=fs.readFileSync(new URL('AppShell/shell.module.css',root),'utf8');assert.doesNotMatch(css,/animation:|@keyframes/);
});
