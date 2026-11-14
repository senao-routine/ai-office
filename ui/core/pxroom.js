// R99: コックピットの俯瞰オフィス（論理 184×160・×2〜×4 の整数倍）の間取りと配置を決める純関数。
//
// R98 の帯（ui/core/stripLayout.js）との違いは視点だけではない。帯は「1 行の情報」だったが、
// こちらは**部屋そのもの**＝机・受付・ラウンジ・会議室が固定の場所に在り、人がそこへ入る。
// なので間取りは可変幅ではなく**固定**（整数倍の掟があり、割合で縮めるとドットが滲む。
// 倍率は形状で選ぶ整数＝roomScale。×2 でも入らない画面は CSS が横スクロールで逃がす）。
//
// ここが決めるのは「誰がどこに立つか」「家具がどこに在るか」「誰が何色か（添字）」だけ。**どう見えるか**は
// ui/cockpit/room.js、**どのコマか・どの印か**は ui/core/pxtop.js、**どう歩くか**は ui/core/pxwalk.js。
// DOM・時刻・乱数には触らない（tools/js_layer_lint.py）。
//
// 間取り（論理 px・左上原点）:
//   y 0–15    壁（窓 3 枚・時計・掲示板）
//   y 16–43   奥: ソファ（ラウンジ）／ ガラスの会議室 ／ ラック・ロッカー（前に外部社員が立つ）
//   y 45–126  仕事場 3×3 ＝ 9 席。右 x135 から先は通路と給湯・ボード
//   y 128–    手前: 受付カウンター（確認待ちが並ぶ）
//   y 157–159 手前の壁。右寄り x 150..167 に扉（人はここから入って出る）
// 人が歩く通路と歩き方は ui/core/pxwalk.js（家具の足元はこの表の foot／solid が正本）。
import { needsAttention, vendorOf, zoneOf as worldZoneOf } from "./world.js";
import { activityKind, roomArchetype } from "./archetype.js";
import { sessionKind, withKindFields } from "./kind.js";
import { isReadVerb, TOP_READ_TOOLS } from "./pxtop.js";
// errand.js も pxroom を import する（tintHash）＝循環。どちらも相手の値を**関数の中でだけ**読む（読み込み時には触らない）ので
// ESM の循環で壊れない。読み込み時に使う値（定数）をここで errand から取らないこと。
import { evidenceTime } from "./errand.js";

/** 部屋の寸法と、机の並びの決め（design/room.js と同じ数字＝見た目の検討はあちらで回す） */
export const ROOM = Object.freeze({
  w: 184,
  h: 160,
  // 既定の倍率（3 カラムの ×3＝552×480・golden cockpit.png）。形状ごとの倍率は roomScale が決める
  scale: 3,
  robot: Object.freeze({ w: 12, h: 16 }),
  desk: Object.freeze({
    cols: 3, rows: 3, w: 30, h: 7,
    // 縦 27 = 天板・引き出し 15 ＋ 上半身 11 ＋ 段間 1。26 を切ると引き出しが次の段の人と重なる。
    pitchX: 40, pitchY: 27, gridX: 16, gridY: 45, sitDy: 11,
  }),
});

/** 席数。これを超えたセッションは受付の standby へ回す（台帳には全員出る）。 */
export const SEATS = ROOM.desk.cols * ROOM.desk.rows;

/**
 * 人（12×16 のセル）の足の矩形。セルの左上からの相対 [x, y, w, h]＝下 2 行の両足（cells.js の脚の列）。
 * 家具の足元（FURNITURE[].foot）とこれが重なったら、人が家具にめり込んで見える。
 */
export const ROBOT_FOOT = Object.freeze([3, 14, 6, 2]);

/**
 * 扉（手前の壁の右寄り・タイル door 18×4＝委譲の契約・論理 (150,156)）。**寸法と位置の正本**（tools/px_lint.py の ⑥ と
 * tests/pxroom_tiles.test.mjs が door タイルと突き合わせる）。タイルの最上行（y 156）は床の上の框（鋼の線）、
 * 下の 3 行（y 157..159）は壁の帯の中の開口（マットは最下の行）。
 * 人は扉から入って扉から出る（ui/core/pxwalk.js の DOOR_XY＝足元の中心は最上行の中央）。床の障害物ではない（foot なし）。
 */
export const DOOR = Object.freeze({ x: 150, y: 156, w: 18, h: 4 });

/**
 * 会議室のガラス（タイル glassWallDoor 64×26・論理 (58,16)）の手前の框の開口 [x, y, w, h]。**開口の正本**。
 * タイル内 x 50..61（委譲の契約＝pxroom.test.js がピン留め）＝論理 x 108..119・y は框の行（足元の 2 行）。
 * 足元（foot／solid）はここを空けて切り分けてある（開口から会議室の卓の奥へ歩いて入る）。ドットの開口が
 * これと一致するかは tools/px_lint.py の ⑥ と tests/pxroom_tiles.test.mjs が見る。
 */
export const GLASS_OPENING = Object.freeze([108, 38, 12, 2]);

/**
 * i 番目の席。`robot` は座る人の左上（上半身 11px だけが天板の上に出る）。
 * @param {number} i 0..SEATS-1
 */
export function deskAt(i) {
  const D = ROOM.desk;
  const col = i % D.cols;
  const row = Math.floor(i / D.cols);
  const x = D.gridX + col * D.pitchX;
  const y = D.gridY + row * D.pitchY;
  return { i, x, y: y + D.sitDy, w: D.w, h: D.h, robot: { x: x + 5, y } };
}

/** 席・ソファ・会議室の座標表（描画側が「空席の椅子」を描くために使う） */
export const DESKS = Object.freeze(
  Array.from({ length: SEATS }, (_, i) => Object.freeze(deskAt(i))));

// ── 家具の配置表（正本）────────────────────────────────────────────────
// 描画（ui/cockpit/room.js）が手で打っていた座標をここへ移した。経路探索と px_lint が
// 「どこに物が在るか」を同じ表から読めるようにするため（手打ちが 2 か所にあると、7/30 の
// 机ずれと同じく片方だけ直って黙ってずれる）。
//
// 1 行 = 1 枚のタイル。**同じ層の中では表の順が塗る順**（後の行が上に重なる）。
//   tile   ui/cockpit/tiles.js の PX_TILES の名前
//   layer  'back' ＝人の後ろ（背景に 1 回だけ焼く）／ 'front' ＝人の手前（腰から下を隠す）
//   paint  null ＝ TILE_PALETTE、'glass' ＝窓・ガラス、'paper' ＝紙を shell[1] に落とす、'floor' ＝床の石
//   foot   床の上で物が占める矩形 [x, y, w, h]（タイルの落ち影の行を除いた体）。null ＝床に足が無い
//          （壁掛け・吊り灯・敷物＝tests の許可リストに理由つきで載せる／天板の上の小物＝`on` を書く）。
//          ガラスの会議室だけは囲いの框（内側は会議室の床）。foot は手前の框の開口より左
//   solid  foot の外にも床を塞ぐ矩形がある家具だけ（ガラスの会議室＝開口より右の框・左右の柱）。
//          経路探索・めり込み・重なりの検査は foot と solid の全部を見る（footRects）
//   seat   机に付いている物の席番号（椅子・机・モニタ・机上の小物）
//   lit    在席のときに差し替えるタイル（モニタ）
//   on     天板の上の小物が載っている家具の id（foot は null・床の障害物は載せ台の foot が受け持つ）
//   glow   この行のタイルの**直前**に塗る光の矩形 [[dx, dy, w, h, alpha, 段], …]。
//          座標は行の (x, y) からの相対（描画側は (f.x + dx, f.y + dy) に塗る＝家具を動かすと光も付いて動く）。
//          段は RAMP.amber の添字（暖色の光だけ）。`lit` を持つ行の glow は**点灯しているときだけ**塗る
//          （消えた画面は光らない）。先に置くと台の不透明なドットで隠れる光もあるので、順番は変えない。
//
// 表に入れない手続きの塗り（描画側に残す）: back の表より前に床の目地・壁・窓の光・通路の縁。
// 在席の机の床の光だまり（DESKS から出す・毎フレーム）。
const F = (id, tile, x, y, layer, paint, foot, extra = {}) => Object.freeze({
  id, tile, x, y, layer, paint, foot: foot ? Object.freeze(foot) : null, ...extra,
  ...(extra.glow ? { glow: Object.freeze(extra.glow.map((g) => Object.freeze([...g]))) } : {}),
  ...(extra.solid ? { solid: Object.freeze(extra.solid.map((r) => Object.freeze([...r]))) } : {}),
});

/**
 * 家具が床で占める矩形の全部（foot ＋ solid）。足元の無い家具は空配列。
 * 経路探索（ui/core/pxwalk.js）と、めり込み・重なりの検査はこれを読む（foot だけを見ると開口の右の框が抜ける）。
 * @returns {ReadonlyArray<ReadonlyArray<number>>} [[x, y, w, h], …]
 */
export function footRects(f) {
  return f && f.foot ? [f.foot, ...(f.solid || [])] : [];
}

/** 光の矩形 1 枚 [dx, dy, w, h, alpha, 段]（段＝RAMP.amber の添字） */
const G = (dx, dy, w, h, alpha, step) => [dx, dy, w, h, alpha, step];

/** 机の上の小物（全席に同じ物を反復しない＝design/README.md の掟）。席番号 → [id, tile, dx, dy] */
const DESK_ITEMS = Object.freeze({
  0: [["deskMug0", "mug", 1, -3]],
  3: [["deskBooks3", "books", 0, -3]],
  5: [["deskPaper5", "paper", 1, -2]],
  7: [["deskMug7", "mug", 1, -3]],
});

export const FURNITURE = Object.freeze([
  F("window0", "window", 6, 1, "back", "glass", null),
  F("window1", "window", 36, 1, "back", "glass", null),
  F("window2", "window", 66, 1, "back", "glass", null),
  F("wallClock", "wallClock", 109, 2, "back", null, null),
  F("bookshelf", "bookshelf", 121, 1, "back", null, null),
  F("bulletin", "bulletin", 138, 1, "back", "paper", null),
  // 扉は壁の帯の上（壁は表より前の手続きの塗り＝その後に置く）。人が通り抜ける＝足元なし
  F("door", "door", DOOR.x, DOOR.y, "back", null, null),
  F("rug", "rug", 4, 17, "back", null, null),
  F("sofaBack", "sofaBack", 6, 22, "back", null, [6, 22, 40, 8]),
  // 会議室の囲い。手前の框の x 108..119（GLASS_OPENING）が開口＝足元はそこを空けて切り分ける:
  //   foot＝框の開口より左 ／ solid＝框の開口より右・左右の柱（框の上まで）
  F("glassWall", "glassWallDoor", 58, 16, "back", "glass", [58, 38, 50, 2], {
    solid: [[120, 38, 2, 2], [58, 16, 1, 22], [121, 16, 1, 22]],
  }),
  F("plantLounge", "plant", 48, 22, "back", null, [48, 22, 8, 13]),
  F("plantGlass", "plant", 124, 23, "back", null, [124, 23, 8, 13]),
  F("rackOn", "rackOn", 145, 18, "back", null, [145, 18, 9, 21]),
  F("rackOff", "rackOff", 155, 18, "back", null, [155, 18, 9, 21]),
  F("locker", "locker", 166, 16, "back", null, [166, 16, 13, 22]),
  F("whiteboard", "whiteboard", 147, 45, "back", "paper", [147, 45, 24, 16]),
  // 給湯台の床の暖色（ペンダントの光だまり）。台より先に塗る＝台の上には乗らない
  F("coffeeStation", "coffeeStation", 147, 76, "back", null, [147, 76, 24, 21], {
    glow: [G(1, -4, 16, 14, 0.13, 2), G(0, 24, 22, 3, 0.13, 2), G(3, 27, 20, 2, 0.13, 1)],
  }),
  F("plantAisle", "plant", 169, 110, "back", null, [169, 110, 8, 13]),
  F("pendant", "pendant", 151, 66, "back", null, null),
  ...DESKS.map((d) => F(`chair${d.i}`, "chair", d.robot.x + 1, d.y - 9, "back", null,
    [d.robot.x + 1, d.y - 9, 10, 9], { seat: d.i })),

  F("sofaSeat", "sofaSeat", 6, 31, "front", null, [6, 31, 40, 8]),
  F("meetTable", "meetTable", 70, 30, "front", null, [70, 30, 40, 8]),
  F("meetPaper", "paper", 75, 29, "front", null, null, { on: "meetTable" }),
  F("meetMug", "mug", 100, 28, "front", null, null, { on: "meetTable" }),
  F("counter", "counter", 8, 145, "front", null, [8, 145, 56, 10]),
  F("counterBooks", "books", 10, 142, "front", null, null, { on: "counter" }),
  // 卓上灯がカウンターに落とす光（灯の足元の左 1px から 6px）
  F("taskLamp", "taskLamp", 58, 136, "front", null, null, { on: "counter", glow: [G(-1, 9, 6, 1, 0.35, 2)] }),
  ...DESKS.flatMap((d) => [
    F(`desk${d.i}`, "desk", d.x, d.y, "front", null, [d.x, d.y, 30, 13], { seat: d.i }),
    // 点いた画面の光が天板に乗る 1px（机の後・モニタの前＝先に置くと天板に隠れ、後に置くとモニタの脚に乗る）
    F(`monitor${d.i}`, "monitorOff", d.x + 18, d.y - 9, "front", null, null,
      { seat: d.i, lit: "monitorOn", on: `desk${d.i}`, glow: [G(-1, 9, 11, 1, 0.35, 2)] }),
    ...(DESK_ITEMS[d.i] || []).map(([id, tile, dx, dy]) =>
      F(id, tile, d.x + dx, d.y + dy, "front", null, null, { seat: d.i, on: `desk${d.i}` })),
  ]),
]);

/**
 * 手前の什器（layer 'front'）を「段」に分けたもの（C3 の重なり順）。描画は段ごとに焼き（在席の集合が変わったとき
 * だけ焼き直す）、人と段を**奥から順に交互に**描く。
 *   key    段の名前: 机の段 "desk0".."desk2"（seat を持つ行＝机・モニタ・机上の小物）／それ以外は載せ台の id
 *          （"sofaSeat"・"meetTable"・"counter"。`on` を持つ小物は載せ台の段）
 *   depth  段の家具の足元（footRects）の下端（y+h の最大・論理 px）
 *   ids    段に入る家具の id（FURNITURE の順＝段の中の塗る順）
 * 並びは depth の昇順（奥 → 手前）。**人は足元の中心の y（左上 y + 15＝ROBOT_FOOT の中心・pxwalk.footOf）が
 * depth より小さければ段より先（奥）に、そうでなければ段の後（手前）に描く**＝座っている人・卓やカウンターの
 * 向こうに立つ人は腰から下が隠れ、通路を歩く人は什器の手前に出る（pxroom.test.js が全ての枠と通路で確かめる）。
 */
export const FRONT_BANDS = (() => {
  const byId = new Map(FURNITURE.map((f) => [f.id, f]));
  const bandOf = (f) => {
    if (Number.isInteger(f.seat)) return `desk${Math.floor(f.seat / ROOM.desk.cols)}`;
    if (f.on !== undefined) return bandOf(byId.get(f.on));
    return f.id;
  };
  const bands = new Map();
  for (const f of FURNITURE) {
    if (f.layer !== "front") continue;
    const key = bandOf(f);
    if (!bands.has(key)) bands.set(key, { key, depth: -Infinity, ids: [] });
    const b = bands.get(key);
    b.ids.push(f.id);
    for (const [, y, , h] of footRects(f)) b.depth = Math.max(b.depth, y + h);
  }
  return Object.freeze([...bands.values()]
    .sort((p, q) => p.depth - q.depth || (p.key < q.key ? -1 : 1))
    .map((b) => Object.freeze({ key: b.key, depth: b.depth, ids: Object.freeze(b.ids) })));
})();

// ── 人の立ち位置（枠）───────────────────────────────────────────────
// `at` ＝この枠の人が重なってよい家具の id（座る・卓やカウンターの向こうに立つ＝手前の什器が
// 腰から下を隠す）。それ以外の家具の足元とは重ならない（tests が FURNITURE と突き合わせる）。
const slot = (x, y, at = []) => Object.freeze({ x, y, at: Object.freeze(at) });

export const SLOTS = Object.freeze({
  /** 机（席番号＝添字）。座る人の左上。 */
  desk: Object.freeze(DESKS.map((d) => slot(d.robot.x, d.robot.y, [`desk${d.i}`, `chair${d.i}`]))),
  /** 受付の待ち位置（前から順に）。先頭 3 枠はカウンターの向こう、残りは手前の列。 */
  queue: Object.freeze([
    slot(15, 132, ["counter"]), slot(32, 132, ["counter"]), slot(49, 132, ["counter"]),
    slot(76, 134), slot(93, 134), slot(110, 134), slot(127, 134), slot(144, 134), slot(161, 134),
  ]),
  /** ソファの座り位置（休憩）。 */
  lounge: Object.freeze([slot(10, 19, ["sofaSeat"]), slot(27, 19, ["sofaSeat"])]),
  /** ガラスの会議室の立ち位置（サブエージェントが動いている人）。卓の内側に収める。 */
  meeting: Object.freeze([
    slot(71, 18, ["meetTable"]), slot(84, 18, ["meetTable"]), slot(97, 18, ["meetTable"]),
  ]),
  /** 外部社員（OpenClaw 等・別の Mac の稼働体）はインフラ区画＝ラックとロッカーの前に立つ。 */
  external: Object.freeze([slot(138, 27), slot(151, 27), slot(164, 27)]),
});

/**
 * 散歩先（R99-P4-L2・ui/core/life.js）＝返事を 1 分以上待っている机の人が、ときどき立ち寄る場所（人の左上・論理 px）。
 * 区分でも枠でもない（roomLayout は使わない＝席の割り当て・人数を変えない）。どの枠・家具の足元・散歩先同士とも重ならず、
 * 全部の机と扉から歩いて届く（ui/core/life.test.js・pxwalk.test.js の枠の組の門が見る）。
 * どれも扉から全部の枠への通り道に掛からない（入退場の人が散歩中の人に重ならない＝life.test.js の門）。
 * 最初の案（給湯台の前 134,71・ボードの左 134,46・植物の横 154,103）は縦の通路と扉の通り道の上だった（検収のレビューで見つけた）。
 */
export const LIFE_SPOTS = Object.freeze([
  Object.freeze({ x: 138, y: 72, name: "coffee" }),       // 給湯台の左脇
  Object.freeze({ x: 156, y: 48, name: "board" }),        // ホワイトボードの正面
  Object.freeze({ x: 163, y: 92, name: "plant" }),        // 通路の植物の上手
]);

/**
 * 用事の行き先（R110-S5・ui/core/errand.js）＝働いている机の人が、いま使っている道具に連動して立ち寄る場所（人の左上・論理 px）。
 * name は errand.js の ERRAND_SPOT_OF の値と EVIDENCE_ERRAND.spot（行き先の種類）。契約は LIFE_SPOTS と同じ: 区分でも枠でもない
 *（roomLayout は使わない＝席の割り当て・人数を変えない）・どの枠・家具の足元・散歩先・ほかの行き先とも重ならない・全部の机と
 * 扉から歩いて届く・扉から全部の枠への通り道に掛からない（ui/core/life.test.js・pxwalk.test.js の門が見る）。
 *   shelf     本棚（壁掛け・FURNITURE bookshelf）の前＝いま読んでいる（Read・Grep・Glob・Explore）。足元 (128, 20) は植物と
 *             会議室の柱の奥＝最寄りの通路へ真っ直ぐ出られない＝ui/core/pxwalk.js の spurs() に枝（(138, 20) → 奥の通路）
 *   bulletin  掲示板の前＝外の情報（WebFetch・WebSearch）を見に行く（窓際はソファ・植物・ガラスで塞がっていて歩いて行けない）
 *   board     ホワイトボードの左手前＝段取りを書く（TodoWrite）。散歩の「眺めて待つ」(156, 48) とは別の点
 *   counter   受付カウンターの右端＝commit・テストの成功を紙にして出す（紙は EVIDENCE_TRAY に置く）。y は手前の列（受付の
 *             枠 3..8）と同じ 134＝足元 (70, 149) が受付の枠 3 の枝 (82, 149) へ真横につながる（計画の 135 だと最後の 1 区間が
 *             斜め（12px 横・1px 縦）になり、pxwalk.test.js の「x か y に平行」の門が落ちた）
 * ラックの前は外部社員の席の意味なので使わない。
 */
export const ERRAND_SPOTS = Object.freeze([
  Object.freeze({ x: 122, y: 5, name: "shelf" }),
  Object.freeze({ x: 134, y: 6, name: "bulletin" }),
  Object.freeze({ x: 137, y: 47, name: "board" }),
  Object.freeze({ x: 64, y: 134, name: "counter" }),
]);

/**
 * 証拠の紙（R110-S5）を置くカウンターのトレイ（紙のタイル paper 6×3 の左上・論理 px）。カウンター（8,145 56×10）の天板の上・
 * 本（10..16）と卓上灯（58..）の間。最大 3 枚＝この数（古い順に消える・置き方は ui/cockpit/room.js）。
 */
export const EVIDENCE_TRAY = Object.freeze([
  Object.freeze([22, 147]), Object.freeze([30, 147]), Object.freeze([38, 147]),
]);

// ── ゾーン（world の語彙 → 部屋の区分）──────────────────────────────────
const WORLD_ZONES = new Set(["desk", "queue", "lounge", "meeting", "external"]);
const ROOM_VENDORS = new Set(["claude", "codex", "openclaw", "bot", "other"]);

/**
 * 本来居たい場所の順（先頭が本来の場所・後ろが仮住まい）。どれも埋まっていたら standby
 *（受付の列の最後尾から逆向きに並ぶ・attention の絵にはしない）。確認待ちは受付の列だけ
 *（呼んでいる人を机に座らせると、呼んでいることが絵から消える）。外部社員はラック前の枠が尽きたら、
 * 確認待ちなら受付の列（attention のまま）、そうでなければ standby（roomLayout の 3〜6）。
 */
const CHAIN = Object.freeze({
  queue: ["queue"],
  desk: ["desk"],
  lounge: ["lounge", "desk"],
  meeting: ["meeting", "desk"],
  external: ["external"],
});

/**
 * world の agent（または台帳の行）→ 部屋のどの区分に居たいか。world の zone が正本:
 *   external → external（別 Mac の稼働体＝専用区画から動かさない。❗は頭上の印で出る。
 *              ラック前の枠が尽きた確認待ちは roomLayout が受付の列へ回す）
 *   確認待ち → queue（受付に立つ＝確認待ち）
 *   lounge → lounge ／ meeting（サブエージェント稼働中）→ meeting
 *   それ以外（desk・食い違って attention の無い queue）→ desk
 * 思考中（kind === "think"）は机のまま（会議室へ歩かせない。思考のコマは描画側が出す）。
 * @returns {"queue"|"desk"|"lounge"|"meeting"|"external"}
 */
export function roomZoneOf(a) {
  const z = WORLD_ZONES.has(a?.zone) ? a.zone : worldZoneOf(a);
  if (z === "external") return "external";
  if (needsAttention(a)) return "queue";
  if (z === "lounge" || z === "meeting") return z;
  return "desk";
}

/** 前回の割り当て（Map か、前回の戻り値の assign）から 1 人ぶんを読む。壊れた値は無視する。 */
function readPrev(prev, key) {
  let v = null;
  if (prev instanceof Map) v = prev.get(key);
  else if (prev && typeof prev === "object" && Object.hasOwn(prev, key)) v = prev[key];
  if (!v || typeof v !== "object") return null;
  return {
    zone: typeof v.zone === "string" ? v.zone : "",
    slot: Number.isInteger(v.slot) ? v.slot : -1,
    home: Number.isInteger(v.home) && v.home >= 0 && v.home < SEATS ? v.home : -1,
    // 部屋の外（overflow）の人が持ち越す、あふれた列（queue / standby）と列の中の順位（0＝先頭）
    line: v.line === "queue" || v.line === "standby" ? v.line : "",
    rank: Number.isInteger(v.rank) && v.rank >= 0 ? v.rank : -1,
  };
}

/**
 * 部屋に出す配置を決める。**同じ入力なら必ず同じ配置**（golden の決定論）。
 *
 * **席番号は固定・詰め直さない**（design/README.md）。前回の割り当て `prev` を渡すと、区分の
 * 変わらない人は同じ枠に残る＝誰かが ❗ になる・休憩に入る・age が逆転して台帳の並びが変わる、
 * のどれでも他の人は動かない（歩行を入れたとき全員が歩き出さないための前提）。
 * 机を離れている人（❗・休憩・会議・standby）の机（home）は**予約**: 新しく来た人は予約の無い空席の
 * 最小の番号へ座り、予約の無い空席が尽きたときだけ予約の机を使う。戻った人は前の机が空いていればそこへ
 *（戻る人は新しく来た人より先に机を選ぶ）。`prev` が無い（固定時刻の初回・`?t=`）ときは台帳の順で決まる。
 *
 * 受付は**列**。確認待ちは前（枠 0）から並ぶ。誰かが抜けたら、その後ろの確認待ちだけが 1 つずつ前へ
 * 詰める（抜けた人より前の人は動かない・新しく呼んだ人は列の最後に付く）。standby（机・ソファ・会議室・
 * ラック前のあふれ）は**最後尾の枠から逆向き**に並ぶ＝確認待ちの人数が変わっても standby の立ち位置は
 * 動かない。standby の列の先頭は最後の枠で、机が空けば先頭から座り、残りは最後尾の側へ 1 つずつ詰める。
 * 両端から埋めて出会ったら、それ以上は overflow の人数で返す（確認待ちが優先・standby は列の後ろ＝
 * 新しく来た人から溢れる。フッターと台帳が受け持つ）。部屋の外へあふれた人も列の中の順位を持ち越す＝
 * 受付に空きができたら、あふれた順に（部屋の中の列の後ろ・新しく並んだ人より前へ）入る。
 *
 * @param {object} world ui/core/world.js の world
 * @param {object[]} board 台帳のグループ（プロジェクトの並び）
 * @param {object|Map|null} prev 前回の戻り値の `assign`（再生と live は別々に持つ）
 * @returns {{actors: Array, occupied: number[], overflow: number, counts: object, assign: object}}
 *   actors: [{key, id, session, projectKey, seat, slot, x, y, zone, want, vendor, attention, state, kind, verb,
 *     stuckTool, age, evidence, tool, evidenceAt, prop, deskWork, assistants, errorNote}]
 *     tool＝最後の工具の名前（無ければ ""）・evidenceAt＝証拠の出来事の時刻（world.generatedAt − evidence.ago・committed /
 *     tested だけ・無ければ null）・prop＝職業（archetype.roomArchetype・頭の小道具と机の小物の鍵・該当なしは "generic"）
 *     key は assign と同じキー（元は session、無ければ id。同じ元が 2 度出たら空いている `#n`）＝部屋の中で一意。
 *     歩行の帳簿（pxwalk.stepWalkers）と印の記録（pxtop.roomMark）はこれで人を追う（actor.id で追わない＝
 *     内訳の行は全員が同じ id を持つ）
 *     zone は "desk" | "queue" | "standby" | "lounge" | "meeting" | "external"、want は本来居たい区分
 *     （受付の列に回った外部社員は zone "queue"・want "external"・attention true）
 *     state / kind / verb / stuckTool / age は本人の様子（台帳の行のまま）＝コマ（pxtop.topPose の読書）と
 *     印（pxtop.roomMark の ⚠・💬）の材料
 *   occupied: 人が座っている席の番号（昇順・描画側が「点いている画面」を決める）
 *   overflow: 部屋に描けなかった人数（台帳には居る）
 *   counts: 部屋に描いた人の区分ごとの人数 {desk, queue, standby, lounge, meeting, external}
 *     （受付に描いた人数＝queue＋standby。overflow は含まない）
 *   assign: 次回へ渡す割り当て { [session（無ければ id）]: {zone, slot, home} }。台帳の全員ぶん在る
 *     （overflow の人は {zone: "overflow", slot: -1, home, line, rank}。机の予約 home と、あふれた列
 *     line（"queue" | "standby"）の中の順位 rank（0＝列の先頭）を次回へ持ち越すため）
 */
export function roomLayout(world, board = [], prev = null) {
  const agents = world?.agents || [];
  const projects = board.length
    ? board.map((g) => ({ key: g.key, sessions: g.sessions || [] }))
    : agents.map((a) => ({ key: a.id, sessions: [a] }));

  // 並び＝台帳の順（同点の決め手）。同じ session が 2 度出たら、空いている #n を付けて別人として扱う
  // （同じ枠を 2 人で奪い合うと、片方の割り当てが次回へ残らない）。
  const people = [];
  const usedKeys = new Set();
  for (const project of projects) {
    for (const a of project.sessions) {
      const base = String(a.session || a.id || "");
      let key = base;
      for (let n = 1; usedKeys.has(key); n += 1) key = `${base}#${n}`;
      usedKeys.add(key);
      const pv = readPrev(prev, key);
      people.push({ a, key, projectKey: project.key, want: roomZoneOf(a), call: needsAttention(a), pv,
        home: pv ? pv.home : -1, zone: null, slot: -1, line: "", rank: -1 });
    }
  }

  const pools = {};
  for (const [z, list] of Object.entries(SLOTS)) pools[z] = new Array(list.length).fill(null);
  const LINE = SLOTS.queue.length;
  const poolOf = (zone) => (zone === "standby" ? "queue" : zone);
  const isFree = (zone, s) => {
    const p = pools[poolOf(zone)];
    return Boolean(p) && Number.isInteger(s) && s >= 0 && s < p.length && p[s] === null;
  };
  const firstFree = (zone) => {
    const p = pools[poolOf(zone)];
    for (let s = 0; s < p.length; s += 1) if (p[s] === null) return s;
    return -1;
  };
  const take = (p, zone, s) => { pools[poolOf(zone)][s] = p; p.zone = zone; p.slot = s; };
  const release = (p) => { pools[poolOf(p.zone)][p.slot] = null; p.zone = null; p.slot = -1; };
  // 受付の列の並び＝前回の列の中の順位（0＝先頭。確認待ちの先頭は枠 0、standby の先頭は最後の枠）の順。
  // 部屋の外へあふれていた人は持ち越した順位（部屋の中の列より後ろ＝新しく並んだ人より前）。同じ順位を持つ
  // 壊れた前回は台帳の順（安定ソート）。前回その列に居なかった人は、その後ろへ台帳の順に付く。
  const rankIn = (p, line) => {
    const pv = p.pv;
    if (pv?.zone === "overflow") return pv.line === line ? pv.rank : -1;
    if (pv?.zone !== line || pv.slot < 0 || pv.slot >= LINE) return -1;
    return line === "queue" ? pv.slot : LINE - 1 - pv.slot;
  };
  const lineOrder = (list, line) => [
    ...list.filter((p) => rankIn(p, line) >= 0).sort((x, y) => rankIn(x, line) - rankIn(y, line)),
    ...list.filter((p) => rankIn(p, line) < 0),
  ];
  const outside = (p, line, rank) => { p.line = line; p.rank = rank; };

  // 1. 居続ける人: 前回の区分が今も居てよい場所で、その枠が空いていれば同じ枠。
  //    受付（確認待ち・standby）は列の並びで決める（4・6）。
  for (const p of people) {
    const pv = p.pv;
    if (!pv || pv.zone === "queue" || pv.zone === "standby") continue;
    if (CHAIN[p.want].includes(pv.zone) && isFree(pv.zone, pv.slot)) take(p, pv.zone, pv.slot);
  }
  // 2. 仮住まい（休憩・会議があふれて机に居る人）は、本来の場所が空いたらそこへ移る
  for (const p of people) {
    if (p.zone === null || p.zone === p.want) continue;
    const s = firstFree(p.want);
    if (s >= 0) { release(p); take(p, p.want, s); }
  }
  // 3. 外部社員はラック前の空いた枠へ（台帳の順）。尽きたら確認待ちは受付の列（4）、それ以外は standby（6）
  for (const p of people) {
    if (p.zone !== null || p.want !== "external") continue;
    const s = firstFree("external");
    if (s >= 0) take(p, "external", s);
  }
  // 4. 受付の列（確認待ち）: 前から詰める。前回の列の順を保つ＝抜けた人の後ろだけが前へ詰まる。
  //    9 枠を超えた人は部屋の外（列の順位を持ち越す）
  const callers = lineOrder(people.filter((p) => p.zone === null && p.call), "queue");
  callers.forEach((p, i) => (i < LINE ? take(p, "queue", i) : outside(p, "queue", i)));
  const k = Math.min(callers.length, LINE);
  // 5. 残り（区分が変わった人・新しく来た人・standby に居た人）: 本来の場所 → 机 → standby 候補。
  //    机を選ぶ順＝自分の机（home）が空いている人 → standby の列の先頭から → それ以外（新しく来た人・
  //    机を取られていた人）は台帳の順。自分の机へ戻れる人を先に置く＝机を取られた人が、別の人の予約の机を
  //    先に取って追い出す連鎖を起こさない。
  const rest = people.filter((p) => p.zone === null && !p.call);
  const backHome = (p) => p.home >= 0 && isFree("desk", p.home);
  const ordered = [
    ...rest.filter(backHome),
    ...lineOrder(rest.filter((p) => !backHome(p)), "standby"),
  ];
  // 机を離れている人（確認待ち・休憩・会議・standby・overflow）の home は予約
  const reservedFor = (s, self) => people.some((q) => q !== self && q.home === s && q.zone !== "desk");
  const pickDesk = (p) => {
    if (isFree("desk", p.home)) return p.home;
    let spare = -1;
    for (let s = 0; s < pools.desk.length; s += 1) {
      if (pools.desk[s] !== null) continue;
      if (!reservedFor(s, p)) return s;
      if (spare < 0) spare = s;
    }
    return spare;
  };
  const standby = [];
  for (const p of ordered) {
    for (const z of CHAIN[p.want]) {
      const s = z === "desk" ? pickDesk(p) : firstFree(z);
      if (s >= 0) { take(p, z, s); break; }
    }
    if (p.zone === null) standby.push(p);
  }
  // 6. standby: 受付の最後の枠から逆向きに、前回の並びを保って（列の先頭＝最後の枠に近い人から）。
  //    確認待ちの列（前の k 枠）と出会ったら、それ以上は overflow（列の順位を持ち越す）。
  //    5 の順は「机を選ぶ順」で、standby の列の順とは限らない（同じ home を持つ 2 人が同時に戻り、片方が
  //    机に入れなかった等）＝standby の並びの正本はここ。外すと列が入れ替わる（性質の検査が捕まえる）
  lineOrder(standby, "standby").forEach((p, i) => (i < LINE - k ? take(p, "standby", LINE - 1 - i) : outside(p, "standby", i)));

  const actors = [];
  const occupied = [];
  const counts = { desk: 0, queue: 0, standby: 0, lounge: 0, meeting: 0, external: 0 };
  const assign = Object.create(null);
  let overflow = 0;
  for (const p of people) {
    if (p.zone === null) {
      // 描けない＝台帳とフッターが持つ。机の予約（home）と列の中の順位は次回へ持ち越す
      overflow += 1;
      assign[p.key] = Object.freeze({ zone: "overflow", slot: -1, home: p.home, line: p.line, rank: p.rank });
      continue;
    }
    const a = p.a;
    const at = SLOTS[poolOf(p.zone)][p.slot];
    counts[p.zone] += 1;
    if (p.zone === "desk") occupied.push(p.slot);
    assign[p.key] = Object.freeze({ zone: p.zone, slot: p.slot, home: p.zone === "desk" ? p.slot : p.home });
    const vendor = String(a.vendor || "").toLowerCase();
    actors.push({
      key: p.key,
      id: a.id,
      session: a.session,
      projectKey: p.projectKey,
      seat: p.zone === "desk" ? p.slot : -1,
      slot: p.slot,
      x: at.x,
      y: at.y,
      zone: p.zone,
      want: p.want,
      // 未知のベンダーを claude に潰さない（描画側が「知らない」を知れるように other のまま運ぶ）
      vendor: ROOM_VENDORS.has(vendor) ? vendor : vendorOf(a),
      // standby は確認待ちではない（受付に居ても挙手させない）。外部社員の ❗ はラックの前でも
      // 受付の列でも出る。
      attention: p.zone !== "standby" && needsAttention(a),
      // コマと印を決めるのは pxtop（topPose・roomMark）。材料（本人の状態）はここが運ぶ。
      // これを落とすと働いている人も待っている人も同じ絵になる（R98 で実測した落とし穴）。
      // verb＝読書のコマ・stuckTool＝⚠・age＝💬 の窓（落とすと読書・⚠・💬 が 1 件も出ないのに赤にならない＝
      // tests/cockpit_cells.test.mjs の固定ワールドの門が数える）
      state: a.state,
      kind: a.kind,
      verb: typeof a.verb === "string" ? a.verb : "",
      stuckTool: typeof a.stuckTool === "string" ? a.stuckTool : "",
      age: Number.isFinite(a.age) ? a.age : null,
      evidence: a.evidence || null,
      // R110-S5: 最後の工具の名前（サーバーの tool・言語に依らない＝用事の行き先・画面の模様・打鍵の速さの材料。中継には
      // 載らない＝スマホは verb から決める）と、証拠の出来事の時刻（generatedAt − ago・committed / tested だけ＝カウンターへ紙を
      // 出しに行くきっかけ・ui/core/errand.js）。職業（頭の小道具・机の小物）は R110-S2
      tool: typeof a.tool === "string" ? a.tool : "",
      evidenceAt: evidenceTime(a.evidence, world?.generatedAt),
      prop: roomArchetype(a),
      deskWork: deskWorkKind(a),
      assistants: assistantCount(a),
      errorNote: hasErrorNote(a),
    });
  }
  occupied.sort((x, y) => x - y);
  return { actors, occupied, overflow, counts, assign: Object.freeze(assign) };
}

// ── 個体色の添字 ─────────────────────────────────────────────────────
/** プロジェクトのキー → 希望の色の添字の元（ui/cockpit/room.js の tintFor と同じハッシュ＝既存の色を変えない）。 */
export function tintHash(key) {
  let h = 0;
  const s = String(key);
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

/** 前回の割り当て（Map か plain object）から 1 キーぶんの添字を読む。壊れた値は -1。 */
function readTint(prev, key) {
  let v;
  if (prev instanceof Map) v = prev.get(key);
  else if (prev && typeof prev === "object" && Object.hasOwn(prev, key)) v = prev[key];
  return Number.isInteger(v) ? v : -1;
}

/**
 * 表示中のプロジェクトへ色の添字（0..n-1）を配る。部屋には名前を描かない掟なので、色が重なると
 * 誰がどれか分からない＝**n 件以下なら重ならない**。さらに**一度配った色は、表示している間は変えない**
 *（色が黙って入れ替わると、誰がどれかが入れ替わって見える）:
 *   1. 前回も表示していたキーは前回の添字（`prev`）。壊れた前回が同じ添字を 2 つに配っていたら昇順の先頭だけ
 *   2. 残りのキーはハッシュの剰余を希望し、空いていれば取る（同じ希望は昇順の先頭）
 *   3. それでも残ったキーは昇順に、希望の次から空いている添字へ。空きが無ければ希望のまま（n 件超＝重なるしかない）
 * `prev` が無い（固定時刻・初回）ときは 2・3 だけ＝表示中のキーの集合だけで決まる（決定論・入力の並び順に依らない）。
 * このとき、同じ剰余を持つキーが新しく現れると、昇順で前に来る方が希望の添字を取る＝既存キーの色が変わりうる。
 * それを止めるのが `prev`（描画側は前回の戻り値を渡し続ける）。
 * 衝突が無ければ全員が希望どおり＝ハッシュの剰余（旧 tintFor）と同じ色。
 * @param {Iterable<string>} keys 表示中のプロジェクトのキー（重複は 1 つに数える）
 * @param {number} n 色の数
 * @param {Map<string, number>|Object<string, number>|null} [prev] 前回の戻り値（Map）か、同じ形の plain object
 * @returns {Map<string, number>} キー（文字列）→ 添字
 */
export function tintIndex(keys, n, prev = null) {
  const out = new Map();
  const size = Math.floor(Number(n));
  if (!(size >= 1)) return out;
  const uniq = [...new Set([...(keys || [])].map(String))].sort();
  const used = new Array(size).fill(false);
  const claim = (key, i) => { used[i] = true; out.set(key, i); };
  for (const key of uniq) {
    const i = readTint(prev, key);
    if (i >= 0 && i < size && !used[i]) claim(key, i);
  }
  const want = new Map(uniq.map((key) => [key, tintHash(key) % size]));
  for (const key of uniq) {
    if (out.has(key)) continue;
    const i = want.get(key);
    if (!used[i]) claim(key, i);
  }
  for (const key of uniq) {
    if (out.has(key)) continue;
    const i = want.get(key);
    let got = i;
    for (let d = 1; d < size; d += 1) {
      const j = (i + d) % size;
      if (!used[j]) { used[j] = true; got = j; break; }
    }
    out.set(key, got);
  }
  return out;
}

// ── 倍率と当たり（R99 Phase 4・P4-2a）─────────────────────────────────────
/** 部屋の倍率の下限と上限（368×320 〜 736×640）。整数倍だけ＝割合の縮小・非整数のズームはしない。 */
export const ROOM_SCALE_MIN = 2;
export const ROOM_SCALE_MAX = 4;
/**
 * 倍率の床（×1＝184×160）。R102-1（本人裁定 2026-09-30「狭い画面では部屋を小さく（×1）描いて全員を入れる」）:
 * スマホの PWA だけが roomScale の第 3 引数で使う（あなた待ちのカードを部屋に重ねない分、窓が ×2 に足りない形がある）。
 * ×1 でも CSS の整数倍＝端末の画素（DPR 2〜3）ではにじまない。コックピットの既定の下限は ROOM_SCALE_MIN のまま。
 */
export const ROOM_SCALE_FLOOR = 1;

/**
 * ロボの当たりの半径（CSS px）。呼ぶ側が倍率で割って論理 px にし、pickNearest へ渡す。
 * ×2 だと描いた矩形は 24×32 CSS px で押せる大きさ（44）に届かない＝矩形の縁からこの半径までを当たりにする
 *（ひとりなら縦横とも 44 以上。PWA 3D の pickAgent と同じ「最寄り」の考え方）。
 */
export const PICK_RADIUS_PX = 22;

const positiveNumber = (v) => typeof v === "number" && Number.isFinite(v) && v > 0;

/**
 * 眺めるモード（R110-S6・コックピットの F＝部屋だけを画面いっぱいに）だけが roomScale の第 4 引数で使う上限（×6＝1104×960）。
 * 既定の上限 ROOM_SCALE_MAX（×4）は据え置き＝3 カラムの段・PWA のピンチの上限・pxroom.test の ×4 のピンは変わらない。
 */
export const ROOM_SCALE_WATCH_MAX = 6;

/**
 * 形状で部屋の倍率を選ぶ: clamp(floor(min(availW / 184, availH / 160)), 2, 4)。
 *   availW  部屋の箱の内寸から**左の余白だけ**を引いた幅（CSS px）。右の余白は ×k の部屋が食ってよい
 *           （スマホ 390 は 374 → ×2 の 368 を置いて右は 6px。両側を引くと 3 カラムの列 584（罫 1 を除いて 583）が
 *           551 になり、×3 の 552 に 1px 足りず ×2 に落ちる）
 *   availH  部屋に使ってよい丈（CSS px）。1 列のコックピットは 480 で頭打ちにして渡す（丈を制限しないときも有限の値を渡す）
 * 数でない・有限でない・0 以下はどちらも下限（入らなければ部屋の中の横スクロールで逃がす＝最小の倍率が安全側）。
 *   min     下限（既定 ROOM_SCALE_MIN＝×2・PWA は ROOM_SCALE_FLOOR＝×1）。FLOOR〜MIN の整数でなければ ×2
 *   max     上限（既定 ROOM_SCALE_MAX＝×4・眺めるモードは ROOM_SCALE_WATCH_MAX＝×6）。MAX〜WATCH_MAX の整数でなければ ×4
 * 同じ入力なら同じ倍率（DOM・時刻に触らない）。
 * @returns {1|2|3|4|5|6}
 */
export function roomScale(availW, availH, min = ROOM_SCALE_MIN, max = ROOM_SCALE_MAX) {
  const lo = Number.isInteger(min) && min >= ROOM_SCALE_FLOOR && min <= ROOM_SCALE_MIN ? min : ROOM_SCALE_MIN;
  const hi = Number.isInteger(max) && max >= ROOM_SCALE_MAX && max <= ROOM_SCALE_WATCH_MAX ? max : ROOM_SCALE_MAX;
  if (!positiveNumber(availW) || !positiveNumber(availH)) return lo;
  const k = Math.floor(Math.min(availW / ROOM.w, availH / ROOM.h));
  return Math.min(hi, Math.max(lo, k));
}

/**
 * 論理 px の点 (lx, ly) に一番近いロボ。距離は描いた矩形（左上 (x, y)・ROOM.robot の 12×16）の縁から測る
 *（矩形の中は 0＝描いた所を押せば必ずその人・角の外は斜めの距離）。半径 radiusLogical より遠ければ null。
 * 同距離（重なった人の中を押した等）は**足元の y が大きい人**（手前＝後から描いて上に見えている人）→ **key の昇順**
 *（文字コード順）。入力の順には依らない。
 *   actors  反復できる人の並び（roomLayout の actors・描いた人 {key, x, y, leaving?}）。leaving === true
 *           （退場中）と、座標が有限の数でない人は読み飛ばす
 *   radiusLogical  論理 px の半径（PICK_RADIUS_PX / 倍率）。数でない・負は 0（矩形の中だけ当たる）
 * 点が有限の数でない・並びが反復できないときは null。当たった人は**入力の同じ参照**を返す。
 */
export function pickNearest(actors, lx, ly, radiusLogical) {
  if (actors == null || typeof actors[Symbol.iterator] !== "function") return null;
  if (!Number.isFinite(lx) || !Number.isFinite(ly)) return null;
  const r = typeof radiusLogical === "number" && radiusLogical >= 0 ? radiusLogical : 0;
  const r2 = r * r;
  const { w, h } = ROOM.robot;
  const footDy = ROBOT_FOOT[1] + ROBOT_FOOT[3] / 2;   // 足元の中心の y＝左上 y + 15（pxwalk.footOf と同じ）
  let best = null, bestD = Infinity, bestFoot = -Infinity, bestKey = "";
  for (const a of actors) {
    if (!a || typeof a !== "object" || a.leaving === true) continue;
    const { x, y } = a;
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    const dx = Math.max(x - lx, 0, lx - (x + w));
    const dy = Math.max(y - ly, 0, ly - (y + h));
    const d = dx * dx + dy * dy;
    if (!(d <= r2)) continue;
    const foot = y + footDy;
    const key = String(a.key ?? "");
    if (d < bestD || (d === bestD && (foot > bestFoot || (foot === bestFoot && key < bestKey)))) {
      best = a; bestD = d; bestFoot = foot; bestKey = key;
    }
  }
  return best;
}

// ── 机上の作業と助手（R103-6）───────────────────────────────────────────
const DESK_TOOLS = Object.freeze({
  implementation: Object.freeze(["Edit", "Write", "NotebookEdit", "Artifact"]),
  research: TOP_READ_TOOLS,
  planning: Object.freeze(["TodoWrite", "EnterPlanMode", "ExitPlanMode"]),
});
const IMPLEMENTATION_ACTIVITIES = new Set(["code", "test", "build", "ship"]);

/**
 * buildWorld が落とす err・minionsLive を、同じ office の搬送済み行から部屋だけに補う。
 * roster は社員 ID と代表 session の両方を照合する。代表が変わった別の行の値は混ぜない。
 * project モードの roster.minionsLive は合算値なので代表 employee より優先する。
 * state・zone・累計 minions・席・内訳は元の world のまま。raw が無い再生には現在値を足さない。
 */
export function roomWorldFields(world, office) {
  if (!world || !Array.isArray(world.agents)) return world;
  const roster = (Array.isArray(office?.roster) ? office.roster : []).filter((a) => a && typeof a === "object");
  const employees = (Array.isArray(office?.employees) ? office.employees : []).filter((a) => a && typeof a === "object");
  const byId = new Map(roster.map((a) => [a.projectId || a.session || "", a]));
  const bySession = new Map(roster.filter((a) => a.session).map((a) => [a.session, a]));
  const members = new Map(employees.filter((a) => a.session).map((a) => [a.session, a]));
  let changed = false;
  const agents = world.agents.map((a) => {
    if (!a || typeof a !== "object") return a;
    const candidate = byId.get(a.id);
    const project = candidate && (candidate.session || "") === a.session ? candidate : bySession.get(a.session);
    const member = members.get(a.session);
    if (!project && !member) return a;
    changed = true;
    return withKindFields(a, project ? { ...member, ...project } : member);
  });
  return changed ? { ...world, agents } : world;
}

/**
 * 搬送済みの作業語彙だけから机上の小物の種類を決める。新しいサーバー項目・本文の推測は要らない。
 * 優先は現在の確認ツール（ask.tool）→止まっているツール（stuckTool）→kind の think→日英の verb。
 * 書類の執筆・報告・指示待ちなど、3 種を確定できない作業は null（従来の小物を残す）。
 * @returns {"implementation"|"research"|"planning"|null}
 */
export function deskWorkKind(e) {
  if (!e || e.state === "resting") return null;
  for (const tool of [e.ask?.tool, e.stuckTool]) {
    for (const [kind, tools] of Object.entries(DESK_TOOLS)) {
      if (tools.includes(tool)) return kind;
    }
  }
  if (e.kind === "think") return "planning";
  if (isReadVerb(e.verb)) return "research";
  if (/^(?:段取り整理中|planning)$/i.test(String(e.verb || "").trim())) return "planning";
  const kind = activityKind(e);
  if (kind === "research") return "research";
  if (kind === "think") return "planning";
  return IMPLEMENTATION_ACTIVITIES.has(kind) ? "implementation" : null;
}

/** 稼働中のサブエージェントだけを助手にする。累計 minions は使わず、有限数の整数部分を最大 3 体にする。 */
export function assistantCount(e) {
  const count = Number(e?.minionsLive);
  return Number.isFinite(count) && count > 0 ? Math.min(3, Math.floor(count)) : 0;
}

/** 赤い紙は状態の語「エラーで停止」と同じ条件。再開済みや確認待ちに古い err を貼らない。 */
export function hasErrorNote(e) {
  return sessionKind(e) === "error";
}
