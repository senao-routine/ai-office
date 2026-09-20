// R98-W2: 帯のコマ選び（純関数）。時刻・乱数・DOM に触らないことは層 lint が、
// 「どのコマを出すか」はここが固定する。golden の決定論はこの写像に乗っている。
import test from "node:test";
import assert from "node:assert/strict";
import { CELLS, CELL_COUNT, VENDORS, deskPitch, pxScale, pxpose, vendorCell } from "./pxpose.js";

const agent = (over = {}) => ({ state: "working", zone: "desk", kind: "tool", vendor: "claude", ...over });

test("pxpose: 状態 → 7 種類のコマ（❗が最優先・歩行が次）", () => {
  assert.equal(pxpose(agent(), 0).kind, "type");
  assert.equal(pxpose(agent({ state: "resting" }), 0).kind, "rest");
  assert.equal(pxpose(agent({ zone: "lounge" }), 0).kind, "rest");
  assert.equal(pxpose(agent({ kind: "think", state: "waiting" }), 0).kind, "think");
  assert.equal(pxpose(agent({ state: "waiting" }), 0).kind, "idle");
  assert.equal(pxpose(agent(), 0, { walkPhase: 0.3 }).kind, "walk");
  // ❗で受付へ歩いている間は**歩く**（挙手のまま床を滑ると壊れて見える）。
  // 「呼んでいる」は消えない＝頭上の点滅が歩行中も続く。着いたら挙手へ戻る。
  const walkingAttn = pxpose(agent({ attention: true }), 0, { walkPhase: 0.3 });
  assert.equal(walkingAttn.kind, "walk");
  assert.equal(walkingAttn.blink, true);
  assert.equal(pxpose(agent({ attention: true }), 0).kind, "raise");
});

test("pxpose: コマは表の中だけ・時間で循環する（同じ t なら同じ絵）", () => {
  for (const t of [0, 0.4, 1, 2.5, 7.75, 123.5]) {
    for (const over of [{}, { state: "resting" }, { attention: true }, { state: "waiting" }]) {
      const p = pxpose(agent(over), t);
      assert.ok(CELLS.includes(p.cell), `${p.cell} が表にない`);
      assert.deepEqual(p, pxpose(agent(over), t));       // 決定論
    }
  }
  assert.equal(pxpose(agent(), 0).cell, "type0");
  assert.equal(pxpose(agent(), 0.5).cell, "type1");
  assert.equal(pxpose(agent(), 1).cell, "type0");
});

test("pxpose: 歩行は距離（walkPhase）で駆動し、左向きは反転で作る", () => {
  assert.equal(pxpose(agent(), 99, { walkPhase: 0 }).cell, "walk0");
  assert.equal(pxpose(agent(), 99, { walkPhase: 0.5 }).cell, "walk2");
  assert.equal(pxpose(agent(), 99, { walkPhase: 0.99 }).cell, "walk3");
  assert.equal(pxpose(agent(), 0, { walkPhase: 0.1, facing: -1 }).flip, true);
  assert.equal(pxpose(agent(), 0, { walkPhase: 0.1, facing: 1 }).flip, false);
});

test("pxpose: ❗は 2Hz で点滅・✓旗は doneUntil まで", () => {
  assert.equal(pxpose(agent({ attention: true }), 0).blink, true);
  assert.equal(pxpose(agent({ attention: true }), 0.5).blink, false);
  assert.equal(pxpose(agent({ state: "waiting" }), 5, { doneUntil: 8 }).kind, "done");
  assert.equal(pxpose(agent({ state: "waiting" }), 9, { doneUntil: 8 }).kind, "idle");
});

test("pxpose: 壊れた入力でも表の中のコマを返す", () => {
  for (const [a, t] of [[null, 0], [undefined, NaN], [{}, Infinity], [{ state: 42 }, -1]]) {
    const p = pxpose(a, t);
    assert.ok(CELLS.includes(p.cell));
  }
});

test("コマ表とベンダーの数（42 セル＝14 × 3）", () => {
  assert.equal(CELL_COUNT, 14);
  assert.equal(new Set(CELLS).size, 14);
  assert.deepEqual(VENDORS, ["claude", "codex", "openclaw"]);
  assert.equal(CELL_COUNT * VENDORS.length, 42);
  assert.equal(vendorCell("Codex"), "codex");
  assert.equal(vendorCell("gemini"), "claude");
  assert.equal(vendorCell(undefined), "claude");
});

test("deskPitch: 何プロジェクトでも帯からはみ出さない（16..24 の整数）", () => {
  for (let n = 0; n <= 40; n++) {
    const { pitch, capacity } = deskPitch(n);
    assert.ok(Number.isInteger(pitch) && pitch >= 16 && pitch <= 24, `n=${n} pitch=${pitch}`);
    assert.ok(pitch * Math.min(n, capacity) <= 360, `n=${n} がはみ出す`);
  }
  assert.equal(deskPitch(1).pitch, 24);
  assert.equal(deskPitch(22).pitch, 16);
  assert.equal(deskPitch(-3).pitch, 24);
});

test("pxScale: 整数倍だけ（1..2）・部屋の最小幅 400 が入る最大の倍率", () => {
  assert.equal(pxScale(1406), 2);          // 1440 の帯（既定画面）
  assert.equal(pxScale(800), 2);           // iPad 縦 834 → ここが ×1 に落ちるとロボが実寸で読めない
  assert.equal(pxScale(856), 2);           // 折りたたみ 890
  assert.equal(pxScale(356), 1);           // スマホ 390（CSS がこの幅では帯ごと隠す）
  assert.equal(pxScale(400), 1);
  assert.equal(pxScale(NaN), 1);
  assert.equal(pxScale(5000), 2);          // 上限 2（×3 にすると帯が 216px になり行が 2 本消える）
});
