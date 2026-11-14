// R98-W1: HUD の部品（ui/hud/*.js）が要求する DOM の契約を、どの様式の shell も満たすことを機械で固定する。
//
// なぜ要るか: ui/hud/*.js（17 本）は three を参照しない＝様式に依らない操作系だが、
// `querySelector("#sheet")` のように **id と class で shell を掴む**。ここが様式ごとに
// 食い違うと「シートが開かない・数字キーが効かない」が黙って起きる（片方の様式だけ壊れる）。
// 契約は「hud が掴む id/class の集合 ⊆ その様式の shell が持つ集合」。
//
// 任意の要素（様式によっては無い）は `optional(shell, "#x")`（ui/hud/shell.js）で掴む＝ここでは数えない。
// 実測（2026-09-18）: hud が掴む id は 39 個・class は 24 個（R98-W1 の分離後）。
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const HUD = join(ROOT, "ui", "hud");

/** その様式から実際に**辿れる** hud の部品だけを集める（import の連鎖を 1 回たどる）。
 *
 * R99: 全部の hud ファイルを一律に「必須」と数えると、台帳（ui/hud/table.js）を持たない iso にまで
 * `.pxhrow` を要求してしまう。壊れようがないものを要求する門は、正しい実装を落とす。
 * 契約は「**その様式が読み込む** hud の部品が掴む id/class ⊆ その様式の shell が持つ集合」。
 */
function hudReach(styleDir) {
  const seen = new Set();
  const queue = [];
  const addFrom = (src) => {
    for (const m of src.matchAll(/from\s+"\/ui\/hud\/([A-Za-z0-9_-]+)\.js"/g)) {
      const f = `${m[1]}.js`;
      if (!seen.has(f)) { seen.add(f); queue.push(f); }
    }
  };
  for (const f of readdirSync(styleDir)) {
    if (f.endsWith(".js") && !f.endsWith(".test.js")) addFrom(readFileSync(join(styleDir, f), "utf8"));
  }
  while (queue.length) addFrom(readFileSync(join(HUD, queue.shift()), "utf8"));
  return [...seen];
}

/** hud/*.js が querySelector で掴む id と class を静的に集める（実行しない）。 */
function hudDemands(files) {
  const ids = new Set(), classes = new Set();
  for (const f of files) {
    if (!f.endsWith(".js") || f.endsWith(".test.js")) continue;
    const src = readFileSync(join(HUD, f), "utf8");
    for (const m of src.matchAll(/querySelector(?:All)?\("#([A-Za-z0-9_-]+)/g)) ids.add(m[1]);
    for (const m of src.matchAll(/querySelector(?:All)?\("\.([A-Za-z0-9_-]+)/g)) classes.add(m[1]);
  }
  return { ids, classes };
}

/** 様式の index.js（テンプレート）と、それが動的に作る要素（el(...)/className 代入）から供給側を集める。 */
function shellSupplies(styleDir) {
  const ids = new Set(), classes = new Set();
  const files = readdirSync(styleDir).filter((f) => f.endsWith(".js"));
  // hud 自身が動的に生成する要素（例: firstrun の .setup-*, tray の .traylock）は hud 側で供給される
  for (const dir of [styleDir, HUD]) {
    for (const f of readdirSync(dir)) {
      if (!f.endsWith(".js") || f.endsWith(".test.js")) continue;
      const src = readFileSync(join(dir, f), "utf8");
      for (const m of src.matchAll(/\bid="([A-Za-z0-9_-]+)"/g)) ids.add(m[1]);
      for (const m of src.matchAll(/\.id = "([A-Za-z0-9_-]+)"/g)) ids.add(m[1]);
      for (const m of src.matchAll(/\bclass="([^"]+)"/g)) for (const c of m[1].split(/\s+/)) classes.add(c);
      for (const m of src.matchAll(/className = "([^"]+)"/g)) for (const c of m[1].split(/\s+/)) classes.add(c);
      // 第 2 引数は文字列でもテンプレートリテラルでもよい（`pxrow ${kind}` の形。R99 で漏れを発見:
      // 台帳を ui/hud へ移した瞬間 .pxrow が「誰も供給していない」ことになり、全様式が落ちた）
      for (const m of src.matchAll(/\bel\("[a-z]+",\s*[`"]([^`"]+)[`"]/g)) for (const c of m[1].split(/\s+/)) classes.add(c.replace(/\$\{.*$/, ""));
      for (const m of src.matchAll(/mEl\("[a-z]+",\s*"([^"]+)"/g)) for (const c of m[1].split(/\s+/)) classes.add(c);
      // hud の各部品が持つ小さな要素工場（sEl / mEl / el / cell）とテンプレートリテラルの class=... も供給側。
      // 工場を 1 つ書き足すたびにここも足す必要がある＝この行が抽出漏れの入口（R99 で .aprog が漏れた）。
      for (const m of src.matchAll(/\b(?:sEl|cell)\("?[a-z]*"?,?\s*[`"]([^`"]+)[`"]/g)) for (const c of m[1].split(/\s+/)) classes.add(c.replace(/\$\{.*$/, ""));
      for (const m of src.matchAll(/classList\.add\("([^"]+)"/g)) classes.add(m[1]);
      // delivery.js の append(cls, text) のような局所工場: 第 1 引数の文字列を class とみなす
      for (const m of src.matchAll(/\bappend\("([a-z][a-z0-9-]*)",/g)) classes.add(m[1]);
    }
  }
  return { ids, classes, files };
}


// 検査する様式: ui/<style>/index.js が在るディレクトリ全部（pixel を足したら自動で対象になる）
const styles = readdirSync(join(ROOT, "ui")).filter((d) => {
  try { readFileSync(join(ROOT, "ui", d, "index.js")); return d !== "pwa"; } catch { return false; }
});
assert.ok(styles.includes("iso"), "iso が見つからない");
assert.ok(styles.includes("app"), "app の殻が見つからない");

const counted = {};
for (const style of styles) {
  const styleDir = join(ROOT, "ui", style);
  const demand = hudDemands(hudReach(styleDir));
  assert.ok(demand.ids.size >= 30,
    `様式 ${style} から辿れる hud が掴む id が ${demand.ids.size} 個しか取れていない（抽出が壊れている）`);
  const supply = shellSupplies(styleDir);
  counted[style] = { ids: demand.ids.size, classes: demand.classes.size };
  const missingIds = [...demand.ids].filter((id) => !supply.ids.has(id));
  const missingClasses = [...demand.classes].filter((c) => !supply.classes.has(c));
  assert.deepEqual(missingIds, [], `様式 ${style} の shell に、hud が掴む id が無い: ${missingIds.join(" ")}`);
  assert.deepEqual(missingClasses, [], `様式 ${style} の shell に、hud が掴む class が無い: ${missingClasses.join(" ")}`);
}
console.log(`✓ HUD の DOM 契約: ${styles.map((s) => `${s}(${counted[s].ids}/${counted[s].classes})`).join("・")}`
  + " ＝ 様式ごとに「辿れる hud の部品が掴む id/class」を満たしている");
