// R58: 歩行ナビの機械検査。「経路が机・部屋を横切らない」をここで強制する
// （すり抜けはユーザーが実際に目撃した回帰＝再発したらこのテストが落ちる）。
//   node --test ui/core/nav.test.js
import assert from "node:assert/strict";
import { test } from "node:test";
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
    if (s.area === "lounge" || s.area === "sofa") {
      // ソファ席は自エリアのゾーン矩形の中に載るのが意図（最終アプローチ扱い）。
      // ただし「別の」障害物には入っていないこと
      const own = s.area === "lounge" ? "lounge" : "stage";
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
