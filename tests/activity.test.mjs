// R121: 作業ビューの組み立て（ui/core/activity.js）の番人。本人 2026-11-01「コードがつらつら出てきたら分からない」＝
// 道具の実行は文と文の間で 1 つに畳む・走っている 1 手だけを外に出す・人の指示から画像の印を外す、を行の並びで確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { buildActivity, catOf, DOOR_EVENTS, groupParts, optionLabel, questionOptions, questionStem, REPORT_KINDS, reportParts, sameQuestion, userText } from "../ui/core/activity.js";

const tool = (id, n, t, extra = {}) => ({ k: "tool", n, t, id, ts: 100, ...extra });
const res = (id, extra = {}) => ({ k: "res", for: id, t: "ok", ts: 103, ...extra });

test("文と文の間の道具は 1 つの group に畳み、種類ごとの数とファイルの +/− を持つ", () => {
  const rows = [
    { k: "u", t: "直して", ts: 90 },
    tool("a1", "Bash", "ls", { ds: "一覧を見る" }), res("a1"),
    tool("a2", "Read", "src/a.js"), res("a2", { rl: 10 }),
    tool("a3", "Edit", "src/a.js"), res("a3", { f: "src/a.js", add: 3, rem: 1 }),
    tool("a4", "Edit", "src/a.js"), res("a4", { f: "src/a.js", add: 2, rem: 0 }),
    tool("a5", "Bash", "npm test", { ds: "テストを回す" }), res("a5"),
    { k: "a", t: "直しました。", ts: 110 },
  ];
  const { blocks, summary } = buildActivity(rows);
  assert.deepEqual(blocks.map((b) => b.type), ["user", "group", "text"]);
  const g = blocks[1];
  assert.equal(g.steps.length, 5);
  assert.deepEqual(g.parts.map((p) => [p.cat, p.n]), [["cmd", 2], ["read", 1], ["edit", 2]]);
  assert.deepEqual(g.parts[2].files, [{ f: "src/a.js", add: 5, rem: 1, wl: 0 }]);
  assert.equal(g.steps[0].ds, "一覧を見る");
  assert.equal(g.steps[0].state, "ok");
  assert.equal(g.steps[0].dur, 3);
  assert.deepEqual(summary.files, [{ f: "src/a.js", add: 5, rem: 1, wl: 0 }]);
});

test("live の間は末尾の結果の無い手が run（group の外）・無ければ考え中・live でなければ印を付けない", () => {
  const rows = [tool("b1", "Bash", "ls"), res("b1"), tool("b2", "Bash", "sleep 9", { ds: "待つ", ts: 120 })];
  const live = buildActivity(rows, { live: true }).blocks;
  assert.deepEqual(live.map((b) => b.type), ["group", "run"]);
  assert.equal(live[1].step.ds, "待つ");
  assert.equal(live[1].step.state, "run");
  assert.equal(live[0].steps.length, 1);

  const idle = buildActivity(rows).blocks;
  assert.deepEqual(idle.map((b) => b.type), ["group"]);
  assert.equal(idle[0].steps[1].state, "open");

  const thinking = buildActivity([...rows.slice(0, 2), { k: "th", n: 1, chars: 0, ts: 130 }], { live: true }).blocks;
  assert.deepEqual(thinking.map((b) => b.type), ["group", "think"]);
  assert.equal(thinking[1].since, 130);
});

test("並列で出した手は、末尾の 1 つだけを run に出し、残りは group の中で run の印", () => {
  const rows = [tool("c1", "Read", "a.md"), tool("c2", "Read", "b.md")];
  const { blocks } = buildActivity(rows, { live: true });
  assert.deepEqual(blocks.map((b) => b.type), ["group", "run"]);
  assert.equal(blocks[0].steps[0].state, "run");
  assert.equal(blocks[1].step.arg, "b.md");
});

test("別モデルレビュー: 後から出した手が先に終わった並列でも、走っている手を run に出す（考え中にしない）", () => {
  const rows = [tool("p1", "Bash", "sleep 9", { ds: "長い手" }), tool("p2", "Read", "a.md"), res("p2")];
  const { blocks } = buildActivity(rows, { live: true });
  assert.deepEqual(blocks.map((b) => b.type), ["group", "run"]);
  assert.equal(blocks[1].step.ds, "長い手");
  assert.deepEqual(blocks[0].steps.map((s) => s.state), ["ok"]);
});

test("別モデルレビュー: Codex のファイル変更（for の無い結果・f あり）は編集／作成の 1 手として畳みと要約に入る", () => {
  const rows = [
    { k: "a", t: "直します" },
    { k: "res", f: "memory.md", wl: 12, d: [["+", 1, "x"]], t: "memory.md に 12 行を書き込み", ts: 200 },
    { k: "res", f: "app.js", add: 3, rem: 1, d: [], t: "app.js を更新（+3 −1）" },
    { k: "res", t: "窓の外の結果" },
  ];
  const { blocks, summary } = buildActivity(rows);
  assert.deepEqual(blocks.map((b) => b.type), ["text", "group"]);
  assert.deepEqual(blocks[1].steps.map((s) => [s.tool, s.cat, s.file, s.state]),
    [["Write", "write", "memory.md", "ok"], ["Edit", "edit", "app.js", "ok"]]);
  assert.deepEqual(summary.files.map((f) => [f.f, f.add, f.rem, f.wl]), [["memory.md", 0, 0, 12], ["app.js", 3, 1, 0]]);
});

test("失敗・中断・裏で動く手の印", () => {
  const rows = [
    tool("d1", "Bash", "false"), res("d1", { err: 1 }),
    tool("d2", "Bash", "sleep"), res("d2", { err: 1, int: 1 }),
    tool("d3", "Bash", "serve", { bg: 1 }), res("d3", { bg: 1 }),
    tool("d4", "Task", "調べる"), res("d4", { st: "bg" }),
  ];
  const s = buildActivity(rows).blocks[0].steps;
  assert.deepEqual(s.map((x) => x.state), ["err", "int", "ok", "ok"]);
  assert.deepEqual(s.map((x) => x.bg), [false, false, true, true]);
});

test("Todo は流れに置かず要約へ（最後の Todo）・考えの印も流れに出さない", () => {
  const rows = [
    { k: "todo", i: [["c", "調べる"], ["p", "直す"]] },
    { k: "th", n: 2, chars: 40 },
    { k: "todo", i: [["c", "調べる"], ["i", "直す"], ["p", "確かめる"]] },
    { k: "a", t: "進めます" },
  ];
  const { blocks, summary } = buildActivity(rows);
  assert.deepEqual(blocks.map((b) => b.type), ["text"]);
  assert.deepEqual(summary.todo, { done: 1, total: 3, now: "直す" });
});

test("人の指示: 画像の印は数に・貼り付けの囲みは外す・画像だけの指示も残す", () => {
  assert.deepEqual(userText("[Image #2] [Image #3]"), { text: "", images: 2 });
  assert.deepEqual(userText("見て [Image #1]\n[Image: source: /tmp/a.png]"), { text: "見て", images: 2 });
  assert.deepEqual(userText('<pasted_content id="1">\n続きから\n</pasted_content id="1">'), { text: "続きから", images: 0 });
  const { blocks } = buildActivity([{ k: "u", t: "[Image #1]" }, { k: "cmd", t: "/compact" }]);
  assert.deepEqual(blocks.map((b) => [b.type, b.images, b.cmd]), [["user", 1, false], ["user", 0, true]]);
});

test("質問の選択肢: (Recommended)・（おすすめ）は印にして外す", () => {
  assert.deepEqual(questionOptions(["Mac を先に (Recommended)", "同時に", "", "あとで（おすすめ）"]),
    [{ label: "Mac を先に", rec: true }, { label: "同時に", rec: false }, { label: "あとで", rec: true }]);
});

test("対の無い結果は出さない・知らない種類の行は飛ばす・壊れた入力でも落ちない", () => {
  assert.deepEqual(buildActivity([res("zz"), { k: "??" }, null, 3]).blocks, []);
  assert.deepEqual(buildActivity(null).blocks, []);
  assert.equal(catOf("mcp__x__y"), "mcp");
  assert.equal(catOf("Frobnicate"), "other");
  assert.deepEqual(groupParts([]), []);
});

test("会話の圧縮・中断・1 ターンの所要は note", () => {
  const { blocks } = buildActivity([{ k: "sys" }, { k: "int" }, { k: "turn", ms: 65000 }]);
  assert.deepEqual(blocks.map((b) => [b.type, b.kind, b.ms]), [["note", "sys", 0], ["note", "int", 0], ["note", "turn", 65000]]);
});

// サーバーの short(s, 120) の写し（空白を畳み、120 字を超えたら 119 字＋…）
const serverShort = (text, n = 120) => { const t = text.replace(/\s+/g, " ").trim(); return [...t].length <= n ? t : [...t].slice(0, n - 1).join("") + "…"; };
const serverQuestion = (q, labels) => serverShort(q + (labels.length ? `（${labels.join(" / ")}）` : ""));

test("R123-B: 質問文の後ろのサーバーの連結だけを外す（完全一致か 120 字切れ）・質問そのものの括弧は残す", () => {
  const opts = [{ label: "種類ごとに同じ階層へ (Recommended)" }, { label: "画像・判定も別フォルダに分ける" }, { label: "改名と掃除だけ" }];
  const labels = opts.map((o) => o.label);
  const q = "中をどこまで並べ替えますか？";
  assert.equal(questionStem(serverQuestion(q, labels), opts), q);
  // 120 字で連結の途中が切れた形
  const long = "ここまでの作業で検証フォルダの中身を種類ごとに分け、古い判定の結果も別の場所へ移しました。次はどこまで並べ替えるかを決めたいので、いまの使い方にいちばん近いものを選んでください。";
  const cut = serverQuestion(long, labels);
  assert.ok(cut.endsWith("…") && [...cut].length === 120, "前提: 連結の途中で 120 字に切れている");
  assert.equal(questionStem(cut, opts), long);
  // 別モデルレビュー（medium）: 質問の括弧が選択肢の頭と同じ字で始まっても外さない（連結の無い質問＝承認フックの title など）
  const scoped = "実行してよいですか？（本番環境のみ）";
  assert.equal(questionStem(scoped, [{ label: "本番環境のみを更新" }, { label: "やめる" }]), scoped);
  assert.equal(questionStem("どちらの案（A か B）にしますか？", [{ label: "A" }, { label: "B" }]), "どちらの案（A か B）にしますか？", "質問の中の括弧は残す");
  assert.equal(questionStem("この設計で確定していいですか？", [{ label: "案A で進める", recommended: true }]), "この設計で確定していいですか？");
  assert.equal(questionStem("続けますか？（はい / いいえ）", []), "続けますか？（はい / いいえ）", "選択肢が無ければ触らない");
  assert.equal(questionStem("短い質問？（はい / いい…", [{ label: "はい" }, { label: "いいえ" }]), "短い質問？（はい / いい…", "120 字に届かない … は切れた連結と見なさない");
  assert.equal(questionStem("", opts), "");
  assert.equal(questionStem(null, null), "");
});

test("R123-B: 会話の質問と同じかは本文の全部と選択肢の並びで見る（先頭が同じ別の質問は別）", () => {
  const opts = [{ label: "案A で進める (Recommended)" }, { label: "案B" }];
  const text = "この設計で確定していいですか？検証環境の設定も、いまの案のまま進めます。";
  const block = (t, o = ["案A で進める (Recommended)", "案B"]) => ({ type: "question", text: t, options: questionOptions(o) });
  const asked = serverQuestion(text, opts.map((o) => o.label));
  assert.equal(sameQuestion(block(text), asked, opts), true);
  assert.equal(sameQuestion(block(text.replace("検証環境", "本番環境")), asked, opts), false, "末尾だけ違う質問を同じと見なした");
  assert.equal(sameQuestion(block(text, ["案A で進める", "案C"]), asked, opts), false, "選択肢が違うのに同じと見なした");
  assert.equal(sameQuestion({ type: "text", text }, asked, opts), false);
  assert.equal(sameQuestion(null, asked, opts), false);
  // 質問そのものが 120 字を超えてサーバーで切れた（連結が丸ごと落ちた）ときは、切れた所までの一致で見る
  const long = "あ".repeat(130) + "？";
  assert.equal(sameQuestion(block(long), serverQuestion(long, opts.map((o) => o.label)), opts), true);
  assert.equal(sameQuestion(block("い" + long.slice(1)), serverQuestion(long, opts.map((o) => o.label)), opts), false);
});

test("R123-B: 選択肢の 1 本 → 文字の印と recommended の両方をおすすめにする", () => {
  assert.deepEqual(optionLabel({ label: "案A で進める", recommended: true }), { label: "案A で進める", rec: true });
  assert.deepEqual(optionLabel({ label: "種類ごとに同じ階層へ (Recommended)" }), { label: "種類ごとに同じ階層へ", rec: true });
  assert.deepEqual(optionLabel("あとで"), { label: "あとで", rec: false });
  assert.deepEqual(optionLabel(null), { label: "", rec: false });
});

// ── R128: 出勤の口の Bot の記録（server/source_door_log.py の行）─────────────────────────────────
// 正本= docs/door.md「記録の画面」。報告＝a＋rk・相談＝q・人の答えと指示＝u＋as（選んだ選択肢は ch）・出勤の変化と相談の閉じ方＝ev。
test("R128: Bot の記録の行 → 誰が（Bot の本文・人の吹き出し・出来事の 1 行）・何を（報告の種類・答えか指示か・選択肢）", () => {
  const rows = [
    { k: "ev", e: "working", t: "受信箱を見回り中", ts: 100 },
    { k: "a", t: "請求書を 3 通確認\n\n送り先は 3 社\n2 行目", rk: "warn", ts: 110 },
    { k: "q", t: "送ってよい？", o: ["はい", "いいえ"], ts: 120 },
    { k: "u", t: "選択肢「いいえ」でお願いします。", as: "answer", ch: 1, ts: 130 },
    { k: "u", t: "[Image #1] 棚も見て", as: "instruct", ts: 140 },
    { k: "u", t: "自由な答え", as: "answer", ts: 150 },
    { k: "ev", e: "withdrawn", ts: 160 },
    { k: "ev", e: "expired", ts: 170 },
    { k: "ev", e: "resting", t: "夜は止めます", ts: 180 },
    { k: "a", t: "題だけ", rk: "info", ts: 190 },
  ];
  const { blocks } = buildActivity(rows, { live: true });
  assert.deepEqual(blocks.map((b) => b.type), ["event", "text", "question", "user", "user", "user", "event", "event", "event", "text", "think"]);
  assert.deepEqual([blocks[0].event, blocks[0].text, blocks[0].ts], ["working", "受信箱を見回り中", 100]);
  assert.deepEqual([blocks[1].report, blocks[1].title, blocks[1].text, blocks[1].err], ["warn", "請求書を 3 通確認", "送り先は 3 社\n2 行目", false]);
  assert.deepEqual([blocks[3].said, blocks[3].choice], ["answer", 1]);
  assert.deepEqual([blocks[4].said, blocks[4].choice, blocks[4].text, blocks[4].images], ["instruct", null, "[Image #1] 棚も見て", 0],
    "人の言葉は書いたまま（Claude Code の画像の印として読み替えない）");
  assert.deepEqual([blocks[5].said, blocks[5].choice], ["answer", null]);
  assert.deepEqual(blocks.slice(6, 9).map((b) => [b.event, b.text]), [["withdrawn", ""], ["expired", ""], ["resting", "夜は止めます"]]);
  assert.deepEqual([blocks[9].title, blocks[9].text], ["題だけ", ""]);
});

test("R128: 知らない印・形の違う値は今までどおり（セッションの行は変えない・知らない ev は飛ばす）", () => {
  const { blocks } = buildActivity([
    { k: "ev", e: "sleeping", t: "?" }, { k: "ev" }, { k: "a", t: "AI の文", rk: "nope" },
    { k: "u", t: "指示", as: "other" }, { k: "u", t: "選択肢", as: "answer", ch: -1 }, { k: "u", t: "選択肢 2", as: "answer", ch: 1.5 },
    { k: "cmd", t: "/x", as: "answer" },
  ]);
  assert.deepEqual(blocks.map((b) => b.type), ["text", "user", "user", "user", "user"]);
  assert.equal(blocks[0].report, undefined);
  assert.equal(blocks[0].text, "AI の文");
  assert.equal(blocks[1].said, undefined, "知らない as は人の指示（セッションと同じ）");
  assert.deepEqual([blocks[2].said, blocks[2].choice, blocks[3].choice], ["answer", null, null]);
  assert.deepEqual([blocks[4].cmd, blocks[4].said], [true, undefined]);
  assert.deepEqual(reportParts("題\n\n\n本文"), { title: "題", text: "本文" });
  assert.deepEqual(reportParts(""), { title: "", text: "" });
  assert.deepEqual([...DOOR_EVENTS], ["working", "waiting", "resting", "expired", "withdrawn"]);
  assert.deepEqual([...REPORT_KINDS], ["done", "info", "warn"]);
});
