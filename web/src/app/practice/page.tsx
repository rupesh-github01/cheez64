import { Suspense } from "react";
import PracticeLibrary from "@/components/Practice/PracticeLibrary";
export default function PracticePage() {
  return <Suspense fallback={<p>Loading practice…</p>}><PracticeLibrary /></Suspense>;
}
