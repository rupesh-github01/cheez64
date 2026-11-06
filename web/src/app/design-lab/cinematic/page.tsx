import type { Metadata } from 'next';
import CinematicLanding, { type LandingSearchParams } from './CinematicLanding';
export const metadata:Metadata={title:'Cheez64 — See beyond the move.',description:'An isolated cinematic chess prototype. Original procedural artwork and illustrative content.',robots:{index:false,follow:false}};
export default function CinematicPage({searchParams}:{searchParams:LandingSearchParams}){
  return CinematicLanding({searchParams});
}
