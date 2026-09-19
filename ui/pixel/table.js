// 台帳（R98）: プロジェクト行を既定で畳み、❗と選択中のプロジェクトだけセッション行を展開する。
// 行は key 付きで再利用する（クリック中の detach を避ける＝R67 と同じ型）。並べ替えは様式ローカル。
// 文字は全部 DOM。canvas（フロア帯・W2）には一切描かない。
import { STARVE_MIN, agoStr, attentionQueue } from "/ui/core/world.js";
import { frozen } from "/ui/platform/clock.js";
import { deliveryChip, paintDeliveryChip } from "/ui/hud/delivery.js";
import { paintArrivalBadge } from "/ui/hud/shell.js";

const SORT_KEY = "aioffice.pixel.sort";
const RANK = { attention: 0, working: 1, waiting: 2, resting: 3 };
const stateOf = (a) => (a.attention ? "attention" : a.state === "working" ? "working"
  : a.state === "resting" ? "resting" : "waiting");
const groupState = (rows) => (rows.some((a) => a.attention) ? "attention"
  : rows.some((a) => a.state === "working") ? "working"
  : rows.every((a) => a.state === "resting") ? "resting" : "waiting");

/** ctx: shell, T, el, getBoard(), getWorld(), selectedId(), onPick(projectId, session), arrivals, stalled(session) */
export function init({ shell, T, el, getBoard, getWorld, selectedId, onPick, arrivals, stalled }) {
  const host = shell.querySelector("#agents");
  let sort = "triage";
  try { sort = localStorage.getItem(SORT_KEY) || "triage"; } catch { /* プライベートモード */ }

  const cell = (cls, ...kids) => { const n = el("span", cls); n.append(...kids); return n; };
  const makeRow = (kind, a) => {
    const row = el("div", `pxrow ${kind}`);
    row.tabIndex = 0; row.setAttribute("role", "row");
    const prog = cell("c-prog aprog");
    const track = el("i", "abar"); track.append(el("i", "afill"));
    prog.append(track, el("span", "apct"));
    row.append(
      cell("c-mono", el("i", "mono")),
      cell("c-project", el("b", "pname"), el("span", "pvendors")),
      cell("c-session", el("span", "sname"), el("span", "scount")),
      cell("c-state", el("span", "stext"), deliveryChip({ T, agent: a, aliases: true, quietLive: true })),
      cell("c-detail", el("span", "dtext")),
      cell("c-evd", el("span", "evd", "—")),
      prog,
      cell("c-age", el("i", "age")));
    return row;
  };
  const setText = (node, text) => { if (node.textContent !== text) node.textContent = text; };
  const paintEvidence = (row, a, evidence, lang) => {
    const evd = row.querySelector(".evd");
    const ev = evidence && ["committed", "tested", "failed"].includes(evidence.kind) ? evidence : null;
    const cls = ev ? `evd evd-${ev.kind}` : "evd";
    if (evd.className !== cls) evd.className = cls;
    setText(evd, ev ? T(`evd_${ev.kind}`, agoStr(ev.ago, lang)) : T("evd_none"));
    evd.title = ev ? "" : T(a.vendor === "claude" ? "evd_none_hint" : "evd_nohook");
  };
  const fill = (row, a, { name, count, vendors, kind, selected, offline, lang, state, evidence }) => {
    // プロジェクト行の状態は**グループ全体**（groupState）。顔のセッションの状態で描くと、
    // 別のセッションが作業中でも「待機」に見える（別モデルレビュー）。セッション行は自分の状態。
    const st = state || stateOf(a);
    const cls = `pxrow ${kind} st-${a.state} zone-${a.zone}${a.attention ? " attn" : ""}${selected ? " sel" : ""}`;
    if (row.className !== cls) row.className = cls;
    row.dataset.project = a.id; row.dataset.session = a.session;
    row.dataset.state = st;
    const mono = row.querySelector(".mono");
    setText(mono, a.badge || "?");
    mono.className = `mono st-${st}`;
    setText(row.querySelector(".pname"), name);
    const pv = row.querySelector(".pvendors");
    if (pv.dataset.v !== vendors.join(",")) {
      pv.replaceChildren(...vendors.map((v) => {
        const dot = el("i", `pxvendor vendor-${v}`); dot.title = T(`board_vendor_${v}`); return dot;
      }));
      pv.dataset.v = vendors.join(",");
    }
    setText(row.querySelector(".sname"), kind === "sub" || count === 1 ? (a.name !== name ? a.name : "") : "");
    setText(row.querySelector(".scount"), kind === "proj" && count > 1 ? `×${count}` : "");
    setText(row.querySelector(".stext"), T(`board_${st}`));
    paintDeliveryChip(row.querySelector(".dstate"), { T, agent: a, aliases: true, quietLive: true,
      offline, stalled: stalled(a.session) });
    const detail = row.querySelector(".c-detail");
    detail.className = "c-detail" + (a.attention && a.approvalMin >= STARVE_MIN ? " starve" : "");
    setText(row.querySelector(".dtext"), a.detail || T("board_no_detail"));
    const c = a.work?.counts || {};
    const done = Number(c.completed) || 0;
    const total = done + (Number(c.in_progress ?? c.inProgress) || 0) + (Number(c.pending) || 0);
    const prog = row.querySelector(".aprog");
    prog.classList.toggle("empty", !(total > 0));
    if (total > 0) {
      prog.querySelector(".afill").style.width = `${Math.round(done / total * 100)}%`;
      setText(prog.querySelector(".apct"), `${done}/${total}`);
    } else setText(prog.querySelector(".apct"), "");
    setText(row.querySelector(".age"), agoStr(a.age, lang));
    paintEvidence(row, a, evidence === undefined ? a.evidence : evidence, lang);
    row.title = [name, a.name !== name ? a.name : "", a.detail, T(`board_${st}`)].filter(Boolean).join(" · ");
    if (!frozen) paintArrivalBadge(row.querySelector(".c-project"), arrivals?.label(a.session), T);
  };

  /** グループの証拠＝一番新しいもの（失敗も含めて「最後に起きたこと」） */
  const groupEvidence = (rows) => rows.map((s) => s.evidence).filter((e) => e && typeof e.ago === "number")
    .sort((p, q) => p.ago - q.ago)[0] || null;
  const render = (w, board = getBoard()) => {
    const sel = selectedId();
    const offline = Boolean(shell.closest(".offline"));
    // ❗の並びは ❗トレイ（attentionQueue）と同じ順＝最優先が面ごとに別人にならない（R80 の順序パリティ）。
    // session で引く: avatarMode=session では複数の agent が同じ id（プロジェクト）を持つ。
    // ★順位の正本は **❗トレイと同じ `attentionQueue(w.agents)`**（数字キーの回答先と一致させる）。
    //   リプレイで補った行など w.agents に居ない ❗だけを、その後ろへ並べる（live の順は動かさない）。
    const primary = attentionQueue(w.agents);
    const queueRank = new Map(primary.map((a, i) => [a.session, i]));
    let extra = primary.length;
    for (const g of board) {
      for (const s of g.sessions) {
        if (s.attention && !queueRank.has(s.session)) queueRank.set(s.session, extra++);
      }
    }
    const rank = (s) => queueRank.get(s.session) ?? Infinity;
    const groups = board.map((g, i) => ({ ...g, i, state: groupState(g.sessions),
      q: Math.min(...g.sessions.map(rank)) }));
    groups.sort((p, q) => {
      if (p.state === "attention" && q.state === "attention") return p.q - q.q;
      if (RANK[p.state] !== RANK[q.state] && (p.state === "attention" || q.state === "attention" || sort === "state")) {
        return RANK[p.state] - RANK[q.state];
      }
      if (sort === "name") return p.name.localeCompare(q.name, w.lang);
      if (sort === "age") return (Number(p.sessions[0]?.age) || 0) - (Number(q.sessions[0]?.age) || 0);
      return p.i - q.i;
    });
    const old = new Map([...host.querySelectorAll(".pxrow")].map((n) => [n.dataset.key, n]));
    let cursor = host.firstElementChild;
    const place = (row) => {
      if (cursor === row) cursor = cursor.nextElementSibling;
      else host.insertBefore(row, cursor);
    };
    for (const g of groups) {
      // ❗があるなら**キューで最優先のセッション**を行の顔にする（トレイと数字キーの対象と一致）。
      // 無ければ triageSort 順の先頭（代表セッション）。展開行も同じ順。
      const ordered = [...g.sessions].sort((a, b) => rank(a) - rank(b));
      const face = ordered[0];
      const vendors = [...new Set(g.sessions.map((s) => s.vendor))];
      const selected = g.sessions.some((s) => s.id === sel);
      const key = `p:${g.key}`;
      let row = old.get(key);
      if (!row) { row = makeRow("proj", face); row.dataset.key = key; }
      old.delete(key); place(row);
      fill(row, face, { name: g.name, count: g.sessions.length, vendors, kind: "proj", selected, offline, lang: w.lang,
        state: g.state, evidence: groupEvidence(g.sessions) });
      const expanded = g.sessions.length > 1 && (g.state === "attention" || selected);
      if (!expanded) continue;
      for (const s of ordered) {
        const skey = `s:${s.id}/${s.session}`;
        let sub = old.get(skey);
        if (!sub) { sub = makeRow("sub", s); sub.dataset.key = skey; }
        old.delete(skey); place(sub);
        fill(sub, s, { name: g.name, count: 1, vendors: [s.vendor], kind: "sub", selected: s.id === sel, offline, lang: w.lang });
      }
    }
    for (const n of old.values()) n.remove();
    let empty = host.querySelector(".pxempty");
    if (!groups.length && !empty) { empty = el("div", "pxempty", T("px_no_rows")); host.append(empty); }
    else if (groups.length && empty) empty.remove();
  };

  host.addEventListener("click", (e) => {
    const row = e.target.closest(".pxrow");
    if (row) onPick(row.dataset.project, row.dataset.session);
  });
  host.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches(".pxrow")) { e.preventDefault(); e.target.click(); }
  });
  shell.querySelector(".pxhrow").addEventListener("click", (e) => {
    const b = e.target.closest(".pxsort");
    if (!b) return;
    sort = sort === b.dataset.sort ? "triage" : b.dataset.sort;
    try { localStorage.setItem(SORT_KEY, sort); } catch { /* プライベートモード */ }
    for (const h of shell.querySelectorAll(".pxsort")) h.classList.toggle("on", h.dataset.sort === sort);
    const w = getWorld();
    if (w) render(w);
  });
  for (const h of shell.querySelectorAll(".pxsort")) h.classList.toggle("on", h.dataset.sort === sort);

  return {
    render,
    rows: () => host.querySelectorAll(".pxrow").length,
    /** テストの照準: プロジェクト行の中心（ページ座標） */
    point: (id) => {
      const r = host.querySelector(`.pxrow.proj[data-project="${CSS.escape(id)}"]`)?.getBoundingClientRect();
      return r ? { left: r.left + r.width / 2, top: r.top + r.height / 2 } : null;
    },
  };
}
