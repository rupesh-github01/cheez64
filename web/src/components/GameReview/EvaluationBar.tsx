"use client";

interface Props {
  eval_cp: number; // centipawns, positive = white advantage
}

const MATE_THRESHOLD = 90000;

export default function EvaluationBar({ eval_cp }: Props) {
  const isMate = Math.abs(eval_cp) >= MATE_THRESHOLD;
  const mateMoves = isMate ? Math.max(1, 100000 - Math.abs(eval_cp)) : 0;

  // Percentage calculation
  const whitePct = (() => {
    if (isMate) {
      return eval_cp > 0 ? 100 : 0;
    }
    // Sigmoidal winning chance mapping:
    // 0 cp -> 50%
    // +100 cp -> ~59%
    // +200 cp -> ~68%
    // +500 cp -> ~87%
    // +1000 cp -> ~96%
    const winChance = 2 / (1 + Math.exp(-0.00368208 * eval_cp)) - 1;
    return Math.max(4, Math.min(96, 50 + winChance * 50));
  })();

  const evalText = (() => {
    if (isMate) {
      return eval_cp > 0 ? `+M${mateMoves}` : `-M${mateMoves}`;
    }
    const pawns = eval_cp / 100;
    if (pawns >= 0) return `+${pawns.toFixed(1)}`;
    return pawns.toFixed(1);
  })();

  // Positive eval -> text at bottom over white fill (dark text)
  // Negative eval -> text at top over black background (light text)
  const isWhiteAdvantage = eval_cp >= 0;

  return (
    <div
      className="eval-bar"
      style={{ height: 520, position: "relative" }}
      title={`Evaluation: ${evalText} (${eval_cp > 0 ? "White" : eval_cp < 0 ? "Black" : "Equal"})`}
    >
      <div
        className="eval-bar-fill"
        style={{
          height: `${whitePct}%`,
          transition: "height 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: "50%",
          ...(isWhiteAdvantage ? { bottom: 10 } : { top: 10 }),
          transform: "translateX(-50%) rotate(-90deg)",
          transformOrigin: "center center",
          fontSize: "0.62rem",
          fontWeight: 800,
          fontFamily: "var(--font-geist-mono), monospace",
          color: isWhiteAdvantage ? "#18181b" : "#f4f4f5",
          whiteSpace: "nowrap",
          pointerEvents: "none",
          userSelect: "none",
        }}
      >
        {evalText}
      </div>
    </div>
  );
}
