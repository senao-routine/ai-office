// R96-D3: 生成体ロボ（?rig=1 既定）の「姿勢の写像」と「重ねる部品の集合」を機械で固定する。
// R96-D2 の欠陥（手の小道具が 0 個・着席中に立ち clip・状態ランプ消滅）は全ゲート緑のまま本番へ出た＝ここが門。
import assert from "node:assert/strict";
import { test } from "node:test";
import { registerHooks } from "node:module";

// ブラウザ用の絶対パス（/ui/...）を node から読めるようにする（tests/iso_acts.mjs と同じ流儀）
registerHooks({ resolve(specifier, context, next) {
  if (specifier.startsWith("/ui/")) return next(new URL(`..${specifier}`, import.meta.url).href, context);
  return next(specifier, context);
} });
const { clipFor, transitionTracker, D_PARTS, D_PARTS_CLAW, HYBRID_PARTS } = await import("../ui/iso/rigbot.js");

/** scene3d が実際に組む poseKind の語彙（ui/iso/scene3d.js の poseKind 代入箇所から）。 */
const STANDING = {
  walk: "walk", "walk:question": "walk", "walk:celebrate": "walk", "walk:approval": "walk",
  enter: "walk", run: "walk", exit: "walk",
  greet: "cheer", celebrate: "cheer",
  question: "look_around", think: "wait", chibi: "wait",
  "meeting:present": "idle", "meeting:stand": "idle", "external:": "idle",
};
test("立っているときの poseKind → clip", () => {
  for (const [kind, clip] of Object.entries(STANDING)) {
    assert.equal(clipFor(kind, false), clip, `${kind} が ${clip} にならない`);
  }
});

test("着席中は question / think でも座り clip（立ち clip だと椅子と床を貫く・R96-D3）", () => {
  for (const kind of ["question", "think", "desk:", "meeting:", "lounge:", "relax", "loungeTab", "read:book", "chat", ""]) {
    assert.equal(clipFor(kind, true), "sit", `${kind} が座り clip にならない`);
  }
  // 歩き・一発芸は着席フラグより強い（遷移中に座り clip へ落ちない）
  assert.equal(clipFor("walk", true), "walk");
  assert.equal(clipFor("greet", true), "cheer");
  assert.equal(clipFor("celebrate", true), "cheer");
});

test("重ねる部品の集合（消えると表情・状態・小道具が黙って落ちる）", () => {
  for (const part of ["visorRig", "chest", "antTip", "__acc", "__prop"]) {
    assert.ok(D_PARTS.has(part), `D_PARTS に ${part} が無い`);
    assert.ok(D_PARTS_CLAW.has(part), `D_PARTS_CLAW に ${part} が無い`);
  }
  assert.ok(D_PARTS_CLAW.has("claw"), "OpenClaw のハサミが出ない");
  assert.ok(!D_PARTS.has("claw"), "claude/codex にハサミが付いている");
  // ハイブリッド（?rig=2）は頭ごと procedural
  for (const part of ["head", "visor", "chest", "__acc"]) assert.ok(HYBRID_PARTS.has(part), `HYBRID_PARTS に ${part} が無い`);
});

test("遷移元の clip は補間が終わるまで動かない（1 フレームで遷移先に化けると 0.45 秒の補間が消える）", () => {
  const t = transitionTracker();
  assert.equal(t.step("desk:", "sit"), null);          // 初回は遷移元なし
  assert.equal(t.step("desk:", "sit"), null);          // 同じ姿勢のあいだは据え置き
  assert.equal(t.step("walk", "walk"), "sit");         // 遷移の 1 フレーム目
  assert.equal(t.step("walk", "walk"), "sit");         // 2 フレーム目以降も遷移元は sit のまま（ここが回帰した箇所）
  assert.equal(t.step("walk", "walk"), "sit");
  assert.equal(t.step("greet", "cheer"), "walk");      // 次の遷移で初めて更新される
  assert.equal(t.step("greet", "cheer"), "walk");
});

test("同じ clip へ渡る遷移は恒等（kind だけ変わって clip が同じなら混合は無害）", () => {
  const t = transitionTracker();
  t.step("walk", "walk");
  assert.equal(t.step("run", "walk"), "walk");
});
