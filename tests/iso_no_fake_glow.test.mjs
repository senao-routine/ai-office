// R93-H2: 「発光は post.js の bloom（emissive な画面材質）だけ」を構造で固定する。
//
// 旧 R85 の見た目は fake glow 板（reflP/glowP/neonRing/HOLO_PANELS/screenGlow）で光らせていて、
// R90-V1 で全部退役した。R93 で「黒いモニタが光る」雰囲気を戻すとき、板を戻すのが一番安直だが、
// それは drawCalls と決定論を壊し、README に「実 bloom」と書けなくなる。
// ここは WebGL 不要（node --test）。makeMaterials() の**キーと材質の属性**だけを見る。
import assert from "node:assert/strict";
import { test } from "node:test";
import { registerHooks } from "node:module";

const root = new URL("../", import.meta.url);
registerHooks({ resolve(specifier, context, next) {
  return next(specifier.startsWith("/ui/") ? new URL(specifier.slice(1), root).href : specifier, context);
} });
globalThis.location = { search: "?t=3.2&seed=11" };
// makeMaterials は手続きテクスチャのために canvas を作る。描画結果は見ない＝inert な stub で足りる
// （tests/iso_growth.test.mjs と同じ型）。
function canvas() {
  const context = new Proxy({
    createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
    measureText: (text) => ({ width: String(text).length * 6 }),
    createLinearGradient: () => ({ addColorStop() {} }),
    createRadialGradient: () => ({ addColorStop() {} }),
  }, { get: (object, key) => object[key] ?? (() => {}) });
  return { width: 0, height: 0, getContext: () => context };
}
globalThis.document = { createElement: () => canvas() };
const { makeMaterials } = await import("../ui/iso/scene3d.js");
const THREE = await import("../ui/vendor/three/three.module.min.js");

// 旧 fake glow のキー（f671101:ui/iso/scene3d.js:47-145 に在った）。復活したら赤。
const RETIRED = ["neon", "neonC", "holo", "screenGlow", "eye", "reflP", "reflC", "glowP", "glowC", "stage"];
// emissiveIntensity > 1 を持ってよい材質。窓（sky）・ランプ・（V4' 後）画面だけ。
const MAY_GLOW = /^(sky|lampWarm|screen\w*)$/;

for (const quality of ["high", "mobile", "off"]) {
  const materials = makeMaterials(quality);

  test(`fake glow のキーが makeMaterials(${quality}) に無い`, () => {
    for (const key of RETIRED) assert.ok(!(key in materials), `${key} が復活している`);
  });

  test(`AdditiveBlending は glowW の1個だけ（${quality}）`, () => {
    const additive = Object.entries(materials)
      .filter(([, m]) => m && m.blending === THREE.AdditiveBlending).map(([k]) => k);
    assert.deepEqual(additive, ["glowW"], `Additive な材質: ${additive.join(",")}`);
  });

  test(`emissiveIntensity>1 は sky / lampWarm / screen* だけ（${quality}）`, () => {
    const hot = Object.entries(materials)
      .filter(([, m]) => m && typeof m.emissiveIntensity === "number" && m.emissiveIntensity > 1)
      .map(([k]) => k);
    for (const key of hot) assert.match(key, MAY_GLOW, `${key} が発光している（板グローの兆候）`);
  });

  test(`materials は 64 個以下（${quality}）`, () => {
    assert.ok(Object.keys(materials).length <= 64);
  });
}
