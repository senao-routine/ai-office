// R129 今日の 1 枚（2026-11-14）: その日のダイジェスト（GET /api/digest?since=0）から、人に見せてよい数だけを取り出す。
// 名前・プロジェクト・フォルダ・会話は**読まない**（返す形にも鍵が無い）＝SNS や動画にそのまま出せる 1 枚の材料。
// 純関数（DOM・通信・時刻・乱数に触らない＝ui/core の掟）。描くのは ui/hud/today_card.js。

const VENDORS = ["claude", "codex", "openclaw"];
const count = (v) => (Number.isFinite(v) && v > 0 ? Math.floor(v) : 0);

/**
 * @param digest office_timeline.digest_json の返り値（sessions・totals・day・available）
 * @param {{ bots?: number }} extra 今いる Bot の数（Bot は時間軸に入らない＝ダイジェストに居ない・R126）
 * @returns {{ day: string, sessions: number, vendors: Record<string, number>, bots: number, commits: number,
 *   answered: number, activeHours: number, tools: number, available: boolean }}
 */
export function todaySummary(digest, { bots = 0 } = {}) {
  const sessions = Array.isArray(digest?.sessions) ? digest.sessions : [];
  const totals = digest?.totals && typeof digest.totals === "object" ? digest.totals : {};
  const vendors = { claude: 0, codex: 0, openclaw: 0, other: 0 };
  for (const s of sessions) vendors[VENDORS.includes(s?.vendor) ? s.vendor : "other"] += 1;
  const tools = totals.tools && typeof totals.tools === "object"
    ? Object.values(totals.tools).reduce((sum, n) => sum + count(n), 0) : 0;
  const minutes = Number.isFinite(totals.activeMin) && totals.activeMin > 0 ? totals.activeMin : 0;
  return {
    day: typeof digest?.day === "string" && /^\d{4}-\d{2}-\d{2}$/.test(digest.day) ? digest.day : "",
    sessions: sessions.length,
    vendors,
    bots: count(bots),
    commits: count(totals.commits),
    answered: count(totals.asksAnswered),
    activeHours: Math.round(minutes / 6) / 10,
    tools,
    available: Boolean(digest?.available),
  };
}

/** 保存するファイルの名前（日付だけ＝名前やプロジェクトを入れない） */
export const todayFileName = (day) => `ai-office-${/^\d{4}-\d{2}-\d{2}$/.test(String(day)) ? day : "today"}.png`;
