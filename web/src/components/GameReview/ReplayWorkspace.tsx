'use client';
import {useEffect,useState} from 'react';
import dynamic from 'next/dynamic';
import {usePGNReplay} from '@/components/PlayerDashboard/usePGNReplay';
import {selectedPly} from '@/components/PlayerDashboard/pgn-context';
import {Panel,LoadingState,EmptyState,SectionHeading} from '@/components/ui/Primitives';
import PracticeLink from '@/components/Practice/PracticeLink';
import ChessboardPane from './ChessboardPane';
import PositionEvaluation from './PositionEvaluation';
import ScoreSheet from './ScoreSheet';
import {isReviewTypingTarget,replayEntries} from './review-navigation';
import s from './review.module.css';
const Understanding=dynamic(()=>import('@/components/Understanding/UnderstandingPanel'),{ssr:false});
const Reasoning=dynamic(()=>import('@/components/Reasoning/ReasoningPanel'),{ssr:false});
export default function ReplayWorkspace({pgn,gameID,initialPly=0}:{pgn:string;gameID:string;initialPly?:number}){
  const {replay,error}=usePGNReplay(pgn);
  const [choice,setChoice]=useState<{pgn:string;initial:number;ply:number}>();
  const [barTarget,setBarTarget]=useState<HTMLDivElement|null>(null);
  const ply=replay?selectedPly(choice?.pgn===pgn&&choice.initial===initialPly?choice.ply:initialPly,replay.moves.length):0;
  const count=replay?.moves.length??0;
  function go(ply:number){setChoice({pgn,initial:initialPly,ply});}
  useEffect(()=>{
    function key(event:KeyboardEvent){if(isReviewTypingTarget(event.target)||event.altKey||event.ctrlKey||event.metaKey)return;const next=event.key==='ArrowRight'?Math.min(count,ply+1):event.key==='ArrowLeft'?Math.max(0,ply-1):event.key==='Home'?0:event.key==='End'?count:null;if(next!==null){event.preventDefault();setChoice({pgn,initial:initialPly,ply:next});}}
    window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);
  },[ply,count,pgn,initialPly]);
  if(!replay)return <Panel>{error?<EmptyState role="alert">{error}</EmptyState>:<LoadingState label="Loading legal game history"/>}</Panel>;
  return <><div className={s.primary}>
    <div className={s.boardColumn}><ChessboardPane fen={replay.fens[ply]} currentMove={null} lastMove={replay.moves[ply-1]} currentPly={ply} maxPly={count} evaluationRailRef={setBarTarget} onStart={()=>go(0)} onPrev={()=>go(Math.max(0,ply-1))} onNext={()=>go(Math.min(count,ply+1))} onEnd={()=>go(count)}/>
      <p className={s.positionLabel}>{ply?`After ${replay.sans[ply-1]}`:'Initial position'} · {replay.fens[ply].split(' ')[1]==='w'?'White':'Black'} to move</p>
      <details className={s.technical}><summary>Position & history details</summary><p>{replay.root_history_complete?'Complete history from the standard initial position.':'Setup root: preceding history is unverified; trusted lookup is unavailable.'}</p><code>{replay.fens[ply]}</code></details>
    </div>
    <div className={s.analysisColumn}><Panel className={s.movesPanel}><div className={s.panelHeading}><SectionHeading>Score sheet</SectionHeading><span>{count} recorded plies</span></div><button className={s.initialMove} aria-current={ply===0?'step':undefined} onClick={()=>go(0)}>Initial position</button><ScoreSheet entries={replayEntries(replay)} currentPly={ply} onSelectMove={go}/></Panel><PositionEvaluation pgn={pgn} replay={replay} ply={ply} barTarget={barTarget}/></div>
  </div><section className={s.insights} aria-label="Position insights"><div className={s.insightHeading}><div><p className={s.kicker}>EXAMINE THE POSITION</p><h2>From moves to ideas.</h2></div><PracticeLink gameID={gameID} pgn={pgn} ply={ply}/></div><Understanding gameID={gameID} pgn={pgn} ply={ply}/><details className={s.reasoningDisclosure}><summary>Compare continuations · existing reasoning evidence</summary><Reasoning gameID={gameID} pgn={pgn} ply={ply}/></details></section></>;
}
