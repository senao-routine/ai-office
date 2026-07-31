// ── スタイル2: 3Dアイソメ・グラス ────────────────────────────────
// R50-P3(8) でHUDを参考画像2のガラス3カラムへ書き直した。
//   左=ブランド＋ゾーン概況 ／ 中央=挨拶＋3Dステージ＋タスク/履歴 ／
//   右=オフィス概況タイル＋エージェント一覧
// 全ての数値は world（実データ）から。参考画像にある FUNDS 等の
// 実データが無い数値は出さない（嘘のメトリクス禁止＝プラン確定事項）。
import { agoStr, attentionQueue, buildWorld, summarizeWorld } from "/ui/core/world.js";
import {
  getOffice, getStatusBoard, licenseSet, licenseStatus, newProject,
  pairList, pairNew, pairRevoke, pickProjectFolder, poll, postInstruction, spendApply,
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

export async function mount(root) {
  root.replaceChildren();
  root.className = "ui-iso";

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
        <button class="abtn" id="btn-pair" type="button"></button>
        <button class="abtn" id="btn-res" type="button"></button>
        <button class="abtn" id="btn-license" type="button"></button>
      </div>
    </aside>
    <main class="main">
      <header class="head">
        <h1 id="greet"></h1>
        <p class="sub" id="sub"></p>
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
              <button class="sheetsnd" id="sheetsnd" type="button">🔇</button>
              <button class="sheetclose" id="sheetclose" type="button">✕</button>
            </span>
          </header>
          <p class="sheetact" id="sheetact"></p>
          <div class="sheetbody" id="sheetbody"></div>
          <p class="sheettarget" id="sheettarget" hidden></p>
          <div class="compose" id="compose">
            <input id="composeinput" type="text" autocomplete="off">
          </div>
        </aside>
        <div class="toast" id="toast" hidden></div>
        <div class="modalwrap" id="modalwrap" hidden>
          <div class="modal" id="modal"></div>
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

  const scene = new IsoScene(shell.querySelector("#viewport"));
  const onResize = () => scene.resize();
  window.addEventListener("resize", onResize);

  let world = null;
  let built = null;
  const draw = (t) => {
    if (!built) return;
    scene.update(built, t);
    paintLabels(shell, scene, built);
  };
  const apply = (office) => {
    world = office;
    built = buildWorld(office);
    // 言語は office_json の lang が正本（サーバー設定に追随）。変わったら静的文言も貼り直す
    if (built.lang !== lang()) { setLang(built.lang); applyStaticStrings(shell); }
    lastDataMono = now();
    // 回答済み(楽観表示)の解除: サーバーデータでその❗が消えた/内容が変わったら戻す
    for (const [sess, key] of answered) {
      const a = built.agents.find((x) => x.session === sess);
      if (!a || !a.attention || attnKeyFor(a) !== key) answered.delete(sess);
    }
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
  const toastEl = shell.querySelector("#toast");
  let toastTimer = 0;
  const showToast = (msg, ok = true) => {
    toastEl.textContent = msg;
    toastEl.classList.toggle("err", !ok);
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 2600);
  };
  const send = async (session, name, text, attnKey = "") => {
    if (DEMO) { showToast(T("demo_no_send")); return; }
    if (sending || !session || !text) return;
    sending = true;
    try {
      await postInstruction(session, text);
      showToast(T("deliver_ok", name));
      if (attnKey) {
        // ❗への回答は「回答済み・反映待ち」を楽観表示し、二重送信の窓を即closeする
        // （実セッションでは transcript 反映まで❗が数十秒残るため）
        answered.set(session, attnKey);
        if (built) render(shell, built);
      }
    } catch (err) {
      showToast(T("deliver_fail", err.message), false);
    } finally {
      sending = false;
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
    const doing = a.activity ? T("hs_doing", a.activity) : "";
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
  const openCompose = (agent) => {
    composeTarget = { session: agent.session, name: agent.name, id: agent.id };
    shell.querySelector("#sheetname").textContent =
      agent.crew > 1 ? `${agent.name} ×${agent.crew}` : agent.name;
    typewrite(shell.querySelector("#sheetact"), humanSummary(agent));
    const body = shell.querySelector("#sheetbody");
    body.replaceChildren();
    if (agent.attention) {
      const q = sEl("div", "sheetq");
      q.append(sEl("b", "", agent.question
        ? `❓ ${agent.question}` : `❗ ${T("approval_min", agent.approvalMin)}`));
      const opts = (agent.questionOptions || []).length
        ? agent.questionOptions.slice(0, 4).map((o) => ({
            label: o.label ?? o, text: T("opt_text", o.label ?? o) }))
        : [{ label: T("opt_approve"), text: T("opt_approve_text") },
           { label: T("opt_pause"), text: T("opt_pause_text") }];
      for (const o of opts) {
        const b = sEl("button", "sheetopt", o.label);
        b.type = "button";
        b.addEventListener("click", () => {
          send(agent.session, agent.name, o.text, attnKeyFor(agent));
          closeCompose();
        });
        q.append(b);
      }
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
          composeInput.focus();
        });
        crewWrap.append(row);
      });
      body.append(crewWrap);
    }
    const work = agent.work || {};
    for (const [key, label] of [["now", T("work_now")], ["next", T("work_next")],
                                ["done", T("work_done")]]) {
      for (const item of (work[key] || []).slice(0, 3)) {
        body.append(sEl("div", "sheetline", `${label} ${item}`));
      }
    }
    if ((agent.feed || []).length) {
      body.append(sEl("b", "sheetsub", T("recent_moves")));
      for (const line of agent.feed.slice(-8)) {
        body.append(sEl("div", "sheetline feed", line));
      }
    }
    const quick = sEl("div", "sheetquick");
    for (const q of T("quick")) {
      const b = sEl("button", "qchip", q);
      b.type = "button";
      // 内訳で宛先を切り替えた後は QUICK もその宛先へ（❗回答ボタンは代表=❗保持者のまま）
      b.addEventListener("click", () => {
        send(composeTarget?.session || agent.session, agent.name, q);
        closeCompose();
      });
      quick.append(b);
    }
    body.append(quick);
    paintTarget(agent);
    sheetEl.hidden = false;
    composeInput.focus();
    if (built) render(shell, built);            // 選択ハイライトを反映
  };
  const closeCompose = () => {
    composeTarget = null;
    sheetEl.hidden = true;
    composeInput.value = "";
    clearInterval(typeTimer);
    if (built) render(shell, built);
  };
  const sndBtn = shell.querySelector("#sheetsnd");
  const paintSnd = () => { sndBtn.textContent = soundOn() ? "🔈" : "🔇"; };
  paintSnd();
  sndBtn.addEventListener("click", () => { setSound(!soundOn()); paintSnd(); });
  shell.querySelector("#sheetclose").addEventListener("click", closeCompose);
  composeInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && composeInput.value.trim() && composeTarget) {
      send(composeTarget.session, composeTarget.name, composeInput.value.trim());
      closeCompose();
    } else if (e.key === "Escape") {
      closeCompose();
    }
  });
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
  viewportEl.addEventListener("click", (e) => {
    const { x, y } = stagePoint(e);
    const p2 = scene.projectBoss?.();
    if (p2 && Math.hypot(x - p2.left, y - p2.top) <= 52) {
      modal.replaceChildren(mEl("b", "mtitle", T("boss_title")),
        mEl("p", "mnote", T("boss_note")));
      for (const a of built?.agents || []) {
        const row = mEl("button", "mpick", "");
        row.type = "button";
        const dot = mEl("i", `sq-ish st-${a.state}`);
        row.append(dot, mEl("b", "", a.crew > 1 ? `${a.name} ×${a.crew}` : a.name),
          mEl("span", "", a.activity || ""));
        row.addEventListener("click", () => { closeModal(); openCompose(a); });
        modal.append(row);
      }
      openModal();
      return;
    }
    const id = scene.pickAgent?.(x, y);
    const a = id && (built?.agents || []).find((q) => q.id === id);
    if (a) openCompose(a);
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
    for (const chip of shell.querySelectorAll("#labels .lbl.hov")) chip.classList.remove("hov");
    if (id) {
      shell.querySelector(`#labels .lbl[data-project="${CSS.escape(id)}"]`)?.classList.add("hov");
    }
  });
  // ── 管理フロー: ➕新プロジェクト / 📱スマホ連携（旧UIから移植） ──────
  const modalWrap = shell.querySelector("#modalwrap");
  const modal = shell.querySelector("#modal");
  const closeModal = () => { modalWrap.hidden = true; modal.replaceChildren(); };
  modalWrap.addEventListener("click", (e) => { if (e.target === modalWrap) closeModal(); });
  const openModal = () => { modalWrap.hidden = false; };
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
    const optGen = mEl("label", "mopt");
    const cbGen = mEl("input"); cbGen.type = "checkbox"; cbGen.checked = true;
    optGen.append(cbGen, document.createTextNode(T("np_gen")));
    const optLaunch = mEl("label", "mopt");
    const cbLaunch = mEl("input"); cbLaunch.type = "checkbox"; cbLaunch.checked = true;
    optLaunch.append(cbLaunch, document.createTextNode(T("np_launch")));
    modal.append(optGen, optLaunch);
    const go = mEl("button", "mgo", T("np_go"));
    go.type = "button";
    go.id = "mgo-newproj";
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        await newProject(picked.path, nameIn.value.trim(),
          { genSprite: cbGen.checked, launch: cbLaunch.checked });
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
      // Pro未解錠(403)や中継未設定はサーバーの文言をそのまま出す
      modal.replaceChildren(mEl("b", "mtitle", T("btn_pair")),
        mEl("p", "mnote merr", err.message));
      if (err.status === 403) {
        // Pro壁で袋小路にしない: 次の一歩（ライセンス登録）へワンクリック遷移
        const go = mEl("button", "mgo", T("pair_to_license"));
        go.type = "button";
        go.addEventListener("click", () => renderLicensePanel());
        modal.append(go);
      }
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

  // ── ⚡リソース（status_board 読み取りビュー） ─────────────────────
  const fmtTok = (v) => v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` :
    v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : String(v);
  shell.querySelector("#btn-res").addEventListener("click", async () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_res")),
      mEl("p", "mnote", T("loading")));
    openModal();
    let sb;
    try {
      sb = await getStatusBoard();
    } catch (err) {
      modal.replaceChildren(mEl("b", "mtitle", T("btn_res")),
        mEl("p", "mnote merr", err.message));
      return;
    }
    modal.replaceChildren(mEl("b", "mtitle", T("btn_res")),
      mEl("p", "mnote", T("res_note")));
    const jpy = sb.fx?.jpyPerUsd || 155;
    for (const pr of sb.providers || []) {
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
      } else if (pr.kind === "external") {
        sub = pr.connected
          ? (pr.cap ? `${fmtTok(pr.used || 0)} / ${fmtTok(pr.cap)}` : T("res_connected"))
          : T("res_notconnected");
      }
      head.append(mEl("span", "mressub", sub));
      row.append(head);
      modal.append(row);
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
    modal.append(led);
  });

  // ── 🧾ライセンス（状態表示＋キー登録） ─────────────────────────────
  const renderLicensePanel = async () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_license")),
      mEl("p", "mnote", T("loading")));
    openModal();
    let st;
    try {
      st = await licenseStatus();
    } catch (err) {
      modal.replaceChildren(mEl("b", "mtitle", T("btn_license")),
        mEl("p", "mnote merr", err.message));
      return;
    }
    modal.replaceChildren(mEl("b", "mtitle", T("btn_license")));
    const lic = st.license || {};
    modal.append(mEl("p", "mnote",
      T("lic_line", st.edition) +
      (lic.valid ? T("lic_valid") : lic.reason ? T("lic_reason", lic.reason) : T("lic_none"))));
    const feats = st.features || {};
    const featRow = mEl("p", "mnote",
      Object.entries({ relayPwa: T("feat_relay"), push: T("feat_push"),
                       costDash: T("feat_cost"), openclaw: T("feat_openclaw") })
        .map(([k, label]) => `${feats[k] ? "✅" : "⬜"} ${label}`).join("  "));
    modal.append(featRow);
    if (!lic.valid) {
      // 購入導線: LS checkout 確定までは「リリース通知を受け取る」を置く
      // （興味を持った瞬間の熱を袋小路にしない）。URL確定後はここを Buy に差し替える
      const buy = mEl("a", "mbuy", T("lic_buy"));
      buy.href = "https://github.com/senao-routine/ai-office";
      buy.target = "_blank";
      buy.rel = "noopener";
      modal.append(buy);
    }
    const ta = mEl("textarea", "mtextarea");
    ta.placeholder = T("lic_ph");
    ta.rows = 4;
    modal.append(ta);
    const go = mEl("button", "mgo", T("lic_go"));
    go.type = "button";
    go.id = "mgo-license";
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        const r = await licenseSet(ta.value.trim());
        showToast(r.message || T("lic_ok"));
        renderLicensePanel();
      } catch (err) {
        go.disabled = false;
        showToast(T("reg_fail", err.message), false);
      }
    });
    modal.append(go);
  };
  shell.querySelector("#btn-license").addEventListener("click", renderLicensePanel);

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
    // クレジット消費（サブスク枠の使用率＝%）とコスト（¥）は別物なので分けて描く
    creditsBody.replaceChildren();
    moneyBody.replaceChildren();
    for (const pr of sb.providers || []) {
      if (pr.kind === "gauge" && pr.status === "ok") {
        const pct = pr.usedPercent ?? 0;
        creditsBody.append(gaugeRow(pr.label || pr.id, pct / 100,
          `${Math.round(pct)}%`, pct >= 80));
      } else if (pr.kind === "external" && pr.connected && pr.cap) {
        creditsBody.append(gaugeRow(pr.label, (pr.pct ?? 0) / 100,
          `${Math.round(pr.pct ?? 0)}%`, (pr.pct ?? 0) >= 80));
      } else if (pr.kind === "tokens" && pr.tokens?.byModel) {
        const usd = Object.values(pr.tokens.byModel).reduce((a, m) => a + (m.usd || 0), 0);
        const row = gEl("div", "ghead");
        row.append(gEl("span", "", T("g_today", pr.label)),
          gEl("b", "", `≈¥${Math.round(usd * jpy).toLocaleString()}`));
        moneyBody.append(row);
      }
    }
    if (sb.spend) {
      const total = Math.round((sb.spend.totalJpy || 0) + (sb.spend.totalUsd || 0) * jpy);
      const row = gEl("div", "ghead");
      row.append(gEl("span", "", T("g_fixed")), gEl("b", "", `≈¥${total.toLocaleString()}`));
      moneyBody.append(row);
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
    },
  });
  return () => {
    stop(); stopLoop(); uninstall();
    window.removeEventListener("resize", onResize);
    window.removeEventListener("keydown", onKey);
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
  const host = shell.querySelector("#labels");
  host.replaceChildren();
  const perZone = {};
  const placed = [];
  for (const a of w.agents) {
    const idx = (perZone[a.zone] = (perZone[a.zone] ?? -1) + 1);
    if (placed.length >= 10) break;
    const at = scene.labelAnchorFor(a, w, idx);
    if (!Number.isFinite(at.left) || !Number.isFinite(at.top)) continue;
    const el = document.createElement("div");
    el.className = `lbl st-${a.state} zone-${a.zone}${a.attention ? " attn" : ""}` +
      (a.id === shell._traySel ? " sel" : "");
    el.dataset.project = a.id;
    const dot = document.createElement("i");
    dot.className = "dot";
    const name = document.createElement("b");
    name.textContent = (a.attention ? "❗" : a.pending ? "📨" : "") +
      (a.crew > 1 ? `${a.name} ×${a.crew}` : a.name);
    el.append(dot, name);
    el.style.left = `${at.left}px`;
    el.style.top = `${at.top + 6}px`;
    host.append(el);
    // 足元チップ同士の軽い重なりだけ下へ逃がす（頭上スタックの塔は作らない）
    const r = el.getBoundingClientRect();
    let top = at.top + 6;
    const box = () => ({ l: at.left - r.width / 2, r: at.left + r.width / 2,
      t: top, b: top + r.height });
    for (let guard = 0; guard < 6; guard++) {
      const me = box();
      const hit = placed.find((q) =>
        me.l < q.r + 4 && me.r > q.l - 4 && me.t < q.b + 3 && me.b > q.t - 3);
      if (!hit) break;
      top = hit.b + 3;
    }
    el.style.top = `${top}px`;
    placed.push(box());
  }
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
  shell.querySelector("#btn-pair").textContent = T("btn_pair");
  shell.querySelector("#btn-res").textContent = T("btn_res");
  shell.querySelector("#btn-license").textContent = T("btn_license");
  shell.querySelector("#greet").textContent = T("office_fallback");
  shell.querySelector("#sub").textContent = T("loading");
  shell.querySelector("#sheetsnd").title = T("snd_title");
  shell.querySelector("#composeinput").placeholder = T("compose_ph");
  shell.querySelector("#title-tasks").textContent = T("card_tasks");
  shell.querySelector("#title-hist").textContent = T("card_hist");
  shell.querySelector("#title-agents").textContent = T("card_agents");
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
    T("sub_line", w.agents.length, z.desk + z.meeting, z.queue, z.lounge);

  // 📮 配達未設定バナー（旧UIのオンボーディング表現の復元）。demoでは出ない
  let setupBar = shell.querySelector("#setupbar");
  if (!DEMO && w.setup && w.setup.hookInstalled === false) {
    if (!setupBar) {
      setupBar = el("div", "setupbar");
      setupBar.id = "setupbar";
      shell.querySelector(".head").append(setupBar);
    }
    setupBar.textContent = T("setup_hook");
  } else if (setupBar) {
    setupBar.remove();
  }

  const tray = shell.querySelector("#attn");
  tray.replaceChildren();
  tray.hidden = !attn;
  const acts = [];
  if (attn) {
    tray.append(el("b", "", T("tray_head", attn.name, ti + 1, queue.length)),
      el("span", "", attn.question || T("approval_min", attn.approvalMin)));
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
        // 承認まち: 定型2択（文言はPWAのQUICKと同思想）
        acts.push({ ...base, label: T("opt_approve"), text: T("opt_approve_text") });
        acts.push({ ...base, label: T("opt_pause"), text: T("opt_pause_text") });
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
  const agents = shell.querySelector("#agents");
  agents.replaceChildren();
  const selectedId = shell._ops?.selectedId();
  for (const a of w.agents) {
    const row = el("div", `arow st-${a.state} zone-${a.zone}${a.id === selectedId ? " sel" : ""}`);
    row.dataset.session = a.session;
    row.dataset.project = a.id;
    row.dataset.zone = a.zone;
    const head = el("div", "arowhead");
    head.append(el("i", "adot"), el("b", "aname", a.name || "?"));
    if (a.crew > 1) head.append(el("span", "acrew", `×${a.crew}`));
    if (a.pending) head.append(el("span", "apend", "📨"));   // 投函済み・未配達（inbox待ち）
    const act = el("div", "aact", a.attention
      ? (a.question ? `❓ ${a.question}` : `❗ ${T("approval_min", a.approvalMin)}`)
      : (a.activity || zoneLabel(a.zone)));
    if (!a.attention && a.age > 90) {
      act.append(el("i", "aage", ` · ${agoStr(a.age, w.lang)}`));   // 「いつの話か」を常に添える
    }
    row.append(head, act);
    const c = a.work?.counts || {};
    const done = Number(c.completed) || 0;
    const total = done + (Number(c.in_progress ?? c.inProgress) || 0) + (Number(c.pending) || 0);
    if (total > 0) {
      const prog = el("div", "aprog");
      const track = el("div", "abar");
      const fill = el("i", "afill");
      fill.style.width = `${Math.round(done / total * 100)}%`;
      track.append(fill);
      prog.append(track, el("span", "apct", `${done}/${total}`));
      row.append(prog);
    }
    agents.append(row);
  }
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
  for (const h of (w.history || []).slice(0, 4)) {
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
