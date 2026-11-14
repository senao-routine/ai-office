import test from "node:test";
import assert from "node:assert/strict";
import { sessionLine } from "./session_line.js";

const words = { read: "ファイルを読んでいます", edit: "ファイルを直しています", write: "ファイルを作っています",
  run: "作業を進めています", think: "次の進め方を考えています", rest: "休憩中", ask: "確認を待っています" };
const line = (member, attention = "") => sessionLine(member, "ja", attention, words);
const working = (feed, extra = {}) => ({ state: "working", feed: [feed], ...extra });

test("あなたの番は attnLine を最優先にし、一行だけにする", () => {
  assert.equal(line({ attention: true, question: "古い質問", feed: ["編集中 index.js"] }, "❗ 進め方を選んでください\n続き"), "進め方を選んでください");
  assert.equal(line({ attention: true, question: "ボタンを付けますか？\n補足" }), "ボタンを付けますか？");
  assert.equal(line({ ask: { kind: "permission", title: "コマンドの許可" } }), "コマンドの許可");
  assert.equal(line({ attention: true }), words.ask);
});

test("作業中は feed 先頭の動詞だけを除き、文章の説明を残す", () => {
  assert.equal(line(working("実行中 スマホの幅で画面を確かめています")), "スマホの幅で画面を確かめています");
  assert.equal(line(working("段取り整理中 保存先の切替 3 件")), "保存先の切替 3 件");
  assert.equal(line(working({ text: "読込中： 画面の構成を調べています" })), "画面の構成を調べています");
  assert.equal(line(working({ t: "調査中 次の進め方を確かめています\n別の行" })), "次の進め方を確かめています");
});

test("パスとファイル名は動詞に応じた文章へ置き換え、一覧に漏らさない", () => {
  const cases = [
    ["読込中 src/login.js", words.read], ["読込 README.md", words.read],
    ["編集中 router.ts", words.edit], ["編集中 src/views/画面", words.edit],
    ["作成中 report.txt", words.write], ["書込中 `new_file.json`", words.write],
    ["実行中 tools/check.py", words.run], ["編集中 C:\\work\\view.js", words.edit],
  ];
  for (const [text, expected] of cases) assert.equal(line(working(text)), expected, text);
});

test("feed が無い場合は verb と target、tool を使い、本文が無ければ考え中", () => {
  assert.equal(line({ state: "working", verb: "編集中", target: "router.ts" }), words.edit);
  assert.equal(line({ state: "working", target: "README.md", tool: "Read" }), words.read);
  assert.equal(line(working("実行中")), words.think);
  assert.equal(line({ state: "working" }), words.think);
});

test("作業中の発言と休憩中の lastSaid は一行目を引用する", () => {
  assert.equal(line(working("💬 ログイン画面を直しました\n次の行")), "「ログイン画面を直しました」");
  assert.equal(line({ state: "waiting", lastSaid: "直しました\n次の行", feed: ["編集中 old.js"] }), "「直しました」");
  assert.equal(line({ state: "idle" }), words.rest);
  assert.equal(sessionLine({ state: "idle", lastSaid: "Done\nDetails" }, "en", "", words), "“Done”");
});

test("補助の人が動いているときも近況の説明を使い、入力を書き換えない", () => {
  const member = { state: "idle", minionsLive: 2, feed: [{ text: "編集中 a.ts" }], lastSaid: "以前の報告" };
  const snapshot = JSON.stringify(member);
  assert.equal(line(member), words.edit);
  assert.equal(JSON.stringify(member), snapshot);
  assert.equal(line(null), words.rest);
});
