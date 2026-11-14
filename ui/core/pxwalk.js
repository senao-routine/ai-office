// R99 Phase 3（C1・C2）: 俯瞰オフィス（ui/core/pxroom.js の固定間取り・論理 184×160）の通路グラフと、
// 人の歩き方を決める純関数。DOM・時刻・乱数には触らない（tools/js_layer_lint.py）＝時刻は引数 t（秒）。
//
// 座標は 2 つある。**外に出すのは左上**（roomLayout の actor.x/y と同じ・描画がそのまま使う）。
// **経路は足元の中心**（左上 + FOOT_CENTER）で引く＝家具の足元（FURNITURE の foot／solid）とロボの足（ROBOT_FOOT）を
// 同じ床の上で比べるため。障害物はロボの足の半幅ぶん膨らませて渡す（中心の線分が膨らませた矩形を横切らない
// ⇔ 足の矩形が家具の足元に一度も重ならない）。
//
// 通路は格子（縦 4 本・横 4 本）で、枠（SLOTS の全区分）と扉は、格子から真っ直ぐ伸びる短い枝で繋ぐ。
// 辺は全部 x か y に平行＝歩く向きは 4 方向（S・N・E・W）のどれかに決まり、座標も整数のまま。
// 枝は自分の枠の家具（`at`＝座る机と椅子・ソファの座面・会議卓・カウンター）だけを横切ってよい（座りに行く）。
// 経路探索そのものは ui/core/nav.js の routePath（任意の単位で動く・届かないと [from, from] を返す）。
//
// 歩き方の掟（R98 の帯＝.claude/rules/ui-2d.md から移植）:
//   - 初めて見えた人（baseline の間）は歩かせない。baseline の後に現れた人だけ扉から歩いて入る
//     （最初に描いた世界が空だったときも baseline のまま＝取得の後に全員が扉から入る嘘を出さない）
//   - t が進まない間（固定時刻 ?t=・停止ボタン・reduced-motion＝frozen）は全員を目的地に置く
//     （時計が進まないので、歩かせると旧位置で止まる）
//   - 巻き戻し（t が出発時刻より前）は目的地
//   - 目的地が変わったら**今いる場所から**引き直す（出発点へ瞬間移動しない）。退場の歩き出しも同じ
//   - 位置は整数・歩行の位相は進んだ距離で決める・向きは区間の主軸の 4 方向。引き直しても位相は**続きから**
//     （旧経路で進んだ距離の端数 d0 を新しい経路へ持ち越す＝引き直した刻みでも歩行コマは +1。0 に戻さない）
//   - 歩く人が居る間の描画は 12fps（ROOM_FPS）で、描画 1 回に歩行コマがちょうど 1 つ進む（WALK_SPEED・WALK_STRIDE）
//   - 消えた人は live のときだけ扉まで歩いて消える。再生（live=false）は帳簿を更新しない
//   - 届かない（planPath が null）なら瞬間移動して数える（stats.unreachable）。永久に止まらない
//
// 歩き出しと着いた瞬間の一拍（R110-S4・本人「歩く速度が速すぎてパッと着く。せっせと働いている感じを」）:
//   - **立ち上がり**: 止まっていた人（帳簿の記録が立っている＝path null）が新しい経路を引いたときだけ、歩き出しを
//     RISE_SEC 遅らせる（経路の t0 を未来に置く）。その間は出発点に立ったまま歩行コマを出さず、drawn.lift = 1
//     （描く y を 1px 上げる＝挨拶の跳ねと同じ口）。stats.walking には数える（描画の刻みは 12fps）。
//     歩いている途中の引き直し（曲がり直し）は立ち上がらない＝位相の「続きから」（d0）を崩さない。扉から入る人は
//     外から歩いて来る＝立ち上がらない。立ち上がりの途中で引き直した人は残りの立ち上がりを持ち越す
//   - **到着の一拍**: 歩いていた人が着いた回の時刻を帳簿の arrivedAt に書き、立っている間は drawn.settled＝
//     着いてからの秒（pxtop.topPose が SETTLE_SEC 未満のあいだ正面の待機コマで一拍置く）。初めて見えた人・固定時刻・
//     古い帳簿・届かずに瞬間移動した人には付けない（settled は null＝止め絵は今の絵のまま）
import { routePath } from "./nav.js";
import { DOOR, ERRAND_SPOTS, FURNITURE, GLASS_OPENING, ROBOT_FOOT, ROOM, SLOTS, footRects } from "./pxroom.js";

/**
 * 描画の刻み（fps）。**誰も歩いていない間は 6**（design/README.md の約束）・**歩いている人（入退場を含む）が居る間だけ 12**。
 * 12 は topPose のコマ送り（3・2・1・0.5fps）と割り切れる＝描画ごとにコマが飛ばない。どちらを使うかは roomFps。
 */
export const ROOM_FPS = Object.freeze({ idle: 6, walk: 12 });
/**
 * 歩く速さ（論理 px／秒）。12fps の描画 1 回で 2px＝歩行の 1 コマ（WALK_STRIDE / 4）ちょうど進む
 *（描画 1 回で歩行コマがちょうど 1 つ進む＝逆回しにも飛びにもならない。歩いている途中で経路を引き直した刻みも同じ＝
 *  posAt の d0。pxwalk.test.js の刻みの門が回して確かめる）。
 * R110-S4: 36 → 24。歩調（1 秒に 3 周＝コマ 4 枚 × 12fps）はそのままで歩幅を縮めた＝足はせっせと動き、扉から机 0 まで
 * 264px を 11 秒で歩く（36 のときは 7.3 秒で「パッと着く」と本人が感じた）。
 */
export const WALK_SPEED = 24;
/** 歩行の 1 周（px）＝4 コマ×2px。phase ＝ ((d0 ＋ 進んだ距離) % WALK_STRIDE) / WALK_STRIDE（d0 は posAt）。 */
export const WALK_STRIDE = 8;
/**
 * 立ち上がり（秒）: 止まっていた人が歩き出す前に、出発点で 1px 浮く間（12fps の 2 刻み）。stepWalkers が新しい経路の t0 を
 * この秒数だけ未来に置く（頭の注記「歩き出しと着いた瞬間の一拍」）。歩いている途中の引き直しには付けない。
 */
export const RISE_SEC = 1 / 6;
/** 立ち上がりの終わり（t0）と今の時刻を比べるときの許し（秒）。描画の刻み（k/12）の浮動小数の誤差で 1 刻み余計に立たない */
const RISE_EPS = 1e-6;

/**
 * 距離を 1e-6px に丸める: 描画の刻み（t0 + k/12）の浮動小数の誤差で 2k が 1.9999… になり、歩行のコマが
 * 1 つ手前に落ちる（描画ごとの +1 が崩れて止まって見える）のを防ぐ。見た目の位置は変わらない。
 */
const roundDist = (d) => Math.round(d * 1e6) / 1e6;
/** 距離 → 歩行の 1 周の中の位置（px・0 以上 WALK_STRIDE 未満・負でも）。 */
const strideOf = (d) => {
  const m = roundDist(d) % WALK_STRIDE;
  return m < 0 ? m + WALK_STRIDE : m;
};

/**
 * この回の描画の刻み（ROOM_FPS のどちらか）。stepWalkers の stats を渡す。歩いている人（入場・退場を含む）が
 * 1 人でも居れば 12、居なければ 6。壊れた入力は 6（静かな方）。
 * @param {{walking?: number}|null|undefined} stats stepWalkers(...).stats
 * @returns {number}
 */
export function roomFps(stats) {
  return Number(stats?.walking) > 0 ? ROOM_FPS.walk : ROOM_FPS.idle;
}
/**
 * 帳簿の記録がこの秒数より古い（または未来の）ときは「続きを歩かない」。停止・非表示タブ・再生から live に
 * 戻った瞬間、その間に起きた入退場を**遅れて演じない**（居る人は目的地へ置き、消えた人は歩かせずに消す）。
 * 帳簿が古い（空の帳簿も書いた時刻で見る）ときは、その回に現れた人も扉から歩かせない（baseline と同じ）。
 * ui/core/pxtop.js の MARK_STALE_SEC と同じ考え。
 */
export const WALK_STALE_SEC = 4;

/** 左上 → 足元の中心のずれ [dx, dy]（ROBOT_FOOT の中心＝[6, 15]）。 */
export const FOOT_CENTER = Object.freeze([ROBOT_FOOT[0] + ROBOT_FOOT[2] / 2, ROBOT_FOOT[1] + ROBOT_FOOT[3] / 2]);
const [FX, FY] = FOOT_CENTER;
/** 障害物を膨らませる幅（ロボの足の半幅・半高） */
const HX = ROBOT_FOOT[2] / 2;
const HY = ROBOT_FOOT[3] / 2;

/** [x, y] か {x, y} を読む（有限の数でなければ null）。 */
function readXY(p) {
  const x = Array.isArray(p) ? p[0] : p?.x;
  const y = Array.isArray(p) ? p[1] : p?.y;
  return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
}

/** 左上 [x, y]（か {x, y}）→ 足元の中心 [x, y]。 */
export function footOf(p) {
  const q = readXY(p);
  return q ? [q[0] + FX, q[1] + FY] : null;
}

/** 足元の中心 [x, y]（か {x, y}）→ 左上 [x, y]。 */
export function topLeftOf(p) {
  const q = readXY(p);
  return q ? [q[0] - FX, q[1] - FY] : null;
}

/**
 * 扉に立つ人の足元の中心（扉の横の中央・扉のタイルの最上行＝壁の帯のすぐ内側の床の上の框）。
 * 足（ROBOT_FOOT・2 行）は y DOOR.y-1..DOOR.y＝壁の帯（y 157..159）に掛からない。
 */
const DOOR_FOOT = Object.freeze([DOOR.x + DOOR.w / 2, DOOR.y]);
/** 扉に立つ人の左上。入る人はここから歩き出し、出る人はここで消える。 */
export const DOOR_XY = Object.freeze(topLeftOf(DOOR_FOOT));

/**
 * 壁（論理 px の矩形 [x, y, w, h]）。奥の壁 y 0..15・左右 3px・手前 y 157..159（ui/cockpit/room.js の paintBack と同じ帯）。
 * 扉は手前の壁の上＝扉に立つ人の足元（y 155..156）は壁に掛からない。
 */
export const WALLS = Object.freeze([
  Object.freeze([0, 0, ROOM.w, 16]),
  Object.freeze([0, 0, 3, ROOM.h]),
  Object.freeze([ROOM.w - 3, 0, 3, ROOM.h]),
  Object.freeze([0, ROOM.h - 3, ROOM.w, 3]),
]);

/**
 * 通路（足元の中心の座標）。どれも家具の足元をロボの足の半幅ぶん避けた床の帯の中＝tests が表と突き合わせる。
 *   x: 左の壁と 1 列目の机の間（6..13）・机の列の間 2 本（49..53・89..93）・3 列目の机とボード／給湯台の間（129..144。
 *      床の縁の線 x 135 の内側＝右通路）
 *   y: 奥（1 段目の椅子の奥・ソファ／会議室の開口／ラックの前＝外部社員の枠の足元と同じ 42。41..46）・
 *      2 段目の椅子の奥（70..73）・3 段目の椅子の奥（97..100）・手前（3 段目の机と受付の間＝124..143）
 */
export const LANES = Object.freeze({
  x: Object.freeze([9, 51, 91, 131]),
  y: Object.freeze([42, 71, 98, 125]),
});
const FRONT_LANE = LANES.y[LANES.y.length - 1];

/** 足元の矩形 [x, y, w, h] → 足元の中心にとっての障害物（ロボの足の半幅ぶん膨らませる・辺の上は通れる） */
const inflate = ([x, y, w, h], id) => Object.freeze({ id, x1: x - HX, z1: y - HY, x2: x + w + HX, z2: y + h + HY });

/**
 * 経路探索の障害物（足元の中心の座標・ui/core/nav.js の形 {x1, z1, x2, z2}）。家具の足元（foot＋solid）と壁。
 * id は家具の id（solid は `id#k`）か `wall#k`。
 */
export const OBSTACLES = Object.freeze([
  ...FURNITURE.flatMap((f) => footRects(f).map((r, k) => inflate(r, k ? `${f.id}#${k}` : f.id))),
  ...WALLS.map((r, k) => inflate(r, `wall#${k}`)),
]);

/**
 * 枠と扉の足元の中心と、そこから通路へ出る道筋（足元の中心の折れ線・先頭が枠・最後が通路の上）。
 *   机      真上（奥）へ＝自分の椅子を越えて、その段の椅子の奥の通路
 *   ソファ  真下（手前）へ＝座面を越えて奥の通路
 *   会議室  卓の奥を横へ（卓の右＝開口の上まで）→ 開口を抜けて奥の通路
 *   ラック前  奥の通路の上に立つ（枝なし）
 *   受付    真上へ手前の通路（カウンターの 3 枠はカウンターを越える）
 *   扉      真上へ手前の通路
 *   本棚の前（R110-S5・用事の行き先 ERRAND_SPOTS の shelf）  右へ (138, 20) → 真下へ奥の通路。足元 (128, 20) の真下は植物
 *           （plantGlass）と会議室の右の柱で塞がっていて、最寄りの通路へ真っ直ぐ出られない（nav.routePath の自動のつなぎが
 *           無い）。枝は家具を越えない（at なし）。ほかの行き先（掲示板・ボード・カウンター）は planPath が最寄りの通路へつなぐ
 */
const ERRAND_VIA = Object.freeze({ shelf: Object.freeze([[138, 20], [138, LANES.y[0]]]) });
function spurs() {
  const out = [];
  const add = (key, at, pts) => out.push({ key, at: Object.freeze([...at]), pts: pts.map((p) => Object.freeze(p)) });
  const f = (s) => [s.x + FX, s.y + FY];
  SLOTS.desk.forEach((s, i) => {
    const [x, y] = f(s);
    add(`desk#${i}`, s.at, [[x, y], [x, LANES.y[Math.floor(i / ROOM.desk.cols)]]]);
  });
  SLOTS.lounge.forEach((s, i) => { const [x, y] = f(s); add(`lounge#${i}`, s.at, [[x, y], [x, LANES.y[0]]]); });
  // 会議室: 卓の奥（枠の足元の行）を開口の中央まで横へ → 開口を抜けて奥の通路。枠どうしは卓の奥の 1 本の線で繋がる
  const gx = GLASS_OPENING[0] + GLASS_OPENING[2] / 2;
  const meet = SLOTS.meeting.map((s, i) => ({ i, p: f(s) })).sort((a, b) => a.p[0] - b.p[0]);
  meet.forEach(({ i, p }, k) => {
    const [x, y] = p;
    const tail = k + 1 < meet.length ? [meet[k + 1].p] : [[gx, y], [gx, LANES.y[0]]];
    add(`meeting#${i}`, SLOTS.meeting[i].at, [[x, y], ...tail]);
  });
  SLOTS.external.forEach((s, i) => { const [x, y] = f(s); add(`external#${i}`, s.at, [[x, y], [x, LANES.y[0]]]); });
  SLOTS.queue.forEach((s, i) => { const [x, y] = f(s); add(`queue#${i}`, s.at, [[x, y], [x, FRONT_LANE]]); });
  add("door", [], [[...DOOR_FOOT], [DOOR_FOOT[0], FRONT_LANE]]);
  for (const s of ERRAND_SPOTS) {
    const via = Object.hasOwn(ERRAND_VIA, s.name) ? ERRAND_VIA[s.name] : null;
    if (via) add(`errand#${s.name}`, [], [f(s), ...via.map((p) => [...p])]);
  }
  return out;
}

/** 通路グラフを組む（読み込み時に 1 回）。ノードは整数・辺は x か y に平行。 */
function buildGraph() {
  const nodes = [];
  const index = new Map();
  const edges = [];
  const seen = new Set();
  const node = ([x, y]) => {
    const k = `${x},${y}`;
    if (!index.has(k)) { index.set(k, nodes.length); nodes.push(Object.freeze([x, y])); }
    return index.get(k);
  };
  const edge = (a, b) => {
    const k = a < b ? `${a}-${b}` : `${b}-${a}`;
    if (a !== b && !seen.has(k)) { seen.add(k); edges.push(Object.freeze([a, b])); }
  };
  // 通路の上の停留点（交差点と、枝が通路に付く点）
  const onY = new Map(LANES.y.map((y) => [y, new Set(LANES.x)]));
  const onX = new Map(LANES.x.map((x) => [x, new Set(LANES.y)]));
  const spots = spurs();
  for (const { pts } of spots) {
    for (let k = 0; k + 1 < pts.length; k += 1) edge(node(pts[k]), node(pts[k + 1]));
    const [x, y] = pts[pts.length - 1];
    if (onY.has(y)) onY.get(y).add(x);
    else if (onX.has(x)) onX.get(x).add(y);
  }
  // 同じ通路の上の停留点を順に繋ぐ（重なる辺を作らない＝nav.routePath の「最寄りの辺」が一意に決まる）
  for (const [y, xs] of onY) {
    const s = [...xs].sort((a, b) => a - b);
    for (let k = 0; k + 1 < s.length; k += 1) edge(node([s[k], y]), node([s[k + 1], y]));
  }
  for (const [x, ys] of onX) {
    const s = [...ys].sort((a, b) => a - b);
    for (let k = 0; k + 1 < s.length; k += 1) edge(node([x, s[k]]), node([x, s[k + 1]]));
  }
  const spot = Object.fromEntries(spots.map((s) => [s.key, Object.freeze({
    foot: s.pts[0], at: s.at, node: index.get(`${s.pts[0][0]},${s.pts[0][1]}`),
  })]));
  return Object.freeze({
    nodes: Object.freeze(nodes), edges: Object.freeze(edges), obstacles: OBSTACLES, spots: Object.freeze(spot),
  });
}

const GRAPH = buildGraph();

/**
 * 部屋の通路グラフ（足元の中心の座標）。ui/core/nav.js の routePath にそのまま渡せる形＋枠の索引:
 *   nodes [[x, y], …] ／ edges [[i, j], …] ／ obstacles [{id, x1, z1, x2, z2}, …]（OBSTACLES）
 *   spots { "desk#0" | … | "queue#8" | "door" | "errand#shelf": {foot: [x, y], at: [家具 id…], node: nodes の添字} }
 * 凍結済み（読むだけ）。読み込み時に 1 回だけ組む。
 */
export function roomGraph() {
  return GRAPH;
}

/** 折れ線から、長さ 0 の区間と、同じ向きに続く途中の点を落とす（通る床は変わらない）。 */
function simplify(pts) {
  const out = [];
  for (const p of pts) {
    const last = out[out.length - 1];
    if (last && last[0] === p[0] && last[1] === p[1]) continue;
    if (out.length >= 2) {
      const a = out[out.length - 2];
      const d1 = [Math.sign(last[0] - a[0]), Math.sign(last[1] - a[1])];
      const d2 = [Math.sign(p[0] - last[0]), Math.sign(p[1] - last[1])];
      const straight = (last[0] - a[0]) * (p[1] - last[1]) === (last[1] - a[1]) * (p[0] - last[0]);
      if (straight && d1[0] === d2[0] && d1[1] === d2[1]) out.pop();
    }
    out.push(p);
  }
  return out;
}

/**
 * 左上 from から左上 to への経路。**左上の座標の折れ線（整数）**を返す。先頭は from・最後は to（どちらも端数は
 * 丸めた点＝入力を丸めてから引く。経路の点も丸める）。
 * 同じ点なら 1 点だけの折れ線。**届かないなら null**（nav.routePath は届かないと [from, from] を返す＝
 * そのまま歩かせると永久に止まる。呼び側は瞬間移動して数える）。入力が数でなくても null。
 * @param {[number, number]|{x:number, y:number}} fromXY 左上
 * @param {[number, number]|{x:number, y:number}} toXY 左上
 * @returns {Array<[number, number]>|null}
 */
export function planPath(fromXY, toXY) {
  const a = readXY(fromXY);
  const b = readXY(toXY);
  if (!a || !b) return null;
  const from = [Math.round(a[0]), Math.round(a[1])];
  const to = [Math.round(b[0]), Math.round(b[1])];
  if (from[0] === to[0] && from[1] === to[1]) return [from];
  const fs = [from[0] + FX, from[1] + FY];
  const ts = [to[0] + FX, to[1] + FY];
  const raw = routePath(fs, ts, GRAPH);
  const end = raw[raw.length - 1];
  if (raw.length < 2 || end[0] !== ts[0] || end[1] !== ts[1]) return null;
  // 格子の上の点は整数。辺の途中へ最短で繋いだ点が端数になったときだけ丸める（丸めで生じた長さ 0 は落とす）
  const pts = simplify(raw.map(([x, y]) => [Math.round(x), Math.round(y)]));
  return pts.map(([x, y]) => [x - FX, y - FY]);
}

/** 区間 a→b の向き（主軸・|dx| と |dy| が同じなら横）。 */
function dirOf(a, b) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? "E" : "W";
  return dy >= 0 ? "S" : "N";
}

/**
 * 折れ線 path を時刻 t0 に歩き出した人の、時刻 t の位置。**距離で弧長パラメータ化**（区間の長さに比例して進む）。
 *   x, y   左上（整数に丸める）
 *   dir    今いる区間の主軸（"S" 手前へ・"N" 奥へ・"E" 右へ・"W" 左へ）。着いたら最後の区間の向き
 *          （1 点だけの折れ線は "S"）
 *   phase  ((d0 ＋ 進んだ距離) % WALK_STRIDE) / WALK_STRIDE（0..1）。着いたら 0
 *   done   着いたか。**t < t0（巻き戻し）・t や t0 が数でない・speed が正でない も着いた扱い**（目的地に置く）
 * @param {Array<[number, number]>} path 左上の折れ線（planPath の戻り値）
 * @param {number} t0 歩き出した時刻（秒）
 * @param {number} t 今の時刻（秒）
 * @param {number} [speed] px／秒（既定 WALK_SPEED）
 * @param {number} [d0] 歩き出す前にすでに進んでいた距離（px・既定 0・数でなければ 0）。**位相だけ**に効く
 *   （位置は path の先頭から）。歩いている途中で経路を引き直した人の歩行コマを続きから回すためのもの
 *   （stepWalkers が帳簿の記録に持たせる）。
 * @returns {{x:number, y:number, dir:"S"|"N"|"E"|"W", phase:number, done:boolean}|null} path が空・壊れているなら null
 */
export function posAt(path, t0, t, speed = WALK_SPEED, d0 = 0) {
  const pts = Array.isArray(path) ? path.map(readXY) : [];
  if (!pts.length || pts.some((p) => !p)) return null;
  const last = pts[pts.length - 1];
  const lens = [];
  let total = 0;
  for (let k = 0; k + 1 < pts.length; k += 1) {
    const l = Math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]);
    lens.push(l);
    total += l;
  }
  let lastDir = "S";
  for (let k = lens.length - 1; k >= 0; k -= 1) {
    if (lens[k] > 0) { lastDir = dirOf(pts[k], pts[k + 1]); break; }
  }
  const arrived = { x: Math.round(last[0]), y: Math.round(last[1]), dir: lastDir, phase: 0, done: true };
  if (!(total > 0) || !Number.isFinite(t0) || !Number.isFinite(t) || !(speed > 0) || t < t0) return arrived;
  // 進んだ距離は 1e-6px に丸める（roundDist）。d0 を足した和も丸め直す（strideOf）
  const dist = roundDist((t - t0) * speed);
  if (!(dist < total)) return arrived;
  let rest = dist;
  let k = 0;
  while (k < lens.length - 1 && rest >= lens[k]) { rest -= lens[k]; k += 1; }
  const a = pts[k];
  const b = pts[k + 1];
  const u = lens[k] > 0 ? rest / lens[k] : 0;
  return {
    x: Math.round(a[0] + (b[0] - a[0]) * u),
    y: Math.round(a[1] + (b[1] - a[1]) * u),
    dir: dirOf(a, b),
    phase: strideOf((Number.isFinite(d0) ? d0 : 0) + dist) / WALK_STRIDE,
    done: false,
  };
}

// ── 歩く人の帳簿 ─────────────────────────────────────────────────────
/**
 * stepWalkers が返した帳簿の印（列挙されない・JSON に出ない）。値は凍結した {t: 書いた時刻, met: 一度でも人を見たか}。
 *   - 印の無い帳簿（null・呼び側が作った {} や Map・写し）は「まだ誰も見ていない」＝baseline の既定が true。
 *     呼び側が {} で始めても、開いた瞬間に全員が扉から歩いて入る（R98 で最初に踏んだ嘘）にならない。
 *   - met が false の帳簿（最初に描いた世界が空＝取得の前・一時的に空が返った）も baseline の既定が true。
 *     空の部屋で開いて、次の取得で全員が届いた瞬間に全員が扉から入る、も同じ嘘なので塞ぐ。
 *     一度でも人を見た帳簿は、全員が出ていって空になっても met のまま＝次に来た人は扉から入る（1 人で使う人の再起動）。
 *   - t は空の帳簿にも残る＝空のまま停止・非表示・再生で長く止まって戻った回も「古い帳簿」として扱える。
 */
const BOOK = Symbol("pxwalk.book");

/**
 * 帳簿の 1 件を読む（壊れた値は null＝初めて見た人と同じ扱い）。帳簿は plain object か Map。
 * 経路の t0 が書いた時刻より RISE_SEC を超えて未来の記録も壊れた扱い（永久に立ち上がったままにしない）。
 * arrivedAt は立っている記録（path null）で、書いた時刻以前の有限の数のときだけ読む（それ以外は null）。
 */
function readRec(book, key) {
  const r = book instanceof Map ? book.get(key) : book && Object.hasOwn(book, key) ? book[key] : null;
  if (!r || typeof r !== "object") return null;
  const target = readXY(r.target);
  if (!target || !Number.isFinite(r.t0) || !Number.isFinite(r.seen)) return null;
  const path = Array.isArray(r.path) && r.path.length && r.path.every(readXY) ? r.path : null;
  if (path && r.t0 - r.seen > RISE_SEC + RISE_EPS) return null;
  const d0 = Number.isFinite(r.d0) && r.d0 >= 0 && r.d0 < WALK_STRIDE ? r.d0 : 0;
  const arrivedAt = !path && Number.isFinite(r.arrivedAt) && r.arrivedAt <= r.seen ? r.arrivedAt : null;
  return { path, t0: r.t0, d0, target, seen: r.seen, leaving: r.leaving === true, enter: r.enter === true, who: r.who || null,
    arrivedAt };
}

const freshAt = (seen, t) => Number.isFinite(seen) && seen <= t && t - seen <= WALK_STALE_SEC;
const fresh = (r, t) => Boolean(r) && freshAt(r.seen, t);
/** 帳簿の印（stepWalkers が返した帳簿だけが持つ）。無い・壊れていれば null。 */
const metaOf = (book) => {
  const m = book && typeof book === "object" ? book[BOOK] : null;
  return m && typeof m === "object" && Number.isFinite(m.t) ? m : null;
};
const same = (p, q) => p[0] === q[0] && p[1] === q[1];
/**
 * 立ち上がりの間か（経路を引いたが、まだ歩き出していない＝t0 が t より RISE_EPS を超えて未来）。1 点だけの経路は立たない。
 * 帳簿の新しい記録の t0 は書いた時刻以前か、立ち上がりの終わり（書いた時刻 ＋ RISE_SEC 以内）だけ＝巻き戻しは fresh が先に落とす。
 */
const rising = (r, t) => Boolean(r) && Array.isArray(r.path) && r.path.length >= 2 && r.t0 - t > RISE_EPS;
/** 歩き出した後の位置を読む時刻（t0 の RISE_EPS 手前までは t0 に寄せる＝posAt の巻き戻しの目的地に化けない） */
const walkClock = (r, t) => Math.max(t, r.t0);
/**
 * 記録の人が時刻 t に居る場所（左上・整数）と、そこまでに進んだ距離の端数（新しい経路へ持ち越す d0）。
 * 端数は歩数計＝(d0 ＋ (t − t0) × 速さ) % WALK_STRIDE（着いた後の刻みも数える＝着いた刻みに引き直しても +1）。
 * 立っている人（path null）は 0＝止まっていた人は歩行の 1 コマ目から歩き出す。立ち上がりの間（t < t0）は経路の出発点に居て、
 * 端数は記録の d0 のまま（まだ 1 歩も進んでいない）。
 */
const nowOf = (r, t) => {
  if (r.path && t < r.t0) {
    const [x, y] = readXY(r.path[0]);
    return { here: [Math.round(x), Math.round(y)], d0: r.d0 };
  }
  const p = r.path ? posAt(r.path, r.t0, t) : null;          // 位置だけ読む（d0 は位相にしか効かない）
  const here = p ? [p.x, p.y] : [Math.round(r.target[0]), Math.round(r.target[1])];
  const d0 = r.path ? strideOf(r.d0 + roundDist((t - r.t0) * WALK_SPEED)) : 0;
  return { here, d0 };
};
/**
 * 新しい経路の歩き出す時刻（t0）。止まっていた人（記録が立っている）は RISE_SEC 後（立ち上がり）・歩いていた人は今すぐ
 *（曲がり直しに間を入れない＝位相の続き）・立ち上がりの途中の人は残りを持ち越す。1 点だけの経路（動かない）は今。
 */
const departAt = (rec, t, path) => {
  if (!Array.isArray(path) || path.length < 2) return t;
  if (rec.path) return rising(rec, t) ? rec.t0 : t;
  return t + RISE_SEC;
};
/** 着いてからの秒（1e-6 秒に丸める＝描画の刻みの誤差で SETTLE の境目が 1 刻みずれない）。起点が無ければ null */
const settledOf = (arrivedAt, t) => (Number.isFinite(arrivedAt) ? Math.round((t - arrivedAt) * 1e6) / 1e6 : null);
const freezePath = (path) => (path ? Object.freeze(path.map((p) => Object.freeze([p[0], p[1]]))) : null);

/**
 * 1 フレームぶん、誰がどこを歩いているかを決める。**同じ入力なら必ず同じ結果**・入力（prev・actors）は書き換えない。
 *
 * 呼び方（1 フレーム・ui/cockpit/room.js）: `const w = stepWalkers(book, layout.actors, t, {live, frozen, baseline});
 * book = w.walkers;` 帳簿の初期値は null。w.drawn を足元の y の順に描き、w.stats を roomFps に渡して次の刻みを決める。
 *   - t が進まない間（prefers-reduced-motion の t 固定・停止ボタン・`?t=`）は frozen: true（全員を目的地に置く）。
 *     再生・非表示タブは live: false（帳簿に触らない）。
 *   - 最初の取得が成功するまでは baseline: true（取得前の空の世界を「最初に見た世界」にしない。帳簿の met でも塞いである）。
 *
 * @param {object|Map|null|undefined} prev 前回の戻り値の walkers（帳簿）。初回は null（stepWalkers が返したもの以外は
 *   「まだ誰も見ていない」扱い＝baseline の既定が true）。
 *   形は { [actor.key（無ければ session か id。同じものが 2 度出たら `#n` を付ける＝roomLayout と同じ）]:
 *   {path: 左上の折れ線|null, t0, d0, target: [x, y], leaving, enter, seen, who, arrivedAt} }（path null ＝目的地に立っている・
 *   d0 ＝引き直す前に進んでいた距離の端数＝posAt の d0。0 以上 WALK_STRIDE 未満・壊れていれば 0・
 *   t0 が seen より未来＝立ち上がりの間（RISE_SEC まで）・arrivedAt ＝歩いて着いた回の時刻（立っている間だけ・無ければ null））
 * @param {object[]} actors roomLayout(...).actors（x, y＝目的地の左上・key・session・id を読む）
 * @param {number} t 秒（UI クロック）
 * @param {{frozen?: boolean, live?: boolean, baseline?: boolean}} [opts]
 *   frozen   t が進まない間（固定時刻 ?t=・停止ボタン・reduced-motion）。全員を目的地に置く（退場の歩行も出さない）。
 *            live なら帳簿も目的地で書き直す
 *   live     実時間の描画か（既定 false）。false（再生・非表示）は全員を目的地に置き、**walkers は prev をそのまま（同じ参照）返す**
 *   baseline 初めて見えた人を歩かせない間か。省略すると「prev が stepWalkers の返した帳簿でない（初回）か、
 *            その帳簿がまだ一度も人を見ていない（最初の世界が空だった）とき」
 * @returns {{walkers: object, drawn: object[], stats: {walking:number, entering:number, leaving:number, unreachable:number}}}
 *   drawn: 描く人（actors の順・その後に退場中の人＝帳簿の順）。各要素は凍結した actor の写し＋
 *     {key, x, y（今の左上・整数）, walk: null|{dir, phase}（topPose の opts.walk にそのまま渡せる）, entering, leaving,
 *      lift: 0|1（立ち上がりの間だけ 1＝topPose の opts.lift へ）, settled: null|秒（歩いて着いてからの秒＝topPose の opts.settled へ）}。
 *     退場中の人は、消える前に最後に見えた actor の写し（leaving: true）＝印も挙手も出さない（roomMark が null を返す）
 *   stats: walking＝歩いている人数（入場・退場・立ち上がりの間を含む）／entering＝扉から入ってくる途中の人数／
 *     leaving＝扉へ向かう途中の人数（立ち上がりの間を含む）／
 *     unreachable＝この回に届かなかった数（その人は目的地へ瞬間移動・退場なら即座に消える）
 */
export function stepWalkers(prev, actors, t, opts = {}) {
  const o = opts && typeof opts === "object" ? opts : {};
  const time = Number(t);
  // 時刻が数でないときは再生と同じ扱い（seen に NaN を書くと、次の回に帳簿が全部壊れた値として読める）
  const live = o.live === true && Number.isFinite(time);
  const frozen = o.frozen === true;
  const book = prev && typeof prev === "object" ? prev : null;
  const list = Array.isArray(actors) ? actors : [];
  const stats = { walking: 0, entering: 0, leaving: 0, unreachable: 0 };
  const drawn = [];
  const stand = (a, key, settled = null) => Object.freeze({ ...a, key, x: Math.round(a.x), y: Math.round(a.y),
    walk: null, entering: false, leaving: false, lift: 0, settled });
  // 立ち上がりの間の人（経路の出発点に立ち、1px 浮く・歩行コマは無い）
  const risen = (a, key, r, leaving) => {
    const [x, y] = readXY(r.path[0]);
    return Object.freeze({ ...a, key, x: Math.round(x), y: Math.round(y), walk: null, entering: r.enter === true, leaving,
      lift: 1, settled: null });
  };

  // 並び＝actors の順。キーは roomLayout が振った actor.key（無ければ session か id）。同じキーが 2 度出たら
  // 空いている #n を付けて別人にする（片方の帳簿が消えない・roomLayout の key と混ざっても衝突しない）
  const keyed = [];
  const used = new Set();
  for (const a of list) {
    if (!a || typeof a !== "object" || !readXY(a)) continue;
    const base = typeof a.key === "string" && a.key ? a.key : String(a.session || a.id || "");
    let key = base;
    for (let n = 1; used.has(key); n += 1) key = `${base}#${n}`;
    used.add(key);
    keyed.push([key, a]);
  }

  // 再生（live でない）: 帳簿に触らない＝全員を目的地へ・walkers は受け取ったものをそのまま返す
  if (!live) return { walkers: prev, drawn: keyed.map(([key, a]) => stand(a, key)), stats };

  const keys = book instanceof Map ? [...book.keys()] : book ? Object.keys(book) : [];
  const meta = metaOf(book);
  // 初めて見えた人を歩かせない回: baseline（省略時は、stepWalkers が返した帳簿がまだ無い＝初回か、
  // その帳簿がまだ一度も人を見ていない＝最初の世界が空だった）か、帳簿が古い（停止・非表示・再生から戻った）とき。
  // 帳簿の古さは印の時刻で見る＝空の帳簿も古くなる。印の無い帳簿（呼び側の Map など）は記録の seen で見る
  const baseline = typeof o.baseline === "boolean" ? o.baseline : !(meta && meta.met === true);
  const stale = meta ? !freshAt(meta.t, time) : keys.length > 0 && !keys.some((k) => fresh(readRec(book, k), time));
  const quiet = baseline || stale;
  const next = Object.create(null);
  const met = Boolean(meta && meta.met === true) || keyed.length > 0;
  Object.defineProperty(next, BOOK, { value: Object.freeze({ t: time, met }) });
  const put = (key, r) => {
    next[key] = Object.freeze({ path: freezePath(r.path), t0: r.t0, d0: r.path && Number.isFinite(r.d0) ? r.d0 : 0,
      target: Object.freeze([...r.target]), leaving: r.leaving === true, enter: r.enter === true, seen: time, who: r.who,
      arrivedAt: !r.path && Number.isFinite(r.arrivedAt) ? r.arrivedAt : null });
  };

  for (const [key, a] of keyed) {
    const target = [Math.round(a.x), Math.round(a.y)];
    const rec = readRec(book, key);
    let r;
    if (frozen || !fresh(rec, time)) {
      if (!frozen && !rec && !quiet) {
        // baseline の後に現れた人: 扉から歩いて入る（外から歩いて来る＝立ち上がらない）
        const path = planPath(DOOR_XY, target);
        if (path) r = { path, t0: time, target, enter: true };
        else stats.unreachable += 1;
      }
    } else if (!rec.leaving && same(rec.target, target)) {
      r = rec;                                    // 同じ目的地へ歩き続ける（か、立っている・立ち上がっている）
    } else {
      // 目的地が変わった（退場の途中で戻ってきた人も）: 今いる場所から引き直す・歩行の位相は続きから。
      // 立っていた人だけ立ち上がりの間を置く（departAt）
      const now = nowOf(rec, time);
      const path = planPath(now.here, target);
      if (path) r = { path, t0: departAt(rec, time, path), d0: now.d0, target, enter: false };
      else stats.unreachable += 1;
    }
    if (rising(r, time)) {
      put(key, { ...r, who: a });
      stats.walking += 1;
      if (r.enter) stats.entering += 1;
      drawn.push(risen(a, key, r, false));
      continue;
    }
    const p = r?.path ? posAt(r.path, r.t0, walkClock(r, time), WALK_SPEED, r.d0) : null;
    if (!p || p.done) {
      // 歩いていた人がこの回に着いた＝到着の一拍の起点。立っている人は前回の起点を持ち越す（初めて見えた人・固定時刻・
      // 古い記録・届かずに瞬間移動した人は起点なし＝一拍を置かない）
      const arrivedAt = r?.path ? time : r === rec && rec ? rec.arrivedAt : null;
      put(key, { path: null, t0: time, target, who: a, arrivedAt });
      drawn.push(stand(a, key, settledOf(arrivedAt, time)));
      continue;
    }
    put(key, { ...r, who: a });
    stats.walking += 1;
    if (r.enter) stats.entering += 1;
    drawn.push(Object.freeze({ ...a, key, x: p.x, y: p.y, walk: Object.freeze({ dir: p.dir, phase: p.phase }),
      entering: r.enter === true, leaving: false, lift: 0, settled: null }));
  }

  // 消えた人: live のときだけ、今いる場所から扉まで歩いて消える（固定時刻・停止・古い記録は歩かせずに消す）。
  // 席に立っていた人は立ち上がってから歩き出す（departAt）
  if (!frozen) {
    const present = new Set(keyed.map(([key]) => key));
    for (const key of keys) {
      if (present.has(key)) continue;
      const rec = readRec(book, key);
      if (!fresh(rec, time) || !rec.who || typeof rec.who !== "object") continue;
      let r = rec;
      if (!rec.leaving) {
        const now = nowOf(rec, time);                // 歩いている途中で消えた人は、歩行の位相も続きから
        const path = planPath(now.here, DOOR_XY);
        if (!path) { stats.unreachable += 1; continue; }
        r = { path, t0: departAt(rec, time, path), d0: now.d0, target: [...DOOR_XY], leaving: true, who: rec.who };
      }
      if (rising(r, time)) {
        put(key, r);
        stats.walking += 1;
        stats.leaving += 1;
        drawn.push(risen(r.who, key, r, true));
        continue;
      }
      const p = r.path ? posAt(r.path, r.t0, walkClock(r, time), WALK_SPEED, r.d0) : null;
      if (!p || p.done) continue;                 // 扉に着いた＝消える（帳簿からも落とす）
      put(key, r);
      stats.walking += 1;
      stats.leaving += 1;
      drawn.push(Object.freeze({ ...r.who, key, x: p.x, y: p.y, walk: Object.freeze({ dir: p.dir, phase: p.phase }),
        entering: false, leaving: true, lift: 0, settled: null }));
    }
  }
  return { walkers: next, drawn, stats };
}
