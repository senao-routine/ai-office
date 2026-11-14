// 詳細シート・宛先切替・compose・常設定型ボード。
import { activityGloss, agoStr, isMuted, tidyActivity } from "/ui/core/world.js";
import { isBot } from "/ui/core/kind.js";
import { focusTerminal } from "/ui/platform/api.js";
import { setSound, soundOn } from "/ui/platform/sound.js";
import { deliveryChip } from "/ui/hud/delivery.js";
import { answerActs } from "/ui/hud/tray.js";
import { attnLine } from "/ui/hud/board.js";
import { plainText } from "/ui/hud/plain.js";

/** ctx: shell, T, lang, DEMO, attnKeyFor, getWorld(), render(),
 *  focusOn(id), focusOff(), delivery, dialog, getTemplates(), openTemplateEditor() */
export function init({ shell, T, lang, DEMO, attnKeyFor, getWorld, render,
  focusOn, focusOff, delivery: { send, allow, showToast, isStalled },
  dialog: { loadDialog }, getTemplates, openTemplateEditor, paintGrowth = () => {},
  openCustomize = () => {} }) {
  let composeTarget = null;          // {session, name, id}
  const sheetEl = shell.querySelector("#sheet");
  const composeInput = shell.querySelector("#composeinput");
  // b4-mac: 絵文字なしの T（cockpit・T.plain）では、シートの中の直書きの印（🟢 ❓ ❗ 📨）や作業の一言（🧪 …）からも落とす
  const sEl = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = T.plain ? plainText(text) : text;
    return n;
  };
  const stateOptions = (agent) => ({ T, agent,
    offline: !!shell.closest(".offline"), stalled: isStalled(agent.session) });
  /** 非代表セッションの内訳（_session_brief）: 表示用の名前は代表から、配達に関わる項目（listening/ask/pending/attention/state）は
   *  内訳自身の値だけを使う（代表の listening を引き継ぐと更新後に「届く」へ化ける・別モデルレビュー）。listening 未搬送の旧サーバーでは脅さない。 */
  const withDelivery = (agent, brief) => (brief === agent ? agent
    : { ...agent, ...brief, listening: brief.listening, ask: brief.ask || null,
      pending: Boolean(brief.pending), attention: Boolean(brief.attention) });
  /**
   * 入力欄へフォーカスする。**ページ（文書のスクロール）は動かさない**: 1 列のコックピット（ページが縦に流れる形）では
   * シートがページの丈いっぱいに伸び、入力欄はその底＝何画面も下にある。既定の focus() はそこまでページを送り、
   * 質問文（シートの先頭）が画面の外へ出た（台帳の行から開くと、何を聞かれているかが見えない）。
   * シート自身の中のスクロールだけは今までどおり入力欄まで寄せる（縦だけ・シートが溢れているときだけ）。
   */
  const focusCompose = () => {
    composeInput.focus({ preventScroll: true });
    const box = sheetEl.getBoundingClientRect();
    const r = composeInput.getBoundingClientRect();
    if (r.bottom > box.bottom) sheetEl.scrollTop += r.bottom - box.bottom;
    else if (r.top < box.top) sheetEl.scrollTop -= box.top - r.top;
  };
  /**
   * 開いたシートの見出し（名前）から質問文までを画面の中へ寄せる。**縦だけ・ページだけ**を動かす（scrollIntoView は
   * overflow:hidden の祖先まで・横にも動かす＝開く途中の translateX(100%) のシートへ横に送って画面が崩れる）。
   * 見えていれば何もしない（3 カラム・iso はページが流れない＝動かない）。見出しから質問文までが画面に入りきらない
   * （長い質問）ときは質問文の先頭を画面の上へ（何を聞かれているかを優先する）。
   */
  const revealHead = () => {
    const head = sheetEl.querySelector(".sheethead");
    const q = sheetEl.querySelector(".sheetq") || head;
    if (!head || !q) return;
    const top = head.getBoundingClientRect().top;
    const qr = q.getBoundingClientRect();
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const margin = 8;
    if (top >= 0 && qr.bottom <= vh) return;
    window.scrollBy(0, (qr.bottom - top <= vh - 2 * margin ? top : qr.top) - margin);
  };
  const paintActivity = (agent) => {
    shell.querySelector("#sheetact").replaceChildren(
      sEl("span", "sheetgloss", activityGloss(agent, lang())),
      deliveryChip(stateOptions(agent)));
  };
  /** R86-D: 受信待機が切れている相手を選んだとき、シート先頭に正直な但し書きを出す。
   *  投函はブロックしない（inboxに残り、そのセッションが次に動いた瞬間に届く）。 */
  const listenNote = (a) => {
    if (!isMuted(a)) return null;
    const note = sEl("div", "dlgnote listenoff");
    note.append(deliveryChip({ ...stateOptions(a), hint: true }));
    return note;
  };
  const paintListenNote = (agent) => {
    const body = shell.querySelector("#sheetbody");
    body.querySelector(".listenoff")?.remove();
    const note = listenNote(agent);
    if (note) body.prepend(note);
  };
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
    agent = getWorld()?.agents.find((a) => a.session === agent.session) || agent;
    // R100-0: 開いた時点の要求の番号（無ければ ""）＝押した瞬間に最古の要求へ決め直さない
    composeTarget = { session: agent.session, name: agent.name, id: agent.id, askId: agent.ask?.askId || "" };
    // R128: 出勤の口の Bot にはターミナルが無い＝「ターミナルへ移動」を出さない（押しても失敗の文で終わっていた）
    shell.querySelector("#sheetterm").hidden = isBot(agent);
    let dlgListEl = null;                     // R86-B: crewrow の宛先切替から参照する
    let dlgHeadEl = null;                     // R86-C: previousSibling への暗黙依存をやめる
    shell.querySelector("#sheetname").textContent =
      agent.crew > 1 ? `${agent.name} ×${agent.crew}` : agent.name;
    paintActivity(agent);
    const body = shell.querySelector("#sheetbody");
    body.replaceChildren();
    paintGrowth(agent, body, shell.querySelector("#sheetname"));
    const note = listenNote(agent);
    if (note) body.append(note);
    if (agent.attention) {
      // 質問文の表示は本文の先頭に。回答ボタンは quickboard（compose直上の常設ボード）へ
      // 集約＝「返信はここ」の一箇所感（R54ユーザーFB）
      const q = sEl("div", "sheetq");
      // b4-mac: 質問文の無い ❗ も種類を言う（許可待ち・計画の確認・止まっている?）＝トレイ・台帳と同じ語
      q.append(sEl("b", "", agent.question
        ? `❓ ${agent.question}`
        : `❗ ${attnLine(T, agent, getWorld()?.lang)}`));
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
    // R128: Bot（bot-）も会話（transcript）を持たない＝節を出さない（記録は中央の作業ビューに出る）
    if (!DEMO && !agent.external && !String(agent.session || "").startsWith("oc-") && !isBot(agent)) {
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
          composeTarget = { session: s2.session, name: agent.name, id: agent.id,
            askId: s2.session === agent.session ? (agent.ask?.askId || "") : undefined };
          for (const r of crewWrap.children) r.classList.remove("sel");
          row.classList.add("sel");
          paintTarget(agent);
          paintActivity(withDelivery(agent, s2));
          paintListenNote(withDelivery(agent, s2));
          // R86-B: 会話ビューアも切替先セッションのやり取りへ追随
          if (dlgListEl) loadDialog(s2.session, dlgListEl, dlgHeadEl);
          focusCompose();
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
    const boardHead = sEl("b", "qb-head", T("qb_head"));
    const edit = sEl("button", "qb-edit", T("qb_edit"));
    edit.type = "button";
    edit.addEventListener("click", () => openTemplateEditor());
    boardHead.append(edit);
    board.append(boardHead);
    if (agent.attention) {
      const answers = sEl("div", "qb-answers");
      // 答え方はトレイと同じ正本（answerActs）。許可待ち＝許可する／許可しない、計画（ExitPlanMode）＝承認／差し戻す。
      // 許可・承認は「はい」という**文章**では通らない（文章は deny）＝許可そのものを送るボタンを出す。
      // （この経路＝loopback+CSRF＝Macの前の人間だけが押せる。スマホには出さない）
      for (const o of answerActs(agent, T, 4)) {
        if (o.compose && !o.comment) continue;           // 「自由に書く」＝下の入力欄そのもの
        const b = sEl("button", o.allow ? "sheetopt allowbtn" : "sheetopt", o.label);
        b.type = "button";
        b.addEventListener("click", async () => {
          if (o.allow) { if (await allow(agent)) closeCompose(); return; }
          if (o.compose) { focusCompose(); return; }   // 差し戻す: 理由を書いて Enter（deny＋本文で届く）
          // R67: 成功時のみクローズ（失敗トーストの裏でシートが消える混乱を防ぐ）
          if (await send(agent.session, agent.name, o.text, attnKeyFor(agent), agent.ask?.askId || "")) closeCompose();
        });
        answers.append(b);
      }
      board.append(answers);
    }
    const quick = sEl("div", "sheetquick");
    const QUICK_ICONS = ["▶", "👍", "🧪", "⏸"];
    T("quick").forEach((q, i) => {
      const b = sEl("button", "qchip");
      b.type = "button";
      b.title = q;                       // 狭幅で省略された全文はツールチップで
      if (!T.plain) b.append(sEl("i", "qicon", QUICK_ICONS[i] || "・"));   // 絵文字なしの T（cockpit）は字形を出さない
      b.append(sEl("span", "", q));
      // 内訳で宛先を切り替えた後は QUICK もその宛先へ（❗回答ボタンは代表=❗保持者のまま）
      b.addEventListener("click", async () => {
        if (await send(composeTarget?.session || agent.session, agent.name, q, "", composeTarget?.askId)) closeCompose();
      });
      quick.append(b);
    });
    // R82: ユーザー定義の定型文（保存はMac・スマホへは office_json.templates で同期）
    for (const tp of getTemplates()) {
      const b = sEl("button", "qchip tplchip");
      b.type = "button";
      b.title = tp.text;
      b.append(sEl("i", "qicon", "✳"), sEl("span", "", tp.label));
      b.addEventListener("click", async () => {
        if (await send(composeTarget?.session || agent.session, agent.name, tp.text, "", composeTarget?.askId)) closeCompose();
      });
      quick.append(b);
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
    focusCompose();
    if (getWorld()) render();            // 選択ハイライトを反映
    revealHead();                        // 描き直しの後（トレイ・台帳の丈が替わってから）質問文を画面へ
  };
  const closeCompose = () => {
    focusOff();                 // R70: 全景へ戻る
    composeTarget = null;
    sheetEl.classList.remove("show");
    sheetEl.hidden = true;
    shell.classList.remove("sheet-open");
    composeInput.value = "";
    if (composeInput.tagName === "TEXTAREA") composeInput.style.height = "auto";
    shell.querySelector("#quickdock").replaceChildren();
    if (getWorld()) render();
  };
  const sndBtn = shell.querySelector("#sheetsnd");
  // b4-mac: 印（data-px）を持つ様式（cockpit）は字形でなく印を替える（🔈/🔇 の絵文字を出さない）。押された状態は aria-pressed
  const paintSnd = () => {
    if (sndBtn.hasAttribute("data-px")) {
      sndBtn.dataset.px = soundOn() ? "sound" : "bell-off";
      sndBtn.textContent = "";
      sndBtn.setAttribute("aria-pressed", String(soundOn()));
      sndBtn.setAttribute("aria-label", T("snd_title"));
    } else sndBtn.textContent = soundOn() ? "🔈" : "🔇";
  };
  paintSnd();
  sndBtn.addEventListener("click", () => { setSound(!soundOn()); paintSnd(); });
  // R53: 🖥 実ターミナルへジャンプ（宛先切替中はそのセッションのターミナルへ）
  const jumpTerminal = async (session, name) => {
    if (DEMO) { showToast(T("demo_no_send")); return; }
    // R128: Bot（bot-）はターミナルを持たない＝入口は出さない（ロボのダブルクリックなど残りの口も黙って何もしない＝外部の相手と同じ）
    if (isBot({ session })) return;
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
  // R91: 🎨 このアバターをカスタマイズ（名札クリックはここ＝会話へ来る。本人指摘の再構成）
  // ラベル（title/aria-label）は applyStaticStrings が言語切替のたびに貼り直す
  const archBtn = shell.querySelector("#sheetarch");
  archBtn.addEventListener("click", () => {
    const agent = getWorld()?.agents.find((a) => a.id === composeTarget?.id) || composeTarget;
    if (agent) openCustomize(agent);
  });
  // R91: ⤢ 会話を読むときだけ広げる（既定幅は3Dを潰さない範囲・選択は端末に残す）
  const WIDE_KEY = "aioffice.iso.sheetwide";
  const wideBtn = shell.querySelector("#sheetwide");
  const applyWide = (on) => {
    sheetEl.classList.toggle("wide", on);
    wideBtn.classList.toggle("on", on);
    wideBtn.setAttribute("aria-pressed", String(on));
  };
  let wide = false;
  try { wide = localStorage.getItem(WIDE_KEY) === "1"; } catch { /* Optional storage. */ }
  applyWide(wide);
  wideBtn.addEventListener("click", () => {
    wide = !wide;
    applyWide(wide);
    try { localStorage.setItem(WIDE_KEY, wide ? "1" : "0"); } catch { /* Optional storage. */ }
  });
  // R106-11: 複数行（textarea）対応。Enter=送信・Shift+Enter=改行・IME 確定の Enter（isComposing）は送らない
  // （cockpit は #composeinput がtextarea・iso/pixel は従来の input のままで Shift+Enter は単に無視される）。
  composeInput.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      // textarea の既定（改行を入れる）は本文が空でも止める＝input だった頃の no-op を維持
      // （先に preventDefault し、送信条件を満たす時だけ送る。レビュー指摘: 空のまま Enter で空行が積み重なる実バグ）
      e.preventDefault();
      if (composeInput.value.trim() && composeTarget) {
        // R67: 送信成功時のみクローズ＝失敗しても本文が残る（従来は入力全喪失の実バグ）
        if (await send(composeTarget.session, composeTarget.name, composeInput.value.trim(), "",
          composeTarget.askId)) {
          closeCompose();
        }
      }
    } else if (e.key === "Escape") {
      closeCompose();
    }
  });
  // 自動で伸びる textarea（cockpit だけ・iso/pixel の input では何もしない）。シート自体のスクロールは動かさない。
  if (composeInput.tagName === "TEXTAREA") {
    const autoGrow = () => {
      composeInput.style.height = "auto";
      composeInput.style.height = `${composeInput.scrollHeight}px`;
    };
    composeInput.addEventListener("input", autoGrow);
    autoGrow();
  }
  return {
    openCompose, closeCompose, jumpTerminal, paintSound: paintSnd,
    refreshGrowth: () => {
      const agent = getWorld()?.agents.find((a) => a.id === composeTarget?.id);
      if (agent && !sheetEl.hidden) {
        paintGrowth(agent, shell.querySelector("#sheetbody"), shell.querySelector("#sheetname"));
        const target = agent.session === composeTarget.session ? agent
          : agent.sessions?.find((s) => s.session === composeTarget.session) || agent;
        paintActivity(withDelivery(agent, target));
        paintListenNote(withDelivery(agent, target));
      }
    },
    selectedId: () => composeTarget?.id ?? null,
    dispose: () => {},
  };
}
