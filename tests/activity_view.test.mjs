// R121: ブラウザに依存しない小さな DOM で安全な本文と遅延展開を確かめる。
import test from "node:test";
import assert from "node:assert/strict";
import { createActivityView, en, formatDuration, ja } from "../ui/hud/activity.js";

class Node {
  constructor(doc, tag = "", value = "") {
    this.ownerDocument = doc; this.tagName = tag.toUpperCase(); this.value = value;
    this.childNodes = []; this.attrs = new Map(); this.events = {}; this.className = "";
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
    this.classList = {
      contains: (c) => this.className.split(/\s+/).includes(c),
      add: (...cs) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...cs])].join(" "); },
      remove: (...cs) => { this.className = this.className.split(/\s+/).filter((c) => !cs.includes(c)).join(" "); },
      toggle: (c, force) => { const on = force ?? !this.classList.contains(c); this.classList[on ? "add" : "remove"](c); return on; },
    };
  }
  get children() { return this.childNodes.filter((n) => n.tagName); }
  get textContent() { return this.value + this.childNodes.map((n) => n.textContent).join(""); }
  set textContent(text) { this.value = String(text); this.childNodes = []; }
  set innerHTML(_) { throw new Error("HTML parsing is forbidden"); }
  append(...nodes) {
    for (const node of nodes) {
      const child = typeof node === "string" ? this.ownerDocument.createTextNode(node) : node;
      child.parentNode = this; this.childNodes.push(child);
    }
  }
  replaceChildren(...nodes) { this.value = ""; this.childNodes = []; this.append(...nodes); }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  getAttribute(name) { return this.attrs.get(name) ?? null; }
  hasAttribute(name) { return this.attrs.has(name); }
  addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
  click() { for (const fn of this.events.click || []) fn({ stopPropagation() {} }); }
  getBoundingClientRect() { return { top: 0, bottom: 30 }; }
}
const document = {
  createElement: (tag) => new Node(document, tag),
  createTextNode: (value) => new Node(document, "", String(value)),
};
const host = () => document.createElement("div");
const all = (node, predicate) => node.children.flatMap((child) => [...(predicate(child) ? [child] : []), ...all(child, predicate)]);
const cls = (node, name) => all(node, (n) => n.classList.contains(name));
const tags = (node, tag) => all(node, (n) => n.tagName === tag.toUpperCase());
const toolRows = [
  { k: "tool", id: "a1", n: "Bash", ds: "テストを実行する", t: "sensitive-command\nsecond line", ts: 100 },
  { k: "res", for: "a1", t: "one\ntwo\nthree\nfour", nl: 9, ts: 103,
    d: [["+", 1, "added source"], ["-", 2, "removed source"]],
    kids: [{ k: "a", t: "child reply" }], kx: 2 },
];

test("作業ビューの ja/en は同じキーを持ち、経過は渡された秒だけで整形する", () => {
  assert.deepEqual(Object.keys(ja).sort(), Object.keys(en).sort());
  assert.equal(formatDuration(411), "6m 51s"); assert.equal(formatDuration(12), "12s");
  assert.equal(formatDuration(-5), "0s");
});

test("Markdown は安全な要素で描き、HTML の本文を解釈しない", () => {
  const h = host(), view = createActivityView();
  view.render(h, [{ k: "a", t: '# 見出し\n\n**太字** と `code` <img src=x onerror=alert(1)>\n\n- 項目\n* 項目2\n\n1. first\n2. second\n\n| col |\n| --- |\n\n```js\n<script>bad()</script>\n```' }]);
  assert.equal(tags(h, "img").length, 0); assert.equal(tags(h, "script").length, 0);
  assert.equal(tags(h, "strong").length, 2); assert.equal(tags(h, "code").length, 1);
  assert.equal(tags(h, "ul").length, 1); assert.equal(tags(h, "ol").length, 1);
  assert.equal(tags(h, "li").length, 4); assert.equal(tags(h, "pre").length, 2);
  assert.match(h.textContent, /<img src=x onerror=alert\(1\)>/);
});

test("道具と結果は group/step を開くまで DOM に作らず、開閉は再描画でも保持する", () => {
  const h = host(), view = createActivityView();
  view.render(h, toolRows);
  assert.equal(cls(h, "act-step").length, 0); assert.doesNotMatch(h.textContent, /sensitive-command|child reply|added source/);
  cls(h, "act-group-head")[0].click();
  assert.equal(cls(h, "act-step").length, 1); assert.doesNotMatch(h.textContent, /sensitive-command|child reply|added source/);
  assert.equal(cls(h, "act-step")[0].getAttribute("data-state"), "ok");
  cls(h, "act-step-head")[0].click();
  assert.match(h.textContent, /sensitive-command/); assert.equal(cls(h, "act-output")[0].textContent.includes("four"), false);
  assert.equal(cls(h, "act-diffline").length, 2); assert.doesNotMatch(h.textContent, /child reply/);
  cls(cls(h, "act-output")[0], "act-more")[0].click();
  assert.match(cls(h, "act-output")[0].textContent, /four/); assert.match(h.textContent, /残り 5 行/);
  cls(cls(h, "act-kids")[0], "act-more")[0].click(); assert.match(h.textContent, /child reply/);
  view.render(h, toolRows);
  assert.equal(cls(h, "act-step").length, 1); assert.match(h.textContent, /sensitive-command|child reply/);
  assert.match(cls(h, "act-output")[0].textContent, /four/);
  view.dispose(); view.render(h, toolRows); assert.equal(cls(h, "act-step").length, 0);
});

test("吹き出し・画像札・質問・計画・所要と現在の手を同じビューで描く", () => {
  const h = host(), seen = [], view = createActivityView({ decorate: (root, result, options) => { seen.push([root, result, options.now]); } });
  const rows = [{ k: "u", t: `${"長い指示".repeat(60)}\n[Image #1]` }, { k: "cmd", t: "/compact" },
    { k: "q", t: "どちら？", o: ["こちら（おすすめ）", "あちら"] },
    { k: "plan", t: Array.from({ length: 10 }, (_, i) => `line${i + 1}`).join("\n") },
    { k: "turn", ms: 192000 }, { k: "tool", id: "live", n: "Read", t: "src/a.js", ts: 100 }];
  const result = view.render(h, rows, { live: true, now: 511 });
  assert.equal(seen.length, 1); assert.equal(seen[0][1], result);
  assert.equal(cls(h, "act-user").length, 2); assert.match(h.textContent, /画像 1 枚/);
  assert.equal(cls(h, "act-question")[0].getAttribute("data-qtext"), "どちら？");
  assert.match(cls(h, "act-recommended")[0].textContent, /おすすめ/);
  assert.doesNotMatch(cls(h, "act-plan")[0].textContent, /line9/);
  cls(cls(h, "act-plan")[0], "act-more")[0].click(); assert.match(cls(h, "act-plan")[0].textContent, /line10/);
  assert.match(h.textContent, /3分12秒 作業/); assert.match(h.textContent, /a.js を読む/);
  assert.equal(cls(h, "act-elapsed")[0].getAttribute("data-since"), "100");
  assert.equal(cls(h, "act-elapsed")[0].textContent, "6m 51s");
  cls(cls(h, "act-user")[0], "act-more")[0].click();
  assert.equal(cls(h, "act-user-text")[0].classList.contains("act-clamp"), false);
  view.render(h, rows, { live: true, now: 512 }); assert.equal(cls(h, "act-user-text")[0].classList.contains("act-clamp"), false);
});

test("R122: 畳みの行は 1 文の文章（ファイル名は出さない・+/− は文の後ろ）・英語も 1 文", () => {
  const rows = [
    { k: "tool", id: "c1", n: "Bash", t: "ls", ds: "一覧を見る", ts: 1 }, { k: "res", for: "c1", t: "ok", ts: 2 },
    { k: "tool", id: "c2", n: "Bash", t: "npm test", ts: 3 }, { k: "res", for: "c2", t: "ok", ts: 4 },
    { k: "tool", id: "r1", n: "Read", t: "src/secret_name.js", ts: 5 }, { k: "res", for: "r1", rl: 10, ts: 6 },
    { k: "tool", id: "e1", n: "Edit", t: "src/login_form.tsx", ts: 7 }, { k: "res", for: "e1", f: "src/login_form.tsx", add: 12, rem: 3, ts: 8 },
    { k: "tool", id: "e2", n: "Edit", t: "src/login_form.tsx", ts: 9 }, { k: "res", for: "e2", f: "src/login_form.tsx", add: 3, rem: 0, ts: 10 },
    { k: "tool", id: "w1", n: "Write", t: "docs/new_page.md", ts: 11 }, { k: "res", for: "w1", f: "docs/new_page.md", wl: 20, ts: 12 },
    { k: "a", t: "直しました", ts: 13 },
  ];
  const h = host();
  createActivityView({ lang: "ja" }).render(h, rows);
  const head = cls(h, "act-group-head")[0];
  assert.equal(head.textContent.replace(/\s+/g, " ").trim(),
    "コマンドを 2 回実行し、ファイルを 1 件読み、ファイルを 1 つ直し、ファイルを 1 つ作りました +35 −3 ▸");
  assert.doesNotMatch(head.textContent, /login_form|secret_name|new_page/);
  const e = host();
  createActivityView({ lang: "en" }).render(e, rows);
  assert.equal(cls(e, "act-group-head")[0].textContent.replace(/\s+/g, " ").trim(),
    "Ran 2 commands, read 1 file, edited 1 file and created 1 file +35 −3 ▸");
});

test("R128: Bot の記録は、2 分たっていなくても日付が替わったら区切る（23:59 の次の 00:00 が前の日に見えない）・セッションは今までどおり", () => {
  // 別モデルレビュー（R128 #3）: 区切りは 120 秒以上の間だけで判定していた＝日またぎの 60 秒は区切りが出ず、翌日の行が前の日に属して見えた
  const late = Math.floor(new Date(2026, 10, 14, 23, 59, 30).getTime() / 1000);
  const rows = [
    { k: "ev", e: "working", t: "夜の見回り", ts: late },
    { k: "a", t: "日またぎの報告", rk: "done", ts: late + 60 },    // 翌日 00:00:30（60 秒後）
    { k: "a", t: "同じ日の続き", rk: "done", ts: late + 90 },      // 翌日 00:01:00（区切らない）
  ];
  const hm = (ts) => { const d = new Date(ts * 1000); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
  const md = (ts) => { const d = new Date(ts * 1000); return `${d.getMonth() + 1}/${d.getDate()}`; };
  const h = host();
  createActivityView({ lang: "ja" }).render(h, rows, { record: true });
  assert.deepEqual(cls(h, "act-time").map((n) => n.textContent), [`${md(late)} ${hm(late)}`, `${md(late + 60)} ${hm(late + 60)}`]);
  const s = host();
  createActivityView({ lang: "ja" }).render(s, rows, {});
  assert.equal(cls(s, "act-time").length, 0, "セッションの描き方が変わった（2 分未満は区切らない）");
});

// R128: 出勤の口の Bot の記録（server/source_door_log.py の行・正本= docs/door.md「記録の画面」）。Mac の中央とスマホの端末画面が同じ描き手。
test("R128: Bot の記録＝報告の札と題・答え／指示の札（選択肢の番号）・出勤と相談の閉じ方の 1 行・毒の文は文字のまま・考え中を出さない", () => {
  const poison = '<img src=x onerror="window.__pwned=1">';
  const t0 = Math.floor(new Date(2026, 10, 14, 12, 0, 0).getTime() / 1000), day = 86400;   // 地域の正午（同じ日の中で 430 秒）
  const rows = [
    { k: "ev", e: "working", t: "見回り" + poison, ts: t0 },
    { k: "a", t: "題" + poison + "\n\n**本文**" + poison, rk: "warn", ts: t0 + 60 },
    { k: "q", t: "送る？" + poison, o: ["はい" + poison, "いいえ"], ts: t0 + 70 },
    { k: "u", t: "選択肢「いいえ」でお願いします。", as: "answer", ch: 1, ts: t0 + 400 },
    { k: "u", t: "自由な答え" + poison, as: "answer", ts: t0 + 410 },
    { k: "u", t: "棚も見て", as: "instruct", ts: t0 + 420 },
    { k: "ev", e: "withdrawn", ts: t0 + 430 },
    { k: "ev", e: "expired", ts: t0 + day },
  ];
  const h = host();
  createActivityView({ lang: "ja" }).render(h, rows, { live: true, now: t0 + day + 5, record: true });
  assert.equal(tags(h, "img").length, 0);
  assert.equal(h.textContent.split(poison).length - 1, 6, "Bot と人の文の毒が文字のまま 6 か所に出ていない");
  assert.equal(cls(h, "act-live").length, 0, "Bot の記録に「考え中」を出した（Bot は考えない）");
  assert.deepEqual(cls(h, "act-event").map((n) => n.textContent),
    ["出勤 見回り" + poison, "Bot が相談を取り下げました", "相談の期限が切れました（答えはありません）"]);
  assert.deepEqual(cls(h, "act-event").map((n) => n.className), ["act-event act-event-working", "act-event act-event-withdrawn", "act-event act-event-expired"]);
  const report = cls(h, "act-report")[0];
  assert.equal(cls(report, "act-report-kind")[0].textContent, "注意");
  assert.equal(cls(report, "act-report-title")[0].textContent, "題" + poison);
  assert.equal(tags(report, "strong").length, 2, "題と本文の **…** の 2 つ");
  assert.deepEqual(cls(h, "act-said").map((n) => n.textContent), ["相談への答え（選択肢 2）", "相談への答え", "指示"]);
  assert.equal(cls(h, "act-user").length, 3);
  assert.equal(cls(h, "act-question")[0].getAttribute("data-qtext"), "送る？" + poison);
  // いつ: 最初の行にも時刻（日付つき）・2 分以上の間に時刻・日が替わったら日付
  const hm = (ts) => { const d = new Date(ts * 1000); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
  const md = (ts) => { const d = new Date(ts * 1000); return `${d.getMonth() + 1}/${d.getDate()}`; };
  assert.deepEqual(cls(h, "act-time").map((n) => n.textContent), [`${md(t0)} ${hm(t0)}`, hm(t0 + 400), `${md(t0 + day)} ${hm(t0 + day)}`]);
  // セッション（record でない）の描き方は今までどおり（最初の行に時刻を付けない・日付を付けない・live なら考え中）
  const s = host();
  createActivityView({ lang: "ja" }).render(s, rows, { live: true, now: t0 + day + 5 });
  assert.deepEqual(cls(s, "act-time").map((n) => n.textContent), [hm(t0 + 400), hm(t0 + day)]);
  assert.equal(cls(s, "act-live").length, 1);
  const e = host();
  createActivityView({ lang: "en" }).render(e, rows, { record: true });
  assert.deepEqual(cls(e, "act-said").map((n) => n.textContent), ["Answer (option 2)", "Answer", "Instruction"]);
  assert.deepEqual(cls(e, "act-event").map((n) => cls(n, "act-event-word")[0].textContent),
    ["On duty", "The bot withdrew its question", "The question expired (no answer)"]);
  assert.equal(cls(e, "act-report-kind")[0].textContent, "Warning");
});
