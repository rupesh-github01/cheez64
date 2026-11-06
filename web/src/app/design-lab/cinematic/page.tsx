import type { Metadata } from 'next';
import CinematicPrototype from './CinematicPrototype';
export const metadata:Metadata={title:'Cheez64 — See beyond the move.',description:'An isolated cinematic chess prototype. Original procedural artwork and illustrative content.',robots:{index:false,follow:false}};
// Request-time rendering keeps the full static experience in HTML, even with JS disabled.
export default async function CinematicPage({searchParams}:{searchParams:Promise<Record<string,string|string[]|undefined>>}){
  const params=await searchParams;
  return <><script dangerouslySetInnerHTML={{__html:`(()=>{try{document.documentElement.dataset.cheez64Cinematic=${params.view==='static'||params.motion==='reduce'?'false':"!matchMedia('(prefers-reduced-motion: reduce)').matches&&!navigator.connection?.saveData&&!(innerHeight<680||(innerWidth<=700&&innerHeight<740))"}?"on":"off"}catch{}})()`}}/><CinematicPrototype initialStatic={params.view==='static'} forceReduced={params.motion==='reduce'} exportMode={params.artwork==='1'} diagnostics={params.diagnostics==='1'||params.artwork==='1'}/></>;
}
