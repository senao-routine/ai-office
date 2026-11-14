// R99: 俯瞰オフィスを整数倍で描く（論理 184×160 → ×2 の 368×320 〜 ×4 の 736×640・既定は ×3 の 552×480）。
// 倍率は呼び手（index.js）が形から ui/core/pxroom.js の roomScale で選んで init の scale・setScale で渡す。
// ここは受け取った整数倍で塗るだけ（整数でない・範囲外の倍率は受けない＝割合の縮小・非整数のズームをしない掟）。
//
// 役割は 5 つだけ: 生きているか／どこに居るか／呼んでいるか／誰か（個体色）／何が変わったか。
// **名前も数字も canvas には描かない**（それは DOM の仕事）。tests/cockpit_smoke.py が
// fillText を throw に差し替えて機械で確かめる。
//
// 決定論: 位置は ui/core/pxroom.js（roomLayout）、歩き方は ui/core/pxwalk.js（stepWalkers）、
// コマと頭上の印は ui/core/pxtop.js（topPose・roomMark・topMarkAt）。どれも純関数。
// ここは「受け取った配置・コマ・印を整数倍で塗る」だけ＝同じ world と同じ t と同じ帳簿なら必ず同じ絵。
// 時刻は呼び手（index.js が ui/platform/clock.js から取る）が渡す＝ここで時計を読まない。
//
// 1 フレームの流れ（draw）:
//   roomLayout（目的地）→ errandTargets（用事＝道具に連動して本棚・掲示板・ボード・カウンターへ）→ lifeTargets（散歩）
//   → stepWalkers（今いる場所と歩行）→ 1 人ずつ roomMark（印）→ topPose（コマ）→ topMarkAt（印の場所）・topPropAt（職業の小道具）
//   → 背景 → 在席の机の床の光 → 足跡 → 人（＋小道具・助手）と手前の什器の段を足元の y で交互に（カウンターの段の直後に証拠の紙）
//   → 机の画面 → 印 → 選択の角かっこ → ホバーの足元の線
// **同じ t の描き直し**（新しいデータ・ホバー・選択）は、前の t の終わりの帳簿（base＝歩行・印の記録・退場中の個体色・
// unreachable の累計）から計算し直す＝冪等。
// roomMark を同じ t で 2 度進めない（進めるとその描画だけ ✓ が消え、同じ t に届いた遷移は祝われずに失われる）。
// 非表示のタブ（document.hidden）では描かない＝どの帳簿も進めない。
//
// 絵の掟（design/README.md が正本・破ると「塗り分けた図」に戻る）:
//   1. 色はランプ（RAMP）からだけ取る。物ごとに色を足さない。
//   2. 光は左上から。どの面も上と左が明るい。
//   3. 床を黒に寄せない（寄せると影も家具も黒の帯に潰れる）。
//   4. 立っている物は必ず影を落とす。
//   5. 描く順は 後ろの什器 → 人と手前の什器（足元の y で交互）→ 印。守らないと机の上に人が乗る／印が机に隠れる。
//   6. 人が居る机だけ画面が点く（絵に事実を語らせる）。
//   7. 家具の座標は ui/core/pxroom.js の FURNITURE が正本（扉・会議室の開口つきガラスも）。ここで数字を打たない。
//   8. 知らないコマ・無いベンダーのシートは**描かずに数える**（stats().missingCells）。別のコマで黙って代用しない。
//
// 描画コスト（stats().drawCalls＝メインの canvas に 1 フレームで呼んだ fillRect＋drawImage の実数）:
// 背景は光の段ごと、手前の什器は段ごと（pxroom.FRONT_BANDS）に在席や机上の小物が変わったときだけ焼く。ロボ 1 体は fillRect を
// 約 150 回呼ぶので、(ベンダー, セル, 個体色, 左右) ごとに 1 回だけオフスクリーンへ焼き、以後は drawImage 1 回。
// 印と足跡のタイルも同じく焼き置き（1 枚 drawImage 1 回）。
// R102-10: 相対パスで import する（スマホは /u/<BUILD>/cockpit/room.js＝版つきの道で読む。ui/pwa/bootpx.js の冒頭）。
import { DESKS, ERRAND_SPOTS, EVIDENCE_TRAY, FRONT_BANDS, FURNITURE, PICK_RADIUS_PX, ROOM as GRID, ROOM_SCALE_FLOOR,
  ROOM_SCALE_WATCH_MAX, SLOTS, pickNearest, roomLayout, tintHash } from "../core/pxroom.js";
import { FOOT_CENTER, WALK_SPEED, WALK_STRIDE, posAt, roomFps, stepWalkers } from "../core/pxwalk.js";
import { GREET_SEC, PHASE_STEPS, TOP_DESK_SIZE, TOP_DESK_TILES, TOP_MARKS, roomMark, screenFrame, thinkDots, topMarkAt,
  topPose, topPropAt, topVendor, typePace } from "../core/pxtop.js";
import { lifeTargets } from "../core/life.js";
import { EVIDENCE_ERRAND, errandTargets, toolClassOf } from "../core/errand.js";
import { PX_CELLS, PX_CELL_H, PX_CELL_W } from "./cells.js";
import { PX_TILES } from "./tiles.js";
import { PALETTE, RAMP, ROOM_LIGHT, SESSION_COLORS, TILE_PALETTE, dimTint } from "./palette.js";

const W = GRID.w, H = GRID.h;
/** 既定の倍率（3 カラムの ×3・golden cockpit.png）。形ごとの倍率は呼び手が roomScale で選んで渡す */
const DEFAULT_SCALE = GRID.scale;
/** 受けてよい倍率＝ROOM_SCALE_FLOOR〜WATCH_MAX の整数だけ（roomScale の値域と同じ・×5〜×6 は眺めるモードだけ＝R110-S6）。それ以外では描かない */
const validScale = (k) => Number.isInteger(k) && k >= ROOM_SCALE_FLOOR && k <= ROOM_SCALE_WATCH_MAX;   // ×1 は PWA の狭い窓だけ（R102-1）

const ROOM = {
  base: RAMP.ink[0],
  floor: RAMP.stone[2], shadow: RAMP.ink[0],
  glowFloor: RAMP.amber[1],
};
const GLASS_PALETTE = {
  ...TILE_PALETTE,
  e: RAMP.glass[0], f: RAMP.glass[1], g: RAMP.glass[2], h: RAMP.glass[3],
};
const MORNING_WINDOW = { ...TILE_PALETTE,
  e: RAMP.dawn[0], f: RAMP.dawn[1], g: RAMP.dawn[2], h: RAMP.dawn[3],
};
const EVENING_WINDOW = { ...TILE_PALETTE,
  e: RAMP.dusk[0], f: RAMP.dusk[1], g: RAMP.dusk[2], h: RAMP.dusk[3],
};
const NIGHT_WINDOW = { ...TILE_PALETTE,
  e: RAMP.moon[0], f: RAMP.moon[1], g: RAMP.moon[2], h: RAMP.moon[3],
};
const WINDOW_PAINTS = { morning: MORNING_WINDOW, day: GLASS_PALETTE, evening: EVENING_WINDOW, night: NIGHT_WINDOW };
const ERROR_PAINT = { ...TILE_PALETTE, r: RAMP.red[1], s: RAMP.red[2], t: RAMP.red[3] };
const ASSISTANT_PAINT = { ...TILE_PALETTE,
  e: RAMP.bot[0], f: RAMP.bot[1], g: RAMP.bot[2], h: RAMP.bot[3],
};
const FLOOR_PALETTE = {
  ...TILE_PALETTE,
  v: RAMP.stone[1], w: RAMP.stone[2], x: RAMP.stone[3],
};
const PAPER = { ...TILE_PALETTE, y: RAMP.shell[1] };
/** 配置表の paint → 凡例の染め方（null は TILE_PALETTE） */
const PAINTS = { glass: GLASS_PALETTE, paper: PAPER, floor: FLOOR_PALETTE };
const colorsOf = (f) => PAINTS[f.paint] || TILE_PALETTE;
/**
 * 呼んでいる印（❗・markCall）だけは確認待ちの色（RAMP.signal）で染める＝台帳・タブ・一覧の ❗（--cp-alert＝signal[2]）と
 * 同じ色（palette.js「UI とオフィスの印で同じ色を使う」）。点滅はさせない（design/README.md「受付の印は点滅させず」）。
 * ほかの印（⚠ 琥珀・✓ 緑・💬 💭 鋼）はタイルの凡例のまま。
 */
const CALL_PAINT = { ...TILE_PALETTE, t: RAMP.signal[3], s: RAMP.signal[2] };
const MARK_COLORS = { call: CALL_PAINT };
const BACK = FURNITURE.filter((f) => f.layer === "back");
// 光（給湯台の暖色・卓上灯・点いた画面の照り返し）も表の行が持つ（glow＝行の (x, y) からの相対）。
// 段が RAMP.amber に無いと fillStyle の代入が黙って無視され、直前の色で塗られる＝読み込み時に落とす。
for (const f of FURNITURE) {
  for (const g of f.glow || []) {
    if (!RAMP.amber[g[5]]) throw new Error(`pxroom.FURNITURE ${f.id} の glow の段 ${g[5]} が RAMP.amber に無い`);
  }
}

// ── 手前の什器の段（C3）────────────────────────────────────────────────
// 段（pxroom.FRONT_BANDS・奥 → 手前）ごとに外接矩形を焼く。段に入らない手前の行があると、その家具は黙って消える
// ＝読み込み時に落とす（表の正本は pxroom・段の分け方も pxroom）。
const BY_ID = new Map(FURNITURE.map((f) => [f.id, f]));
{
  const banded = new Set(FRONT_BANDS.flatMap((b) => b.ids));
  for (const f of FURNITURE) {
    if (f.layer === "front" && !banded.has(f.id)) throw new Error(`pxroom.FURNITURE ${f.id}（front）がどの段（FRONT_BANDS）にも無い`);
  }
}
/** タイルの寸法 [幅, 高さ]（無い・壊れたタイルは null） */
const tileSize = (name) => {
  const rows = PX_TILES[name];
  return Array.isArray(rows) && rows.length ? [Math.max(...rows.map((r) => String(r).length)), rows.length] : null;
};
/**
 * 職業の机の小物（R110-S2・TOP_DESK_TILES）の左上。天板（机の行 0..4）の右寄り・ロボ（机の x+5..x+16）の右隣・モニタの脚
 *（x+23..x+24）の左。下端は天板の手前から 1 行奥（机の行 3）＝天板の上に載って見える。
 */
const DESK_PROP_DX = 17;
const DESK_PROP_BASE = 3;
function deskPropAt(f, h) {
  return [f.x + DESK_PROP_DX, f.y + DESK_PROP_BASE - h + 1];
}

const BANDS = FRONT_BANDS.map((b) => {
  const rows = b.ids.map((id) => BY_ID.get(id)).filter(Boolean);
  let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
  const grow = (x, y, w, h) => {
    if (!(w > 0 && h > 0)) return;
    x1 = Math.min(x1, x); y1 = Math.min(y1, y); x2 = Math.max(x2, x + w); y2 = Math.max(y2, y + h);
  };
  for (const f of rows) {
    for (const name of [f.tile, f.lit]) {
      const sz = name ? tileSize(name) : null;
      if (sz) grow(f.x, f.y, sz[0], sz[1]);
    }
    for (const [dx, dy, w, h] of f.glow || []) grow(f.x + dx, f.y + dy, w, h);
    // 仕事の小物と赤い紙も、その机の段に焼く。種類が変わっても外接矩形は同じ。
    if (f.tile === "desk") {
      for (const name of ["deskScreen", "deskDocuments", "deskBoard"]) {
        const sz = tileSize(name);
        if (sz) grow(f.x, f.y + 1 - sz[1], sz[0], sz[1]);
      }
      const sz = tileSize("errorNote");
      if (sz) grow(f.x + 11, f.y, sz[0], sz[1]);
      // R110-S2: 職業の机の小物（天板の右・ロボとモニタの脚の間）
      for (const [w, h] of Object.values(TOP_DESK_SIZE)) {
        const [x, y] = deskPropAt(f, h);
        grow(x, y, w, h);
      }
    }
  }
  return Object.freeze({ key: b.key, depth: b.depth, rows, box: x1 < x2 ? [x1, y1, x2 - x1, y2 - y1] : null });
});

/** 行の光を塗る（タイルの直前）。座標は行の (x, y) からの相対＝家具を動かすと光も付いて動く。(ox, oy) は焼き先のずれ。
 *  S は倍率（以下の塗る関数はどれも、焼き先の倍率を引数で受ける＝1 つのモジュールで複数の倍率を焼ける）。 */
function paintGlow(ctx, S, f, ox = 0, oy = 0) {
  for (const [dx, dy, w, h, alpha, step] of f.glow) {
    ctx.fillStyle = RAMP.amber[step];
    ctx.globalAlpha = alpha;
    ctx.fillRect((f.x + dx - ox) * S, (f.y + dy - oy) * S, w * S, h * S);
  }
  ctx.globalAlpha = 1;
}

/**
 * 個体色の既定（ハッシュの剰余だけ）。本番は index.js が tintIndex で衝突を解いた `colorOf` を渡す
 *（部屋・タブ・一覧で 1 本の割り当てを共有する）。これは単体で使うときの受け止め。
 */
const hashTint = (key) => SESSION_COLORS[tintHash(key) % SESSION_COLORS.length];

/**
 * 選択（中央の端末が映しているセッション）の印＝頭上の角かっこの矩形 [x, y, w, h]（人の左上からの論理 px）。
 * design/cockpit.html の選択の印と同じ形。印のタイル（topMarkAt＝セルの横の中央）とは横に重ならない。
 */
export const SELECT_MARK = Object.freeze([[-2, -3, 3, 1], [-2, -3, 1, 3], [PX_CELL_W - 1, -3, 3, 1], [PX_CELL_W + 1, -3, 1, 3]]
  .map((r) => Object.freeze(r)));
/** ホバー（台帳の行に乗っている人）の印＝足元の線 [x, y, w, h]。選択（頭上の角かっこ）と描き分ける。手前の什器より後に描く。 */
export const HOVER_LINE = Object.freeze([1, PX_CELL_H, PX_CELL_W - 2, 1]);

// ── 足跡 ───────────────────────────────────────────────────────────
/**
 * 足跡の間隔（論理 px）＝歩行の 1 周（R110-S4 で歩幅 12→8px にしたとき、半周 4px のままだと足跡が詰まって帯になる＝
 * 1 周 8px で旧の半周 6px に近い密度を保つ）。
 */
export const PRINT_STEP = WALK_STRIDE;
/** 1 人が残す足跡の数（直近だけ）と、1 フレームの上限（drawCalls の上限 300 に入れる）。 */
export const PRINTS_PER_WALKER = 3;
export const PRINTS_MAX = 30;

/**
 * 歩いている人の直近の足跡（footstep タイル 2×2）の左上（論理 px・整数）。**純関数**。
 * 足跡は経路の上の固定の距離（PRINT_STEP の倍数）に置く＝人が進んでも床の上で滑らない。足元から 1 歩（PRINT_STEP）以上
 * 後ろの直近 n 個（新しい順）。左右の足を交互に: 縦に歩く（N・S）ときは横に、横に歩く（E・W）ときは縦に振る。
 * @param {Array<[number, number]>} path 帳簿の記録の経路（左上の折れ線・pxwalk.planPath の戻り値）
 * @param {number} t0 歩き出した時刻（秒）
 * @param {number} t 今の時刻（秒）
 * @param {number} [n] 数
 * @returns {Array<[number, number]>}
 */
export function footprints(path, t0, t, n = PRINTS_PER_WALKER) {
  if (!Array.isArray(path) || path.length < 2 || !Number.isFinite(t0) || !Number.isFinite(t) || !(t >= t0)) return [];
  // 進んだ距離の丸めは pxwalk.posAt と同じ（描画の刻みの浮動小数の誤差で 1 歩手前に落ちない）
  const dist = Math.round((t - t0) * WALK_SPEED * 1e6) / 1e6;
  const out = [];
  for (let k = Math.floor(dist / PRINT_STEP) - 1; k >= 0 && out.length < n; k -= 1) {
    const p = posAt(path, 0, k * PRINT_STEP, 1);             // 経路の上の距離 k×PRINT_STEP の点
    if (!p || p.done) continue;
    const cx = p.x + FOOT_CENTER[0], cy = p.y + FOOT_CENTER[1];
    const side = k % 2;
    out.push(p.dir === "N" || p.dir === "S" ? [side ? cx + 1 : cx - 3, cy - 1] : [cx - 1, side ? cy - 2 : cy - 1]);
  }
  return out;
}

// ── 用事と証拠の紙（R110-S5）──────────────────────────────────────────────
/**
 * 部屋全体で同時に机を離れてよい人数（用事＋散歩）。用事（ui/core/errand.js・既定 2 人まで）が先に取り、散歩
 *（ui/core/life.js）は残り（AWAY_MAX − 用事の人数）。席の割り当て・人数・在席はどちらも変えない。
 */
export const AWAY_MAX = 3;
/** 証拠の紙をカウンターに残す秒数（最大 EVIDENCE_TRAY.length 枚・古い順に消える）。 */
export const EVIDENCE_PAPER_SEC = 600;
/** 証拠の紙を描く段（pxroom.FRONT_BANDS のカウンターの段の鍵）＝カウンターの天板の上 */
const COUNTER_BAND = "counter";
const COUNTER_SPOT = ERRAND_SPOTS.find((s) => s.name === EVIDENCE_ERRAND.spot) || null;
if (!FRONT_BANDS.some((b) => b.key === COUNTER_BAND)) throw new Error(`pxroom.FRONT_BANDS に ${COUNTER_BAND} の段が無い（証拠の紙を置けない）`);

/**
 * 証拠の紙の帳簿を 1 フレームぶん進める（**純関数**・live のときだけ呼ぶ）。カウンターの行き先に立った人（歩き終えた・
 * 立ち上がっていない・退場中でない）が、その用事 1 回（用事の記録の出発の t＝lastGo）につき 1 枚置く。置いた t から
 * EVIDENCE_PAPER_SEC で消え（巻き戻った紙も消す）、EVIDENCE_TRAY が埋まっていればいちばん古い紙をどけてその場所へ置く
 *（残りの紙は動かない）。同じ t・同じ入力で呼び直しても同じ結果（置いた紙は id で 1 枚）。
 * @param {Array<{id: string, t: number, slot: number}>} prev 前の帳簿
 * @param {object[]} drawn stepWalkers(...).drawn（errand・x・y・walk・lift・leaving・key を読む）
 * @param {Map<string, object>|null} book errandTargets(...).book（出発の t を読む）
 * @param {number} t 秒
 * @returns {Array<{id: string, t: number, slot: number}>}
 */
export function trayPapers(prev, drawn, book, t) {
  let next = (Array.isArray(prev) ? prev : []).filter((p) => p && Number.isFinite(p.t) && Number.isInteger(p.slot)
    && p.slot >= 0 && p.slot < EVIDENCE_TRAY.length && t >= p.t && t - p.t < EVIDENCE_PAPER_SEC);
  if (!COUNTER_SPOT || !Number.isFinite(t)) return next;
  for (const d of Array.isArray(drawn) ? drawn : []) {
    if (!d || d.errand !== EVIDENCE_ERRAND.spot || d.walk || d.lift > 0 || d.leaving
      || d.x !== COUNTER_SPOT.x || d.y !== COUNTER_SPOT.y) continue;
    const id = `${d.key}@${book instanceof Map ? book.get(d.key)?.lastGo ?? "" : ""}`;
    if (next.some((p) => p.id === id)) continue;
    const used = new Set(next.map((p) => p.slot));
    let slot = EVIDENCE_TRAY.findIndex((_, i) => !used.has(i));
    if (slot < 0) {
      const oldest = next.reduce((a, b) => (b.t < a.t || (b.t === a.t && b.slot < a.slot) ? b : a));
      slot = oldest.slot;
      next = next.filter((p) => p !== oldest);
    }
    next = [...next, Object.freeze({ id, t, slot })];
  }
  return next;
}

/**
 * 同じ文字グリッドを材質のランプで染める。形ごとの新色は作らない。無い・壊れたタイルは false（何も塗らない）。
 * flip で左右を鏡に（職業の小道具を左へ歩く人＝W の鏡のロボに合わせる）。鏡の軸はタイルの幅（いちばん長い行）。
 */
function tile(ctx, S, rows, x, y, colors = TILE_PALETTE, flip = false) {
  if (!Array.isArray(rows)) return false;
  const w = flip ? Math.max(0, ...rows.map((r) => String(r).length)) : 0;
  for (let ry = 0; ry < rows.length; ry += 1) {
    const row = String(rows[ry]);
    for (let rx = 0; rx < row.length; rx += 1) {
      const c = colors[row[rx]];
      if (!c) continue;
      ctx.fillStyle = c;
      ctx.fillRect((x + (flip ? w - 1 - rx : rx)) * S, (y + ry) * S, S, S);
    }
  }
  return true;
}

/** 1 体（12×16）を塗る。`a` は個体色、`c` はその暗いほう、他は PALETTE。flip で左右を鏡に。 */
function blit(ctx, S, rows, x, y, tint, flip = false) {
  const dim = dimTint(tint);
  for (let ry = 0; ry < rows.length; ry += 1) {
    const row = rows[ry];
    for (let rx = 0; rx < row.length; rx += 1) {
      const ch = row[rx];
      if (ch === ".") continue;
      const c = ch === "a" ? tint : ch === "c" ? dim : PALETTE[ch];
      if (!c) continue;
      ctx.fillStyle = c;
      ctx.fillRect((x + (flip ? row.length - 1 - rx : rx)) * S, (y + ry) * S, S, S);
    }
  }
}

/** 人の後ろに来るもの。壁・床・光・奥の什器（扉・会議室の開口つきガラスも表から）・空席の椅子。光の段が変わると焼き直す。 */
function paintBack(ctx, S, missing, stage) {
  const light = ROOM_LIGHT[stage];
  const R = (x, y, w, h, c) => { ctx.fillStyle = c; ctx.fillRect(x * S, y * S, w * S, h * S); };
  const T = (name, x, y, colors) => { if (!tile(ctx, S, PX_TILES[name], x, y, colors)) missing(name); };

  R(0, 0, W, H, ROOM.floor);
  // 継ぎ目を半枚ずらす。床の模様より、人と家具の輪郭を先に読める密度にする。
  for (let y = 16, row = 0; y < H; y += 12, row += 1) {
    for (let x = row % 2 ? -12 : 0; x < W; x += 24) T("floor", x, y, FLOOR_PALETTE);
  }
  R(0, 15, 3, H - 15, RAMP.stone[0]);
  R(181, 15, 3, H - 15, RAMP.stone[0]);
  R(3, 157, 178, 3, RAMP.stone[0]);

  // 窓から左上→右下へ伸びる冷たい光。ぼかしを使わず整数の段で切る。
  ctx.globalAlpha = light.alpha;
  for (const x of [8, 38, 68]) {
    for (let k = 0; k < 8; k += 1) {
      R(x + k * 3, 15 + k * 6, 22, 6, light.beam);
      R(x + k * 3 + 10, 15 + k * 6, 1, 6, light.beamShade);
    }
  }
  ctx.globalAlpha = 1;

  // 右の通路は縁の 1px だけ。塗り潰した帯で部屋を分断しない。
  R(135, 43, 1, 84, RAMP.stone[1]);
  R(8, 128, 164, 1, RAMP.stone[1]);
  R(0, 0, W, 15, light.wall);
  R(0, 0, W, 1, light.wallLit);
  R(0, 14, W, 2, ROOM.base);

  // 家具（窓・時計・掲示板・扉・ソファ・ガラス・植物・ラック・ボード・給湯・空席の椅子）は配置表の順に。
  // 光（給湯台の床の暖色）は行のタイルの直前＝台そのものの上には乗らない。
  for (const f of BACK) {
    if (f.glow) paintGlow(ctx, S, f);
    T(f.tile, f.x, f.y, f.tile === "window" ? WINDOW_PAINTS[stage] : colorsOf(f));
  }
}

/**
 * 人が居る席だけ床へ光を落とす。`fill` はメインの canvas の fillRect（呼んだ数を数える口）。
 * 毎フレーム描く（机の段ごとに α が違う＝焼き置きの 1 枚に畳むと手前の什器と順序が入れ替わる）。
 */
function paintLight(ctx, S, fill, occupied) {
  for (const i of occupied) {
    const d = DESKS[i];
    if (!d) continue;
    // モニタは右側。明るさは机の段で少し落とし、全席同じ光だまりにしない。
    ctx.globalAlpha = 0.10 + (2 - Math.floor(i / GRID.desk.cols)) * 0.025;
    ctx.fillStyle = ROOM.glowFloor;
    for (let k = 0; k < 3; k += 1) {
      fill((d.x + 15 + k * 2) * S, (d.y + 12 + k * 2) * S, (17 - k * 4) * S, 2 * S);
    }
  }
  ctx.globalAlpha = 1;
}

/** 机の人（か予約している人）の職業の机の小物のタイル名（無ければ null）。 */
const deskPropOf = (a) => (a && Object.hasOwn(TOP_DESK_TILES, String(a.prop)) ? TOP_DESK_TILES[a.prop] : null);

/** 手前の什器の段 1 つを焼き先へ塗る（(ox, oy) は焼き先の左上の論理 px）。上半身 11px を残し、腰から下だけを隠す。 */
function paintBand(ctx, S, band, occupied, decor, ox, oy, missing) {
  const T = (name, x, y, colors) => { if (!tile(ctx, S, PX_TILES[name], x - ox, y - oy, colors)) missing(name); };
  for (const f of band.rows) {
    // 分類できた仕事は既存の小物と差し替える。分類の無い机・空席は元のまま。
    if (decor.get(f.seat)?.deskWork && f.on === `desk${f.seat}` && !f.lit) continue;
    const lit = Boolean(f.lit) && occupied.has(f.seat);
    // 卓上灯がカウンターに落とす光・点いた画面が天板に乗せる 1px。`lit` を持つ行（モニタ）は点いているときだけ
    //（消えた画面は光らない）。タイルの直前＝机の後・モニタの前（先に置くと天板に隠れる）。
    if (f.glow && (!f.lit || lit)) paintGlow(ctx, S, f, ox, oy);
    const name = lit ? f.lit : f.tile;
    if (!tile(ctx, S, PX_TILES[name], f.x - ox, f.y - oy, colorsOf(f))) missing(name);
  }
  for (const f of band.rows) {
    if (f.tile !== "desk") continue;
    const item = decor.get(f.seat);
    if (item?.deskWork === "implementation") T("deskScreen", f.x, f.y - 5);
    else if (item?.deskWork === "research") T("deskDocuments", f.x, f.y - 4);
    else if (item?.deskWork === "planning") T("deskBoard", f.x, f.y - 6);
    // R110-S2: 職業の机の小物（動画＝タイムライン・書く人と文字起こし＝紙の束・デザイン＝パレット）。止め絵にも出す（職業は事実）
    const prop = deskPropOf(item);
    if (prop) {
      const [x, y] = deskPropAt(f, TOP_DESK_SIZE[prop][1]);
      T(prop, x, y);
    }
    if (item?.errorNote) T("errorNote", f.x + 11, f.y, ERROR_PAINT);
  }
}

/**
 * 席の割り当て（roomLayout の `assign`）の帳簿。live と再生（ダイジェストの巻き戻し）で**別々**に持つ:
 *   - 再生の割り当てを live へ持ち帰ると、再生を閉じた瞬間に全員が再生時の席へ飛ぶ
 *   - live の割り当てを再生へ持ち込むと、過去の部屋が「今の席」で描かれる（再生の初回は前回なし）
 * live のフレームを描いたら再生の分は捨てる＝次の再生はまた前回なしから始まる。
 * keepPrev=false（固定時刻のページ）は常に前回なし＝同じ world なら必ず同じ配置（golden）。
 * 口は `layout` の 1 つだけ＝前回を読む側と残す側へ**同じ旗を 1 回だけ**渡す（読む・残すを別の口にすると、
 * 呼び出し口で片方の旗だけ食い違っても、どちらの門にも写らなかった）。
 * 門: tests/cockpit_room.test.mjs（帳簿そのものと、init().draw の { replay } が帳簿まで届くこと）。
 * @param {boolean} keepPrev
 * @param {Function} layoutOf 配置の関数（既定 roomLayout。テストが「どの前回を渡したか」を覗く差し替え口）
 */
export function assignBook(keepPrev = true, layoutOf = roomLayout) {
  let live = null;
  let replay = null;
  return {
    /** 1 フレームぶん配置する: その側（再生か live か）の前回で並べ、割り当てを同じ側へ残す */
    layout(world, board, isReplay = false) {
      const out = layoutOf(world, board, isReplay ? replay : live);   // keepPrev=false なら両方とも null のまま
      if (keepPrev) {
        if (isReplay) replay = out.assign;
        else { live = out.assign; replay = null; }
      }
      return out;
    },
  };
}

/** オフスクリーンの canvas（背景・手前・ロボ・印の焼き置き）。ぼかさない。 */
function offscreen(w, h) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const x = c.getContext("2d");
  x.imageSmoothingEnabled = false;
  return [c, x];
}

const emptyMarks = () => Object.fromEntries(TOP_MARKS.map((m) => [m, 0]));

/**
 * 部屋を host に作って、draw で描く。
 * @param {object} opts
 *   host      HTMLElement
 *   frozen    () => boolean  **t が進まない間**か（固定時刻 ?t=・「動きを止める」・prefers-reduced-motion）。
 *             true の間は動きの時計を 0 に固定し、歩かせず（全員を目的地）、✓ も出さない（印の live=false）
 *   keepPrev  前回の席の割り当てを次のフレームへ持ち越すか。live は true（誰かが ❗ になっても
 *             他の人の席が動かない）。固定時刻のページは false＝同じ world なら必ず同じ配置（golden）
 *   colorOf   (projectKey) => 色。index.js が tintIndex で衝突を解いた 1 本を渡す（タブ・一覧と同じ色）。
 *             退場中の人には引かない（台帳から落ちたキーはハッシュの剰余に戻る）＝その人の直近の色で描く
 *   scale     倍率（ROOM_SCALE_FLOOR〜MAX の整数・index.js が roomScale で形から選ぶ・×1 は PWA だけ）。無い・整数でない・範囲外は
 *             既定の ROOM.scale（×3）。後から替えるのは setScale（焼き置きだけ作り直し、帳簿は持ち越す）
 * 非表示のタブ（document.hidden）の間は draw が何もしない（下の draw の冒頭）。
 */
export function init({ host, frozen = () => false, keepPrev = true, colorOf = hashTint, scale, life = true } = {}) {
  let S = validScale(scale) ? scale : DEFAULT_SCALE;
  const canvas = document.createElement("canvas");
  canvas.className = "cproom";
  canvas.width = W * S;
  canvas.height = H * S;
  canvas.setAttribute("aria-hidden", "true");   // 情報は DOM（台帳・一覧）が持つ
  host.append(canvas);
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;

  // メインの canvas へ塗る口はこの 2 つだけ＝stats().drawCalls は推定でなく実際に呼んだ数
  //（tests/cockpit_smoke.py が CanvasRenderingContext2D を外から包んで同じ数になるかを見る）。
  // layers は同じ口で控える「何をどの順で塗ったか」（debug・tests/cockpit_room.test.mjs の重なり順の門）。
  let calls = 0;
  let layers = [];
  const fill = (x, y, w, h) => { calls += 1; ctx.fillRect(x, y, w, h); };
  const put = (img, x, y, what) => { calls += 1; ctx.drawImage(img, x, y); if (what) layers.push(what); };

  // 描けなかったタイル（生成物に無い名前）。黙って代用せず数える（累計）。
  let missingTiles = 0;
  const missTile = () => { missingTiles += 1; };

  // 背景は倍率と光の段が変わったときだけ焼く（毎フレーム全塗りすると、この密度では描画が支配的になる）。
  let bg = null;
  let bgBakes = 0;
  let lightStage = "day";
  const bakeBack = () => {
    let x;
    [bg, x] = offscreen(canvas.width, canvas.height);
    paintBack(x, S, missTile, lightStage);
    bgBakes += 1;
  };
  // 手前の什器は在席・小物・赤い紙か倍率が変わったときだけ焼き直す（数えるのは焼き直しの回数）。
  let bandCanvases = [];
  let fgKey = null;
  let fgBakes = 0;
  // ロボと印・足跡の焼き置き（下の spriteOf・tileSprite）。倍率を替えたら捨てる
  const sprites = new Map();
  const tileSprites = new Map();
  let spriteBakes = 0;
  /** 今の倍率 S で焼き置きの器を用意する（背景を焼き・段の器を作り・スプライトを捨てる）。init と setScale だけが呼ぶ */
  const bakeScale = () => {
    canvas.width = W * S;
    canvas.height = H * S;
    ctx.imageSmoothingEnabled = false;   // canvas の寸法を替えると 2D の状態が既定（true）に戻る
    bg = null;                           // 背景は最初の draw で焼く（init の時点では光の段が分からない＝昼で焼いて朝・夕・夜に焼き直していた）
    bandCanvases = BANDS.map((b) => (b.box ? offscreen(b.box[2] * S, b.box[3] * S) : null));
    fgKey = null;                        // 次の描画で段を焼き直す（在席の集合が同じでも器が新しい）
    sprites.clear();
    tileSprites.clear();
  };
  bakeScale();
  const bakeFront = (occupied, decor) => {
    const key = [...occupied].sort((a, b) => a - b).join(",") + ";"
      + [...decor].sort(([a], [b]) => a - b)
        .map(([seat, d]) => `${seat}:${d.deskWork || ""}:${d.errorNote ? 1 : 0}:${deskPropOf(d) || ""}`).join(",");
    if (key === fgKey) return;
    fgKey = key;
    fgBakes += 1;
    BANDS.forEach((b, i) => {
      const c = bandCanvases[i];
      if (!c) return;
      c[1].clearRect(0, 0, c[0].width, c[0].height);
      paintBand(c[1], S, b, occupied, decor, b.box[0], b.box[1], missTile);
    });
  };

  // ロボは (ベンダー, セル, 個体色, 左右) ごとに 1 回だけ焼く。種類は 3 × 24 × 色 9 ×（左右は歩行 E の 4 コマだけ）で頭打ち
  //（ベンダーは topVendor が 3 つに畳み、セルは topPose が TOP_CELLS から、色は SESSION_COLORS から＝外から来る文字列で増えない）。
  // 不透明なドットを整数座標へ等倍で貼るので、fillRect で直接塗った絵とビット一致する。
  const spriteOf = (vendor, cell, rows, tint, flip) => {
    const key = `${vendor}|${cell}|${tint}|${flip ? 1 : 0}`;
    let c = sprites.get(key);
    if (!c) {
      let x;
      [c, x] = offscreen(PX_CELL_W * S, PX_CELL_H * S);
      blit(x, S, rows, 0, 0, tint, flip);
      sprites.set(key, c);
      spriteBakes += 1;
    }
    return c;
  };
  // 印・足跡・小道具・紙のタイルも焼き置き（無いタイルは null＝描かずに数える）。flip＝左右の鏡（小道具の W 向き）。
  // 凡例の色は既定値でだけ受ける（design/check.py の門: TILE_PALETTE を関数へ渡さない）＝鏡は propSprite の口から
  const bakeTile = (name, flip, colors = TILE_PALETTE) => {
    const key = `${name}|${colors === CALL_PAINT ? "call" : colors === ASSISTANT_PAINT ? "assistant" : ""}|${flip ? 1 : 0}`;
    if (tileSprites.has(key)) return tileSprites.get(key);
    const sz = tileSize(name);
    let c = null;
    if (sz) {
      let x;
      [c, x] = offscreen(sz[0] * S, sz[1] * S);
      tile(x, S, PX_TILES[name], 0, 0, colors, flip);
      spriteBakes += 1;
    }
    tileSprites.set(key, c);
    return c;
  };
  const tileSprite = (name, colors = TILE_PALETTE) => bakeTile(name, false, colors);
  /** 職業の小道具（凡例の色のまま・W は鏡） */
  const propSprite = (name, flip) => bakeTile(name, flip === true);
  // 机の画面の模様（R99-P4-L3・R110-S5）: 種類 → 送りの 2 コマ（screenFrame の 1・2）の [タイル名, 焼き置き]。
  // タイル名は字面で呼ぶ（tools/px_lint.py の ⑤ が「描画のコードが直接描くタイル」として数える）。
  //   think＝考え中の点・type＝打鍵の行（既定）・bash＝墨の地に緑の行（Bash）・edit＝差分の行（Edit・Write）・
  //   read＝めくれる紙（Read・Grep・Glob と読む動詞）・web＝上の帯と 2 段の行（WebFetch・WebSearch）
  const SCREENS = {
    think: [["screenThink0", () => tileSprite("screenThink0")], ["screenThink1", () => tileSprite("screenThink1")]],
    type: [["screenType0", () => tileSprite("screenType0")], ["screenType1", () => tileSprite("screenType1")]],
    bash: [["screenBash0", () => tileSprite("screenBash0")], ["screenBash1", () => tileSprite("screenBash1")]],
    edit: [["screenDiff0", () => tileSprite("screenDiff0")], ["screenDiff1", () => tileSprite("screenDiff1")]],
    read: [["screenPage0", () => tileSprite("screenPage0")], ["screenPage1", () => tileSprite("screenPage1")]],
    web: [["screenWeb0", () => tileSprite("screenWeb0")], ["screenWeb1", () => tileSprite("screenWeb1")]],
  };
  /** 画面の模様の種類: 考え中 → think／読むコマ → web か read（紙）／打鍵 → bash・edit・それ以外は type */
  const screenKind = (kind, cls) => (kind === "think" ? "think"
    : kind === "read" ? (cls === "web" ? "web" : "read")
      : cls === "bash" || cls === "edit" ? cls : "type");
  const screenName = (kind, cls, k) => SCREENS[screenKind(kind, cls)][k === 1 ? 0 : 1][0];
  const screenSprite = (kind, cls, k) => SCREENS[screenKind(kind, cls)][k === 1 ? 0 : 1][1]();

  let hover = null;              // {key} プロジェクト全員 / {session} 1 体
  let selected = null;           // 中央の端末が映しているセッション（角かっこ）
  // 挨拶（タップ・クリックの手応え）: 人の key → 始めた t（null＝次の live の描画の t で刻む）。入力起点＝固定時刻・停止中は受けない
  let greets = new Map();
  let greeting = 0;              // 直近のフレームで挨拶を描いた人数
  let away = 0;                  // 直近のフレームで散歩先へ向かった・立っている人数（ui/core/life.js）
  let screens = 0;               // 直近のフレームで画面を重ねた机の数（机の画面が語る）
  let assistants = 0;            // 本人の脇に添えた助手。人数・当たり・名札には含めない。
  let deskItems = 0;
  let errorNotes = 0;
  let lifeBook = new Map();      // 散歩の割り当て（key → 散歩先の添字）＝前のフレームから持ち越す（live だけが進める）
  let errandBook = null;         // 用事の記録（ui/core/errand.js の book・key → 道具の種類・出発・行き先）。live だけが進める
  let errandsAway = 0;           // 直近のフレームで用事の行き先へ向かった・立っている人数
  // 証拠の紙（R110-S5）: カウンターに着いた人が置いた紙 [{id: "key@出発の t", t: 置いた t, slot: EVIDENCE_TRAY の添字}]。
  // live だけが進める・EVIDENCE_PAPER_SEC で消える・最大 EVIDENCE_TRAY.length 枚（古い順に消える）。止め絵・再生には出さない
  let papers = [];
  let papersDrawn = 0;
  let propsDrawn = 0;            // 直近のフレームで貼った職業の小道具の数
  let lifeT = null;              // 直近に散歩の段を live で回した t（止め・再生・非表示から戻った最初のフレームを見分ける）
  let drawn = [];                // 直近のフレームで貼った人（描いた順・コマ込み）
  let people = [];               // 直近のフレームの全員（貼れなかった人も・印と歩行の控え）
  let last = new Map();          // session/id → 貼った人（point()・pick() の照準・退場中は除く）
  let lastT = 0;
  let frames = 0;
  let hovered = 0;               // 直近のフレームで実際にホバーの線を描いた人数
  let selectedDrawn = 0;         // 直近のフレームで実際に角かっこを描いた人数
  let placed = null;             // 直近のフレームの区分ごとの人数＋overflow（フッターと同じ事実）
  let walkStats = { walking: 0, entering: 0, leaving: 0, unreachable: 0 };
  let fps = roomFps(null);
  let unreachable = 0;           // 累計（届かずに瞬間移動した回数・t ごとに 1 回だけ数える＝同じ t の描き直しで増えも消えもしない）
  let missingCells = 0;          // 累計（描けなかったコマ）
  let marks = emptyMarks();      // 直近のフレームの印の数
  let reads = 0;                 // 直近のフレームで読書のコマを出した人数
  const book = assignBook(keepPrev);
  // 歩行の帳簿（pxwalk）と印の記録（pxtop）。live だけが進める（再生は触らない）。
  let walkBook = null;
  let markMemo;
  // 人（歩行の帳簿のキー）ごとの直近の個体色。live だけが進める。退場中の人はこの色で描く: colorOf（index.js の tintIndex）は
  // 台帳に並ぶプロジェクトの集合から衝突を解くので、最後のセッションが帰るとそのキーが集合から落ち、ハッシュの剰余へ戻る
  //＝衝突でずらされていた人が、扉まで歩くあいだ別のプロジェクトの色に化ける（「誰か」が嘘になる）。
  let tintBook = new Map();
  // この t の計算の出発点＝前の t の終わりの帳簿・記録・個体色と、前の t までの unreachable の累計。
  // 同じ t の描き直しはここから計算し直す（冪等）。
  let base = null;

  const isHovered = (a) => Boolean(hover
    && ((hover.session && a.session === hover.session)
      || (hover.key && !hover.session && a.projectKey === hover.key)));

  /** 1 人を塗る（影 → 焼き置きのロボ）。 */
  function paintPerson(p) {
    const { d } = p;
    // 影は右下へ。立っている物は必ず影を落とす（無いと床に貼り付いて見える）。
    ctx.fillStyle = ROOM.shadow;
    ctx.globalAlpha = 0.55;
    fill((d.x + 3) * S, (d.y + PX_CELL_H) * S, 8 * S, S);
    ctx.globalAlpha = 1;
    // 挨拶の跳ねと歩き出す前の立ち上がり（pose.lift）は描く y だけ＝当たり・名札・足元・drawn の x,y は動かさない（影も床に残す）
    const lift = Number.isInteger(p.pose.lift) ? p.pose.lift : 0;
    put(spriteOf(p.vendor, p.pose.cell, p.rows, p.tint, p.pose.mirror), d.x * S, (d.y - lift) * S, `person:${d.key}`);
    // 職業の小道具（R110-S2・頭か胸に重ねる別タイル）: ロボのすぐ後・同じ lift・W は鏡。止め絵にも出す（「誰か」は事実）。
    // 退場中の人は propAt が null（助手と同じく貼らない）
    if (p.propAt) {
      const img = propSprite(p.propAt.tile, p.propAt.flip);
      if (!img) missTile();
      else {
        put(img, p.propAt.x * S, p.propAt.y * S, `prop:${d.key}`);
        propsDrawn += 1;
      }
    }
    if (!d.leaving && d.assistants > 0) {
      const img = tileSprite("assistant", ASSISTANT_PAINT);
      if (!img) missTile();
      else {
        // 12×16 の本人の両脇。会議室や歩行中も本人に付いていく静止した小さなセル。
        const offsets = [[PX_CELL_W, 2], [PX_CELL_W, 9], [-5, 9]];   // 5×7 の助手: 右に上下 2 体・左に 1 体（本人の胴に重ねない）
        for (let i = 0; i < d.assistants; i += 1) {
          const [dx, dy] = offsets[i];
          put(img, (d.x + dx) * S, (d.y + dy) * S, `assistant:${d.key}:${i}`);
          assistants += 1;
        }
      }
    }
    drawn.push(Object.freeze({ key: d.key, id: d.id, session: d.session, projectKey: d.projectKey, x: d.x, y: d.y,
      zone: d.zone, want: d.want, seat: d.seat, state: d.state, attention: d.attention, vendor: p.vendor,
      prop: d.prop ?? "generic", errand: typeof d.errand === "string" ? d.errand : null,
      cell: p.pose.cell, flip: p.pose.mirror, kind: p.pose.kind, mark: p.mark, tint: p.tint, lift,
      // 立ち上がりの間（歩き出す直前）も歩く人に数える（stats().walking と同じ＝今から動く人をクリックの的にしない）
      walking: Boolean(d.walk) || d.lift > 0, rising: d.lift > 0, entering: d.entering === true, leaving: d.leaving === true }));
    if (!d.leaving) last.set(d.session || d.id, d);
  }

  /**
   * 1 フレーム描く。戻り値は部屋に置いた人数（フッターの「席 n/9・受付 m」の材料）。
   * **同じ t で呼び直してよい**（新しいデータ・ホバー・選択の描き直し）＝前の t の帳簿から計算し直すので結果は冪等。
   * @param {object} opts { replay: 再生（ダイジェスト）の world か, lightStage: platform が決めた朝・昼・夕・夜 }
   */
  function draw(world, board, t = 0, { replay = false, lightStage: requestedStage = "day" } = {}) {
    // 非表示のタブでは描かない（帳簿・記録・個体色・累計のどれも進めない＝隠れている間は t が進まない扱い）。
    // 戻った最初の描画は、帳簿の古さの門（pxwalk WALK_STALE_SEC・pxtop MARK_STALE_SEC）が「初めて見た」と同じに扱う
    //＝隠れていた間の入場・報告を、戻った瞬間に扉からの歩行や ✓ で遅れて演じない。持ち主はここだけ（index.js は見ない）。
    // 門: tests/cockpit_room.test.mjs（偽の document の hidden を差し替える）。
    if (document.hidden) return placed;
    lastT = t;
    frames += 1;
    calls = 0;
    layers = [];
    const nextStage = Object.hasOwn(ROOM_LIGHT, requestedStage) ? requestedStage : "day";
    if (!bg || nextStage !== lightStage) { lightStage = nextStage; bakeBack(); }
    const still = frozen() || replay;
    const tm = still ? 0 : t;                          // 動きの時計（t が進まない間は固定）
    const { actors, occupied, overflow, counts, assign } = book.layout(world, board, replay);
    placed = Object.freeze({ ...counts, overflow });
    const occ = new Set(occupied);
    // 在席の机を先に取り、机を離れた人は空いている予約机だけに小物と紙を残す。
    const decor = new Map(actors.filter((a) => a.seat >= 0).map((a) => [a.seat, a]));
    for (const a of actors) {
      const home = assign[a.key]?.home;
      if (home >= 0 && !decor.has(home)) decor.set(home, a);
    }
    deskItems = [...decor.values()].filter((a) => a.deskWork).length;
    errorNotes = [...decor.values()].filter((a) => a.errorNote).length;
    bakeFront(occ, decor);
    assistants = 0;
    propsDrawn = 0;

    // 歩行と印。再生は帳簿に触らない（live=false）。live は前の t の終わりの帳簿から（同じ t なら同じ出発点から）。
    const fresh = !replay && (!base || base.t !== tm);
    if (fresh) base = { t: tm, walkers: walkBook, memo: markMemo, tints: tintBook, unreachable, errands: errandBook };
    // 暮らし（散歩・ui/core/life.js）: 返事を 1 分以上待っている机の人の目的地だけを散歩先へ差し替える（live のときだけ・
    // 席の割り当て・人数・在席は変えない＝机は点いたまま）。歩く・戻るは stepWalkers の「目的地が変われば引き直す」に任せる
    // 止め・再生・非表示から戻った最初のフレーム（前回の live から 4 秒より空いた・巻き戻った）は散歩させない＝全員を机に置き、
    // 次のフレームから机を出て歩く（戻った瞬間に散歩先へ瞬間移動しない＝歩行の帳簿の古さの門と同じ考え）
    const lifeLive = life === true && !replay && !still;
    const resumed = lifeLive && (lifeT === null || tm < lifeT || tm - lifeT > 4);
    // 用事（R110-S5・ui/core/errand.js）: 机で道具を使って働いている人が、道具に連動して本棚（読む）・掲示板（Web）・ボード
    //（段取り）・カウンター（証拠）へ行き、同じ机へ戻る。門は散歩と同じ（新しい旗は作らない＝?life=0 で用事も止まる）。
    // 記録は前の t の終わりから（同じ t の描き直しは冪等）。用事が先・散歩は残り（部屋全体で机を離れるのは AWAY_MAX 人まで）
    const errands = errandTargets(actors, tm, { live: lifeLive && !resumed, prev: replay ? null : base.errands, spots: ERRAND_SPOTS });
    if (lifeLive) errandBook = errands.book;
    errandsAway = errands.away;
    const lives = lifeTargets(errands.actors, tm, { live: lifeLive && !resumed, prev: lifeBook, max: AWAY_MAX - errands.away });
    if (lifeLive) { lifeBook = lives.assign; lifeT = tm; } else if (!replay) { lifeT = null; }
    away = lives.away;
    const w = stepWalkers(replay ? walkBook : base.walkers, lives.actors, tm, { live: !replay, frozen: still });
    if (!replay) {
      walkBook = w.walkers;
      // 累計＝前の t までの累計＋この t の数（最初の描画だけ足すと、同じ t に届いた新しいデータの「届かない」を数え損ねる。
      // 次の t にはその人がもう帳簿に居る＝二度と数えられない。本番のデータはほぼ全部この同じ t の描き直しで描かれる）
      unreachable = base.unreachable + w.stats.unreachable;
    }
    walkStats = w.stats;
    fps = roomFps(w.stats);
    const markLive = !replay && !still;                // t が実時間で進んでいるときだけ ✓ を出す
    // 証拠の紙（R110-S5）: カウンターに着いた（歩き終えて立った）人が、その用事 1 回につき 1 枚置く。live だけ
    if (markLive) papers = trayPapers(papers, w.drawn, errands.book, tm);
    let memo = replay ? markMemo : base.memo;
    marks = emptyMarks();
    reads = 0;
    // 個体色: 退場中の人は前の t の終わりに控えた色（無ければ colorOf）。再生は控えを読まず書かない（過去の部屋は今の割り当て）
    const tints = new Map();
    const tintOf = (d) => {
      if (replay) return colorOf(d.projectKey);
      const c = d.leaving && base.tints.has(d.key) ? base.tints.get(d.key) : colorOf(d.projectKey);
      tints.set(d.key, c);
      return c;
    };
    // 挨拶の帳簿: 受けた人を次の live の描画の t で刻み、GREET_SEC を過ぎたら落とす（同じ t の描き直しは同じ結果＝冪等）
    greeting = 0;
    if (markLive && greets.size) {
      const next = new Map();
      for (const [key, since] of greets) {
        const t0 = since === null ? tm : since;
        if (tm >= t0 && tm - t0 < GREET_SEC) next.set(key, t0);
      }
      greets = next;
    }
    people = w.drawn.map((d, i) => {
      const m = roomMark(d, tm, memo, { live: markLive });
      memo = m.memo;
      // 個体ごとの開始位相（全員が同じ瞬間に切り替わらない）。止め絵（固定時刻・停止・再生）は位相 0＝今の絵（golden 不変）
      const phase = markLive ? (tintHash(d.key) % PHASE_STEPS) / PHASE_STEPS : 0;
      // 経過秒は 1e-6 秒で丸める（41 + 4/6 − 41 が 0.66666…43 になり、コマの境目で 1 つ手前へ落ちる浮動小数の誤差）
      const g = markLive && greets.has(d.key) ? Math.round((tm - greets.get(d.key)) * 1e6) / 1e6 : null;
      // 道具の種類（R110-S5）: 工具を使っている最中（kind tool）だけ＝画面の模様と打鍵の速さの材料（止め絵は type0 のまま）
      const cls = d.kind === "tool" ? toolClassOf(d) : null;
      // 立ち上がり（d.lift）と到着の一拍（d.settled）は歩行の帳簿から＝live だけ（frozen・再生の stepWalkers は 0 と null）
      const pose = topPose(d, tm, { walk: d.walk, lift: d.lift, settled: d.settled, done: m.mark === "done", phase, greet: g,
        pace: typePace(d, cls) });
      if (pose.kind === "greet") greeting += 1;
      const vendor = topVendor(d.vendor);
      const rows = PX_CELLS[vendor]?.[pose.cell];
      const ok = Array.isArray(rows);
      if (!ok) missingCells += 1;                        // 別のコマで代用しない（描かずに数える）
      if (ok && m.mark) marks[m.mark] += 1;              // 数えるのは貼った人だけ（貼れなかった人の印は描かない）
      if (ok && pose.kind === "read") reads += 1;
      return { i, d, pose, vendor, phase, cls, since: m.since, rows: ok ? rows : null, mark: m.mark,
        at: topMarkAt(d, m.mark, { since: m.since, t: tm }), markTile: null,
        // 退場中の人には貼らない（助手と同じ＝台帳から落ちた人・入れ替わりの山の drawCalls を部屋の 26 人分に抑える）
        propAt: ok && !d.leaving ? topPropAt(d, d.prop, rows, { mirror: pose.mirror, lift: pose.lift, dir: pose.dir }) : null,
        tint: tintOf(d), footY: d.y + FOOT_CENTER[1] };
    });
    if (!replay) { markMemo = memo; tintBook = tints; }

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    put(bg, 0, 0, "bg");
    paintLight(ctx, S, fill, occupied);

    // 足跡（床の上＝人と手前の什器より先。机の足元を抜ける所は段が隠す）
    let prints = 0;
    const step = tileSprite("footstep");
    for (const p of people) {
      if (!p.d.walk || !p.rows) continue;
      const rec = w.walkers?.[p.d.key];
      for (const [x, y] of footprints(rec?.path, rec?.t0, tm)) {
        if (prints >= PRINTS_MAX) break;
        if (!step) { missTile(); break; }
        put(step, x * S, y * S, `print:${p.d.key}`);
        prints += 1;
      }
    }

    // 人と手前の什器の段を、足元の y で奥から交互に（段の depth より足元が奥の人を先に・同じなら段の後＝手前）
    last = new Map();
    drawn = [];
    const order = people.filter((p) => p.rows).sort((a, b) => a.footY - b.footY || a.i - b.i);
    let k = 0;
    papersDrawn = 0;
    // 証拠の紙はカウンターの天板の上＝カウンターの段の直後（カウンターの向こうに立つ人より手前・前を通る人より奥）
    const paintPapers = () => {
      if (!markLive) return;                           // 止め絵・再生には出さない（golden 不変）
      for (const pp of [...papers].sort((a, b) => a.slot - b.slot)) {
        const img = tileSprite("paper");
        if (!img) { missTile(); break; }
        const [x, y] = EVIDENCE_TRAY[pp.slot];
        put(img, x * S, y * S, `paper:${pp.id}`);
        papersDrawn += 1;
      }
    };
    BANDS.forEach((band, i) => {
      while (k < order.length && order[k].footY < band.depth) paintPerson(order[k++]);
      if (bandCanvases[i]) put(bandCanvases[i][0], band.box[0] * S, band.box[1] * S, `band:${band.key}`);
      if (band.key === COUNTER_BAND) paintPapers();
    });
    while (k < order.length) paintPerson(order[k++]);

    // 机の画面が語る（R99-P4-L3）: 席に座っている人の画面に、打鍵・読書なら流れる行、考え中なら増える点を重ねる。
    // live のときだけ・コマ 0 は貼らない（止め絵は今の光った画面＝golden 不変）。散歩中・歩いている人の画面は光ったまま（貼らない）。
    // タイル名は字面で呼ぶ（tools/px_lint.py の⑤が「描画のコードが直接描くタイル」として数える）
    // R110-S5: 模様は道具の種類で替わる（Bash＝墨の地に緑の行・Edit＝差分・Read＝めくれる紙・Web＝帯と行・既定は打鍵の行）
    screens = 0;
    if (markLive) {
      for (const p of order) {
        const { d } = p;
        if (d.leaving || d.walk || d.zone !== "desk" || !Number.isInteger(d.seat) || d.seat < 0) continue;
        const slot = SLOTS.desk[d.seat];
        const desk = DESKS[d.seat];
        if (!slot || !desk || d.x !== slot.x || d.y !== slot.y) continue;     // 席に座っている（散歩先・通路に居ない）
        const k = screenFrame(p.pose.kind, tm, p.phase);
        if (!k) continue;
        const img = screenSprite(p.pose.kind, p.cls, k);
        if (!img) { missTile(); continue; }
        put(img, (desk.x + 19) * S, (desk.y - 8) * S, `screen:${d.key}:${screenName(p.pose.kind, p.cls, k)}`);
        screens += 1;
      }
    }

    // 頭上の印（手前の什器より後＝机に隠れない）。退場中の人は roomMark が null を返す＝出ない。
    // 考え中の雲（R110-S5）は live だけ点が増える（0 → 2 → 3 個を繰り返す・止め絵は markThink のまま）
    // 点の付いた雲は 💭 の写し＝元の 💭 が描けなければ点の雲も貼らない（別の絵で代用しない・描かずに数える）
    for (const p of order) {
      if (!p.at) continue;
      const base = tileSprite(p.at.tile, MARK_COLORS[p.mark] || TILE_PALETTE);
      if (!base) { missTile(); continue; }
      const dots = markLive && p.mark === "think" ? thinkDots(tm, p.since, p.phase) : 0;
      const img = dots === 1 ? tileSprite("markThink1") : dots === 2 ? tileSprite("markThink2") : base;
      if (!img) { missTile(); continue; }
      p.markTile = dots === 1 ? "markThink1" : dots === 2 ? "markThink2" : p.at.tile;
      put(img, p.at.x * S, p.at.y * S, `mark:${p.d.key}:${p.mark}`);
    }
    // 選択（頭上の角かっこ）とホバー（足元の線）。退場中の人は台帳に居ない＝出さない
    selectedDrawn = 0;
    hovered = 0;
    for (const p of order) {
      const { d } = p;
      if (d.leaving || !selected || d.session !== selected) continue;
      ctx.fillStyle = p.tint;
      for (const [dx, dy, w2, h2] of SELECT_MARK) fill((d.x + dx) * S, (d.y + dy) * S, w2 * S, h2 * S);
      layers.push(`select:${d.key}`);
      selectedDrawn += 1;
    }
    for (const p of order) {
      const { d } = p;
      if (d.leaving || !isHovered(d)) continue;
      const [dx, dy, w2, h2] = HOVER_LINE;
      ctx.fillStyle = p.tint;
      fill((d.x + dx) * S, (d.y + dy) * S, w2 * S, h2 * S);
      layers.push(`hover:${d.key}`);
      hovered += 1;
    }
    return placed;
  }

  return {
    draw,
    /**
     * 倍率を替える（index.js が形の変化で roomScale を選び直したとき）。受けるのは ROOM_SCALE_FLOOR〜MAX の整数だけ。
     * 焼き置き（背景・手前の段・ロボと印と足跡）は新しい倍率で作り直し、帳簿（席の割り当て・歩行・印の記録・個体色）は
     * そのまま持ち越す＝倍率を替えても歩いている人が瞬間移動しない・入場をやり直さない。描き直しは呼び手が同じ t で頼む
     *（draw は同じ t なら冪等＝×3 へ戻せば元と同じ絵）。替えたら true、同じ倍率・受けない値は false（何もしない）。
     */
    setScale: (k) => {
      if (!validScale(k) || k === S) return false;
      S = k;
      bakeScale();
      return true;
    },
    setHover: (h) => {
      const v = typeof h === "string" ? { key: h, session: null } : h;
      hover = v && (v.session || v.key) ? v : null;
    },
    /** 中央の端末が映しているセッション（無ければ null）。頭上の角かっこで示す。 */
    setSelected: (session) => { selected = typeof session === "string" && session ? session : null; },
    /**
     * 挨拶（手を振る 0.9 秒）を頼む。引数は session か id（直近のフレームに居る人）。止まっている間（固定時刻・停止・
     * 視差効果を減らす）は受けない＝false。受けたら次の描画の t から GREET_SEC 秒（描き直しは呼び手が頼む）。
     */
    setGreet: (sessionOrId) => {
      if (frozen() || typeof sessionOrId !== "string" || !sessionOrId) return false;
      const d = last.get(sessionOrId) || [...last.values()].find((x) => x.id === sessionOrId);
      // 歩いている人・立ち上がっている人は手を振るコマより歩行が勝つ＝受けない（呼び手は別の手応えで返す）
      if (!d || !d.key || d.walk || d.lift > 0) return false;
      greets.set(d.key, null);
      return true;
    },
    /** 次の描画の刻み（fps）＝直近のフレームで歩いている人が居れば 12、居なければ 6（pxwalk.roomFps）。 */
    fps: () => fps,
    /** テストの照準・性能ゲート用。drawCalls は直近 1 フレームでメインの canvas に呼んだ fillRect＋drawImage の数。
     *  bgBakes / fgBakes / spriteBakes は焼き置きを作り直した累計（焼き置きが効いているかの証拠・印と足跡のタイルも spriteBakes）。
     *  hovered / selected は直近のフレームで実際にホバーの線・選択の角かっこを描いた人数、placed は区分ごとの人数＋overflow。
     *  walking / entering / leaving は直近のフレームの歩いている人数（入退場を含む）、fps は次の刻み。
     *  marks は直近のフレームの印の数、reads は読書のコマを出した人数。
     *  errands は用事の行き先へ向かった・立っている人数（away は散歩）・papers はカウンターに貼った証拠の紙の枚数・
     *  props は貼った職業の小道具の数（R110）。
     *  unreachable（届かずに瞬間移動）・missingCells（描けなかったコマ）・missingTiles（無いタイル）は累計＝どれも 0 が正。
     *  unreachable は t ごとに 1 回（同じ t の描き直しは前の t までの累計＋この t の数＝冪等）。missingCells・missingTiles は描くたびに足す。 */
    stats: () => ({ drawCalls: calls, materials: 0, scale: S, logicalW: W, logicalH: H,
      actors: drawn.length, frames, t: lastT, hovered, selected: selectedDrawn, greeting, away, screens, life: life === true,
      errands: errandsAway, papers: papersDrawn, props: propsDrawn,
      bgBakes, fgBakes, spriteBakes, placed, lightStage, assistants, deskItems, errorNotes,
      fps, walking: walkStats.walking, entering: walkStats.entering, leaving: walkStats.leaving,
      unreachable, missingCells, missingTiles, marks: { ...marks }, reads }),
    /** テストの照準: 直近のフレームで貼った人（論理 px の今の左上・区分・貼ったコマ・印・歩行の旗）。描いた順。 */
    actors: () => drawn.slice(),
    /**
     * debug: 直近のフレームの全員（貼れなかった人も）の コマ・向き・印・印の置き場所・歩行。並びは stepWalkers の drawn の順。
     * missing は描けなかった（そのベンダーのシートにコマが無い）人。
     */
    marks: () => people.map((p) => ({ key: p.d.key, session: p.d.session, id: p.d.id, zone: p.d.zone,
      vendor: p.vendor, cell: p.pose.cell, kind: p.pose.kind, dir: p.pose.dir, mirror: p.pose.mirror,
      lift: Number.isInteger(p.pose.lift) ? p.pose.lift : 0, settled: p.d.settled ?? null,
      mark: p.mark, markAt: p.at, markTile: p.at ? p.markTile ?? p.at.tile : null,
      prop: p.d.prop ?? "generic", propAt: p.propAt, toolClass: p.cls,
      errand: typeof p.d.errand === "string" ? p.d.errand : null,
      x: p.d.x, y: p.d.y, walk: p.d.walk, entering: p.d.entering === true,
      leaving: p.d.leaving === true, missing: !p.rows,
      selected: Boolean(selected) && !p.d.leaving && p.d.session === selected })),
    /** debug: 歩行の帳簿の要約（live だけが進める）。path は経路の点の数（null＝目的地に立っている）。 */
    walkers: () => Object.entries(walkBook || {}).map(([key, r]) => ({ key, target: r.target, path: r.path ? r.path.length : null,
      t0: r.t0, seen: r.seen, enter: r.enter, leaving: r.leaving })),
    /** debug: 直近のフレームで何をどの順で貼ったか（"bg"・"print:<key>"・"person:<key>"・"prop:<key>"・"assistant:<key>:<n>"・
     *  "band:<段>"・"paper:<id>"・"screen:<key>:<タイル>"・"mark:<key>:<印>"・"select:<key>"・"hover:<key>"）。
     *  重なり順（足元の y で人と段を交互）の門が読む。 */
    layers: () => layers.slice(),
    point: (id) => {
      const a = last.get(id) || [...last.values()].find((x) => x.id === id);
      if (!a) return null;
      const r = canvas.getBoundingClientRect();
      return { left: r.left + (a.x + PX_CELL_W / 2) * S, top: r.top + (a.y + 4) * S };
    },
    /**
     * canvas の座標 → 一番近い人（クリック・タップで端末とトレイをその人に合わせるため）。当たりは描いた矩形の縁から
     * PICK_RADIUS_PX（CSS px）まで＝倍率で割って論理 px で pickNearest へ渡す（×2 の矩形 24×32 は指の 44 に届かない＝
     * 矩形だけの当たりに戻さない）。同じ距離なら手前（足元の y が大きい人）→ key の順・退場中の人は当てない。
     */
    pick: (clientX, clientY) => {
      const r = canvas.getBoundingClientRect();
      const a = pickNearest(drawn, (clientX - r.left) / S, (clientY - r.top) / S, PICK_RADIUS_PX / S);
      return a ? last.get(a.session || a.id) || a : null;
    },
    dispose: () => canvas.remove(),
  };
}
