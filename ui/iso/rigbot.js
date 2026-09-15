// R96-D2 S4: Tripo で生成したリグ付きロボ（SkinnedMesh）を、既存の RobotBatch と同じ nodes 契約の裏で動かす試作。
// three は core だけ（Bone / Skeleton / SkinnedMesh）。AnimationMixer は使わない＝時刻 t の純関数サンプラ（ui/core/clip.js）で骨を置く。
// `?rig=1` のときだけ scene3d が createRigKit() を呼ぶ。ui/iso/gen/robot_body.js / robot_clips.js が無い（PWA のスタブ null）なら kit は null。
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { decodeClips, sampleClip, blendPoses } from "/ui/core/clip.js";
import { RIG as ANIM_RIG } from "/ui/core/anim.js";
const smoothstep = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
import robotBody from "./gen/robot_body.js";
import robotBodyH from "./gen/robot_body_h.js";   // 首（y≈0.655）で頭を落とした胴体＝ハイブリッド C 用
import robotClips from "./gen/robot_clips.js";

const FRONT_YAW = -Math.PI / 2;   // 生成体は +x を向く（バイザーの頂点重心）→ 既存ロボの前＝+z（胸リング attach(hip,0,.10,+.207) の側）へ。R_y(-π/2)·(1,0,0)=(0,0,1)。+π/2 だと歩行が後ろ向きになる（本人指摘）
const bytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

/** poseKind（scene3d が組む文字列）→ clip 名。無い種類は idle。 */
export function clipFor(poseKind, seated) {
  const k = poseKind || "";
  if (k.startsWith("walk") || k === "enter" || k === "run" || k === "exit") return "walk";
  if (k.startsWith("question")) return "look_around";
  if (k.startsWith("celebrate") || k === "greet") return "cheer";
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

/** ハイブリッド C で procedural 側から出す部品（頭・顔・耳・アンテナ・胸リング・職業アクセサリ）。胴体・腕・脚は生成体。 */
export const HYBRID_PARTS = new Set(["head", "headCodex", "headOpenclaw", "visor", "ear", "antStem", "antCodex", "antOpenclaw", "antTip", "chest", "__acc"]);
/** D（採用案）で生成体に重ねる部品: 表情アトラスのバイザー・胸の状態リング・職業アクセサリ。頭・耳・アンテナは生成体のもの。 */
export const D_PARTS = new Set(["visorRig", "chest", "__acc"]);
const HEAD_LIFT = 0.166;   // C: Head 骨（首・0.645）から procedural の neck 原点へ: 頭の底が切り口に載る高さ（1.035 − HEAD_Y 0.2244 − 0.645）
const HEAD_LIFT_D = 0.18;  // D: 生成体の頭の中心に procedural の neck 原点（+HEAD_Y 0.2244）を合わせる（帽子が頭頂に触れる高さ・実レンダで 0.205→0.18）
const HEAD_Y_P = 0.2244, FACE_Y_P = -0.054;   // robot.js の HEAD_Y / FACE_Y（visor 原点 = neck + HEAD_Y + FACE_Y）
const CHEST_FWD = 0.40;    // Spine02 から胸リングまでの前方オフセット（生成体の胸の前面 z≈0.40・実測）
const CHEST_LIFT = 0.12;   // Spine02（y≈0.41）から胸リングの高さへ

/** mode 1= 生成体そのまま（D）／ mode 2= ハイブリッド（C）。 */
/**
 * 生成体の顔（焼き込みの黒いバイザー領域）をそのまま切り出して、表情アトラス用のプレート形状にする。
 * procedural の facePlate は半径 0.36 の球面で、生成体の頭（楕円・半径 0.32×0.41）に載せると縁が浮く/めり込む（実測）ので、
 * 頭の面そのものを 3mm 外へ押し出したものを使う。座標は visor ノード（rest の neck 原点 + HEAD_Y + FACE_Y）基準。UV は領域の x/y を 0..1 に写す。
 */
export function facePlateFromBody(mod, headLift) {
  const part = mod.parts[0], n = part.n;
  const q = new Int16Array(bytes(part.pos).buffer), qn = new Int8Array(bytes(part.nrm).buffer), qc = bytes(part.col), idx = new Uint16Array(bytes(part.idx).buffer);
  const P = (i) => [q[i * 3] * mod.scale + mod.offset[0], q[i * 3 + 1] * mod.scale + mod.offset[1], q[i * 3 + 2] * mod.scale + mod.offset[2]];
  // rest の Head 骨（首）位置 → visor 原点（体は +x 向きなので前＝+x・上＝+y・左右＝z）
  const sk = mod.skeleton, headNode = sk.joints.map((ni) => sk.nodes[ni]).find((nd) => nd.name === "Head");
  let hx = 0, hy = 0.645, hz = 0;
  if (headNode) { // rest のワールド位置を親から積む
    const world = (i) => { const nd = sk.nodes[i]; const t = nd.t; if (nd.parent < 0) return [t[0], t[1], t[2]];
      const p = world(nd.parent), r = sk.nodes[nd.parent]; const rot = new THREE.Quaternion(); // 親までの回転を積む
      let acc = new THREE.Quaternion(); let j = nd.parent; const chain = []; while (j >= 0) { chain.unshift(sk.nodes[j]); j = sk.nodes[j].parent; }
      for (const c of chain) acc.multiply(new THREE.Quaternion(c.r[0], c.r[1], c.r[2], c.r[3]));
      const v = new THREE.Vector3(t[0], t[1], t[2]).applyQuaternion(acc); return [p[0] + v.x, p[1] + v.y, p[2] + v.z]; };
    [hx, hy, hz] = world(sk.nodes.indexOf(headNode));
  }
  const ox = hx, oy = hy + headLift + HEAD_Y_P + FACE_Y_P, oz = hz;   // visor 原点（体ローカル）
  const dark = new Uint8Array(n); let cnt = 0;
  for (let i = 0; i < n; i++) { const [x, y] = P(i); if (y > 0.8 && x > hx && qc[i * 3] + qc[i * 3 + 1] + qc[i * 3 + 2] < 150) { dark[i] = 1; cnt++; } }
  const tris = []; for (let t = 0; t < idx.length; t += 3) if (dark[idx[t]] && dark[idx[t + 1]] && dark[idx[t + 2]]) tris.push(idx[t], idx[t + 1], idx[t + 2]);
  const used = [...new Set(tris)]; const remap = new Map(used.map((v, k) => [v, k]));
  const pos = new Float32Array(used.length * 3), nrm = new Float32Array(used.length * 3), uv = new Float32Array(used.length * 2);
  let minY = 1e9, maxY = -1e9, minZ = 1e9, maxZ = -1e9;
  used.forEach((i, k) => { const [x, y, z] = P(i); const nx = qn[i * 3] / 127, ny = qn[i * 3 + 1] / 127, nz = qn[i * 3 + 2] / 127;
    // 体ローカル(+x 前) → visor ローカル(+z 前): (x,y,z)_body → (−z, y, x)（FRONT_YAW=−π/2 と同じ回し方）
    pos[k * 3] = -(z - oz) - nz * 0.003; pos[k * 3 + 1] = (y - oy) + ny * 0.003; pos[k * 3 + 2] = (x - ox) + nx * 0.003;
    nrm[k * 3] = -nz; nrm[k * 3 + 1] = ny; nrm[k * 3 + 2] = nx;
    minY = Math.min(minY, pos[k * 3 + 1]); maxY = Math.max(maxY, pos[k * 3 + 1]); minZ = Math.min(minZ, pos[k * 3]); maxZ = Math.max(maxZ, pos[k * 3]); });
  used.forEach((i, k) => { uv[k * 2] = (pos[k * 3] - minZ) / (maxZ - minZ || 1); uv[k * 2 + 1] = (pos[k * 3 + 1] - minY) / (maxY - minY || 1); });
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3)); g.setAttribute("normal", new THREE.BufferAttribute(nrm, 3)); g.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  g.setAttribute("color", new THREE.Float32BufferAttribute(new Float32Array(used.length * 3).fill(1), 3));
  g.setIndex(new THREE.BufferAttribute(new Uint16Array(tris.map((v) => remap.get(v))), 1)); g.computeBoundingSphere();
  g.userData.faceStats = { darkVerts: cnt, plateVerts: used.length, tris: tris.length / 3, width: +(maxZ - minZ).toFixed(3), height: +(maxY - minY).toFixed(3) };
  return g;
}

export function createRigKit(materials, scene, mode = 1) {
  const bodyMod = mode === 2 ? robotBodyH : robotBody;
  if (!bodyMod || !robotClips) return null;
  const geometry = buildGeometry(bodyMod);
  const sk = bodyMod.skeleton;
  const facePlate = mode === 2 ? null : facePlateFromBody(robotBody, HEAD_LIFT_D);
  const pool = [];   // 退場した個体の BufferGeometry（色バッファ付き）を次の入場者へ回す
  const ibm = sk.ibm ? new Float32Array(bytes(sk.ibm).buffer) : null;
  const clips = decodeClips(robotClips);
  const material = materials.shell;
  const rest = sk.nodes;
  const jointIndex = (name) => sk.joints.findIndex((ni) => rest[ni].name === name);
  const HEAD_J = jointIndex("Head"), SPINE_J = jointIndex("Spine02");
  return {
    clips, mode, headParts: mode === 2 ? HYBRID_PARTS : D_PARTS, facePlate,
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
      // 個体ジオメトリ: 位置/法線/skin/index は共有（GPU バッファ 1 組）・color だけ個体別。退場した個体の器はプールで再利用する
      //（geo.dispose() は共有バッファまで消して在席中の個体を再アップロードさせる・別モデルレビュー）。
      const baseColor = geometry.getAttribute("color");
      let geo = pool.pop();
      if (!geo) {
        geo = new THREE.BufferGeometry();
        for (const name of ["position", "normal", "skinIndex", "skinWeight"]) geo.setAttribute(name, geometry.getAttribute(name));
        geo.setIndex(geometry.getIndex()); geo.boundingSphere = geometry.boundingSphere;
        geo.setAttribute("color", new THREE.BufferAttribute(baseColor.array.slice(), 3));
      }
      const color = geo.getAttribute("color");
      let tintKey = "";
      const setTint = (tint) => {
        const key = tint ? tint.getHexString() : "";
        if (key === tintKey) return; tintKey = key;
        const src = baseColor.array, dst = color.array;
        if (!tint) dst.set(src);
        else for (let i = 0; i < dst.length; i += 3) {
          // 焼いた色が明るい（白い殻）ほど tint が効き、暗い（バイザー・関節）ほど元の色を残す
          const l = Math.max(src[i], src[i + 1], src[i + 2]), k = Math.min(1, Math.max(0, (l - .15) / .6));
          dst[i] = src[i] * (1 - k + k * tint.r); dst[i + 1] = src[i + 1] * (1 - k + k * tint.g); dst[i + 2] = src[i + 2] * (1 - k + k * tint.b);
        }
        color.needsUpdate = true;
      };
      const mesh = new THREE.SkinnedMesh(geo, material);
      mesh.frustumCulled = false; mesh.castShadow = true; mesh.receiveShadow = false;
      group.add(mesh);
      mesh.bind(skeleton, new THREE.Matrix4());   // bindMatrix は単位行列（GLTFLoader と同じ・root の scale を二重に掛けない）
      scene.add(group);
      // rest 姿勢での骨の向きを控える（root は attach 時点で回転 0）
      nodes.root.updateMatrix(); nodes.root.updateMatrixWorld(true);
      group.matrix.multiplyMatrices(nodes.root.matrix, FRONT_ROT); group.updateMatrixWorld(true);
      const restTRS = sk.joints.map((ni) => ({ t: rest[ni].t, r: rest[ni].r }));
      // 頭（D はバイザーだけ出す）・胸リング・蝶ネクタイの dummy を root 直下へ移し、毎フレーム骨の位置＋差分回転へ追従させる
      nodes.root.add(nodes.neck); nodes.root.add(nodes.chest);
      const bowtie = nodes.acc?.bowtie; if (bowtie) nodes.root.add(bowtie);
      const baseQuat = new Map();   // 部品の初期回転（胸リングは X 回転 π/2＝円面が前を向く）を保持して差分回転と合成する
      // D: バイザーは生成体の顔から切り出したプレート（RobotBatch の visor 形状を差し替え）＝拡縮・前後ずらし無し
      // 部品は骨の位置＋回転（rest からの差分）に追従する。位置だけだと首を傾げた時に帽子が頭から浮く（実測）。
      const _v = new THREE.Vector3(), _q = new THREE.Quaternion(), _qr = new THREE.Quaternion(), _off = new THREE.Vector3();
      const boneLocalQuat = (bone, out) => { bone.getWorldQuaternion(out); nodes.root.getWorldQuaternion(_qr); return out.premultiply(_qr.invert()); };
      const restQuat = new Map();
      const follow = (node, bone, lift, fwd) => {
        bone.getWorldPosition(_v); nodes.root.worldToLocal(_v);
        boneLocalQuat(bone, _q);
        if (!restQuat.has(bone)) restQuat.set(bone, _q.clone());   // 最初の呼び出し＝rest（attach 直後）
        if (!baseQuat.has(node)) baseQuat.set(node, node.quaternion.clone());
        const delta = _q.multiply(restQuat.get(bone).clone().invert());
        _off.set(0, lift, fwd).applyQuaternion(delta);
        node.position.set(_v.x + _off.x, _v.y + _off.y, _v.z + _off.z);
        node.quaternion.copy(delta).multiply(baseQuat.get(node));
      };
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
      const followAll = () => {
        if (HEAD_J >= 0) follow(nodes.neck, jointBones[HEAD_J], mode === 2 ? HEAD_LIFT : HEAD_LIFT_D, 0);
        if (SPINE_J >= 0) { follow(nodes.chest, jointBones[SPINE_J], CHEST_LIFT, CHEST_FWD); if (bowtie) follow(bowtie, jointBones[SPINE_J], CHEST_LIFT + 0.06, CHEST_FWD - 0.02); }
      };
      followAll();
      return {
        group, mesh, setTint,
        apply(poseKind, t, dist, seated, changedAt = -Infinity, prevKind = null, seed = 0, prevDist = dist) {
          const name = clipFor(poseKind, seated), clip = clips.clips[name] || clips.clips.idle;
          let sample = sampleClip(clip, clips.fps, timeFor(name, t, dist, seed));
          const w = smoothstep(0, .45, t - changedAt);
          if (prevKind !== null && w < 1) {
            // 遷移元も同じ距離（累積の歩行距離＝経路再計算や停止で 0 に戻らない）でサンプルする。
            // 同じ clip 同士なら位相が同じで混合は恒等、違う clip なら 0.45s で混ざる（別モデルレビュー 2 巡分の帰結）。
            const pname = clipFor(prevKind, seated), pclip = clips.clips[pname] || clips.clips.idle;
            sample = blendPoses(sampleClip(pclip, clips.fps, timeFor(pname, t, prevDist, seed)), sample, w);
          }
          setPose(sample);
          nodes.root.updateMatrix(); nodes.root.updateMatrixWorld(true);
          group.matrix.multiplyMatrices(nodes.root.matrix, FRONT_ROT);
          group.updateMatrixWorld(true);
          followAll();
          nodes.root.updateMatrixWorld(true);
        },
        // 個体専用の資源（BufferGeometry の器と色バッファ）を解放する。共有属性（位置/法線/skin/index）は kit.dispose() が持つ
        dispose() { scene.remove(group); pool.push(geo); },
      };
    },
    dispose() { for (const g of pool) g.dispose(); pool.length = 0; geometry.dispose(); },
  };
}
