// Node-only regression checks for the shared floor irradiance and static vertex bake.
// Run: node --test tests/iso_floor_ao.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import * as THREE from "../ui/vendor/three/three.module.min.js";

// Adapt the two browser URL imports in memory; no loader install or generated files.
const threeURL = new URL("../ui/vendor/three/three.module.min.js", import.meta.url).href;
const nodeSource = (file, aliases) => {
  let source = readFileSync(new URL(file, import.meta.url), "utf8");
  for (const [from, to] of Object.entries(aliases)) source = source.replaceAll(JSON.stringify(from), JSON.stringify(to));
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
};
const bakeURL = nodeSource("../ui/iso/bake.js", {
  "/ui/vendor/three/three.module.min.js": threeURL,
  "/ui/core/nav.js": new URL("../ui/core/nav.js", import.meta.url).href,
});
const { floorAOAt, floorVertexAO, floorLightMap } = await import(bakeURL);
const { buildStaticBatches } = await import(nodeSource("../ui/iso/merge.js", {
  "/ui/vendor/three/three.module.min.js": threeURL, "./bake.js": bakeURL,
}));
const floor = Object.freeze({ w: 10, d: 20, z: 10 });
const windows = Object.freeze([Object.freeze({ x1: -5, z1: 0, x2: -5, z2: 20 })]);
const empty = Object.freeze({ floor, windows, obstacleRects: Object.freeze([]) });
const rect = Object.freeze({ x1: -1, z1: 8, x2: 1, z2: 12 });
const near = (a, b, tolerance = 1e-6) => assert.ok(Math.abs(a - b) <= tolerance, `${a} != ${b}`);

test("floorAOAt: window peak, smooth midpoint, far floor and immutable deterministic input", () => {
  const before = JSON.stringify(empty);
  near(floorAOAt(empty, -5, 10), 1.15);
  near(floorAOAt(empty, -1.75, 10), 1.035);
  near(floorAOAt(empty, 1.5, 10), .92);
  near(floorAOAt(empty, 5, 10), .92);
  for (const x of [-5, -1.75, 1.5, 5]) assert.equal(floorAOAt(empty, x, 10), floorAOAt(empty, x, 10));
  assert.equal(JSON.stringify(empty), before);
});

test("floorAOAt: furniture contact, monotone penumbra, clear aisle and overlapping lower bound", () => {
  const model = Object.freeze({ floor, windows: [], obstacleRects: Object.freeze([rect]) });
  const samples = [0, 1.05, 1.15, 1.25, 1.35, 1.46].map((x) => floorAOAt(model, x, 10));
  near(samples[0], .72); near(samples.at(-1), .92);
  assert.ok(new Set(samples).size >= 3, "contact, penumbra and open floor differ");
  for (let i = 1; i < samples.length; i++) assert.ok(samples[i] >= samples[i - 1]);
  const overlap = { ...model, obstacleRects: [rect, rect, rect] };
  near(floorAOAt(overlap, 0, 10), .72);
  const onWindow = { ...empty, obstacleRects: [{ x1: -6, z1: 9, x2: -4, z2: 11 }] };
  near(floorAOAt(onWindow, -5, 10), .72);
});

test("floorVertexAO: final world placement and static merging preserve the bake exactly once", () => {
  const points = new THREE.BufferGeometry();
  points.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 8, 0, 0], 3));
  floorVertexAO(points, empty, new THREE.Matrix4().makeTranslation(-5, .12, 10));
  near(points.getAttribute("color").getX(0), 1.15);
  near(points.getAttribute("color").getX(1), .92);
  const geometry = new THREE.PlaneGeometry(4, 4, 8, 8).rotateX(-Math.PI / 2);
  const matrix = new THREE.Matrix4().makeRotationY(.7).setPosition(-2, .12, 10);
  const model = { ...empty, obstacleRects: [rect] };
  floorVertexAO(geometry, model, matrix);
  const expected = geometry.toNonIndexed();
  const material = new THREE.MeshStandardMaterial();
  const [mesh] = buildStaticBatches([{ geometry, material: "woodFloor", matrix, preserveColor: true }], { woodFloor: material });
  assert.deepEqual(mesh.geometry.getAttribute("color").array, expected.getAttribute("color").array);
  assert.ok(new Set(mesh.geometry.getAttribute("color").array).size > 3, "merged deck has spatial AO");
  const first = geometry.getAttribute("color").array.slice();
  floorVertexAO(geometry, model, matrix);
  assert.deepEqual(geometry.getAttribute("color").array, first, "rebaking does not compound darkness");
  points.dispose(); geometry.dispose(); expected.dispose(); mesh.geometry.dispose(); material.dispose();
});

test("floorLightMap: canvas texels and floor vertices share linear irradiance within 8-bit precision", () => {
  const oldDocument = globalThis.document;
  let pixels;
  const canvas = { getContext: () => ({
    createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
    putImageData: (image) => { pixels = image; },
  }) };
  globalThis.document = { createElement: () => canvas };
  try {
    const model = { ...empty, obstacleRects: [rect] }, texture = floorLightMap(model);
    assert.equal(texture.userData.intensity, 1.15);
    assert.equal(texture.colorSpace, THREE.NoColorSpace);
    for (const [px, py] of [[0, 0], [11, 40], [102, 256], [217, 256], [255, 255], [335, 180], [511, 511]]) {
      const x = -floor.w / 2 + (px + .5) / canvas.width * floor.w;
      const z = floor.z - floor.d / 2 + (py + .5) / canvas.height * floor.d;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.Float32BufferAttribute([x, .12, z], 3));
      floorVertexAO(geometry, model);
      const i = (py * canvas.width + px) * 4, value = pixels.data[i];
      assert.equal(value, Math.round(floorAOAt(model, x, z) / texture.userData.intensity * 255));
      near(value / 255 * texture.userData.intensity, geometry.getAttribute("color").getX(0), 1.15 / 255);
      assert.equal(pixels.data[i + 3], 255);
      geometry.dispose();
    }
    texture.dispose();
  } finally {
    if (oldDocument === undefined) delete globalThis.document;
    else globalThis.document = oldDocument;
  }
});
