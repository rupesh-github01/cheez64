import * as T from 'three';
import { sampleIllustration } from './illustration';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { PIECES, squarePosition } from '../fixtures';
import { boardGeometry, pieceGeometry } from './geometry';
import { drawingScale, sampleScene, pieceOffset, type Quality } from './timeline';

export type SceneStats = { calls: number; triangles: number; rasterTriangles:number; geometryBytes: number; textureBytes: number; drawingWidth: number; drawingHeight: number; dpr: number; geometries: number; materials: number; pieces: number; knightLift: number; bishopLift: number; progress: number; replayStage:string; completedPlies:number; positionFen:string|null; moving:boolean; positionLabel:string };
export type SceneHandle = { render: (q: number) => SceneStats; resize: (width: number,height: number,dpr: number) => void; dispose: () => void; canvas: HTMLCanvasElement; degrade: () => void };
export function createScene(host: HTMLElement, tier: Quality): SceneHandle {
  const low = tier !== 'desktop';
  const geometries = new Set<T.BufferGeometry>();
  const materials = new Set<T.Material>();
  const instances = new Set<T.InstancedMesh>();
  const textures = new Set<T.Texture>();
  const ownG = <G extends T.BufferGeometry>(g:G):G => { geometries.add(g); return g; };
  const ownM = <M extends T.Material>(m:M):M => { materials.add(m); return m; };
  let renderer: T.WebGLRenderer | undefined;
  let environment: T.WebGLCubeRenderTarget | undefined;
  let disposed = false;
  let width = 1, height = 1, scale = 1;
  const scene = new T.Scene();
  const camera = new T.PerspectiveCamera(tier === 'desktop' ? 34 : tier === 'tablet' ? 40 : 44,1,.1,90);
  const root = new T.Group(); scene.add(root);
  const key = new T.DirectionalLight(0xffeee0,2.7); key.position.set(-5,10,5);key.castShadow=true;
  key.shadow.mapSize.set(low?512:1024,low?512:1024);key.shadow.camera.left=-7;key.shadow.camera.right=7;key.shadow.camera.top=7;key.shadow.camera.bottom=-7;key.shadow.camera.near=.1;key.shadow.camera.far=28;key.shadow.bias=-.0003;key.shadow.normalBias=.025;
  scene.add(key,key.target);
  const rim = new T.DirectionalLight(0xcf344e,2.2);rim.position.set(5,4,-6);scene.add(rim);
  const fill = new T.DirectionalLight(0xe1d4cc,.55);fill.position.set(5,3,7);scene.add(fill);
  function dispose() {
    if(disposed)return;disposed=true;
    instances.forEach(m=>m.dispose());instances.clear();
    geometries.forEach(g=>g.dispose());geometries.clear();materials.forEach(m=>m.dispose());materials.clear();textures.forEach(t=>t.dispose());textures.clear();
    environment?.dispose();key.shadow.dispose();scene.clear();renderer?.dispose();renderer?.forceContextLoss();renderer?.domElement.remove();
  }
  try {
    renderer = new T.WebGLRenderer({alpha:true,antialias:!low,powerPreference:'low-power',preserveDrawingBuffer:false});
    const gl = renderer;
    gl.setClearColor(0x0b0609,0);gl.outputColorSpace=T.SRGBColorSpace;gl.toneMapping=T.ACESFilmicToneMapping;gl.toneMappingExposure=.90;
    gl.shadowMap.enabled=true;gl.shadowMap.type=T.PCFShadowMap;gl.info.autoReset=false;
    gl.domElement.setAttribute('aria-hidden','true');host.append(gl.domElement);
    // Original studio environment, generated locally. No HDRI, texture or model request.
    const studio = new T.Scene();studio.background=new T.Color(.10,.075,.085);
    const tempG = new T.BoxGeometry(1,1,1);
    const tempM: T.MeshBasicMaterial[]=[];
    for(const [x,y,z,sx,sy,sz,intensity] of [[-5,4,1,2,7,3,10],[3,5,2,3,1,5,8],[0,2,-5,6,4,1,3]]) {
      const m = new T.MeshBasicMaterial({color:new T.Color(intensity,intensity*.87,intensity*.76)});tempM.push(m);
      const light = new T.Mesh(tempG,m);light.position.set(x,y,z);light.scale.set(sx,sy,sz);studio.add(light);
    }
    const generator = new T.PMREMGenerator(gl);
    try { environment=generator.fromScene(studio,0,.1,40,{size:256}); }
    finally { generator.dispose();tempG.dispose();tempM.forEach(m=>m.dispose());studio.clear(); }
    scene.environment=environment.texture;scene.environmentIntensity=.6;
    const grainData=new Uint8Array(64*64*4);let seed=64;
    for(let i=0;i<grainData.length;i+=4){seed=(seed*1664525+1013904223)>>>0;const v=220+(seed%35);grainData[i]=grainData[i+1]=grainData[i+2]=v;grainData[i+3]=255;}
    const grain=new T.DataTexture(grainData,64,64);grain.wrapS=grain.wrapT=T.RepeatWrapping;grain.needsUpdate=true;textures.add(grain);
    const ivory=ownM(new T.MeshStandardMaterial({color:0xcbbca4,roughness:.29,metalness:.035,roughnessMap:grain,envMapIntensity:.8}));
    const charcoal=ownM(new T.MeshStandardMaterial({color:0x242229,roughness:.27,metalness:.22,roughnessMap:grain,envMapIntensity:1.15}));
    const stone=ownM(new T.MeshStandardMaterial({color:0x21171e,roughness:.35,metalness:.15}));
    const metal=ownM(new T.MeshStandardMaterial({color:0x796164,metalness:.78,roughness:.34}));
    const body=new T.Mesh(ownG(boardGeometry(low)),stone);body.castShadow=true;body.receiveShadow=true;root.add(body);
    const trim=new T.Mesh(ownG(new RoundedBoxGeometry(8.43,.052,8.43,1,.025).translate(0,-.45,0)),metal);root.add(trim);
    const plinth=new T.Mesh(ownG(new RoundedBoxGeometry(8.57,.11,8.57,1,.035).translate(0,-.53,0)),stone);plinth.castShadow=true;root.add(plinth);
    const tiles=ownG(new RoundedBoxGeometry(.994,.055,.994,1,.008).translate(0,-.0275,0));
    const matrix=new T.Matrix4();
    for(let parity=0;parity<2;parity++) {
      const material=ownM(new T.MeshStandardMaterial({color:parity?0x665b5d:0xc7bdad,roughness:.4,metalness:.045,roughnessMap:grain}));
      const mesh=new T.InstancedMesh(tiles,material,32);instances.add(mesh);let index=0;
      for(let f=0;f<8;f++)for(let r=1;r<=8;r++)if((f+r)%2===parity){matrix.makeTranslation(f-3.5,0,4.5-r);mesh.setMatrixAt(index++,matrix);}
      mesh.receiveShadow=true;root.add(mesh);
    }
    const groups: { mesh:T.InstancedMesh; pieces:typeof PIECES }[]=[];
    for(const kind of ['p','r','n','b','q','k'] as const) {
      const geometry=ownG(pieceGeometry(kind,low));
      for(const color of ['w','b'] as const) {
        const pieces=PIECES.filter(p=>p.kind===kind&&p.color===color);
        const mesh=new T.InstancedMesh(geometry,color==='w'?ivory:charcoal,pieces.length);instances.add(mesh);
        mesh.castShadow=true;mesh.receiveShadow=true;mesh.frustumCulled=false;root.add(mesh);groups.push({mesh,pieces});
      }
    }
    const eyeMaterial=ownM(new T.MeshStandardMaterial({color:0x211611,roughness:.5}));
    const eyes=new T.InstancedMesh(ownG(new T.SphereGeometry(.022,8,6)),eyeMaterial,8);instances.add(eyes);root.add(eyes);
    const shadow=new T.Mesh(ownG(new T.PlaneGeometry(30,30)),ownM(new T.ShadowMaterial({opacity:.17})));shadow.rotation.x=-Math.PI/2;shadow.position.y=-.65;shadow.receiveShadow=true;scene.add(shadow);
    const outlines=new T.Group();root.add(outlines);
    const markMaterial=ownM(new T.MeshBasicMaterial({color:0xb15e72,transparent:true,opacity:.65,depthWrite:false}));
    const ring=ownG(new T.RingGeometry(.37,.405,40).rotateX(-Math.PI/2));
    for(const sq of ['c3','d4']){const m=new T.Mesh(ring,markMaterial);const [x,,z]=squarePosition(sq);m.position.set(x,.012,z);outlines.add(m);}
    const object=new T.Object3D();
    function resize(w:number,h:number,dpr:number) {
      width=Math.max(1,w);height=Math.max(1,h);scale=drawingScale(width,height,dpr,tier);
      gl.setPixelRatio(scale);gl.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();
    }
    function render(q:number):SceneStats {
      if(disposed)throw new Error('Disposed scene cannot render');
      const pose=sampleScene(q,tier),replay=sampleIllustration(q);root.rotation.y=pose.yaw;rim.intensity=pose.rim*3.2;
      for(const {mesh,pieces} of groups) {
        pieces.forEach((piece,index)=>{
          const displayed=replay.pieces.find(p=>p.id===piece.square)!;
          const [x,y,z]=displayed.position;
          const [dx,lift,dz]=pieceOffset(piece.square,piece.color,piece.kind,q,tier);
          object.position.set(x+dx,y+lift,z+dz);object.scale.setScalar(displayed.scale);object.rotation.set(0,piece.color==='b'?Math.PI:0,0);object.updateMatrix();mesh.setMatrixAt(index,object.matrix);
        });mesh.instanceMatrix.needsUpdate=true;
      }
      object.scale.setScalar(1);
      let eye=0;for(const piece of PIECES.filter(p=>p.kind==='n')) {
        const [x,,z]=squarePosition(piece.square);const [dx,lift,dz]=pieceOffset(piece.square,piece.color,piece.kind,q,tier);
        for(const side of [-1,1]){object.position.set(x+dx+side*.151,1.29+lift,z+dz+(piece.color==='w'?-.19:.19));object.rotation.set(0,0,0);object.updateMatrix();eyes.setMatrixAt(eye++,object.matrix);}
      }eyes.instanceMatrix.needsUpdate=true;
      outlines.visible=pose.emphasis;
      const target=new T.Vector3(...pose.target);
      const direction=new T.Vector3(...pose.camera).sub(target).normalize();
      // Preserve authored viewing angles; fit all board corners and lifted silhouettes.
      let distance=new T.Vector3(...pose.camera).distanceTo(target);
      const corners=[];
      for(const x of [-4.38,4.38])for(const z of [-4.38,4.38])corners.push(new T.Vector3(x,-.6,z).applyAxisAngle(new T.Vector3(0,1,0),pose.yaw));
      for(const piece of PIECES){const displayed=replay.pieces.find(p=>p.id===piece.square)!;if(displayed.scale===0)continue;const [x,y,z]=displayed.position,[dx,dy,dz]=pieceOffset(piece.square,piece.color,piece.kind,q,tier);for(const side of [-.38,.38])corners.push(new T.Vector3(x+dx+side,y+dy+(piece.kind==='p'?.88:1.7),z+dz).applyAxisAngle(new T.Vector3(0,1,0),pose.yaw));}
      for(let i=0;i<24;i++){
        camera.position.copy(target).addScaledVector(direction,distance);camera.lookAt(target);camera.updateMatrixWorld();
        const fits=corners.every(v=>{const p=v.clone().project(camera);return Math.abs(p.x)<.94&&Math.abs(p.y)<.88;});
        if(fits)break;distance*=1.035;
      }
      gl.info.reset();gl.render(scene,camera);
      let visibleTriangles=0;scene.traverseVisible(obj=>{const mesh=obj as T.Mesh;if(mesh.isMesh)visibleTriangles+=((mesh.geometry.index?.count||mesh.geometry.attributes.position.count)/3)*((mesh as T.InstancedMesh).isInstancedMesh?(mesh as T.InstancedMesh).count:1);});
      let geometryBytes=0;geometries.forEach(g=>{for(const a of Object.values(g.attributes))geometryBytes+=a.array.byteLength;geometryBytes+=g.index?.array.byteLength||0;});
      return {calls:gl.info.render.calls,triangles:visibleTriangles,rasterTriangles:gl.info.render.triangles,geometryBytes,textureBytes:256*256*6*8*4/3+64*64*4+(gl.shadowMap.enabled?key.shadow.mapSize.x*key.shadow.mapSize.y*8:0),drawingWidth:gl.domElement.width,drawingHeight:gl.domElement.height,dpr:scale,geometries:geometries.size,materials:materials.size,pieces:replay.pieceCount,replayStage:replay.stage,completedPlies:replay.completed,positionFen:pose.settled?replay.fen:null,moving:replay.moving||!pose.settled,positionLabel:pose.settled?replay.label:'Display composition · original history unchanged',knightLift:pose.knightLift,bishopLift:pose.bishopLift,progress:q};
    }
    return {canvas:gl.domElement,render,resize,dispose,degrade:()=>{gl.shadowMap.enabled=false;resize(width,height,1);}};
  } catch(error) {dispose();throw error;}
}
