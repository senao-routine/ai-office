// R99 Phase 3（C1・C2）: 俯瞰オフィスの通路グラフと歩行（ui/core/pxwalk.js）の機械検査。
// 「扉と全ての枠が繋がっている」「経路が家具の足元を横切らない（座りに行く自分の家具だけ）」「届かないは null」
// 「歩き方の掟（R98 の ui-2d.md から移植）」を 1 つずつ固定する。
// 門が本当に効くことは、写しのツリーで 1 か所ずつ壊して赤になることを確かめてある（R99 Phase 3 第 2 波・15 通り）:
//   障害物を渡さない／会議室の開口を塞ぐ／baseline を無視する／巻き戻しを目的地にしない／引き直しを出発点から／
//   再生でも帳簿を更新する／整数に丸めない／届かないときに [from, from] をそのまま返す／古い帳簿でも入場させる／
//   退場を目的地から始める／固定時刻でも歩かせる／障害物を膨らませない／solid を渡さない／
//   呼び側の {} を見た帳簿として扱う／Map の帳簿を読まない
// 第 2 波の統合で足した門（同じく 1 か所ずつ壊して赤を確認）: 歩行の刻み（12fps で描画 1 回＝コマ +1）・
//   距離の丸め（浮動小数で 3k が 2.999…）・空の帳簿の古さ・最初の世界が空・途中の点を畳む・未来の記録・
//   drawn の並び・退場で届かない・引き直しで entering を下ろす・who の更新・入力の丸め・actor.key で追う
// 統合の修正ラウンドで足した門（同じく 1 か所ずつ壊して赤を確認）: 引き直しの刻み（引き直し・退場の歩き出しで
//   位相の持ち越し d0 を捨てる／着いた刻みの端数を捨てる／posAt が d0 を無視する）・扉の横の中央と DOOR_XY の字面
import test from "node:test";
import assert from "node:assert/strict";
import { segIntersectsRect, routePath } from "./nav.js";
import { DOOR, ERRAND_SPOTS, FURNITURE, GLASS_OPENING, LIFE_SPOTS, ROBOT_FOOT, ROOM, SLOTS, footRects, roomLayout } from "./pxroom.js";
import {
  DOOR_XY, FOOT_CENTER, LANES, OBSTACLES, RISE_SEC, ROOM_FPS, WALK_SPEED, WALK_STALE_SEC, WALK_STRIDE, WALLS,
  footOf, planPath, posAt, roomFps, roomGraph, stepWalkers, topLeftOf,
} from "./pxwalk.js";
import { SETTLE_SEC, TOP_CELLS, topPose } from "./pxtop.js";
import { buildWorld } from "./world.js";

const G = roomGraph();
/** 枝を持つ用事の行き先（pxwalk.spurs の ERRAND_VIA）。ここに無い行き先は枝なしで届く（下の門が見る） */
const ERRAND_SPURS = new Set(["shelf"]);
/** 全ての枠（区分#番号 → 左上と at）＋扉 */
const SPOTS = [
  ...Object.entries(SLOTS).flatMap(([zone, list]) => list.map((s, i) => ({ key: `${zone}#${i}`, xy: [s.x, s.y], at: s.at }))),
  { key: "door", xy: [...DOOR_XY], at: [] },
  // R99-P4-L2: 散歩先（ui/core/life.js）も全部の枠と扉から届き、足元が家具と交差しない。散歩先は通路グラフの枠
  //（roomGraph().spots＝枝を持つ点）ではなく、planPath が最寄りの通路へ自動でつなぐ点＝グラフの門（GRAPH_SPOTS）には入れない
  ...LIFE_SPOTS.map((s, i) => ({ key: `life#${i}`, xy: [s.x, s.y], at: [], life: true })),
  // R110-S5: 用事の行き先（ui/core/errand.js）も同じ。本棚の前（shelf）だけは真っ直ぐ通路へ出られない＝枝（errand#shelf）を
  // 持つグラフの点。ほかは planPath が最寄りの通路へつなぐ点
  ...ERRAND_SPOTS.map((s) => ({ key: `errand#${s.name}`, xy: [s.x, s.y], at: [], life: !ERRAND_SPURS.has(s.name) })),
];
/** 通路グラフに枝を持つ枠（SLOTS の全区分＋扉）＝グラフの索引の門が見る点 */
const GRAPH_SPOTS = SPOTS.filter((s) => !s.life);
const baseId = (r) => r.id.split("#")[0];
/** 点が障害物の内側（辺の上は外＝segIntersectsRect と同じ） */
const inside = (p, r) => p[0] > r.x1 && p[0] < r.x2 && p[1] > r.z1 && p[1] < r.z2;
const lengthOf = (path) => path.slice(1).reduce((s, p, k) => s + Math.hypot(p[0] - path[k][0], p[1] - path[k][1]), 0);
/** 歩行のコマ名 → 添字（walkS2 → 2） */
const walkIndex = (cell) => Number(cell.slice(-1));

/**
 * 経路（左上の折れ線）が家具の足元・壁を横切っていないか。例外は「出発の枠の at の家具を出るまで」と
 * 「到着の枠の at の家具に入ってから」だけ（座る机と椅子・ソファの座面・会議卓・カウンター）。
 * 戻り値は問題の文字列の配列（空＝問題なし）。
 */
function pathProblems(path, from, to) {
  const pts = path.map(footOf);
  const own = (spot) => OBSTACLES.filter((r) => spot.at.includes(baseId(r)));
  const inOwn = (p, spot) => own(spot).some((r) => inside(p, r));
  // 出発の家具を出た最初の点・到着の家具に入る直前の点
  let exit = 0;
  while (exit < pts.length && inOwn(pts[exit], from)) exit += 1;
  let entry = pts.length - 1;
  while (entry >= 0 && inOwn(pts[entry], to)) entry -= 1;
  const bad = [];
  for (let k = 0; k + 1 < pts.length; k += 1) {
    const [a, b] = [pts[k], pts[k + 1]];
    for (const r of OBSTACLES) {
      if (!segIntersectsRect(a[0], a[1], b[0], b[1], r)) continue;
      const okHead = k < exit && from.at.includes(baseId(r));
      const okTail = k >= entry && to.at.includes(baseId(r));
      if (!okHead && !okTail) bad.push(`区間 ${k} (${a})→(${b}) が ${r.id} を横切る`);
    }
  }
  return bad;
}

// ── 座標の変換 ─────────────────────────────────────────────────────────
test("pxwalk: 足元の中心は ROBOT_FOOT の中心（整数）・左上との往復で戻る", () => {
  assert.deepEqual([...FOOT_CENTER], [ROBOT_FOOT[0] + ROBOT_FOOT[2] / 2, ROBOT_FOOT[1] + ROBOT_FOOT[3] / 2]);
  assert.ok(FOOT_CENTER.every(Number.isInteger), `足元の中心 ${FOOT_CENTER} が整数でない＝経路の点が端数になる`);
  for (const s of SPOTS) {
    assert.deepEqual(topLeftOf(footOf(s.xy)), s.xy);
    assert.deepEqual(footOf({ x: s.xy[0], y: s.xy[1] }), footOf(s.xy));
  }
  assert.equal(footOf(null), null);
  assert.equal(topLeftOf([Number.NaN, 1]), null);
});

test("pxwalk: 扉に立つ人は扉のマットの上（横は扉の中央・足元は壁に掛からない・受付の枠と重ならない）", () => {
  const [fx, fy] = footOf(DOOR_XY);
  assert.ok(fx - ROBOT_FOOT[2] / 2 >= DOOR.x && fx + ROBOT_FOOT[2] / 2 <= DOOR.x + DOOR.w, `扉の足元 x ${fx} が扉の外`);
  // 扉の横の中央（DOOR_FOOT の JSDoc の約束）と、統合の報告の API に書いた字面（DOOR 150,156,18×4 → 足元 [159, 156]
  // → 左上 [153, 141]）。1px ずれても「扉の中」だけの検査は緑のまま＝ここで字面を固定する
  assert.equal(fx, DOOR.x + DOOR.w / 2, `扉に立つ人の足元 x ${fx} が扉の横の中央でない`);
  assert.deepEqual([...DOOR_XY], [153, 141]);
  assert.ok(Object.isFrozen(DOOR_XY));
  assert.equal(fy, DOOR.y, "扉に立つ人の足元が扉のマットの行に無い");
  assert.equal(DOOR.y + DOOR.h, ROOM.h, "扉が手前の壁の帯に嵌まっていない");
  for (const r of OBSTACLES) assert.ok(!inside([fx, fy], r), `扉に立つ人が ${r.id} にめり込む`);
  const foot = [fx - 3, fy - 1, 6, 2];
  for (const s of SLOTS.queue) {
    const q = [s.x + ROBOT_FOOT[0], s.y + ROBOT_FOOT[1], ROBOT_FOOT[2], ROBOT_FOOT[3]];
    const over = foot[0] < q[0] + q[2] && q[0] < foot[0] + foot[2] && foot[1] < q[1] + q[3] && q[1] < foot[1] + foot[3];
    assert.ok(!over, `扉に立つ人が受付の枠 (${s.x},${s.y}) と重なる`);
  }
});

// ── 通路グラフ ─────────────────────────────────────────────────────────
test("pxwalk グラフ: ノードは整数で床の中・辺は x か y に平行（歩く向きが 4 方向に決まる）・重なる辺が無い", () => {
  assert.ok(G.nodes.length > 0 && G.edges.length > 0);
  for (const [x, y] of G.nodes) {
    assert.ok(Number.isInteger(x) && Number.isInteger(y), `ノード (${x},${y}) が整数でない`);
    assert.ok(x > 2 && x < ROOM.w - 3 && y >= 16 && y < ROOM.h - 3, `ノード (${x},${y}) が床の外`);
  }
  const seen = new Set();
  for (const [i, j] of G.edges) {
    const [a, b] = [G.nodes[i], G.nodes[j]];
    assert.ok(a[0] === b[0] || a[1] === b[1], `辺 (${a})→(${b}) が斜め`);
    assert.notEqual(i, j);
    const k = i < j ? `${i}-${j}` : `${j}-${i}`;
    assert.ok(!seen.has(k), `辺 ${k} が 2 本ある`);
    seen.add(k);
  }
  // 同じ直線の上で辺が重なると nav.routePath の「最寄りの辺」が一意に決まらず、行き過ぎて戻る経路になる
  for (let p = 0; p < G.edges.length; p += 1) {
    for (let q = p + 1; q < G.edges.length; q += 1) {
      const [a, b] = G.edges[p].map((n) => G.nodes[n]);
      const [c, d] = G.edges[q].map((n) => G.nodes[n]);
      const hor = a[1] === b[1] && c[1] === d[1] && a[1] === c[1];
      const ver = a[0] === b[0] && c[0] === d[0] && a[0] === c[0];
      if (!hor && !ver) continue;
      const ax = hor ? 0 : 1;
      const lo = Math.max(Math.min(a[ax], b[ax]), Math.min(c[ax], d[ax]));
      const hi = Math.min(Math.max(a[ax], b[ax]), Math.max(c[ax], d[ax]));
      assert.ok(hi <= lo, `辺 (${a})→(${b}) と (${c})→(${d}) が重なっている`);
    }
  }
});

test("pxwalk グラフ: 障害物は家具の足元（foot＋solid）と壁を、ロボの足の半幅ぶん膨らませたもの", () => {
  const want = [
    ...FURNITURE.flatMap((f) => footRects(f)),
    ...WALLS,
  ].map(([x, y, w, h]) => [x - ROBOT_FOOT[2] / 2, y - ROBOT_FOOT[3] / 2, x + w + ROBOT_FOOT[2] / 2, y + h + ROBOT_FOOT[3] / 2]);
  assert.deepEqual(OBSTACLES.map((r) => [r.x1, r.z1, r.x2, r.z2]), want);
  assert.equal(G.obstacles, OBSTACLES, "経路探索に渡す障害物が OBSTACLES と別物");
  assert.ok(OBSTACLES.some((r) => r.id === "glassWall#1"), "会議室の開口の右の框（solid）が障害物に入っていない");
  const ids = OBSTACLES.map((r) => r.id);
  assert.equal(new Set(ids).size, ids.length, "障害物の id が重複");
});

test("pxwalk グラフ: 辺が家具・壁を横切るのは、自分の枠の at の家具を越える枝だけ（座りに行く）", () => {
  const atOf = new Map(Object.values(G.spots).map((s) => [s.node, s.at]));
  let n = 0;
  for (const [i, j] of G.edges) {
    const [a, b] = [G.nodes[i], G.nodes[j]];
    for (const r of OBSTACLES) {
      if (!segIntersectsRect(a[0], a[1], b[0], b[1], r)) continue;
      const ok = [atOf.get(i), atOf.get(j)].some((at) => at && at.includes(baseId(r)));
      assert.ok(ok, `辺 (${a})→(${b}) が ${r.id} を横切る（枠の at にも無い）`);
      n += 1;
    }
  }
  assert.ok(n >= SLOTS.desk.length, `座りに行く枝が ${n} 本しか家具を越えない＝枝が机に届いていない`);
});

test("pxwalk グラフ: 通路（LANES）の上は障害物に掛からない（格子の交差点も）", () => {
  for (const x of LANES.x) for (const y of LANES.y) {
    for (const r of OBSTACLES) assert.ok(!inside([x, y], r), `通路の交差点 (${x},${y}) が ${r.id} の中`);
  }
});

test("pxwalk グラフ: 扉と全ての枠（SLOTS の全区分）が 1 つの連結成分", () => {
  const spots = Object.entries(G.spots);
  assert.equal(spots.length, GRAPH_SPOTS.length, `枠の索引 ${spots.length} 件 ≠ SLOTS の全区分＋扉 ${GRAPH_SPOTS.length}（枠を足したら枝も足す）`);
  for (const s of GRAPH_SPOTS) {
    assert.ok(Object.hasOwn(G.spots, s.key), `${s.key} がグラフに無い`);
    assert.deepEqual([...G.spots[s.key].foot], footOf(s.xy), `${s.key} の足元がグラフと枠でずれている`);
  }
  const adj = G.nodes.map(() => []);
  for (const [i, j] of G.edges) { adj[i].push(j); adj[j].push(i); }
  const reach = new Set([G.spots.door.node]);
  const queue = [G.spots.door.node];
  while (queue.length) for (const v of adj[queue.shift()]) if (!reach.has(v)) { reach.add(v); queue.push(v); }
  for (const [key, s] of spots) assert.ok(reach.has(s.node), `${key} が扉と繋がっていない`);
  assert.equal(reach.size, G.nodes.length, "扉から届かないノードがある（離れ小島）");
});

// ── planPath ──────────────────────────────────────────────────────────
test("pxwalk planPath: 全ての枠の組（扉を含む）で届き、x か y に平行な整数の折れ線で、足元と交差しない", () => {
  let pairs = 0;
  for (const from of SPOTS) {
    for (const to of SPOTS) {
      if (from === to) continue;
      const path = planPath(from.xy, to.xy);
      const label = `${from.key} → ${to.key}`;
      assert.ok(path, `${label} が届かない`);
      assert.deepEqual(path[0], from.xy, `${label}: 先頭が出発点でない`);
      assert.deepEqual(path[path.length - 1], to.xy, `${label}: 最後が目的地でない`);
      assert.ok(path.length >= 2);
      for (let k = 0; k < path.length; k += 1) {
        assert.ok(path[k].every(Number.isInteger), `${label}: 点 ${path[k]} が整数でない`);
        if (k) {
          const [a, b] = [path[k - 1], path[k]];
          assert.ok((a[0] === b[0]) !== (a[1] === b[1]), `${label}: 区間 (${a})→(${b}) が斜めか長さ 0`);
        }
      }
      assert.deepEqual(pathProblems(path, from, to), [], label);
      pairs += 1;
    }
  }
  assert.equal(pairs, SPOTS.length * (SPOTS.length - 1));
});

test("pxwalk planPath: 会議室へは開口からだけ入る（框を越える区間は開口の中）", () => {
  const [ox, oy, ow, oh] = GLASS_OPENING;
  for (let i = 0; i < SLOTS.meeting.length; i += 1) {
    const s = SLOTS.meeting[i];
    const path = planPath(DOOR_XY, [s.x, s.y]).map(footOf);
    const cross = path.slice(1).map((b, k) => [path[k], b])
      .filter(([a, b]) => Math.min(a[1], b[1]) < oy && Math.max(a[1], b[1]) >= oy + oh);
    assert.equal(cross.length, 1, `会議室 #${i}: 框の線を ${cross.length} 回越える`);
    const [[ax, ay], [bx]] = cross[0];
    assert.equal(ax, bx, "框を斜めに越えている");
    assert.ok(ax - ROBOT_FOOT[2] / 2 >= ox && ax + ROBOT_FOOT[2] / 2 <= ox + ow,
      `会議室 #${i}: 框を x ${ax}（足 ${ax - 3}..${ax + 2}）で越える＝開口 ${ox}..${ox + ow - 1} の外`);
    assert.ok(ay >= oy, "会議室の外から入っていない");
  }
});

test("pxwalk planPath: ソファは枠の真下（手前）の奥の通路から直に入る（植物をずらさなくても前が空いている）", () => {
  for (let i = 0; i < SLOTS.lounge.length; i += 1) {
    const s = SLOTS.lounge[i];
    const path = planPath([s.x, s.y], SPOTS.find((p) => p.key === "desk#4").xy);
    assert.equal(path[1][0], s.x, `ソファ #${i}: 真下へ出ていない`);
    assert.equal(footOf(path[1])[1], LANES.y[0], `ソファ #${i}: 最初の角が奥の通路でない`);
    assert.ok(path[1][1] > s.y, `ソファ #${i}: 手前へ出ていない`);
  }
});

test("pxwalk planPath: 届かない目的地は null（nav.routePath の [from, from] を黙って返さない）", () => {
  const from = SPOTS[0].xy;
  const cases = [[300, 300], [-40, 60], [90, 170], [ROOM.w, 60]];
  for (const to of cases) {
    // 前提: routePath 自体は届かないとき [from, from] を返す＝そのまま歩かせると永久に止まる
    const raw = routePath(footOf(from), footOf(to), G);
    assert.deepEqual(raw, [footOf(from), footOf(from)], `前提: (${to}) に routePath が届いてしまう＝検査になっていない`);
    assert.equal(planPath(from, to), null, `(${to}) に届いたことになっている`);
  }
  assert.equal(planPath(null, from), null);
  assert.equal(planPath(from, [1, Number.NaN]), null);
});

test("pxwalk planPath: 同じ点は 1 点の折れ線・{x, y} でも [x, y] でも同じ・決定論", () => {
  assert.deepEqual(planPath(DOOR_XY, DOOR_XY), [[...DOOR_XY]]);
  const s = SLOTS.desk[8];
  assert.deepEqual(planPath({ x: DOOR_XY[0], y: DOOR_XY[1] }, s), planPath(DOOR_XY, [s.x, s.y]));
  const a = SPOTS.map((f) => SPOTS.map((t) => planPath(f.xy, t.xy)));
  const b = SPOTS.map((f) => SPOTS.map((t) => planPath(f.xy, t.xy)));
  assert.equal(JSON.stringify(a), JSON.stringify(b));
});

test("pxwalk planPath: 経路の途中（歩いている最中）からも引き直せる", () => {
  const path = planPath(DOOR_XY, [SLOTS.desk[0].x, SLOTS.desk[0].y]);
  const total = lengthOf(path);
  for (let d = 0; d <= total; d += 7) {
    const p = posAt(path, 0, d / WALK_SPEED);
    for (const to of SPOTS) {
      const again = planPath([p.x, p.y], to.xy);
      assert.ok(again, `途中 (${p.x},${p.y}) から ${to.key} へ届かない`);
      assert.deepEqual(again[0], [p.x, p.y], "引き直した経路が今の位置から始まらない");
    }
  }
});

// ── posAt ─────────────────────────────────────────────────────────────
test("pxwalk posAt: 距離で弧長パラメータ化（区間の長さに比例）・整数・向きは区間の主軸・位相は距離", () => {
  const path = [[10, 10], [10, 50], [30, 50], [30, 20], [5, 20]];    // S 40・E 20・N 30・W 25
  // 時刻は秒なので、距離 d ぶん進んだ時刻の積は浮動小数の誤差を持つ＝位相は近さで比べる
  const at = (d) => {
    const p = posAt(path, 2, 2 + d / WALK_SPEED);
    return { ...p, phase: Math.round(p.phase * 1e6) / 1e6 };
  };
  const ph = (d) => Math.round(((d % WALK_STRIDE) / WALK_STRIDE) * 1e6) / 1e6;
  assert.deepEqual(at(0), { x: 10, y: 10, dir: "S", phase: 0, done: false });
  assert.deepEqual(at(WALK_STRIDE / 2), { x: 10, y: 10 + WALK_STRIDE / 2, dir: "S", phase: 0.5, done: false });
  assert.deepEqual(at(44), { x: 14, y: 50, dir: "E", phase: ph(44), done: false });
  assert.deepEqual(at(70), { x: 30, y: 40, dir: "N", phase: ph(70), done: false });
  assert.deepEqual(at(100), { x: 20, y: 20, dir: "W", phase: ph(100), done: false });
  assert.deepEqual(at(115 + 1e-6), { x: 5, y: 20, dir: "W", phase: 0, done: true });
  // 端数の時刻でも整数・位相は 0..1
  for (let k = 0; k < 400; k += 1) {
    const p = posAt(path, 2, 2 + k * 0.0137);
    assert.ok(Number.isInteger(p.x) && Number.isInteger(p.y), `(${p.x},${p.y}) が整数でない`);
    assert.ok(p.phase >= 0 && p.phase < 1);
    assert.ok(["S", "N", "E", "W"].includes(p.dir));
  }
});

test("pxwalk posAt: d0（引き直す前に進んでいた距離）は位相だけを進める（位置・向き・done は同じ・数でなければ 0）", () => {
  const path = [[10, 10], [10, 50], [30, 50]];      // S 40・E 20
  const ph = (d) => Math.round(((((d % WALK_STRIDE) + WALK_STRIDE) % WALK_STRIDE) / WALK_STRIDE) * 1e6) / 1e6;
  for (const d of [0, 3, 7.5, 44, 57]) {
    const t = 2 + d / WALK_SPEED;
    const plain = posAt(path, 2, t);
    for (const d0 of [0, 1.5, 3, 6, 9, 11, 12, 27, -3]) {
      const p = posAt(path, 2, t, WALK_SPEED, d0);
      assert.deepEqual([p.x, p.y, p.dir, p.done], [plain.x, plain.y, plain.dir, false], `d=${d} d0=${d0}`);
      assert.equal(Math.round(p.phase * 1e6) / 1e6, ph(d + d0), `d=${d} d0=${d0} の位相`);
      assert.ok(p.phase >= 0 && p.phase < 1, `d=${d} d0=${d0} の位相 ${p.phase} が 0..1 の外`);
    }
    for (const bad of [Number.NaN, Infinity, "3", null]) assert.deepEqual(posAt(path, 2, t, WALK_SPEED, bad), plain, String(bad));
  }
  // 12fps の刻みの上では、d0 ＝ 3px（歩行の 1 コマ）で添字がちょうど +1（浮動小数の誤差で 1 つ手前に落ちない）
  for (let k = 0; k < 20; k += 1) {
    const t = (360 + k) / ROOM_FPS.walk;
    const t0 = 360 / ROOM_FPS.walk;
    const a = walkIndex(topPose({}, t, { walk: posAt(path, t0, t) }).cell);
    const b = walkIndex(topPose({}, t, { walk: posAt(path, t0, t, WALK_SPEED, WALK_STRIDE / 4) }).cell);
    assert.equal(b, (a + 1) % 4, `k=${k}`);
  }
  // d0 も進んだ距離と同じく 1e-6px に丸める（呼び側が浮動小数で作った 3 − ε で 1 コマ手前に落ちない）
  assert.equal(posAt(path, 2, 2, WALK_SPEED, WALK_STRIDE / 4 - 1e-9).phase, 0.25);
  assert.equal(posAt(path, 2, 2, WALK_SPEED, WALK_STRIDE - 1e-9).phase, 0);
  // 着いたら d0 に関係なく 0
  assert.deepEqual(posAt(path, 2, 2 + 61 / WALK_SPEED, WALK_SPEED, 5), { x: 30, y: 50, dir: "E", phase: 0, done: true });
});

test("pxwalk posAt: 巻き戻し（t < t0）・数でない時刻・速さ 0 は目的地（done）", () => {
  const path = planPath(DOOR_XY, [SLOTS.lounge[0].x, SLOTS.lounge[0].y]);
  const end = path[path.length - 1];
  for (const [t0, t, speed] of [[10, 9.99, WALK_SPEED], [10, -1e9, WALK_SPEED], [10, Number.NaN, WALK_SPEED],
    [Number.NaN, 10, WALK_SPEED], [10, 11, 0], [10, 11, -5]]) {
    const p = posAt(path, t0, t, speed);
    assert.deepEqual([p.x, p.y, p.done, p.phase], [...end, true, 0], `t0=${t0} t=${t} speed=${speed}`);
  }
  const one = posAt([[4, 5]], 0, 3);
  assert.deepEqual(one, { x: 4, y: 5, dir: "S", phase: 0, done: true });
  assert.equal(posAt([], 0, 1), null);
  assert.equal(posAt(null, 0, 1), null);
});

test("pxwalk posAt: 所要時間 ＝ 経路の長さ ÷ 24px/s（着いた瞬間に done）・扉 → 机 0 は 264px＝11 秒", () => {
  const path = planPath(DOOR_XY, [SLOTS.desk[0].x, SLOTS.desk[0].y]);
  const sec = lengthOf(path) / WALK_SPEED;
  // 統括の裁定（R110-S4・本人「歩く速度が速すぎてパッと着く」: 歩く速さ 36→24・1 周 4 コマ×2px・描画の刻み 6／歩く人が居る間 12
  // ＝歩調はそのまま歩幅を縮める）。変えるなら刻みの門も同時に
  assert.equal(WALK_SPEED, 24);
  assert.equal(WALK_STRIDE, 8);
  assert.deepEqual({ ...ROOM_FPS }, { idle: 6, walk: 12 });
  assert.equal(WALK_SPEED / ROOM_FPS.walk, WALK_STRIDE / 4, "不変条件: 12fps の描画 1 回にコマ +1");
  assert.equal(lengthOf(path), 264, "前提: 扉 → 机 0 の経路の長さ（間取りが変わったら所要の説明も直す）");
  assert.equal(sec, 11);
  assert.equal(posAt(path, 5, 5 + sec * 0.999).done, false);
  assert.equal(posAt(path, 5, 5 + sec + 1e-9).done, true);
});

// ── stepWalkers ───────────────────────────────────────────────────────
const who = (session, slot, extra = {}) => ({ id: session, session, projectKey: `p-${session}`, x: slot.x, y: slot.y, ...extra });
const D = SLOTS.desk;
const live = { live: true };
const byKey = (res) => Object.fromEntries(res.drawn.map((d) => [d.key, d]));

test("pxwalk stepWalkers: 初めて見えた人は歩かせない（帳簿が無い初回・baseline: true の間）", () => {
  const actors = [who("s0", D[0]), who("s1", D[4]), who("s2", SLOTS.queue[2])];
  const r1 = stepWalkers(null, actors, 10, live);
  assert.deepEqual(r1.stats, { walking: 0, entering: 0, leaving: 0, unreachable: 0 });
  for (const d of r1.drawn) {
    assert.equal(d.walk, null, `${d.key} が初回に歩いている`);
    assert.deepEqual([d.x, d.y], [actors.find((a) => a.session === d.key).x, actors.find((a) => a.session === d.key).y]);
  }
  assert.deepEqual(Object.keys(r1.walkers).sort(), ["s0", "s1", "s2"]);
  // 帳簿はあるが baseline の間（明示）: 新しく見えた s3 も歩かない
  const r2 = stepWalkers(r1.walkers, [...actors, who("s3", D[8])], 10.2, { live: true, baseline: true });
  assert.equal(byKey(r2).s3.walk, null);
  assert.deepEqual([byKey(r2).s3.x, byKey(r2).s3.y], [D[8].x, D[8].y]);
  assert.equal(r2.stats.entering, 0);
});

test("pxwalk stepWalkers: 呼び側が作った帳簿（{}・Map）は初回扱い・空になった帳簿の次に来た人は扉から入る", () => {
  const actors = [who("s0", D[0]), who("s1", D[4])];
  for (const start of [{}, new Map(), Object.create(null)]) {
    const r = stepWalkers(start, actors, 10, live);
    assert.equal(r.stats.walking, 0, `${start.constructor?.name ?? "null-proto"} で始めたら開いた瞬間に全員が歩いた`);
  }
  // Map の帳簿も読める（前回の記録を落とさない）
  const r1 = stepWalkers(null, actors, 10, live);
  const asMap = new Map(Object.entries(r1.walkers));
  const r2 = stepWalkers(asMap, [who("s0", D[8]), who("s1", D[4])], 10.1, { live: true, baseline: false });
  // 席に立っていた人は、その場で立ち上がってから歩き出す（扉から入り直さない）
  assert.ok(byKey(r2).s0.lift === 1 && !byKey(r2).s0.entering && r2.stats.walking === 1,
    "Map の帳簿の記録を読めていない（扉から入り直した）");
  assert.deepEqual([byKey(r2).s0.x, byKey(r2).s0.y], [D[0].x, D[0].y], "Map の帳簿の記録を読めていない（席から歩き出さない）");
  // 全員が出ていって空になった帳簿のあと、次に来た人は扉から歩いて入る（1 人で使う人の再起動）
  let book = stepWalkers(null, [who("s0", D[0])], 10, live).walkers;
  let t = 10.1;
  let out = stepWalkers(book, [], t, live);
  assert.equal(out.stats.leaving, 1, "前提: 最後の 1 人が扉へ歩かない");
  while (out.stats.walking > 0) { book = out.walkers; t += 0.25; out = stepWalkers(book, [], t, live); }
  assert.deepEqual(Object.keys(out.walkers), [], "前提: 帳簿が空になっていない");
  const next = stepWalkers(out.walkers, [who("s9", D[3])], t + 0.2, live);
  assert.equal(next.stats.entering, 1, "空になった部屋に来た人が扉から入らない");
});

test("pxwalk stepWalkers: 最初に描いた世界が空（取得の前・一時的な空）でも、次に届いた全員が扉から入らない", () => {
  // R98 の「開いた瞬間に全員が扉から入る」と同じ形: 空の世界を最初に見て、0.16 秒後に 2 人が届く
  const empty = stepWalkers(null, [], 10, live);
  assert.deepEqual(Object.keys(empty.walkers), []);
  const again = stepWalkers(empty.walkers, [], 10.1, live);   // 空が続いても同じ
  const r = stepWalkers(again.walkers, [who("s0", D[0]), who("s1", D[4])], 10.26, live);
  assert.equal(r.stats.entering, 0, "取得の前の空の世界を「最初に見た世界」にした＝全員が扉から入った");
  assert.deepEqual(r.drawn.map((d) => [d.key, d.x, d.y, d.walk]), [["s0", D[0].x, D[0].y, null], ["s1", D[4].x, D[4].y, null]]);
  // 一度人を見た帳簿の後は普通に扉から入る
  const r2 = stepWalkers(r.walkers, [who("s0", D[0]), who("s1", D[4]), who("s2", D[8])], 10.4, live);
  assert.equal(r2.stats.entering, 1);
  // 呼び側が baseline:false を明示すれば空の後でも入る（明示が既定に勝つ）
  const forced = stepWalkers(empty.walkers, [who("s0", D[0])], 10.2, { live: true, baseline: false });
  assert.equal(forced.stats.entering, 1);
});

test("pxwalk stepWalkers: 空の帳簿も古くなる（空の部屋のまま長く止まって戻った回に、遅れて扉から入らない）", () => {
  // 一度人を見た（met）空の帳簿: 600 秒止まって戻った回に現れた人は扉から入らない
  let book = stepWalkers(null, [who("s0", D[0])], 10, live).walkers;
  let t = 10.1;
  let out = stepWalkers(book, [], t, live);
  while (out.stats.walking > 0) { book = out.walkers; t += 0.25; out = stepWalkers(book, [], t, live); }
  assert.deepEqual(Object.keys(out.walkers), []);
  const late = stepWalkers(out.walkers, [who("s9", D[3])], t + 600, live);
  assert.equal(late.stats.entering, 0, "空の帳簿が古くならない＝止まっていた間の入場を遅れて演じた");
  const fresh = stepWalkers(out.walkers, [who("s9", D[3])], t + WALK_STALE_SEC - 0.5, live);
  assert.equal(fresh.stats.entering, 1, "新しい空の帳簿の後に来た人が扉から入らない");
  // 未来（巻き戻し）の空の帳簿も古い扱い
  assert.equal(stepWalkers(out.walkers, [who("s9", D[3])], t - 1, live).stats.entering, 0);
});

test("pxwalk stepWalkers: baseline の後に現れた人は扉から歩いて入り、所要時間で席に着く", () => {
  const r1 = stepWalkers(null, [who("s0", D[0])], 10, live);
  const actors = [who("s0", D[0]), who("s1", D[5])];
  const r2 = stepWalkers(r1.walkers, actors, 10.25, live);
  const s1 = byKey(r2).s1;
  assert.deepEqual([s1.x, s1.y], [...DOOR_XY], "入ってくる人が扉から始まらない");
  assert.ok(s1.walk && s1.entering, "扉から歩いていない");
  assert.equal(s1.walk.dir, "N", "扉から奥へ歩き出していない");
  assert.deepEqual(r2.stats, { walking: 1, entering: 1, leaving: 0, unreachable: 0 });
  assert.equal(byKey(r2).s0.walk, null, "居続ける人まで歩いた");
  const path = r2.walkers.s1.path;
  assert.deepEqual(path, planPath(DOOR_XY, [D[5].x, D[5].y]));
  const sec = lengthOf(path) / WALK_SPEED;
  let book = r2.walkers;
  let last = [s1.x, s1.y];
  for (let t = 10.25 + 0.2; t < 10.25 + sec; t += 0.2) {
    const r = stepWalkers(book, actors, t, live);
    const d = byKey(r).s1;
    assert.ok(d.walk, `t=${t.toFixed(2)} で歩きやめた`);
    assert.ok(Math.abs(d.x - last[0]) + Math.abs(d.y - last[1]) <= Math.ceil(0.2 * WALK_SPEED) + 1, "位置が飛んだ");
    last = [d.x, d.y];
    book = r.walkers;
  }
  const done = stepWalkers(book, actors, 10.25 + sec + 0.01, live);
  assert.equal(byKey(done).s1.walk, null);
  assert.equal(byKey(done).s1.entering, false);
  assert.deepEqual([byKey(done).s1.x, byKey(done).s1.y], [D[5].x, D[5].y]);
  assert.equal(done.walkers.s1.path, null, "着いた人の帳簿に古い経路が残っている");
});

test("pxwalk stepWalkers: 固定時刻・停止（frozen）は全員を目的地に置く（退場も出さない・live なら帳簿も目的地）", () => {
  const r1 = stepWalkers(null, [who("s0", D[0]), who("s1", D[1])], 10, live);
  const r2 = stepWalkers(r1.walkers, [who("s0", D[0]), who("s1", D[1]), who("s2", D[2])], 10.2, live);   // s2 が入ってくる途中
  const moved = [who("s0", D[7]), who("s2", D[2]), who("s3", D[3])];                                     // s0 の目的地が変わり・s1 が消え・s3 が来た
  const r3 = stepWalkers(r2.walkers, moved, 10.4, { live: true, frozen: true });
  assert.deepEqual(r3.stats, { walking: 0, entering: 0, leaving: 0, unreachable: 0 });
  assert.deepEqual(r3.drawn.map((d) => [d.key, d.x, d.y, d.walk]), moved.map((a) => [a.session, a.x, a.y, null]));
  for (const a of moved) {
    assert.deepEqual([...r3.walkers[a.session].target], [a.x, a.y]);
    assert.equal(r3.walkers[a.session].path, null, `${a.session} の帳簿が歩いたまま＝停止を解いた瞬間に旧経路へ戻る`);
  }
  assert.ok(!Object.hasOwn(r3.walkers, "s1"), "停止中に消えた人が帳簿に残っている");
  // 停止を解いた次の回も、誰も歩き出さない（目的地は同じ）
  const r4 = stepWalkers(r3.walkers, moved, 10.6, live);
  assert.equal(r4.stats.walking, 0);
});

test("pxwalk stepWalkers: 巻き戻し（t が記録より前）は目的地", () => {
  const r1 = stepWalkers(null, [who("s0", D[0])], 10, live);
  const r2a = stepWalkers(r1.walkers, [who("s0", D[8])], 10.1, live);
  const r2 = stepWalkers(r2a.walkers, [who("s0", D[8])], 10.5, live);
  assert.ok(byKey(r2).s0.walk, "前提: 歩いていない");
  const r3 = stepWalkers(r2.walkers, [who("s0", D[8])], 9, live);
  assert.deepEqual([byKey(r3).s0.x, byKey(r3).s0.y, byKey(r3).s0.walk], [D[8].x, D[8].y, null]);
});

test("pxwalk stepWalkers: 目的地が変わったら今いる場所から引き直す（出発点・目的地へ飛ばない）", () => {
  const r1 = stepWalkers(null, [who("s0", D[0])], 10, live);
  const r2 = stepWalkers(r1.walkers, [who("s0", D[8])], 10, live);
  const t = 11.3;
  const mid = stepWalkers(r2.walkers, [who("s0", D[8])], t, live);
  const here = [byKey(mid).s0.x, byKey(mid).s0.y];
  assert.ok(byKey(mid).s0.walk, "前提: まだ歩いている");
  assert.notDeepEqual(here, [D[0].x, D[0].y]);
  const target = SLOTS.lounge[1];
  const r3 = stepWalkers(r2.walkers, [who("s0", target)], t, live);
  const s0 = byKey(r3).s0;
  assert.deepEqual([s0.x, s0.y], here, "引き直した瞬間に位置が飛んだ");
  assert.deepEqual(r3.walkers.s0.path, planPath(here, [target.x, target.y]), "今の位置から引いていない");
  assert.equal(r3.walkers.s0.t0, t);
  assert.equal(s0.entering, false);
});

test("pxwalk stepWalkers: 消えた人は live のときだけ扉まで歩いて消える（再生では歩かせない）", () => {
  const both = [who("s0", D[0]), who("s1", D[4], { vendor: "codex" })];
  const r1 = stepWalkers(null, both, 10, live);
  const r2 = stepWalkers(r1.walkers, [both[0]], 10.1, live);
  const s1 = byKey(r2).s1;
  // 席に立っていた人は立ち上がってから歩き出す（R110-S4）＝最初の回は立ち上がりの間（lift 1・歩行コマなし）
  assert.ok(s1 && s1.leaving && s1.lift === 1 && !s1.walk, "消えた人が席で立ち上がらない");
  assert.equal(s1.vendor, "codex", "退場中の人が最後に見えた actor の写しでない");
  assert.deepEqual([s1.x, s1.y], [D[4].x, D[4].y], "退場が今いる場所から始まらない");
  assert.deepEqual(r2.stats, { walking: 1, entering: 0, leaving: 1, unreachable: 0 });
  assert.deepEqual(r2.walkers.s1.path, planPath([D[4].x, D[4].y], DOOR_XY));
  const sec = lengthOf(r2.walkers.s1.path) / WALK_SPEED;
  const go = stepWalkers(r2.walkers, [both[0]], 10.1 + RISE_SEC + 0.05, live);
  assert.ok(byKey(go).s1?.leaving && byKey(go).s1.walk, "立ち上がりの後に扉へ歩き出さない");
  const mid = stepWalkers(go.walkers, [both[0]], 10.1 + RISE_SEC + sec / 2, live);
  assert.ok(byKey(mid).s1?.leaving);
  const gone = stepWalkers(mid.walkers, [both[0]], 10.1 + RISE_SEC + sec + 0.01, live);
  assert.ok(!byKey(gone).s1, "扉に着いたのに描いている");
  assert.ok(!Object.hasOwn(gone.walkers, "s1"), "扉に着いたのに帳簿に残っている");
  // 再生: 消えた人は描かない・帳簿はそのまま
  const replay = stepWalkers(r1.walkers, [both[0]], 10.1, { live: false });
  assert.ok(!byKey(replay).s1);
  assert.equal(replay.walkers, r1.walkers, "再生が帳簿を作り直した");
});

test("pxwalk stepWalkers: 再生（live でない）は帳簿を更新しない（同じ参照）・全員を目的地に置く", () => {
  const r1 = stepWalkers(null, [who("s0", D[0])], 10, live);
  const before = JSON.stringify(r1.walkers);
  const moved = [who("s0", D[6]), who("s9", D[2])];
  for (const opts of [{ live: false }, {}, { live: false, frozen: true }, undefined]) {
    const r = stepWalkers(r1.walkers, moved, 10.2, opts);
    assert.equal(r.walkers, r1.walkers);
    assert.deepEqual(r.drawn.map((d) => [d.key, d.x, d.y, d.walk]), moved.map((a) => [a.session, a.x, a.y, null]));
    assert.equal(r.stats.walking, 0);
  }
  assert.equal(JSON.stringify(r1.walkers), before);
  // 時刻が数でない live も帳簿に触らない（seen に NaN を書かない）
  assert.equal(stepWalkers(r1.walkers, moved, Number.NaN, live).walkers, r1.walkers);
});

test("pxwalk stepWalkers: 届かない目的地は瞬間移動して数える（1 回だけ・永久に止まらない）", () => {
  const r1 = stepWalkers(null, [who("s0", D[0])], 10, live);
  const far = { x: 300, y: 300 };
  const r2 = stepWalkers(r1.walkers, [who("s0", far), who("s1", far)], 10.1, live);
  assert.equal(r2.stats.unreachable, 2);
  for (const k of ["s0", "s1"]) assert.deepEqual([byKey(r2)[k].x, byKey(r2)[k].y, byKey(r2)[k].walk], [300, 300, null]);
  const r3 = stepWalkers(r2.walkers, [who("s0", far), who("s1", far)], 10.2, live);
  assert.equal(r3.stats.unreachable, 0, "同じ瞬間移動を毎回数えている");
});

test("pxwalk stepWalkers: 帳簿が古い（停止・非表示・再生から戻った）ときは入退場を遅れて演じない", () => {
  const r1 = stepWalkers(null, [who("s0", D[0]), who("s1", D[1])], 10, live);
  const t = 10 + WALK_STALE_SEC + 5;
  const r2 = stepWalkers(r1.walkers, [who("s0", D[8]), who("s2", D[2])], t, live);
  assert.deepEqual(r2.stats, { walking: 0, entering: 0, leaving: 0, unreachable: 0 });
  assert.deepEqual(r2.drawn.map((d) => [d.key, d.x, d.y]), [["s0", D[8].x, D[8].y], ["s2", D[2].x, D[2].y]]);
  assert.ok(!Object.hasOwn(r2.walkers, "s1"));
  // 戻った後は普通に歩く
  const r3 = stepWalkers(r2.walkers, [who("s0", D[8]), who("s2", D[2]), who("s3", D[3])], t + 0.2, live);
  assert.equal(r3.stats.entering, 1);
});

test("pxwalk stepWalkers: 歩いている途中で消えた人は、今いる場所から扉へ（目的地へ飛んでから出ない）", () => {
  const r1 = stepWalkers(null, [who("s0", D[0]), who("s1", D[2])], 10, live);
  const r2 = stepWalkers(r1.walkers, [who("s0", D[0]), who("s1", SLOTS.lounge[0])], 10, live);   // 机 2 → ソファへ歩き出す
  const mid = stepWalkers(r2.walkers, [who("s0", D[0]), who("s1", SLOTS.lounge[0])], 10.8, live);
  const here = [byKey(mid).s1.x, byKey(mid).s1.y];
  assert.ok(byKey(mid).s1.walk, "前提: まだ歩いている");
  assert.notDeepEqual(here, [SLOTS.lounge[0].x, SLOTS.lounge[0].y]);
  const gone = stepWalkers(r2.walkers, [who("s0", D[0])], 10.8, live);
  const s1 = byKey(gone).s1;
  assert.ok(s1?.leaving, "消えた人が出ていかない");
  assert.deepEqual([s1.x, s1.y], here, "退場が今いる場所から始まらない（目的地へ瞬間移動した）");
  assert.deepEqual(gone.walkers.s1.path, planPath(here, DOOR_XY));
});

test("pxwalk stepWalkers: 退場の途中で戻ってきた人は、今いる場所から目的地へ引き直す", () => {
  const r1 = stepWalkers(null, [who("s0", D[0]), who("s1", D[4])], 10, live);
  const r2 = stepWalkers(r1.walkers, [who("s0", D[0])], 10, live);
  const mid = stepWalkers(r2.walkers, [who("s0", D[0])], 11, live);
  const here = [byKey(mid).s1.x, byKey(mid).s1.y];
  const back = stepWalkers(r2.walkers, [who("s0", D[0]), who("s1", D[4])], 11, live);
  const s1 = byKey(back).s1;
  assert.deepEqual([s1.x, s1.y], here, "戻ってきた瞬間に位置が飛んだ");
  assert.equal(s1.leaving, false);
  assert.ok(s1.walk, "戻ってきた人が目的地へ歩いていない");
  assert.equal(back.walkers.s1.leaving, false);
});

test("pxwalk stepWalkers: 同じ session が 2 度出ても 2 人（#n）・決定論・入力を書き換えない", () => {
  const deepFreeze = (o) => { if (o && typeof o === "object" && !Object.isFrozen(o)) { Object.freeze(o); Object.values(o).forEach(deepFreeze); } return o; };
  const run = () => {
    const out = [];
    let book = null;
    const frames = [
      [who("s", D[0]), who("s", D[1]), who("a", D[2])],
      [who("s", D[0]), who("s", D[1]), who("a", D[2]), who("b", D[3])],
      [who("s", D[5]), who("a", D[2]), who("b", D[3])],
      [who("s", D[5]), who("b", D[6])],
    ];
    frames.forEach((actors, k) => {
      const r = stepWalkers(deepFreeze(book), deepFreeze(actors), 10 + k * 0.5, live);
      out.push(r);
      book = r.walkers;
    });
    return out;
  };
  const a = run();
  assert.equal(JSON.stringify(a), JSON.stringify(run()));
  assert.deepEqual(a[0].drawn.map((d) => d.key), ["s", "s#1", "a"]);
  assert.deepEqual(Object.keys(a[0].walkers), ["s", "s#1", "a"]);
  assert.ok(a[1].stats.entering === 1 && a[2].stats.leaving >= 1);
});

test("pxwalk stepWalkers: 歩いている人の位置は整数・walk は topPose の opts.walk にそのまま渡せる", () => {
  const w = buildWorld({ employees: Array.from({ length: 6 }, (_, i) => ({
    session: `s${i}`, dept: `p${i}`, cwd: `/w${i}`, state: i === 2 ? "resting" : "working", ...(i === 4 ? { question: "?" } : {}),
  })) });
  const board = w.agents.map((a) => ({ key: a.id, sessions: [a] }));
  const lay = roomLayout(w, board);
  const r1 = stepWalkers(null, lay.actors.slice(0, 3), 10, live);
  let book = r1.walkers;
  let walked = 0;
  for (let k = 1; k < 40; k += 1) {
    const r = stepWalkers(book, lay.actors, 10 + k * 0.137, live);
    for (const d of r.drawn) {
      assert.ok(Number.isInteger(d.x) && Number.isInteger(d.y), `${d.key} (${d.x},${d.y}) が整数でない`);
      if (d.walk) {
        walked += 1;
        assert.ok(["S", "N", "E", "W"].includes(d.walk.dir));
        assert.ok(d.walk.phase >= 0 && d.walk.phase < 1);
      }
      assert.equal(typeof d.zone, "string", "actor の材料（zone など）を落とした");
      // topPose にそのまま渡す: 歩いている人は歩行のコマ（W は E の鏡像）・止まっている人は歩行以外
      const pose = topPose(d, 10 + k * 0.137, { walk: d.walk });
      assert.ok(TOP_CELLS.includes(pose.cell));
      if (d.walk) {
        const dir = d.walk.dir === "W" ? "E" : d.walk.dir;
        assert.equal(pose.kind, "walk");
        assert.match(pose.cell, new RegExp(`^walk${dir}[0-3]$`));
        assert.equal(pose.mirror, d.walk.dir === "W");
        assert.equal(pose.dir, d.walk.dir);
      } else {
        assert.notEqual(pose.kind, "walk");
      }
    }
    book = r.walkers;
  }
  assert.ok(walked > 0, "誰も歩かなかった＝検査になっていない");
});

// ── 描画の刻みと歩行のコマ ─────────────────────────────────────────────

test("pxwalk 刻み: 12fps で stepWalkers → topPose を回すと、歩行コマの添字が描画 1 回ごとに +1（mod 4）", () => {
  assert.equal(WALK_SPEED / ROOM_FPS.walk, WALK_STRIDE / 4, "12fps の 1 回で進む距離が歩行の 1 コマと違う");
  // 扉 → 全ての机・ソファ・会議室・ラック前（曲がり角を含む経路）。刻みの時刻は 2 通りの作り方で
  //（量子化した時計＝(n + k) / 12 と、足し算で進める時計）＝浮動小数の誤差で 3k が 2.999… になっても崩れない
  const targets = [...SLOTS.desk, ...SLOTS.lounge, ...SLOTS.meeting, ...SLOTS.external];
  let steps = 0;
  for (const mode of ["quantized", "accumulated"]) {
    for (const [i, slot] of targets.entries()) {
      const n0 = 123 + i * 7;
      const t0 = n0 / ROOM_FPS.walk;
      let book = stepWalkers(null, [who("base", D[0])], t0 - 1 / ROOM_FPS.walk, live).walkers;
      const actors = [who("base", D[0]), who("s", slot)];
      let prev = null;
      let t = t0;
      for (let k = 0; k < 400; k += 1) {
        if (mode === "quantized") t = (n0 + k) / ROOM_FPS.walk;
        else if (k) t += 1 / ROOM_FPS.walk;
        const r = stepWalkers(book, actors, t, live);
        book = r.walkers;
        const d = byKey(r).s;
        if (!d.walk) break;
        assert.equal(roomFps(r.stats), ROOM_FPS.walk, "歩いている人が居るのに 12fps でない");
        const idx = walkIndex(topPose(d, t, { walk: d.walk }).cell);
        if (prev !== null) {
          assert.equal((idx - prev + 4) % 4, 1, `${mode} 机 ${i}: k=${k} で添字 ${prev}→${idx}（+1 でない＝止まる・逆回し・飛ぶ）`);
          steps += 1;
        }
        prev = idx;
      }
    }
  }
  assert.ok(steps > 500, `歩いた刻みが ${steps} 回＝検査になっていない`);
});

/**
 * 12fps で frames(k)（k 回目の刻みの actors・k = -1 は最初の世界）を回し、キー "s" の人が歩いている間の歩行コマの
 * 添字が描画 1 回ごとに +1 か確かめる。戻り値は「引き直した刻み ats の全部で、差を歩いたまま確かめたか」。
 */
function walkTicks(frames, ats, mode, label) {
  const fps = ROOM_FPS.walk;
  const n0 = 240;
  let book = stepWalkers(null, frames(-1), (n0 - 1) / fps, live).walkers;
  let prev = null;
  let t = n0 / fps;
  const crossed = new Set();
  for (let k = 0; k < 400; k += 1) {
    if (mode === "quantized") t = (n0 + k) / fps;
    else if (k) t += 1 / fps;
    const r = stepWalkers(book, frames(k), t, live);
    book = r.walkers;
    const d = byKey(r).s;
    if (!d?.walk) {
      // 歩き出す前の立ち上がり（R110-S4・席に立っていた人だけ）は飛ばす。歩き出した後に歩行コマが消えたら（曲がり直しで
      // 立ち上がった・止まった）ここで抜ける＝引き直しの刻みを跨げず、下の crossed が足りなくて赤
      if (prev === null && d?.lift === 1) continue;
      break;
    }
    const idx = walkIndex(topPose(d, t, { walk: d.walk }).cell);
    if (prev !== null) {
      assert.equal((idx - prev + 4) % 4, 1,
        `${label} ${mode}: k=${k}（引き直しは k=${ats}）で添字 ${prev}→${idx}（+1 でない＝止まる・逆回し・飛ぶ）`);
      if (ats.includes(k)) crossed.add(k);
    }
    prev = idx;
  }
  return crossed.size === ats.length;
}

test("pxwalk 刻み: 歩いている途中で引き直しても（目的地の変更・消えて退場・退場の途中で戻る・入場の途中で ❗）、添字は描画 1 回ごとに +1", () => {
  // 引き直した刻みで位相を 0 に戻すと、添字が 0→0・1→0・2→0・3→0 になる（止まる・飛ぶ・逆回し）。旧経路で進んだ距離の
  // 端数（帳簿の d0）を新しい経路へ持ち越せば、引き直した刻みも +1 のまま
  const base = who("base", D[1]);
  const stride = WALK_SPEED / ROOM_FPS.walk;                 // 12fps の 1 回で進む距離（2px）
  // 席に立っていた人は立ち上がり（RISE_SEC＝12fps の 2 刻み）の後に歩き出す（R110-S4）＝最初の脚の刻みはその分ずれる
  const R = Math.round(RISE_SEC * ROOM_FPS.walk);
  /** 最初の脚の経路で「着いた刻み」（その刻みで引き直すと、旧経路は着いた扱い＝端数は歩数計から） */
  const arrive = (from, to, rise = R) => rise + Math.ceil(lengthOf(planPath([from.x, from.y], [to.x, to.y])) / stride);
  const cases = {
    // 机 0 → 机 8 を歩いている途中で、ソファへ（休憩に変わった）
    reroute: [(k, at) => (k < 0 ? [who("s", D[0]), base] : [who("s", k < at ? D[8] : SLOTS.lounge[1]), base]),
      arrive(D[0], D[8])],
    // 同じく、受付へ向かい直した 5 刻み後に、もう一度（ソファへ）＝引き直した経路の端数も次へ持ち越す
    twice: [(k, at) => (k < 0 ? [who("s", D[0]), base]
      : [who("s", k < at ? D[8] : k < at + 5 ? SLOTS.queue[4] : SLOTS.lounge[1]), base]), arrive(D[0], D[8]), 5],
    // 机 0 → 机 8 を歩いている途中で、消えた（今いる場所から扉へ）
    vanish: [(k, at) => (k < 0 ? [who("s", D[0]), base] : k < at ? [who("s", D[8]), base] : [base]), arrive(D[0], D[8])],
    // 机 4 から扉へ出ていく途中で、戻ってきた
    back: [(k, at) => (k < 0 || k >= at ? [who("s", D[4]), base] : [base]), arrive(D[4], { x: DOOR_XY[0], y: DOOR_XY[1] })],
    // 扉から机 5 へ入ってくる途中で、❗ になって受付へ（扉から入る人は外から歩いて来る＝立ち上がらない）
    call: [(k, at) => (k < 0 ? [base] : [base, who("s", k < at ? D[5] : SLOTS.queue[2])]),
      arrive({ x: DOOR_XY[0], y: DOOR_XY[1] }, D[5], 0), undefined, 0],
  };
  let crossed = 0;
  let tried = 0;
  for (const [label, [frames, arrival, again, rise = R]] of Object.entries(cases)) {
    // 最初の脚の途中の全ての剰余（mod 4）と、最初の脚に着いた刻みそのもの（歩き出した刻みから数える）
    for (const at of [...Array.from({ length: 28 }, (_, i) => rise + i + 2), arrival]) {
      const ats = again ? [at, at + again] : [at];
      for (const mode of ["quantized", "accumulated"]) {
        tried += 1;
        if (walkTicks((k) => frames(k, at), ats, mode, `${label} at=${at}`)) crossed += 1;
      }
    }
  }
  // 前提: どの場合も、引き直した刻みの前後で歩いていた（差を確かめた）＝検査が空振りしていない
  assert.equal(crossed, tried, `引き直しの刻みを跨いで歩いたのは ${crossed}/${tried} 回`);
});

test("pxwalk 刻み: 6fps（誰も歩いていない）では止まっている人のコマが描画ごとに逆回しにも飛びにもならない", () => {
  const cases = {
    idle: [{}, {}], type: [{ state: "working" }, {}], read: [{ state: "working", verb: "読込中" }, {}],
    raise: [{ attention: true, zone: "queue" }, {}], rest: [{ state: "resting" }, {}],
    think: [{ kind: "think" }, {}], done: [{ state: "waiting" }, { done: true }],
  };
  for (const [kind, [over, opts]] of Object.entries(cases)) {
    const a = { session: "s", zone: "desk", state: "idle", kind: "idle", verb: "", ...over };
    // コマの並び＝細かく刻んだときに現れる順（ループ 1 周ぶん）
    const order = [];
    for (let t = 0; t < 8; t += 1 / 64) {
      const p = topPose(a, t, opts);
      assert.equal(p.kind, kind, `前提: ${kind}`);
      if (!order.includes(p.cell)) order.push(p.cell);
    }
    for (const fps of [ROOM_FPS.idle, ROOM_FPS.walk]) {
      const seen = new Set();
      let prev = null;
      for (let k = 0; k < fps * 8; k += 1) {
        const cell = topPose(a, (600 + k) / fps, opts).cell;
        const i = order.indexOf(cell);
        assert.ok(i >= 0, `${kind}: ${cell}`);
        if (prev !== null) {
          const step = (i - prev + order.length) % order.length;
          assert.ok(step === 0 || step === 1, `${kind} ${fps}fps: ${order[prev]}→${cell}（逆回し・飛び）`);
        }
        seen.add(cell);
        prev = i;
      }
      // どのコマも描かれる（描画の刻みとコマ送りが割り切れず、同じコマに張り付く＝止まって見える、を許さない）
      assert.deepEqual([...seen].sort(), [...order].sort(), `${kind} ${fps}fps: 描かれないコマがある`);
    }
  }
  // 歩く人が 6fps で描かれる瞬間（歩き出した回・刻みの切り替え前）も逆回しにはならない（+2 まで）
  const path = planPath(DOOR_XY, [D[4].x, D[4].y]);
  let prev = null;
  for (let k = 0; k < 40; k += 1) {
    const p = posAt(path, 0, k / ROOM_FPS.idle);
    if (p.done) break;
    const idx = walkIndex(topPose({}, 0, { walk: p }).cell);
    if (prev !== null) assert.notEqual((idx - prev + 4) % 4, 3, `6fps で歩行が逆回し（${prev}→${idx}）`);
    prev = idx;
  }
});

test("pxwalk roomFps: 歩いている人（入退場を含む）が居る間だけ 12・それ以外は 6", () => {
  assert.equal(roomFps({ walking: 0, entering: 0, leaving: 0, unreachable: 0 }), 6);
  assert.equal(roomFps({ walking: 1, entering: 1, leaving: 0, unreachable: 0 }), 12);
  assert.equal(roomFps({ walking: 2 }), 12);
  for (const bad of [null, undefined, {}, { walking: "x" }, { walking: -1 }]) assert.equal(roomFps(bad), 6, JSON.stringify(bad));
  // 実際の stats で: 入場の途中は 12・着いたら 6・退場の途中は 12
  const r1 = stepWalkers(null, [who("s0", D[0])], 10, live);
  assert.equal(roomFps(r1.stats), 6);
  const r2 = stepWalkers(r1.walkers, [who("s0", D[0]), who("s1", D[1])], 10.1, live);
  assert.equal(roomFps(r2.stats), 12);
  const r3 = stepWalkers(r2.walkers, [who("s0", D[0])], 10.2, live);
  assert.ok(r3.stats.leaving === 1 && roomFps(r3.stats) === 12);
  // 12fps で描いても 6fps の刻み（topPose の最速 3fps）と割り切れる
  for (const f of [3, 2, 1, 0.5]) assert.ok(Number.isInteger(ROOM_FPS.walk / f) && Number.isInteger(ROOM_FPS.idle / f), `${f}fps`);
});

// ── 約束の細部（1 つずつ・壊したら赤になることを確かめてある）─────────────────────
test("pxwalk planPath: 同じ向きに続く途中の点は畳む（どの枠の組でも、隣り合う区間は向きが違う）", () => {
  let folded = 0;
  for (const from of SPOTS) {
    for (const to of SPOTS) {
      if (from === to) continue;
      const path = planPath(from.xy, to.xy);
      for (let k = 1; k + 1 < path.length; k += 1) {
        const d1 = [Math.sign(path[k][0] - path[k - 1][0]), Math.sign(path[k][1] - path[k - 1][1])];
        const d2 = [Math.sign(path[k + 1][0] - path[k][0]), Math.sign(path[k + 1][1] - path[k][1])];
        assert.notDeepEqual(d1, d2, `${from.key} → ${to.key}: 点 ${path[k]} が同じ向きの途中（畳まれていない）`);
      }
      folded += 1;
    }
  }
  assert.ok(folded > 100);
  // 前提: 畳む前の経路（格子の停留点を通る）には同じ向きの途中の点がある＝検査になっている
  const raw = routePath(footOf(DOOR_XY), footOf([D[0].x, D[0].y]), G);
  assert.ok(raw.length > planPath(DOOR_XY, [D[0].x, D[0].y]).length, "前提: 畳む点が無い経路で確かめている");
});

test("pxwalk planPath: 入力は整数に丸める（先頭と最後も丸めた点）", () => {
  const s = SLOTS.desk[4];
  const path = planPath([DOOR_XY[0] + 0.4, DOOR_XY[1] - 0.4], [s.x - 0.3, s.y + 0.2]);
  assert.deepEqual(path, planPath(DOOR_XY, [s.x, s.y]));
  assert.deepEqual(path[0], [...DOOR_XY]);
  assert.deepEqual(path[path.length - 1], [s.x, s.y]);
  // 丸めると同じ点になる入力は 1 点の折れ線
  assert.deepEqual(planPath([10.2, 20.4], [9.8, 19.6]), [[10, 20]]);
});

test("pxwalk グラフ: 枠の索引（spots）は凍結（扉の at も）", () => {
  assert.ok(Object.isFrozen(G.spots));
  for (const [key, s] of Object.entries(G.spots)) {
    assert.ok(Object.isFrozen(s), `${key} が凍結されていない`);
    assert.ok(Object.isFrozen(s.at), `${key}.at が凍結されていない`);
    assert.ok(Object.isFrozen(s.foot), `${key}.foot が凍結されていない`);
  }
  assert.deepEqual([...G.spots.door.at], []);
});

test("pxwalk stepWalkers: 記録が未来（巻き戻し）なら歩きの続きも入退場も演じない", () => {
  const r1 = stepWalkers(null, [who("s0", D[0]), who("s1", D[1])], 10, live);
  const r2 = stepWalkers(r1.walkers, [who("s0", D[8]), who("s1", D[1])], 10, live);    // s0 が歩き出す（t0=10）
  const ahead = stepWalkers(r2.walkers, [who("s0", D[8]), who("s1", D[1])], 12, live);  // 帳簿の seen=12
  assert.ok(byKey(ahead).s0.walk, "前提: 12 秒でまだ歩いている");
  // t=11（帳簿より前・歩き出しより後）: 未来の記録は古い扱い＝目的地に置く（posAt の続きを出さない）
  const back = stepWalkers(ahead.walkers, [who("s0", D[8]), who("s2", D[2])], 11, live);
  assert.deepEqual([byKey(back).s0.x, byKey(back).s0.y, byKey(back).s0.walk], [D[8].x, D[8].y, null]);
  assert.deepEqual(back.stats, { walking: 0, entering: 0, leaving: 0, unreachable: 0 }, "巻き戻しで入退場を演じた");
});

test("pxwalk stepWalkers: drawn は actors の順、その後ろに退場中の人（帳簿の順）", () => {
  const r1 = stepWalkers(null, [who("a", D[0]), who("b", D[1]), who("c", D[2]), who("d", D[3])], 10, live);
  const r2 = stepWalkers(r1.walkers, [who("d", D[3]), who("c", D[2])], 10.1, live);   // a と b が出ていく
  assert.deepEqual(r2.drawn.map((d) => [d.key, d.leaving]), [["d", false], ["c", false], ["a", true], ["b", true]]);
});

test("pxwalk stepWalkers: 退場で扉へ届かない人も数える（瞬間に消える）", () => {
  const far = { x: 300, y: 300 };
  const r1 = stepWalkers(null, [who("s0", D[0]), who("s1", far)], 10, live);
  const r2 = stepWalkers(r1.walkers, [who("s0", D[0])], 10.1, live);
  assert.equal(r2.stats.unreachable, 1, "扉へ届かない退場を数えていない");
  assert.ok(!byKey(r2).s1 && !Object.hasOwn(r2.walkers, "s1"), "届かない退場の人が残っている");
});

test("pxwalk stepWalkers: 入場の途中で目的地が変わったら entering を下ろす（続けて歩く間は entering のまま）", () => {
  const r1 = stepWalkers(null, [who("s0", D[0])], 10, live);
  const r2 = stepWalkers(r1.walkers, [who("s0", D[0]), who("s1", D[5])], 10.1, live);
  assert.ok(byKey(r2).s1.entering);
  const cont = stepWalkers(r2.walkers, [who("s0", D[0]), who("s1", D[5])], 10.5, live);
  assert.ok(byKey(cont).s1.entering && cont.walkers.s1.enter, "入場を続けている間に entering が落ちた");
  const moved = stepWalkers(cont.walkers, [who("s0", D[0]), who("s1", SLOTS.lounge[0])], 10.9, live);
  assert.ok(byKey(moved).s1.walk, "前提: 引き直した後も歩いている");
  assert.equal(byKey(moved).s1.entering, false, "引き直した後も入場中のまま");
  assert.equal(moved.walkers.s1.enter, false);
  assert.equal(moved.stats.entering, 0);
});

test("pxwalk stepWalkers: 歩いている人の帳簿の who は最新の actor（退場の写しに古い様子を使わない）", () => {
  const r1 = stepWalkers(null, [who("s0", D[0]), who("s1", D[1], { state: "working" })], 10, live);
  const r2 = stepWalkers(r1.walkers, [who("s0", D[0]), who("s1", D[8], { state: "working" })], 10.1, live);   // 歩き出す
  const r3 = stepWalkers(r2.walkers, [who("s0", D[0]), who("s1", D[8], { state: "waiting", vendor: "codex" })], 10.3, live);
  assert.ok(byKey(r3).s1.walk, "前提: 歩いている");
  assert.equal(r3.walkers.s1.who.state, "waiting", "歩いている人の who が古いまま");
  const gone = stepWalkers(r3.walkers, [who("s0", D[0])], 10.5, live);
  assert.equal(byKey(gone).s1.state, "waiting", "退場の写しが最後に見えた actor でない");
  assert.equal(byKey(gone).s1.vendor, "codex");
});

test("pxwalk stepWalkers: 人は actor.key（roomLayout の割り当てキー）で追う・同じキーには空いた #n", () => {
  // roomLayout は台帳の全員（部屋の外の人を含む）で #n を振る＝描く人だけで数え直すと食い違う
  const a = (key, slot, extra = {}) => ({ ...who("s", slot, extra), key });
  const r1 = stepWalkers(null, [a("s#1", D[0]), a("s#2", D[1])], 10, live);
  assert.deepEqual(Object.keys(r1.walkers), ["s#1", "s#2"]);
  assert.deepEqual(r1.drawn.map((d) => d.key), ["s#1", "s#2"]);
  const r2 = stepWalkers(r1.walkers, [a("s#2", D[1]), a("s#1", D[5])], 10.1, live);   // 並びが入れ替わっても同じ人
  assert.ok(byKey(r2)["s#1"].lift === 1 && !byKey(r2)["s#1"].entering, "並びが変わった人を別人として扉から入れた");
  assert.deepEqual([byKey(r2)["s#1"].x, byKey(r2)["s#1"].y], [D[0].x, D[0].y], "並びが変わった人が自分の席から歩き出さない");
  assert.equal(byKey(r2)["s#2"].walk, null);
  // key の無い人と混ざって同じキーになるときは、空いている #n（衝突させない）
  const mixed = stepWalkers(null, [a("s#1", D[0]), who("s", D[1]), who("s", D[2])], 10, live);
  const keys = mixed.drawn.map((d) => d.key);
  assert.equal(new Set(keys).size, 3, `キーが衝突: ${keys}`);
  assert.deepEqual(keys, ["s#1", "s", "s#2"]);
  // roomLayout と同じキー（部屋の外の人が先に居ても）
  const w = buildWorld({ employees: [0, 1, 2].map((i) => ({ session: `x${i}`, dept: "p", cwd: "/w", state: "working" })) });
  const lay = roomLayout(w, [{ key: "g", sessions: [w.agents[0], w.agents[0], w.agents[1]] }]);
  const st = stepWalkers(null, lay.actors, 10, live);
  assert.deepEqual(st.drawn.map((d) => d.key), lay.actors.map((x) => x.key));
  assert.deepEqual(Object.keys(st.walkers), Object.keys(lay.assign).filter((k) => lay.assign[k].zone !== "overflow"));
});

// ── R110-S4: 歩行のテンポ（24px/秒・歩き出す前の立ち上がり・着いた瞬間の一拍）─────────────────────────
// 本人「歩く速度が速すぎてパッと着く。もう少し落として、せっせと働いている感じを」（2026-10-06）。
// 歩調（12fps の描画 1 回にコマ +1）は保ったまま歩幅を縮め、止まっていた人は歩き出す前に RISE_SEC 立ち上がり、
// 歩いて着いた人は SETTLE_SEC だけ正面で一拍置く。どれも live の帳簿からだけ（固定時刻・再生・初めて見えた人には出ない）。
const R_TICKS = Math.round(RISE_SEC * ROOM_FPS.walk);

/** 12fps で frames(k) を回し、キー key の人の drawn・stats を控える（k=-1 の世界で帳簿を作ってから） */
function runTicks(frames, key, n, n0 = 600) {
  const fps = ROOM_FPS.walk;
  let book = stepWalkers(null, frames(-1), (n0 - 1) / fps, live).walkers;
  const out = [];
  for (let k = 0; k < n; k += 1) {
    const t = (n0 + k) / fps;
    const r = stepWalkers(book, frames(k), t, live);
    book = r.walkers;
    out.push({ k, t, d: byKey(r)[key] || null, stats: r.stats, rec: r.walkers[key] || null });
  }
  return out;
}

test("pxwalk（R110-S4）: 扉 → 机 0 の入場は 264px ÷ 24px/s＝11 秒で着く（扉から入る人は立ち上がらない）・歩行中の 1 刻みは 4px 以下", () => {
  const fps = ROOM_FPS.walk;
  const path = planPath(DOOR_XY, [D[0].x, D[0].y]);
  const want = lengthOf(path) / WALK_SPEED;
  assert.equal(want, 11);
  const ticks = runTicks((k) => (k < 0 ? [who("base", D[4])] : [who("base", D[4]), who("s", D[0])]), "s", fps * 13);
  assert.ok(ticks[0].d.walk && ticks[0].d.entering && ticks[0].d.lift === 0, "扉から入る人が最初の刻みから歩いていない（立ち上がった）");
  assert.deepEqual([ticks[0].d.x, ticks[0].d.y], [...DOOR_XY]);
  const arrived = ticks.find((x) => !x.d.walk);
  assert.ok(arrived, "13 秒で机に着かない");
  assert.equal(arrived.k, want * fps, `扉から机 0 まで ${arrived.k} 刻み（${(arrived.k / fps).toFixed(3)} 秒）＝${want} 秒でない`);
  assert.deepEqual([arrived.d.x, arrived.d.y], [D[0].x, D[0].y]);
  let maxStep = 0;
  for (let k = 1; k < arrived.k; k += 1) {
    const a = ticks[k - 1].d;
    const b = ticks[k].d;
    maxStep = Math.max(maxStep, Math.abs(b.x - a.x) + Math.abs(b.y - a.y));
  }
  assert.ok(maxStep <= 4, `歩行中の 1 刻みに ${maxStep}px 動いた（瞬間移動）`);
  assert.ok(maxStep >= 1, "前提: 歩いている間に動いていない");
});

test("pxwalk（R110-S4）: 席に立っていた人は RISE_SEC（12fps の 2 刻み）その場で立ち上がり（lift 1・歩行コマなし・12fps に数える）、それから歩行コマ 0 で歩き出す", () => {
  assert.equal(RISE_SEC, 1 / 6);
  assert.equal(R_TICKS, 2);
  // 机 0 → 机 8（目的地が変わった）・机 2 から消えた（扉へ）の 2 通り
  const cases = {
    reroute: [(k) => [who("s", k < 0 ? D[0] : D[8]), who("base", D[4])], D[0], false],
    leave: [(k) => (k < 0 ? [who("s", D[2]), who("base", D[4])] : [who("base", D[4])]), D[2], true],
  };
  for (const [label, [frames, from, leaving]] of Object.entries(cases)) {
    const ticks = runTicks(frames, "s", 12);
    for (let k = 0; k < R_TICKS; k += 1) {
      const { d, stats, rec } = ticks[k];
      assert.ok(d, `${label}: k=${k} で描かれていない`);
      assert.deepEqual([d.x, d.y, d.walk, d.lift, d.leaving], [from.x, from.y, null, 1, leaving],
        `${label}: k=${k}（立ち上がりの間）の位置・歩行・lift`);
      assert.equal(stats.walking, 1, `${label}: 立ち上がりの間を歩く人に数えない（描画が 12fps にならない）`);
      assert.equal(roomFps(stats), ROOM_FPS.walk);
      assert.equal(stats.leaving, leaving ? 1 : 0);
      assert.ok(rec.path && rec.t0 > ticks[k].t, `${label}: 立ち上がりの間の帳簿の t0 が未来でない`);
      // topPose: 正面の待機コマで 1px 浮く（rise）
      const pose = topPose(d, ticks[k].t, { walk: d.walk, lift: d.lift, settled: d.settled });
      assert.deepEqual([pose.kind, pose.cell, pose.lift], ["rise", "idle0", 1], `${label}: k=${k} のコマ`);
    }
    const go = ticks[R_TICKS].d;
    assert.ok(go.walk && go.lift === 0, `${label}: 立ち上がりの後（k=${R_TICKS}）に歩き出さない`);
    assert.deepEqual([go.x, go.y], [from.x, from.y], `${label}: 歩き出した刻みに位置が飛んだ`);
    assert.equal(walkIndex(topPose(go, ticks[R_TICKS].t, { walk: go.walk }).cell), 0, `${label}: 止まっていた人が歩行コマ 0 から歩き出さない`);
    // 歩き出した後は描画 1 回にコマ +1・lift 0
    for (let k = R_TICKS + 1; k < 12; k += 1) {
      const { d, t } = ticks[k];
      assert.ok(d.walk && d.lift === 0, `${label}: k=${k} で歩きやめた／浮いた`);
      assert.equal(walkIndex(topPose(d, t, { walk: d.walk }).cell), (k - R_TICKS) % 4, `${label}: k=${k} の歩行コマ`);
    }
  }
});

test("pxwalk（R110-S4）: 歩いている途中の引き直しは立ち上がらない・立ち上がりの途中の引き直しは残りを持ち越す・一点の経路は立たない", () => {
  // 歩いている途中（机 0 → 机 8 の 10 刻み目）でソファへ: その刻みも歩行のまま（lift 0）・位置は続き
  const mid = runTicks((k) => [who("s", k < 0 ? D[0] : k < 10 ? D[8] : SLOTS.lounge[1]), who("base", D[4])], "s", 14);
  for (let k = R_TICKS; k < 14; k += 1) assert.ok(mid[k].d.walk && mid[k].d.lift === 0, `k=${k}: 歩いている途中の引き直しで止まった`);
  assert.ok(Math.abs(mid[10].d.x - mid[9].d.x) + Math.abs(mid[10].d.y - mid[9].d.y) <= 4, "引き直した刻みに位置が飛んだ");
  // 立ち上がりの途中（k=1）で目的地が変わった: 位置は出発点のまま・歩き出す時刻（t0）は最初の立ち上がりの終わりのまま
  const re = runTicks((k) => [who("s", k < 0 ? D[0] : k < 1 ? D[8] : SLOTS.queue[3]), who("base", D[4])], "s", 6);
  assert.equal(re[1].d.lift, 1, "立ち上がりの途中に引き直したら立ち上がりが消えた");
  assert.deepEqual([re[1].d.x, re[1].d.y], [D[0].x, D[0].y]);
  assert.equal(re[1].rec.t0, re[0].rec.t0, "立ち上がりの途中の引き直しで立ち上がりをやり直した（延びた）");
  assert.deepEqual(re[1].rec.path, planPath([D[0].x, D[0].y], [SLOTS.queue[3].x, SLOTS.queue[3].y]), "新しい目的地へ引き直していない");
  assert.ok(re[R_TICKS].d.walk, "立ち上がりの終わりに歩き出さない");
  // 立ち上がりの途中で元の席へ戻った（目的地＝今いる場所）: 立ったまま着いた扱い（浮きも歩きもしない）
  const sat = runTicks((k) => [who("s", k < 0 || k >= 1 ? D[0] : D[8]), who("base", D[4])], "s", 3);
  assert.equal(sat[0].d.lift, 1, "前提: 立ち上がった");
  assert.deepEqual([sat[1].d.walk, sat[1].d.lift, sat[1].stats.walking], [null, 0, 0], "席へ戻ったのに浮いたまま・歩く人に数えた");
});

test("pxwalk（R110-S4）: 歩いて着いた人は帳簿に arrivedAt・drawn.settled＝着いてからの秒（SETTLE_SEC は正面の一拍 → 打鍵）", () => {
  const fps = ROOM_FPS.walk;
  const ticks = runTicks((k) => [who("s", k < 0 ? D[0] : D[1], { state: "working" }), who("base", D[4])], "s", fps * 6);
  const k0 = ticks.findIndex((x, k) => k > R_TICKS && !x.d.walk);
  assert.ok(k0 > 0, "前提: 6 秒で隣の机に着かない");
  const a = ticks[k0];
  assert.equal(a.rec.arrivedAt, a.t, "着いた回の時刻を帳簿の arrivedAt に書いていない");
  assert.equal(a.d.settled, 0);
  const kinds = [];
  for (let k = k0; k < Math.min(ticks.length, k0 + fps); k += 1) {
    const { d, t, rec } = ticks[k];
    assert.equal(rec.arrivedAt, a.t, `k=${k}: 立っている間に起点が動いた`);
    assert.equal(d.settled, Math.round((t - a.t) * 1e6) / 1e6, `k=${k}: settled が着いてからの秒でない`);
    const p = topPose(d, t, { walk: d.walk, lift: d.lift, settled: d.settled });
    kinds.push(p.kind);
    if (Math.round((t - a.t) * 1e6) < Math.round(SETTLE_SEC * 1e6)) assert.equal(p.cell, "idle0", `k=${k}: 一拍の間が正面の待機でない`);
  }
  // 一拍は SETTLE_SEC＝12fps の 4 刻み → その後は打鍵
  assert.deepEqual(kinds.slice(0, 6), ["settle", "settle", "settle", "settle", "type", "type"]);
  // 着いて立っている人は歩く人に数えない（一拍の間も 6fps）
  assert.equal(a.stats.walking, 0);
  // 6fps の格子（着いた後の描画の刻み）でも一拍はちょうど 2 刻み
  const fps6 = ROOM_FPS.idle;
  let book = stepWalkers(null, [who("s", D[0]), who("base", D[4])], 100, live).walkers;
  let t = 100;
  const world = [who("s", D[1], { state: "working" }), who("base", D[4])];
  for (let k = 1; k < fps6 * 10; k += 1) {
    t = 100 + k / fps6;
    const r = stepWalkers(book, world, t, live);
    book = r.walkers;
    if (k > 1 && !byKey(r).s.walk && !byKey(r).s.lift) break;
  }
  assert.equal(book.s.arrivedAt, t, "前提: 6fps の格子で着いた");
  const n6 = [0, 1, 2, 3].map((j) => {
    const tt = t + j / fps6;
    const x = stepWalkers(book, world, tt, live);
    book = x.walkers;
    return topPose(byKey(x).s, tt, { settled: byKey(x).s.settled }).kind;
  });
  assert.deepEqual(n6, ["settle", "settle", "type", "type"], "6fps の格子で一拍が 2 刻みでない");
});

test("pxwalk（R110-S4）: 一拍と立ち上がりは live の帳簿からだけ（初めて見えた人・固定時刻・再生・古い帳簿・届かない・壊れた帳簿には出ない）", () => {
  // 初めて見えた人（baseline）: 立っているが一拍の起点は無い
  const r1 = stepWalkers(null, [who("s0", D[0])], 10, live);
  assert.deepEqual([byKey(r1).s0.settled, byKey(r1).s0.lift, r1.walkers.s0.arrivedAt], [null, 0, null]);
  // 歩いて着いた直後の帳簿
  let book = r1.walkers;
  let t = 10;
  for (let k = 1; k < 12 * 8; k += 1) {
    t = 10 + k / 12;
    const r = stepWalkers(book, [who("s0", D[1])], t, live);
    book = r.walkers;
    if (k > R_TICKS && !byKey(r).s0.walk) break;
  }
  assert.equal(book.s0.arrivedAt, t, "前提: 着いた");
  // 固定時刻（frozen）: 目的地に置き、一拍も立ち上がりも無い（帳簿の起点も消す＝止めを解いても遅れて一拍しない）
  const fz = stepWalkers(book, [who("s0", D[1])], t + 1 / 12, { live: true, frozen: true });
  assert.deepEqual([byKey(fz).s0.settled, byKey(fz).s0.lift, fz.walkers.s0.arrivedAt], [null, 0, null]);
  const fz2 = stepWalkers(book, [who("s0", D[5])], t + 1 / 12, { live: true, frozen: true });
  assert.deepEqual([byKey(fz2).s0.x, byKey(fz2).s0.y, byKey(fz2).s0.lift, fz2.stats.walking], [D[5].x, D[5].y, 0, 0], "止めている間に立ち上がった");
  // 再生（live でない）
  const rp = stepWalkers(book, [who("s0", D[5])], t + 1 / 12, { live: false });
  assert.deepEqual([byKey(rp).s0.settled, byKey(rp).s0.lift], [null, 0]);
  // 古い帳簿（止めて戻った）: 一拍の続きも立ち上がりも演じない
  const st = stepWalkers(book, [who("s0", D[1])], t + WALK_STALE_SEC + 1, live);
  assert.deepEqual([byKey(st).s0.settled, st.walkers.s0.arrivedAt], [null, null]);
  const st2 = stepWalkers(book, [who("s0", D[5])], t + WALK_STALE_SEC + 1, live);
  assert.deepEqual([byKey(st2).s0.lift, st2.stats.walking, byKey(st2).s0.x], [0, 0, D[5].x]);
  // 届かない目的地（瞬間移動）は一拍なし
  const far = stepWalkers(book, [who("s0", { x: 300, y: 300 })], t + 1 / 12, live);
  assert.deepEqual([far.stats.unreachable, byKey(far).s0.settled, byKey(far).s0.lift], [1, null, 0]);
  // 壊れた帳簿: t0 が書いた時刻より RISE_SEC を超えて未来＝読まない（永久に立ち上がったままにしない）
  const bad = { s0: { path: planPath([D[0].x, D[0].y], [D[8].x, D[8].y]), t0: 1e9, d0: 0, target: [D[8].x, D[8].y],
    seen: t, leaving: false, enter: false, who: who("s0", D[8]) } };
  const rb = stepWalkers(bad, [who("s0", D[8])], t + 1 / 12, live);
  assert.deepEqual([byKey(rb).s0.lift, rb.stats.walking], [0, 0], "未来すぎる t0 の記録で立ち上がり続けた");
  // arrivedAt の壊れた値（未来・数でない）は読まない
  for (const v of [t + 100, NaN, "3", null]) {
    const b2 = { s0: { ...book.s0, arrivedAt: v } };
    assert.equal(byKey(stepWalkers(b2, [who("s0", D[1])], t + 1 / 12, live)).s0.settled, null, `arrivedAt=${String(v)}`);
  }
});

test("pxwalk（R110-S5）: 本棚の前の枝は足元 (128,20) → (138,20) → 奥の通路 (138,42)・家具を越えない・ほかの行き先は枝を持たない", () => {
  const shelf = ERRAND_SPOTS.find((s) => s.name === "shelf");
  assert.deepEqual(footOf([shelf.x, shelf.y]), [128, 20]);
  const spot = G.spots["errand#shelf"];
  assert.ok(spot, "本棚の前の枝がグラフに無い");
  assert.deepEqual([...spot.at], [], "本棚の枝が家具を越えてよいことになっている");
  const node = (p) => G.nodes.findIndex((n) => n[0] === p[0] && n[1] === p[1]);
  const edge = (a, b) => G.edges.some(([i, j]) => (i === node(a) && j === node(b)) || (i === node(b) && j === node(a)));
  assert.ok(edge([128, 20], [138, 20]) && edge([138, 20], [138, LANES.y[0]]), "枝の辺が (128,20)→(138,20)→(138,42) でない");
  for (const name of ["bulletin", "board", "counter"]) {
    assert.equal(Object.hasOwn(G.spots, `errand#${name}`), false, `${name} に枝を足した（planPath の自動のつなぎで届く）`);
  }
  // 全部の机から本棚の前へは、枝の曲がり角 (138,20)（足元）を通って入る
  for (const d of SLOTS.desk) {
    const path = planPath([d.x, d.y], [shelf.x, shelf.y]).map(footOf);
    assert.ok(path.some(([x, y]) => x === 138 && y === 20), `机 (${d.x},${d.y}) → 本棚が枝を通らない: ${JSON.stringify(path)}`);
  }
});
