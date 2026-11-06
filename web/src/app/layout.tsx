import Link from "next/link";
import Script from "next/script";
import { CINEMATIC_PREPAINT } from "./design-lab/cinematic/prepaint";
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cheez64",
  description:
    "Evidence-based chess game review with move-level analysis, tactical detection, and coaching explanations.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <Script id="cheez64-prepaint" strategy="beforeInteractive" dangerouslySetInnerHTML={{ __html: CINEMATIC_PREPAINT }} />
        <header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "12px 24px",
            borderBottom: "1px solid var(--border-color)",
            background: "var(--bg-secondary)",
          }}
        >
          <Link
            href="/"
            aria-label="Cheez64 homepage"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "10px",
              textDecoration: "none",
              color: "var(--text-primary)",
            }}
          >
            <span style={{ fontSize: "1.3rem" }}>♟</span>
            <span style={{ fontWeight: 700, fontSize: "1rem", letterSpacing: "-0.02em" }}>
              Cheez64
            </span>
          </Link>
          <nav style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
            <Link href="/study" style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>Study</Link>
            <Link href="/practice" style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>Practice</Link>
            <Link
              href="/dashboard"
              style={{
                color: "var(--text-secondary)",
                textDecoration: "none",
                fontSize: "0.85rem",
                fontWeight: 500,
                transition: "color 0.15s ease",
              }}
            >
              Dashboard
            </Link>
            <Link
              href="/games"
              style={{
                color: "var(--text-secondary)",
                textDecoration: "none",
                fontSize: "0.85rem",
                fontWeight: 500,
                transition: "color 0.15s ease",
              }}
            >
              Game Library
            </Link>
            <Link
              href="/library/manage"
              style={{
                color: "var(--text-secondary)",
                textDecoration: "none",
                fontSize: "0.85rem",
                fontWeight: 500,
                transition: "color 0.15s ease",
              }}
            >
              Manage
            </Link>
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
