'use client';
import type { ReactNode } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Status, Tabs } from '../ui/Primitives';
import '../ui/workspace-tokens.css';
import { WORKSPACE_LINKS, activeRoute, immersiveRoute, workspaceSection } from './routes';
import s from './shell.module.css';
function Icon({kind}:{kind:string}) {
  const paths:Record<string,ReactNode>={
    board:<><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M12 4v16M4 12h16"/></>,
    chart:<><path d="M4 5v15h16M8 15v-4m5 4V7m5 8v-6"/></>,
    target:<><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/></>,
    book:<><path d="M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14"/></>,
    settings:<><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2"/><circle cx="15" cy="17" r="2"/></>,
    home:<><path d="m5 12 7-7 7 7M7 10v10h10V10"/></>,
  };
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[kind]}</svg>;
}
export default function AppShell({children}:{children:ReactNode}) {
  const pathname=usePathname();
  if(immersiveRoute(pathname))return <main>{children}</main>;
  return <div data-workspace-shell className={s.shell}>
    <a className={s.skip} href="#workspace-content">Skip to content</a>
    <header className={s.navigation}>
      <Link className={s.brand} href="/" prefetch={false} aria-label="Cheez64 homepage"><svg viewBox="0 0 24 24" width="25" height="25" fill="none" aria-hidden="true"><path d="M18 5H5v14h14v-5M18 5v3" stroke="currentColor" strokeWidth="2.7"/></svg><span>Cheez64</span></Link>
      <p className={s.kicker}>PERSONAL CHESS INTELLIGENCE</p>
      <Tabs label="Workspace navigation" className={s.primaryNav}>{WORKSPACE_LINKS.map(link=><Link key={link.href} href={link.href} prefetch={false} aria-current={activeRoute(pathname,link.href)?'page':undefined}><Icon kind={link.icon}/><span>{link.label}</span></Link>)}</Tabs>
      <nav className={s.utilities} aria-label="Workspace utilities"><Link href="/library/manage" prefetch={false} aria-current={activeRoute(pathname,'/library/manage')?'page':undefined}><Icon kind="settings"/><span>Manage library</span></Link><Link href="/" prefetch={false}><Icon kind="home"/><span>Homepage</span></Link></nav>
      <p className={s.note}>Observe. Question.<br/><em>Understand.</em></p>
    </header>
    <div className={s.frame}><div className={s.context}><p><span>WORKSPACE</span><i aria-hidden="true">/</i>{workspaceSection(pathname)}</p><Status>Personal workspace</Status></div><main id="workspace-content" tabIndex={-1} className={s.main}>{children}</main></div>
  </div>;
}
