// R99 Phase 3: 俯瞰オフィス（?ui=cockpit の部屋・ロボ 12×16）で、1 体が**どのコマを・どの向きで**出すかと、
// 頭上に**どの印**を出すかを決める純関数。
//
// pixel の帯（ui/core/pxpose.js＝側面視 42 セル）とは語彙が違う。こちらは真上寄りの俯瞰なので、歩行は
// S（手前へ）・N（奥へ）・E（右へ）の 3 方向を描き、W（左へ）は E を左右反転して作る（mirror）。
// ここが決めるのは「どの名前のコマか」だけ。ドットそのもの（PX_CELLS[vendor][cell]）は design/gen_cells.py が
// 起こし、描画は ui/cockpit/room.js がする。DOM・時刻・乱数には触らない（tools/js_layer_lint.py）＝時刻は引数。
//
// 完全性の門: tests/cockpit_cells.test.mjs が「ここが返しうるコマの全部が、3 ベンダーぶん PX_CELLS に
// 別名なしで在る」と「固定ワールドを台帳 → roomLayout → ここまで通すと、読書・⚠・💬・💭・❗ が 1 件以上出る」を見る。
// コマを足すときは TOP_CELLS と gen_cells.py の ORDER を同時に直す。
//
// 配線（ui/cockpit/room.js・1 フレーム）: stepWalkers の drawn の 1 人ずつ
//   const m = roomMark(d, t, memo, {live}); memo = m.memo;
//   const cls = d.kind === "tool" ? toolClassOf(d) : null;          // ui/core/errand.js（R110-S5）
//   const pose = topPose(d, t, {walk: d.walk, lift: d.lift, settled: d.settled, done: m.mark === "done", pace: typePace(d, cls)});
//   const at = topMarkAt(d, m.mark);   // 印のタイルと置き場所（手前の什器の後に描く）
//   const prop = topPropAt(d, d.prop, rows, {mirror: pose.mirror, lift: pose.lift, dir: pose.dir});   // 職業の小道具（R110-S2）
//   考え中の雲の点は live だけ thinkDots(t, m.since, phase)（止め絵は markThink のまま）
// live は「t が実時間で進んでいる」ときだけ true（roomMark の JSDoc）。

/**
 * 1 ベンダーあたりのコマ（24）。名前と順は design/gen_cells.py の ORDER・ui/cockpit/cells.js と完全一致
 * （委譲の契約。順を入れ替えない）。
 */
export const TOP_CELLS = Object.freeze([
  "idle0", "idle1", "type0", "type1", "read0", "read1", "raise0", "raise1",
  "think0", "think1", "rest0", "rest1", "done0",
  "walkS0", "walkS1", "walkS2", "walkS3",
  "walkN0", "walkN1", "walkN2", "walkN3",
  "walkE0", "walkE1", "walkE2", "walkE3",
]);

/** 描き分けるベンダー（ui/cockpit/cells.js の PX_VENDORS と同じ順）。それ以外は claude の形で描く。 */
export const TOP_VENDORS = Object.freeze(["claude", "codex", "openclaw", "bot"]);

/** 向き。W は描かない（E の鏡像）。止まっている人は S（正面）。 */
export const TOP_DIRS = Object.freeze(["S", "N", "E", "W"]);

/**
 * 種別（優先の高い順）。greet はタップ・クリックの挨拶（入力起点・live のときだけ・GREET_SEC 秒）。
 * rise（歩き出す前の立ち上がり）と settle（着いた瞬間の一拍）は歩行の帳簿（ui/core/pxwalk.js の drawn.lift・drawn.settled）
 * からだけ起きる＝live だけ（R110-S4）。どちらも正面の待機コマ（idle0）を出す。
 */
export const TOP_KINDS = Object.freeze(["walk", "rise", "greet", "settle", "raise", "done", "rest", "read", "think", "type", "idle"]);

/** 挨拶（手を振る）の長さ（秒）。万歳 ↔ 待機を 3fps で繰り返し、途中の 0.3〜0.6 秒だけ 1px 跳ねる。 */
export const GREET_SEC = 0.9;
/**
 * 着いた瞬間の一拍（秒・R110-S4）。歩いて着いてからこの秒数のあいだは正面の待機コマ（idle0＝腕を下ろす）で立ち、
 * その後に打鍵・読書・挙手・休憩などの種別のコマへ移る（「パッと着いてすぐ打ち始める」を和らげる）。
 * 6fps の描画で 2 刻み・12fps で 4 刻み（どちらの格子でも割り切れる）。
 */
export const SETTLE_SEC = 1 / 3;
/**
 * 個体ごとの開始位相（秒）の刻み。全員が同じ瞬間にコマを切り替えると部屋が「一斉に瞬く」だけに見える
 *（R99-P4 の調査: 同じ種別のロボが全員同時に切り替わっていた）。位相は `(ハッシュ % PHASE_STEPS) / PHASE_STEPS` 秒。
 * 6fps の格子で区別できる数（idle 6・type 6・raise 7・rest 6 通り）から 12 を選んだ。**止め絵（固定時刻・停止）は位相 0**＝今の絵。
 */
export const PHASE_STEPS = 12;

/**
 * 「読んでいる」と見なすツール（server/office_server.py の TOOL_VERB / TOOL_VERB_EN のキー）と、その動詞。
 * サーバーは verb に**表の値そのもの**を載せる（describe_tool）ので、ここは字面で突き合わせる。
 * サーバーの表と食い違ったら tests/cockpit_cells.test.mjs が落ちる（server の表を文字列で読んで比べる）。
 */
export const TOP_READ_TOOLS = Object.freeze(["Read", "Grep", "Glob", "Explore", "WebFetch", "WebSearch"]);
export const TOP_READ_VERBS = Object.freeze({
  ja: Object.freeze(["読込中", "調査中", "リサーチ中"]),
  en: Object.freeze(["reading", "researching"]),
});
const READ_VERBS = new Set([...TOP_READ_VERBS.ja, ...TOP_READ_VERBS.en]);

/** 種別 → コマの並び（ループする）と毎秒のコマ数。done と think は 1 コマ（止め絵）。 */
const SEQ = Object.freeze({
  idle: ["idle0", "idle1"],
  type: ["type0", "type1"],
  read: ["read0", "read1"],
  raise: ["raise0", "raise1"],
  rest: ["rest0", "rest1"],
  done: ["done0"],
  think: ["think0", "think1"],
  greet: ["done0", "idle0"],
});
const FPS = Object.freeze({ idle: 1, type: 2, read: 1, raise: 3, rest: 0.5, done: 0, think: 0.5, greet: 3 });

/**
 * 打鍵の速さ（R110-S5・毎秒のコマ数）。本人「せっせと働いている感じを」: 書いている（Edit・Write の工具の最中）人は速く、
 * 工具を使っていない（指示を受けた直後・報告の途中など）人はゆっくり打つ。3・2・1 は描画の刻み（6・12fps）で割り切れる。
 * どれになるかは呼び手（ui/cockpit/room.js）が道具の種類（ui/core/errand.js の toolClassOf）から決めて topPose の
 * opts.pace に渡す（pxtop は errand を import しない＝pxroom を経た循環を作らない）。省略・知らない値は normal（従来の 2fps）。
 * 止め絵（位相 0・t 0）はどの速さでも type0＝golden 不変。
 */
export const TYPE_FPS = Object.freeze({ burst: 3, normal: 2, calm: 1 });

/**
 * 打鍵の速さの段。kind が "tool"（工具を使っている最中）で道具の種類が edit なら burst・ほかの工具は normal・
 * 工具を使っていない（kind が tool でない）なら calm。
 * @param {object} actor roomLayout の actor（kind を読む）
 * @param {string|null} toolClass ui/core/errand.js の toolClassOf(actor)
 * @returns {"burst"|"normal"|"calm"}
 */
export function typePace(actor, toolClass) {
  if (!actor || typeof actor !== "object" || actor.kind !== "tool") return "calm";
  return toolClass === "edit" ? "burst" : "normal";
}

/**
 * 机の画面が語る（R99-P4-L3）: 席に座っている人の種別 → 画面の送りの毎秒のコマ数。打鍵は行が流れ（2fps）、読書は
 * ゆっくり（1fps）、考え中は点が増える（1fps）。送りは 3 コマ＝0（今の光った画面のまま・描画側は何も貼らない）→ 1 → 2。
 * コマ 0 を貼らない＝止め絵（位相 0・t 0）は今の絵（golden 不変）。
 */
export const SCREEN_FPS = Object.freeze({ type: 2, read: 1, think: 1 });

/**
 * 画面の送りのコマ（0・1・2）。0 は「貼らない」。種別が SCREEN_FPS に無ければ 0。位相は topPose と同じ（秒・0 以上の有限数）。
 * @param {string} kind topPose(...).kind
 * @param {number} t 秒
 * @param {number} [phase] 開始位相（秒）
 * @returns {0|1|2}
 */
export function screenFrame(kind, t, phase = 0) {
  if (!Object.hasOwn(SCREEN_FPS, String(kind))) return 0;
  const time = Number.isFinite(t) ? t : 0;
  const ph = Number.isFinite(phase) && phase >= 0 ? phase : 0;
  return frame((time + ph) * SCREEN_FPS[kind], 3);
}

/** 歩行の並び（1 周 4 コマ）。W は E を使い、mirror で反転する。 */
const WALK = Object.freeze({
  S: ["walkS0", "walkS1", "walkS2", "walkS3"],
  N: ["walkN0", "walkN1", "walkN2", "walkN3"],
  E: ["walkE0", "walkE1", "walkE2", "walkE3"],
});

/** x の整数部を n で割った余り（負・巨大・NaN でも 0..n-1。Infinity の剰余は NaN になるので潰す）。 */
function frame(x, n) {
  const f = Math.floor(x);
  if (!Number.isFinite(f)) return 0;
  return ((f % n) + n) % n;
}

/** ベンダー名 → 描き分けのキー。未知（other・空・大文字違い以外の名前）は claude。 */
export function topVendor(v) {
  const s = String(v ?? "").trim().toLowerCase();
  return TOP_VENDORS.includes(s) ? s : "claude";
}

/** 動詞が「読んでいる」語彙か（日英）。 */
export function isReadVerb(verb) {
  return READ_VERBS.has(String(verb ?? "").trim());
}

/** 止まっている人の種別（walk 以外）。優先: raise > done > rest > read > think > type > idle。standby は idle。 */
function stillKind(a, done) {
  // 受付の standby は「席が無くて立って待っている人」＝確認待ちではない（挙手させない）し、
  // 机も椅子も無い場所で打鍵・読書・休憩のコマを出すと嘘になる。
  if (a.zone === "standby") return "idle";
  if (a.attention) return "raise";
  if (done) return "done";
  if (a.state === "resting" || a.zone === "lounge") return "rest";
  if (a.state === "working" && isReadVerb(a.verb)) return "read";
  if (a.kind === "think") return "think";
  if (a.state === "working") return "type";
  return "idle";
}

/**
 * 俯瞰のロボ 1 体のコマを決める。**同じ入力なら必ず同じ結果**（golden の決定論）。
 *
 * 種別の優先: walk > rise（opts.lift）> greet（opts.greet）> settle（opts.settled < SETTLE_SEC）
 *   > raise（attention かつ zone≠standby）> done（opts.done）> rest（state resting か zone lounge）
 *   > read（working かつ読む動詞）> think（kind think）> type（working）> idle。zone standby は（歩いていなければ）idle。
 * コマ送り: idle 1fps・type は opts.pace の TYPE_FPS（burst 3・normal 2・calm 1・省略は normal）・read 1・raise 3・
 *   rest 0.5・done / think は 1 コマ・walk は phase×4（距離で駆動＝滑らない）。
 * rise と settle は idle0 の 1 コマ（rise だけ lift を返す＝描く y を上げる）。
 * 描画の刻み（ui/core/pxwalk.js の ROOM_FPS: 6fps・歩く人が居る間 12fps）で割り切れる＝描画ごとにコマが飛ばない。
 * 退場中の人（walk あり・leaving）は walk が勝つ＝挙手しない。立ち上がりの間の退場中の人も rise＝挙手しない。
 *
 * @param {object} actor ui/core/pxroom.js の roomLayout().actors[i]（state / zone / attention / kind / verb を読む）
 * @param {number} t 秒（UI クロック。frozen なら固定）。非数は 0
 * @param {{walk?: null|{phase:number, dir:"S"|"N"|"E"|"W"}, lift?: number, settled?: number|null, done?: boolean,
 *   phase?: number, greet?: number, pace?: "burst"|"normal"|"calm"}} [opts]
 *   walk: 歩いている間だけ渡す（phase は 0..1＝1 周 4 コマの中の位置。1 以上は周回として扱う）。
 *   lift: 立ち上がりの間だけ 1 以上の整数（stepWalkers の drawn.lift）。描く y を何 px 上げるかとしてそのまま返す。
 *   settled: 歩いて着いてからの秒（stepWalkers の drawn.settled）。0 以上 SETTLE_SEC 未満のときだけ一拍（null・非数は無し）。
 *   done: ✓ の窓の中か（roomMark(...).mark === "done" を渡す想定）。
 *   phase: 止まっている人の開始位相（秒・0 以上の有限数・それ以外は 0）。描画側が live のときだけ個体ごとに渡す。
 *   greet: 挨拶を始めてからの秒（0 ≤ greet < GREET_SEC のときだけ挨拶・歩行と立ち上がりが勝つ）。
 *   pace: 打鍵の速さの段（typePace・TYPE_FPS の鍵）。type のコマ送りにだけ効く。
 * @returns {{cell: string, dir: "S"|"N"|"E"|"W", mirror: boolean, kind: string, lift?: number}}
 *   cell は必ず TOP_CELLS のどれか。mirror が true なら描画側が左右反転する（W のときだけ）。
 *   lift は挨拶と立ち上がりのときだけ（描く y を何 px 上げるか＝当たり・名札・足元の y は動かさない）。
 */
export function topPose(actor, t, opts = {}) {
  const a = actor && typeof actor === "object" ? actor : {};
  const o = opts && typeof opts === "object" ? opts : {};
  const w = o.walk && typeof o.walk === "object" ? o.walk : null;
  if (w) {
    const dir = TOP_DIRS.includes(w.dir) ? w.dir : "S";
    const seq = WALK[dir === "W" ? "E" : dir];
    const phase = Number.isFinite(w.phase) ? w.phase : 0;
    return { cell: seq[frame(phase * seq.length, seq.length)], dir, mirror: dir === "W", kind: "walk" };
  }
  // 立ち上がり（R110-S4）: 歩き出す前に正面で 1px 浮く（歩行コマは出さない＝足は出発点のまま）
  if (Number.isInteger(o.lift) && o.lift > 0) return { cell: "idle0", dir: "S", mirror: false, kind: "rise", lift: o.lift };
  const greet = Number.isFinite(o.greet) && o.greet >= 0 && o.greet < GREET_SEC ? o.greet : null;
  if (greet !== null) {
    const seq = SEQ.greet;
    // コマ番号は 1/1000 コマで丸めてから数える（描画の 6fps の格子の t から引き算した経過秒は 0.33333…3 のように
    // 境目のすぐ手前に落ち、3fps の切り替わりが 1 刻み遅れる）
    return { cell: seq[frame(Math.round(greet * FPS.greet * 1000) / 1000, seq.length)], dir: "S", mirror: false, kind: "greet",
      lift: greet >= 0.3 && greet < 0.6 ? 1 : 0 };
  }
  // 到着の一拍（R110-S4）: 着いてから SETTLE_SEC は正面で腕を下ろして立つ。境目は 1e-6 秒で丸めて比べる
  //（6fps の格子の t から引いた 2/6 が 0.33333…1 に落ち、1 刻み余計に一拍が続くのを防ぐ）
  if (Number.isFinite(o.settled) && o.settled >= 0 && Math.round(o.settled * 1e6) < Math.round(SETTLE_SEC * 1e6)) {
    return { cell: "idle0", dir: "S", mirror: false, kind: "settle" };
  }
  const kind = stillKind(a, o.done === true);
  const seq = SEQ[kind];
  const time = Number.isFinite(t) ? t : 0;
  const ph = Number.isFinite(o.phase) && o.phase >= 0 ? o.phase : 0;
  const fps = kind === "type" ? TYPE_FPS[Object.hasOwn(TYPE_FPS, String(o.pace)) ? o.pace : "normal"] : FPS[kind];
  return { cell: seq[frame((time + ph) * fps, seq.length)], dir: "S", mirror: false, kind };
}

// ── 職業の小道具（R110-S2）───────────────────────────────────────────────
/**
 * 職業（ui/core/archetype.js の roomArchetype）→ 頭（か胸）に重ねるタイル（ui/cockpit/tiles.js の PX_TILES のキー）。
 * セルに焼き込まない別タイル（px_lint のベンダー × コマを増やさない）。dev（打鍵の速さで見せる）と generic（該当なし＝
 * 嘘の帽子を被せない）は付けない＝鍵が無い。値は tools/px_lint.py --cockpit の ⑤ が「置かれているタイル」として読む。
 */
export const TOP_ARCH_TILES = Object.freeze({
  video: "propBeret",            // 監督のベレー
  design: "propBeretL",          // 明るい布のベレー
  audio: "propHeadset",          // 文字起こし・音声のヘッドホン
  support: "propHeadsetMic",     // 受付・広報のマイクつきヘッドセット
  writer: "propPencil",          // 耳あての段の鉛筆
  research: "propMortar",        // 角帽
  infra: "propHardhat",          // ヘルメット
  finance: "propEyeshade",       // アイシェード
  ops: "propBowtie",             // 胸の蝶ネクタイ
});
/**
 * 小道具のタイルの寸法 `{タイル名: [幅, 高さ]}`（委譲の契約＝design/gen_tiles.py が起こす寸法の**正本**・印の TOP_MARK_SIZE と
 * 同じ型）。キーは TOP_ARCH_TILES の値ちょうど。読む側: topPropAt・tests/cockpit_cells.test.mjs（PX_TILES と突き合わせる）。
 */
export const TOP_ARCH_SIZE = Object.freeze({
  propBeret: Object.freeze([8, 3]),
  propBeretL: Object.freeze([8, 3]),
  propHeadset: Object.freeze([8, 2]),
  propHeadsetMic: Object.freeze([9, 2]),
  propPencil: Object.freeze([3, 1]),
  propMortar: Object.freeze([8, 2]),
  propHardhat: Object.freeze([8, 3]),
  propEyeshade: Object.freeze([8, 2]),
  propBowtie: Object.freeze([4, 2]),
});
/**
 * 職業 → 机の上の小物（天板の右・ロボとモニタの脚の間）。動画＝タイムライン・書く人と音声（文字起こし）＝紙の束・
 * デザイン＝パレット。ほかの職業は小物を足さない。値は px_lint ⑤ が読む（描画は ui/cockpit/room.js の paintBand）。
 */
export const TOP_DESK_TILES = Object.freeze({
  video: "deskTimeline", writer: "deskStack", audio: "deskStack", design: "deskBrush",
});
/** 机の小物の寸法（TOP_DESK_TILES の値ちょうど・正本）。 */
export const TOP_DESK_SIZE = Object.freeze({
  deskTimeline: Object.freeze([6, 6]),
  deskStack: Object.freeze([6, 6]),
  deskBrush: Object.freeze([4, 3]),
});
/**
 * 小道具の置き場所の段（セルの中・左上からの列 dx と行の決め方）。
 *   head  頭の上端の行（headTopRow）に下端を重ねる・dx 2（頭の幅 8＝列 2..9）。9 幅のマイクは右へ 1 列はみ出す
 *   ear   頭の上端の行（ヘッドホンの耳あてと同じ段）・右の耳あての位置（列 7..9）
 *   chest 胴の上端（全ベンダー・全コマで行 7..8＝頭が 1px 上下しても胴は動かない）・列 4..7。正面の物＝奥へ歩く（N）ときは出さない
 */
const PROP_ANCHOR = Object.freeze({ propPencil: ["ear", 7], propBowtie: ["chest", 4] });
const CHEST_ROW = 7;

/**
 * 頭の上端の行＝列 2..9 に色が 4 つ以上並ぶ最初の行（アンテナの点＝codex・openclaw の 0..1 行を頭と取り違えない）。
 * 待機・打鍵の 2 コマ目は頭が 1px 下がる＝それに付いて小道具も下がる。見つからなければ 0。
 * @param {string[]} rows セル（12×16 の文字グリッド）
 */
export function headTopRow(rows) {
  if (!Array.isArray(rows)) return 0;
  const r = rows.findIndex((row) => (String(row).slice(2, 10).match(/[^.]/g) || []).length >= 4);
  return r < 0 ? 0 : r;
}

/**
 * 小道具を置く場所（左上・論理 px・整数）。**同じ入力なら同じ結果**（止め絵にも出す＝「誰か」は事実）。
 * @param {[number, number]|{x:number, y:number}} xy 人の左上（stepWalkers の drawn の x, y）
 * @param {string} prop roomArchetype の職業（TOP_ARCH_TILES に無ければ null）
 * @param {string[]} rows 貼るセル（頭の上端の行を求める）
 * @param {{mirror?: boolean, lift?: number, dir?: string}} [opts] topPose の mirror（W＝左右反転）・lift（挨拶の跳ねと
 *   立ち上がり＝ロボと一緒に上げる）・dir（N なら胸の物を出さない）
 * @returns {{tile: string, x: number, y: number, w: number, h: number, flip: boolean}|null}
 */
export function topPropAt(xy, prop, rows, opts = {}) {
  const x = Array.isArray(xy) ? xy[0] : xy?.x;
  const y = Array.isArray(xy) ? xy[1] : xy?.y;
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Object.hasOwn(TOP_ARCH_TILES, String(prop))) return null;
  const tile = TOP_ARCH_TILES[prop];
  const [w, h] = TOP_ARCH_SIZE[tile];
  const o = opts && typeof opts === "object" ? opts : {};
  const [how, dx] = PROP_ANCHOR[tile] || ["head", 2];
  if (how === "chest" && o.dir === "N") return null;
  const head = headTopRow(rows);
  const dy = how === "chest" ? CHEST_ROW : how === "ear" ? head : head - h + 1;
  const flip = o.mirror === true;
  const lift = Number.isInteger(o.lift) && o.lift > 0 ? o.lift : 0;
  return { tile, x: Math.round(x) + (flip ? CELL_W - dx - w : dx), y: Math.round(y) + dy - lift, w, h, flip };
}

/** 考え中の雲の点の送り（毎秒の段数）。3 段で 1.5 秒の周期（6・12fps の描画の格子で割り切れる）。 */
export const THINK_DOTS_FPS = 2;
/**
 * 考え中の雲の点（R110-S5）: 0（markThink）→ 1（markThink1）→ 2（markThink2）を THINK_DOTS_FPS で繰り返す。
 * 起点は印が出た t（roomMark の since＝考え始めを見た瞬間は点なしから）。since が無い（開いた時にもう考えていた）ときは
 * 個体の位相から。**呼ぶのは live のときだけ**（止め絵は markThink のまま＝golden 不変）。
 * @param {number} t 秒
 * @param {number|null} [since] 印が出た t
 * @param {number} [phase] 個体の開始位相（秒）
 * @returns {0|1|2}
 */
export function thinkDots(t, since = null, phase = 0) {
  const time = Number.isFinite(t) ? t : 0;
  const origin = Number.isFinite(since) && time >= since ? since : -(Number.isFinite(phase) && phase >= 0 ? phase : 0);
  // 1/1000 コマで丸めてから数える（6fps の格子の t から引いた経過秒が境目の手前に落ちない）
  return frame(Math.round((time - origin) * THINK_DOTS_FPS * 1000) / 1000, 3);
}

// ── 頭上の印 ─────────────────────────────────────────────────────────
/** 印（優先の高い順）。 */
export const TOP_MARKS = Object.freeze(["call", "warn", "done", "wait", "think"]);
/** 印 → タイル名（ui/cockpit/tiles.js の PX_TILES のキー）。対応の字面は pxtop.test.js が固定する（委譲の契約）。 */
export const TOP_MARK_TILES = Object.freeze({
  call: "markCall", warn: "markWarn", done: "markDone", wait: "markWait", think: "markThink",
});
/**
 * 印のタイルの寸法 `{タイル名: [幅, 高さ]}`（委譲の契約＝design/gen_tiles.py が起こす寸法の**正本**）。
 * キーは TOP_MARK_TILES の値ちょうど。読む側: tests/cockpit_cells.test.mjs（PX_TILES と突き合わせる）・
 * tools/px_lint.py --cockpit の ⑥（node で評価して読む＝名前を変えるなら px_lint の MARK_SIZE_EXPORT も同時に）・
 * topMarkAt（頭上の置き場所）。数字をほかへ書き写さない。
 */
export const TOP_MARK_SIZE = Object.freeze({
  markCall: Object.freeze([3, 7]),
  markWarn: Object.freeze([7, 7]),
  markDone: Object.freeze([7, 6]),
  markWait: Object.freeze([9, 7]),
  markThink: Object.freeze([9, 8]),
});
/** ✓ を出す長さ（秒）。working → 報告して待機（waiting かつ kind said）へ変わった瞬間から。 */
export const MARK_DONE_SEC = 8;
/**
 * 💬（返事待ち）を出すのは、報告からこの秒数未満だけ（古い待ちまで出すと部屋が吹き出しだらけになる）。
 * **統括の既定（本人が覆せる）**＝仕様（wave2-plan D-1）は「waiting かつ kind said」だけで上限を決めていない。
 * サーバーは age 25〜1800 秒を waiting にする（server/office_server.py の parse_session）＝600〜1800 秒の
 * 報告待ちは 💬 を出さず、台帳だけが持つ。本人の裁定一覧に載せる数字。
 */
export const MARK_WAIT_MAX_AGE = 600;
/**
 * 前回の記録がこの秒数より古い（または未来の）ときは「初めて見た」と同じに扱う。停止・非表示タブ・再生・
 * 固定時刻から live に戻った瞬間、その間に起きた遷移を**遅れて祝わない**ための門。roomMark は live の間
 * 描画のたび（数秒以内の間隔）に呼ぶ前提（データの取得周期でだけ呼ぶと、毎回「初めて見た」になって ✓ が出ない）。
 */
export const MARK_STALE_SEC = 4;
/**
 * ❗ が片付いた（前回 attention → 今は呼んでいない・standby に落ちたのではない）瞬間から ✓ を出す長さ（秒）。
 * R99-P4-L2: 承認・回答が済んで受付から机へ戻る歩行に付いて動く。報告の ✓（8 秒）とは別の起点。
 * R110-S4: 3 → 5。歩く速さを 24px/秒に落として、受付 → 机の歩行が立ち上がり（1/6 秒）込みで 3.7〜11.2 秒になった
 *（ui/core/pxwalk.js の planPath で全部の机 × 受付の組を測った値）＝帰り道の前半に ✓ を付けて歩く。
 */
export const MARK_CLEAR_SEC = 5;
/** 印が出た瞬間の「ぴょこっ」: 最初の 1/6 秒は 2px 上・次の 1/6 秒は 1px 上・以後は定位置（点滅ではない＝design/README の掟）。 */
export const MARK_POP = Object.freeze([2, 1]);

/** 報告して返事を待っている（waiting かつ kind said）。 */
const reported = (a) => a.state === "waiting" && a.kind === "said";

/**
 * ツールで止まっている（⚠）。stuckTool（サーバーは承認まちのときだけ出す＝その人は ❗ が勝つ）か、
 * 最後の出来事がツールのまま待機に落ちた人（kind tool かつ waiting＝サーバーはツールが 240 秒以上
 * 返ってこないと waiting にする・server/office_server.py の parse_session）。
 * 後者を入れないと、stuckTool を持つ人は全員 ❗ なので ⚠ は本番で 1 度も出ない（tests/cockpit_cells.test.mjs の
 * 固定ワールドの門が数える）。Codex は waiting を said で出す（server/source_codex.py）ので後者に掛からない。
 */
const stuck = (a) => Boolean(String(a.stuckTool ?? "").trim()) || (a.state === "waiting" && a.kind === "tool");

/**
 * memo のキー。roomLayout が振った `key`（session か id に、同じものが 2 度出たら `#n`）。無ければ session。
 * **id では追わない**（内訳の行は全員が同じ id を持つ＝.claude/rules/ui-2d.md）。どちらも無ければ追跡しない（✓ は出ない）。
 */
function markKey(a) {
  if (typeof a.key === "string" && a.key) return a.key;
  return typeof a.session === "string" ? a.session : "";
}

/** 記録が今の t から見て新しいか（未来の記録＝巻き戻しも古い扱い）。 */
function fresh(rec, t) {
  return Boolean(rec) && typeof rec === "object" && Number.isFinite(rec.seen)
    && rec.seen <= t && t - rec.seen <= MARK_STALE_SEC;
}

/**
 * 俯瞰のロボ 1 体の頭上の印を決める純関数。
 *
 * 印（優先: call > warn > done > wait > think）:
 *   call  attention かつ zone≠standby（❗・受付で呼んでいる）
 *   warn  ツールで止まっている（⚠・stuckTool が空でない、または waiting かつ kind tool）
 *   done  前回 working → 今 waiting かつ kind said の遷移から MARK_DONE_SEC 秒（✓）。**live のときだけ**
 *   wait  waiting かつ kind said かつ age（秒）< MARK_WAIT_MAX_AGE（💬 返事待ち）。age が数でなければ出さない
 *   think kind think（💭）。ただし state resting は出さない（ソファで寝ている人に考え中の泡は嘘）
 *   退場中（actor.leaving＝pxwalk.stepWalkers の drawn）の人は印を出さない（歩くだけ）＝null・memo も触らない
 *
 * memo は `{ [actor.key（無ければ session）]: {state, seen, doneAt, attention, clearAt, mark, since} }` の null プロトタイプの
 * object（前回までの記録）。clearAt＝❗ が片付いた起点（MARK_CLEAR_SEC 秒の ✓）・mark と since＝印とそれが出た t（ぴょこっ）。
 * 呼び手は戻り値の memo を次の呼び出しへ渡す（全員ぶんを 1 本の memo で回す:
 * `memo = roomMark(a, t, memo, {live}).memo`）。入力の memo は書き換えない（live なら新しい object を返す）。
 * - **live は「t が実時間で進んでいる」ときだけ true**。t が進まない間（prefers-reduced-motion の t 固定・停止ボタン・
 *   `?t=` の固定時刻・非表示タブ・再生）は必ず live=false＝memo を**そのまま**（同一の参照を）返し、done を出さない。
 * - **1 フレームに 1 人 1 回だけ呼ぶ**。✓ が出るのは「前回の記録から t が進んだ」呼び出しだけ＝同じ t の 2 回目以降は
 *   ✓ を出さない（t を固定したまま live=true で呼ばれても ✓ は居座らない・窓も延びない）。同じ t で working → 報告に
 *   変わった遷移も祝わない（時計が止まっている間の遷移＝停止中と同じ）。**同じ t で描き直す（ホバー・選択の変更など）
 *   ときは呼び直さず、その t で得た mark を使い回す**（呼び直すとその描画だけ ✓ が消える）。
 * - 初めて見たセッション（記録が無い・MARK_STALE_SEC より古い・未来）は記録するだけ＝開いた瞬間に全員が ✓ を出さない。
 * - live の呼び出しは、MARK_STALE_SEC より古い他人の記録を落とす（部屋から消えた人の記録が溜まらない）。
 *
 * @param {object} actor roomLayout().actors[i]（key / session / state / kind / zone / attention / stuckTool / age /
 *   leaving を読む）
 * @param {number} t 秒（UI クロック）
 * @param {object|null|undefined} memo 前回の戻り値の memo（初回は undefined か {}）
 * @param {{live?: boolean}} [opts] live: t が実時間で進んでいるときだけ true（既定 false）
 * @returns {{mark: "call"|"warn"|"done"|"wait"|"think"|null, memo: object, since: number|null}}
 *   since: 今の印が出た t（live で、前回の記録から時計が進んで印が変わった呼び出しの t を持ち越す。初めて見た人・live でない
 *   ときは null＝ぴょこっとしない。topMarkAt の opts.since へ渡す）
 */
export function roomMark(actor, t, memo, opts = {}) {
  const a = actor && typeof actor === "object" ? actor : {};
  if (a.leaving === true) return { mark: null, memo };
  const live = Boolean(opts && opts.live === true) && Number.isFinite(t);
  const key = markKey(a);
  let done = false;
  let next = memo;
  let rec = null;
  let prev = null;
  let moved = false;
  if (live && key) {
    const book = memo && typeof memo === "object" ? memo : {};
    prev = Object.hasOwn(book, key) && fresh(book[key], t) ? book[key] : null;
    // 前回の記録から時計が進んだか（同じ t の呼び出しは ✓ を始めも見せもしない）
    moved = prev !== null && t > prev.seen;
    let doneAt = null;
    if (prev && reported(a)) {
      // 前回 working → 今 報告して待機 ＝ ✓ の起点。すでに待機していたなら起点を持ち越す
      if (prev.state === "working") doneAt = moved ? t : null;
      else if (Number.isFinite(prev.doneAt)) doneAt = prev.doneAt;
    }
    // ❗ が片付いた（前回 attention → 今は呼んでいない・standby へ落ちたのではない）＝短い ✓ の起点（R99-P4-L2）
    let clearAt = null;
    if (prev && prev.attention === true && !a.attention && a.zone !== "standby") clearAt = moved ? t : null;
    else if (prev && !a.attention && Number.isFinite(prev.clearAt)) clearAt = prev.clearAt;
    const open = doneAt !== null && t >= doneAt && t - doneAt < MARK_DONE_SEC;
    const cleared = clearAt !== null && t >= clearAt && t - clearAt < MARK_CLEAR_SEC;
    done = (open || cleared) && moved;
    next = Object.create(null);
    for (const k of Object.keys(book)) {
      if (k !== key && fresh(book[k], t)) next[k] = book[k];
    }
    rec = {
      state: typeof a.state === "string" ? a.state : "",
      seen: t,
      // 窓が開いている間は起点を持ち越す（同じ t の呼び出しで見せなかっただけ＝時計が進めば続きを見せる）
      doneAt: open ? doneAt : null,
      attention: Boolean(a.attention) && a.zone !== "standby",
      clearAt: cleared ? clearAt : null,
    };
  }
  let mark = null;
  if (a.attention && a.zone !== "standby") mark = "call";
  else if (stuck(a)) mark = "warn";
  else if (done) mark = "done";
  else if (reported(a) && Number.isFinite(a.age) && a.age >= 0 && a.age < MARK_WAIT_MAX_AGE) mark = "wait";
  else if (a.kind === "think" && a.state !== "resting") mark = "think";
  let since = null;
  if (rec) {
    if (prev && prev.seen === t) {
      // 同じ t の 2 回目（描き直し）: 状態は今回の値で書く（止まっている間の遷移を後で遅れて祝わない）が、印とその t は
      // 1 回目のまま（✓ を見せないのはこの呼び出しだけ＝印の記録を書き換えない・ぴょこっの起点も動かさない）
      next[key] = Object.freeze({ ...rec, mark: prev.mark ?? null, since: Number.isFinite(prev.since) ? prev.since : null });
      since = mark && prev.mark === mark && Number.isFinite(prev.since) ? prev.since : null;
    } else {
      // 印が変わった（出た・替わった）瞬間の t を持ち越す＝ぴょこっ。初めて見た人（開いた瞬間）はぴょこっとしない
      if (mark && prev && prev.mark === mark) since = Number.isFinite(prev.since) ? prev.since : null;
      else if (mark && moved) since = t;
      next[key] = Object.freeze({ ...rec, mark, since });
    }
  }
  return { mark, memo: next, since };
}

/** ロボのセルの幅（ui/core/pxroom.js の ROOM.robot.w と同じ＝tests が突き合わせる。pxtop は import を持たない）。 */
const CELL_W = 12;

/**
 * 頭上の印を置く場所（左上・論理 px・整数）。セルの横の中央に揃え、頭の 1px 上に下端を置く。
 * 部屋の上端（y 0）より上へは出さない。**手前の什器より後に**描く（机に隠れない）のは描画側の掟。
 * @param {[number, number]|{x:number, y:number}} xy 人の左上（stepWalkers の drawn の x, y）
 * @param {string|null} mark roomMark(...).mark
 * @returns {{tile: string, x: number, y: number, w: number, h: number}|null} 印が無い・知らない印・xy が数でないなら null
 */
export function topMarkAt(xy, mark, opts = {}) {
  const x = Array.isArray(xy) ? xy[0] : xy?.x;
  const y = Array.isArray(xy) ? xy[1] : xy?.y;
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Object.hasOwn(TOP_MARK_TILES, String(mark))) return null;
  const tile = TOP_MARK_TILES[mark];
  const [w, h] = TOP_MARK_SIZE[tile];
  // ぴょこっ（R99-P4-L2）: 印が出た t（opts.since）から 1/6 秒ずつ MARK_POP の px だけ上へ。since が無ければ定位置（今の絵）
  const o = opts && typeof opts === "object" ? opts : {};
  let pop = 0;
  if (Number.isFinite(o.since) && Number.isFinite(o.t) && o.t >= o.since) {
    const step = Math.floor(Math.round((o.t - o.since) * 6 * 1000) / 1000);
    pop = step < MARK_POP.length ? MARK_POP[step] : 0;
  }
  return { tile, x: Math.round(x) + Math.floor((CELL_W - w) / 2), y: Math.max(0, Math.round(y) - h - 1 - pop), w, h };
}
