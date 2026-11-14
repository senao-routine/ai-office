// R110 S5: 用事（ui/core/errand.js）の単体テスト。資格・初見・きっかけ・窓・繰り返し・live の門・立ち位置と上限・証拠・
// 席を変えない・冪等・語彙のサーバーとの一致。行き先は計画の実測の立ち位置で仮に置く（正本の ERRAND_SPOTS は pxroom 側）。
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  ERRAND, ERRAND_GRID, ERRAND_SPOT_OF, ERRAND_STALE_SEC, EVIDENCE_ERRAND, TOOL_CLASSES, TOP_TOOL_VERBS,
  canErrand, errandTargets, evidenceErrand, evidenceTime, planErrand, toolClassOf,
} from "./errand.js";
import { roomLayout, tintHash } from "./pxroom.js";
import { MARK_STALE_SEC, TOP_READ_TOOLS, TOP_READ_VERBS } from "./pxtop.js";
import { ROOM_FPS, WALK_STALE_SEC } from "./pxwalk.js";

const SPOTS = Object.freeze([
  Object.freeze({ name: "shelf", x: 128, y: 20 }),
  Object.freeze({ name: "bulletin", x: 134, y: 6 }),
  Object.freeze({ name: "board", x: 137, y: 47 }),
  Object.freeze({ name: "counter", x: 64, y: 135 }),
]);
/** 立ち位置が名前ごとに 3 つずつ（上限と取り合いの検査用） */
const MANY = Object.freeze(["shelf", "bulletin", "board", "counter"].flatMap((name, n) =>
  [0, 1, 2].map((k) => Object.freeze({ name, x: 20 + 40 * n + 14 * k, y: 150 }))));
const FPS = ROOM_FPS.idle;                    // 部屋の待機の描画（6fps）
const worker = (i, over = {}) => ({ id: `p${i}`, session: `s${i}`, state: "working", kind: "tool", verb: "編集中",
  age: 5, zone: "desk", vendor: "claude", ...over });

/** n 人の机の人（roomLayout を本番の経路で通す＝key・席・座標は本物） */
function desks(n) {
  return roomLayout({ agents: Array.from({ length: n }, (_, i) => worker(i)) }, [], null).actors;
}

/**
 * t0 から t1 の手前まで 1/FPS 秒ごとに errandTargets を live で回す。at(t) がその時刻の actors。
 * 戻り値の frames[k] = {t, r}（r は errandTargets の戻り値）。t は t0 + k / FPS（割り算＝浮動小数の累積誤差を入れない）。
 */
function simulate(at, t0, t1, opts = {}) {
  let book = opts.prev ?? null;
  const frames = [];
  for (let k = 0; ; k += 1) {
    const t = t0 + k / FPS;
    if (t >= t1) break;
    const r = errandTargets(at(t), t, { spots: SPOTS, ...opts, live: true, prev: book });
    book = r.book;
    frames.push({ t, r });
  }
  return { frames, book };
}

const errandOf = (r, key) => r.actors.find((a) => a.key === key)?.errand;
/** key の人が出発した時刻の並び（errand が無い → 在る、に変わったフレーム） */
function departures(frames, key) {
  const out = [];
  let was = false;
  for (const f of frames) {
    const now = Boolean(errandOf(f.r, key));
    if (now && !was) out.push(f.t);
    was = now;
  }
  return out;
}
/** key の人の窓（連続して errand が在ったフレーム数）の並び */
function runs(frames, key) {
  const out = [];
  let n = 0;
  for (const f of frames) {
    if (errandOf(f.r, key)) n += 1;
    else if (n) { out.push(n); n = 0; }
  }
  if (n) out.push(n);
  return out;
}

test("errand: 用事に出るのは机で道具を使って働いている人だけ（desk×working×tool・❗・退場中・⚠ は出ない・bash/edit は机に残る）", () => {
  const [base] = desks(1);
  const went = (over, before = "Edit", after = "Read") => {
    const at = (t) => [{ ...base, ...over, tool: t < 100 ? before : after }];
    return simulate(at, 40, 130).frames.map((f) => f.r.actors[0].errand).filter(Boolean);
  };
  const ok = went({});
  assert.equal(ok.length, ERRAND.span * FPS);
  assert.ok(ok.every((s) => s === "shelf"));
  for (const over of [{ state: "waiting" }, { state: "resting" }, { kind: "said" }, { kind: "think" }, { kind: "idle" },
    { attention: true }, { leaving: true }, { stuckTool: "Read" }, { zone: "queue" }, { zone: "standby" },
    { zone: "lounge" }, { zone: "meeting" }, { zone: "external" }]) {
    assert.deepEqual(went(over), [], JSON.stringify(over));
  }
  // 机に残る道具（bash・edit・種類の無い工具）へ変わっても出ない
  for (const [b, a] of [["Read", "Bash"], ["Bash", "Edit"], ["Edit", "Write"], ["Read", "NotebookEdit"],
    ["Read", "Agent"], ["Read", "Task"], ["Read", "Skill"], ["Read", "mcp__x__y"], ["Read", "AskUserQuestion"]]) {
    assert.deepEqual(went({}, b, a), [], `${b} → ${a}`);
  }
  assert.equal(canErrand({ ...base, tool: "Read" }), true);
  assert.equal(canErrand({ ...base, tool: "Bash" }), false);
  assert.equal(canErrand({ ...base, tool: "Read", kind: "think" }), false);
  assert.equal(canErrand({ ...base, tool: "Read", attention: true }), false);
  assert.equal(canErrand(null), false);
});

test("errand: 初見は記録だけ（開いた直後に全員が出ない）・start より前の切り替えでは出ない・途中から現れた人も初見", () => {
  assert.equal(ERRAND.start, 30);
  const [a, b] = desks(2);
  // 最初のフレームから読んでいる（t=40 で初見）→ repeat までは出ない
  const reading = simulate(() => [{ ...a, tool: "Read" }], 40, 40 + ERRAND.repeat);
  assert.deepEqual(departures(reading.frames, a.key), []);
  // 開いた直後（t=0）から見ていて、start より前に読み始めた → 切り替えの出発は無い（その後も repeat まで出ない）
  const early = simulate((t) => [{ ...a, tool: t < 10 ? "Edit" : "Read" }], 0, 200);
  assert.deepEqual(departures(early.frames, a.key), []);
  // 途中（t=100）から現れた人がもう読んでいる → 現れた瞬間には出ない
  const later = simulate((t) => (t < 100 ? [{ ...a, tool: "Edit" }] : [{ ...a, tool: "Edit" }, { ...b, tool: "Read" }]), 40, 300);
  assert.deepEqual(departures(later.frames, b.key), []);
});

test("errand: 道具の種類が変わった瞬間に出る（read→本棚・web→掲示板・plan→ボード）・動詞だけでも同じ", () => {
  const [base] = desks(1);
  const where = Object.fromEntries(SPOTS.map((s) => [s.name, [s.x, s.y]]));
  for (const [tool, spot] of [["Read", "shelf"], ["Grep", "shelf"], ["Glob", "shelf"], ["Explore", "shelf"],
    ["WebFetch", "bulletin"], ["WebSearch", "bulletin"], ["TodoWrite", "board"]]) {
    const { frames } = simulate((t) => [{ ...base, tool: t < 100 ? "Edit" : tool }], 40, 110);
    const first = frames.find((f) => f.r.actors[0].errand);
    assert.ok(first, `${tool}: 出ない`);
    assert.equal(first.t, 100, `${tool}: 切り替えた瞬間に出ていない（${first.t}）`);
    const a = first.r.actors[0];
    assert.equal(a.errand, spot, tool);
    assert.deepEqual([a.x, a.y], where[spot], tool);
    assert.equal(first.r.away, 1);
  }
  // サーバーの tool が無い（古い snapshot）＝動詞で決める（JA は一意）
  for (const [verb, spot] of [["読込中", "shelf"], ["調査中", "shelf"], ["リサーチ中", "bulletin"], ["段取り整理中", "board"],
    ["reading", "shelf"], ["planning", "board"]]) {
    const { frames } = simulate((t) => [{ ...base, verb: t < 100 ? "編集中" : verb }], 40, 110);
    assert.equal(frames.find((f) => f.r.actors[0].errand)?.r.actors[0].errand, spot, verb);
  }
  // EN の researching は read と web の両方＝動詞だけでは出ない（嘘の行き先を作らない）
  const amb = simulate((t) => [{ ...base, verb: t < 100 ? "editing" : "researching" }], 40, 130);
  assert.deepEqual(departures(amb.frames, base.key), []);
});

test("errand: span 秒たつと目的地が消え、元の座標（席）に戻る＝入力と同じ物", () => {
  const [base] = desks(1);
  const input = { ...base, tool: "Edit" };
  const reading = { ...base, tool: "Read" };
  const { frames } = simulate((t) => [t < 100 ? input : reading], 40, 160);
  const on = frames.filter((f) => f.r.actors[0].errand);
  assert.equal(on.length, ERRAND.span * FPS);
  assert.equal(on[0].t, 100);
  assert.equal(on.at(-1).t, 100 + ERRAND.span - 1 / FPS);
  for (const f of frames.filter((g) => g.t >= 100 + ERRAND.span)) {
    assert.equal(f.r.actors[0], reading, `t=${f.t}: 用事の後なのに写しのまま`);
    assert.deepEqual([f.r.actors[0].x, f.r.actors[0].y], [base.x, base.y]);
    assert.equal(f.r.away, 0);
  }
});

test("errand: 同じ種類が続くと repeat 秒以降の格子で 1 回だけ出る（キーのハッシュで散らす・注入したハッシュに従う）", () => {
  assert.deepEqual(ERRAND_GRID, { scatter: 60, tick: 1 });
  const people = desks(6);
  const starts = new Map();
  for (const p of people) {
    const { frames } = simulate(() => [{ ...p, tool: "Read" }], 40, 40 + 1200);
    const d = departures(frames, p.key);
    assert.ok(d.length >= 3, `${p.key}: 20 分で ${d.length} 回しか出ない`);
    assert.ok(d[0] >= 40 + ERRAND.repeat && d[0] < 40 + 2 * ERRAND.repeat, `${p.key}: 最初の出発 ${d[0]}`);
    for (let i = 1; i < d.length; i += 1) {
      assert.ok(d[i] - d[i - 1] >= ERRAND.repeat && d[i] - d[i - 1] < 2 * ERRAND.repeat, `${p.key}: 間隔 ${d[i] - d[i - 1]}`);
    }
    const off = tintHash(p.key) % ERRAND_GRID.scatter;
    for (const t of d) assert.ok(((t + off) % ERRAND.repeat) < ERRAND_GRID.tick, `${p.key}: 格子の外 ${t}`);
    // 1 回だけ＝格子の幅（6 フレーム）で何度も出ない・窓はちょうど span 秒
    assert.deepEqual(runs(frames, p.key), d.map(() => ERRAND.span * FPS));
    starts.set(p.key, d[0]);
  }
  assert.ok(new Set(starts.values()).size > 1, "全員が同じ瞬間に立つ（散らしていない）");
  // ハッシュの注入: 0 と 30 で出発が 30 秒ずれる
  const [p] = people;
  const at = () => [{ ...p, tool: "Read" }];
  const d0 = departures(simulate(at, 40, 40 + 600, { hash: () => 0 }).frames, p.key);
  const d30 = departures(simulate(at, 40, 40 + 600, { hash: () => 30 }).frames, p.key);
  assert.equal(d0[0], 480);
  assert.equal(d30[0], 450);
});

test("errand: 窓の途中で種類が変わっても閉じない・行き先も変えない（働く資格を失ったら閉じて机へ戻る）", () => {
  const [base] = desks(1);
  const seq = (t) => (t < 100 ? "Edit" : t < 105 ? "Read" : t < 110 ? "WebFetch" : "Bash");
  const a = simulate((t) => [{ ...base, tool: seq(t) }], 40, 160).frames;
  assert.deepEqual(a.filter((f) => f.r.actors[0].errand).map((f) => f.r.actors[0].errand),
    Array(ERRAND.span * FPS).fill("shelf"));
  assert.deepEqual(departures(a, base.key), [100]);
  // 考え中に変わっても続く（kind は窓を閉じない）
  const b = simulate((t) => [{ ...base, tool: t < 100 ? "Edit" : "Read", kind: t < 110 ? "tool" : "think" }], 40, 160).frames;
  assert.deepEqual(runs(b, base.key), [ERRAND.span * FPS]);
  // 返事待ちに変わった・❗が立った → その瞬間に閉じる
  for (const over of [{ state: "waiting" }, { attention: true }, { stuckTool: "Read" }, { leaving: true }]) {
    const c = simulate((t) => [{ ...base, tool: t < 100 ? "Edit" : "Read", ...(t >= 110 ? over : {}) }], 40, 160).frames;
    assert.deepEqual(runs(c, base.key), [10 * FPS], JSON.stringify(over));
  }
});

test("errand: live でなければ何も変えない（写しを作らない・prev を進めない・人数 0）・入力を書き換えない", () => {
  const people = desks(4);
  const at = (t) => people.map((p, i) => ({ ...p, tool: t < 100 ? "Edit" : ["Read", "WebFetch", "TodoWrite", "Bash"][i] }));
  const { book } = simulate(at, 40, 105);
  const snap = JSON.stringify([...book]);
  for (let t = 0; t < 600; t += 7) {
    const input = at(t);
    const frozen = JSON.stringify(input);
    for (const opts of [{ live: false }, {}, { live: "yes" }]) {
      const r = errandTargets(input, t, { spots: SPOTS, prev: book, ...opts });
      assert.equal(r.actors, input, "live でないのに写しを作った");
      assert.equal(r.book, book, "live でないのに記録を進めた");
      assert.equal(r.away, 0);
    }
    const r = errandTargets(input, NaN, { spots: SPOTS, prev: book, live: true });
    assert.equal(r.actors, input);
    assert.equal(r.book, book);
    errandTargets(input, t, { spots: SPOTS, prev: book, live: true });
    assert.equal(JSON.stringify(input), frozen, "入力の actors を書き換えた");
  }
  assert.equal(JSON.stringify([...book]), snap, "prev を書き換えた");
  assert.ok(errandTargets([], 50, { live: false }).book instanceof Map);
});

test("errand: 同じ立ち位置に 2 人を立たせない（あぶれた人は机に残り、窓の途中から遅れて出ない）・立ち位置が 2 つなら 2 人", () => {
  const people = desks(3);
  const at = (t) => people.map((p) => ({ ...p, tool: t < 100 ? "Edit" : "Read" }));
  const one = simulate(at, 40, 160).frames;
  for (const f of one) assert.ok(f.r.away <= 1, `t=${f.t}: 本棚が 1 つなのに ${f.r.away} 人`);
  const went = people.filter((p) => departures(one, p.key).length);
  assert.equal(went.length, 1, "本棚に行けた人が 1 人でない");
  assert.equal(went[0].key, [...people.map((p) => p.key)].sort()[0], "キーの昇順で先の人が取っていない");
  for (const p of people.filter((q) => q !== went[0])) {
    for (const f of one.filter((g) => g.t >= 100)) {
      const a = f.r.actors.find((x) => x.key === p.key);
      assert.deepEqual([a.x, a.y], [p.x, p.y], `${p.key}: 行けなかったのに机を離れた（t=${f.t}）`);
    }
  }
  const two = [{ name: "shelf", x: 128, y: 20 }, { name: "shelf", x: 112, y: 20 }];
  const both = simulate(at, 40, 130, { spots: two }).frames.find((f) => f.t === 100);
  assert.equal(both.r.away, 2);
  const pos = both.r.actors.filter((a) => a.errand).map((a) => `${a.x},${a.y}`);
  assert.equal(new Set(pos).size, 2, "2 人が同じ立ち位置");
});

test("errand: 同時に出るのは max 人まで（既定 2・0 なら誰も出ない）・長い混在の流れでも上限・立ち位置・席の座標を守る", () => {
  assert.equal(ERRAND.maxAway, 2);
  const people = desks(6);
  const tools = ["Read", "WebFetch", "TodoWrite", "Grep", "WebSearch", "Glob"];
  const at = (t) => people.map((p, i) => ({ ...p, tool: t < 100 ? "Edit" : tools[i] }));
  for (const [max, want] of [[undefined, 2], [1, 1], [0, 0], [5, 5]]) {
    const opts = { spots: MANY, ...(max === undefined ? {} : { max }) };
    const peak = Math.max(...simulate(at, 40, 130, opts).frames.map((f) => f.r.away));
    assert.equal(peak, want, `max=${max}`);
  }
  // 26 人・道具は人と 7 秒の枠ごとに決定的に切り替える・証拠もときどき届く
  const crowd = desks(26);
  const all = ["Read", "Grep", "WebFetch", "WebSearch", "TodoWrite", "Bash", "Edit", "Write", "Agent"];
  const flow = (t) => crowd.map((p, i) => {
    const k = Math.floor(t / 7);
    const h = tintHash(`${p.key}:${k}`);
    return { ...p, tool: all[h % all.length], kind: h % 11 === 0 ? "think" : "tool",
      evidenceAt: 1000 + 30 * Math.floor((t + i * 13) / 90), evidence: { kind: "committed", ago: 3 } };
  });
  const spotAt = new Map(MANY.map((s) => [`${s.x},${s.y}`, s.name]));
  for (const max of [2, 3]) {
    const { frames } = simulate(flow, 0, 1200, { spots: MANY, max });
    let peak = 0;
    for (const f of frames) {
      assert.ok(f.r.away <= max, `t=${f.t}: ${f.r.away} 人 > ${max}`);
      peak = Math.max(peak, f.r.away);
      const seen = new Set();
      f.r.actors.forEach((a, i) => {
        const src = crowd[i];
        if (a.errand) {
          const p = `${a.x},${a.y}`;
          assert.equal(spotAt.get(p), a.errand, `${a.key}: 行き先の名前と立ち位置が違う`);
          assert.ok(!seen.has(p), `t=${f.t}: ${p} に 2 人`);
          seen.add(p);
        } else {
          assert.deepEqual([a.x, a.y], [src.x, src.y], `${a.key}: 用事でないのに席の座標でない`);
        }
      });
      assert.equal(seen.size, f.r.away);
    }
    assert.equal(peak, max, `max=${max}: 一度も上限まで出ない（流れが用事を作っていない）`);
    // 窓（同じ出発＝記録の lastGo）ごとの長さは行き先の span まで（本棚の窓の直後にカウンターへ続くのは別の窓）
    for (const p of crowd) {
      const len = new Map();
      for (const f of frames) {
        const spot = errandOf(f.r, p.key);
        if (!spot) continue;
        const id = `${spot}@${f.r.book.get(p.key).lastGo}`;
        len.set(id, (len.get(id) || 0) + 1);
      }
      for (const [id, n] of len) {
        const span = id.startsWith(`${EVIDENCE_ERRAND.spot}@`) ? EVIDENCE_ERRAND.span : ERRAND.span;
        assert.ok(n <= span * FPS, `${p.key}: 窓 ${id} が span より長い（${n}）`);
      }
    }
  }
});

test("errand: 新しい証拠のときだけカウンターへ 1 回（span 14 秒・道具より先・揺れ・初見・古い・失敗・働いていないは行かない）", () => {
  assert.equal(EVIDENCE_ERRAND.span, 14);
  const [base] = desks(1);
  const ev = (at, over = {}) => ({ evidenceAt: at, evidence: { kind: "committed", ago: 3 }, ...over });
  const counter = (at) => simulate(at, 40, 200).frames;
  // 初見の証拠では行かない → t=100 に新しい証拠 → カウンターへ 14 秒
  let f = counter((t) => [{ ...base, tool: "Bash", ...ev(t < 100 ? 1000 : 2000) }]);
  assert.deepEqual(departures(f, base.key), [100]);
  assert.deepEqual(runs(f, base.key), [EVIDENCE_ERRAND.span * FPS]);
  assert.equal(f.find((g) => g.t === 100).r.actors[0].errand, "counter");
  // 揺れ（同じ出来事の generatedAt − ago が 1 秒未満ずれる）では行かない
  f = counter((t) => [{ ...base, tool: "Bash", ...ev(t < 100 ? 1000 : 1000 + (Math.floor(t) % 2) * 0.9) }]);
  assert.deepEqual(departures(f, base.key), []);
  // 道具の切り替えと同時 → カウンターが先（本棚へは行かない＝きっかけは使い切り）
  f = counter((t) => [{ ...base, tool: t < 100 ? "Edit" : "Read", ...ev(t < 100 ? 1000 : 2000) }]);
  assert.deepEqual(departures(f, base.key), [100]);
  assert.ok(f.filter((g) => errandOf(g.r, base.key)).every((g) => errandOf(g.r, base.key) === "counter"));
  // 初見で証拠が無く、あとから 24 時間前の成功が届く（ago が fresh より古い）→ 行かない
  f = counter((t) => [{ ...base, tool: "Bash", ...(t < 100 ? {} : ev(-86400, { evidence: { kind: "tested", ago: 86400 } })) }]);
  assert.deepEqual(departures(f, base.key), []);
  // 初見で証拠が無く、新しい成功が届く → 行く
  f = counter((t) => [{ ...base, tool: "Bash", ...(t < 100 ? {} : ev(2000, { evidence: { kind: "tested", ago: 1 } })) }]);
  assert.deepEqual(departures(f, base.key), [100]);
  // 失敗の証拠では行かない
  f = counter((t) => [{ ...base, tool: "Bash", ...ev(t < 100 ? 1000 : 2000, { evidence: { kind: "failed", ago: 1 } }) }]);
  assert.deepEqual(departures(f, base.key), []);
  // 働いていない間に届いた証拠は使い切り（あとで働き始めても遅れて行かない）
  f = counter((t) => [{ ...base, tool: "Bash", state: t < 120 ? "waiting" : "working", ...ev(t < 100 ? 1000 : 2000) }]);
  assert.deepEqual(departures(f, base.key), []);
  // 本棚の窓の間に届いた証拠は窓の後へ（本棚 → カウンター → 机）
  f = counter((t) => [{ ...base, tool: t < 100 ? "Edit" : "Read", ...ev(t < 105 ? 1000 : 2000) }]);
  const trail = f.filter((g) => g.t >= 100 && g.t < 100 + ERRAND.span + EVIDENCE_ERRAND.span).map((g) => errandOf(g.r, base.key));
  assert.deepEqual(trail, [...Array(ERRAND.span * FPS).fill("shelf"), ...Array(EVIDENCE_ERRAND.span * FPS).fill("counter")]);
  assert.equal(errandOf(f.find((g) => g.t === 100 + ERRAND.span + EVIDENCE_ERRAND.span).r, base.key), undefined);
  // カウンターに居る間に届いた次の成功は同じ紙（2 往復しない）
  f = counter((t) => [{ ...base, tool: "Bash", ...ev(t < 100 ? 1000 : t < 105 ? 2000 : 2010) }]);
  assert.deepEqual(runs(f, base.key), [EVIDENCE_ERRAND.span * FPS]);
  // 単体: 記録が無い・古い・同じ±揺れは false、記録より jitter 秒より新しければ true
  const rec = { tool: "bash", since: 50, lastGo: null, spot: null, slot: -1, ev: 1000, seen: 100 };
  const a = { ...base, evidenceAt: 1003, evidence: { kind: "committed", ago: 2 } };
  assert.equal(evidenceErrand(a, rec, 100), true);
  assert.equal(evidenceErrand({ ...a, evidenceAt: 1002 }, rec, 100), false);
  assert.equal(evidenceErrand(a, undefined, 100), false);
  assert.equal(evidenceErrand(a, rec, 100 + ERRAND_STALE_SEC + 1), false);
  assert.equal(evidenceErrand(a, rec, 99), false);
  assert.equal(evidenceErrand({ ...a, evidenceAt: null }, rec, 100), false);
  assert.equal(evidenceErrand(a, { ...rec, ev: null }, 100), true);
  assert.equal(evidenceErrand({ ...a, evidence: { kind: "committed", ago: EVIDENCE_ERRAND.fresh + 1 } }, { ...rec, ev: null }, 100), false);
  // 出来事の時刻はスナップショットの時刻から引く・行く種類だけ
  assert.equal(evidenceTime({ kind: "committed", ago: 10 }, 5000), 4990);
  assert.equal(evidenceTime({ kind: "tested", ago: 0 }, 5000), 5000);
  assert.equal(evidenceTime({ kind: "failed", ago: 10 }, 5000), null);
  assert.equal(evidenceTime({ kind: "committed" }, 5000), null);
  assert.equal(evidenceTime({ kind: "committed", ago: 10 }, 0), null);
  assert.equal(evidenceTime(null, 5000), null);
});

test("errand: 席の割り当て・人数・在席（assign/counts/occupied）と席・区分・key は全フレームで変わらない", () => {
  const world = { agents: Array.from({ length: 8 }, (_, i) => worker(i)) };
  const layout = roomLayout(world, [], null);
  const snap = JSON.stringify(layout);
  const tools = ["Read", "WebFetch", "TodoWrite", "Bash", "Edit", "Grep", "WebSearch", "Glob"];
  const at = (t) => layout.actors.map((a, i) => ({ ...a, tool: tools[(i + Math.floor(t / 25)) % tools.length] }));
  const { frames } = simulate(at, 0, 600, { spots: MANY, max: 3 });
  assert.ok(frames.some((f) => f.r.away > 0), "誰も用事に出ていない（検査が空回り）");
  for (const f of frames) {
    f.r.actors.forEach((a, i) => {
      const src = layout.actors[i];
      for (const k of ["key", "id", "session", "seat", "slot", "zone", "want", "state", "attention", "vendor", "projectKey"]) {
        assert.deepEqual(a[k], src[k], `t=${f.t}: ${a.key} の ${k} が変わった`);
      }
    });
  }
  assert.equal(JSON.stringify(layout), snap, "roomLayout の結果（assign/counts/occupied/actors）を書き換えた");
});

test("errand: 冪等＝同じ t・同じ記録なら同じ結果・返った記録で同じ t を呼び直しても同じ結果（記録も同じ）", () => {
  const people = desks(5);
  const tools = ["Read", "WebFetch", "TodoWrite", "Grep", "Read"];
  const at = (t) => people.map((p, i) => ({ ...p, tool: t < 100 + i ? "Edit" : tools[i],
    evidenceAt: t < 130 ? 1000 : 2000, evidence: { kind: "committed", ago: 1 } }));
  let book = null;
  let checked = 0;
  for (let k = 0; k < 6 * 400; k += 1) {
    const t = 40 + k / FPS;
    const input = at(t);
    const r1 = errandTargets(input, t, { spots: SPOTS, live: true, prev: book });
    const r2 = errandTargets(input, t, { spots: SPOTS, live: true, prev: book });
    assert.deepEqual(r2.actors, r1.actors, `t=${t}: 同じ入力で違う配置`);
    assert.deepEqual([...r2.book], [...r1.book], `t=${t}: 同じ入力で違う記録`);
    const r3 = errandTargets(input, t, { spots: SPOTS, live: true, prev: r1.book });
    assert.deepEqual(r3.actors, r1.actors, `t=${t}: 描き直し（同じ t）で配置が変わった`);
    assert.equal(r3.away, r1.away);
    assert.deepEqual([...r3.book], [...r1.book], `t=${t}: 描き直しで記録が進んだ`);
    if (r1.away) checked += 1;
    book = r1.book;
  }
  assert.ok(checked > 0, "用事に出ているフレームが無い（検査が空回り）");
  const [p] = people;
  const rec = { tool: "edit", since: 50, lastGo: null, spot: null, slot: -1, ev: null, seen: 99.9 };
  const a = { ...p, tool: "Read" };
  assert.deepEqual(planErrand(a, rec, 100, { spots: SPOTS }), planErrand(a, rec, 100, { spots: SPOTS }));
  const once = planErrand(a, rec, 100, { spots: SPOTS });
  assert.equal(once.go, true);
  assert.equal(once.spot, "shelf");
  assert.equal(once.until, 100 + ERRAND.span);
  assert.equal(once.held, false);
  const again = planErrand(a, once.rec, 100, { spots: SPOTS });
  assert.deepEqual([again.go, again.spot, again.until], [true, "shelf", 100 + ERRAND.span]);
  assert.equal(planErrand(a, rec, 100, {}).go, false, "行き先が無いのに出た");
});

test("errand: 古い記録（非表示・停止から戻った・巻き戻った）は初見と同じ＝その間の切り替えを遅れて演じない", () => {
  assert.equal(ERRAND_STALE_SEC, WALK_STALE_SEC);
  assert.equal(ERRAND_STALE_SEC, MARK_STALE_SEC);
  const [base] = desks(1);
  const at = (t) => [{ ...base, tool: t < 130 ? "Edit" : "Read" }];
  const before = simulate(at, 40, 100);
  // 100〜160 は描かない（隠れていた）。130 に読み始めた。160 に戻る
  const after = simulate(at, 160, 160 + ERRAND.repeat - 30, { prev: before.book });
  assert.deepEqual(departures(after.frames, base.key), []);
  // 巻き戻った（t が記録より前）も初見＝記録は Edit のまま・戻った先ではもう Read でも出ない
  const back = simulate(() => [{ ...base, tool: "Read" }], 50, 80, { prev: before.book });
  assert.deepEqual(departures(back.frames, base.key), []);
  // 戻った後に生で見た切り替えでは出る（初見の後は普段どおり）
  const live = simulate((t) => [{ ...base, tool: t < 170 ? "Edit" : "Read" }], 160, 200, { prev: before.book });
  assert.deepEqual(departures(live.frames, base.key), [170]);
});

test("errand: toolClassOf の優先（tool → ask.tool → stuckTool → kind tool の verb）・考え中と報告中は null", () => {
  assert.equal(toolClassOf({ kind: "tool", tool: "Read", ask: { tool: "Bash" }, stuckTool: "Edit", verb: "実行中" }), "read");
  assert.equal(toolClassOf({ kind: "tool", tool: "", ask: { tool: "WebFetch" }, stuckTool: "Bash" }), "web");
  assert.equal(toolClassOf({ kind: "tool", stuckTool: "TodoWrite", verb: "実行中" }), "plan");
  assert.equal(toolClassOf({ kind: "tool", verb: "段取り整理中" }), "plan");
  assert.equal(toolClassOf({ kind: "order", verb: "段取り整理中" }), null, "kind が tool でないのに動詞で決めた");
  assert.equal(toolClassOf({ kind: "tool", tool: "Agent", verb: "読込中" }), null, "最初に在った名前が決める（後ろへ落とさない）");
  for (const kind of ["think", "said"]) assert.equal(toolClassOf({ kind, tool: "Read", verb: "読込中" }), null, kind);
  assert.equal(toolClassOf({ kind: "tool", tool: "mcp__x__y" }), null);
  assert.equal(toolClassOf({ kind: "tool", tool: "TaskCreate" }), null);
  assert.equal(toolClassOf(null), null);
  // EN の researching は read と web にまたがる＝動詞だけでは決めない（tool が在れば決まる）
  assert.equal(toolClassOf({ kind: "tool", verb: "researching" }), null);
  assert.equal(toolClassOf({ kind: "tool", tool: "WebFetch", verb: "researching" }), "web");
  assert.equal(toolClassOf({ kind: "tool", tool: "Grep", verb: "researching" }), "read");
  for (const [cls, tools] of Object.entries(TOOL_CLASSES)) {
    for (const tool of tools) assert.equal(toolClassOf({ kind: "tool", tool }), cls, tool);
  }
  for (const [lang, table] of Object.entries(TOP_TOOL_VERBS)) {
    for (const [cls, verbs] of Object.entries(table)) {
      for (const verb of verbs) {
        const want = verb === "researching" ? null : cls;
        assert.equal(toolClassOf({ kind: "tool", verb }), want, `${lang}: ${verb}`);
      }
    }
  }
  assert.deepEqual(Object.keys(TOP_TOOL_VERBS.ja), Object.keys(TOOL_CLASSES));
  assert.deepEqual(Object.keys(TOP_TOOL_VERBS.en), Object.keys(TOOL_CLASSES));
  for (const cls of Object.keys(ERRAND_SPOT_OF)) assert.ok(Object.hasOwn(TOOL_CLASSES, cls), cls);
});

// ── サーバーの動詞の表との突き合わせ（tests/cockpit_cells.test.mjs の「読書の語彙」と同じ型）──────────────
const SERVER = readFileSync(new URL("../../server/office_server.py", import.meta.url), "utf8");

/** server/office_server.py の `NAME = { "Tool": "動詞", ... }` を文字列で読む（Python は起動しない） */
function serverTable(name) {
  const m = SERVER.match(new RegExp(`^${name}\\s*=\\s*\\{([\\s\\S]*?)^\\}`, "m"));
  assert.ok(m, `server/office_server.py に ${name} の表が見つからない`);
  const table = {};
  for (const [, k, v] of m[1].matchAll(/"([^"]+)"\s*:\s*"([^"]*)"/g)) table[k] = v;
  assert.ok(Object.keys(table).length >= 10, `${name} の表を読めていない（${Object.keys(table).length} 件）`);
  return table;
}

test("errand: TOP_TOOL_VERBS がサーバーの TOOL_VERB / TOOL_VERB_EN の値と一致する（種類ごと・種類の外へ漏れない）", () => {
  const classOf = new Map(Object.entries(TOOL_CLASSES).flatMap(([cls, tools]) => tools.map((t) => [t, cls])));
  for (const [name, lang] of [["TOOL_VERB", "ja"], ["TOOL_VERB_EN", "en"]]) {
    const table = serverTable(name);
    for (const [cls, tools] of Object.entries(TOOL_CLASSES)) {
      const known = tools.filter((t) => Object.hasOwn(table, t));
      assert.ok(known.length >= 1, `${name}: ${cls} の工具が 1 つも表に無い（${tools.join("・")}）`);
      const got = [...new Set(known.map((t) => table[t]))].sort();
      assert.deepEqual(got, [...TOP_TOOL_VERBS[lang][cls]].sort(), `${name}: ${cls} の動詞がサーバーと違う`);
    }
    // 種類の無い工具が種類の動詞を名乗っていない（走らせる・頼むのに本棚へ行く、を許さない）
    const vocab = new Set(Object.values(TOP_TOOL_VERBS[lang]).flat());
    const leak = Object.entries(table).filter(([tool, verb]) => !classOf.has(tool) && vocab.has(verb));
    assert.deepEqual(leak, [], `${name}: 種類の無い工具が種類の動詞を使っている`);
  }
  // pxtop の「読んでいる」語彙（読書のコマ）＝ read と web の和（二つの語彙を食い違わせない）
  assert.deepEqual([...TOP_READ_TOOLS].sort(), [...TOOL_CLASSES.read, ...TOOL_CLASSES.web].sort());
  for (const lang of ["ja", "en"]) {
    assert.deepEqual([...new Set([...TOP_TOOL_VERBS[lang].read, ...TOP_TOOL_VERBS[lang].web])].sort(),
      [...TOP_READ_VERBS[lang]].sort(), `${lang}: 読書の動詞が pxtop と違う`);
  }
  // 動詞が種類をまたぐのは EN の researching だけ（増えたら toolClassOf の「決めない」が広がる＝ここで気づく）
  const cross = [];
  for (const [lang, table] of Object.entries(TOP_TOOL_VERBS)) {
    const owner = new Map();
    for (const [cls, verbs] of Object.entries(table)) {
      for (const v of verbs) {
        if (owner.has(v) && owner.get(v) !== cls) cross.push(`${lang}:${v}`);
        owner.set(v, cls);
      }
    }
  }
  assert.deepEqual(cross, ["en:researching"]);
});
