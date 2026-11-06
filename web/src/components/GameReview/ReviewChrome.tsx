import Link from 'next/link';
import type {Game} from '@/lib/types';
import {Status} from '@/components/ui/Primitives';
import {analysisLabel,gameDate,gameOpening,gameResult,playerName} from '@/app/games/library-presentation';
import s from './review.module.css';
export function ReviewHeader({game}:{game:Game}) {
  const opening=gameOpening(game),status=analysisLabel(game.analysis_status);
  return <header className={s.header}>
    <div className={s.breadcrumb}><Link href="/games" prefetch={false}>← Game library</Link><span>GAME REVIEW</span></div>
    <div className={s.titleRow}><h1>{playerName(game.white)} <span>vs</span> {playerName(game.black)}</h1><strong className={s.result}>{gameResult(game.result)}</strong></div>
    <div className={s.metadata}><span>White{validRating(game.white_rating)&&` · ${game.white_rating}`} / Black{validRating(game.black_rating)&&` · ${game.black_rating}`}</span>{gameDate(game.date)&&<time dateTime={gameDate(game.date)!}>{gameDate(game.date)}</time>}<span>{opening.code} {opening.name||'Opening unavailable'}</span><Status tone={status.tone} title={status.description}>{status.label}</Status></div>
  </header>;
}
function validRating(value:number|null){return typeof value==='number'&&Number.isFinite(value)&&value>0;}
