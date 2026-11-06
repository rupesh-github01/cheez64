export const WORKSPACE_LINKS=[
  {href:'/games',label:'Games',icon:'board'},
  {href:'/dashboard',label:'Dashboard',icon:'chart'},
  {href:'/practice',label:'Practice',icon:'target'},
  {href:'/study',label:'Studies',icon:'book'},
] as const;
export function activeRoute(pathname:string,href:string){return pathname===href||pathname.startsWith(href+'/');}
export function immersiveRoute(pathname:string){return pathname==='/'||activeRoute(pathname,'/design-lab');}
export function workspaceSection(pathname:string){
  if(activeRoute(pathname,'/games')&&pathname!=='/games')return 'Game review';
  if(activeRoute(pathname,'/practice')&&pathname!=='/practice')return 'Practice session';
  if(activeRoute(pathname,'/library/manage'))return 'Library management';
  return WORKSPACE_LINKS.find(link=>activeRoute(pathname,link.href))?.label??'Workspace';
}
