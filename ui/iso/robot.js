// Guide §6: two-head-tall ceramic robots, inset black screens and vendor silhouettes.
// Mesh-free pose skeletons feed shared InstancedMesh parts; no external models/addons.
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { mergeGeometries } from "./merge.js";
import { vertexAO } from "./bake.js";
import { FaceAtlasBatch } from "./faces.js";
import { PROP_MATERIALS, propGeometries } from "./props.js";

const PARTS = [
  "head", "headCodex", "headOpenclaw", "visor", "ear",
  "antStem", "antCodex", "antOpenclaw", "antTip", "collar", "chest", "shoulder",
  "torso", "pelvis", "upper", "fore", "hand", "thigh", "shin", "foot",
  "claw",
  // R80.7/R80.8: 職業アクセサリ（core/archetype.js が決める）。各1 InstancedMesh
  "phones", "cap", "beret", "pencil", "bowtie",
  "mortar", "headset", "hardhat", "eyeshade",
  ...Object.keys(PROP_MATERIALS),
];

/** Vendor shell colors are supplied once through instanceColor. */
export const LOBSTER_TINT = new THREE.Color(0xbb4838);
export const GRAPHITE_TINT = new THREE.Color(0x787676);
export const CREAM_TINT = new THREE.Color(0xe9e0d5);
const WHITE = new THREE.Color(1, 1, 1);
/** 殻の色を差し替える部品（目・バイザー・靴は共通のまま＝表情と接地感を壊さない）。 */
const TINT_PARTS = new Set([
  "head", "headCodex", "headOpenclaw", "ear", "torso", "pelvis", "upper", "fore",
  "thigh", "shin", "antStem", "antCodex", "antOpenclaw",
]);

const HEAD_H = .72;
const FACE_W = HEAD_H * .67, FACE_H = HEAD_H * .65;
const FACE_Y = -HEAD_H * .075, FACE_R = HEAD_H * .20;
// Standing sole is .015; antenna is excluded from the two-head proportion.
const HEAD_Y = HEAD_H / .49 + .015 - HEAD_H / 2 - .54 - .36;
const VENDORS = {
  claude: { head: "head", antenna: "antStem", width: 1, tint: CREAM_TINT },
  codex: { head: "headCodex", antenna: "antCodex", width: 1.37, tint: GRAPHITE_TINT },
  openclaw: { head: "headOpenclaw", antenna: "antOpenclaw", width: 1.5, tint: LOBSTER_TINT },
};
/** R91: 個体の殻の色。ベンダー既定色に乗算する（Codex は暗いまま色味だけ乗る）。 */
export function shellTintFor(vendor, rgb) {
  const base = (VENDORS[vendor] || VENDORS.claude).tint;
  return new THREE.Color(base.r * rgb[0], base.g * rgb[1], base.b * rgb[2]);
}

const faceDepth = (x, y) => .32 - .035 * (x / (FACE_W / 2)) ** 2
  - .020 * (y / (FACE_H / 2)) ** 2;

function roundedOutline(pad = 0) {
  const points = [], r = FACE_R + pad;
  for (let corner = 0; corner < 4; corner++) {
    const a = corner * Math.PI / 2;
    const cx = (corner === 0 || corner === 3 ? 1 : -1) * (FACE_W / 2 - FACE_R);
    const cy = (corner < 2 ? 1 : -1) * (FACE_H / 2 - FACE_R);
    for (let i = 0; i <= 12; i++) {
      const angle = a + i / 12 * Math.PI / 2;
      points.push([cx + r * Math.cos(angle), cy + r * Math.sin(angle)]);
    }
  }
  return points;
}

/** Curved rounded rectangle with planar 0..1 UVs; rim is baked into each shell. */
function facePlate(rim = false) {
  const outline = roundedOutline(), outer = roundedOutline(.012);
  const positions = [], uvs = [], indices = [], rings = rim ? 1 : 12;
  for (let j = 0; j <= rings; j++) {
    for (let i = 0; i < outline.length; i++) {
      const [x, y] = rim ? (j ? outer[i] : outline[i]) : outline[i].map(v => v * j / rings);
      positions.push(x, y, faceDepth(x, y) - (rim ? 0 : .0035));
      uvs.push(x / FACE_W + .5, y / FACE_H + .5);
      if (j) {
        const a = (j - 1) * outline.length + i, b = (j - 1) * outline.length + (i + 1) % outline.length;
        const c = j * outline.length + i, d = j * outline.length + (i + 1) % outline.length;
        indices.push(a, c, d, a, d, b);
      }
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  g.setIndex(indices); g.computeVertexNormals();
  return g;
}

function headGeometry(vendor) {
  const width = VENDORS[vendor].width;
  let g;
  if (vendor === "codex") {
    const r = HEAD_H * .35;
    g = new THREE.BoxGeometry(HEAD_H * width, HEAD_H, .66, 32, 24, 24);
    const p = g.getAttribute("position"), point = new THREE.Vector3(), inner = new THREE.Vector3();
    for (let i = 0; i < p.count; i++) {
      point.fromBufferAttribute(p, i);
      inner.set(THREE.MathUtils.clamp(point.x, -HEAD_H * width / 2 + r, HEAD_H * width / 2 - r),
        THREE.MathUtils.clamp(point.y, -HEAD_H / 2 + r, HEAD_H / 2 - r),
        THREE.MathUtils.clamp(point.z, -.33 + r, .33 - r));
      point.sub(inner).normalize().multiplyScalar(r).add(inner);
      p.setXYZ(i, point.x, point.y, point.z);
    }
  } else {
    g = new THREE.SphereGeometry(HEAD_H / 2, 80, 64);
    g.scale(width, 1, vendor === "openclaw" ? .90 : 1);
  }
  // Seat the shared curved screen in a shallow recess; blend its surround into the shell.
  const p = g.getAttribute("position");
  for (let i = 0; i < p.count; i++) {
    if (p.getZ(i) <= 0) continue;
    const x = p.getX(i) / width, y = p.getY(i) - FACE_Y;
    const qx = Math.abs(x) - (FACE_W / 2 - FACE_R), qy = Math.abs(y) - (FACE_H / 2 - FACE_R);
    const distance = Math.hypot(Math.max(0, qx), Math.max(0, qy)) + Math.min(0, Math.max(qx, qy)) - FACE_R;
    const mix = 1 - THREE.MathUtils.smoothstep(distance, .006, .065);
    p.setZ(i, THREE.MathUtils.lerp(p.getZ(i), faceDepth(x, y) - .009, mix));
  }
  g.computeVertexNormals();
  const rim = facePlate(true); rim.translate(0, FACE_Y, 0); rim.scale(width, 1, 1);
  const merged = mergeGeometries([g, rim]); g.dispose(); rim.dispose();
  return merged;
}

function antennaPoints(vendor, side = 0) {
  const y = HEAD_H / 2 - .025;
  if (vendor === "openclaw") return [
    new THREE.Vector3(side * .22, y, 0), new THREE.Vector3(side * .27, y + .25, -.015),
    new THREE.Vector3(side * .49, y + HEAD_H * .85, -.035),
  ];
  const length = HEAD_H * .38, angle = side * Math.PI / 12;
  const x = side * .19;
  return [new THREE.Vector3(x, y, 0), new THREE.Vector3(x + Math.sin(angle) * length,
    y + Math.cos(angle) * length, 0)];
}

function antennaGeometry(vendor) {
  const pieces = (vendor === "claude" ? [0] : [-1, 1]).map(side => {
    const points = antennaPoints(vendor, side);
    const curve = points.length === 3 ? new THREE.QuadraticBezierCurve3(...points)
      : new THREE.LineCurve3(...points);
    return new THREE.TubeGeometry(curve, 24, .012, 8, false);
  });
  const merged = mergeGeometries(pieces); pieces.forEach(g => g.dispose()); return merged;
}

function pearGeometry() {
  const points = [[0, -.22], [.15, -.215], [.24, -.175], [.28, -.09], [.275, .02],
    [.25, .11], [.205, .185], [.14, .21], [0, .215]].map(([x, y]) => new THREE.Vector2(x, y));
  const curve = new THREE.SplineCurve(points);
  const g = new THREE.LatheGeometry(curve.getPoints(48), 40);
  g.computeBoundingBox();
  g.scale(HEAD_H * .78 / (g.boundingBox.max.x - g.boundingBox.min.x), 1, .77);
  return g;
}

function mittenGeometry() {
  const palm = new THREE.SphereGeometry(.075, 20, 16); palm.scale(.90, 1, .8);
  const thumb = new THREE.SphereGeometry(.037, 14, 10); thumb.translate(.05, -.005, .018);
  const g = mergeGeometries([palm, thumb]); palm.dispose(); thumb.dispose(); return g;
}

/** 部品のジオメトリ。全ロボットで共有する（1つだけ作る）。 */
export function buildPartGeometries() {
  const parts = {
    head: headGeometry("claude"), headCodex: headGeometry("codex"), headOpenclaw: headGeometry("openclaw"),
    visor: facePlate(),
    ear: new THREE.CylinderGeometry(0.088, 0.088, 0.05, 24),
    antStem: antennaGeometry("claude"), antCodex: antennaGeometry("codex"), antOpenclaw: antennaGeometry("openclaw"),
    antTip: new THREE.SphereGeometry(0.043, 16, 12),
    collar: new THREE.CylinderGeometry(HEAD_H * .26, HEAD_H * .26, .05, 28),
    chest: new THREE.CylinderGeometry(0.078, 0.078, 0.026, 24),
    shoulder: new THREE.SphereGeometry(.075, 20, 16),
    torso: pearGeometry(),
    pelvis: new THREE.SphereGeometry(0.14, 24, 18),
    upper: new THREE.CapsuleGeometry(0.070, 0.11, 6, 18),
    fore: new THREE.CapsuleGeometry(0.062, 0.10, 6, 18),
    hand: mittenGeometry(),
    thigh: new THREE.CapsuleGeometry(0.080, 0.10, 6, 18),
    shin: new THREE.CapsuleGeometry(0.070, 0.10, 6, 18),
    foot: new THREE.SphereGeometry(1, 24, 16).scale(.10, .04, .14),
    claw: clawGeometry(),
    phones: phonesGeometry(),
    cap: capGeometry(),
    beret: beretGeometry(),
    pencil: pencilGeometry(),
    bowtie: bowtieGeometry(),
    mortar: mortarGeometry(),
    headset: headsetGeometry(),
    hardhat: hardhatGeometry(),
    eyeshade: eyeshadeGeometry(),
    ...propGeometries(),
  };
  for (const [part, geometry] of Object.entries(parts)) {
    if (part === "visor") continue;
    vertexAO(geometry, { strength: .88, height: part.startsWith("head") ? .22 : .10 });
    // The top of each sleeve is the armpit contact, not its open lower end.
    if (part === "upper") {
      geometry.rotateZ(Math.PI); vertexAO(geometry, { strength: .88, height: .06 }); geometry.rotateZ(Math.PI);
    }
  }
  return parts;
}

// ── R80.7 職業アクセサリ（頭中心=原点にベイク。かわいさの本体=比率は触らない） ──
function bake(g, x, y, z) { g.translate(x, y, z); return g; }

/** 🎧 動画編集: ヘッドホン（左右カップ＋頭上バンド） */
function phonesGeometry() {
  const parts = [new THREE.TorusGeometry(0.375, 0.030, 10, 26, Math.PI)];
  for (const s of [-1, 1]) {
    const cup = new THREE.CylinderGeometry(0.115, 0.115, 0.075, 20);
    cup.rotateZ(Math.PI / 2);
    parts.push(bake(cup, s * 0.375, 0.02, 0));
  }
  return mergeGeometries(parts);
}

/** 🧢 開発: キャップ（ドーム＋前ツバ。バイザーの上に浅く載せる） */
function capGeometry() {
  const dome = new THREE.SphereGeometry(0.35, 28, 14, 0, Math.PI * 2, 0, 0.62);
  const brim = new THREE.BoxGeometry(0.30, 0.032, 0.20);
  return mergeGeometries([bake(dome, 0, 0.045, 0), bake(brim, 0, 0.185, 0.335)]);
}

/** 🎨 デザイン: ベレー帽（斜めの平たい円＋ちょこん） */
function beretGeometry() {
  const disk = new THREE.SphereGeometry(0.30, 24, 12);
  disk.scale(1, 0.34, 1);
  disk.rotateZ(0.24);
  const stem = new THREE.SphereGeometry(0.035, 10, 8);
  return mergeGeometries([bake(disk, 0.06, 0.295, 0), bake(stem, 0.13, 0.40, 0)]);
}

/** ✏️ 執筆: 耳の上の鉛筆（軸＋先端コーン） */
function pencilGeometry() {
  const body = new THREE.CylinderGeometry(0.028, 0.028, 0.24, 10);
  const tip = new THREE.ConeGeometry(0.028, 0.075, 10);
  tip.translate(0, 0.155, 0);
  const g = mergeGeometries([body, tip]);
  g.rotateZ(1.15);
  g.translate(0.315, 0.235, 0.04);
  return g;
}

/** 🎓 リサーチ/研究: 角帽（正方形の板＋タッセル） */
function mortarGeometry() {
  const board = new THREE.BoxGeometry(0.52, 0.030, 0.52);
  board.rotateY(0.5);
  const base = new THREE.CylinderGeometry(0.20, 0.23, 0.10, 20);
  const tassel = new THREE.SphereGeometry(0.038, 10, 8);
  return mergeGeometries([bake(board, 0, 0.315, 0), bake(base, 0, 0.255, 0),
    bake(tassel, 0.24, 0.30, 0.20)]);
}

/** 🎤 広報/サポート: ヘッドセット（細いバンド＋片耳カップ＋マイクブーム） */
function headsetGeometry() {
  const band = new THREE.TorusGeometry(0.36, 0.018, 8, 24, Math.PI);
  const cup = new THREE.CylinderGeometry(0.085, 0.085, 0.055, 16);
  cup.rotateZ(Math.PI / 2);
  const boom = new THREE.CylinderGeometry(0.014, 0.014, 0.24, 8);
  boom.rotateX(Math.PI / 2 - 0.35);
  boom.rotateY(-0.45);
  const mic = new THREE.SphereGeometry(0.036, 10, 8);
  return mergeGeometries([band, bake(cup, 0.355, 0.01, 0),
    bake(boom, 0.28, -0.10, 0.16), bake(mic, 0.20, -0.185, 0.27)]);
}

/** ⛑ インフラ/移行: ヘルメット（ドーム＋つば一周＋天面リブ） */
function hardhatGeometry() {
  const dome = new THREE.SphereGeometry(0.355, 26, 14, 0, Math.PI * 2, 0, 0.95);
  const brim = new THREE.CylinderGeometry(0.42, 0.44, 0.028, 26);
  const rib = new THREE.BoxGeometry(0.055, 0.035, 0.56);
  return mergeGeometries([bake(dome, 0, 0.055, 0), bake(brim, 0, 0.10, 0),
    bake(rib, 0, 0.315, 0)]);
}

/** 👓 経理/会計: アイシェード（緑の半透明ツバ＝会計士の記号。ツバだけ＝目は隠さない） */
function eyeshadeGeometry() {
  const brim = new THREE.CylinderGeometry(0.36, 0.40, 0.030, 24, 1, false, -0.75, 1.5);
  const bandR = new THREE.TorusGeometry(0.345, 0.020, 8, 22, Math.PI * 1.2);
  bandR.rotateX(Math.PI / 2);
  bandR.rotateZ(-0.19);
  return mergeGeometries([bake(brim, 0, 0.235, 0.10), bake(bandR, 0, 0.235, 0)]);
}

/** 🎀 運用/事務: 蝶ネクタイ（襟元。左右の羽＋結び目） */
function bowtieGeometry() {
  const parts = [new THREE.SphereGeometry(0.030, 10, 8)];
  for (const s of [-1, 1]) {
    const wing = new THREE.BoxGeometry(0.095, 0.058, 0.026);
    wing.rotateZ(s * 0.35);
    parts.push(bake(wing, s * 0.062, 0, 0));
  }
  return mergeGeometries(parts);
}

/** R75: ハサミ（手の代わり）。付け根の球＋開いた2本の爪を1ジオメトリへ統合＝
 *  何体いても InstancedMesh 1つ（drawCalls +1）で済む。 */
function clawGeometry() {
  const parts = [];
  const base = new THREE.SphereGeometry(0.072, 16, 12);
  base.scale(1, 0.9, 1.15);
  parts.push(base);
  for (const s of [-1, 1]) {
    const prong = new THREE.CapsuleGeometry(0.030, 0.115, 4, 10);
    prong.rotateX(-Math.PI / 2);                 // 前方（-z）へ倒す
    prong.rotateY(s * 0.30);                     // 上下の爪をV字に開く
    prong.translate(s * 0.030, s * 0.024, -0.105);
    parts.push(prong);
  }
  return mergeGeometries(parts);
}

/**
 * 骨格。メッシュを持たない Object3D の階層なので、体数が増えても
 * 描画コストは増えない（行列計算だけ）。
 * 比率: 腰0.50 / 胴は短く / 首は低く / 頭はでかい（かわいさの本体）。
 */
export function makeSkeleton() {
  const root = new THREE.Object3D();
  const hip = new THREE.Object3D();
  hip.position.y = 0.54;
  root.add(hip);

  const neck = new THREE.Object3D();
  neck.position.y = 0.36;                       // 首を低く＝頭が体に埋まる幼児体型
  hip.add(neck);

  const nodes = {
    root, hip, neck,
    pelvis: attach(hip, 0, -0.02, 0),
    torso: attach(hip, 0, 0.015, 0),
    collar: attach(hip, 0, 0.205, 0),
    chest: attach(hip, 0, 0.10, 0.207, Math.PI / 2),
    head: attach(neck, 0, HEAD_Y, 0),
    visor: attach(neck, 0, HEAD_Y + FACE_Y, 0),
    antStem: attach(neck, 0, HEAD_Y, 0),
    antTips: [attach(neck, 0, 0, 0), attach(neck, 0, 0, 0)],
    acc: {
      phones: attach(neck, 0, 0.16, 0),
      cap: attach(neck, 0, 0.16, 0),
      beret: attach(neck, 0, 0.16, 0),
      pencil: attach(neck, 0, 0.16, 0),
      bowtie: attach(hip, 0, 0.18, 0.18),
      mortar: attach(neck, 0, 0.16, 0),
      headset: attach(neck, 0, 0.16, 0),
      hardhat: attach(neck, 0, 0.16, 0),
      eyeshade: attach(neck, 0, 0.16, 0),
    },
    ears: [], arms: [], legs: [],
  };
  for (const [part, node] of Object.entries(nodes.acc)) {
    if (part === "bowtie") continue;
    node.position.y = HEAD_Y;
    node.scale.setScalar(HEAD_H / .67);
  }
  for (const s of [-1, 1]) {
    nodes.ears.push(attach(neck, s * HEAD_H / 2, HEAD_Y - .03, 0, 0, 0, Math.PI / 2));
  }
  for (const side of [-1, 1]) {
    const shoulder = new THREE.Object3D();
    shoulder.position.set(side * 0.27, 0.17, 0);
    hip.add(shoulder);
    const elbow = new THREE.Object3D();
    elbow.position.y = -0.185;
    shoulder.add(elbow);
    nodes.arms.push({
      side, shoulder, elbow,
      joint: attach(shoulder, 0, 0, 0),
      upper: attach(shoulder, 0, -0.09, 0),
      fore: attach(elbow, 0, -0.08, 0),
      hand: attach(elbow, 0, -0.185, 0),
    });

    const hipJoint = new THREE.Object3D();
    hipJoint.position.set(side * 0.105, -0.10, 0);
    hip.add(hipJoint);
    const knee = new THREE.Object3D();
    knee.position.y = -0.20;
    hipJoint.add(knee);
    nodes.legs.push({
      side, hipJoint, knee,
      thigh: attach(hipJoint, 0, -0.09, 0),
      shin: attach(knee, 0, -0.08, 0),
      foot: attach(knee, 0, -0.185, 0.045),
    });
  }
  return nodes;
}

function setVendor(nodes, vendor) {
  if (nodes.vendor === vendor) return;
  const profile = VENDORS[vendor], previous = VENDORS[nodes.vendor]?.width || 1;
  const ratio = profile.width / previous;
  nodes.visor.scale.x *= ratio;
  for (const node of [nodes.collar, nodes.torso, nodes.pelvis, ...Object.values(nodes.acc)]) node.scale.x *= ratio;
  for (const ear of nodes.ears) ear.position.x *= ratio;
  for (const arm of nodes.arms) arm.shoulder.position.x *= ratio;
  for (const [i, side] of (vendor === "claude" ? [0] : [-1, 1]).entries()) {
    const tip = antennaPoints(vendor, side).at(-1);
    nodes.antTips[i].position.copy(tip); nodes.antTips[i].position.y += HEAD_Y;
  }
  nodes.vendor = vendor;
}

/**
 * 🧒 チビロボ骨格（R58・会議の部下）。同じ部品ジオメトリを使い、
 * ノードのスケールだけで「2頭身」を作る（InstancedMesh の行列に乗るので追加コスト無し）:
 *   頭グループ(neck)を1.5倍 ＝ 頭・バイザー・目・耳・アンテナが一緒に大きくなる
 *   腕脚(shoulder/hipJoint)を0.72倍 ＝ ずんぐり短い手足
 *   胴まわりを0.8倍 ＝ 小さな体に大きな頭が乗る（かわいさの本体は比率）
 * 立位の腰高は脚が縮んだぶん低い＝ポーズ側は anim.js の chibiPose（CHIBI_HIP_Y）を使うこと。
 */
export function makeChibiSkeleton() {
  const nodes = makeSkeleton();
  nodes.neck.scale.setScalar(1.5);
  nodes.neck.position.y = 0.30;                 // 大きな頭を胴に少し埋める（幼児体型）
  for (const arm of nodes.arms) arm.shoulder.scale.setScalar(0.72);
  for (const leg of nodes.legs) leg.hipJoint.scale.setScalar(0.72);
  nodes.torso.scale.set(0.82, 0.66, 0.82);
  nodes.pelvis.scale.setScalar(0.82);
  nodes.collar.scale.setScalar(0.88);
  nodes.collar.position.y = 0.21;
  nodes.chest.scale.setScalar(0.85);
  nodes.chest.position.y = 0.10;
  nodes.chest.position.z = 0.155;
  return nodes;
}

/**
 * 🧹 掃除ロボ（R68・アンビエント役者）。ルンバ風の円盤＋ドーム＋センサー柱を
 * 胴1メッシュ（+1 drawCall）と visor 材の前部センサーで +1＝計2ドロー。
 * 骨格は不要（回転なし・pathTravel の位置と首振りだけ）。
 */
export function makeCleanerBot(materials) {
  const group = new THREE.Group();
  const bake = (g, x, y, z) => { g.translate(x, y, z); return g; };
  const body = mergeGeometries([
    bake(new THREE.CylinderGeometry(0.30, 0.33, 0.13, 28), 0, 0.075, 0),
    bake(new THREE.SphereGeometry(0.16, 24, 14, 0, Math.PI * 2, 0, Math.PI / 2),
      0, 0.13, 0),
    bake(new THREE.CylinderGeometry(0.016, 0.016, 0.14, 8), 0, 0.30, -0.04),
    bake(new THREE.SphereGeometry(0.035, 12, 8), 0, 0.38, -0.04),
  ]);
  vertexAO(body, { strength: .88, height: .12 });
  const color = body.getAttribute("color");
  for (let i = 0; i < color.count; i++) color.setXYZ(i,
    color.getX(i) * CREAM_TINT.r, color.getY(i) * CREAM_TINT.g, color.getZ(i) * CREAM_TINT.b);
  const bodyMesh = new THREE.Mesh(body, materials.shell);
  bodyMesh.castShadow = true;
  group.add(bodyMesh);
  const sensor = new THREE.SphereGeometry(0.045, 12, 8);
  // Shared visor now carries the face atlas; the cleaner samples its black corner only.
  const sensorUV = sensor.getAttribute("uv");
  for (let i = 0; i < sensorUV.count; i++) sensorUV.setXY(i, .01, .99);
  const light = new THREE.Mesh(sensor, materials.visor);
  light.position.set(0, 0.12, 0.27);            // 前部のセンサーライト
  group.add(light);
  // dark 等倍だと床の影に溶けて見えない（実測）。白シェル＋1.35倍で「居る」と分かる大きさに
  group.scale.setScalar(1.35);
  return group;
}

function attach(parent, x, y, z, rx = 0, ry = 0, rz = 0) {
  const o = new THREE.Object3D();
  o.position.set(x, y, z);
  o.rotation.set(rx, ry, rz);
  parent.add(o);
  return o;
}

/** ポーズ（ui/core/anim.js の純関数が返す角度）を骨格へ流し込む。 */
export function applyPose(nodes, pose) {
  nodes.hip.position.y = pose.hipY;
  nodes.hip.rotation.set(0, pose.hipYaw, pose.hipRoll);
  nodes.neck.rotation.set(pose.headPitch, pose.headYaw, 0);
  pose.legs.forEach((l, i) => {
    const leg = nodes.legs[i];
    leg.hipJoint.rotation.x = l.hip;
    leg.knee.rotation.x = l.knee;
  });
  pose.arms.forEach((a, i) => {
    const arm = nodes.arms[i];
    arm.shoulder.rotation.set(a.shoulder, 0, arm.side * 0.07);
    arm.elbow.rotation.x = a.elbow;
  });
}

/**
 * 部品ごとの InstancedMesh 群。
 * ロボット何体でも drawCalls は PARTS の数に収まる。
 */
export class RobotBatch {
  constructor(scene, materials, capacity) {
    this.capacity = capacity;
    this.geoms = buildPartGeometries();
    this.materials = materials;
    this.meshes = {};
    this.counts = {};
    this.partMaterial = {
      head: "shell", headCodex: "shell", headOpenclaw: "shell", visor: "visor", ear: "shell",
      antStem: "shell", antCodex: "shell", antOpenclaw: "shell", antTip: "accent", collar: "joint", chest: "accent", shoulder: "joint",
      torso: "shell", pelvis: "shell", upper: "shell", fore: "shell",
      hand: "joint", thigh: "shell", shin: "shell", foot: "joint",
      claw: "joint",
      phones: "shell", cap: "shell", beret: "shell", pencil: "shell",
      bowtie: "shell", mortar: "shell", headset: "shell", hardhat: "shell",
      eyeshade: "shell",
      ...PROP_MATERIALS,
    };
    this.perBody = {
      antTip: 2, shoulder: 2, ear: 2, upper: 2, fore: 2, hand: 2, thigh: 2, shin: 2, foot: 2,
      claw: 2,
    };
    this.bodyColor = new THREE.Color();
    for (const part of PARTS) {
      const n = capacity * (this.perBody[part] || 1);
      // AO and instanceColor multiply; shared robot parts always carry a color attribute.
      const geometry = this.geoms[part];
      if (!geometry.getAttribute("color")) {
        geometry.setAttribute("color", new THREE.Float32BufferAttribute(
          new Float32Array(geometry.getAttribute("position").count * 3).fill(1), 3));
      }
      const mesh = new THREE.InstancedMesh(
        geometry, materials[this.partMaterial[part]], n);
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      // Small hand props add at most four draws, with no extra shadow-pass draws.
      mesh.castShadow = part !== "visor" && !Object.hasOwn(PROP_MATERIALS, part);
      mesh.receiveShadow = false;
      mesh.frustumCulled = false;
      mesh.count = 0;
      scene.add(mesh);
      this.meshes[part] = mesh;
    }
    this.faces = new FaceAtlasBatch(scene, this.meshes.visor, capacity);
  }

  begin() {
    this.bodyCount = 0;
    for (const part of PARTS) this.counts[part] = 0;
  }

  /** 1体ぶんの世界行列を各 InstancedMesh へ書き込む。
   *  accent は胸リング・アンテナ先端のインスタンスカラー（HUDの状態ドットと同じ意味色）。
   *  vendor selects head/antenna/hand parts without changing the pose contract. */
  push(nodes, accent = null, tint = null, arch = null, vendor = "claude", expression = "idle", prop = null) {
    if (this.bodyCount >= this.capacity) return;
    this.bodyCount += 1;
    if (!VENDORS[vendor]) vendor = "claude";
    setVendor(nodes, vendor);
    nodes.root.updateMatrixWorld(true);
    const profile = VENDORS[vendor];
    const bodyTint = this.bodyColor.copy(tint || profile.tint);
    const put = (part, obj) => {
      const i = this.counts[part];
      if (i >= this.capacity * (this.perBody[part] || 1)) return;
      this.meshes[part].setMatrixAt(i, obj.matrixWorld);
      if (part === "chest" || part === "antTip") {
        this.meshes[part].setColorAt(i, accent || WHITE);
      } else if (TINT_PARTS.has(part)) {
        // 毎回パレット色を明示し、前フレームの instanceColor を残さない
        this.meshes[part].setColorAt(i, bodyTint);
      }
      this.counts[part] = i + 1;
    };
    put(profile.head, nodes.head);
    put(profile.antenna, nodes.antStem);
    for (let i = 0; i < (vendor === "claude" ? 1 : 2); i++) put("antTip", nodes.antTips[i]);
    this.faces.setCell(this.counts.visor, expression);
    for (const part of ["visor", "collar",
      "chest", "torso", "pelvis"]) {
      put(part, nodes[part]);
    }
    for (const ear of nodes.ears) put("ear", ear);
    for (const arm of nodes.arms) {
      put("shoulder", arm.joint);
      put("upper", arm.upper);
      put("fore", arm.fore);
      put(vendor === "openclaw" ? "claw" : "hand", arm.hand);
    }
    for (const leg of nodes.legs) {
      put("thigh", leg.thigh);
      put("shin", leg.shin);
      put("foot", leg.foot);
    }
    if (Object.hasOwn(PROP_MATERIALS, prop)) put(prop, nodes.arms[1].hand);
    // R80.7: 職業アクセサリ（該当アーキタイプのロボにだけ1個・専用色）
    if (arch && arch.part && nodes.acc && nodes.acc[arch.part]) {
      const part = arch.part;
      const i = this.counts[part];
      if (i < this.capacity) {
        this.meshes[part].setMatrixAt(i, nodes.acc[part].matrixWorld);
        this.meshes[part].setColorAt(i, arch.accC || WHITE);
        this.counts[part] = i + 1;
      }
    }
  }

  end() {
    for (const part of PARTS) {
      const mesh = this.meshes[part];
      mesh.count = this.counts[part];
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      if (mesh.count) mesh.computeBoundingSphere();
    }
    this.faces.end(this.counts.visor);
  }

  dispose() {
    this.faces.dispose();
    for (const part of PARTS) {
      this.meshes[part].dispose();
      this.geoms[part].dispose();
    }
  }
}

export { PARTS };
