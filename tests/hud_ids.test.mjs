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

/** hud/*.js が querySelector で掴む id と class を静的に集める（実行しない）。 */
function hudDemands() {
  const ids = new Set(), classes = new Set();
  for (const f of readdirSync(HUD)) {
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
      for (const m of src.matchAll(/\bel\("[a-z]+",\s*"([^"]+)"/g)) for (const c of m[1].split(/\s+/)) classes.add(c);
      for (const m of src.matchAll(/mEl\("[a-z]+",\s*"([^"]+)"/g)) for (const c of m[1].split(/\s+/)) classes.add(c);
      // hud の各部品が持つ小さな要素工場（sEl / mEl / el）とテンプレートリテラルの class=... も供給側
      for (const m of src.matchAll(/\bsEl\("[a-z]+",\s*[`"]([^`"]+)[`"]/g)) for (const c of m[1].split(/\s+/)) classes.add(c.replace(/\$\{.*$/, ""));
      for (const m of src.matchAll(/classList\.add\("([^"]+)"/g)) classes.add(m[1]);
      // delivery.js の append(cls, text) のような局所工場: 第 1 引数の文字列を class とみなす
      for (const m of src.matchAll(/\bappend\("([a-z][a-z0-9-]*)",/g)) classes.add(m[1]);
    }
  }
  return { ids, classes, files };
}

const demand = hudDemands();
assert.ok(demand.ids.size >= 30, `hud が掴む id が ${demand.ids.size} 個しか取れていない（抽出が壊れている）`);

// 検査する様式: ui/<style>/index.js が在るディレクトリ全部（pixel を足したら自動で対象になる）
const styles = readdirSync(join(ROOT, "ui")).filter((d) => {
  try { readFileSync(join(ROOT, "ui", d, "index.js")); return d !== "pwa"; } catch { return false; }
});
assert.ok(styles.includes("iso"), "iso が見つからない");

for (const style of styles) {
  const supply = shellSupplies(join(ROOT, "ui", style));
  const missingIds = [...demand.ids].filter((id) => !supply.ids.has(id));
  const missingClasses = [...demand.classes].filter((c) => !supply.classes.has(c));
  assert.deepEqual(missingIds, [], `様式 ${style} の shell に、hud が掴む id が無い: ${missingIds.join(" ")}`);
  assert.deepEqual(missingClasses, [], `様式 ${style} の shell に、hud が掴む class が無い: ${missingClasses.join(" ")}`);
}
console.log(`✓ HUD の DOM 契約: id ${demand.ids.size} / class ${demand.classes.size} を ${styles.join("・")} の shell が満たす`);
