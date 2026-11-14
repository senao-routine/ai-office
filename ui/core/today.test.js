import assert from "node:assert/strict";
import { test } from "node:test";
import { todayFileName, todaySummary } from "./today.js";

const digest = {
  day: "2026-11-14", available: true,
  sessions: [
    { sid: "s1", vendor: "claude", name: "秘密のプロジェクト", projectId: "abc123", commits: 3 },
    { sid: "s2", vendor: "codex", name: "顧客 A の見積もり", projectId: "def456" },
    { sid: "s3", vendor: "codex", name: "x" },
    { sid: "s4", vendor: "openclaw", name: "y" },
    { sid: "s5", vendor: "", name: "z" },
  ],
  totals: { commits: 126, asksAnswered: 27, activeMin: 3244.9, tools: { Bash: 7926, Edit: 1220, Read: 653, Bad: -5, Nan: "x" } },
};

test("今日の 1 枚: 数だけを取り出す（セッション・ベンダー別・commit・答えた相談・時間・道具の回数）", () => {
  const s = todaySummary(digest, { bots: 2 });
  assert.equal(s.day, "2026-11-14");
  assert.equal(s.sessions, 5);
  assert.deepEqual(s.vendors, { claude: 1, codex: 2, openclaw: 1, other: 1 });
  assert.equal(s.bots, 2);
  assert.equal(s.commits, 126);
  assert.equal(s.answered, 27);
  assert.equal(s.activeHours, 54.1);
  assert.equal(s.tools, 7926 + 1220 + 653, "負の数・数でない値は足さない");
  assert.equal(s.available, true);
});

test("今日の 1 枚: 名前・プロジェクト・sid は返す形に入らない（SNS に出せる材料だけ）", () => {
  const text = JSON.stringify(todaySummary(digest));
  for (const secret of ["秘密のプロジェクト", "顧客 A", "abc123", "def456", "s1", "sid", "name", "project"]) {
    assert.ok(!text.includes(secret), `${secret} が漏れた: ${text}`);
  }
});

test("今日の 1 枚: 壊れた入力でも落ちずにゼロ（読めない日付は空・ファイル名は today）", () => {
  for (const bad of [null, undefined, {}, { sessions: "x", totals: 3, day: "../etc" }, { totals: { tools: null, activeMin: -4 } }]) {
    const s = todaySummary(bad, { bots: -1 });
    assert.equal(s.sessions, 0);
    assert.equal(s.tools, 0);
    assert.equal(s.activeHours, 0);
    assert.equal(s.bots, 0);
    assert.equal(s.day, "");
    assert.equal(s.available, false);
  }
  assert.equal(todayFileName("2026-11-14"), "ai-office-2026-11-14.png");
  assert.equal(todayFileName("../x"), "ai-office-today.png");
  assert.equal(todayFileName(""), "ai-office-today.png");
});
