import { test } from "node:test";
import assert from "node:assert/strict";
import { archetypeFor } from "./archetype.js";

test("キーワードで職業が決まる（名前/role/deptのどれでも）", () => {
  assert.equal(archetypeFor({ name: "動画編集ライン" }).kind, "video");
  assert.equal(archetypeFor({ name: "ブログ自動化" }).kind, "writer");     // writer が ops より先勝ち
  assert.equal(archetypeFor({ role: "LP制作", name: "x" }).kind, "design");
  assert.equal(archetypeFor({ name: "経費申請bot" }).kind, "ops");
  assert.equal(archetypeFor({ name: "AIオフィス開発" }).kind, "dev");
});

test("該当なしは generic＝アクセサリ無し・ハッシュで決まる淡色（決定論）", () => {
  const a = archetypeFor({ name: "こんにちは", id: "aaa111" });
  const b = archetypeFor({ name: "こんにちは", id: "aaa111" });
  assert.equal(a.kind, "generic");
  assert.equal(a.acc, null);
  assert.deepEqual(a.tint, b.tint);
  assert.equal(a.tint.length, 3);
  for (const v of a.tint) assert.ok(v >= 0.85 && v <= 1.0, "淡色レンジ＝白ロボの人格を保つ");
});

test("tint/acc は 0..1 のRGB", () => {
  for (const name of ["動画", "ブログ", "デザイン", "運用", "開発"]) {
    const r = archetypeFor({ name });
    for (const v of r.tint) assert.ok(v >= 0 && v <= 1);
    if (r.acc) for (const v of r.acc) assert.ok(v >= 0 && v <= 1);
  }
});
