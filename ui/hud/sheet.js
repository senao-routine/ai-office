// 詳細シート・宛先切替・compose・常設定型ボード。
import { activityGloss, agoStr, isMuted, tidyActivity } from "/ui/core/world.js";
import { focusTerminal } from "/ui/platform/api.js";
import { setSound, soundOn } from "/ui/platform/sound.js";

/** ctx: shell, T, lang, DEMO, attnKeyFor, getWorld(), render(),
 *  focusOn(id), focusOff(), delivery, dialog, getTemplates(), openTemplateEditor() */
export function init({ shell, T, lang, DEMO, attnKeyFor, getWorld, render,
  focusOn, focusOff, delivery: { send, allow, showToast },
  dialog: { loadDialog }, getTemplates, openTemplateEditor, paintGrowth = () => {} }) {
  let composeTarget = null;          // {session, name, id}
  const sheetEl = shell.querySelector("#sheet");
  const composeInput = shell.querySelector("#composeinput");
  const sEl = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const replyChoice = (button, text) => {
    const wrap = sEl("div", "reply-choice");
    const menu = sEl("details", "reply-menu");
    const summary = sEl("summary", "", "⋯");
    summary.setAttribute("aria-label", T("reply_options"));
    const save = sEl("button", "reply-save", T("reply_save")); save.type = "button";
    save.addEventListener("click", () => { menu.open = false; openTemplateEditor(text); });
    menu.append(summary, save); wrap.append(button, menu);
    return wrap;
  };
  let typeTimer = 0;
  const typewrite = (el2, text) => {
    clearInterval(typeTimer);
    el2.textContent = "";
    let i = 0;
    typeTimer = setInterval(() => {
      if (i >= text.length) { clearInterval(typeTimer); return; }
      el2.textContent += text[i];
      i += 1;
    }, 26);
  };
  /** ターミナルの生ログではなく「人間が読む1文」へ変換する（ユーザーFBの核）。 */
  const humanSummary = (a) => {
    const g = activityGloss(a, lang());
    const doing = g ? T("hs_doing", g) : "";
    if (a.question) return `${doing}${T("hs_question")}`;
    if (a.attention) return `${doing}${T("hs_approval", a.approvalMin)}`;
    if (a.zone === "meeting") return `${doing}${T("hs_meeting", a.minions)}`;
    if (a.zone === "lounge") return T("hs_lounge");
    if (a.zone === "external") return T("hs_external");
    if (a.state === "working") return doing || T("hs_working");
    return T("hs_waiting");
  };
  /** R86-D: 受信待機が切れている相手を選んだとき、シート先頭に正直な但し書きを出す。
   *  投函はブロックしない（inboxに残り、そのセッションが次に動いた瞬間に届く）。 */
  const listenNote = (a) => (!isMuted(a) ? null : sEl("div", "dlgnote listenoff",
    `📴 ${T("listen_off")}`));
  // 宛先表示（crew>1 のときだけ・内訳行のクリックで切替）
  const targetEl = () => shell.querySelector("#sheettarget");
  const paintTarget = (agent) => {
    const el2 = targetEl();
    if (!agent || agent.crew <= 1) { el2.hidden = true; return; }
    const idx = (agent.sessions || []).findIndex((s2) => s2.session === composeTarget?.session);
    el2.textContent = (composeTarget?.session === agent.session || idx < 0)
      ? T("target_rep") : T("target_n", idx + 1);
    el2.hidden = false;
  };
  const openCompose = (agent) => {
    composeTarget = { session: agent.session, name: agent.name, id: agent.id };
    let dlgListEl = null;                     // R86-B: crewrow の宛先切替から参照する
    let dlgHeadEl = null;                     // R86-C: previousSibling への暗黙依存をやめる
    shell.querySelector("#sheetname").textContent =
      agent.crew > 1 ? `${agent.name} ×${agent.crew}` : agent.name;
    typewrite(shell.querySelector("#sheetact"), humanSummary(agent));
    const body = shell.querySelector("#sheetbody");
    body.replaceChildren();
    paintGrowth(agent, body, shell.querySelector("#sheetname"));
    const note = listenNote(agent);
    if (note) body.append(note);
    if (agent.attention) {
      // 質問文の表示は本文の先頭に。回答ボタンは quickboard（compose直上の常設ボード）へ
      // 集約＝「返信はここ」の一箇所感（R54ユーザーFB）
      const q = sEl("div", "sheetq");
      q.append(sEl("b", "", agent.question
        ? `❓ ${agent.question}`
        : `❗ ${T("approval_min", agent.approvalMin)}` +
          (agent.stuckTool ? `\n${T("attn_target", tidyActivity(agent.stuckTool, 60))}` : "")));
      // R86-H: 推測ではなく事実（フックが掲示している「いま聞かれていること」）が
      // あるときは明示する＝ここから答えれば本当に届く、の根拠を見せる
      if (agent.ask) {
        q.append(sEl("div", "askfact", agent.ask.kind === "permission"
          ? T("ask_perm", agent.ask.tool || "", agent.ask.title || "")
          : T("ask_live")));
      }
      body.append(q);
    }
    // 💬 セッションのやり取り（R86-B／R86-C で本文の**先頭**へ移動）: 実会話をオンデマンド取得
    // （office_json非搭載＝中継へ流れる経路が構造的に無い）。承認判断の主材料なので、
    // 📋いまの仕事・🕑最近の動きより上に置く（末尾だとスクロールしないと到達できず、
    // 「もっと見る」も本文の折り返しの下に隠れて押せない＝実測 y=833 で不可視だった）。
    // DEMO/外部/oc- はサーバー無し・別Macなので節ごと出さない（demo golden 不変の条件）。
    if (!DEMO && !agent.external && !String(agent.session || "").startsWith("oc-")) {
      const dsec = sEl("div", "sheetsec");
      const dhead = sEl("b", "sheetsec-head dlghead", T("dialog_head"));
      const dlist = sEl("div", "dlglist");
      dsec.append(dhead, dlist);
      body.append(dsec);
      dlgListEl = dlist;
      dlgHeadEl = dhead;
      loadDialog(composeTarget?.session || agent.session, dlist, dhead);
    }
    // ×N集約の内訳: 非代表セッションへの宛先切替（配達経路・APIは無改変＝sessionの差替だけ）
    if (agent.crew > 1 && (agent.sessions || []).length > 1) {
      body.append(sEl("b", "sheetsub", T("crew_head", agent.sessions.length)));
      const crewWrap = sEl("div", "crewlist");
      agent.sessions.slice(0, 8).forEach((s2, i) => {
        const row = sEl("button",
          `crewrow${s2.session === agent.session ? " sel" : ""}`);
        row.type = "button";
        row.dataset.session = s2.session;
        const st = s2.state === "working" ? "🟢" : s2.state === "resting" ? "💤" : "🟡";
        const marks = `${s2.attention ? "❗" : ""}${s2.pending ? "📨" : ""}`;
        row.append(
          sEl("span", "", `${st} ${T("crew_n", i + 1)}${s2.session === agent.session ? T("crew_rep") : ""}`),
          sEl("span", "crewage", agoStr(s2.age || 0, lang())),
          sEl("span", "crewmark", marks));
        row.addEventListener("click", () => {
          composeTarget = { session: s2.session, name: agent.name, id: agent.id };
          for (const r of crewWrap.children) r.classList.remove("sel");
          row.classList.add("sel");
          paintTarget(agent);
          // R86-B: 会話ビューアも切替先セッションのやり取りへ追随
          if (dlgListEl) loadDialog(s2.session, dlgListEl, dlgHeadEl);
          composeInput.focus();
        });
        crewWrap.append(row);
      });
      body.append(crewWrap);
    }
    // 📋 いまの仕事: ラベル列＋内容列で整列（フラットな sheetline 羅列をやめ読める形に）
    const work = agent.work || {};
    const workRows = [["now", T("work_now"), true], ["next", T("work_next"), false],
                      ["done", T("work_done"), false]]
      .flatMap(([key, label, strong]) => (work[key] || []).slice(0, 3)
        .map((item, i) => ({ label, item, strong: strong && i === 0 })));
    if (workRows.length) {
      const sec = sEl("div", "sheetsec");
      sec.append(sEl("b", "sheetsec-head", T("work_head")));
      const grid = sEl("div", "workgrid");
      for (const r of workRows) {
        grid.append(sEl("i", "wl", r.label),
          sEl("span", r.strong ? "wv strong" : "wv", tidyActivity(r.item, 80)));
      }
      sec.append(grid);
      body.append(sec);
    }
    // 🕑 最近の動き: 件数バッジ＋独立スクロール・💬発言は色分け
    if ((agent.feed || []).length) {
      // R67: サーバーの feed は newest-first。slice(-8) は古い8件＝最新2行を
      // 取りこぼしていた実測バグ。先頭8件（新しい順のまま上から）へ修正
      const feed = agent.feed.slice(0, 8);
      const sec = sEl("div", "sheetsec");
      const head = sEl("b", "sheetsec-head", T("recent_moves"));
      head.append(sEl("i", "seccount", String(feed.length)));
      sec.append(head);
      const listEl = sEl("div", "feedlist");
      for (const line of feed) {
        const isSay = line.trimStart().startsWith("💬");
        listEl.append(sEl("div", `sheetline feed feedline${isSay ? " say" : ""}`,
          tidyActivity(line, 90)));
      }
      sec.append(listEl);
      body.append(sec);
    }
    // ⚡ 定型ボード: 回答ボタン＋よく押す定型を compose 直上に常設（本文スクロールでも動かない）
    const dock = shell.querySelector("#quickdock");
    dock.replaceChildren();
    const board = sEl("div", "quickboard");
    board.append(sEl("b", "qb-head", T("qb_head")));
    if (agent.attention) {
      const answers = sEl("div", "qb-answers");
      const opts = (agent.questionOptions || []).length
        ? agent.questionOptions.slice(0, 4).map((o) => ({
            label: o.label ?? o, text: T("opt_text", o.label ?? o) }))
        : [{ label: T("opt_approve"), text: T("opt_approve_text") },
           { label: T("opt_pause"), text: T("opt_pause_text") },
           { label: T("opt_report"), text: T("opt_report_text") }];   // R80: スマホと同じ3本
      if (agent.ask?.kind === "permission") {
        // 実行の許可は「はい」という**文章**では通らない。許可そのものを送るボタンを出す。
        // （この経路＝loopback+CSRF＝Macの前の人間だけが押せる。スマホには出さない）
        const yes = sEl("button", "sheetopt allowbtn", T("opt_allow"));
        yes.type = "button";
        yes.addEventListener("click", async () => {
          if (await allow(agent)) closeCompose();
        });
        answers.append(yes);
        opts.length = 0;
        opts.push({ label: T("opt_refuse"), text: T("opt_refuse_text") });
      }
      for (const o of opts) {
        const b = sEl("button", "sheetopt", o.label);
        b.type = "button";
        b.addEventListener("click", async () => {
          // R67: 成功時のみクローズ（失敗トーストの裏でシートが消える混乱を防ぐ）
          if (await send(agent.session, agent.name, o.text, attnKeyFor(agent))) closeCompose();
        });
        answers.append(replyChoice(b, o.text));
      }
      board.append(answers);
    }
    const quick = sEl("div", "sheetquick");
    const QUICK_ICONS = ["▶", "👍", "🧪", "⏸"];
    T("quick").forEach((q, i) => {
      const b = sEl("button", "qchip");
      b.type = "button";
      b.title = q;                       // 狭幅で省略された全文はツールチップで
      b.append(sEl("i", "qicon", QUICK_ICONS[i] || "・"), sEl("span", "", q));
      // 内訳で宛先を切り替えた後は QUICK もその宛先へ（❗回答ボタンは代表=❗保持者のまま）
      b.addEventListener("click", async () => {
        if (await send(composeTarget?.session || agent.session, agent.name, q)) closeCompose();
      });
      quick.append(replyChoice(b, q));
    });
    // R82: ユーザー定義の定型文（保存はMac・スマホへは office_json.templates で同期）
    for (const tp of getTemplates()) {
      const b = sEl("button", "qchip tplchip");
      b.type = "button";
      b.title = tp.text;
      b.append(sEl("i", "qicon", "✳"), sEl("span", "", tp.label));
      b.addEventListener("click", async () => {
        if (await send(composeTarget?.session || agent.session, agent.name, tp.text)) closeCompose();
      });
      quick.append(replyChoice(b, tp.text));
    }
    board.append(quick);
    dock.append(board);
    paintTarget(agent);
    sheetEl.hidden = false;
    shell.classList.add("sheet-open");  // R80-A15: トレイを詰めて回答ボタンを生かす
    focusOn(agent.id);          // R70: 選んだロボへカメラが寄る
    // R67: 開きだけ .show 2段階でフェードイン（hidden の即時性は維持＝スモーク互換・
    // frozen は .no-anim で transition:none＝golden 非干渉）
    requestAnimationFrame(() => sheetEl.classList.add("show"));
    composeInput.focus();
    if (getWorld()) render();            // 選択ハイライトを反映
  };
  const closeCompose = () => {
    focusOff();                 // R70: 全景へ戻る
    composeTarget = null;
    sheetEl.classList.remove("show");
    sheetEl.hidden = true;
    shell.classList.remove("sheet-open");
    composeInput.value = "";
    shell.querySelector("#quickdock").replaceChildren();
    clearInterval(typeTimer);
    if (getWorld()) render();
  };
  const sndBtn = shell.querySelector("#sheetsnd");
  const paintSnd = () => { sndBtn.textContent = soundOn() ? "🔈" : "🔇"; };
  paintSnd();
  sndBtn.addEventListener("click", () => { setSound(!soundOn()); paintSnd(); });
  // R53: 🖥 実ターミナルへジャンプ（宛先切替中はそのセッションのターミナルへ）
  const jumpTerminal = async (session, name) => {
    if (DEMO) { showToast(T("demo_no_send")); return; }
    if (!session || session.startsWith("oc-")) { showToast(T("term_none"), false); return; }
    try {
      const r = await focusTerminal(session);
      showToast(T("term_ok", r.app || "Terminal"));
    } catch (err) {
      showToast(`${name ? name + ": " : ""}${err.message}`, false);
    }
  };
  shell.querySelector("#sheetterm").addEventListener("click", () => {
    if (composeTarget) jumpTerminal(composeTarget.session, composeTarget.name);
  });
  shell.querySelector("#sheetclose").addEventListener("click", closeCompose);
  composeInput.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && composeInput.value.trim() && composeTarget) {
      // R67: 送信成功時のみクローズ＝失敗しても本文が残る（従来は入力全喪失の実バグ）
      if (await send(composeTarget.session, composeTarget.name, composeInput.value.trim())) {
        closeCompose();
      }
    } else if (e.key === "Escape") {
      closeCompose();
    }
  });
  return {
    openCompose, closeCompose, jumpTerminal,
    refreshGrowth: () => {
      const agent = getWorld()?.agents.find((a) => a.id === composeTarget?.id);
      if (agent && !sheetEl.hidden) paintGrowth(agent, shell.querySelector("#sheetbody"),
        shell.querySelector("#sheetname"));
    },
    selectedId: () => composeTarget?.id ?? null,
    dispose: () => clearInterval(typeTimer),
  };
}
