// R86-I: 識別バッジ／短縮名の割り当てを Mac とスマホで**同一**に保つ機械ピン。
// ここが食い違うと「PCでは7号なのにスマホでは制」＝同じ相手を別の記号で呼ぶ最悪の形になる
// （R79-6で「識別記号を1本化」した設計そのものが崩れる）。
import { APP_HTML as src } from "../relay/src/app_html.js";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const begin = src.indexOf("var SEQ_PATTERNS=");
const end = src.indexOf("function idOf(", begin);
assert.ok(begin >= 0 && end > begin, "PWA_BADGE source not found in app_html.js");
// 生成モジュールを評価した配信HTMLから、対象関数の範囲だけを抽出。
const chunk = src.slice(begin, end);
// 配信する正規表現のエスケープが保たれていることも検査する。
assert.ok(chunk.includes("[\\s"), "正規表現の \\s が潰れている（app_html.js）");
assert.ok(chunk.includes("SEQ_PATTERNS"), "抽出に失敗");
globalThis.__badge = {};
globalThis.idOf = (e) => (e && (e.projectId || e.session)) || "";
globalThis.nameOf = (e) => (e && (e.title || e.disp || e.name || e.dept)) || "";
(0, eval)(chunk.replace("function assignLabels", "__badge.assignLabels = function assignLabels"));
const pwa = globalThis.__badge.assignLabels;

const core = await import(join(ROOT, "ui/core/world.js"));

const CASES = [
  ["制作本部(works)", "制作本部(works) 3号", "制作本部(works) 5号", "制作本部(works) 7号",
   "20260714 - ai-office", "GLM5.3", "AKOOL"],
  ["案件A(api)", "案件B(api)"],                       // 短縮が衝突する組
  ["works #2", "works #10", "works"],                  // #表記
  ["ひとつだけ"],
  ["A", "A", "A"],                                     // 完全同名（衝突を連番で割る）
];

for (const names of CASES) {
  const ags = names.map((n, i) => ({ id: "id" + String(i).padStart(2, "0"), name: n }));
  const a = core.assignLabels(ags);
  const b = pwa(ags.map((x) => ({ projectId: x.id, disp: x.name })));
  for (const x of ags) {
    const ca = a.get(x.id);
    const cb = b[x.id];
    assert.deepEqual({ short: cb.short, badge: cb.badge }, { short: ca.short, badge: ca.badge },
      `不一致: ${x.name} core=${JSON.stringify(ca)} pwa=${JSON.stringify(cb)}`);
  }
  const badges = ags.map((x) => a.get(x.id).badge);
  assert.equal(new Set(badges).size, badges.length, `バッジが重複: ${names}`);
}
console.log(`✓ badge parity: ${CASES.length} 組で Mac↔スマホ一致（バッジは常に一意）`);
