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
const { RobotBatch, makeSkeleton, applyPose, BOT_TINT } = await import("../ui/iso/robot.js");
const { createRigKit, BOT_PARTS, D_PARTS, D_PARTS_CLAW, HYBRID_PARTS } = await import("../ui/iso/rigbot.js");
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

// Real geometry/rig checks share the existing canvas stub; no renderer or port is required.
function withRobotMaterials(check) {
  const previous = globalThis.document;
  const ctx = new Proxy({}, { get: (object, key) => object[key] ?? (() => {}) });
  globalThis.document = { createElement: () => ({ getContext: () => ctx }) };
  const materials = Object.fromEntries(["shell", "visor", "joint", "accent", "crown", ...Object.values(PROP_MATERIALS)]
    .map(key => [key, new THREE.MeshStandardMaterial({ color: key === "joint" ? 0x23213a : 0xffffff })]));
  try {
    check(materials);
  } finally {
    Object.values(materials).forEach(material => material.dispose());
    if (previous === undefined) delete globalThis.document; else globalThis.document = previous;
  }
}
const near = (actual, expected, epsilon = 1e-6) => assert.ok(Math.abs(actual - expected) <= epsilon,
  `${actual} differs from ${expected}`);
function instanceColor(mesh, index = 0) {
  const color = new THREE.Color(); mesh.getColorAt(index, color); return color;
}
function oldInstances(batch) {
  return Object.fromEntries(Object.entries(batch.meshes).filter(([part]) => !["headBot", "lampBot"].includes(part))
    .map(([part, mesh]) => [part, {
      count: mesh.count, matrix: Array.from(mesh.instanceMatrix.array.slice(0, mesh.count * 16)),
      color: mesh.instanceColor ? Array.from(mesh.instanceColor.array.slice(0, mesh.count * 3)) : null,
    }]));
}

test("Bot has a square steel-blue box, small rounded corners, one hemispherical status lamp and black mittens", () => {
  withRobotMaterials(materials => {
    const renderScene = new THREE.Scene(), batch = new RobotBatch(renderScene, materials, 8);
    try {
      assert.equal(BOT_TINT.getHexString(), "5d76a8");
      assert.equal(batch.meshes.headBot, undefined); assert.equal(batch.meshes.lampBot, undefined);
      const accent = new THREE.Color(0xd89342);
      batch.begin(); batch.push(makeSkeleton(), accent, null, null, "bot"); batch.end();
      const head = batch.meshes.headBot, lamp = batch.meshes.lampBot;
      assert.equal(head.count, 1); assert.equal(lamp.count, 1);
      assert.equal(head.material, materials.shell); assert.equal(lamp.material, materials.accent);
      assert.equal(batch.meshes.hand.count, 2); assert.equal(batch.meshes.hand.material, materials.joint);
      assert.equal(batch.meshes.hand.material.color.getHexString(), "23213a");
      for (const part of ["head", "headCodex", "headOpenclaw", "antStem", "antCodex", "antOpenclaw", "antTip", "claw"])
        assert.equal(batch.meshes[part].count, 0, part);
      near(instanceColor(head).r, BOT_TINT.r); near(instanceColor(head).g, BOT_TINT.g); near(instanceColor(head).b, BOT_TINT.b);
      near(instanceColor(lamp).r, accent.r); near(instanceColor(lamp).g, accent.g); near(instanceColor(lamp).b, accent.b);
      for (const mesh of [head, lamp]) mesh.geometry.computeBoundingBox();
      near(head.geometry.boundingBox.max.x - head.geometry.boundingBox.min.x, .72);
      near(head.geometry.boundingBox.max.y - head.geometry.boundingBox.min.y, .72);
      const pos = head.geometry.getAttribute("position"), radius = .72 * .12, inner = .36 - radius;
      let roundedCorners = 0;
      for (let i = 0; i < pos.count; i++) {
        const x = Math.abs(pos.getX(i)), y = Math.abs(pos.getY(i)), z = Math.abs(pos.getZ(i));
        if (x > inner + 1e-5 && y > inner + 1e-5 && z < .33 - radius) {
          near(Math.hypot(x - inner, y - inner), radius); roundedCorners++;
        }
      }
      assert.ok(roundedCorners > 0, "Box corners are not rounded at twelve percent of head height");
      near(lamp.geometry.boundingBox.min.y, .36); near(lamp.geometry.boundingBox.max.y, .468);
      near(lamp.geometry.boundingBox.max.x - lamp.geometry.boundingBox.min.x, .216);
      near(lamp.geometry.boundingBox.max.z - lamp.geometry.boundingBox.min.z, .216);
      const lampPos = lamp.geometry.getAttribute("position");
      for (let i = 0; i < lampPos.count; i++) assert.ok(lampPos.getY(i) >= .36 - 1e-6, "Lamp includes a lower hemisphere or stem");
      assert.ok(Object.values(batch.meshes).every(mesh => Object.values(materials).includes(mesh.material)), "Bot created another material");
    } finally { batch.dispose(); }
  });
});

test("Bot-free batches keep their old instances; adding and removing Bot resets only its new part counts", () => {
  withRobotMaterials(materials => {
    const renderScene = new THREE.Scene(), batch = new RobotBatch(renderScene, materials, 8);
    try {
      const original = ["claude", "codex", "openclaw"].map((vendor, i) => {
        const nodes = makeSkeleton(); nodes.root.position.x = i * 2; return { nodes, vendor };
      });
      const paintOriginal = () => original.forEach(({ nodes, vendor }) => batch.push(nodes, null, null, null, vendor));
      batch.begin(); paintOriginal(); batch.end();
      assert.equal(batch.meshes.headBot, undefined); assert.equal(batch.meshes.lampBot, undefined);
      const before = oldInstances(batch), materialSet = new Set(renderScene.children.filter(n => n.isMesh).map(n => n.material));
      batch.begin(); paintOriginal(); batch.push(makeSkeleton(), null, null, null, "bot"); batch.end();
      for (const [part, saved] of Object.entries(before)) {
        const mesh = batch.meshes[part];
        assert.deepEqual(Array.from(mesh.instanceMatrix.array.slice(0, saved.count * 16)), saved.matrix, part);
        if (saved.color) assert.deepEqual(Array.from(mesh.instanceColor.array.slice(0, saved.count * 3)), saved.color, part);
      }
      assert.ok(materialSet.has(batch.meshes.headBot.material)); assert.ok(materialSet.has(batch.meshes.lampBot.material));
      batch.begin(); paintOriginal(); batch.end();
      assert.deepEqual(oldInstances(batch), before);
      assert.equal(batch.meshes.headBot.count, 0); assert.equal(batch.meshes.lampBot.count, 0);
      batch.begin(); batch.push(makeSkeleton(), null, null, null, "unknown-vendor"); batch.end();
      assert.equal(batch.meshes.head.count, 1); assert.equal(batch.meshes.headBot.count, 0);
      assert.equal(batch.meshes.antStem.count, 1); assert.equal(batch.meshes.antTip.count, 1);
    } finally { batch.dispose(); }
  });
});

for (const mode of [1, 2]) {
  test(`Bot uses its procedural head on the real mode ${mode} rig, restores old geometry and resets pooled indices`, () => {
    withRobotMaterials(materials => {
      const renderScene = new THREE.Scene(), kit = createRigKit(materials, renderScene, mode);
      assert.ok(kit);
      let batch, rig, reused;
      try {
        assert.deepEqual(new Set(BOT_PARTS), new Set(["headBot", "lampBot", "visor", "ear", "chest", "__acc", "__prop"]));
        assert.equal(kit.partsFor("bot"), BOT_PARTS);
        assert.equal(kit.partsFor("claude"), mode === 2 ? HYBRID_PARTS : D_PARTS);
        assert.equal(kit.partsFor("openclaw"), mode === 2 ? HYBRID_PARTS : D_PARTS_CLAW);
        batch = new RobotBatch(renderScene, materials, 8, kit.facePlate ? { visorRig: kit.facePlate } : null);
        const nodes = makeSkeleton(); rig = kit.attach(nodes);
        const fullIndex = rig.mesh.geometry.getIndex(), originalIndex = Array.from(fullIndex.array);
        const headBone = rig.mesh.skeleton.bones.find(bone => bone.name === "Head");
        const cutoff = headBone.getWorldPosition(new THREE.Vector3()).y + .01;
        rig.setTint(BOT_TINT); rig.setVendorShape("bot"); rig.apply("wait", 2, 0, false);
        assert.equal(rig.mesh.material, materials.shell);
        const botIndex = rig.mesh.geometry.getIndex(), pos = rig.mesh.geometry.getAttribute("position");
        if (mode === 1) {
          assert.ok(botIndex.count > 0 && botIndex.count < fullIndex.count, "Generated head/antenna triangles remain");
          for (let i = 0; i < botIndex.count; i += 3)
            assert.ok(Math.min(pos.getY(botIndex.getX(i)), pos.getY(botIndex.getX(i + 1)), pos.getY(botIndex.getX(i + 2))) < cutoff,
              "Bot retained a triangle wholly above its neck");
        } else assert.equal(botIndex, fullIndex, "Hybrid body already has no generated head");
        batch.begin(); batch.push(nodes, null, BOT_TINT, null, "bot", "idle", null, kit.partsFor("bot")); batch.end();
        assert.equal(batch.meshes.headBot.count, 1); assert.equal(batch.meshes.lampBot.count, 1);
        assert.equal(batch.meshes.visor.count, 1); assert.equal(batch.meshes.ear.count, 2);
        for (const part of ["head", "headCodex", "headOpenclaw", "antStem", "antCodex", "antOpenclaw", "antTip"])
          assert.equal(batch.meshes[part].count, 0, part);
        if (batch.meshes.visorRig) assert.equal(batch.meshes.visorRig.count, 0);
        for (const vendor of ["claude", "codex", "openclaw", "unknown-vendor"]) {
          rig.setVendorShape(vendor);
          assert.equal(rig.mesh.geometry.getIndex(), fullIndex, `${vendor} did not restore the ordinary index`);
          assert.deepEqual(Array.from(rig.mesh.geometry.getIndex().array), originalIndex);
        }
        rig.setVendorShape("bot");
        const pooledGeometry = rig.mesh.geometry; rig.dispose(); rig = null;
        reused = kit.attach(makeSkeleton());
        assert.equal(reused.mesh.geometry, pooledGeometry);
        assert.equal(reused.mesh.geometry.getIndex(), fullIndex, "A new ordinary actor inherited Bot's headless index");
      } finally {
        reused?.dispose(); rig?.dispose(); batch?.dispose(); kit.dispose();
        if (batch?.materials.visorRig) batch.materials.visorRig.dispose();
      }
    });
  });

  test(`Scene keeps Bot actor and meeting chibi steel-blue in rig mode ${mode}`, () => {
    withRobotMaterials(materials => {
      const renderScene = new THREE.Scene(), kit = createRigKit(materials, renderScene, mode);
      let batch;
      const s = scene(true);
      try {
        batch = new RobotBatch(renderScene, materials, 12, kit.facePlate ? { visorRig: kit.facePlate } : null);
        s.robots = batch; s.rigKit = kit; s._chibiTint = new THREE.Color();
        s.renderer = { debug: {} }; s.post = { render() {} }; s.scene = renderScene;
        s.update(world([agent("a", { vendor: "bot", session: "bot-mail", minions: 1, color: "slate" })]), 2);
        const actor = s.actors.get("a");
        assert.equal(actor.vendor, "bot"); assert.equal(actor.rigTint.getHexString(), "5d76a8");
        assert.equal(s._shellTint(null, "bot").getHexString(), "5d76a8");
        assert.equal(s._shellTint("slate", "bot").getHexString(), "5d76a8");
        assert.equal(batch.meshes.headBot.count, 2); assert.equal(batch.meshes.lampBot.count, 2);
        assert.equal(s.chibiRigs.length, 1); assert.equal(s.chibiRigs[0].group.visible, true);
        assert.deepEqual(Array.from(s.chibiRigs[0].mesh.geometry.getAttribute("color").array),
          Array.from(actor.rig.mesh.geometry.getAttribute("color").array), "Actor/chibi body tints differ");
        for (let i = 0; i < 2; i++) {
          near(instanceColor(batch.meshes.headBot, i).r, BOT_TINT.r);
          near(instanceColor(batch.meshes.headBot, i).b, BOT_TINT.b);
        }
      } finally {
        for (const actor of s.actors.values()) actor.rig?.dispose();
        for (const rig of s.chibiRigs || []) rig.dispose();
        batch?.dispose(); kit.dispose();
        if (batch?.materials.visorRig) batch.materials.visorRig.dispose();
      }
    });
  });
}
