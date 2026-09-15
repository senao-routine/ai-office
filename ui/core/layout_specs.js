// レイアウトの寸法・席・巡回演出の正本（単位 m、yaw は rad）。
// R94: M はガラス会議室とラウンジを持つ基準間取り。L/XL は机の既存ピッチを保つ。
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

// Doors face the circulation spine. Room dimensions include the complete glass frame.
const rooms = [
  { id: "meet", zone: "meetZone", door: { side: "south", offset: 0, w: 1.3 },
    table: { w: 4.4, d: 1.65, h: .86 }, rug: { w: 6.5, d: 3.8 },
    media: { dx: 0, dz: -1.99, w: 2.7, screen: true },
    plant: { dx: -2.95, dz: -1.38, scale: .85, species: "strelitzia" },
    seats: [
      ...[-1.4, 0, 1.4].map((dx) => ({ dx, dz: -1.3, yaw: 0 })),
      ...[-1.4, 0, 1.4].map((dx) => ({ dx, dz: 1.3, yaw: Math.PI })),
      { dx: -2.72, dz: 0, yaw: Math.PI / 2 }, { dx: 2.72, dz: 0, yaw: -Math.PI / 2 },
    ], chibi: [
      { dx: -2.6, dz: -1.3, yaw: 0 }, { dx: 2.6, dz: -1.3, yaw: 0 },
      { dx: -2.6, dz: 1.3, yaw: Math.PI }, { dx: 2.6, dz: 1.3, yaw: Math.PI },
    ] },
  { id: "meet2", zone: "meet2Zone", door: { side: "west", offset: 0, w: 1.2 },
    table: { w: 2.45, d: 1.25, h: .82 }, rug: { w: 4.6, d: 2.65 },
    media: { dx: 2.38, dz: 0, w: 1.75, yaw: -Math.PI / 2, screen: false },
    plant: { dx: 1.82, dz: -1.0, scale: .65, species: "snake" },
    seats: [
      { dx: -.75, dz: 1.03, yaw: Math.PI }, { dx: .75, dz: 1.03, yaw: Math.PI },
      { dx: -.75, dz: -1.03, yaw: 0 }, { dx: .75, dz: -1.03, yaw: 0 },
    ], chibi: [
      { dx: -1.75, dz: -.65, yaw: Math.PI / 2 }, { dx: 1.75, dz: -.65, yaw: -Math.PI / 2 },
      { dx: -1.75, dz: .65, yaw: Math.PI / 2 }, { dx: 1.75, dz: .65, yaw: -Math.PI / 2 },
    ] },
  { id: "meet3", zone: "meet3Zone", door: { side: "east", offset: 0, w: 1.1 },
    table: { w: 1.55, d: .9, h: .80 }, rug: { w: 2.25, d: 2.0 },
    media: { dx: -1.1, dz: 0, w: 1.1, yaw: Math.PI / 2, screen: false },
    plant: { dx: -.93, dz: -.75, scale: .5, species: "snake" },
    seats: [
      { dx: -.35, dz: .82, yaw: Math.PI }, { dx: .35, dz: -.82, yaw: 0 },
    ], chibi: [
      { dx: -1.02, dz: -.35, yaw: Math.PI / 2 }, { dx: 1.02, dz: -.35, yaw: -Math.PI / 2 },
      { dx: -1.02, dz: .35, yaw: Math.PI / 2 }, { dx: 1.02, dz: .35, yaw: -Math.PI / 2 },
    ] },
  { id: "meet4", zone: "meet4Zone", door: { side: "south", offset: -1.75, w: 1.3 },
    table: { w: 4.25, d: 1.65, h: .86 }, rug: { w: 5.95, d: 3.85 },
    media: { dx: 0, dz: -2.04, w: 2.8, screen: true },
    plant: { dx: 2.65, dz: -1.5, scale: .82, species: "monstera" },
    seats: [
      ...[-1.35, 0, 1.35].map((dx) => ({ dx, dz: -1.3, yaw: 0 })),
      ...[-1.35, 0, 1.35].map((dx) => ({ dx, dz: 1.3, yaw: Math.PI })),
      { dx: -2.65, dz: 0, yaw: Math.PI / 2 }, { dx: 2.65, dz: 0, yaw: -Math.PI / 2 },
    ], chibi: [
      { dx: -2.55, dz: -1.3, yaw: 0 }, { dx: 2.55, dz: -1.3, yaw: 0 },
      { dx: -2.55, dz: 1.3, yaw: Math.PI }, { dx: 2.55, dz: 1.3, yaw: Math.PI },
    ] },
];

const medium = {
  id: "M",
  layout: {
    floor: { w: 28.8, d: 19.0, z: -0.6 },
    deskZone: { x: -0.6, z: 0.35, w: 15.8, d: 11.4, lift: 0.12 },
    meetZone: { x: -9.2, z: -7.7, w: 7.2, d: 4.4, lift: 0.22 },
    stageZone: { x: -12.55, z: 0.5, w: 2.9, d: 3.4, lift: 0.12 },
    meet2Zone: { x: 10.85, z: 0.35, w: 5.2, d: 3.2, lift: 0.22 },
    loungeZone: { x: 10.75, z: 5.875, w: 6.0, d: 4.15, lift: 0.12 },
    meet3Zone: { x: -12.8, z: -3.35, w: 2.5, d: 2.4, lift: 0.22 },
    meet4Zone: { x: 10.7, z: -5.3, w: 6.6, d: 4.5, lift: 0.22 },
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
    { id: "receptionSign", kind: "sign", ref: "queueZone", dx: -.55, dz: 1.2,
      ...FURNITURE.sign, w: .6, h: .35, d: .22, boardY: .94, solid: true },
    { id: "bookcase", kind: "shelf", ref: "back", dx: -4.5, dz: .65, ...FURNITURE.shelf },
    { id: "coffee", kind: "cafeCounter", ref: "back", dx: 3.8, dz: .80,
      w: 2.8, h: 1.06, d: .85, approach: 2.0 },
    ...[-.85, 0, .85].map((dx, i) => ({ id: `cafeStool${i}`, kind: "stool", ref: "back",
      dx: 3.8 + dx, dz: 2.03, w: .5, d: .5, h: .73, yaw: Math.PI, solid: true })),
    { id: "phoneBooth", kind: "phoneBooth", ref: "back", dx: -4.6, dz: 2.65,
      w: 1.35, d: 1.65, h: 2.25, solid: true },
    { id: "bossDesk", kind: "desk", gen: "desk_long", ref: "back", dx: -.6, dz: 2.1, dy: .26, w: 2.9, d: 1.15 },
    { id: "stageNorth", kind: "sofa", ref: "stageZone", dx: .1, dz: -.95, w: 2.2, d: .95, seats: 2 },
    { id: "stageSouth", kind: "sofa", ref: "stageZone", dx: .1, dz: 1.0, yaw: Math.PI, w: 2.2, d: .95, seats: 2 },
    { id: "stageTable", kind: "desk", ref: "stageZone", dx: .1, dz: .05, w: 1.5, d: .65, h: .34, table: true },
    { id: "bench", kind: "sofa", ref: "left", dx: .9, dz: 4.9, yaw: Math.PI / 2, w: 3.4, d: .95, seats: 3 },
    { id: "benchTable", kind: "desk", ref: "left", dx: 2.1, dz: 4.9, w: .85, d: .85, h: .36, table: true },
    { id: "loungeSofa", kind: "sofa", ref: "loungeZone", dx: -.55, dz: -1.18,
      w: 3.2, d: 1.05, seats: 3, material: "linen" },
    { id: "loungeSofaSouth", kind: "sofa", ref: "loungeZone", dx: -.55, dz: 1.18,
      w: 3.2, d: 1.05, seats: 3, yaw: Math.PI, material: "linen" },
    { id: "loungeChair", kind: "sofa", ref: "loungeZone", dx: 2.0, dz: -.62,
      w: .92, d: .94, seats: 1, yaw: -Math.PI / 2, material: "sofaC" },
    { id: "loungeChairSouth", kind: "sofa", ref: "loungeZone", dx: 2.0, dz: .62,
      w: .92, d: .94, seats: 1, yaw: -Math.PI / 2, material: "sofaC" },
    { id: "loungeTable", kind: "roundTable", ref: "loungeZone", dx: -.65, dz: 0, w: 1.12, d: 1.12, h: .36 },
    { id: "loungeSideTable", kind: "roundTable", ref: "loungeZone", dx: .65, dz: .16, w: .66, d: .66, h: .43 },
    { id: "loungeStorage", kind: "shelf", ref: "loungeZone", dx: -2.62, dz: 0,
      w: 1.8, d: .40, h: .72, yaw: Math.PI / 2, rows: 2 },
    { id: "loungeLamp", kind: "floorLamp", ref: "loungeZone", dx: 1.15, dz: -1.52, w: .58, d: .58, h: 1.78 },
    { id: "loungePendant", kind: "pendant", ref: "loungeZone", dx: -.55, dz: 0, dy: 2.28,
      radius: .52, h: .28, cordH: 0, nonEmissive: true },
    { id: "windowPlanter", kind: "planter", ref: "left", dx: .62, dz: 2.68, w: .60, d: .70, h: .55 },
    { id: "lockers", kind: "shelf", ref: "left", dx: 4.8, dz: .25,
      w: 1.7, d: .45, h: 1.5, yaw: Math.PI / 2, rows: 2, sideboard: true, solid: true },
    { id: "water", kind: "waterStation", ref: "left", dx: 4.8, dz: 1.60,
      w: .58, d: .52, h: 1.25, yaw: Math.PI / 2, solid: true },
    { id: "touchdown", kind: "desk", ref: "front", dx: 4.2, dz: -1.9, w: 2.7, d: .95, h: 1.015, table: true },
    { id: "extConsole", kind: "shelf", ref: "left", dx: 4.2, dz: 4.45,
      w: 4.1, d: .65, h: .96, yaw: Math.PI / 2, rows: 1, sideboard: true },
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
  ],
  art: [{ ref: "back", dx: -3.0, dz: .27, dy: 1.9 }, { ref: "back", dx: 1.8, dz: .27, dy: 1.9 }],
  queue: { rows: 2, columns: 6, pitch: 1.1 },
  external: { count: 5, ref: "left", dx: 5.3, z: 2.55, pitch: .95, yaw: -Math.PI / 2 },
  idle: [
    { dx: 3.8, dz: -7.3, yaw: Math.PI, why: "coffee" },
    { ref: "left", dx: 2.0, dz: 2.6, yaw: -Math.PI / 2, why: "plant" },
    // The old window stop stood inside the bench seat; use the clear floor beyond its south arm.
    { dx: -13.3, dz: 7.15, yaw: Math.PI / 2, why: "window" },
    { ref: "loungeZone", dx: -3.65, dz: 0, yaw: Math.PI / 2, why: "lounge" },
  ],
  rest: [
    { area: "lounge", ref: "loungeZone", dx: 1.9, dz: -.62, yaw: -Math.PI / 2, role: "tablet" },
    { area: "lounge", ref: "loungeZone", dx: -.55, dz: -1.08, yaw: 0 },
    { area: "lounge", ref: "loungeZone", dx: -.55, dz: 1.08, yaw: Math.PI },
    { area: "sofa", ref: "stageZone", dx: -0.2, dz: -0.95, yaw: 0 },
    { area: "sofa", ref: "stageZone", dx: 0.4, dz: 1.15, yaw: Math.PI },
    { area: "bench", ref: "left", dx: 0.9, dz: 3.95, yaw: Math.PI / 2 },
    { area: "bench", ref: "left", dx: 0.9, dz: 4.90, yaw: Math.PI / 2 },
    { area: "bench", ref: "left", dx: 0.9, dz: 5.85, yaw: Math.PI / 2 },
  ],
  // 巡回演出は従来の折れ線を維持する。歩行用グラフの入力には使わない。
  cleaner: [
    [-8.8, -5.0], [-3.3, -5.0], [2.1, -5.0], [3.9, -5.0], [6.95, -5.0],
    [6.95, 0.35], [2.1, 0.35], [2.1, 5.3], [5.9, 5.3],
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
    floor: { w: 28.8, d: 22.0, z: 0.9 },
    deskZone: { ...medium.layout.deskZone, z: 1.85, d: 14.4 },
    loungeZone: { ...medium.layout.loungeZone, z: 8.875 },
    queueZone: { ...medium.layout.queueZone, z: 9.0 },
  },
  desks: { ...medium.desks, rows: [-2.35, 1.85, 6.05] },
  idle: medium.idle,
  cleaner: medium.cleaner.map(([x, z]) => [x, z >= 5.3 ? z + 3.0 : z]),
};

const extraLarge = {
  ...large, id: "XL",
  layout: {
    ...large.layout,
    // Keep the east wall and every meeting/reception anchor in place.
    floor: { ...large.layout.floor, x: -1.1, w: 31.0 },
    deskZone: { ...large.layout.deskZone, x: -1.9, w: 18.4 },
    stageZone: { ...large.layout.stageZone, x: -14.75 },
  },
  desks: { ...large.desks, columns: [-8.65, -4.15, 0.35, 4.85] },
  idle: large.idle.map((spot) => spot.why === "window"
    ? { ...spot, dx: spot.dx - 2.2 } : spot),
  // Keep the patrol in the narrowed aisles beside the shifted desk columns.
  cleaner: large.cleaner.map(([x, z]) => [x === -8.8 ? -11.0 : x === -3.3 ? -1.75 : x === 2.1 ? 2.75 : x, z]),
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
