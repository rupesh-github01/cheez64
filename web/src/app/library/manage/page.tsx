"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import {
  getManagementSummary,
  startImport,
  listImports,
  resumeImport,
  queueAnalysis,
  listAnalysisQueue,
  retryAnalysisJob,
  cancelAnalysisJob,
  retryAllFailedAnalysisJobs,
  getWorkerStatus,
} from "@/lib/api";
import type {
  ManagementSummaryResponse,
  ImportJob,
  AnalysisJob,
  WorkerStatusResponse,
} from "@/lib/types";

export default function LibraryManagePage() {
  // Summary & status state
  const [summary, setSummary] = useState<ManagementSummaryResponse | null>(null);
  const [workerStatus, setWorkerStatus] = useState<WorkerStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Active section tab
  const [activeSection, setActiveSection] = useState<"all" | "imports" | "queue" | "workers">("all");

  // Import form state
  const [platform, setPlatform] = useState<"chess.com" | "lichess">("chess.com");
  const [username, setUsername] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [maxGames, setMaxGames] = useState<string>("");
  const [importSubmitting, setImportSubmitting] = useState(false);

  // Import jobs history
  const [importsList, setImportsList] = useState<ImportJob[]>([]);
  const [importStatusFilter, setImportStatusFilter] = useState<string>("");

  // Analysis Queue state
  const [queueList, setQueueList] = useState<AnalysisJob[]>([]);
  const [queueStatusFilter, setQueueStatusFilter] = useState<string>("");
  const [queuePriority, setQueuePriority] = useState<number>(100);
  const [queueDepth, setQueueDepth] = useState<number>(18);
  const [queueSubmitting, setQueueSubmitting] = useState(false);
  const [retryingAll, setRetryingAll] = useState(false);

  // Polling management
  const pollingRef = useRef<NodeJS.Timeout | null>(null);
  const isVisibleRef = useRef(true);

  const fetchAllData = useCallback(async (isInitial = false) => {
    if (isInitial) setLoading(true);
    try {
      const [sumRes, wrkRes, impRes, qRes] = await Promise.all([
        getManagementSummary(),
        getWorkerStatus(),
        listImports({ status: importStatusFilter || undefined, limit: 15 }),
        listAnalysisQueue({ status: queueStatusFilter || undefined, limit: 25 }),
      ]);
      setSummary(sumRes);
      setWorkerStatus(wrkRes);
      setImportsList(impRes.jobs);
      setQueueList(qRes.jobs);
      setError(null);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load management data";
      setError(msg);
    } finally {
      if (isInitial) setLoading(false);
    }
  }, [importStatusFilter, queueStatusFilter]);

  // Initial fetch and visibility-aware polling
  useEffect(() => {
    fetchAllData(true);

    const handleVisibilityChange = () => {
      isVisibleRef.current = !document.hidden;
      if (!document.hidden) {
        fetchAllData(false);
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);

    const interval = setInterval(() => {
      if (isVisibleRef.current) {
        fetchAllData(false);
      }
    }, 3500);
    pollingRef.current = interval;

    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [fetchAllData]);

  // Clear feedback after 6 seconds
  useEffect(() => {
    if (feedback) {
      const timer = setTimeout(() => setFeedback(null), 6000);
      return () => clearTimeout(timer);
    }
  }, [feedback]);

  // Handlers
  const handleStartImport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim()) {
      setFeedback({ type: "error", message: "Username is required." });
      return;
    }

    setImportSubmitting(true);
    try {
      const res = await startImport({
        platform,
        username: username.trim(),
        since: dateFrom.trim() || undefined,
        until: dateTo.trim() || undefined,
        max_games: maxGames ? parseInt(maxGames, 10) : undefined,
      });
      setFeedback({
        type: "success",
        message: res.message || `Started background import for ${username}`,
      });
      setUsername("");
      fetchAllData(false);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to start import";
      setFeedback({ type: "error", message: msg });
    } finally {
      setImportSubmitting(false);
    }
  };

  const handleResumeImport = async (jobId: string) => {
    try {
      const res = await resumeImport(jobId);
      setFeedback({
        type: "success",
        message: res.message || `Resumed import job ${jobId}`,
      });
      fetchAllData(false);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to resume import";
      setFeedback({ type: "error", message: msg });
    }
  };

  const handleQueueAllUnanalyzed = async () => {
    setQueueSubmitting(true);
    try {
      const res = await queueAnalysis({
        all_unanalyzed: true,
        priority: queuePriority,
        depth: queueDepth,
      });
      setFeedback({
        type: "success",
        message: res.message || `Queued ${res.queued_count} games for analysis!`,
      });
      fetchAllData(false);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to queue games";
      setFeedback({ type: "error", message: msg });
    } finally {
      setQueueSubmitting(false);
    }
  };

  const handleRetryJob = async (jobId: string) => {
    try {
      await retryAnalysisJob(jobId);
      setFeedback({ type: "success", message: `Job ${jobId} set to PENDING` });
      fetchAllData(false);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to retry job";
      setFeedback({ type: "error", message: msg });
    }
  };

  const handleCancelJob = async (jobId: string) => {
    try {
      await cancelAnalysisJob(jobId);
      setFeedback({ type: "success", message: `Job ${jobId} cancelled` });
      fetchAllData(false);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to cancel job";
      setFeedback({ type: "error", message: msg });
    }
  };

  const handleRetryAllFailed = async () => {
    setRetryingAll(true);
    try {
      const res = await retryAllFailedAnalysisJobs();
      setFeedback({
        type: "success",
        message: res.message || `Retried ${res.retried_count} failed jobs`,
      });
      fetchAllData(false);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to retry jobs";
      setFeedback({ type: "error", message: msg });
    } finally {
      setRetryingAll(false);
    }
  };

  // Helper for status badge styling
  const getWorkerBadge = (status?: string) => {
    switch (status) {
      case "online/analyzing":
        return { label: "Online (Analyzing)", bg: "rgba(59, 130, 246, 0.15)", color: "#60a5fa", dot: "#3b82f6" };
      case "online/idle":
        return { label: "Online (Idle)", bg: "rgba(34, 197, 94, 0.15)", color: "#4ade80", dot: "#22c55e" };
      case "stale":
        return { label: "Stale Heartbeat", bg: "rgba(234, 179, 8, 0.15)", color: "#facc15", dot: "#eab308" };
      default:
        return { label: "Offline (Worker not running)", bg: "rgba(239, 68, 68, 0.15)", color: "#f87171", dot: "#ef4444" };
    }
  };

  const getJobStatusBadge = (status: string) => {
    switch (status) {
      case "COMPLETED":
        return { bg: "rgba(34, 197, 94, 0.15)", color: "#4ade80" };
      case "RUNNING":
        return { bg: "rgba(59, 130, 246, 0.15)", color: "#60a5fa" };
      case "FAILED":
        return { bg: "rgba(239, 68, 68, 0.15)", color: "#f87171" };
      case "CANCELLED":
        return { bg: "rgba(107, 114, 128, 0.15)", color: "#9ca3af" };
      default:
        return { bg: "rgba(234, 179, 8, 0.15)", color: "#facc15" };
    }
  };

  const workerBadge = getWorkerBadge(summary?.worker?.status || workerStatus?.status);

  return (
    <div style={{ maxWidth: 1300, margin: "0 auto", padding: "28px 20px" }}>
      {/* Page Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 16,
          marginBottom: 24,
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h1 style={{ fontSize: "1.75rem", fontWeight: 700, letterSpacing: "-0.02em" }}>
              Library Management
            </h1>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                background: workerBadge.bg,
                color: workerBadge.color,
                padding: "3px 10px",
                borderRadius: 12,
                fontSize: "0.75rem",
                fontWeight: 600,
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: workerBadge.dot,
                  display: "inline-block",
                }}
              />
              {workerBadge.label}
            </span>
          </div>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.88rem", marginTop: 4 }}>
            Import historical chess games, orchestrate Stockfish analysis jobs, and supervise background workers.
          </p>
        </div>

        {/* Live sync badge */}
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              background: "var(--bg-secondary)",
              border: "1px solid var(--border-color)",
              padding: "5px 12px",
              borderRadius: 20,
              fontSize: "0.78rem",
              color: "var(--text-muted)",
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "#22c55e",
                display: "inline-block",
                boxShadow: "0 0 6px #22c55e",
              }}
            />
            Live Sync (3.5s)
          </span>
          <button
            onClick={() => fetchAllData(false)}
            className="btn-secondary"
            style={{
              padding: "5px 12px",
              fontSize: "0.78rem",
              borderRadius: 6,
              cursor: "pointer",
              background: "var(--bg-secondary)",
              color: "var(--text-primary)",
              border: "1px solid var(--border-color)",
            }}
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Global Toast / Feedback */}
      {feedback && (
        <div
          style={{
            padding: "12px 18px",
            borderRadius: "var(--radius-sm)",
            marginBottom: 20,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            background:
              feedback.type === "success"
                ? "rgba(34, 197, 94, 0.12)"
                : "rgba(239, 68, 68, 0.12)",
            border: `1px solid ${
              feedback.type === "success"
                ? "rgba(34, 197, 94, 0.3)"
                : "rgba(239, 68, 68, 0.3)"
            }`,
            color: feedback.type === "success" ? "#4ade80" : "#f87171",
            fontSize: "0.88rem",
          }}
        >
          <span>{feedback.message}</span>
          <button
            onClick={() => setFeedback(null)}
            style={{
              background: "transparent",
              border: "none",
              color: "inherit",
              cursor: "pointer",
              fontWeight: 600,
              fontSize: "1rem",
            }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Error banner if network/server issue */}
      {error && (
        <div
          style={{
            padding: "12px 18px",
            borderRadius: "var(--radius-sm)",
            marginBottom: 20,
            background: "rgba(239, 68, 68, 0.1)",
            border: "1px solid var(--accent-red)",
            color: "#f87171",
            fontSize: "0.85rem",
          }}
        >
          ⚠️ {error}
        </div>
      )}

      {/* Overview Cards Row */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
          gap: 16,
          marginBottom: 28,
        }}
      >
        {/* Card 1: Library Games */}
        <div className="card" style={{ padding: "18px 20px" }}>
          <div style={{ color: "var(--text-muted)", fontSize: "0.8rem", fontWeight: 600, textTransform: "uppercase" }}>
            Total Games
          </div>
          <div style={{ fontSize: "1.8rem", fontWeight: 700, margin: "6px 0 4px" }}>
            {summary?.library.total_games ?? "—"}
          </div>
          <div style={{ display: "flex", gap: 12, fontSize: "0.8rem" }}>
            <span style={{ color: "var(--accent-green)" }}>
              ✓ {summary?.library.analyzed_games ?? 0} Analyzed
            </span>
            <span style={{ color: "var(--text-muted)" }}>
              ⏳ {summary?.library.unanalyzed_games ?? 0} Pending
            </span>
          </div>
        </div>

        {/* Card 2: Analysis Queue */}
        <div className="card" style={{ padding: "18px 20px" }}>
          <div style={{ color: "var(--text-muted)", fontSize: "0.8rem", fontWeight: 600, textTransform: "uppercase" }}>
            Analysis Queue
          </div>
          <div style={{ fontSize: "1.8rem", fontWeight: 700, margin: "6px 0 4px" }}>
            {summary?.analysis_queue.total ?? "—"}
          </div>
          <div style={{ display: "flex", gap: 10, fontSize: "0.8rem", flexWrap: "wrap" }}>
            <span style={{ color: "#60a5fa" }}>
              ⚡ {summary?.analysis_queue.running ?? 0} Running
            </span>
            <span style={{ color: "#facc15" }}>
              ⏱ {summary?.analysis_queue.pending ?? 0} Queued
            </span>
            {(summary?.analysis_queue.failed ?? 0) > 0 && (
              <span style={{ color: "#f87171" }}>
                ✗ {summary?.analysis_queue.failed} Failed
              </span>
            )}
          </div>
        </div>

        {/* Card 3: Platform Imports */}
        <div className="card" style={{ padding: "18px 20px" }}>
          <div style={{ color: "var(--text-muted)", fontSize: "0.8rem", fontWeight: 600, textTransform: "uppercase" }}>
            Import Jobs
          </div>
          <div style={{ fontSize: "1.8rem", fontWeight: 700, margin: "6px 0 4px" }}>
            {summary?.imports.total ?? "—"}
          </div>
          <div style={{ display: "flex", gap: 12, fontSize: "0.8rem" }}>
            <span style={{ color: summary?.imports.running ? "#60a5fa" : "var(--text-muted)" }}>
              {summary?.imports.running ? "⚡ 1 Active Import" : "0 Active"}
            </span>
            <span style={{ color: "var(--text-muted)" }}>
              {summary?.imports.completed ?? 0} Completed
            </span>
          </div>
        </div>

        {/* Card 4: Worker Engine */}
        <div className="card" style={{ padding: "18px 20px" }}>
          <div style={{ color: "var(--text-muted)", fontSize: "0.8rem", fontWeight: 600, textTransform: "uppercase" }}>
            Worker Engine
          </div>
          <div style={{ fontSize: "1.8rem", fontWeight: 700, margin: "6px 0 4px", display: "flex", alignItems: "center", gap: 8 }}>
            <span>{summary?.worker.active_worker_count ?? 0}</span>
            <span style={{ fontSize: "0.85rem", fontWeight: 500, color: workerBadge.color }}>
              Active {summary?.worker.active_worker_count === 1 ? "Process" : "Processes"}
            </span>
          </div>
          <div style={{ fontSize: "0.78rem", color: "var(--text-muted)", fontFamily: "monospace" }}>
            {summary?.worker.status === "offline" ? "./run.sh worker" : `Last seen: ${summary?.worker.last_heartbeat ? new Date(summary.worker.last_heartbeat).toLocaleTimeString() : "now"}`}
          </div>
        </div>
      </div>

      {/* Section Navigation Pills */}
      <div
        style={{
          display: "flex",
          gap: 8,
          marginBottom: 24,
          borderBottom: "1px solid var(--border-color)",
          paddingBottom: 12,
        }}
      >
        {[
          { id: "all", label: "Overview & Controls" },
          { id: "imports", label: "Historical Importer" },
          { id: "queue", label: "Analysis Queue" },
          { id: "workers", label: "Worker Supervision" },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveSection(tab.id as any)}
            style={{
              padding: "6px 16px",
              borderRadius: 6,
              border: "none",
              fontSize: "0.85rem",
              fontWeight: 600,
              cursor: "pointer",
              background: activeSection === tab.id ? "var(--bg-active)" : "transparent",
              color: activeSection === tab.id ? "var(--text-primary)" : "var(--text-secondary)",
              transition: "all 0.15s ease",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Main Grid: Form & Queue Controls */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: activeSection === "imports" ? "1fr" : activeSection === "queue" ? "1fr" : "1fr 1fr",
          gap: 24,
          marginBottom: 32,
        }}
      >
        {/* LEFT COLUMN: Importer Form */}
        {(activeSection === "all" || activeSection === "imports") && (
          <div className="card" style={{ padding: 24 }}>
            <h2 style={{ fontSize: "1.15rem", fontWeight: 700, marginBottom: 4 }}>
              📥 Import Platform Games
            </h2>
            <p style={{ color: "var(--text-secondary)", fontSize: "0.82rem", marginBottom: 20 }}>
              Bulk import player game archives into the SQLite library with resumable checkpoints.
            </p>

            <form onSubmit={handleStartImport} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {/* Platform selector */}
              <div>
                <label style={{ display: "block", fontSize: "0.8rem", color: "var(--text-muted)", marginBottom: 6 }}>
                  Platform
                </label>
                <div style={{ display: "flex", gap: 10 }}>
                  {[
                    { id: "chess.com", label: "Chess.com" },
                    { id: "lichess", label: "Lichess" },
                  ].map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setPlatform(p.id as any)}
                      style={{
                        flex: 1,
                        padding: "8px 12px",
                        borderRadius: 6,
                        border: `1px solid ${platform === p.id ? "var(--accent-primary, #6366f1)" : "var(--border-color)"}`,
                        background: platform === p.id ? "rgba(99, 102, 241, 0.12)" : "var(--bg-secondary)",
                        color: platform === p.id ? "var(--text-primary)" : "var(--text-secondary)",
                        fontSize: "0.85rem",
                        fontWeight: 600,
                        cursor: "pointer",
                      }}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Username input */}
              <div>
                <label style={{ display: "block", fontSize: "0.8rem", color: "var(--text-muted)", marginBottom: 6 }}>
                  Platform Username *
                </label>
                <input
                  type="text"
                  placeholder={platform === "chess.com" ? "e.g. MagnusCarlsen" : "e.g. DrNykterstein"}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "9px 12px",
                    borderRadius: 6,
                    border: "1px solid var(--border-color)",
                    background: "var(--bg-secondary)",
                    color: "var(--text-primary)",
                    fontSize: "0.88rem",
                    outline: "none",
                  }}
                />
              </div>

              {/* Date Filters Row */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div>
                  <label style={{ display: "block", fontSize: "0.8rem", color: "var(--text-muted)", marginBottom: 6 }}>
                    Since (YYYY-MM-DD)
                  </label>
                  <input
                    type="date"
                    value={dateFrom}
                    onChange={(e) => setDateFrom(e.target.value)}
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      borderRadius: 6,
                      border: "1px solid var(--border-color)",
                      background: "var(--bg-secondary)",
                      color: "var(--text-primary)",
                      fontSize: "0.82rem",
                      outline: "none",
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "0.8rem", color: "var(--text-muted)", marginBottom: 6 }}>
                    Until (YYYY-MM-DD)
                  </label>
                  <input
                    type="date"
                    value={dateTo}
                    onChange={(e) => setDateTo(e.target.value)}
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      borderRadius: 6,
                      border: "1px solid var(--border-color)",
                      background: "var(--bg-secondary)",
                      color: "var(--text-primary)",
                      fontSize: "0.82rem",
                      outline: "none",
                    }}
                  />
                </div>
              </div>

              {/* Max Games optional */}
              <div>
                <label style={{ display: "block", fontSize: "0.8rem", color: "var(--text-muted)", marginBottom: 6 }}>
                  Max Games Limit (Optional)
                </label>
                <input
                  type="number"
                  placeholder="Leave empty for all available"
                  value={maxGames}
                  onChange={(e) => setMaxGames(e.target.value)}
                  min={1}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    borderRadius: 6,
                    border: "1px solid var(--border-color)",
                    background: "var(--bg-secondary)",
                    color: "var(--text-primary)",
                    fontSize: "0.85rem",
                    outline: "none",
                  }}
                />
              </div>

              {/* Submit Button */}
              <button
                type="submit"
                disabled={importSubmitting || (summary?.imports.running ?? 0) > 0 || !username.trim()}
                className="btn-primary"
                style={{
                  marginTop: 6,
                  padding: "10px 16px",
                  borderRadius: 6,
                  border: "none",
                  fontSize: "0.88rem",
                  fontWeight: 600,
                  cursor: importSubmitting || (summary?.imports.running ?? 0) > 0 || !username.trim() ? "not-allowed" : "pointer",
                  opacity: importSubmitting || (summary?.imports.running ?? 0) > 0 || !username.trim() ? 0.6 : 1,
                }}
              >
                {importSubmitting ? "Starting Import..." : (summary?.imports.running ?? 0) > 0 ? "Import Currently Active" : "Start Background Import"}
              </button>
            </form>

            {/* Active Import Progress Card */}
            {summary?.imports.active_job && (
              <div
                style={{
                  marginTop: 20,
                  padding: "14px 16px",
                  borderRadius: 8,
                  background: "rgba(59, 130, 246, 0.08)",
                  border: "1px solid rgba(59, 130, 246, 0.25)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "#60a5fa" }}>
                    ⚡ Active Import in Progress
                  </span>
                  <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                    {summary.imports.active_job.platform}/{summary.imports.active_job.username}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: "0.8rem" }}>
                  <div>Fetched: <strong>{summary.imports.active_job.games_fetched}</strong></div>
                  <div>Imported: <strong style={{ color: "#4ade80" }}>{summary.imports.active_job.games_imported}</strong></div>
                  <div>Dupes: <strong>{summary.imports.active_job.duplicates_skipped}</strong></div>
                  {summary.imports.active_job.games_failed > 0 && (
                    <div style={{ color: "#f87171" }}>Failed: <strong>{summary.imports.active_job.games_failed}</strong></div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* RIGHT COLUMN: Analysis Queue Controls */}
        {(activeSection === "all" || activeSection === "queue") && (
          <div className="card" style={{ padding: 24 }}>
            <h2 style={{ fontSize: "1.15rem", fontWeight: 700, marginBottom: 4 }}>
              ⚙️ Stockfish Analysis Queue
            </h2>
            <p style={{ color: "var(--text-secondary)", fontSize: "0.82rem", marginBottom: 20 }}>
              Schedule games for server-side Stockfish evaluation. Processed asynchronously by the worker.
            </p>

            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {/* Batch Queue Card */}
              <div
                style={{
                  background: "var(--bg-secondary)",
                  borderRadius: 8,
                  padding: "16px 18px",
                  border: "1px solid var(--border-color)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontSize: "0.9rem", fontWeight: 600 }}>
                    Queue All Unanalyzed
                  </span>
                  <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
                    {summary?.library.unanalyzed_games ?? 0} games pending
                  </span>
                </div>
                <p style={{ fontSize: "0.78rem", color: "var(--text-secondary)", marginBottom: 14 }}>
                  Enqueues all imported games that do not yet have Stockfish evaluations. Deduplicated automatically.
                </p>

                {/* Depth & Priority row */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
                  <div>
                    <label style={{ display: "block", fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4 }}>
                      Evaluation Depth
                    </label>
                    <select
                      value={queueDepth}
                      onChange={(e) => setQueueDepth(parseInt(e.target.value, 10))}
                      style={{
                        width: "100%",
                        padding: "6px 10px",
                        borderRadius: 6,
                        border: "1px solid var(--border-color)",
                        background: "var(--bg-tertiary)",
                        color: "var(--text-primary)",
                        fontSize: "0.82rem",
                        outline: "none",
                      }}
                    >
                      <option value={14}>Depth 14 (Fast)</option>
                      <option value={18}>Depth 18 (Standard Recommended)</option>
                      <option value={20}>Depth 20 (Deep)</option>
                      <option value={22}>Depth 22 (High Precision)</option>
                    </select>
                  </div>

                  <div>
                    <label style={{ display: "block", fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4 }}>
                      Job Priority
                    </label>
                    <select
                      value={queuePriority}
                      onChange={(e) => setQueuePriority(parseInt(e.target.value, 10))}
                      style={{
                        width: "100%",
                        padding: "6px 10px",
                        borderRadius: 6,
                        border: "1px solid var(--border-color)",
                        background: "var(--bg-tertiary)",
                        color: "var(--text-primary)",
                        fontSize: "0.82rem",
                        outline: "none",
                      }}
                    >
                      <option value={100}>Normal Priority (100)</option>
                      <option value={50}>High Priority (50)</option>
                      <option value={10}>Highest Priority (10)</option>
                    </select>
                  </div>
                </div>

                <button
                  onClick={handleQueueAllUnanalyzed}
                  disabled={queueSubmitting || (summary?.library.unanalyzed_games ?? 0) === 0}
                  className="btn-primary"
                  style={{
                    width: "100%",
                    padding: "9px 14px",
                    borderRadius: 6,
                    border: "none",
                    fontSize: "0.85rem",
                    fontWeight: 600,
                    cursor: queueSubmitting || (summary?.library.unanalyzed_games ?? 0) === 0 ? "not-allowed" : "pointer",
                    opacity: queueSubmitting || (summary?.library.unanalyzed_games ?? 0) === 0 ? 0.6 : 1,
                  }}
                >
                  {queueSubmitting ? "Queueing Games..." : `Queue All Unanalyzed (${summary?.library.unanalyzed_games ?? 0})`}
                </button>
              </div>

              {/* Retry All Failed Button */}
              {(summary?.analysis_queue.failed ?? 0) > 0 && (
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "12px 16px",
                    borderRadius: 8,
                    background: "rgba(239, 68, 68, 0.08)",
                    border: "1px solid rgba(239, 68, 68, 0.25)",
                  }}
                >
                  <span style={{ fontSize: "0.82rem", color: "#f87171" }}>
                    <strong>{summary?.analysis_queue.failed}</strong> analysis jobs failed.
                  </span>
                  <button
                    onClick={handleRetryAllFailed}
                    disabled={retryingAll}
                    style={{
                      padding: "6px 12px",
                      borderRadius: 6,
                      border: "none",
                      background: "var(--accent-red)",
                      color: "#fff",
                      fontSize: "0.8rem",
                      fontWeight: 600,
                      cursor: retryingAll ? "default" : "pointer",
                    }}
                  >
                    {retryingAll ? "Retrying..." : "Retry All Failed"}
                  </button>
                </div>
              )}

              {/* Worker Instruction Callout */}
              <div
                style={{
                  background: "var(--bg-secondary)",
                  borderRadius: 8,
                  padding: "14px 16px",
                  border: "1px solid var(--border-color)",
                }}
              >
                <div style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 4 }}>
                  💻 Running the Background Worker
                </div>
                <p style={{ fontSize: "0.78rem", color: "var(--text-secondary)", marginBottom: 8 }}>
                  To analyze queued games with Stockfish, launch the worker script from your project root:
                </p>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    background: "var(--bg-tertiary)",
                    padding: "6px 10px",
                    borderRadius: 4,
                    border: "1px solid var(--border-color)",
                    fontFamily: "monospace",
                    fontSize: "0.82rem",
                    color: "var(--accent-cyan)",
                  }}
                >
                  <code>./run.sh worker</code>
                  <span style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>terminal</span>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* SECTION: Recent Platform Imports Table */}
      {(activeSection === "all" || activeSection === "imports") && (
        <div className="card" style={{ padding: 24, marginBottom: 32 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <div>
              <h2 style={{ fontSize: "1.15rem", fontWeight: 700 }}>
                Recent Platform Imports
              </h2>
              <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem" }}>
                History of platform import runs and checkpoints.
              </p>
            </div>
            {/* Filter pills */}
            <div style={{ display: "flex", gap: 6 }}>
              {["", "running", "completed", "failed"].map((st) => (
                <button
                  key={st}
                  onClick={() => setImportStatusFilter(st)}
                  style={{
                    padding: "4px 10px",
                    borderRadius: 4,
                    border: "1px solid var(--border-color)",
                    background: importStatusFilter === st ? "var(--bg-active)" : "var(--bg-secondary)",
                    color: importStatusFilter === st ? "var(--text-primary)" : "var(--text-muted)",
                    fontSize: "0.75rem",
                    cursor: "pointer",
                  }}
                >
                  {st ? st.charAt(0).toUpperCase() + st.slice(1) : "All"}
                </button>
              ))}
            </div>
          </div>

          {importsList.length === 0 ? (
            <div style={{ padding: "24px 0", textAlign: "center", color: "var(--text-muted)", fontSize: "0.85rem" }}>
              No import jobs recorded yet. Use the form above to import your games.
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border-color)", color: "var(--text-muted)", textAlign: "left" }}>
                    <th style={{ padding: "8px 10px" }}>Job ID</th>
                    <th style={{ padding: "8px 10px" }}>Platform</th>
                    <th style={{ padding: "8px 10px" }}>Username</th>
                    <th style={{ padding: "8px 10px" }}>Status</th>
                    <th style={{ padding: "8px 10px" }}>Imported</th>
                    <th style={{ padding: "8px 10px" }}>Dupes</th>
                    <th style={{ padding: "8px 10px" }}>Started</th>
                    <th style={{ padding: "8px 10px", textAlign: "right" }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {importsList.map((job) => {
                    const badge = getJobStatusBadge(job.status.toUpperCase());
                    return (
                      <tr key={job.job_id} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                        <td style={{ padding: "10px 10px", fontFamily: "monospace", color: "var(--text-muted)" }}>
                          {job.job_id.slice(0, 12)}
                        </td>
                        <td style={{ padding: "10px 10px" }}>{job.platform}</td>
                        <td style={{ padding: "10px 10px", fontWeight: 600 }}>{job.username}</td>
                        <td style={{ padding: "10px 10px" }}>
                          <span
                            style={{
                              background: badge.bg,
                              color: badge.color,
                              padding: "2px 8px",
                              borderRadius: 4,
                              fontSize: "0.72rem",
                              fontWeight: 600,
                            }}
                          >
                            {job.status}
                          </span>
                        </td>
                        <td style={{ padding: "10px 10px", color: "#4ade80", fontWeight: 600 }}>
                          {job.games_imported}
                        </td>
                        <td style={{ padding: "10px 10px", color: "var(--text-muted)" }}>
                          {job.duplicates_skipped}
                        </td>
                        <td style={{ padding: "10px 10px", color: "var(--text-muted)" }}>
                          {new Date(job.started_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                        </td>
                        <td style={{ padding: "10px 10px", textAlign: "right" }}>
                          {job.status === "failed" && (
                            <button
                              onClick={() => handleResumeImport(job.job_id)}
                              style={{
                                padding: "3px 8px",
                                borderRadius: 4,
                                border: "1px solid var(--accent-primary, #6366f1)",
                                background: "rgba(99, 102, 241, 0.15)",
                                color: "var(--text-primary)",
                                fontSize: "0.75rem",
                                cursor: "pointer",
                              }}
                            >
                              Resume
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* SECTION: Analysis Queue Jobs Table */}
      {(activeSection === "all" || activeSection === "queue") && (
        <div className="card" style={{ padding: 24, marginBottom: 32 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <div>
              <h2 style={{ fontSize: "1.15rem", fontWeight: 700 }}>
                Stockfish Analysis Jobs
              </h2>
              <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem" }}>
                Queue of games scheduled for background Stockfish evaluation.
              </p>
            </div>
            {/* Filter pills */}
            <div style={{ display: "flex", gap: 6 }}>
              {["", "PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"].map((st) => (
                <button
                  key={st}
                  onClick={() => setQueueStatusFilter(st)}
                  style={{
                    padding: "4px 10px",
                    borderRadius: 4,
                    border: "1px solid var(--border-color)",
                    background: queueStatusFilter === st ? "var(--bg-active)" : "var(--bg-secondary)",
                    color: queueStatusFilter === st ? "var(--text-primary)" : "var(--text-muted)",
                    fontSize: "0.75rem",
                    cursor: "pointer",
                  }}
                >
                  {st || "All"}
                </button>
              ))}
            </div>
          </div>

          {queueList.length === 0 ? (
            <div style={{ padding: "24px 0", textAlign: "center", color: "var(--text-muted)", fontSize: "0.85rem" }}>
              No analysis jobs matching filter.
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border-color)", color: "var(--text-muted)", textAlign: "left" }}>
                    <th style={{ padding: "8px 10px" }}>Job ID</th>
                    <th style={{ padding: "8px 10px" }}>Game</th>
                    <th style={{ padding: "8px 10px" }}>Priority</th>
                    <th style={{ padding: "8px 10px" }}>Status</th>
                    <th style={{ padding: "8px 10px" }}>Attempts</th>
                    <th style={{ padding: "8px 10px" }}>Created</th>
                    <th style={{ padding: "8px 10px", textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {queueList.map((job) => {
                    const badge = getJobStatusBadge(job.status);
                    return (
                      <tr key={job.job_id} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                        <td style={{ padding: "10px 10px", fontFamily: "monospace", color: "var(--text-muted)" }}>
                          {job.job_id.slice(0, 14)}
                        </td>
                        <td style={{ padding: "10px 10px" }}>
                          <Link
                            href={`/games/${job.game_id}`}
                            style={{
                              color: "var(--accent-primary, #6366f1)",
                              textDecoration: "none",
                              fontFamily: "monospace",
                            }}
                          >
                            {job.game_id.slice(0, 12)} →
                          </Link>
                        </td>
                        <td style={{ padding: "10px 10px" }}>{job.priority}</td>
                        <td style={{ padding: "10px 10px" }}>
                          <span
                            style={{
                              background: badge.bg,
                              color: badge.color,
                              padding: "2px 8px",
                              borderRadius: 4,
                              fontSize: "0.72rem",
                              fontWeight: 600,
                            }}
                          >
                            {job.status}
                          </span>
                        </td>
                        <td style={{ padding: "10px 10px", color: "var(--text-muted)" }}>
                          {job.attempt_count} / {job.max_attempts}
                        </td>
                        <td style={{ padding: "10px 10px", color: "var(--text-muted)" }}>
                          {new Date(job.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                        </td>
                        <td style={{ padding: "10px 10px", textAlign: "right" }}>
                          {job.status === "PENDING" && (
                            <button
                              onClick={() => handleCancelJob(job.job_id)}
                              style={{
                                padding: "2px 8px",
                                borderRadius: 4,
                                border: "1px solid var(--border-color)",
                                background: "transparent",
                                color: "var(--text-muted)",
                                fontSize: "0.72rem",
                                cursor: "pointer",
                              }}
                            >
                              Cancel
                            </button>
                          )}
                          {(job.status === "FAILED" || job.status === "CANCELLED") && (
                            <button
                              onClick={() => handleRetryJob(job.job_id)}
                              style={{
                                padding: "2px 8px",
                                borderRadius: 4,
                                border: "1px solid var(--accent-primary, #6366f1)",
                                background: "rgba(99, 102, 241, 0.12)",
                                color: "var(--text-primary)",
                                fontSize: "0.72rem",
                                cursor: "pointer",
                              }}
                            >
                              Retry
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* SECTION: Worker Supervision & Health */}
      {(activeSection === "all" || activeSection === "workers") && (
        <div className="card" style={{ padding: 24 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <div>
              <h2 style={{ fontSize: "1.15rem", fontWeight: 700 }}>
                Worker Supervision &amp; Heartbeats
              </h2>
              <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem" }}>
                Supervise background Stockfish analyzer processes registered in SQLite.
              </p>
            </div>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                background: workerBadge.bg,
                color: workerBadge.color,
                padding: "4px 10px",
                borderRadius: 12,
                fontSize: "0.75rem",
                fontWeight: 600,
              }}
            >
              {workerBadge.label}
            </span>
          </div>

          {workerStatus?.workers && workerStatus.workers.length > 0 ? (
            <div style={{ overflowX: "auto", marginBottom: 16 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border-color)", color: "var(--text-muted)", textAlign: "left" }}>
                    <th style={{ padding: "8px 10px" }}>Worker ID</th>
                    <th style={{ padding: "8px 10px" }}>PID</th>
                    <th style={{ padding: "8px 10px" }}>Hostname</th>
                    <th style={{ padding: "8px 10px" }}>Active Job</th>
                    <th style={{ padding: "8px 10px" }}>Last Heartbeat</th>
                  </tr>
                </thead>
                <tbody>
                  {workerStatus.workers.map((w) => (
                    <tr key={w.worker_id} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                      <td style={{ padding: "10px 10px", fontFamily: "monospace", fontWeight: 600 }}>
                        {w.worker_id}
                      </td>
                      <td style={{ padding: "10px 10px", color: "var(--text-muted)" }}>
                        {w.pid ?? "—"}
                      </td>
                      <td style={{ padding: "10px 10px", color: "var(--text-muted)" }}>
                        {w.hostname ?? "localhost"}
                      </td>
                      <td style={{ padding: "10px 10px" }}>
                        {w.current_job_id ? (
                          <span style={{ color: "#60a5fa", fontFamily: "monospace" }}>
                            {w.current_job_id.slice(0, 14)}
                          </span>
                        ) : (
                          <span style={{ color: "var(--text-muted)" }}>Idle (Polling)</span>
                        )}
                      </td>
                      <td style={{ padding: "10px 10px", color: "var(--text-muted)" }}>
                        {new Date(w.last_seen_at).toLocaleTimeString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div
              style={{
                padding: "20px",
                borderRadius: 8,
                background: "var(--bg-secondary)",
                border: "1px solid var(--border-color)",
                marginBottom: 16,
                fontSize: "0.85rem",
                color: "var(--text-secondary)",
              }}
            >
              No active worker processes detected. Analysis jobs will remain PENDING in SQLite until a worker is started.
            </div>
          )}

          <div
            style={{
              padding: "14px 16px",
              borderRadius: 8,
              background: "var(--bg-secondary)",
              border: "1px solid var(--border-color)",
            }}
          >
            <div style={{ fontSize: "0.85rem", fontWeight: 600, marginBottom: 4 }}>
              How to Start a Worker Process
            </div>
            <p style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginBottom: 10 }}>
              The analysis worker runs as an independent CLI process. It claims pending jobs from SQLite, runs Stockfish evaluations, and updates the library:
            </p>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                background: "var(--bg-tertiary)",
                padding: "8px 12px",
                borderRadius: 4,
                border: "1px solid var(--border-color)",
                fontFamily: "monospace",
                fontSize: "0.85rem",
                color: "var(--accent-cyan)",
              }}
            >
              <code>./run.sh worker</code>
              <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>or: python3 -m src.analysis_worker</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
