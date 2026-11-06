import type {Replay} from '@/components/PlayerDashboard/pgn-context';
import type {ScoreEntry} from './ScoreSheet';
export function isReviewTypingTarget(target:EventTarget|null){return target instanceof Element&&!!target.closest('input,textarea,select,[contenteditable="true"],[contenteditable=""]');}

export function replayEntries(replay:Replay):ScoreEntry[]{
  const root=replay.root_fen.split(' '),first=Number(root[5])||1,black=root[1]==='b';
  return replay.sans.map((san,i)=>({ply:i+1,number:first+Math.floor((i+(black?1:0))/2),color:(i+(black?1:0))%2?'Black':'White',san}));
}
