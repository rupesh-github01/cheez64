'use client';
import { useEffect, useRef } from 'react';
import type { SceneHandle, SceneStats } from './scene/create-scene';
import { clamp, pacing, qualityForWidth, chapterPresentation } from './scene/timeline';
import { frameOwner } from './scene/lifetime';
export type Metrics = SceneStats & { tier:string; frames:number; frameSamples:number; medianMs:number; p95Ms:number; fps:number; readyMs:number; renderMs:number; firstFrameMs:number; shadowsReduced:boolean; settled:boolean };
export default function SceneCanvas({ onReady, onFailure, onMetrics, onExport }: { onReady:()=>void; onExport:(uri:string)=>void; onFailure:(message:string)=>void; onMetrics:(metrics:Metrics)=>void }) {
  const host=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const element=host.current!;
    const story=element.closest('[data-story]') as HTMLElement;
    const stage=element.closest('[data-stage]') as HTMLElement;
    const landing=element.closest('[data-cheez64]') as HTMLElement;
    let active=true,handle:SceneHandle|undefined,current=0,target=0,previous=0,frames=0,ready=false,shadowsReduced=false,lastDowngrade=0,exportRequested=false;
    const tier=qualityForWidth(innerWidth);
    let lastStats:SceneStats|undefined;
    const intervals:number[]=[];const renderTimes:number[]=[];const started=performance.now();
    const timeout=setTimeout(()=>fail('The 3D view took too long. The static artwork is available.'),10000);
    function report() {
      if(!lastStats||!active)return;
      const frame=pacing(intervals);
      onMetrics({...lastStats,tier,frames,frameSamples:frame.samples,medianMs:frame.medianMs,p95Ms:frame.p95Ms,fps:frame.fps,readyMs:readyTime,firstFrameMs,renderMs:renderTimes.length?renderTimes.reduce((a,b)=>a+b,0)/renderTimes.length:0,shadowsReduced,settled:!owner.pending});
    }
    let readyTime=0,firstFrameMs=0,grounded=false,replayStage="";
    const owner=frameOwner(requestAnimationFrame,cancelAnimationFrame,time=>{
      if(!active||!handle||document.hidden)return false;
      const dt=previous?time-previous:16.67;
      if(previous&&dt>0) {intervals.push(dt);if(intervals.length>240)intervals.shift();}
      previous=time;
      current+= (target-current)*(1-Math.exp(-Math.min(dt,50)/85));
      if(Math.abs(target-current)<.0005)current=target;
      try {
        const renderStart=performance.now();lastStats=handle.render(current);const cpu=performance.now()-renderStart;if(ready){renderTimes.push(cpu);if(renderTimes.length>240)renderTimes.shift();}else{firstFrameMs=cpu;}frames++;
        element.parentElement?.style.setProperty('--scene-shift',String(clamp(current/.6)));
        const chapter=chapterPresentation(current);
        for(const [id,opacity] of Object.entries(chapter)){landing.style.setProperty(`--${id}-opacity`,String(opacity));landing.style.setProperty(`--${id}-visibility`,opacity>0?'visible':'hidden');}
        if(lastStats.replayStage!==replayStage){replayStage=lastStats.replayStage;report();}
        const nowGrounded=current>=.216;if(nowGrounded!==grounded){grounded=nowGrounded;report();}
        if(lastStats.triangles>(tier==='desktop'?150000:60000)||lastStats.calls>(tier==='desktop'?90:70)||lastStats.textureBytes>(tier==='desktop'?48:24)*1024*1024||lastStats.geometryBytes>(tier==='desktop'?12:6)*1024*1024) { fail('The scene exceeded its resource budget. Static view is available.');return false; }
        if(exportRequested){exportRequested=false;onExport(handle.canvas.toDataURL('image/webp',.92));}
        if(!ready){ready=true;readyTime=performance.now()-started;clearTimeout(timeout);onReady();console.info('[Cheez64 scene]',JSON.stringify({event:'ready',tier,readyMs:Math.round(readyTime),pieces:lastStats.pieces}));}
        if(intervals.length>=45&&frames-lastDowngrade>45&&pacing(intervals.slice(-45)).fps<20){if(!shadowsReduced){handle.degrade();shadowsReduced=true;lastDowngrade=frames;}else{fail('This device is better suited to the static view.');return false;}}
        if(current!==target)return true;
        report();return false;
      }catch {fail('The 3D view is unavailable. The static artwork remains usable.');return false;}
    });
    function progress(){const bounds=story.getBoundingClientRect();const top=stage.getBoundingClientRect().top;target=clamp((Math.max(0,top)-bounds.top)/Math.max(1,story.getBoundingClientRect().height-stage.getBoundingClientRect().height));if(target>.999)target=1;if(target<.001)target=0;}
    function testLoss(){handle?.canvas.getContext('webgl2')?.getExtension('WEBGL_lose_context')?.loseContext();}
    function exportArt(){exportRequested=true;owner.wake();}
    function scroll(){progress();if(!owner.pending)previous=0;owner.wake();}
    function resize(){if(!handle)return;const nextTier=qualityForWidth(innerWidth);if(nextTier!==tier){owner.pause();return;}handle.resize(element.clientWidth,element.clientHeight,devicePixelRatio||1);progress();previous=0;owner.wake();}
    function visibility(){previous=0;if(document.hidden){owner.pause();}else{progress();owner.wake();}}
    function lost(event:Event){event.preventDefault();fail('The graphics context was lost. Static view is available.');}
    function teardown(){
      if(!active)return;active=false;clearTimeout(timeout);owner.dispose();
      element.removeEventListener('cheez64-context-loss',testLoss);element.removeEventListener('cheez64-export',exportArt);window.removeEventListener('scroll',scroll);window.removeEventListener('resize',resize);document.removeEventListener('visibilitychange',visibility);observer.disconnect();
      handle?.canvas.removeEventListener('webglcontextlost',lost);handle?.dispose();
      console.info('[Cheez64 scene]',JSON.stringify({event:'disposed',pendingFrame:owner.pending,canvasRemoved:!element.querySelector('canvas'),resourcesDisposed:true,frames}));
    }
    function fail(message:string){teardown();onFailure(message);}
    const observer=new ResizeObserver(()=>resize());
    void import('./scene/create-scene').then(({createScene})=>{
      if(!active)return;
      try {
        handle=createScene(element,tier);if(!active){handle.dispose();return;}
        handle.canvas.addEventListener('webglcontextlost',lost);element.addEventListener('cheez64-export',exportArt);element.addEventListener('cheez64-context-loss',testLoss);
        window.addEventListener('scroll',scroll,{passive:true});window.addEventListener('resize',resize);document.addEventListener('visibilitychange',visibility);observer.observe(element);
        handle.resize(element.clientWidth,element.clientHeight,devicePixelRatio||1);progress();current=target;owner.wake();
      }catch{fail('WebGL2 could not start. The static artwork is available.');}
    }).catch(()=>{if(active)fail('The 3D module could not load. Static view is available.');});
    return teardown;
  },[onReady,onFailure,onMetrics,onExport]);
  return <div ref={host} className="cz-canvas-host" aria-hidden="true"/>;
}
