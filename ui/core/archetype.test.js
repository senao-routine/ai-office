import { test } from "node:test";
import assert from "node:assert/strict";
import { archetypeFor, SHELL_COLORS } from "./archetype.js";

test("キーワードで職業が決まる（名前/role/deptのどれでも）", () => {
  assert.equal(archetypeFor({ name: "動画編集ライン" }).kind, "video");
  assert.equal(archetypeFor({ name: "ブログ自動化" }).kind, "writer");     // writer が ops より先勝ち
  assert.equal(archetypeFor({ role: "LP制作", name: "x" }).kind, "design");
  assert.equal(archetypeFor({ name: "経費申請bot" }).kind, "ops");
  assert.equal(archetypeFor({ name: "AIオフィス開発" }).kind, "dev");
  // R80.8 拡充ぶん
  assert.equal(archetypeFor({ name: "ポッドキャスト編集" }).kind, "audio");   // audioがvideoの「編集」より先
  assert.equal(archetypeFor({ name: "リサーチ部" }).kind, "research");
  assert.equal(archetypeFor({ name: "収益ダッシュボード" }).kind, "finance");
  assert.equal(archetypeFor({ name: "広報チーム" }).kind, "support");
  assert.equal(archetypeFor({ name: "CI移行" }).kind, "infra");
  assert.equal(archetypeFor({ name: "台本チーム" }).kind, "writer");
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

test("R91: 殻の色は職業判定と直交する（帽子は帽子のまま・未知の色は無視）", () => {
  const dev = archetypeFor({ id: "a", name: "開発ツール" });
  const tinted = archetypeFor({ id: "a", name: "開発ツール", color: "sage" });
  assert.equal(tinted.kind, dev.kind);
  assert.deepEqual(tinted.acc, dev.acc);
  assert.deepEqual(tinted.tint, SHELL_COLORS.sage.tint);
  assert.equal(tinted.color, "sage");
  // 明示アクセサリと併用しても両方効く
  const both = archetypeFor({ id: "a", name: "開発ツール", arch: "beret", color: "clay" });
  assert.equal(both.kind, "design");
  assert.deepEqual(both.tint, SHELL_COLORS.clay.tint);
  // 未知の色は既定へ戻る（サーバーが弾いた後の最後の砦）。
  // 継承プロパティ名（toString/constructor 等）でも崩れない＝own-prop 索引
  for (const bad of ["neon", "toString", "constructor", "__proto__", 3, null]) {
    assert.deepEqual(archetypeFor({ id: "a", name: "開発ツール", color: bad }), dev, String(bad));
  }
  // スウォッチは全色そろっている（カスタマイズ画面が空チップを出さない）
  for (const [name, v] of Object.entries(SHELL_COLORS)) {
    assert.match(v.swatch, /^#[0-9a-f]{6}$/, name);
    assert.equal(v.tint.length, 3, name);
  }
});
