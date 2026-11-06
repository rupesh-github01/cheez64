/** Fixed code for Next's root-layout beforeInteractive script; no user text is interpolated. */
export const CINEMATIC_PREPAINT = `(()=>{try{
  if(!['/','/design-lab/cinematic','/design-lab/cinematic/'].includes(location.pathname))return;
  const params=new URLSearchParams(location.search);
  document.documentElement.dataset.cheez64Cinematic=
    params.get('view')!=='static'&&params.get('motion')!=='reduce'&&
    !matchMedia('(prefers-reduced-motion: reduce)').matches&&!navigator.connection?.saveData&&
    !(innerHeight<680||(innerWidth<=700&&innerHeight<740))?'on':'off';
}catch{}})()`;
