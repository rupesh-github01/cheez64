import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Personal Chess Coach",
  description:
    "Evidence-based chess game review with move-level analysis, tactical detection, and coaching explanations.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
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
          <a
            href="/games"
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
              Personal Chess Coach
            </span>
          </a>
          <nav style={{ display: "flex", gap: "16px" }}>
            <a
              href="/games"
              style={{
                color: "var(--text-secondary)",
                textDecoration: "none",
                fontSize: "0.85rem",
                fontWeight: 500,
              }}
            >
              Game Library
            </a>
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
