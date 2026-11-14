// R100-0: デスクトップの回答は「画面に出していた要求」にだけ効く（押した瞬間の最古へ決め直さない）。
// 同じセッションで許可が 2 件並び、表示していた 1 件目が片付いた直後に文章や拒否を送ると、2 件目（表示して
// いない要求）へ効いた（R100-0 のレビュー・再現済み）。あわせて ❗ の内容キーに要求の番号を含める＝1 件目に
// 答えた後の 2 件目が「回答済み・反映待ち」に化けてボタンが消えない。
import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";

const root = new URL("../", import.meta.url);
const calls = [];
globalThis.__hudSendCalls = calls;
const API_STUB = "data:text/javascript," + encodeURIComponent(
  "export const approvalReply = async (...a) => { globalThis.__hudSendCalls.push(['reply', ...a]); return {ok:true}; };"
  + "export const postInstruction = async (...a) => { globalThis.__hudSendCalls.push(['post', ...a]); return {ok:true}; };");
registerHooks({ resolve(specifier, context, next) {
  if (specifier === "/ui/platform/api.js") return { url: API_STUB, shortCircuit: true };
  return next(specifier.startsWith("/ui/") ? new URL(specifier.slice(1), root).href : specifier, context);
} });

// 最小の DOM（send.js が触る分だけ）
class N {
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.hidden = false; this.textContent = ""; }
  get classList() {
    const set = (this._cls ||= new Set());
    return Object.assign([...set], { add: (...c) => c.forEach((x) => set.add(x)), remove: (...c) => c.forEach((x) => set.delete(x)),
      toggle: (c, on) => ((on ?? !set.has(c)) ? set.add(c) : set.delete(c)), contains: (c) => set.has(c) });
  }
  get firstChild() { return this.children[0]; }
  get parentElement() { return new N("div"); }
  append(...k) { for (const c of k) { if (c && typeof c === "object") c._parent = this; this.children.push(c); } }
  appendChild(k) { this.append(k); return k; }
  replaceChildren(...k) { this.children = [...k]; }
  removeAttribute() {}
  getAttribute() { return null; }
  setAttribute() {}
  querySelector() { return new N("i"); }
  querySelectorAll() { return []; }
  addEventListener() {}
  remove() { const p = this._parent; if (p) p.children = p.children.filter((c) => c !== this); this._parent = null; }
}
globalThis.document = { createElement: (t) => new N(t) };
globalThis.requestAnimationFrame = () => 0;

const { init } = await import("../ui/hud/send.js");
const { attnKeyFor } = await import("../ui/hud/board.js");

const T = (k) => k;
let world = null;
const nodes = new Map();
const shell = { querySelector: (q) => nodes.get(q) || nodes.set(q, new N("div")).get(q), querySelectorAll: () => [] };
const s = init({ shell, T, DEMO: false, attnKeyFor, getWorld: () => world, render: () => {} });
const askAgent = (askId) => ({ session: "sess-1", name: "p", attention: true, ask: { tool: "Bash", kind: "permission", askId } });

test("表示していた要求が替わっていたら、拒否や文章は送らない（別の要求へ効かせない）", async () => {
  calls.length = 0;
  world = { agents: [askAgent("b".repeat(32))] };           // 画面に出していたのは a、いまの最古は b
  const ok = await s.send("sess-1", "p", "Postgres で", "", "a".repeat(32));
  assert.equal(ok, false);
  assert.deepEqual(calls, [], "表示していない要求へ回答を送った");
});

test("表示していた要求がそのままなら、その番号で答える", async () => {
  calls.length = 0;
  world = { agents: [askAgent("b".repeat(32))] };
  assert.equal(await s.send("sess-1", "p", "やめて", "", "b".repeat(32)), true);
  assert.deepEqual(calls, [["reply", "sess-1", "deny", "やめて", "b".repeat(32)]]);
});

test("表示の番号を渡さない呼び出し（履歴の再送など）は、いまの要求へ（従来どおり）", async () => {
  calls.length = 0;
  world = { agents: [askAgent("c".repeat(32))] };
  assert.equal(await s.send("sess-1", "p", "続けて"), true);
  assert.deepEqual(calls, [["reply", "sess-1", "deny", "続けて", "c".repeat(32)]]);
});

test("❗ の内容キーは要求ごとに違う（1 件目に答えた後の 2 件目を「回答済み」にしない）", () => {
  assert.notEqual(attnKeyFor(askAgent("a".repeat(32))), attnKeyFor(askAgent("b".repeat(32))));
  assert.equal(attnKeyFor({ session: "sess-1" }), "approval:sess-1", "掲示の無い❗（推測）は従来のキー");
  const q = (askId) => ({ session: "sess-1", question: "どちら?", ask: { kind: "question", askId } });
  assert.notEqual(attnKeyFor(q("a".repeat(32))), attnKeyFor(q("b".repeat(32))));
});

// ── R102-11: ❗トレイ（とダイジェスト・シート）の答え方は掲示の種類に従う ─────────────────────────
// 実バグ: トレイは掲示を見ずに「承認して進める」の**文章**を出していた＝許可待ちに deny＋「そのまま進めて」が届き、
// 計画（ExitPlanMode）は差し戻されて plan モードから抜けられなかった（シートだけが allow を出していた）。
const { answerActs, init: initTray } = await import("../ui/hud/tray.js");
const { renderHistory } = await import("../ui/hud/board.js");

test("answerActs: 許可待ちは 許可する(allow)／許可しない／自由に書く＝「承認して進める」の文章を出さない", () => {
  const acts = answerActs({ ask: { tool: "Bash", kind: "permission" } }, T);
  assert.deepEqual(acts.map((a) => a.label), ["opt_allow", "opt_refuse", "opt_free"]);
  assert.equal(acts[0].allow, true);
  assert.equal(acts[1].text, "opt_refuse_text");
  assert.ok(!acts.some((a) => a.text === "opt_approve_text"), "許可待ちへ「はい、そのまま」を deny で送る");
});

test("answerActs: 計画（ExitPlanMode）は 承認(allow)／差し戻す（コメント）", () => {
  const acts = answerActs({ question: "プランの承認", ask: { tool: "ExitPlanMode", kind: "question" } }, T);
  assert.deepEqual(acts.map((a) => a.label), ["opt_plan_approve", "opt_plan_back"]);
  assert.equal(acts[0].allow, true);
  assert.equal(acts[1].compose, true);
  assert.equal(acts[1].comment, true);
});

test("answerActs: 質問の選択肢は文章（上限つき）・掲示の無い ❗（推測）は従来の 3 本＋自由に書く", () => {
  const q = answerActs({ ask: { tool: "AskUserQuestion", kind: "question" },
    questionOptions: [{ label: "A" }, { label: "B" }, { label: "C" }, { label: "D" }] }, T, 3);
  assert.deepEqual(q.map((a) => a.label), ["A", "B", "C", "opt_free"]);
  assert.ok(!q.some((a) => a.allow), "質問に allow を出す（答えにならない）");
  const guess = answerActs({ approvalMin: 3 }, T);
  assert.deepEqual(guess.map((a) => a.label), ["opt_approve", "opt_pause", "opt_report", "opt_free"]);
});

test("R128: 出勤の口の Bot の選択肢の無い相談に、セッション向けの 3 本（承認して進める・停止・進捗）を出さない＝自由な文だけ", () => {
  const botAsk = { tool: "office_ask", kind: "question", askId: "e".repeat(32) };
  for (const bot of [{ session: "bot-mailbox", vendor: "other" }, { session: "x", vendor: "bot" }, { session: "bot-mailbox", vendor: "bot" }]) {
    const open = answerActs({ ...bot, question: "どうする？", ask: botAsk }, T);
    assert.deepEqual(open.map((a) => a.label), ["opt_free"], JSON.stringify(bot));
    assert.ok(open[0].compose && !open[0].allow);
    const opts = answerActs({ ...bot, question: "送る？", ask: botAsk, questionOptions: [{ label: "はい" }, { label: "いいえ" }] }, T);
    assert.deepEqual(opts.map((a) => [a.label, a.text]), [["はい", "opt_text"], ["いいえ", "opt_text"], ["opt_free", undefined]]);
    assert.ok(!opts.some((a) => a.allow), "Bot に allow を出した（Bot は許可を持たない）");
  }
  // セッション（Claude・Codex）の選択肢の無い ❗ は今までどおり 3 本
  const session = answerActs({ session: "sess-1", vendor: "claude", approvalMin: 3 }, T);
  assert.deepEqual(session.map((a) => a.label), ["opt_approve", "opt_pause", "opt_report", "opt_free"]);
});

// トレイ本体: 最小の DOM で init し、数字キー 1 とダイジェストの経路（answerFirst）が allow になることを見る
const keyHandlers = [];
globalThis.window = { addEventListener: (t, f) => { if (t === "keydown") keyHandlers.push(f); }, removeEventListener() {} };
document.addEventListener = () => {};
document.removeEventListener = () => {};
globalThis.MutationObserver = class { observe() {} disconnect() {} };
const trayNodes = new Map();
const trayShell = {
  querySelector: (q) => trayNodes.get(q) || trayNodes.set(q, Object.assign(new N("div"), { hidden: q !== "#attn" })).get(q),
  closest: () => null,
};
const el = (tag, cls, text) => { const n = new N(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const delivered = [];
const trayWorld = { agents: [] };
const tray = initTray({ shell: trayShell, T, el, attnKeyFor, getWorld: () => trayWorld, render: () => {},
  delivery: { send: async (...a) => { delivered.push(["send", ...a]); return true; },
    allow: async (agent) => { delivered.push(["allow", agent.session, agent.ask?.askId]); return true; },
    answeredKey: () => "", answeredAt: () => null, retryAnswer() {}, isStalled: () => false },
  sheet: { openCompose: (a) => delivered.push(["compose", a.session]), closeCompose() {} },
  modals: { closeModal() {} } });
const permAgent = { id: "p1", session: "sess-9", name: "infra", attention: true, approvalMin: 1,
  ask: { tool: "Bash", kind: "permission", askId: "d".repeat(32) } };
const press = (key) => keyHandlers.forEach((f) => f({ key, target: { closest: () => null } }));
const trayButtons = () => trayNodes.get("#attn").children.filter((c) => c.dataset.idx !== undefined);

test("トレイ: 許可待ちに数字キー 1 → allow（deny の文章を送らない）", () => {
  delivered.length = 0;
  trayWorld.agents = [permAgent];
  tray.render(trayWorld);
  assert.deepEqual(trayButtons().map((c) => c.textContent), ["1 opt_allow", "2 opt_refuse", "3 opt_free"]);
  press("1");
  assert.deepEqual(delivered, [["allow", "sess-9", "d".repeat(32)]]);
  press("2");
  assert.deepEqual(delivered[1].slice(0, 4), ["send", "sess-9", "infra", "opt_refuse_text"]);
});

test("ダイジェスト（answerFirst）も同じ経路: 許可待ちの 1 番は allow", async () => {
  delivered.length = 0;
  trayWorld.agents = [permAgent];
  const acts = tray.first();
  assert.equal(acts[0].allow, true);
  assert.equal(await tray.answerFirst(0, acts[0]), true);
  assert.deepEqual(delivered, [["allow", "sess-9", "d".repeat(32)]]);
});

test("トレイ: 計画（ExitPlanMode）の 1 番は承認（allow）・2 番は差し戻しの指示欄", () => {
  delivered.length = 0;
  trayWorld.agents = [{ ...permAgent, question: "プランの承認",
    ask: { tool: "ExitPlanMode", kind: "question", askId: "e".repeat(32) } }];
  tray.render(trayWorld);
  press("1");
  press("2");
  assert.deepEqual(delivered, [["allow", "sess-9", "e".repeat(32)], ["compose", "sess-9"]]);
});

test("トレイ・最近の指示: 同じ中身で描き直してもノードを作り直さない（押した瞬間のクリックが消えない）", () => {
  trayWorld.agents = [permAgent];
  tray.render(trayWorld);
  const before = [...trayNodes.get("#attn").children];
  tray.render({ agents: [{ ...permAgent }] });
  assert.deepEqual(trayNodes.get("#attn").children, before, "同じ ❗ でトレイのボタンが作り直された");
  assert.equal(trayButtons()[0], before.find((c) => c.dataset.idx === "0"));
  tray.render({ agents: [{ ...permAgent, ask: { ...permAgent.ask, askId: "f".repeat(32) } }] });
  assert.notEqual(trayButtons()[0], before.find((c) => c.dataset.idx === "0"), "要求が替わったのに古いボタンのまま");

  const hist = new N("div");
  const hshell = { querySelector: () => hist };
  const w = { generatedAt: 1000, lang: "ja", history: [{ session: "s1", text: "テスト", disp: "p", ts: 900, pending: false }] };
  renderHistory(hshell, w, T);
  const row = hist.children[0];
  renderHistory(hshell, { ...w, history: w.history.map((h) => ({ ...h })) }, T);
  assert.equal(hist.children[0], row, "同じ履歴で ↻ の行が作り直された");
  renderHistory(hshell, { ...w, history: [{ ...w.history[0], pending: true }] }, T);
  assert.notEqual(hist.children[0], row, "状態が変わったのに描き直さない");
});
