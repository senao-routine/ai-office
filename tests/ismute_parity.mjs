// R86-G: 「送っても即座には届かない」の判定を Mac と スマホで**同一**に保つ機械ピン。
// 片方だけ直すとここが落ちる（gloss_parity / triage_parity と同じ流儀）。
//
// この判定が食い違うと最悪の形で壊れる: ❗が立っている（＝ターンが終わらず届かない）のに
// 片方の画面だけ「届きます」と言う。実際 R86-E では working を一律除外していたため
// **❗が立ってから最初の164秒だけ警告が消える**穴があった（実測）。
import { APP_HTML as src } from "../relay/src/app_html.js";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const begin = src.indexOf("function isMute(");
const end = src.indexOf("function muteChip(", begin);
assert.ok(begin >= 0 && end > begin, "PWA_ISMUTE source not found in app_html.js");

// 生成モジュールを評価した配信HTMLから、対象関数の範囲だけを抽出。
const chunk = src.slice(begin, end);
globalThis.__ismuteParity = {};
// PWA 側は needsAttn（APP_HTML 内の別関数）に依存する。正本と同義の実装を与える
globalThis.needsAttn = (e) => (e.approvalMin > 0) || !!e.question;
(0, eval)(chunk.replace("function isMute", "__ismuteParity.isMute = function isMute"));
const pwa = globalThis.__ismuteParity.isMute;

const core = await import(join(ROOT, "ui/core/world.js"));

const CASES = [
  { listening: false, state: "waiting" },
  { listening: false, state: "resting" },
  { listening: false, state: "working" },                       // 稼働中＝誤警告しない
  { listening: false, state: "working", approvalMin: 2 },       // ❗中は working でも届かない
  { listening: false, state: "working", question: "どっち?" },   // 質問中も同じ
  { listening: false, state: "waiting", approvalMin: 5 },
  { listening: true, state: "waiting" },
  { listening: true, state: "working", approvalMin: 3 },
  { state: "waiting" },                                          // listening 未搬送＝脅さない
  { state: "working", approvalMin: 1 },
  // R86-H: 承認フックが待っている＝Stop hook が動いていなくても「いま直接届く」
  { listening: false, state: "working", approvalMin: 4, ask: { tool: "Bash", kind: "permission" } },
  { listening: false, state: "waiting", ask: { tool: "AskUserQuestion", kind: "question" } },
];

for (const c of CASES) {
  const a = core.isMuted(c);
  const b = pwa(c);
  assert.equal(b, a, `不一致: ${JSON.stringify(c)} core=${a} pwa=${b}`);
}
// 穴の再発を直接ピン（working + ❗ は必ず「届かない」側）
assert.equal(core.isMuted({ listening: false, state: "working", approvalMin: 2 }), true);
assert.equal(pwa({ listening: false, state: "working", question: "q" }), true);
// 承認フックが待っているときは両側とも「届く」（📴を出さない）
assert.equal(core.isMuted({ listening: false, state: "working", approvalMin: 2,
                            ask: { kind: "permission" } }), false);
assert.equal(pwa({ listening: false, state: "working", approvalMin: 2, ask: { kind: "permission" } }), false);
console.log(`✓ isMute parity: ${CASES.length} ケースで Mac↔スマホ一致`);

// R96-B: 配達状態の 5 語彙（live/saved/pending/stalled/offline）も Mac と スマホで同一に保つ。
// PWA の deliveryState は isMute と同じ APP_HTML から切り出す（順序: offline > stalled > pending > saved > live）。
{
  const dBegin = src.indexOf("function deliveryState(");
  const dEnd = src.indexOf("function deliveryChip(", dBegin);
  assert.ok(dBegin >= 0 && dEnd > dBegin, "PWA deliveryState source not found in app_html.js");
  globalThis.isMute = pwa;
  (0, eval)(src.slice(dBegin, dEnd).replace("function deliveryState", "__ismuteParity.deliveryState = function deliveryState"));
  const pwaState = globalThis.__ismuteParity.deliveryState;
  const OPTS = [{}, { offline: true }, { stalled: true }, { offline: true, stalled: true }];
  let n = 0;
  for (const c of [...CASES, { ...CASES[0], pending: true }, { ...CASES[6], pending: true }, { listening: false, state: "working", approvalMin: 2, pending: true }]) {
    for (const o of OPTS) {
      const a = core.deliveryState(c, o), b = pwaState(c, o);
      assert.ok(core.DELIVERY_STATES.includes(a), `core が未知の状態: ${a}`);
      assert.equal(b, a, `deliveryState 不一致: ${JSON.stringify(c)} ${JSON.stringify(o)} core=${a} pwa=${b}`);
      n++;
    }
  }
  assert.equal(core.deliveryState({ listening: true, state: "waiting", pending: true }), "pending");
  assert.equal(pwaState({ listening: false, state: "waiting" }), "saved");
  assert.equal(pwaState({ listening: true, state: "waiting" }, { stalled: true }), "stalled");
  console.log(`✓ deliveryState parity: ${n} ケースで Mac↔スマホ一致`);
}

// R102-1/R102-4（b4-mac）: セッションの種類（計画・許可・質問・止まっている?・エラー…）と待っている分の語を
// Mac（ui/core/world.js の sessionKind / waitMinutes / waitWord）と スマホ（sessKind / waitMin / waitText）で同一に保つ。
// Mac の ❗トレイ・台帳・コックピットが「N分 止まっています」とだけ言い、種類を言わなかった（スマホは札を出していた）。
{
  const kind = await import(join(ROOT, "ui/core/kind.js"));
  const kBegin = src.indexOf("function errOf(");
  const kEnd = src.indexOf("function kindWord(", kBegin);
  const wBegin = src.indexOf("function waitMin(");
  const wEnd = src.indexOf("function actBtn(", wBegin);
  assert.ok(kBegin >= 0 && kEnd > kBegin, "PWA sessKind source not found in app_html.js");
  assert.ok(wBegin >= 0 && wEnd > wBegin, "PWA waitText source not found in app_html.js");
  (0, eval)(src.slice(kBegin, kEnd).replace("function errOf", "globalThis.errOf = function errOf")
    .replace("function sessKind", ";__ismuteParity.sessKind = function sessKind"));
  let LANG = "ja";
  globalThis.T = (ja, en) => (LANG === "en" ? en : ja);
  (0, eval)(src.slice(wBegin, wEnd).replace("function waitMin", "globalThis.waitMin = function waitMin")
    .replace("function waitText", ";__ismuteParity.waitText = function waitText"));
  const { sessKind, waitText } = globalThis.__ismuteParity;
  const KC = [
    ...CASES,
    { state: "working", ask: { tool: "ExitPlanMode", kind: "permission" }, approvalMin: 2 },
    { state: "working", ask: { tool: "Bash", kind: "permission", min: 6 }, approvalMin: 1 },
    { state: "waiting", question: "どっち?", age: 400 },
    { state: "waiting", ask: { tool: "AskUserQuestion", kind: "question" }, session: "s1", age: 30,
      sessions: [{ session: "s1", age: 3725 }] },
    { state: "waiting", err: { k: "limit", ago: 900 } },
    { state: "working", err: { k: "api", ago: 60 } },               // 作業中なら古い理由を貼らない
    { state: "resting", err: { k: "ctx", ago: 7300 } },
    { state: "waiting", minions: 2 },
    { state: "waiting", minions: 2, minionsLive: 1 },
    { state: "resting" },
    {},
  ];
  let n = 0;
  for (const c of KC) {
    const a = kind.sessionKind(c), b = sessKind(c);
    assert.equal(b, a, `sessKind 不一致: ${JSON.stringify(c)} core=${a} pwa=${b}`);
    for (const lang of ["ja", "en"]) {
      LANG = lang;
      const wa = kind.waitWord(kind.waitMinutes(c), lang), wb = waitText(c);
      assert.equal(wb, wa, `waitText 不一致(${lang}): ${JSON.stringify(c)} core=${wa} pwa=${wb}`);
    }
    n++;
  }
  // ❗の種類: 分の無い attention も「止まっている?」（Mac だけの語・トレイの見出し）
  assert.equal(kind.attnKind({ attention: true, state: "waiting" }), "stuck");
  assert.equal(kind.attnKind({ ask: { tool: "Bash", kind: "permission" }, approvalMin: 1 }), "perm");
  assert.equal(kind.attnKind({ state: "waiting", err: { k: "limit", ago: 60 } }), "error");
  assert.equal(kind.attnKind({ state: "working" }), "");
  console.log(`✓ sessKind/waitText parity: ${n} ケースで Mac↔スマホ一致`);
}
