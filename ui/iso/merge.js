// ジオメトリ統合（自前）。
//
// なぜ自前か: three.js の BufferGeometryUtils は addons/ 側で core に入っていない。
// vendor するファイルを増やしたくないので、必要な最小機能だけをここに書く。
//
// これが drawCalls を下げる主力。静的な家具は何十個あっても
// 「マテリアルごとに1メッシュ」へ畳めば、その分だけドローコールが消える。
import * as THREE from "/ui/vendor/three/three.module.min.js";

const ATTRS = ["position", "normal", "uv"];

/**
 * 同じ属性構成の BufferGeometry 群を1つに統合する。
 * 各ジオメトリは呼び出し前に applyMatrix4 でワールド変換を焼き込んでおくこと。
 */
export function mergeGeometries(geometries) {
  const list = geometries.filter(Boolean);
  if (!list.length) return null;
  if (list.length === 1) return list[0];

  // すべて非インデックス化して揃える（インデックスの有無が混ざると結合できない）
  const flat = list.map((g) => (g.index ? g.toNonIndexed() : g));

  const counts = {};
  for (const name of ATTRS) {
    let total = 0;
    for (const g of flat) {
      const a = g.getAttribute(name);
      if (!a) { total = -1; break; }
      total += a.count;
    }
    counts[name] = total;
  }

  const out = new THREE.BufferGeometry();
  for (const name of ATTRS) {
    if (counts[name] <= 0) continue;
    const size = flat[0].getAttribute(name).itemSize;
    const arr = new Float32Array(counts[name] * size);
    let off = 0;
    for (const g of flat) {
      const a = g.getAttribute(name);
      arr.set(a.array.subarray(0, a.count * size), off);
      off += a.count * size;
    }
    out.setAttribute(name, new THREE.BufferAttribute(arr, size));
  }
  out.computeBoundingSphere();
  return out;
}

/**
 * 「置きたい家具のリスト」→「マテリアルごとに統合した Mesh の配列」。
 * 何個置いてもマテリアル数ぶんのドローコールにしかならない。
 */
export function buildStaticBatches(pieces, materials) {
  const byMat = new Map();
  for (const piece of pieces) {
    const { geometry, matrix, material } = piece;
    if (!geometry) continue;
    const g = geometry.clone();
    if (matrix) g.applyMatrix4(matrix);
    if (!byMat.has(material)) byMat.set(material, []);
    byMat.get(material).push(g);
  }
  const meshes = [];
  for (const [matKey, geos] of byMat) {
    const merged = mergeGeometries(geos);
    if (!merged) continue;
    const mesh = new THREE.Mesh(merged, materials[matKey] || materials.white);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.matrixAutoUpdate = false;          // 静的なので毎フレーム再計算しない
    meshes.push(mesh);
    for (const g of geos) g.dispose();
  }
  return meshes;
}
