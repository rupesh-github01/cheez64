# Cheez64 UI.1.6 original prototype artwork

The chess set, board and reflection studio are original procedural prototype assets authored in `web/src/app/design-lab/cinematic/scene/geometry.ts` and `create-scene.ts`. No third-party chess model, HDRI, photograph or texture was downloaded. The knight is a stylized beveled profile with carved eye/nostril openings, not a commissioned production sculpture.

The four transparent WebP posters are exports of the actual Three.js renderer through the prototype's explicit artwork export control (`?artwork=1`). They contain no game records or evaluation evidence. Desktop renders were made at 1440×900; mobile at 390×844. The scene is the synthetic legal 18-ply Closed Ruy López fixture; hero lifts are a separated display composition, not recorded moves. Analyze has all pieces grounded. No numeric engine scores exist in this scene.

Geometry is generated locally at two LODs, sharing buffers/materials and instancing repeated pieces/tiles. There is no GLB download, animation rig, physics dependency, remote environment, font download, bloom or depth-of-field effect. Georgia/system fonts are used by the surrounding prototype. The UI.1.5 GLB acquisition proposal is superseded for this prototype by the explicitly authorized procedural asset route.

`manifest.json` records poster hashes/dimensions and provenance. `THIRD_PARTY_NOTICES.txt` contains the Three.js MIT runtime notice and MIT notice for development-only TypeScript declarations. The declaration package's transitive development dependencies are not imported by the browser scene. No license for a final commissioned identity or artwork is implied.
