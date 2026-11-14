import test from "node:test";
import assert from "node:assert/strict";
import { BOT_RECORD_POLL_SEC, activityMotionSignature, activityPaintKey, botRecordDue, createActivityFeed, createActivityPage, mergeActivityPage, samePaint } from "../ui/hud/activity_feed.js";

const member = (extra = {}) => ({ session: "s1", state: "working", feed: ["実行中 テスト"], age: 0, ...extra });
const response = (extra = {}) => ({ ok: true, rows: [{ k: "a", t: "回答", ts: 1 }], cur: { off: 10, h: "h1" },
  beg: { off: 0, h: "h1", i: 0 }, top: false, ...extra });

test("差分を末尾、遡りを先頭に足し、遡りで最新のカーソルを変えない", () => {
  const empty = createActivityPage();
  const initial = mergeActivityPage(empty, response());
  const tail = mergeActivityPage(initial, response({ rows: [{ k: "a", t: "新しい回答" }], cur: { off: 20, h: "h1" }, top: true }));
  assert.equal(empty.rows.length, 0);
  assert.deepEqual(tail.rows.map((r) => r.t), ["回答", "新しい回答"]);
  assert.deepEqual(tail.beg, initial.beg);
  assert.equal(tail.top, false);
  const older = mergeActivityPage(tail, response({ rows: [{ k: "u", t: "古い指示" }], cur: { off: 4, h: "h1" }, beg: null, top: true }), { before: true });
  assert.deepEqual(older.rows.map((r) => r.t), ["古い指示", "回答", "新しい回答"]);
  assert.deepEqual(older.cur, tail.cur);
  assert.equal(older.top, true);
  assert.equal(older.beg, null);
});

test("reset は本文とカーソル・gap を置き換え、不正な返事なら元のページを残す", () => {
  const initial = mergeActivityPage(createActivityPage(), response({ gap: true }));
  const reset = mergeActivityPage(initial, response({ reset: true, rows: [{ k: "u", t: "新しい窓" }], gap: false }), { before: true });
  assert.deepEqual(reset.rows.map((r) => r.t), ["新しい窓"]);
  assert.equal(reset.gap, false);
  assert.equal(mergeActivityPage(initial, { ok: false }), initial);
  assert.deepEqual(mergeActivityPage(createActivityPage(), response({ rows: null })).rows, []);
});

test("同じ状態では取得せず、変化は3秒間引きし、セッション切替ではすぐ取得する", async () => {
  let tick = 0;
  const requests = [];
  const feed = createActivityFeed({ now: () => tick, fetchActivity: async (...args) => { requests.push(args); return response(); } });
  assert.equal(await feed.refresh(member()), true);
  tick = 5;
  assert.equal(await feed.refresh(member({ age: 5 })), false);
  tick = 6;
  assert.equal(await feed.refresh(member({ feed: ["実行中 次の検査"] })), true);
  tick = 8;
  assert.equal(await feed.refresh(member({ feed: ["編集中 変更中"] })), false);
  tick = 9;
  assert.equal(await feed.refresh(member({ feed: ["編集中 変更中"] })), true);
  assert.equal(await feed.refresh(member({ session: "s2" })), true);
  assert.equal(requests.length, 4);
  assert.deepEqual(requests[0], ["s1", { after: null }]);
  assert.deepEqual(requests[1], ["s1", { after: { off: 10, h: "h1" } }]);
});

test("age が巻き戻った場合も再取得し、毎回の増加は進捗の変化とみなさない", async () => {
  let tick = 0, calls = 0;
  const feed = createActivityFeed({ now: () => tick, fetchActivity: async () => { calls++; return response(); } });
  await feed.refresh(member({ age: 12 }));
  tick = 4;
  await feed.refresh(member({ age: 20 }));
  tick = 8;
  await feed.refresh(member({ age: 2 }));
  assert.equal(calls, 2);
});

test("遡りカーソルを使い、先頭に達したら更なる要求をしない", async () => {
  const requests = [];
  const feed = createActivityFeed({ fetchActivity: async (...args) => {
    requests.push(args); return response(requests.length === 1 ? {} : { rows: [{ k: "u", t: "昔" }], top: true, beg: null });
  } });
  assert.equal(await feed.load(member(), { before: true }), false);
  await feed.load(member());
  await feed.load(member(), { before: true });
  assert.deepEqual(requests[1], ["s1", { before: { off: 0, h: "h1", i: 0 } }]);
  assert.equal(feed.page("s1").rows[0].t, "昔");
  assert.equal(await feed.load(member(), { before: true }), false);
  assert.equal(requests.length, 2);
});

test("固定時刻とデモは取得・遡りともネットワークを呼ばない", async () => {
  for (const mode of [{ frozen: true }, { demo: true }]) {
    let calls = 0;
    const feed = createActivityFeed({ ...mode, fetchActivity: async () => { calls++; return response(); } });
    await feed.refresh(member()); await feed.load(member()); await feed.load(member(), { before: true });
    assert.equal(calls, 0);
  }
});

test("取得中は重複要求をせず、破棄後に届いた返事で更新しない", async () => {
  let finish, calls = 0;
  const changes = [];
  const feed = createActivityFeed({ fetchActivity: () => { calls++; return new Promise((resolve) => { finish = resolve; }); },
    onChange: (...args) => changes.push(args) });
  const first = feed.load(member());
  assert.equal(feed.busy, true);
  assert.equal(await feed.load(member({ session: "s2" })), false);
  assert.equal(calls, 1);
  feed.dispose(); finish(response());
  assert.equal(await first, false);
  assert.equal(feed.page("s1").loaded, false);
  assert.equal(feed.busy, false);
  assert.equal(changes.length, 0);
});

test("通信エラーで取得済み本文を消さず、失敗後も再試行できる", async () => {
  let failed = false;
  const changes = [];
  const feed = createActivityFeed({ fetchActivity: () => { if (failed) throw new Error("offline"); return response(); },
    onChange: (session, page, meta) => changes.push({ session, page, meta }) });
  await feed.load(member()); failed = true;
  assert.equal(await feed.load(member()), false);
  assert.equal(feed.page("s1").rows[0].t, "回答");
  assert.equal(feed.busy, false);
  failed = false;
  assert.equal(await feed.load(member()), true);
  assert.equal(changes.at(-1).meta.settled, true);
});

test("状態・質問・要求ID・作業中の手も変化の印へ含める", () => {
  const base = activityMotionSignature(member());
  for (const extra of [{ state: "idle" }, { question: "質問" }, { ask: { askId: "new" } }, { work: { now: ["テスト"] } }])
    assert.notEqual(activityMotionSignature(member(extra)), base);
});

test("R128: Bot は未読の印（pending）の替わり目でも記録を取り直す（人の指示で 1 行増えても Bot の状態は変わらない）・セッションは今までどおり", () => {
  const bot = (extra = {}) => member({ session: "bot-mailbox", vendor: "other", feed: [], ...extra });
  assert.notEqual(activityMotionSignature(bot({ pending: true })), activityMotionSignature(bot({ pending: false })));
  assert.notEqual(activityMotionSignature(member({ session: "s", vendor: "bot", pending: true })),
    activityMotionSignature(member({ session: "s", vendor: "bot", pending: false })));
  assert.equal(activityMotionSignature(member({ pending: true })), activityMotionSignature(member({ pending: false })));
});

test("R128: Bot の記録は、行が何も変わらなくても選んでいる間は定期に差分を取りに行く（未読が立ったままの 2 通目・別の画面からの答え）・セッションは取りに行かない", async () => {
  // 別モデルレビュー（R128 #3）: 取り直しの印が未読の印（真偽）だけだった＝既に未読がある Bot への 2 通目は、記録に 1 行増えても画面に出なかった
  let tick = 0;
  const requests = [];
  const feed = createActivityFeed({ now: () => tick, fetchActivity: async (...args) => { requests.push(args); return response({ rows: [] }); } });
  const bot = (extra = {}) => member({ session: "bot-mailbox", vendor: "bot", state: "waiting", feed: [], pending: true, mtime: 100, ...extra });
  assert.equal(await feed.refresh(bot()), true);                       // 最初の 1 枚
  tick = BOT_RECORD_POLL_SEC - 1;
  assert.equal(await feed.refresh(bot()), false, "間隔より前に取りに行った");
  tick = BOT_RECORD_POLL_SEC;
  assert.equal(await feed.refresh(bot()), true, "行が同じままの Bot の記録を取り直さなかった（2 通目の指示が出ない）");
  assert.deepEqual(requests[1], ["bot-mailbox", { after: { off: 10, h: "h1" } }], "差分のカーソルで取りに行っていない");
  tick += BOT_RECORD_POLL_SEC;
  assert.equal(await feed.refresh(bot()), true);
  // Bot が動いた（報告・相談＝mtime が替わる）ら、3 秒の間引きの後すぐ
  tick += 3;
  assert.equal(await feed.refresh(bot({ mtime: 130 })), true, "Bot が最後に動いた時刻の替わり目で取り直さなかった");
  // セッションは今までどおり（同じ行なら何秒たっても取りに行かない）
  const calm = createActivityFeed({ now: () => tick, fetchActivity: async (...args) => { requests.push(args); return response(); } });
  assert.equal(await calm.refresh(member()), true);
  tick += 600;
  assert.equal(await calm.refresh(member()), false, "セッションを定期に取りに行った");
  // 判定の関数（cockpit も同じ関数を使う）
  assert.equal(botRecordDue(bot(), { session: "bot-mailbox", at: 0 }, BOT_RECORD_POLL_SEC), true);
  assert.equal(botRecordDue(bot(), { session: "bot-other", at: 0 }, 999), false);
  assert.equal(botRecordDue(bot(), null, 999), false);
  assert.equal(botRecordDue(member(), { session: "s1", at: 0 }, 999), false);
  assert.notEqual(activityMotionSignature(bot({ mtime: 100 })), activityMotionSignature(bot({ mtime: 130 })));
  assert.equal(activityMotionSignature(member({ mtime: 100 })), activityMotionSignature(member({ mtime: 130 })));
});

test("描き直すかの印: 毎回替わる数（age・mtime・feed）では替わらず、描き手が読む項目と extra で替わる", () => {
  const base = member({ attention: true, question: "進めてよい？", questionOptions: [{ label: "はい" }], ask: { askId: "a".repeat(32), kind: "question" } });
  const key = activityPaintKey(base, ["ja", "質問 · いま"]);
  assert.equal(activityPaintKey({ ...base, age: 99, mtime: 123, feed: ["別の行"], lastSaid: "別" }, ["ja", "質問 · いま"]), key);
  for (const changed of [{ session: "s2" }, { state: "idle" }, { attention: false }, { question: "別の問い" },
    { questionOptions: [{ label: "いいえ" }] }, { ask: { askId: "b".repeat(32), kind: "question" } }]) {
    assert.notEqual(activityPaintKey({ ...base, ...changed }, ["ja", "質問 · いま"]), key, JSON.stringify(changed));
  }
  assert.notEqual(activityPaintKey(base, ["ja", "質問 · 1分"]), key, "待ち時間の文が替われば描き直す");
  assert.notEqual(activityPaintKey(base, ["en", "質問 · いま"]), key, "言葉が替われば描き直す");
});

test("前回の節がそのまま残っていて、行（参照）と印が同じときだけ描き直さない", () => {
  const first = { id: 1 }, last = { id: 2 }, rows = [];
  const host = { firstElementChild: first, lastElementChild: last };
  const painted = { host, rows, key: "k", first, last };
  assert.equal(samePaint(painted, host, rows, "k"), true);
  assert.equal(samePaint(null, host, rows, "k"), false);
  assert.equal(samePaint(painted, host, [...rows], "k"), false, "取り直した行（新しい配列）は描き直す");
  assert.equal(samePaint(painted, host, rows, "k2"), false);
  assert.equal(samePaint(painted, { ...host }, rows, "k"), false, "別の host");
  assert.equal(samePaint(painted, { firstElementChild: { id: 3 }, lastElementChild: last }, rows, "k"), false);
  host.lastElementChild = { id: 4 };
  assert.equal(samePaint(painted, host, rows, "k"), false, "ほかの描き手が中身を替えたら描き直す");
});
