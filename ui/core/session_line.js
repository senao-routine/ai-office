// 一覧とオフィスの人の一覧で共有する、一文だけの近況。
// 文言は呼び出し側が strings.js から渡す。本文に DOM・時刻・保存状態を混ぜない。
const firstLine = (text) => String(text ?? "").split(/\r?\n/, 1)[0].trim();
const plain = (text) => firstLine(text).replace(/^[\p{Extended_Pictographic}\uFE0F\s]+/u, "").trim();
const quoted = (text, language) => language === "en" ? `“${text}”` : `「${text}」`;
const fileLike = (text) => /[/\\]/.test(text) || /\.[a-z][a-z\d_-]{0,15}[)）\]」』。.,]*$/i.test(text.replace(/[`*]/g, ""));
const prefix = /^(実行中|読込中|読込|読み込み中|読んでいます|編集中|編集|直しています|書込中|書き込み中|作成中|検索中|調査中|段取り整理中|準備中|考え中|Running|Reading|Editing|Writing|Creating|Searching)(?:\s*[:：·・]\s*|\s+|$)/i;

/**
 * words: { read, edit, write, run, think, rest, ask }（ui/hud/strings.js から注入）。
 * attentionText には既存の attnLine を渡す。ファイルの名前は働いている人の一文に出さない。
 */
export function sessionLine(member, language = "ja", attentionText = "", words = {}) {
  const m = member || {};
  if (m.attention || m.question || m.ask?.kind === "permission" || m.ask?.kind === "question")
    return plain(attentionText || m.question || m.ask?.title) || words.ask || "";

  if (m.state !== "working" && !(Number(m.minionsLive) > 0)) {
    const said = firstLine(m.lastSaid);
    return said ? quoted(said, language) : words.rest || "";
  }

  const item = m.feed?.[0];
  const raw = firstLine(typeof item === "string" ? item : item?.text || item?.t || "");
  if (/^💬\s*/u.test(raw)) {
    const said = raw.replace(/^💬\s*/u, "").trim();
    return said ? quoted(said, language) : words.think || "";
  }
  const text = plain(raw || [m.verb, m.target].filter(Boolean).join(" "));
  const match = prefix.exec(text);
  const description = text.replace(prefix, "").trim();
  if (!description) return words.think || "";
  if (!fileLike(description)) return description;

  const action = match?.[1] || m.verb || m.tool || "";
  if (/読|read/i.test(action)) return words.read || words.run || "";
  if (/編|直|edit/i.test(action)) return words.edit || words.run || "";
  if (/書|作成|writ|creat/i.test(action)) return words.write || words.run || "";
  return words.run || "";
}
