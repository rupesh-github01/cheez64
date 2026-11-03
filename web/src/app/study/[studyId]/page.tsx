"use client";
import { useParams } from 'next/navigation';
import { StudyDetail } from '@/components/Study/StudyWorkspace';
export default function Page() { const { studyId } = useParams<{ studyId: string }>(); return <StudyDetail key={studyId} id={studyId} />; }
