import type { Metadata } from "next";
import CinematicLanding, { type LandingSearchParams } from "./design-lab/cinematic/CinematicLanding";

export const metadata: Metadata = {
  title: "Cheez64 — See beyond the move.",
  description: "Your personal chess workspace. Review games, explore ideas, build studies and practise with purpose.",
};

export default function Home({ searchParams }: { searchParams: LandingSearchParams }) {
  return CinematicLanding({ searchParams, production: true });
}
