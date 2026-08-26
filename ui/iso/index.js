// ── スタイル2: 3Dアイソメ・グラス ────────────────────────────────
// R50-P3(8) でHUDを参考画像2のガラス3カラムへ書き直した。
//   左=ブランド＋ゾーン概況 ／ 中央=挨拶＋3Dステージ＋タスク/履歴 ／
//   右=オフィス概況タイル＋エージェント一覧
// 全ての数値は world（実データ）から。参考画像にある FUNDS 等の
// 実データが無い数値は出さない（嘘のメトリクス禁止＝プラン確定事項）。
import {
  STARVE_MIN, activityGloss, agoStr, attentionQueue, buildWorld,
  deliveryTransitions, summarizeWorld, tidyActivity,
} from "/ui/core/world.js";
import {
  focusTerminal, getKeysStatus, getOffice, getRecipes, getStatusBoard,
  getDialog, getTemplates, setRecipes, setTemplates,
  newProject, pairList, pairNew, pairRevoke, pickProjectFolder, poll, postInstruction,
  budgetApply, fxApply, launchProject, setOfficeKey, setServerLang, spendApply,
} from "/ui/platform/api.js";
import { frozen, loop, now } from "/ui/platform/clock.js";
import { installProbe } from "/ui/platform/probe.js";
import { blip, setSound, soundOn } from "/ui/platform/sound.js";
import { STYLES } from "/ui/platform/style.js";
import { IsoScene } from "./scene3d.js";
import { T, lang, setLang } from "./strings.js";

export const STYLE = STYLES.ISO;

const ZONES = ["desk", "meeting", "queue", "lounge", "external"];
const zoneLabel = (z) => (ZONES.includes(z) ? T(`zone_${z}`) : "");

/** ❗の内容キー。質問文が変われば別の❗として扱う（回答済み楽観表示の解除判定に使う）。 */
const attnKeyFor = (a) => (a?.question ? `q:${a.question}` : `approval:${a?.session || ""}`);

/** 🎬デモモード（?demo=1）: /ui/demo/world.json を1回だけ読み、投函は行わない。 */
const DEMO = new URLSearchParams(
  typeof location === "undefined" ? "" : location.search).get("demo") === "1";

// R85-2: 購入導線 PRODUCT_SITE は R84 全機能無料化で撤去（LPへの導線は README が担う）。

export async function mount(root) {
  root.replaceChildren();
  // R67: ?t=固定（回帰スクショ）では全 transition を無効化＝入場フェード込みでも
  // golden ビット一致（監査で実証済みの方式）。通常時だけ動きが付く
  root.className = frozen ? "ui-iso no-anim" : "ui-iso";

  const shell = document.createElement("div");
  shell.className = "shell";
  shell.innerHTML = `
    <aside class="side glass">
      <div class="brand">
        <span class="mark">🤖</span>
        <span class="txt"><b>AI Office</b><i id="brandoffice">…</i></span>
      </div>
      <nav class="zones" id="zones"></nav>
      <div class="gauges" id="gauges" hidden>
        <div class="gcard" id="gcredits" hidden>
          <b class="gtitle" id="gtitle-credits"></b>
          <div class="gbody" id="gcreditbody"></div>
        </div>
        <div class="gcard" id="gmoney" hidden>
          <b class="gtitle" id="gtitle-money"></b>
          <div class="gbody" id="gmoneybody"></div>
        </div>
      </div>
      <div class="spacer"></div>
      <div class="admin">
        <button class="abtn" id="btn-newproj" type="button"></button>
        <button class="abtn" id="btn-launch" type="button"></button>
        <button class="abtn" id="btn-pair" type="button"></button>
        <button class="abtn" id="btn-run" type="button"></button>
        <button class="abtn" id="btn-res" type="button"></button>
        <button class="abtn" id="btn-settings" type="button"></button>
      </div>
    </aside>
    <main class="main">
      <header class="head">
        <h1 id="greet"></h1>
        <span class="edbadge" id="edbadge" hidden></span>
        <p class="sub" id="sub"></p>
        <i class="freshness" id="freshness" hidden></i>
      </header>
      <section class="stage" id="stage">
        <div class="viewport" id="viewport"></div>
        <div class="labels" id="labels"></div>
        <div class="offbar" id="offbar" hidden></div>
        <div class="tray" id="attn" hidden></div>
        <aside class="sheet" id="sheet" hidden>
          <header class="sheethead">
            <b id="sheetname"></b>
            <span class="sheettools">
              <button class="sheetterm" id="sheetterm" type="button">🖥</button>
              <button class="sheetsnd" id="sheetsnd" type="button">🔇</button>
              <button class="sheetclose" id="sheetclose" type="button">✕</button>
            </span>
          </header>
          <p class="sheetact" id="sheetact"></p>
          <div class="sheetbody" id="sheetbody"></div>
          <div class="quickdock" id="quickdock"></div>
          <p class="sheettarget" id="sheettarget" hidden></p>
          <div class="compose" id="compose">
            <input id="composeinput" type="text" autocomplete="off">
          </div>
        </aside>
        <div class="toast" id="toast" hidden></div>
        <div class="modalwrap" id="modalwrap" hidden>
          <div class="modal" id="modal"></div>
          <button class="modalclose" id="modalclose" type="button" aria-label="閉じる">✕</button>
        </div>
        <footer class="bottom">
          <div class="card donutcard">
            <b class="cardtitle" id="title-tasks"></b>
            <div class="donutwrap">
              <svg id="donut" viewBox="0 0 100 100" aria-hidden="true"></svg>
              <div class="donutmid" id="donutmid"></div>
            </div>
            <div class="legend" id="donutlegend"></div>
          </div>
          <div class="card histcard">
            <b class="cardtitle" id="title-hist"></b>
            <div class="hist" id="hist"></div>
          </div>
        </footer>
      </section>
    </main>
    <aside class="rail">
      <div class="card agentscard">
        <b class="cardtitle" id="title-agents"></b>
        <div class="agents" id="agents"></div>
      </div>
    </aside>
  `;
  root.append(shell);
  applyStaticStrings(shell);

  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "/ui/iso/style.css";
  await new Promise((res) => { css.onload = res; css.onerror = res; document.head.append(css); });

  // R80-B6: WebGLが使えない環境（古いGPU・仮想マシン・リモートデスクトップ・ドライバ拒否）で
  // 以前は `new IsoScene()` の例外が mount() ごと落ち、boot.html が英語の行き止まりを出していた。
  // スマホPWAは同じ状況で「リスト表示へ自動退避」するのに、デスクトップだけ白画面という逆転。
  // ここでは **3Dだけを諦めて、操作面（右レール・❗トレイ・詳細シート・下部）は全部生かす**。
  // 分岐を増やさないため、失敗時は同じ形の「何もしないシーン」を差す（null object）。
  let scene3dOk = true;
  let scene;
  try {
    scene = new IsoScene(shell.querySelector("#viewport"));
  } catch (err) {
    scene3dOk = false;
    console.warn("[iso] 3D unavailable — falling back to the list view", err);
    const nil = () => null;
    scene = {
      ready: false, update: () => {}, resize: () => {}, dispose: () => {},
      pickAgent: nil, projectAgent: nil, project: nil, projectBoss: nil,
      labelAnchorFor: nil, focusOn: () => {}, focusOff: () => {},
      stats: () => ({ drawCalls: 0, robots: 0 }),
    };
    const vp = shell.querySelector("#viewport");
    if (vp) {
      const note = document.createElement("div");
      note.id = "no3d";
      note.className = "no3d";
      note.textContent = T("no3d");
      vp.append(note);
    }
  }
  const onResize = () => scene.resize();
  window.addEventListener("resize", onResize);

  let world = null;
  let built = null;
  let freshShown = -1;
  const freshEl = shell.querySelector("#freshness");
  const draw = (t) => {
    if (!built) return;
    scene.update(built, t);
    if (scene3dOk) paintLabels(shell, scene, built);
    // R67: 「今更新された」の可視化。frozen では非表示＝golden 撮り直し不要
    if (!frozen && lastDataMono !== null) {
      const s = Math.max(0, Math.round(t - lastDataMono));
      if (s !== freshShown) {
        freshShown = s;
        freshEl.textContent = T("updated_ago", s);
        freshEl.hidden = false;
      }
    }
  };
  const apply = (office) => {
    world = office;
    built = buildWorld(office);
    // 言語は office_json の lang が正本（サーバー設定に追随）。変わったら静的文言も貼り直す
    if (built.lang !== lang()) { setLang(built.lang); applyStaticStrings(shell); }
    // R42.6骨格: openclaw版はCSS変数オーバーライドでダーク基調へ（R42.1bのカフェ転用と同思想）
    root.classList.toggle("ed-openclaw", built.edition === "openclaw");
    const eb = shell.querySelector("#edbadge");
    eb.hidden = built.edition !== "openclaw";
    if (!eb.hidden) eb.textContent = T("ed_badge_openclaw");
    lastDataMono = now();
    // 回答済み(楽観表示)の解除: サーバーデータでその❗が消えた/内容が変わったら戻す
    for (const [sess, key] of answered) {
      const a = built.agents.find((x) => x.session === sess);
      if (!a || !a.attention || attnKeyFor(a) !== key) answered.delete(sess);
    }
    // R53.2 配達の手応え: 自分が最近投函した相手の「📨解消=動き出し」「❗解消=反映」を知らせる
    // （全遷移を鳴らすとノイズ＝このUI発の指示だけ・15分でトラッキング解除）
    for (const [sess, at] of recentSends) {
      if (now() - at > 900) recentSends.delete(sess);
    }
    for (const tr of deliveryTransitions(prevAgents, built.agents)) {
      if (!recentSends.has(tr.session)) continue;
      showToast(tr.kind === "woke" ? T("woke", tr.name) : T("attn_resolved", tr.name));
      wakeUntil.set(tr.session, now() + 5);
    }
    prevAgents = built.agents;
    if (!gaugesKicked) { gaugesKicked = true; refreshGauges(); }   // features判明後に初回起動
    render(shell, built);
    // frozen（?t=固定）だと loop は起動時の1回しか回らず、それはデータ到着前なので
    // 何も描かれない。データが来た時点でも必ず描く（実際にこれで空画面を踏んだ）。
    draw(now());
  };

  // ── 操作系: ❗トレイの回答・エージェントへの指示投函 ──────────────
  let sending = false;
  let composeTarget = null;          // {session, name}
  let trayActions = [];              // [{label, text, session, name}] 数字キー1..Nに対応
  let trayIndex = 0;                 // ❗キューの表示位置（J/K・▸次へ で巡回）
  let lastDataMono = null;           // 最後にデータが届いた時刻（clock.now の単調秒）
  let gaugesKicked = false;          // 経費ゲージの初回起動フラグ（features判明後に）
  const answered = new Map();        // session -> ❗内容キー（回答済み・反映待ちの楽観状態）
  let prevAgents = null;             // 前回world（配達の手応え=遷移検出用）
  const recentSends = new Map();     // session -> mono秒（このUIから最近投函した相手だけ手応えを出す）
  const wakeUntil = new Map();       // session -> mono秒（足元チップの動き出しハイライト期限）
  const toastEl = shell.querySelector("#toast");
  // R67: トーストは直近2件の縦スタック（連続送信で前のメッセージが無告知で消えるのを根絶）。
  // #toast コンテナの hidden は ops_smoke の判定面なので維持（子0件のときだけ true）
  const showToast = (msg, ok = true) => {
    const t = document.createElement("div");
    t.className = `tmsg${ok ? "" : " err"}`;
    t.textContent = msg;
    toastEl.append(t);
    while (toastEl.children.length > 2) toastEl.firstChild.remove();
    toastEl.hidden = false;
    requestAnimationFrame(() => t.classList.add("show"));
    setTimeout(() => {
      t.remove();
      if (!toastEl.children.length) toastEl.hidden = true;
    }, 2600);
  };
  // R67: 送信中のUI状態（無反応の800msを可視化・入力とボタンを塞ぐ）
  const sendingUi = (on) => {
    composeInput.disabled = on;
    composeInput.placeholder = on ? T("sending") : T("compose_ph");
    for (const b of shell.querySelectorAll("#quickdock button, #attn button")) {
      b.disabled = on;
    }
  };
  const send = async (session, name, text, attnKey = "") => {
    if (DEMO) { showToast(T("demo_no_send")); return false; }
    if (!session || !text) return false;
    if (sending) {
      // R67: 飛行中の2通目はサイレント破棄せず「送信中」を知らせる（実測: network到達1件で黙殺していた）
      showToast(T("sending_busy"), false);
      return false;
    }
    sending = true;
    sendingUi(true);
    try {
      await postInstruction(session, text);
      showToast(T("deliver_ok", name));
      recentSends.set(session, now());   // R53.2: 手応え（woke/answered）を出す対象に登録
      if (attnKey) {
        // ❗への回答は「回答済み・反映待ち」を楽観表示し、二重送信の窓を即closeする
        // （実セッションでは transcript 反映まで❗が数十秒残るため）
        answered.set(session, attnKey);
        if (built) render(shell, built);
      }
      return true;
    } catch (err) {
      showToast(T("deliver_fail", err.message), false);
      return false;
    } finally {
      sending = false;
      sendingUi(false);
    }
  };
  const sheetEl = shell.querySelector("#sheet");
  const composeInput = shell.querySelector("#composeinput");
  const sEl = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  let typeTimer = 0;
  const typewrite = (el2, text) => {
    clearInterval(typeTimer);
    el2.textContent = "";
    let i = 0;
    typeTimer = setInterval(() => {
      if (i >= text.length) { clearInterval(typeTimer); return; }
      el2.textContent += text[i];
      if (i % 2 === 0 && text[i] !== " ") blip(523 + (i % 5) * 18);
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
  // 💬 R86-B: 会話のオンデマンド取得。stale ガード＝取得中に宛先が切り替わったら破棄。
  let dlgSeq = 0;
  const loadDialog = async (session, listEl, headEl) => {
    const seq = ++dlgSeq;
    listEl.replaceChildren(sEl("div", "dlgnote", T("loading")));
    let msgs = [];
    try {
      msgs = (await getDialog(session)).messages || [];
    } catch {
      if (seq === dlgSeq) listEl.replaceChildren(sEl("div", "dlgnote", T("dialog_err")));
      return;
    }
    if (seq !== dlgSeq) return;
    if (!msgs.length) {
      listEl.replaceChildren(sEl("div", "dlgnote", T("dialog_empty")));
      return;
    }
    if (headEl) {
      headEl.querySelector(".seccount")?.remove();
      headEl.append(sEl("i", "seccount", String(msgs.length)));
    }
    const renderMsgs = (items, moreCount) => {
      listEl.replaceChildren();
      if (moreCount > 0) {
        const more = sEl("button", "dlgmore", T("dialog_more", moreCount));
        more.type = "button";
        more.addEventListener("click", () => renderMsgs(msgs, 0));
        listEl.append(more);
      }
      for (const m of items) {
        listEl.append(sEl("div", `dlgmsg ${m.role === "user" ? "user" : "ai"}`, m.text));
      }
      listEl.scrollTop = listEl.scrollHeight;   // 最新（下端）を見せる
    };
    const over = Math.max(0, msgs.length - 12);
    renderMsgs(over ? msgs.slice(-12) : msgs, over);
  };

  const openCompose = (agent) => {
    composeTarget = { session: agent.session, name: agent.name, id: agent.id };
    let dlgListEl = null;                     // R86-B: crewrow の宛先切替から参照する
    shell.querySelector("#sheetname").textContent =
      agent.crew > 1 ? `${agent.name} ×${agent.crew}` : agent.name;
    typewrite(shell.querySelector("#sheetact"), humanSummary(agent));
    const body = shell.querySelector("#sheetbody");
    body.replaceChildren();
    if (agent.attention) {
      // 質問文の表示は本文の先頭に。回答ボタンは quickboard（compose直上の常設ボード）へ
      // 集約＝「返信はここ」の一箇所感（R54ユーザーFB）
      const q = sEl("div", "sheetq");
      q.append(sEl("b", "", agent.question
        ? `❓ ${agent.question}`
        : `❗ ${T("approval_min", agent.approvalMin)}` +
          (agent.stuckTool ? `\n${T("attn_target", tidyActivity(agent.stuckTool, 60))}` : "")));
      body.append(q);
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
          if (dlgListEl) loadDialog(s2.session, dlgListEl, dlgListEl.previousSibling);
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
    // 💬 セッションのやり取り（R86-B）: 実会話をオンデマンド取得（office_json非搭載＝
    // 中継へ流れる経路が構造的に無い）。DEMO/外部/oc- はサーバー無し・別Macなので節ごと出さない
    // （demo golden 不変の条件）。
    if (!DEMO && !agent.external && !String(agent.session || "").startsWith("oc-")) {
      const dsec = sEl("div", "sheetsec");
      const dhead = sEl("b", "sheetsec-head", T("dialog_head"));
      const dlist = sEl("div", "dlglist");
      dsec.append(dhead, dlist);
      body.append(dsec);
      dlgListEl = dlist;
      loadDialog(composeTarget?.session || agent.session, dlist, dhead);
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
      for (const o of opts) {
        const b = sEl("button", "sheetopt", o.label);
        b.type = "button";
        b.addEventListener("click", async () => {
          // R67: 成功時のみクローズ（失敗トーストの裏でシートが消える混乱を防ぐ）
          if (await send(agent.session, agent.name, o.text, attnKeyFor(agent))) closeCompose();
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
      b.append(sEl("i", "qicon", QUICK_ICONS[i] || "・"), sEl("span", "", q));
      // 内訳で宛先を切り替えた後は QUICK もその宛先へ（❗回答ボタンは代表=❗保持者のまま）
      b.addEventListener("click", async () => {
        if (await send(composeTarget?.session || agent.session, agent.name, q)) closeCompose();
      });
      quick.append(b);
    });
    // R82: ユーザー定義の定型文（保存はMac・スマホへは office_json.templates で同期）
    for (const tp of TEMPLATES) {
      const b = sEl("button", "qchip tplchip");
      b.type = "button";
      b.title = tp.text;
      b.append(sEl("i", "qicon", "✳"), sEl("span", "", tp.label));
      b.addEventListener("click", async () => {
        if (await send(composeTarget?.session || agent.session, agent.name, tp.text)) closeCompose();
      });
      quick.append(b);
    }
    const tplBtn = sEl("button", "qchip tpladd");
    tplBtn.type = "button";
    tplBtn.title = T("tpl_note");
    tplBtn.append(sEl("span", "", T("tpl_add")));
    tplBtn.addEventListener("click", openTemplateEditor);
    quick.append(tplBtn);
    board.append(quick);
    dock.append(board);
    paintTarget(agent);
    sheetEl.hidden = false;
    shell.classList.add("sheet-open");  // R80-A15: トレイを詰めて回答ボタンを生かす
    scene.focusOn?.(agent.id);          // R70: 選んだロボへカメラが寄る
    // R67: 開きだけ .show 2段階でフェードイン（hidden の即時性は維持＝スモーク互換・
    // frozen は .no-anim で transition:none＝golden 非干渉）
    requestAnimationFrame(() => sheetEl.classList.add("show"));
    composeInput.focus();
    if (built) render(shell, built);            // 選択ハイライトを反映
  };
  const closeCompose = () => {
    scene.focusOff?.();                 // R70: 全景へ戻る
    composeTarget = null;
    sheetEl.classList.remove("show");
    sheetEl.hidden = true;
    shell.classList.remove("sheet-open");
    composeInput.value = "";
    shell.querySelector("#quickdock").replaceChildren();
    clearInterval(typeTimer);
    if (built) render(shell, built);
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
  // R54-A: デスクトップ通知→タブへ戻ってきた瞬間、❗集合が変わっていれば最優先の1件を
  // トレイへ出し直す（「通知を見て開いたら該当❗が待っている」）。入力中の誤リセット無し
  let hiddenAttnIds = null;
  const onVis = () => {
    if (document.hidden) {
      hiddenAttnIds = new Set(attentionQueue(built?.agents || []).map((a) => a.id));
      return;
    }
    if (!hiddenAttnIds) return;
    const cur = attentionQueue(built?.agents || []);
    const changed = cur.length !== hiddenAttnIds.size ||
      cur.some((a) => !hiddenAttnIds.has(a.id));
    hiddenAttnIds = null;
    if (changed && cur.length) {
      trayIndex = 0;
      if (built) render(shell, built);
    }
  };
  document.addEventListener("visibilitychange", onVis);

  // ❗キューの巡回（J/K・▸次へ）。件数は render 時点の attentionQueue と同期する
  const cycleTray = (delta) => {
    const q = attentionQueue(built?.agents || []);
    if (q.length < 2) return;
    trayIndex = ((trayIndex + delta) % q.length + q.length) % q.length;
    if (built) render(shell, built);
  };
  const onKey = (e) => {
    if (e.target.closest?.("input, textarea")) return;
    if (e.key === "Escape") {
      closeCompose();
      shell.querySelector("#modalwrap").hidden = true;
      return;
    }
    // モーダル/シート表示中のキーは背後のトレイへ流さない（誤投函ガード）
    if (!shell.querySelector("#modalwrap").hidden || !sheetEl.hidden) return;
    if (e.key === "j" || e.key === "J") { cycleTray(1); return; }
    if (e.key === "k" || e.key === "K") { cycleTray(-1); return; }
    const n = Number.parseInt(e.key, 10);
    if (Number.isInteger(n) && n >= 1 && n <= trayActions.length) {
      const act = trayActions[n - 1];
      if (act.compose) openCompose(act);
      else send(act.session, act.name, act.text, act.attnKey);
    }
  };
  window.addEventListener("keydown", onKey);
  shell.querySelector("#attn").addEventListener("click", (e) => {
    if (e.target.closest(".traynext")) { cycleTray(1); return; }
    const btn = e.target.closest("button[data-idx]");
    if (!btn) return;
    const act = trayActions[Number(btn.dataset.idx)];
    if (!act) return;
    if (act.compose) openCompose(act);
    else send(act.session, act.name, act.text, act.attnKey);
  });
  shell.querySelector("#agents").addEventListener("click", (e) => {
    const row = e.target.closest(".arow");
    if (!row) return;
    const a = (built?.agents || []).find((x) => x.id === row.dataset.project);
    if (a) openCompose(a);
  });
  shell.querySelector("#labels").addEventListener("click", (e) => {
    const chip = e.target.closest(".lbl");
    if (!chip) return;
    const a = (built?.agents || []).find((x) => x.id === chip.dataset.project);
    if (a) openCompose(a);
  });
  // 3Dステージのクリック: ボス＝「ボス指令」／ロボット＝そのプロジェクトのシート
  const viewportEl = shell.querySelector("#viewport");
  const stagePoint = (e) => {
    const rect = viewportEl.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };
  // R67: click は 250ms 遅延実行し、dblclick で取り消す（従来は dblclick の1打目で
  // シートが必ず開いてしまいターミナルジャンプと競合していた）
  let clickTimer = 0;
  viewportEl.addEventListener("click", (e) => {
    const { x, y } = stagePoint(e);
    clearTimeout(clickTimer);
    clickTimer = setTimeout(() => {
      const p2 = scene.projectBoss?.();
      if (p2 && Math.hypot(x - p2.left, y - p2.top) <= 52) {
        modal.replaceChildren(mEl("b", "mtitle", T("boss_title")),
          mEl("p", "mnote", T("boss_note")));
        for (const a of built?.agents || []) {
          const row = mEl("button", "mpick", "");
          row.type = "button";
          const dot = mEl("i", `sq-ish st-${a.state}`);
          row.append(dot, mEl("b", "", a.crew > 1 ? `${a.name} ×${a.crew}` : a.name),
            mEl("span", "", activityGloss(a, lang())));
          row.addEventListener("click", () => { closeModal(); openCompose(a); });
          modal.append(row);
        }
        openModal();
        return;
      }
      const id = scene.pickAgent?.(x, y);
      const a = id && (built?.agents || []).find((q) => q.id === id);
      if (a) { scene.greet?.(a.id); openCompose(a); }   // R80.8: 挨拶=スマホと同じ演出
    }, 250);
  });
  // R80.8: PCにもスマホと同じカメラ操作を（デザイン統一のユーザーFB）。
  // ホイール=ズーム（ポインタ位置ピボット）／ドラッグ=パン／空きダブルクリック=全景。
  viewportEl.addEventListener("wheel", (e) => {
    e.preventDefault();
    const { x, y } = stagePoint(e);
    scene.viewZoomBy?.(e.deltaY < 0 ? 1.12 : 1 / 1.12, x, y);
  }, { passive: false });
  let dragFrom = null;
  let dragMoved = 0;
  viewportEl.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    dragFrom = { x: e.clientX, y: e.clientY };
    dragMoved = 0;
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragFrom) return;
    const dx = e.clientX - dragFrom.x;
    const dy = e.clientY - dragFrom.y;
    dragMoved += Math.abs(dx) + Math.abs(dy);
    if (dragMoved > 5) scene.viewPanBy?.(dx, dy);
    dragFrom = { x: e.clientX, y: e.clientY };
  });
  window.addEventListener("mouseup", () => { dragFrom = null; });
  viewportEl.addEventListener("click", (e) => {
    // ドラッグ後の click はパン操作の残骸＝選択に化けさせない（capture で先取り）
    if (dragMoved > 5) { e.stopImmediatePropagation(); clearTimeout(clickTimer); }
  }, { capture: true });

  // R53: ロボをダブルクリック → そのセッションの実ターミナルを前面へ（見る→実物の輪）
  viewportEl.addEventListener("dblclick", (e) => {
    clearTimeout(clickTimer);                 // R67: シングルクリック側を無効化
    const { x, y } = stagePoint(e);
    const id = scene.pickAgent?.(x, y);
    const a = id && (built?.agents || []).find((q) => q.id === id);
    if (a && !a.external) jumpTerminal(a.session, a.name);
    else if (!a) scene.viewReset?.();       // R80.8: 空きダブルクリック=全景（スマホと同じ）
  });
  // ホバー: ロボット/ボスの上で cursor:pointer＋対応する足元チップを強調（60msスロットリング）
  let hoverLast = 0;
  viewportEl.addEventListener("mousemove", (e) => {
    const tNow = performance.now();
    if (tNow - hoverLast < 60) return;
    hoverLast = tNow;
    const { x, y } = stagePoint(e);
    const boss = scene.projectBoss?.();
    const overBoss = Boolean(boss && Math.hypot(x - boss.left, y - boss.top) <= 52);
    const id = overBoss ? null : scene.pickAgent?.(x, y);
    viewportEl.style.cursor = (overBoss || id) ? "pointer" : "";
    // R67: 直接 .hov を付けても毎フレームの paintLabels に消されて一度も見えていなかった
    // （実測）。描画状態 hoverId に記録し、paintLabels が毎フレーム反映する（wakeと同じ型）
    shell._fx.hoverId = id || null;
  });
  // ── 管理フロー: ➕新プロジェクト / 📱スマホ連携（旧UIから移植） ──────
  const modalWrap = shell.querySelector("#modalwrap");
  const modal = shell.querySelector("#modal");
  const closeModal = () => {
    modalWrap.classList.remove("show");
    modalWrap.hidden = true;
    modal.replaceChildren();
  };
  modalWrap.addEventListener("click", (e) => { if (e.target === modalWrap) closeModal(); });
  // R80-A8: 全モーダルに閉じる手段を出す。従来は「背景クリック」か Escape だけで、
  // **画面上に閉じ方が一切見えていなかった**（スマホは全シートに「閉じる」があるのに逆転）。
  shell.querySelector("#modalclose").addEventListener("click", closeModal);
  const openModal = () => {
    modalWrap.hidden = false;
    requestAnimationFrame(() => modalWrap.classList.add("show"));   // R67: 開きのフェード
  };

  // R82: 定型文エディタ（8件×120字・保存でスマホにも同期）
  let TEMPLATES = [];
  const refreshTemplates = async () => {
    try { TEMPLATES = (await getTemplates())?.templates || []; } catch { /* 未対応サーバーでも動く */ }
  };
  refreshTemplates();
  const openTemplateEditor = () => {
    const draft = TEMPLATES.map((tp) => ({ ...tp }));
    const paint = () => {
      modal.replaceChildren(mEl("b", "mtitle", T("tpl_title")),
        mEl("p", "mnote", T("tpl_note")));
      draft.forEach((tp, i2) => {
        const row = mEl("div", "tplrow");
        row.append(mEl("b", "", tp.label), mEl("span", "tpltext", tp.text));
        const del = mEl("button", "tpldel", T("tpl_del"));
        del.type = "button";
        del.addEventListener("click", () => { draft.splice(i2, 1); paint(); });
        row.append(del);
        modal.append(row);
      });
      const li = mEl("input", "minput");
      li.type = "text"; li.placeholder = T("tpl_label_ph"); li.maxLength = 20;
      const ti = mEl("input", "minput");
      ti.type = "text"; ti.placeholder = T("tpl_text_ph"); ti.maxLength = 120;
      const add = mEl("button", "sub tpladdrow", T("tpl_add_row"));
      add.type = "button";
      add.addEventListener("click", () => {
        if (!li.value.trim() || !ti.value.trim() || draft.length >= 8) return;
        draft.push({ label: li.value.trim(), text: ti.value.trim() });
        paint();
      });
      const save = mEl("button", "mgo", T("tpl_save"));
      save.type = "button";
      save.addEventListener("click", async () => {
        try {
          await setTemplates(draft);
          TEMPLATES = draft.map((tp) => ({ ...tp }));
          showToast(T("tpl_saved"));
          closeModal();
        } catch (err) {
          showToast(T("reg_fail", err.message), false);
        }
      });
      modal.append(li, ti, add, save);
    };
    paint();
    openModal();
  };
  const mEl = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  shell.querySelector("#btn-newproj").addEventListener("click", async () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_newproj")),
      mEl("p", "mnote", T("np_opening")));
    openModal();
    let picked;
    try {
      picked = await pickProjectFolder();
    } catch (err) {
      modal.replaceChildren(mEl("b", "mtitle", T("btn_newproj")),
        mEl("p", "mnote merr", err.message));
      return;
    }
    modal.replaceChildren();
    modal.append(mEl("b", "mtitle", T("btn_newproj")));
    modal.append(mEl("p", "mpath", picked.path));
    const nameIn = mEl("input", "minput");
    nameIn.type = "text";
    nameIn.value = picked.suggest || "";
    nameIn.placeholder = T("np_name_ph");
    modal.append(nameIn);
    const optLaunch = mEl("label", "mopt");
    const cbLaunch = mEl("input"); cbLaunch.type = "checkbox"; cbLaunch.checked = true;
    optLaunch.append(cbLaunch, document.createTextNode(T("np_launch")));
    modal.append(optLaunch);
    const go = mEl("button", "mgo", T("np_go"));
    go.type = "button";
    go.id = "mgo-newproj";
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        await newProject(picked.path, nameIn.value.trim(),
          { launch: cbLaunch.checked });
        showToast(T("np_joined", nameIn.value.trim() || picked.suggest));
        closeModal();
      } catch (err) {
        go.disabled = false;
        showToast(T("reg_fail", err.message), false);
      }
    });
    modal.append(go);
    nameIn.focus();
  });

  const renderPairPanel = async () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_pair")),
      mEl("p", "mnote", T("pair_issuing")));
    openModal();
    let dev;
    try {
      dev = await pairNew(T("pair_device_label"));
    } catch (err) {
      // 中継未設定などはサーバーの文言をそのまま出す（Pro壁はR84撤去済み）
      modal.replaceChildren(mEl("b", "mtitle", T("btn_pair")),
        mEl("p", "mnote merr", err.message));
      return;
    }
    modal.replaceChildren(mEl("b", "mtitle", T("btn_pair")));
    if (dev.pairUrl) {
      if (dev.qrSvg) {
        const img = mEl("img", "mqr");
        img.alt = T("pair_qr_alt");
        img.src = `data:image/svg+xml;utf8,${encodeURIComponent(dev.qrSvg)}`;
        modal.append(img);
      }
      modal.append(mEl("p", "mnote", T("pair_scan")));
      const copy = mEl("button", "mgo", T("pair_copy"));
      copy.type = "button";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(dev.pairUrl);
          showToast(T("pair_copied"));
        } catch {
          showToast(T("pair_copy_fail"), false);
        }
      });
      modal.append(copy);
    } else {
      modal.append(mEl("p", "mnote", T("pair_norelay")));
    }
    try {
      const { devices } = await pairList();
      if (devices?.length) {
        const list = mEl("div", "mdevices");
        list.append(mEl("b", "msub", T("pair_devices", devices.length)));
        for (const d of devices) {
          const row = mEl("div", "mdev");
          row.append(mEl("span", "", d.label || d.device_id));
          const rv = mEl("button", "mrevoke", T("pair_revoke"));
          rv.type = "button";
          rv.addEventListener("click", async () => {
            try {
              await pairRevoke(d.device_id);
              showToast(T("pair_revoked"));
              renderPairPanel();
            } catch (err) {
              showToast(err.message, false);
            }
          });
          row.append(rv);
          list.append(row);
        }
        modal.append(list);
      }
    } catch { /* 一覧はベストエフォート */ }
  };
  shell.querySelector("#btn-pair").addEventListener("click", renderPairPanel);

  // ── ▶ 遠隔実行の許可リスト（R79-10）─────────────────────────────────
  // **ここが唯一の作成・編集口**（loopback+CSRF＝Macの前の人間だけ）。スマホ側は
  // 登録済み id を参照して実行を依頼できるだけ＝鍵が漏れても未登録コマンドは動かない。
  const renderRunPanel = async () => {
    modal.replaceChildren(mEl("b", "mtitle", T("run_head")), mEl("p", "mnote", T("run_note")));
    openModal();
    let data = null;
    try {
      data = await getRecipes();
    } catch (err) {
      modal.append(mEl("p", "mnote merr", err.message));
      return;
    }
    const recipes = data?.recipes || [];
    if (!recipes.length) modal.append(mEl("p", "mnote", T("run_empty")));
    for (const r of recipes) {
      const row = mEl("div", "mkeyrow");
      row.append(mEl("b", null, (r.dangerous ? "⚠️ " : "▶ ") + r.label),
                 mEl("code", "mnote", r.argv.join(" ")));
      modal.append(row);
    }
    for (const e of (data?.errors || [])) modal.append(mEl("p", "mnote merr", e));
    const ta = document.createElement("textarea");
    ta.className = "mtextarea";
    ta.rows = 10;
    ta.value = JSON.stringify({ recipes }, null, 1);
    const save = mEl("button", "abtn", T("run_save"));
    save.type = "button";
    save.addEventListener("click", async () => {
      let parsed;
      try {
        parsed = JSON.parse(ta.value);
      } catch (err) {
        showToast(String(err.message || err), false);
        return;
      }
      try {
        const res = await setRecipes(parsed.recipes ?? parsed);
        showToast(res?.msg || T("run_saved"));
        renderRunPanel();
      } catch (err) {
        showToast(err.message, false);
      }
    });
    // R80-A6: 雛形へUIから到達できるようにする（従来は example.json がリポジトリにしか無く、
    // 初見に argv配列・絶対cwd・returnOutput を素手で書かせていた）
    const sample = mEl("button", "abtn", T("run_sample"));
    sample.type = "button";
    sample.addEventListener("click", () => {
      const cur = (() => {
        try { return JSON.parse(ta.value).recipes || []; } catch { return []; }
      })();
      const example = {
        id: "r_status", label: T("run_sample_label"),
        argv: ["git", "status", "--short"],
        cwd: "/Users/you/path/to/your-project",
        timeoutSec: 30, returnOutput: "tail",
      };
      ta.value = JSON.stringify({ recipes: [...cur, example] }, null, 1);
      showToast(T("run_sample_note"));
    });
    modal.append(mEl("p", "mnote", T("run_hint")), ta, sample, save);
  };
  shell.querySelector("#btn-run").addEventListener("click", renderRunPanel);

  // ── ⚡リソース（status_board 読み取りビュー） ─────────────────────
  // R72: 課金方式の正本はサーバー（status_board の billing）。ただし app/ 未更新など
  // 版ズレで billing 欠落のときにグループが空になると「全部消えた」ように見えるので、
  // kind から同じ規則で補う（旧server後方互換の掟＝サーバー正本＋クライアント補完）。
  const BILLING_FALLBACK = { tokens: "subscription", gauge: "subscription",
    login: "subscription", external: "apikey", api: "apikey", ledger: "manual" };
  const billingOf = (pr) => pr?.billing || BILLING_FALLBACK[pr?.kind] || "";
  const fmtTok = (v) => v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` :
    v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : String(v);
  // R66: 🔑連携はPro未解錠(403)でも使えるべき＝status_board失敗時にも単独描画できる関数
  const renderKeysSection = async (sb) => {
    // 🔑 アカウント連携（R54: 旧UIの連携設定を移植）。Claude/Codex/Gemini は接続状態と
    // 手順ヒント・key型（OpenAI/X等）は行内フォームで保存（/api/keys/set・値はマスク入力）
    let ks = null;
    try {
      ks = await getKeysStatus();
    } catch { /* 取得失敗時はセクションごと出さない（嘘の状態を見せない） */ }
    if (ks?.providers?.length) {
      const NAME_BY_ID = { openai_key: "OPENAI_API_KEY", x_api: "X_BEARER_TOKEN",
                           openai_usage: "OPENAI_ADMIN_KEY",
                           // R65: R63のAPIプロバイダが未登録で接続ボタンが出なかった実バグ修正
                           openrouter: "OPENROUTER_API_KEY", moonshot: "MOONSHOT_API_KEY",
                           deepseek: "DEEPSEEK_API_KEY", groq: "GROQ_API_KEY" };
      // R66: 「いま: <アカウント>」ガイド用（statusBoardは既に取得済み=追加fetchしない）
      const acctEmail = (id) => {
        const em = (sb?.providers || []).find((p2) => p2.id === id)?.account?.email || "";
        return em ? em.split("@")[0] : "";
      };
      const sec = mEl("div", "mkeys");
      sec.append(mEl("b", "msub", T("keys_head")));
      // R66ユーザーFB「接続方法が分かりづらい」: 方式別2グループ＝
      // 🅰 自動（ターミナルでログインするだけ・キー入力UIを出さない） / 🅱 APIキーを貼る
      const grpA = mEl("div", "mkeygrp");
      grpA.append(mEl("b", "mkeygrph", T("keys_grp_auto")),
        mEl("p", "mkeygrpsub", T("keys_grp_auto_sub")));
      const grpB = mEl("div", "mkeygrp");
      grpB.append(mEl("b", "mkeygrph", T("keys_grp_key")),
        mEl("p", "mkeygrpsub", T("keys_grp_key_sub")));
      for (const pr of ks.providers) {
        const row = mEl("div", "mkeyrow");
        const head = mEl("div", "mkeyhead");
        head.append(mEl("i", "mkeydot" + (pr.connected ? " on" : "")),
          mEl("span", "mkeyname", pr.label || pr.id));
        const keyName = NAME_BY_ID[pr.id];
        if (pr.mode !== "key") {
          if (pr.id === "claude" && built?.features?.claudeSessions === false) continue;
          // 🅰: バッジ「自動」＋ガイド1行（Claude/Codexは今のアカウントも見せる）
          head.append(mEl("span", "mkeyauto", T("keys_badge_auto")));
          const email = (pr.id === "claude" || pr.id === "codex") ? acctEmail(pr.id) : "";
          const guide = (pr.hint || "") +
            (pr.connected && email ? `（${T("keys_now", email)}）` : "");
          row.append(head, mEl("i", "mkeyhint", guide));
          grpA.append(row);
          continue;
        }
        if (pr.connected && pr.masked) head.append(mEl("i", "mkeymask", pr.masked));
        // ガイド行: 何が取れるか＋未接続なら発行場所（URL文字列表示のみ）
        const guideParts = [pr.hint || ""];
        if (!pr.connected && pr.getFrom) guideParts.push(T("keys_getfrom", pr.getFrom));
        if (keyName) {
          const btn = mEl("button", "mkeybtn",
            pr.connected ? T("keys_change") : T("keys_connect"));
          btn.type = "button";
          btn.dataset.key = keyName;
          btn.addEventListener("click", () => {
            const open = row.querySelector(".mkeyform");
            if (open) { open.remove(); return; }
            const kform = mEl("span", "mkeyform");
            const input = mEl("input", "mkeyin");
            input.type = "password";
            input.placeholder = pr.ph || T("keys_ph");   // R66: キー形式の例（sk-or-v1-…等）
            input.autocomplete = "off";
            const save = mEl("button", "mkeysave", T("keys_save"));
            save.type = "button";
            save.addEventListener("click", async () => {
              save.disabled = true;
              try {
                await setOfficeKey(keyName, input.value.trim());
                showToast(T("keys_saved"));
                shell.querySelector("#btn-res").click();   // connected/masked を反映
              } catch (err) {
                save.disabled = false;
                showToast(err.message, false);
              }
            });
            kform.append(input, save);
            row.append(kform);
            input.focus();
          });
          head.append(btn);
          if (pr.connected) {
            // R65: 解除（value=""で行削除）。↻再送と同じ2クリック制＝誤爆ガード
            const rv = mEl("button", "mkeyrevoke", T("keys_revoke"));
            rv.type = "button";
            let armed = 0;
            rv.addEventListener("click", async () => {
              if (!armed) {
                armed = setTimeout(() => { armed = 0; rv.textContent = T("keys_revoke"); }, 3000);
                rv.textContent = T("keys_revoke_arm");
                return;
              }
              clearTimeout(armed);
              rv.disabled = true;
              try {
                await setOfficeKey(keyName, "");
                showToast(T("keys_revoked"));
                shell.querySelector("#btn-res").click();
              } catch (err) {
                rv.disabled = false;
                showToast(err.message, false);
              }
            });
            head.append(rv);
          }
        }
        row.append(head, mEl("i", "mkeyhint", guideParts.filter(Boolean).join(" · ")));
        grpB.append(row);
      }
      sec.append(grpA, grpB);
      modal.append(sec);
    }
  };
  shell.querySelector("#btn-res").addEventListener("click", async () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_res")),
      mEl("p", "mnote", T("loading")));
    openModal();
    let sb;
    try {
      sb = await getStatusBoard();
    } catch (err) {
      // R66: Pro未解錠(403)等でも🔑アカウント連携には到達できる（キャラ生成キー等は無料機能）
      modal.replaceChildren(mEl("b", "mtitle", T("btn_res")),
        mEl("p", "mnote merr", err.message));
      await renderKeysSection(null);
      return;
    }
    modal.replaceChildren(mEl("b", "mtitle", T("btn_res")),
      mEl("p", "mnote", T("res_note")));
    const jpy = sb.fx?.jpyPerUsd || 155;
    // R72: 「定額のサブスク枠」と「APIキーの従量課金」を混ぜて並べると、どれが
    // 使い放題でどれが使うほど請求されるのか読めない（ユーザーFB）。billing（サーバー正本）で
    // 2グループに割り、見出しに課金の性質を1行で書く。
    const usdJpy = (v) => `$${v.toFixed(2)} ≈ ¥${Math.round(v * jpy).toLocaleString()}`;
    const subs = (sb.providers || []).filter((p) => billingOf(p) === "subscription");
    if (subs.length) {
      modal.append(mEl("b", "msub", T("res_grp_sub")),
        mEl("p", "mnote", T("res_grp_sub_note")));
    }
    for (const pr of subs) {
      const row = mEl("div", "mres");
      const head = mEl("div", "mreshead");
      head.append(mEl("b", "", pr.label || pr.id));
      let sub = "";
      if (pr.kind === "tokens" && pr.tokens?.today) {
        const usd = Object.values(pr.tokens.byModel || {})
          .reduce((a, m) => a + (m.usd || 0), 0);
        sub = T("res_today", fmtTok(pr.tokens.today.total),
          Math.round(usd * jpy).toLocaleString());
      } else if (pr.kind === "gauge") {
        sub = T("res_used", pr.plan || "", Math.round(pr.usedPercent ?? 0));
        if (pr.resetsAt) {
          const d = new Date(pr.resetsAt * 1000);
          sub += T("res_reset", d.getMonth() + 1, d.getDate());
        }
      } else if (pr.kind === "login") {
        sub = pr.loggedIn ? T("res_login_yes") : T("res_login_no");
      }
      head.append(mEl("span", "mressub", sub));
      row.append(head);
      modal.append(row);
    }
    // ── R63: 🔌 APIプロバイダ（消費・残高・上限を1箇所に集約） ──────────
    // 上限が判明しているものだけバー。取れないものは「上限が設定されていません」と
    // 明示して消費/残高だけ出す（嘘の%を作らない掟）。予算はその場で設定できる。
    // R72: external（X API / OpenAI 管理キー）も課金方式は同じ従量なので同じ節に入れる。
    // 旧実装では OpenAI は上の一覧で「接続済み」としか出ず、取得済みの当月額が
    // どこにも出ていなかった（＝キーを入れたのに認識されていないように見える実UX欠陥）。
    const apis = (sb.providers || []).filter((p) => billingOf(p) === "apikey");
    if (apis.length) {
      const sec = mEl("div", "mapis");
      sec.append(mEl("b", "msub", T("api_head")), mEl("p", "mnote", T("res_grp_api_note")));
      for (const pr of apis) {
        const row = mEl("div", "mapi");
        const head = mEl("div", "mreshead");
        head.append(mEl("b", "", pr.label || pr.id));
        if (pr.kind === "external") {
          let sub;
          if (!pr.connected) sub = T("res_nokey");
          else if (pr.cap) sub = `${fmtTok(pr.used || 0)} / ${fmtTok(pr.cap)}`;
          else if (pr.monthUsd != null) {
            sub = pr.sinceDay
              ? T("res_month_since", usdJpy(pr.monthUsd), pr.sinceDay)
              : T("res_month", usdJpy(pr.monthUsd));
          } else sub = T("res_connected");
          head.append(mEl("span", "mressub", sub));
          row.append(head);
          if (pr.connected && pr.pct != null && pr.cap) {
            const track = mEl("div", "gbar big");
            const fill = mEl("i", pr.pct >= 80 ? "gfill warn" : "gfill");
            fill.style.width = `${Math.max(2, Math.round(pr.pct))}%`;
            track.append(fill);
            row.append(track);
          } else if (pr.connected && pr.monthUsd != null) {
            row.append(mEl("span", "gsub gnolimit", T("api_no_limit")));
          }
          sec.append(row);
          continue;
        }
        const money = (v) => (pr.currency === "CNY" ? `CN¥${v.toFixed(2)}`
          : pr.currency === "JPY" ? `¥${Math.round(v).toLocaleString()}`
          : `$${v.toFixed(2)}`);
        let sub = "";
        if (pr.status === "error") sub = pr.error || T("api_err");
        else if (pr.spentMonth != null && pr.limit != null) {
          sub = `${money(pr.spentMonth)} / ${money(pr.limit)}`;
        } else if (pr.spentMonth != null) sub = money(pr.spentMonth);
        else if (pr.balance != null) sub = T("api_balance", money(pr.balance));
        else sub = T("api_nodata");
        head.append(mEl("span", "mressub", sub));
        row.append(head);
        if (pr.pct != null) {
          const track = mEl("div", "gbar big");
          const fill = mEl("i", pr.pct >= 80 ? "gfill warn" : "gfill");
          fill.style.width = `${Math.max(2, Math.round(pr.pct))}%`;
          track.append(fill);
          row.append(track);
          row.append(mEl("span", "gsub",
            `${Math.round(pr.pct)}%${pr.limitSource === "manual" ? T("api_budget_tag") : ""}`));
        } else if (pr.note === "no_limit") {
          row.append(mEl("span", "gsub gnolimit", T("api_no_limit")));
        }
        // 予算の設定（上限がAPIから取れないプロバイダで意味を持つ）
        if (pr.limitSource !== "api") {
          const form = mEl("div", "mapibudget");
          const amt = mEl("input", "minput mnum");
          amt.type = "number";
          amt.min = "0";
          amt.step = "1";
          amt.placeholder = T("api_budget_ph");
          if (pr.limitSource === "manual" && pr.limit != null) amt.value = String(pr.limit);
          const save = mEl("button", "mgo mgosm", T("api_budget_save"));
          save.type = "button";
          save.addEventListener("click", async () => {
            save.disabled = true;
            try {
              await budgetApply(pr.id, Number(amt.value) || 0,
                pr.currency === "CNY" ? "CNY" : "USD");
              showToast(T("api_budget_saved"));
              shell.querySelector("#btn-res").click();     // 再読込
            } catch (err) {
              save.disabled = false;
              showToast(err.message, false);
            }
          });
          form.append(amt, save);
          row.append(form);
        }
        sec.append(row);
      }
      modal.append(sec);
    }
    if (sb.spend) {
      const total = Math.round((sb.spend.totalJpy || 0) +
        (sb.spend.totalUsd || 0) * jpy);
      const row = mEl("div", "mres");
      const head = mEl("div", "mreshead");
      head.append(mEl("b", "", T("res_spend")));
      head.append(mEl("span", "mressub",
        T("res_spend_sub", total.toLocaleString(), sb.spend.items?.length || 0)));
      row.append(head);
      modal.append(row);
    }
    // 💳 台帳の編集（旧UI誘導を廃止しここで完結＝R50-残1「先に移植」の本体）。
    // API は既存 POST /api/status_board/spend をそのまま使う（形は spend_apply が正本）
    const led = mEl("div", "mled");
    led.append(mEl("b", "msub", T("led_head")));
    const list = mEl("div", "mledlist");
    for (const it of sb.spend?.items || []) {
      const row = mEl("div", "mledrow");
      const cur = it.currency === "usd" ? "$" : "¥";
      row.append(mEl("span", "mledname", it.label),
        mEl("b", "", `${cur}${Number(it.amount).toLocaleString()}`),
        mEl("i", "mledkind", it.kind === "payg" ? T("led_kind_payg") : T("led_kind_sub")));
      const del = mEl("button", "mledel", T("led_del"));
      del.type = "button";
      del.dataset.id = it.id || "";
      del.addEventListener("click", async () => {
        del.disabled = true;
        try {
          await spendApply({ op: "delete", id: it.id });
          showToast(T("led_deleted"));
          shell.querySelector("#btn-res").click();     // 最新台帳で描き直す
        } catch (err) {
          del.disabled = false;
          showToast(err.message, false);
        }
      });
      row.append(del);
      list.append(row);
    }
    led.append(list);
    const form = mEl("div", "mledform");
    const nameIn = mEl("input", "mledin mledname-in");
    nameIn.type = "text";
    nameIn.placeholder = T("led_name_ph");
    const amtIn = mEl("input", "mledin mledamt");
    amtIn.type = "number";
    amtIn.min = "0";
    amtIn.placeholder = T("led_amount_ph");
    const curSel = mEl("select", "mledin");
    for (const [v, l] of [["jpy", "¥"], ["usd", "$"]]) {
      const o = mEl("option", "", l);
      o.value = v;
      curSel.append(o);
    }
    const kindSel = mEl("select", "mledin");
    for (const [v, l] of [["sub", T("led_kind_sub")], ["payg", T("led_kind_payg")]]) {
      const o = mEl("option", "", l);
      o.value = v;
      kindSel.append(o);
    }
    const renewIn = mEl("input", "mledin mledrenew");
    renewIn.type = "number";
    renewIn.min = "1";
    renewIn.max = "31";
    renewIn.placeholder = T("led_renew_ph");
    const addBtn = mEl("button", "mgo", T("led_add"));
    addBtn.type = "button";
    addBtn.id = "mgo-ledger";
    addBtn.addEventListener("click", async () => {
      const label = nameIn.value.trim();
      const amount = Number(amtIn.value);
      if (!label || !Number.isFinite(amount) || amount < 0) {
        showToast(T("led_invalid"), false);
        return;
      }
      addBtn.disabled = true;
      const item = { label, amount, currency: curSel.value, kind: kindSel.value, note: "" };
      const rd = Number(renewIn.value);
      if (kindSel.value === "sub" && Number.isInteger(rd) && rd >= 1 && rd <= 31) item.renewDay = rd;
      try {
        await spendApply({ op: "upsert", item });
        showToast(T("led_saved"));
        shell.querySelector("#btn-res").click();
      } catch (err) {
        addBtn.disabled = false;
        showToast(err.message, false);
      }
    });
    form.append(nameIn, amtIn, curSel, kindSel, renewIn, addBtn);
    led.append(form);
    // R85-3: 💱 円換算レート（POST /api/status_board/fx＝実装済みだが呼び手ゼロだったAPIを接続。
    // UIの ≈¥ 表示は全部この1値から導出＝155固定のままだと全額表示が古くなる）
    const fxForm = mEl("div", "mledform");
    fxForm.append(mEl("span", "mledname", T("fx_label")));
    const fxIn = mEl("input", "mledin mledamt");
    fxIn.type = "number";
    fxIn.min = "1";
    fxIn.step = "0.1";
    fxIn.value = String(jpy);
    const fxBtn = mEl("button", "mgo", T("fx_save"));
    fxBtn.type = "button";
    fxBtn.id = "mgo-fx";
    fxBtn.addEventListener("click", async () => {
      fxBtn.disabled = true;
      try {
        await fxApply(Number(fxIn.value));
        showToast(T("fx_saved"));
        shell.querySelector("#btn-res").click();       // 新レートで全額を描き直す
      } catch (err) {
        fxBtn.disabled = false;
        showToast(err.message, false);
      }
    });
    fxForm.append(fxIn, fxBtn);
    led.append(fxForm);
    modal.append(led);
    await renderKeysSection(sb);
  });
  // クレジットのゲージをクリック→⚡（アカウント連携・台帳がある画面）を開く（R54ユーザーFB）
  shell.querySelector("#gauges").addEventListener("click", () => {
    shell.querySelector("#btn-res").click();
  });

  // 🧾ライセンスパネルは R84 全機能無料化で撤去（R85-2）。購入導線・鍵登録UIは存在しない。

  // ── R85-3: ▶ プロジェクト起動（launchable[]＝直近に開いたプロジェクトの再起動。
  //    従来は➕新規登録しかなく「昨日のプロジェクトを今日開き直す」導線がスマホにしか無かった） ──
  shell.querySelector("#btn-launch").addEventListener("click", () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_launch")));
    const items = built?.launchable || [];
    if (!items.length) {
      modal.append(mEl("p", "mnote", T("launch_empty")));
    } else {
      modal.append(mEl("p", "mnote", T("launch_note")));
      for (const it of items) {
        const row = mEl("button", "mpick");
        row.type = "button";
        row.textContent = `▶ ${it.name || it.projectId}`;
        row.addEventListener("click", async () => {
          row.disabled = true;
          try {
            await launchProject(it.projectId);
            showToast(T("launch_ok", it.name || it.projectId));
            closeModal();
          } catch (err) {
            row.disabled = false;
            showToast(err.message, false);
          }
        });
        modal.append(row);
      }
    }
    openModal();
  });

  // ── R85-3: ⚙ 設定（PC初の設定パネル＝ダークテーマ・言語。PWAの⚙タブと対） ──
  const THEME_KEY = "aioffice.iso.theme";
  const savedTheme = () => {
    try { return localStorage.getItem(THEME_KEY) || "light"; } catch { return "light"; }
  };
  const applyTheme = (t) => root.classList.toggle("th-dark", t === "dark");
  applyTheme(savedTheme());
  const renderSettings = () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_settings")));
    const seg = (label, opts, current, onPick) => {
      const row = mEl("div", "mledform");
      row.append(mEl("span", "mledname", label));
      for (const [v, l] of opts) {
        const b = mEl("button", `mkeybtn${v === current ? " on" : ""}`, l);
        b.type = "button";
        b.addEventListener("click", () => onPick(v));
        row.append(b);
      }
      modal.append(row);
    };
    seg(T("set_theme"), [["light", T("set_theme_light")], ["dark", T("set_theme_dark")]],
      savedTheme(), (v) => {
        try { localStorage.setItem(THEME_KEY, v); } catch { /* プライベートモード */ }
        applyTheme(v);
        renderSettings();                                  // 選択状態を描き直す
      });
    // 🌐 サーバーの lang を切り替える（office_json.lang が正本＝PWA/通知の言語も揃う）
    seg(T("set_lang"), [["ja", "日本語"], ["en", "English"]], lang(), async (v) => {
      try {
        await setServerLang(v);
        setLang(v);
        applyStaticStrings(shell);
        renderSettings();
      } catch (err) {
        showToast(err.message, false);
      }
    });
    openModal();
  };
  shell.querySelector("#btn-settings").addEventListener("click", renderSettings);

  // ↻再送は2クリック制（1回目=3秒のアーム表示・2回目で送信）。誤爆で同じ指示が飛ぶのを防ぐ
  let resendArm = { key: "", timer: 0 };
  const disarmResend = () => {
    clearTimeout(resendArm.timer);
    resendArm = { key: "", timer: 0 };
    for (const b of shell.querySelectorAll(".hresend.arm")) {
      b.classList.remove("arm");
      b.textContent = "↻";
    }
  };
  shell.querySelector("#hist").addEventListener("click", (e) => {
    const btn = e.target.closest(".hresend");
    if (!btn || !btn.dataset.session || !btn.dataset.text) return;
    const key = `${btn.dataset.session}|${btn.dataset.text}`;
    if (resendArm.key !== key) {
      disarmResend();
      resendArm = { key, timer: setTimeout(disarmResend, 3000) };
      btn.classList.add("arm");
      btn.textContent = T("resend_arm");
      return;
    }
    disarmResend();
    send(btn.dataset.session, btn.dataset.disp || T("resend_name"), btn.dataset.text);
  });

  // render() から参照できるように束ねる（描画は純粋なまま・状態はここに集約）
  // 描画側（paintLabels）が読む演出状態。純粋なworldに混ぜない
  shell._fx = { wakeActive: (sess) => now() < (wakeUntil.get(sess) || 0), hoverId: null };
  shell._ops = {
    setTrayActions: (acts) => { trayActions = acts; },
    selectedId: () => composeTarget?.id ?? null,
    answeredKey: (session) => answered.get(session) || "",
    trayIndex: () => trayIndex,
    setTrayIndex: (i) => { trayIndex = i; },
  };

  // オフライン表示は .ui-iso ルート（root）に付ける（CSS は .ui-iso.offline を見る。
  // shell に付けるとセレクタが永遠にマッチしない＝実際にサイレント故障していた）
  const offBar = shell.querySelector("#offbar");
  const stop = DEMO ? (() => {}) : poll(
    getOffice, apply,
    (offline) => {
      root.classList.toggle("offline", Boolean(offline));
      if (offline) {
        const age = lastDataMono === null ? null : Math.max(0, Math.round(now() - lastDataMono));
        offBar.textContent = age === null
          ? T("off_noconn")
          : T("off_stale", age < 90 ? T("ago_sec", age) : T("ago_min", Math.round(age / 60)));
      }
      offBar.hidden = !offline;
    },
    frozen ? 1e9 : 3000,          // 固定時刻のときはポーリングしない（スクショが揺れる）
  );

  // ── 経費ゲージ（左サイドバー常設・status_board 60秒ポーリング） ──────
  const gaugesEl = shell.querySelector("#gauges");
  const creditsCard = shell.querySelector("#gcredits");
  const creditsBody = shell.querySelector("#gcreditbody");
  const moneyCard = shell.querySelector("#gmoney");
  const moneyBody = shell.querySelector("#gmoneybody");
  const gEl = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const gaugeRow = (label, ratio, sub, warn) => {
    const row = gEl("div", "grow");
    const head = gEl("div", "ghead");
    head.append(gEl("span", "", label), gEl("b", warn ? "gwarn" : "", sub));
    const track = gEl("div", "gbar");
    const fill = gEl("i", warn ? "gfill warn" : "gfill");
    fill.style.width = `${Math.round(Math.min(1, Math.max(0, ratio)) * 100)}%`;
    track.append(fill);
    row.append(head, track);
    return row;
  };
  // R55: 旧UIのCodexバー式リッチゲージ（プロバイダ名+planチップ+%大表示+太バー+窓/リセット残）
  const fmtRemain = (resetsAt, nowEpoch) => {
    if (!resetsAt || !nowEpoch) return "";
    const s = Math.max(0, resetsAt - nowEpoch);
    if (s >= 172800) return T("g_remain_d", Math.round(s / 86400));
    if (s >= 5400) return T("g_remain_h", Math.round(s / 3600));
    return T("g_remain_m", Math.max(1, Math.round(s / 60)));
  };
  const winLabel = (minutes) => {
    if (!minutes) return "";
    if (minutes <= 600) return T("g_win_5h");
    if (minutes >= 9000) return T("g_win_week");
    return T("g_win_h", Math.round(minutes / 60));
  };
  const provBar = (pct, warn) => {
    const track = gEl("div", "gbar big");
    const fill = gEl("i", warn ? "gfill warn" : "gfill");
    // 3%や0%でも「ゲージが存在する」ことが見えるように最小フィルを敷く
    fill.style.width = `${Math.max(2, Math.round(Math.min(100, Math.max(0, pct))))}%`;
    track.append(fill);
    return track;
  };
  const provBlock = (name, plan, pct) => {
    const box = gEl("div", "gprov");
    const head = gEl("div", "gprovhead");
    head.append(gEl("b", "gname", name));
    if (plan) head.append(gEl("span", "gplan", plan));
    if (pct !== null) {
      head.append(gEl("b", `gpct${pct >= 80 ? " gwarn" : ""}`, `${Math.round(pct)}%`));
    }
    box.append(head);
    return box;
  };
  // R57: ログイン中アカウントのチップ（emailローカル部・title=フル。ローカル表示専用）。
  // ヘッダ行は名前+plan+%で幅が尽きる＝チップは専用行に置く（詰め込むと縦書き潰れ・実測1敗）
  const acctChip = (box, email) => {
    if (!email) return;
    const chip = gEl("span", "gacct", String(email).split("@")[0]);
    chip.title = String(email);
    const line = gEl("div", "gacctline");
    line.append(chip);
    box.append(line);
  };
  const refreshGauges = async () => {
    if (document.hidden) return;
    // データ到着前は判定できない（初回は apply が起動する）。Pro未解錠
    // (costDash=false) は fetch 自体をしない＝初回ロードの403 consoleノイズを根絶
    // （R42.2の教訓「閉機能はUI側でポーリング自体を止める」。未定義キー=true は旧server後方互換の掟）
    if (!built) return;
    if (built.features?.costDash === false) { gaugesEl.hidden = true; return; }
    let sb;
    try {
      sb = await getStatusBoard();
    } catch {
      gaugesEl.hidden = true;                  // エラーは黙って畳む
      return;
    }
    const jpy = sb.fx?.jpyPerUsd || 155;
    const usdJpy = (v) => `$${v.toFixed(2)} ≈ ¥${Math.round(v * jpy).toLocaleString()}`;
    // クレジット消費（サブスク枠の使用率＝%）とコスト（¥）は別物なので分けて描く。
    // R55: 旧UIのCodexバー式＝プロバイダごとのブロック（planチップ・%大表示・太バー・
    // 窓ラベル+リセット残時間・secondary窓は2本目）。実データが無い数値は出さない掟のまま
    // （Claudeのサブスク枠%はAPIが無い＝バー化しない・トークン実測だけを見せる）。
    const nowEpoch = Number(sb.generatedAt) || built?.generatedAt || 0;
    creditsBody.replaceChildren();
    moneyBody.replaceChildren();
    const renderProv = (pr) => {
      if (pr.kind === "gauge" && pr.status === "ok") {
        const pct = pr.usedPercent ?? 0;
        const box = provBlock(pr.label || pr.id, pr.plan || "", pct);
        acctChip(box, pr.account?.email);
        box.append(provBar(pct, pct >= 80));
        const sub = [winLabel(pr.windowMinutes), fmtRemain(pr.resetsAt, nowEpoch)]
          .filter(Boolean).join(" · ");
        if (sub) box.append(gEl("span", "gsub", sub));
        const sec = pr.secondary;
        if (sec && sec.usedPercent != null) {
          box.append(provBar(sec.usedPercent, sec.usedPercent >= 80));
          const sub2 = [winLabel(sec.windowMinutes), fmtRemain(sec.resetsAt, nowEpoch),
            `${Math.round(sec.usedPercent)}%`].filter(Boolean).join(" · ");
          box.append(gEl("span", "gsub", sub2));
        }
        // R57: 別アカウントの前回確認スナップショット＝2アカウント運用でも両方の残枠が見える
        for (const ac of (pr.accounts || []).filter((a) => !a.active).slice(0, 2)) {
          if (ac.usedPercent == null) continue;
          const bar = provBar(ac.usedPercent, ac.usedPercent >= 80);
          bar.classList.add("pale");
          box.append(bar);
          const who = String(ac.email || ac.id || "?").split("@")[0];
          const ago = (nowEpoch && ac.seenAt)
            ? agoStr(Math.max(0, nowEpoch - ac.seenAt), lang()) : "";
          box.append(gEl("span", "gsub",
            [who, ago ? `${T("g_prev_seen")} ${ago}` : T("g_prev_seen"),
             `${Math.round(ac.usedPercent)}%`].join(" · ")));
        }
        creditsBody.append(box);
      } else if (pr.kind === "external" && pr.connected && pr.cap) {
        const pct = pr.pct ?? 0;
        const box = provBlock(pr.label || pr.id, "", pct);
        box.append(provBar(pct, pct >= 80));
        box.append(gEl("span", "gsub",
          `${fmtTok(pr.used || 0)} / ${fmtTok(pr.cap)}`));
        creditsBody.append(box);
      } else if (pr.kind === "external" && pr.connected && pr.monthUsd != null) {
        // R72: 枠(cap)を持たない従量プロバイダ（OpenAI 管理キー）。旧実装は cap 必須の
        // 分岐しか無く、当月額を取得できていてもドロワーから丸ごと消えていた。
        const box = provBlock(pr.label || pr.id, "", null);
        box.append(gEl("span", "gsub",
          `${pr.sinceDay ? T("res_month_since", usdJpy(pr.monthUsd), pr.sinceDay)
            : T("res_month", usdJpy(pr.monthUsd))}`));
        box.append(gEl("span", "gsub gnolimit", T("api_no_limit")));
        creditsBody.append(box);
        const mrow = gEl("div", "ghead");
        mrow.append(gEl("span", "", T("g_month", pr.label || pr.id)),
          gEl("b", "", `≈¥${Math.round(pr.monthUsd * jpy).toLocaleString()}`));
        moneyBody.append(mrow);
      } else if (pr.kind === "tokens" && pr.tokens?.byModel) {
        if (built?.features?.claudeSessions === false) return;   // openclaw版=Claude面を出さない
        // Claude: R61=statusLine capture の実測枠%（rate_limits）が新鮮(15分以内)なら
        // それを主役にし、推定のペースゲージは隠す（実測>推定・両方出すと二重表示）。
        // 実測が無い/古いときだけ従来の「直近7日の5hピーク比」ペース（R55.1）へ戻す。
        const sq = pr.subscription;
        const live = (sq && sq.staleSec != null && sq.staleSec < 900
          && (sq.fiveHour || sq.sevenDay)) ? sq : null;
        const pace = !live && pr.pace && pr.pace.pct != null ? pr.pace : null;
        const headPct = live ? (live.fiveHour?.pct ?? live.sevenDay?.pct)
          : (pace ? pace.pct : null);
        const box = provBlock(pr.label || pr.id,
          live ? T("g_live_chip") : (pace ? T("g_pace_chip") : ""), headPct);
        acctChip(box, live?.account?.email || pr.account?.email);
        if (live) {
          for (const [w, lab] of [[live.fiveHour, T("g_win_5h")],
            [live.sevenDay, T("g_win_week")]]) {
            if (!w) continue;
            box.append(provBar(w.pct, w.pct >= 80));
            box.append(gEl("span", "gsub",
              [lab, fmtRemain(w.resetsAt, nowEpoch), `${Math.round(w.pct)}%`]
                .filter(Boolean).join(" · ")));
          }
        } else if (pace) {
          box.append(provBar(pace.pct, pace.pct >= 90));
          box.append(gEl("span", "gsub", T("g_pace_sub", fmtTok(pace.peak5h || 0))));
        }
        const today = pr.tokens.today?.total || 0;
        const last5h = pr.tokens.last5h?.total || 0;
        box.append(gEl("span", "gsub",
          `${T("g_tok_today", fmtTok(today))} · ${T("g_tok_5h", fmtTok(last5h))}`));
        // R57: 2アカウント以上を観測している日は、アカウント別の当日消費ミニ行を出す
        if ((pr.accounts || []).length >= 2) {
          for (const ac of pr.accounts.slice(0, 3)) {
            const row2 = gEl("div", `gacctrow${ac.active ? " on" : ""}`);
            row2.append(gEl("i", "gadot"),
              gEl("span", "galab", String(ac.email || ac.id || "?").split("@")[0]),
              gEl("b", "", T("g_tok_today", fmtTok(ac.todayTok || 0))));
            box.append(row2);
          }
        }
        creditsBody.append(box);
        const usd = Object.values(pr.tokens.byModel).reduce((a, m) => a + (m.usd || 0), 0);
        const row = gEl("div", "ghead");
        row.append(gEl("span", "", T("g_today", pr.label)),
          gEl("b", "", `≈¥${Math.round(usd * jpy).toLocaleString()}`));
        moneyBody.append(row);
      } else if (pr.kind === "login" && pr.status === "ok") {
        // R72: loggedIn を見ずに常に「ログイン済み」と出していた（未ログインでも
        // 繋がって見える誤報＝モーダル側の表示とも食い違っていた）
        const box = provBlock(pr.label || pr.id, "", null);
        box.append(gEl("span", pr.loggedIn ? "gsub gok" : "gsub",
          pr.loggedIn ? T("res_login_yes") : T("res_login_no")));
        creditsBody.append(box);
      } else if (pr.kind === "api" && pr.status === "ok") {
        // R63: 上限が判明しているものだけバー。取れないものは金額テキストのみ
        //（推測の%を作らない＝実測と推定を混ぜない掟の系）
        const money = (v) => (pr.currency === "CNY" ? `CN¥${v.toFixed(2)}`
          : pr.currency === "JPY" ? `¥${Math.round(v).toLocaleString()}`
          : `$${v.toFixed(2)}`);
        const box = provBlock(pr.label || pr.id,
          pr.limitSource === "manual" ? T("api_budget_chip") : "",
          pr.pct != null ? pr.pct : null);
        if (pr.pct != null) {
          box.append(provBar(pr.pct, pr.pct >= 80));
          box.append(gEl("span", "gsub",
            `${money(pr.spentMonth || 0)} / ${money(pr.limit)}`));
        } else if (pr.spentMonth != null) {
          box.append(gEl("span", "gsub", money(pr.spentMonth)));
          box.append(gEl("span", "gsub gnolimit", T("api_no_limit")));
        } else if (pr.balance != null) {
          box.append(gEl("span", "gsub", T("api_balance", money(pr.balance))));
        } else {
          box.append(gEl("span", "gsub gnolimit", T("api_no_limit")));
        }
        creditsBody.append(box);
      }
    };
    // R72: 定額サブスクの枠と、APIキーの従量課金を見出しで分ける（ユーザーFB
    // 「サブスクプランなのかAPIキー消費なのか分かるようにしてほしい」）。
    const byBilling = (b) => (sb.providers || []).filter((p) => billingOf(p) === b);
    for (const [billing, head] of [["subscription", "res_grp_sub"], ["apikey", "api_head"]]) {
      const group = byBilling(billing);
      if (!group.length) continue;
      creditsBody.append(gEl("div", "gbillhead", T(head)));
      group.forEach(renderProv);
    }
    if (sb.spend) {
      const total = Math.round((sb.spend.totalJpy || 0) + (sb.spend.totalUsd || 0) * jpy);
      const row = gEl("div", "ghead");
      row.append(gEl("span", "", T("g_fixed")), gEl("b", "", `≈¥${total.toLocaleString()}`));
      moneyBody.append(row);
    }
    // R85-3: 📡 中継使用量（office_json.relay＝R80の自己防衛メーター。従来はPWAだけが
    // 表示し、Cloudflare無料枠を管理する当人がMacで見えなかった）。閾値はPWAと同じ70/90%。
    const rl = built?.relay;
    if (rl && typeof rl.pct === "number") {
      const lvl = Number(rl.level) || 0;
      const sub = `${Math.round(rl.pct)}%` +
        (lvl >= 2 ? T("relay_throttled") : lvl >= 1 ? T("relay_slowed") : "");
      creditsBody.append(gEl("div", "gbillhead", T("relay_head")),
        gaugeRow(T("relay_today"), (Number(rl.pct) || 0) / 100, sub, rl.pct >= 70));
    }
    creditsCard.hidden = creditsBody.children.length === 0;
    moneyCard.hidden = moneyBody.children.length === 0;
    gaugesEl.hidden = creditsCard.hidden && moneyCard.hidden;
  };
  const gaugeTimer = frozen ? 0 : setInterval(refreshGauges, 60000);

  if (DEMO) {
    // 🎬デモ: 同梱worldを1回だけ読む（ポーリングしない・実セッション不要）。
    // 読めなければライブを1回だけ取得して静かにフォールバック。
    // ※ apply が参照する refreshGauges の定義より後に置くこと（前に置くとTDZで沈黙する＝実際に踏んだ）
    try {
      const res = await fetch("/ui/demo/world.json", { headers: { "X-Office-Local": "1" } });
      if (res.ok) apply(await res.json());
    } catch { /* fallthrough */ }
    if (world === null) {
      try { apply(await getOffice()); } catch { /* オフラインでも空画面のまま起動 */ }
    }
  }

  // 描画ループ。frozen のときは1フレームだけ描いて止まる＝スクショが必ず同じ絵になる。
  const stopLoop = loop(draw);

  const uninstall = installProbe({
    style: STYLE,
    t: () => now(),
    // データ到着＋非同期アセット（都市パノラマ）確定まで ready にしない（golden の決定論）
    isReady: () => world !== null && scene.ready(),
    dumpWorld: () => summarizeWorld(world),
    inject: apply,                          // apply が描画まで済ませる
    stats: () => scene.stats(),             // drawCalls 等の性能ゲート用
    debug: {
      // テストのクリック照準（座標の暗算をしない掟）。契約外＝ui_contract は比較しない
      agentPoint: (id) => scene.projectAgent(id),
      bossPoint: () => scene.projectBoss(),
      // 間取りの実測用（R73）。床座標→画面座標＝候補地が本当に空床かをレンダに投影して確かめる
      worldPoint: (x, y, z) => scene.project(x, y, z),
    },
  });
  return () => {
    stop(); stopLoop(); uninstall();
    window.removeEventListener("resize", onResize);
    window.removeEventListener("keydown", onKey);
    document.removeEventListener("visibilitychange", onVis);
    document.title = "AI Office";
    clearTimeout(toastTimer);
    clearInterval(gaugeTimer);
    disarmResend();
    scene.dispose();
  };
}

/**
 * ガラスのフローティングラベル（参考画像2の署名）。3D座標をスクリーンへ投影して貼る。
 * 重なりは「先に下限（❗トレイの下端）を決めてから」上へ逃がして解消する。
 * clamp を解消の後に掛けると押し戻して再び重なる（実際に踏んだ）。
 */
function paintLabels(shell, scene, w) {
  // R67: 毎フレームの replaceChildren 全再生成をやめキー付き再利用へ。
  // 従来は mousemove が付けた .hov が1フレームで消され3Dホバーが一度も機能していなかった
  // （実測）。幅/高さ測定もテキスト変化時だけに（毎フレームの全chip getBoundingClientRect 削減）
  const host = shell.querySelector("#labels");
  const oldChips = new Map([...host.children].map((n) => [n.dataset.project, n]));
  const perZone = {};
  const placed = [];
  for (const a of w.agents) {
    const idx = (perZone[a.zone] = (perZone[a.zone] ?? -1) + 1);
    // R69: cap10撤廃＝11体目以降も名札を出す（差分更新化済みでコストは許容・実測14体で確認）
    const at = scene.labelAnchorFor(a, w, idx);
    if (!Number.isFinite(at.left) || !Number.isFinite(at.top)) continue;
    let chip = oldChips.get(a.id);
    if (!chip) {
      chip = document.createElement("div");
      chip.dataset.project = a.id;
      const mono = document.createElement("i");
      mono.className = "mono";      // R80-A17: スマホのピン/チップと同じ「1文字＋状態リング」
      chip.append(mono, document.createElement("b"));
      host.append(chip);
    }
    oldChips.delete(a.id);
    const cls = `lbl st-${a.state} zone-${a.zone}${a.attention ? " attn" : ""}` +
      (a.id === shell._traySel ? " sel" : "") +
      (shell._fx?.wakeActive?.(a.session) ? " wake" : "") +   // R53.2 動き出しハイライト
      (shell._fx?.hoverId === a.id ? " hov" : "");            // R67 3Dホバー（描画状態から反映）
    if (chip.className !== cls) chip.className = cls;
    const nameEl = chip.lastElementChild;
    const txt = (a.attention ? "❗" : a.pending ? "📨" : "") +
      (a.crew > 1 ? `${a.name} ×${a.crew}` : a.name);
    if (nameEl.textContent !== txt) {
      nameEl.textContent = txt;
      const monoEl = chip.firstElementChild;
      if (monoEl) monoEl.textContent = [...String(a.name || "?")][0]?.toUpperCase() || "?";
      chip.title = a.crew > 1 ? `${a.name} ×${a.crew}` : a.name;   // R69: 省略時も全文が読める
      chip.dataset.w = "";                                    // テキスト変化＝寸法キャッシュ無効化
    }
    if (!chip.dataset.w) {
      const r = chip.getBoundingClientRect();
      chip.dataset.w = String(r.width || 60);
      chip.dataset.h = String(r.height || 22);
    }
    const cw = parseFloat(chip.dataset.w) || 60;
    const ch = parseFloat(chip.dataset.h) || 22;
    // R62: 端の席（左壁のソファ等）で名札が画面外へはみ出して名前が切れるのを防ぐ。
    // チップは translateX(-50%) 基準なので、中心を [半幅, 幅-半幅] へ丸める
    const half = cw / 2;
    const maxL = host.clientWidth - half - 2;
    const left = maxL > half + 2
      ? Math.min(Math.max(at.left, half + 2), maxL) : at.left;
    // 足元チップ同士の軽い重なりだけ下へ逃がす（頭上スタックの塔は作らない）
    let top = at.top + 6;
    const box = () => ({ l: left - half, r: left + half, t: top, b: top + ch });
    for (let guard = 0; guard < 6; guard++) {
      const me = box();
      const hit = placed.find((q) =>
        me.l < q.r + 4 && me.r > q.l - 4 && me.t < q.b + 3 && me.b > q.t - 3);
      if (!hit) break;
      top = hit.b + 3;
    }
    const leftPx = `${left}px`;
    const topPx = `${top}px`;
    if (chip.style.left !== leftPx) chip.style.left = leftPx;
    if (chip.style.top !== topPx) chip.style.top = topPx;
    placed.push(box());
  }
  for (const leftover of oldChips.values()) leftover.remove();
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

/** 言語で変わる静的クローム（テンプレート直書きだった部分）。mount と言語切替時に貼る。 */
function applyStaticStrings(shell) {
  shell.querySelector("#gtitle-credits").textContent = T("gauge_credits");
  shell.querySelector("#gtitle-money").textContent = T("gauge_money");
  shell.querySelector("#btn-newproj").textContent = T("btn_newproj");
  shell.querySelector("#btn-launch").textContent = T("btn_launch");
  shell.querySelector("#btn-pair").textContent = T("btn_pair");
  shell.querySelector("#btn-run").textContent = T("btn_run");
  shell.querySelector("#btn-res").textContent = T("btn_res");
  shell.querySelector("#btn-settings").textContent = T("btn_settings");
  shell.querySelector("#greet").textContent = T("office_fallback");
  shell.querySelector("#sub").textContent = T("loading");
  shell.querySelector("#sheetsnd").title = T("snd_title");
  shell.querySelector("#sheetterm").title = T("term_title");
  shell.querySelector("#composeinput").placeholder = T("compose_ph");
  // R80-B6: 3D不可の案内は mount 時（＝office_json の lang 到着前）に作られるので、
  // 言語が確定したここで必ず貼り直す（旧: 日本語UIに英語の案内が出ていた）
  const no3d = shell.querySelector("#no3d");
  if (no3d) no3d.textContent = T("no3d");
  shell.querySelector("#title-tasks").textContent = T("card_tasks");
  shell.querySelector("#title-hist").textContent = T("card_hist");
  shell.querySelector("#title-agents").textContent = T("card_agents");
  shell.querySelector("#gauges").title = T("gauges_title");
}

function render(shell, w) {
  const z = w.counts;
  // ❗キュー: 表示位置は mount 側の状態（J/K・▸次へ で巡回）。縮んだら先頭へ戻す
  const queue = attentionQueue(w.agents);
  let ti = shell._ops?.trayIndex?.() ?? 0;
  if (ti >= queue.length) {
    ti = 0;
    shell._ops?.setTrayIndex?.(0);
  }
  const attn = queue[ti] || null;
  shell._traySel = attn?.id ?? null;           // 足元チップの強調用（paintLabels が読む）
  // R54-A: タブタイトルに❗件数（別タブ作業中でも視界に入る）
  document.title = queue.length ? `(${queue.length}❗) AI Office` : "AI Office";

  // ── 左: ブランド＋ゾーン概況 ─────────────────────────────────
  shell.querySelector("#brandoffice").textContent = w.officeName || T("office_fallback");
  const zones = shell.querySelector("#zones");
  zones.replaceChildren();
  for (const key of ZONES) {
    const row = el("div", `zrow z-${key}`);
    row.append(el("i", "zdot"), el("span", "zlabel", zoneLabel(key)),
      el("b", "zcount", String(z[key] ?? 0)));
    zones.append(row);
  }

  // ── 中央: 挨拶＋❗トレイ ─────────────────────────────────────
  shell.querySelector("#greet").textContent = w.officeName || T("office_fallback");
  shell.querySelector("#sub").textContent =
    T(w.avatarMode === "session" ? "sub_line_session" : "sub_line",   // R86-A: 粒度で単位語を変える
      w.agents.length,
      w.agents.filter((a) => a.state === "working").length,   // R69: ゾーン頭数でなく実働数
      z.queue, z.lounge);

  // R80-A20: 0体のとき、視線が向かう**中央ステージ**にも一言置く
  //（右レールのカードだけでは、広い空オフィスを見て「壊れている?」と思われる）
  const stage = shell.querySelector("#viewport");
  let stageHint = shell.querySelector("#stagehint");
  if (!DEMO && stage && w.agents.length === 0 && !shell.querySelector("#no3d")) {
    if (!stageHint) {
      stageHint = el("div", "stagehint");
      stageHint.id = "stagehint";
      stage.append(stageHint);
    }
    stageHint.textContent = T("ob_p1");
  } else if (stageHint) {
    stageHint.remove();
  }

  // 📮 配達未設定バナー（旧UIのオンボーディング表現の復元）。demoでは出ない
  let setupBar = shell.querySelector("#setupbar");
  if (!DEMO && w.setup && w.setup.hookInstalled === false) {
    if (!setupBar) {
      setupBar = el("div", "setupbar");
      setupBar.id = "setupbar";
      shell.querySelector(".head").append(setupBar);
    }
    // R80-A21: コマンドを読ませるだけでなく**コピーできる**ようにする
    //（この帯は「回答が実セッションへ届かない」という致命的な前提条件を伝えている）
    if (!setupBar.dataset.built) {
      setupBar.replaceChildren();
      setupBar.append(el("span", "sb-msg"), el("code", "sb-cmd"));
      const copy = el("button", "sb-copy");
      copy.type = "button";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(T("setup_hook_cmd"));
          copy.textContent = T("setup_hook_copied");
          setTimeout(() => { copy.textContent = T("setup_hook_copy"); }, 1800);
        } catch { /* クリップボード不許可でもコマンドは読める */ }
      });
      setupBar.append(copy);
      setupBar.dataset.built = "1";
    }
    setupBar.querySelector(".sb-msg").textContent = T("setup_hook");
    setupBar.querySelector(".sb-cmd").textContent = T("setup_hook_cmd");
    setupBar.querySelector(".sb-copy").textContent = T("setup_hook_copy");
  } else if (setupBar) {
    setupBar.remove();
  }

  const tray = shell.querySelector("#attn");
  tray.replaceChildren();
  tray.hidden = !attn;
  const acts = [];
  if (attn) {
    tray.append(el("b", "", T("tray_head", attn.name, ti + 1, queue.length)),
      el("span", "", attn.question
        || T("approval_min", attn.approvalMin)
          + (attn.stuckTool ? ` — ${T("attn_target", tidyActivity(attn.stuckTool, 40))}` : "")));
    const isAnswered = (shell._ops?.answeredKey?.(attn.session) || "") === attnKeyFor(attn);
    if (isAnswered) {
      // 回答済み・反映待ち: ボタンを出さない＝数字キーも無効（二重送信の窓を閉じる）
      tray.append(el("i", "trayans", T("tray_answered")));
    } else {
      const base = { session: attn.session, name: attn.name, attnKey: attnKeyFor(attn) };
      if ((attn.questionOptions || []).length) {
        attn.questionOptions.slice(0, 3).forEach((o) => {
          const label = o.label ?? o;
          acts.push({ ...base, label, text: T("opt_text", label) });
        });
      } else {
        // R80: 承認まちの定型は **スマホと完全に同じ3本・同じ本文**。
        // 従来はMac 2本/スマホ 3本で、同じ「承認」でも送られる日本語が違った
        // （どちらで答えたかによってセッションが受け取る文が変わる状態だった）。
        acts.push({ ...base, label: T("opt_approve"), text: T("opt_approve_text") });
        acts.push({ ...base, label: T("opt_pause"), text: T("opt_pause_text") });
        acts.push({ ...base, label: T("opt_report"), text: T("opt_report_text") });
      }
      acts.push({ ...base, label: T("opt_free"), compose: true });
      acts.forEach((a, i) => {
        const btn = el("button", "kbd", `${i + 1} ${a.label}`);
        btn.type = "button";
        btn.dataset.idx = String(i);
        tray.append(btn);
      });
    }
    if (queue.length > 1) {
      // 巡回導線（回答済み表示中も次へ進める＝残りを捌く手が止まらない）
      const next = el("button", "kbd traynext", T("tray_next"));
      next.type = "button";
      tray.append(next);
    }
  }
  shell._ops?.setTrayActions(acts);

  // ── 右: エージェント一覧（概況タイルは左ゾーンと重複のため撤去=ユーザーFB） ──
  // R67: 3秒毎の replaceChildren 全捨てをやめキー付き差分更新へ。
  // 従来はクリック瞬間に .arow が detach されて空振りしていた（Playwright実測30sタイムアウト）。
  // 既存ノードを再利用し、変化した部分だけ書き換える（変化行は .fresh で300msハイライト）
  const agents = shell.querySelector("#agents");
  const selectedId = shell._ops?.selectedId();
  agents.querySelector(".onboard")?.remove();
  const oldRows = new Map([...agents.children]
    .filter((n) => n.classList.contains("arow"))
    .map((n) => [n.dataset.project, n]));
  const buildRow = () => {
    const row = el("div", "arow");
    const head = el("div", "arowhead");
    head.append(el("i", "adot"), el("b", "aname"), el("span", "acrew"),
      el("span", "apend", "📨"));
    const act = el("div", "aact");
    act.append(el("span", "atext"), el("i", "aage"));
    const prog = el("div", "aprog");
    const track = el("div", "abar");
    track.append(el("i", "afill"));
    prog.append(track, el("span", "apct"));
    row.append(head, act, prog);
    return row;
  };
  const setText = (node, text) => {
    if (node.textContent !== text) { node.textContent = text; return true; }
    return false;
  };
  let cursor = agents.firstElementChild;
  for (const a of w.agents) {
    let row = oldRows.get(a.id);
    const isNew = !row;
    if (!row) row = buildRow();
    oldRows.delete(a.id);
    if (cursor === row) {
      cursor = cursor.nextElementSibling;
    } else {
      agents.insertBefore(row, cursor);       // 既存ノードの移動＝同一性維持（detachしない）
    }
    row.dataset.session = a.session;
    row.dataset.project = a.id;
    row.dataset.zone = a.zone;
    const cls = `arow st-${a.state} zone-${a.zone}${a.id === selectedId ? " sel" : ""}`;
    if (row.className.replace(" fresh", "") !== cls) row.className = cls;
    let changed = setText(row.querySelector(".aname"), a.name || "?");
    row.title = a.crew > 1 ? `${a.name} ×${a.crew}` : (a.name || "");   // R69: 省略時の全文
    const crewEl = row.querySelector(".acrew");
    crewEl.hidden = !(a.crew > 1);
    if (a.crew > 1) changed = setText(crewEl, `×${a.crew}`) || changed;
    row.querySelector(".apend").hidden = !a.pending;
    const act = row.querySelector(".aact");
    const actCls = "aact" + (a.attention && a.approvalMin >= STARVE_MIN ? " starve" : "");
    if (act.className !== actCls) act.className = actCls;
    changed = setText(act.querySelector(".atext"), a.attention
      ? (a.question ? `❓ ${a.question}` : `❗ ${T("approval_min", a.approvalMin)}`)
      : (activityGloss(a, w.lang) || zoneLabel(a.zone))) || changed;
    const ageEl = act.querySelector(".aage");
    ageEl.hidden = !(!a.attention && a.age > 90);
    if (!ageEl.hidden) setText(ageEl, ` · ${agoStr(a.age, w.lang)}`);
    const c = a.work?.counts || {};
    const done = Number(c.completed) || 0;
    const total = done + (Number(c.in_progress ?? c.inProgress) || 0) + (Number(c.pending) || 0);
    const prog = row.querySelector(".aprog");
    prog.hidden = !(total > 0);
    if (total > 0) {
      const width = `${Math.round(done / total * 100)}%`;
      const fill = prog.querySelector(".afill");
      if (fill.style.width !== width) fill.style.width = width;   // .3s transitionで滑らかに
      setText(prog.querySelector(".apct"), `${done}/${total}`);
    }
    // 変化した既存行に一瞬のハイライト（frozenは.no-animで無効＝golden非干渉）
    if (changed && !isNew && !frozen) {
      row.classList.add("fresh");
      setTimeout(() => row.classList.remove("fresh"), 400);
    }
  }
  for (const leftover of oldRows.values()) leftover.remove();
  if (!w.agents.length) {
    // 空オフィス: 次の一歩を必ず示す（美しい無人オフィスで放置しない＝初回体験の断線対策）
    const card = el("div", "onboard");
    card.append(
      el("b", "", T("ob_title")),
      el("p", "", T("ob_p1")),
      el("p", "", T("ob_p2")));
    if (!DEMO) {
      const demoLink = el("a", "odemo", T("ob_demo"));
      demoLink.href = "?demo=1";
      card.append(demoLink);
    }
    agents.append(card);
  }

  // ── 下段: タスクのドーナツ＋指示履歴 ──────────────────────────
  renderDonut(shell, w.tasks);
  const hist = shell.querySelector("#hist");
  hist.replaceChildren();
  // R67: 4件目は全解像度でカード高さから完全にはみ出て不可視だった（実測）＝
  // 見える3件＋「他N件」注記に正直化
  const histItems = (w.history || []).slice(0, 3);
  for (const h of histItems) {
    const row = el("div", "hrow");
    const resend = el("button", "hresend", "↻");
    resend.type = "button";
    resend.title = T("resend_title");
    resend.dataset.session = h.session || "";
    resend.dataset.text = h.text || "";
    resend.dataset.disp = h.disp || "";
    row.append(el("b", "", h.disp || ""),
      el("span", "", h.text || ""));
    if (w.generatedAt && h.ts) {
      row.append(el("i", "hago", agoStr(w.generatedAt - h.ts, w.lang)));
    }
    row.append(
      el("i", h.pending ? "hp wait" : "hp done", h.pending ? T("hist_wait") : T("hist_done")),
      resend);
    hist.append(row);
  }
  if ((w.history || []).length > 3) {
    hist.append(el("div", "hmore", T("hist_more", w.history.length - 3)));
  }
  if (!hist.children.length) hist.append(el("div", "hempty", T("hist_empty")));
}

/** タスクのドーナツ（SVG・実データのみ・アニメ無し＝golden を揺らさない）。 */
function renderDonut(shell, tasks) {
  const svg = shell.querySelector("#donut");
  const total = tasks.pending + tasks.inProgress + tasks.completed;
  const C = 2 * Math.PI * 40;
  const segs = [
    ["#5fd39b", tasks.completed],
    ["#7c5cff", tasks.inProgress],
    ["#e8e6f4", tasks.pending],
  ];
  let acc = 0;
  svg.replaceChildren();
  const ns = "http://www.w3.org/2000/svg";
  for (const [color, value] of segs) {
    // タスク0件のときは「未着手」色の1周＝空のドーナツとして描く
    const frac = total ? value / total : (color === "#e8e6f4" ? 1 : 0);
    const c = document.createElementNS(ns, "circle");
    c.setAttribute("cx", "50"); c.setAttribute("cy", "50"); c.setAttribute("r", "40");
    c.setAttribute("fill", "none");
    c.setAttribute("stroke", color);
    c.setAttribute("stroke-width", "13");
    c.setAttribute("stroke-dasharray", `${frac * C} ${C}`);
    c.setAttribute("stroke-dashoffset", String(-acc * C));
    c.setAttribute("transform", "rotate(-90 50 50)");
    svg.append(c);
    acc += frac;
  }
  shell.querySelector("#donutmid").textContent = String(total);
  const legend = shell.querySelector("#donutlegend");
  legend.replaceChildren();
  for (const [label, value, color] of [
    [T("leg_done"), tasks.completed, "#5fd39b"],
    [T("leg_prog"), tasks.inProgress, "#7c5cff"],
    [T("leg_todo"), tasks.pending, "#b9b6cf"],
  ]) {
    const row = el("div", "lrow");
    const dot = el("i", "ldot");
    dot.style.background = color;
    row.append(dot, el("span", "", label), el("b", "", String(value)));
    legend.append(row);
  }
}
