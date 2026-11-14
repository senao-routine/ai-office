// 台帳（R98）: プロジェクト行を既定で畳み、❗と選択中のプロジェクトだけセッション行を展開する。
// 行は key 付きで再利用する（クリック中の detach を避ける＝R67 と同じ型）。並べ替えは様式ローカル。
// 文字は全部 DOM。canvas（フロア帯・俯瞰オフィス）には一切描かない。
//
// R99: ui/pixel/ から ui/hud/ へ移した。様式に依らない（core・platform・hud しか import しない）のに
// 様式の中に居ると、2 つ目の様式が**コピーを作るしかなくなる**（様式同士の import は禁止）。
// 並べ替えの保存キーだけ様式ごとに分ける（sortKey）＝台帳を 2 つ開いても互いの並びを壊さない。
import { STARVE_MIN, agoStr, attentionQueue } from "/ui/core/world.js";
import { ATTN_KINDS, attnKind, errOf } from "/ui/core/kind.js";
import { frozen } from "/ui/platform/clock.js";
import { deliveryChip, paintDeliveryChip } from "/ui/hud/delivery.js";
import { optional, paintArrivalBadge } from "/ui/hud/shell.js";

// b4-mac: 「エラーで停止」（利用上限・API エラー・会話の長さ）は ❗の次に並べる。以前は普通の「待機」に紛れていた
const RANK = { attention: 0, error: 0.5, working: 1, waiting: 2, resting: 3 };
const stateOf = (a) => (a.attention ? "attention" : errOf(a) ? "error" : a.state === "working" ? "working"
  : a.state === "resting" ? "resting" : "waiting");
const groupState = (rows) => (rows.some((a) => a.attention) ? "attention"
  : rows.some((a) => errOf(a)) ? "error"
  : rows.some((a) => a.state === "working") ? "working"
  : rows.every((a) => a.state === "resting") ? "resting" : "waiting");

/**
 * ctx: shell, T, el, getBoard(), getWorld(), selectedId(), onPick(projectId, session), arrivals, stalled(session)
 * R110（任意・渡した様式だけが変わる＝iso / pixel は渡さない＝従来の行のまま）:
 *   colorOf(projectKey)   行の個体色（--session）と `.c-project` の先頭の点 `<i class="dot">`（タブ・端末の点と同じ 1 本の割り当て）
 *   shortEvidence         証拠を短い語（`✓ 14分前`＝cp_evd_*）で出し、従来の全文（evd_*）は title へ
 *   selectedSession()     選択の印（.sel）を「このセッションを選んでいるか」で付ける（cockpit＝端末で選んでいる人）。
 *                         無ければ従来どおり selectedId()（指示欄の対象の id）で付ける
 *   preview(session)      任意の {text, live}。cockpit だけが渡し、2 行目に作業／最後の返事を出す
 */
export function init({ shell, T, el, getBoard, getWorld, selectedId, onPick, arrivals, stalled,
  sortKey = "aioffice.pixel.sort", colorOf = null, shortEvidence = false, selectedSession = null, preview = null }) {
  const host = shell.querySelector("#agents");
  const cockpit = shell.closest(".ui-cockpit") !== null;
  let sort = "triage";
  let dir = 1;         // R106-17: 1=昇順・-1=降順（3段階トグルの正本はここ）
  let rovingKey = null;   // R106-9: roving tabindex（台帳は Tab 1 か所だけ止まる）の正本
  try { sort = localStorage.getItem(sortKey) || "triage"; } catch { /* プライベートモード */ }
  // R106-12: 台帳の絞り込み（cockpit だけの要素＝optional で掴む・無ければ query は常に空）
  const searchInput = optional(shell, "#cp-ledger-search");
  let query = "";
  const norm = (s) => String(s || "").toLowerCase();

  const cell = (cls, ...kids) => { const n = el("span", cls); n.append(...kids); return n; };
  const makeRow = (kind, a) => {
    const row = el("div", `pxrow ${kind}`);
    // R106-9: roving tabindex（render() の最後で 1 行だけ 0 に戻す）。既定 -1＝素の Tab が
    // 行を 1 つずつ全部通過しない（47 行あると端末のタブ・一覧の手前が遠くなる実測バグ）。
    row.tabIndex = -1; row.setAttribute("role", "row");
    const prog = cell("c-prog aprog");
    const track = el("i", "abar"); track.append(el("i", "afill"));
    prog.append(track, el("span", "apct"));
    // R106-a11y: バッジ（1字の頭文字）は名前の直前に置かれていて、読み上げが区切りなしで
    // 「頭文字＋名前」を続けて言う（例: 「W 制作本部(works)」）＝名前の前に飾りの頭文字が重複して聞こえる。
    // 行自体に title と同じ aria-label を貼る（下で row.title を貼る直後）ので、バッジは読み上げから外す。
    const mono = el("i", "mono");
    mono.setAttribute("aria-hidden", "true");
    // R110: 個体色の点（colorOf を渡した様式だけ＝pixel の行の DOM は変わらない）。名前の前の飾り＝読み上げから外す
    const project = cell("c-project", el("b", "pname"), el("span", "pvendors"));
    if (colorOf) {
      const dot = el("i", "dot");
      dot.setAttribute("aria-hidden", "true");
      project.prepend(dot);
    }
    row.append(
      cell("c-mono", mono),
      project,
      cell("c-session", el("span", "sname"), el("span", "scount")),
      cell("c-state", el("span", "stext"), deliveryChip({ T, agent: a, aliases: true, quietLive: true })),
      cell("c-detail", el("span", "dtext")),
      cell("c-evd", el("span", "evd", "—")),
      prog,
      cell("c-age", el("i", "age")));
    if (preview) {
      const dots = el("span", "cppreview-dots");
      dots.setAttribute("aria-hidden", "true");
      dots.append(el("i"), el("i"), el("i"));
      row.querySelector(".dtext").append(dots, el("span", "cppreview-text"));
    }
    return row;
  };
  const setText = (node, text) => { if (node.textContent !== text) node.textContent = text; };
  /** 状態の語。❗は種類（許可・質問・計画・止まっている?）・エラーは理由（上限など）を言う（スマホの札と同じ語） */
  const stateWord = (a, st) => {
    if (st === "attention") {
      const k = a.attention ? attnKind(a) : "";
      return ATTN_KINDS.includes(k) ? T(`kind_${k}`) : T("board_attention");
    }
    if (st === "error") {
      if (cockpit) return T("cp_kind_error");
      const er = errOf(a);
      return er ? T("kind_error", T(`err_${er.k}`)) : T("board_waiting");
    }
    if (cockpit) return T(`cp_kind_${st}`);
    return T(`board_${st}`);
  };
  const paintEvidence = (row, a, evidence, lang) => {
    const evd = row.querySelector(".evd");
    const ev = evidence && ["committed", "tested", "failed"].includes(evidence.kind) ? evidence : null;
    const cls = ev ? `evd evd-${ev.kind}` : "evd";
    if (evd.className !== cls) evd.className = cls;
    const full = ev ? T(`evd_${ev.kind}`, agoStr(ev.ago, lang)) : T("evd_none");
    // R110: 短い語（✓ 14分前）は列を細くするため。何の証拠か（commit / test / 失敗）は title の全文が言う＝情報は落とさない
    setText(evd, ev && shortEvidence ? T(`cp_evd_${ev.kind}`, agoStr(ev.ago, lang)) : full);
    const title = ev ? (shortEvidence ? full : "") : T(a.vendor === "claude" ? "evd_none_hint" : "evd_nohook");
    if (evd.title !== title) evd.title = title;
  };
  const fill = (row, a, { name, count, vendors, kind, selected, offline, lang, state, evidence, color }) => {
    // プロジェクト行の状態は**グループ全体**（groupState）。顔のセッションの状態で描くと、
    // 別のセッションが作業中でも「待機」に見える（別モデルレビュー）。セッション行は自分の状態。
    const st = state || stateOf(a);
    const cls = `pxrow ${kind} st-${a.state} zone-${a.zone}${a.attention ? " attn" : ""}${selected ? " sel" : ""}`;
    if (row.className !== cls) row.className = cls;
    row.dataset.project = a.id; row.dataset.session = a.session;
    row.dataset.state = st;
    if (color && row.style.getPropertyValue("--session") !== color) row.style.setProperty("--session", color);
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
    setText(row.querySelector(".stext"), stateWord(a, st));
    paintDeliveryChip(row.querySelector(".dstate"), { T, agent: a, aliases: true, quietLive: true,
      offline, stalled: stalled(a.session) });
    const detail = row.querySelector(".c-detail");
    detail.className = "c-detail" + (a.attention && a.approvalMin >= STARVE_MIN ? " starve" : "");
    const pvLine = preview ? preview(a) : null;
    if (preview) {
      setText(row.querySelector(".cppreview-text"), pvLine?.text || a.detail || T("board_no_detail"));
      row.querySelector(".cppreview-dots").hidden = !pvLine?.live;
      detail.classList.toggle("live", Boolean(pvLine?.live));
      detail.title = pvLine?.text || "";
    } else setText(row.querySelector(".dtext"), a.detail || T("board_no_detail"));
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
    row.title = [name, a.name !== name ? a.name : "", pvLine?.text || a.detail, stateWord(a, st)].filter(Boolean).join(" · ");
    // R106-a11y: 行全体に 1 本の読み上げ名を貼る（区切りなしで頭文字や列が繋がって聞こえるのを防ぐ）。
    // title と同じ文面＝正本を 2 つ持たない。
    if (row.getAttribute("aria-label") !== row.title) row.setAttribute("aria-label", row.title);
    if (!frozen) paintArrivalBadge(row.querySelector(".c-project"), arrivals?.label(a.session), T);
  };

  /** グループの証拠＝一番新しいもの（失敗も含めて「最後に起きたこと」） */
  const groupEvidence = (rows) => rows.map((s) => s.evidence).filter((e) => e && typeof e.ago === "number")
    .sort((p, q) => p.ago - q.ago)[0] || null;
  const render = (w, board = getBoard()) => {
    // R110: selectedSession を渡した様式（cockpit）はセッションで選択を見る（端末で選んでいる人）。渡さなければ従来の id
    const selS = selectedSession ? selectedSession() : null;
    const sel = selectedSession ? null : selectedId();
    const isSel = (s) => (selectedSession ? Boolean(selS) && s.session === selS : s.id === sel);
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
      // ❗・エラーは並べ替えの列や向きを問わず上へ（R106-17: 降順でも埋もれさせない＝安全側の既定）。
      if (RANK[p.state] !== RANK[q.state] && (p.state === "attention" || q.state === "attention")) {
        return RANK[p.state] - RANK[q.state];
      }
      // R106-17: 同じ見出しの2回目クリックは降順（dir=-1）・3回目で既定(triage)へ戻る。
      if (sort === "state" && RANK[p.state] !== RANK[q.state]) return dir * (RANK[p.state] - RANK[q.state]);
      if (sort === "name") return dir * p.name.localeCompare(q.name, w.lang);
      if (sort === "age") return dir * ((Number(p.sessions[0]?.age) || 0) - (Number(q.sessions[0]?.age) || 0));
      return p.i - q.i;
    });
    // R106-12: 台帳の絞り込み（あいまい一致＝プロジェクト名かいずれかのセッション名/セッションIDに部分一致）。
    // 一致しない行は old に入らない＝下の「残りを消す」ループでそのまま畳まれる（別経路を持たない）。
    const visible = query ? groups.filter((g) => norm(g.name).includes(query)
      || g.sessions.some((s) => norm(s.name).includes(query) || norm(s.session).includes(query))) : groups;
    const old = new Map([...host.querySelectorAll(".pxrow")].map((n) => [n.dataset.key, n]));
    let cursor = host.firstElementChild;
    const place = (row) => {
      if (cursor === row) cursor = cursor.nextElementSibling;
      else host.insertBefore(row, cursor);
    };
    for (const g of visible) {
      // ❗があるなら**キューで最優先のセッション**を行の顔にする（トレイと数字キーの対象と一致）。
      // 無ければ triageSort 順の先頭（代表セッション）。展開行も同じ順。
      const ordered = [...g.sessions].sort((a, b) => rank(a) - rank(b));
      const face = ordered[0];
      const vendors = [...new Set(g.sessions.map((s) => s.vendor))];
      const selected = g.sessions.some(isSel);
      const color = colorOf ? colorOf(g.key) : null;
      const key = `p:${g.key}`;
      let row = old.get(key);
      if (!row) { row = makeRow("proj", face); row.dataset.key = key; }
      // 帯のホバーはここで引く。既定の avatarMode=session では同じフォルダのセッションが
      // 別々の id を持つので、代表の id で引くと本人しか光らない（別モデルレビューで実測）。
      if (row.dataset.group !== g.key) row.dataset.group = g.key;
      old.delete(key); place(row);
      fill(row, face, { name: g.name, count: g.sessions.length, vendors, kind: "proj", selected, offline, lang: w.lang,
        state: g.state, evidence: groupEvidence(g.sessions), color });
      const expanded = g.sessions.length > 1 && (g.state === "attention" || selected);
      if (!expanded) continue;
      for (const s of ordered) {
        const skey = `s:${s.id}/${s.session}`;
        let sub = old.get(skey);
        if (!sub) { sub = makeRow("sub", s); sub.dataset.key = skey; }
        old.delete(skey); place(sub);
        fill(sub, s, { name: cockpit ? s.name || s.session : g.name, count: 1,
          vendors: [s.vendor], kind: "sub", selected: isSel(s), offline, lang: w.lang, color });
      }
    }
    for (const n of old.values()) n.remove();
    let empty = host.querySelector(".pxempty");
    if (!visible.length) {
      if (!empty) { empty = el("div", "pxempty"); host.append(empty); }
      // 絞り込みで0件＝台帳自体は空でない（文言を分ける。同じ「セッションはまだありません」だと
      // 「サーバーが壊れた」と「打った名前が違う」を見分けられない）
      const msg = groups.length && query ? T("cp_ledger_search_empty") : T("px_no_rows");
      if (empty.textContent !== msg) empty.textContent = msg;
    } else if (empty) { empty.remove(); }
    // R106-9: roving tabindex（WAI-ARIA の grid/listbox と同じ型）。Tab はここに 1 か所だけ止まる・
    // 行をまたぐのは矢印キー。再描画のたびに「直前にフォーカスが居た行→選択中の行→先頭行」の優先で1つだけ0に戻す
    // （行は key 付きで再利用するので、同じ行なら同じ DOM ノード＝フォーカスは消えない）。
    const rows = [...host.querySelectorAll(".pxrow")];
    if (rows.length) {
      // 本当にフォーカスが居る行を最優先（tabIndex を変えても今のフォーカスは外れない＝ズレても
      // 「次に Tab で戻る場所」が実際のフォーカスと食い違うだけなので、ここで揃え直す）
      const active = document.activeElement;
      const keep = (active && rows.includes(active) && active)
        || rows.find((r) => r.dataset.key === rovingKey)
        || rows.find((r) => r.classList.contains("sel")) || rows[0];
      for (const r of rows) r.tabIndex = r === keep ? 0 : -1;
      rovingKey = keep.dataset.key;
    }
  };

  host.addEventListener("click", (e) => {
    const row = e.target.closest(".pxrow");
    if (row) onPick(row.dataset.project, row.dataset.session);
  });
  host.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches(".pxrow")) { e.preventDefault(); e.target.click(); return; }
    // R106-9: 矢印キーで行をまたぐ（roving tabindex）。Tab はここに留まらず次の面へ進む。
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    const row = e.target.closest(".pxrow");
    if (!row) return;
    const rows = [...host.querySelectorAll(".pxrow")];
    const i = rows.indexOf(row);
    if (i < 0) return;
    e.preventDefault();
    const j = e.key === "ArrowDown" ? Math.min(i + 1, rows.length - 1)
      : e.key === "ArrowUp" ? Math.max(i - 1, 0)
      : e.key === "Home" ? 0 : rows.length - 1;
    rows[j].focus();         // tabIndex の繰り上げ・rovingKey の更新は下の focusin に任せる（経路を 1 本にする）
  });
  // クリックでフォーカスが移った行も roving の「1 か所」として覚える（マウスの後に Tab で戻る位置が飛ばない）
  host.addEventListener("focusin", (e) => {
    const row = e.target.closest(".pxrow");
    if (!row) return;
    for (const r of host.querySelectorAll(".pxrow")) r.tabIndex = r === row ? 0 : -1;
    rovingKey = row.dataset.key;
  });
  const paintSortButtons = () => {
    for (const h of shell.querySelectorAll(".pxsort")) {
      const on = h.dataset.sort === sort;
      h.classList.toggle("on", on);
      if (on) h.dataset.dir = dir === -1 ? "desc" : "asc"; else delete h.dataset.dir;
    }
  };
  shell.querySelector(".pxhrow").addEventListener("click", (e) => {
    const b = e.target.closest(".pxsort");
    if (!b) return;
    // R106-17: 1回目=昇順・2回目（同じ列）=降順・3回目=既定(triage)に戻る3段階（従来は2回目で既定に戻っていた）
    if (sort !== b.dataset.sort) { sort = b.dataset.sort; dir = 1; }
    else if (dir === 1) { dir = -1; }
    else { sort = "triage"; dir = 1; }
    try { localStorage.setItem(sortKey, sort); } catch { /* プライベートモード */ }
    paintSortButtons();
    const w = getWorld();
    if (w) render(w);
  });
  paintSortButtons();
  // R106-12: 台帳の絞り込み（cockpit だけ・あいまい一致・Esc で消す・⌘K か / で入る）
  if (searchInput) {
    const refilter = () => {
      query = norm(searchInput.value.trim());
      const w = getWorld();
      if (w) render(w);
    };
    searchInput.addEventListener("input", refilter);
    searchInput.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      if (searchInput.value) { searchInput.value = ""; refilter(); } else searchInput.blur();
    });
    document.addEventListener("keydown", (e) => {
      const k = e.key.toLowerCase();
      const cmdK = (e.metaKey || e.ctrlKey) && k === "k";
      if (!cmdK && !(k === "/" && !e.metaKey && !e.ctrlKey && !e.altKey)) return;
      const active = document.activeElement;
      const tag = active?.tagName;
      // 本文入力中は "/" を奪わない（⌘K はどこからでも・ブラウザ既定のアドレスバー検索と被らないよう preventDefault）。
      // ただし指示を書いている最中（#sheet 内＝compose）は ⌘K も奪わない（レビュー指摘: ドラフトからフォーカスが無警告で持っていかれる）
      if (!cmdK && (tag === "INPUT" || tag === "TEXTAREA")) return;
      if (cmdK && active?.closest?.("#sheet")) return;
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    });
  }

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
