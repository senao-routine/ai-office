// Deterministic vertex AO and a canvas light map. No stochastic sampling/filter blur.
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { obstacleRects } from "/ui/core/nav.js";

/** Rules use the geometry's current coordinate space; bake before merging. */
export function vertexAO(geometry, rules = {}) {
  geometry.computeBoundingBox();
  const position = geometry.getAttribute("position");
  const previous = geometry.getAttribute("color");
  const color = new Float32Array(position.count * 3);
  const bottom = rules.bottom ?? geometry.boundingBox.min.y;
  const height = Math.max(0.001, rules.height ?? 0.45);
  const strength = rules.strength ?? 0.90;
  const corners = rules.corners ?? [];
  for (let i = 0; i < position.count; i++) {
    const t = THREE.MathUtils.smoothstep(position.getY(i), bottom, bottom + height);
    let ao = strength + (1 - strength) * t;
    for (const { x, z, radius = 0.6, strength: cornerStrength = 0.93 } of corners) {
      const d = Math.hypot(position.getX(i) - x, position.getZ(i) - z);
      ao *= THREE.MathUtils.lerp(cornerStrength, 1, THREE.MathUtils.smoothstep(d, 0, radius));
    }
    color[i * 3] = (previous ? previous.getX(i) : 1) * ao;
    color[i * 3 + 1] = (previous ? previous.getY(i) : 1) * ao;
    color[i * 3 + 2] = (previous ? previous.getZ(i) : 1) * ao;
  }
  geometry.setAttribute("color", new THREE.BufferAttribute(color, 3));
  return geometry;
}

// Linear irradiance shared by the canvas light map and raised floor/rug vertices.
// Six nested rounded footprints provide a deterministic contact penumbra in metres.
const FLOOR_PEAK = 1.15, FLOOR_FAR = .92, FLOOR_MIN = .72;
const FLOOR_PADS = Object.freeze([.45, .32, .22, .14, .07, 0]);
const FLOOR_ATTENUATION = Math.pow(FLOOR_MIN / FLOOR_PEAK, 1 / FLOOR_PADS.length);

/** Pure world-space sample. model = {LAYOUT:{floor}, obstacleRects, windows}.
 * Windows are segments {x1,z1,x2,z2}; no canvas, globals, mutation or random state.
 */
export function floorAOAt(model, x, z) {
  const floor = model.LAYOUT?.floor ?? model.floor;
  const reach = Math.max(1, Math.min(floor.w, floor.d) * .65);
  let distance = reach;
  for (const w of model.windows ?? []) {
    const dx = w.x2 - w.x1, dz = w.z2 - w.z1;
    const t = THREE.MathUtils.clamp(((x - w.x1) * dx + (z - w.z1) * dz)
      / Math.max(.00001, dx * dx + dz * dz), 0, 1);
    distance = Math.min(distance, Math.hypot(x - w.x1 - t * dx, z - w.z1 - t * dz));
  }
  let light = THREE.MathUtils.lerp(FLOOR_PEAK, FLOOR_FAR, THREE.MathUtils.smoothstep(distance, 0, reach));
  for (const r of model.obstacleRects ?? []) {
    // Reject distant furniture before testing its six rounded silhouettes.
    if (x < r.x1 - FLOOR_PADS[0] || x > r.x2 + FLOOR_PADS[0]
      || z < r.z1 - FLOOR_PADS[0] || z > r.z2 + FLOOR_PADS[0]) continue;
    for (const pad of FLOOR_PADS) {
      const radius = Math.min((r.x2 - r.x1) / 2 + pad, (r.z2 - r.z1) / 2 + pad, pad + .06);
      const dx = Math.max(r.x1 - pad + radius - x, 0, x - (r.x2 + pad - radius));
      const dz = Math.max(r.z1 - pad + radius - z, 0, z - (r.z2 + pad - radius));
      if (dx * dx + dz * dz <= radius * radius) light *= FLOOR_ATTENUATION;
    }
  }
  return Math.max(FLOOR_MIN, light);
}

/** Bake at the final placement, not the local origin. Preserve these colors when batching. */
export function floorVertexAO(geometry, model, matrix = new THREE.Matrix4()) {
  const position = geometry.getAttribute("position"), world = new THREE.Vector3();
  const color = new Float32Array(position.count * 3);
  for (let i = 0; i < position.count; i++) {
    world.fromBufferAttribute(position, i).applyMatrix4(matrix);
    const light = floorAOAt(model, world.x, world.z);
    color.fill(light, i * 3, i * 3 + 3);
  }
  geometry.setAttribute("color", new THREE.BufferAttribute(color, 3));
  return geometry;
}

/** Canvas top corresponds to floor -Z. Keep the outer floor's existing lightMap. */
export function floorLightMap(layout, windows = layout.windows ?? []) {
  const model = { ...layout, windows, obstacleRects: layout.obstacleRects ?? obstacleRects() };
  const floor = layout.LAYOUT?.floor ?? layout.floor, size = 512;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d"), pixels = ctx.createImageData(size, size);
  const left = (floor.x ?? 0) - floor.w / 2, back = floor.z - floor.d / 2;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const wx = left + (x + .5) / size * floor.w;
      const wz = back + (y + .5) / size * floor.d;
      const value = Math.round(floorAOAt(model, wx, wz) / FLOOR_PEAK * 255);
      const i = (y * size + x) * 4;
      pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = value;
      pixels.data[i + 3] = 255;
    }
  }
  ctx.putImageData(pixels, 0, 0);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.NoColorSpace;
  texture.channel = 0;
  texture.userData.intensity = FLOOR_PEAK; // Decode the >1 radiance stored in the 8-bit map.
  return texture;
}
