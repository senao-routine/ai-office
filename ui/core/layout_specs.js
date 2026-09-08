// レイアウトの寸法・席・巡回演出の正本（単位 m、yaw は rad）。
// M は既存の造作とビット一致。L は南へ1行、XL はさらに西へ1列拡張する。
// 通路ノード/エッジは spec に持たない。layout.js が床と障害物から生成する。
// R90-V4: furniture dimensions in metres. Geometry and obstacle footprints share this source.
export const FURNITURE = freeze({
  desk: { h: .77, top: .03, radius: .008, edge: .006, panel: .045 },
  chair: { w: .56, d: .54, seat: .40, seatH: .14, radius: .20,
    backH: .62, backD: .05, backY: .78, backZ: -.27,
    armX: .32, armY: .64, baseRadius: .31, caster: .045 },
  sofa: { w: 3.1, d: 1.05, h: .94, seat: .44, seatH: .28,
    arm: .18, back: .20, leg: .13, puff: .03, radius: .06 },
  partition: { w: 2.8, h: .35, d: .05, frame: .012 },
  pendant: { radius: .44, h: .34, cord: .008, cordH: 12, y: 2.6 },
  frame: { w: .70, h: .875, rail: .035, d: .045 },
  counter: { w: 3.4, d: 1.0, h: 1.06, top: .03, flute: .018, pitch: .045 },
  shelf: { w: 2.3, h: 1.9, d: .50, panel: .035, rows: 4 },
  glassBox: { h: 2.35, rail: .04, pitch: 2.0 },
  planter: { w: 2.4, h: .55, d: .60, rim: .04, leafH: .30, leafW: .20, pitch: .19 },
  sign: { w: .68, h: .30, d: .045, post: .035, boardY: .48, footW: .40, footD: .22 },
  wall: { h: 2.5, d: .25, skirting: .10 },
});

const rooms = [
  { id: "meet", zone: "meetZone", table: { w: 4.2, d: 1.6, h: .96 }, rug: { w: 5.8, d: 3.2 }, seats: [
    { dx: -1.4, dz: -1.55, yaw: Math.PI, role: "present" },
    { dx: -1.6, dz: 1.15, yaw: Math.PI },
    { dx: 0, dz: 1.15, yaw: Math.PI },
    { dx: 1.6, dz: 1.15, yaw: Math.PI },
    { dx: 0.8, dz: -1.15, yaw: 0 },
  ], chibi: [
    { dx: -2.55, dz: -0.2, yaw: Math.PI / 2 },
    { dx: 2.55, dz: -0.2, yaw: -Math.PI / 2 },
    { dx: -0.6, dz: -1.2, yaw: 0 },
    { dx: 2.2, dz: 1.15, yaw: Math.PI },
  ] },
  { id: "meet2", zone: "meet2Zone", table: { w: 3.0, d: 1.35, h: .86 }, rug: { w: 3.6, d: 2.4 }, seats: [
    { dx: -1.1, dz: 1.25, yaw: Math.PI },
    { dx: 0.45, dz: 1.25, yaw: Math.PI },
    { dx: -0.3, dz: -1.25, yaw: 0 },
  ], chibi: [
    { dx: -1.85, dz: 0, yaw: Math.PI / 2 },
    { dx: 1.7, dz: 0, yaw: -Math.PI / 2 },
    { dx: 0.85, dz: -1.25, yaw: 0 },
    { dx: 1.5, dz: 1.25, yaw: Math.PI },
  ] },
  { id: "meet3", zone: "meet3Zone", table: { w: 1.7, d: 1.0, h: .86 }, rug: { w: 2.3, d: 2.0 }, seats: [
    { dx: -0.35, dz: 0.95, yaw: Math.PI },
    { dx: 0.35, dz: -0.95, yaw: 0 },
  ], chibi: [
    { dx: -1.05, dz: 0, yaw: Math.PI / 2 },
    { dx: 1.05, dz: 0, yaw: -Math.PI / 2 },
    { dx: 0.75, dz: 0.95, yaw: Math.PI },
    { dx: -0.75, dz: -0.95, yaw: 0 },
  ] },
  { id: "meet4", zone: "meet4Zone", table: { w: 1.5, d: 2.7, h: .94 }, rug: { w: 3.0, d: 3.3 }, seats: [
    { dx: -1.35, dz: -0.65, yaw: Math.PI / 2 },
    { dx: -1.35, dz: 0.75, yaw: Math.PI / 2 },
    { dx: 1.35, dz: -0.65, yaw: -Math.PI / 2 },
    { dx: 1.35, dz: 0.75, yaw: -Math.PI / 2 },
    { dx: 0, dz: 1.7, yaw: Math.PI },
  ], chibi: [
    { dx: -1.35, dz: 1.55, yaw: Math.PI / 2 },
    { dx: 1.35, dz: 1.55, yaw: -Math.PI / 2 },
    { dx: -0.8, dz: -1.6, yaw: 0 },
    { dx: 0.8, dz: -1.6, yaw: 0 },
  ] },
];

const medium = {
  id: "M",
  layout: {
    floor: { w: 28.8, d: 19.0, z: -0.6 },
    deskZone: { x: -0.6, z: 0.35, w: 15.8, d: 11.4, lift: 0.12 },
    meetZone: { x: -9.2, z: -7.7, w: 7.2, d: 4.4, lift: 0.22 },
    stageZone: { x: -12.55, z: 0.1, w: 2.9, d: 3.4, lift: 0.12 },
    meet2Zone: { x: 11.3, z: 2.2, w: 4.8, d: 3.2, lift: 0.22 },
    loungeZone: { x: 8.6, z: 6.25, w: 4.8, d: 3.4, lift: 0.12 },
    meet3Zone: { x: 13.15, z: 6.7, w: 2.5, d: 2.7, lift: 0.22 },
    meet4Zone: { x: 9.9, z: -2.65, w: 4.0, d: 3.9, lift: 0.22 },
    serverZone: { x: 10.2, z: -9.25 },
    queueZone: { x: -2.2, z: 6.0 },
  },
  desks: {
    columns: [-6.05, -0.6, 4.85], rows: [-2.35, 3.05],
    top: { w: 3.0, d: 2.3 }, obstacleMargin: 0.10, seatOffset: 1.32,
    seatsPerPod: 2, sign: { gap: .24, dz: .65, yaw: Math.PI / 2 },
  },
  rooms,
  sign: FURNITURE.sign,
  shell: { backPanel: { x: -.4, w: 10.6 }, cutH: .65 },
  entrance: { x: -8.3, fromFront: .85, opening: [-9.8, -6.8] },
  // Placements are resolved by layout.js for both drawing and navigation.
  furnishings: [
    { id: "reception", kind: "counter", ref: "queueZone", dx: 1.6, dz: 1.2, ...FURNITURE.counter },
    { id: "bookcase", kind: "shelf", ref: "back", dx: -4.5, dz: .65, ...FURNITURE.shelf },
    { id: "coffee", kind: "shelf", ref: "back", dx: 3.9, dz: .75,
      w: 2.05, h: .96, d: .85, rows: 1, sideboard: true },
    { id: "bossDesk", kind: "desk", ref: "back", dx: -.6, dz: 2.1, dy: .26, w: 2.9, d: 1.15 },
    { id: "stageNorth", kind: "sofa", ref: "stageZone", dx: .1, dz: -.95, w: 2.2, d: .95, seats: 2 },
    { id: "stageSouth", kind: "sofa", ref: "stageZone", dx: .1, dz: 1.0, yaw: Math.PI, w: 2.2, d: .95, seats: 2 },
    { id: "stageTable", kind: "desk", ref: "stageZone", dx: .1, dz: .05, w: 1.5, d: .65, h: .34, table: true },
    { id: "bench", kind: "sofa", ref: "left", dx: .9, dz: 4.9, yaw: Math.PI / 2, w: 3.4, d: .95, seats: 3 },
    { id: "benchTable", kind: "desk", ref: "left", dx: 2.1, dz: 4.9, w: .85, d: .85, h: .36, table: true },
    { id: "loungeSofa", kind: "sofa", ref: "loungeZone", dx: -.65, dz: -.60, w: 3.1, d: 1.05, seats: 3 },
    { id: "loungeChair", kind: "sofa", ref: "loungeZone", dx: 1.5, dz: .7, w: 1.0, d: 1.0, seats: 1 },
    { id: "loungeTable", kind: "desk", ref: "loungeZone", dx: .1, dz: .75, w: 1.0, d: .8, h: .34, table: true },
    { id: "touchdown", kind: "desk", ref: "front", dx: 4.2, dz: -1.9, w: 2.7, d: .95, h: 1.015, table: true },
    { id: "extConsole", kind: "shelf", ref: "right", dx: -1.05, dz: -2.6,
      w: 5.1, d: .7, h: .96, yaw: -Math.PI / 2, rows: 1, sideboard: true },
    { id: "entranceHedgeWest", kind: "planter", ref: "front", dx: -11.5, dz: -.65, w: 2.4, d: .60 },
    { id: "entranceHedgeEast", kind: "planter", ref: "front", dx: -5.3, dz: -.65, w: 2.4, d: .60 },
  ],
  solidZones: [{ id: "stage", zone: "stageZone" }, { id: "lounge", zone: "loungeZone" }],
  // 矩形の両端は床/壁を基準に解決する。受付の待機列そのものは歩ける床。
  fixtures: [
    { id: "reception", from: { ref: "queueZone", dx: -0.1, dz: 0.7 },
      to: { ref: "queueZone", dx: 3.3, dz: 1.7 } },
    { id: "boss", from: { ref: "back", dx: -3.3 }, to: { ref: "back", dx: 2.1, dz: 3.5 } },
    { id: "server", from: { ref: "back", dx: 5.4 }, to: { ref: "backRight", dz: 1.5 } },
    { id: "touchdown", from: { ref: "front", dx: 2.7, dz: -2.6 }, to: { ref: "front", dx: 5.7, dz: -0.9 } },
    { id: "extConsole", from: { ref: "right", dx: -1.4, dz: -5.6 }, to: { ref: "right", dz: 0.4 } },
  ],
  art: [{ ref: "back", dx: -3.0, dz: .27, dy: 1.9 }, { ref: "back", dx: 1.8, dz: .27, dy: 1.9 }],
  queue: { rows: 2, columns: 6, pitch: 1.1 },
  external: { count: 5, dx: -2.0, z: -4.4, pitch: 1.15 },
  idle: [
    { dx: 4.65, dz: -8.25, yaw: Math.PI, why: "coffee" },
    { dx: -10.6, dz: 0.9, yaw: -Math.PI / 2, why: "plant" },
    // The old window stop stood inside the bench seat; use the clear floor beyond its south arm.
    { dx: -13.3, dz: 7.15, yaw: Math.PI / 2, why: "window" },
    { dx: 5.6, dz: 5.9, yaw: Math.PI / 4, why: "lounge" },
  ],
  rest: [
    { area: "lounge", ref: "loungeZone", dx: 1.5, dz: 0.7, yaw: -1.7, dy: 0.05, role: "tablet" },
    { area: "lounge", ref: "loungeZone", dx: -1.5, dz: -0.35, yaw: 0.15 },
    { area: "lounge", ref: "loungeZone", dx: -0.3, dz: -0.35, yaw: -0.15 },
    { area: "sofa", ref: "stageZone", dx: -0.2, dz: -0.95, yaw: 0 },
    { area: "sofa", ref: "stageZone", dx: 0.4, dz: 1.15, yaw: Math.PI },
    { area: "bench", ref: "left", dx: 0.9, dz: 3.95, yaw: Math.PI / 2 },
    { area: "bench", ref: "left", dx: 0.9, dz: 4.90, yaw: Math.PI / 2 },
    { area: "bench", ref: "left", dx: 0.9, dz: 5.85, yaw: Math.PI / 2 },
  ],
  // 巡回演出は従来の折れ線を維持する。歩行用グラフの入力には使わない。
  cleaner: [
    [-8.8, -5.0], [-3.3, -5.0], [2.1, -5.0], [3.9, -5.0], [7.4, -5.0],
    [7.4, 0.35], [2.1, 0.35], [2.1, 5.3], [5.9, 5.3],
    [2.1, 5.3], [-3.3, 5.3], [-8.8, 5.3], [-8.8, 7.6], [-8.8, 0.35], [-8.8, -5.0],
  ],
  boss: [
    [-0.6, -8.85 + 2.2], [-0.6, -5.0], [-3.3, -5.0], [2.1, -5.0], [-0.6, -5.0], [-0.6, -8.85 + 2.2],
  ],
  navigation: { grid: 0.5, margin: 0.45 },
};

const { meet3Zone, meet4Zone, ...smallLayout } = medium.layout;
const small = {
  ...medium, id: "S", layout: smallLayout,
  desks: { ...medium.desks, columns: [-6.05, -0.6] },
  rooms: rooms.slice(0, 2), queue: { ...medium.queue, columns: 4 },
};

const large = {
  ...medium, id: "L",
  layout: {
    ...medium.layout,
    floor: { w: 28.8, d: 24.4, z: 2.1 },
    deskZone: { ...medium.layout.deskZone, z: 3.05, d: 16.8 },
    loungeZone: { ...medium.layout.loungeZone, z: 11.65 },
    meet3Zone: { ...medium.layout.meet3Zone, z: 12.1 },
    queueZone: { ...medium.layout.queueZone, z: 11.4 },
  },
  desks: { ...medium.desks, rows: [-2.35, 3.05, 8.45] },
  idle: medium.idle.map((spot) => spot.why === "lounge" ? { ...spot, dz: 11.3 } : spot),
  cleaner: medium.cleaner.map(([x, z]) => [x, z >= 5.3 ? z + 5.4 : z]),
};

const extraLarge = {
  ...large, id: "XL",
  layout: {
    ...large.layout,
    // Keep the east wall and every meeting/reception anchor in place.
    floor: { ...large.layout.floor, x: -2.725, w: 34.25 },
    deskZone: { ...large.layout.deskZone, x: -3.325, w: 21.25 },
    stageZone: { ...large.layout.stageZone, x: -18.0 },
  },
  desks: { ...large.desks, columns: [-11.5, -6.05, -0.6, 4.85] },
  idle: large.idle.map((spot) => ["plant", "window"].includes(spot.why)
    ? { ...spot, dx: spot.dx - 5.45 } : spot),
};

function freeze(value) {
  if (value && typeof value === "object") {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

export const S = freeze(small);
export const M = freeze(medium);
export const L = freeze(large);
export const XL = freeze(extraLarge);
export const DEFAULT_SPEC = M;
export const LAYOUT_SPECS = Object.freeze({ S, M, L, XL });
