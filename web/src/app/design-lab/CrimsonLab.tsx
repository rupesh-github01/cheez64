"use client";

import { useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import { Chess } from "chess.js";
import { review, reviewMoves, studyLines, studyPositions, variations } from "./fixtures";
import s from "./crimson.module.css";

const LabBoard = dynamic(() => import("./LabBoard"), { ssr: false, loading: () => <div className={s.boardLoading} role="status">Setting the board…</div> });
type Screen = "home" | "review" | "study";
type IconName = "home" | "board" | "book" | "arrow" | "chevron" | "back" | "first" | "last" | "flip" | "check" | "edit" | "eye" | "spark" | "menu";
function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, React.ReactNode> = {
    home: <><path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/></>,
    board: <><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 12h16M12 4v16M4 8h16M8 4v16M16 4v16M4 16h16"/></>,
    book: <><path d="M12 5c-4-3-9-1-9-1v15s5-2 9 1c4-3 9-1 9-1V4s-5-2-9 1Zm0 0v15"/></>,
    arrow: <><path d="M4 12h15m-6-6 6 6-6 6"/></>,
    chevron: <path d="m9 5 7 7-7 7"/>, back: <path d="m15 5-7 7 7 7"/>,
    first: <><path d="M5 5v14m13-14-8 7 8 7"/></>, last: <><path d="M19 5v14M6 5l8 7-8 7"/></>,
    flip: <><path d="m4 8 4-4 4 4M8 4v13m12-1-4 4-4-4m4 4V7"/></>,
    check: <path d="m5 12 4 4L19 6"/>, edit: <><path d="m4 16 12-12 4 4L8 20H4Zm9-9 4 4"/></>,
    eye: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></>,
    spark: <><path d="m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6Z"/></>,
    menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
function KnightArt() {
  return <svg className={s.knightArt} viewBox="0 0 480 500" fill="none" aria-hidden="true">
    <defs>
      <linearGradient id="cg-metal" x1="115" y1="70" x2="345" y2="410" gradientUnits="userSpaceOnUse"><stop stopColor="#f1c8b4"/><stop offset=".22" stopColor="#84514d"/><stop offset=".47" stopColor="#352124"/><stop offset=".72" stopColor="#171315"/><stop offset="1" stopColor="#7a3c3e"/></linearGradient>
      <linearGradient id="cg-edge"><stop stopColor="#bd7770"/><stop offset="1" stopColor="#30191e"/></linearGradient>
      <radialGradient id="cg-halo"><stop stopColor="#a42c3e" stopOpacity=".32"/><stop offset="1" stopColor="#4e1523" stopOpacity="0"/></radialGradient>
      <filter id="cg-shadow"><feGaussianBlur stdDeviation="12"/></filter>
    </defs>
    <ellipse cx="258" cy="273" rx="215" ry="220" fill="url(#cg-halo)"/>
    <g opacity=".14" stroke="#c18483"><circle cx="265" cy="262" r="170"/><circle cx="265" cy="262" r="190" strokeDasharray="2 9"/><path d="M65 262h400M265 62v400"/></g>
    <ellipse cx="262" cy="442" rx="108" ry="16" fill="#000" filter="url(#cg-shadow)"/>
    <g transform="rotate(-8 265 265)">
      <path d="M193 376c6-37 25-60 29-92l-39-19-36 10-18-23 25-39 26-24 19-37 14-43 22 15 35-16c64 19 93 67 82 125-7 39-37 79-35 143Z" fill="url(#cg-metal)" stroke="url(#cg-edge)" strokeWidth="2"/>
      <path d="m213 109 5 49-38 31-26 24m81-89 22 38c52 17 57 63 27 110-23 37-27 71-22 104" stroke="#e9bba9" strokeOpacity=".35" strokeWidth="2"/>
      <path d="m180 189 31-5 15 17-37 30-42 9m42-9 32 18m21-77-6 40-33 28" stroke="#0e0c10" strokeWidth="4"/>
      <path d="m279 145 28 29-10 6 25 24-13 9 18 24-15 10 7 27-17 4" stroke="#a86560" strokeOpacity=".48" strokeWidth="3"/>
      <ellipse cx="205" cy="200" rx="5" ry="4" fill="#f2c4a8"/><circle cx="157" cy="247" r="3" fill="#0b0b0e"/>
      <path d="M188 376h140l13 18H176Z" fill="url(#cg-metal)" stroke="url(#cg-edge)"/>
      <path d="M177 394h163v20H177Z" fill="#342025" stroke="url(#cg-edge)"/>
      <path d="m177 414-15 15v13h194v-13l-16-15" fill="url(#cg-metal)" stroke="url(#cg-edge)"/>
      <path d="M166 429h185M181 397h154" stroke="#c98978" strokeOpacity=".5"/>
    </g>
    <path d="M365 112h45m-22-22v45" stroke="#bc7775" opacity=".5"/>
    <text x="355" y="376" fill="#bd8c87" fontFamily="monospace" fontSize="10" letterSpacing="3">N / 03</text>
  </svg>;
}
function Pill({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "sage" | "amber" | "crimson" }) {
  return <span className={`${s.pill} ${s[tone]}`}>{children}</span>;
}
function SectionTitle({ number, title, link, href = "/design-lab?screen=review" }: { number: string; title: string; link?: string; href?: string }) {
  return <div className={s.sectionTitle}><h2><span>{number}</span>{title}</h2>{link && <Link href={href}>{link}<Icon name="arrow" size={16}/></Link>}</div>;
}
function Dashboard() {
  return <>
    <div className={s.pageHeading}><div><p className={s.eyebrow}>YOUR PERSONAL CHESS ATELIER</p><h1>A clearer view of your game.</h1></div><span className={s.dateLabel}>FRIDAY, 06 NOVEMBER <span>Illustrative workspace</span></span></div>
    <section className={s.hero} aria-labelledby="hero-title">
      <div className={s.heroCopy}><span className={s.heroKicker}><span/> THE ART OF PAYING ATTENTION</span><h2 id="hero-title">Every move<br/>has a <em>story.</em></h2><p>Return to the positions that stayed with you.<br className={s.desktopBreak}/> Find the ideas behind the moves.</p><Link className={s.primaryButton} href="/design-lab?screen=review">Explore your last game <Icon name="arrow" size={18}/></Link><span className={s.heroFoot}>01 / GAME INTELLIGENCE <span>OBSERVE. QUESTION. UNDERSTAND.</span></span></div>
      <KnightArt/>
    </section>
    <div className={s.overviewStrip} aria-label="Illustrative collection counts"><div><strong>24<span>games</span></strong><p>A small, considered collection</p></div><div><strong>03<span>studies</span></strong><p>Ideas worth returning to</p></div><div><strong>08<span>practice positions</span></strong><p>From the fixture collection</p></div><div className={s.sampleNote}><span className={s.smallDot}/><p>DESIGN SPECIMEN<br/><span>Sample counts, not player metrics</span></p></div></div>
    <div className={s.dashboardColumns}>
      <section><SectionTitle number="01" title="Back to the board" link="Open review"/>
        <div className={s.gameList}>{[
          ["W", "Alex Morgan", "vs. River Knight", "Ruy López", "13 moves · Rapid", "Review"],
          ["B", "Alex Morgan", "vs. North Bishop", "Italian Game", "24 moves · Rapid", "Browse"],
          ["W", "Alex Morgan", "vs. Cedar Rook", "Queen’s Gambit", "31 moves · Classical", "Browse"],
        ].map((row, i) => <Link className={s.gameRow} href="/design-lab?screen=review" key={row[2]}><span className={`${s.colorTile} ${i === 1 ? s.blackTile : ""}`}>{row[0]}</span><div className={s.gamePlayers}><strong>{row[1]} <span>{row[2]}</span></strong><small>{row[3]} <span>· {row[4]}</span></small></div><span className={s.gameAction}>{row[5]}<Icon name="arrow" size={17}/></span></Link>)}</div>
        <p className={s.quietCaption}>Illustrative games · no imported personal data or performance claims</p>
      </section>
      <section className={s.resumeSection}><SectionTitle number="02" title="Keep an idea alive"/>
        <Link href="/design-lab?screen=study" className={s.studyFeature}><div><Pill>YOUR STUDY</Pill><Icon name="arrow" size={18}/></div><h3>The Spanish<br/><em>notebook.</em></h3><p>Closed structures · 2 illustrative lines</p><span className={s.resumeLink}>Continue the central break <Icon name="chevron" size={14}/></span><div className={s.miniGrid} aria-hidden="true">{Array.from({ length: 24 }, (_, i) => <i key={i}/>)}</div></Link>
        <div className={s.practicePrompt}><Icon name="spark"/><p><strong>Make room for practice.</strong><span>Practice styling is outside this three-screen exploration.</span></p><button disabled aria-label="Practice screen not included in prototype"><Icon name="arrow"/></button></div>
      </section>
    </div>
  </>;
}
function MoveList({ moves, selected, onSelect }: { moves: string[]; selected: number; onSelect: (ply: number) => void }) {
  return <div className={s.moveList} aria-label="Recorded moves">{Array.from({ length: Math.ceil(moves.length / 2) }, (_, i) => <div className={s.movePair} key={i}><span>{i + 1}.</span>{moves.slice(i * 2, i * 2 + 2).map((san, j) => { const ply = i * 2 + j + 1; return <button key={ply} aria-label={`Go to ply ${ply}: ${san}`} aria-current={selected === ply ? "step" : undefined} className={selected === ply ? s.selectedMove : ""} onClick={() => onSelect(ply)}>{san}{ply === 19 && <span className={s.moveDot}/>}</button>; })}</div>)}</div>;
}
function BoardStage({ fen, lastMove, ply, count, onSelect, label = "Mainline", evaluation = false }: { fen: string; lastMove?: { from: string; to: string }; ply: number; count: number; onSelect: (ply: number) => void; label?: string; evaluation?: boolean }) {
  const [flipped, setFlipped] = useState(false);
  const turn = fen.split(" ")[1] === "w" ? "White" : "Black";
  return <div className={s.boardStage}>
    <div className={s.playerLine}><span className={`${s.playerAvatar} ${s.darkAvatar}`}>RK</span><strong>{flipped ? "Alex Morgan" : "River Knight"}<small>{flipped ? "White" : "Black"} · Fixture player</small></strong><span className={s.clock}>08:42</span></div>
    <div className={s.boardAndEval}>{evaluation && <div className={s.evalBar} aria-label="Illustrative White-relative score: plus 0.32 pawns; not engine evidence"><span className={s.evalBlack}/><span className={s.evalWhite}/><strong>+.32</strong></div>}{!evaluation && label === "Mainline" && <div className={s.evalUnavailable} aria-label="Evaluation unavailable">—</div>}<div className={s.boardFrame} data-board-fen={fen}><LabBoard fen={fen} lastMove={lastMove} flipped={flipped}/></div></div>
    <div className={s.playerLine}><span className={s.playerAvatar}>AM</span><strong>{flipped ? "River Knight" : "Alex Morgan"}<small>{flipped ? "Black" : "White"} · Fixture player</small></strong><span className={s.clock}>09:16</span></div>
    <div className={s.boardToolbar}><span>{label} <b>{ply}/{count}</b></span><div><button disabled={ply === 0} onClick={() => onSelect(0)} aria-label="First position"><Icon name="first" size={18}/></button><button disabled={ply === 0} onClick={() => onSelect(ply - 1)} aria-label="Previous move"><Icon name="back" size={18}/></button><button disabled={ply === count} onClick={() => onSelect(ply + 1)} aria-label="Next move"><Icon name="chevron" size={18}/></button><button disabled={ply === count} onClick={() => onSelect(count)} aria-label="Last position"><Icon name="last" size={18}/></button></div><button onClick={() => setFlipped(!flipped)} aria-label="Flip board" aria-pressed={flipped}><Icon name="flip" size={18}/></button></div>
    <p className={s.boardCaption}><span className={s.smallDot}/>{turn} to move <span>Legal fixture replay · clocks illustrative</span></p>
  </div>;
}
function Review() {
  const [ply, setPly] = useState(18);
  const [panel, setPanel] = useState("Analysis");
  const [preview, setPreview] = useState<number | null>(null);
  const showEvaluation = ply === 18 && preview === null;
  let fen = review.positions[ply];
  if (preview !== null) { const game = new Chess(review.positions[18]); for (const move of variations[preview].moves) game.move(move); fen = game.fen(); }
  function select(next: number) { setPly(next); setPreview(null); }
  return <>
    <div className={s.workspaceHeading}><div><p className={s.eyebrow}>GAME REVIEW <span>/ FIXTURE NO. 01</span></p><h1>The Spanish, revisited.</h1></div><Pill tone="sage">Legal position replay</Pill></div>
    <div className={s.gameMeta}><span><span className={s.smallDot}/> Ruy López · Closed variation</span><span>Rapid · 10 + 0 <i/> Illustrative game</span><span className={s.fixtureTag}>NO ENGINE EXECUTION</span></div>
    <div className={s.reviewGrid}>
      <BoardStage fen={fen} lastMove={preview === null ? review.history[ply - 1] : undefined} ply={ply} count={reviewMoves.length} onSelect={select} evaluation={showEvaluation} label={preview === null ? "Mainline" : "Variation preview"}/>
      <aside className={s.reviewRail}>
        <div className={s.evaluationPanel}><div className={s.panelHeading}><h2>Position evaluation</h2><Pill tone={showEvaluation ? "amber" : "neutral"}>{showEvaluation ? "Illustrative" : "Unavailable"}</Pill></div>
          {showEvaluation ? <><div className={s.scoreHeader}><strong>+0.32 <small>WHITE</small></strong><span>3 sample lines <i/> depth 22*</span></div><div className={s.pvList}>{variations.map((line, i) => <button key={line.score} onClick={() => setPreview(i)} aria-label={`Preview illustrative variation ${i + 1}`}><span>{i + 1}</span><b>{line.score}</b><span>{line.moves.map((san, j) => `${j % 2 === 0 ? `${10 + j / 2}. ` : ""}${san}`).join(" ")}</span><Icon name="chevron" size={13}/></button>)}</div><p className={s.evidenceNote}>*Scores and depth are layout specimens, not engine results. Continuations are legal. No evaluation source is connected.</p></> : <div className={s.unavailable}><span>—</span><div><strong>{preview !== null ? "Previewing a legal continuation" : "No evidence for this position"}</strong><p>{preview !== null ? "The recorded game is unchanged." : "Unavailable does not mean equal. No engine or cloud is connected."}</p><button onClick={() => { select(18); }}>Return to evaluation specimen <Icon name="arrow" size={14}/></button></div></div>}
        </div>
        <div className={s.notationPanel}><div className={s.panelHeading}><h2>The game</h2><span className={s.muted}>13 moves</span></div><MoveList moves={reviewMoves} selected={ply} onSelect={select}/></div>
        <section className={s.insightPanel}><div className={s.panelTabs} aria-label="Review panels">{["Analysis", "Reasoning", "Coach"].map(tab => <button key={tab} aria-pressed={panel === tab} onClick={() => setPanel(tab)}>{tab}</button>)}</div>
          {panel === "Analysis" && <div className={s.insightContent}><Pill tone="sage">Board fact</Pill><h3>{ply >= 16 ? "Both kings have castled." : "Read the position first."}</h3><p>{ply >= 16 ? "The castling moves are present in the legal mainline. That is an observation, not a claim about king safety." : "This board is reconstructed from the selected legal moves. No engine judgment is attached to it."}</p><details><summary>Evidence & coverage</summary><p>Legal fixture history only. The three display lines at ply 18 have illustrative scores; trusted engine evidence is unavailable.</p></details></div>}
          {panel === "Reasoning" && <div className={s.insightContent}><Pill tone="amber">Heuristic question</Pill><h3>What changes if the centre opens?</h3><p>Look at the c- and d-pawns, then trace the bishops’ diagonals. Compare candidate moves before deciding on a plan.</p><p className={s.evidenceNote}>A study prompt, not a best-move or forced-win claim. No compatible search evidence.</p></div>}
          {panel === "Coach" && <div className={s.insightContent}><Pill>Unassessed</Pill><h3>Pause before your next move.</h3><p>What is your opponent threatening? Name two candidate moves and one response to each.</p><p className={s.evidenceNote}>Illustrative coaching copy. No player-strength assessment or personalized calculation.</p></div>}
        </section>
      </aside>
    </div>
  </>;
}
function Study() {
  const [lineIndex, setLineIndex] = useState(0);
  const [ply, setPly] = useState(studyLines[0].selected);
  const [mode, setMode] = useState("Read");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(true);
  const line = studyLines[lineIndex];
  const position = studyPositions[lineIndex];
  const key = `${line.id}-${ply}`;
  const note = notes[key] ?? (ply === line.selected ? line.note : `Position after ${ply === 0 ? "the starting position" : `${Math.ceil(ply / 2)}${ply % 2 ? "." : "…"} ${line.moves[ply - 1]}`}\n\nNo note has been written for this move. Switch to Edit to add a local prototype note.`);
  const [treeOpen, setTreeOpen] = useState(false);
  function switchLine(index: number) { setLineIndex(index); setPly(studyLines[index].selected); setTreeOpen(false); }
  const tree = <><p className={s.treeLabel}>STUDY COLLECTION</p><div className={s.treeRoot}><Icon name="book" size={17}/><strong>Opening notebooks</strong></div><div className={s.treeBranch}><span>01</span> Open games</div><div className={s.treeLines}>{studyLines.map((item, i) => <button key={item.id} aria-current={lineIndex === i ? "page" : undefined} onClick={() => switchLine(i)}><span className={s.lineMark}/><span>{item.title}<small>{item.moves.length} plies</small></span></button>)}</div><div className={s.treeFooter}>A place for ideas,<br/><em>not just variations.</em></div></>;
  return <>
    <div className={s.workspaceHeading}><div><p className={s.eyebrow}>STUDY <span>/ OPEN GAMES / LINE {String(lineIndex + 1).padStart(2, "0")}</span></p><h1>The opening notebooks.</h1></div><span className={s.saveStatus} role="status"><Icon name={saved ? "check" : "edit"} size={16}/>{saved ? "Saved in this session" : "Unsaved local note"}</span></div>
    <div className={s.studyMeta}><button className={s.mobileLinePicker} aria-expanded={treeOpen} onClick={() => setTreeOpen(!treeOpen)}><Icon name="book" size={16}/>{line.title}<Icon name="menu" size={16}/></button><span>{line.opening}</span><Pill>LOCAL FIXTURE</Pill></div>
    {treeOpen && <nav className={s.mobileTree} aria-label="Study lines">{tree}</nav>}
    <div className={s.studyGrid}>
      <nav className={s.studyTree} aria-label="Study hierarchy">{tree}</nav>
      <div className={s.studyBoardColumn}><BoardStage fen={position.positions[ply]} lastMove={position.history[ply - 1]} ply={ply} count={line.moves.length} onSelect={setPly} label="Study line"/><div className={s.studyNotation}><div className={s.panelHeading}><h2>{line.title}</h2><span className={s.muted}>Recorded line</span></div><MoveList moves={line.moves} selected={ply} onSelect={setPly}/></div></div>
      <aside className={s.notesPanel}><div className={s.notesHeading}><span className={s.eyebrow}>MOVE NOTE</span><div className={s.modeSwitch} aria-label="Note mode">{["Read", "Edit"].map(m => <button key={m} aria-pressed={mode === m} onClick={() => setMode(m)}><Icon name={m === "Read" ? "eye" : "edit"} size={13}/>{m}</button>)}</div></div><h2>{ply === 0 ? "Starting position" : `${Math.ceil(ply / 2)}${ply % 2 ? "." : "…"} ${line.moves[ply - 1]}`}<span>{line.title}</span></h2><div className={s.noteRule}/>{mode === "Read" ? <div className={s.noteProse}>{note.split("\n\n").map((p, i) => <p key={i}>{p}</p>)}</div> : <><label className={s.srOnly} htmlFor="study-note">Note for the selected move</label><textarea id="study-note" className={s.noteInput} value={note} onChange={e => { setNotes({ ...notes, [key]: e.target.value }); setSaved(false); }}/><button className={s.primaryButton} onClick={() => setSaved(true)} disabled={saved}><Icon name="check" size={16}/>{saved ? "Saved locally" : "Save local note"}</button></>}<div className={s.noteFooter}><Icon name="book" size={16}/><p>Notes belong to this move.<span>Session only · no database writes</span></p></div></aside>
    </div>
  </>;
}
export default function CrimsonLab() {
  const params = useSearchParams();
  const requested = params.get("screen");
  const screen: Screen = requested === "review" || requested === "study" ? requested : "home";
  const items: { id: Screen; label: string; icon: IconName; short: string }[] = [{ id: "home", label: "Overview", icon: "home", short: "Home" }, { id: "review", label: "Game review", icon: "board", short: "Review" }, { id: "study", label: "Study workspace", icon: "book", short: "Study" }];
  return <div className={`${s.lab} ${screen === "home" ? s.cinematic : s.obsidian}`}>
    <a href="#cg-content" className={s.skipLink}>Skip to workspace</a>
    <aside className={s.sidebar}><Link className={s.brand} href="/design-lab"><span className={s.brandMark}>♜</span><span>CHESS<span>INTELLIGENCE</span></span></Link><span className={s.navLabel}>YOUR WORKSPACE</span><nav aria-label="Design prototype">{items.map(item => <Link key={item.id} href={`/design-lab?screen=${item.id}`} aria-current={screen === item.id ? "page" : undefined} title={item.label}><Icon name={item.icon}/><span>{item.label}</span>{screen === item.id && <i/>}</Link>)}</nav><div className={s.sidebarBottom}><span className={s.edition}>CRIMSON GLASS <b>EXPLORATION / 01</b></span><div className={s.profile}><span>AM</span><div>Alex Morgan<small>Illustrative profile</small></div></div></div></aside>
    <div className={s.mainShell}><header className={s.topbar}><span className={s.breadcrumb}>Workspace <Icon name="chevron" size={12}/> <b>{screen === "home" ? "Overview" : screen === "review" ? "Game review" : "Study editor"}</b></span><span className={s.prototypeBadge}><span/> DESIGN LAB <span className={s.prototypeExtra}>· FIXTURE DATA ONLY</span></span><span className={s.mobileMonogram}>♜</span></header><div id="cg-content" tabIndex={-1} className={s.content}>{screen === "home" ? <Dashboard/> : screen === "review" ? <Review/> : <Study/>}<footer className={s.pageFooter}><span>PERSONAL CHESS INTELLIGENCE</span><span>UI.1 · Isolated design exploration · No live data</span></footer></div></div>
    <nav className={s.mobileNav} aria-label="Mobile prototype navigation">{items.map(item => <Link href={`/design-lab?screen=${item.id}`} key={item.id} aria-current={screen === item.id ? "page" : undefined}><Icon name={item.icon}/><span>{item.short}</span></Link>)}</nav>
  </div>;
}
