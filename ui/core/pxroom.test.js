// R99: 俯瞰オフィスの間取り（純関数）。「何人居ても部屋からはみ出さない」
// 「同じ入力なら同じ配置」「席が動かない（机を離れた人の机は予約）」「受付に立つ＝確認待ち」
// 「受付は列＝確認待ちは前から・standby は最後尾から詰める」「色は表示している間は変えない」を機械で固定する。
// 門が本当に効くことは、写しのツリーで 1 か所ずつ壊して赤になることを確かめてある（R99 Phase 3 修正ラウンド）。
// タイル名とタイルの寸法の突き合わせは tests/pxroom_tiles.test.mjs（core から cockpit を import しない）。
import test from "node:test";
import assert from "node:assert/strict";
import {
  DESKS, DOOR, FRONT_BANDS, FURNITURE, GLASS_OPENING, PICK_RADIUS_PX, ROBOT_FOOT, ROOM, ROOM_SCALE_FLOOR, ROOM_SCALE_MAX, ROOM_SCALE_MIN,
  SEATS, SLOTS, assistantCount, deskAt, deskWorkKind, footRects, hasErrorNote, pickNearest, roomLayout, roomScale, roomWorldFields, roomZoneOf, tintHash, tintIndex,
} from "./pxroom.js";
import { buildWorld, needsAttention } from "./world.js";
import { LANES } from "./pxwalk.js";

const officeOf = (n, over = () => ({})) => ({
  employees: Array.from({ length: n }, (_, i) => ({
    session: `s${i}`, dept: `p${i}`, cwd: `/w${i}`, state: "working", ...over(i),
  })),
});
const boardOf = (world) => world.agents.map((a) => ({ key: a.id, name: a.name, sessions: [a] }));
const layout = (n, over) => {
  const w = buildWorld(officeOf(n, over));
  return roomLayout(w, boardOf(w));
};
const zones = (res, z) => res.actors.filter((a) => a.zone === z);

/** 決定論の擬似乱数（core のテストでも Math.random は使えない＝層 lint） */
function lcg(seed) {
  let s = seed >>> 0;
  return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 2 ** 32; };
}
const overlap = (a, b) => a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3];
const footAt = (x, y) => [x + ROBOT_FOOT[0], y + ROBOT_FOOT[1], ROBOT_FOOT[2], ROBOT_FOOT[3]];

test("pxroom: 机は 3×3 で、重ならず部屋に収まる", () => {
  assert.equal(SEATS, 9);
  assert.equal(DESKS.length, 9);
  const seen = new Set();
  for (const d of DESKS) {
    assert.ok(d.x >= 0 && d.x + d.w <= ROOM.w, `机が横にはみ出す: ${d.x}`);
    assert.ok(d.y >= 0 && d.y + d.h <= ROOM.h, `机が縦にはみ出す: ${d.y}`);
    assert.ok(d.robot.y >= 0 && d.robot.y + ROOM.robot.h <= ROOM.h, `人が縦にはみ出す: ${d.robot.y}`);
    assert.ok(!seen.has(`${d.x},${d.y}`), `机が重なっている: ${d.x},${d.y}`);
    seen.add(`${d.x},${d.y}`);
  }
});

test("pxroom: 縦の間隔は 26 以上（天板 7 ＋ 引き出し 8 ＋ 上半身 11）", () => {
  // 25 にしたら引き出しが次の段の人と重なった（R99 の実測）。ここを緩めない。
  assert.ok(ROOM.desk.pitchY >= 26, `pitchY=${ROOM.desk.pitchY}`);
  assert.equal(deskAt(3).y - deskAt(0).y, ROOM.desk.pitchY);
});

test("pxroom: 9 人までは全員が席に着く", () => {
  const { actors, occupied, overflow } = layout(9);
  assert.equal(actors.length, 9);
  assert.equal(overflow, 0);
  assert.deepEqual(occupied, [0, 1, 2, 3, 4, 5, 6, 7, 8]);
  assert.ok(actors.every((a) => a.zone === "desk"));
});

test("pxroom: 席が尽きたら standby（受付の枠・挙手しない）、受付も尽きたら overflow", () => {
  const res = layout(14);
  assert.equal(res.occupied.length, SEATS);
  assert.equal(zones(res, "standby").length, 5);
  assert.equal(zones(res, "queue").length, 0, "確認待ちでない人が queue（❗の絵）に居る");
  assert.ok(zones(res, "standby").every((a) => a.attention === false));
  // 受付の定員（9）を超えたら部屋には描かず overflow で数える
  const big = layout(30);
  assert.equal(big.overflow, 30 - SEATS - SLOTS.queue.length);
  assert.equal(big.actors.length + big.overflow, 30);
});

test("pxroom: 確認待ちは席より優先して受付に立つ", () => {
  const { actors } = layout(4, (i) => (i === 2 ? { question: "これで進めていいですか？" } : {}));
  const a = actors.find((x) => x.session === "s2");
  assert.equal(a.zone, "queue");
  assert.ok(a.attention);
  // 呼んでいない 3 人はちゃんと席に居る
  assert.equal(actors.filter((x) => x.zone === "desk").length, 3);
});

test("pxroom: 確認待ちが受付の定員を超えたぶんは overflow の人数で返す", () => {
  const res = layout(12, () => ({ question: "?" }));
  assert.equal(zones(res, "queue").length, SLOTS.queue.length);
  assert.equal(res.overflow, 12 - SLOTS.queue.length);
  assert.equal(res.counts.queue, SLOTS.queue.length);
});

test("pxroom B3: 休憩はソファ、あふれは受付ではなく空いている机へ", () => {
  const rest = layout(5, () => ({ state: "resting" }));
  assert.equal(zones(rest, "lounge").length, SLOTS.lounge.length);
  assert.equal(zones(rest, "desk").length, 5 - SLOTS.lounge.length);
  assert.equal(zones(rest, "queue").length + zones(rest, "standby").length, 0);
  assert.ok(zones(rest, "desk").every((a) => a.want === "lounge"), "仮住まいの人は want に本来の場所を持つ");
});

test("pxroom B3: 机も埋まっていたら standby（attention の絵にしない）", () => {
  // 働く 9 人が机を埋め、休憩 3 人のうち 2 人がソファ、1 人は机も無い＝standby
  const res = layout(12, (i) => (i >= 9 ? { state: "resting" } : {}));
  assert.equal(zones(res, "desk").length, 9);
  assert.equal(zones(res, "lounge").length, 2);
  const sb = zones(res, "standby");
  assert.equal(sb.length, 1);
  assert.equal(sb[0].attention, false);
  assert.equal(sb[0].want, "lounge");
});

test("pxroom B3: 思考中（think）は机のまま、会議室はサブエージェント稼働中だけ", () => {
  const think = layout(5, () => ({ state: "working", kind: "think" }));
  assert.equal(zones(think, "meeting").length, 0, "think を会議室へ送っている");
  assert.equal(zones(think, "desk").length, 5);
  const meet = layout(5, () => ({ state: "working", minions: 2 }));
  assert.equal(zones(meet, "meeting").length, SLOTS.meeting.length);
  assert.equal(zones(meet, "desk").length, 5 - SLOTS.meeting.length, "会議室のあふれは机へ");
});

test("pxroom B3: 外部社員は机に座らずラックの前に立つ・あふれは standby", () => {
  const ext = (i) => ({ external: { provider: "openclaw", host: `mini${i}` } });
  const res = layout(5, ext);
  assert.equal(zones(res, "desk").length, 0, "外部社員が机に座っている");
  assert.equal(zones(res, "external").length, SLOTS.external.length);
  assert.equal(zones(res, "standby").length, 5 - SLOTS.external.length);
  assert.ok(res.actors.every((a) => a.vendor === "openclaw"));
  // 外部社員の ❗ は専用区画のまま頭上の印で出す（world の zone が external を優先する）
  const call = layout(2, (i) => ({ ...ext(i), ...(i === 0 ? { question: "?" } : {}) }));
  const a = call.actors.find((x) => x.session === "s0");
  assert.equal(a.zone, "external");
  assert.equal(a.attention, true);
});

test("pxroom B3: ラック前からあふれた外部社員の ❗ は受付の列（attention のまま）、❗ でない人は standby", () => {
  // 台帳の順（age）で s0..s2 がラック前、s3（❗）と s4 があふれる
  const ext = (i) => ({ external: { provider: "openclaw", host: `mini${i}` }, age: 10 + i * 10,
    ...(i === 3 ? { question: "?" } : {}) });
  const w = buildWorld(officeOf(5, ext));
  const res = roomLayout(w, boardOf(w));
  const by = new Map(res.actors.map((x) => [x.session, x]));
  assert.deepEqual(["s0", "s1", "s2"].map((s) => by.get(s).zone), ["external", "external", "external"]);
  assert.equal(by.get("s3").zone, "queue", "あふれた ❗ が受付の列に居ない（❗ が絵から消える）");
  assert.equal(by.get("s3").attention, true);
  assert.equal(by.get("s3").want, "external");
  assert.equal(by.get("s3").slot, 0);
  assert.equal(by.get("s4").zone, "standby");
  assert.equal(by.get("s4").attention, false);
  assert.equal(res.counts.queue, 1);
  // ラック前が空いたら、列に居た外部社員はラック前へ戻る（❗ はその場で出る）
  const emp = officeOf(5, ext).employees.filter((e) => e.session !== "s0");
  const w2 = buildWorld({ employees: emp });
  const res2 = roomLayout(w2, boardOf(w2), res.assign);
  const s3 = res2.actors.find((x) => x.session === "s3");
  assert.equal(s3.zone, "external");
  assert.equal(s3.attention, true);
  assert.equal(res2.actors.find((x) => x.session === "s4").zone, "standby");
});

test("pxroom B3: world の zone／attention → 部屋の区分の対応表", () => {
  const TABLE = [
    [{ zone: "desk", attention: false }, "desk"],
    [{ zone: "desk", attention: false, kind: "think" }, "desk"],
    [{ zone: "desk", attention: true }, "queue"],
    [{ zone: "queue", attention: true }, "queue"],
    [{ zone: "queue", attention: false }, "desk"],
    [{ zone: "lounge", attention: false }, "lounge"],
    [{ zone: "lounge", attention: true }, "queue"],
    [{ zone: "meeting", attention: false }, "meeting"],
    [{ zone: "meeting", attention: false, kind: "think" }, "meeting"],
    [{ zone: "meeting", attention: true }, "queue"],
    [{ zone: "external", attention: false }, "external"],
    [{ zone: "external", attention: true }, "external"],
    // zone が無い（素の行）は world の zoneOf で決める
    [{ state: "resting" }, "lounge"],
    [{ state: "working", minions: 2 }, "meeting"],
    [{ state: "working", kind: "think" }, "desk"],
    [{ question: "?" }, "queue"],
    [{ external: { provider: "openclaw" }, question: "?" }, "external"],
    [{ zone: "rest", state: "resting" }, "lounge"],
  ];
  for (const [a, want] of TABLE) assert.equal(roomZoneOf(a), want, JSON.stringify(a));
});

test("pxroom B3: ベンダーを claude に潰さない", () => {
  const w = buildWorld(officeOf(3, (i) => ({ vendor: ["codex", "openclaw", "other"][i] })));
  const res = roomLayout(w, boardOf(w));
  assert.deepEqual(res.actors.map((a) => a.vendor).sort(), ["codex", "openclaw", "other"]);
  // 台帳の行が "other" を運んできたらそのまま（描画側が未知を知れる）
  const row = { ...w.agents[0], vendor: "other" };
  assert.equal(roomLayout(w, [{ key: "k", sessions: [row] }]).actors[0].vendor, "other");
});

test("pxroom: 同じ入力なら同じ配置（前回あり・なし両方で決定論）", () => {
  const w = buildWorld(officeOf(16, (i) => (i % 3 === 0 ? { state: "resting" } : i % 5 === 0 ? { question: "?" } : {})));
  const a = roomLayout(w, boardOf(w));
  assert.equal(JSON.stringify(a), JSON.stringify(roomLayout(w, boardOf(w))));
  const b = roomLayout(w, boardOf(w), a.assign);
  assert.equal(JSON.stringify(b), JSON.stringify(roomLayout(w, boardOf(w), a.assign)));
  // 前回＝今回の入力なら、何も動かない（毎フレーム呼んでも席替えしない）
  assert.equal(JSON.stringify(b), JSON.stringify(a));
  // Map で渡しても同じ
  assert.equal(JSON.stringify(roomLayout(w, boardOf(w), new Map(Object.entries(a.assign)))), JSON.stringify(a));
});

test("pxroom: 誰も同じ座標に立たない", () => {
  for (const n of [3, 9, 14, 25]) {
    const { actors } = layout(n);
    const seen = new Set();
    for (const a of actors) {
      const key = `${a.x},${a.y}`;
      assert.ok(!seen.has(key), `${n} 人で ${key} に 2 体（片方が消える）`);
      seen.add(key);
      assert.ok(a.x >= 0 && a.x + ROOM.robot.w <= ROOM.w, `横にはみ出す: ${a.x}`);
      assert.ok(a.y >= 0 && a.y + ROOM.robot.h <= ROOM.h, `縦にはみ出す: ${a.y}`);
    }
  }
});

test("pxroom: 状態の材料を落とさない（全員が同じ絵になるのを防ぐ）", () => {
  const { actors } = layout(3, (i) => ({ state: ["working", "resting", "idle"][i] }));
  assert.deepEqual(actors.map((a) => a.state).sort(), ["idle", "resting", "working"]);
  assert.ok(actors.every((a) => a.session && a.projectKey && a.vendor));
});

test("pxroom 第 2 波: actors は印とコマの材料（verb・stuckTool・age）と一意の key を運ぶ", () => {
  // verb＝読書のコマ・stuckTool＝⚠・age＝💬 の窓。落とすと本番で読書・⚠・💬 が 1 件も出ないのに赤にならない
  const res = layout(4, (i) => [
    { verb: "読込中", age: 14 },
    { state: "waiting", verb: "実行中", approvalMin: 3, stuckTool: "実行中 deploy.sh", age: 380 },
    { state: "waiting", kind: "said", verb: "指示待ち", age: 120 },
    { state: "waiting", kind: "tool", verb: "実行中", age: 960 },
  ][i]);
  const by = new Map(res.actors.map((a) => [a.session, a]));
  assert.deepEqual(["s0", "s1", "s2", "s3"].map((k) => [by.get(k).verb, by.get(k).stuckTool, by.get(k).age]), [
    ["読込中", "", 14], ["実行中", "実行中 deploy.sh", 380], ["指示待ち", "", 120], ["実行中", "", 960],
  ]);
  for (const a of res.actors) {
    assert.equal(typeof a.verb, "string");
    assert.equal(typeof a.stuckTool, "string");
    assert.ok(Number.isFinite(a.age));
    assert.equal(a.key, a.session, "重複の無い人の key は session");
  }
  // 台帳の行に verb / stuckTool / age が無くても型を保つ（壊れた入力で undefined を運ばない）
  const bare = roomLayout({ agents: [] }, [{ key: "g", sessions: [{ session: "z", id: "z", state: "working" }] }]).actors[0];
  assert.deepEqual([bare.verb, bare.stuckTool, bare.age], ["", "", null]);
});

test("pxroom 第 2 波: actors の key は assign のキーと同じで部屋の中で一意（id では振らない・# を含む session でも衝突しない）", () => {
  const w = buildWorld(officeOf(3));
  const [a, b, c] = w.agents;
  const odd = { ...c, session: "s0#1" };   // 「s0 の 2 人目」と同じ字面の session
  const board = [{ key: "A", sessions: [a, a, a, odd] }, { key: "B", sessions: [b, { ...b, session: "", id: "p-same" },
    { ...b, session: "", id: "p-same" }] }];
  const res = roomLayout(w, board);
  const keys = res.actors.map((x) => x.key);
  assert.equal(new Set(keys).size, keys.length, `key が重複: ${keys}`);
  assert.deepEqual(keys, ["s0", "s0#1", "s0#2", "s0#1#1", "s1", "p-same", "p-same#1"]);
  for (const x of res.actors) assert.ok(Object.hasOwn(res.assign, x.key), `${x.key} が assign に無い`);
  // 前回を渡しても同じ key（歩行の帳簿と印の記録が人を取り違えない）
  assert.deepEqual(roomLayout(w, board, res.assign).actors.map((x) => x.key), keys);
});

// ── B1: 席の安定（前回の割り当てを渡すと、居続ける人は同じ枠に残る）────────────
const where = (res) => new Map(res.actors.map((a) => [a.session, `${a.zone}@${a.x},${a.y}`]));
const worldOf = (n, over) => buildWorld(officeOf(n, (i) => ({ age: 10 + i * 10, ...over(i) })));
const assertOthersStay = (label, before, after, moved) => {
  const a = where(before), b = where(after);
  for (const [s, pos] of a) {
    if (s === moved) continue;
    assert.equal(b.get(s), pos, `${label}: ${s} が ${pos} → ${b.get(s)} へ動いた（詰め直し）`);
  }
};

test("pxroom B1: s1 が ❗ になっても他の全員の席が変わらない", () => {
  const w1 = worldOf(6, () => ({}));
  const first = roomLayout(w1, boardOf(w1));
  const w2 = worldOf(6, (i) => (i === 1 ? { question: "進めていい？" } : {}));
  const second = roomLayout(w2, boardOf(w2), first.assign);
  assert.equal(second.actors.find((a) => a.session === "s1").zone, "queue");
  assertOthersStay("❗", first, second, "s1");
});

test("pxroom B1: s1 が休憩に入っても他の全員の席が変わらない", () => {
  const w1 = worldOf(6, () => ({}));
  const first = roomLayout(w1, boardOf(w1));
  const w2 = worldOf(6, (i) => (i === 1 ? { state: "resting" } : {}));
  const second = roomLayout(w2, boardOf(w2), first.assign);
  assert.equal(second.actors.find((a) => a.session === "s1").zone, "lounge");
  assertOthersStay("休憩", first, second, "s1");
});

test("pxroom B1: age が逆転しても（台帳の並びが変わっても）誰の席も変わらない", () => {
  const w1 = worldOf(6, () => ({}));
  const first = roomLayout(w1, boardOf(w1));
  // s0 と s4 の age を入れ替える＝トリアージ順が入れ替わる
  const w2 = worldOf(6, (i) => (i === 0 ? { age: 45 } : i === 4 ? { age: 5 } : {}));
  assert.notDeepEqual(boardOf(w2).map((g) => g.key), boardOf(w1).map((g) => g.key), "並びが変わっていない＝検査になっていない");
  const second = roomLayout(w2, boardOf(w2), first.assign);
  assertOthersStay("age", first, second, null);
});

test("pxroom B1: 前回が無いときは台帳の順に前から埋まる", () => {
  const w = worldOf(6, () => ({}));
  const res = roomLayout(w, boardOf(w));
  assert.deepEqual(res.actors.map((a) => a.seat), [0, 1, 2, 3, 4, 5]);
  assert.deepEqual(res.actors.map((a) => a.session), boardOf(w).map((g) => g.sessions[0].session));
});

test("pxroom B1: 帰った人の席は空けたまま、新しく来た人は空いている最小の席へ", () => {
  const w1 = worldOf(6, () => ({}));
  const first = roomLayout(w1, boardOf(w1));
  const seatOf2 = first.actors.find((a) => a.session === "s2").seat;
  // s2 が帰り、s9 が来る
  const emp = officeOf(6, (i) => ({ age: 10 + i * 10 })).employees.filter((e) => e.session !== "s2");
  emp.push({ session: "s9", dept: "p9", cwd: "/w9", state: "working", age: 1 });
  const w2 = buildWorld({ employees: emp });
  const second = roomLayout(w2, boardOf(w2), first.assign);
  assertOthersStay("入れ替わり", { actors: first.actors.filter((a) => a.session !== "s2") }, second, "s9");
  assert.equal(second.actors.find((a) => a.session === "s9").seat, seatOf2);
});

test("pxroom B1: ❗から戻った人は、もっと前の席が空いていても自分の席へ戻る", () => {
  const w1 = worldOf(6, () => ({}));
  const l1 = roomLayout(w1, boardOf(w1));
  const home = l1.actors.find((a) => a.session === "s3").seat;
  const w2 = worldOf(6, (i) => (i === 3 ? { question: "?" } : {}));
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.equal(l2.actors.find((a) => a.session === "s3").zone, "queue");
  // s0 が帰って席 0 が空く・同時に s3 が答えてもらって戻る・新人 s9 も来る
  const emp = officeOf(6, (i) => ({ age: 10 + i * 10 })).employees.filter((e) => e.session !== "s0");
  emp.push({ session: "s9", dept: "p9", cwd: "/w9", state: "working", age: 1 });
  const w3 = buildWorld({ employees: emp });
  const l3 = roomLayout(w3, boardOf(w3), l2.assign);
  assert.equal(l3.actors.find((a) => a.session === "s3").seat, home, "自分の席へ戻っていない");
  assert.equal(l3.actors.find((a) => a.session === "s9").seat, 0, "新人は空いている最小の席");
});

/** 机 0..n-1 に座った n 人から、s3 が ❗ になるまでの 2 フレーム（l2 の s3 は受付・home=3） */
const s3Calls = (n) => {
  const w1 = worldOf(n, () => ({}));
  const l1 = roomLayout(w1, boardOf(w1));
  const w2 = worldOf(n, (i) => (i === 3 ? { question: "?" } : {}));
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.equal(l2.actors.find((a) => a.session === "s3").zone, "queue");
  return l2;
};
const officeWithout = (n, gone, extra = []) => buildWorld({ employees: [
  ...officeOf(n, (i) => ({ age: 10 + i * 10 })).employees.filter((e) => !gone.includes(e.session)),
  ...extra] });
const newcomer = (session, over = {}) => ({ session, dept: `p-${session}`, cwd: `/w-${session}`, state: "working", age: 1, ...over });
const seatOf = (res, s) => res.actors.find((a) => a.session === s)?.seat;

test("pxroom B1 home: 新人なしで s0 が帰り、同時に s3 が ❗ から戻る → s3 は空いた席 0 ではなく自分の席 3", () => {
  // home を記録しない壊し方（assign の home を前回の値のまま）では、s3 は最小の空席 0 へ落ちる
  const l2 = s3Calls(6);
  const w3 = officeWithout(6, ["s0"]);
  const l3 = roomLayout(w3, boardOf(w3), l2.assign);
  assert.equal(seatOf(l3, "s3"), 3);
  assert.equal(l3.assign.s3.home, 3);
});

test("pxroom B1 home: 机が満席のとき、戻る人は同じフレームに台帳で先に来た新人より先に自分の机を取る", () => {
  // 9 席が埋まっている → s3 が ❗（空くのは席 3 だけ）→ s3 が戻るのと同時に新人 s9（age 1＝台帳の先頭）が来る。
  // 戻る人を先に置かないと、新人が「予約以外が尽きた」ので席 3 を取り、s3 が standby へ追い出される
  const l2 = s3Calls(9);
  const w3 = officeWithout(9, [], [newcomer("s9")]);
  assert.equal(boardOf(w3)[0].sessions[0].session, "s9", "新人が台帳の先頭に来ていない＝検査になっていない");
  const l3 = roomLayout(w3, boardOf(w3), l2.assign);
  assert.equal(seatOf(l3, "s3"), 3, "自分の机を新人に取られた");
  assert.equal(l3.actors.find((a) => a.session === "s9").zone, "standby");
});

test("pxroom B1 予約: ❗ の間に来た新人は、❗ の人の机を避けて座る → 戻ると自分の机", () => {
  const l2 = s3Calls(6);                                // 席 3 が空いている（s3 の予約）・6,7,8 は誰の机でもない
  const w3 = buildWorld({ employees: [...officeOf(6, (i) => ({ age: 10 + i * 10, ...(i === 3 ? { question: "?" } : {}) }))
    .employees, newcomer("s9")] });
  const l3 = roomLayout(w3, boardOf(w3), l2.assign);
  assert.equal(seatOf(l3, "s9"), 6, "新人が ❗ の人の机（予約）に座った");
  const w4 = officeWithout(6, [], [newcomer("s9")]);
  const l4 = roomLayout(w4, boardOf(w4), l3.assign);
  assert.equal(seatOf(l4, "s3"), 3, "戻ったら自分の机が取られていた");
  assert.equal(seatOf(l4, "s9"), 6, "新人が動いた");
});

test("pxroom B1 予約: 予約以外の机が尽きたときだけ予約の机を使う（部屋の外へは出さない）", () => {
  const l2 = s3Calls(9);                                // 空いているのは s3 の予約の席 3 だけ
  const w3 = buildWorld({ employees: [...officeOf(9, (i) => ({ age: 10 + i * 10, ...(i === 3 ? { question: "?" } : {}) }))
    .employees, newcomer("s9")] });
  const l3 = roomLayout(w3, boardOf(w3), l2.assign);
  assert.equal(seatOf(l3, "s9"), 3, "予約しか残っていないのに新人を standby にした");
  // s3 が戻ると自分の机は埋まっている → 机が無いので standby（最後尾）
  const w4 = officeWithout(9, [], [newcomer("s9")]);
  const l4 = roomLayout(w4, boardOf(w4), l3.assign);
  assert.equal(seatOf(l4, "s9"), 3);
  const s3 = l4.actors.find((a) => a.session === "s3");
  assert.equal(s3.zone, "standby");
  assert.equal(s3.slot, SLOTS.queue.length - 1);
});

test("pxroom B1 予約: 戻る人が 2 人・片方の机は取られている → もう片方は自分の机（予約を横取りされない）", () => {
  // 9 席満席 → s3 と s4 が ❗（席 3・4 が予約）→ 新人 s9 が来て、予約以外が無いので席 3 を使う
  const w1 = worldOf(9, () => ({}));
  const l1 = roomLayout(w1, boardOf(w1));
  const calling = (i) => (i === 3 || i === 4 ? { question: "?" } : {});
  const w2 = buildWorld({ employees: [...officeOf(9, (i) => ({ age: 10 + i * 10, ...calling(i) })).employees, newcomer("s9")] });
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.equal(seatOf(l2, "s9"), 3);
  // s3 と s4 が同時に戻る: s4 は自分の机 4。机を取られた s3 が s4 の予約を先に取って s4 を追い出さない
  const w3 = officeWithout(9, [], [newcomer("s9")]);
  const l3 = roomLayout(w3, boardOf(w3), l2.assign);
  assert.equal(seatOf(l3, "s4"), 4, "自分の机が空いていたのに、机を取られた人に横取りされた");
  assert.equal(l3.actors.find((a) => a.session === "s3").zone, "standby");
});

test("pxroom B1: 前回ありでも occupied は席番号の昇順（台帳の順に並ばない）", () => {
  const w1 = worldOf(6, () => ({}));
  const l1 = roomLayout(w1, boardOf(w1));
  // age を逆にする＝台帳の順が席の逆順になる
  const w2 = worldOf(6, (i) => ({ age: 100 - i * 10 }));
  assert.equal(boardOf(w2)[0].sessions[0].session, "s5", "台帳の順が逆になっていない＝検査になっていない");
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.deepEqual(l2.occupied, [0, 1, 2, 3, 4, 5]);
});

test("pxroom B1: 同じ session が 2 つのグループに出ても 2 体として重ならず、次回も同じ枠", () => {
  const w = worldOf(3, () => ({}));
  const twin = w.agents[0];
  const board = [{ key: "A", sessions: [twin, w.agents[1]] }, { key: "B", sessions: [twin, w.agents[2]] }];
  const l1 = roomLayout(w, board);
  assert.equal(l1.actors.length, 4);
  assert.equal(new Set(l1.actors.map((a) => `${a.x},${a.y}`)).size, 4, "同じ session の 2 体が重なった");
  assert.deepEqual(Object.keys(l1.assign).sort(), [twin.session, `${twin.session}#1`, "s1", "s2"].sort());
  // 前回を渡しても 2 体とも同じ枠（片方の割り当てが上書きされて入れ替わらない）
  const l2 = roomLayout(w, board, l1.assign);
  assert.deepEqual(l2.actors.map((a) => `${a.projectKey}:${a.seat}`), l1.actors.map((a) => `${a.projectKey}:${a.seat}`));
});

test("pxroom B1: overflow の人も割り当てを持ち越す（机の予約 home を失わない）", () => {
  // 確認待ち 10 人（受付 9 枠）→ 列の最後の s8（机 8 の人）が部屋の外。その人の home は次回へ残る
  const w1 = worldOf(10, () => ({}));
  const l1 = roomLayout(w1, boardOf(w1));            // 机 9（s0..s8）・standby 1（s9）
  const w2 = worldOf(10, (i) => ({ question: "?", ...(i === 9 ? { age: 1 } : {}) }));
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.equal(l2.overflow, 1);
  assert.equal(Object.keys(l2.assign).length, 10, "overflow の人の割り当てが消えた");
  assert.deepEqual({ ...l2.assign.s8 }, { zone: "overflow", slot: -1, home: 8, line: "queue", rank: 9 });
  // 全員が答えてもらって戻る → s8 は自分の机 8（台帳で先の s9 に取られない）
  const w3 = worldOf(10, (i) => (i === 9 ? { age: 1 } : {}));
  const l3 = roomLayout(w3, boardOf(w3), l2.assign);
  assert.equal(seatOf(l3, "s8"), 8);
  assert.equal(l3.actors.find((a) => a.session === "s9").zone, "standby");
});

test("pxroom B1 予約: 部屋の外（overflow）に居続ける人の机も予約 → 間に来た新人は避けて座り、戻ると自分の机", () => {
  // s0..s5 が机 0..5・c0..c8 が受付を満員にしている → s3 が ❗＝列の 10 人目で部屋の外
  const callers = Array.from({ length: 9 }, (_, i) => [`c${i}`, 1 + i, { question: "?" }]);
  const desks = (call3) => Array.from({ length: 6 }, (_, i) => [`s${i}`, 100 + i * 10, i === 3 && call3 ? { question: "?" } : {}]);
  const w1 = callerWorld([...desks(false), ...callers]);
  const l1 = roomLayout(w1, boardOf(w1));
  const w2 = callerWorld([...desks(true), ...callers]);
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.equal(l2.assign.s3.zone, "overflow", "前提: s3 が部屋の外に居ない＝検査になっていない");
  // 部屋の外のまま 1 フレーム経ったところで新人 n が来る＝前回も overflow の人の予約を見る場面
  const w3 = callerWorld([...desks(true), ...callers, ["n", 0]]);
  const l3 = roomLayout(w3, boardOf(w3), l2.assign);
  assert.equal(l3.assign.s3.zone, "overflow");
  assert.equal(seatOf(l3, "n"), 6, "新人が部屋の外に居る人の机（予約）に座った");
  const w4 = callerWorld([...desks(false), ...callers, ["n", 0]]);
  const l4 = roomLayout(w4, boardOf(w4), l3.assign);
  assert.equal(seatOf(l4, "s3"), 3, "戻ったら自分の机が取られていた");
  assert.equal(seatOf(l4, "n"), 6);
});

test("pxroom B1: 壊れた前回（同じ枠の取り合い・範囲外・知らない区分）でも重ならず決定論", () => {
  const w = worldOf(5, () => ({}));
  const clean = roomLayout(w, boardOf(w));
  const junk = { s1: { zone: "desk", slot: 0 }, s0: { zone: "desk", slot: 0 }, s2: { zone: "nope", slot: 99 },
    s3: null, s4: "x" };
  const res = roomLayout(w, boardOf(w), junk);
  const coords = res.actors.map((a) => `${a.x},${a.y}`);
  assert.equal(new Set(coords).size, coords.length);
  assert.equal(JSON.stringify(res), JSON.stringify(roomLayout(w, boardOf(w), junk)));
  assert.equal(res.actors.length, clean.actors.length);
});

test("pxroom B1: 壊れた前回が standby の同じ枠を 2 人に割り当てていても重ねない", () => {
  const w = worldOf(12, () => ({}));   // 机 9・standby 3
  const junk = { s9: { zone: "standby", slot: 4 }, s10: { zone: "standby", slot: 4 }, s11: { zone: "standby", slot: 4 } };
  const res = roomLayout(w, boardOf(w), junk);
  const sb = zones(res, "standby").map((a) => `${a.x},${a.y}`);
  assert.equal(sb.length, 3);
  assert.equal(new Set(sb).size, 3, `standby が同じ枠に重なった: ${sb}`);
});

test("pxroom B3: 確認待ちが受付の前を取る（standby は後ろへ下がる）", () => {
  // 働く 9 人・休憩 4 人（ソファ 2・standby 2）・確認待ち 2 人
  const over = (i) => (i < 2 ? { question: "?" } : i >= 11 ? { state: "resting" } : {});
  const res = layout(15, over);
  const q = zones(res, "queue").map((a) => a.slot);
  const s = zones(res, "standby").map((a) => a.slot);
  assert.deepEqual(q.sort(), [0, 1]);
  assert.ok(s.length === 2 && Math.min(...s) > Math.max(...q), `standby ${s} が確認待ち ${q} より前`);
  assert.deepEqual(s.sort((x, y) => x - y), [SLOTS.queue.length - 2, SLOTS.queue.length - 1], "standby は最後尾から");
});

// ── 受付の列: 確認待ちは前から・standby は最後尾から。列として詰める ─────────────────
const callerWorld = (sessions) => buildWorld({ employees: sessions.map(([session, age, over = {}]) => ({
  session, dept: session, cwd: `/w/${session}`, state: "working", age, ...over })) });
const slotsOf = (res, zone) => Object.fromEntries(zones(res, zone).map((a) => [a.session, a.slot]));

test("pxroom 受付の列: 抜けた人より前は動かず、後ろだけが 1 つずつ前へ・新しく呼んだ人は最後に付く", () => {
  const w1 = worldOf(6, (i) => (i < 4 ? { question: "?" } : {}));
  const l1 = roomLayout(w1, boardOf(w1));
  assert.deepEqual(slotsOf(l1, "queue"), { s0: 0, s1: 1, s2: 2, s3: 3 });
  // s1 が答えてもらった: s0 は動かない・s2 と s3 が 1 つずつ前へ
  const w2 = worldOf(6, (i) => (i < 4 && i !== 1 ? { question: "?" } : {}));
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.deepEqual(slotsOf(l2, "queue"), { s0: 0, s2: 1, s3: 2 });
  // s1 と s5 がまた呼ぶ: 台帳では s1 が s2 より前だが、列の最後に付く（割り込まない）
  const w3 = worldOf(6, (i) => (i < 4 || i === 5 ? { question: "?" } : {}));
  const l3 = roomLayout(w3, boardOf(w3), l2.assign);
  assert.deepEqual(slotsOf(l3, "queue"), { s0: 0, s2: 1, s3: 2, s1: 3, s5: 4 });
  // 先頭の s0 が抜けた: 全員が 1 つずつ前へ（最後尾が先頭へ飛ばない）
  const w4 = worldOf(6, (i) => (i >= 1 && i < 4) || i === 5 ? { question: "?" } : {});
  const l4 = roomLayout(w4, boardOf(w4), l3.assign);
  assert.deepEqual(slotsOf(l4, "queue"), { s2: 0, s3: 1, s1: 2, s5: 3 });
});

test("pxroom 受付の列: age が入れ替わっても（台帳の並びが変わっても）確認待ちの並びは変わらない", () => {
  const w1 = worldOf(4, (i) => (i < 3 ? { question: "?" } : {}));
  const l1 = roomLayout(w1, boardOf(w1));
  const w2 = worldOf(4, (i) => (i === 0 ? { question: "?", age: 999 } : i < 3 ? { question: "?" } : {}));
  assert.notDeepEqual(boardOf(w2).map((g) => g.key), boardOf(w1).map((g) => g.key), "並びが変わっていない＝検査になっていない");
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.deepEqual(slotsOf(l2, "queue"), slotsOf(l1, "queue"));
});

test("pxroom 受付の列: standby は最後尾から逆向き・確認待ちの人数が変わっても動かない", () => {
  const base = Array.from({ length: 12 }, (_, i) => [`s${i}`, 10 + i * 10]);   // 机 9・standby 3
  const w1 = callerWorld(base);
  const l1 = roomLayout(w1, boardOf(w1));
  const sb = { s9: 8, s10: 7, s11: 6 };
  assert.deepEqual(slotsOf(l1, "standby"), sb);
  // 呼んでいる人が 3 人来る → 前から 0,1,2。standby は動かない
  const w2 = callerWorld([...base, ["c0", 5, { question: "?" }], ["c1", 6, { question: "?" }], ["c2", 7, { question: "?" }]]);
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.deepEqual(slotsOf(l2, "queue"), { c0: 0, c1: 1, c2: 2 });
  assert.deepEqual(slotsOf(l2, "standby"), sb);
  // c0 が帰る → c1・c2 が 1 つずつ前へ。standby は動かない
  const w3 = callerWorld([...base, ["c1", 6, { question: "?" }], ["c2", 7, { question: "?" }]]);
  const l3 = roomLayout(w3, boardOf(w3), l2.assign);
  assert.deepEqual(slotsOf(l3, "queue"), { c1: 0, c2: 1 });
  assert.deepEqual(slotsOf(l3, "standby"), sb);
  // 全員答えてもらって帰る → standby は動かない
  const l4 = roomLayout(w1, boardOf(w1), l3.assign);
  assert.deepEqual(slotsOf(l4, "standby"), sb);
});

test("pxroom 受付の列: age が入れ替わっても standby の並びは変わらない", () => {
  const w1 = worldOf(12, () => ({}));
  const l1 = roomLayout(w1, boardOf(w1));
  const w2 = worldOf(12, (i) => (i === 9 ? { age: 999 } : {}));
  assert.notDeepEqual(boardOf(w2).map((g) => g.key), boardOf(w1).map((g) => g.key), "並びが変わっていない＝検査になっていない");
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.deepEqual(slotsOf(l2, "standby"), slotsOf(l1, "standby"));
  assert.deepEqual(slotsOf(l2, "standby"), { s9: 8, s10: 7, s11: 6 });
});

test("pxroom 受付の列: 両端から埋めて出会ったら、それ以上は overflow（確認待ちが優先・standby は列の後ろから溢れる）", () => {
  const base = Array.from({ length: 14 }, (_, i) => [`s${i}`, 10 + i * 10]);   // 机 9・standby 5（8..4）
  const w1 = callerWorld(base);
  const l1 = roomLayout(w1, boardOf(w1));
  assert.deepEqual(slotsOf(l1, "standby"), { s9: 8, s10: 7, s11: 6, s12: 5, s13: 4 });
  const callers = (m) => Array.from({ length: m }, (_, i) => [`c${i}`, 1 + i, { question: "?" }]);
  // 4 人呼ぶ → 0..3 と 4..8 で丁度埋まる（overflow 0）
  const w2 = callerWorld([...base, ...callers(4)]);
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.equal(l2.overflow, 0);
  assert.deepEqual(slotsOf(l2, "standby"), slotsOf(l1, "standby"));
  // 6 人呼ぶ → standby の列の後ろ 2 人（s13・s12）が部屋の外へ。前の 3 人は動かない
  const w3 = callerWorld([...base, ...callers(6)]);
  const l3 = roomLayout(w3, boardOf(w3), l2.assign);
  assert.deepEqual(slotsOf(l3, "queue"), { c0: 0, c1: 1, c2: 2, c3: 3, c4: 4, c5: 5 });
  assert.deepEqual(slotsOf(l3, "standby"), { s9: 8, s10: 7, s11: 6 });
  assert.equal(l3.overflow, 2);
  assert.deepEqual({ queue: l3.counts.queue, standby: l3.counts.standby }, { queue: 6, standby: 3 });
  // 部屋の外の 2 人は standby の列の順位を持ち越す。c4・c5 が帰り、同時に台帳で先に来る新人 n が来る →
  // 空いた 2 枠へはあふれた順に s12・s13 が戻り、新人 n は列の最後（部屋の外）
  assert.deepEqual([l3.assign.s12, l3.assign.s13].map((v) => `${v.line}#${v.rank}`), ["standby#3", "standby#4"]);
  const w4 = callerWorld([...base, ...callers(4), ["n", 0]]);
  const l4 = roomLayout(w4, boardOf(w4), l3.assign);
  assert.deepEqual(slotsOf(l4, "standby"), { s9: 8, s10: 7, s11: 6, s12: 5, s13: 4 }, "新人があふれた人より先に入った");
  assert.equal(l4.assign.n.zone, "overflow");
});

test("pxroom 受付の列: 9 枠を超えて部屋の外へあふれた確認待ちも列の順を失わない（後から呼んだ人が先に入らない）", () => {
  const ten = Array.from({ length: 10 }, (_, i) => [`c${i}`, 10 + i, { question: "?" }]);
  const w1 = callerWorld(ten);
  const l0 = roomLayout(w1, boardOf(w1));
  const l1 = roomLayout(w1, boardOf(w1), l0.assign);   // 部屋の外のまま 1 フレーム待つ
  assert.deepEqual({ ...l1.assign.c9 }, { zone: "overflow", slot: -1, home: -1, line: "queue", rank: 9 });
  // c0 が答えてもらって抜け、同時に n（台帳の先頭）が呼ぶ → 空いた 1 枠は前から並んでいた c9 のもの
  const w2 = callerWorld([["c0", 10], ...ten.slice(1), ["n", 1, { question: "?" }]]);
  assert.equal(boardOf(w2)[0].sessions[0].session, "n", "n が台帳の先頭に来ていない＝検査になっていない");
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.deepEqual(slotsOf(l2, "queue"), Object.fromEntries(ten.slice(1).map(([s], i) => [s, i])), "後から呼んだ n が先に入った");
  assert.deepEqual({ ...l2.assign.n }, { zone: "overflow", slot: -1, home: -1, line: "queue", rank: 9 });
});

test("pxroom 受付の列: 机が空いたら standby の列の先頭（最後の枠）から座り、残りは最後尾の側へ詰める", () => {
  const w1 = worldOf(12, () => ({}));
  const l1 = roomLayout(w1, boardOf(w1));
  // s0 が帰り、台帳で先に来る新人 n（age 1）が来る
  const emp = officeOf(12, (i) => ({ age: 10 + i * 10 })).employees.filter((e) => e.session !== "s0");
  emp.push({ session: "n", dept: "pn", cwd: "/wn", state: "working", age: 1 });
  const w2 = buildWorld({ employees: emp });
  const l2 = roomLayout(w2, boardOf(w2), l1.assign);
  assert.equal(l2.actors.find((a) => a.session === "s9").seat, 0, "standby の先頭が空いた机に座っていない（新人が割り込んだ）");
  assert.deepEqual(slotsOf(l2, "standby"), { s10: 8, s11: 7, n: 6 });
});

test("pxroom 受付の列: 壊れた前回が確認待ちの同じ枠を 2 人に割り当てていても重ねない", () => {
  const w = worldOf(3, () => ({ question: "?" }));
  const junk = { s0: { zone: "queue", slot: 0 }, s1: { zone: "queue", slot: 0 }, s2: { zone: "queue", slot: 0 } };
  const res = roomLayout(w, boardOf(w), junk);
  const q = zones(res, "queue");
  assert.equal(q.length, 3);
  assert.equal(new Set(q.map((a) => `${a.x},${a.y}`)).size, 3, "確認待ちが同じ枠に重なった");
  assert.deepEqual(q.map((a) => a.slot).sort(), [0, 1, 2]);
});

// ── 性質の検査: ランダムな状態の列で、受付の意味と席の安定が崩れないこと ──────────
function randomOffice(rnd, n) {
  const ids = Array.from({ length: n }, (_, i) => i);
  return {
    employees: ids.map((i) => {
      const e = { session: `s${i}`, dept: `p${i % 7}`, cwd: `/w${i % 7}`, age: Math.floor(rnd() * 500) };
      e.state = ["working", "waiting", "idle", "resting"][Math.floor(rnd() * 4)];
      if (rnd() < 0.2) e.question = "?";
      if (rnd() < 0.15) e.minions = 1 + Math.floor(rnd() * 3);
      if (rnd() < 0.1) e.external = { provider: "openclaw" };
      if (rnd() < 0.2) e.kind = "think";
      e.vendor = ["claude", "codex", "openclaw", "zzz", ""][Math.floor(rnd() * 5)];
      return e;
    }),
  };
}
function checkInvariants(res, w, label) {
  const qCoords = new Set(SLOTS.queue.map((s) => `${s.x},${s.y}`));
  const LINE = SLOTS.queue.length;
  const people = w.agents.length;
  const src = new Map(w.agents.map((x) => [x.session, x]));
  assert.equal(src.size, people, `${label}: session が一意でない（入力との突き合わせが引き違える）`);
  assert.equal(res.actors.length + res.overflow, people, `${label}: 人数が合わない`);
  // counts は区分ごとに実際の人数と一致（合計だけでは standby を queue に数える嘘を見逃す）
  assert.deepEqual(Object.keys(res.counts).sort(), ["desk", "external", "lounge", "meeting", "queue", "standby"]);
  for (const z of Object.keys(res.counts)) {
    assert.equal(res.counts[z], res.actors.filter((a) => a.zone === z).length, `${label}: counts.${z}`);
  }
  // occupied は机に居る人の席番号の昇順
  assert.deepEqual(res.occupied, res.actors.filter((a) => a.zone === "desk").map((a) => a.seat).sort((x, y) => x - y),
    `${label}: occupied`);
  assert.deepEqual(res.occupied, [...res.occupied].sort((x, y) => x - y), `${label}: occupied が昇順でない`);
  // 割り当ては台帳の全員ぶん（部屋の外の人は overflow）
  assert.equal(Object.keys(res.assign).length, people, `${label}: assign の人数`);
  assert.equal(Object.values(res.assign).filter((v) => v.zone === "overflow").length, res.overflow, `${label}: assign の overflow`);
  const seen = new Set();
  for (const a of res.actors) {
    const c = `${a.x},${a.y}`;
    assert.ok(!seen.has(c), `${label}: ${c} に 2 体`);
    seen.add(c);
    // 受付に居る＝確認待ちか standby 印付きのどちらか
    if (qCoords.has(c)) {
      assert.ok((a.zone === "queue" && a.attention) || (a.zone === "standby" && !a.attention),
        `${label}: 受付に ${a.zone}/attention=${a.attention} が立っている`);
    }
    if (a.zone === "queue") assert.equal(a.attention, true, `${label}: queue なのに attention でない`);
    if (a.zone === "standby") assert.equal(a.attention, false, `${label}: standby が挙手している`);
    if (a.want === "queue") assert.equal(a.zone, "queue", `${label}: 確認待ちが受付以外に居る`);
    if (a.want === "external") assert.ok(["external", "standby", "queue"].includes(a.zone), `${label}: 外部社員が ${a.zone}`);
    // ❗ は入力（world の agent）と突き合わせる。出力の attention は zone から作るので、出力同士で比べると空回りする。
    // 呼んでいる人は部屋のどこに居ても ❗ の絵（受付の列かラック前＝外部社員のあふれも standby に落とさない）・
    // 呼んでいない人は ❗ を出さない
    const call = needsAttention(src.get(a.session));
    if (call) assert.ok(a.zone === "queue" || a.zone === "external", `${label}: 呼んでいる ${a.session}（want ${a.want}）が ${a.zone} に居る＝❗ が絵から消える`);
    assert.equal(a.attention, call, `${label}: ${a.session} の attention が入力と違う`);
    if (a.zone === "meeting") assert.equal(a.want, "meeting", `${label}: 会議室に ${a.want} の人`);
  }
  const q = res.actors.filter((a) => a.zone === "queue").map((a) => a.slot).sort((x, y) => x - y);
  const s = res.actors.filter((a) => a.zone === "standby").map((a) => a.slot).sort((x, y) => x - y);
  // 確認待ちは前から詰まっている（k 人なら 0..k-1）・standby は最後尾から詰まっている（m 人なら LINE-m..LINE-1）
  assert.deepEqual(q, q.map((_, i) => i), `${label}: 確認待ちが前詰めでない`);
  assert.deepEqual(s, s.map((_, i) => LINE - s.length + i), `${label}: standby が最後尾から詰まっていない`);
  assert.ok(q.length + s.length <= LINE, `${label}: 受付の定員超え`);
  // 部屋の外に人が居るなら、受付は満員（両端が出会ってから溢れる）
  if (res.overflow > 0) assert.equal(q.length + s.length, LINE, `${label}: 受付に空きがあるのに overflow`);
}

test("pxroom 性質: どの状態でも受付は確認待ちか standby だけ・席は動かない・受付は列として詰める", () => {
  const rnd = lcg(20260924);
  const seen = { stay: 0, temp: 0, queueKept: 0, queueShift: 0, queueNew: 0, standbyKept: 0, standbyShift: 0,
    both: 0, overflow: 0, extLine: 0, outBack: 0, outBackVsFresh: 0 };
  for (let round = 0; round < 80; round += 1) {
    const n = 1 + Math.floor(rnd() * 26);
    let office = randomOffice(rnd, n);
    let w = buildWorld(office);
    let prev = roomLayout(w, boardOf(w));
    checkInvariants(prev, w, `round ${round} 初回`);
    for (let step = 0; step < 6; step += 1) {
      // 何人かの状態だけを変える（出入りは別のテスト）。age も時々入れ替える＝台帳の並びが変わる
      office = { employees: office.employees.map((e) => (rnd() < 0.25 ? randomOffice(rnd, 1).employees
        .map((r) => ({ ...r, session: e.session, dept: e.dept, cwd: e.cwd }))[0]
        : { ...e, age: rnd() < 0.15 ? Math.floor(rnd() * 500) : e.age + 1 })) };
      const w2 = buildWorld(office);
      const next = roomLayout(w2, boardOf(w2), prev.assign);
      const label = `round ${round} step ${step}`;
      checkInvariants(next, w2, label);
      const before = new Map(prev.actors.map((a) => [a.session, a]));
      const inZone = (res, z) => res.actors.filter((a) => a.zone === z);
      const nowQ = new Set(inZone(next, "queue").map((a) => a.session));
      const nowS = new Set(inZone(next, "standby").map((a) => a.session));
      if (nowQ.size && nowS.size) seen.both += 1;
      if (next.overflow > 0) seen.overflow += 1;
      seen.extLine += inZone(next, "queue").filter((a) => a.want === "external").length;
      const keptQ = [], newQ = [], keptS = [], newS = [];
      // 部屋の外から列へ戻った人（前回 overflow・同じ列）は [持ち越した順位, 今の枠]。列に初めて並んだ人は枠だけ
      const backQ = [], freshQ = [], backS = [], freshS = [];
      const outRank = (a, line) => {
        const v = prev.assign[a.session];
        return v?.zone === "overflow" && v.line === line ? v.rank : -1;
      };
      for (const a of next.actors) {
        const b = before.get(a.session);
        if (a.zone === "queue" && b?.zone !== "queue") {
          if (outRank(a, "queue") >= 0) backQ.push([outRank(a, "queue"), a.slot]); else freshQ.push(a.slot);
        }
        if (a.zone === "standby" && b?.zone !== "standby") {
          if (outRank(a, "standby") >= 0) backS.push([outRank(a, "standby"), a.slot]); else freshS.push(a.slot);
        }
        // 受付の列: 前回も列に居た人は「前で抜けた人数」だけ前へ（前で誰も抜けなければ同じ枠）
        if (a.zone === "queue") {
          if (b?.zone === "queue") {
            const gone = inZone(prev, "queue").filter((c) => c.slot < b.slot && !nowQ.has(c.session)).length;
            assert.equal(a.slot, b.slot - gone, `${label}: 確認待ち ${a.session} が queue#${b.slot} → #${a.slot}（前で抜けたのは ${gone} 人）`);
            seen.queueKept += 1;
            if (gone) seen.queueShift += 1;
            keptQ.push(a.slot);
          } else newQ.push(a.slot);
        }
        // standby: 最後尾から。前回も standby の人は「後ろ（最後尾の側）で抜けた人数」だけ最後尾へ
        if (a.zone === "standby") {
          if (b?.zone === "standby") {
            const gone = inZone(prev, "standby").filter((c) => c.slot > b.slot && !nowS.has(c.session)).length;
            assert.equal(a.slot, b.slot + gone, `${label}: standby ${a.session} が #${b.slot} → #${a.slot}（最後尾の側で抜けたのは ${gone} 人）`);
            seen.standbyKept += 1;
            if (gone) seen.standbyShift += 1;
            keptS.push(a.slot);
          } else newS.push(a.slot);
        }
        if (!b || b.want !== a.want) continue;
        // 本来の場所に居続けた人は同じ枠（机・ソファ・会議室・外部）
        if (["desk", "lounge", "meeting", "external"].includes(b.zone) && b.zone === b.want) {
          assert.equal(`${a.zone}#${a.slot}`, `${b.zone}#${b.slot}`,
            `${label}: ${a.session}（${b.want}）が ${b.zone}#${b.slot} から動いた`);
          seen.stay += 1;
        }
        // 仮住まいの机に居た人は、同じ机か本来の場所へ移っただけ
        if (b.zone === "desk" && b.want !== "desk") {
          assert.ok((a.zone === "desk" && a.slot === b.slot) || a.zone === a.want,
            `${label}: ${a.session} が仮住まいの机から別の机へ動いた`);
          seen.temp += 1;
        }
      }
      // 新しく並んだ人は列の後ろに付く（割り込まない）
      if (keptQ.length && newQ.length) assert.ok(Math.max(...keptQ) < Math.min(...newQ), `${label}: 確認待ちの列に割り込んだ`);
      if (keptS.length && newS.length) assert.ok(Math.min(...keptS) > Math.max(...newS), `${label}: standby の列に割り込んだ`);
      seen.queueNew += newQ.length && keptQ.length ? 1 : 0;
      // 部屋の外から戻った人は、あふれた順に・初めて並んだ人より前へ（確認待ちは前＝小さい枠、standby は最後尾側＝大きい枠）
      const byRank = (list) => [...list].sort((x, y) => x[0] - y[0]).map(([, s]) => s);
      const inc = byRank(backQ), dec = byRank(backS);
      assert.deepEqual(inc, [...inc].sort((x, y) => x - y), `${label}: 部屋の外から戻った確認待ちの順が入れ替わった`);
      assert.deepEqual(dec, [...dec].sort((x, y) => y - x), `${label}: 部屋の外から戻った standby の順が入れ替わった`);
      if (inc.length && freshQ.length) assert.ok(Math.max(...inc) < Math.min(...freshQ), `${label}: 後から呼んだ人が部屋の外の人より先に入った`);
      if (dec.length && freshS.length) assert.ok(Math.min(...dec) > Math.max(...freshS), `${label}: 新しく来た人が部屋の外の人より先に standby へ入った`);
      seen.outBack += backQ.length + backS.length;
      seen.outBackVsFresh += (inc.length && freshQ.length) || (dec.length && freshS.length) ? 1 : 0;
      prev = next;
    }
  }
  // 空振り防止: 検査が実際に踏んだ回数の下限（randomOffice を変えて自明な状態しか出なくなったら赤）
  // （実測 2026-09-24: stay 3760・temp 219・queueKept 1020・queueShift 277・queueNew 164・standbyKept 327・
  //   standbyShift 180・both 190・overflow 42・extLine 7・outBack 34・outBackVsFresh 13。下限はおよそ 1/3）
  const floor = { stay: 1200, temp: 70, queueKept: 340, queueShift: 90, queueNew: 55, standbyKept: 115, standbyShift: 55,
    both: 60, overflow: 14, extLine: 2, outBack: 11, outBackVsFresh: 4 };
  for (const [k, min] of Object.entries(floor)) {
    assert.ok(seen[k] >= min, `性質の検査が ${k} を ${seen[k]} 回しか踏んでいない（下限 ${min}）＝検査になっていない`);
  }
});

// ── B2: 家具の配置表と人の枠 ────────────────────────────────────────────
/** 意図して足元を重ねてある家具の組（いまは無い。足すときは理由と一緒に） */
const INTENDED = new Set([]);

test("pxroom B2: 家具の表の形（id は一意・層と塗りは決まった語彙・足元は部屋の中）", () => {
  const ids = FURNITURE.map((f) => f.id);
  assert.equal(new Set(ids).size, ids.length, "id が重複している");
  for (const f of FURNITURE) {
    assert.ok(["back", "front"].includes(f.layer), `${f.id}: layer=${f.layer}`);
    assert.ok([null, "glass", "floor", "paper"].includes(f.paint), `${f.id}: paint=${f.paint}`);
    assert.ok(Number.isInteger(f.x) && Number.isInteger(f.y), `${f.id}: 座標が整数でない`);
    // solid（foot の外で床を塞ぐ矩形）は foot のある行だけ・形は foot と同じ
    if (f.solid !== undefined) {
      assert.ok(f.foot, `${f.id}: foot が無いのに solid がある（footRects が読まない）`);
      assert.ok(Array.isArray(f.solid) && f.solid.length > 0 && Object.isFrozen(f.solid), `${f.id}: solid=${f.solid}`);
    }
    for (const r of footRects(f)) {
      const [x, y, w, h] = r;
      assert.ok(r.length === 4 && [x, y, w, h].every(Number.isInteger) && w > 0 && h > 0, `${f.id}: 足元 ${r}`);
      assert.ok(x >= 0 && y >= 0 && x + w <= ROOM.w && y + h <= ROOM.h, `${f.id}: 足元 ${r} が部屋の外`);
      assert.ok(Object.isFrozen(r), `${f.id}: 足元 ${r} が凍結されていない`);
    }
  }
  // 机ひと組（机・椅子・モニタ）が全席にある
  for (const d of DESKS) {
    for (const kind of ["desk", "chair", "monitor"]) {
      assert.equal(FURNITURE.filter((f) => f.id === `${kind}${d.i}` && f.seat === d.i).length, 1, `${kind}${d.i}`);
    }
  }
});

/**
 * 床に足の無い家具（foot: null）の許可リスト。foot が null の行は重なり・めり込み・経路の検査を全部すり抜けるので、
 * 理由の無い null は赤にする。天板の上の小物はここに書かず、表の `on`（載せ台の id）で示す。
 */
const FOOTLESS = Object.freeze({
  window0: "壁掛け（窓）", window1: "壁掛け（窓）", window2: "壁掛け（窓）",
  wallClock: "壁掛け（時計）", bulletin: "壁掛け（掲示板）",
  bookshelf: "壁掛け（本棚・R110 の用事「読む」の目的地＝足元の立ち位置は ERRAND_SPOTS が持つ）",
  rug: "敷物（平らで上を歩ける）",
  pendant: "吊り灯（天井から吊る・床に届かない）",
  door: "扉（手前の壁の帯に嵌まる・人が通り抜ける＝床の障害物ではない）",
});
const WALL_BAND = 15;   // y 0..14 が壁（pxroom.js の間取りの注記）

test("pxroom B2: 足元の無い家具は、理由つきの許可リストか天板の上（on）だけ", () => {
  const byId = new Map(FURNITURE.map((f) => [f.id, f]));
  for (const [id, why] of Object.entries(FOOTLESS)) {
    assert.ok(byId.has(id), `許可リストの ${id} が表に無い（リストを掃除する）`);
    assert.equal(byId.get(id).foot, null, `許可リストの ${id}（${why}）に足元がある`);
    assert.equal(byId.get(id).on, undefined, `${id} は許可リストと on の両方に居る`);
    if (why.startsWith("壁掛け")) assert.ok(byId.get(id).y < WALL_BAND, `${id}（${why}）が壁の帯の外 y=${byId.get(id).y}`);
  }
  let on = 0;
  for (const f of FURNITURE) {
    if (f.foot) {
      assert.equal(f.on, undefined, `${f.id}: 足元があるのに on（載せ台）を持つ`);
      continue;
    }
    if (f.on !== undefined) {
      // 天板の上: 載せ台は足元を持つ行で、小物の左上は載せ台の足元の横幅の内・下端より上
      const base = byId.get(f.on);
      assert.ok(base?.foot, `${f.id}: 載せ台 ${f.on} が無いか足元が無い`);
      const [bx, by, bw, bh] = base.foot;
      assert.ok(f.x >= bx && f.x < bx + bw && f.y < by + bh, `${f.id}: (${f.x},${f.y}) が載せ台 ${f.on} ${base.foot} の上に無い`);
      if (f.seat !== undefined) assert.equal(base.seat, f.seat, `${f.id}: 席 ${f.seat} の小物が別の席の机 ${f.on} に載っている`);
      on += 1;
      continue;
    }
    assert.ok(Object.hasOwn(FOOTLESS, f.id), `${f.id}: foot が null（理由の無い逃げ道。床に立つ物なら足元を書く・壁掛けなどなら許可リストへ理由つきで）`);
  }
  assert.ok(on >= SEATS, `天板の上の小物が ${on} 件しか無い＝モニタの on が落ちた`);
});

test("pxroom B2: 光（glow）は純データ・部屋の中・天板の光は載せ台の足元の上", () => {
  const byId = new Map(FURNITURE.map((f) => [f.id, f]));
  const lit = FURNITURE.filter((f) => f.glow);
  // 給湯台の暖色・卓上灯・点いた画面 9 台の照り返し
  assert.deepEqual(lit.map((f) => f.id).sort(), ["coffeeStation", "taskLamp", ...DESKS.map((d) => `monitor${d.i}`)].sort());
  for (const f of lit) {
    assert.ok(Object.isFrozen(f.glow) && f.glow.every(Object.isFrozen), `${f.id}: glow が凍結されていない`);
    assert.ok(f.glow.length >= 1);
    for (const g of f.glow) {
      assert.equal(g.length, 6, `${f.id}: glow の形 ${g}`);
      const [dx, dy, w, h, alpha, step] = g;
      assert.ok([dx, dy, w, h, step].every(Number.isInteger) && w > 0 && h > 0 && step >= 0, `${f.id}: glow ${g}`);
      assert.ok(alpha > 0 && alpha <= 1, `${f.id}: glow の alpha ${alpha}`);
      const x = f.x + dx, y = f.y + dy;
      assert.ok(x >= 0 && y >= 0 && x + w <= ROOM.w && y + h <= ROOM.h, `${f.id}: glow (${x},${y}) ${w}×${h} が部屋の外`);
      // 天板の上の物の光は載せ台（机の天板・カウンター）の上に落ちる
      if (f.on) {
        const [bx, by, bw, bh] = byId.get(f.on).foot;
        assert.ok(x >= bx && y >= by && x + w <= bx + bw && y + h <= by + bh,
          `${f.id}: glow (${x},${y}) ${w}×${h} が載せ台 ${f.on} ${byId.get(f.on).foot} の外`);
      }
    }
  }
  // 家具を動かすと光も付いて動く（相対座標の約束）: 同じ形の 9 台のモニタは同じ glow
  const monitors = DESKS.map((d) => byId.get(`monitor${d.i}`).glow);
  assert.ok(monitors.every((g) => JSON.stringify(g) === JSON.stringify(monitors[0])), "モニタの glow が席ごとに違う");
});

test("pxroom B2: 家具の足元どうしが重ならない（solid を含む・同じ家具の矩形どうしも）", () => {
  const feet = FURNITURE.flatMap((f) => footRects(f).map((r, k) => ({ id: f.id, k, r })));
  for (let i = 0; i < feet.length; i += 1) {
    for (let j = i + 1; j < feet.length; j += 1) {
      const pair = `${feet[i].id}+${feet[j].id}`;
      if (INTENDED.has(pair)) continue;
      assert.ok(!overlap(feet[i].r, feet[j].r), `${pair} の足元（${feet[i].r} と ${feet[j].r}）が重なっている`);
    }
  }
});

test("pxroom B2: 人の枠は家具にめり込まない（座る・卓の向こうに立つ枠は at に書いた家具だけ許す）", () => {
  const byId = new Map(FURNITURE.map((f) => [f.id, f]));
  for (const [zone, list] of Object.entries(SLOTS)) {
    list.forEach((s, i) => {
      const label = `${zone}#${i}(${s.x},${s.y})`;
      for (const id of s.at) assert.ok(byId.has(id), `${label}: at の ${id} が表に無い`);
      const foot = footAt(s.x, s.y);
      for (const f of FURNITURE) {
        if (s.at.includes(f.id)) continue;
        for (const r of footRects(f)) assert.ok(!overlap(foot, r), `${label} の足が ${f.id} の足元 ${r} にめり込んでいる`);
      }
      // 壁（y<16）に頭を埋めない・部屋からはみ出さない
      assert.ok(s.x >= 0 && s.x + ROOM.robot.w <= ROOM.w && s.y >= 16 && s.y + ROOM.robot.h <= ROOM.h,
        `${label}: 部屋の外`);
    });
  }
});

test("pxroom B2: 人の枠どうしが重ならない（重なると 1 体ぶんの絵になって片方が消える）", () => {
  const all = Object.entries(SLOTS).flatMap(([z, list]) => list.map((s, i) => ({ ...s, label: `${z}#${i}` })));
  for (let i = 0; i < all.length; i += 1) {
    for (let j = i + 1; j < all.length; j += 1) {
      const a = [all[i].x, all[i].y, ROOM.robot.w, ROOM.robot.h];
      const b = [all[j].x, all[j].y, ROOM.robot.w, ROOM.robot.h];
      assert.ok(!overlap(a, b), `${all[i].label} と ${all[j].label} が重なっている`);
    }
  }
});

test("pxroom B2: 外部社員の枠はラック前のインフラ区画（右上）", () => {
  const racks = FURNITURE.filter((f) => ["rackOn", "rackOff", "locker"].includes(f.id));
  assert.equal(racks.length, 3);
  const left = Math.min(...racks.map((f) => f.foot[0]));
  const base = Math.max(...racks.map((f) => f.foot[1] + f.foot[3]));
  assert.ok(SLOTS.external.length >= 2 && SLOTS.external.length <= 3);
  for (const s of SLOTS.external) {
    assert.ok(s.x + ROOM.robot.w > left, `外部の枠 ${s.x} がラックの左に離れすぎ`);
    assert.ok(s.y + ROBOT_FOOT[1] >= base, `外部の枠 ${s.y} がラックの奥（めり込み）`);
    assert.ok(s.y + ROOM.robot.h <= 45, `外部の枠 ${s.y} が仕事場に食い込む`);
  }
});

test("pxroom 第 2 波: 扉は手前の壁の帯の上（足元なし・右寄り・受付のカウンターの外）", () => {
  const door = FURNITURE.filter((f) => f.tile === "door");
  assert.equal(door.length, 1, `扉が ${door.length} 枚`);
  const [d] = door;
  // 委譲の契約: 扉は論理 (150,156)・18×4（DOOR が正本＝design/room.js・px_lint・tiles のテストが突き合わせる）
  assert.deepEqual({ ...DOOR }, { x: 150, y: 156, w: 18, h: 4 });
  assert.ok(Object.isFrozen(DOOR) && Object.isFrozen(GLASS_OPENING));
  assert.deepEqual([d.x, d.y, d.layer, d.foot], [DOOR.x, DOOR.y, "back", null]);
  assert.equal(DOOR.y + DOOR.h, ROOM.h, "扉が手前の壁の帯（y 157..159＋床のマット 1 行）に嵌まっていない");
  assert.ok(DOOR.x > ROOM.w / 2 && DOOR.x + DOOR.w <= ROOM.w - 3, `扉 x ${DOOR.x}..${DOOR.x + DOOR.w - 1} が右寄りの壁の内でない`);
  const counter = FURNITURE.find((f) => f.id === "counter");
  assert.ok(DOOR.x >= counter.foot[0] + counter.foot[2], "扉がカウンターの前に在る（受付を越えて入ることになる）");
});

test("pxroom 第 2 波: 会議室の開口は手前の框の中で、どの家具の足元にも塞がれていない", () => {
  const glass = FURNITURE.find((f) => f.id === "glassWall");
  assert.equal(glass.tile, "glassWallDoor");
  const [ox, oy, ow, oh] = GLASS_OPENING;
  // 委譲の契約: タイル内 x 50..61（12 幅）・框（最下の 2 行＝足元の行）
  assert.deepEqual([ox - glass.x, ow, oy, oh], [50, 12, glass.foot[1], glass.foot[3]]);
  // 開口の左は foot の右端・右は solid の左端＝框が開口ぶんだけ切り分けてある
  assert.equal(glass.foot[0] + glass.foot[2], ox, "框の左（foot）が開口まで届いていない／開口に食い込む");
  assert.ok(glass.solid.some((r) => r[0] === ox + ow && r[1] === oy && r[3] === oh), "開口の右の框（solid）が無い");
  for (const f of FURNITURE) {
    for (const r of footRects(f)) assert.ok(!overlap(r, GLASS_OPENING), `${f.id} の足元 ${r} が会議室の開口を塞ぐ`);
  }
  // ロボの足（6 幅）が開口を通れる
  assert.ok(ow >= ROBOT_FOOT[2], `開口 ${ow} がロボの足 ${ROBOT_FOOT[2]} より狭い`);
});

test("pxroom 第 2 波 C3: 手前の什器の段（FRONT_BANDS）＝机 3 段・受付のカウンター・ソファの座面・会議卓", () => {
  assert.deepEqual(FRONT_BANDS.map((b) => b.key), ["meetTable", "sofaSeat", "desk0", "desk1", "desk2", "counter"]);
  // 手前の什器は全部、ちょうど 1 つの段に入る（段の中は FURNITURE の順）・奥の什器は入らない
  const front = FURNITURE.filter((f) => f.layer === "front").map((f) => f.id);
  const inBands = FRONT_BANDS.flatMap((b) => b.ids);
  assert.deepEqual([...inBands].sort(), [...front].sort(), "段に入らない（か 2 度入る）手前の什器がある");
  const order = new Map(FURNITURE.map((f, i) => [f.id, i]));
  const byId = new Map(FURNITURE.map((f) => [f.id, f]));
  for (const b of FRONT_BANDS) {
    assert.deepEqual(b.ids, [...b.ids].sort((x, y) => order.get(x) - order.get(y)), `${b.key}: 段の中の順が表の順でない`);
    // 机の段は同じ行の席の物だけ・小物は載せ台と同じ段
    for (const id of b.ids) {
      const f = byId.get(id);
      if (Number.isInteger(f.seat)) assert.equal(b.key, `desk${Math.floor(f.seat / ROOM.desk.cols)}`, `${id} の段`);
      if (f.on !== undefined) assert.ok(b.ids.includes(f.on), `${id} が載せ台 ${f.on} と別の段`);
    }
    // depth は段の家具の足元の下端
    const want = Math.max(...b.ids.flatMap((id) => footRects(byId.get(id)).map((r) => r[1] + r[3])));
    assert.equal(b.depth, want, `${b.key}: depth`);
    assert.ok(Object.isFrozen(b) && Object.isFrozen(b.ids));
  }
  assert.ok(FRONT_BANDS.every((b, i) => !i || FRONT_BANDS[i - 1].depth <= b.depth), "奥から手前の順でない");
  assert.ok(Object.isFrozen(FRONT_BANDS));
});

test("pxroom 第 2 波 C3: 重なり順の規則（足元の中心の y が depth より小さい人は段の奥）で、座る・卓の向こうの人は隠れ、通路の人は手前", () => {
  const footY = (y) => y + ROBOT_FOOT[1] + ROBOT_FOOT[3] / 2;
  const bandOf = new Map(FRONT_BANDS.flatMap((b) => b.ids.map((id) => [id, b])));
  let hidden = 0;
  for (const [zone, list] of Object.entries(SLOTS)) {
    list.forEach((s, i) => {
      for (const id of s.at) {
        const b = bandOf.get(id);
        if (!b) continue;                                   // 奥の什器（椅子）は背景＝順は関係ない
        assert.ok(footY(s.y) < b.depth, `${zone}#${i} (${s.x},${s.y}) が ${b.key} の手前に描かれる（腰から下が隠れない）`);
        hidden += 1;
      }
    });
  }
  assert.ok(hidden >= SLOTS.desk.length + SLOTS.lounge.length + SLOTS.meeting.length + 3, `隠れる枠が ${hidden}＝検査になっていない`);
  // 通路（机の段の間・ソファ／会議室の前）を歩く人（足元の中心＝pxwalk の LANES）は、すぐ奥の段より手前
  const [L0, L1, L2, L3] = LANES.y;
  for (const [lane, key] of [[L0, "sofaSeat"], [L0, "meetTable"], [L1, "desk0"], [L2, "desk1"], [L3, "desk2"]]) {
    const b = FRONT_BANDS.find((x) => x.key === key);
    assert.ok(lane >= b.depth, `通路 y ${lane} の人が ${key}（depth ${b.depth}）の奥に描かれる`);
  }
});

// ── B5: 個体色の添字 ─────────────────────────────────────────────────────
/** 今の ui/cockpit/room.js の tintFor（ここに写して、色を不用意に変えていないことの基準にする） */
const oldTint = (key, n) => {
  let h = 0;
  const s = String(key);
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h % n;
};

test("pxroom B5: tintHash は今の tintFor と同じハッシュ", () => {
  for (const k of ["", "cwd:/Users/a/project", "id:s1", "日本語のパス", "x".repeat(200)]) {
    assert.equal(tintHash(k) % 9, oldTint(k, 9), k);
  }
});

test("pxroom B5: 9 件なら色が重ならない（衝突のある組を含めて）", () => {
  const rnd = lcg(7);
  let collided = 0;
  for (let round = 0; round < 300; round += 1) {
    const size = 1 + Math.floor(rnd() * 9);
    const keys = Array.from({ length: size }, () => `cwd:/w/${Math.floor(rnd() * 1e6)}`);
    const uniq = [...new Set(keys)];
    if (new Set(uniq.map((k) => oldTint(k, 9))).size < uniq.length) collided += 1;
    const idx = tintIndex(keys, 9);
    const got = uniq.map((k) => idx.get(k));
    assert.ok(got.every((i) => Number.isInteger(i) && i >= 0 && i < 9), `範囲外: ${got}`);
    assert.equal(new Set(got).size, uniq.length, `重なった: ${uniq} → ${got}`);
  }
  assert.ok(collided > 20, `ハッシュが衝突する組が ${collided} 回しか出ていない＝検査になっていない`);
});

test("pxroom B5: 同じ入力なら同じ出力（並び順にも依らない）", () => {
  const keys = Array.from({ length: 9 }, (_, i) => `cwd:/p/${i * 7}`);
  const a = tintIndex(keys, 9);
  const b = tintIndex([...keys].reverse(), 9);
  assert.deepEqual([...a.entries()].sort(), [...b.entries()].sort());
  assert.deepEqual([...tintIndex(keys, 9).entries()], [...a.entries()]);
});

test("pxroom B5: 衝突が無ければ今の tintFor と同じ添字（既存の色を変えない）", () => {
  // 剰余が全部違う 9 件を選ぶ
  const byMod = new Map();
  for (let i = 0; byMod.size < 9; i += 1) {
    const k = `cwd:/proj/${i}`;
    if (!byMod.has(oldTint(k, 9))) byMod.set(oldTint(k, 9), k);
  }
  const keys = [...byMod.values()];
  const idx = tintIndex(keys, 9);
  for (const k of keys) assert.equal(idx.get(k), oldTint(k, 9), k);
  // 衝突した組でも、衝突していないキーは希望どおり
  const [k0, k1] = keys;
  const twin = Array.from({ length: 500 }, (_, i) => `twin:${i}`).find((k) => oldTint(k, 9) === oldTint(k0, 9));
  const mixed = tintIndex([k0, k1, twin], 9);
  assert.equal(mixed.get(k1), oldTint(k1, 9));
  assert.equal(mixed.get([k0, twin].sort()[0]), oldTint(k0, 9), "昇順の先頭が希望の添字を取る");
});

test("pxroom B5: 前回の割り当てを渡すと、新しいキーが既存キーの色を奪わない", () => {
  const b = "cwd:/Users/x/proj-b", a0 = "cwd:/Users/x/a0";   // 剰余が同じ（昇順では a0 が先）
  assert.equal(oldTint(a0, 9), oldTint(b, 9), "前提: 剰余が衝突する組でない＝検査になっていない");
  const first = tintIndex([b], 9);
  assert.equal(first.get(b), oldTint(b, 9));
  // 前回なし（固定時刻・決定論）: 集合だけで決まる＝昇順の先頭 a0 が希望の添字を取り、b は動く
  const bare = tintIndex([b, a0], 9);
  assert.equal(bare.get(a0), oldTint(a0, 9));
  assert.notEqual(bare.get(b), first.get(b));
  // 前回あり: b は前回の色のまま、新しい a0 が空いている次の添字へ
  for (const prev of [first, Object.fromEntries(first)]) {
    const next = tintIndex([b, a0], 9, prev);
    assert.equal(next.get(b), first.get(b), "既存キーの色が新しいキーに奪われた");
    assert.equal(next.get(a0), (oldTint(a0, 9) + 1) % 9);
  }
});

test("pxroom B5: prev なし・空の prev は今までと同じ結果（決定論）", () => {
  const rnd = lcg(11);
  for (let round = 0; round < 100; round += 1) {
    const keys = Array.from({ length: 1 + Math.floor(rnd() * 12) }, () => `cwd:/w/${Math.floor(rnd() * 1e5)}`);
    const base = [...tintIndex(keys, 9).entries()];
    assert.deepEqual([...tintIndex(keys, 9, null).entries()], base);
    assert.deepEqual([...tintIndex(keys, 9, new Map()).entries()], base);
    assert.deepEqual([...tintIndex(keys, 9, {}).entries()], base);
    // 前回＝今回の出力なら何も変わらない（毎フレーム渡しても色替えしない）
    assert.deepEqual([...tintIndex(keys, 9, tintIndex(keys, 9)).entries()].sort(), [...base].sort());
  }
});

test("pxroom B5 性質: 表示し続けているキーの色は、キーが出入りしても変わらない（9 件以下なら重ならない）", () => {
  const rnd = lcg(99);
  let stole = 0, kept = 0;
  for (let round = 0; round < 60; round += 1) {
    let shown = [];
    let prev = null;
    for (let step = 0; step < 12; step += 1) {
      // 何件か帰り、何件か来る（合計は 9 件まで）
      shown = shown.filter(() => rnd() > 0.2);
      while (shown.length < 9 && rnd() < 0.6) shown.push(`cwd:/p/${Math.floor(rnd() * 1e6)}`);
      shown = [...new Set(shown)];
      const next = tintIndex(shown, 9, prev);
      const got = shown.map((k) => next.get(k));
      assert.equal(new Set(got).size, shown.length, `round ${round} step ${step}: 色が重なった`);
      if (prev) {
        const held = new Set(prev.values());
        for (const k of shown) {
          if (prev.has(k)) { assert.equal(next.get(k), prev.get(k), `round ${round} step ${step}: ${k} の色が変わった`); kept += 1; }
          else if (held.has(tintHash(k) % 9) && shown.some((o) => prev.get(o) === tintHash(k) % 9)) stole += 1;
        }
      }
      prev = next;
    }
  }
  // 空振り防止: 「新しいキーの希望が既存キーの色と同じ」場面を十分踏んだ（実測 stole 317・kept 2537。下限はおよそ 1/3）
  assert.ok(stole >= 100 && kept >= 800, `奪い合いの場面 ${stole} 回・持ち越し ${kept} 回＝検査になっていない`);
});

test("pxroom B5: 壊れた前回（同じ添字の取り合い・範囲外・整数でない）でも重ならず範囲内", () => {
  const keys = ["k0", "k1", "k2", "k3"];
  keys.push("k4", "k5");
  const junk = { k0: 2, k1: 2, k2: 42, k3: "x", k4: 5.5, k5: -2 };
  const idx = tintIndex(keys, 9, junk);
  const got = keys.map((k) => idx.get(k));
  assert.equal(new Set(got).size, 6, `重なった: ${got}`);
  assert.ok(got.every((i) => Number.isInteger(i) && i >= 0 && i < 9));
  assert.equal(idx.get("k0"), 2, "昇順の先頭が前回の添字を取る");
  // 前回の添字が使えない（取り合いに負けた・範囲外・整数でない・負）キーは、前回が無いのと同じ＝ハッシュの希望
  // （剰余で丸めたり切り捨てたりして使わない）。前提: この 4 キーの希望は互いにも k0 の 2 とも重ならない
  const wants = ["k1", "k2", "k4", "k5"].map((k) => tintHash(k) % 9);
  assert.equal(new Set([...wants, 2]).size, 5, `前提が崩れた: 希望 ${wants}`);
  for (const k of ["k1", "k2", "k4", "k5"]) assert.equal(idx.get(k), tintHash(k) % 9, `${k}: 壊れた前回 ${junk[k]} を使った`);
  assert.deepEqual([...tintIndex(keys, 9, junk).entries()], [...idx.entries()]);
});

test("pxroom B5: 件数が色数を超えたら重なるしかない（それでも範囲内・落とさない）", () => {
  const keys = Array.from({ length: 14 }, (_, i) => `k${i}`);
  const idx = tintIndex(keys, 9);
  assert.equal(idx.size, 14);
  assert.ok([...idx.values()].every((i) => i >= 0 && i < 9));
  assert.equal(new Set(idx.values()).size, 9, "空いている添字を使い切っていない");
  assert.equal(tintIndex(keys, 0).size, 0);
});

// ── R99 Phase 4（P4-2a）: 部屋の倍率は形状で選ぶ整数・ロボの当たりは最寄り ─────────────────
// roomScale(availW, availH) = clamp(floor(min(availW / 184, availH / 160)), 2, 4)。割合で縮めない・非整数にしない。
// avail は「部屋の箱の内寸から**左の余白 16 だけ**を引いた幅」と「部屋に使ってよい丈」（CSS px）。
// 右の余白は ×k の部屋が食ってよい（p4gap §3: スマホ 390 で ×2 の 368 を置くと右は 6px）。
const GUTTER = 16;           // ui/cockpit/style.css の `.cpstage .viewport { padding: 0 16px }`
const COL3 = 584 - 1;        // 3 カラムの左の列（style.css `.main` の 584px）から `.cpleft` の右の罫 1px を引いた内寸
const CAP_H = 480;           // 1 列の部屋の丈の上限（p4gap §3・今の `.cpstage { min-height: 480px }`）

test("pxroom P4 roomScale: 形状ごとの倍率（コックピット＝今の CSS から出した avail／PWA ドット＝p4gap §3 の設計）", () => {
  const TABLE = [
    // コックピット（Mac のブラウザ・公開デモ）。1181px 未満は 1 列＝部屋の箱はページの幅いっぱい、丈は 480 まで
    ["コックピット スマホ 390×844（1 列）", 390 - GUTTER, CAP_H, 2],
    ["コックピット iPad 縦 834×1112（1 列）", 834 - GUTTER, CAP_H, 3],
    ["コックピット 折りたたみ 890×626（1 列）", 890 - GUTTER, CAP_H, 3],
    ["コックピット iPad 横 1112×834（1 列）", 1112 - GUTTER, CAP_H, 3],
    ["コックピット 1440×900（3 カラム・左の列 584）", COL3 - GUTTER, CAP_H, 3],
    // PWA のドット（P4-6 の設計・右の列は ❗ドック＋ロスターの 344）。丈は「部屋の箱に残る丈」
    ["PWA スマホ 390×844（幅で ×2）", 390 - GUTTER, 844, 2],
    ["PWA 折りたたみ 890×626（2 列・幅で ×2）", 890 - 344 - GUTTER, 626, 2],
    ["PWA iPad 縦 834×1112（1 列・丈 640 以上で ×4）", 834 - GUTTER, 640, 4],
    ["PWA iPad 横 1112×834（2 列・丈 640 以上で ×4）", 1112 - 344 - GUTTER, 640, 4],
  ];
  for (const [shape, w, h, k] of TABLE) {
    assert.equal(roomScale(w, h), k, `${shape}: avail ${w}×${h} → ×${roomScale(w, h)}（期待 ×${k}）`);
    // ×k の部屋は avail に収まる（×2 だけは最小＝入らなければ部屋の中を横スクロールで逃がす）
    if (k > ROOM_SCALE_MIN) assert.ok(ROOM.w * k <= w && ROOM.h * k <= h, `${shape}: ×${k} が avail に入らない`);
  }
  // iPad 横で ×4 を取るには部屋の箱の丈が 640 要る＝見出しとドックに使えるのは 834 − 640 = 194 まで（P4-6 の制約）
  assert.equal(roomScale(1112 - 344 - GUTTER, 639), 3);
  // 罠: 3 カラムの内寸から**両側**の余白を引くと 551＝×3 の 552 に 1px 足りず ×2 に落ちる（今の golden は ×3）
  assert.equal(roomScale(COL3 - 2 * GUTTER, CAP_H), 2, "両側の余白を引いた avail は ×2 になる（呼ぶ側は左だけ引く）");
});

test("pxroom P4 roomScale: 3 カラムの既定は今の ×3（ROOM.scale・golden cockpit.png の 552×480）を変えない", () => {
  assert.ok(Number.isInteger(ROOM.scale) && ROOM.scale >= ROOM_SCALE_MIN && ROOM.scale <= ROOM_SCALE_MAX,
    `ROOM.scale ${ROOM.scale} が roomScale の範囲の外`);
  assert.equal(roomScale(COL3 - GUTTER, CAP_H), ROOM.scale);
  assert.deepEqual([ROOM_SCALE_MIN, ROOM_SCALE_MAX], [2, 4]);
});

test("pxroom P4 roomScale: 境界（ちょうど k 倍は k・1px でも足りなければ k−1・上下限で止める）", () => {
  for (let k = ROOM_SCALE_MIN; k <= ROOM_SCALE_MAX; k += 1) {
    const W = ROOM.w * k, H = ROOM.h * k, below = Math.max(ROOM_SCALE_MIN, k - 1);
    assert.equal(roomScale(W, H), k, `ちょうど ×${k}（${W}×${H}）`);
    assert.equal(roomScale(W - 1, H), below, `幅が 1px 足りない ×${k}`);
    assert.equal(roomScale(W, H - 1), below, `丈が 1px 足りない ×${k}`);
    assert.equal(roomScale(W - 0.5, H), below, `幅が 0.5px 足りない ×${k}（小数の avail も切り捨て）`);
    assert.equal(roomScale(W + 1, H + 1), k, `1px 余る ×${k}`);
    // 片方だけ広くても狭い方で決まる
    assert.equal(roomScale(W * 10, H), k);
    assert.equal(roomScale(W, H * 10), k);
  }
  assert.equal(roomScale(ROOM.w * 5, ROOM.h * 5), ROOM_SCALE_MAX, "×5 が入っても ×4 で止める");
  assert.equal(roomScale(1e7, 1e7), ROOM_SCALE_MAX);
  assert.equal(roomScale(ROOM.w * 2 - 1, ROOM.h * 2 - 1), ROOM_SCALE_MIN, "×2 も入らなくても ×2（横スクロールで逃がす）");
  assert.equal(roomScale(1, 1), ROOM_SCALE_MIN);
});

test("pxroom R102-1 roomScale: PWA の床 ×1（第 3 引数）・既定（コックピット）は ×2 のまま", () => {
  assert.equal(ROOM_SCALE_FLOOR, 1);
  assert.equal(roomScale(ROOM.w * 2 - 1, ROOM.h * 2 - 1, ROOM_SCALE_FLOOR), 1, "×2 が入らなければ ×1");
  assert.equal(roomScale(ROOM.w * 2, ROOM.h * 2, ROOM_SCALE_FLOOR), 2, "ちょうど ×2 は ×2");
  assert.equal(roomScale(390 - 6, 196, ROOM_SCALE_FLOOR), 1, "390×664 であなた待ちのカードが出た窓");
  assert.equal(roomScale(390 - 6, 436, ROOM_SCALE_FLOOR), 2, "同じ形でカードが無い窓");
  assert.equal(roomScale(10, 10, ROOM_SCALE_FLOOR), 1, "×1 も入らなくても ×1（パンで逃がす）");
  assert.equal(roomScale(Number.NaN, 5, ROOM_SCALE_FLOOR), 1);
  assert.equal(roomScale(ROOM.w * 2 - 1, ROOM.h * 2 - 1), ROOM_SCALE_MIN, "第 3 引数なし＝コックピットは ×2 のまま");
  for (const bad of [0, 3, 1.5, "1", null, -1]) assert.equal(roomScale(10, 10, bad), ROOM_SCALE_MIN, `下限 ${bad} は受けない`);
});

test("pxroom R110-S6 roomScale: 眺めるモードの上限（第 4 引数・×6）・既定の上限 ×4 と PWA の床は変わらない", async () => {
  const { ROOM_SCALE_WATCH_MAX } = await import("./pxroom.js");
  assert.equal(ROOM_SCALE_WATCH_MAX, 6);
  assert.equal(ROOM_SCALE_MAX, 4, "既定の上限は据え置き（PWA のピンチ・3 カラムの段）");
  // 1920×1080 の画面いっぱい（ティッカーの帯 40px を引く）＝×6・1440×900＝×5・1280×640＝×3
  assert.equal(roomScale(1920, 1080 - 40, ROOM_SCALE_MIN, ROOM_SCALE_WATCH_MAX), 6);
  assert.equal(roomScale(1440, 900 - 40, ROOM_SCALE_MIN, ROOM_SCALE_WATCH_MAX), 5);
  assert.equal(roomScale(1280, 640 - 40, ROOM_SCALE_MIN, ROOM_SCALE_WATCH_MAX), 3);
  assert.equal(roomScale(1e7, 1e7, ROOM_SCALE_MIN, ROOM_SCALE_WATCH_MAX), 6, "×7 が入っても ×6 で止める");
  assert.equal(roomScale(1e7, 1e7), ROOM_SCALE_MAX, "第 4 引数なし＝×4 で止める（従来どおり）");
  assert.equal(roomScale(1e7, 1e7, ROOM_SCALE_FLOOR), ROOM_SCALE_MAX, "PWA（第 3 引数だけ）も ×4 で止める");
  assert.equal(roomScale(1, 1, ROOM_SCALE_MIN, ROOM_SCALE_WATCH_MAX), ROOM_SCALE_MIN, "入らなければ下限");
  for (const k of [5, 6]) {
    const W = ROOM.w * k, H = ROOM.h * k;
    assert.equal(roomScale(W, H, ROOM_SCALE_MIN, ROOM_SCALE_WATCH_MAX), k, `ちょうど ×${k}`);
    assert.equal(roomScale(W - 1, H, ROOM_SCALE_MIN, ROOM_SCALE_WATCH_MAX), k - 1, `幅が 1px 足りない ×${k}`);
  }
  // 上限は MAX〜WATCH_MAX の整数だけ（それ以外は既定の ×4＝黙って大きくしない）
  for (const bad of [3, 7, 5.5, "6", null, -1, NaN, Infinity]) {
    assert.equal(roomScale(1e7, 1e7, ROOM_SCALE_MIN, bad), ROOM_SCALE_MAX, `上限 ${String(bad)} は受けない`);
  }
  assert.equal(roomScale(1e7, 1e7, ROOM_SCALE_MIN, 5), 5);
});

test("pxroom P4 roomScale: 非数・負・0・無限・数でない値は ×2", () => {
  const BAD = [0, -0, -1, -552, NaN, Infinity, -Infinity, undefined, null, "552", "abc", {}, [], true];
  for (const bad of BAD) {
    assert.equal(roomScale(bad, 800), ROOM_SCALE_MIN, `幅 ${String(bad)}`);
    assert.equal(roomScale(1000, bad), ROOM_SCALE_MIN, `丈 ${String(bad)}`);
  }
  assert.equal(roomScale(), ROOM_SCALE_MIN);
});

test("pxroom P4 roomScale 性質: 整数・範囲内・単調・収まる・それ以上は入らない（全域を掃く）", () => {
  let checked = 0;
  for (let W = 0; W <= 1200; W += 1) {
    for (let H = 0; H <= 900; H += 3) {
      const k = roomScale(W, H);
      assert.ok(Number.isInteger(k) && k >= ROOM_SCALE_MIN && k <= ROOM_SCALE_MAX, `${W}×${H} → ${k}`);
      // 単調（広げて倍率が下がらない）
      assert.ok(roomScale(W + 1, H) >= k && roomScale(W, H + 3) >= k, `${W}×${H}: 広げたら倍率が下がった`);
      // 収まる（×2 だけは最小）・一段上は入らない（×4 は上限）
      assert.ok(k === ROOM_SCALE_MIN || (ROOM.w * k <= W && ROOM.h * k <= H), `${W}×${H}: ×${k} が入らない`);
      assert.ok(k === ROOM_SCALE_MAX || ROOM.w * (k + 1) > W || ROOM.h * (k + 1) > H, `${W}×${H}: ×${k + 1} が入るのに ×${k}`);
      checked += 1;
    }
  }
  assert.ok(checked > 300000, `掃いた点が少ない: ${checked}`);
});

test("pxroom P4 roomScale: 同じ入力なら同じ倍率（呼ぶ順・回数に依らない）", () => {
  const inputs = [];
  for (let i = 0; i < 400; i += 1) inputs.push([(i * 37) % 1300, (i * 53) % 900]);
  const fwd = inputs.map(([w, h]) => roomScale(w, h));
  const rev = inputs.slice().reverse().map(([w, h]) => roomScale(w, h)).reverse();
  assert.deepEqual(rev, fwd);
  assert.deepEqual(inputs.map(([w, h]) => roomScale(w, h)), fwd);
});

// pickNearest(actors, lx, ly, radiusLogical): 描いた矩形（ROOM.robot 12×16）までの距離が最小の人。
// 矩形の中は距離 0（今の「中だけ当たる」を含む）。半径の外は null。同距離は足元の y が大きい人（手前）→ key の昇順。
const act = (key, x, y, extra = {}) => ({ key, x, y, ...extra });
const RW = ROOM.robot.w, RH = ROOM.robot.h;

test("pxroom P4 pickNearest: 矩形の中は距離 0＝その人（半径 0 でも当たる＝今の当たりを含む）", () => {
  const a = act("a", 50, 60);
  const others = [act("b", 90, 60), act("c", 50, 100)];
  for (const [px, py] of [[50, 60], [56, 68], [50 + RW, 60 + RH], [50, 60 + RH], [50 + RW - 0.25, 60]]) {
    assert.equal(pickNearest([a, ...others], px, py, 0), a, `(${px}, ${py})`);
    assert.equal(pickNearest([...others, a], px, py, 11), a, `(${px}, ${py}) 半径 11`);
  }
  assert.equal(pickNearest([a], 49.9, 60, 0), null, "半径 0 は矩形の外に当てない");
});

test("pxroom P4 pickNearest: 半径は矩形の縁から測る（ちょうど半径は当たり・超えたら null・角は斜めの距離）", () => {
  const a = act("a", 50, 60);
  const r = 11;
  assert.equal(pickNearest([a], 50 - r, 68, r), a, "左にちょうど半径");
  assert.equal(pickNearest([a], 50 - r - 0.01, 68, r), null, "左に半径＋0.01");
  assert.equal(pickNearest([a], 50 + RW + r, 68, r), a, "右にちょうど半径");
  assert.equal(pickNearest([a], 56, 60 - r, r), a, "頭の上にちょうど半径");
  assert.equal(pickNearest([a], 56, 60 + RH + r + 0.01, r), null, "足の下に半径＋0.01");
  // 角の外は斜めの距離（右下の角から (6, 8) ＝距離 10 は当たり、(9, 8) ＝ 12.04 は外れ）
  assert.equal(pickNearest([a], 50 + RW + 6, 60 + RH + 8, r), a);
  assert.equal(pickNearest([a], 50 + RW + 9, 60 + RH + 8, r), null, "角の外を縦横別々（チェビシェフ）で測っている");
});

test("pxroom P4 pickNearest: ×2〜×4 で PICK_RADIUS_PX を倍率で割った半径なら、ひとりの当たりは縦横とも 44 CSS px 以上", () => {
  assert.ok(PICK_RADIUS_PX >= 22, `半径 ${PICK_RADIUS_PX} CSS px（直径 44 に届かない）`);
  const a = act("a", 80, 70);
  for (let k = ROOM_SCALE_MIN; k <= ROOM_SCALE_MAX; k += 1) {
    const r = PICK_RADIUS_PX / k;
    const e = 1e-6;   // r が 22/3 のような循環小数でも縁の丸めで落ちないよう、縁のわずかに内側を当てる
    const cx = a.x + RW / 2, cy = a.y + RH / 2;
    assert.equal(pickNearest([a], a.x - r + e, cy, r), a, `×${k}: 左の縁`);
    assert.equal(pickNearest([a], a.x + RW + r - e, cy, r), a, `×${k}: 右の縁`);
    assert.equal(pickNearest([a], cx, a.y - r + e, r), a, `×${k}: 上の縁`);
    assert.equal(pickNearest([a], cx, a.y + RH + r - e, r), a, `×${k}: 下の縁`);
    assert.equal(pickNearest([a], a.x - r - 0.01, cy, r), null, `×${k}: 左の縁の外`);
    assert.ok((RW + 2 * r) * k >= 44 && (RH + 2 * r) * k >= 44, `×${k}: 当たりが 44 CSS px に届かない`);
  }
});

test("pxroom P4 pickNearest: 近い人が勝つ（手前でも遠ければ負ける）", () => {
  const back = act("back", 50, 60);   // 矩形の右の縁 62
  const front = act("front", 70, 70); // 矩形の左の縁 70
  assert.equal(pickNearest([front, back], 64, 72, 11), back, "縁から 2 の奥の人より、縁から 6 の手前の人を選んだ");
  assert.equal(pickNearest([back, front], 67, 72, 11), front);
});

test("pxroom P4 pickNearest: 同距離は足元の y が大きい人（手前）→ key の昇順・入力の順に依らない", () => {
  // 重なり（どちらも距離 0）: 手前（y 大）が key では後ろでも勝つ＝描いた順で上に居る人
  const backA = act("a", 50, 60), frontZ = act("z", 52, 64);
  assert.equal(pickNearest([backA, frontZ], 55, 70, 11), frontZ);
  assert.equal(pickNearest([frontZ, backA], 55, 70, 11), frontZ);
  // 足元の y も同じなら key の昇順（文字コード順）
  const k2 = act("k2", 50, 60), k1 = act("k1", 55, 60), k10 = act("k10", 53, 60);
  for (const order of [[k2, k1, k10], [k10, k2, k1], [k1, k10, k2]]) {
    assert.equal(pickNearest(order, 58, 66, 11), k1, `${order.map((a) => a.key)}`);
  }
  // 矩形の外で同距離（左右から 6）: 足元の y が大きい方 → 同じなら key
  const L = act("b", 30, 60), R = act("m", 54, 61);
  assert.equal(pickNearest([L, R], 48, 70, 11), R, "同距離で手前の人を選ばない（key の昇順を先に見ている）");
  assert.equal(pickNearest([R, L], 48, 70, 11), R);
  const L2 = act("m", 30, 60), R2 = act("b", 54, 60);
  assert.equal(pickNearest([L2, R2], 48, 70, 11), R2, "同距離・同じ y で key の昇順にならない");
  assert.equal(pickNearest([R2, L2], 48, 70, 11), R2);
});

test("pxroom P4 pickNearest: 退場中（leaving）は当てない・壊れた入力は null か読み飛ばす", () => {
  const out = act("out", 50, 60, { leaving: true });
  const near = act("near", 70, 60);
  assert.equal(pickNearest([out, near], 62, 66, 11), near, "退場中の人に当てた");
  assert.equal(pickNearest([out], 62, 66, 11), null);
  // 入れ物が無い・座標が数でない → null
  for (const bad of [null, undefined, 42, "abc", {}]) assert.equal(pickNearest(bad, 55, 66, 11), null);
  for (const [x, y] of [[NaN, 66], [55, NaN], [Infinity, 66], [undefined, 66], ["55", 66]]) {
    assert.equal(pickNearest([near], x, y, 1e6), null, `(${x}, ${y})`);
  }
  // 壊れた人は読み飛ばす（落ちない）
  const junk = [null, 7, act("nx", NaN, 60), act("ny", 60, undefined), act("s", "50", 60)];
  assert.equal(pickNearest([...junk, near], 72, 66, 11), near);
  assert.equal(pickNearest(junk, 55, 66, 1e6), null);
  // 半径が数でない・負は 0（中だけ当たる）
  for (const r of [NaN, -1, undefined, "11", null]) {
    assert.equal(pickNearest([near], 72, 66, r), near, `半径 ${String(r)}: 中なのに外れた`);
    assert.equal(pickNearest([near], 69, 66, r), null, `半径 ${String(r)}: 外なのに当たった`);
  }
  // Map の値など、配列でない反復も受ける
  assert.equal(pickNearest(new Map([["near", near]]).values(), 72, 66, 0), near);
});

test("pxroom P4 pickNearest 性質: 全部の枠に人が居る部屋で、当たった人より近い人は居ない・半径内に誰か居れば必ず当たる", () => {
  const people = Object.entries(SLOTS).flatMap(([z, list]) => list.map((s, i) => act(`${z}#${i}`, s.x, s.y)));
  // 自分の中心と四隅は自分（枠は重ならない＝pxroom B2）
  for (const p of people) {
    for (const [px, py] of [[p.x + RW / 2, p.y + RH / 2], [p.x, p.y], [p.x + RW - 0.5, p.y + RH - 0.5]]) {
      for (let k = ROOM_SCALE_MIN; k <= ROOM_SCALE_MAX; k += 1) {
        assert.equal(pickNearest(people, px, py, PICK_RADIUS_PX / k)?.key, p.key, `${p.key} の (${px}, ${py}) ×${k}`);
      }
    }
  }
  // 部屋の全域を 0.5 刻みで掃く: 別の書き方の神託（全員の距離を出して並べる）と一致する
  const dist2 = (p, x, y) => {
    const dx = Math.max(p.x - x, 0, x - (p.x + RW));
    const dy = Math.max(p.y - y, 0, y - (p.y + RH));
    return dx * dx + dy * dy;
  };
  const r = PICK_RADIUS_PX / 2;
  let hits = 0, ties = 0, misses = 0;
  for (let y = -12; y <= ROOM.h + 12; y += 0.5) {
    for (let x = -12; x <= ROOM.w + 12; x += 0.5) {
      const within = people.map((p) => ({ p, d: dist2(p, x, y) })).filter((q) => q.d <= r * r);
      const got = pickNearest(people, x, y, r);
      if (!within.length) { assert.equal(got, null, `(${x}, ${y}) 半径の外なのに当たった`); misses += 1; continue; }
      within.sort((u, v) => u.d - v.d || v.p.y - u.p.y || (u.p.key < v.p.key ? -1 : u.p.key > v.p.key ? 1 : 0));
      assert.equal(got, within[0].p, `(${x}, ${y}) → ${got?.key}（期待 ${within[0].p.key}）`);
      if (within.length > 1 && within[1].d === within[0].d) ties += 1;
      hits += 1;
    }
  }
  // 空振り防止: 当たり・外れ・同距離の場面を十分踏んだ
  assert.ok(hits > 10000 && misses > 1000 && ties > 20, `当たり ${hits}・外れ ${misses}・同距離 ${ties}＝検査になっていない`);
});

test("pxroom P4 pickNearest: 同じ入力なら同じ人（入力の順に依らない・同じ参照を返す）", () => {
  const people = [act("q0", 15, 132), act("q1", 32, 132), act("q2", 49, 132), act("w", 40, 128, { leaving: false })];
  const perms = [[0, 1, 2, 3], [3, 2, 1, 0], [1, 3, 0, 2], [2, 0, 3, 1]];
  for (let x = 10; x <= 70; x += 1) {
    const want = pickNearest(people, x, 140, 11);
    for (const perm of perms) assert.equal(pickNearest(perm.map((i) => people[i]), x, 140, 11), want, `x=${x} 順 ${perm}`);
    if (want) assert.ok(people.includes(want), "写しを返した（呼ぶ側が同じ人として扱えない）");
  }
});

test("pxroom R103: 搬送済みの日英 verb・kind・ツール名から机の仕事を決める", () => {
  const cases = [
    [{ verb: "編集中", target: "room.js" }, "implementation"],
    [{ verb: "editing", target: "room.js" }, "implementation"],
    [{ verb: "実行中", target: "node --test room.test.mjs" }, "implementation"],
    [{ verb: "running", target: "npm run build" }, "implementation"],
    [{ verb: "読込中", target: "guide.md" }, "research"],
    [{ verb: "調査中", target: "資料" }, "research"],
    [{ verb: "リサーチ中", target: "資料" }, "research"],
    [{ verb: "reading", target: "guide.md" }, "research"],
    [{ verb: "researching", target: "guide.md" }, "research"],
    [{ verb: "Searching", target: "資料" }, "research"],
    [{ kind: "think", verb: "実行中" }, "planning"],
    [{ verb: "考え中…" }, "planning"],
    [{ verb: "Thinking" }, "planning"],
    [{ verb: "段取り整理中" }, "planning"],
    [{ verb: "planning" }, "planning"],
  ];
  for (const [fields, expected] of cases) {
    // 実際の buildWorld の搬送経路でも同じ分類になる（新しいフィールドを必要としない）。
    const world = buildWorld(officeOf(1, () => ({ kind: "tool", ...fields })));
    assert.equal(deskWorkKind(world.agents[0]), expected, JSON.stringify(fields));
  }
  for (const tool of ["Edit", "Write", "NotebookEdit", "Artifact"]) {
    assert.equal(deskWorkKind({ ask: { tool } }), "implementation", tool);
  }
  for (const tool of ["Read", "Grep", "Glob", "Explore", "WebFetch", "WebSearch"]) {
    assert.equal(deskWorkKind({ stuckTool: tool }), "research", tool);
  }
  for (const tool of ["TodoWrite", "EnterPlanMode", "ExitPlanMode"]) {
    assert.equal(deskWorkKind({ ask: { tool } }), "planning", tool);
  }
});

test("pxroom R103: 現在のツールを優先し、3 種を確定できない作業は従来の小物", () => {
  assert.equal(deskWorkKind({ ask: { tool: "Read" }, stuckTool: "Edit", kind: "think" }), "research");
  assert.equal(deskWorkKind({ ask: { tool: "Unknown" }, stuckTool: "Edit", verb: "読込中" }), "implementation");
  assert.equal(deskWorkKind({ kind: "think", verb: "読込中" }), "planning");
  for (const e of [null, undefined, {}, { kind: "tool", verb: "実行中", target: "some-command" },
    { verb: "執筆中", target: "guide.md" }, { verb: "報告中" }, { verb: "指示待ち" },
    { work: { now: ["実装と調査と計画"] } }, { state: "resting", ask: { tool: "Read" } }]) {
    assert.equal(deskWorkKind(e), null, JSON.stringify(e));
  }
});

test("pxroom R103: 助手は minionsLive 0/1/3/5 に合わせて最大 3 体、累計は使わない", () => {
  for (const [count, expected] of [[0, 0], [1, 1], [3, 3], [5, 3], [1.8, 1], ["2", 2]]) {
    assert.equal(assistantCount({ minionsLive: count, minions: 100 }), expected, String(count));
  }
  for (const e of [null, undefined, {}, { minions: 5 }, { minionsLive: -1 },
    { minionsLive: NaN }, { minionsLive: Infinity }, { minionsLive: "unknown" }]) {
    assert.equal(assistantCount(e), 0);
  }
});

test("pxroom R103: 赤い紙は err で停止した人だけ、確認待ちや再開済みには出さない", () => {
  for (const k of ["api", "limit", "ctx"]) {
    assert.equal(hasErrorNote({ state: "waiting", err: { k, ago: 1 } }), true, k);
    assert.equal(hasErrorNote({ state: "working", err: { k, ago: 1 } }), false, k);
  }
  for (const fields of [{ ask: { tool: "ExitPlanMode" } }, { ask: { kind: "permission" } },
    { ask: { kind: "question" } }, { question: "続けますか" }, { approvalMin: 1 }]) {
    assert.equal(hasErrorNote({ state: "waiting", err: { k: "api" }, ...fields }), false, JSON.stringify(fields));
  }
  for (const e of [null, undefined, {}, { state: "waiting" }, { err: null }, { err: {} },
    { evidence: { kind: "failed", ago: 1 } }]) assert.equal(hasErrorNote(e), false);
});

test("pxroom R103: 配置は机上の種類・助手数・赤い紙を運び、会議室の場所は変えない", () => {
  const agents = [
    { id: "coder", session: "coder", state: "working", kind: "tool", verb: "編集中", target: "room.js" },
    { id: "lead", session: "lead", state: "working", kind: "think", minions: 5, minionsLive: 5 },
    { id: "stopped", session: "stopped", state: "waiting", err: { k: "api" } },
  ];
  const result = roomLayout({ agents });
  const actor = (session) => result.actors.find((a) => a.session === session);
  assert.deepEqual([actor("coder").deskWork, actor("coder").assistants, actor("coder").errorNote],
    ["implementation", 0, false]);
  assert.deepEqual([actor("lead").zone, actor("lead").deskWork, actor("lead").assistants],
    ["meeting", "planning", 3]);
  assert.deepEqual([actor("stopped").zone, actor("stopped").errorNote], ["desk", true]);
});

test("pxroom R103 roomWorldFields: session モードは raw の助手とエラーを actor まで運ぶ", () => {
  const office = {
    avatarMode: "session",
    roster: [
      { projectId: "pa", session: "sa", state: "waiting", minions: 0, minionsLive: 0,
        err: { k: "api", ago: 3, text: "本文は部屋に渡さない" } },
      { projectId: "pb", session: "sb", state: "working", minions: 5, minionsLive: 2 },
    ],
  };
  const original = buildWorld(office);
  const before = structuredClone(original);
  const enriched = roomWorldFields(original, office);
  assert.notEqual(enriched, original);
  assert.deepEqual(original, before, "元の world を書き換えた");
  for (const key of ["seats", "overflow", "counts", "tasks"]) assert.equal(enriched[key], original[key]);
  assert.deepEqual(enriched.agents.map((a) => [a.id, a.state, a.zone, a.minions]),
    original.agents.map((a) => [a.id, a.state, a.zone, a.minions]));
  const actors = roomLayout(enriched).actors;
  assert.equal(actors.find((a) => a.session === "sa").errorNote, true);
  assert.equal(actors.find((a) => a.session === "sb").assistants, 2);
  assert.deepEqual(enriched.agents.find((a) => a.session === "sa").err, { k: "api", ago: 3 });
});

test("pxroom R103 roomWorldFields: project モードの助手は合算値、エラーは代表の値", () => {
  const office = {
    avatarMode: "project",
    roster: [{ projectId: "p", session: "lead", state: "waiting", minions: 20, minionsLive: 5,
      err: { k: "limit", ago: 2 }, sessions: [{ session: "other" }, { session: "lead" }] }],
    employees: [
      { session: "other", state: "waiting", minionsLive: 4, err: { k: "ctx" } },
      { session: "lead", state: "waiting", minionsLive: 1, err: { k: "limit", ago: 2 } },
    ],
  };
  const original = buildWorld(office);
  const enriched = roomWorldFields(original, office);
  assert.equal(enriched.agents[0].minionsLive, 5, "代表本人の数で合算値を上書きした");
  assert.deepEqual(enriched.agents[0].err, { k: "limit", ago: 2 });
  assert.equal(enriched.agents[0].sessions, original.agents[0].sessions);
  assert.equal(enriched.agents[0].zone, original.agents[0].zone);
  const actor = roomLayout(enriched).actors[0];
  assert.deepEqual([actor.assistants, actor.errorNote, actor.zone], [3, true, original.agents[0].zone]);
});

test("pxroom R103 roomWorldFields: 欠けた raw は代表 session だけから補い、他の内訳や新代表の値を混ぜない", () => {
  const office = {
    roster: [{ projectId: "p", session: "lead", state: "waiting",
      sessions: [{ session: "other" }, { session: "lead" }] }],
    employees: [
      { session: "other", minionsLive: 3, err: { k: "ctx" } },
      { session: "lead", minionsLive: 1, err: { k: "api" } },
    ],
  };
  const original = buildWorld(office);
  const enriched = roomWorldFields(original, office);
  assert.deepEqual([enriched.agents[0].minionsLive, enriched.agents[0].err], [1, { k: "api", ago: 0 }]);
  const withoutLead = { ...office, employees: office.employees.slice(0, 1) };
  assert.deepEqual([roomWorldFields(original, withoutLead).agents[0].minionsLive,
    roomWorldFields(original, withoutLead).agents[0].err], [0, null]);
  const changedLead = { roster: [{ projectId: "p", session: "next", minionsLive: 3, err: { k: "ctx" } }] };
  assert.equal(roomWorldFields(original, changedLead), original, "新代表の現在値を古い代表に貼った");
});

test("pxroom R103 roomWorldFields: employees のみの旧形式でも補完し、累計や raw の state で配置を変えない", () => {
  const office = { employees: [
    { session: "stopped", state: "waiting", err: { k: "api" }, minions: 0, minionsLive: 0 },
    { session: "quiet", state: "waiting", minions: 30, minionsLive: 0 },
  ] };
  const original = buildWorld(office);
  const raw = { employees: office.employees.map((a) => ({ ...a, state: "resting", minions: 0 })) };
  const enriched = roomWorldFields(original, raw);
  assert.deepEqual(enriched.agents.map((a) => [a.state, a.zone, a.minions]),
    original.agents.map((a) => [a.state, a.zone, a.minions]));
  const actors = roomLayout(enriched).actors;
  assert.equal(actors.find((a) => a.session === "stopped").errorNote, true);
  assert.equal(actors.find((a) => a.session === "quiet").assistants, 0);
});

test("pxroom R103 roomWorldFields: raw の無い world は既存の値と参照を保つ", () => {
  const world = { agents: [{ id: "p", session: "past", state: "waiting", minionsLive: 1, err: { k: "api" } }] };
  for (const office of [null, undefined, {}, { roster: [], employees: [] },
    { roster: [null], employees: [null, { session: "unrelated", minionsLive: 5 }] }]) {
    assert.equal(roomWorldFields(world, office), world);
  }
  for (const worldValue of [null, undefined, {}]) assert.equal(roomWorldFields(worldValue, {}), worldValue);
  const actor = roomLayout(roomWorldFields(world, null)).actors[0];
  assert.deepEqual([actor.assistants, actor.errorNote], [1, true]);
});


// ── R110: 用事と小道具の材料（tool・evidenceAt・prop）─────────────────────────────
test("pxroom R110: actors は tool（最後の工具）・evidenceAt（generatedAt − ago・committed/tested だけ）・prop（職業）を運ぶ", () => {
  const office = {
    generatedAt: 5000,
    employees: [
      { session: "s0", dept: "動画編集部", cwd: "/Users/x/work/video-cut", state: "working", kind: "tool", tool: "Read",
        evidence: { kind: "committed", ago: 40 } },
      { session: "s1", dept: "atlas", cwd: "/Users/x/work/blog-studio", state: "working", kind: "tool", tool: "Bash",
        evidence: { kind: "failed", ago: 3 } },
      { session: "s2", dept: "なにか", cwd: "/Users/dev/blog/zzz", state: "working", evidence: { kind: "tested", ago: 0 } },
    ],
  };
  const w = buildWorld(office);
  // 台帳の行（hud/board.js）は withKindFields で tool を足す＝ここではその形を写す
  const rows = w.agents.map((a) => ({ ...a, tool: office.employees.find((e) => e.session === a.session).tool || "" }));
  const res = roomLayout(w, [{ key: "g", sessions: rows }]);
  const by = new Map(res.actors.map((a) => [a.session, a]));
  assert.deepEqual(["s0", "s1", "s2"].map((k) => by.get(k).tool), ["Read", "Bash", ""]);
  assert.deepEqual(["s0", "s1", "s2"].map((k) => by.get(k).evidenceAt), [4960, null, 5000], "失敗の証拠でカウンターへ行く材料を作った");
  // 職業: 名前・役割で当たる → 動画（ベレー）／当たらなければ cwd の末尾のフォルダ名（blog-studio → 書く人）／
  // 親のフォルダ名（/Users/dev/blog）では決めない
  assert.deepEqual(["s0", "s1", "s2"].map((k) => by.get(k).prop), ["video", "writer", "generic"]);
  // world の agent にそのまま渡しても型を保つ（tool が無い＝スマホの中継・古い daemon）
  const bare = roomLayout({ agents: [{ id: "z", session: "z", state: "working" }] }, []).actors[0];
  assert.deepEqual([bare.tool, bare.evidenceAt, bare.prop], ["", null, "generic"]);
});

test("pxroom R110: roomWorldFields は roster / employees の tool を部屋の world に足す（中継から来た world は空のまま）", () => {
  const office = { roster: [{ projectId: "p0", session: "s0", name: "x", state: "working", tool: "WebFetch" }] };
  const w = roomWorldFields(buildWorld(office), office);
  assert.equal(w.agents[0].tool, "WebFetch");
  const relayed = { roster: [{ projectId: "p0", session: "s0", name: "x", state: "working" }] };
  assert.equal(roomWorldFields(buildWorld(relayed), relayed).agents[0].tool, "");
});
