// R80-A11: PWAの❗並び順が、正本 ui/core/world.js の attentionQueue と**同じ先頭**を選ぶことの機械ピン。
// 従来スマホは session の辞書順で並べており、「Macで言われた相手」と「スマホで最初に出る相手」が
// 食い違っていた（同じ❗キューを見ているのに順序の正本が2つあった）。
// gloss_parity と同じ流儀＝worker.js から該当片を抽出して実行し、core と突き合わせる。
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(ROOT, "relay/src/worker.js"), "utf8");
const begin = src.indexOf("// PWA_TRIAGE_BEGIN");
const end = src.indexOf("// PWA_TRIAGE_END");
assert.ok(begin >= 0 && end > begin, "PWA_TRIAGE markers not found in worker.js");

// worker.js は「JS文字列を + で連ねた1本のスクリプト」なので、文字列リテラルを繋いで実体を得る
const chunk = src.slice(begin, end);
const code = [...chunk.matchAll(/^'(.*)' \+$/gm)].map((m) => m[1]).join("\n");
assert.ok(code.includes("function triageSort"), "triageSort を抽出できていない");

globalThis.__triage = {};
(0, eval)(
  "globalThis.needsAttn = (e) => (e.approvalMin > 0) || !!e.question;\n" +
  "globalThis.isPend = (e) => !!e.pending && !needsAttn(e);\n" +
  "globalThis.stateSort = (a, b) => { const x = a.session || '', y = b.session || '';" +
  " return x < y ? -1 : x > y ? 1 : 0; };\n" +
  "globalThis.rankEmp = (e) => needsAttn(e) ? 0 : isPend(e) ? 1 :" +
  " e.state === 'working' ? 2 : e.state === 'waiting' ? 3 : 4;\n" +
  code.replace(/^var STARVE_MIN=15;$/m, "globalThis.STARVE_MIN = 15;")
      .replace("function attnRank", "globalThis.attnRank = function attnRank")
      .replace("function triageSort", "globalThis.triageSort = function triageSort"),
);

const world = await import(join(ROOT, "ui/core/world.js"));

// 「並び方の違いが結果に出る」形を選ぶ（session名の辞書順と、待たせた分数の順が逆になる組み合わせ）
const CASES = [
  [{ session: "zzz", approvalMin: 20 }, { session: "aaa", question: "どっち?" },
   { session: "bbb", approvalMin: 3 }],
  [{ session: "aaa", approvalMin: 2 }, { session: "zzz", approvalMin: 40 }],
  [{ session: "mmm", question: "A?" }, { session: "aaa", approvalMin: 1 }],
  [{ session: "aaa", question: "A?" }, { session: "bbb", question: "B?" }],
  [{ session: "zzz", approvalMin: 16 }, { session: "aaa", question: "急ぎ?" }],
];

let ng = 0;
for (const agents of CASES) {
  const canonical = world.attentionQueue(agents)[0];
  const pwa = agents.slice().sort(globalThis.triageSort).filter(globalThis.needsAttn)[0];
  if ((canonical?.session || null) !== (pwa?.session || null)) {
    console.error(`✗ 先頭が不一致: core=${canonical?.session} / PWA=${pwa?.session}`,
      JSON.stringify(agents));
    ng += 1;
  }
}
if (ng) {
  console.error(`✗ ❗順序パリティ ${ng}件不一致`);
  process.exit(1);
}
console.log(`✓ ❗順序パリティ（core attentionQueue ↔ PWA triageSort・${CASES.length}ケース）`);
