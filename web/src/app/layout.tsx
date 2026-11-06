import AppShell from "@/components/AppShell/AppShell";
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
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
