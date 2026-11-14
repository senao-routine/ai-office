// ──────────────────────────────────────────────────────────────
// セッションの「種類」（計画の確認・許可待ち・質問・止まっている?・エラー…）と待っている長さの語。
// 純ロジック（DOM・通信・時刻・乱数に触らない）。Mac の HUD（❗トレイ・台帳・コックピット）が使う。
//
// スマホ（ui/pwa/app.js の sessKind / waitMin / waitText）と**同じ判定順・同じ刻み**で書く＝
// tests/ismute_parity.mjs が配信 HTML から PWA 側を切り出して突き合わせる（片方だけ直すとそこが落ちる）。
//
// world.js ではなくここに置く理由: world.js はスマホの部屋（relay/src/modules_data.js）にも同梱される＝触ると
// Worker の配信物と中継 E2E の指紋が変わる。Mac だけが使う判定はスマホの同梱物の外に置く。
// ──────────────────────────────────────────────────────────────
import { needsAttention } from "./world.js";

/** 止まった理由の列挙（server の _api_error_kind・relay_agent の _ERR_KINDS と同じ）。 */
export const ERR_KINDS = Object.freeze(["api", "limit", "ctx"]);
/** 権限モード（server の PERM_MODES・relay_agent の _PERM_MODES と同じ）。default は語を出さない。 */
export const PERM_MODES = Object.freeze(["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"]);

/** 生の err（{k, ago, …}）を列挙値と経過秒だけに畳む。知らない値は null（本文を持ち込まない）。 */
export function errFields(r) {
  if (!r || typeof r !== "object" || !ERR_KINDS.includes(r.k)) return null;
  return { k: r.k, ago: Math.max(0, Number(r.ago) || 0) };
}

/** 権限モード（知らない値は ""）。 */
export function permModeOf(p) {
  return PERM_MODES.includes(p?.permMode) ? p.permMode : "";
}

/** 止まった理由（作業中なら出さない＝再開した後に古い理由を貼らない）。PWA の errOf と同じ。 */
export function errOf(a) {
  const r = a && a.err;
  return r && typeof r.k === "string" && a.state !== "working" ? r : null;
}

/**
 * セッションの種類。PWA の sessKind と同じ判定順:
 * 計画の確認 → 許可待ち → 質問 → 止まっている?（承認フックの外で止まった推定） → エラー → 作業中 → 指示待ち → 休憩。
 */
export function sessionKind(a) {
  if (!a) return "rest";
  const ask = a.ask || {};
  if (ask.tool === "ExitPlanMode") return "plan";
  if (ask.kind === "permission") return "perm";
  if (a.question || ask.kind === "question") return "question";
  if ((Number(a.approvalMin) || 0) > 0) return "stuck";
  if (errOf(a)) return "error";
  if (a.state === "working" || (Number(a.minionsLive) || 0) > 0) return "working";
  if (a.state === "waiting") return "idle";
  return "rest";
}

/** ❗（あなた待ち）の種類。 */
export const ATTN_KINDS = Object.freeze(["plan", "perm", "question", "stuck"]);

/**
 * ❗の種類: plan / perm / question / stuck。❗でない人は error か ""。
 * サーバーが attention だけを立てて分を持たない形（承認フックの外の推定）も「止まっている?」に寄せる。
 */
export function attnKind(a) {
  const k = sessionKind(a);
  if (ATTN_KINDS.includes(k)) return k;
  if (needsAttention(a)) return "stuck";
  return k === "error" ? "error" : "";
}

/**
 * 待たせている分。PWA の waitMin と同じ順: 掲示が出てからの分（ask.min）→ approvalMin →
 * 質問なら最後に書いてからの age（質問は approvalMin が 0 のまま）→ 止まった理由の経過。
 */
export function waitMinutes(a) {
  if (!a) return 0;
  const ask = a.ask;
  if (ask && typeof ask.min === "number" && Number.isFinite(ask.min) && ask.min >= 0) return Math.floor(ask.min);
  const m = Math.floor(Number(a.approvalMin) || 0);
  if (m > 0) return m;
  if (a.question || (ask && ask.kind === "question")) {
    const lead = (Array.isArray(a.sessions) ? a.sessions : []).find((x) => x && x.session === a.session) || a;
    const age = Number(lead.age);
    if (lead.age != null && Number.isFinite(age) && age >= 0) return Math.floor(age / 60);
  }
  const er = errOf(a);
  return er ? Math.floor((Number(er.ago) || 0) / 60) : 0;
}

/** 待っている長さの短い語（「いま」「6分」「1時間5分」）。PWA の waitText と同じ刻み。 */
export function waitWord(minutes, lang = "ja") {
  const m = Math.max(0, Math.floor(Number(minutes) || 0));
  const en = lang === "en";
  if (m < 1) return en ? "now" : "いま";
  if (m < 60) return en ? `${m}m` : `${m}分`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return en ? `${h}h${r ? `${r}m` : ""}` : `${h}時間${r ? `${r}分` : ""}`;
}

/**
 * world の agent（buildWorld の 1 件）に、Mac の画面だけが読む生のフィールドを足す（err・permMode・minionsLive・tool）。
 * buildWorld はスマホと共有なので、Mac 専用の足し算はここで行う（raw は office_json の roster / employees の 1 件）。
 * tool（R110）＝最後の工具の名前（サーバーが 40 字に切る・中継には載らない＝スマホでは ""）。部屋の用事・画面の模様の材料。
 */
export function withKindFields(agent, raw) {
  if (!agent) return agent;
  return { ...agent, err: errFields(raw?.err), permMode: permModeOf(raw), minionsLive: Number(raw?.minionsLive) || 0,
    tool: typeof raw?.tool === "string" ? raw.tool.slice(0, 40) : "" };
}

/**
 * R128: 出勤の口の Bot（session "bot-<id>"・vendor "bot"）か。Bot は会話の transcript もターミナルも持たない＝
 * セッション向けの定型（承認して進める・停止して報告・進捗を報告）とターミナルへの移動を出さない・中央は記録（docs/door.md）。
 * vendor は world の正規化で "other" になることがある＝session の形でも見る。
 */
export function isBot(a) {
  return Boolean(a) && (String(a.vendor || "").toLowerCase() === "bot" || /^bot-/.test(String(a.session || "")));
}
