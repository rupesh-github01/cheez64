"use client";

import { useState, useEffect, useRef } from "react";
import type { MoveData, CoachMessage, CoachStatusResponse } from "@/lib/types";
import { classificationColor, classificationLabel } from "@/lib/types";
import { askCoach, getCoachStatus } from "@/lib/api";

interface Props {
  gameId: string;
  currentPly: number;
  currentMove: MoveData | null;
  playerName?: string;
}

export default function CoachChatPanel({
  gameId,
  currentPly,
  currentMove,
  playerName,
}: Props) {
  const [messages, setMessages] = useState<CoachMessage[]>([]);
  const [inputQuestion, setInputQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<CoachStatusResponse | null>(null);
  const [currentHintLevel, setCurrentHintLevel] = useState<number>(0);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevPlyRef = useRef<number>(currentPly);

  // Fetch coach status once on mount
  useEffect(() => {
    getCoachStatus()
      .then((s) => setStatus(s))
      .catch(() => {
        // Fallback default status
        setStatus({
          provider: "deterministic_fallback",
          is_fallback: true,
          is_external_configured: false,
          suggested_questions: [
            "Why was this move a mistake?",
            "Why is the engine's best move better than my move?",
            "Explain this position in simpler terms.",
            "What should I have considered before playing this move?",
            "What recurring mistakes do I make across my games?",
            "What should I work on next?",
          ],
        });
      });
  }, []);

  // Reset hint level when user changes position
  useEffect(() => {
    if (prevPlyRef.current !== currentPly) {
      setCurrentHintLevel(0);
      prevPlyRef.current = currentPly;
    }
  }, [currentPly]);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleSend = async (questionText: string, forcedHintLevel?: number) => {
    if (!questionText.trim() || loading) return;

    setError(null);
    const newMsg: CoachMessage = { role: "user", content: questionText.trim() };
    const updatedHistory = [...messages, newMsg];
    setMessages(updatedHistory);
    setInputQuestion("");
    setLoading(true);

    try {
      const res = await askCoach({
        question: questionText.trim(),
        game_id: gameId,
        ply: currentPly,
        hint_level: forcedHintLevel ?? (currentHintLevel > 0 ? currentHintLevel : undefined),
        messages: messages.slice(-8), // Send recent relevant history
        player: playerName,
      });

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.response },
      ]);
      if (res.hint_level) {
        setCurrentHintLevel(res.hint_level);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to communicate with coach");
    } finally {
      setLoading(false);
    }
  };

  const handleProgressiveHint = () => {
    const nextLevel = Math.min(3, currentHintLevel + 1);
    setCurrentHintLevel(nextLevel);
    let prompt = "Give me a conceptual hint for this position.";
    if (nextLevel === 2) {
      prompt = "Give me a more specific clue about the target or piece to move.";
    } else if (nextLevel === 3) {
      prompt = "Reveal the engine's best move and recommended continuation.";
    }
    handleSend(prompt, nextLevel);
  };

  const handleRetry = () => {
    if (messages.length === 0) return;
    const lastUserMsg = [...messages].reverse().find((m) => m.role === "user");
    if (lastUserMsg) {
      handleSend(lastUserMsg.content);
    }
  };

  // Render markdown with basic formatting (bold, code blocks, bullet points)
  const renderFormatted = (text: string) => {
    const lines = text.split("\n");
    return lines.map((line, idx) => {
      if (line.startsWith("### ")) {
        return (
          <h4
            key={idx}
            style={{
              fontSize: "0.95rem",
              fontWeight: 700,
              color: "var(--accent-cyan)",
              marginTop: 8,
              marginBottom: 4,
            }}
          >
            {line.replace("### ", "")}
          </h4>
        );
      }
      if (line.startsWith("- ") || line.startsWith("* ")) {
        return (
          <div
            key={idx}
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 6,
              marginLeft: 8,
              marginBottom: 4,
            }}
          >
            <span style={{ color: "var(--accent-blue)", fontSize: "0.8rem" }}>•</span>
            <span>{renderInline(line.slice(2))}</span>
          </div>
        );
      }
      if (line.startsWith("> ")) {
        return (
          <div
            key={idx}
            style={{
              borderLeft: "3px solid var(--accent-yellow)",
              paddingLeft: 8,
              fontStyle: "italic",
              color: "var(--text-muted)",
              margin: "6px 0",
            }}
          >
            {renderInline(line.replace("> ", ""))}
          </div>
        );
      }
      if (!line.trim()) {
        return <div key={idx} style={{ height: 6 }} />;
      }
      return (
        <p key={idx} style={{ margin: "3px 0", lineHeight: 1.45 }}>
          {renderInline(line)}
        </p>
      );
    });
  };

  const renderInline = (str: string) => {
    // Regex matching **bold** and `code`
    const parts = str.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
    return parts.map((part, i) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return (
          <strong key={i} style={{ color: "var(--text-primary)" }}>
            {part.slice(2, -2)}
          </strong>
        );
      }
      if (part.startsWith("`") && part.endsWith("`")) {
        return (
          <code
            key={i}
            style={{
              background: "var(--bg-tertiary)",
              color: "var(--accent-cyan)",
              padding: "1px 5px",
              borderRadius: 4,
              fontFamily: "monospace",
              fontSize: "0.85em",
            }}
          >
            {part.slice(1, -1)}
          </code>
        );
      }
      return part;
    });
  };

  const suggestedQuestions = status?.suggested_questions || [
    "Why was this move a mistake?",
    "Why is the engine's best move better than my move?",
    "Explain this position in simpler terms.",
    "What should I have considered before playing this move?",
    "What recurring mistakes do I make across my games?",
    "What should I work on next?",
  ];

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        minHeight: 460,
      }}
    >
      {/* Context & Status Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 8,
          padding: "8px 12px",
          background: "var(--bg-secondary)",
          borderRadius: "var(--radius-sm)",
          border: "1px solid var(--border-color)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: "1rem" }}>💬</span>
          <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>
            {currentMove ? (
              <>
                Move {currentMove.move_number}
                {currentMove.color === "White" ? "." : "..."}{" "}
                <strong style={{ color: "var(--accent-blue)" }}>
                  {currentMove.played_move}
                </strong>
                {currentMove.classification && (
                  <span
                    className={`badge badge-${currentMove.classification}`}
                    style={{ marginLeft: 6, fontSize: "0.7rem", padding: "1px 6px" }}
                  >
                    {classificationLabel(currentMove.classification)}
                  </span>
                )}
                {currentMove.centipawn_loss > 0 && (
                  <span
                    style={{
                      fontSize: "0.75rem",
                      color: "var(--text-muted)",
                      marginLeft: 6,
                    }}
                  >
                    -{currentMove.centipawn_loss} cp
                  </span>
                )}
              </>
            ) : (
              "Starting Position"
            )}
          </span>
        </div>

        {/* Provider badge */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {status && (
            <span
              style={{
                fontSize: "0.7rem",
                padding: "2px 8px",
                borderRadius: 9999,
                fontWeight: 600,
                background: status.is_fallback
                  ? "rgba(234, 179, 8, 0.15)"
                  : "rgba(34, 197, 94, 0.15)",
                color: status.is_fallback
                  ? "var(--accent-yellow)"
                  : "var(--accent-green)",
                border: `1px solid ${
                  status.is_fallback
                    ? "rgba(234, 179, 8, 0.3)"
                    : "rgba(34, 197, 94, 0.3)"
                }`,
              }}
              title={
                status.is_fallback
                  ? "Using deterministic rule-based analysis (offline fallback)"
                  : `Powered by ${status.provider}`
              }
            >
              {status.is_fallback
                ? "🤖 Offline Fallback"
                : `⚡ ${status.provider}`}
            </span>
          )}

          {messages.length > 0 && (
            <button
              onClick={() => {
                setMessages([]);
                setCurrentHintLevel(0);
              }}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--text-muted)",
                fontSize: "0.75rem",
                cursor: "pointer",
                padding: "2px 6px",
              }}
              title="Clear conversation"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Progressive Hint Station */}
      {currentMove && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "8px 12px",
            background: "rgba(59, 130, 246, 0.08)",
            borderRadius: "var(--radius-sm)",
            border: "1px dashed rgba(59, 130, 246, 0.3)",
          }}
        >
          <div>
            <div style={{ fontSize: "0.78rem", fontWeight: 600, color: "var(--accent-blue)" }}>
              {currentHintLevel === 0 && "💡 Progressive Hint Mode"}
              {currentHintLevel === 1 && "💡 Level 1: Conceptual Hint Active"}
              {currentHintLevel === 2 && "🔎 Level 2: Specific Clue Active"}
              {currentHintLevel === 3 && "🔓 Level 3: Best Move Revealed"}
            </div>
            <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
              {currentHintLevel === 0 && "Get a gentle nudge without giving away the move."}
              {currentHintLevel === 1 && "Need more help? Request a specific tactical clue."}
              {currentHintLevel === 2 && "Ready to see the engine's answer? Reveal the best move."}
              {currentHintLevel === 3 && "Full engine move and variation disclosed."}
            </div>
          </div>

          <div style={{ display: "flex", gap: 6 }}>
            {currentHintLevel < 3 ? (
              <button
                className="btn-primary"
                onClick={handleProgressiveHint}
                disabled={loading}
                style={{
                  fontSize: "0.75rem",
                  padding: "4px 10px",
                  cursor: loading ? "not-allowed" : "pointer",
                }}
              >
                {currentHintLevel === 0 && "Request Hint (L1)"}
                {currentHintLevel === 1 && "Specific Clue (L2)"}
                {currentHintLevel === 2 && "Reveal Best Move (L3)"}
              </button>
            ) : (
              <button
                className="btn-secondary"
                onClick={() => setCurrentHintLevel(0)}
                style={{ fontSize: "0.75rem", padding: "4px 8px" }}
              >
                Reset Hint
              </button>
            )}
          </div>
        </div>
      )}

      {/* Suggested Quick Questions Chips */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {suggestedQuestions.map((q, idx) => (
          <button
            key={idx}
            onClick={() => handleSend(q)}
            disabled={loading}
            style={{
              background: "var(--bg-tertiary)",
              border: "1px solid var(--border-color)",
              color: "var(--text-secondary)",
              padding: "4px 10px",
              borderRadius: 9999,
              fontSize: "0.74rem",
              cursor: loading ? "not-allowed" : "pointer",
              transition: "all 0.15s ease",
            }}
            onMouseOver={(e) => {
              e.currentTarget.style.borderColor = "var(--accent-blue)";
              e.currentTarget.style.color = "var(--text-primary)";
            }}
            onMouseOut={(e) => {
              e.currentTarget.style.borderColor = "var(--border-color)";
              e.currentTarget.style.color = "var(--text-secondary)";
            }}
          >
            {q}
          </button>
        ))}
      </div>

      {/* Messages Thread */}
      <div
        style={{
          flex: 1,
          minHeight: 220,
          maxHeight: 360,
          overflowY: "auto",
          padding: 12,
          background: "var(--bg-primary)",
          borderRadius: "var(--radius-sm)",
          border: "1px solid var(--border-color)",
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        {messages.length === 0 && (
          <div
            style={{
              margin: "auto",
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: "0.85rem",
              padding: "20px 0",
            }}
          >
            <div style={{ fontSize: "2rem", marginBottom: 8 }}>♟️</div>
            <p style={{ margin: 0, fontWeight: 600, color: "var(--text-secondary)" }}>
              Ask your chess coach anything about this game
            </p>
            <p style={{ margin: "4px 0 0", fontSize: "0.78rem" }}>
              Click a suggested question above, request a hint, or type your own question.
            </p>
          </div>
        )}

        {messages.map((msg, idx) => {
          const isUser = msg.role === "user";
          return (
            <div
              key={idx}
              style={{
                alignSelf: isUser ? "flex-end" : "flex-start",
                maxWidth: "85%",
                background: isUser
                  ? "rgba(59, 130, 246, 0.2)"
                  : "var(--bg-secondary)",
                border: `1px solid ${
                  isUser ? "rgba(59, 130, 246, 0.4)" : "var(--border-color)"
                }`,
                borderRadius: "var(--radius-md)",
                padding: "8px 12px",
                fontSize: "0.85rem",
              }}
            >
              <div
                style={{
                  fontSize: "0.68rem",
                  color: isUser ? "var(--accent-blue)" : "var(--accent-cyan)",
                  fontWeight: 600,
                  marginBottom: 4,
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                }}
              >
                {isUser ? "You" : "Coach"}
              </div>
              <div>{renderFormatted(msg.content)}</div>
            </div>
          );
        })}

        {loading && (
          <div
            style={{
              alignSelf: "flex-start",
              background: "var(--bg-secondary)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--radius-md)",
              padding: "8px 14px",
              fontSize: "0.8rem",
              color: "var(--text-muted)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <span>Analyzing position...</span>
          </div>
        )}

        {error && (
          <div
            style={{
              padding: 10,
              background: "rgba(239, 68, 68, 0.12)",
              border: "1px solid rgba(239, 68, 68, 0.3)",
              borderRadius: "var(--radius-sm)",
              color: "var(--accent-red)",
              fontSize: "0.8rem",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span>⚠️ {error}</span>
            <button
              onClick={handleRetry}
              style={{
                background: "var(--accent-red)",
                color: "#fff",
                border: "none",
                borderRadius: 4,
                padding: "2px 8px",
                fontSize: "0.75rem",
                cursor: "pointer",
              }}
            >
              Retry
            </button>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Box */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSend(inputQuestion);
        }}
        style={{ display: "flex", gap: 8 }}
      >
        <input
          type="text"
          value={inputQuestion}
          onChange={(e) => setInputQuestion(e.target.value)}
          placeholder="Ask why a move failed, for a hint, or about recurring mistakes..."
          disabled={loading}
          style={{
            flex: 1,
            background: "var(--bg-secondary)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--radius-sm)",
            padding: "8px 12px",
            color: "var(--text-primary)",
            fontSize: "0.85rem",
            outline: "none",
          }}
          onFocus={(e) => (e.target.style.borderColor = "var(--accent-blue)")}
          onBlur={(e) => (e.target.style.borderColor = "var(--border-color)")}
        />
        <button
          type="submit"
          className="btn-primary"
          disabled={loading || !inputQuestion.trim()}
          style={{
            padding: "8px 16px",
            fontSize: "0.85rem",
            cursor: loading || !inputQuestion.trim() ? "not-allowed" : "pointer",
            opacity: loading || !inputQuestion.trim() ? 0.6 : 1,
          }}
        >
          Send
        </button>
      </form>
    </div>
  );
}
