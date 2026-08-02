// R65: PWAへ移植した gloss（worker.js PWA_GLOSS_BEGIN..END）が
// 正本 ui/core/world.js の tidyActivity/activityGloss と同一出力であることの機械ピン。
// どちらか片方だけ直すと、ここが落ちる（js_sign_kat / pushTargets 抽出と同じ流儀）。
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(ROOT, "relay/src/worker.js"), "utf8");
const begin = src.indexOf("// PWA_GLOSS_BEGIN");
const end = src.indexOf("// PWA_GLOSS_END");
assert.ok(begin >= 0 && end > begin, "PWA_GLOSS markers not found in worker.js");
// 間接evalはグローバルスコープで実行される＝globalThis経由で受け取る
globalThis.__glossParity = {};
(0, eval)(src.slice(begin, end)
  .replace("function tidyActivityPWA", "__glossParity.tidy = function tidyActivityPWA")
  .replace("function activityGlossPWA", "__glossParity.gloss = function activityGlossPWA"));
const evalScope = globalThis.__glossParity;
// 抽出片の中で tidyActivityPWA を名前参照している（gloss→tidy 呼び出し）ため別名も生やす
globalThis.tidyActivityPWA = evalScope.tidy;

const world = await import(join(ROOT, "ui/core/world.js"));

const CASES = [
  {},
  { verb: "実行中", target: "bash verify.sh" },
  { verb: "実行中", target: "git push origin master" },
  { verb: "実行中", target: "npm install three" },
  { verb: "実行中", target: "なにかのコマンド --flag" },
  { verb: "編集中", target: "server/office_server.py" },
  { verb: "編集中", target: "README.md" },
  { verb: "編集中", target: "" },
  { verb: "執筆中", target: "docs/ROADMAP.md" },
  { verb: "執筆中", target: "ブログ原稿" },
  { verb: "調査中", target: "x" },
  { verb: "報告中", target: "長い報告文（`やること/配布手順書_2026073" },
  { verb: "指示待ち", target: "" },
  { verb: "考え中…", target: "次の一手", kind: "think" },
  { state: "resting", verb: "休憩中" },
  { state: "working", verb: "", target: "" },
  { verb: "点検中", target: "`server/x.py`" },
  { verb: "Running", target: "verify.sh" },
  { verb: "Editing", target: "scene3d.js" },
  { verb: "Waiting for input", target: "" },
  { verb: "Thinking", target: "…" },
  { verb: "Reporting", target: "done" },
  { work: { now: ["ビジュアル回帰の実行", "次"] }, verb: "実行中", target: "x" },
  { work: { now: ["", "  ", "実タスク名"] }, verb: "編集中", target: "y.py" },
  { verb: "実行中", target: "見出し # 残骸 と パス tests/fixtures/world/basic.json" },
];

let n = 0;
for (const c of CASES) {
  for (const lang of ["ja", "en"]) {
    const expected = world.activityGloss(c, lang);
    const actual = evalScope.gloss(c, lang);
    assert.equal(actual, expected,
      `gloss mismatch for ${JSON.stringify(c)} lang=${lang}: pwa=${actual} core=${expected}`);
    n += 1;
  }
}
// tidy 単体も数点
for (const s of ["`a/b/c.md`", "報告（途切れ", "  x   y  ", "https://example.com/a/b", "あ".repeat(90)]) {
  assert.equal(evalScope.tidy(s), world.tidyActivity(s));
  n += 1;
}
console.log(`gloss parity OK (${n} checks)`);
