"use client";
import { useEffect, useState } from "react";
import { practiceRead } from "@/lib/practice";
export function usePractice<T>(path: string | null, revision = 0) {
  const key = JSON.stringify([path, revision]);
  const [result, setResult] = useState<{ key: string; data?: T; error?: string }>();
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    practiceRead<T>(path, controller.signal).then(data => { if (!controller.signal.aborted) setResult({ key, data }); }).catch(error => { if (!controller.signal.aborted) setResult({ key, error: String(error.message) }); });
    return () => controller.abort();
  }, [path, key]);
  return { data: result?.key === key ? result.data : undefined, error: result?.key === key ? result.error : undefined, loading: !!path && result?.key !== key };
}
