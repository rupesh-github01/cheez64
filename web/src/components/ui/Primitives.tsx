import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react';
import s from './primitives.module.css';
const classes=(...values:(string|undefined)[])=>values.filter(Boolean).join(' ');
export function Button({variant='secondary',busy=false,className,disabled,children,type='button',...props}:ButtonHTMLAttributes<HTMLButtonElement>&{variant?:'primary'|'secondary'|'quiet';busy?:boolean}) {
  return <button {...props} type={type} disabled={disabled||busy} aria-busy={busy||undefined} className={classes(s.button,s[variant],className)}>{children}</button>;
}
export function Panel({className,children,...props}:HTMLAttributes<HTMLElement>) {
  return <section {...props} className={classes(s.panel,className)}>{children}</section>;
}
export function PageHeading({description,children,className,...props}:HTMLAttributes<HTMLHeadingElement>&{description?:ReactNode}) {
  return <div className={s.pageHeading}><h1 {...props} className={className}>{children}</h1>{description&&<p>{description}</p>}</div>;
}
export function SectionHeading({className,...props}:HTMLAttributes<HTMLHeadingElement>) {
  return <h2 {...props} className={classes(s.sectionHeading,className)}/>;
}
export function Status({tone='neutral',className,...props}:HTMLAttributes<HTMLSpanElement>&{tone?:'neutral'|'success'|'warning'|'danger'}) {
  return <span {...props} className={classes(s.status,s[tone],className)}/>;
}
/** Navigation tabs are normal links, not an ARIA tablist with missing arrow-key behavior. */
export function Tabs({label,children,className}: {label:string;children:ReactNode;className?:string}) {
  return <nav aria-label={label} className={classes(s.tabs,className)}>{children}</nav>;
}
export function EmptyState({children,className,...props}:HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={classes(s.empty,className)}>{children}</div>;
}
export function LoadingState({label='Loading…',className}: {label?:string;className?:string}) {
  return <div className={classes(s.loading,className)} role="status" aria-label={label}><span/><span/><span/></div>;
}
