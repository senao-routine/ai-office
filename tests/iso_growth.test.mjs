// Node-only ownership, persistence and geometry checks; no WebGL rendering or UI shots.
import assert from "node:assert/strict";
import { test } from "node:test";
import { registerHooks } from "node:module";
import { createHash } from "node:crypto";

const root = new URL("../", import.meta.url);
registerHooks({ resolve(specifier, context, next) {
  return next(specifier.startsWith("/ui/") ? new URL(specifier.slice(1), root).href : specifier, context);
} });
globalThis.location = { search: "?t=3.2&seed=11" };
const { createGrowth } = await import("../ui/platform/growth.js");
const { rand, randState, resetRand, withRandState } = await import("../ui/platform/clock.js");
const { IsoScene, makeMaterials } = await import("../ui/iso/scene3d.js");
const { ActivityScreens } = await import("../ui/iso/screens.js");
const { buildOffice, buildMonitors } = await import("../ui/iso/office.js");
const { M, LAYOUT_SPECS } = await import("../ui/core/layout_specs.js");
const { buildLayout } = await import("../ui/core/layout.js");
const { specFor, tierFor } = await import("../ui/core/tier.js");
const { buildWorld } = await import("../ui/core/world.js");
const THREE = await import("../ui/vendor/three/three.module.min.js");

function memory(initial = null) {
  let value = initial;
  return { getItem(key) { assert.equal(key, "aioffice.growth"); return value; },
    setItem(key, next) { assert.equal(key, "aioffice.growth"); value = next; }, value: () => value };
}

test("maxSeen alone persists across reload and tabs; server level never enters storage", () => {
  const store = memory();
  const first = createGrowth({ isolated: false, storage: () => store });
  assert.equal(first.observe(20), 20);
  assert.deepEqual(JSON.parse(store.value()), { maxSeen: 20 });
  const reload = createGrowth({ isolated: false, storage: () => store });
  assert.equal(reload.observe(10), 20);
  assert.equal(tierFor({ maxSeen: reload.observe(10), officeLevel: 0 }), "XL");
  store.setItem("aioffice.growth", '{"maxSeen":24}');
  assert.equal(first.observe(9), 24);
  assert.equal(first.observe(25), 25);
  assert.deepEqual(JSON.parse(store.value()), { maxSeen: 25 });
});

test("corrupt/denied storage keeps an in-memory peak; frozen fixtures never access storage", () => {
  for (const value of ["broken", '{"maxSeen":-3}', '{"maxSeen":"99"}', "null"]) {
    const store = memory(value), growth = createGrowth({ isolated: false, storage: () => store });
    assert.equal(growth.observe(9), 9);
    assert.deepEqual(JSON.parse(store.value()), { maxSeen: 9 });
  }
  const denied = createGrowth({ isolated: false, storage() { throw new Error("denied"); } });
  assert.equal(denied.observe(22), 22);
  assert.equal(denied.observe(10), 22);
  const frozen = createGrowth({ storage() { assert.fail("fixture touched storage"); } });
  assert.equal(frozen.observe(9), 9);
  assert.equal(createGrowth().observe(9), 9);
});

// Canvas calls are inert: these checks inspect geometry/ownership, not raster output.
function canvas() {
  const context = new Proxy({
    createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
    measureText: (text) => ({ width: String(text).length * 6 }),
    createLinearGradient: () => ({ addColorStop() {} }),
    createRadialGradient: () => ({ addColorStop() {} }),
  }, { get: (object, key) => object[key] ?? (() => {}) });
  return { width: 0, height: 0, getContext: () => context };
}
globalThis.document = { createElement: () => canvas() };

function geometryDigest(meshes) {
  const hash = createHash("sha256");
  for (const mesh of meshes) {
    hash.update(JSON.stringify([mesh.name, mesh.position.toArray(), mesh.rotation.toArray(),
      mesh.castShadow, mesh.receiveShadow]));
    for (const attribute of Object.values(mesh.geometry.attributes)) {
      hash.update(new Uint8Array(attribute.array.buffer, attribute.array.byteOffset, attribute.array.byteLength));
    }
  }
  return hash.digest("hex");
}

const population = (n, level) => buildWorld({ roster: Array.from({ length: n }, (_, i) =>
  ({ session: `worker-${i}`, state: "working" })),
  ...(level == null ? {} : { growth: { office: { level } } }) });

test("static replacement retains actors, camera, selection and materials; atlas grows once; 22→10 stays XL", () => {
  resetRand();
  const scene = Object.create(IsoScene.prototype);
  scene.scene = new THREE.Scene(); scene.materials = makeMaterials();
  scene.growth = createGrowth(); scene._setLayoutModel(M); scene.layoutKey = "M:legacy";
  scene.displays = new ActivityScreens(scene.materials.board.map);
  scene._staticSeed = randState();
  scene.staticMeshes = buildOffice(scene.materials, M, scene.model);
  scene.monitors = buildMonitors(scene.displays, scene.materials, scene.model);
  for (const mesh of [...scene.staticMeshes, scene.monitors]) scene.scene.add(mesh);
  const actor = { nodes: { pose: "thinking" }, path: [[1, 2]], seed: .37, trYaw: { to: 1 } };
  scene.actors = new Map([["worker-0", actor]]); scene.seeded = true;
  scene.camera = new THREE.OrthographicCamera(); scene.camera.zoom = 1.7;
  scene._focusId = "worker-0"; scene._userPan = { x: 2, y: -1 }; scene.viewScale = 1.14;
  scene.contentBox = new THREE.Box3(); scene._fitShadowCamera = () => {};
  scene.resize = () => assert.fail("live growth reset camera framing");
  const atlas = scene.displays.texture, screenMaterial = scene.displays.material;
  const dailyMaterial = scene.displays.dailyMaterial, materials = Object.values(scene.materials);
  const original = scene.staticMeshes;
  const poses = actor.nodes, path = actor.path, yaw = actor.trYaw;
  let disposed = 0, lightDisposed = 0, atlasDisposed = 0;
  atlas.addEventListener("dispose", () => atlasDisposed++);
  for (const mesh of original) mesh.geometry.addEventListener("dispose", () => disposed++);
  scene.materials.floor.lightMap.addEventListener("dispose", () => lightDisposed++);
  const beforeRand = randState();
  const grown = scene.prepareWorld(population(22));
  assert.equal(scene.spec.id, "XL"); assert.equal(grown.seats.size, 22); assert.equal(grown.overflow.size, 0);
  assert.equal(disposed, original.length); assert.equal(lightDisposed, 1);
  assert.equal(scene.actors.get("worker-0"), actor); assert.equal(actor.nodes, poses);
  assert.equal(actor.path, path); assert.equal(actor.trYaw, yaw); assert.equal(actor.reroute, true);
  assert.equal(scene.camera.zoom, 1.7); assert.equal(scene._focusId, "worker-0");
  assert.deepEqual(scene._userPan, { x: 2, y: -1 }); assert.equal(scene.viewScale, 1.14);
  assert.deepEqual(Object.values(scene.materials), materials);
  assert.equal(atlasDisposed, 1); assert.notEqual(scene.displays.texture, atlas);
  assert.equal(scene.displays.material, screenMaterial);
  assert.equal(scene.displays.material.map, scene.displays.texture);
  assert.equal(scene.displays.dailyMaterial, dailyMaterial);
  assert.equal(dailyMaterial.map, scene.materials.board.map);
  assert.deepEqual(scene.displays.texture.userData, { seats: 24, cols: 5, rows: 5 });
  assert.equal(randState(), beforeRand);
  assert.equal(scene.projectSignAnchors().length, 12);
  const xl = scene.staticMeshes, xlAtlas = scene.displays.texture;
  scene.prepareWorld(population(10)); assert.equal(scene.staticMeshes, xl);
  scene.prepareWorld(population(10, 8)); assert.notEqual(scene.staticMeshes, xl);
  assert.ok(!scene.model.anchors.meeting.byRoom.meet4);
  const lv8 = scene.staticMeshes;
  scene.prepareWorld(population(10, 9)); assert.equal(scene.staticMeshes, lv8, "no rebuild between unlocks");
  scene.prepareWorld(population(10, 20));
  assert.equal(scene.displays.texture, xlAtlas, "same seat count retains its atlas across rebuilds");
  assert.equal(scene.spec.decor.cafe, true);
  assert.equal(scene.spec.id, "XL", "server level changes cannot shrink population capacity");
  assert.equal(scene.actors.get("worker-0"), actor);
  scene._disposeStatic(); scene.displays.dispose();
});

test("all tier monitor UVs sample their own upright canvas tile; reports stay separate; shrink disposes", () => {
  const materials = makeMaterials(), displays = new ActivityScreens(materials.board.map);
  const material = displays.material, report = displays.dailyMaterial;
  for (const spec of [LAYOUT_SPECS.XL, LAYOUT_SPECS.L, M, LAYOUT_SPECS.S]) {
    const model = buildLayout(spec), n = model.anchors.desk.length;
    const previous = displays.texture;
    let disposed = 0;
    previous.addEventListener("dispose", () => disposed++);
    displays.resize(n);
    assert.equal(disposed, 1);
    displays.update(population(n));
    assert.equal(displays.keys.length, n, "every tile is repainted after resizing");
    const version = displays.texture.version;
    displays.update(population(n));
    assert.equal(displays.texture.version, version, "identical state avoids a texture upload");
    const monitors = buildMonitors(displays, materials, model);
    const seats = monitors.children.filter(mesh => mesh.name.startsWith("monitor:seat:"));
    assert.equal(seats.length, n);
    const canvas = displays.texture.image;
    assert.equal(canvas.width, Math.ceil(Math.sqrt(n)) * 512);
    assert.equal(displays.texture.generateMipmaps, false);
    for (const [slot, mesh] of seats.entries()) {
      assert.equal(mesh.material, material);
      assert.equal(mesh.material.map, displays.texture);
      assert.equal(Object.hasOwn(mesh, "onBeforeRender"), false);
      const uv = mesh.geometry.getAttribute("uv");
      // Three's real texture transform includes flipY. Sample both top and bottom
      // of each plane to catch upside-down rows as well as the wrong tile index.
      for (const v of [.1, .9]) {
        const point = new THREE.Vector2((uv.getX(0) + uv.getX(1)) / 2,
          uv.getY(2) * (1 - v) + uv.getY(0) * v);
        displays.texture.transformUv(point);
        const x = point.x * canvas.width, y = point.y * canvas.height;
        assert.equal(Math.floor(y / 320) * (canvas.width / 512) + Math.floor(x / 512), slot);
        assert.ok(Math.abs((y % 320) - (1 - v) * 320) < .001, "tile stays upright");
      }
    }
    assert.equal(monitors.getObjectByName("screen:daily").material, report);
    assert.notEqual(report, material); assert.equal(report.map, materials.board.map);
    for (const mesh of monitors.children) mesh.geometry?.dispose();
  }
  let disposed = 0, reportDisposed = 0;
  displays.texture.addEventListener("dispose", () => disposed++);
  report.addEventListener("dispose", () => reportDisposed++);
  displays.dispose();
  assert.equal(disposed, 1); assert.equal(reportDisposed, 1);
});

test("every tier builds deterministic static batches with one mesh per material key", () => {
  resetRand(); const materials = makeMaterials(); const seed = randState();
  const counts = [];
  for (const [tier, spec] of Object.entries(LAYOUT_SPECS)) {
    const first = withRandState(seed, () => buildOffice(materials, spec));
    const digest = geometryDigest(first);
    // Captured independently by executing the pre-V7 office.js from HEAD with seed=11.
    // This covers geometry, placement and shadow flags; pixel golden is a separate gate.
    if (tier === "M") assert.equal(digest, "7113265b0d475f51ada29388a7de2243ab67057aefc7cca2a6af40063aa1a59a");
    counts.push({ tier, batches: first.length, shadowBatches: first.filter((m) => m.castShadow).length,
      monitorScreens: spec.desks.columns.length * spec.desks.rows.length * 2 });
    assert.equal(new Set(first.map((m) => m.name)).size, first.length);
    for (const mesh of first) mesh.geometry.dispose(); materials.floor.lightMap.dispose();
    // Advancing the live stream cannot affect rebuilding this layout.
    rand(); rand(); const stream = randState();
    const second = withRandState(seed, () => buildOffice(materials, spec));
    assert.equal(geometryDigest(second), digest); assert.equal(randState(), stream);
    for (const mesh of second) mesh.geometry.dispose(); materials.floor.lightMap.dispose();
    const locked = withRandState(seed, () => buildOffice(materials, specFor(tier, 0)));
    for (const mesh of locked) mesh.geometry.dispose(); materials.floor.lightMap.dispose();
  }
  assert.equal(new Set(counts.map((c) => c.batches)).size, 1, "furniture is merged across all tiers");
  console.log("Static geometry counts (not GPU drawCalls):", JSON.stringify(counts));
});
