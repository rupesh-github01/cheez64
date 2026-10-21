"use client";
import { Suspense } from "react";
import { useParams } from "next/navigation";
import PuzzleSession from "@/components/Practice/PuzzleSession";
export default function PracticePuzzle() {
  const params = useParams<{ puzzle_id: string }>();
  return <Suspense fallback={<p>Loading practice…</p>}><PuzzleSession key={params.puzzle_id} puzzleID={params.puzzle_id} /></Suspense>;
}
