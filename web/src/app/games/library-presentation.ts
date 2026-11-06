import type { Game } from '@/lib/types';
export const PAGE_SIZE=100;
export type StatusFilter=''|'ANALYZED'|'NOT_ANALYZED';
export type ResultFilter=''|'1-0'|'0-1'|'1/2-1/2';
export const ANALYSIS_FILTERS=[{value:'',label:'All games'},{value:'ANALYZED',label:'Analyzed'},{value:'NOT_ANALYZED',label:'Not analyzed'}] as const;
export const RESULT_FILTERS=[{value:'',label:'Any result'},{value:'1-0',label:'1–0'},{value:'0-1',label:'0–1'},{value:'1/2-1/2',label:'½–½'}] as const;
export function analysisLabel(value:string):{label:string;tone:'neutral'|'success'|'warning'|'danger';description:string}{
  switch(value){
    case 'ANALYZED':return {label:'Analyzed',tone:'success',description:'Saved game status is analyzed; this does not certify every position.'};
    case 'NOT_ANALYZED':return {label:'Pending',tone:'neutral',description:'Not analyzed. This does not mean a job is queued.'};
    case 'ANALYZING':return {label:'Analyzing',tone:'warning',description:'Saved game status is analyzing.'};
    case 'FAILED':return {label:'Failed',tone:'danger',description:'Saved analysis failed. Open the game for available details.'};
    default:return {label:'Unavailable',tone:'neutral',description:'No recognized game analysis status is available.'};
  }
}
export function gameDate(value:string|null){
  const match=value?.match(/^(\d{4})[.-](\d{2})[.-](\d{2})(?:$|[T\s])/);
  if(!match)return null;
  const year=+match[1],month=+match[2],day=+match[3];
  const leap=year%4===0&&(year%100!==0||year%400===0);
  const days=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
  return year>0&&month>=1&&month<=12&&day>=1&&day<=days[month-1]?`${match[1]}-${match[2]}-${match[3]}`:null;
}
export function gameOpening(game:Game){return {code:game.opening?.eco||game.eco||null,name:game.opening?.full_name||game.opening?.opening_name||game.opening?.variation_name||null};}
export function gameResult(result:string){return ({'1-0':'1–0','0-1':'0–1','1/2-1/2':'½–½','*':'No result'} as Record<string,string>)[result]??'Unavailable';}
export function playerName(name:string){return name?.trim()||'Unknown player';}
export function gameHref(game:Game){return `/games/${encodeURIComponent(game.game_id)}`;}
export function gameLinkLabel(game:Game){return `Review ${playerName(game.white)} versus ${playerName(game.black)} · ${gameDate(game.date)??'date unavailable'}`;}
