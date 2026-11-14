// R111-A1: Mac コックピット（3 カラム）の 4 つの窓（01 オフィス・04 受付・03 端末・02 名簿）の配置＝正本（純関数）。
// 本人の裁定（2026-10-07）: 窓は題名の帯をつかんで入れ替える（3 列のどこへでも・上下に重ねてもよい・窓は重ならない・部屋は常に全部見える）・
// 境目をドラッグすると隣の 2 列だけ広がる・配置はブラウザごとに記憶・⚙ で初期化。計画= R111「決めたこと 1〜4・9」。
//
// 純関数（DOM・通信・時刻・乱数に触らない）。入力は書き換えない。返す配置は凍結した写し（無変化なら入力をそのまま返す）。
// 不変条件: 常に 3 列・4 窓がちょうど 1 回ずつ・空の列なし（＝ちょうど 1 列が 2 窓）。崩れた値は既定へ戻す。
// 既定の配置は CSS の静的な升目が描く（index.js は既定でないときだけ gridTemplate の結果を .main の inline に書く）＝
// gridTemplate(DEFAULT_LAYOUT) は style.css の既定の升目と同じ値を返す（ピン＝cplayout.test.js）。
// 2 カラム・1 列（@media）には配置を適用しない（呼び手の仕事）。ここが扱うのは 3 カラムだけ。
import { ROOM, ROOM_SCALE_MAX as PX_SCALE_MAX, ROOM_SCALE_MIN as PX_SCALE_MIN } from "./pxroom.js";

/** 窓の id（.cpsec の data-win・保存する配置の語彙）。順は既定の配置を読む順 */
export const WINDOWS = Object.freeze(["office", "reception", "terminal", "roster"]);

const deepFreeze = (l) => {
  l.cols.forEach((c) => Object.freeze(c));
  Object.freeze(l.cols);
  Object.freeze(l.widths);
  Object.freeze(l.rows);
  return Object.freeze(l);
};

/**
 * 既定の配置（R110 骨格 A の 3 カラム）: 左＝部屋の上・受付の下／中＝端末／右＝名簿。widths・rows が空＝全部既定。
 * 型: { v: 1, cols: [[id…],[id…],[id…]], widths: { roster?: px, reception?: px, termMin?: px, roomCap?: 2|3 }, rows: { split?: 0.2..0.8 } }
 */
export const DEFAULT_LAYOUT = deepFreeze({ v: 1, cols: [["office", "reception"], ["terminal"], ["roster"]], widths: {}, rows: {} });

// ── 定数（値の出どころ）────────────────────────────────────────────────────
/** 端末の列の既定の最低の幅（80 桁）。R110 骨格 A の ui/cockpit/index.js の CENTER_MIN_W＝R111-A2 からここが正本（index.js が読む） */
export const TERM_MIN_W_DEFAULT = 560;
/** 部屋の列の幅のうち絵の外＝窓の罫 1×2＋部屋の左右の余白 8×2（style.css の .cpwin の罫と .cpstage .viewport の余白）。
 *  R111-A2 からここが正本（index.js の LEFT_CHROME はこれを読む） */
export const ROOM_CHROME_W = 18;
/** 端末の列を境目のドラッグで狭めてよい桁の下限（本人の裁定「40 桁まで許す」・文字は小さくしない＝桁が減るだけ） */
export const TERM_MIN_COLS = 40;
/** 40 桁の実測（term.js の widthForCols）が取れないときの端末の幅の下限（14px の字で 40 桁＋余白の退避値） */
export const TERM_MIN_W_FLOOR = 352;
/** 名簿・受付だけの列（fixed）に保存できる幅の範囲（px） */
export const ROSTER_W_MIN = 240;
export const ROSTER_W_MAX = 520;
/** 窓の間の溝（style.css の .main の column-gap と、2 窓の列の 8px の行） */
export const GUTTER = 8;
/** 部屋の論理寸法と倍率の範囲。出どころ= ui/core/pxroom.js の ROOM・ROOM_SCALE_MIN/MAX（ここは写さず読む） */
export const ROOM_W = ROOM.w;
export const ROOM_H = ROOM.h;
export const ROOM_SCALE_MIN = PX_SCALE_MIN;
export const ROOM_SCALE_MAX = PX_SCALE_MAX;
/**
 * 部屋の列で部屋の相方（同じ列のもう 1 窓）に残す最低の丈（見出しの下の中身）。R110 の index.js の RECEPTION_MIN_H（200）・
 * RECEPTION_MIN_H_TIGHT（120＝受付を詰めれば ×4 が入る形だけ）・LEDGER_MIN_H（120＝名簿 2 行）＝R111-A2 から 3 カラムはここが正本
 *（index.js の 3 カラムの倍率は roomAvail を通す）。端末は 200（会話 1 画面の下限）
 */
export const PARTNER_MIN_H = Object.freeze({ reception: 200, receptionTight: 120, roster: 120, terminal: 200 });
/**
 * 升目の行の最低（grid-template-rows の minmax の下限）。受付は CSS の床 120（style.css の .cpleft の minmax(120px, 1fr)・
 * 倍率の計算が 200 を残すのは roomAvail の reserveH の仕事）・名簿 120・端末 200
 */
export const ROW_MIN_H = Object.freeze({ reception: PARTNER_MIN_H.receptionTight, roster: PARTNER_MIN_H.roster, terminal: PARTNER_MIN_H.terminal });
/** 名簿・受付だけの列の既定の幅。出どころ= style.css の --cp-right-w（同じ文字列） */
export const FIXED_W_RANGE = Object.freeze({ min: 280, vw: 18, max: 320 });
export const FIXED_W_DEFAULT = `clamp(${FIXED_W_RANGE.min}px, ${FIXED_W_RANGE.vw}vw, ${FIXED_W_RANGE.max}px)`;
/** 窓 1 枚の上下の罫（1px×2）。index.js の roomAvailH の「+ 4」は窓 2 枚分 */
export const WIN_BORDER_H = 2;
/** 部屋を含まない 2 窓の列の上の窓の割合（rows.split）の範囲と既定 */
export const SPLIT_MIN = 0.2;
export const SPLIT_MAX = 0.8;
export const SPLIT_DEFAULT = 0.5;

/** 指示欄（sheet）の行＝丈 0px の錨（style.css の既定の升目の最後の行と同じ・R111-S0） */
const SHEET_ROW = "0px";
/** 升目の名前（既定の CSS の grid-template-areas と同じ語）。窓が別の列へ移っても名前は窓に付いて回る */
const AREA_NAME = Object.freeze({
  office: "office", reception: "reception", terminal: "center", roster: "right", sheet: "sheet", rowsplit: "ogap",
});
/** widths のキーの順（直列化の順＝決定論） */
const WIDTH_KEYS = ["roster", "reception", "termMin", "roomCap"];
/** fixed の列の幅を持つ窓（名簿が先＝2 窓の fixed 列はこの順で先に値を持つ方の幅） */
const FIXED_IDS = ["roster", "reception"];

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const fin = (v) => typeof v === "number" && Number.isFinite(v);
const num = (v) => (fin(v) ? v : 0);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const round4 = (v) => Math.round(v * 10000) / 10000;
const envOf = (env) => (isObj(env) ? env : {});
const gapOf = (e) => (fin(e.gap) && e.gap >= 0 ? e.gap : GUTTER);

/**
 * widths を正規化する。省略（undefined）は {}。オブジェクトでない・既知のキーの値が有限の数でない（null・文字列を含む）は
 * 壊れた値＝null（呼び手が既定へ戻す）。有限の数で範囲外のものは丸めて残す（保存した後に範囲を変えても配置を捨てない）:
 * roster・reception＝240〜520 の整数／termMin＝352〜560 の整数（560＝既定なので落とす）／roomCap＝2〜4 の整数（4＝上限なし＝既定なので落とす）。
 * 未知のキーは落とす（配置に効かない）。
 */
function normWidths(w) {
  if (w === undefined) return {};
  if (!isObj(w)) return null;
  const out = {};
  for (const key of WIDTH_KEYS) {
    const v = w[key];
    if (v === undefined) continue;
    if (!fin(v)) return null;
    if (key === "roster" || key === "reception") out[key] = Math.round(clamp(v, ROSTER_W_MIN, ROSTER_W_MAX));
    else if (key === "termMin") {
      const t = Math.round(clamp(v, TERM_MIN_W_FLOOR, TERM_MIN_W_DEFAULT));
      if (t !== TERM_MIN_W_DEFAULT) out.termMin = t;
    } else {
      const k = Math.round(clamp(v, ROOM_SCALE_MIN, ROOM_SCALE_MAX));
      if (k !== ROOM_SCALE_MAX) out.roomCap = k;
    }
  }
  return out;
}

/** rows を正規化する（判断は normWidths と同じ）。split＝0.2〜0.8・小数 4 桁・0.5（既定）は落とす */
function normRows(r) {
  if (r === undefined) return {};
  if (!isObj(r)) return null;
  const out = {};
  if (r.split !== undefined) {
    if (!fin(r.split)) return null;
    const s = round4(clamp(r.split, SPLIT_MIN, SPLIT_MAX));
    if (s !== SPLIT_DEFAULT) out.split = s;
  }
  return out;
}

const sameCols = (a, b) => Array.isArray(b) && b.length === a.length
  && a.every((c, i) => Array.isArray(b[i]) && b[i].length === c.length && c.every((id, j) => b[i][j] === id));
const sameFlat = (a, b) => {
  if (!isObj(b)) return false;
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => b[k] === a[k]);
};
/** 正規化した n と l が同じ中身か（l がもともと正しい配置なら、無変化の操作は l をそのまま返す） */
const sameLayout = (n, l) => isObj(l) && l.v === n.v && sameCols(n.cols, l.cols) && sameFlat(n.widths, l.widths)
  && sameFlat(n.rows, l.rows);

/**
 * 保存された値（JSON.parse の結果など）を配置へ正す。壊れた値（オブジェクトでない・v が 1 でない・列が 3 本でない・
 * 未知の id・重複・欠け・空の列・3 窓の列・widths／rows がオブジェクトでない・既知のキーに数でない値）は DEFAULT_LAYOUT。
 * 範囲外の数だけなら丸めて残す（normWidths・normRows）。既定と同じ中身なら DEFAULT_LAYOUT そのもの（同じ参照）を返す。
 * 返す配置は凍結した写し。入力は書き換えない。
 * @param {unknown} raw
 * @returns {{v: 1, cols: string[][], widths: object, rows: object}}
 */
export function normalizeLayout(raw) {
  if (!isObj(raw) || raw.v !== 1 || !Array.isArray(raw.cols) || raw.cols.length !== 3) return DEFAULT_LAYOUT;
  const seen = new Set();
  const cols = [];
  for (const col of raw.cols) {
    if (!Array.isArray(col) || col.length < 1 || col.length > 2) return DEFAULT_LAYOUT;
    for (const id of col) {
      if (typeof id !== "string" || !WINDOWS.includes(id) || seen.has(id)) return DEFAULT_LAYOUT;
      seen.add(id);
    }
    cols.push(col.slice());
  }
  if (seen.size !== WINDOWS.length) return DEFAULT_LAYOUT;
  const widths = normWidths(raw.widths);
  const rows = normRows(raw.rows);
  if (!widths || !rows) return DEFAULT_LAYOUT;
  const out = { v: 1, cols, widths, rows };
  if (sameLayout(DEFAULT_LAYOUT, out)) return DEFAULT_LAYOUT;
  return deepFreeze(out);
}

/** 操作の入口: 正しい配置ならそのまま・違えば正規化した写し */
function asLayout(l) {
  const n = normalizeLayout(l);
  return sameLayout(n, l) ? l : n;
}

/** 操作の出口: 新しい中身を正規化し、n と同じ中身なら n をそのまま返す（無変化＝同じ参照） */
function commit(n, cols, widths, rows) {
  const next = normalizeLayout({ v: 1, cols, widths, rows });
  return sameLayout(next, n) ? n : next;
}

/** 既定の配置か（壊れた値も既定として扱う＝normalizeLayout と同じ判断） */
export function isDefaultLayout(l) {
  return normalizeLayout(l) === DEFAULT_LAYOUT;
}

/**
 * 保存する文字列。既定なら null（呼び手は鍵を消す）。既定値のキーは落とし、空の widths・rows は書かない。
 * キーの順は v・cols・widths（roster・reception・termMin・roomCap）・rows（決定論）。
 * @returns {string|null}
 */
export function serializeLayout(l) {
  const n = normalizeLayout(l);
  if (n === DEFAULT_LAYOUT) return null;
  const out = { v: 1, cols: n.cols };
  if (Object.keys(n.widths).length) out.widths = Object.fromEntries(WIDTH_KEYS.filter((k) => k in n.widths).map((k) => [k, n.widths[k]]));
  if (Object.keys(n.rows).length) out.rows = { ...n.rows };
  return JSON.stringify(out);
}

/** 保存した文字列を配置へ（JSON でない・文字列でない（getItem の null）・壊れた値は DEFAULT_LAYOUT） */
export function parseLayout(str) {
  if (typeof str !== "string") return DEFAULT_LAYOUT;
  try {
    return normalizeLayout(JSON.parse(str));
  } catch {
    return DEFAULT_LAYOUT;
  }
}

// ── 読み取り ────────────────────────────────────────────────────────────────
const findIn = (n, id) => {
  for (let c = 0; c < n.cols.length; c += 1) {
    const r = n.cols[c].indexOf(id);
    if (r >= 0) return { col: c, row: r };
  }
  return null;
};

/** 窓の居場所 { col: 0〜2, row: 0|1 }（未知の id は null） */
export function findWindow(l, id) {
  return findIn(asLayout(l), id);
}

/** 2 窓の列の番号（不変条件で必ず 1 本） */
export function stackedColumn(l) {
  return asLayout(l).cols.findIndex((c) => c.length === 2);
}

/** 部屋（office）の列の番号。端末と同じ列なら flexColumn と同じ番号（その列の種類は "flex"） */
export function roomColumn(l) {
  return findIn(asLayout(l), "office").col;
}

/** 端末（terminal）の列の番号＝伸び縮みする列（minmax(termMin, 1fr)） */
export function flexColumn(l) {
  return findIn(asLayout(l), "terminal").col;
}

const kindIn = (n, c) => {
  const col = n.cols[c];
  if (!col) return null;
  if (col.includes("terminal")) return "flex";
  if (col.includes("office")) return "room";
  return "fixed";
};

/**
 * 列の種類（中身で決まる）。office を含む→ "room"（幅 184k＋18・k は呼び手が roomScale で決める）／terminal を含み office を
 * 含まない→ "flex"（minmax(termMin, 1fr)）／それ以外→ "fixed"（保存した px か FIXED_W_DEFAULT）。office と terminal が
 * 同じ列なら "flex"（部屋の倍率は列の実幅から）。c が 0〜2 でなければ null。
 * @returns {"room"|"flex"|"fixed"|null}
 */
export function columnKind(l, c) {
  return kindIn(asLayout(l), c);
}

/** fixed の列の幅を持つキー（名簿→受付の順に、値を持つ方を先に・どちらも無ければ列に居る先の方） */
const fixedKeyOf = (n, col) => {
  const ids = FIXED_IDS.filter((id) => col.includes(id));
  return ids.find((id) => n.widths[id] !== undefined) ?? ids[0];
};

/** 端末の列の最低の幅（env.termMin が数ならそれ・無ければ配置の widths.termMin・それも無ければ 560） */
const termMinOf = (n, e) => (fin(e.termMin) ? e.termMin : n.widths.termMin ?? TERM_MIN_W_DEFAULT);

/** flex の列の最低の幅。部屋と同じ列なら ×2 の部屋（184×2＋18＝386）も必ず入る幅 */
const flexMinOf = (n, e) => {
  const t = termMinOf(n, e);
  return findIn(n, "office").col === findIn(n, "terminal").col ? Math.max(t, ROOM_W * ROOM_SCALE_MIN + ROOM_CHROME_W) : t;
};

/**
 * 40 桁の実測（env.termFloor・px）。無ければ TERM_MIN_W_FLOOR。352〜560 へ丸める（352 未満は保存の termMin の下限と食い違う＝
 * 部屋が育たない・560 超は端末を既定より広く強制する）
 */
const termFloorOf = (e) => (fin(e.termFloor) ? clamp(e.termFloor, TERM_MIN_W_FLOOR, TERM_MIN_W_DEFAULT) : TERM_MIN_W_FLOOR);

/** 部屋の列の幅（184k＋18） */
const roomColW = (k) => ROOM_W * k + ROOM_CHROME_W;

// ── 窓の入れ替え ────────────────────────────────────────────────────────────
/** 窓 a と b の位置を入れ替える（同じ列の上下も可）。a === b・未知の id は無変化（同じ参照）。widths・rows は持ち越す */
export function swapWindows(l, a, b) {
  const n = asLayout(l);
  const pa = findIn(n, a);
  const pb = findIn(n, b);
  if (!pa || !pb || a === b) return n;
  const cols = n.cols.map((c) => c.slice());
  cols[pa.col][pa.row] = b;
  cols[pb.col][pb.row] = a;
  return commit(n, cols, n.widths, n.rows);
}

/**
 * 窓 id を列 col の段 row へ移す（row は 0〜その列の窓の数へ丸める＝0 は上・大きければ下）。元の列が空になる操作・
 * 範囲外の列・整数でない段・未知の id は無変化（同じ参照）。同じ列の中なら上下の並べ替え。
 */
export function moveWindow(l, id, col, row) {
  const n = asLayout(l);
  const p = findIn(n, id);
  if (!p || !Number.isInteger(col) || col < 0 || col > 2 || !Number.isInteger(row)) return n;
  const cols = n.cols.map((c) => c.slice());
  cols[p.col].splice(p.row, 1);
  if (!cols[p.col].length) return n;
  cols[col].splice(clamp(row, 0, cols[col].length), 0, id);
  if (cols[col].length > 2) return n;
  return commit(n, cols, n.widths, n.rows);
}

/**
 * キー（Alt+矢印）・メニューの「左へ／右へ／上へ／下へ」。
 * 上下＝同じ列の 2 窓を入れ替える（1 窓の列・上端の上・下端の下は無変化）。
 * 左右＝自分の列が 2 窓なら隣の列へ移す（隣の 1 窓の下に挿入）・1 窓なら隣の列の同じ段（無ければ先頭）と入れ替える。
 * 端（左端の左・右端の右）と未知の方向は無変化（同じ参照）。
 * @param {"left"|"right"|"up"|"down"} dir
 */
export function nudgeWindow(l, id, dir) {
  const n = asLayout(l);
  const p = findIn(n, id);
  if (!p) return n;
  const col = n.cols[p.col];
  if (dir === "up" || dir === "down") {
    const other = p.row + (dir === "up" ? -1 : 1);
    if (col.length !== 2 || other < 0 || other > 1) return n;
    return swapWindows(n, id, col[other]);
  }
  if (dir === "left" || dir === "right") {
    const to = p.col + (dir === "left" ? -1 : 1);
    if (to < 0 || to > 2) return n;
    if (col.length === 2) return moveWindow(n, id, to, 1);
    return swapWindows(n, id, n.cols[to][p.row] ?? n.cols[to][0]);
  }
  return n;
}

// ── 升目 ────────────────────────────────────────────────────────────────────
/**
 * .main の升目（3 カラムだけ）。
 * @param {object} l 配置
 * @param {{k?: number, mainW?: number, gap?: number, termMin?: number}} [env]
 *   k＝今の部屋の倍率（roomScale の結果・部屋の列の幅 184k＋18・数でなければ ×3）。
 *   mainW（.main の内幅）を渡すと、保存した fixed の幅が今の画面に入らないとき（部屋 ×2＋端末の最低＋fixed＋溝 2 本 > mainW）、
 *   保存した px の列を広い方から ROSTER_W_MIN まで詰めて返す（窓を画面の外へ押し出さない・保存値は変えない）。
 *   termMin は省略時 layout.widths.termMin ?? 560。
 * @returns {{cols: string, rows: string, areas: string, area: {office: string, reception: string, terminal: string,
 *   roster: string, sheet: string, rowsplit: string}, colsplits: number[]}}
 *   cols／rows／areas＝grid-template-columns／-rows／-areas の値。area＝各窓の grid-area の値（"r1 / c1 / r2 / c2"）。
 *   行は 4 本固定・2 窓の列が段の割りを決める（上が office→ `auto 8px minmax(下の最低, 1fr) 0px`／下が office→
 *   `minmax(上の最低, 1fr) 8px auto 0px`／office を含まない→ `minmax(上, Sfr) 8px minmax(下, (1−S)fr) 0px`・S＝rows.split ?? 0.5）。
 *   指示欄（sheet）の行は丈 0px の錨（R111-S0: 指示欄は行の丈を取らず、下端を錨に揃えて中身の丈だけ上へはみ出す・
 *   端末の窓は index.js が測った丈だけ margin-bottom で持ち上がる）。
 *   端末が 2 窓の列の上なら指示欄をその直下に置く（`minmax(…) 0px 8px minmax(…)`＝端末 1・sheet 2・溝 3・下の窓 4）。
 *   端末が 1 窓の列なら端末は行 1〜3・sheet は行 4。1 窓の列は行 1〜4・2 窓の列の下の窓は最後の行まで（穴を作らない）。
 *   rowsplit＝2 窓の列の溝（8px の行）の area。colsplits＝列の境目のハンドルが居る列の番号（第 2・第 3 列の左端＝常に [1, 2]）。
 */
export function gridTemplate(l, env = {}) {
  const n = asLayout(l);
  const e = envOf(env);
  const k = fin(e.k) && e.k >= 1 ? Math.round(e.k) : ROOM.scale;
  const flexMin = flexMinOf(n, e);
  // 列の幅（px の数・flex は最低の幅・fixed の既定は 18vw を mainW で見積もる＝溢れの検査だけに使う）
  const tracks = n.cols.map((col, c) => {
    const kind = kindIn(n, c);
    if (kind === "room") return { kind, px: roomColW(k) };
    if (kind === "flex") return { kind, px: flexMin };
    const key = fixedKeyOf(n, col);
    const px = n.widths[key];
    return px === undefined ? { kind, px: null } : { kind, px, stored: true };
  });
  if (fin(e.mainW) && e.mainW > 0) {
    const est = clamp(e.mainW * FIXED_W_RANGE.vw / 100, FIXED_W_RANGE.min, FIXED_W_RANGE.max);
    let over = tracks.reduce((s, t) => s + (t.px ?? est), 0) + gapOf(e) * 2 - e.mainW;
    const order = tracks.map((t, c) => c).filter((c) => tracks[c].stored).sort((a, b) => tracks[b].px - tracks[a].px || a - b);
    for (const c of order) {
      if (over <= 0) break;
      const cut = Math.min(Math.ceil(over), tracks[c].px - ROSTER_W_MIN);
      if (cut <= 0) continue;
      tracks[c] = { ...tracks[c], px: tracks[c].px - cut };
      over -= cut;
    }
  }
  const cols = tracks.map((t) => (t.kind === "flex" ? `minmax(${t.px}px, 1fr)` : t.px === null ? FIXED_W_DEFAULT : `${t.px}px`)).join(" ");

  // 段: 2 窓の列の上 A・下 B から行の寸法と、各窓の行の範囲 [r1, r2)
  const s = n.cols.findIndex((c) => c.length === 2);
  const [A, B] = n.cols[s];
  const S = n.rows.split ?? SPLIT_DEFAULT;
  const fr = (x) => `${round4(x)}fr`;
  const minH = (id) => `${ROW_MIN_H[id]}px`;
  const span = {};                         // id → [r1, r2]（2 窓の列の分）
  let rows;
  if (A === "terminal") {
    rows = B === "office"
      ? `minmax(${minH("terminal")}, 1fr) ${SHEET_ROW} ${GUTTER}px auto`
      : `minmax(${minH(A)}, ${fr(S)}) ${SHEET_ROW} ${GUTTER}px minmax(${minH(B)}, ${fr(1 - S)})`;
    Object.assign(span, { terminal: [1, 2], sheet: [2, 3], rowsplit: [3, 4], [B]: [4, 5] });
  } else {
    if (A === "office") rows = `auto ${GUTTER}px minmax(${minH(B)}, 1fr) ${SHEET_ROW}`;
    else if (B === "office") rows = `minmax(${minH(A)}, 1fr) ${GUTTER}px auto ${SHEET_ROW}`;
    else rows = `minmax(${minH(A)}, ${fr(S)}) ${GUTTER}px minmax(${minH(B)}, ${fr(1 - S)}) ${SHEET_ROW}`;
    Object.assign(span, { [A]: [1, 2], rowsplit: [2, 3] });
    if (B === "terminal") Object.assign(span, { terminal: [3, 4], sheet: [4, 5] });
    else span[B] = [3, 5];
  }
  // 1 窓の列: 端末は行 1〜3（sheet は行 4）・それ以外は行 1〜4
  const rect = {};                         // name → [r1, c1, r2, c2]
  n.cols.forEach((col, c) => {
    if (c === s) {
      for (const [name, [r1, r2]] of Object.entries(span)) rect[name] = [r1, c + 1, r2, c + 2];
    } else if (col[0] === "terminal") {
      rect.terminal = [1, c + 1, 4, c + 2];
      rect.sheet = [4, c + 1, 5, c + 2];
    } else {
      rect[col[0]] = [1, c + 1, 5, c + 2];
    }
  });
  const grid = [0, 1, 2, 3].map(() => ["", "", ""]);
  for (const [name, [r1, c1, r2, c2]] of Object.entries(rect)) {
    for (let r = r1; r < r2; r += 1) for (let c = c1; c < c2; c += 1) grid[r - 1][c - 1] = AREA_NAME[name];
  }
  const areas = grid.map((row) => `"${row.join(" ")}"`).join(" ");
  const area = {};
  for (const name of ["office", "reception", "terminal", "roster", "sheet", "rowsplit"]) area[name] = rect[name].join(" / ");
  return { cols, rows, areas, area, colsplits: [1, 2] };
}

// ── 部屋に使ってよい寸法 ────────────────────────────────────────────────────
/** 部屋の列で部屋以外に使う丈と、相方に残す最低（colH＝列の丈＝.main の丈） */
function heightBudget(n, e, colH) {
  const col = n.cols[findIn(n, "office").col];
  const partner = col.length === 2 ? col.find((id) => id !== "office") : null;
  const ch = isObj(e.chromeH) ? e.chromeH : {};
  let usedH = num(ch.office) + WIN_BORDER_H;
  if (partner) usedH += gapOf(e) + num(ch[partner]) + WIN_BORDER_H;
  let reserveH = 0;
  if (partner === "reception") {
    let tight = e.tight;
    if (typeof tight !== "boolean") {
      const slack = num(colH) - usedH - ROOM_H * ROOM_SCALE_MAX;      // 部屋を ×4 にしたとき受付に残る丈
      tight = slack >= PARTNER_MIN_H.receptionTight && slack < PARTNER_MIN_H.reception;
    }
    reserveH = tight ? PARTNER_MIN_H.receptionTight : PARTNER_MIN_H.reception;
  } else if (partner) {
    reserveH = PARTNER_MIN_H[partner];
  }
  return { usedH, reserveH, partner };
}

/**
 * 部屋に使ってよい幅と丈（3 カラムだけ）。呼び手（index.js）は availW・availH を roomScale に渡す。
 * @param {object} l 配置
 * @param {{mainW: number, mainH: number, gap?: number, k?: number, termMin?: number,
 *   fixedW?: {roster?: number, reception?: number}, chromeH?: {office?: number, reception?: number, roster?: number, terminal?: number},
 *   tight?: boolean}} env 呼び手が測った素の数。
 *   mainW／mainH＝.main の内寸（clientWidth／clientHeight）。gap＝溝（既定 8）。termMin＝端末の列の最低（省略時は配置の値か 560）。
 *   fixedW＝fixed の列の窓の実幅（offsetWidth・無ければ 0）。chromeH＝窓ごとの部屋以外の丈（office＝#sec-office＋#cp-setup-slot・
 *   reception＝#sec-reception・roster＝#sec-roster・terminal＝.cpsec＋.cptabbar）。tight＝受付を 120 に詰めれば ×4 が入る形か
 *  （省略時はここで index.js と同じ規則で決める: mainH − usedH − 640 が 120 以上 200 未満）。
 * @returns {{availW: number, availH: number, usedH: number, reserveH: number}}
 *   availW＝mainW − 溝 2 本 − fixed の列の実幅の和 −（端末が別の列なら termMin）− ROOM_CHROME_W。
 *   usedH＝部屋の窓の見出し（chromeH.office）＋罫 2 ＋（相方があれば 溝 ＋ 相方の見出し ＋ 罫 2）。
 *   reserveH＝相方の最低（PARTNER_MIN_H・受付は tight なら 120）・相方が無ければ 0。
 *   availH＝clamp(mainH − usedH − reserveH, ×2 の 320, ×4 の 640)（index.js の roomAvailH の 3 カラムの枝と同じ式）。
 *   既定の配置では index.js の roomAvailW／roomAvailH（R110）と同じ数（ピン＝cplayout.test.js）。
 */
export function roomAvail(l, env = {}) {
  const n = asLayout(l);
  const e = envOf(env);
  const fixedW = isObj(e.fixedW) ? e.fixedW : {};
  let fixedSum = 0;
  n.cols.forEach((col, c) => {
    if (kindIn(n, c) === "fixed") fixedSum += Math.max(...col.map((id) => num(fixedW[id])));
  });
  const separate = findIn(n, "office").col !== findIn(n, "terminal").col;
  const availW = num(e.mainW) - gapOf(e) * 2 - fixedSum - (separate ? termMinOf(n, e) : 0) - ROOM_CHROME_W;
  const { usedH, reserveH } = heightBudget(n, e, e.mainH);
  const availH = clamp(num(e.mainH) - usedH - reserveH, ROOM_H * ROOM_SCALE_MIN, ROOM_H * ROOM_SCALE_MAX);
  return { availW, availH, usedH, reserveH };
}

// ── 境目のドラッグ ──────────────────────────────────────────────────────────
/** 部屋の列の幅（提案）を倍率へスナップ: clamp(round((w − 18) / 184), 2, 4)。数でなければ ×2 */
export function snapRoomK(w) {
  if (!fin(w)) return ROOM_SCALE_MIN;
  return clamp(Math.round((w - ROOM_CHROME_W) / ROOM_W), ROOM_SCALE_MIN, ROOM_SCALE_MAX);
}

const withWidths = (n, patch) => {
  const widths = { ...n.widths };
  for (const [key, v] of Object.entries(patch)) {
    if (v === undefined) delete widths[key];
    else widths[key] = v;
  }
  return commit(n, n.cols, widths, n.rows);
};

/**
 * 列の境目のドラッグ（隣り合う 2 列だけが変わる）。制約を破る連続の値（fixed の幅）は近い有効値へ丸め、倍率（k）は
 * スナップした値が成立しなければ同じ l を返す（元のまま）。
 * @param {object} l 配置
 * @param {0|1} between 境目（0＝第 1・第 2 列の間・1＝第 2・第 3 列の間）
 * @param {number} proposedLeftW 左の列の提案幅（px）
 * @param {{pairW: number, gap?: number, termFloor?: number, termMin?: number}} env
 *   pairW＝隣り合う 2 列の今の幅の合計に間の溝 1 本を足した幅（左の列の左端から右の列の右端まで）＝右の列の提案幅は
 *   pairW − gap − proposedLeftW。termFloor＝端末 40 桁の実測（px・無ければ TERM_MIN_W_FLOOR・352〜560 へ丸める）。termMin＝今の端末の最低
 *  （省略時は配置の値か 560）。env.kFit は読まない（下の room×flex の termMin は残りの幅から直接決まる）。
 * 規則（左×右の種類・左右対称）:
 *   room×flex＝k' = snapRoomK(部屋の側の提案)・端末の残り（pairW − gap − (184k'+18)）が termFloor 以上なら
 *     widths.roomCap = k'（4＝上限なしは落とす）・widths.termMin = clamp(残り, termFloor, 560)（560＝既定は落とす）＝
 *     k' が今の最低で入らないときだけ端末の最低が下がり、部屋を戻せば既定の 80 桁へ戻る。足りなければ同じ l。
 *   fixed×flex＝widths[fixed の窓] = clamp(fixed の側の提案, 240, min(520, pairW − gap − 端末の最低))・上限が 240 未満なら同じ l。
 *   room×fixed＝k' をスナップ・fixed の残り（pairW − gap − (184k'+18)）が 240 以上なら roomCap = k'・fixed = min(残り, 520)
 *    （520 を超える分は離れた flex の列が吸う）。足りなければ同じ l。
 *   fixed×fixed（office と terminal が同じ列のときだけ起きる）＝左 = clamp(提案, 240, min(520, 2 列 − 240))・右 = min(520, 2 列 − 左)
 *    （2 列の和を超えない＝離れた flex の列から取らない・520 を超える分はその列が吸う）。2 列が 480 未満なら同じ l。
 */
export function dragColumnSplit(l, between, proposedLeftW, env = {}) {
  const n = asLayout(l);
  const e = envOf(env);
  if ((between !== 0 && between !== 1) || !fin(proposedLeftW) || !fin(e.pairW) || e.pairW <= 0) return n;
  const inner = e.pairW - gapOf(e);                     // 2 列の幅の和
  const cl = between;
  const cr = between + 1;
  const L = kindIn(n, cl);
  const R = kindIn(n, cr);
  const leftW = proposedLeftW;
  const rightW = inner - proposedLeftW;
  if ((L === "room" && R === "flex") || (L === "flex" && R === "room")) {
    const k = snapRoomK(L === "room" ? leftW : rightW);
    const rest = inner - roomColW(k);
    const floor = termFloorOf(e);
    if (rest < floor) return n;
    const termMin = Math.round(clamp(rest, floor, TERM_MIN_W_DEFAULT));
    return withWidths(n, { roomCap: k, termMin });
  }
  if ((L === "fixed" && R === "flex") || (L === "flex" && R === "fixed")) {
    const fc = L === "fixed" ? cl : cr;
    const hi = Math.min(ROSTER_W_MAX, inner - flexMinOf(n, e));
    if (hi < ROSTER_W_MIN) return n;
    const w = Math.round(clamp(L === "fixed" ? leftW : rightW, ROSTER_W_MIN, hi));
    return withWidths(n, { [fixedKeyOf(n, n.cols[fc])]: w });
  }
  if ((L === "room" && R === "fixed") || (L === "fixed" && R === "room")) {
    const fc = L === "fixed" ? cl : cr;
    const k = snapRoomK(L === "room" ? leftW : rightW);
    const rest = inner - roomColW(k);
    if (rest < ROSTER_W_MIN) return n;
    return withWidths(n, { roomCap: k, [fixedKeyOf(n, n.cols[fc])]: Math.round(Math.min(rest, ROSTER_W_MAX)) });
  }
  if (L === "fixed" && R === "fixed") {
    if (inner < ROSTER_W_MIN * 2) return n;
    const lw = Math.round(clamp(leftW, ROSTER_W_MIN, Math.min(ROSTER_W_MAX, inner - ROSTER_W_MIN)));
    const rw = Math.round(Math.min(ROSTER_W_MAX, inner - lw));
    return withWidths(n, { [fixedKeyOf(n, n.cols[cl])]: lw, [fixedKeyOf(n, n.cols[cr])]: rw });
  }
  return n;
}

/**
 * 2 窓の列の段の境目のドラッグ。
 * @param {object} l 配置
 * @param {number} proposedTopH 上の窓の提案の丈（px・窓の罫を含む外形）
 * @param {{colH: number, gap?: number, chromeH?: object, tight?: boolean}} env colH＝列の丈（.main の clientHeight）・
 *   chromeH・tight は roomAvail と同じ。
 * 規則: office を含む列＝部屋の窓の丈から k = clamp(round((丈 − chromeH.office − 罫 2) / 160), 2, 4)（office が下なら
 *   部屋の窓の丈＝colH − gap − proposedTopH）・相方の最低（roomAvail の reserveH）を割らないときだけ widths.roomCap = k
 *  （4＝上限なしは落とす）・割るなら同じ l。
 *   office を含まない列＝rows.split = clamp(proposedTopH / (colH − gap), max(0.2, 上の最低 / (colH − gap)),
 *   min(0.8, 1 − 下の最低 / (colH − gap)))（最低＝ROW_MIN_H・小数 4 桁・0.5＝既定は落とす）・下限が上限を超える丈なら同じ l。
 *   端末が上のときに間に挟まる指示欄の丈は数えない（開いている間だけの近似）。
 */
export function dragRowSplit(l, proposedTopH, env = {}) {
  const n = asLayout(l);
  const e = envOf(env);
  if (!fin(proposedTopH) || !fin(e.colH) || e.colH <= 0) return n;
  const gap = gapOf(e);
  const [A, B] = n.cols[n.cols.findIndex((c) => c.length === 2)];
  if (A === "office" || B === "office") {
    const officeH = A === "office" ? proposedTopH : e.colH - gap - proposedTopH;
    const ch = isObj(e.chromeH) ? e.chromeH : {};
    const k = clamp(Math.round((officeH - num(ch.office) - WIN_BORDER_H) / ROOM_H), ROOM_SCALE_MIN, ROOM_SCALE_MAX);
    const { usedH, reserveH } = heightBudget(n, e, e.colH);
    if (e.colH - usedH - ROOM_H * k < reserveH) return n;
    return withWidths(n, { roomCap: k });
  }
  const H = e.colH - gap;
  const lo = Math.max(SPLIT_MIN, ROW_MIN_H[A] / H);
  const hi = Math.min(SPLIT_MAX, 1 - ROW_MIN_H[B] / H);
  if (lo > hi) return n;
  const split = round4(clamp(proposedTopH / H, lo, hi));
  return commit(n, n.cols, n.widths, { ...n.rows, split });
}

/**
 * その境目だけ既定へ（ハンドルの dblclick）。0|1＝列の境目: room×flex は roomCap と termMin・fixed×flex は fixed の窓の幅・
 * room×fixed は roomCap と fixed の窓の幅・fixed×fixed は両方の幅。"row"＝段の境目: office を含む列は roomCap・
 * 含まない列は rows.split。それ以外は無変化（同じ参照）。
 * @param {0|1|"row"} which
 */
export function resetSplit(l, which) {
  const n = asLayout(l);
  if (which === "row") {
    const col = n.cols[n.cols.findIndex((c) => c.length === 2)];
    if (col.includes("office")) return withWidths(n, { roomCap: undefined });
    return commit(n, n.cols, n.widths, {});
  }
  if (which !== 0 && which !== 1) return n;
  const kinds = [kindIn(n, which), kindIn(n, which + 1)];
  const patch = {};
  if (kinds.includes("room")) patch.roomCap = undefined;
  if (kinds.includes("room") && kinds.includes("flex")) patch.termMin = undefined;
  for (const c of [which, which + 1]) {
    if (kindIn(n, c) !== "fixed") continue;
    for (const id of FIXED_IDS) if (n.cols[c].includes(id)) patch[id] = undefined;
  }
  return withWidths(n, patch);
}
