import { test } from "node:test";
import assert from "node:assert/strict";
import { ARCHETYPE_RULES, archetypeFor, roomArchetype, SHELL_COLORS } from "./archetype.js";

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


test("roomArchetype（R110-S2）: name/role/dept → projectName と cwd の末尾のフォルダ名 → work.now[0] の順・該当なしは generic", () => {
  // 1 段目（3D と同じ文字列）が勝つ
  assert.equal(roomArchetype({ name: "動画編集部", projectName: "ブログ", cwd: "/w/blog" }), "video");
  assert.equal(roomArchetype({ role: "記事制作", name: "x" }), "writer");
  // 2 段目: projectName・cwd の末尾だけ（親のフォルダ名でユーザー名や develop を拾わない）
  assert.equal(roomArchetype({ name: "x", projectName: "リサーチ室" }), "research");
  assert.equal(roomArchetype({ name: "x", cwd: "/Users/someone/work/video-cut" }), "video");
  assert.equal(roomArchetype({ name: "x", cwd: "/Users/dev/blog/zzz/" }), "generic", "親のフォルダ名（dev・blog）で決めた");
  assert.equal(roomArchetype({ name: "x", projectKey: "cwd:/Users/a/podcast-studio" }), "audio", "cwd が無いときは projectKey の cwd:");
  assert.equal(roomArchetype({ name: "x", projectKey: "id:podcast" }), "generic", "cwd でない projectKey を読んだ");
  // 3 段目: いまの作業の 1 行目
  assert.equal(roomArchetype({ name: "x", work: { now: ["サムネの差し替え", "ブログ"] } }), "design");
  assert.equal(roomArchetype({ name: "x", work: { now: [] } }), "generic");
  // 前の段で当たれば後ろは見ない（同じ段の中は ARCHETYPE_RULES の先勝ち＝3D と同じ順）
  assert.equal(roomArchetype({ name: "経費申請bot", work: { now: ["動画"] } }), "ops");
  assert.equal(roomArchetype({ name: "ブログ自動化" }), "writer", "writer が ops より先勝ち（3D と同じ順）");
  for (const kind of ARCHETYPE_RULES.map(([k]) => k)) assert.ok(typeof kind === "string");
  // 該当なし・壊れた入力は generic（嘘の帽子を被せない）・カスタマイズの arch は読まない
  for (const a of [null, undefined, 3, {}, { name: "こんにちは" }, { name: 5, cwd: 7, work: { now: [9] } }]) {
    assert.equal(roomArchetype(a), "generic", JSON.stringify(a));
  }
  assert.equal(roomArchetype({ name: "こんにちは", arch: "beret" }), "generic");
  // 3D（archetypeFor）とは独立（同じ名前なら同じ職業・cwd では 3D は変わらない）
  assert.equal(archetypeFor({ name: "x", cwd: "/w/video-cut" }).kind, "generic");
});
