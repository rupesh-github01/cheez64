import CinematicPrototype from './CinematicPrototype';
// Request-time rendering keeps the full static experience in HTML, even with JS disabled.
export type LandingSearchParams = Promise<Record<string,string|string[]|undefined>>;
export default async function CinematicLanding({searchParams,production=false}:{searchParams:LandingSearchParams;production?:boolean}){
  const params=await searchParams;
  return <CinematicPrototype production={production} initialStatic={params.view==='static'} forceReduced={params.motion==='reduce'} exportMode={!production&&params.artwork==='1'} diagnostics={!production&&(params.diagnostics==='1'||params.artwork==='1')}/>;
}
