// ジオメトリ統合（自前）。
//
// なぜ自前か: three.js の BufferGeometryUtils は addons/ 側で core に入っていない。
// vendor するファイルを増やしたくないので、必要な最小機能だけをここに書く。
//
// これが drawCalls を下げる主力。静的な家具は何十個あっても
// 「マテリアルごとに1メッシュ」へ畳めば、その分だけドローコールが消える。
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { vertexAO } from "./bake.js";

const ATTRS = ["position", "normal", "uv", "color"];

const NO_SHADOW = { castShadow: false, receiveShadow: false };
export const STATIC_SHADOWS = {
  sky: NO_SHADOW, glass: NO_SHADOW, glassPane: NO_SHADOW, wallart: NO_SHADOW,
  shadow: NO_SHADOW, islandShadow: NO_SHADOW,
  glowW: NO_SHADOW,
  screen0: NO_SHADOW, screen1: NO_SHADOW, screen2: NO_SHADOW, screen3: NO_SHADOW,
};

/**
 * 同じ属性構成の BufferGeometry 群を1つに統合する。
 * 各ジオメトリは呼び出し前に applyMatrix4 でワールド変換を焼き込んでおくこと。
 */
export function mergeGeometries(geometries) {
  const list = geometries.filter(Boolean);
  if (!list.length) return null;

  // すべて非インデックス化して揃える（インデックスの有無が混ざると結合できない）
  const flat = list.map((g) => (g.index ? g.toNonIndexed() : g));

  const counts = {};
  for (const name of ATTRS) {
    let total = 0;
    for (const g of flat) {
      const a = g.getAttribute(name) || (name === "color" ? g.getAttribute("position") : null);
      if (!a) { total = -1; break; }
      total += a.count;
    }
    counts[name] = total;
  }

  const out = new THREE.BufferGeometry();
  for (const name of ATTRS) {
    if (counts[name] <= 0) continue;
    const size = name === "color" ? 3 : flat[0].getAttribute(name).itemSize;
    const arr = new Float32Array(counts[name] * size);
    let off = 0;
    for (const g of flat) {
      const a = g.getAttribute(name);
      const count = g.getAttribute("position").count;
      if (a) arr.set(a.array.subarray(0, count * size), off);
      else arr.fill(1, off, off + count * size);
      off += count * size;
    }
    out.setAttribute(name, new THREE.BufferAttribute(arr, size));
  }
  out.computeBoundingSphere();
  flat.forEach((g, i) => { if (g !== list[i]) g.dispose(); });
  return out;
}

/**
 * 「置きたい家具のリスト」→「マテリアルごとに統合した Mesh の配列」。
 * 何個置いてもマテリアル数ぶんのドローコールにしかならない。
 */
export function buildStaticBatches(pieces, materials, shadowFlags = STATIC_SHADOWS) {
  const byMat = new Map();
  for (const piece of pieces) {
    const { geometry, matrix } = piece;
    let { material } = piece;
    if (!geometry) continue;
    const g = geometry.clone();
    const mat = materials[material] || materials.white;
    // Sofa aliases share one Physical material; their exact guide colors live in vertices.
    if (material === "sofaB") {
      const target = new THREE.Color(0xffffff), base = materials.linen.color;
      const color = new Float32Array(g.getAttribute("position").count * 3);
      for (let i = 0; i < color.length; i += 3) {
        color[i] = target.r / base.r; color[i + 1] = target.g / base.g; color[i + 2] = target.b / base.b;
      }
      g.setAttribute("color", new THREE.BufferAttribute(color, 3));
    }
    if (material === "sofa" || material === "sofaB") material = "linen";
    // Contact side of each furnishing, before its placement transform.
    if (!piece.ao && !piece.preserveColor && mat.isMeshStandardMaterial && !mat.transparent && material !== "sky") {
      vertexAO(g, { strength: 0.90, height: 0.35 });
    }
    if (matrix) g.applyMatrix4(matrix);
    if (piece.ao) vertexAO(g, piece.ao);
    if (!byMat.has(material)) byMat.set(material, []);
    byMat.get(material).push(g);
  }
  const meshes = [];
  for (const [matKey, geos] of byMat) {
    const merged = mergeGeometries(geos);
    if (!merged) continue;
    const mat = materials[matKey] || materials.white;
    mat.vertexColors = true;
    const mesh = new THREE.Mesh(merged, mat);
    mesh.name = `static:${matKey}`;
    if (matKey === "glass" || matKey === "glassPane") {
      mesh.renderOrder = 10;
      mat.forceSinglePass = true;
    }
    const flags = shadowFlags[matKey] || STATIC_SHADOWS[matKey];
    mesh.castShadow = flags?.castShadow ?? true;
    mesh.receiveShadow = flags?.receiveShadow ?? true;
    mesh.matrixAutoUpdate = false;          // 静的なので毎フレーム再計算しない
    meshes.push(mesh);
    for (const g of geos) g.dispose();
  }
  return meshes;
}
