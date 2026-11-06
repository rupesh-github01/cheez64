import * as T from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import type { PieceKind } from '../fixtures';

// Original procedural set. Dimensions are board-square units, not commissioned assets.
function lathe(profile: number[][], segments: number) {
  return new T.LatheGeometry(profile.map(([r, y]) => new T.Vector2(r, y)), segments);
}
function merge(parts: T.BufferGeometry[]) {
  const flat = parts.map(part => part.index ? part.toNonIndexed() : part.clone());
  const result = mergeGeometries(flat)!;
  flat.forEach(p => p.dispose()); parts.forEach(p => p.dispose());
  result.computeBoundingSphere(); return result;
}
function sphere(radius: number, x: number, y: number, z: number, n: number) {
  return new T.SphereGeometry(radius, n, Math.max(6, n / 2)).translate(x, y, z);
}
const base = [[0, .015], [.29, .015], [.34, .035], [.36, .07], [.36, .105], [.34, .135], [.29, .16], [.28, .19], [.29, .205], [.29, .23], [.25, .255]];
export function pieceGeometry(kind: PieceKind, low: boolean): T.BufferGeometry {
  const n = low ? 20 : 36;
  const body = (rest: number[][]) => lathe([...base, ...rest], n);
  if (kind === 'p') return merge([body([[.21,.28],[.14,.34],[.11,.44],[.12,.48],[.18,.50],[.18,.54],[.12,.56],[0,.58]]), sphere(.16,0,.70,0,n)]);
  if (kind === 'r') {
    const parts: T.BufferGeometry[] = [body([[.22,.29],[.18,.41],[.17,.78],[.2,.86],[.28,.89],[.29,.94],[.25,.98],[.25,1.08],[.18,1.08],[.18,.99],[0,.99]])];
    for(let i=0;i<6;i++) { const a=i*Math.PI/3; const g=new RoundedBoxGeometry(.145,.155,.145,2,.024); g.rotateY(-a).translate(Math.cos(a)*.215,1.12,Math.sin(a)*.215);parts.push(g); }
    return merge(parts);
  }
  if (kind === 'n') {
    // Sculpted volume: elliptical sections follow a curved spine, not a flat extrusion.
    const spine = new T.CatmullRomCurve3([
      new T.Vector3(0,.29,.025),new T.Vector3(0,.48,.09),new T.Vector3(0,.75,.16),
      new T.Vector3(0,1.02,.13),new T.Vector3(0,1.27,-.015),
      new T.Vector3(0,1.38,-.17),new T.Vector3(0,1.30,-.33),new T.Vector3(0,1.18,-.47),
    ],false,'centripetal');
    const radii=[[.23,.22],[.185,.21],[.14,.22],[.135,.205],[.155,.17],[.14,.13],[.12,.11],[.09,.085]];
    const rows=low?28:44, sides=low?14:24, positions:number[]=[], uvs:number[]=[], indices:number[]=[];
    for(let j=0;j<=rows;j++) {
      const t=j/rows, c=spine.getPoint(t), tangent=spine.getTangent(t);
      const v=t*(radii.length-1), i=Math.min(radii.length-2,Math.floor(v)), f=v-i;
      const rx=T.MathUtils.lerp(radii[i][0],radii[i+1][0],f), rz=T.MathUtils.lerp(radii[i][1],radii[i+1][1],f);
      const normal=new T.Vector3(0,-tangent.z,tangent.y).normalize();
      for(let k=0;k<=sides;k++){const a=k/sides*Math.PI*2;positions.push(Math.cos(a)*rx,c.y+normal.y*Math.sin(a)*rz,c.z+normal.z*Math.sin(a)*rz);uvs.push(k/sides,t);}
    }
    for(let j=0;j<rows;j++)for(let k=0;k<sides;k++){const a=j*(sides+1)+k,b=a+sides+1;indices.push(a,b,a+1,b,b+1,a+1);}
    // Close both end sections without a visible seam in the muzzle.
    for(const row of [0,rows]){const center=positions.length/3,c=spine.getPoint(row/rows);positions.push(c.x,c.y,c.z);uvs.push(.5,.5);for(let k=0;k<sides;k++){const a=row*(sides+1)+k;indices.push(center,...(row===0?[a+1,a]:[a,a+1]));}}
    const skin=new T.BufferGeometry();skin.setAttribute('position',new T.Float32BufferAttribute(positions,3));skin.setAttribute('uv',new T.Float32BufferAttribute(uvs,2));skin.setIndex(indices);skin.computeVertexNormals();
    const parts:T.BufferGeometry[]=[body([[.24,.28],[.225,.32],[.18,.345],[0,.345]]),skin];
    // Two individually curved, tapered ears and a close-fitting mane ridge.
    for(const side of [-1,1]) {
      const ear=new T.Shape();ear.moveTo(-.075,0);ear.bezierCurveTo(-.09,.13,-.045,.24,0,.27);ear.quadraticCurveTo(.10,.13,.06,0);ear.closePath();
      parts.push(new T.ExtrudeGeometry(ear,{depth:.065,bevelEnabled:true,bevelSize:.018,bevelThickness:.018,bevelSegments:2,curveSegments:low?5:8}).translate(side*.092,1.37,-.11));
      const cheek=new T.SphereGeometry(1,low?12:18,low?8:12);cheek.scale(.064,.13,.12).translate(side*.117,1.18,-.10);parts.push(cheek);
    }
    const manePath=new T.CatmullRomCurve3([new T.Vector3(0,.42,.27),new T.Vector3(0,.78,.36),new T.Vector3(0,1.10,.30),new T.Vector3(0,1.39,.045)]);
    parts.push(new T.TubeGeometry(manePath,low?12:20,.039,low?5:7,false));
    return merge(parts);
  }
  if (kind === 'b') {
    const shape = new T.Shape();
    shape.moveTo(0,1.5);shape.quadraticCurveTo(-.28,1.27,-.18,1.1);shape.quadraticCurveTo(0,.97,.18,1.1);
    shape.quadraticCurveTo(.29,1.27,.09,1.43);shape.lineTo(-.015,1.22);shape.lineTo(-.07,1.25);shape.lineTo(.025,1.47);shape.closePath();
    const mitre = new T.ExtrudeGeometry(shape,{depth:.17,bevelEnabled:true,bevelThickness:.045,bevelSize:.025,bevelSegments:2,curveSegments:low?6:10});mitre.translate(0,0,-.085).rotateY(-.25);
    return merge([body([[.22,.29],[.17,.4],[.11,.83],[.15,.91],[.23,.94],[.23,.98],[.15,1.02],[0,1.04]]),mitre]);
  }
  if (kind === 'q') {
    const parts:T.BufferGeometry[]=[body([[.22,.29],[.17,.43],[.105,.9],[.18,1.03],[.25,1.06],[.25,1.1],[.19,1.13],[.19,1.19],[.26,1.36],[.22,1.39],[.16,1.25],[0,1.25]])];
    for(let i=0;i<7;i++){const a=i*Math.PI*2/7;const leaf=new T.SphereGeometry(1,low?8:12,8);leaf.scale(.042,.102,.042).translate(0,1.43,.225).rotateY(a);parts.push(leaf);}
    parts.push(sphere(.065,0,1.46,0,low?10:16));return merge(parts);
  }
  return merge([body([[.22,.29],[.18,.45],[.115,1.0],[.17,1.1],[.23,1.13],[.23,1.18],[.15,1.23],[.14,1.34],[0,1.36]]),new RoundedBoxGeometry(.085,.31,.085,2,.019).translate(0,1.49,0),new RoundedBoxGeometry(.25,.085,.085,2,.019).translate(0,1.52,0)]);
}
export function boardGeometry(low: boolean) { return new RoundedBoxGeometry(8.54,.48,8.54,low?2:3,.085).translate(0,-.29,0); }
