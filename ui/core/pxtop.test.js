// R99 Phase 3: 俯瞰のコマ表と頭上の印（純関数）。時刻・乱数・DOM に触らないことは層 lint が、
// 「どのコマを・どの印を出すか」はここが固定する。ドットが PX_CELLS に揃っているかは tests/cockpit_cells.test.mjs。
import test from "node:test";
import assert from "node:assert/strict";
import {
  GREET_SEC, SCREEN_FPS, SETTLE_SEC, screenFrame, MARK_CLEAR_SEC, MARK_DONE_SEC, MARK_POP, MARK_STALE_SEC, MARK_WAIT_MAX_AGE, PHASE_STEPS, TOP_CELLS, TOP_DIRS, TOP_KINDS, TOP_MARKS,
  TOP_MARK_SIZE, TOP_MARK_TILES, TOP_READ_VERBS, TOP_VENDORS, isReadVerb, roomMark, topMarkAt, topPose, topVendor,
  THINK_DOTS_FPS, TOP_ARCH_SIZE, TOP_ARCH_TILES, TOP_DESK_SIZE, TOP_DESK_TILES, TYPE_FPS, headTopRow, thinkDots, topPropAt,
  typePace,
} from "./pxtop.js";
import { ARCHETYPE_RULES } from "./archetype.js";
import { ROOM, SLOTS } from "./pxroom.js";

const base = (over = {}) => ({ session: "s1", id: "p1", state: "idle", zone: "desk", kind: "idle", verb: "", ...over });

// ── 種別の優先 ──────────────────────────────────────────────────────
// 各種別の「引き金」。互いに食い合わない（state は read と type がどちらも working を求めるだけ）ので、
// 任意の組み合わせを重ねられる。期待値＝組み合わせの中でいちばん優先の高い種別（無ければ idle）。
const TRIGGERS = {
  walk: { opts: { walk: { phase: 0.3, dir: "E" } } },
  raise: { over: { attention: true } },
  done: { opts: { done: true } },
  rest: { over: { zone: "lounge" } },
  read: { over: { state: "working", verb: "読込中" } },
  think: { over: { kind: "think" } },
  type: { over: { state: "working" } },
};
const NAMES = Object.keys(TRIGGERS);

function build(subset, extra = {}) {
  let over = {};
  let opts = {};
  for (const n of subset) {
    over = { ...over, ...(TRIGGERS[n].over || {}) };
    opts = { ...opts, ...(TRIGGERS[n].opts || {}) };
  }
  return { actor: base({ ...over, ...extra }), opts };
}

function* subsets(names) {
  for (let m = 0; m < 1 << names.length; m += 1) yield names.filter((_, i) => m & (1 << i));
}

test("topPose: 種別の優先（7 つの引き金の全 128 通り）＝ walk > raise > done > rest > read > think > type > idle", () => {
  // greet（挨拶）は入力起点の opts.greet でだけ起きる＝7 つの引き金の組み合わせには現れない（下の挨拶のテストが見る）。
  // rise（立ち上がり）と settle（到着の一拍）は歩行の帳簿の opts.lift・opts.settled でだけ起きる（R110-S4 のテストが見る）
  assert.deepEqual(TOP_KINDS, ["walk", "rise", "greet", "settle", "raise", "done", "rest", "read", "think", "type", "idle"]);
  let n = 0;
  for (const s of subsets(NAMES)) {
    const { actor, opts } = build(s);
    const want = TOP_KINDS.find((k) => s.includes(k)) || "idle";
    assert.equal(topPose(actor, 0, opts).kind, want, `引き金 [${s.join(",")}] → ${want}`);
    n += 1;
  }
  assert.equal(n, 128);
});

test("topPose: standby は（歩いていなければ）idle＝挙手も打鍵も読書も休憩もしない", () => {
  for (const s of subsets(NAMES.filter((k) => k !== "rest"))) {
    const { actor, opts } = build(s, { zone: "standby" });
    const want = s.includes("walk") ? "walk" : "idle";
    assert.equal(topPose(actor, 0, opts).kind, want, `standby + [${s.join(",")}]`);
  }
  assert.equal(topPose(base({ zone: "standby", state: "resting" }), 0).kind, "idle");
  assert.equal(topPose(base({ zone: "standby", attention: true }), 0).cell, "idle0");
});

test("topPose: rest は state resting でも zone lounge でも（読む動詞でも resting なら rest）", () => {
  assert.equal(topPose(base({ state: "resting" }), 0).kind, "rest");
  assert.equal(topPose(base({ zone: "lounge", state: "working" }), 0).kind, "rest");
  assert.equal(topPose(base({ state: "resting", verb: "読込中" }), 0).kind, "rest");
});

test("topPose: read の語彙（日英）＝ working のときだけ", () => {
  for (const v of [...TOP_READ_VERBS.ja, ...TOP_READ_VERBS.en]) {
    assert.equal(topPose(base({ state: "working", verb: v }), 0).kind, "read", v);
    assert.equal(topPose(base({ state: "working", verb: ` ${v} ` }), 0).kind, "read", `前後の空白: ${v}`);
    assert.equal(topPose(base({ state: "waiting", verb: v }), 0).kind, "idle", `待機中の ${v} は読んでいない`);
    assert.ok(isReadVerb(v));
  }
  assert.deepEqual([...TOP_READ_VERBS.ja], ["読込中", "調査中", "リサーチ中"]);
  assert.deepEqual([...TOP_READ_VERBS.en], ["reading", "researching"]);
  // 読まない動詞（書く・走らせる・考える・報告）は read にしない。字面の完全一致だけ（読む語を含む長い文字列も読書ではない）
  for (const v of ["実行中", "編集中", "執筆中", "running", "editing", "writing", "考え中…", "報告中", "Reading", "",
    "読込中 x", "x 読込中", "proofreading", "not reading", "researching now", "リサーチ中…"]) {
    assert.equal(topPose(base({ state: "working", verb: v }), 0).kind, "type", `「${v}」は読書ではない`);
    assert.equal(isReadVerb(v), false);
  }
  assert.equal(isReadVerb(undefined), false);
  assert.equal(isReadVerb(null), false);
});

// ── コマ送り ────────────────────────────────────────────────────────
test("topPose: コマ送りは t だけで決まる（idle 1fps・type 2（pace 省略＝normal）・read 1・raise 3・rest 0.5・done/think は 1 コマ）", () => {
  const at = (over, t, opts) => topPose(base(over), t, opts).cell;
  // idle 1fps
  assert.equal(at({}, 0), "idle0");
  assert.equal(at({}, 0.99), "idle0");
  assert.equal(at({}, 1), "idle1");
  assert.equal(at({}, 2), "idle0");
  // type 2fps
  assert.equal(at({ state: "working" }, 0.49), "type0");
  assert.equal(at({ state: "working" }, 0.5), "type1");
  assert.equal(at({ state: "working" }, 1), "type0");
  // read 1fps
  assert.equal(at({ state: "working", verb: "reading" }, 0.99), "read0");
  assert.equal(at({ state: "working", verb: "reading" }, 1), "read1");
  // raise 3fps
  assert.equal(at({ attention: true }, 0.33), "raise0");
  assert.equal(at({ attention: true }, 0.34), "raise1");
  assert.equal(at({ attention: true }, 0.67), "raise0");
  // rest 0.5fps
  assert.equal(at({ state: "resting" }, 1.99), "rest0");
  assert.equal(at({ state: "resting" }, 2), "rest1");
  assert.equal(at({ state: "resting" }, 4), "rest0");
  // done は止め絵。think は 0.5fps の 2 コマ（R99-P4-L3: 1 コマの止め絵で、考え中の人が部屋でいちばん動かなかった）
  for (const t of [0, 0.5, 1, 2.5, 7.75, 1e6]) assert.equal(at({}, t, { done: true }), "done0");
  assert.equal(at({ kind: "think" }, 0), "think0");
  assert.equal(at({ kind: "think" }, 1.99), "think0");
  assert.equal(at({ kind: "think" }, 2), "think1");
  assert.equal(at({ kind: "think" }, 4), "think0");
});

test("topPose: 同じ入力なら同じ結果（決定論）・壊れた t でも表の中", () => {
  const cases = [base(), base({ state: "working" }), base({ attention: true }), base({ state: "resting" }),
    base({ state: "working", verb: "調査中" }), base({ kind: "think" })];
  for (const a of cases) {
    for (const t of [0, 0.4, 1, 2.5, 7.75, 123.5, -3.2, NaN, Infinity, -Infinity, 1e308]) {
      const p = topPose(a, t);
      assert.ok(TOP_CELLS.includes(p.cell), `${p.cell} が表に無い（t=${t}）`);
      assert.deepEqual(p, topPose({ ...a }, t));
      assert.equal(p.dir, "S");
      assert.equal(p.mirror, false);
    }
  }
  // 非数の t は 0 と同じ
  assert.deepEqual(topPose(base({ state: "working" }), NaN), topPose(base({ state: "working" }), 0));
});

test("topPose: 歩行は phase×4（距離で駆動）・W は walkE の鏡像・止まっている人は S", () => {
  const walk = (phase, dir) => topPose(base({ state: "working" }), 99, { walk: { phase, dir } });
  for (const d of ["S", "N", "E"]) {
    assert.equal(walk(0, d).cell, `walk${d}0`);
    assert.equal(walk(0.25, d).cell, `walk${d}1`);
    assert.equal(walk(0.5, d).cell, `walk${d}2`);
    assert.equal(walk(0.999, d).cell, `walk${d}3`);
    assert.equal(walk(1, d).cell, `walk${d}0`, "1 は次の周の頭");
    assert.equal(walk(0.3, d).mirror, false);
    assert.equal(walk(0.3, d).dir, d);
  }
  for (const [ph, i] of [[0, 0], [0.26, 1], [0.6, 2], [0.8, 3]]) {
    const p = walk(ph, "W");
    assert.equal(p.cell, `walkE${i}`, "W は描かない＝E を使う");
    assert.equal(p.mirror, true);
    assert.equal(p.dir, "W");
    assert.equal(p.kind, "walk");
  }
  // 位相は t に依らない（距離で駆動＝同じ位置なら同じコマ）
  assert.deepEqual(topPose(base(), 0, { walk: { phase: 0.4, dir: "N" } }), topPose(base(), 55.5, { walk: { phase: 0.4, dir: "N" } }));
  // 壊れた向き・位相は S の頭へ（表の外へ出ない）
  assert.equal(walk(0.3, "X").dir, "S");
  assert.equal(walk(0.3, undefined).cell, "walkS1");
  assert.equal(walk(NaN, "E").cell, "walkE0");
  assert.equal(walk(Infinity, "E").cell, "walkE0");
  assert.equal(walk(-0.1, "E").cell, "walkE3", "負の位相も周回");
  // walk: null は止まっている
  assert.equal(topPose(base(), 0, { walk: null }).kind, "idle");
  assert.equal(topPose(base(), 0, { walk: null }).dir, "S");
});

test("topPose: 返しうるコマの全集合＝ TOP_CELLS ちょうど（描いたのに出ないコマも、表に無いコマも無い）", () => {
  const seen = new Set();
  const states = ["idle", "working", "waiting", "resting", undefined, 42];
  const zones = ["desk", "lounge", "meeting", "queue", "standby", "external", undefined];
  const kinds = ["idle", "tool", "think", "said", "order", undefined];
  const verbs = ["", "読込中", "調査中", "リサーチ中", "reading", "researching", "実行中", undefined];
  const times = Array.from({ length: 33 }, (_, i) => i * 0.125).concat([-1.5, NaN, 1e9]);
  for (const state of states) for (const zone of zones) for (const kind of kinds) for (const verb of verbs) {
    for (const attention of [false, true]) for (const done of [false, true]) for (const t of times) {
      const p = topPose({ state, zone, kind, verb, attention }, t, { done });
      assert.ok(TOP_CELLS.includes(p.cell), `${p.cell}`);
      seen.add(p.cell);
    }
  }
  for (const dir of [...TOP_DIRS, "?"]) for (let i = 0; i < 16; i += 1) {
    seen.add(topPose(base(), 0, { walk: { phase: i / 16, dir } }).cell);
  }
  assert.deepEqual([...seen].sort(), [...TOP_CELLS].sort());
});

test("topPose: 壊れた入力でも表の中のコマを返す", () => {
  for (const [a, t, o] of [[null, 0], [undefined, NaN], [{}, Infinity], [{ state: 42 }, -1], [base(), 0, null],
    [base(), 0, { walk: "E" }], [base(), 0, { done: "yes" }]]) {
    const p = topPose(a, t, o);
    assert.ok(TOP_CELLS.includes(p.cell));
    assert.ok(TOP_DIRS.includes(p.dir));
  }
  // done は true のときだけ（真偽値以外で ✓ のコマにしない）
  assert.equal(topPose(base(), 0, { done: "yes" }).kind, "idle");
});

test("コマ表・ベンダー・印の語彙（委譲の契約）", () => {
  assert.equal(TOP_CELLS.length, 25);
  assert.equal(new Set(TOP_CELLS).size, 25);
  assert.deepEqual([...TOP_CELLS], [
    "idle0", "idle1", "type0", "type1", "read0", "read1", "raise0", "raise1", "think0", "think1", "rest0", "rest1", "done0",
    "walkS0", "walkS1", "walkS2", "walkS3", "walkN0", "walkN1", "walkN2", "walkN3",
    "walkE0", "walkE1", "walkE2", "walkE3"]);
  assert.ok(!TOP_CELLS.some((c) => c.startsWith("walkW")), "W は描かない（E の鏡像）");
  assert.deepEqual([...TOP_VENDORS], ["claude", "codex", "openclaw", "bot"]);
  assert.deepEqual([...TOP_MARKS], ["call", "warn", "done", "wait", "think"]);
  assert.deepEqual(Object.keys(TOP_MARK_TILES).sort(), [...TOP_MARKS].sort());
  // 印 → タイルの対応そのもの（委譲の契約の字面・TOP_MARK_SIZE と同じく固定する）。集合で見るだけだと、
  // warn と done、wait と think を取り違えても（✓ が ⚠ の絵で出ても）どの門も緑のまま
  assert.deepEqual({ ...TOP_MARK_TILES }, {
    call: "markCall", warn: "markWarn", done: "markDone", wait: "markWait", think: "markThink",
  });
  // 統括の既定（本人が覆せる）の数字（✓ は 8 秒・💬 は報告から 600 秒未満）。他のテストは定数で書くので、
  // ここで字面を固定する（変えるなら本人の裁定と一緒に＝ここも直す）
  assert.equal(MARK_DONE_SEC, 8);
  assert.equal(MARK_WAIT_MAX_AGE, 600);
  assert.ok(MARK_STALE_SEC > 0 && MARK_STALE_SEC < MARK_DONE_SEC, "古さの門は ✓ の窓より短い");
  assert.ok(Object.isFrozen(TOP_CELLS) && Object.isFrozen(TOP_VENDORS) && Object.isFrozen(TOP_MARK_TILES));
});

test("topVendor: 4 ベンダーはそのまま・未知は claude", () => {
  for (const v of TOP_VENDORS) assert.equal(topVendor(v), v);
  assert.equal(topVendor("Codex"), "codex");
  assert.equal(topVendor(" openclaw "), "openclaw");
  for (const v of ["other", "", null, undefined, 42, "gemini", {}]) assert.equal(topVendor(v), "claude", String(v));
});

// ── 頭上の印 ────────────────────────────────────────────────────────
const LIVE = { live: true };
/** 前回 working を見た記録つきの memo（t0 の 1 秒前） */
const workedAt = (t0, session = "s1") => roomMark(base({ session, state: "working", kind: "tool" }), t0 - 1, undefined, LIVE).memo;

test("roomMark: 印の優先（引き金の全組み合わせ）＝ call > warn > done > wait > think", () => {
  // think は kind think、done / wait は kind said を求める＝同時には起きない（組み合わせから外す）
  const names = ["call", "warn", "done", "wait", "think"];
  let n = 0;
  for (const s of subsets(names)) {
    if (s.includes("think") && (s.includes("done") || s.includes("wait"))) continue;
    let over = {};
    if (s.includes("call")) over.attention = true;
    if (s.includes("warn")) over.stuckTool = "実行中 npm test";
    if (s.includes("done") || s.includes("wait")) over = { ...over, state: "waiting", kind: "said" };
    if (s.includes("think")) over.kind = "think";
    // wait を引かないときは age を窓の外へ（done だけを見たい）
    over.age = s.includes("wait") ? 30 : MARK_WAIT_MAX_AGE + 1;
    const memo = s.includes("done") ? workedAt(100) : undefined;
    const want = TOP_MARKS.find((m) => s.includes(m)) || null;
    assert.equal(roomMark(base(over), 100, memo, LIVE).mark, want, `引き金 [${s.join(",")}] → ${want}`);
    n += 1;
  }
  assert.equal(n, 20);   // 32 通り − think と said の同居 12 通り
});

test("roomMark: standby の attention は call にしない（次の印へ落ちる）", () => {
  assert.equal(roomMark(base({ zone: "standby", attention: true }), 0, undefined).mark, null);
  assert.equal(roomMark(base({ zone: "standby", attention: true, stuckTool: "実行中 x" }), 0, undefined).mark, "warn");
  assert.equal(roomMark(base({ zone: "queue", attention: true }), 0, undefined).mark, "call");
  assert.equal(roomMark(base({ zone: "external", attention: true }), 0, undefined).mark, "call");
  // stuckTool は空白だけなら無いのと同じ
  assert.equal(roomMark(base({ stuckTool: "  " }), 0, undefined).mark, null);
});

test("roomMark: ✓ は working → 報告して待機 の遷移から 8 秒だけ", () => {
  const t0 = 50;
  const said = base({ state: "waiting", kind: "said", age: MARK_WAIT_MAX_AGE + 5 });
  let memo = workedAt(t0);
  // 遷移の瞬間
  let r = roomMark(said, t0, memo, LIVE);
  assert.equal(r.mark, "done");
  memo = r.memo;
  // 窓の中（毎フレーム呼ばれる）
  for (let t = t0 + 0.5; t < t0 + MARK_DONE_SEC; t += 0.5) {
    r = roomMark(said, t, memo, LIVE);
    assert.equal(r.mark, "done", `t=${t}`);
    memo = r.memo;
  }
  // 8 秒で消える（age が古いので wait も出ない）
  r = roomMark(said, t0 + MARK_DONE_SEC, memo, LIVE);
  assert.equal(r.mark, null);
  memo = r.memo;
  // 一度消えたら戻らない
  r = roomMark(said, t0 + MARK_DONE_SEC + 0.5, memo, LIVE);
  assert.equal(r.mark, null);
  // ✓ の後は（新しい報告なら）💬 に替わる
  const recent = base({ state: "waiting", kind: "said", age: 30 });
  let m2 = workedAt(t0);
  const steps = [[t0, "done"], [t0 + 4, "done"], [t0 + MARK_DONE_SEC, "wait"], [t0 + MARK_DONE_SEC + 1, "wait"]];
  for (const [t, want] of steps) {
    const s = roomMark(recent, t, m2, LIVE);
    assert.equal(s.mark, want, `t=${t}`);
    m2 = s.memo;
  }
});

test("roomMark: ✓ の窓の途中で働き出したら消える・working 以外からの遷移では出ない", () => {
  const t0 = 10;
  const said = base({ state: "waiting", kind: "said", age: 700 });
  let memo = roomMark(said, t0, workedAt(t0), LIVE).memo;
  const back = roomMark(base({ state: "working", kind: "order" }), t0 + 1, memo, LIVE);
  assert.equal(back.mark, null, "働き出したら ✓ は消える");
  assert.equal(back.memo.s1.doneAt, null);
  memo = back.memo;
  // また報告して待機＝新しい遷移（起点は t0+2）
  let r = roomMark(said, t0 + 2, memo, LIVE);
  assert.equal(r.mark, "done");
  assert.equal(r.memo.s1.doneAt, t0 + 2);
  // waiting（tool）→ 報告して待機 は ✓ にしない
  let m = roomMark(base({ state: "waiting", kind: "tool" }), 0, undefined, LIVE).memo;
  assert.equal(roomMark(said, 1, m, LIVE).mark, null);
  // resting → 報告して待機 も ✓ にしない
  m = roomMark(base({ state: "resting" }), 0, undefined, LIVE).memo;
  assert.equal(roomMark(said, 1, m, LIVE).mark, null);
});

test("roomMark: 初めて見たセッションは記録だけ（開いた瞬間に全員が ✓ を出さない）", () => {
  const said = base({ state: "waiting", kind: "said", age: 700 });
  const r = roomMark(said, 5, undefined, LIVE);
  assert.equal(r.mark, null);
  assert.equal(r.memo.s1.state, "waiting");
  assert.equal(roomMark(said, 5, {}, LIVE).mark, null);
  // 別のセッションの記録は、自分の遷移に使わない
  const other = workedAt(5, "s2");
  assert.equal(roomMark(said, 5, other, LIVE).mark, null);
  // key も session も無ければ追跡しない（✓ は出ない・memo はそのまま）。**id では追わない**
  // （内訳の行は全員が同じ id＝.claude/rules/ui-2d.md。id で追うと同じ id の 2 人が互いの記録を上書きする）
  const noKey = { state: "waiting", kind: "said", age: 700 };
  const m0 = roomMark({ state: "working" }, 4, undefined, LIVE).memo;
  assert.equal(m0, undefined);
  assert.equal(roomMark(noKey, 5, m0, LIVE).mark, null);
  const byId = roomMark({ id: "p9", state: "working" }, 4, undefined, LIVE);
  assert.equal(byId.memo, undefined, "id だけの人を記録した");
  assert.equal(roomMark({ id: "p9", ...noKey }, 5, byId.memo, LIVE).mark, null, "id で追跡した");
  // key（roomLayout が振る・session＋#n）が session より優先
  const byKey = roomMark({ key: "s1#1", session: "s1", state: "working" }, 4, undefined, LIVE).memo;
  assert.deepEqual(Object.keys(byKey), ["s1#1"]);
  assert.equal(roomMark({ key: "s1#1", session: "s1", ...noKey }, 5, byKey, LIVE).mark, "done");
  assert.equal(roomMark({ key: "s1", session: "s1", ...noKey }, 5, byKey, LIVE).mark, null, "別の key の記録で祝った");
});

test("roomMark: 同じ id・同じ session の 2 人（key が #n で別）は別々に追う（片方の ✓ が毎フレーム出直さない）", () => {
  // 内訳の行は全員が同じ id を持つ。session が欠けて id で追うと、working の人と報告した人が同じ記録を
  // 毎フレーム書き換え、報告した人の ✓ が窓を無視して永久に出る（検収のプローブで 60 フレーム中 60 回）
  const a = (key, over) => ({ key, id: "p1", session: "s", zone: "desk", age: 700, ...over });
  const worker = a("s", { state: "working", kind: "tool" });
  const done = a("s#1", { state: "waiting", kind: "said" });
  let memo = roomMark(a("s#1", { state: "working", kind: "tool" }), 0, undefined, LIVE).memo;
  memo = roomMark(worker, 0, memo, LIVE).memo;
  let shown = 0;
  for (let k = 1; k <= 80; k += 1) {
    const t = k / 8;   // 2 進で割り切れる刻み（1/6 だと 8 秒の境目が浮動小数の誤差で 1 回ずれる）
    const r1 = roomMark(worker, t, memo, LIVE);
    const r2 = roomMark(done, t, r1.memo, LIVE);
    memo = r2.memo;
    assert.equal(r1.mark, null, "働いている人に印が出た");
    if (r2.mark === "done") shown += 1;
  }
  // 1/8 秒ごと 80 回＝10 秒: ✓ は遷移（t=1/8）から 8 秒未満の 64 回だけ
  assert.equal(shown, 64, `✓ が ${shown} 回（窓 8 秒＝64 回のはず）`);
});

test("roomMark: live=false（再生・固定時刻・停止中）は memo を更新せず ✓ を出さない", () => {
  const t0 = 30;
  const said = base({ state: "waiting", kind: "said", age: 700 });
  const memo = workedAt(t0);
  const snapshot = JSON.stringify(memo);
  for (const opts of [{ live: false }, {}, undefined, { live: "yes" }]) {
    const r = roomMark(said, t0, memo, opts);
    assert.equal(r.mark, null, JSON.stringify(opts));
    assert.equal(r.memo, memo, "同一の参照を返す");
  }
  assert.equal(JSON.stringify(memo), snapshot, "入力の memo を書き換えない");
  // live でも t が数でなければ記録しない
  assert.equal(roomMark(said, NaN, memo, LIVE).memo, memo);
  // live=false でも ✓ 以外の印は出る
  assert.equal(roomMark(base({ attention: true }), 0, memo, { live: false }).mark, "call");
  assert.equal(roomMark(base({ state: "waiting", kind: "said", age: 30 }), 0, memo, { live: false }).mark, "wait");
  assert.equal(roomMark(base({ kind: "think" }), 0, memo, { live: false }).mark, "think");
});

test("roomMark: 停止・非表示・巻き戻しから戻っても遅れて祝わない", () => {
  const said = base({ state: "waiting", kind: "said", age: 700 });
  // working を見た後、停止中（live=false）に遷移し、MARK_STALE_SEC を超えて再開
  const memo = workedAt(100);
  assert.equal(roomMark(said, 100 + MARK_STALE_SEC + 0.5, memo, LIVE).mark, null);
  // 間隔が MARK_STALE_SEC 以内なら（普通の描画の間隔）遷移として祝う
  assert.equal(roomMark(said, 99 + MARK_STALE_SEC, memo, LIVE).mark, "done");
  // 巻き戻し（t が前回より前）は初見と同じ
  assert.equal(roomMark(said, 50, memo, LIVE).mark, null);
});

test("roomMark: ✓ の窓の途中で巻き戻したら初見と同じ（窓を持ち越さない＝次に進んだ刻みでも ✓ を出さない）", () => {
  // 巻き戻した刻みで ✓ が出ないことは「時計が進んだか（t > 前回）」だけでも決まる。未来の記録を初見と同じに扱う規則
  // （fresh の seen <= t）が効くのは、その次の刻み: 規則が無いと、巻き戻した刻みで窓（doneAt）を持ち越し、
  // 次に進んだ刻みで ✓ を出し直す（止めていた ✓ が巻き戻しの後に戻ってくる）
  const said = base({ state: "waiting", kind: "said", age: 700 });
  let r = roomMark(said, 100, workedAt(100), LIVE);
  assert.equal(r.mark, "done", "前提: 遷移で ✓");
  r = roomMark(said, 101, r.memo, LIVE);
  assert.equal(r.mark, "done", "前提: 窓の中");
  // 巻き戻す（窓の起点 100 より後の 100.5）
  r = roomMark(said, 100.5, r.memo, LIVE);
  assert.equal(r.mark, null, "巻き戻した刻みで ✓");
  assert.deepEqual({ ...r.memo.s1 }, { state: "waiting", seen: 100.5, doneAt: null, attention: false, clearAt: null, mark: null, since: null },
    "巻き戻しを初見として記録していない");
  // 次に進んだ刻み（まだ元の窓の中）でも出さない
  r = roomMark(said, 100.7, r.memo, LIVE);
  assert.equal(r.mark, null, "巻き戻しの後に ✓ が戻ってきた（未来の記録の窓を持ち越した）");
  assert.equal(roomMark(said, 102, r.memo, LIVE).mark, null);
});

test("roomMark: live の呼び出しは古い記録を落とし、入力を書き換えない", () => {
  let memo = roomMark(base({ session: "a", state: "working" }), 0, undefined, LIVE).memo;
  memo = roomMark(base({ session: "b", state: "working" }), 0, memo, LIVE).memo;
  const before = JSON.stringify(memo);
  const r = roomMark(base({ session: "b", state: "working" }), MARK_STALE_SEC + 1, memo, LIVE);
  assert.deepEqual(Object.keys(r.memo), ["b"], "部屋から消えた a の記録は落ちる");
  assert.notEqual(r.memo, memo);
  assert.equal(JSON.stringify(memo), before);
  assert.ok(Object.isFrozen(r.memo.b));
  // 1 フレームに 1 人 1 回の掟: 同じ t の 2 回目は ✓ を出さない（t 固定で居座らないための代わり）。
  // 記録（起点）は持ち越す＝次のフレーム（t が進んだ）で窓の続きが出る
  const said = base({ session: "b", state: "waiting", kind: "said", age: 700 });
  const once = roomMark(said, MARK_STALE_SEC + 2, r.memo, LIVE);
  const twice = roomMark(said, MARK_STALE_SEC + 2, once.memo, LIVE);
  assert.equal(once.mark, "done");
  assert.equal(twice.mark, null);
  assert.deepEqual({ ...twice.memo.b }, { ...once.memo.b }, "同じ t の 2 回目が記録を変えた");
  assert.equal(roomMark(said, MARK_STALE_SEC + 2.2, twice.memo, LIVE).mark, "done", "次のフレームで ✓ が戻らない");
  // __proto__ のような名前のセッションでも memo の形が壊れない（{} だと代入がプロトタイプの差し替えに化け、
  // own の記録にならない＝そのセッションは毎フレーム「初めて見た」になって ✓ が出ない）
  const odd = roomMark(base({ session: "__proto__", state: "working" }), 0, undefined, LIVE).memo;
  assert.equal(Object.getPrototypeOf(odd), null, "memo が null プロトタイプでない");
  assert.ok(Object.hasOwn(odd, "__proto__"), "__proto__ の記録が own でない");
  assert.equal(Object.getOwnPropertyDescriptor(odd, "__proto__").value.state, "working");
  const oddSaid = roomMark(base({ session: "__proto__", state: "waiting", kind: "said", age: 700 }), 0.2, odd, LIVE);
  assert.equal(oddSaid.mark, "done", "__proto__ という名のセッションで ✓ が出ない");
});

test("roomMark: 💬 は報告から 600 秒未満だけ・age が数でなければ出さない", () => {
  const w = (age) => roomMark(base({ state: "waiting", kind: "said", age }), 0, undefined).mark;
  assert.equal(w(0), "wait");
  assert.equal(w(MARK_WAIT_MAX_AGE - 1), "wait");
  assert.equal(w(MARK_WAIT_MAX_AGE - 0.001), "wait");
  assert.equal(w(MARK_WAIT_MAX_AGE), null);
  assert.equal(w(1799), null);
  for (const bad of [undefined, null, "30", NaN, -1]) assert.equal(w(bad), null, String(bad));
  // 報告して待機のときだけ（working の said・waiting の tool は 💬 にしない＝後者は ⚠）
  assert.equal(roomMark(base({ state: "working", kind: "said", age: 10 }), 0, undefined).mark, null);
  assert.equal(roomMark(base({ state: "waiting", kind: "tool", age: 300 }), 0, undefined).mark, "warn");
});

test("roomMark: 💭 は kind think（休憩中＝ソファの人には出さない）", () => {
  assert.equal(roomMark(base({ kind: "think", state: "working" }), 0, undefined).mark, "think");
  assert.equal(roomMark(base({ kind: "think", state: "waiting" }), 0, undefined).mark, "think");
  assert.equal(roomMark(base({ kind: "think", state: "resting" }), 0, undefined).mark, null);
  assert.equal(roomMark(base({ kind: "tool", state: "working" }), 0, undefined).mark, null);
});

test("roomMark: 壊れた入力でも印の語彙の中", () => {
  for (const [a, t, m, o] of [[null, 0], [undefined, NaN, null], [{}, Infinity, 42, LIVE], [{ state: 42 }, -1, "x", LIVE]]) {
    const r = roomMark(a, t, m, o);
    assert.ok(r.mark === null || TOP_MARKS.includes(r.mark));
  }
});

test("roomMark と topPose の配線例: ✓ の窓の中は done0、窓の外は元のコマ", () => {
  const said = base({ state: "waiting", kind: "said", age: 700 });
  const r = roomMark(said, 20, workedAt(20), LIVE);
  assert.equal(topPose(said, 20, { done: r.mark === "done" }).cell, "done0");
  const later = roomMark(said, 20 + MARK_DONE_SEC, r.memo, LIVE);
  assert.equal(topPose(said, 20 + MARK_DONE_SEC, { done: later.mark === "done" }).kind, "idle");
});

test("roomMark: t が進まない live の呼び出しでは ✓ が居座らない（reduced-motion の t 固定を live=true で配線しても）", () => {
  const said = base({ state: "waiting", kind: "said", age: 700 });
  // (1) 時計が止まったまま working → 報告に変わった: 祝わない（停止中の遷移と同じ）
  let memo = roomMark(base({ state: "working", kind: "tool" }), 100, undefined, LIVE).memo;
  for (let k = 0; k < 500; k += 1) {
    const r = roomMark(said, 100, memo, LIVE);
    assert.equal(r.mark, null, `t 固定の ${k} 回目に ✓`);
    memo = r.memo;
  }
  // その後に時計が動き出しても、止まっている間の遷移は祝わない（起点を作っていない）
  assert.equal(roomMark(said, 100.2, memo, LIVE).mark, null);
  // (2) 遷移を祝った直後に時計が止まった: ✓ はその 1 回だけ（同じ t の 2 回目からは出さない・窓も延びない）
  memo = workedAt(200);
  const first = roomMark(said, 200, memo, LIVE);
  assert.equal(first.mark, "done");
  memo = first.memo;
  let lingering = 0;
  for (let k = 0; k < 500; k += 1) {
    const r = roomMark(said, 200, memo, LIVE);
    if (r.mark === "done") lingering += 1;
    memo = r.memo;
  }
  assert.equal(lingering, 0, `t 固定のまま ✓ が ${lingering} 回居座った`);
  assert.equal(topPose(said, 200, { done: roomMark(said, 200, memo, LIVE).mark === "done" }).cell, "idle0",
    "万歳（done0）が居座る");
  // 時計が動き出したら窓の残りだけ出る（起点は持ち越している）・起点から 8 秒で消える
  assert.equal(roomMark(said, 201, memo, LIVE).mark, "done");
  const later = roomMark(said, 201, memo, LIVE).memo;
  assert.equal(roomMark(said, 200 + MARK_DONE_SEC, later, LIVE).mark, null);
  // (3) live=false（配線の掟どおり）なら t 固定の間は 1 度も ✓ を出さない
  assert.equal(roomMark(said, 300, workedAt(300), { live: false }).mark, null);
});

test("roomMark: 退場中（leaving）の人は印を出さない・memo に触らない（歩くだけ）", () => {
  const memo = workedAt(10);
  for (const over of [{ attention: true, zone: "queue" }, { stuckTool: "実行中 x" }, { state: "waiting", kind: "tool" },
    { state: "waiting", kind: "said", age: 30 }, { kind: "think" }]) {
    const r = roomMark(base({ ...over, leaving: true }), 10, memo, LIVE);
    assert.equal(r.mark, null, JSON.stringify(over));
    assert.equal(r.memo, memo, "退場中の人の古い写しで記録を書き換えた");
    assert.notEqual(roomMark(base(over), 10, memo, LIVE).mark, null, `前提: ${JSON.stringify(over)} は印が出る`);
  }
  // leaving は true のときだけ（真偽値以外で印を消さない）
  assert.equal(roomMark(base({ attention: true, zone: "queue", leaving: "yes" }), 10, memo).mark, "call");
  // 退場中の人は歩く（walk が挙手に勝つ）
  assert.equal(topPose(base({ attention: true, zone: "queue", leaving: true }), 10, { walk: { phase: 0.5, dir: "S" } }).kind, "walk");
});

test("roomMark: ⚠ はツールで止まっている人（stuckTool か、ツールのまま待機に落ちた人）", () => {
  // サーバーは stuckTool を承認まち（＝❗）のときだけ出す＝stuckTool だけでは ⚠ は本番で 1 度も出ない
  assert.equal(roomMark(base({ state: "waiting", kind: "tool", age: 960 }), 0).mark, "warn");
  assert.equal(roomMark(base({ state: "waiting", kind: "tool", stuckTool: "実行中 x", attention: true, zone: "queue" }), 0).mark,
    "call", "承認まちは ❗ が勝つ");
  assert.equal(roomMark(base({ state: "working", kind: "tool", age: 100 }), 0).mark, null, "動いているツールは止まっていない");
  assert.equal(roomMark(base({ state: "resting", kind: "tool", age: 4000 }), 0).mark, null, "休憩は止まっていない");
  assert.equal(roomMark(base({ state: "waiting", kind: "said", age: 300 }), 0).mark, "wait", "報告して待機は 💬");
  assert.equal(roomMark(base({ state: "waiting", kind: "order", age: 300 }), 0).mark, null);
});

test("topMarkAt: 印のタイルを頭上（セルの横の中央・頭の 1px 上）に置く・寸法は TOP_MARK_SIZE", () => {
  assert.equal(ROOM.robot.w, 12, "pxtop のセル幅（12）が pxroom の ROOM.robot.w と違う");
  for (const m of TOP_MARKS) {
    const tile = TOP_MARK_TILES[m];
    const [w, h] = TOP_MARK_SIZE[tile];
    const at = topMarkAt([40, 60], m);
    assert.deepEqual(at, { tile, x: 40 + Math.floor((12 - w) / 2), y: 60 - h - 1, w, h }, m);
    assert.deepEqual(topMarkAt({ x: 40, y: 60 }, m), at);
    // 下端は頭の 1px 上・横はセルの中
    assert.equal(at.y + at.h, 59);
    assert.ok(at.x >= 40 && at.x + at.w <= 52);
  }
  // 端数は整数に丸める（整数倍の掟）
  assert.deepEqual(topMarkAt([40.4, 59.6], "call"), topMarkAt([40, 60], "call"));
  // どの枠に立つ人の印も部屋の中（上端より上へ出さない）
  for (const [zone, list] of Object.entries(SLOTS)) {
    for (const s of list) {
      for (const m of TOP_MARKS) {
        const at = topMarkAt(s, m);
        assert.ok(at.x >= 0 && at.y >= 0 && at.x + at.w <= ROOM.w && at.y + at.h <= ROOM.h, `${zone} (${s.x},${s.y}) の ${m}`);
      }
    }
  }
  assert.equal(topMarkAt([5, 3], "think").y, 0, "上端で止まらない");
  for (const [xy, m] of [[[1, 2], null], [[1, 2], "sleep"], [[1, 2], "__proto__"], [[NaN, 2], "call"], [null, "call"]]) {
    assert.equal(topMarkAt(xy, m), null, `${JSON.stringify(xy)} ${m}`);
  }
});

test("TOP_MARK_SIZE: 印のタイルの寸法の正本（キー＝TOP_MARK_TILES の値ちょうど・正の整数・凍結）", () => {
  assert.deepEqual(Object.keys(TOP_MARK_SIZE).sort(), Object.values(TOP_MARK_TILES).sort());
  for (const [name, wh] of Object.entries(TOP_MARK_SIZE)) {
    assert.ok(Array.isArray(wh) && wh.length === 2 && wh.every((v) => Number.isInteger(v) && v > 0), `${name}: ${wh}`);
    assert.ok(Object.isFrozen(wh), `${name} が凍結されていない`);
    // 印は人（12×16）より小さい（委譲の契約「人より小さく」）
    assert.ok(wh[0] < ROOM.robot.w && wh[1] < ROOM.robot.h, `${name} ${wh} が人より大きい`);
  }
  assert.ok(Object.isFrozen(TOP_MARK_SIZE));
  // 委譲の契約の字面（design/gen_tiles.py へ渡した表）。変えるなら gen_tiles.py と同時に
  assert.deepEqual(JSON.parse(JSON.stringify(TOP_MARK_SIZE)),
    { markCall: [3, 7], markWarn: [7, 7], markDone: [7, 6], markWait: [9, 7], markThink: [9, 8] });
});


// ── R99-P4-L1: 個体ごとの位相と挨拶（本人「しっかりアバターが動くように」2026-09-28）─────────────
test("topPose: 位相は開始時刻をずらすだけ（省略・0・壊れた値は今と同じ・同じ t で違う位相なら違うコマになりうる）", () => {
  const w = base({ state: "working" });
  for (const t of [0, 0.25, 0.5, 1.75, 3.2]) {
    const plain = topPose(w, t).cell;
    for (const ph of [undefined, 0, -1, NaN, Infinity, "0.5", null]) {
      assert.equal(topPose(w, t, { phase: ph }).cell, plain, `phase=${String(ph)} は 0 と同じ（t=${t}）`);
    }
  }
  // 12 通りの位相で、同じ t に type0 と type1 が両方現れる（全員同時に切り替わらない）
  const seen = new Set();
  for (let k = 0; k < PHASE_STEPS; k += 1) seen.add(topPose(w, 0.1, { phase: k / PHASE_STEPS }).cell);
  assert.deepEqual([...seen].sort(), ["type0", "type1"]);
  // 位相は t に足すだけ＝ t + ph の素の値と同じ（6fps・12fps の格子で逆回し・飛びが起きないのは素の表と同じ理由）
  for (const [over, name] of [[{}, "idle"], [{ state: "working" }, "type"], [{ attention: true }, "raise"], [{ state: "resting" }, "rest"]]) {
    for (let k = 0; k < PHASE_STEPS; k += 1) {
      const ph = k / PHASE_STEPS;
      for (const t of [0, 1 / 6, 0.5, 1, 2.25, 9.5]) {
        assert.equal(topPose(base(over), t, { phase: ph }).cell, topPose(base(over), t + ph).cell, `${name} ph=${ph} t=${t}`);
      }
    }
  }
});

test("topPose: 挨拶は GREET_SEC 秒だけ・万歳と待機を 3fps・0.3〜0.6 秒だけ 1px 跳ねる・歩行が勝つ", () => {
  assert.equal(GREET_SEC, 0.9);
  const a = base({ state: "working" });
  const at = (g) => topPose(a, 5, { greet: g });
  assert.deepEqual(at(0), { cell: "done0", dir: "S", mirror: false, kind: "greet", lift: 0 });
  assert.equal(at(0.34).cell, "idle0");
  assert.equal(at(0.34).lift, 1);
  assert.equal(at(0.67).cell, "done0");
  assert.equal(at(0.67).lift, 0);
  // 窓の外・壊れた値は挨拶しない（いつもの打鍵）
  for (const g of [0.9, 1, -0.1, NaN, Infinity, "0.1", null, undefined]) {
    assert.equal(topPose(a, 5, { greet: g }).kind, "type", `greet=${String(g)}`);
    assert.equal("lift" in topPose(a, 5, { greet: g }), false, "挨拶以外は lift を持たない（戻り値の形は今のまま）");
  }
  // 歩いている人は歩行が勝つ（扉から入ってくる人に万歳させない）
  assert.equal(topPose(a, 5, { greet: 0.1, walk: { phase: 0.3, dir: "E" } }).kind, "walk");
  // ❗ の人でも挨拶の 0.9 秒だけは手を振る（タップの手応え）・その後は挙手に戻る
  assert.equal(topPose(base({ attention: true }), 5, { greet: 0.1 }).kind, "greet");
  assert.equal(topPose(base({ attention: true }), 5, { greet: 1 }).kind, "raise");
  // 挨拶のコマは全部 TOP_CELLS に在る
  for (let g = 0; g < GREET_SEC; g += 0.05) assert.ok(TOP_CELLS.includes(at(g).cell));
});


// ── R99-P4-L2: ❗ が片付いた ✓・印のぴょこっ ──────────────────────────────────────────────
test("roomMark: ❗ が片付いた瞬間から MARK_CLEAR_SEC 秒だけ ✓（live・時計が進んだときだけ・standby へ落ちたのでは出さない）", () => {
  // R110-S4: 3 → 5（歩く速さを 24px/秒に落とした＝受付から机へ戻る歩行が長くなった。帰り道の前半に ✓ を付けて歩く）
  assert.equal(MARK_CLEAR_SEC, 5);
  const call = base({ state: "waiting", attention: true, question: "q" });
  const back = base({ state: "working", kind: "tool" });
  let memo = roomMark(call, 50, undefined, LIVE).memo;
  let r = roomMark(call, 50.5, memo, LIVE);
  assert.equal(r.mark, "call");
  r = roomMark(back, 51, r.memo, LIVE);
  assert.equal(r.mark, "done", "❗ が片付いた瞬間に ✓ が出ない");
  memo = r.memo;
  for (const t of [51.5, 53, 54, 55.9]) {
    r = roomMark(back, t, memo, LIVE);
    assert.equal(r.mark, "done", `t=${t}（窓の中）で ✓ が消えた`);
    memo = r.memo;
  }
  assert.equal(roomMark(back, 56, memo, LIVE).mark, null, "5 秒を過ぎても ✓ が残った");
  // standby へ落ちた（席が無い）＝片付いたのではない
  memo = roomMark(call, 60, undefined, LIVE).memo;
  memo = roomMark(call, 60.5, memo, LIVE).memo;
  assert.equal(roomMark(base({ state: "waiting", zone: "standby" }), 61, memo, LIVE).mark, null);
  // live でない・初めて見た人・同じ t は出さない
  memo = roomMark(call, 70, undefined, LIVE).memo;
  assert.equal(roomMark(back, 70.5, memo).mark, null, "live でないのに ✓");
  assert.equal(roomMark(back, 70.5, undefined, LIVE).mark, null, "初めて見た人に ✓");
  assert.equal(roomMark(back, 70, memo, LIVE).mark, null, "同じ t の呼び出しで ✓");
});

test("roomMark と topMarkAt: 印が出た t（since）から 1/6 秒ずつ 2px・1px 上・以後は定位置（開いた瞬間・live でないときはぴょこっとしない）", () => {
  assert.deepEqual([...MARK_POP], [2, 1]);
  const think = base({ state: "working", kind: "think" });
  const plain = base({ state: "working", kind: "tool" });
  let r = roomMark(think, 10, undefined, LIVE);
  assert.equal(r.mark, "think");
  assert.equal(r.since, null, "開いた瞬間（初めて見た人）にぴょこっとした");
  r = roomMark(plain, 10.5, r.memo, LIVE);
  assert.equal(r.mark, null);
  r = roomMark(think, 11, r.memo, LIVE);
  assert.equal(r.since, 11, "印が出た瞬間の t を持たない");
  const again = roomMark(think, 11, r.memo, LIVE);
  assert.equal(again.since, 11, "同じ t の描き直しで since が変わった");
  r = roomMark(think, 11.5, r.memo, LIVE);
  assert.equal(r.since, 11, "同じ印が続いているのに since が動いた");
  const xy = { x: 40, y: 60 };
  const at = (t) => topMarkAt(xy, "think", { since: 11, t }).y;
  const rest = topMarkAt(xy, "think").y;
  assert.equal(at(11), rest - 2);
  assert.equal(at(11 + 1 / 6), rest - 1);
  assert.equal(at(11 + 2 / 6), rest);
  assert.equal(at(12), rest);
  assert.equal(at(10.9), rest, "since より前の t でぴょこっとした");
  assert.equal(topMarkAt(xy, "think", { since: null, t: 11 }).y, rest);
  // 上端のクランプは保つ（y 0 より上へは出さない）
  assert.equal(topMarkAt({ x: 40, y: 2 }, "think", { since: 11, t: 11 }).y, 0);
});


// ── R99-P4-L3: 机の画面が語る ─────────────────────────────────────────────────
test("screenFrame: 打鍵 2fps・読書 1fps・考え中 1fps の 3 コマ（0 は貼らない）・t 0 と位相 0 は 0（止め絵は今の画面）・ほかの種別は 0", () => {
  assert.deepEqual({ ...SCREEN_FPS }, { type: 2, read: 1, think: 1 });
  assert.equal(screenFrame("type", 0), 0);
  assert.equal(screenFrame("type", 0.5), 1);
  assert.equal(screenFrame("type", 1), 2);
  assert.equal(screenFrame("type", 1.5), 0);
  assert.equal(screenFrame("think", 1), 1);
  assert.equal(screenFrame("read", 2), 2);
  for (const k of ["idle", "rest", "raise", "done", "greet", "walk", "rise", "settle", "", undefined]) {
    for (const t of [0, 0.5, 1, 3.2]) assert.equal(screenFrame(k, t), 0, `${String(k)} で画面を送った`);
  }
  assert.equal(screenFrame("type", 0.2, 0.3), screenFrame("type", 0.5));
  assert.equal(screenFrame("type", NaN), 0);
  assert.equal(screenFrame("type", 0, -1), 0);
});


// ── R110-S4: 歩き出す前の立ち上がり・着いた瞬間の一拍 ──────────────────────────────────
test("topPose（R110-S4）: 立ち上がり（opts.lift）は正面の待機コマで lift を返す・歩行だけが勝つ・挙手も打鍵も挨拶もしない", () => {
  for (const over of [{}, { state: "working" }, { state: "working", verb: "読込中" }, { attention: true }, { state: "resting" },
    { kind: "think" }, { zone: "standby" }, { zone: "lounge" }]) {
    for (const t of [0, 0.4, 7.75]) {
      const p = topPose(base(over), t, { lift: 1, done: true, greet: 0.4, phase: 0.5, settled: 0.1 });
      assert.deepEqual(p, { cell: "idle0", dir: "S", mirror: false, kind: "rise", lift: 1 }, JSON.stringify(over));
    }
  }
  // 歩行が勝つ（歩き出したら lift は描かない）
  const w = topPose(base({ state: "working" }), 0, { walk: { phase: 0.25, dir: "N" }, lift: 1 });
  assert.equal(w.kind, "walk");
  assert.equal(w.lift, undefined);
  // 1 以上の整数のときだけ（0・小数・真偽値・文字列では立ち上がらない）
  for (const bad of [0, -1, 0.5, true, "1", null, undefined, NaN]) {
    assert.equal(topPose(base({ state: "working" }), 0, { lift: bad }).kind, "type", `lift=${String(bad)}`);
  }
});

test("topPose（R110-S4）: 着いてから SETTLE_SEC は正面の待機コマ（idle0）・その後に種別のコマ・境目は 6fps と 12fps の格子で割り切れる", () => {
  assert.equal(SETTLE_SEC, 1 / 3);
  const typing = base({ state: "working" });
  for (const s of [0, 0.1, 1 / 6, 0.3, 1 / 3 - 1e-3]) {
    assert.deepEqual(topPose(typing, 5, { settled: s }), { cell: "idle0", dir: "S", mirror: false, kind: "settle" }, `settled=${s}`);
  }
  for (const s of [1 / 3, 0.34, 1, 600]) assert.equal(topPose(typing, 5, { settled: s }).kind, "type", `settled=${s}`);
  // 描画の格子（6fps・12fps）で着いた時刻から引いた秒: 一拍はちょうど 2 刻み・4 刻み（浮動小数の誤差で 1 刻み延びない）
  for (const fps of [6, 12]) {
    for (const a0 of [10 + 1 / fps, 123.25, 7 / 3]) {
      let n = 0;
      for (let k = 0; k < fps; k += 1) {
        const t = a0 + k / fps;
        if (topPose(typing, t, { settled: Math.round((t - a0) * 1e6) / 1e6 }).kind === "settle") n += 1;
      }
      assert.equal(n, fps / 3, `${fps}fps で一拍が ${n} 刻み（着いた ${a0}）`);
    }
  }
  // 挙手・休憩・読書の人も、着いた一拍の後にそれぞれのコマへ
  assert.equal(topPose(base({ attention: true }), 5, { settled: 0.2 }).kind, "settle");
  assert.equal(topPose(base({ attention: true }), 5, { settled: 0.5 }).kind, "raise");
  assert.equal(topPose(base({ state: "resting" }), 5, { settled: 0.5 }).kind, "rest");
  // 挨拶は一拍より勝つ（入力の手応えを飲み込まない）・歩行も勝つ
  assert.equal(topPose(typing, 5, { settled: 0.1, greet: 0.1 }).kind, "greet");
  assert.equal(topPose(typing, 5, { settled: 0.1, walk: { phase: 0, dir: "S" } }).kind, "walk");
  // 起点が無い・壊れた値は一拍なし（止め絵＝stepWalkers の frozen・再生は settled null）
  for (const bad of [null, undefined, NaN, -0.1, "0.1", Infinity]) {
    assert.equal(topPose(typing, 5, { settled: bad }).kind, "type", `settled=${String(bad)}`);
  }
  // 決定論
  assert.deepEqual(topPose(typing, 5, { settled: 0.2 }), topPose({ ...typing }, 99, { settled: 0.2 }));
});


// ── R110-S5: 打鍵の速さ・考え中の点 ──────────────────────────────────────────────
test("TYPE_FPS（R110-S5）: 打鍵は burst 3・normal 2・calm 1（6・12fps で割り切れる）・省略と知らない値は normal・止め絵は type0", () => {
  assert.deepEqual({ ...TYPE_FPS }, { burst: 3, normal: 2, calm: 1 });
  assert.ok(Object.isFrozen(TYPE_FPS));
  for (const fps of Object.values(TYPE_FPS)) for (const draw of [6, 12]) assert.equal(draw % fps, 0, `${fps}fps が ${draw}fps で割り切れない`);
  const w = base({ state: "working" });
  const cells = (pace) => Array.from({ length: 12 }, (_, k) => topPose(w, k / 6, { pace }).cell);
  // 2 秒（6fps × 12 刻み）の中のコマの切り替わりの数＝毎秒のコマ数 × 2
  const flips = (seq) => seq.slice(1).filter((c, i) => c !== seq[i]).length;
  assert.equal(flips(cells("burst")), 3 * 2 - 1);
  assert.equal(flips(cells("normal")), 2 * 2 - 1);
  assert.equal(flips(cells("calm")), 1 * 2 - 1);
  assert.deepEqual(cells(undefined), cells("normal"));
  assert.deepEqual(cells("toString"), cells("normal"), "継承プロパティの名前で速さが壊れた");
  for (const pace of ["burst", "normal", "calm"]) {
    assert.equal(topPose(w, 0, { pace }).cell, "type0", `${pace}: 止め絵（t 0・位相 0）が type0 でない`);
    // 打鍵以外の種別には効かない
    assert.equal(topPose(base({ state: "working", verb: "読込中" }), 0.6, { pace }).cell, "read0");
    assert.equal(topPose(base(), 0.6, { pace }).cell, "idle0");
  }
});

test("typePace（R110-S5）: 工具の最中（kind tool）の edit は burst・ほかの工具は normal・工具を使っていなければ calm", () => {
  assert.equal(typePace({ kind: "tool" }, "edit"), "burst");
  for (const cls of ["bash", "read", "web", "plan", null]) assert.equal(typePace({ kind: "tool" }, cls), "normal", String(cls));
  for (const kind of ["order", "said", "idle", "think", "", undefined]) assert.equal(typePace({ kind }, "edit"), "calm", String(kind));
  assert.equal(typePace(null, "edit"), "calm");
});

test("thinkDots（R110-S5）: 印が出た t から 0 → 1 → 2 を THINK_DOTS_FPS で繰り返す・since が無ければ位相から・壊れた値は 0 起点", () => {
  assert.equal(THINK_DOTS_FPS, 2);
  const seq = (since, phase) => Array.from({ length: 8 }, (_, k) => thinkDots(10 + k / 2, since, phase));
  assert.deepEqual(seq(10), [0, 1, 2, 0, 1, 2, 0, 1], "考え始めは点なしから増えない");
  // since より前の t（その印が出る前）は位相の起点（位相 0＝t 10 で 20 段目 → 2）・since からは 0 から
  assert.deepEqual(seq(10.5), [2, 0, 1, 2, 0, 1, 2, 0]);
  // 6fps の格子（t + k/6）でも境目の手前に落ちない
  for (let k = 0; k < 60; k += 1) assert.equal(thinkDots(20 + k / 6, 20), Math.floor(k / 3) % 3, `k=${k}`);
  // since が無い・未来（巻き戻し）は位相から
  assert.equal(thinkDots(0, null, 0), 0);
  assert.equal(thinkDots(1, null, 0), 2);
  assert.equal(thinkDots(1, null, 0.5), 0);
  assert.equal(thinkDots(5, 9, 0), thinkDots(5, null, 0));
  for (const t of [NaN, Infinity, -Infinity]) assert.ok([0, 1, 2].includes(thinkDots(t, null, 0)));
});

// ── R110-S2: 職業の小道具 ──────────────────────────────────────────────────────
test("TOP_ARCH_TILES / TOP_DESK_TILES（R110-S2）: 職業の語彙の中・dev と generic は付けない・寸法の表と鍵が一致（凍結）", () => {
  const kinds = new Set(ARCHETYPE_RULES.map(([k]) => k));
  for (const k of [...Object.keys(TOP_ARCH_TILES), ...Object.keys(TOP_DESK_TILES)]) assert.ok(kinds.has(k), `${k} は職業の語彙に無い`);
  for (const k of ["dev", "generic"]) {
    assert.equal(Object.hasOwn(TOP_ARCH_TILES, k), false, `${k} に小道具を付けた（嘘の帽子）`);
    assert.equal(Object.hasOwn(TOP_DESK_TILES, k), false);
  }
  assert.deepEqual(Object.values(TOP_ARCH_TILES).sort(), Object.keys(TOP_ARCH_SIZE).sort());
  assert.deepEqual([...new Set(Object.values(TOP_DESK_TILES))].sort(), Object.keys(TOP_DESK_SIZE).sort());
  for (const t of [TOP_ARCH_TILES, TOP_ARCH_SIZE, TOP_DESK_TILES, TOP_DESK_SIZE]) assert.ok(Object.isFrozen(t));
  for (const [w, h] of [...Object.values(TOP_ARCH_SIZE), ...Object.values(TOP_DESK_SIZE)]) {
    assert.ok(Number.isInteger(w) && Number.isInteger(h) && w > 0 && h > 0);
  }
  // 計画の表（R110 S2）: 職業 → タイル
  assert.deepEqual({ ...TOP_ARCH_TILES }, { video: "propBeret", design: "propBeretL", audio: "propHeadset", support: "propHeadsetMic",
    writer: "propPencil", research: "propMortar", infra: "propHardhat", finance: "propEyeshade", ops: "propBowtie" });
  assert.deepEqual({ ...TOP_DESK_TILES }, { video: "deskTimeline", writer: "deskStack", audio: "deskStack", design: "deskBrush" });
});

test("headTopRow（R110-S2）: 列 2..9 に色が 4 つ以上並ぶ最初の行（アンテナの点を頭と取り違えない）", () => {
  const g = (rows) => [...rows, ...Array(16 - rows.length).fill("............")];
  assert.equal(headTopRow(g([".rrrrrrrrrr."])), 0);
  assert.equal(headTopRow(g(["............", ".rrrrrrrrrr."])), 1);
  assert.equal(headTopRow(g(["..a......a..", "..r......r..", "BBBBBBBBBBBB"])), 2, "アンテナの行を頭にした");
  assert.equal(headTopRow(g(["rr........rr", "..rrrr......"])), 1, "列 2..9 の外の色を数えた");
  assert.equal(headTopRow(null), 0);
  assert.equal(headTopRow(g([])), 0);
});

test("topPropAt（R110-S2）: 頭の物は頭の上端の行に下端を重ねる（列 2..）・耳の鉛筆・胸の蝶ネクタイ（奥向きは出さない）・W は鏡・lift で上がる", () => {
  const head0 = [".rrrrrrrrrr.", ...Array(15).fill("rrrrrrrrrrrr")];
  const head1 = ["............", ".rrrrrrrrrr.", ...Array(14).fill("rrrrrrrrrrrr")];
  assert.deepEqual(topPropAt([20, 30], "video", head0), { tile: "propBeret", x: 22, y: 28, w: 8, h: 3, flip: false });
  assert.deepEqual(topPropAt({ x: 20, y: 30 }, "video", head1), { tile: "propBeret", x: 22, y: 29, w: 8, h: 3, flip: false },
    "頭が 1px 下がるコマで小道具が付いてこない");
  assert.deepEqual(topPropAt([20, 30], "research", head0), { tile: "propMortar", x: 22, y: 29, w: 8, h: 2, flip: false });
  assert.deepEqual(topPropAt([20, 30], "support", head0, { mirror: true }), { tile: "propHeadsetMic", x: 21, y: 29, w: 9, h: 2, flip: true },
    "W（鏡）でマイクが左へ出ない");
  assert.deepEqual(topPropAt([20, 30], "writer", head0), { tile: "propPencil", x: 27, y: 30, w: 3, h: 1, flip: false });
  assert.deepEqual(topPropAt([20, 30], "writer", head0, { mirror: true }), { tile: "propPencil", x: 22, y: 30, w: 3, h: 1, flip: true });
  // 胸は頭の上下に付いていかない（胴は動かない）・奥へ歩く（N）ときは出さない
  assert.deepEqual(topPropAt([20, 30], "ops", head0), { tile: "propBowtie", x: 24, y: 37, w: 4, h: 2, flip: false });
  assert.deepEqual(topPropAt([20, 30], "ops", head1), topPropAt([20, 30], "ops", head0));
  assert.equal(topPropAt([20, 30], "ops", head0, { dir: "N" }), null);
  assert.ok(topPropAt([20, 30], "video", head0, { dir: "N" }), "頭の物は奥向きでも出す");
  // 挨拶の跳ね・立ち上がり（lift）でロボと一緒に上がる
  assert.equal(topPropAt([20, 30], "video", head0, { lift: 1 }).y, 27);
  assert.equal(topPropAt([20, 30], "video", head0, { lift: -2 }).y, 28, "負の lift で下げた");
  // 付けない・壊れた入力
  for (const prop of ["dev", "generic", "", undefined, "toString", "__proto__"]) assert.equal(topPropAt([20, 30], prop, head0), null, String(prop));
  assert.equal(topPropAt([NaN, 30], "video", head0), null);
  assert.equal(topPropAt(null, "video", head0), null);
  // 座標は整数に丸める（人の左上は整数だが念のため）
  assert.deepEqual(topPropAt([20.4, 29.6], "video", head0), topPropAt([20, 30], "video", head0));
});
