// R58: 歩行ナビの機械検査。「経路が机・部屋を横切らない」をここで強制する
// （すり抜けはユーザーが実際に目撃した回帰＝再発したらこのテストが落ちる）。
//   node --test ui/core/nav.test.js
import assert from "node:assert/strict";
import { test } from "node:test";
import { assignSeats } from "./world.js";
import { buildLayout } from "./layout.js";
import { LAYOUT_SPECS } from "./layout_specs.js";
import {
  BOSS_WALK, CLEANER_ROUTE, IDLE_SPOTS, LAYOUT, PODS, REST_SPOTS, WALL,
  obstacleRects, routePath, segIntersectsRect, walkGraph,
} from "./nav.js";

test("segIntersectsRect: 交差・内包・非交差・掠め", () => {
  const r = { x1: -1, z1: -1, x2: 1, z2: 1 };
  assert.equal(segIntersectsRect(-2, 0, 2, 0, r), true);     // 貫通
  assert.equal(segIntersectsRect(0, 0, 3, 0, r), true);      // 端点が中
  assert.equal(segIntersectsRect(-2, 2, 2, 2, r), false);    // 外を平行
  assert.equal(segIntersectsRect(-2, -2, -1.5, 2, r), false); // 左を通過
  assert.equal(segIntersectsRect(-2, 1, 2, 1, r), false);    // 辺上の掠めは交差にしない
});

test("通路グラフのエッジは全障害物と交差しない（レーン設計の機械保証）", () => {
  const { nodes, edges } = walkGraph();
  const rects = obstacleRects();
  for (const [i, j] of edges) {
    const [ax, az] = nodes[i];
    const [bx, bz] = nodes[j];
    for (const r of rects) {
      assert.equal(segIntersectsRect(ax, az, bx, bz, r), false,
        `エッジ(${ax},${az})→(${bx},${bz}) が ${r.id} を横切っている`);
    }
  }
  // 全ノードが床の内側
  for (const [x, z] of nodes) {
    assert.ok(x > WALL.left && x < WALL.right && z > WALL.back && z < WALL.front + 0.2,
      `ノード(${x},${z})が床の外`);
  }
});

/** 席・入口・スポットなど代表点。scene の anchor 定義と同じ式から導出する。 */
function seatPoints() {
  return PODS.flatMap(([cx, cz]) => [
    [cx, cz + 1.32], [cx, cz - 1.32],
  ]);
}

test("代表経路の中間セグメントは机・部屋を横切らない（すり抜け根絶ピン）", () => {
  const rects = obstacleRects();
  const entrance = [-8.3, WALL.front - 0.85];
  const coffee = [3.9, WALL.back + 1.85];
  const seats = seatPoints();
  const pairs = [];
  for (const s of seats) {
    pairs.push([entrance, s]);        // 出社
    pairs.push([s, coffee]);          // コーヒー
    for (const spot of IDLE_SPOTS) pairs.push([s, [spot.x, spot.z]]);   // 待機ライフ
    for (const spot of REST_SPOTS) pairs.push([s, [spot.x, spot.z]]);   // R59 休憩スポット
  }
  pairs.push([entrance, [WALL.right - 1.35, -4.4]]);   // 外部コンソール
  pairs.push([entrance, [LAYOUT.queueZone.x, LAYOUT.queueZone.z]]);
  for (const [from, to] of pairs) {
    const path = routePath(from, to);
    assert.ok(path.length >= 2, "経路が生成される");
    // 最初（現在地→最寄りノード）と最後（ノード→目的地）は目的の什器へ踏み込むため対象外。
    // 中間＝通路上のセグメントは障害物と交差してはならない。
    for (let i = 1; i < path.length - 2; i++) {
      const [ax, az] = path[i];
      const [bx, bz] = path[i + 1];
      for (const r of rects) {
        assert.equal(segIntersectsRect(ax, az, bx, bz, r), false,
          `${JSON.stringify(from)}→${JSON.stringify(to)} の中間区間` +
          `(${ax},${az})→(${bx},${bz}) が ${r.id} を横切る`);
      }
    }
    // アプローチ（最初・最後の区間）は短いこと＝「通路までワープ級の直線」を許さない
    const approach = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
    assert.ok(approach(path[0], path[1]) < 6.5,
      `${JSON.stringify(from)} の合流が長すぎる`);
    assert.ok(approach(path[path.length - 2], path[path.length - 1]) < 6.5,
      `${JSON.stringify(to)} へのアプローチが長すぎる`);
  }
});

test("IDLE_SPOTS は障害物の外・床の内側", () => {
  const rects = obstacleRects();
  for (const s of IDLE_SPOTS) {
    assert.ok(s.x > WALL.left && s.x < WALL.right && s.z > WALL.back && s.z < WALL.front,
      `${s.why} が床の外`);
    for (const r of rects) {
      const inside = s.x > r.x1 && s.x < r.x2 && s.z > r.z1 && s.z < r.z2;
      assert.equal(inside, false, `${s.why} が ${r.id} の中`);
    }
  }
});

test("routePath: 同一点・近接点でも壊れない", () => {
  const p = routePath([0, 0], [0, 0]);
  assert.ok(p.length >= 2);
  const q = routePath([1, 1], [1.05, 1.05]);
  assert.ok(q.length >= 2);
});

test("R59: REST_SPOTS は床の内側・エリア外の席は障害物に載らない・容量分散の前提", () => {
  const rects = obstacleRects();
  const areas = {};
  for (const s of REST_SPOTS) {
    areas[s.area] = (areas[s.area] || 0) + 1;
    assert.ok(s.x > WALL.left && s.x < WALL.right && s.z > WALL.back && s.z < WALL.front,
      `${s.area} が床の外`);
    if (["lounge", "sofa", "bench"].includes(s.area)) {
      // ソファ席は自エリアのゾーン矩形の中に載るのが意図（最終アプローチ扱い）。
      // ただし「別の」障害物には入っていないこと
      const own = s.area === "sofa" ? "stage" : s.area;
      for (const r of rects) {
        if (r.id === own) continue;
        const inside = s.x > r.x1 && s.x < r.x2 && s.z > r.z1 && s.z < r.z2;
        assert.equal(inside, false, `${s.area} の席が ${r.id} の中`);
      }
    } else {
      for (const r of rects) {
        const inside = s.x > r.x1 && s.x < r.x2 && s.z > r.z1 && s.z < r.z2;
        assert.equal(inside, false, `${s.area} の席が ${r.id} の中`);
      }
    }
  }
  // 分散の前提: エリアが3つ以上・どのエリアも2席以上（R62=1箇所に溜めない）
  assert.ok(Object.keys(areas).length >= 3, "休憩エリアが複数ある");
  assert.ok(areas.lounge >= 2 && areas.sofa >= 2 && areas.bench >= 3,
    `席数が足りない: ${JSON.stringify(areas)}`);
  // どのエリアも定員3以内（「だいたい3体ぐらい」のユーザー指定）
  for (const [k, n] of Object.entries(areas)) assert.ok(n <= 3, `${k} の席が多すぎる: ${n}`);
});

test("R62: bench席は実在のソファ座面の上（宙に座らせない機械ピン）", () => {
  // office.js のソファヌック: 座 slab(0.95, 0.40, 3.4) を (W.left+0.85, 0.24, 4.9) に置く。
  // 天面 y=0.44・x[-0.475,+0.475]・z[-1.7,+1.7] の範囲に席が載っていることを固定する。
  const cx = WALL.left + 0.85;
  const cz = 4.9;
  const bench = REST_SPOTS.filter((s) => s.area === "bench");
  assert.equal(bench.length, 3, "赤黄クッションのソファは3席");
  for (const s of bench) {
    assert.ok(Math.abs(s.x - cx) <= 0.475, `bench席 x=${s.x} が座面の外`);
    assert.ok(Math.abs(s.z - cz) <= 1.7, `bench席 z=${s.z} が座面の外`);
    // 座面天面0.44 に対し、ソファ座席の規約 y = 天面-0.45（ソファコーナー=0.57→0.12）
    assert.ok(Math.abs(s.y - (0.44 - 0.45)) < 0.05, `bench席 y=${s.y} が座面高と合わない`);
    assert.ok(Math.abs(s.yaw - Math.PI / 2) < 1e-9, "背もたれ（左壁）を背に部屋を向く");
  }
  // 3席が重ならない（0.9m以上離す＝肩幅ぶん）
  const zs = bench.map((s) => s.z).sort((a, b) => a - b);
  for (let i = 1; i < zs.length; i++) {
    assert.ok(zs[i] - zs[i - 1] >= 0.9, `bench席が近すぎる: ${zs}`);
  }
});

// ── R68: アンビエント役者の巡回路も歩行者と同じ掟（障害物と交差しない） ──
test("R68: 掃除ロボの巡回路は全障害物と交差しない・ループしている", () => {
  const rects = obstacleRects();
  for (let i = 1; i < CLEANER_ROUTE.length; i++) {
    const [ax, az] = CLEANER_ROUTE[i - 1];
    const [bx, bz] = CLEANER_ROUTE[i];
    for (const r of rects) {
      assert.equal(segIntersectsRect(ax, az, bx, bz, r), false,
        `掃除ロボ区間${i}(${ax},${az})→(${bx},${bz}) が ${r.id} を横切る`);
    }
  }
  // ループ（最後の点=最初の点）＝永久巡回で終端ワープしない
  assert.deepEqual(CLEANER_ROUTE[0], CLEANER_ROUTE[CLEANER_ROUTE.length - 1]);
});

test("R68: ボスの見回り路は北通路レーン上（壇アプローチの先頭/末尾以外は交差0）", () => {
  const rects = obstacleRects();
  // 先頭と末尾の区間は壇（boss矩形）へ入るアプローチ＝例外。中間は交差0を強制
  for (let i = 2; i < BOSS_WALK.length - 1; i++) {
    const [ax, az] = BOSS_WALK[i - 1];
    const [bx, bz] = BOSS_WALK[i];
    for (const r of rects) {
      assert.equal(segIntersectsRect(ax, az, bx, bz, r), false,
        `ボス見回り区間${i}(${ax},${az})→(${bx},${bz}) が ${r.id} を横切る`);
    }
  }
});

test("R70: queue 12席（2列×6）が障害物の外＋entranceからの経路が交差0", () => {
  // queueAnchors は iso 層なので同じ式で再現（queueZone 相対・1.1mピッチ×6・2列目は北へ1.1）
  const seats = [];
  for (let row = 0; row < 2; row++) {
    for (let i = 0; i < 6; i++) {
      seats.push({ x: LAYOUT.queueZone.x + 1.1 * i, z: LAYOUT.queueZone.z - row * 1.1 });
    }
  }
  const obstacles = obstacleRects();
  for (const s of seats) {
    for (const r of obstacles) {
      assert.ok(!(s.x > r.x1 && s.x < r.x2 && s.z > r.z1 && s.z < r.z2),
        `queue席(${s.x},${s.z})が障害物 ${JSON.stringify(r)} の中`);
    }
    // ENTRANCE は iso 層＝coreからimportしない（office.js の定義と同式でWALLから導出）
    const entrance = { x: -8.3, z: WALL.front - 0.85 };
    const path = routePath(entrance, s);
    for (let i = 0; i + 1 < path.length - 1; i++) {   // 中間セグメントのみ（末尾は席へのアプローチ）
      for (const r of obstacles) {
        assert.ok(!segIntersectsRect(path[i].x, path[i].z, path[i + 1].x, path[i + 1].z, r),
          `entrance→queue席(${s.x.toFixed(1)}) が障害物と交差`);
      }
    }
  }
});

test("R70: 第3会議室=障害物登録・全席が部屋の内側・南口ノード経由の経路が交差0", () => {
  const k = LAYOUT.meet3Zone;
  const obstacles = obstacleRects();
  assert.ok(obstacles.some((r) => r.id === "meet3"), "meet3 が障害物に登録されている");
  // 会議席（iso 層の meetingAnchorsByRoom と同式・小卓の南北）
  const seats = [
    { x: k.x - 0.35, z: k.z + 0.95 }, { x: k.x + 0.35, z: k.z - 0.95 },
  ];
  for (const s of seats) {
    assert.ok(Math.abs(s.x - k.x) <= k.w / 2 && Math.abs(s.z - k.z) <= k.d / 2,
      `meet3席(${s.x},${s.z})が部屋の外`);
    // エントランス→席の中間セグメントが meet3 以外の障害物と交差しない
    const entrance = { x: -8.3, z: WALL.front - 0.85 };
    const path = routePath(entrance, s);
    for (let i = 0; i + 1 < path.length - 1; i++) {
      for (const r of obstacles) {
        if (r.id === "meet3") continue;               // 目的の部屋自身へは入ってよい
        assert.ok(!segIntersectsRect(path[i].x, path[i].z, path[i + 1].x, path[i + 1].z, r),
          `entrance→meet3席 が ${r.id} と交差`);
      }
    }
  }
  // 新設の南辺通路エッジ（ラウンジ南→meet3口）も全障害物と非交差=walkGraphテストが包括するが
  // ラウンジ縮小の回帰として明示ピン: ラウンジ矩形が南辺通路(z8.45)に達していない
  const lounge = obstacles.find((r) => r.id === "lounge");
  assert.ok(lounge.z2 < 8.45, "ラウンジが南辺通路を塞いでいる");
});

test("R73: 第4会議室=障害物登録・全席が部屋の内側・北通路を塞がない・経路が交差0", () => {
  const q = LAYOUT.meet4Zone;
  const obstacles = obstacleRects();
  assert.ok(obstacles.some((r) => r.id === "meet4"), "meet4 が障害物に登録されている");
  // 会議席（iso 層の meetingAnchorsByRoom と同式・長卓の南2席＋北1席）
  // R73.2: 縦長卓を挟んで西2＋東2＋南の議長1（iso 層の meetingAnchorsByRoom と同式）
  const seats = [
    { x: q.x - 1.35, z: q.z - 0.65 }, { x: q.x - 1.35, z: q.z + 0.75 },
    { x: q.x + 1.35, z: q.z - 0.65 }, { x: q.x + 1.35, z: q.z + 0.75 },
    { x: q.x, z: q.z + 1.7 },
  ];
  const entrance = { x: -8.3, z: WALL.front - 0.85 };
  for (const s of seats) {
    assert.ok(Math.abs(s.x - q.x) <= q.w / 2 && Math.abs(s.z - q.z) <= q.d / 2,
      `meet4席(${s.x},${s.z})が部屋の外`);
    const path = routePath(entrance, s);
    for (let i = 0; i + 1 < path.length - 1; i++) {
      for (const r of obstacles) {
        if (r.id === "meet4") continue;               // 目的の部屋自身へは入ってよい
        assert.ok(!segIntersectsRect(path[i].x, path[i].z, path[i + 1].x, path[i + 1].z, r),
          `entrance→meet4席 が ${r.id} と交差`);
      }
    }
  }
  // R73.2: 移設後の成立条件（崩れると「棚裏の机」や「通路封鎖」が静かに再発する）
  //   ①北通路 ZN=-5.0 より南＝通路を塞がない ②机の島の東端(x=7.3)に触れない
  //   ③外部コンソールの立ち位置(x=13.05)との間に通路を残す ④第2会議室と重ならない
  const room = obstacles.find((r) => r.id === "meet4");
  const meet2 = obstacles.find((r) => r.id === "meet2");
  assert.ok(room.z1 > -5.0, "meet4 が北通路(z=-5.0)に掛かっている");
  assert.ok(room.x1 > 7.3, "meet4 が机の島に食い込んでいる");
  assert.ok(room.x2 < 12.6, "meet4 が外部コンソールの通路を潰している");
  assert.ok(room.z2 < meet2.z1, "meet4 が第2会議室と重なっている");
});

test("R74: 右手前の通路幅＝第2↔第3・ラウンジ↔第3が人ひとり分より広い", () => {
  // ユーザーFB「幅間が狭い」。0.70m/0.45m はロボの幅とほぼ同じで通れない見た目だった。
  // 通路として成立する下限を 0.9m と決めて機械固定する（部屋を足すたびに潰れるため）。
  const MIN = 0.9;
  const rect = (z) => ({ x1: z.x - z.w / 2, x2: z.x + z.w / 2,
    z1: z.z - z.d / 2, z2: z.z + z.d / 2 });
  const m2 = rect(LAYOUT.meet2Zone);
  const m3 = rect(LAYOUT.meet3Zone);
  const lg = rect(LAYOUT.loungeZone);
  assert.ok(m3.z1 - m2.z2 >= MIN,
    `第2↔第3の通路が狭い: ${(m3.z1 - m2.z2).toFixed(2)}m`);
  assert.ok(m3.x1 - lg.x2 >= MIN,
    `ラウンジ↔第3の通路が狭い: ${(m3.x1 - lg.x2).toFixed(2)}m`);
  // 南辺通路（z=8.45・R70で新設）を第3会議室が塞いでいない
  assert.ok(m3.z2 < 8.45, "第3会議室が南辺通路に掛かっている");
});

// R90-V7: every tier and unlock stage must satisfy the same navigation contract.
import { specFor } from "./tier.js";
const navigationSpecs = [...Object.entries(LAYOUT_SPECS),
  ...Object.keys(LAYOUT_SPECS).flatMap((tier) => [0, 3, 5, 8, 12, 15, 20]
    .map((level) => [`${tier}:Lv${level}`, specFor(tier, level)]))];
for (const [id, spec] of navigationSpecs) {
  const layout = buildLayout(spec);
  const { nodes, edges } = layout.walkGraph;
  const rects = layout.obstacleRects;
  const margin = spec.navigation.margin;
  const padded = rects.map((r) => ({ ...r,
    x1: r.x1 - margin, x2: r.x2 + margin, z1: r.z1 - margin, z2: r.z2 + margin,
  }));
  const adj = nodes.map(() => []);
  for (const [i, j] of edges) { adj[i].push(j); adj[j].push(i); }

  test(`${id}: 縮約辺の途中と移動経路の途中から安全に再探索する`, () => {
    const target = [layout.anchors.desk[0].x, layout.anchors.desk[0].z];
    const starts = edges.filter(([i, j]) => Math.hypot(nodes[i][0] - nodes[j][0], nodes[i][1] - nodes[j][1]) > 1)
      .flatMap(([i, j]) => [0.25, 0.5, 0.75].map((t) =>
        nodes[i].map((value, axis) => value + t * (nodes[j][axis] - value))));
    const destination = layout.anchors.meeting.byRoom.meet3?.[0] || layout.anchors.meeting.byRoom.meet2[0];
    const outbound = routePath([-8.3, layout.WALL.front - 0.85], [destination.x, destination.z], layout.walkGraph);
    for (let i = 1; i < outbound.length - 1; i++) {
      starts.push(outbound[i - 1].map((value, axis) => (value + outbound[i][axis]) / 2));
    }
    if (id === "M") starts.push([6.7, 8.45]);  // Independent review reproduction.
    for (const from of starts) {
      const path = routePath(from, target, layout.walkGraph);
      assert.deepEqual(path.at(-1), target);
      for (let i = 1; i < path.length; i++) {
        for (const r of rects) assert.equal(segIntersectsRect(...path[i - 1], ...path[i], r), false,
          `${id}: reroute ${from}: ${path[i - 1]} -> ${path[i]} crosses ${r.id}`);
      }
    }
  });

  test(`${id}: 机8→ラウンジ0は実物の受付カウンターを避ける`, () => {
    const q = layout.LAYOUT.queueZone;
    // Independent dimensions from iso/office.js: top 3.4 x 1.0 at q+(1.6,1.2).
    const counter = { x1: q.x - 0.1, x2: q.x + 3.3, z1: q.z + 0.7, z2: q.z + 1.7 };
    assert.deepEqual(rects.find((r) => r.id === "reception"), { id: "reception", ...counter });
    const desk = layout.anchors.desk[Math.min(8, layout.anchors.desk.length - 1)];
    const lounge = layout.anchors.lounge[0];
    const path = routePath([desk.x, desk.z], [lounge.x, lounge.z], layout.walkGraph);
    assert.deepEqual(path.at(-1), [lounge.x, lounge.z]);
    for (let i = 1; i < path.length; i++) {
      assert.equal(segIntersectsRect(...path[i - 1], ...path[i], counter), false);
    }
  });

  if (spec.id !== "S") test(`${id}: 実席割当の12人同時移動が全員の目的席に到達する`, () => {
    const agents = Array.from({ length: 12 }, (_, n) => ({ id: `employee-${n}`, state: "working" }));
    const seats = assignSeats(agents, layout.anchors.desk.length);
    assert.equal(seats.size, 12);
    const rooms = Object.values(layout.anchors.meeting.byRoom).flat();
    const pairs = [...seats.values()].map((seat, n) => [layout.anchors.desk[seat], rooms[n % rooms.length]]);
    for (const [a, b] of pairs) {
      const path = routePath([a.x, a.z], [b.x, b.z], layout.walkGraph);
      assert.deepEqual(path.at(-1), [b.x, b.z]);
    }
  });

  test(`${id}: 全エッジが軸平行・全障害物と非交差・余白込みの通路幅 >= 0.9m`, () => {
    assert.ok(nodes.length > 1 && edges.length > 0);
    assert.ok(margin * 2 >= 0.9);
    for (const [i, j] of edges) {
      const a = nodes[i];
      const b = nodes[j];
      assert.ok(a[0] === b[0] || a[1] === b[1]);
      assert.notDeepEqual(a, b);
      for (const r of [...rects, ...padded]) {
        assert.equal(segIntersectsRect(...a, ...b, r), false,
          `${id}: ${a} -> ${b} overlaps ${r.id}`);
      }
    }
    for (const [x, z] of nodes) {
      assert.ok(x >= layout.WALL.left + margin - 1e-9 && x <= layout.WALL.right - margin + 1e-9);
      assert.ok(z >= layout.WALL.back + margin - 1e-9 && z <= layout.WALL.front - margin + 1e-9);
    }
  });

  test(`${id}: グラフ全体が連結している（Dijkstra の到達不能による直線化を防ぐ）`, () => {
    const seen = new Set([0]);
    const queue = [0];
    for (let head = 0; head < queue.length; head++) {
      for (const j of adj[queue[head]]) {
        if (!seen.has(j)) { seen.add(j); queue.push(j); }
      }
    }
    assert.equal(seen.size, nodes.length);
    assert.equal(new Set(nodes.map((p) => p.join(","))).size, nodes.length);
    assert.equal(new Set(edges.map(([i, j]) => [Math.min(i, j), Math.max(i, j)].join(","))).size, edges.length);
  });

  test(`${id}: 0.5mグリッドを保持し、直線上の中間ノードだけ間引く`, () => {
    for (let i = 0; i < nodes.length; i++) {
      const [x, z] = nodes[i];
      for (const n of [(layout.WALL.right - margin - x) / 0.5, (layout.WALL.front - margin - z) / 0.5]) {
        assert.ok(Math.abs(n - Math.round(n)) < 1e-8);
      }
      if (adj[i].length !== 2) continue;
      const [a, b] = adj[i].map((j) => nodes[j]);
      assert.ok(a[0] !== b[0] && a[1] !== b[1], `直線上の次数2ノードが残っている: ${nodes[i]}`);
    }
    assert.ok(edges.some(([i, j]) => Math.hypot(nodes[i][0] - nodes[j][0], nodes[i][1] - nodes[j][1]) > 0.5),
      "直線のグリッド辺が縮約されている");
  });

  test(`${id}: 入口から全種類の席へ到達し、往復経路は目的の部屋以外を横切らない`, () => {
    const targets = [
      ...layout.anchors.desk.map((a) => [a, null]),
      ...layout.anchors.queue.map((a) => [a, null]),
      ...layout.anchors.external.map((a) => [a, null]),
      ...layout.idleSpots.map((a) => [a, null]),
      ...layout.restSpots.map((a) => [a, a.area === "sofa" ? "stage" : a.area]),
      ...Object.entries(layout.anchors.meeting.byRoom).flatMap(([room, seats]) => seats.map((a) => [a, room])),
      ...Object.entries(layout.anchors.chibi).flatMap(([room, seats]) => seats.map((a) => [a, room])),
    ];
    const entrance = [-8.3, layout.WALL.front - 0.85];
    for (const [anchor, own] of targets) {
      const target = [anchor.x, anchor.z];
      for (const [from, to] of [[entrance, target], [target, entrance]]) {
        const path = routePath(from, to, layout.walkGraph);
        assert.ok(path.length >= 2);
        assert.deepEqual(path[0], from);
        const last = path[path.length - 1];
        assert.ok(Math.hypot(last[0] - to[0], last[1] - to[1]) < 0.15);
        for (let i = 0; i + 1 < path.length; i++) {
          const a = path[i];
          const b = path[i + 1];
          assert.ok([...a, ...b].every(Number.isFinite));
          const approach = i === 0 || i === path.length - 2;
          if (approach) assert.ok(Math.hypot(a[0] - b[0], a[1] - b[1]) < 6.5);
          for (const r of rects) {
            if (approach && r.id === own) continue;
            assert.equal(segIntersectsRect(...a, ...b, r), false,
              `${id}: ${from} -> ${to}: ${a} -> ${b} crosses ${r.id}`);
          }
        }
      }
    }
  });

  test(`${id}: 掃除ロボの閉路とボスの見回り路も障害物と交差しない`, () => {
    const cleaner = layout.cleanerRoute;
    assert.deepEqual(cleaner[0], cleaner[cleaner.length - 1]);
    for (const [route, start, end] of [[cleaner, 1, cleaner.length], [layout.bossWalk, 2, layout.bossWalk.length - 1]]) {
      for (let i = start; i < end; i++) {
        for (const r of rects) assert.equal(segIntersectsRect(...route[i - 1], ...route[i], r), false, r.id);
      }
    }
  });

  test(`${id}: 待機スポットは全障害物の外、休憩は自エリア以外を避ける`, () => {
    for (const spot of [...layout.idleSpots, ...layout.restSpots]) {
      assert.ok(spot.x > layout.WALL.left && spot.x < layout.WALL.right);
      assert.ok(spot.z > layout.WALL.back && spot.z < layout.WALL.front);
      const own = spot.area === "sofa" ? "stage" : spot.area;
      for (const r of rects.filter((r) => r.id !== own)) {
        assert.ok(!(spot.x > r.x1 && spot.x < r.x2 && spot.z > r.z1 && spot.z < r.z2), r.id);
      }
    }
  });
}
