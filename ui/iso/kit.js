// R90-V4: pure furniture builders. Inputs are metres; outputs are local, AO-baked
// pieces for buildStaticBatches. No scene, material allocation, clock or random state.
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { DEFAULT_SPEC, FURNITURE as F } from "/ui/core/layout_specs.js";
import { vertexAO } from "./bake.js";
import { mergeGeometries } from "./merge.js";
import { leafCardGeometry } from "./plants.js";
import { GEN } from "./gen/index.js";

const slabCache = new Map();
/** Rounded horizontal slab, with exact outside dimensions and an optional bevel. */
export function slab(w, h, d, r = .008, bevel = .02) {
  const key = `${w}|${h}|${d}|${r}|${bevel}`;
  if (!slabCache.has(key)) {
    const radius = Math.max(0, Math.min(r, w / 2 - .0001, d / 2 - .0001));
    const b = Math.max(0, Math.min(bevel, h / 3, radius / 3));
    const rr = Math.max(0, radius - b), x = -w / 2 + b, z = -d / 2 + b;
    const ww = w - 2 * b, dd = d - 2 * b;
    const shape = new THREE.Shape();
    shape.moveTo(x + rr, z);
    shape.lineTo(x + ww - rr, z); shape.quadraticCurveTo(x + ww, z, x + ww, z + rr);
    shape.lineTo(x + ww, z + dd - rr); shape.quadraticCurveTo(x + ww, z + dd, x + ww - rr, z + dd);
    shape.lineTo(x + rr, z + dd); shape.quadraticCurveTo(x, z + dd, x, z + dd - rr);
    shape.lineTo(x, z + rr); shape.quadraticCurveTo(x, z, x + rr, z);
    const g = new THREE.ExtrudeGeometry(shape, {
      depth: h - 2 * b, bevelEnabled: b > 0, bevelThickness: b, bevelSize: b,
      bevelSegments: 3, curveSegments: 12,
    });
    g.rotateX(-Math.PI / 2).translate(0, b - h / 2, 0);
    g.computeVertexNormals();
    slabCache.set(key, g);
  }
  // Each caller owns its geometry; neither placement nor AO contaminates the cache.
  return slabCache.get(key).clone();
}

export const at = (x = 0, y = 0, z = 0, yaw = 0) =>
  new THREE.Matrix4().makeRotationY(yaw).setPosition(x, y, z);
export const flat = (w, d) => new THREE.PlaneGeometry(w, d).rotateX(-Math.PI / 2);
const box = (w, h, d) => new THREE.BoxGeometry(w, h, d);
const cyl = (r, h, bottom = r) => new THREE.CylinderGeometry(r, bottom, h, 12);
const vertical = (w, h, d, r = .008) => slab(w, d, h, r).rotateX(Math.PI / 2);

/** Vertex tint ratios let existing materials supply paper/caps/wood without new keys. */
export function tint(geometry, target, base = 0xffffff) {
  const a = new THREE.Color(target), b = new THREE.Color(base);
  const previous = geometry.getAttribute("color"), count = geometry.getAttribute("position").count;
  const color = new Float32Array(count * 3), ratio = [a.r / b.r, a.g / b.g, a.b / b.b];
  for (let i = 0; i < count; i++) for (let k = 0; k < 3; k++) {
    color[i * 3 + k] = ratio[k] * (previous ? previous.array[i * 3 + k] : 1);
  }
  geometry.setAttribute("color", new THREE.BufferAttribute(color, 3));
  return geometry;
}

function builder(spec) {
  const pieces = [], parent = at(spec.x, spec.y, spec.z, spec.yaw);
  const put = (geometry, material, x = 0, y = 0, z = 0, yaw = 0) => {
    // Bake exactly once, in the part's upright local space.
    vertexAO(geometry, { strength: .90, height: .35 });
    pieces.push({ geometry, material: material === "sofaB" ? "linen" : material,
      matrix: parent.clone().multiply(at(x, y, z, yaw)), preserveColor: true });
  };
  return { pieces, put };
}

// ---- R96-D: Tripo 生成什器 ---------------------------------------------------------
const genCache = new Map();
/** 生成モジュール（tools/glb_to_geom.py の出力）→ 部品ごとの BufferGeometry（ローカル空間・底面 y=0・中心 x/z=0）。 */
function genGeometries(mod) {
  if (genCache.has(mod)) return genCache.get(mod);
  const bytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const [lo, hi] = mod.bbox, cx = (lo[0] + hi[0]) / 2, cz = (lo[2] + hi[2]) / 2;
  const out = mod.parts.map((part) => {
    const q = new Int16Array(bytes(part.pos).buffer), n = part.n;
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n * 3; i += 3) {
      pos[i] = q[i] * mod.scale + mod.offset[0] - cx;
      pos[i + 1] = q[i + 1] * mod.scale + mod.offset[1] - lo[1];
      pos[i + 2] = q[i + 2] * mod.scale + mod.offset[2] - cz;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    if (part.nrm) {
      const qn = new Int8Array(bytes(part.nrm).buffer), nrm = new Float32Array(n * 3);
      for (let i = 0; i < n * 3; i++) nrm[i] = qn[i] / 127;
      g.setAttribute("normal", new THREE.BufferAttribute(nrm, 3));
    }
    if (part.col) {
      // 焼いた色は sRGB のバイト。three の頂点色はリニア前提なので、ここで伝達関数を戻す。
      const qc = bytes(part.col), col = new Float32Array(n * 3);
      for (let i = 0; i < n * 3; i++) {
        const c = qc[i] / 255;
        col[i] = c <= .04045 ? c / 12.92 : Math.pow((c + .055) / 1.055, 2.4);
      }
      g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    }
    g.setIndex(new THREE.BufferAttribute(new Uint16Array(bytes(part.idx).buffer), 1));
    if (!part.nrm) g.computeVertexNormals();
    return { name: part.name, geometry: g, size: [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]] };
  });
  genCache.set(mod, out);
  return out;
}

/**
 * 生成什器を置く。無ければ null（呼び出し側が procedural へ落ちる）。
 * spec.w/d/h のどれか（manifest の fit・既定 w）に合わせて一様スケール。yaw で正面を +z へ。
 */
export function generated(name, spec = {}, opts = {}) {
  const mod = GEN[name];
  if (!mod) return null;
  const s = { yaw: 0, fit: "w", fitH: "top", ...(mod.place || {}), ...opts };
  const { put, pieces } = builder(spec);
  const parts = genGeometries(mod), raw = parts[0].size;
  // yaw を掛けた後の外形で合わせる（長辺が z に出た生成物は yaw=π/2 で x へ回してから spec.w に合わせる）。
  const c = Math.abs(Math.cos(s.yaw)), sn = Math.abs(Math.sin(s.yaw));
  const size = [c * raw[0] + sn * raw[2], raw[1], sn * raw[0] + c * raw[2]];
  // fit: "w"|"d"|"h" は一様スケール。"box" は w/d/h を個別に合わせる（比率を指定して生成した物だけ＝歪みは小さい）。
  // "box" の高さは「一番大きい上向きの面」（天板・座面）を spec.h に合わせる＝仕切りや背もたれは上へ自然にはみ出す。
  const topH = mod.top != null && s.fitH !== "full" ? mod.top - mod.bbox[0][1] : size[1];
  const axis = { w: [spec.w, size[0]], d: [spec.d, size[2]], h: [spec.h, size[1]], top: [spec.h, topH] };
  const ratio = ([want, have]) => (want && have ? want / have : 1);
  let kx, ky, kz;
  if (s.fit === "box") { kx = ratio(axis.w); ky = ratio(axis.top); kz = ratio(axis.d); }
  else kx = ky = kz = ratio(s.fit === "h" ? axis.top : axis[s.fit] || axis.w);
  // 回転してから世界軸で拡縮する（R·S だと拡縮が生成物のローカル軸に掛かり、yaw=π/2 の物は w/d が入れ替わる＝別モデルレビューの実測）。
  const m = new THREE.Matrix4().makeScale(kx, ky, kz).multiply(new THREE.Matrix4().makeRotationY(s.yaw));
  for (const part of parts) {
    const g = part.geometry.clone().applyMatrix4(m);
    // 頂点 AO は builder の put が焼く（色は保持＝preserveColor）。材質は 1 つ・頂点色で塗り分ける。
    put(g, "generated", s.dx || 0, s.dy || 0, s.dz || 0);
  }
  return pieces;
}

export function desk(spec = {}) {
  const s = { ...F.desk, ...DEFAULT_SPEC.desks.top, ...spec }, { put, pieces } = builder(s);
  if (!s.table) { const g = generated(s.gen || "desk", s); if (g) return g; }
  // A light oak edge band occupies the lower 6 mm of the 30 mm top.
  put(slab(s.w, s.top, s.d, s.radius), "wood", 0, s.h - s.top / 2);
  put(tint(slab(s.w, s.edge, s.d, s.radius), 0xf1ddba, 0xd3b48a), "wood2",
    0, s.h - s.top + s.edge / 2);
  if (s.table) {
    for (const x of [-1, 1]) for (const z of [-1, 1])
      put(cyl(.025, s.h - s.top, .018), "steel", x * (s.w / 2 - .18),
        (s.h - s.top) / 2, z * (s.d / 2 - .18));
  } else {
    for (const x of [-1, 1]) put(box(s.panel, s.h - s.top, s.d - .12), "white",
      x * (s.w / 2 - .08), (s.h - s.top) / 2);
    put(box(s.w - .16, .36, s.panel), "white", 0, s.h - s.top - .18);
  }
  return pieces;
}

export function meetingTable(spec = {}) {
  const s = { w: 4.2, d: 1.6, h: .86, ...spec }, { put, pieces } = builder(s);
  { const g = generated("meeting_table", s); if (g) return g; }
  const top = .065, radius = Math.min(s.w, s.d) / 2;
  put(slab(s.w, top, s.d, radius), "white", 0, s.h - top / 2);
  put(slab(s.w - .08, .025, s.d - .08, radius - .04), "wood2", 0, s.h - top - .0125);
  for (const side of [-1, 1]) {
    put(slab(.50, .04, s.d * .60, .13), "steel", side * s.w * .27, .02);
    put(slab(.26, s.h - top - .065, s.d * .48, .10), "white",
      side * s.w * .27, (s.h - top - .025 + .04) / 2);
  }
  // A flush cable channel reads as a usable conference table without another screen.
  put(slab(Math.min(.85, s.w * .28), .009, .115, .045), "darker", 0, s.h - .0045);
  for (const side of [-1, 1]) put(box(.022, .002, .065), "steel", side * .09, s.h - .0005);
  return pieces;
}

export function roundTable(spec = {}) {
  const s = { w: 1.25, d: 1.25, h: .36, ...spec }, { put, pieces } = builder(s), top = .06;
  put(new THREE.CylinderGeometry(1, 1, top, 48).scale(s.w / 2, 1, s.d / 2), "white",
    0, s.h - top / 2);
  put(new THREE.CylinderGeometry(1, 1, .035, 32).scale(s.w * .31, 1, s.d * .31), "wood2", 0, .0175);
  put(cyl(Math.min(s.w, s.d) * .14, s.h - top - .035, Math.min(s.w, s.d) * .20), "wood2",
    0, (s.h - top + .035) / 2);
  return pieces;
}

export function floorLamp(spec = {}) {
  const s = { w: .65, d: .65, h: 1.8, ...spec }, { put, pieces } = builder(s);
  { const g = generated("floor_lamp", s); if (g) return g; }
  const radius = Math.min(s.w, s.d) * .46, shadeH = Math.min(.36, s.h * .24);
  put(new THREE.CylinderGeometry(radius * .75, radius * .75, .035, 32), "steel", 0, .0175);
  put(cyl(.016, s.h - shadeH * .50 - .035), "steel", 0, (s.h - shadeH * .50 + .035) / 2);
  // The shade is an opaque ceramic form; room lighting supplies all of its light.
  put(new THREE.CylinderGeometry(radius * .64, radius, shadeH, 32), "white", 0, s.h - shadeH / 2);
  put(new THREE.TorusGeometry(radius - .008, .008, 6, 32).rotateX(Math.PI / 2), "wood2",
    0, s.h - shadeH + .008);
  return pieces;
}

export function stool(spec = {}) {
  const s = { w: .50, d: .50, h: .73, ...spec }, { put, pieces } = builder(s), seatH = .105;
  { const g = generated("stool", s); if (g) return g; }
  put(slab(s.w, seatH, s.d, Math.min(s.w, s.d) / 2), s.material || "seatB", 0, s.h - seatH / 2);
  put(slab(s.w * .74, .028, s.d * .74, .10), "steel", 0, s.h - seatH - .014);
  const legH = s.h - seatH - .028, x = s.w * .29, z = s.d * .29;
  for (const sideX of [-1, 1]) for (const sideZ of [-1, 1])
    put(cyl(.018, legH), "steel", sideX * x, legH / 2, sideZ * z);
  for (const side of [-1, 1]) {
    put(box(x * 2, .02, .02), "steel", 0, s.h * .31, side * z);
    put(box(.02, .02, z * 2), "steel", side * x, s.h * .31);
  }
  return pieces;
}

export function chair(spec = {}) {
  const s = { ...F.chair, ...spec }, { put, pieces } = builder(s), mat = s.material || "seat";
  // 生成椅子は座面の高さ（一番大きい上向きの面）を s.seat に合わせる＝ロボの着席位置はそのまま。
  { const g = generated("chair", { ...s, h: s.seat }); if (g) return g; }
  put(slab(s.w, s.seatH, s.d, s.radius), mat, 0, s.seat);
  // Open mesh back, with a 5 cm frame. Fine intersecting ribbons reveal the room behind it.
  const rail = .018, bw = s.w - .02;
  for (const side of [-1, 1]) {
    put(vertical(rail, s.backH, s.backD), "white", side * (bw - rail) / 2, s.backY, s.backZ);
    put(vertical(bw, rail, s.backD), "white", 0, s.backY + side * (s.backH - rail) / 2, s.backZ);
    // Two L arms: vertical riser + horizontal pad.
    put(box(.035, s.armY - s.seat, .035), "dark", side * s.armX, (s.armY + s.seat) / 2, -.13);
    put(slab(.05, .04, .33, .02), "dark", side * s.armX, s.armY, .015);
  }
  const mesh = [];
  for (let i = 1; i < 14; i++) mesh.push(box(.007, s.backH - rail * 2, .011)
    .translate(-bw / 2 + i * bw / 14, s.backY, s.backZ));
  for (let i = 1; i < 18; i++) mesh.push(box(bw - rail * 2, .007, .011)
    .translate(0, s.backY - s.backH / 2 + i * s.backH / 18, s.backZ));
  put(mergeGeometries(mesh), mat); mesh.forEach((g) => g.dispose());
  put(cyl(.035, .27), "steel", 0, .225);
  for (let i = 0; i < 5; i++) {
    const angle = i * Math.PI * 2 / 5, x = Math.cos(angle), z = Math.sin(angle);
    put(box(s.baseRadius, .035, .045), "steel", x * s.baseRadius / 2, .10, z * s.baseRadius / 2, -angle);
    put(new THREE.SphereGeometry(s.caster, 10, 8), "dark", x * s.baseRadius, s.caster, z * s.baseRadius);
  }
  return pieces;
}

/** Six subdivisions per face; rounded seams and a 3 cm face-normal cushion bulge. */
export function puff(w, h, d, radius = F.sofa.radius) {
  const g = new THREE.BoxGeometry(w, h, d, 6, 6, 6), p = g.getAttribute("position"), n = g.getAttribute("normal");
  const half = [w / 2, h / 2, d / 2], r = Math.min(radius, ...half), core = half.map((v) => v - r);
  for (let i = 0; i < p.count; i++) {
    const v = [p.getX(i), p.getY(i), p.getZ(i)], normal = [n.getX(i), n.getY(i), n.getZ(i)];
    const axes = [0, 1, 2].filter((k) => normal[k] === 0);
    const [u, t] = axes.map((k) => v[k] / half[k]);
    const push = (1 - u * u) * (1 - t * t) * F.sofa.puff;
    const inner = v.map((a, k) => Math.max(-core[k], Math.min(core[k], a)));
    const delta = v.map((a, k) => a - inner[k]), length = Math.hypot(...delta);
    p.setXYZ(i, ...v.map((a, k) => inner[k] + delta[k] / length * r + normal[k] * push));
  }
  g.computeVertexNormals();
  return g;
}

export function sofa(spec = {}) {
  const s = { ...F.sofa, ...spec }, { put, pieces } = builder(s), mat = s.material || "linen";
  if (!s.pouf) { const g = generated("sofa", { ...s, h: s.seat }); if (g) return g; }
  else { const g = generated("pouf", s); if (g) return g; }
  const pad = F.sofa.puff, count = s.seats ?? Math.max(1, Math.round(s.w / .95));
  if (!s.pouf) {
    const width = (s.w - s.arm * 2) / count;
    for (let i = 0; i < count; i++) {
      const x = (i - (count - 1) / 2) * width;
      put(puff(width - .025 - pad * 2, s.seatH - pad * 2, s.d - s.back - pad * 2), mat,
        x, s.seat - s.seatH / 2, s.back / 2);
      put(puff(width - .025 - pad * 2, s.h - s.seat - pad * 2, s.back - pad * 2), mat,
        x, (s.h + s.seat) / 2, -(s.d - s.back) / 2);
    }
    for (const side of [-1, 1]) put(puff(s.arm - pad * 2, s.h * .68 - s.leg - pad * 2, s.d - pad * 2), mat,
      side * (s.w - s.arm) / 2, (s.h * .68 + s.leg) / 2);
    put(puff(.44, .40, .13), s.cushion || "cushionB", -s.w * .20, s.seat + .22, -s.d * .14, -.12);
  } else put(puff(s.w - pad * 2, s.h - s.leg - pad * 2, s.d - pad * 2), mat, 0, (s.h + s.leg) / 2);
  for (const x of [-1, 1]) for (const z of [-1, 1]) put(cyl(.026, s.leg, .015), "dark",
    x * (s.w / 2 - .12), s.leg / 2, z * (s.d / 2 - .12));
  return pieces;
}

export function partition(spec = {}) {
  const s = { ...F.partition, ...spec }, { put, pieces } = builder(s);
  put(vertical(s.w, s.h, s.d), s.material || "felt", 0, s.h / 2);
  for (const side of [-1, 1]) {
    put(box(s.frame, s.h, s.d + .006), "white", side * (s.w - s.frame) / 2, s.h / 2);
    put(box(s.w, s.frame, s.d + .006), "white", 0, s.h / 2 + side * (s.h - s.frame) / 2);
  }
  return pieces;
}

export function pendant(spec = {}) {
  const s = { ...F.pendant, ...spec }, { put, pieces } = builder(s);
  const profile = Array.from({ length: 12 }, (_, i) => {
    const t = i / 11 * Math.PI / 2;
    return new THREE.Vector2(s.radius * (.08 + .92 * Math.sin(t)), s.h * Math.cos(t));
  }).reverse(); // rim -> crown gives outward normals
  const outer = new THREE.LatheGeometry(profile, 24), inner = outer.clone().scale(.97, .97, .97);
  const index = inner.index;
  for (let i = 0; i < index.count; i += 3) {
    const b = index.getX(i + 1); index.setX(i + 1, index.getX(i + 2)); index.setX(i + 2, b);
  }
  inner.computeVertexNormals();
  put(outer, "rattan"); put(inner, s.nonEmissive ? "white" : "lampWarm");
  if (s.cordH > 0) put(cyl(s.cord, s.cordH), "dark", 0, s.h + s.cordH / 2);
  return pieces;
}

export function frame(spec = {}) {
  const s = { ...F.frame, ...spec }, { put, pieces } = builder(s);
  const print = new THREE.PlaneGeometry(s.w, s.h), uv = print.getAttribute("uv");
  for (let i = 0; i < uv.count; i++) uv.setX(i, ((s.index ?? 0) % 2 + .01 + uv.getX(i) * .98) / 2);
  put(print, "wallart", 0, 0, s.d / 2 + .001);
  put(box(s.w, s.h, .01), "paper", 0, 0, -.01);
  for (const side of [-1, 1]) {
    put(vertical(s.rail, s.h + s.rail * 2, s.d), "wood2", side * (s.w + s.rail) / 2);
    put(vertical(s.w, s.rail, s.d), "wood2", 0, side * (s.h + s.rail) / 2);
  }
  return pieces;
}

export function counter(spec = {}) {
  const s = { ...F.counter, ...spec }, { put, pieces } = builder(s);
  { const g = generated("counter", s); if (g) return g; }
  put(slab(s.w, s.top, s.d, .12), "wood", 0, s.h - s.top / 2);
  put(slab(s.w - .08, .10, s.d - .08, .10), "white", 0, .05);
  put(box(s.w - .12, s.h - s.top - .10, s.d - .12), "wood2", 0, (s.h - s.top + .10) / 2);
  const ribs = [], count = Math.floor((s.w - .24) / s.pitch);
  for (let i = 0; i <= count; i++) ribs.push(cyl(s.flute, s.h - s.top - .10)
    .translate((i - count / 2) * s.pitch, (s.h - s.top + .10) / 2, s.d / 2 - .055));
  put(tint(mergeGeometries(ribs), 0xac8356, 0xc9a86a), "signWood"); ribs.forEach((g) => g.dispose());
  return pieces;
}

export function cafeCounter(spec = {}) {
  const s = { w: 3.4, d: .90, h: 1.06, ...spec }, { put, pieces } = builder(s), top = .055;
  { const g = generated("cafe_counter", s); if (g) return g; }
  put(slab(s.w - .14, .075, s.d - .12, .08), "steel", 0, .0375);
  put(slab(s.w - .08, s.h - top - .075, s.d - .08, .085), "white", 0, (s.h - top + .075) / 2);
  put(slab(s.w, top, s.d, .10), "white", 0, s.h - top / 2);
  const ribs = [], count = Math.max(1, Math.floor((s.w - .22) / .065));
  for (let i = 0; i <= count; i++) ribs.push(slab(.025, s.h - top - .15, .025, .01)
    .translate((i - count / 2) * (s.w - .22) / count, (s.h - top + .15) / 2, s.d / 2 - .036));
  put(tint(mergeGeometries(ribs), 0xe8e8f4, 0xf6f7fd), "white"); ribs.forEach((g) => g.dispose());
  const machineX = -s.w * .27, machineD = Math.min(.35, s.d * .52), machineW = Math.min(.46, s.w * .23);
  put(slab(machineW, .39, machineD, .04), "darker", machineX, s.h + .195, -.06);
  put(box(machineW * .80, .17, .018), "steel", machineX, s.h + .285, -.06 + machineD / 2);
  put(box(machineW * .28, .058, .006), "darker", machineX, s.h + .32, -.047 + machineD / 2);
  put(slab(machineW * .78, .02, machineD * .76, .035), "steel", machineX, s.h + .012, .035);
  put(box(.055, .045, .06), "steel", machineX, s.h + .18, .04 + machineD / 2);
  const trayX = s.w * .23, trayW = Math.min(.76, s.w * .36), trayD = Math.min(.31, s.d * .47);
  put(slab(trayW, .022, trayD, .055), "wood2", trayX, s.h + .011, .04);
  for (let i = 0; i < 3; i++) {
    const x = trayX + (i - 1) * trayW * .27, y = s.h + .022, mat = ["mugB", "white", "mugC"][i];
    put(new THREE.CylinderGeometry(.045, .038, .11, 20), mat, x, y + .055, .04);
    put(new THREE.TorusGeometry(.025, .007, 6, 12), mat, x + .046, y + .06, .04);
    put(cyl(.034, .003), "darker", x, y + .105, .04);
  }
  return pieces;
}

export function mediaWall(spec = {}) {
  const s = { w: 2.2, d: .12, h: 1.85, screen: true, ...spec }, { put, pieces } = builder(s);
  const panelH = Math.min(s.h - .42, s.w * .62), panelY = s.h - panelH / 2, frame = .055;
  put(vertical(s.w, panelH, s.d * .58, .035), "white", 0, panelY, -s.d * .12);
  put(vertical(s.w - frame * 2, panelH - frame * 2, s.d * .12, .015), s.screen ? "darker" : "white",
    0, panelY, s.d * .23);
  for (const side of [-1, 1]) {
    put(box(.035, s.h - panelH + .04, s.d * .48), "steel", side * s.w * .32, (s.h - panelH + .04) / 2);
    put(slab(s.w * .25, .035, s.d, .025), "steel", side * s.w * .32, .0175);
  }
  if (!s.screen) {
    put(box(s.w * .42, .025, s.d * .72), "steel", 0, panelY - panelH / 2 + .055, s.d * .14);
    for (const side of [-1, 1]) put(box(.12, .016, .018), side < 0 ? "bookA" : "bookB",
      side * .10, panelY - panelH / 2 + .076, s.d * .20);
  }
  return pieces;
}

export function shelf(spec = {}) {
  const s = { ...F.shelf, ...spec }, { put, pieces } = builder(s), t = s.panel;
  { const g = generated("shelf", s); if (g) return g; }
  put(box(s.w, s.h, t), "wood2", 0, s.h / 2, -(s.d - t) / 2);
  for (const side of [-1, 1]) put(box(t, s.h, s.d), "wood2", side * (s.w - t) / 2, s.h / 2);
  for (let row = 0; row <= s.rows; row++) put(box(s.w, t, s.d), "wood", 0, t / 2 + (s.h - t) * row / s.rows);
  if (s.sideboard) {
    for (const side of [-1, 1]) {
      put(box(s.w / 2 - .02, s.h - t * 2, t), "wood2", side * s.w / 4, s.h / 2, (s.d - t) / 2 - .025);
      put(box(.12, .018, .025), "dark", side * .10, s.h * .70, s.d / 2 - .0125);
    }
  } else {
    const count = Math.max(3, Math.floor((s.w - t * 2) / .17));
    for (let row = 0; row < s.rows; row++) for (let i = 0; i < count; i++) {
      if ((i + row) % 7 === 6) continue;
      const h = Math.min(.34, s.h / s.rows * .75) * (1 - (i % 3) * .07);
      put(box(.10 + i % 3 * .012, h, s.d * .70), ["bookA", "bookB", "bookC", "bookD", "bookE"][(i + row) % 5],
        (i - (count - 1) / 2) * .17, t + (s.h - t) * row / s.rows + h / 2, .025);
    }
  }
  return pieces;
}

/** Segment list uses local x/z/yaw; callers can leave a doorway open explicitly. */
export function glassBox(spec = {}) {
  const s = { w: DEFAULT_SPEC.layout.meetZone.w, d: DEFAULT_SPEC.layout.meetZone.d,
    ...F.glassBox, ...spec }, { put, pieces } = builder(s), t = s.rail;
  const walls = s.walls || [
    { w: s.w, z: -(s.d - t) / 2 }, { w: s.d - t * 2, x: -(s.w - t) / 2, yaw: Math.PI / 2 },
    { w: s.w, z: (s.d - t) / 2 }, { w: s.d - t * 2, x: (s.w - t) / 2, yaw: Math.PI / 2 },
  ];
  for (const wall of walls) {
    const base = at(wall.x, 0, wall.z, wall.yaw);
    const add = (g, mat, x, y) => { g.applyMatrix4(base.clone().multiply(at(x, y))); put(g, mat); };
    add(new THREE.PlaneGeometry(wall.w - t, s.h - t * 2), "glass", 0, s.h / 2);
    for (const y of [t / 2, s.h - t / 2]) add(box(wall.w, t, t), "dark", 0, y);
    const count = Math.max(1, Math.ceil(wall.w / s.pitch));
    for (let i = 0; i <= count; i++) add(box(t, s.h, t), "dark", -wall.w / 2 + t / 2 + i * (wall.w - t) / count, s.h / 2);
  }
  // Keep the glass batch separate from windows; buildOffice binds it to glassPane (.10).
  // buildStaticBatches retains renderOrder=10 without changing geometry or draw count.
  return pieces;
}

/** Four glass walls with a clear opening, continuous head rail and a door pull. */
export function roomShell(spec = {}) {
  const s = { ...F.glassBox, ...spec }, { put, pieces } = builder(s), t = s.rail;
  const door = s.door || { side: "south", offset: 0, w: 1.1 };
  const walls = [];
  for (const side of ["north", "south", "west", "east"]) {
    const horizontal = side === "north" || side === "south";
    const span = (horizontal ? s.w : s.d) - t;
    const place = (w, along) => walls.push(horizontal
      ? { w, x: along, z: (side === "north" ? -1 : 1) * (s.d - t) / 2 }
      : { w, z: along, x: (side === "west" ? -1 : 1) * (s.w - t) / 2, yaw: Math.PI / 2 });
    if (side !== door.side) { place(span, 0); continue; }
    const left = door.offset - door.w / 2, right = door.offset + door.w / 2;
    place(left + span / 2, (left - span / 2) / 2);
    place(span / 2 - right, (right + span / 2) / 2);
    const edge = (side === "north" || side === "west" ? -1 : 1)
      * ((horizontal ? s.d : s.w) - t) / 2;
    put(box(horizontal ? door.w : t, t, horizontal ? t : door.w), "steel",
      horizontal ? door.offset : edge, s.h - t / 2, horizontal ? edge : door.offset);
    put(box(.025, .30, .025), "steel", horizontal ? left - .08 : edge,
      1.02, horizontal ? edge : left - .08);
    const plaqueW = Math.min(.34, span / 2 - right - .04);
    put(box(horizontal ? plaqueW : .025, .13, horizontal ? .025 : plaqueW), "wood2",
      horizontal ? right + .02 + plaqueW / 2 : edge, 1.62,
      horizontal ? edge : right + .02 + plaqueW / 2);
  }
  return [...pieces, ...glassBox({ ...s, walls })];
}

export function phoneBooth(spec = {}) {
  const s = { w: 1.35, d: 1.65, h: 2.25, ...spec }, { put, pieces } = builder(s);
  put(slab(s.w - .08, .035, s.d - .08, .06), "rugB", 0, .0175);
  put(box(s.w - .12, s.h - .12, .04), "panelA", 0, s.h / 2, -s.d / 2 + .08);
  put(slab(s.w - .25, .055, .42, .08), "white", 0, 1.02, -.38);
  put(box(.08, 1.0, .08), "steel", 0, .5, -.45);
  const parent = at(s.x, s.y, s.z, s.yaw);
  for (const piece of stool({ w: .45, d: .45, h: .65, z: .18 })) {
    piece.matrix.premultiply(parent); pieces.push(piece);
  }
  return [...pieces, ...roomShell({ ...s, door: { side: "south", offset: 0, w: .95 } })];
}

export function waterStation(spec = {}) {
  const s = { w: .58, d: .52, h: 1.25, ...spec }, { put, pieces } = builder(s);
  { const g = generated("water_station", s); if (g) return g; }
  put(slab(s.w, s.h * .82, s.d, .05), "white", 0, s.h * .41);
  put(slab(s.w * .72, .06, s.d * .65, .03), "darker", 0, s.h * .76, .04);
  put(box(s.w * .65, .30, .04), "wood2", 0, s.h * .87, -s.d * .35);
  for (const [i, x] of [-.09, .09].entries()) put(box(.07, .07, .05), i ? "bookB" : "bookA",
    x, s.h * .92, -.06);
  return pieces;
}

export function planter(spec = {}) {
  const s = { ...F.planter, ...spec }, { put, pieces } = builder(s), t = s.rim;
  put(slab(s.w, t, s.d, .08), "white", 0, t / 2);
  for (const side of [-1, 1]) {
    put(box(s.w, s.h, t), "white", 0, s.h / 2, side * (s.d - t) / 2);
    put(box(t, s.h, s.d), "white", side * (s.w - t) / 2, s.h / 2);
  }
  put(box(s.w - t * 2, .02, s.d - t * 2), "dark", 0, s.h - .07);
  const reach = s.d * .30, count = Math.floor((s.w - reach * 2) / s.pitch);
  for (let i = 0; i <= count; i++) for (let row = 0; row < 3; row++) {
    const leaf = leafCardGeometry(s.leafW, s.leafH, 2, row === 1);
    leaf.rotateX(.40 + (i % 3) * .16).rotateY(i * 2.399963 + row * 1.7);
    leaf.computeBoundingBox();
    const b = leaf.boundingBox, extent = Math.max(Math.abs(b.min.x), b.max.x, Math.abs(b.min.z), b.max.z);
    const scale = Math.min(1, reach / extent);
    leaf.scale(scale, 1, scale);
    put(leaf, "leafCard", (i - count / 2) * s.pitch, s.h - .035, (row - 1) * s.d * .15);
  }
  return pieces;
}

export function sign(spec = {}) {
  const s = { ...F.sign, ...spec }, { put, pieces } = builder(s);
  put(vertical(s.w, s.h, s.d, .025), "signWood", 0, s.boardY);
  put(box(s.post, s.boardY - s.h / 2, s.post), "wood2", 0, (s.boardY - s.h / 2) / 2);
  put(slab(s.footW, .03, s.footD, .025), "wood2", 0, .015);
  // Project text belongs to HTML labels, never a CanvasTexture or mesh here.
  return pieces;
}

/** Cutaway wall: paper inside (+Z), neutral shell outside, white cut caps. */
export function wall(spec = {}) {
  const s = { ...F.wall, ...spec }, { put, pieces } = builder(s);
  const wallBox = (w, h, d) => {
    const g = box(w, h, d), n = g.getAttribute('normal'), count = n.count;
    const base = new THREE.Color(0xf1ede7), color = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const target = new THREE.Color(n.getY(i) > .5 || Math.abs(n.getX(i)) > .5
        ? 0xfbfbfb : n.getZ(i) < -.5 ? 0xbcbcbc : 0xf6f2ec);
      color.set([target.r / base.r, target.g / base.g, target.b / base.b], i * 3);
    }
    g.setAttribute('color', new THREE.BufferAttribute(color, 3));
    return g;
  };
  if (!s.windows) put(wallBox(s.w, s.h, s.d), 'white', 0, s.h / 2);
  else {
    const sill = .34, lintel = .22, opening = s.h - sill - lintel;
    put(wallBox(s.w, sill, s.d), 'white', 0, sill / 2);
    put(wallBox(s.w, lintel, s.d), 'white', 0, s.h - lintel / 2);
    const count = Math.max(1, Math.round(s.w / 2.8)), pitch = s.w / count, jamb = .12;
    for (let i = 0; i <= count; i++) put(wallBox(jamb, opening, s.d), 'white',
      -s.w / 2 + jamb / 2 + i * (s.w - jamb) / count, sill + opening / 2);
    for (let i = 0; i < count; i++) {
      const pane = new THREE.PlaneGeometry(pitch - jamb, opening), uv = pane.getAttribute('uv');
      for (let k = 0; k < uv.count; k++) uv.setX(k, (i + uv.getX(k)) / count);
      put(pane, 'sky', (i - (count - 1) / 2) * pitch, sill + opening / 2);
      put(new THREE.PlaneGeometry(pitch - jamb, opening), 'glassPane',
        (i - (count - 1) / 2) * pitch, sill + opening / 2, .012);
    }
  }
  put(tint(box(s.w, s.skirting, .025), 0xebe7e2, 0xf1ede7), 'white',
    0, s.skirting / 2, s.d / 2 + .0125);
  return pieces;
}
