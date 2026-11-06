/** Route-owned editorial fixtures; no analysis/evaluation or persistence connection. */
export const CHAPTERS = [
  {id:'hero',number:'01',label:'The Perspective',progress:0},
  {id:'analyze',number:'02',label:'The Analysis',progress:.34},
  {id:'understand',number:'03',label:'The Understanding',progress:.66},
  {id:'improve',number:'04',label:'The Improvement',progress:1},
] as const;
export const ILLUSTRATIVE_LINE = ['d4','exd4','cxd4'] as const;
