// R98-W2: 帯の間取り（純関数）。「何人居ても帯からはみ出さない」「同じ入力なら同じ配置」を固定する。
import test from "node:test";
import assert from "node:assert/strict";
import { STRIP, stripLayout, stripRoom } from "./stripLayout.js";
import { pxpose } from "./pxpose.js";
import { buildWorld } from "./world.js";

const officeOf = (n, over = () => ({})) => ({
  employees: Array.from({ length: n }, (_, i) => ({
    session: `s${i}`, dept: `p${i}`, cwd: `/w${i}`, state: "working", ...over(i),
  })),
});
const boardOf = (world) => world.agents.map((a) => ({ key: a.id, name: a.name, sessions: [a] }));

test("stripLayout: 22 プロジェクトでも机が帯の中に収まる", () => {
  const w = buildWorld(officeOf(22));
  const { desks, pitch } = stripLayout(w, boardOf(w));
  assert.ok(pitch >= 16 && pitch <= 24, `pitch=${pitch}`);
  for (const d of desks) {
    assert.ok(d.x >= STRIP.desks.x, `机が左へはみ出す: ${d.x}`);
    assert.ok(d.x + STRIP.robot.w <= STRIP.desks.x + STRIP.desks.w + STRIP.robot.w,
      `机が右へはみ出す: ${d.x}`);
  }
});

test("stripLayout: 全員が帯の矩形の中（0..480 / 0..72）", () => {
  for (const n of [0, 1, 5, 12, 22, 40]) {
    const w = buildWorld(officeOf(n, (i) => ({
      state: ["working", "waiting", "resting"][i % 3],
      approvalMin: i % 5 === 0 ? 3 : 0,
      question: i % 5 === 0 ? "?" : "",
    })));
    const { actors } = stripLayout(w, boardOf(w));
    for (const a of actors) {
      assert.ok(a.x >= 0 && a.x + STRIP.robot.w <= STRIP.width, `x=${a.x} n=${n}`);
      assert.ok(a.y >= 0 && a.y + STRIP.robot.h <= STRIP.height, `y=${a.y} n=${n}`);
    }
  }
});

test("stripLayout: 同じ入力なら同じ配置（乱数も時刻も使わない）", () => {
  const w = buildWorld(officeOf(9));
  const a = stripLayout(w, boardOf(w));
  const b = stripLayout(w, boardOf(w));
  assert.deepEqual(a, b);
});

test("stripLayout: ❗は受付の列へ立ち、こちらを向く", () => {
  const w = buildWorld(officeOf(4, (i) => (i === 2 ? { approvalMin: 7, question: "?" } : {})));
  const { actors } = stripLayout(w, boardOf(w));
  const attn = actors.filter((a) => a.attention);
  assert.equal(attn.length, 1);
  assert.equal(attn[0].zone, "queue");
  assert.ok(attn[0].x < STRIP.desks.x, "受付の側に居ない");
  assert.equal(attn[0].facing, -1);
});

test("stripLayout: 机にあぶれた分は帯に出さない（表には出る）", () => {
  const w = buildWorld(officeOf(40));
  const { desks, actors } = stripLayout(w, boardOf(w));
  const atDesk = actors.filter((a) => a.zone === "desk");
  assert.equal(atDesk.length, desks.length);
  assert.ok(desks.length < 40);
});

test("stripRoom: 動かない部分は常に同じ・帯の中", () => {
  const room = stripRoom();
  assert.deepEqual(room, stripRoom());
  for (const [name, r] of Object.entries(room)) {
    const list = Array.isArray(r) ? r : [r];
    for (const box of list) {
      assert.ok(box.x >= 0 && box.x + box.w <= STRIP.width, `${name} が横にはみ出す`);
      assert.ok(box.y >= 0 && box.y + box.h <= STRIP.height, `${name} が縦にはみ出す`);
    }
  }
});

test("stripLayout: 配置は本人の状態を持って出る（働いている人と待っている人が同じ絵にならない）", () => {
  // 別モデルレビューの指摘（2026-09-20）。state / kind を落としていたので全員 idle のコマになり、
  // 同じ机の working と waiting の描画が一致していた。帯の役割「生きているか」が死ぬ。
  const w = buildWorld(officeOf(4, (i) => ({
    state: ["working", "waiting", "resting", "waiting"][i],
    kind: i === 3 ? "think" : "",
  })));
  const { actors } = stripLayout(w, boardOf(w));
  const cell = (sid) => pxpose(actors.find((a) => a.session === sid), 0).cell;   // 並び順に頼らない
  assert.equal(cell("s0"), "type0");
  assert.equal(cell("s1"), "idle0");
  assert.equal(cell("s2"), "rest0");
  assert.equal(cell("s3"), "think0");
  assert.notEqual(cell("s0"), cell("s1"));
});

test("stripLayout: 誰も完全には重ならない（同じ机の 2 本目が消えない）", () => {
  // 別モデルレビューの指摘（2026-09-20）。同じプロジェクトの複数セッションは同じ机の座標を返すので、
  // 後から描いた 1 体が前の 1 体を塗り潰していた（fixture の (120,48) で実測）。
  for (const n of [1, 3, 9, 22]) {
    const w = buildWorld({
      employees: Array.from({ length: n * 3 }, (_, i) => ({
        session: `s${i}`, dept: `p${i % n}`, cwd: `/w${i % n}`,
        state: ["working", "resting", "waiting"][i % 3],
        approvalMin: i % 4 === 0 ? 2 : 0, minions: i % 5 === 0 ? 1 : 0,
      })),
    });
    const board = [...new Map(w.agents.map((a) => [a.dept || a.id, a])).values()]
      .map((a) => ({ key: a.id, name: a.name, sessions: w.agents.filter((x) => x.dept === a.dept) }));
    const { actors } = stripLayout(w, board);
    const seen = new Set();
    for (const a of actors) {
      const at = `${a.x},${a.y}`;
      assert.ok(!seen.has(at), `n=${n} で ${at} に 2 体が重なった`);
      seen.add(at);
    }
  }
});
