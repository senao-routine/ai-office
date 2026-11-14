// 明るい3列のアプリ様式。会話・送信・許可の実処理は共有HUDを使う。
import { agoStr, buildWorld, summarizeWorld } from "/ui/core/world.js";
import { attnKind, isBot, permModeOf, waitMinutes, waitWord } from "/ui/core/kind.js";
import { optionLabel, questionStem, sameQuestion } from "/ui/core/activity.js";
import { sessionLine } from "/ui/core/session_line.js";
import { epochNow, frozen, loop, now, setTime } from "/ui/platform/clock.js";
import { getActivity, getDigest } from "/ui/platform/api.js";
import { createArrivals } from "/ui/platform/arrivals.js";
import { installProbe } from "/ui/platform/probe.js";
import { STYLES } from "/ui/platform/style.js";
import { activityPaintKey, createActivityFeed, samePaint } from "/ui/hud/activity_feed.js";
import { createActivityView, formatDuration } from "/ui/hud/activity.js";
import { attnKeyFor, attnLine, departmentBoard, mutedSessions } from "/ui/hud/board.js";
import { init as initSend } from "/ui/hud/send.js";
import { init as initSheet } from "/ui/hud/sheet.js";
import { init as initDialog } from "/ui/hud/dialog.js";
import { init as initTray, answerActs } from "/ui/hud/tray.js";
import { init as initAdmin } from "/ui/hud/admin.js";
import { init as initModals } from "/ui/hud/modals.js";
import { init as initGauges, billingOf, fmtTok } from "/ui/hud/gauges.js";
import { init as initHire } from "/ui/hud/hire.js";
import { init as initSession } from "/ui/hud/session.js";
import { applyStaticStrings, el } from "/ui/hud/shell.js";
import { plainT, plainText } from "/ui/hud/plain.js";
import { T as rawT, lang, setLang } from "/ui/hud/strings.js";
import { createPressHold } from "/ui/hud/press_hold.js";
import { drawTodayCard } from "/ui/hud/today_card.js";
import { todayFileName, todaySummary } from "/ui/core/today.js";
import { init as initTerm } from "/ui/cockpit/term.js";
import { createOffice } from "./office.js";
import { applyAppStrings, template } from "./shell.js";

export const STYLE = STYLES.APP;
const T = plainT(rawT);

export async function mount(root) {
  const params = new URLSearchParams(location.search);
  const DEMO = params.get("demo") === "1" || document.querySelector('meta[name="office-demo"]')?.content === "1";
  root.replaceChildren();
  root.className = frozen ? "hud ui-app no-anim" : "hud ui-app";
  const shell = el("div", "app-shell");
  shell.innerHTML = template();
  root.append(shell);
  // 配色を確定してからCSSを載せる。保存済みの端末テーマで画面全体を暗くしない。
  document.documentElement.dataset.theme = "light";
  document.documentElement.removeAttribute("data-theme-auto");
  for (const href of ["/ui/hud/hud.css", "/ui/vendor/xterm/xterm.css", "/ui/app/style.css"]) {
    const css = document.createElement("link");
    css.rel = "stylesheet"; css.href = href;
    await new Promise((resolve) => { css.onload = resolve; css.onerror = resolve; document.head.append(css); });
  }
  const q = (selector) => shell.querySelector(selector);
  let officeData = null, built = null, board = [], all = [], selected = null;
  let terminal = false, termUi = null, session = null, gauges = null, disposed = false;
  let activityResult = null, beforeScroll = null, composing = false, sending = false;
  // draftAsk: 下書きを書き始めた時点の要求の番号（R100-0 と同じ縛り）。途中で要求が替わっても別の要求へ文章を答えない。
  const views = new Map(), drafts = new Map(), draftAsk = new Map();
  const getWorld = () => built && ({ ...built, agents: all });
  const member = () => all.find((a) => a.session === selected) || null;
  const liveTerm = () => termUi?.forSession(selected);
  const strings = () => { applyStaticStrings(shell, T); applyAppStrings(shell, T); };
  strings();
  const words = () => Object.fromEntries(["read", "edit", "write", "run", "think", "rest", "ask"].map((key) => [key, T(`app_line_${key}`)]));
  // R123-B: 質問はサーバーが付けた「（選択肢 / …）」の連結を外して言う＝ボタンや会話の中の選択肢と二重にしない。
  // bare: 同じ質問が会話の末尾に見えているときは種類と待ち時間だけ（「質問 · 2分」）。
  const isQuestion = (m) => Boolean(m?.question) && attnKind(m) === "question";
  const asked = (m, bare = false) => isQuestion(m)
    ? T("attn_question", bare ? "" : questionStem(m.question, m.questionOptions), waitWord(waitMinutes(m), lang()))
    : attnLine(T, m, lang());
  const line = (m) => sessionLine(m, lang(), asked(m), words());
  // 待たせている行は「最後に動いた時刻」ではなく待ち始めた時刻で言う（同じ行の「質問 · 1分」と食い違わない）
  const rowAgo = (m) => agoStr(m.attention ? waitMinutes(m) * 60 : Math.max(0, Number(m.age) || 0), lang());
  const shortTime = (seconds) => seconds >= 60 ? T("app_minutes", Math.floor(seconds / 60)) : T("app_seconds", Math.max(0, Math.floor(seconds)));
  const stateWord = (m) => T(m?.attention ? "app_waiting" : m?.state === "working" ? "app_working" : "app_resting");
  const statusClass = (m) => m.attention ? "is-attention" : m.state === "working" ? "is-working" : "is-idle";
  const permissionWord = (m) => T("perm_mode", permModeOf(m)) || T("app_permission_confirm");

  const modals = initModals({ shell });
  const common = { shell, T, getWorld, render: () => render() };
  const delivery = initSend({ ...common, DEMO, attnKeyFor });
  const dialog = initDialog({ T, el, modals });
  const sheet = initSheet({ ...common, lang, DEMO, attnKeyFor, delivery, dialog,
    focusOn() {}, focusOff() {}, getTemplates: () => admin.getTemplates(),
    openTemplateEditor: (text) => admin.openTemplateEditor(text) });
  const tray = initTray({ ...common, el, attnKeyFor, delivery, sheet, modals, DEMO });
  const admin = initAdmin({ ...common, root, lang, setLang, demo: DEMO, modals, billingOf, fmtTok,
    showToast: delivery.showToast, applyStaticStrings: strings,
    version: () => officeData?.app?.version || null,
    reapplyWorld: () => { if (officeData) apply({ ...officeData, lang: lang() }); } });
  const arrivals = createArrivals({ isolated: frozen, demo: DEMO });
  const hire = initHire({ ...common, lang, DEMO, modals, arrivals, refresh: () => session?.refresh(), showToast: delivery.showToast });
  const office = createOffice({ host: q("#app-room"), T, demo: DEMO, onSelect: (a) => select(a.session || a.id) });
  const paintRoomMode = () => {
    q("#app-room-3d").setAttribute("aria-pressed", String(office.mode() === "3d"));
    q("#app-room-pixel").setAttribute("aria-pressed", String(office.mode() === "pixel"));
  };
  q("#app-room").addEventListener("app-room-change", paintRoomMode);
  for (const mode of ["3d", "pixel"]) q(`#app-room-${mode}`).addEventListener("click", () => { office.setMode(mode); paintRoomMode(); });
  paintRoomMode();

  // R128: Bot へ送れた後の取り直し。取得中に送れたら、その取得が終わってからもう 1 回（取得中の load は黙って何もしない）
  let reloadAfter = null;
  const reloadBot = (sid) => {
    const m = member();
    if (!m || m.session !== sid) return;
    if (feed.busy) reloadAfter = sid; else feed.load(m);
  };
  const feed = createActivityFeed({ fetchActivity: getActivity, now, frozen, demo: DEMO,
    onChange: (sid, _page, options) => {
      if (options.settled && reloadAfter === sid) { reloadAfter = null; reloadBot(sid); }
      if (disposed || sid !== selected || terminal) return;
      const scroll = q("#app-activity-scroll");
      const atBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 48;
      paintActivity();
      if (options.before && beforeScroll?.session === sid) {
        scroll.scrollTop = beforeScroll.top + scroll.scrollHeight - beforeScroll.height;
        beforeScroll = null;
      } else if (atBottom) scroll.scrollTop = scroll.scrollHeight;
    } });

  function answerFromCenter(m, index, label, key) {
    const current = all.find((a) => a.session === m.session);
    if (!current || attnKeyFor(current) !== key) { delivery.showToast(T("ask_changed"), false); render(); return; }
    if (!tray.show(m.session)) { sheet.openCompose(current); return; }
    const button = q(`#attn button[data-idx="${index}"]`);
    if (button && button.textContent === `${index + 1} ${label}`) button.click();
    else sheet.openCompose(current);
  }

  function decorateActivity(host, activity, options) {
    const { page, member: m } = options;
    if (page?.beg && !page.top) {
      const more = el("button", "app-before", T("cp_activity_before"));
      more.type = "button"; more.disabled = feed.busy;
      more.addEventListener("click", () => {
        const scroll = q("#app-activity-scroll");
        beforeScroll = { session: m.session, top: scroll.scrollTop, height: scroll.scrollHeight };
        feed.load(m, { before: true });
      });
      host.prepend(more);
    } else if (page?.loaded && page.top) host.prepend(el("p", "act-note", T(isBot(m) ? "bot_record_start" : "cp_activity_start")));
    if (page?.gap) host.prepend(el("p", "act-note", T("cp_activity_gap")));
    if (!m?.attention && !m?.ask) return;
    // R123-B: 同じ質問が会話の末尾（選択肢つき）に見えているなら、カードは種類と待ち時間とボタンだけ。
    // 「同じ」は本文の全部と選択肢の並び（先頭だけだと、状態が会話より先に替わったとき古い質問を見ながら新しい要求に答えてしまう）
    const above = isQuestion(m) && sameQuestion(activity?.blocks?.at(-1), m.question, m.questionOptions);
    const box = el("section", `app-answer act-pending${above ? " is-follow" : ""}`); box.id = "app-answer";
    box.dataset.ask = m.question ? "question" : m.ask?.kind === "permission" ? "permission" : "ask";
    box.append(el("p", above ? "app-question-kind" : "app-question", asked(m, above) || T("ask_live")));
    if (m.attention && !DEMO) {
      const acts = el("div", "app-answer-actions"), key = attnKeyFor(m);
      const opts = isQuestion(m) && Array.isArray(m.questionOptions) ? m.questionOptions : [];
      if (delivery.answeredKey(m.session) === key) acts.append(el("span", "act-note", T("dstate_pending")));
      else answerActs(m, T, 3).forEach((act, index) => {
        // 選択肢のボタンは「(Recommended)」を文から外して札にする（会話の中の選択肢と同じ見せ方）。届く文は元のまま
        const shown = !act.compose && index < opts.length ? optionLabel(opts[index]) : { label: act.label, rec: false };
        const b = el("button", act.allow ? "app-primary" : "", shown.label || act.label);
        if (shown.rec) b.append(el("span", "app-rec", T("app_recommended")));
        b.type = "button"; b.dataset.answer = String(index);
        b.addEventListener("click", () => answerFromCenter(m, index, act.label, key));
        acts.append(b);
      });
      box.append(acts);
    }
    host.append(box);
  }

  // 押している間は中央を描き直さない（作り直しが来ると押したボタンのクリックが消える＝ui/hud/press_hold.js）
  const hold = createPressHold(q("#app-activity"));
  let painted = null;   // 前回描いた { host, rows, key, first, last }＝同じなら描き直さない（activity_feed.js の activityPaintKey）
  function paintActivity() {
    if (hold.defer(paintActivity)) return;
    const m = member(), host = q("#app-activity");
    if (!m) { painted = null; host.replaceChildren(el("p", "app-empty", T("app_empty"))); activityResult = null; paintDetails(); return; }
    if (!views.has(m.session)) views.set(m.session, createActivityView({ lang, decorate: decorateActivity }));
    const page = feed.page(m.session);
    // 固定時刻/デモの既存本文は取得せず、ワールドに載った最後の返事だけを表示する。
    const rows = !page.loaded && (frozen || DEMO) && m.lastSaid ? [{ k: "a", t: m.lastSaid }] : page.rows;
    // R128: 出勤の口の Bot は記録（server/source_door_log.py の行・同じ GET /api/session/activity）。record＝考え中を出さない・日時を添える
    const bot = isBot(m), live = m.state === "working" && !m.attention;
    // 同じ人・同じ行・同じ相談の材料なら描き直さない（文の選択と押したボタンを保つ・経過秒は下の毎秒のループが書き換える）
    const key = activityPaintKey(m, [lang(), live, bot, page.loaded, page.top, page.beg, page.gap, feed.busy, DEMO,
      delivery.answeredKey(m.session) || "", attnKeyFor(m), asked(m), asked(m, true)]);
    if (activityResult && samePaint(painted, host, rows, key)) { paintHeader(); paintDetails(); return; }
    activityResult = views.get(m.session).render(host, rows, {
      live, now: epochNow(), member: m, page, gap: page.gap,
      state: m.state, ask: m.ask, record: bot,
    });
    if (!rows.length && !m.attention && (bot ? page.loaded : m.state !== "working")) host.append(el("p", "act-note", T(bot ? "bot_record_empty" : "cp_term_empty")));
    painted = { host, rows, key, first: host.firstElementChild, last: host.lastElementChild };
    paintHeader(); paintDetails();
  }

  // 行は session ごとに 1 つの節を使い回す＝再取得のたびに作り直すとフォーカスと押しかけのクリックが失われる。
  const rowCache = new WeakMap(), groupCache = new Map();
  function fillRow(b, m, people) {
    const cls = `app-session-row ${statusClass(m)}${selected === m.session ? " is-selected" : ""}`;
    if (b.className !== cls) b.className = cls;
    b.setAttribute("aria-current", selected === m.session ? "true" : "false");
    const [dot, copy, time] = b._parts;
    dot.setAttribute("aria-label", stateWord(m));
    const [name, said] = copy.children;
    if (name.textContent !== m.name) name.textContent = m.name;
    const text = line(m);
    if (said.textContent !== text) said.textContent = text;
    const ago = rowAgo(m);
    if (time.textContent !== ago) time.textContent = ago;
    const want = people ? [dot, copy, time] : [copy, m.state === "working" && !m.attention ? dot : time];
    if (want.length !== b.children.length || want.some((n, i) => b.children[i] !== n)) b.replaceChildren(...want);
  }
  function syncChildren(host, nodes) {
    if (nodes.length !== host.children.length || nodes.some((n, i) => host.children[i] !== n)) host.replaceChildren(...nodes);
  }
  function syncRows(host, list, people = false, lead = []) {
    let cache = rowCache.get(host);
    if (!cache) rowCache.set(host, cache = new Map());
    const nodes = list.map((m) => {
      let b = cache.get(m.session);
      if (!b) {
        b = el("button", ""); b.type = "button"; b.dataset.appSession = m.session;
        b._parts = [el("span", "app-row-dot"), el("span", "app-row-copy"), el("span", "app-row-time")];
        b._parts[1].append(el("span", "app-row-name"), el("span", "app-row-line"));
        b.addEventListener("click", () => select(b.dataset.appSession));
        cache.set(m.session, b);
      }
      fillRow(b, m, people);
      return b;
    });
    const keep = new Set(list.map((m) => m.session));
    for (const sid of cache.keys()) if (!keep.has(sid)) cache.delete(sid);
    syncChildren(host, [...lead, ...nodes]);
  }

  function paintLists() {
    const filter = q("#app-search").value.trim().toLocaleLowerCase();
    // 名前（自動タイトル）でもプロジェクト名でも引ける
    const matches = (m) => !filter || [m.name, m.projectName].some((v) => String(v || "").toLocaleLowerCase().includes(filter));
    const waiting = all.filter((a) => a.attention);
    q("#app-your-count").textContent = String(waiting.length);
    q(".app-your-turn").classList.toggle("is-empty", !waiting.length);
    syncRows(q("#app-attention-list"), waiting.filter(matches));
    const projects = q("#app-projects"), sections = [], keep = new Set();
    for (const group of board) {
      const sessions = group.sessions.filter(matches);
      if (!sessions.length) continue;
      let g = groupCache.get(group.key);
      if (!g) groupCache.set(group.key, g = { section: el("section", "app-project-group"), head: el("h2", "") });
      if (g.head.textContent !== group.name) g.head.textContent = group.name;
      syncRows(g.section, sessions, false, [g.head]);
      sections.push(g.section); keep.add(group.key);
    }
    for (const key of groupCache.keys()) if (!keep.has(key)) groupCache.delete(key);
    if (!sections.length) {
      const empty = projects.querySelector(":scope > .app-empty") || el("p", "app-empty");
      empty.textContent = T(filter ? "app_no_matches" : "app_no_sessions");
      sections.push(empty);
    }
    syncChildren(projects, sections);
    const rank = (a) => a.attention ? 0 : a.state === "working" ? 1 : 2;
    const people = [...all].sort((a, b) => rank(a) - rank(b));
    syncRows(q("#app-people"), people, true);
  }

  function paintHeader() {
    const m = member(), live = liveTerm(), bot = isBot(m);
    q("#app-title").textContent = m?.name || "AI Office";
    // 名前がプロジェクト名そのもの（/rename も自動タイトルも無い）なら、同じ字を 2 回並べない
    const project = q("#app-project");
    project.textContent = m && m.projectName !== m.name ? m.projectName || "" : "";
    project.hidden = !project.textContent;   // 空の要素で題と札の間の gap が 2 つ分にならない
    const state = q("#app-state"); state.hidden = !m;
    if (m) {
      const turn = [...(activityResult?.blocks || [])].reverse().find((b) => b.type === "user" && Number.isFinite(b.ts));
      const elapsed = turn && epochNow() >= turn.ts ? shortTime(epochNow() - turn.ts) : "";
      state.className = `app-state ${statusClass(m)}`;
      // R128: Bot の作業の経過は人の言葉からではない（Bot の記録の吹き出しは答え・指示）＝最後に動いた時刻で言う
      const when = m.attention ? waitWord(waitMinutes(m), lang()) : m.state === "working" && !bot ? elapsed : agoStr(m.age, lang());
      state.textContent = [stateWord(m), when].filter(Boolean).join(" · ");
    }
    q("#app-terminal-toggle").hidden = !live;
    q("#app-terminal-toggle").textContent = T(terminal ? "app_conversation" : "app_terminal");
    // R128: Bot にはターミナルが無い＝「ターミナルを前へ」を出さない（押すと失敗の文で終わっていた）
    q("#app-jump").hidden = !m || Boolean(m.external) || bot || DEMO;
    q("#app-input").disabled = !m || DEMO || sending;
    q("#app-send").disabled = !m || DEMO || sending;
    q("#app-input").placeholder = T(bot ? (m.ask ? "app_input_bot_answer_ph" : "app_input_bot_ph") : "app_input_ph");
    q("#app-delivery").textContent = m ? T(bot ? "app_delivery_bot" : m.state === "working" ? "app_delivery_break" : m.listening === false ? "app_delivery_terminal" : "app_delivery_ready") : T("app_empty");
    q("#app-activity-scroll").hidden = terminal && Boolean(live);
    q("#app-terminal").hidden = !terminal || !live;
    if (terminal && live) termUi.show(live.id, { host: q("#app-terminal"), focus: false });
    else { terminal = false; termUi?.hide(); }
  }

  function paintDetails() {
    const m = member();
    const running = activityResult?.blocks.find((b) => b.type === "run");
    q("#app-current").textContent = m ? running?.step?.ds || line(m) : T("app_empty");
    const rows = m ? feed.page(m.session).rows : [];
    const todoRow = [...rows].reverse().find((r) => r.k === "todo" && Array.isArray(r.i));
    const items = todoRow ? todoRow.i.filter(Array.isArray).map(([state, text]) => ({ state, text }))
      : [
        ...(m?.work?.done || []).map((text) => ({ state: "c", text })),
        ...(m?.work?.now || []).map((text) => ({ state: "i", text })),
        ...(m?.work?.next || []).map((text) => ({ state: "p", text })),
      ];
    const counts = !todoRow && m?.work?.counts;
    const done = counts ? Math.max(0, Number(counts.completed) || 0) : items.filter((i) => i.state === "c").length;
    const total = counts ? done + Math.max(0, Number(counts.pending) || 0) + Math.max(0, Number(counts.in_progress) || 0) : items.length;
    q("#app-todo-count").textContent = total ? T("app_todo_progress", done, total) : "";
    q("#app-todo-progress").hidden = !total;
    q("#app-todo-progress i").style.width = `${total ? 100 * done / total : 0}%`;
    q("#app-todos").replaceChildren(...(items.length ? items.map((item) => {
      const row = el("div", `app-todo${item.state === "c" ? " is-done" : item.state === "i" ? " is-current" : ""}`);
      const mark = el("span", "", item.state === "c" ? "✓" : item.state === "i" ? "●" : "○"); mark.setAttribute("aria-hidden", "true");
      row.append(mark, el("span", "", String(item.text))); return row;
    }) : [el("p", "app-muted", T("app_no_todos"))]));
    const files = activityResult?.summary?.files || [];
    q("#app-file-count").textContent = T("app_files", files.length);
    const added = files.reduce((sum, f) => sum + (Number(f.add) || 0) + (Number(f.wl) || 0), 0);
    const removed = files.reduce((sum, f) => sum + (Number(f.rem) || 0), 0);
    q("#app-file-delta").textContent = files.length ? `+${added} −${removed}` : T("app_no_changes");
    q("#app-files").hidden = !files.length;
    q("#app-file-list").replaceChildren(...files.map((f) => el("p", "app-filename", f.f)));
    const env = q("#app-environment"); env.replaceChildren();
    const folder = String(m?.cwd || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop();
    for (const [key, text] of [["app_folder", folder], ["app_permissions", m ? permissionWord(m) : ""], ["app_branch", m?.branch || m?.gitBranch]]) {
      env.append(el("dt", "", T(key)), el("dd", "", text || T("app_unknown")));
    }
  }

  function render() {
    if (!built || disposed) return;
    tray.render(getWorld());
    paintLists(); paintActivity();
    office.update(getWorld(), selected);
  }

  function select(sid) {
    if (!all.some((a) => a.session === sid)) return false;
    if (selected) drafts.set(selected, q("#app-input").value);
    selected = sid; terminal = false; activityResult = null;
    q("#app-input").value = drafts.get(sid) || "";
    resizeInput();
    // 相手を替えると前の端末へキー入力が残らないようにフォーカスを外す。
    if (q("#app-terminal").contains(document.activeElement)) document.activeElement.blur();
    closeDrawers(); render(); feed.refresh(member());
    q("#app-activity-scroll").scrollTop = q("#app-activity-scroll").scrollHeight;
    return true;
  }

  function apply(data) {
    if (!data || disposed) return;
    officeData = data; built = buildWorld(data);
    built.muted = mutedSessions(data);   // R128 #4: 消音の Bot（画面の通知を出さない＝tray）
    if (built.lang !== lang()) { setLang(built.lang); strings(); }
    const raw = [...(data.employees || []), ...(data.roster || []).flatMap((p) => [p, ...(p.sessions || [])])];
    board = departmentBoard(data, built, T);
    for (const group of board) for (const m of group.sessions) {
      const source = raw.filter((r) => r.session === m.session);
      m.lastSaid = source.find((r) => r.lastSaid)?.lastSaid || "";
      m.cwd = source.find((r) => r.cwd)?.cwd || group.key;
      m.branch = source.find((r) => r.branch || r.gitBranch)?.branch || source.find((r) => r.gitBranch)?.gitBranch || "";
      m.projectName = group.name;
    }
    all = board.flatMap((group) => group.sessions);
    const bySession = new Map(all.map((m) => [m.session, m]));
    built.agents = built.agents.map((a) => ({ ...a, ...(bySession.get(a.session) || {}) }));
    if (!all.some((a) => a.session === selected)) {
      if (selected) drafts.set(selected, q("#app-input").value);
      selected = all.find((a) => a.attention)?.session || all[0]?.session || null;
      q("#app-input").value = drafts.get(selected) || "";
      activityResult = null; terminal = false;
    }
    delivery.update(getWorld()); session?.markData(); gauges?.start();
    render(); if (member()) feed.refresh(member());
    session?.applyHashOnce();
  }

  termUi = initTerm({ host: q("#app-terminal"), frozen, disabled: DEMO, fontSize: 14,
    focusedId: () => liveTerm()?.id || null,
    onChange: () => { if (built) paintHeader(); }, onInputError: () => delivery.showToast(T("cp_term_input_err")) });
  q("#app-terminal-toggle").addEventListener("click", () => {
    if (!liveTerm()) return;
    terminal = !terminal; paintHeader();
    if (terminal) termUi.show(liveTerm().id, { host: q("#app-terminal"), focus: true });
    else { if (q("#app-terminal").contains(document.activeElement)) document.activeElement.blur(); paintActivity(); }
  });
  q("#app-jump").addEventListener("click", () => { const m = member(); if (m) sheet.jumpTerminal(m.session, m.name); });

  const resizeInput = () => { const input = q("#app-input"); input.style.height = "auto"; input.style.height = `${Math.min(144, Math.max(28, input.scrollHeight))}px`; };
  q("#app-input").addEventListener("input", () => {
    resizeInput();
    if (!selected) return;
    const text = q("#app-input").value;
    drafts.set(selected, text);
    if (!text.trim()) draftAsk.delete(selected);
    else if (!draftAsk.has(selected)) draftAsk.set(selected, member()?.ask?.askId || "");
  });
  q("#app-input").addEventListener("compositionstart", () => { composing = true; });
  q("#app-input").addEventListener("compositionend", () => { composing = false; });
  q("#app-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !composing && event.keyCode !== 229) {
      event.preventDefault(); q("#app-compose").requestSubmit();
    }
  });
  q("#app-compose").addEventListener("submit", async (event) => {
    event.preventDefault();
    const m = member(), input = q("#app-input"), text = input.value.trim();
    if (!m || !text || sending || DEMO || composing) return;
    const sid = m.session, bound = draftAsk.has(sid) ? draftAsk.get(sid) : m.ask?.askId || "";
    if (m.ask && (m.ask.askId || "") !== bound) {
      // 書き始めた後で要求が替わった＝送らずに知らせる。いまの要求を見せた後なので、次の送信はいまの要求へ縛り直す
      // （send も同じ照合を持つ＝二重の門）。
      draftAsk.set(sid, m.ask.askId || ""); delivery.showToast(T("ask_changed"), false); render(); return;
    }
    sending = true; input.disabled = true; q("#app-send").disabled = true;
    const delivered = await delivery.send(sid, m.name, text, "", bound);
    sending = false;
    if (delivered) {
      drafts.delete(sid); draftAsk.delete(sid);
      if (selected === sid && input.value.trim() === text) { input.value = ""; resizeInput(); }
      // R128: Bot の記録は人の言葉で 1 行増えるが、Bot の行（状態・未読の印）は変わらないことがある＝送れたらすぐ取り直す
      if (isBot(m) && selected === sid) reloadBot(sid);
    }
    paintHeader();
    if (selected === sid) input.focus({ preventScroll: true });
  });
  q("#app-search").addEventListener("input", paintLists);

  const paintDrawers = () => {
    const narrow = innerWidth < 1000, mobile = innerWidth < 720;
    const right = narrow ? shell.classList.contains("details-open") : !shell.classList.contains("details-closed");
    const left = !mobile || shell.classList.contains("sidebar-open");
    q("#app-details-toggle").setAttribute("aria-expanded", String(right));
    q("#app-menu").setAttribute("aria-expanded", String(left));
    q("#app-details").inert = !right; q("#app-sidebar").inert = !left;
    q("#app-backdrop").hidden = !(narrow && right || mobile && left);
    office.resize();
  };
  function closeDrawers() { shell.classList.remove("details-open", "sidebar-open"); paintDrawers(); }
  q("#app-details-toggle").addEventListener("click", () => {
    shell.classList.toggle(innerWidth < 1000 ? "details-open" : "details-closed"); shell.classList.remove("sidebar-open"); paintDrawers();
  });
  q("#app-menu").addEventListener("click", () => { shell.classList.toggle("sidebar-open"); shell.classList.remove("details-open"); paintDrawers(); });
  for (const id of ["app-details-close", "app-sidebar-close", "app-backdrop"]) q(`#${id}`).addEventListener("click", closeDrawers);
  const onKey = (event) => {
    if (event.key === "Escape") { closeDrawers(); closeNew(); }
    // 非表示のトレイへ数字キーを渡さない。中央に見えている相手への回答はカードのボタンで行う。
    if (!event.target.closest?.("input, textarea, select, [contenteditable=true]")
        && (event.key === "j" || event.key === "J" || event.key === "k" || event.key === "K" || /^[1-9]$/.test(event.key)))
      event.stopImmediatePropagation();
  };
  window.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", paintDrawers);
  const newMenu = q("#app-new-menu");
  const closeNew = () => { newMenu.hidden = true; q("#app-new").setAttribute("aria-expanded", "false"); };
  q("#app-new").addEventListener("click", () => {
    newMenu.hidden = !newMenu.hidden; q("#app-new").setAttribute("aria-expanded", String(!newMenu.hidden));
    if (!newMenu.hidden) newMenu.querySelector("button:not([hidden])")?.focus();
  });
  shell.addEventListener("click", (event) => { if (!event.target.closest(".app-new-wrap") || event.target.closest('[role="menuitem"]')) closeNew(); });
  q("#app-usage-open").addEventListener("click", () => q("#btn-res").click());
  gauges = initGauges({ ...common, lang, demo: DEMO, onPins: (pins) => {
    // 既存ゲージが取得した7日枠を優先する。該当する枠が無ければ数値を作らない。
    const pin = pins.find((p) => p.window === T("g_win_week")) || pins.find((p) => Number.isFinite(p.pct));
    const pct = pin && Number.isFinite(pin.pct) ? Math.min(100, Math.max(0, pin.pct)) : null;
    q("#app-usage-value").textContent = pct === null ? T("app_usage_unknown") : `${Math.round(pct)}%`;
    q("#app-usage-bar").hidden = pct === null;
    q("#app-usage-bar").setAttribute("aria-valuenow", String(pct || 0));
    q("#app-usage-bar i").style.width = `${pct || 0}%`;
  } });
  q("#app-usage-value").textContent = T("app_usage_unknown"); q("#app-usage-bar").hidden = true;
  session = initSession({ root, shell, T, DEMO, apply, tray, getWorld, lang, onOffline: () => {} });
  let tick = -1;
  const stopLoop = loop((t) => {
    if (Math.floor(t) === tick || !built) return;
    tick = Math.floor(t); paintHeader();
    for (const n of q("#app-activity").querySelectorAll("[data-since]")) n.textContent = formatDuration(epochNow() - Number(n.dataset.since));
    if (member() && !terminal) feed.refresh(member());
  });
  paintDrawers(); paintActivity();
  const uninstall = installProbe({ style: STYLE, dumpWorld: () => summarizeWorld(officeData), inject: apply, t: now,
    isReady: () => Boolean(built && office.scene.ready()), stats: () => office.scene.stats(), pollMs: () => session.intervalMs(),
    debug: { world: () => built, selected: () => selected, select, appRoomMode: () => office.mode(),
      agentPoint: (id) => office.scene.projectAgent(id),
      step: (seconds) => { setTime(seconds); if (built) office.update(getWorld(), selected); paintHeader(); } } });
  // R129 今日の 1 枚: 部屋のドット絵と今日の数を 1 枚の PNG に（名前は入らない）。見せてから保存する。デモは記録が無いので出さない
  const todayButton = q("#app-today");
  todayButton.hidden = DEMO;
  todayButton.addEventListener("click", openTodayCard);
  async function openTodayCard() {
    if (todayButton.disabled) return;
    todayButton.disabled = true;
    todayButton.textContent = T("today_making");
    try {
      const summary = todaySummary(await getDigest(null, null, 0), { bots: all.filter((a) => isBot(a)).length });
      const canvas = drawTodayCard(document.createElement("canvas"), { summary, room: office.snapshot(), T, lang: lang(), tokensFrom: root });
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("PNG");
      showTodayCard(blob, todayFileName(summary.day));
    } catch (err) {
      delivery.showToast(T("today_failed", err?.message || "?"), false);
    } finally {
      todayButton.disabled = false;
      todayButton.textContent = T("today_btn");
    }
  }
  function showTodayCard(blob, name) {
    closeTodayCard();
    const url = URL.createObjectURL(blob);
    const wrap = el("div", "app-today-card");
    wrap.id = "app-today-card";
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-modal", "true");
    wrap.setAttribute("aria-label", T("today_btn"));
    const panel = el("div", "app-today-panel");
    const img = el("img", "app-today-img");
    img.src = url;
    img.alt = T("today_title");
    const acts = el("div", "app-today-acts");
    const save = el("a", "app-today-save", T("today_save"));
    save.href = url;
    save.download = name;
    const close = el("button", "app-today-close", T("today_close"));
    close.type = "button";
    close.addEventListener("click", closeTodayCard);
    wrap.addEventListener("click", (event) => { if (event.target === wrap) closeTodayCard(); });
    acts.append(save, close);
    panel.append(img, el("p", "app-today-note", T("today_note")), acts);
    wrap.append(panel);
    wrap._url = url;
    shell.append(wrap);
    window.addEventListener("keydown", onTodayKey, true);
    save.focus();
  }
  function onTodayKey(event) {
    if (event.key !== "Escape" || !q("#app-today-card")) return;
    event.stopPropagation();
    closeTodayCard();
  }
  function closeTodayCard() {
    const open = q("#app-today-card");
    if (!open) return;
    URL.revokeObjectURL(open._url);
    open.remove();
    window.removeEventListener("keydown", onTodayKey, true);
    todayButton.focus();
  }

  if (DEMO) await session.loadDemo();
  return () => {
    closeTodayCard();
    disposed = true; feed.dispose(); hold.dispose(); for (const view of views.values()) view.dispose();
    session.dispose(); gauges.dispose(); hire.dispose(); delivery.dispose(); tray.dispose();
    termUi.dispose(); office.dispose(); stopLoop(); uninstall();
    window.removeEventListener("keydown", onKey, true); window.removeEventListener("resize", paintDrawers);
  };
}
