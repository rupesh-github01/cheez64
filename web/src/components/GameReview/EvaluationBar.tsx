"use client";
import { scoreText, whiteFill } from "@/lib/position-evaluation";
import type { Score } from "@/lib/position-evaluation";
export default function EvaluationBar({ score, loading = false, tier }: { score?: Score; loading?: boolean; tier?: "LEGACY_ESTIMATE" }) {
  const fill = whiteFill(score);
  return <div className={`position-eval-bar${fill === undefined ? " unknown" : ""}`} role="img" aria-label={`${tier ? tier + " · " : ""}White-relative evaluation: ${loading ? "Loading" : scoreText(score)}`} title="White at bottom; visual score scale, not a win probability">
    <span className="position-eval-black">Black</span>{fill !== undefined && <div className="position-eval-fill" style={{ height: `${fill}%` }} />}<span className="position-eval-value">{loading ? "…" : score ? scoreText(score) : "?"}</span><span className="position-eval-white">White</span>
  </div>;
}
