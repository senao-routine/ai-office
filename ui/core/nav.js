// ──────────────────────────────────────────────────────────────
// フロアのレイアウト定数と歩行ナビゲーション（R58）。
//
// ここは ui/core＝純ロジック層。DOM も three.js も触らないので
// node --test でそのまま「経路が机を横切らない」ことを機械検査できる。
// LAYOUT/WALL/PODS の正本はここ（ui/iso/office.js は再エクスポートして使う）＝
// 障害物の矩形と描画物が同じ数字から出る（二重管理でズレると即すり抜けに戻る）。
//
// 設計: 通路の中心線に沿った軸平行レーンのグラフを敷き、歩行は必ず
// 「現在地 → 最寄りノード → グラフ経路 → 目的地」の折れ線を通る。
// 目的地への最終アプローチだけは目的の什器（自分の机・会議卓）へ踏み込む。
// ──────────────────────────────────────────────────────────────

export const LAYOUT = Object.freeze({
  floor: { w: 28.8, d: 19.0, z: -0.6 },
  deskZone: { x: -0.6, z: 0.35, w: 15.8, d: 11.4, lift: 0.12 },
  meetZone: { x: -9.2, z: -7.7, w: 7.2, d: 4.4, lift: 0.22 },
  stageZone: { x: -12.55, z: 0.1, w: 2.9, d: 3.4, lift: 0.12 },   // ソファコーナー
  // R74: 第2↔第3の隙間が 0.70m（ロボの幅とほぼ同じ＝通れない見た目）だったので
  // 第2を浅く・第3を南へ寄せて通路を確保する（ユーザーFB「幅間が狭い」）。
  meet2Zone: { x: 11.3, z: 2.2, w: 4.8, d: 3.2, lift: 0.22 },     // 第2会議室（右中）
  // R70: ラウンジを西へ寄せて（家具は元からx6.5..11.0に集中）東側の空床に第3会議室を新設。
  // 位置はオーバーレイ投影で実測確定（scratchpad/r70_overlay.png・候補A=空床/候補B=タッチダウン干渉）
  loungeZone: { x: 8.6, z: 6.25, w: 4.8, d: 3.4, lift: 0.12 },
  meet3Zone: { x: 13.15, z: 6.7, w: 2.5, d: 2.7, lift: 0.22 },    // 第3会議室（小・右手前・予備）
  // R73: 第4会議室（右奥）＝奥壁のサーバーラック帯（z<=-8.6）と机の島（x<=6.35）の
  // 間に残っていた空床。北通路 ZN=-5.0 の north 側に収める（通路を塞がない）。
  // R73.2: サーバーラック帯の直下（z-8.3..-5.7）は幅2.6しか無く、部屋ではなく
  // 「棚の裏に置いた机」に見えた（ユーザーFB）。ラックの手前に開いている
  // 机の島(x<=7.3)と外部コンソール(x>=13.05)の間の床へ移設＝会議室として成立する広さ。
  meet4Zone: { x: 9.9, z: -2.65, w: 4.0, d: 3.9, lift: 0.22 },    // 第4会議室（右・ラック手前）
  serverZone: { x: 10.2, z: -9.25 },
  queueZone: { x: -2.2, z: 6.0 },
});

/** 壁の位置（床から導出）。壁に付く什器は必ずここから座標を決める。 */
export const WALL = Object.freeze({
  left: -LAYOUT.floor.w / 2,                    // -14.4
  right: LAYOUT.floor.w / 2,                    //  14.4
  back: LAYOUT.floor.z - LAYOUT.floor.d / 2,    //  -10.1
  front: LAYOUT.floor.z + LAYOUT.floor.d / 2,   //    8.9
});

/** 机の島 3列×2行（島1つ=対面2席）。順序= core の席インデックス。 */
export const PODS = Object.freeze([
  [-6.05, -2.35], [-0.6, -2.35], [4.85, -2.35],
  [-6.05, 3.05], [-0.6, 3.05], [4.85, 3.05],
]);

/**
 * 歩行の障害物（axis-aligned 矩形 {x1,z1,x2,z2}）。
 * 通路グラフのエッジはこの矩形群と交差してはならない（nav.test.js が機械強制）。
 * 目的地への最終アプローチは対象の什器へ入るため対象外。
 */
export function obstacleRects() {
  const rects = [];
  const M = 0.10;   // 机まわりの余白（体の厚みぶん）
  for (const [cx, cz] of PODS) {
    // 机の天板 3.0×2.3（椅子・席は天板の外＝席には近づける）
    rects.push({ id: `pod:${cx},${cz}`,
      x1: cx - 1.5 - M, z1: cz - 1.15 - M, x2: cx + 1.5 + M, z2: cz + 1.15 + M });
  }
  const zone = (id, zdef) => rects.push({ id,
    x1: zdef.x - zdef.w / 2, z1: zdef.z - zdef.d / 2,
    x2: zdef.x + zdef.w / 2, z2: zdef.z + zdef.d / 2 });
  zone("meet", LAYOUT.meetZone);
  zone("meet2", LAYOUT.meet2Zone);
  zone("meet3", LAYOUT.meet3Zone);
  zone("meet4", LAYOUT.meet4Zone);
  zone("stage", LAYOUT.stageZone);
  zone("lounge", LAYOUT.loungeZone);
  // ボス壇（5.4×3.5・奥中央）とサーバーラック帯（北壁東側）・タッチダウン・外部コンソール
  rects.push({ id: "boss", x1: -3.3, z1: WALL.back, x2: 2.1, z2: WALL.back + 3.5 });
  rects.push({ id: "server", x1: 5.4, z1: WALL.back, x2: WALL.right, z2: WALL.back + 1.5 });
  rects.push({ id: "touchdown", x1: 2.7, z1: WALL.front - 2.6, x2: 5.7, z2: WALL.front - 0.9 });
  rects.push({ id: "extConsole", x1: 13.6, z1: -5.6, x2: WALL.right, z2: 0.4 });
  return rects;
}

// 通路レーン（x軸/z軸に平行）。値は LAYOUT/PODS の隙間の中心から導出:
//   縦: -8.8=左壁側通路 / -3.3, 2.1=机の列間 / 3.9=コーヒー支線 / 7.4=右通路
//   横: -5.0=北通路（机と会議・ボス壇の間）/ 0.35=机の行間 / 5.3=南通路
const XL = -8.8;
const XA = -3.3;
const XB = 2.1;
const XD = 3.9;
const XC = 7.4;
const ZN = -5.0;
const ZM = 0.35;
const ZS = 5.3;

/** 通路グラフ。nodes[i]=[x,z]・edges=[i,j]（無向・重み=距離）。 */
export function walkGraph() {
  const nodes = [
    [XL, ZN], [XA, ZN], [XB, ZN], [XD, ZN], [XC, ZN], [11.5, ZN],   // 0..5 北通路
    [XL, ZM], [XA, ZM], [XB, ZM], [XC, ZM],                          // 6..9 行間
    [XL, ZS], [XA, ZS], [XB, ZS], [5.9, ZS],                         // 10..13 南通路
    [XL, 7.6],                                                       // 14 エントランス前
    [XD, -7.0],                                                      // 15 コーヒー支線
    [5.9, 5.9],                                                      // 16 ラウンジ口
    [5.9, 8.45], [13.05, 8.45],                                      // 17..18 南辺通路（R70 meet3口）
  ];
  const edges = [
    [0, 1], [1, 2], [2, 3], [3, 4], [4, 5],       // 北通路
    [6, 7], [7, 8], [8, 9],                       // 行間
    [10, 11], [11, 12], [12, 13],                 // 南通路
    [0, 6], [6, 10], [10, 14],                    // 左通路（縦）
    [1, 7], [7, 11],                              // -3.3 縦
    [2, 8], [8, 12],                              // 2.1 縦
    [4, 9],                                       // 7.4 縦
    [3, 15],                                      // コーヒー支線
    [13, 16],                                     // ラウンジ口
    [16, 17], [17, 18],                           // R70: 南辺通路（ラウンジ南→meet3南口）
  ];
  return { nodes, edges };
}

/**
 * R68: 掃除ロボの巡回路（通路グラフのノード座標を辿るループ・最後の点=最初の点）。
 * 北通路→右縦→南通路→左縦→北通路 の外周まわり。障害物と交差しないことを
 * nav.test がピンする（歩行者と同じレーン＝机をすり抜けない）。
 */
export const CLEANER_ROUTE = Object.freeze([
  [XL, ZN], [XA, ZN], [XB, ZN], [XD, ZN], [XC, ZN],   // 北通路を東へ
  [XC, ZM], [XB, ZM], [XB, ZS], [5.9, ZS],            // 行間→南通路を東へ
  [XB, ZS], [XA, ZS], [XL, ZS], [XL, 7.6],            // 南通路を西へ→エントランス前
  [XL, ZM], [XL, ZN],                                 // 左通路を北へ戻る（ループ）
]);

/**
 * R68: ボスの見回り路（壇前→北通路を往復→壇前・一本道の折れ線）。
 * 全区間が既存の北通路レーン(z=-5.0)上＝障害物交差0は nav.test がピン。
 * 先頭/末尾の壇アプローチ（boss矩形へ入る区間）だけは例外扱い。
 */
export const BOSS_WALK = Object.freeze([
  [-0.6, -8.85 + 2.2], [-0.6, ZN],              // 壇の前へ降りる
  [XA, ZN], [XB, ZN], [-0.6, ZN],               // 北通路を往復
  [-0.6, -8.85 + 2.2],                          // 壇へ戻る
]);

/** 待機エージェントの立ち寄り先（開けた床の上＝障害物矩形の外。nav.test がピン）。 */
export const IDLE_SPOTS = Object.freeze([
  { x: 4.65, z: -8.25, yaw: Math.PI, why: "coffee" },      // コーヒーバー
  { x: -10.6, z: 0.9, yaw: -Math.PI / 2, why: "plant" },   // ソファコーナー脇の植物
  { x: -13.3, z: 4.6, yaw: Math.PI / 2, why: "window" },   // 左壁の窓際・ネオンサイン前
  { x: 5.6, z: 5.9, yaw: Math.PI / 4, why: "lounge" },     // ラウンジを覗く
]);

/**
 * R59: 休憩スポット（複数エリアへ分散＝「右手前ラウンジに溜まりすぎ」FBへの対応）。
 * area ごとの席数が容量。ラウンジ/ソファの席はゾーン矩形（=障害物）の中に載るのが意図
 * （最終アプローチは目的の什器へ踏み込む扱い）。bench だけは障害物外の左壁ベンチ。
 * 座標は LAYOUT/WALL からの相対で決める（暗算しない・photoshoot照準は __debugScene.project）。
 */
export const REST_SPOTS = Object.freeze([
  // ラウンジ（右手前・従来のたまり場＝容量3のまま）
  { area: "lounge", x: LAYOUT.loungeZone.x + 1.5, z: LAYOUT.loungeZone.z + 0.7,
    yaw: -1.7, y: LAYOUT.loungeZone.lift + 0.05, role: "tablet" },
  { area: "lounge", x: LAYOUT.loungeZone.x - 1.5, z: LAYOUT.loungeZone.z - 0.35,
    yaw: 0.15, y: LAYOUT.loungeZone.lift },
  { area: "lounge", x: LAYOUT.loungeZone.x - 0.3, z: LAYOUT.loungeZone.z - 0.35,
    yaw: -0.15, y: LAYOUT.loungeZone.lift },
  // ソファコーナー（左中・向かい合わせ2席＝おしゃべりの定位置）
  { area: "sofa", x: LAYOUT.stageZone.x - 0.2, z: LAYOUT.stageZone.z - 0.95,
    yaw: 0, y: LAYOUT.stageZone.lift },
  { area: "sofa", x: LAYOUT.stageZone.x + 0.4, z: LAYOUT.stageZone.z + 1.15,
    yaw: Math.PI, y: LAYOUT.stageZone.lift },
  // 赤黄クッションのソファヌック（左壁・office.js の 3.4m ソファ＝cushionA/B/C の真上）。
  // 座面 slab(0.95,0.40,3.4) は y中心0.24＝天面0.44・x中心 W.left+0.85・クッションは
  // z=3.95/4.90/5.85 の3つ。席はその3点に載せる（cap3・ユーザーFB「あそこも休憩スペースに」）。
  // y=0 の根拠: ソファコーナー（天面0.57）で y=lift(0.12) ＝ 天面-0.45。ここは 0.44-0.45≒0。
  { area: "bench", x: WALL.left + 0.9, z: 3.95, yaw: Math.PI / 2, y: 0 },
  { area: "bench", x: WALL.left + 0.9, z: 4.90, yaw: Math.PI / 2, y: 0 },
  { area: "bench", x: WALL.left + 0.9, z: 5.85, yaw: Math.PI / 2, y: 0 },
]);

/** 線分(a→b)と矩形の交差（端点が矩形内の場合も交差扱い・辺上の掠めは非交差）。 */
export function segIntersectsRect(ax, az, bx, bz, r) {
  // 境界（辺上）を交差に数えない＝矩形をεだけ内側へ縮めて厳密内部で判定する
  const EPS = 1e-6;
  const x1 = r.x1 + EPS;
  const x2 = r.x2 - EPS;
  const z1 = r.z1 + EPS;
  const z2 = r.z2 - EPS;
  const inside = (x, z) => x > x1 && x < x2 && z > z1 && z < z2;
  if (inside(ax, az) || inside(bx, bz)) return true;
  // 各辺との交差判定（軸平行なのでパラメトリックで十分）
  const dx = bx - ax;
  const dz = bz - az;
  let t0 = 0;
  let t1 = 1;
  // Liang–Barsky クリッピング: 交差区間が残れば矩形を通過している
  for (const [p, q] of [
    [-dx, ax - x1], [dx, x2 - ax],
    [-dz, az - z1], [dz, z2 - az],
  ]) {
    if (p === 0) {
      if (q < 0) return false;          // 平行で外側
    } else {
      const t = q / p;
      if (p < 0) t0 = Math.max(t0, t);
      else t1 = Math.min(t1, t);
      if (t0 > t1) return false;
    }
  }
  return t1 - t0 > 1e-9;                // 掠め（接触のみ）は交差扱いしない
}

function nearestNode(nodes, x, z) {
  let best = 0;
  let bestD = Infinity;
  for (let i = 0; i < nodes.length; i++) {
    const d = Math.hypot(nodes[i][0] - x, nodes[i][1] - z);
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}

/**
 * 経路: from → 最寄りノード → （Dijkstra最短） → 目的地最寄りノード → to。
 * 戻り値は [[x,z],...] の折れ線。微小セグメント(<0.15m)は間引く。
 */
export function routePath(from, to, graph = walkGraph()) {
  const { nodes, edges } = graph;
  const adj = nodes.map(() => []);
  for (const [i, j] of edges) {
    const w = Math.hypot(nodes[i][0] - nodes[j][0], nodes[i][1] - nodes[j][1]);
    adj[i].push([j, w]);
    adj[j].push([i, w]);
  }
  const s = nearestNode(nodes, from[0], from[1]);
  const g = nearestNode(nodes, to[0], to[1]);
  // Dijkstra（ノード数~17なので線形探索で十分）
  const dist = nodes.map(() => Infinity);
  const prev = nodes.map(() => -1);
  const done = nodes.map(() => false);
  dist[s] = 0;
  for (;;) {
    let u = -1;
    let best = Infinity;
    for (let i = 0; i < nodes.length; i++) {
      if (!done[i] && dist[i] < best) { best = dist[i]; u = i; }
    }
    if (u < 0) break;
    done[u] = true;
    if (u === g) break;
    for (const [v, w] of adj[u]) {
      if (dist[u] + w < dist[v]) { dist[v] = dist[u] + w; prev[v] = u; }
    }
  }
  const chain = [];
  for (let n = g; n >= 0; n = prev[n]) {
    chain.unshift(nodes[n]);
    if (n === s) break;
  }
  const pts = [[from[0], from[1]], ...chain, [to[0], to[1]]];
  const out = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    const last = out[out.length - 1];
    if (Math.hypot(pts[i][0] - last[0], pts[i][1] - last[1]) >= 0.15) out.push(pts[i]);
  }
  if (out.length === 1) out.push([to[0], to[1]]);
  return out;
}
