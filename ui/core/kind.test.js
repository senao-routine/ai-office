// ui/core/kind.js（セッションの種類・待っている長さの語）。PWA との突き合わせは tests/ismute_parity.mjs。
import assert from "node:assert/strict";
import { test } from "node:test";
import { attnKind, errFields, permModeOf, sessionKind, waitMinutes, waitWord, withKindFields } from "./kind.js";

test("sessionKind: 判定順（計画 → 許可 → 質問 → 止まっている? → エラー → 作業中 → 指示待ち → 休憩）", () => {
  assert.equal(sessionKind({ ask: { tool: "ExitPlanMode", kind: "permission" }, question: "x" }), "plan");
  assert.equal(sessionKind({ ask: { tool: "Bash", kind: "permission" }, question: "x" }), "perm");
  assert.equal(sessionKind({ question: "どっち?", approvalMin: 4 }), "question");
  assert.equal(sessionKind({ approvalMin: 4, err: { k: "api" } }), "stuck");
  assert.equal(sessionKind({ state: "waiting", err: { k: "limit", ago: 5 } }), "error");
  assert.equal(sessionKind({ state: "working", err: { k: "limit", ago: 5 } }), "working");
  assert.equal(sessionKind({ state: "waiting", minionsLive: 1 }), "working");
  assert.equal(sessionKind({ state: "waiting" }), "idle");
  assert.equal(sessionKind({ state: "resting" }), "rest");
  assert.equal(sessionKind(null), "rest");
});

test("attnKind: ❗の 4 種・分の無い attention は stuck・エラーは error・それ以外は空", () => {
  assert.equal(attnKind({ attention: true, ask: { tool: "Bash", kind: "permission" } }), "perm");
  assert.equal(attnKind({ attention: true, state: "waiting" }), "stuck");
  assert.equal(attnKind({ state: "waiting", err: { k: "ctx", ago: 1 } }), "error");
  assert.equal(attnKind({ state: "working" }), "");
});

test("waitWord / waitMinutes: 1 分未満は「いま」・時間は「1時間5分」", () => {
  assert.equal(waitWord(0), "いま");
  assert.equal(waitWord(0, "en"), "now");
  assert.equal(waitWord(6), "6分");
  assert.equal(waitWord(65), "1時間5分");
  assert.equal(waitWord(120, "en"), "2h");
  assert.equal(waitMinutes({ ask: { kind: "permission", min: 3 }, approvalMin: 1 }), 3);
  assert.equal(waitMinutes({ question: "q", session: "s", age: 9999, sessions: [{ session: "s", age: 130 }] }), 2);
  assert.equal(waitMinutes({ state: "waiting", err: { k: "api", ago: 600 } }), 10);
});

test("errFields / permModeOf / withKindFields: 列挙値だけを通す（本文を持ち込まない）", () => {
  assert.deepEqual(errFields({ k: "limit", ago: 120, text: "本文" }), { k: "limit", ago: 120 });
  assert.equal(errFields({ k: "weird" }), null);
  assert.equal(errFields(null), null);
  assert.equal(permModeOf({ permMode: "plan" }), "plan");
  assert.equal(permModeOf({ permMode: "rootkit" }), "");
  const a = withKindFields({ id: "a", state: "waiting" }, { err: { k: "api", ago: 3, text: "x" }, permMode: "acceptEdits", minionsLive: 2 });
  assert.deepEqual([a.err, a.permMode, a.minionsLive], [{ k: "api", ago: 3 }, "acceptEdits", 2]);
  assert.equal(JSON.stringify(a).includes("text"), false);
  // R110: 最後の工具の名前（40 字まで・文字列でなければ ""）
  assert.equal(withKindFields({ id: "a" }, { tool: "WebFetch" }).tool, "WebFetch");
  assert.equal(withKindFields({ id: "a" }, { tool: "x".repeat(60) }).tool.length, 40);
  for (const tool of [undefined, null, 3, { name: "Bash" }]) assert.equal(withKindFields({ id: "a" }, { tool }).tool, "");
});
