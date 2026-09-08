// R90-V5: assemble the shared furniture kit from the pure layout specification.
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { LAYOUT, WALL } from "/ui/core/nav.js";
import { buildStaticBatches } from "./merge.js";
import { floorLightMap, floorVertexAO } from "./bake.js";
import { addPlant } from "./plants.js";
import { rand } from "/ui/platform/clock.js";
import { slab, flat, at } from "./kit.js";
import * as kit from "./kit.js";
import { monitorGeometry, reportGeometry } from "./screens.js";
import { DEFAULT_SPEC, FURNITURE as F } from "/ui/core/layout_specs.js";
import { buildLayout } from "/ui/core/layout.js";

export { LAYOUT, WALL };
const defaultModel = buildLayout(DEFAULT_SPEC);
const copy = (list) => list.map((a) => ({ ...a }));
export const seatAnchors = () => copy(defaultModel.anchors.desk);
export const meetingAnchorsByRoom = () => Object.fromEntries(Object.entries(defaultModel.anchors.meeting.byRoom)
  .map(([id, seats]) => [id, copy(seats)]));
export const meetingAnchors = () => Object.values(meetingAnchorsByRoom()).flat();
export const loungeAnchors = () => copy(defaultModel.anchors.lounge);
export const queueAnchors = () => copy(defaultModel.anchors.queue);
export const externalAnchors = () => copy(defaultModel.anchors.external);
export const chibiSeats = () => Object.fromEntries(Object.entries(defaultModel.anchors.chibi).map(([id, seats]) => [id, copy(seats)]));
export const projectSignAnchors = (model = defaultModel) => model.signs.map((s) => ({ ...s, y: s.y + F.sign.boardY }));
const coffeeBar = defaultModel.furnishings.find((f) => f.id === "coffee");
const bossDesk = defaultModel.furnishings.find((f) => f.id === "bossDesk");
export const COFFEE_STOP = Object.freeze({ x: coffeeBar.x, z: coffeeBar.z + 1.1 });
export const ENTRANCE = Object.freeze({ x: DEFAULT_SPEC.entrance.x, z: WALL.front - DEFAULT_SPEC.entrance.fromFront });
export const BOSS_SEAT = Object.freeze({ x: bossDesk.x, z: bossDesk.z - .85, baseY: bossDesk.y });

export function officeStops(spec, model) {
  const coffee = model.furnishings.find((f) => f.id === "coffee");
  const boss = model.furnishings.find((f) => f.id === "bossDesk");
  return {
    coffee: coffee ? { x: coffee.x, z: coffee.z + 1.1 } : null,
    boss: { x: boss.x, z: boss.z - .85, baseY: boss.y },
    entrance: { x: spec.entrance.x, z: model.WALL.front - spec.entrance.fromFront },
  };
}

// ── 手続きテクスチャ（全部決定論。Math.random / Date.now 禁止） ──────
/** キーボードのキー面。のっぺりした板は安く見える。 */
export function keyboardTexture() {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 96;
  const g = c.getContext("2d");
  g.fillStyle = "#e8e3db"; g.fillRect(0, 0, 256, 96);
  for (let row = 0; row < 4; row++) {
    for (let col = 0; col < 14; col++) {
      const x = 8 + col * 17.2 + (row === 3 ? 6 : row * 3);
      g.fillStyle = "#c9bfae"; g.fillRect(x, 10 + row * 19, 14, 15);
      g.fillStyle = "#f1ede7"; g.fillRect(x + 1, 11 + row * 19, 12, 12);
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** Oak fallback: longitudinal boards, with the same metre UV scale as the webp. */
export function floorTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 512;
  const g = c.getContext("2d");
  const colors = ["#c4a47c", "#c9aa83", "#bea078", "#cfb08a"];
  for (let row = 0; row < 2; row++) {
    g.fillStyle = colors[row % colors.length]; g.fillRect(0, row * 256, 512, 256);
    g.fillStyle = "#ad8c61"; g.fillRect(0, row * 256, 512, 2);
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = THREE.RepeatWrapping; t.wrapT = THREE.MirroredRepeatWrapping;
  t.repeat.set(1 / 1.6, 1 / 1.6); t.anisotropy = 4;
  return t;
}

/** Tangent-space normals from recessed plank seams; never saved as an asset. */
export function floorNormalTexture() {
  const size = 512, data = new Uint8Array(size * size * 4);
  const height = (x, y) => {
    const yy = ((y % size) + size) % size;
    const seam = Math.min(yy % 256, 256 - yy % 256);
    return -Math.max(0, 1 - seam / 2);
  };
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const nx = height(x - 1, y) - height(x + 1, y);
    const ny = height(x, y - 1) - height(x, y + 1);
    const length = Math.hypot(nx, ny, 1), i = (y * size + x) * 4;
    data[i] = Math.round((nx / length * .5 + .5) * 255);
    data[i + 1] = Math.round((ny / length * .5 + .5) * 255);
    data[i + 2] = Math.round((1 / length * .5 + .5) * 255); data[i + 3] = 255;
  }
  const t = new THREE.DataTexture(data, size, size);
  t.colorSpace = THREE.NoColorSpace;
  t.wrapS = THREE.RepeatWrapping; t.wrapT = THREE.MirroredRepeatWrapping;
  t.repeat.set(1 / 1.6, 1 / 1.6); t.anisotropy = 4;
  t.flipY = true; t.magFilter = t.minFilter = THREE.LinearFilter; t.needsUpdate = true;
  return t;
}

/** 木目（デスク天板用・ほぼ白地に淡い杢目＝マテリアル色に乗算される）。 */
export function woodTexture() {
  const t = floorTexture();
  t.repeat.set(1 / 1.2, 1 / 1.2);
  return t;
}

/** カーペットの織り目（ラグ・ランナー用・白地の点綴り＝色は材質側）。 */
export function rugTexture() {
  const c = document.createElement("canvas");
  c.width = 128; c.height = 128;
  const g = c.getContext("2d");
  g.fillStyle = "#ffffff"; g.fillRect(0, 0, 128, 128);
  const r = rand;
  for (let y = 0; y < 128; y += 4) {
    for (let x = 0; x < 128; x += 4) {
      const v = 0.88 + r() * 0.12;
      g.fillStyle = `rgba(${(v * 255) | 0}, ${(v * 255) | 0}, ${(v * 250) | 0}, 1)`;
      g.fillRect(x, y, 4, 4);
    }
  }
  g.fillStyle = "rgba(255,255,255,.25)";
  for (let y = 0; y < 128; y += 8) g.fillRect(0, y, 128, 1);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.MirroredRepeatWrapping;
  t.repeat.set(2.4, 2.4);
  return t;
}

/** Pale daytime canopy fallback; one continuous image per window wall. */
export function skyTexture() {
  const c = document.createElement("canvas");
  c.width = 1280; c.height = 320;
  const g = c.getContext("2d");
  g.fillStyle = "#f4efe7"; g.fillRect(0, 0, 1280, 320);
  g.fillStyle = "#b2b290";
  for (let i = 0; i < 15; i++) {
    g.beginPath(); g.ellipse(i * 95, 300, 120, 85 + i % 4 * 18, 0, 0, 7); g.fill();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace; t.wrapS = THREE.RepeatWrapping;
  return t;
}

/** Sign fallback uses abstract ink bars, never language-specific text. */
export function signTexture() {
  const c = document.createElement("canvas");
  c.width = 768; c.height = 432;
  const g = c.getContext("2d");
  g.fillStyle = "#f1ede7"; g.fillRect(0, 0, 768, 432);
  g.fillStyle = "#2e2d2c";
  g.fillRect(90, 120, 460, 30); g.fillRect(90, 200, 560, 30);
  g.fillStyle = "#7a9469"; g.fillRect(90, 295, 160, 18);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// ── ジオメトリと行列のヘルパ ────────────────────────────────────
export { slab } from "./kit.js";
/** 垂直に立てる行列。Y回転だけだと板は平置きになる（実際に踏んだ）。 */
const upright = (x, y, z, yaw = 0) => new THREE.Matrix4()
  .makeRotationY(yaw)
  .multiply(new THREE.Matrix4().makeRotationX(Math.PI / 2))
  .setPosition(x, y, z);

// ── Assembly ───────────────────────────────────────────────────
export function buildOffice(materials, spec = DEFAULT_SPEC, model = buildLayout(spec)) {
  const P = [], L = model.LAYOUT, W = model.WALL, PODS = model.PODS;
  const { boss: BOSS_SEAT, entrance: ENTRANCE } = officeStops(spec, model);
  const enabled = (key) => !spec.decor || spec.decor[key];
  const aoModel = { ...model, windows: [
    { x1: W.left, z1: W.back, x2: W.right, z2: W.back },
    { x1: W.left, z1: W.back, x2: W.left, z2: W.front },
  ] };
  const put = (geometry, material, x, y, z, yaw = 0) => P.push({ geometry, material, matrix: at(x, y, z, yaw) });
  const add = (fn, spec) => P.push(...fn(spec));
  const shadow = (x, y, z, w, d) => put(flat(w, d), "shadow", x, y, z);
  const ground = (w, d, x, y, z, material) => {
    // <= 0.6 m between samples. Rugs also need the bake: they hide the deck below.
    const geometry = new THREE.PlaneGeometry(w, d, Math.ceil(w / .6), Math.ceil(d / .6)).rotateX(-Math.PI / 2);
    if (material === "woodFloor") {
      const uv = geometry.getAttribute("uv");
      for (let i = 0; i < uv.count; i++) uv.setXY(i, uv.getX(i) * w, uv.getY(i) * d);
    }
    const matrix = at(x, y, z);
    floorVertexAO(geometry, aoModel, matrix);
    P.push({ geometry, material, matrix, preserveColor: true });
  };
  const mug = (x, y, z, material = "mugB") =>
    put(new THREE.CylinderGeometry(.06, .05, .14, 12), material, x, y + .07, z);
  const papers = (x, y, z, yaw = 0) => {
    put(slab(.30, .006, .22, .005), "paper", x, y + .003, z, yaw);
    put(slab(.28, .006, .20, .005), "paper", x + .015, y + .009, z - .012, yaw + .10);
  };
  const book = (x, y, z, material = "bookA", yaw = 0) => {
    put(slab(.32, .008, .23, .008), material, x, y + .004, z, yaw);
    put(slab(.30, .030, .21, .006), "paper", x, y + .023, z, yaw);
    put(slab(.32, .008, .23, .008), material, x, y + .042, z, yaw);
  };
  const laptop = (x, y, z, yaw) => {
    const parent = at(x, y, z, yaw);
    const part = (geometry, material, matrix) => P.push({ geometry, material, matrix: parent.clone().multiply(matrix) });
    part(slab(.46, .018, .32, .02), "steel", at(0, .009));
    part(flat(.38, .19), "kbd", at(0, .019, .03));
    part(slab(.46, .022, .29, .015), "dark", upright(0, .164, -.15));
    part(new THREE.PlaneGeometry(.41, .24), "paper", at(0, .164, -.137));
  };

  put(flat(L.floor.w + 6, L.floor.d + 6), "islandShadow", (L.floor.x ?? 0) + .35, -.72, L.floor.z + .35);
  put(slab(L.floor.w + .6, .62, L.floor.d + .6, .7), "base", L.floor.x ?? 0, -.34, L.floor.z);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(L.floor.w, L.floor.d), materials.floor);
  floor.name = "floor"; floor.rotation.x = -Math.PI / 2;
  floor.position.set(L.floor.x ?? 0, .035, L.floor.z); floor.receiveShadow = true;
  materials.floor.map.repeat.set(L.floor.w / 1.6, L.floor.d / 1.6);
  materials.floor.normalMap.repeat.copy(materials.floor.map.repeat);
  materials.floor.lightMap = floorLightMap(aoModel);
  materials.floor.lightMapIntensity = materials.floor.lightMap.userData.intensity;

  for (const zone of [L.deskZone, L.stageZone, L.loungeZone,
    ...spec.rooms.map((r) => L[r.zone])]) {
    put(slab(zone.w, zone.lift, zone.d, .12), "woodFloor", zone.x, zone.lift / 2, zone.z);
    ground(zone.w, zone.d, zone.x, zone.lift + .001, zone.z, "woodFloor");
  }

  // 25 cm walls, with full-height back/left walls and low front/right cuts.
  const { backPanel, cutH } = spec.shell;
  add(kit.wall, { ...backPanel, z: W.back + F.wall.d / 2 });
  const westEnd = backPanel.x - backPanel.w / 2, eastEnd = backPanel.x + backPanel.w / 2;
  add(kit.wall, { x: (W.left + westEnd) / 2, z: W.back + F.wall.d / 2, w: westEnd - W.left, windows: true });
  add(kit.wall, { x: (W.right + eastEnd) / 2, z: W.back + F.wall.d / 2, w: W.right - eastEnd, windows: true });
  add(kit.wall, { x: W.left + F.wall.d / 2, z: L.floor.z, w: L.floor.d, yaw: Math.PI / 2, windows: true });
  add(kit.wall, { x: W.right - F.wall.d / 2, z: L.floor.z, w: L.floor.d, h: cutH, yaw: -Math.PI / 2 });
  const [doorLeft, doorRight] = spec.entrance.opening;
  for (const [left, right] of [[W.left, doorLeft], [doorRight, W.right]]) {
    add(kit.wall, { x: (left + right) / 2, z: W.front - F.wall.d / 2, w: right - left, h: cutH, yaw: Math.PI });
  }

  // Each consecutive pair of desk anchors belongs to one project island.
  for (const [i, [x, z]] of PODS.entries()) {
    const y = L.deskZone.lift;
    // A separate, walkable jute rug under each island; retain the oak aisles between pods.
    ground(spec.desks.top.w + .6, spec.desks.top.d + .5, x, y + .012, z, "rug");
    add(kit.desk, { ...spec.desks.top, x, y, z });
    add(kit.partition, { x, y: y + F.desk.h, z, material: i % 2 ? "panelB" : "panelA" });
    add(kit.sign, { ...F.sign, ...model.signs[i] });
    for (const [side, front] of [1, -1].entries()) {
      add(kit.chair, { ...model.anchors.desk[i * 2 + side], material: ["seat", "seatB", "seatC"][i % 3] });
      put(slab(.68, .024, .24, .02), "kbd", x - .30, y + F.desk.h + .012, z + front * .42);
      papers(x + .48, y + F.desk.h, z + front * .87, front * .08);
    }
    mug(x + .78, y + F.desk.h, z + .52, ["mugA", "mugB", "mugC"][i % 3]);
    mug(x + .78, y + F.desk.h, z - .48, ["mugB", "mugC", "mugA"][i % 3]);
    laptop(x - 1.03, y + F.desk.h, z + (i % 2 ? -.70 : .70), i % 2 ? Math.PI : 0);
    const lx = x + 1.08, lz = z - .72, top = y + F.desk.h;
    put(slab(.15, .03, .15, .04), "dark", lx, top + .015, lz);
    put(new THREE.CylinderGeometry(.012, .012, .36, 8), "steel", lx, top + .20, lz);
    put(slab(.24, .07, .13, .04), "white", lx - .07, top + .39, lz);
    put(slab(.18, .012, .09, .02), "lampWarm", lx - .07, top + .35, lz);
    shadow(x, y + .005, z, spec.desks.top.w + .6, spec.desks.top.d + .8);
  }

  // Open entries face the room's approach; all frame footprints remain inside its rectangle.
  for (const room of spec.rooms) {
    const zone = L[room.zone], { x, z, lift: y } = zone;
    ground(room.rug.w, room.rug.d, x, y + .012, z, "rugArt");
    add(kit.desk, { ...room.table, x, y, z, table: true });
    for (const [i, a] of model.anchors.meeting.byRoom[room.id].entries()) {
      if (a.role !== "present") add(kit.chair, { ...a, material: ["seat", "seatB", "seatC"][i % 3] });
    }
    const inset = F.glassBox.rail / 2, hw = zone.w / 2 - inset, hd = zone.d / 2 - inset;
    const walls = room.id === "meet" ? [
      { w: zone.d - inset * 2, x: hw, yaw: Math.PI / 2 },
      { w: zone.w / 2 - .65, x: -(zone.w / 4 + .325), z: hd },
      { w: zone.w / 2 - .65, x: zone.w / 4 + .325, z: hd },
    ] : [
      { w: zone.d - inset * 2, x: -hw, yaw: Math.PI / 2 },
      { w: zone.w - inset * 2, z: -hd },
    ];
    add(kit.glassBox, { x, y, z, walls });
    add(kit.pendant, { x, y: F.pendant.y, z, radius: Math.min(F.pendant.radius, room.table.w * .25) });
    const roomIndex = spec.rooms.indexOf(room);
    mug(x + room.table.w * .25, y + room.table.h, z - .16, ["mugA", "mugB", "mugC"][roomIndex % 3]);
    mug(x - room.table.w * .28, y + room.table.h, z + room.table.d * .27, ["mugC", "mugA", "mugB"][roomIndex % 3]);
    papers(x - room.table.w * .20, y + room.table.h, z - room.table.d * .20, -.08);
    papers(x + room.table.w * .15, y + room.table.h, z + room.table.d * .24, .12);
    shadow(x, y + .005, z, zone.w, zone.d);
  }

  // All other furniture is a placement record, shared with the navigation footprints.
  let sofaIndex = 0;
  for (const f of model.furnishings) {
    if (f.kind !== "sofa") { add(kit[f.kind], f); continue; }
    const cushions = ["cushionA", "cushionB", "cushionC"];
    add(kit.sofa, { ...f, cushion: cushions[sofaIndex % 3] });
    if (f.w > 1.5) {
      P.push({ geometry: kit.puff(.44, .40, .13), material: cushions[(sofaIndex + 1) % 3],
        matrix: at(f.x, f.y, f.z, f.yaw).multiply(at(f.w * .25, F.sofa.seat + .22, -f.d * .14, .16)) });
    }
    sofaIndex++;
  }
  for (const [i, id] of ["stageTable", "benchTable", "loungeTable"].entries()) {
    const table = model.furnishings.find((f) => f.id === id), top = table.y + table.h;
    book(table.x - .16, top, table.z + .02, ["bookA", "bookC", "bookE"][i], -.12);
    mug(table.x + .22, top, table.z - .10, ["mugB", "mugA", "mugC"][i]);
  }
  const lounge = L.loungeZone;
  ground(lounge.w - .20, lounge.d - .12, lounge.x, lounge.lift + .012, lounge.z, "rug");
  for (const [i, art] of model.art.entries()) add(kit.frame, { ...art, index: i });
  add(kit.chair, { x: BOSS_SEAT.x, y: BOSS_SEAT.baseY, z: BOSS_SEAT.z });
  put(slab(5.4, .26, 3.5, .18), "woodFloor", -.6, .13, W.back + 1.95);
  ground(5.4, 3.5, -.6, .261, W.back + 1.95, "woodFloor");
  if (enabled("cafe")) for (const dx of [-1.0, -.35, .35, 1.0]) {
    add(kit.sofa, { x: 4.2 + dx, y: .035, z: W.front - 1.15, w: .4, d: .4, h: .55, pouf: true, material: "sofaC" });
  }
  // Small devices stay as simple solids: they do not need a furniture silhouette.
  const coffee = model.furnishings.find((f) => f.id === "coffee");
  if (coffee) {
    put(slab(.42, .34, .34, .035), "dark", coffee.x - .3, coffee.h + .17, coffee.z);
    for (let i = 0; i < 3; i++) mug(coffee.x + .25 + i * .20, coffee.h, coffee.z + .1, ["mugA", "mugB", "mugC"][i]);
  }
  if (enabled("cafe")) for (const x of [3.5, 4.9]) {
    if (spec.decor) {
      // At Lv20 the touchdown table becomes a cafe corner, using the existing palette.
      mug(x, 1.015, W.front - 1.95, x < 4 ? "mugA" : "mugB");
      put(new THREE.CylinderGeometry(.12, .12, .012, 16), "paper", x, 1.021, W.front - 1.95);
      continue;
    }
    put(slab(.42, .035, .30, .02), "dark", x, 1.04, W.front - 1.95);
    P.push({ geometry: slab(.30, .025, .20, .015), material: "dark", matrix: upright(x, 1.15, W.front - 2.08) });
  }
  put(slab(.68, .024, .24, .02), "kbd", -.75, BOSS_SEAT.baseY + F.desk.h + .012, W.back + 2.32);
  put(slab(.56, .10, .56, .25), "crown", BOSS_SEAT.x, 2.42, BOSS_SEAT.z);
  for (const [dx, dz] of [[-.18, 0], [0, -.16], [.18, 0], [0, .16]]) {
    put(slab(.09, .17, .09, .03), "crown", BOSS_SEAT.x + dx, 2.55, BOSS_SEAT.z + dz);
  }
  for (let i = 0; i < 6; i++) {
    put(slab(1.14, 2.3, 1.0, .04), "darker", L.serverZone.x - 3.375 + i * 1.35, 1.185, L.serverZone.z);
  }
  add(kit.glassBox, { x: L.serverZone.x, z: W.back + 1.42, h: 2.3, walls: [{ w: 8.4 }] });

  let plantIndex = 0;
  for (const [x, z, scale] of [
    [-5.4, W.back + .75, 1.3], [5.7, -7.0, 1.2], [W.right - .7, 2.1, 1.25],
    [7.85, 4.8, 1.05], [W.left + .75, -4.2, 1.2], [W.right - .7, 7.7, 1.25],
  ]) {
    addPlant(P, { x, z, scale, species: ["monstera", "strelitzia", "pothos", "snake"][plantIndex % 4], terra: plantIndex++ % 2 === 1 });
  }
  // Six small desk pots, two shelf pots and three room-corner pots: 17 individual
  // pots in total, plus the two existing hedge boxes. Floor pots stay inside existing room obstacles.
  if (enabled("plants")) for (const [i, [x, z]] of PODS.entries()) addPlant(P, {
    x: x + 1.10, y: L.deskZone.lift + F.desk.h, z: z + .77,
    scale: .34, species: i % 2 ? "snake" : "pothos", terra: i % 3 === 1,
  });
  const bookcase = model.furnishings.find((f) => f.id === "bookcase");
  if (enabled("plants")) addPlant(P, { x: bookcase.x - .65, y: bookcase.y + bookcase.h, z: bookcase.z, scale: .45, species: "snake" });
  if (coffee && enabled("plants")) addPlant(P, { x: coffee.x + .75, y: coffee.y + coffee.h, z: coffee.z, scale: .55, species: "pothos", terra: true });
  for (const [zone, dx, dz, scale, species] of [
    [L.meetZone, -2.85, -.9, 1.1, "strelitzia"],
    [L.meet2Zone, 1.9, -1.1, .85, "snake"],
    [L.meet4Zone, 1.5, -1.45, .85, "monstera"],
  ]) if (zone && enabled("plants")) addPlant(P, { x: zone.x + dx, y: zone.lift + .012, z: zone.z + dz, scale, species });
  ground(2.6, 1.2, ENTRANCE.x, .05, W.front - 1.2, "rugArt");
  return [floor, ...buildStaticBatches(P, materials)];
}

/**
 * テクスチャ付きの立て板（モニタ画面・木のサイン・白板の中身）。
 * ここだけは PlaneGeometry（UV 0..1）でないと絵が壊れる。
 * ベゼル等の無地部分は materials のバッチに乗せる。
 */
export function buildMonitors(displays, materials, model = defaultModel) {
  const { LAYOUT, WALL, PODS } = model;
  const group = new THREE.Group();
  const parts = [];
  const screens = [];
  let i = 0;
  for (const [cx, cz] of PODS) {
    for (const front of [1, -1]) {
      const y = LAYOUT.deskZone.lift + 1.36;
      const z = cz - front * 0.52;
      const yaw = front > 0 ? 0 : Math.PI;
      const mx = cx - 0.30;
      parts.push({ geometry: slab(1.42, 0.04, 0.86, 0.05), material: "dark",
        matrix: upright(mx, y, z, yaw) });
      parts.push({ geometry: slab(0.66, 0.06, 0.40, 0.05), material: "darker",
        matrix: upright(mx, y, z - front * 0.045, yaw) });
      parts.push({ geometry: slab(0.06, 0.30, 0.06, 0.015), material: "steel",
        matrix: at(mx, LAYOUT.deskZone.lift + F.desk.h + .18, z) });
      parts.push({ geometry: slab(0.44, 0.03, 0.26, 0.05), material: "dark",
        matrix: at(mx, LAYOUT.deskZone.lift + F.desk.h + .015, z) });
      const scr = new THREE.Mesh(monitorGeometry(1.32, 0.76, displays.texture, i), displays.material);
      scr.name = `monitor:seat:${i}`;
      scr.position.set(mx, y, z + (front > 0 ? 0.05 : -0.05));
      scr.rotation.y = yaw;
      screens.push(scr);
      i++;
    }
  }
  // 発表ステージの白板（左壁・中身つき）
  const boardMat = materials.board;
  const board1 = new THREE.Mesh(reportGeometry(2.5, 1.55), boardMat);
  board1.position.set(WALL.left + 0.15, 1.35, LAYOUT.stageZone.z);
  board1.rotation.y = Math.PI / 2;
  screens.push(board1);
  // 会議白板（奥壁・中身つき）
  const board2 = new THREE.Mesh(reportGeometry(2.7, 1.6), boardMat);
  board2.position.set(LAYOUT.meetZone.x + 0.2, 1.72, WALL.back + 1.62);
  screens.push(board2);
  // 第2会議室の自立白板（東縁・西向き）
  const n2 = LAYOUT.meet2Zone;
  const board3 = new THREE.Mesh(reportGeometry(2.0, 1.25), boardMat);
  board3.position.set(n2.x + 2.13, n2.lift + 1.05, n2.z);
  board3.rotation.y = -Math.PI / 2;
  screens.push(board3);

  // Reception backboard sits on the rear edge of the counter, below the wall cap.
  const reception = model.furnishings.find((f) => f.id === "reception");
  const rz = reception.z - reception.d / 2 + .04;
  parts.push({ geometry: slab(3.15, .06, 1.10, .04), material: "white",
    matrix: upright(reception.x, reception.h + .55, rz) });
  const dx = reception.x - .43, dy = reception.h + .55;
  parts.push({ geometry: slab(2.12, .04, .96, .04), material: "dark",
    matrix: upright(dx, dy, rz + .05) });
  const daily = new THREE.Mesh(reportGeometry(2.02, .86, true), displays.dailyMaterial);
  daily.name = "screen:daily"; daily.position.set(dx, dy, rz + .08); screens.push(daily);

  const clock = new THREE.Group();
  clock.name = "clock:reception"; clock.position.set(reception.x + 1.08, dy + .12, rz + .08);
  parts.push({ geometry: new THREE.CircleGeometry(.29, 48), material: "dark",
    matrix: at(clock.position.x, clock.position.y, rz + .045) });
  parts.push({ geometry: new THREE.CircleGeometry(.26, 48), material: "white",
    matrix: at(clock.position.x, clock.position.y, rz + .05) });
  const hand = (width, length, z) => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, length, .012).translate(0, length / 2 - .025, 0), materials.dark);
    mesh.position.z = z; clock.add(mesh); return mesh;
  };
  clock.userData.hourHand = hand(.027, .17, 0);
  clock.userData.minuteHand = hand(.018, .23, .014);
  group.userData.clock = clock; group.add(clock);
  updateClock(group, 11);

  for (const b of buildStaticBatches(parts, materials)) group.add(b);
  for (const sc of screens) group.add(sc);
  return group;
}

/** Caller supplies platform time; no time source or texture repaint lives here. */
export function updateClock(monitors, hour) {
  const clock = monitors.userData.clock;
  const minutes = ((Math.floor(hour * 60) % 1440) + 1440) % 1440;
  if (clock.userData.minutes === minutes) return;
  clock.userData.hourHand.rotation.z = -(minutes % 720) / 720 * Math.PI * 2;
  clock.userData.minuteHand.rotation.z = -(minutes % 60) / 60 * Math.PI * 2;
  clock.userData.minutes = minutes;
}
