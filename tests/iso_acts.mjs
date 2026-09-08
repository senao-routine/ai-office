// Node regression checks: real scene update/rig/instances, no browser or WebGL render.
import assert from "node:assert/strict";
import { test } from "node:test";
import { registerHooks } from "node:module";
import * as THREE from "../ui/vendor/three/three.module.min.js";
import { buildWorld } from "../ui/core/world.js";
import { walkGraph } from "../ui/core/nav.js";
import { M } from "../ui/core/layout_specs.js";
import { readPose } from "../ui/core/anim.js";

const clockURL = "data:text/javascript," + encodeURIComponent(`
  export let frozen = false;
  export const freeze = value => { frozen = value; };
  export const localHour = () => 11, rand = () => .5, resetRand = () => {};
  export const randState = () => 0, withRandState = (_state, build) => build();
`);
registerHooks({ resolve(specifier, context, next) {
  if (specifier === "/ui/platform/clock.js") return { url: clockURL, shortCircuit: true };
  if (specifier.startsWith("/ui/")) return next(new URL(`..${specifier}`, import.meta.url).href, context);
  return next(specifier, context);
} });
const { freeze } = await import(clockURL);
const { IsoScene } = await import("../ui/iso/scene3d.js");
const { RobotBatch, makeSkeleton, applyPose } = await import("../ui/iso/robot.js");
const { BOSS_SEAT, ENTRANCE } = await import("../ui/iso/office.js");
const { PROP_MATERIALS } = await import("../ui/iso/props.js");
const { markerTexture } = await import("../ui/iso/markers.js");
const agent = (id, extra = {}) => ({ projectId: id, state: "working", verb: "Reading", target: "guide.md", ...extra });
const world = (roster, level = 1) => buildWorld({ roster, growth: { byProject: { a: { xp: level * 100, level } } } });
const markers = count => Array.from({ length: count }, () => new THREE.Object3D());
function scene(frozen = false) {
  freeze(frozen);
  const s = Object.create(IsoScene.prototype);
  s._setLayoutModel(M);
  Object.assign(s, {
    container: { clientWidth: 800, clientHeight: 600 }, _w: 800, _h: 600,
    actors: new Map(), levels: new Map(), levelUps: new Map(), attnResolved: new Map(),
    markerSince: new Map(), chibiGone: new Map(), chibiPool: [], seeded: false,
    displays: { update() {} }, monitors: { userData: { clock: { userData: { minutes: 660 } } } },
    _applyHour() {}, _lampIntensity: 2.8, materials: { lampWarm: {} },
    cleaner: new THREE.Object3D(), _cleanerTotal: 100, navGraph: walkGraph(),
    boss: makeSkeleton(), bossAccent: new THREE.Color(),
    attnMarkers: markers(6), thinkMarkers: markers(6), chatMarkers: markers(4), doneMarkers: markers(4),
    robots: { begin() {}, push() {}, end() {}, faces: { render() {} } },
    prepareWorld: world => world, // Static rebuilding is covered by iso_growth.test.mjs.
    excursionFor() {}, idleLifeFor() {}, _archFor() {},
    anchorFor(a) { return { x: a.id === "b" ? 2 : -2, z: 1, y: 0, yaw: 0 }; },
  });
  return s;
}
const position = actor => actor.nodes.root.position.toArray();

test("first snapshot is seated even during patrol time; new arrival greets along existing path", () => {
  const s = scene(); s.update(world([agent("a")]), 301);
  assert.equal(s.actors.get("a").walking, false);
  assert.equal(s.bossTrip, undefined);
  const bossStart = s.boss.root.position.clone();
  s.update(world([agent("a"), agent("b")]), 301.1);
  const b = s.actors.get("b");
  assert.equal(b.poseKind, "enter");
  assert.equal(b.nodes.root.position.x, ENTRANCE.x);
  assert.equal(b.nodes.root.position.z, ENTRANCE.z);
  assert.equal(s.bossTrip.welcome, true);
  assert.ok(s.bossTrip.route.some(([x, z]) => x === ENTRANCE.x && z === ENTRANCE.z));
  assert.deepEqual(s.boss.root.position, bossStart);
  s.update(world([agent("a"), agent("b")]), 301.2);
  assert.ok(s.boss.root.position.distanceTo(bossStart) < .2);
  s.update(world([agent("a"), agent("b")]), 302.1);
  assert.notEqual(b.poseKind, "enter");
  s.update(world([agent("a"), agent("b")]), 400);
  assert.equal(b.walking, false);
  assert.equal(s.bossTrip, null);
});
test("departure pauses .7 seconds, preserves position, then walks; reappearance cancels exit", () => {
  const s = scene(); s.update(world([agent("a")]), 0);
  const a = s.actors.get("a"), start = position(a);
  s.update(world([]), 0);
  assert.equal(a.leavingAt, 0);
  assert.equal(a.poseKind, "leave");
  s.update(world([]), .6);
  assert.equal(a.leavingAt, 0);
  assert.equal(position(a)[0], start[0]); assert.equal(position(a)[2], start[2]);
  s.update(world([]), 1);
  assert.equal(a.poseKind, "exit");
  assert.notDeepEqual([position(a)[0], position(a)[2]], [start[0], start[2]]);
  s.update(world([agent("a")]), 1.1);
  assert.equal(a.leavingAt, null); assert.deepEqual(a.dest, [-2, 1]);
  s.update(world([]), 2); s.update(world([]), 100);
  assert.equal(s.actors.size, 0);
});
test("answer acknowledgement covers actors beyond the marker pool and never retriggers", () => {
  const s = scene(), roster = Array.from({ length: 8 }, (_, i) => agent(`p${i}`, { attention: true }));
  s.update(world(roster), 2);
  assert.equal(s.actors.get("p7").poseKind, "question");
  const answered = roster.map(a => ({ ...a, attention: false }));
  s.update(world(answered), 3);
  assert.equal(s.actors.get("p7").poseKind, "approval");
  assert.equal(s.attnResolved.size, 8);
  s.update(world(answered), 3.2);
  assert.equal(s.attnResolved.get("p7").at, 3);
  s.update(world(answered), 4);
  assert.equal(s.attnResolved.size, 0);
});
test("answer nod and celebration remain visible during a change of zone", () => {
  const s = scene();
  s.anchorFor = a => ({ x: a.zone === "queue" ? 0 : -2, z: 1, y: 0, yaw: 0 });
  s.update(world([agent("a", { attention: true })]), 2);
  s.update(world([agent("a", { attention: false })]), 3);
  assert.equal(s.actors.get("a").walking, true);
  assert.equal(s.actors.get("a").poseKind, "walk:approval");
  s.update(world([agent("a")], 2), 3.1);
  assert.equal(s.actors.get("a").poseKind, "walk:celebrate");
});
test("level growth is a baseline then one short celebration/check, including skipped levels", () => {
  const s = scene(); s.update(world([agent("a")], 3), 1);
  assert.equal(s.levelUps.size, 0);
  s.update(world([agent("a")], 5), 2);
  assert.equal(s.actors.get("a").poseKind, "celebrate");
  assert.equal(s.actors.get("a").expression, "happy");
  s.update(world([agent("a")], 5), 2.3);
  assert.ok(s.doneMarkers.some(m => m.visible));
  const actor = s.actors.get("a");
  assert.ok(actor.accent.r > actor.accentCur.r);
  assert.ok(actor.accent.toArray().every(channel => channel >= 0 && channel <= .85));
  assert.equal(s.levelUps.get("a").at, 2);
  s.update(world([agent("a")], 5), 2.7);
  assert.deepEqual(actor.accent, actor.accentCur);
  s.update(world([agent("a")], 5), 3.3);
  assert.equal(s.levelUps.size, 0);
  assert.ok(s.doneMarkers.every(m => !m.visible));
  s.update(world([agent("a")], 3), 4); s.update(world([agent("a")], 5), 5);
  assert.equal(s.levelUps.size, 0);
});
test("frozen updates suppress arrival, departure, welcome, answer and level events", () => {
  const s = scene(true); s.update(world([agent("a", { attention: true })]), 301);
  s.update(world([agent("a"), agent("b")], 4), 301);
  assert.equal(s.actors.get("b").walking, false);
  assert.equal(s.actors.get("b").enteringAt, null);
  assert.equal(s.actors.get("a").poseKind, "read:book");
  assert.equal(s.actors.get("a").flashAccent, undefined);
  assert.equal(s.bossTrip, undefined);
  assert.equal(s.levelUps.size, 0); assert.equal(s.attnResolved.size, 0);
  assert.ok(s.doneMarkers.every(m => !m.visible));
  const before = position(s.actors.get("b"));
  s.update(world([agent("b")], 5), 301);
  assert.equal(s.actors.has("a"), false);
  assert.deepEqual(position(s.actors.get("b")), before);
  assert.equal(s.boss.root.position.x, BOSS_SEAT.x);
});
test("hand props share four existing materials, one instance per body and no shadow draws", () => {
  const previous = globalThis.document;
  const ctx = new Proxy({}, { get: (object, key) => object[key] ?? (() => {}) });
  globalThis.document = { createElement: () => ({ getContext: () => ctx }) };
  let batch;
  const materials = Object.fromEntries(["shell", "visor", "joint", "accent", ...Object.values(PROP_MATERIALS)]
    .map(key => [key, new THREE.MeshStandardMaterial()]));
  try {
    batch = new RobotBatch(new THREE.Scene(), materials, 8);
    const nodes = makeSkeleton(); applyPose(nodes, readPose(3.2, 11));
    batch.begin();
    for (const prop of Object.keys(PROP_MATERIALS)) batch.push(nodes, null, null, null, "claude", "focus", prop);
    batch.push(nodes); batch.push(nodes, null, null, null, "claude", "idle", "unknown"); batch.end();
    for (const [prop, material] of Object.entries(PROP_MATERIALS)) {
      const mesh = batch.meshes[prop];
      assert.equal(mesh.count, 1); assert.equal(mesh.material, materials[material]);
      assert.equal(mesh.castShadow, false); assert.equal(mesh.geometry.groups.length, 0);
      const matrix = new THREE.Matrix4(); mesh.getMatrixAt(0, matrix);
      assert.ok(matrix.elements.every((value, i) => Math.abs(value - nodes.arms[1].hand.matrixWorld.elements[i]) < 1e-6));
    }
    batch.begin(); batch.push(nodes); batch.end();
    for (const prop of Object.keys(PROP_MATERIALS)) assert.equal(batch.meshes[prop].count, 0);
  } finally {
    batch?.dispose(); Object.values(materials).forEach(material => material.dispose());
    if (previous === undefined) delete globalThis.document; else globalThis.document = previous;
  }
});
test("marker canvases use only semantic ink and white paths, without any font API", () => {
  const previous = globalThis.document;
  try {
    for (const [kind, ink] of Object.entries({ attention: "#c0483a", think: "#2e2d2c", chat: "#2e2d2c", done: "#5f7d59" })) {
      const colors = new Set(), calls = [];
      const ctx = new Proxy({}, {
        get(object, key) {
          assert.ok(!["font", "fillText", "strokeText", "measureText"].includes(key));
          return (...args) => calls.push([key, ...args]);
        },
        set(object, key, value) {
          assert.notEqual(key, "font");
          if (key === "strokeStyle" || key === "fillStyle") colors.add(value);
          return true;
        },
      });
      globalThis.document = { createElement: () => ({ getContext: () => ctx }) };
      const texture = markerTexture(kind);
      assert.deepEqual(colors, new Set([ink, "#ffffff"]));
      assert.ok(calls.some(([key]) => key === "stroke"));
      assert.equal(texture.image.width, 128); assert.equal(texture.image.height, 128);
      const first = JSON.stringify(calls); calls.length = 0;
      const again = markerTexture(kind);
      assert.equal(JSON.stringify(calls), first);
      texture.dispose(); again.dispose();
    }
  } finally {
    if (previous === undefined) delete globalThis.document; else globalThis.document = previous;
  }
});
