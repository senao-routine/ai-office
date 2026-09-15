// R96-D2 S4: Tripo で生成したリグ付きロボ（SkinnedMesh）を、既存の RobotBatch と同じ nodes 契約の裏で動かす試作。
// three は core だけ（Bone / Skeleton / SkinnedMesh）。AnimationMixer は使わない＝時刻 t の純関数サンプラ（ui/core/clip.js）で骨を置く。
// `?rig=1` のときだけ scene3d が createRigKit() を呼ぶ。ui/iso/gen/robot_body.js / robot_clips.js が無い（PWA のスタブ null）なら kit は null。
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { decodeClips, sampleClip, blendPoses } from "/ui/core/clip.js";
import { RIG as ANIM_RIG } from "/ui/core/anim.js";
const smoothstep = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
import robotBody from "./gen/robot_body.js";
import robotClips from "./gen/robot_clips.js";

const FRONT_YAW = Math.PI / 2;    // 生成体は +x を向く（バイザーの頂点重心で判定）→ 既存ロボの向きへ（-π/2 だと背中を見せた・実レンダで確認）
const bytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

/** poseKind（scene3d が組む文字列）→ clip 名。無い種類は idle。 */
export function clipFor(poseKind, seated) {
  const k = poseKind || "";
  if (k.startsWith("walk") || k === "enter" || k === "run") return "walk";
  if (k.startsWith("question")) return "look_around";
  if (k.startsWith("celebrate")) return "cheer";
  if (k === "think") return "wait";
  if (k.startsWith("read") || k === "relax" || k === "loungeTab" || k.startsWith("lounge")) return "sit";
  if (k.startsWith("meeting:present") || k.endsWith(":stand")) return "idle";
  if (seated || k.startsWith("desk") || k.startsWith("meeting")) return "sit";
  return "idle";
}

function buildGeometry(mod) {
  const part = mod.parts[0], n = part.n;
  const q = new Int16Array(bytes(part.pos).buffer), pos = new Float32Array(n * 3);
  for (let i = 0; i < n * 3; i += 3) {
    pos[i] = q[i] * mod.scale + mod.offset[0]; pos[i + 1] = q[i + 1] * mod.scale + mod.offset[1]; pos[i + 2] = q[i + 2] * mod.scale + mod.offset[2];
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  const qn = new Int8Array(bytes(part.nrm).buffer), nrm = new Float32Array(n * 3);
  for (let i = 0; i < n * 3; i++) nrm[i] = qn[i] / 127;
  g.setAttribute("normal", new THREE.BufferAttribute(nrm, 3));
  if (part.col) {
    const qc = bytes(part.col), col = new Float32Array(n * 3);
    for (let i = 0; i < n * 3; i++) { const c = qc[i] / 255; col[i] = c <= .04045 ? c / 12.92 : Math.pow((c + .055) / 1.055, 2.4); }
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
  }
  g.setAttribute("skinIndex", new THREE.Uint8BufferAttribute(bytes(part.joints), 4));
  g.setAttribute("skinWeight", new THREE.Uint8BufferAttribute(bytes(part.weights), 4, true));
  g.setIndex(new THREE.BufferAttribute(new Uint16Array(bytes(part.idx).buffer), 1));
  g.computeBoundingSphere();
  return g;
}

export function createRigKit(materials, scene) {
  if (!robotBody || !robotClips) return null;
  const geometry = buildGeometry(robotBody);
  const sk = robotBody.skeleton;
  const ibm = sk.ibm ? new Float32Array(bytes(sk.ibm).buffer) : null;
  const clips = decodeClips(robotClips);
  const material = materials.shell;
  const rest = sk.nodes;
  return {
    clips,
    /** リグ＋皮を scene に置き、毎フレーム nodes.root の行列（root は scene に居ない数学用の骨格）を写す。
     *  返り値の apply(poseKind, t, dist, seated, changedAt, prevKind, seed) で骨を置く。 */
    attach(nodes) {
      const group = new THREE.Group(); group.matrixAutoUpdate = false;
      const FRONT_ROT = new THREE.Matrix4().makeRotationY(FRONT_YAW);
      const bones = rest.map((n) => { const b = new THREE.Bone(); b.name = n.name; b.position.fromArray(n.t); b.quaternion.fromArray(n.r); b.scale.fromArray(n.s); return b; });
      rest.forEach((n, i) => { if (n.parent >= 0) bones[n.parent].add(bones[i]); else if (n.mesh == null) group.add(bones[i]); });
      const jointBones = sk.joints.map((ni) => bones[ni]);
      const inverses = ibm ? sk.joints.map((_, j) => new THREE.Matrix4().fromArray(ibm, j * 16)) : undefined;
      const skeleton = new THREE.Skeleton(jointBones, inverses);
      const mesh = new THREE.SkinnedMesh(geometry, material);
      mesh.frustumCulled = false; mesh.castShadow = true; mesh.receiveShadow = false;
      group.add(mesh);
      mesh.bind(skeleton, new THREE.Matrix4());   // bindMatrix は単位行列（GLTFLoader と同じ・root の scale を二重に掛けない）
      scene.add(group);
      const restTRS = sk.joints.map((ni) => ({ t: rest[ni].t, r: rest[ni].r }));
      const setPose = (sample) => {
        for (let j = 0; j < jointBones.length; j++) {
          const b = jointBones[j], s = sample[j];
          if (s?.r) b.quaternion.fromArray(s.r); else b.quaternion.fromArray(restTRS[j].r);
          // t は rest からの差分（in_place の retarget は Hip の rest が別の GLB と違う）
          if (s?.t) b.position.set(restTRS[j].t[0] + s.t[0], restTRS[j].t[1] + s.t[1], restTRS[j].t[2] + s.t[2]); else b.position.fromArray(restTRS[j].t);
        }
      };
      const timeFor = (name, t, dist, seed) => {
        const c = clips.clips[name];
        if (name === "walk") return dist / ANIM_RIG.speed;              // 距離駆動＝足が滑らない
        return t + seed;                                               // 個体差は位相オフセット
      };
      return {
        group, mesh,
        apply(poseKind, t, dist, seated, changedAt = -Infinity, prevKind = null, seed = 0) {
          const name = clipFor(poseKind, seated), clip = clips.clips[name] || clips.clips.idle;
          let sample = sampleClip(clip, clips.fps, timeFor(name, t, dist, seed));
          const w = smoothstep(0, .45, t - changedAt);
          if (prevKind !== null && w < 1) {
            const pname = clipFor(prevKind, seated), pclip = clips.clips[pname] || clips.clips.idle;
            sample = blendPoses(sampleClip(pclip, clips.fps, timeFor(pname, t, dist, seed)), sample, w);
          }
          setPose(sample);
          nodes.root.updateMatrix();
          group.matrix.multiplyMatrices(nodes.root.matrix, FRONT_ROT);
          group.updateMatrixWorld(true);
        },
        dispose() { scene.remove(group); },
      };
    },
    dispose() { geometry.dispose(); },
  };
}
