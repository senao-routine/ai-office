// R111-A1: コックピットの配置（ui/core/cplayout.js）の単体テスト。壊れた値は既定・既定の升目は CSS と同じ（ピン）・
// 入れ替えの決定論・部屋に使ってよい寸法は R110 の式と同じ（ピン）・境目の丸め・直列化の往復・どの配置でも升目に穴も重なりも無い。
import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_LAYOUT, FIXED_W_DEFAULT, GUTTER, PARTNER_MIN_H, ROOM_CHROME_W, ROOM_H, ROOM_SCALE_MAX, ROOM_SCALE_MIN, ROOM_W,
  ROSTER_W_MAX, ROSTER_W_MIN, TERM_MIN_COLS, TERM_MIN_W_DEFAULT, TERM_MIN_W_FLOOR, WINDOWS,
  columnKind, dragColumnSplit, dragRowSplit, findWindow, flexColumn, gridTemplate, isDefaultLayout, moveWindow,
  normalizeLayout, nudgeWindow, parseLayout, resetSplit, roomAvail, roomColumn, serializeLayout, snapRoomK, stackedColumn,
  swapWindows,
} from "./cplayout.js";
import { roomScale } from "./pxroom.js";

const L = (cols, widths = {}, rows = {}) => normalizeLayout({ v: 1, cols, widths, rows });

/** 正しい配置を全部（2 窓の列 3 通り × 窓の並び 24 通り＝72） */
function allLayouts() {
  const perms = [];
  const permute = (rest, acc) => {
    if (!rest.length) { perms.push(acc); return; }
    rest.forEach((id, i) => permute([...rest.slice(0, i), ...rest.slice(i + 1)], [...acc, id]));
  };
  permute([...WINDOWS], []);
  const out = [];
  for (let s = 0; s < 3; s += 1) {
    for (const p of perms) {
      const cols = [];
      let i = 0;
      for (let c = 0; c < 3; c += 1) {
        const len = c === s ? 2 : 1;
        cols.push(p.slice(i, i + len));
        i += len;
      }
      out.push(L(cols));
    }
  }
  return out;
}

/** 不変条件: 3 列・4 窓がちょうど 1 回ずつ・空の列なし・ちょうど 1 列が 2 窓 */
function assertValid(l, msg = "") {
  assert.equal(l.v, 1, msg);
  assert.equal(l.cols.length, 3, msg);
  assert.ok(l.cols.every((c) => c.length >= 1 && c.length <= 2), `${msg}: 空の列か 3 窓の列`);
  assert.equal(l.cols.filter((c) => c.length === 2).length, 1, msg);
  assert.deepEqual(l.cols.flat().sort(), [...WINDOWS].sort(), msg);
}

/** 深く凍結した写し（入力を書き換えたら strict mode で投げる） */
function frozenCopy(l) {
  const c = JSON.parse(JSON.stringify(l));
  c.cols.forEach((x) => Object.freeze(x));
  Object.freeze(c.cols);
  Object.freeze(c.widths);
  Object.freeze(c.rows);
  return Object.freeze(c);
}

/** grid-template-* の値を深さ 0 の空白で割る（minmax(…, 1fr) の中の空白では割らない） */
function tracksOf(str) {
  const out = [];
  let depth = 0;
  let cur = "";
  for (const ch of str) {
    if (ch === "(") depth += 1;
    if (ch === ")") depth -= 1;
    if (/\s/.test(ch) && depth === 0) {
      if (cur) out.push(cur);
      cur = "";
    } else cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}

const rectOf = (s) => {
  const m = /^(\d+) \/ (\d+) \/ (\d+) \/ (\d+)$/.exec(s);
  assert.ok(m, `grid-area の形でない: ${s}`);
  return m.slice(1).map(Number);
};

// ── ① 壊れた値は既定 ──────────────────────────────────────────────────────
test("cplayout: 壊れた値は DEFAULT_LAYOUT（JSON でない・v 違い・未知 id・重複・欠け・空の列・列数 ≠3・型違い）", () => {
  const ok = (cols) => ({ v: 1, cols });
  const bad = [
    undefined, null, 1, "x", [], true,
    { v: 0, cols: DEFAULT_LAYOUT.cols }, { v: "1", cols: DEFAULT_LAYOUT.cols }, { cols: DEFAULT_LAYOUT.cols },
    { v: 1 }, { v: 1, cols: "office" }, { v: 1, cols: {} },
    ok([["office", "reception"], ["terminal"], ["chat"]]),                       // 未知の id
    ok([["office", "office"], ["terminal"], ["roster"]]),                        // 重複
    ok([["office", "reception"], ["terminal"], ["terminal"]]),                   // 重複（列をまたぐ）
    ok([["office"], ["terminal"], ["roster"]]),                                  // 欠け
    ok([["office", "reception", "roster"], [], ["terminal"]]),                   // 空の列・3 窓
    ok([["office", "reception", "roster"], ["terminal"]]),                       // 2 列
    ok([["office"], ["reception"], ["terminal"], ["roster"]]),                   // 4 列
    ok([["office", "reception"], ["terminal"], "roster"]),                       // 列が配列でない
    ok([["office", "reception"], ["terminal"], [1]]),                            // id が文字列でない
    { ...ok([["terminal"], ["office", "reception"], ["roster"]]), widths: [] },  // widths が配列
    { ...ok([["terminal"], ["office", "reception"], ["roster"]]), widths: 3 },
    { ...ok([["terminal"], ["office", "reception"], ["roster"]]), widths: { roster: "300" } },
    { ...ok([["terminal"], ["office", "reception"], ["roster"]]), widths: { roomCap: null } },
    { ...ok([["terminal"], ["office", "reception"], ["roster"]]), widths: { termMin: NaN } },
    { ...ok([["terminal"], ["office", "reception"], ["roster"]]), rows: "x" },
    { ...ok([["terminal"], ["office", "reception"], ["roster"]]), rows: { split: "0.3" } },
  ];
  for (const raw of bad) assert.equal(normalizeLayout(raw), DEFAULT_LAYOUT, JSON.stringify(raw) ?? String(raw));
  for (const s of ["garbage", "", "{", "null", "1", '{"v":0}', '{"v":1,"cols":[["office"],["terminal"],["roster"]]}',
    '{"v":1,"cols":[["office","reception"],["terminal"],["nope"]]}']) {
    assert.equal(parseLayout(s), DEFAULT_LAYOUT, s);
  }
  assert.equal(parseLayout(null), DEFAULT_LAYOUT, "getItem の null");
  assert.equal(parseLayout(undefined), DEFAULT_LAYOUT);
  assert.equal(isDefaultLayout("garbage"), true);
});

test("cplayout: 範囲外の数だけなら丸めて残す・既定値のキーは落とす・未知のキーは落とす・既定の中身は DEFAULT_LAYOUT そのもの", () => {
  const cols = [["terminal"], ["office", "reception"], ["roster"]];
  const n = normalizeLayout({ v: 1, cols, widths: { roster: 9999, reception: 10, termMin: 100, roomCap: 1, foo: 3 }, rows: { split: 0.95, bar: 1 } });
  assert.deepEqual(n.widths, { roster: ROSTER_W_MAX, reception: ROSTER_W_MIN, termMin: TERM_MIN_W_FLOOR, roomCap: ROOM_SCALE_MIN });
  assert.deepEqual(n.rows, { split: 0.8 });
  assert.deepEqual(normalizeLayout({ v: 1, cols, rows: { split: 0.01 } }).rows, { split: 0.2 });
  assert.deepEqual(normalizeLayout({ v: 1, cols, widths: { termMin: 9999, roomCap: 7, roster: 300.4 } }).widths, { roster: 300 },
    "termMin 560・roomCap 4 は既定＝落とす");
  assert.deepEqual(normalizeLayout({ v: 1, cols, rows: { split: 0.5 } }).rows, {});
  // 既定と同じ中身（キーの欠け・既定値のキー・余計なキー）は DEFAULT_LAYOUT そのもの
  for (const raw of [
    { v: 1, cols: [["office", "reception"], ["terminal"], ["roster"]] },
    { v: 1, cols: [["office", "reception"], ["terminal"], ["roster"]], widths: { roomCap: 4, termMin: 560 }, rows: { split: 0.5 }, panes: 1 },
  ]) assert.equal(normalizeLayout(raw), DEFAULT_LAYOUT, JSON.stringify(raw));
  // 返す配置は深く凍結・入力は書き換えない
  const raw = { v: 1, cols: [["office"], ["terminal"], ["roster", "reception"]], widths: { roster: 300 } };
  const before = JSON.stringify(raw);
  const m = normalizeLayout(raw);
  assert.equal(JSON.stringify(raw), before);
  assert.ok(Object.isFrozen(m) && Object.isFrozen(m.cols) && m.cols.every(Object.isFrozen) && Object.isFrozen(m.widths));
  assert.notEqual(m.cols, raw.cols);
  assert.ok(Object.isFrozen(DEFAULT_LAYOUT) && Object.isFrozen(DEFAULT_LAYOUT.cols) && DEFAULT_LAYOUT.cols.every(Object.isFrozen));
});

test("cplayout: 定数（出どころの値と一致）", () => {
  assert.equal(TERM_MIN_W_DEFAULT, 560);
  assert.equal(ROOM_CHROME_W, 18);
  assert.equal(TERM_MIN_COLS, 40);
  assert.equal(TERM_MIN_W_FLOOR, 352);
  assert.deepEqual([ROSTER_W_MIN, ROSTER_W_MAX, GUTTER], [240, 520, 8]);
  assert.deepEqual([ROOM_W, ROOM_H, ROOM_SCALE_MIN, ROOM_SCALE_MAX], [184, 160, 2, 4]);
  assert.deepEqual(PARTNER_MIN_H, { reception: 200, receptionTight: 120, roster: 120, terminal: 200 });
  assert.equal(FIXED_W_DEFAULT, "clamp(280px, 18vw, 320px)");
  assert.deepEqual(WINDOWS, ["office", "reception", "terminal", "roster"]);
  assert.deepEqual(DEFAULT_LAYOUT, { v: 1, cols: [["office", "reception"], ["terminal"], ["roster"]], widths: {}, rows: {} });
});

// ── ② 既定の升目のピン ─────────────────────────────────────────────────────
test("cplayout: gridTemplate(DEFAULT) は既定の CSS と同じ升目（×2・×3・×4）", () => {
  for (const k of [2, 3, 4]) {
    for (const env of [{ k }, { k, mainW: 1440 }, { k, mainW: 1920 }]) {
      const g = gridTemplate(DEFAULT_LAYOUT, env);
      assert.equal(g.cols, `${184 * k + 18}px minmax(560px, 1fr) clamp(280px, 18vw, 320px)`);
      assert.equal(g.rows, "auto 8px minmax(120px, 1fr) 0px", "指示欄の行は丈 0px の錨（R111-S0 の style.css と同じ）");
      assert.equal(g.areas, '"office center right" "ogap center right" "reception center right" "reception sheet right"');
      assert.deepEqual(g.area, {
        office: "1 / 1 / 2 / 2", reception: "3 / 1 / 5 / 2", terminal: "1 / 2 / 4 / 3", roster: "1 / 3 / 5 / 4",
        sheet: "4 / 2 / 5 / 3", rowsplit: "2 / 1 / 3 / 2",
      });
      assert.deepEqual(g.colsplits, [1, 2]);
    }
  }
  assert.equal(gridTemplate(DEFAULT_LAYOUT, { k: 3 }).cols.split(" ")[0], "570px");
  assert.equal(gridTemplate(DEFAULT_LAYOUT, { k: 4 }).cols.split(" ")[0], "754px");
  assert.equal(gridTemplate(DEFAULT_LAYOUT).cols.split(" ")[0], "570px", "k が無ければ ×3");
});

// ── ③ 入れ替え・移動・nudge ────────────────────────────────────────────────
test("cplayout: swap を 2 回で元・同じ窓や未知の id は無変化（同じ参照）・widths／rows は持ち越す", () => {
  for (const l of allLayouts()) {
    for (const a of WINDOWS) {
      for (const b of WINDOWS) {
        const once = swapWindows(l, a, b);
        assertValid(once, `${a}↔${b}`);
        if (a === b) assert.equal(once, l);
        else assert.notDeepEqual(once.cols, l.cols);
        assert.deepEqual(swapWindows(once, a, b).cols, l.cols, `${JSON.stringify(l.cols)} ${a}↔${b}`);
      }
    }
    assert.equal(swapWindows(l, "office", "chat"), l);
  }
  const w = L([["office", "reception"], ["terminal"], ["roster"]], { roster: 300, roomCap: 3 }, {});
  const s = swapWindows(w, "terminal", "roster");
  assert.deepEqual(s.cols, [["office", "reception"], ["roster"], ["terminal"]]);
  assert.deepEqual(s.widths, { roster: 300, roomCap: 3 });
  // 入れ替えで既定の並びに戻れば DEFAULT_LAYOUT そのもの
  assert.equal(swapWindows(swapWindows(DEFAULT_LAYOUT, "terminal", "roster"), "terminal", "roster"), DEFAULT_LAYOUT);
  // 入力は書き換えない
  const f = frozenCopy(L([["terminal"], ["office", "reception"], ["roster"]]));
  assert.deepEqual(swapWindows(f, "office", "roster").cols, [["terminal"], ["roster", "reception"], ["office"]]);
});

test("cplayout: move は元の列が空になる操作を無変化にする（同じ参照）・段は丸める・同じ列は並べ替え", () => {
  const d = DEFAULT_LAYOUT;
  assert.equal(moveWindow(d, "terminal", 0, 2), d, "端末の列が空になる");
  assert.equal(moveWindow(d, "roster", 1, 0), d, "名簿の列が空になる");
  assert.equal(moveWindow(d, "office", 0, 0), d, "同じ場所");
  assert.equal(moveWindow(d, "office", 3, 0), d, "列が範囲外");
  assert.equal(moveWindow(d, "office", 1, 0.5), d, "段が整数でない");
  assert.equal(moveWindow(d, "chat", 1, 0), d, "未知の id");
  assert.deepEqual(moveWindow(d, "reception", 1, 1).cols, [["office"], ["terminal", "reception"], ["roster"]]);
  assert.deepEqual(moveWindow(d, "reception", 1, 0).cols, [["office"], ["reception", "terminal"], ["roster"]]);
  assert.deepEqual(moveWindow(d, "reception", 2, 99).cols, [["office"], ["terminal"], ["roster", "reception"]], "段は丸める");
  assert.deepEqual(moveWindow(d, "office", 0, 1).cols, [["reception", "office"], ["terminal"], ["roster"]], "同じ列の上下");
  for (const l of allLayouts()) {
    for (const id of WINDOWS) {
      for (let col = 0; col < 3; col += 1) {
        for (let row = 0; row < 3; row += 1) {
          const m = moveWindow(l, id, col, row);
          assertValid(m);
          const from = findWindow(l, id).col;
          if (l.cols[from].length === 1 && col !== from) assert.equal(m, l, "1 窓の列から出すと空になる");
        }
      }
    }
  }
});

test("cplayout: nudge 4 方向は全窓で決定論・端で無変化・既定の配置での行き先", () => {
  const d = DEFAULT_LAYOUT;
  const cases = [
    ["office", "left", null], ["office", "up", null], ["office", "down", [["reception", "office"], ["terminal"], ["roster"]]],
    ["office", "right", [["reception"], ["terminal", "office"], ["roster"]]],
    ["reception", "up", [["reception", "office"], ["terminal"], ["roster"]]], ["reception", "down", null],
    ["reception", "left", null], ["reception", "right", [["office"], ["terminal", "reception"], ["roster"]]],
    ["terminal", "up", null], ["terminal", "down", null],
    ["terminal", "left", [["terminal", "reception"], ["office"], ["roster"]]],
    ["terminal", "right", [["office", "reception"], ["roster"], ["terminal"]]],
    ["roster", "right", null], ["roster", "up", null], ["roster", "down", null],
    ["roster", "left", [["office", "reception"], ["roster"], ["terminal"]]],
  ];
  for (const [id, dir, want] of cases) {
    const got = nudgeWindow(d, id, dir);
    if (want === null) assert.equal(got, d, `${id} ${dir} は端＝無変化`);
    else assert.deepEqual(got.cols, want, `${id} ${dir}`);
  }
  assert.equal(nudgeWindow(d, "office", "diagonal"), d);
  assert.equal(nudgeWindow(d, "chat", "left"), d);
  for (const l of allLayouts()) {
    for (const id of WINDOWS) {
      for (const dir of ["left", "right", "up", "down"]) {
        const a = nudgeWindow(l, id, dir);
        assertValid(a, `${JSON.stringify(l.cols)} ${id} ${dir}`);
        assert.deepEqual(nudgeWindow(l, id, dir), a, "同じ入力で違う結果");
        const p = findWindow(l, id);
        const edge = (dir === "left" && p.col === 0) || (dir === "right" && p.col === 2)
          || ((dir === "up" || dir === "down") && l.cols[p.col].length === 1)
          || (dir === "up" && p.row === 0) || (dir === "down" && p.row === 1);
        if (edge) assert.equal(a, l, `${JSON.stringify(l.cols)} ${id} ${dir} は端`);
        else assert.notEqual(a, l, `${JSON.stringify(l.cols)} ${id} ${dir} が動かない`);
        // 左右は列が 1 つずれる（2 窓の列からは移動・1 窓の列からは入れ替え）
        if (!edge && (dir === "left" || dir === "right")) {
          assert.equal(findWindow(a, id).col, p.col + (dir === "left" ? -1 : 1));
        }
      }
    }
  }
});

test("cplayout: 読み取り（findWindow・stackedColumn・roomColumn・flexColumn・columnKind）", () => {
  const d = DEFAULT_LAYOUT;
  assert.deepEqual(findWindow(d, "reception"), { col: 0, row: 1 });
  assert.equal(findWindow(d, "chat"), null);
  assert.equal(stackedColumn(d), 0);
  assert.equal(roomColumn(d), 0);
  assert.equal(flexColumn(d), 1);
  assert.deepEqual([0, 1, 2].map((c) => columnKind(d, c)), ["room", "flex", "fixed"]);
  assert.equal(columnKind(d, 3), null);
  const same = L([["roster"], ["office", "terminal"], ["reception"]]);
  assert.deepEqual([0, 1, 2].map((c) => columnKind(same, c)), ["fixed", "flex", "fixed"], "office と terminal が同じ列は flex");
  assert.equal(roomColumn(same), flexColumn(same));
  const room2 = L([["terminal"], ["roster"], ["reception", "office"]]);
  assert.deepEqual([0, 1, 2].map((c) => columnKind(room2, c)), ["flex", "fixed", "room"]);
  assert.equal(stackedColumn(room2), 2);
});

// ── ④ 部屋に使ってよい寸法（R110 の式のピン）──────────────────────────────
/** R110 の index.js（roomAvailW・roomAvailH の 3 カラムの枝）の式をそのまま写したもの（S0 で leftEl の丈→ .main の丈） */
function r110Avail({ mainW, mainH, gap, rightW, secOffice, setup, secReception }) {
  const availW = mainW - gap * 2 - rightW - 560 - 18;
  const used = gap + 4 + secOffice + setup + secReception;
  const colH = mainH;
  let reserve = 200;
  const slack = colH - used - 160 * 4;
  if (slack >= 120 && slack < 200) reserve = 120;
  const availH = Math.max(160 * 2, Math.min(160 * 4, colH - used - reserve));
  return { availW, availH, used, reserve };
}

test("cplayout: roomAvail の既定は R110 の式と同じ数（1440×900 ×3・1920×1080 ×4・1440×640 ×2・1920×950 は受付を詰めて ×4）", () => {
  // .cpshell は 56px の見出し＋36px の足元＝.main の丈は窓の丈 − 92。名簿は clamp(280px, 18vw, 320px)
  const shapes = [
    { name: "1440×900", mainW: 1440, mainH: 808, rightW: 280, k: 3 },
    { name: "1920×1080", mainW: 1920, mainH: 988, rightW: 320, k: 4 },
    { name: "1440×640", mainW: 1440, mainH: 548, rightW: 280, k: 2 },
    { name: "1920×950（本人の窓・受付 120）", mainW: 1920, mainH: 858, rightW: 320, k: 4, tight: true },
    { name: "1424（指示の例）", mainW: 1424, mainH: 808, rightW: 280, k: 2 },
  ];
  for (const s of shapes) {
    for (const setup of [0, 72]) {
      const old = r110Avail({ ...s, gap: 8, secOffice: 35, setup, secReception: 35 });
      const env = { mainW: s.mainW, mainH: s.mainH, gap: 8, k: 3, termMin: 560, fixedW: { roster: s.rightW, reception: 0 },
        chromeH: { office: 35 + setup, reception: 35, roster: 35, terminal: 70 } };
      const r = roomAvail(DEFAULT_LAYOUT, env);
      assert.equal(r.availW, old.availW, `${s.name}: availW`);
      assert.equal(r.availH, old.availH, `${s.name} setup ${setup}: availH`);
      assert.equal(r.usedH, old.used, `${s.name}: usedH`);
      assert.equal(r.reserveH, old.reserve, `${s.name}: reserveH`);
      // tight を呼び手が渡しても同じ（呼び手の判定＝R110 の規則）
      assert.deepEqual(roomAvail(DEFAULT_LAYOUT, { ...env, tight: old.reserve === 120 }), r);
      if (setup === 0) {
        assert.equal(roomScale(r.availW, r.availH), s.k, `${s.name}: ×${s.k}`);
        if (s.tight) assert.equal(r.reserveH, 120, `${s.name}: 受付を詰める形`);
      }
    }
  }
  assert.equal(roomAvail(DEFAULT_LAYOUT, { mainW: 1424, mainH: 808, gap: 8, termMin: 560, fixedW: { roster: 280 } }).availW, 550,
    "1424 − 16 − 280 − 560 − 18");
});

test("cplayout: roomAvail は配置に従う（fixed の列の実幅の和・端末と同じ列なら termMin を引かない・相方ごとの最低）", () => {
  const env = { mainW: 1920, mainH: 988, gap: 8, termMin: 560, fixedW: { roster: 320, reception: 300 },
    chromeH: { office: 35, reception: 35, roster: 40, terminal: 70 } };
  // 部屋が 1 窓の列（相方なし）・受付と名簿がそれぞれ fixed
  const alone = L([["office"], ["terminal"], ["roster", "reception"]]);
  let r = roomAvail(alone, env);
  assert.equal(r.availW, 1920 - 16 - 320 - 560 - 18, "2 窓の fixed 列は窓の実幅（同じ幅）の 1 本だけ");
  assert.deepEqual([r.usedH, r.reserveH], [35 + 2, 0]);
  const two = L([["office"], ["roster"], ["terminal", "reception"]]);
  r = roomAvail(two, env);
  assert.equal(r.availW, 1920 - 16 - 320 - 560 - 18);
  // 相方が名簿＝120・端末＝200
  r = roomAvail(L([["office", "roster"], ["terminal"], ["reception"]]), env);
  assert.equal(r.availW, 1920 - 16 - 300 - 560 - 18);
  assert.deepEqual([r.usedH, r.reserveH], [35 + 2 + 8 + 40 + 2, 120]);
  r = roomAvail(L([["office", "terminal"], ["roster"], ["reception"]]), env);
  assert.equal(r.availW, 1920 - 16 - 320 - 300 - 18, "端末と同じ列＝termMin を引かない");
  assert.deepEqual([r.usedH, r.reserveH], [35 + 2 + 8 + 70 + 2, 200]);
  // termMin は env が先・無ければ配置の値
  const narrow = L([["office", "reception"], ["terminal"], ["roster"]], { termMin: 400 });
  assert.equal(roomAvail(narrow, { ...env, termMin: undefined }).availW, 1920 - 16 - 320 - 400 - 18);
  // 数が無くても投げない（丈は ×2 の床）
  const z = roomAvail(DEFAULT_LAYOUT, {});
  assert.equal(z.availH, 320);
  assert.ok(Number.isFinite(z.availW));
});

// ── ⑤ 列の境目のドラッグ ───────────────────────────────────────────────────
test("cplayout: snapRoomK は (w − 18) / 184 を四捨五入して 2〜4", () => {
  const cases = [[386, 2], [570, 3], [754, 4], [1000, 4], [100, 2], [477, 2], [478, 3], [661, 3], [662, 4], [NaN, 2], [undefined, 2]];
  for (const [w, k] of cases) assert.equal(snapRoomK(w), k, String(w));
});

test("cplayout: 部屋×端末の境目は k をスナップし、端末を 40 桁より狭めない（1440 でも ×4 に届く・戻せば既定）", () => {
  // 1440×900: 部屋 570・端末 574・名簿 280 → 境目 0 の 2 列は 570 + 8 + 574
  const pairW = 570 + 8 + 574;
  const to4 = dragColumnSplit(DEFAULT_LAYOUT, 0, 760, { pairW, gap: 8, termFloor: 352 });
  assert.deepEqual(to4.widths, { termMin: 390 }, "×4＝754 の残り 390 が端末の最低・roomCap 4 は上限なし＝落とす");
  assert.equal(to4.widths.termMin, pairW - 8 - 754);
  // 動かした後の roomAvail は ×4 を許す（境目と倍率の計算が噛み合う）
  const env = { mainW: 1440, mainH: 988, gap: 8, fixedW: { roster: 280 }, chromeH: { office: 35, reception: 35 } };
  const r = roomAvail(to4, env);
  assert.equal(r.availW, 736);
  assert.equal(roomScale(r.availW, r.availH), 4);
  // 40 桁の実測が残りより広い＝×4 は成立しない＝同じ参照
  assert.equal(dragColumnSplit(DEFAULT_LAYOUT, 0, 760, { pairW, gap: 8, termFloor: 420 }), DEFAULT_LAYOUT);
  // termFloor が無ければ 352・352 未満の実測は 352 へ丸める
  assert.equal(dragColumnSplit(DEFAULT_LAYOUT, 0, 760, { pairW: 754 + 8 + 350, gap: 8 }), DEFAULT_LAYOUT);
  assert.equal(dragColumnSplit(DEFAULT_LAYOUT, 0, 760, { pairW: 754 + 8 + 350, gap: 8, termFloor: 300 }), DEFAULT_LAYOUT);
  // ×4 から ×3 へ戻すと端末の最低も既定（80 桁）へ戻る＝roomCap 3 だけ
  const back = dragColumnSplit(to4, 0, 575, { pairW, gap: 8, termFloor: 352 });
  assert.deepEqual(back.widths, { roomCap: 3 });
  // 1920: ×3 へ縮めて ×4 へ戻すと既定そのもの
  const pair19 = 754 + 8 + 830;
  const to3 = dragColumnSplit(DEFAULT_LAYOUT, 0, 560, { pairW: pair19, gap: 8, termFloor: 352 });
  assert.deepEqual(to3.widths, { roomCap: 3 });
  assert.equal(dragColumnSplit(to3, 0, 760, { pairW: pair19, gap: 8, termFloor: 352 }), DEFAULT_LAYOUT);
  // 同じ提案の繰り返しは同じ参照（pointermove のたびに setLayout しない）
  assert.equal(dragColumnSplit(to3, 0, 580, { pairW: pair19, gap: 8, termFloor: 352 }), to3);
  // 端末が左・部屋が右（対称）
  const flip = L([["terminal"], ["office", "reception"], ["roster"]]);
  const f4 = dragColumnSplit(flip, 0, 390, { pairW, gap: 8, termFloor: 352 });
  assert.deepEqual(f4.widths, { termMin: 390 });
  assert.equal(dragColumnSplit(flip, 0, 300, { pairW, gap: 8, termFloor: 420 }), flip);
  // 端末は常に 40 桁以上（全ての提案で）
  for (let p = 0; p <= pairW; p += 7) {
    for (const floor of [352, 400, 480]) {
      const n = dragColumnSplit(DEFAULT_LAYOUT, 0, p, { pairW, gap: 8, termFloor: floor });
      const k = n.widths.roomCap ?? 4;
      const term = pairW - 8 - (184 * k + 18);
      if (n !== DEFAULT_LAYOUT) assert.ok(term >= floor, `p=${p} floor=${floor}: 端末 ${term}px`);
      assert.ok((n.widths.termMin ?? 560) >= floor);
    }
  }
});

test("cplayout: 名簿×端末の境目は 240〜520 と端末の最低で丸める・入らなければ同じ参照", () => {
  // 1440: 端末 574・名簿 280 → 境目 1 の 2 列は 574 + 8 + 280＝862（左＝端末）
  const pairW = 574 + 8 + 280;
  assert.deepEqual(dragColumnSplit(DEFAULT_LAYOUT, 1, 400, { pairW, gap: 8 }).widths, { roster: 862 - 8 - 560 }, "端末の 560 を割らない");
  assert.deepEqual(dragColumnSplit(DEFAULT_LAYOUT, 1, 800, { pairW, gap: 8 }).widths, { roster: 240 }, "名簿の下限");
  // 1920: 端末 830・名簿 320
  const pair19 = 830 + 8 + 320;
  assert.deepEqual(dragColumnSplit(DEFAULT_LAYOUT, 1, 300, { pairW: pair19, gap: 8 }).widths, { roster: 520 }, "名簿の上限");
  assert.deepEqual(dragColumnSplit(DEFAULT_LAYOUT, 1, 700, { pairW: pair19, gap: 8 }).widths, { roster: 450 });
  // 端末の最低を下げてあれば名簿はそこまで広がれる
  const narrow = L(DEFAULT_LAYOUT.cols, { termMin: 390 });
  assert.equal(dragColumnSplit(narrow, 1, 100, { pairW, gap: 8 }).widths.roster, 862 - 8 - 390);
  // 2 列の幅が足りない＝同じ参照
  assert.equal(dragColumnSplit(DEFAULT_LAYOUT, 1, 300, { pairW: 700, gap: 8 }), DEFAULT_LAYOUT);
  // 左が名簿（fixed×flex）・受付だけの列も同じ範囲
  const left = L([["office", "reception"], ["roster"], ["terminal"]]);
  assert.deepEqual(dragColumnSplit(left, 1, 330, { pairW: 280 + 8 + 574, gap: 8 }).widths, { roster: 294 });
  const rec = L([["office", "roster"], ["terminal"], ["reception"]]);
  assert.deepEqual(dragColumnSplit(rec, 1, 500, { pairW: pair19, gap: 8 }).widths, { reception: 520 }, "受付も 520 まで");
  // 端末と部屋が同じ列なら、その列は ×2 の部屋（386）と termMin の大きい方を残す
  const same = L([["roster"], ["office", "terminal"], ["reception"]], { termMin: 352 });
  assert.equal(dragColumnSplit(same, 0, 900, { pairW: 520 + 8 + 400, gap: 8 }).widths.roster, 520);
  assert.equal(dragColumnSplit(same, 0, 900, { pairW: 300 + 8 + 400, gap: 8 }).widths.roster, 700 - 386, "352 でなく 386 を残す");
  // 引数が壊れていれば同じ参照
  for (const [b, p, env] of [[2, 300, { pairW }], [0, NaN, { pairW }], [0, 300, {}], [0, 300, { pairW: -1 }], ["0", 300, { pairW }]]) {
    assert.equal(dragColumnSplit(DEFAULT_LAYOUT, b, p, env), DEFAULT_LAYOUT, JSON.stringify([b, p, env]));
  }
});

test("cplayout: 部屋×名簿の境目は k をスナップ・名簿が 240 を割るなら同じ参照・名簿×名簿は合計を保つ", () => {
  const l = L([["office", "reception"], ["roster"], ["terminal"]]);
  const pairW = 570 + 8 + 280;
  assert.equal(dragColumnSplit(l, 0, 760, { pairW, gap: 8 }), l, "×4 だと名簿 96px");
  assert.deepEqual(dragColumnSplit(l, 0, 380, { pairW, gap: 8 }).widths, { roster: 464, roomCap: 2 });
  const big = dragColumnSplit(l, 0, 380, { pairW: 754 + 8 + 520, gap: 8 });
  assert.deepEqual(big.widths, { roster: 520, roomCap: 2 }, "520 を超える分は離れた端末の列が吸う");
  // 名簿が左・部屋が右（対称）
  const r = L([["roster"], ["office", "reception"], ["terminal"]]);
  assert.deepEqual(dragColumnSplit(r, 0, 464, { pairW, gap: 8 }).widths, { roster: 464, roomCap: 2 });
  // 名簿×受付（端末と部屋が同じ列のときだけ）
  const ff = L([["office", "terminal"], ["roster"], ["reception"]]);
  assert.deepEqual(dragColumnSplit(ff, 1, 300, { pairW: 280 + 8 + 280, gap: 8 }).widths, { roster: 300, reception: 260 });
  assert.deepEqual(dragColumnSplit(ff, 1, 100, { pairW: 280 + 8 + 280, gap: 8 }).widths, { roster: 240, reception: 320 });
  assert.deepEqual(dragColumnSplit(ff, 1, 900, { pairW: 600 + 8 + 600, gap: 8 }).widths, { roster: 520, reception: 520 });
  assert.equal(dragColumnSplit(ff, 1, 300, { pairW: 400, gap: 8 }), ff, "2 列とも 240 が入らない");
});

// ── ⑥ 段の境目のドラッグ ───────────────────────────────────────────────────
test("cplayout: 段の境目＝部屋の列は 160k にスナップ・相方の最低を割らないときだけ・部屋の無い列は split を clamp", () => {
  const ch = { office: 35, reception: 35, roster: 35, terminal: 70 };
  // 1920×1080（列の丈 988）: 部屋の窓の丈 517＝×3
  const d3 = dragRowSplit(DEFAULT_LAYOUT, 35 + 2 + 480, { colH: 988, gap: 8, chromeH: ch });
  assert.deepEqual(d3.widths, { roomCap: 3 });
  assert.deepEqual(dragRowSplit(DEFAULT_LAYOUT, 35 + 2 + 320 + 70, { colH: 988, gap: 8, chromeH: ch }).widths, { roomCap: 2 });
  assert.equal(dragRowSplit(d3, 2000, { colH: 988, gap: 8, chromeH: ch }), DEFAULT_LAYOUT, "×4 へ戻す＝上限なし＝既定");
  // 1440×900（列の丈 808）: ×4 は受付が 200 を割る＝同じ参照・×3 は入る
  assert.equal(dragRowSplit(DEFAULT_LAYOUT, 900, { colH: 808, gap: 8, chromeH: ch }), DEFAULT_LAYOUT);
  assert.deepEqual(dragRowSplit(DEFAULT_LAYOUT, 517, { colH: 808, gap: 8, chromeH: ch }).widths, { roomCap: 3 });
  // 1920×950（列の丈 858）: 受付を 120 に詰めれば ×4＝×4 は成立（既定のまま）・×3 も成立
  assert.equal(dragRowSplit(DEFAULT_LAYOUT, 2000, { colH: 858, gap: 8, chromeH: ch }), DEFAULT_LAYOUT);
  // 1440×640（列の丈 548）: ×2 でも受付が 200 を割る＝何も変えない
  assert.equal(dragRowSplit(DEFAULT_LAYOUT, 357, { colH: 548, gap: 8, chromeH: ch }), DEFAULT_LAYOUT);
  // 部屋が下: 上の窓の提案から部屋の窓の丈を出す
  const under = L([["reception", "office"], ["terminal"], ["roster"]]);
  assert.deepEqual(dragRowSplit(under, 988 - 8 - (35 + 2 + 320), { colH: 988, gap: 8, chromeH: ch }).widths, { roomCap: 2 });
  // 部屋の無い 2 窓の列（端末＋受付）: 丈 808 − 8＝800・下限 max(0.2, 200/800)・上限 min(0.8, 1 − 120/800)
  const tr = L([["office"], ["terminal", "reception"], ["roster"]]);
  const at = (p, colH = 808) => dragRowSplit(tr, p, { colH, gap: 8, chromeH: ch });
  assert.deepEqual(at(100).rows, { split: 0.25 });
  assert.deepEqual(at(700).rows, { split: 0.8 });
  assert.deepEqual(at(480).rows, { split: 0.6 });
  assert.equal(at(400), tr, "0.5＝既定");
  assert.equal(at(100, 300), tr, "丈が両方の最低に足りない");
  const rr = L([["office"], ["roster", "reception"], ["terminal"]]);
  assert.deepEqual(dragRowSplit(rr, 50, { colH: 808, gap: 8 }).rows, { split: 0.2 });
  assert.deepEqual(dragRowSplit(rr, 790, { colH: 808, gap: 8 }).rows, { split: 0.8 });
  // 壊れた引数は同じ参照
  assert.equal(dragRowSplit(tr, NaN, { colH: 808 }), tr);
  assert.equal(dragRowSplit(tr, 400, {}), tr);
});

test("cplayout: resetSplit はその境目だけ既定へ", () => {
  const l = L([["office", "reception"], ["terminal"], ["roster"]], { roster: 400, termMin: 400, roomCap: 3 });
  assert.deepEqual(resetSplit(l, 0).widths, { roster: 400 }, "部屋×端末＝roomCap と termMin");
  assert.deepEqual(resetSplit(l, 1).widths, { termMin: 400, roomCap: 3 }, "端末×名簿＝名簿の幅");
  assert.deepEqual(resetSplit(l, "row").widths, { roster: 400, termMin: 400 }, "部屋の列の段＝roomCap");
  assert.equal(resetSplit(DEFAULT_LAYOUT, 0), DEFAULT_LAYOUT);
  assert.equal(resetSplit(l, 2), l);
  const s = L([["office"], ["terminal", "reception"], ["roster"]], { reception: 300 }, { split: 0.7 });
  assert.deepEqual(resetSplit(s, "row").rows, {});
  assert.deepEqual(resetSplit(s, "row").widths, { reception: 300 });
  const rf = L([["office", "reception"], ["roster"], ["terminal"]], { roster: 464, roomCap: 2, termMin: 400 });
  assert.deepEqual(resetSplit(rf, 0).widths, { termMin: 400 }, "部屋×名簿＝roomCap と名簿の幅");
  const ff = L([["office", "terminal"], ["roster"], ["reception"]], { roster: 300, reception: 260 });
  assert.deepEqual(resetSplit(ff, 1).widths, {}, "名簿×受付＝両方の幅");
  assert.deepEqual(resetSplit(ff, 1).cols, ff.cols);
  assert.deepEqual(resetSplit(ff, 0).widths, { reception: 260 }, "部屋と端末の列×名簿＝名簿の幅だけ");
});

// ── ⑦ 直列化 ────────────────────────────────────────────────────────────────
test("cplayout: serialize∘parse の往復・既定は null・既定値のキーは落ちる・キーの順は決定論", () => {
  assert.equal(serializeLayout(DEFAULT_LAYOUT), null);
  assert.equal(serializeLayout("garbage"), null, "壊れた値＝鍵を消す");
  assert.equal(serializeLayout({ v: 1, cols: DEFAULT_LAYOUT.cols, widths: { roomCap: 4, termMin: 560 }, rows: { split: 0.5 } }), null);
  const w = { v: 1, cols: [["office", "reception"], ["terminal"], ["roster"]], widths: { roomCap: 4, roster: 300, termMin: 560 }, rows: { split: 0.5 } };
  assert.equal(serializeLayout(w), '{"v":1,"cols":[["office","reception"],["terminal"],["roster"]],"widths":{"roster":300}}');
  const all = { v: 1, cols: [["terminal", "reception"], ["office"], ["roster"]],
    widths: { roomCap: 2, termMin: 400, reception: 250, roster: 300 }, rows: { split: 0.65 } };
  assert.equal(serializeLayout(all),
    '{"v":1,"cols":[["terminal","reception"],["office"],["roster"]],"widths":{"roster":300,"reception":250,"termMin":400,"roomCap":2},"rows":{"split":0.65}}');
  for (const l of [...allLayouts(), normalizeLayout(all), normalizeLayout(w)]) {
    const s = serializeLayout(l);
    if (isDefaultLayout(l)) {
      assert.equal(s, null);
      continue;
    }
    const back = parseLayout(s);
    assert.deepEqual(back, l);
    assert.equal(serializeLayout(back), s);
  }
});

// ── ⑧ どの配置でも升目に穴も重なりも無い ─────────────────────────────────
test("cplayout: gridTemplate は全ての配置で 4 行×3 列を穴なく重ならずに埋める・指示欄は端末の直下・areas と area が一致", () => {
  const names = { office: "office", reception: "reception", terminal: "center", roster: "right", sheet: "sheet", rowsplit: "ogap" };
  for (const l of allLayouts()) {
    for (const k of [2, 3, 4]) {
      const g = gridTemplate(l, { k });
      const tag = `${JSON.stringify(l.cols)} ×${k}`;
      assert.equal(tracksOf(g.cols).length, 3, `${tag}: 列 ${g.cols}`);
      assert.equal(tracksOf(g.rows).length, 4, `${tag}: 行 ${g.rows}`);
      assert.deepEqual(g.colsplits, [1, 2]);
      const cell = [0, 1, 2, 3].map(() => [null, null, null]);
      for (const [name, s] of Object.entries(g.area)) {
        const [r1, c1, r2, c2] = rectOf(s);
        assert.ok(r1 >= 1 && r2 <= 5 && r1 < r2 && c1 >= 1 && c2 <= 4 && c2 === c1 + 1, `${tag}: ${name} ${s}`);
        for (let r = r1; r < r2; r += 1) {
          assert.equal(cell[r - 1][c1 - 1], null, `${tag}: ${name} が ${cell[r - 1][c1 - 1]} と重なる`);
          cell[r - 1][c1 - 1] = name;
        }
      }
      assert.ok(cell.every((row) => row.every((x) => x !== null)), `${tag}: 穴 ${JSON.stringify(cell)}`);
      // 指示欄は端末の直下（同じ列・端末の終わりの行から）
      const t = rectOf(g.area.terminal);
      const sh = rectOf(g.area.sheet);
      assert.deepEqual([sh[0], sh[1]], [t[2], t[1]], `${tag}: 指示欄が端末の直下でない`);
      // 溝は 2 窓の列の 8px の行
      const rs = rectOf(g.area.rowsplit);
      assert.equal(rs[1] - 1, stackedColumn(l), tag);
      assert.equal(tracksOf(g.rows)[rs[0] - 1], "8px", `${tag}: 溝の行`);
      // areas の文字列は area と同じ升目
      const rows = [...g.areas.matchAll(/"([^"]*)"/g)].map((m) => m[1].split(" "));
      assert.equal(rows.length, 4, tag);
      for (let r = 0; r < 4; r += 1) {
        for (let c = 0; c < 3; c += 1) assert.equal(rows[r][c], names[cell[r][c]], `${tag}: areas (${r + 1}, ${c + 1})`);
      }
      // 列の幅: 部屋の列は 184k + 18・端末の列は minmax(560px, 1fr)・それ以外は名簿の既定
      l.cols.forEach((col, c) => {
        const tr = tracksOf(g.cols)[c];
        const kind = columnKind(l, c);
        if (kind === "room") assert.equal(tr, `${184 * k + 18}px`, tag);
        else if (kind === "flex") assert.equal(tr, "minmax(560px, 1fr)", tag);
        else assert.equal(tr, FIXED_W_DEFAULT, tag);
      });
      // 2 窓の列の部屋の窓は auto の行だけ（絵の丈ちょうど＝相方が残りを取る）。下の窓のときは最後の 0px の錨の行もまたぐ（丈は取らない）
      if (l.cols[stackedColumn(l)].includes("office")) {
        const o = rectOf(g.area.office);
        assert.equal(tracksOf(g.rows)[o[0] - 1], "auto", `${tag}: 部屋の窓の先頭の行`);
        for (let r = o[0]; r < o[2]; r += 1) {
          assert.ok(["auto", "0px"].includes(tracksOf(g.rows)[r - 1]), `${tag}: 部屋の行 ${r}`);
        }
      }
      // 指示欄の行は丈 0px の錨（R111-S0: 行の丈を取らず、下端を揃えて上へはみ出す）
      assert.equal(tracksOf(g.rows)[sh[0] - 1], "0px", `${tag}: 指示欄の行`);
    }
  }
});

test("cplayout: gridTemplate の非既定の配置（指示の例）・split・保存した幅・画面に入らない幅は詰める", () => {
  let g = gridTemplate(L([["terminal", "reception"], ["office"], ["roster"]]), { k: 3 });
  assert.equal(g.cols, "minmax(560px, 1fr) 570px clamp(280px, 18vw, 320px)");
  assert.equal(g.rows, "minmax(200px, 0.5fr) 0px 8px minmax(120px, 0.5fr)", "端末が上＝指示欄はその直下の 0px の錨");
  assert.deepEqual(g.area, { office: "1 / 2 / 5 / 3", reception: "4 / 1 / 5 / 2", terminal: "1 / 1 / 2 / 2", roster: "1 / 3 / 5 / 4",
    sheet: "2 / 1 / 3 / 2", rowsplit: "3 / 1 / 4 / 2" });
  assert.equal(g.areas, '"center office right" "sheet office right" "ogap office right" "reception office right"');
  g = gridTemplate(L([["roster"], ["office", "reception"], ["terminal"]]), { k: 4 });
  assert.equal(g.cols, "clamp(280px, 18vw, 320px) 754px minmax(560px, 1fr)");
  assert.equal(g.rows, "auto 8px minmax(120px, 1fr) 0px");
  assert.deepEqual(g.area, { office: "1 / 2 / 2 / 3", reception: "3 / 2 / 5 / 3", terminal: "1 / 3 / 4 / 4", roster: "1 / 1 / 5 / 2",
    sheet: "4 / 3 / 5 / 4", rowsplit: "2 / 2 / 3 / 3" });
  // 部屋が下・端末が上
  g = gridTemplate(L([["terminal", "office"], ["reception"], ["roster"]]), { k: 2 });
  assert.equal(g.rows, "minmax(200px, 1fr) 0px 8px auto");
  assert.equal(g.cols, "minmax(560px, 1fr) clamp(280px, 18vw, 320px) clamp(280px, 18vw, 320px)", "部屋と端末が同じ列＝flex");
  // 部屋の無い 2 窓の列の split（小数の fr）
  g = gridTemplate(L([["office"], ["roster", "terminal"], ["reception"]], { roster: 300, reception: 260 }, { split: 0.7 }), { k: 3 });
  assert.equal(g.rows, "minmax(120px, 0.7fr) 8px minmax(200px, 0.3fr) 0px");
  assert.equal(g.cols, "570px minmax(560px, 1fr) 260px", "2 窓の列は端末の列＝flex・受付の列は保存した幅");
  g = gridTemplate(L([["office"], ["terminal"], ["roster", "reception"]], { reception: 300 }), { k: 3 });
  assert.equal(g.cols, "570px minmax(560px, 1fr) 300px", "名簿と受付の列は値を持つ方の幅");
  // 端末の最低を下げた・部屋と同じ列は ×2 の部屋も入る幅
  assert.equal(gridTemplate(L(DEFAULT_LAYOUT.cols, { termMin: 390 }), { k: 4 }).cols,
    "754px minmax(390px, 1fr) clamp(280px, 18vw, 320px)");
  assert.equal(gridTemplate(L([["office", "terminal"], ["roster"], ["reception"]], { termMin: 352 }), { k: 2 }).cols.split(" ")[0],
    "minmax(386px,");
  // 保存した名簿 520 は 1401 の画面に入らない（×2 の部屋 386 + 端末 560 + 溝 16）＝詰める・mainW が無ければそのまま
  const wide = L(DEFAULT_LAYOUT.cols, { roster: 520 });
  assert.equal(gridTemplate(wide, { k: 2 }).cols, "386px minmax(560px, 1fr) 520px");
  assert.equal(gridTemplate(wide, { k: 2, mainW: 1401 }).cols, `386px minmax(560px, 1fr) ${1401 - 16 - 386 - 560}px`);
  assert.equal(gridTemplate(wide, { k: 2, mainW: 1920 }).cols, "386px minmax(560px, 1fr) 520px");
  assert.equal(gridTemplate(wide, { k: 4, mainW: 1000 }).cols.split(" ").pop(), "240px", "詰めても 240 は割らない");
  const two = L([["office", "terminal"], ["roster"], ["reception"]], { roster: 520, reception: 500 });
  assert.equal(gridTemplate(two, { k: 2, mainW: 1500 }).cols, `minmax(560px, 1fr) ${520 - (560 + 520 + 500 + 16 - 1500)}px 500px`,
    "広い方から詰める");
  // 入力は書き換えない
  const f = frozenCopy(L([["terminal", "reception"], ["office"], ["roster"]], { roster: 300 }, { split: 0.3 }));
  assert.doesNotThrow(() => gridTemplate(f, { k: 3, mainW: 1440 }));
});
