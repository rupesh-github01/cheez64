"use client";
import { useParams } from 'next/navigation';
import { StudyLineDetail } from '@/components/Study/StudyWorkspace';
export default function Page() { const { lineId } = useParams<{ lineId: string }>(); return <StudyLineDetail key={lineId} id={lineId} />; }
