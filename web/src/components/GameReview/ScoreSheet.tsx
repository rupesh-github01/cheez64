'use client';
import {useEffect,useRef} from 'react';
import type {CSSProperties} from 'react';
import s from './review.module.css';
export type ScoreEntry={ply:number;number:number;color:'White'|'Black';san:string;title?:string;critical?:boolean;accent?:string;episode?:boolean};
export default function ScoreSheet({entries,currentPly,onSelectMove}:{entries:ScoreEntry[];currentPly:number;onSelectMove:(ply:number)=>void}){
  const list=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const box=list.current,active=box?.querySelector<HTMLElement>('[aria-current="step"]');
    if(box&&active){const b=box.getBoundingClientRect(),a=active.getBoundingClientRect();if(a.top<b.top)box.scrollTop-=b.top-a.top;else if(a.bottom>b.bottom)box.scrollTop+=a.bottom-b.bottom;}
  },[currentPly]);
  const rows=new Map<number,{White?:ScoreEntry;Black?:ScoreEntry}>();
  for(const entry of entries){if(!rows.has(entry.number))rows.set(entry.number,{});rows.get(entry.number)![entry.color]=entry;}
  return <div className={s.scoreSheet} ref={list} aria-label="Recorded moves">
    {!entries.length&&<p className={s.muted}>No moves match the current filter.</p>}
    {[...rows].sort((a,b)=>a[0]-b[0]).map(([number,pair])=><div className={s.scoreRow} key={number}><span className={s.moveNumber}>{number}.</span>{(['White','Black'] as const).map(color=>{const move=pair[color];return move?<button type="button" key={color} aria-current={currentPly===move.ply?'step':undefined} aria-label={`${number}${color==='White'?'.':'…'} ${move.san}`} title={move.title} onClick={()=>onSelectMove(move.ply)} data-critical={!!move.critical} data-episode={!!move.episode} style={{'--move-accent':move.accent} as CSSProperties}>{move.san}{move.critical&&<span className={s.criticalDot} aria-label="critical position"/>}</button>:<span key={color} className={s.missingMove}>—</span>;})}</div>)}
  </div>;
}
