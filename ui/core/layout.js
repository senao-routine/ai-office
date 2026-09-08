// 純粋なレイアウト構築。描画層に依存せず、同じ spec から同じ座標と通路を返す。

const EPS = 1e-9;

function inside([x, z], r) {
  return x > r.x1 + EPS && x < r.x2 - EPS && z > r.z1 + EPS && z < r.z2 - EPS;
}

// グリッドの軸平行な辺専用。点だけの判定では 0.5m 未満の障害物を飛び越すため、辺も調べる。
function crosses(a, b, r) {
  if (a[1] === b[1]) {
    return a[1] > r.z1 + EPS && a[1] < r.z2 - EPS
      && Math.max(a[0], b[0]) > r.x1 + EPS && Math.min(a[0], b[0]) < r.x2 - EPS;
  }
  return a[0] > r.x1 + EPS && a[0] < r.x2 - EPS
    && Math.max(a[1], b[1]) > r.z1 + EPS && Math.min(a[1], b[1]) < r.z2 - EPS;
}

/** 床 → 余白を確保したグリッド → 4近傍 → 直線上の次数2ノードだけ縮約。 */
function buildWalkGraph(wall, obstacles, navigation = {}) {
  const { grid = 0.5, margin = 0.45 } = navigation;
  if (!Number.isFinite(grid) || grid <= 0 || !Number.isFinite(margin) || margin < 0.45) {
    throw new RangeError("navigation requires a positive grid and margin >= 0.45m");
  }
  const padded = obstacles.map((r) => ({
    x1: r.x1 - margin, z1: r.z1 - margin, x2: r.x2 + margin, z2: r.z2 + margin,
  }));
  const nodes = [];
  const adj = [];
  const cells = new Map();
  const nx = Math.floor((wall.right - wall.left - 2 * margin + EPS) / grid);
  const nz = Math.floor((wall.front - wall.back - 2 * margin + EPS) / grid);
  // 南東の内壁から刻む。壁際の幅0.9mの通路も中心線をサンプリングできる。
  // 丸めはグリッドの演算誤差だけに適用（造作・アンカーの数値は丸めない）。
  const snap = (n) => Math.round(n * 1e9) / 1e9;
  for (let iz = 0; iz <= nz; iz++) {
    for (let ix = 0; ix <= nx; ix++) {
      const p = [snap(wall.right - margin - ix * grid), snap(wall.front - margin - iz * grid)];
      if (padded.some((r) => inside(p, r))) continue;
      const i = nodes.length;
      nodes.push(p);
      adj.push([]);
      cells.set(`${ix},${iz}`, i);
      for (const key of [`${ix - 1},${iz}`, `${ix},${iz - 1}`]) {
        const j = cells.get(key);
        if (j === undefined || padded.some((r) => crosses(p, nodes[j], r))) continue;
        adj[i].push(j);
        adj[j].push(i);
      }
    }
  }
  if (!nodes.length) throw new RangeError("layout has no walkable grid cells");

  // 壁/家具の裏の孤立した空床を除外。routePath は到達不能の代替経路を持たないため、
  // 最大の連結成分を歩行領域とする。同数の場合は先に走査した成分で決定論を保つ。
  const seen = new Set();
  let component = [];
  for (let i = 0; i < nodes.length; i++) {
    if (seen.has(i)) continue;
    const queue = [i];
    seen.add(i);
    for (let head = 0; head < queue.length; head++) {
      for (const j of adj[queue[head]]) {
        if (!seen.has(j)) { seen.add(j); queue.push(j); }
      }
    }
    if (queue.length > component.length) component = queue;
  }
  const keep = component.filter((i) => {
    if (adj[i].length !== 2) return true;
    const [a, b] = adj[i].map((j) => nodes[j]);
    return a[0] !== b[0] && a[1] !== b[1]; // 曲がり角は残す
  }).sort((a, b) => a - b);
  const index = new Map(keep.map((i, j) => [i, j]));
  const edges = [];
  for (const i of keep) {
    for (const neighbor of adj[i]) {
      let prev = i;
      let next = neighbor;
      while (!index.has(next)) {
        const after = adj[next].find((j) => j !== prev);
        prev = next;
        next = after;
      }
      if (index.get(i) < index.get(next)) edges.push([index.get(i), index.get(next)]);
    }
  }
  // Route endpoints can be in the middle of contracted edges. Carry the solid
  // rectangles so their connections to the graph can be checked as well.
  return { nodes: keep.map((i) => nodes[i]), edges,
    obstacles: obstacles.map((r) => ({ ...r })) };
}

/**
 * buildLayout(spec) → 独立したデータ一式。obstacleRects は配列、walkGraph は {nodes,edges}。
 * desk/meeting/lounge/queue/external は席アンカー、chibi は部屋ID別の席配列。
 * 会議/休憩の席は自分の部屋に入る（最後のアプローチ）。机の天板は全席が避ける。
 */
export function buildLayout(spec) {
  const LAYOUT = Object.fromEntries(Object.entries(spec.layout).map(([key, value]) => [key, { ...value }]));
  const f = LAYOUT.floor;
  if (![f.x ?? 0, f.w, f.d, f.z].every(Number.isFinite) || f.w <= 0 || f.d <= 0) {
    throw new RangeError("floor requires finite positive dimensions");
  }
  const WALL = {
    left: (f.x ?? 0) - f.w / 2, right: (f.x ?? 0) + f.w / 2, back: f.z - f.d / 2, front: f.z + f.d / 2,
  };
  const PODS = spec.desks.rows.flatMap((z) => spec.desks.columns.map((x) => [x, z]));
  const origins = {
    ...LAYOUT,
    left: { x: WALL.left, z: 0 }, right: { x: WALL.right, z: 0 },
    back: { x: 0, z: WALL.back }, front: { x: 0, z: WALL.front },
    backRight: { x: WALL.right, z: WALL.back },
  };
  const point = ({ ref, dx = 0, dz = 0 }) => {
    const origin = ref ? origins[ref] : { x: 0, z: 0 };
    return { x: origin.x + dx, z: origin.z + dz };
  };
  const seat = (def) => ({
    ...point(def), yaw: def.yaw, y: (origins[def.ref]?.lift ?? 0) + (def.dy ?? 0),
    ...(def.role === undefined ? {} : { role: def.role }),
  });
  const restSpots = spec.rest.map(({ area, ...def }) => ({ area, ...seat(def) }));
  const furnishings = (spec.furnishings || []).map((def) => ({
    ...def, ...point(def), y: (origins[def.ref]?.lift ?? 0) + (def.dy ?? 0),
  }));
  const signs = PODS.map(([x, z], pod) => ({
    pod, x: x + spec.desks.top.w / 2 + (spec.desks.sign?.gap ?? .24),
    y: LAYOUT.deskZone.lift, z: z + (spec.desks.sign?.dz ?? .65),
    yaw: spec.desks.sign?.yaw ?? Math.PI / 2,
  }));
  const anchors = {
    desk: PODS.flatMap(([x, z]) => [1, -1].map((front) => ({
      x, z: z + front * spec.desks.seatOffset, yaw: front > 0 ? Math.PI : 0, y: LAYOUT.deskZone.lift,
    }))),
    meeting: { byRoom: Object.fromEntries(spec.rooms.map((room) => [room.id,
      room.seats.map((def) => seat({ ...def, ref: room.zone })),
    ])) },
    lounge: restSpots.filter((s) => s.area === "lounge").map(({ area, ...s }) => s),
    queue: Array.from({ length: spec.queue.rows }, (_, row) =>
      Array.from({ length: spec.queue.columns }, (_, col) => ({
        x: LAYOUT.queueZone.x + spec.queue.pitch * col,
        z: LAYOUT.queueZone.z - row * spec.queue.pitch, yaw: 0, y: 0,
      }))).flat(),
    external: Array.from({ length: spec.external.count }, (_, i) => ({
      x: WALL.right + spec.external.dx, z: spec.external.z + i * spec.external.pitch, yaw: Math.PI / 2, y: 0,
    })),
    chibi: Object.fromEntries(spec.rooms.map((room) => [room.id,
      room.chibi.map((def) => seat({ ...def, ref: room.zone })),
    ])),
  };
  const { top, obstacleMargin: m } = spec.desks;
  const obstacleRects = PODS.map(([x, z]) => ({
    id: `pod:${x},${z}`, x1: x - top.w / 2 - m, z1: z - top.d / 2 - m,
    x2: x + top.w / 2 + m, z2: z + top.d / 2 + m,
  }));
  for (const { id, zone } of [...spec.rooms, ...spec.solidZones]) {
    const r = LAYOUT[zone];
    obstacleRects.push({ id, x1: r.x - r.w / 2, z1: r.z - r.d / 2, x2: r.x + r.w / 2, z2: r.z + r.d / 2 });
  }
  for (const fixture of spec.fixtures) {
    const a = point(fixture.from);
    const b = point(fixture.to);
    obstacleRects.push({ id: fixture.id, x1: a.x, z1: a.z, x2: b.x, z2: b.z });
  }
  // The additional fixtures use the same rotated footprint as their rendered kit.
  const footprint = (id, s, w, d) => {
    const c = Math.abs(Math.cos(s.yaw || 0)), t = Math.abs(Math.sin(s.yaw || 0));
    const hx = (w * c + d * t) / 2, hz = (w * t + d * c) / 2;
    return { id, x1: s.x - hx, z1: s.z - hz, x2: s.x + hx, z2: s.z + hz };
  };
  // Room furniture is already contained in the room's solid rectangle. Cover the
  // previously unmodelled wall furniture and entrance planting explicitly.
  for (const f of furnishings) {
    if (["bookcase", "coffee", "bench", "benchTable", "extConsole"].includes(f.id) || f.kind === "planter") {
      obstacleRects.push(footprint(f.id === "bench" ? "bench" : `furnishing:${f.id}`, f, f.w, f.d));
    }
  }
  for (const s of signs) obstacleRects.push(footprint(`sign:${s.pod}`, s,
    Math.max(spec.sign.w, spec.sign.footW), Math.max(spec.sign.d, spec.sign.footD)));
  return {
    LAYOUT: Object.freeze(LAYOUT), WALL: Object.freeze(WALL), PODS: Object.freeze(PODS),
    zones: Object.entries(LAYOUT).filter(([id]) => id !== "floor").map(([id, zone]) => ({ id, ...zone })),
    anchors, furnishings, signs, art: (spec.art || []).map((def) => ({ ...point(def), y: def.dy })),
    obstacleRects, walkGraph: buildWalkGraph(WALL, obstacleRects, spec.navigation),
    cleanerRoute: Object.freeze(spec.cleaner.map((p) => [...p])),
    bossWalk: Object.freeze(spec.boss.map((p) => [...p])),
    idleSpots: Object.freeze(spec.idle.map((def) => ({ ...point(def), yaw: def.yaw, why: def.why }))),
    restSpots: Object.freeze(restSpots),
  };
}
