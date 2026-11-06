export type Quality = 'desktop' | 'tablet' | 'mobile';
export const clamp = (v: number, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, Number.isFinite(v) ? v : lo));
const smooth = (a: number, b: number, v: number) => { const x = clamp((v - a) / (b - a)); return x * x * (3 - 2 * x); };
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
export function sampleScene(q: number, tier: Quality) {
  const p = clamp(q/.36) * .4;
  const understanding=smooth(.38,.68,clamp(q)), resolution=smooth(.76,1,clamp(q));
  const approach = smooth(.08, .4, p);
  const settle = 1 - smooth(.12, .24, p);
  return {
    p:clamp(q),
    camera: [lerp(lerp(9.1,6.8,approach)-3.2*understanding,8.1,resolution), lerp(lerp(8.4,9.4,approach)+.8*understanding,9.0,resolution) * (tier === 'mobile' ? 1.22 : tier === 'tablet' ? 1.13 : 1), lerp(lerp(13.3,9.8,approach)+.9*understanding,12.2,resolution)] as [number, number, number],
    target: [lerp(0, .2, approach), lerp(.8, .15, approach), lerp(0, .1, approach)] as [number, number, number],
    yaw: lerp(lerp(-12,-5,approach)+7*understanding,-6,resolution) * Math.PI / 180,
    knightLift: (tier === 'mobile' ? 1.65 : 2.65) * settle,
    bishopLift: (tier === 'mobile' ? .45 : 1.25) * settle,
    rim: lerp(.75, .3, approach),
    settled: p >= .24,
    emphasis:clamp(q)>=.24&&clamp(q)<.80,
  };
}
export function qualityForWidth(width: number): Quality { return width <= 700 ? 'mobile' : width <= 1100 ? 'tablet' : 'desktop'; }
export function drawingScale(width: number, height: number, dpr: number, tier: Quality) {
  const cap = tier === 'desktop' ? 3_000_000 : tier === 'tablet' ? 2_000_000 : 1_000_000;
  return Math.min((Number.isFinite(dpr)&&dpr>0?dpr:1), tier === 'desktop' ? 1.5 : 1.25, Math.sqrt(cap / Math.max(1, width * height)));
}
export function pacing(samples: number[]) {
  const sorted = samples.filter(x => Number.isFinite(x) && x > 0).slice(-240).sort((a, b) => a - b);
  const at = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))] || 0;
  return { samples: sorted.length, medianMs: at(.5), p95Ms: at(.95), fps: at(.5) ? 1000 / at(.5) : 0 };
}

/** Authored offsets are display transforms only; every piece returns to its legal square. */
export function pieceOffset(square:string,color:string,kind:string,q:number,tier:Quality):[number,number,number] {
  const pose=sampleScene(q,tier), weight=1-smooth(.12,.24,clamp(q/.36)*.4);
  if(weight===0)return [0,0,0];
  if(square==='f3'&&color==='w'&&kind==='n')return [.30*weight,pose.knightLift,.20*weight];
  if(square==='c8'&&color==='b'&&kind==='b')return [-.16*weight,pose.bishopLift,-.10*weight];
  if(square==='b3'&&color==='w'&&kind==='b')return [-.20*weight,(tier==='mobile'?.8:1.45)*weight,.12*weight];
  if(square==='f8'&&color==='b'&&kind==='r')return [.18*weight,(tier==='mobile'?.3:.8)*weight,-.08*weight];
  return [0,0,0];
}

export function chapterPresentation(q:number){
  const t=clamp(q);
  return {hero:1-smooth(.19,.25,t),analyze:smooth(.25,.29,t)*(1-smooth(.51,.55,t)),understand:smooth(.55,.59,t)*(1-smooth(.80,.85,t)),improve:smooth(.85,.89,t)};
}
