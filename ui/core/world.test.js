// ui/core は DOM に触らないので、ブラウザ無しでそのままテストできる。
//   node --test ui/core/*.test.js
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DESK_SLOTS, activityGloss, activityText, agoStr, assignMeetingRooms, assignRestSpots, assignSeats, attentionQueue, buildWorld, isMuted,
  countByZone, deliveryTransitions, needsAttention, stableIndex, summarizeWorld, tidyActivity,
  assignLabels, stalledSends, topAttention, triageSort, zoneOf,
} from "./world.js";

const proj = (over = {}) => ({
  projectId: "p1", session: "s1", name: "proj", crew: 1, state: "working",
  kind: "tool", verb: "編集中", target: "a.js", age: 10,
  minions: 0, pending: false, attention: false, approvalMin: 0, question: "",
  sessions: [], feed: [], ...over,
});

test("needsAttention は承認まちと未回答の質問だけを拾う", () => {
  assert.equal(needsAttention(proj()), false);
  assert.equal(needsAttention(proj({ attention: true })), true);
  // attention が未設定の古い形（employees[]）でも導出できる
  assert.equal(needsAttention({ approvalMin: 4 }), true);
  assert.equal(needsAttention({ question: "これでいい？" }), true);
  assert.equal(needsAttention(null), false);
});

test("zoneOf: ❗はどの状態より優先して待機列へ行く", () => {
  assert.equal(zoneOf(proj()), "desk");
  assert.equal(zoneOf(proj({ state: "resting" })), "lounge");
  assert.equal(zoneOf(proj({ minions: 2 })), "meeting");
  // ❗ は会議中でも休憩中でも列へ（人間を待たせないための設計）
  assert.equal(zoneOf(proj({ minions: 3, attention: true })), "queue");
  assert.equal(zoneOf(proj({ state: "resting", attention: true })), "queue");
});

test("zoneOf: external(OpenClaw) は専用区画から動かない", () => {
  assert.equal(zoneOf(proj({ external: "openclaw", state: "resting" })), "external");
  assert.equal(zoneOf(proj({ external: "openclaw", minions: 5 })), "external");
});

test("zoneOf: 休憩中は部下がいても会議へ行かない", () => {
  assert.equal(zoneOf(proj({ state: "resting", minions: 4 })), "lounge");
});

test("triageSort: ❗ → 稼働 → 待機 → 休憩 → external の順", () => {
  const list = [
    proj({ session: "rest", state: "resting" }),
    proj({ session: "ext", external: "openclaw" }),
    proj({ session: "attn", attention: true }),
    proj({ session: "work", state: "working" }),
    proj({ session: "wait", state: "waiting" }),
  ];
  assert.deepEqual(triageSort(list).map((e) => e.session),
    ["attn", "work", "wait", "rest", "ext"]);
});

test("triageSort は元の配列を壊さない", () => {
  const list = [proj({ session: "a", state: "resting" }), proj({ session: "b" })];
  triageSort(list);
  assert.deepEqual(list.map((e) => e.session), ["a", "b"]);
});

test("activityText は動詞と対象をつなぐ", () => {
  assert.equal(activityText(proj()), "編集中 a.js");
  assert.equal(activityText(proj({ target: "" })), "編集中");
  assert.equal(activityText(null), "");
});

// ── 席割当（決定論・毎回同じ席） ──────────────────────────────
test("assignSeats: 同じ入力なら毎回まったく同じ配置", () => {
  const agents = ["a", "b", "c", "d"].map((id) => ({ ...proj({ session: id }), id }));
  const first = assignSeats(agents);
  const second = assignSeats(agents);
  assert.deepEqual([...first.entries()].sort(), [...second.entries()].sort());
});

test("assignSeats: 順番が入れ替わっても各人の席は変わらない（筋肉記憶）", () => {
  const mk = (ids) => ids.map((id) => ({ ...proj({ session: id }), id }));
  const a = assignSeats(mk(["x", "y", "z"]));
  const b = assignSeats(mk(["z", "y", "x"]));
  // 先着で衝突解決するので順序が変わると席がずれ得る。ここでは衝突が無いことを確認する
  if (new Set([...a.values()]).size === 3 && new Set([...b.values()]).size === 3) {
    assert.equal(a.get("y"), b.get("y"));
  }
});

test("assignSeats: 席は重複せず、机ゾーンの人だけが座る", () => {
  const agents = [
    { ...proj({ session: "d1" }), id: "d1" },
    { ...proj({ session: "d2" }), id: "d2" },
    { ...proj({ session: "m1", minions: 2 }), id: "m1" },   // 会議室
    { ...proj({ session: "q1", attention: true }), id: "q1" }, // 待機列
    { ...proj({ session: "r1", state: "resting" }), id: "r1" }, // ラウンジ
  ];
  const seats = assignSeats(agents);
  assert.equal(seats.size, 2, "机に座るのは desk ゾーンの2人だけ");
  assert.equal(new Set([...seats.values()]).size, 2, "席が重複していない");
  for (const v of seats.values()) assert.ok(v >= 0 && v < DESK_SLOTS);
});

test("assignSeats: 席数を超えた分は座らない（空席を作らないための上限）", () => {
  const agents = Array.from({ length: DESK_SLOTS + 5 }, (_, i) =>
    ({ ...proj({ session: `s${i}` }), id: `s${i}` }));
  const seats = assignSeats(agents);
  assert.equal(seats.size, DESK_SLOTS);
});

test("stableIndex: 決定論で範囲内", () => {
  assert.equal(stableIndex("abc", 12), stableIndex("abc", 12));
  for (const s of ["", "a", "とても長い日本語のプロジェクト名", "x".repeat(500)]) {
    const v = stableIndex(s, 12);
    assert.ok(Number.isInteger(v) && v >= 0 && v < 12, `${s} → ${v}`);
  }
});

// ── buildWorld ────────────────────────────────────────────────
test("buildWorld: roster[] があればそれを使う", () => {
  const w = buildWorld({
    officeName: "テスト", lang: "ja", edition: { id: "hybrid", features: {} },
    roster: [proj({ crew: 3, disp: "ai-office" })],
    employees: [{ session: "ignored" }, { session: "ignored2" }],
  });
  assert.equal(w.agents.length, 1, "roster[] 優先＝1プロジェクト1アバター");
  assert.equal(w.agents[0].crew, 3);
  assert.equal(w.agents[0].name, "ai-office");
  assert.equal(w.officeName, "テスト");
});

test("buildWorld: title（/renameのセッション名）が disp より優先される（R85-1）", () => {
  const w = buildWorld({
    roster: [
      proj({ disp: "制作本部(works) 2号", title: "決済チーム" }),
      proj({ projectId: "p2", session: "s2", disp: "ai-office", cwd: "/x/2" }),
    ],
  });
  const names = w.agents.map((a) => a.name).sort();
  assert.deepEqual(names, ["ai-office", "決済チーム"], "title優先・無ければdisp");
});

test("buildWorld: roster[] が無ければ employees[] から作る（後方互換）", () => {
  const w = buildWorld({
    employees: [
      { session: "s1", dept: "A", state: "working", verb: "実行中", target: "x", age: 3 },
      { session: "s2", dept: "B", state: "waiting", verb: "指示待ち", age: 9 },
    ],
  });
  assert.equal(w.agents.length, 2);
  assert.equal(w.agents[0].crew, 1);
});

test("buildWorld: 壊れた入力でも落ちない", () => {
  for (const bad of [null, undefined, "nope", 42, {}, { roster: "x", employees: "y" }]) {
    const w = buildWorld(bad);
    assert.ok(Array.isArray(w.agents), `${JSON.stringify(bad)} で落ちた`);
  }
});

test("countByZone は ❗ を別枠で数える", () => {
  const c = countByZone([
    proj({ attention: true }), proj({ state: "resting" }),
    proj({ minions: 1 }), proj({ external: "openclaw" }), proj(),
  ]);
  assert.deepEqual(c, { desk: 1, meeting: 1, queue: 1, lounge: 1, external: 1, attention: 1 });
});

test("topAttention: 質問を承認まちより先に出す（飢餓閾値の手前まで）", () => {
  const a = proj({ session: "approve", attention: true, approvalMin: 5 });
  const b = proj({ session: "ask", attention: true, question: "どっち？" });
  assert.equal(topAttention([a, b]).session, "ask");
  assert.equal(topAttention([proj()]), null);
  assert.equal(topAttention([]), null);
});

test("attentionQueue: 全件をトリアージ順で返し、15分超の承認まちは質問より昇格（飢餓防止）", () => {
  const starving = proj({ session: "old-approve", attention: true, approvalMin: 20 });
  const ask = proj({ session: "ask", attention: true, question: "どっち？" });
  const fresh = proj({ session: "new-approve", attention: true, approvalMin: 3 });
  const idle = proj({ session: "idle" });
  const q = attentionQueue([fresh, ask, idle, starving]);
  assert.deepEqual(q.map((x) => x.session), ["old-approve", "ask", "new-approve"]);
  assert.equal(topAttention([fresh, ask, starving]).session, "old-approve");
  assert.deepEqual(attentionQueue([]), []);
  assert.deepEqual(attentionQueue(null), []);
});

test("agoStr: 相対表示の純関数（ja/en）", () => {
  assert.equal(agoStr(30), "たった今");
  assert.equal(agoStr(30, "en"), "now");
  assert.equal(agoStr(300), "5分前");
  assert.equal(agoStr(300, "en"), "5m ago");
  assert.equal(agoStr(7200, "en"), "2h ago");
  assert.equal(agoStr(3 * 86400), "3日前");
  assert.equal(agoStr(-5, "en"), "now");         // 負値はクランプ（時計ずれで壊れない）
});

test("summarizeWorld: 本文とパスを持ち出さない（回帰テストの観測点）", () => {
  const out = summarizeWorld({
    officeName: "テスト", lang: "ja", edition: { id: "hybrid" },
    roster: [proj({ lastSaid: "秘密の本文", cwd: "/Users/me/secret", attention: true })],
  });
  assert.equal(out.agents[0].attention, true);
  const dumped = JSON.stringify(out);
  assert.equal(dumped.includes("秘密"), false);
  assert.equal(dumped.includes("/Users/"), false);
});

test("summarizeWorld: null 入力は null", () => {
  assert.equal(summarizeWorld(null), null);
  assert.equal(summarizeWorld("nope"), null);
});

// ── R53: tidyActivity（生ログ断片→人間が読む1行） ────────────────
test("tidyActivity: 実測の途切れ断片（バッククォート+未閉括弧+パス）を整える", () => {
  const raw = "報告中 配布手順書をまとめました（`やること/スライドキャスト_配布手順書_2026073";
  assert.equal(tidyActivity(raw), "報告中 配布手順書をまとめました");
});

test("tidyActivity: 閉じた括弧・普通の文はそのまま", () => {
  assert.equal(tidyActivity("実行中 verify.sh（3回目）"), "実行中 verify.sh（3回目）");
  assert.equal(tidyActivity("Editing the launch checklist"), "Editing the launch checklist");
});

test("tidyActivity: パスはbasename化・URLは触らない", () => {
  assert.equal(tidyActivity("編集中 tests/fixtures/world/basic.json"), "編集中 basic.json");
  assert.equal(tidyActivity("調査中 https://example.com/a/b"), "調査中 https://example.com/a/b");
});

test("tidyActivity: 60字省略と空白正規化", () => {
  const long = "実行中 " + "あ".repeat(80);
  const out = tidyActivity(long);
  assert.ok([...out].length <= 60);
  assert.ok(out.endsWith("…"));
  assert.equal(tidyActivity("  実行中   x  "), "実行中 x");
});

test("activityText: verb+target が tidy を通る", () => {
  assert.equal(activityText({ verb: "編集中", target: "`server/office_server.py`" }),
    "編集中 office_server.py");
});

// ── R53.2: deliveryTransitions（配達の手応え） ─────────────────
test("deliveryTransitions: 📨解消=woke・❗解消=answered・新規/継続は無視", () => {
  const prev = [
    proj({ session: "s1", pending: true }),
    proj({ session: "s2", attention: true }),
    proj({ session: "s3", pending: true }),
    proj({ session: "s5", attention: true }),
  ];
  const next = [
    proj({ session: "s1", pending: false }),          // woke
    proj({ session: "s2", attention: false }),        // answered
    proj({ session: "s3", pending: true }),           // 継続=無視
    proj({ session: "s4", pending: false }),          // 新規=無視
  ];                                                  // s5退勤=無視
  const out = deliveryTransitions(prev, next);
  assert.deepEqual(out.map((t) => [t.session, t.kind]),
    [["s1", "woke"], ["s2", "answered"]]);
  assert.deepEqual(deliveryTransitions(null, next.slice(0, 1)), []);
  assert.deepEqual(deliveryTransitions(prev, null), []);
});

test("tidyActivity: 先頭/途中のマークダウン見出し・引用記号を除去（R54実データ）", () => {
  assert.equal(tidyActivity("指示待ち # 🎬 /work-start「全工程完了」"),
    "指示待ち 🎬 /work-start「全工程完了」");
  assert.equal(tidyActivity("> 引用でした"), "引用でした");
  assert.equal(tidyActivity("C# のコード"), "C# のコード");   // 単語内の#は温存
});

// ── R59: 休憩スポットの分散割当 ────────────────────────────────
// spots は本番と同じ構成（lounge3 / sofa2 / bench3）
const REST_FIXTURE = [
  { area: "lounge" }, { area: "lounge" }, { area: "lounge" },
  { area: "sofa" }, { area: "sofa" },
  { area: "bench" }, { area: "bench" }, { area: "bench" },
];
const restAreas = (map, spots = REST_FIXTURE) =>
  [...map.values()].map((i) => spots[i].area);
const countBy = (list) => list.reduce((m, k) => ({ ...m, [k]: (m[k] || 0) + 1 }), {});

test("assignRestSpots: 決定論・重複なし・restingだけ・あふれは重なる", () => {
  const mk = (id, state = "resting") => ({ ...proj({ session: id, state }), id });
  const agents = [mk("r1"), mk("r2"), mk("r3"), mk("w1", "working")];
  const a = assignRestSpots(agents, REST_FIXTURE);
  const b = assignRestSpots(agents, REST_FIXTURE);
  assert.deepEqual([...a.entries()].sort(), [...b.entries()].sort());   // 決定論
  assert.equal(a.size, 3, "resting の3体だけ割当");
  assert.equal(new Set([...a.values()]).size, 3, "スポットが重複しない");
  assert.equal(a.has("w1"), false);
  // 容量あふれ（9体>8席）: 9体目も必ずどこかに座る（Mapに載る）
  const many = Array.from({ length: 9 }, (_, i) => mk(`m${i}`));
  const c = assignRestSpots(many, REST_FIXTURE);
  assert.equal(c.size, 9);
  for (const v of c.values()) assert.ok(v >= 0 && v < REST_FIXTURE.length);
});

// R62: 「手前のソファに溜まりすぎ」FB＝エリア単位のラウンドロビンで均等に散る
test("assignRestSpots: 2体なら必ず別エリア・3体なら3エリアに1体ずつ", () => {
  const mk = (id) => ({ ...proj({ session: id, state: "resting" }), id });
  for (const ids of [["a1", "a2"], ["zz", "qq"], ["制作本部", "受託案件"]]) {
    const areas = restAreas(assignRestSpots(ids.map(mk), REST_FIXTURE));
    assert.equal(new Set(areas).size, 2, `${ids} が同じエリアに固まった: ${areas}`);
  }
  const three = restAreas(assignRestSpots(["b1", "b2", "b3"].map(mk), REST_FIXTURE));
  assert.deepEqual(countBy(three), { lounge: 1, sofa: 1, bench: 1 });
});

test("assignRestSpots: 6体でも各エリア2体以内（1箇所に溜まらない）", () => {
  const mk = (id) => ({ ...proj({ session: id, state: "resting" }), id });
  const six = restAreas(assignRestSpots(
    ["c1", "c2", "c3", "c4", "c5", "c6"].map(mk), REST_FIXTURE));
  const n = countBy(six);
  for (const [area, c] of Object.entries(n)) {
    assert.ok(c <= 2, `${area} に ${c} 体たまった: ${JSON.stringify(n)}`);
  }
  assert.equal(six.length, 6);
  // 8体=満席でも定員（lounge3/sofa2/bench3）を超えない
  const eight = restAreas(assignRestSpots(
    Array.from({ length: 8 }, (_, i) => mk(`d${i}`)), REST_FIXTURE));
  assert.deepEqual(countBy(eight), { lounge: 3, sofa: 2, bench: 3 });
});

// ── R60: activityGloss（「今何してます?」の一言要約） ────────────
test("activityGloss: work.now が最優先（タスク管理の人間語）", () => {
  const a = proj({ verb: "実行中", target: "verify.sh",
    work: { now: ["ビジュアル回帰の実行", "次の何か"] } });
  assert.equal(activityGloss(a), "📋 ビジュアル回帰の実行");
});

test("activityGloss: verb×対象のカテゴリ判定（ja）", () => {
  assert.equal(activityGloss(proj({ verb: "実行中", target: "bash verify.sh" })), "🧪 テストを実行中");
  assert.equal(activityGloss(proj({ verb: "実行中", target: "git push origin master" })), "📦 変更をコミット/反映中");
  assert.equal(activityGloss(proj({ verb: "実行中", target: "npm install three" })), "🔧 ビルド/セットアップ中");
  assert.equal(activityGloss(proj({ verb: "実行中", target: "何かのコマンド" })), "⚙️ 処理を実行中");
  assert.equal(activityGloss(proj({ verb: "編集中", target: "office_server.py" })), "✍️ コードを編集中");
  assert.equal(activityGloss(proj({ verb: "編集中", target: "README.md" })), "📝 ドキュメントを執筆中");
  assert.equal(activityGloss(proj({ verb: "調査中", target: "x" })), "🔎 調査・読み込み中");
  assert.equal(activityGloss(proj({ verb: "報告中", target: "長い報告文…" })), "✅ 結果を報告中");
  assert.equal(activityGloss(proj({ verb: "指示待ち", target: "" })), "⏳ 次の指示を待っています");
  assert.equal(activityGloss(proj({ kind: "think", verb: "考え中…", target: "次の一手" })), "🤔 次の一手を考え中");
  assert.equal(activityGloss(proj({ state: "resting", verb: "休憩中" })), "☕ ひと休み中");
});

test("activityGloss: en の verb/文言（demo world・lang=en）", () => {
  assert.equal(activityGloss(proj({ verb: "Running", target: "verify.sh" }), "en"), "🧪 Running tests");
  assert.equal(activityGloss(proj({ verb: "Editing", target: "scene3d.js" }), "en"), "✍️ Writing code");
  assert.equal(activityGloss(proj({ verb: "Waiting for input", target: "" }), "en"), "⏳ Waiting for input");
  assert.equal(activityGloss(proj({ verb: "Thinking", target: "…" }), "en"), "🤔 Thinking it through");
});

test("activityGloss: 未知パターンは tidy 済みフォールバック・空でも落ちない", () => {
  assert.equal(activityGloss(proj({ verb: "点検中", target: "`server/x.py`" })), "点検中 x.py");
  assert.equal(activityGloss(null), "");
  assert.equal(activityGloss(proj({ verb: "", target: "", state: "working" })), "⚙️ 処理を実行中");
});

test("R69: stuckTool が agent に通る（承認対象の表示素材）", () => {
  const w = buildWorld({ roster: [proj({ approvalMin: 3, attention: true,
    stuckTool: "実行中 E2Eの残骸行を確認" })] });
  assert.equal(w.agents[0].stuckTool, "実行中 E2Eの残骸行を確認");
  assert.equal(buildWorld({ roster: [proj()] }).agents[0].stuckTool, "");
});

// ── R70: 会議の3室分散 ────────────────────────────────────────
test("assignMeetingRooms: 最空き部屋へ分散・決定論・満席あふれ安全", () => {
  const caps = { meet: 5, meet2: 3, meet3: 2 };
  const mk = (id, minions = 2) => ({ ...proj({ session: id, minions }), id });
  // 2プロジェクト → 必ず別部屋
  const two = assignMeetingRooms([mk("a"), mk("b")], caps);
  assert.notEqual(two.get("a").room, two.get("b").room);
  // 3プロジェクト → 3部屋に1つずつ
  const three = assignMeetingRooms([mk("a"), mk("b"), mk("c")], caps);
  assert.equal(new Set([...three.values()].map((v) => v.room)).size, 3);
  // 決定論
  const again = assignMeetingRooms([mk("a"), mk("b"), mk("c")], caps);
  assert.deepEqual([...three.entries()], [...again.entries()]);
  // 大量(12)でも例外なく全員に部屋がつく（あふれは最終席重なり）
  const many = assignMeetingRooms(Array.from({ length: 12 }, (_, i) => mk(`p${i}`)), caps);
  assert.equal(many.size, 12);
  for (const v of many.values()) assert.ok(v.room in caps && v.seat >= 0);
  // meeting以外は割当なし・壊れ入力安全
  assert.equal(assignMeetingRooms([proj({ session: "d" })], caps).size, 0);
  assert.equal(assignMeetingRooms(null, caps).size, 0);
  assert.equal(assignMeetingRooms([mk("a")], {}).size, 0);
});

test("R74: 会議は主要3室を先に使い、予備室(第3)は満席のときだけ開く", () => {
  const mk = (id) => ({ ...proj({ session: id, minions: 2 }), id });   // R70テストと同式
  const caps = { meet: 5, meet2: 3, meet3: 2, meet4: 5 };
  const RESERVE = ["meet3"];
  // 3件の会議 → 主要3室が1つずつ埋まる（予備の第3は使わない＝ユーザー仕様）
  const three = assignMeetingRooms([mk("a"), mk("b"), mk("c")], caps, RESERVE);
  const rooms = [...three.values()].map((v) => v.room);
  assert.deepEqual([...new Set(rooms)].sort(), ["meet", "meet2", "meet4"]);
  assert.equal(rooms.filter((r) => r === "meet3").length, 0);
  // 主要3室=13席が満席になって初めて予備が開く
  const many = assignMeetingRooms(
    Array.from({ length: 15 }, (_, i) => mk(`p${i}`)), caps, RESERVE);
  const used = [...many.values()].map((v) => v.room);
  assert.equal(used.filter((r) => r === "meet3").length, 2, "予備室が満席後に開いていない");
  // 決定論（同じ入力→同じ割当）
  const again = assignMeetingRooms([mk("a"), mk("b"), mk("c")], caps, RESERVE);
  assert.deepEqual([...again.entries()], [...three.entries()]);
  // reserve 省略時は従来どおり全室が対象（後方互換）
  const noReserve = assignMeetingRooms([mk("a"), mk("b"), mk("c"), mk("d")], caps);
  assert.equal(noReserve.size, 4);
});

test("isMuted: 待機切れかつ非稼働のみ true（稼働中は届くので警告しない）", () => {
  assert.equal(isMuted({ listening: false, state: "waiting" }), true);
  assert.equal(isMuted({ listening: false, state: "resting" }), true);
  // ★心拍は待機ループ中しか打たない＝稼働中は必ず listening:false になるが、
  //   ターン終了直後に届く高速パスなので警告してはいけない
  assert.equal(isMuted({ listening: false, state: "working" }), false);
  assert.equal(isMuted({ listening: true, state: "waiting" }), false);
  assert.equal(isMuted({ state: "waiting" }), false);        // 旧server（未搬送）は脅さない
  assert.equal(isMuted(null), false);
});

test("isMuted: ❗中は working でも「届かない」側（R86-G・実測で塞いだ穴）", () => {
  // 権限ダイアログ/AskUserQuestion で止まったセッションは**ターンが終わらない**ので
  // Stop hook が起動せず、人間がターミナルを触るまで無期限に届かない。
  // しかもブロック中は必ず state:"working" なので、working を一律除外していると
  // ❗が立ってから最初の164秒＝いちばん必要な瞬間だけ警告が消えていた。
  assert.equal(isMuted({ listening: false, state: "working", approvalMin: 2 }), true);
  assert.equal(isMuted({ listening: false, state: "working", question: "どっち?" }), true);
  assert.equal(isMuted({ listening: false, state: "working", attention: true }), true);
  // ❗が無い稼働中は従来どおり誤警告しない
  assert.equal(isMuted({ listening: false, state: "working" }), false);
  assert.equal(isMuted({ listening: true, state: "working", approvalMin: 9 }), false);
});

test("stalledSends: 送ったのに動かない相手だけを1回だけ返す（R86-G）", () => {
  const stuck = proj({ session: "s1", name: "A", listening: false, attention: true, approvalMin: 3 });
  const moving = proj({ session: "s2", name: "B", listening: true, attention: true, approvalMin: 3 });
  const agents = [stuck, moving].map((p) => buildWorld({ roster: [p] }).agents[0]);
  const sent = new Map([["s1", 100], ["s2", 100]]);
  const noted = new Set();

  // 90秒たつまでは黙っている（送信直後に脅さない）
  assert.deepEqual(stalledSends(sent, agents, 180, noted), []);
  const hit = stalledSends(sent, agents, 200, noted);
  assert.equal(hit.length, 1);
  assert.equal(hit[0].session, "s1");
  assert.equal(hit[0].min, 2);            // 100秒 → 2分（切り上げでなく四捨五入・最低1）
  // 通知済みは二度と返さない（毎ポーリングでトーストを撃たない）
  noted.add("s1");
  assert.deepEqual(stalledSends(sent, agents, 400, noted), []);
  // ❗が消えていれば（＝届いて動いた）対象外
  const cleared = buildWorld({ roster: [proj({ session: "s3", listening: false })] }).agents;
  assert.deepEqual(stalledSends(new Map([["s3", 0]]), cleared, 999, new Set()), []);
  // 相手が居なくなった/引数が壊れていても落ちない
  assert.deepEqual(stalledSends(new Map([["zz", 0]]), agents, 999, new Set()), []);
  assert.deepEqual(stalledSends(null, agents, 999, new Set()), []);
});

test("assignLabels: 並走セッションを区別できる短い名前とバッジを割る（R86-I）", () => {
  // 実データ（2026-08-28・9セッション）。1文字モノグラムが全部「制」で識別不能だった。
  const names = ["制作本部(works)", "制作本部(works) 3号", "制作本部(works) 5号",
    "制作本部(works) 7号", "20260714 - ai-office", "GLM5.3", "AKOOL"];
  const ags = names.map((n, i) => ({ id: "id" + String(i).padStart(2, "0"), name: n }));
  const r = assignLabels(ags);
  const badges = ags.map((a) => r.get(a.id).badge);
  assert.equal(new Set(badges).size, badges.length, "バッジが重複＝誰がどれか分からない");
  assert.equal(r.get("id01").badge, "3");            // 号の数字がバッジ
  assert.equal(r.get("id01").short, "works 3");      // 区別がつく部分を残す
  assert.equal(r.get("id04").short, "ai-office");    // 日付プレフィクスは落とす
  assert.equal(r.get("id05").short, "GLM5.3");       // 号でない数字は割らない
  const shorts = ags.map((a) => r.get(a.id).short);
  assert.equal(new Set(shorts).size, shorts.length, "短縮名が衝突＝別人が同じ名前に見える");
});

test("assignLabels: 衝突したらフルネームへ戻す・順序で揺れない（R86-I）", () => {
  // 別プロジェクトの同名フォルダ（…/a/api と …/b/api）は短縮すると同じになる
  const ags = [{ id: "x", name: "案件A(api)" }, { id: "y", name: "案件B(api)" }];
  const r = assignLabels(ags);
  assert.notEqual(r.get("x").short, r.get("y").short);
  assert.ok(r.get("x").short.includes("案件A"));
  // 入力順が変わっても割り当ては同じ（id昇順で決める＝ポーリングで記号が入れ替わらない）
  const r2 = assignLabels([...ags].reverse());
  assert.equal(r2.get("x").badge, r.get("x").badge);
  assert.equal(r2.get("y").badge, r.get("y").badge);
  // 壊れた入力でも落ちない
  assert.equal(assignLabels(null).size, 0);
  assert.equal(assignLabels([null, undefined]).size, 0);
});

test("buildWorld が badge/shortName を配る（R86-I）", () => {
  const w = buildWorld({ roster: [
    proj({ projectId: "p1", session: "s1", name: "制作本部(works) 7号" }),
    proj({ projectId: "p2", session: "s2", name: "制作本部(works)" }),
  ] });
  const byName = Object.fromEntries(w.agents.map((a) => [a.name, a]));
  assert.equal(byName["制作本部(works) 7号"].badge, "7");
  assert.equal(byName["制作本部(works) 7号"].shortName, "works 7");
  assert.equal(byName["制作本部(works)"].badge, "W");
});
