"use client";
import {createPortal} from "react-dom";
import { scoreText, whiteFill } from "@/lib/position-evaluation";
import type { Score } from "@/lib/position-evaluation";
export default function EvaluationBar({ score, loading = false, tier, bound = "exact", target, channel = "cache" }: { score?: Score; loading?: boolean; tier?: "LEGACY_ESTIMATE" | "LIVE_ENGINE_UNVERIFIED" | "BOARD_FACT"; bound?: "exact" | "lower" | "upper"; target?: HTMLElement | null; channel?: "cache" | "live" }) {
  const fill = whiteFill(score);
  const label = `${score && bound !== "exact" ? bound === "lower" ? "≥ " : "≤ " : ""}${scoreText(score)}`;
  const bar = <div className={`position-eval-bar${fill === undefined ? " unknown" : ""}`} role="img" aria-label={`${tier ? tier + " · " : ""}White-relative evaluation: ${loading ? "Loading" : label}`} title="White at bottom; visual score scale, not a win probability">
    <span className="position-eval-black">Black</span>{fill !== undefined && <div className="position-eval-fill" style={{ height: `${fill}%` }} />}<span className="position-eval-value">{loading ? "…" : score ? label : "?"}</span><span className="position-eval-white">White</span>
  </div>;
  return target ? createPortal(<div className="review-eval-portal" data-eval-channel={channel}>{bar}</div>, target) : bar;
}
