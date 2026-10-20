import { Suspense } from "react";
import PlayerDashboard from "@/components/PlayerDashboard/PlayerDashboard";
export default function DashboardPage() {
  return <Suspense fallback={<div className="pd-state">Loading player dashboard…</div>}><PlayerDashboard /></Suspense>;
}
