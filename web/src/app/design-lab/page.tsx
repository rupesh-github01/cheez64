import type { Metadata } from "next";
import { Suspense } from "react";
import CrimsonLab from "./CrimsonLab";

export const metadata: Metadata = {
  title: "Crimson Glass · Design exploration",
  description: "Isolated visual prototype. All content is illustrative fixture data.",
  robots: { index: false, follow: false },
};
export default function DesignLabPage() {
  return <Suspense fallback={<p>Opening design exploration…</p>}><CrimsonLab /></Suspense>;
}
