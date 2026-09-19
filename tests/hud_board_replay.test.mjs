// R98-W2: リプレイの行は「過去の世界」から作る（live の roster で状態を上書きしない）。
// かつ live と同じプロジェクト分け・ベンダーを保つ（別モデルレビューで踏んだ 2 件）。
import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
const root = new URL("../", import.meta.url);
registerHooks({ resolve(specifier, context, next) {
  return next(specifier.startsWith("/ui/") ? new URL(specifier.slice(1), root).href : specifier, context);
} });
const { boardFromWorld, departmentBoard } = await import("../ui/hud/board.js");
const { buildWorld } = await import("../ui/core/world.js");
const T = (key, ...args) => `${key}${args.length ? ":" + args.join(",") : ""}`;

const office = {
  lang: "ja",
  roster: [{ projectId: "p1", session: "s1", name: "works", cwd: "/w", vendor: "claude", state: "working",
             crew: 2, sessions: [{ session: "s1", state: "working" }, { session: "s2", state: "working" }] }],
  employees: [{ session: "s1", dept: "works", cwd: "/w", vendor: "claude", state: "working"},
              { session: "s2", dept: "works", cwd: "/w", vendor: "codex", state: "working"}],
};

test("boardFromWorld: live と同じプロジェクトへ畳む（session モードで行が割れない）", () => {
  const live = buildWorld(office);
  const liveBoard = departmentBoard(office, live, T);
  const keys = new Map(liveBoard.flatMap(
    (g) => g.sessions.map((s) => [s.session, { key: g.key, name: g.name, vendor: s.vendor }])));
  // 過去の世界: 2 セッションとも別 id（createReplay の replay:<sid> 相当）
  const past = buildWorld({ employees: [
    { session: "s1", dept: "works", state: "resting", vendor: "claude" },
    { session: "s2", dept: "works", state: "waiting", vendor: "codex" },
  ] });
  const board = boardFromWorld(past, T, keys);
  assert.equal(board.length, 1, "1 プロジェクトのまま");
  assert.equal(board[0].name, liveBoard[0].name);
  assert.deepEqual(board[0].sessions.map((s) => s.state).sort(), ["resting", "waiting"]);
  assert.deepEqual(board[0].sessions.map((s) => s.vendor).sort(), ["claude", "codex"]);
});

test("boardFromWorld: 状態は過去のまま・証拠は出さない（いまの事実を過去に貼らない）", () => {
  const past = buildWorld({ employees: [
    { session: "s1", dept: "works", state: "resting", evidence: { kind: "committed", ago: 3 } },
  ] });
  const board = boardFromWorld(past, T, new Map());
  assert.equal(board[0].sessions[0].state, "resting");
  assert.equal(board[0].sessions[0].evidence, null);
});

test("boardFromWorld: 現在の作業内容（verb/target/work/feed）を過去の行に貼らない", () => {
  // createReplay は代表セッションの姿を複製する＝いまの verb/target が付いてくる
  const past = buildWorld({ employees: [{
    session: "s1", dept: "works", state: "waiting", kind: "idle",
    verb: "編集中", target: "office_server.py", feed: ["編集中 office_server.py"],
    work: { now: ["いまの作業"], counts: { completed: 3, pending: 1 } },
  }] });
  const row = boardFromWorld(past, T, new Map())[0].sessions[0];
  for (const bad of ["編集中", "office_server", "いまの作業"]) {
    assert.ok(!String(row.detail).includes(bad), `過去の行に現在の文言が出ている: ${row.detail}`);
  }
  assert.equal(row.work, null, "進捗も過去には出さない");
  assert.deepEqual(row.feed, []);
  assert.equal(row.listening, true, "配達の状態も「いまの事実」＝過去には出さない");
  assert.equal(row.pending, false);
});

test("boardFromWorld: 非代表セッションにも本人の名前とベンダーを出す", () => {
  // 過去の世界では代表の姿が複製されている（name も vendor も代表のもの）
  const past = buildWorld({ employees: [
    { session: "lead", dept: "works", title: "代表", vendor: "claude", state: "working" },
    { session: "sub", dept: "works", title: "代表", vendor: "claude", state: "working" },
  ] });
  const live = new Map([
    ["lead", { key: "k", name: "works", sessionName: "代表", vendor: "claude" }],
    ["sub", { key: "k", name: "works", sessionName: "Codex 3号", vendor: "codex" }],
  ]);
  const board = boardFromWorld(past, T, live);
  assert.equal(board.length, 1);
  const bySession = Object.fromEntries(board[0].sessions.map((s) => [s.session, s]));
  assert.equal(bySession.sub.name, "Codex 3号");
  assert.equal(bySession.sub.vendor, "codex");
  assert.equal(bySession.lead.vendor, "claude");
});

test("boardFromWorld: イベントの無いプロジェクトも内訳（sessions）を開く", () => {
  // createReplay は触っていないプロジェクトを「代表 ＋ sessions[]」のまま残す。
  // 代表だけ出すと非代表セッションが台帳から消える（別モデルレビュー）。
  const past = buildWorld({ roster: [{
    projectId: "p1", session: "lead", name: "works", cwd: "/w", vendor: "claude", state: "working", crew: 2,
    sessions: [{ session: "lead", state: "working" }, { session: "sub", state: "resting" }],
  }] });
  const live = new Map([
    ["lead", { key: "k", name: "works", sessionName: "代表", vendor: "claude" }],
    ["sub", { key: "k", name: "works", sessionName: "Codex 3号", vendor: "codex" }],
  ]);
  const board = boardFromWorld(past, T, live);
  assert.equal(board.length, 1);
  const sessions = board[0].sessions.map((s) => s.session).sort();
  assert.deepEqual(sessions, ["lead", "sub"], "非代表が消えている");
  const sub = board[0].sessions.find((s) => s.session === "sub");
  assert.equal(sub.state, "resting");
  assert.equal(sub.vendor, "codex");
  assert.equal(sub.name, "Codex 3号");
});

test("boardFromWorld: 内訳 1 件でも本人の状態を出す・代表の質問を配らない", () => {
  // 非代表が分離された後の代表は sessions が 1 件になる。集約時の状態が残ると
  // 「誰も作業していないのに作業中」になる（別モデルレビュー）。
  const past = buildWorld({ roster: [{
    projectId: "p1", session: "lead", name: "works", state: "working", crew: 1,
    question: "この設計でいいですか", approvalMin: 12,
    sessions: [{ session: "lead", state: "resting", attention: false }],
  }] });
  const row = boardFromWorld(past, T, new Map())[0].sessions[0];
  assert.equal(row.state, "resting");
  assert.equal(row.attention, false);
  assert.equal(row.question, "");
  assert.equal(row.approvalMin, 0);
  assert.notEqual(row.zone, "queue");
  assert.ok(!String(row.detail).includes("この設計"), `代表の質問が出ている: ${row.detail}`);
});

test("boardFromWorld: 代表が質問中でも、休憩中の非代表には出さない", () => {
  const past = buildWorld({ roster: [{
    projectId: "p1", session: "lead", name: "works", state: "waiting", crew: 2,
    question: "消していいですか", approvalMin: 30,
    sessions: [{ session: "lead", state: "waiting", attention: true },
               { session: "sub", state: "resting", attention: false }],
  }] });
  const rows = Object.fromEntries(
    boardFromWorld(past, T, new Map())[0].sessions.map((s) => [s.session, s]));
  assert.equal(rows.sub.attention, false);
  assert.equal(rows.sub.question, "");
  assert.ok(!String(rows.sub.detail).includes("消していい"), `非代表に質問が出ている: ${rows.sub.detail}`);
  assert.equal(rows.lead.attention, true);
});

test("boardFromWorld: createReplay が落とした非代表セッションを live から戻す", () => {
  // 代表にイベントが在ると createReplay は代表単体（sessions: []）に置き換える＝非代表が消える
  const past = buildWorld({ employees: [
    { session: "lead", dept: "works", state: "waiting", projectId: "p1" },
  ] });
  past.agents[0].id = "p1";
  const live = new Map([
    ["lead", { key: "p1", name: "works", sessionName: "代表", vendor: "claude",
      brief: { state: "waiting", age: 10, attention: false } }],
    ["sub", { key: "p1", name: "works", sessionName: "Codex 3号", vendor: "codex",
      brief: { state: "working", age: 30, attention: false } }],
    ["other", { key: "p9", name: "別プロジェクト", sessionName: "x", vendor: "claude",
      brief: { state: "working", age: 5, attention: false } }],
  ]);
  const board = boardFromWorld(past, T, live);
  // イベントの無いセッションは「変わっていない」＝居たはず。プロジェクトごと画面に無くても戻す
  //（代表が再生の途中で参加したプロジェクトでは、既存メンバーまで消えていた・別モデルレビュー）
  assert.deepEqual(board.map((g) => g.key).sort(), ["p1", "p9"]);
  const p1 = board.find((g) => g.key === "p1");
  const sessions = p1.sessions.map((s) => s.session).sort();
  assert.deepEqual(sessions, ["lead", "sub"]);
  const sub = p1.sessions.find((s) => s.session === "sub");
  assert.equal(sub.vendor, "codex");
  assert.equal(sub.state, "working");
  assert.equal(sub.evidence, null);
  assert.equal(sub.question, "");
});

test("boardFromWorld: 再生の途中で始まった/終わったセッションは補わない", () => {
  const past = buildWorld({ employees: [{ session: "lead", dept: "works", state: "waiting", projectId: "p1" }] });
  past.agents[0].id = "p1";
  const live = new Map([
    ["lead", { key: "p1", name: "works", sessionName: "代表", vendor: "claude",
      brief: { state: "waiting", age: 10, attention: false } }],
    ["late", { key: "p1", name: "works", sessionName: "あとから来た人", vendor: "claude",
      brief: { state: "working", age: 1, attention: false } }],
  ]);
  // late は再生区間の途中で hire された＝この時点では居ない（イベントが在る相手は補わない）
  const board = boardFromWorld(past, T, live, new Set(["late"]));
  assert.deepEqual(board.flatMap((g) => g.sessions.map((s) => s.session)), ["lead"]);
  // イベントが無い相手（eventSids に無い）は従来どおり補う
  const filled = boardFromWorld(past, T, live, new Set(["lead"]));
  assert.deepEqual(filled[0].sessions.map((s) => s.session).sort(), ["late", "lead"]);
});

test("boardFromWorld: live の情報が無くても落ちない", () => {
  assert.deepEqual(boardFromWorld(null, T), []);
  assert.deepEqual(boardFromWorld({ agents: [] }, T), []);
  const board = boardFromWorld(buildWorld({ employees: [{ session: "x", dept: "" }] }), T);
  assert.equal(board.length, 1);
  assert.ok(board[0].name);
});
