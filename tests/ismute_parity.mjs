// R86-G: 「送っても即座には届かない」の判定を Mac と スマホで**同一**に保つ機械ピン。
// 片方だけ直すとここが落ちる（gloss_parity / triage_parity と同じ流儀）。
//
// この判定が食い違うと最悪の形で壊れる: ❗が立っている（＝ターンが終わらず届かない）のに
// 片方の画面だけ「届きます」と言う。実際 R86-E では working を一律除外していたため
// **❗が立ってから最初の164秒だけ警告が消える**穴があった（実測）。
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(ROOT, "relay/src/worker.js"), "utf8");
const begin = src.indexOf("// PWA_ISMUTE_BEGIN");
const end = src.indexOf("// PWA_ISMUTE_END");
assert.ok(begin >= 0 && end > begin, "PWA_ISMUTE markers not found in worker.js");

// 抽出片は worker.js のJS文字列連結なので、'...' + の骨組みを剥がして関数本体を得る
const chunk = src.slice(begin, end)
  .split("\n").filter((l) => l.trim().startsWith("'"))
  .map((l) => l.trim().replace(/^'/, "").replace(/'\s*\+?\s*$/, ""))
  .join("");
globalThis.__ismuteParity = {};
// PWA 側は needsAttn（worker.js の別関数）に依存する。正本と同義の実装を与える
globalThis.needsAttn = (e) => (e.approvalMin > 0) || !!e.question;
(0, eval)(chunk.replace("function isMute", "__ismuteParity.isMute = function isMute"));
const pwa = globalThis.__ismuteParity.isMute;

const core = await import(join(ROOT, "ui/core/world.js"));

const CASES = [
  { listening: false, state: "waiting" },
  { listening: false, state: "resting" },
  { listening: false, state: "working" },                       // 稼働中＝誤警告しない
  { listening: false, state: "working", approvalMin: 2 },       // ❗中は working でも届かない
  { listening: false, state: "working", question: "どっち?" },   // 質問中も同じ
  { listening: false, state: "waiting", approvalMin: 5 },
  { listening: true, state: "waiting" },
  { listening: true, state: "working", approvalMin: 3 },
  { state: "waiting" },                                          // listening 未搬送＝脅さない
  { state: "working", approvalMin: 1 },
  // R86-H: 承認フックが待っている＝Stop hook が動いていなくても「いま直接届く」
  { listening: false, state: "working", approvalMin: 4, ask: { tool: "Bash", kind: "permission" } },
  { listening: false, state: "waiting", ask: { tool: "AskUserQuestion", kind: "question" } },
];

for (const c of CASES) {
  const a = core.isMuted(c);
  const b = pwa(c);
  assert.equal(b, a, `不一致: ${JSON.stringify(c)} core=${a} pwa=${b}`);
}
// 穴の再発を直接ピン（working + ❗ は必ず「届かない」側）
assert.equal(core.isMuted({ listening: false, state: "working", approvalMin: 2 }), true);
assert.equal(pwa({ listening: false, state: "working", question: "q" }), true);
// 承認フックが待っているときは両側とも「届く」（📴を出さない）
assert.equal(core.isMuted({ listening: false, state: "working", approvalMin: 2,
                            ask: { kind: "permission" } }), false);
assert.equal(pwa({ listening: false, state: "working", approvalMin: 2, ask: { kind: "permission" } }), false);
console.log(`✓ isMute parity: ${CASES.length} ケースで Mac↔スマホ一致`);
