import { Chess } from 'chess.js';
import { FEN, PIECES, squarePosition } from '../fixtures';
import { ILLUSTRATIVE_LINE } from '../chapters';
import { clamp } from './timeline';

/** Separate synthetic replay. Never mutates recorded HISTORY, FEN or piece identities. */
const board = new Chess(FEN);
const identities = new Map(PIECES.map(p=>[p.square,p.square]));
export const REPLAY = ILLUSTRATIVE_LINE.map((san,index)=>{
  const before=board.fen(), move=board.move(san);
  if(!move)throw new Error('Invalid cinematic fixture');
  const actor=identities.get(move.from)!;
  const victim=identities.get(move.to);
  identities.delete(move.from);identities.set(move.to,actor);
  const start=[.31,.38,.45][index],end=[.35,.42,.49][index];
  return Object.freeze({san,from:move.from,to:move.to,actor,victim,before,after:board.fen(),start,end});
});
export const RESULT_FEN=board.fen();
const ease=(x:number)=>{const t=clamp(x);return t*t*(3-2*t);};
export function sampleIllustration(q:number){
  const pieces=PIECES.map(p=>({id:p.square,square:p.square,position:squarePosition(p.square),scale:1}));
  let fen:string|null=FEN,completed=0,moving=false,label='Recorded position · White to move',stage='recorded';
  for(const step of REPLAY){
    if(q<=step.start)break;
    const actor=pieces.find(p=>p.id===step.actor)!;
    const victim=pieces.find(p=>p.id===step.victim);
    const t=ease((q-step.start)/(step.end-step.start));
    if(q>=step.end){
      actor.square=step.to;actor.position=squarePosition(step.to);if(victim)victim.scale=0;
      completed++;fen=step.after;stage=`settled-${completed}`;
      label=['After 10. d4 · Black to move','After 10…exd4 · White to move','After 11. cxd4 · Black to move'][completed-1];
    }else{
      const from=squarePosition(step.from),to=squarePosition(step.to);
      actor.position=[from[0]+(to[0]-from[0])*t,.20*Math.sin(Math.PI*t),from[2]+(to[2]-from[2])*t];
      if(victim){victim.scale=1-ease((t-.65)/.35);victim.position[1]=-.10*(1-victim.scale);}
      fen=null;moving=true;stage=`moving-${completed+1}`;label=`Illustrative replay · ${['10. d4','10…exd4','11. cxd4'][completed]} in transit`;break;
    }
  }
  return {pieces,fen,completed,moving,label,stage,pieceCount:pieces.filter(p=>p.scale>0).length};
}
