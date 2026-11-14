// R110: スマホ PWA の部屋（ui/pwa/bootpx.js）が ui/core・ui/platform・ui/cockpit から名前つきで import する
// 契約を**静的に**確かめる番人。bootpx.js 自体は DOM 必須で node では import できず、PWA の実動作は
// RUN_RELAY=1 の pwa3d_smoke でしか見ない＝既定の verify では「core の export を改名・削除したら PWA が
// 起動時に落ちる」が黙って通る穴があった。ここで `import { a, b as c } from "../core/x.js"` の各名前が、
// 対象モジュールの export に在ることを文字で突き合わせる（評価はしない）。
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";

const here = new URL(".", import.meta.url);
const BOOTPX = new URL("../ui/pwa/bootpx.js", here);
const BOOT3D = new URL("../ui/pwa/boot3d.js", here);

function importedNames(source) {
  const out = [];
  const re = /import\s*\{([^}]*)\}\s*from\s*["']([^"']+)["']/g;
  for (const m of source.matchAll(re)) {
    const names = m[1].split(",").map((s) => s.trim()).filter(Boolean)
      .map((s) => s.split(/\s+as\s+/)[0].trim());
    out.push({ spec: m[2], names });
  }
  return out;
}

function exportsOf(source) {
  const names = new Set();
  for (const m of source.matchAll(/export\s+(?:const|let|var|function\*?|class|async\s+function)\s+([A-Za-z_$][\w$]*)/g)) names.add(m[1]);
  for (const m of source.matchAll(/export\s+(?:const|let|var)\s*\{([^}]*)\}/g)) {
    for (const n of m[1].split(",")) { const k = n.split(":")[0].trim(); if (k) names.add(k); }
  }
  for (const m of source.matchAll(/export\s*\{([^}]*)\}/g)) {
    for (const n of m[1].split(",")) {
      const parts = n.trim().split(/\s+as\s+/);
      const k = (parts[1] || parts[0] || "").trim(); if (k) names.add(k);
    }
  }
  return names;
}

for (const boot of [BOOTPX, BOOT3D]) test(`${boot.pathname.split("/").pop()} の名前つき import は対象の export に在る`, async () => {
  const src = await readFile(boot, "utf8");
  const imports = importedNames(src);
  assert.ok(imports.length >= 4, "bootpx.js の import 文が見つからない（形が変わったならこの番人の正規表現を直す）");
  const missing = [];
  for (const { spec, names } of imports) {
    const target = new URL(spec, boot);
    assert.ok(existsSync(target), `bootpx.js が import する ${spec} が無い`);
    const exp = exportsOf(await readFile(target, "utf8"));
    for (const n of names) if (!exp.has(n)) missing.push(`${spec} → ${n}`);
  }
  assert.deepEqual(missing, [], "bootpx.js が import する名前が export に無い（PWA が起動時に落ちる）");
});

for (const boot of [BOOTPX, BOOT3D]) test(`${boot.pathname.split("/").pop()} は同じ橋と再マウント契約を持つ`, async () => {
  const src = await readFile(boot, "utf8");
  const method = (name) => new RegExp(`\\b${name}\\s*(?:\\(|[:=]\\s*(?:\\([^)]*\\)|[\\w$]+)\\s*=>)`);
  assert.ok(exportsOf(src).has("mount"), "切替でキャッシュ済み module を再度 mount できない");
  for (const name of ["apply", "pick", "project", "anchor", "agents", "resize", "focus", "greet", "stats", "gestureMoved", "dispose"])
    assert.match(src, method(name), `橋の ${name} が無い`);
  for (const name of ["zoomBy", "panBy", "reset"]) assert.match(src, method(name), `view.${name} が無い`);
  for (const event of ["scene3d-ready", "scene3d-failed", "activity-ready"]) assert.ok(src.includes(event), `${event} が無い`);
  assert.ok(src.includes("window.__scene3d") && src.includes("createActivityView"));
});

test("3D の束は別の版で閉じ、通常の束に three/iso を入れない", async () => {
  const main = await import("../relay/src/modules_data.js");
  const three = await import("../relay/src/modules_3d.js");
  assert.ok(three.MODULES["/ui/pwa/boot3d.js"] && three.MODULES["/ui/iso/scene3d.js"]);
  assert.ok(Object.keys(three.MODULES).some((path) => path.startsWith("/ui/vendor/three/")));
  assert.ok(Object.keys(three.ASSETS).length > 0, "3D のテクスチャが空");
  assert.ok(!Object.keys(main.MODULES).some((path) => /^\/ui\/(?:iso|vendor\/three)/.test(path)));
  assert.notEqual(main.BUILD, three.BUILD);
  for (const [path, source] of Object.entries(three.MODULES)) {
    assert.ok(!/from\s*["']\/ui\//.test(source), `${path} が版の外を import している`);
    for (const match of source.matchAll(/from\s*["'](\.\.?\/[^"']+)["']/g)) {
      const target = new URL(match[1], "https://example.invalid" + path).pathname;
      assert.ok(Object.hasOwn(three.MODULES, target), `${path} → ${target} が3Dの束に無い`);
    }
  }
  assert.match(three.MODULES["/ui/iso/scene3d.js"], /new URL\(`\.\/tex\/\$\{file\}`, import\.meta\.url\)/);
});

test("この番人は壊れた契約を赤にする（自己検査）", () => {
  const src = 'import { ROOM, nope as x } from "../core/pxroom.js";';
  const names = importedNames(src)[0].names;
  assert.deepEqual(names, ["ROOM", "nope"]);
  const exp = exportsOf("export const ROOM = 1; export function roomScale() {}\nexport { a as b, c };\nexport const { d, e } = f;");
  assert.deepEqual([...exp].sort(), ["ROOM", "b", "c", "d", "e", "roomScale"]);
  assert.ok(!exp.has("nope"));
});
