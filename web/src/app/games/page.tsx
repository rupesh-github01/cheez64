'use client';
import {useState} from 'react';
import Link from 'next/link';
import {Button,Panel,PageHeading,SectionHeading,EmptyState,LoadingState} from '@/components/ui/Primitives';
import {GameCollection,Arrow} from './GameCollection';
import {ANALYSIS_FILTERS,RESULT_FILTERS,PAGE_SIZE,type StatusFilter,type ResultFilter} from './library-presentation';
import {useGameLibrary} from './useGameLibrary';
import s from './library.module.css';
export default function GamesPage(){
  const [status,setStatus]=useState<StatusFilter>(''),[result,setResult]=useState<ResultFilter>('');
  const [offset,setOffset]=useState(0),[revision,setRevision]=useState(0);
  const {games,total,loading,error}=useGameLibrary(status,result,offset,revision);
  const filtered=!!(status||result);
  function reset(){setStatus('');setResult('');setOffset(0);}
  const count=loading?'Loading collection…':error?'Count unavailable':`${total?.toLocaleString()} ${total===1?'game':'games'} ${filtered?'matching filters':'in your library'}`;
  return <div className={s.library}>
    <header className={s.heading}><div><p className={s.eyebrow}>YOUR COLLECTION</p><PageHeading description="Return to your games. Find the next idea worth keeping.">Game library.</PageHeading><p className={s.total}>{count}</p></div><Link className={s.importAction} href="/library/manage" prefetch={false}><span className={s.plus} aria-hidden="true">+</span>Import games <Arrow/></Link></header>
    <div className={s.toolbar} aria-label="Filter games"><fieldset className={s.filterGroup}><legend>Analysis</legend><div>{ANALYSIS_FILTERS.map(filter=><button key={filter.value} type="button" aria-pressed={status===filter.value} onClick={()=>{setStatus(filter.value);setOffset(0);}}>{filter.label}</button>)}</div></fieldset><fieldset className={s.filterGroup}><legend>Result</legend><div>{RESULT_FILTERS.map(filter=><button key={filter.value} type="button" aria-pressed={result===filter.value} onClick={()=>{setResult(filter.value);setOffset(0);}}>{filter.label}</button>)}</div></fieldset>{filtered&&<Button variant="quiet" className={s.clear} onClick={reset}>Clear filters</Button>}</div>
    <Panel className={s.collection} aria-label="Game collection" aria-busy={loading}><div className={s.collectionHeader}><SectionHeading>{filtered?'Filtered games':'All games'}</SectionHeading><span aria-live="polite" aria-atomic="true">{loading?'Loading…':error?'Library unavailable':`${games.length} ${games.length===1?'game':'games'} shown`}</span></div>
      {loading?<div className={s.loading}><LoadingState label="Loading games"/><p>Opening your collection…</p></div>:error?<EmptyState className={s.state} role="alert"><span className={s.stateMark} aria-hidden="true">!</span><h3>Your library couldn’t load.</h3><p>Try again to reconnect to your saved games.</p><Button onClick={()=>setRevision(value=>value+1)}>Try again</Button><details><summary>Error details</summary><p>{error}</p></details></EmptyState>:games.length===0?<EmptyState className={s.state}><span className={s.stateMark} aria-hidden="true">♜</span><h3>{offset?'No games on this page.':filtered?'No games match these filters.':'Your library starts here.'}</h3><p>{offset?'The collection may have changed. Return to the first page.':filtered?'Try another result or analysis status to explore your collection.':'Import your games to revisit positions, explore ideas and build your practice.'}</p>{offset?<Button onClick={()=>setOffset(0)}>First page</Button>:filtered?<Button onClick={reset}>Clear filters</Button>:<Link className={s.importAction} href="/library/manage" prefetch={false}>Import your first games <Arrow/></Link>}</EmptyState>:<GameCollection games={games}/>}
      {!loading&&!error&&games.length>0&&<footer className={s.collectionFooter}><p>{offset+1}–{Math.min(offset+games.length,total??0)} of {total?.toLocaleString()} {filtered?'matching games':'games'}</p>{(offset>0||(total??0)>PAGE_SIZE)&&<nav aria-label="Game pages"><Button disabled={offset===0} onClick={()=>setOffset(value=>Math.max(0,value-PAGE_SIZE))}>Previous</Button><Button disabled={offset+games.length>=(total??0)} onClick={()=>setOffset(value=>value+PAGE_SIZE)}>Next</Button></nav>}</footer>}
    </Panel>
    <p className={s.note}>Analysis labels reflect saved game status, not position-by-position coverage. Pending means not analyzed; it does not indicate a queued job. <Link href="/library/manage" prefetch={false}>Manage imports &amp; analysis <span aria-hidden="true">↗</span></Link></p>
  </div>;
}
