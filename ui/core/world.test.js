// ui/core は DOM に触らないので、ブラウザ無しでそのままテストできる。
//   node --test ui/core/*.test.js
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DESK_SLOTS, activityText, agoStr, assignSeats, attentionQueue, buildWorld,
  countByZone, needsAttention, stableIndex, summarizeWorld, topAttention,
  triageSort, zoneOf,
} from "./world.js";

const proj = (over = {}) => ({
  projectId: "p1", session: "s1", name: "proj", crew: 1, state: "working",
  kind: "tool", verb: "編集中", target: "a.js", age: 10,
  minions: 0, pending: false, attention: false, approvalMin: 0, question: "",
  sessions: [], feed: [], ...over,
});

test("needsAttention は承認まちと未回答の質問だけを拾う", () => {
  assert.equal(needsAttention(proj()), false);
  assert.equal(needsAttention(proj({ attention: true })), true);
  // attention が未設定の古い形（employees[]）でも導出できる
  assert.equal(needsAttention({ approvalMin: 4 }), true);
  assert.equal(needsAttention({ question: "これでいい？" }), true);
  assert.equal(needsAttention(null), false);
});

test("zoneOf: ❗はどの状態より優先して待機列へ行く", () => {
  assert.equal(zoneOf(proj()), "desk");
  assert.equal(zoneOf(proj({ state: "resting" })), "lounge");
  assert.equal(zoneOf(proj({ minions: 2 })), "meeting");
  // ❗ は会議中でも休憩中でも列へ（人間を待たせないための設計）
  assert.equal(zoneOf(proj({ minions: 3, attention: true })), "queue");
  assert.equal(zoneOf(proj({ state: "resting", attention: true })), "queue");
});

test("zoneOf: external(OpenClaw) は専用区画から動かない", () => {
  assert.equal(zoneOf(proj({ external: "openclaw", state: "resting" })), "external");
  assert.equal(zoneOf(proj({ external: "openclaw", minions: 5 })), "external");
});

test("zoneOf: 休憩中は部下がいても会議へ行かない", () => {
  assert.equal(zoneOf(proj({ state: "resting", minions: 4 })), "lounge");
});

test("triageSort: ❗ → 稼働 → 待機 → 休憩 → external の順", () => {
  const list = [
    proj({ session: "rest", state: "resting" }),
    proj({ session: "ext", external: "openclaw" }),
    proj({ session: "attn", attention: true }),
    proj({ session: "work", state: "working" }),
    proj({ session: "wait", state: "waiting" }),
  ];
  assert.deepEqual(triageSort(list).map((e) => e.session),
    ["attn", "work", "wait", "rest", "ext"]);
});

test("triageSort は元の配列を壊さない", () => {
  const list = [proj({ session: "a", state: "resting" }), proj({ session: "b" })];
  triageSort(list);
  assert.deepEqual(list.map((e) => e.session), ["a", "b"]);
});

test("activityText は動詞と対象をつなぐ", () => {
  assert.equal(activityText(proj()), "編集中 a.js");
  assert.equal(activityText(proj({ target: "" })), "編集中");
  assert.equal(activityText(null), "");
});

// ── 席割当（決定論・毎回同じ席） ──────────────────────────────
test("assignSeats: 同じ入力なら毎回まったく同じ配置", () => {
  const agents = ["a", "b", "c", "d"].map((id) => ({ ...proj({ session: id }), id }));
  const first = assignSeats(agents);
  const second = assignSeats(agents);
  assert.deepEqual([...first.entries()].sort(), [...second.entries()].sort());
});

test("assignSeats: 順番が入れ替わっても各人の席は変わらない（筋肉記憶）", () => {
  const mk = (ids) => ids.map((id) => ({ ...proj({ session: id }), id }));
  const a = assignSeats(mk(["x", "y", "z"]));
  const b = assignSeats(mk(["z", "y", "x"]));
  // 先着で衝突解決するので順序が変わると席がずれ得る。ここでは衝突が無いことを確認する
  if (new Set([...a.values()]).size === 3 && new Set([...b.values()]).size === 3) {
    assert.equal(a.get("y"), b.get("y"));
  }
});

test("assignSeats: 席は重複せず、机ゾーンの人だけが座る", () => {
  const agents = [
    { ...proj({ session: "d1" }), id: "d1" },
    { ...proj({ session: "d2" }), id: "d2" },
    { ...proj({ session: "m1", minions: 2 }), id: "m1" },   // 会議室
    { ...proj({ session: "q1", attention: true }), id: "q1" }, // 待機列
    { ...proj({ session: "r1", state: "resting" }), id: "r1" }, // ラウンジ
  ];
  const seats = assignSeats(agents);
  assert.equal(seats.size, 2, "机に座るのは desk ゾーンの2人だけ");
  assert.equal(new Set([...seats.values()]).size, 2, "席が重複していない");
  for (const v of seats.values()) assert.ok(v >= 0 && v < DESK_SLOTS);
});

test("assignSeats: 席数を超えた分は座らない（空席を作らないための上限）", () => {
  const agents = Array.from({ length: DESK_SLOTS + 5 }, (_, i) =>
    ({ ...proj({ session: `s${i}` }), id: `s${i}` }));
  const seats = assignSeats(agents);
  assert.equal(seats.size, DESK_SLOTS);
});

test("stableIndex: 決定論で範囲内", () => {
  assert.equal(stableIndex("abc", 12), stableIndex("abc", 12));
  for (const s of ["", "a", "とても長い日本語のプロジェクト名", "x".repeat(500)]) {
    const v = stableIndex(s, 12);
    assert.ok(Number.isInteger(v) && v >= 0 && v < 12, `${s} → ${v}`);
  }
});

// ── buildWorld ────────────────────────────────────────────────
test("buildWorld: roster[] があればそれを使う", () => {
  const w = buildWorld({
    officeName: "テスト", lang: "ja", edition: { id: "hybrid", features: {} },
    roster: [proj({ crew: 3, disp: "ai-office" })],
    employees: [{ session: "ignored" }, { session: "ignored2" }],
  });
  assert.equal(w.agents.length, 1, "roster[] 優先＝1プロジェクト1アバター");
  assert.equal(w.agents[0].crew, 3);
  assert.equal(w.agents[0].name, "ai-office");
  assert.equal(w.officeName, "テスト");
});

test("buildWorld: roster[] が無ければ employees[] から作る（後方互換）", () => {
  const w = buildWorld({
    employees: [
      { session: "s1", dept: "A", state: "working", verb: "実行中", target: "x", age: 3 },
      { session: "s2", dept: "B", state: "waiting", verb: "指示待ち", age: 9 },
    ],
  });
  assert.equal(w.agents.length, 2);
  assert.equal(w.agents[0].crew, 1);
});

test("buildWorld: 壊れた入力でも落ちない", () => {
  for (const bad of [null, undefined, "nope", 42, {}, { roster: "x", employees: "y" }]) {
    const w = buildWorld(bad);
    assert.ok(Array.isArray(w.agents), `${JSON.stringify(bad)} で落ちた`);
  }
});

test("countByZone は ❗ を別枠で数える", () => {
  const c = countByZone([
    proj({ attention: true }), proj({ state: "resting" }),
    proj({ minions: 1 }), proj({ external: "openclaw" }), proj(),
  ]);
  assert.deepEqual(c, { desk: 1, meeting: 1, queue: 1, lounge: 1, external: 1, attention: 1 });
});

test("topAttention: 質問を承認まちより先に出す（飢餓閾値の手前まで）", () => {
  const a = proj({ session: "approve", attention: true, approvalMin: 5 });
  const b = proj({ session: "ask", attention: true, question: "どっち？" });
  assert.equal(topAttention([a, b]).session, "ask");
  assert.equal(topAttention([proj()]), null);
  assert.equal(topAttention([]), null);
});

test("attentionQueue: 全件をトリアージ順で返し、15分超の承認まちは質問より昇格（飢餓防止）", () => {
  const starving = proj({ session: "old-approve", attention: true, approvalMin: 20 });
  const ask = proj({ session: "ask", attention: true, question: "どっち？" });
  const fresh = proj({ session: "new-approve", attention: true, approvalMin: 3 });
  const idle = proj({ session: "idle" });
  const q = attentionQueue([fresh, ask, idle, starving]);
  assert.deepEqual(q.map((x) => x.session), ["old-approve", "ask", "new-approve"]);
  assert.equal(topAttention([fresh, ask, starving]).session, "old-approve");
  assert.deepEqual(attentionQueue([]), []);
  assert.deepEqual(attentionQueue(null), []);
});

test("agoStr: 相対表示の純関数（ja/en）", () => {
  assert.equal(agoStr(30), "たった今");
  assert.equal(agoStr(30, "en"), "now");
  assert.equal(agoStr(300), "5分前");
  assert.equal(agoStr(300, "en"), "5m ago");
  assert.equal(agoStr(7200, "en"), "2h ago");
  assert.equal(agoStr(3 * 86400), "3日前");
  assert.equal(agoStr(-5, "en"), "now");         // 負値はクランプ（時計ずれで壊れない）
});

test("summarizeWorld: 本文とパスを持ち出さない（回帰テストの観測点）", () => {
  const out = summarizeWorld({
    officeName: "テスト", lang: "ja", edition: { id: "hybrid" },
    roster: [proj({ lastSaid: "秘密の本文", cwd: "/Users/me/secret", attention: true })],
  });
  assert.equal(out.agents[0].attention, true);
  const dumped = JSON.stringify(out);
  assert.equal(dumped.includes("秘密"), false);
  assert.equal(dumped.includes("/Users/"), false);
});

test("summarizeWorld: null 入力は null", () => {
  assert.equal(summarizeWorld(null), null);
  assert.equal(summarizeWorld("nope"), null);
});
