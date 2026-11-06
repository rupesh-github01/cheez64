"use client";
import type {MoveData} from '@/lib/types';
import {classificationColor} from '@/lib/types';
import ScoreSheet from './ScoreSheet';
export default function MoveList({moves,currentPly,onSelectMove,highlightEpisode}:{moves:MoveData[];currentPly:number;onSelectMove:(ply:number)=>void;highlightEpisode:number|null}){
  return <ScoreSheet entries={moves.map(move=>({ply:move.ply,number:move.move_number,color:move.color,san:move.played_move,title:`${move.played_move} — ${move.classification} (${move.centipawn_loss} cp)`,critical:move.is_critical,accent:classificationColor(move.classification),episode:highlightEpisode!==null&&move.episode_index===highlightEpisode}))} currentPly={currentPly} onSelectMove={onSelectMove}/>;
}
