import type { ReactNode } from "react";
import type { Metric } from "@/lib/player-dashboard";
import { metricText } from "@/lib/player-dashboard";
export function Panel({ title, kicker, children, id }: { title: string; kicker?: string; children: ReactNode; id?: string }) {
  return <section className="pd-panel" id={id}><header className="pd-panel-heading"><div>{kicker && <p className="pd-kicker">{kicker}</p>}<h2>{title}</h2></div></header>{children}</section>;
}
export function State({ loading, error, empty, retry }: { loading?: boolean; error?: string; empty?: string; retry?: () => void }) {
  if (loading) return <div className="pd-skeleton" role="status" aria-label="Loading section"><span /><span /><span /></div>;
  return <div className={error ? "pd-state pd-error" : "pd-state"} role={error ? "alert" : undefined}><p>{error || empty}</p>{retry && <button onClick={retry}>Try again</button>}</div>;
}
export function Evidence({ value }: { value: string }) { return <span className={`pd-evidence ${value === "HEURISTIC" ? "pd-heuristic" : ""}`}>{value.replaceAll("_", " ").toLowerCase()}</span>; }
export function Fact({ title, metric }: { title: string; metric?: Metric }) {
  return <div className="pd-fact"><div className="pd-split"><h3>{title}</h3><Evidence value={metric?.provenance || "UNAVAILABLE"} /></div><strong>{metricText(metric)}</strong>{metric && <><p>{metric.numerator != null && metric.denominator != null ? `${metric.numerator} / ${metric.denominator} observations · ` : ""}{metric.sample?.games ?? 0} games · {metric.status.toLowerCase().replaceAll("_", " ")}</p><details><summary>Method & evidence</summary><p>{metric.method}</p><p>{metric.sample?.preliminary ? "Small sample: descriptive only. " : ""}{metric.limitations?.join(" ")}</p></details></>}</div>;
}
