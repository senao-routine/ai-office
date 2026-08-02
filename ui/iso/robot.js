// 白いロボット（参考画像2）。外部3Dモデルは使わず、球・カプセル・箱をコードで組む。
//
// 造形の核（参考画像の実測から）:
//   頭が全身のほぼ半分を占める。大きな球头＋顔面を覆う黒いバイザー＋
//   その中に光る2つの目。胴は小さく、手足はずんぐり短い。
//   「かわいさ」はこの比率が9割を決める。細部を足すより比率を守ること。
//
// 性能の要:
//   骨格は Object3D の階層（メッシュを持たない）で作り、
//   描画は「部品ごとに1つの InstancedMesh」へ世界行列を書き込む。
//   → ロボットが何体いても drawCalls は部品数（約17）で頭打ちになる。
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { mergeGeometries } from "./merge.js";

const PARTS = [
  "head", "visor", "eye", "ear", "antStem", "antTip", "collar", "chest",
  "torso", "pelvis", "upper", "fore", "hand", "thigh", "shin", "foot",
];

/** 部品のジオメトリ。全ロボットで共有する（1つだけ作る）。 */
export function buildPartGeometries() {
  const eye = new THREE.SphereGeometry(0.055, 18, 14);
  eye.scale(1, 1.35, 0.55);                    // 縦長の楕円の目（参考画像の目）
  return {
    head: new THREE.SphereGeometry(0.335, 48, 32),
    // 顔の前面を大きく覆うバイザー（ここが参考画像の顔）
    visor: new THREE.SphereGeometry(0.342, 48, 32,
      Math.PI * 0.10, Math.PI * 0.80, Math.PI * 0.28, Math.PI * 0.44),
    eye,
    ear: new THREE.CylinderGeometry(0.088, 0.088, 0.085, 24),
    antStem: new THREE.CylinderGeometry(0.014, 0.014, 0.11, 10),
    antTip: new THREE.SphereGeometry(0.056, 16, 12),
    collar: new THREE.CylinderGeometry(0.135, 0.155, 0.055, 28),
    chest: new THREE.CylinderGeometry(0.078, 0.078, 0.026, 24),
    torso: new THREE.CapsuleGeometry(0.185, 0.16, 8, 28),
    pelvis: new THREE.SphereGeometry(0.16, 24, 18),
    upper: new THREE.CapsuleGeometry(0.070, 0.11, 6, 18),
    fore: new THREE.CapsuleGeometry(0.062, 0.10, 6, 18),
    hand: new THREE.SphereGeometry(0.088, 20, 15),
    thigh: new THREE.CapsuleGeometry(0.080, 0.10, 6, 18),
    shin: new THREE.CapsuleGeometry(0.070, 0.10, 6, 18),
    foot: new THREE.BoxGeometry(0.15, 0.08, 0.22),
  };
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
    torso: attach(hip, 0, 0.13, 0),
    collar: attach(hip, 0, 0.27, 0),
    chest: attach(hip, 0, 0.15, 0.175, Math.PI / 2),
    head: attach(neck, 0, 0.16, 0),
    visor: attach(neck, 0, 0.16, 0, 0, -Math.PI / 2),
    antStem: attach(neck, 0, 0.50, 0),
    antTip: attach(neck, 0, 0.565, 0),
    eyes: [], ears: [], arms: [], legs: [],
  };
  // 目: バイザーの中で光る2つの楕円（かわいさの第2要素）
  for (const s of [-1, 1]) {
    nodes.eyes.push(attach(neck, s * 0.115, 0.185, 0.295));
    nodes.ears.push(attach(neck, s * 0.335, 0.13, 0, 0, 0, Math.PI / 2));
  }
  for (const side of [-1, 1]) {
    const shoulder = new THREE.Object3D();
    shoulder.position.set(side * 0.225, 0.20, 0);
    hip.add(shoulder);
    const elbow = new THREE.Object3D();
    elbow.position.y = -0.185;
    shoulder.add(elbow);
    nodes.arms.push({
      side, shoulder, elbow,
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
 * dark 1メッシュに統合（+1 drawCall）し、前部ライトの eye 材で +1＝計2ドロー。
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
  const bodyMesh = new THREE.Mesh(body, materials.shell);
  bodyMesh.castShadow = true;
  group.add(bodyMesh);
  const light = new THREE.Mesh(new THREE.SphereGeometry(0.045, 12, 8), materials.eye);
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
      head: "white", visor: "visor", eye: "eye", ear: "shell",
      antStem: "shell", antTip: "accent", collar: "white", chest: "accent",
      torso: "shell", pelvis: "shell", upper: "white", fore: "shell",
      hand: "white", thigh: "shell", shin: "white", foot: "dark",
    };
    this.perBody = {
      eye: 2, ear: 2, upper: 2, fore: 2, hand: 2, thigh: 2, shin: 2, foot: 2,
    };
    for (const part of PARTS) {
      const n = capacity * (this.perBody[part] || 1);
      const mesh = new THREE.InstancedMesh(
        this.geoms[part], materials[this.partMaterial[part]], n);
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.castShadow = part !== "visor" && part !== "eye";
      mesh.receiveShadow = false;
      mesh.frustumCulled = false;
      mesh.count = 0;
      scene.add(mesh);
      this.meshes[part] = mesh;
    }
  }

  begin() {
    for (const part of PARTS) this.counts[part] = 0;
  }

  /** 1体ぶんの世界行列を各 InstancedMesh へ書き込む。
   *  accent は胸リング・アンテナ先端のインスタンスカラー（HUDの状態ドットと同じ意味色）。 */
  push(nodes, accent = null) {
    nodes.root.updateMatrixWorld(true);
    const put = (part, obj) => {
      const i = this.counts[part];
      if (i >= this.capacity * (this.perBody[part] || 1)) return;
      this.meshes[part].setMatrixAt(i, obj.matrixWorld);
      if (accent && (part === "chest" || part === "antTip")) {
        this.meshes[part].setColorAt(i, accent);
      }
      this.counts[part] = i + 1;
    };
    for (const part of ["head", "visor", "antStem", "antTip", "collar",
      "chest", "torso", "pelvis"]) {
      put(part, nodes[part]);
    }
    for (const eye of nodes.eyes) put("eye", eye);
    for (const ear of nodes.ears) put("ear", ear);
    for (const arm of nodes.arms) {
      put("upper", arm.upper);
      put("fore", arm.fore);
      put("hand", arm.hand);
    }
    for (const leg of nodes.legs) {
      put("thigh", leg.thigh);
      put("shin", leg.shin);
      put("foot", leg.foot);
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
  }

  dispose() {
    for (const part of PARTS) {
      this.meshes[part].dispose();
      this.geoms[part].dispose();
    }
  }
}

export { PARTS };
