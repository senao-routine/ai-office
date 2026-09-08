// ── スタイル2: 紙色のHUD・2カラム ───────────────────────────────
// R90-U2: 左=部署ボード ／ 中央=1行の概況＋3Dステージ。
// コストの子DOM・投函先IDは維持し、詳細は右から開く。
// 全ての数値は world（実データ）から。参考画像にある FUNDS 等の
// 実データが無い数値は出さない（嘘のメトリクス禁止＝プラン確定事項）。
import {
  STARVE_MIN, activityGloss, agoStr, buildWorld, isMuted, summarizeWorld, tidyActivity,
} from "/ui/core/world.js";
import { events, getOffice, poll } from "/ui/platform/api.js";
import { frozen, loop, now } from "/ui/platform/clock.js";
import { createArrivals } from "/ui/platform/arrivals.js";
import { installProbe } from "/ui/platform/probe.js";
import { STYLES } from "/ui/platform/style.js";
import { init as initSend } from "/ui/hud/send.js";
import { init as initSheet } from "/ui/hud/sheet.js";
import { init as initDialog } from "/ui/hud/dialog.js";
import { init as initAdmin } from "/ui/hud/admin.js";
import { init as initGauges, billingOf, fmtTok } from "/ui/hud/gauges.js";
import { init as initTray } from "/ui/hud/tray.js";
import { init as initModals } from "/ui/hud/modals.js";
import { init as initNullScene } from "/ui/hud/scene_null.js";
import { init as initDigest } from "/ui/hud/digest.js";
import { init as initGrowth } from "/ui/hud/growth.js";
import { init as initCustomize } from "/ui/hud/customize.js";
import { init as initHire } from "/ui/hud/hire.js";
import { init as initOnboarding } from "/ui/hud/onboarding.js";
import { initStream, privateBadge, privateStatus, streamOptions, streamSettings } from "/ui/hud/stream.js";
import { IsoScene } from "./scene3d.js";
import { DEFAULT_SPEC } from "/ui/core/layout_specs.js";
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
        <span class="txt"><b id="greet">AI Office</b><i id="brandoffice">…</i></span>
      </div>
      <nav class="zones" id="zones"></nav>
      <aside class="rail">
        <div class="card agentscard">
          <b class="cardtitle" id="title-agents"></b>
          <div class="agents" id="agents"></div>
        </div>
      </aside>
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
        <p class="sub" id="sub"></p>
        <div class="usage">
          <button id="usage-summary" type="button" aria-expanded="false" aria-controls="gauges"></button>
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
        </div>
        <i class="freshness" id="freshness" hidden></i>
      </header>
      <section class="stage" id="stage">
        <div class="viewport" id="viewport"></div>
        <div class="labels" id="project-labels" aria-hidden="true"></div>
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
  `;
  root.append(shell);
  const stream = streamOptions(location.search);
  shell._stream = stream;
  const broadcast = initStream({ root, shell, options: stream, T });
  applyStaticStrings(shell);

  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "/ui/iso/style.css";
  await new Promise((res) => { css.onload = res; css.onerror = res; document.head.append(css); });

  // コストDOMを変えずに、実際に描かれた値だけをヘッダーへ要約する。
  // クリックで全データを開ける。推定ペースはラベルごと残し、実測%と区別する。
  const usage = shell.querySelector(".usage");
  const gaugesEl = shell.querySelector("#gauges");
  const usageSummary = shell.querySelector("#usage-summary");
  const setUsageOpen = (open) => {
    usage.classList.toggle("open", open);
    usageSummary.setAttribute("aria-expanded", String(open));
    gaugesEl.inert = !open;
  };
  setUsageOpen(false);
  usageSummary.addEventListener("click", () => setUsageOpen(!usage.classList.contains("open")));
  shell.addEventListener("click", (e) => {
    if (!usage.contains(e.target)) setUsageOpen(false);
  });
  const summarizeGauges = () => {
    const parts = [...gaugesEl.querySelectorAll(".gprov")].map((box) => {
      const name = box.querySelector(".gname")?.textContent || "";
      const pct = box.querySelector(".gpct")?.textContent || "";
      const plan = box.querySelector(".gplan")?.textContent || "";
      const sub = box.querySelector(".gsub")?.textContent || "";
      const windowLabel = sub.split(" · ")[0];
      const qualifier = plan === T("g_pace_chip") ? plan
        : windowLabel === T("g_win_5h") ? T("board_window_5h")
        : windowLabel === T("g_win_week") ? T("g_win_week") : "";
      return [name, qualifier, pct || sub].filter(Boolean).join(" ");
    });
    const summary = shell.querySelector("#usage-summary");
    summary.textContent = parts.length && !gaugesEl.hidden
      ? parts.join(" · ") : T("gauge_credits");
    summary.title = summary.textContent;
    usage.hidden = gaugesEl.hidden;
  };
  const gaugeObserver = new MutationObserver(summarizeGauges);
  gaugeObserver.observe(gaugesEl, { childList: true, subtree: true, characterData: true,
    attributes: true, attributeFilter: ["hidden"] });
  summarizeGauges();
  gaugesEl.addEventListener("click", () => setUsageOpen(false));
  shell.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && usage.classList.contains("open")) {
      setUsageOpen(false);
      shell.querySelector("#usage-summary").focus();
      e.stopPropagation();
    }
  }, true);

  // R80-B6: WebGLが使えない環境（古いGPU・仮想マシン・リモートデスクトップ・ドライバ拒否）で
  // 以前は `new IsoScene()` の例外が mount() ごと落ち、boot.html が英語の行き止まりを出していた。
  // スマホPWAは同じ状況で「リスト表示へ自動退避」するのに、デスクトップだけ白画面という逆転。
  // ここでは **3Dだけを諦めて、操作面（部署ボード・❗トレイ・詳細シート・下部）は全部生かす**。
  // 分岐を増やさないため、失敗時は同じ形の「何もしないシーン」を差す（null object）。
  let scene3dOk = true;
  let scene;
  const openList = () => {
    root.classList.add("list-mode");
    shell.querySelector("#agents").tabIndex = -1;
    shell.querySelector("#agents").focus();
  };
  try {
    scene = new IsoScene(shell.querySelector("#viewport"));
    // R90-U5: 配信は **balanced**（contain と cover の中間）。cover だとオフィスの
    // 角（ボス席・右の外部ベイ）がフレーム外へ切れる（実測）。contain だと 16:9 で
    // 上下が余る。配信は「全部見えること」が価値なので中間を採る。
    if (stream.enabled) scene.setViewScale(1, "balanced");
  } catch (err) {
    scene3dOk = false;
    console.warn("[iso] 3D unavailable — falling back to the list view", err);
    scene = initNullScene({ shell, T, openList });
  }
  const onResize = () => scene.resize();
  window.addEventListener("resize", onResize);

  let world = null;
  let built = null;
  let digest = null;
  let freshShown = -1;
  let arrivalTick = -1;
  const arrivals = createArrivals({ isolated: frozen || stream.enabled, demo: DEMO });
  shell._arrivals = arrivals;
  const onboarding = initOnboarding({ shell, T, scene, enabled: scene3dOk && !DEMO && !stream.enabled });
  const freshEl = shell.querySelector("#freshness");
  const draw = (t) => {
    if (!built) return;
    const frame = digest?.frame(t);
    const source = frame?.world || built;
    const shown = scene.prepareWorld?.(source) || source;
    scene.update(shown, frame?.t ?? t);
    broadcast.paint(shown);
    if (scene3dOk) paintLabels(shell, scene, shown);
    onboarding.paint(t);
    if (!frozen && Math.floor(t) !== arrivalTick) {
      arrivalTick = Math.floor(t);
      for (const row of shell.querySelectorAll(".arow")) {
        paintArrivalBadge(row.querySelector(".arowhead"), arrivals.label(row.dataset.session));
      }
    }
    digest?.afterDraw?.();
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
    arrivals.update(office);
    // 言語は office_json の lang が正本（サーバー設定に追随）。変わったら静的文言も貼り直す
    if (built.lang !== lang()) { setLang(built.lang); applyStaticStrings(shell); }
    lastDataMono = now();
    delivery.update(built);
    customize.update(built);
    hud.board = departmentBoard(office, built);
    gauges.start();   // 初回データ到着後に起動
    render(shell, built, hud);
    growth.update();
    sheet.refreshGrowth();
    digest?.update();
    // frozen（?t=固定）だと loop は起動時の1回しか回らず、それはデータ到着前なので
    // 何も描かれない。データが来た時点でも必ず描く（実際にこれで空画面を踏んだ）。
    draw(now());
  };

  // HUD は mount ごとのインスタンス。最新の world と再描画を明示的に渡す。
  let lastDataMono = null;
  const getWorld = () => built;
  const repaint = () => { if (built) render(shell, built, hud); };
  const common = { shell, T, getWorld, render: repaint };
  const modals = initModals({ shell });
  const { modal, openModal, closeModal, mEl } = modals;
  const delivery = initSend({ ...common, DEMO, attnKeyFor });
  const growth = initGrowth({ ...common, getGrowth: () => world?.growth,
    showToast: delivery.showToast });
  shell._growth = growth;
  const dialog = initDialog({ T, el, modals });
  const sheet = initSheet({
    ...common, lang, DEMO, attnKeyFor, delivery, dialog,
    focusOn: (id) => scene.focusOn?.(id), focusOff: () => scene.focusOff?.(),
    getTemplates: () => admin.getTemplates(),
    openTemplateEditor: (text) => admin.openTemplateEditor(text),
    paintGrowth: growth.paintSheet,
  });
  const { openCompose, jumpTerminal } = sheet;
  const tray = initTray({ ...common, el, attnKeyFor, delivery, sheet, modals });
  const hud = { tray, sheet, board: [] };
  digest = initDigest({ ...common, DEMO, tray, showToast: delivery.showToast,
    enabled: !stream.enabled, canvas: scene3dOk ? scene.renderer.domElement : null,
    beforeOpen: () => { closeModal(); sheet.closeCompose(); }, restore: () => draw(now()) });
  shell.querySelector("#agents").addEventListener("click", (e) => {
    const row = e.target.closest(".arow");
    if (!row) return;
    const a = (built?.agents || []).find((x) => x.id === row.dataset.project);
    if (a) {
      const member = hud.board.flatMap((group) => group.sessions)
        .find((item) => item.id === a.id && item.session === row.dataset.session);
      // 非代表も自身の会話・質問・宛先で開く（内訳の8件表示上限には依存しない）。
      if (member && member.session !== a.session) openCompose({ ...member, crew: 1, sessions: [] });
      else openCompose(a);
    }
  });
  shell.querySelector("#agents").addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches(".arow")) {
      e.preventDefault();
      e.target.click();
    }
  });
  shell.querySelector("#labels").addEventListener("click", (e) => {
    if (stream.enabled) return;
    const chip = e.target.closest(".lbl");
    if (!chip) return;
    const a = (built?.agents || []).find((x) => x.id === chip.dataset.project);
    if (a) customize.openAccessories(a);
  });
  shell.querySelector("#labels").addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches(".lbl")) {
      e.preventDefault(); e.target.click();
    }
  });
  // 3Dステージのクリック: ボス＝「ボス指令」／ロボット＝そのプロジェクトのシート
  const viewportEl = shell.querySelector("#viewport");
  if (stream.enabled) viewportEl.addEventListener("pointerdown", () => scene.stopCinematic?.());
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
      if (stream.enabled || shell.classList.contains("replay-active")) return;
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
    if (stream.enabled) { scene.viewReset?.(); return; }
    const { x, y } = stagePoint(e);
    const id = scene.pickAgent?.(x, y);
    const a = id && (built?.agents || []).find((q) => q.id === id);
    if (a && !a.external) jumpTerminal(a.session, a.name);
    else if (!a) scene.viewReset?.();       // R80.8: 空きダブルクリック=全景（スマホと同じ）
  });
  // ホバー: ロボット/ボスの上で cursor:pointer＋対応する足元チップを強調（60msスロットリング）
  let hoverLast = 0;
  viewportEl.addEventListener("mousemove", (e) => {
    const tNow = now() * 1000;
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
  const customize = initCustomize({ shell, T, scene, DEMO, stream, modals, openCompose,
    refresh: () => stop.refresh?.(), showToast: delivery.showToast });
  const admin = initAdmin({
    ...common, root, lang, setLang, modals, billingOf, fmtTok,
    showToast: delivery.showToast, applyStaticStrings: () => applyStaticStrings(shell),
    renderCustomizationSettings: customize.renderSettings,
    renderStreamSettings: () => streamSettings({ modal, mEl, T, showToast: delivery.showToast, replay: digest }),
  });
  const hire = initHire({ ...common, lang, DEMO: DEMO || stream.enabled, modals, arrivals,
    refresh: () => stop.refresh?.(), showToast: delivery.showToast });
  // 描画側（paintLabels）が読む演出状態。純粋なworldに混ぜない。
  shell._fx = { wakeActive: delivery.wakeActive, hoverId: null };

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
  const stopEvents = frozen || DEMO ? (() => {}) : events(
    () => stop.refresh(),
    () => stop.setInterval(3000),
    () => {
      stop.setInterval(15000);
      stop.refresh();             // 接続・再接続時も最新のスナップショットを取得する。
    },
  );

  const gauges = initGauges({ ...common, lang });

  if (DEMO) {
    // 🎬デモ: 同梱worldを1回だけ読む（ポーリングしない・実セッション不要）。
    // 読めなければライブを1回だけ取得して静かにフォールバック。
    // ※ apply が参照する HUD 初期化より後に置くこと（初回描画のTDZを防ぐ）
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
    pollMs: () => stop.intervalMs ?? null,
    debug: {
      // テストのクリック照準（座標の暗算をしない掟）。契約外＝ui_contract は比較しない
      agentPoint: (id) => scene.projectAgent(id),
      bossPoint: () => scene.projectBoss(),
      // 間取りの実測用（R73）。床座標→画面座標＝候補地が本当に空床かをレンダに投影して確かめる
      worldPoint: (x, y, z) => scene.project(x, y, z),
    },
  });
  return () => {
    stopEvents(); stop(); stopLoop(); uninstall();
    window.removeEventListener("resize", onResize);
    gaugeObserver.disconnect();
    broadcast.dispose();
    hire.dispose(); onboarding.dispose(); customize.dispose();
    digest.dispose(); tray.dispose(); sheet.dispose(); delivery.dispose(); gauges.dispose();
    document.title = "AI Office";
    scene.dispose();
  };
}

/**
 * ガラスのフローティングラベル（参考画像2の署名）。3D座標をスクリーンへ投影して貼る。
 * 重なりは「先に下限（❗トレイの下端）を決めてから」上へ逃がして解消する。
 * clamp を解消の後に掛けると押し戻して再び重なる（実際に踏んだ）。
 */
function paintLabels(shell, scene, w) {
  const stream = shell._stream;
  const privacy = stream?.enabled && stream.privacy;
  if (!stream?.enabled) paintProjectSigns(shell, scene, w);
  // R67: 毎フレームの replaceChildren 全再生成をやめキー付き再利用へ。
  // 従来は mousemove が付けた .hov が1フレームで消され3Dホバーが一度も機能していなかった
  // （実測）。幅/高さ測定もテキスト変化時だけに（毎フレームの全chip getBoundingClientRect 削減）
  const host = shell.querySelector("#labels");
  const oldChips = new Map([...host.children].map((n) => [n.dataset.project, n]));
  const perZone = {};
  const placed = [];
  // R86-C: 名札の**下限**。従来はクランプが横方向だけで、重なり回避ループが下へしか逃がさない
  // ため、シートを開くとカメラズーム(focusOn)で足元が下がり、名札が下段カード(.bottom・z:6)の
  // 下に潜る／ステージ外へ切れる（実測: 11枚中7枚・「クリックしたらレイアウトが崩れる」の本体）。
  // .bottom の上端を実測して、そこより下へは置かない（閉じた状態では発火しない＝golden不変）。
  // R90 配備後の実測（22セッション）: 全員に名前を出すと中央で名札が団子になり、
  // 「誰がどれか」がむしろ読めない。13体以上では**名前を出すのは意味のある相手だけ**
  // （選択中・❗・📨・ホバー）にし、残りはバッジ1文字＋状態リングで足元に置く。
  // 12体以下（golden の 9 体を含む）では従来どおり全員に名前を出す＝golden 不変。
  const dense = w.agents.length > 12;
  const hostTop = host.getBoundingClientRect().top;
  const bottomBar = shell.querySelector(stream?.enabled ? "#stream-subtitle" : ".bottom");
  const limitB = bottomBar
    ? bottomBar.getBoundingClientRect().top - hostTop - 3
    : host.clientHeight - 2;
  for (const a of w.agents) {
    const idx = (perZone[a.zone] = (perZone[a.zone] ?? -1) + 1);
    // R69: cap10撤廃＝11体目以降も名札を出す（差分更新化済みでコストは許容・実測14体で確認）
    const at = scene.labelAnchorFor(a, w, idx);
    if (!Number.isFinite(at.left) || !Number.isFinite(at.top)) continue;
    if (stream?.enabled && (at.left < 0 || at.left > host.clientWidth || at.top < 0 || at.top > limitB)) continue;
    let chip = oldChips.get(a.id);
    if (!chip) {
      chip = document.createElement("div");
      chip.dataset.project = a.id;
      if (!stream?.enabled) { chip.tabIndex = 0; chip.setAttribute("role", "button"); }
      const mono = document.createElement("i");
      mono.className = "mono";      // R80-A17: スマホのピン/チップと同じ「1文字＋状態リング」
      chip.append(mono, document.createElement("b"));
      host.append(chip);
    }
    oldChips.delete(a.id);
    const arrival = frozen || stream?.enabled || shell.classList.contains("replay-active")
      ? null : shell._arrivals?.label(a.session);
    const named = arrival || privacy || stream?.enabled && stream.large || !dense || a.attention || a.pending
      || a.id === shell._traySel || shell._fx?.hoverId === a.id;
    const cls = `lbl st-${a.state} zone-${a.zone}${a.attention ? " attn" : ""}` +
      (named ? " nm" : "") +
      (a.id === shell._traySel ? " sel" : "") +
      (shell._fx?.wakeActive?.(a.session) ? " wake" : "") +   // R53.2 動き出しハイライト
      (shell._fx?.hoverId === a.id ? " hov" : "");            // R67 3Dホバー（描画状態から反映）
    if (chip.className !== cls) chip.className = cls;
    const nameEl = chip.querySelector("b");
    if (privacy) chip.firstElementChild.textContent = privateBadge(a);
    const txt = privacy ? privateStatus(a, T) : (a.attention ? "❗" : a.pending ? "📨" : "") +
      (a.crew > 1 ? `${a.name} ×${a.crew}` : a.name);
    if (nameEl.textContent !== txt) {
      nameEl.textContent = txt;
      const monoEl = chip.firstElementChild;
      // R86-I: 「名前の頭1文字」は同一プロジェクトの並走セッションで全部同じになる
      // （実測: 9体中6体が「制」）。区別がつく末尾から作ったバッジを使う。
      if (monoEl) monoEl.textContent = privacy ? privateBadge(a) : a.badge || "?";
      chip.title = privacy ? `${privateBadge(a)} ${txt}` : a.crew > 1 ? `${a.name} ×${a.crew}` : a.name;
      chip.dataset.w = "";                                    // テキスト変化＝寸法キャッシュ無効化
    }
    if (!frozen) {
      if (paintArrivalBadge(chip, arrival)) chip.dataset.w = "";
      const value = stream?.enabled || shell.classList.contains("replay-active") ? null : shell._growth?.label(a);
      let level = chip.querySelector(".label-level");
      if (value && !level) {
        level = el("span", "label-level"); chip.append(level); chip.dataset.w = "";
      }
      if (value) {
        if (level.textContent !== value.level) { level.textContent = value.level; chip.dataset.w = ""; }
      } else if (level) { level.remove(); chip.dataset.w = ""; }
      chip.title = privacy ? `${privateBadge(a)} ${txt}`
        : [a.crew > 1 ? `${a.name} ×${a.crew}` : a.name, value?.text].filter(Boolean).join("\n");
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
    // 足元チップ同士の軽い重なりだけ逃がす（頭上スタックの塔は作らない）。
    // R86-C: **下限を先に決めてから**衝突解決する。解消の後にクランプを掛けると全員が
    // 下限へ押し戻されて重なる（実測: 潜り4枚が重なり10ペアに化けた。この関数の掟どおり）。
    // 下限に当たった後は上へ逃がし、一度上へ逃げたら下へ戻さない（戻すと2値間で振動する）。
    const hardB = limitB > ch + 4 ? limitB - ch : Infinity;
    let top = Math.max(2, Math.min(at.top + 6, hardB));
    let up = false;
    const box = () => ({ l: left - half, r: left + half, t: top, b: top + ch });
    for (let guard = 0; guard < 8; guard++) {
      const me = box();
      const hit = placed.find((q) =>
        me.l < q.r + 4 && me.r > q.l - 4 && me.t < q.b + 3 && me.b > q.t - 3);
      if (!hit) break;
      if (!up && hit.b + 3 <= hardB) top = hit.b + 3;
      else { up = true; top = Math.max(2, hit.t - ch - 3); }
    }
    const leftPx = `${left}px`;
    const topPx = `${top}px`;
    if (chip.style.left !== leftPx) chip.style.left = leftPx;
    if (chip.style.top !== topPx) chip.style.top = topPx;
    placed.push(box());
  }
  for (const leftover of oldChips.values()) leftover.remove();
}

/** Project names stay in HTML; the wooden boards contain no rasterized text. */
function paintProjectSigns(shell, scene, world) {
  const host = shell.querySelector("#project-labels");
  if (!host || typeof scene.project !== "function") return;
  const old = new Map([...host.children].map((n) => [Number(n.dataset.pod), n]));
  const projects = new Map();
  for (const a of world.agents) {
    const seat = world.seats.get(a.id);
    if (seat === undefined) continue;
    const pod = Math.floor(seat / DEFAULT_SPEC.desks.seatsPerPod);
    if (!projects.has(pod)) projects.set(pod, new Map());
    projects.get(pod).set(a.projectKey || a.id, a.projectName || a.name);
  }
  for (const anchor of scene.projectSignAnchors()) {
    const names = projects.get(anchor.pod);
    if (!names) continue;
    const p = scene.project(anchor.x, anchor.y, anchor.z);
    if (!Number.isFinite(p.left) || !Number.isFinite(p.top)) continue;
    let label = old.get(anchor.pod);
    if (!label) {
      label = document.createElement("span"); label.className = "island-label";
      label.dataset.pod = String(anchor.pod); host.append(label);
    }
    old.delete(anchor.pod);
    // R90: 島の看板は「その島に何のプロジェクトが座っているか」を示すもの。
    // 1プロジェクト1セッションだと、その人の名札が同じ文字を出しているので**二重に読める**
    // （デモGIFで実際に「Release Checks」が名札と看板の2箇所に出た）。
    // 島に複数プロジェクトが混ざっているときだけ看板を出す＝看板が意味を持つときだけ描く。
    if (names.size < 2) { label.remove(); continue; }
    const title = [...names.values()].join(" · ");
    if (label.textContent !== title) label.textContent = title;
    label.style.left = `${p.left}px`; label.style.top = `${p.top}px`;
  }
  for (const node of old.values()) node.remove();
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function paintArrivalBadge(host, arrival) {
  let badge = host.querySelector(".arrival-badge");
  const text = [arrival?.isNew ? T("hire_new_badge") : "",
    arrival?.slug ? T("hire_branch_badge", arrival.slug) : ""].filter(Boolean).join(" ");
  if (!text) { if (badge) { badge.remove(); return true; } return false; }
  if (!badge) { badge = el("span", "arrival-badge"); host.append(badge); }
  badge.title = text;
  if (badge.textContent === text) return false;
  badge.textContent = text;
  return true;
}

/** 言語で変わる静的クローム（テンプレート直書きだった部分）。mount と言語切替時に貼る。 */
function applyStaticStrings(shell) {
  shell.querySelector("#gtitle-credits").textContent = T("gauge_credits");
  shell.querySelector("#gtitle-money").textContent = T("gauge_money");
  shell.querySelector("#btn-newproj").textContent = T("btn_newproj");
  const hireButton = shell.querySelector("#btn-hire");
  if (hireButton) hireButton.textContent = T("btn_hire");
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
  const no3dList = shell.querySelector("#no3d-list");
  if (no3dList) no3dList.textContent = T("no3d_list");
  shell.querySelector("#title-tasks").textContent = T("card_tasks");
  shell.querySelector("#title-hist").textContent = T("card_hist");
  shell.querySelector("#title-agents").textContent = T("board_title");
  shell.querySelector("#gauges").title = T("gauges_title");
}

function render(shell, w, { tray, sheet, board }) {
  const z = w.counts;
  // Missing recipe metadata is unknown (older servers/fixtures), not a confirmed empty list.
  shell.querySelector("#btn-run").hidden = Array.isArray(w.actions?.recipes) && w.actions.recipes.length === 0;
  shell._traySel = tray.render(w);           // 足元チップの強調用（paintLabels が読む）

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
  const sessions = board.flatMap((group) => group.sessions);
  shell.querySelector("#sub").textContent = T("board_summary", sessions.length,
    sessions.filter((a) => a.state === "working" && !a.attention).length,
    sessions.filter((a) => a.attention).length,
    sessions.filter((a) => a.state === "resting" && !a.attention).length);

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
  if (!DEMO && w.setup && (!frozen || w.setup.hookInstalled === false)) {
    if (!setupBar) {
      setupBar = el("div", "setupbar");
      setupBar.id = "setupbar";
      shell.querySelector(".main").insertBefore(setupBar, shell.querySelector("#stage"));
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
    if (!frozen) {
      if (!setupBar.querySelector(".setup-checklist")) {
        const checklist = el("div", "setup-checklist");
        checklist.append(el("b", "setup-title"), el("span", "setup-hooks"), el("span", "setup-events"));
        setupBar.prepend(checklist);
      }
      const hook = w.setup.hookInstalled === true;
      const events = w.setup.eventsWired === true;
      setupBar.querySelector(".setup-title").textContent = T("setup_checklist");
      setupBar.querySelector(".setup-hooks").textContent = T(hook ? "setup_hooks_ready" : "setup_hooks_pending");
      setupBar.querySelector(".setup-events").textContent = T(events ? "setup_events_ready"
        : w.setup.eventsWired === false ? "setup_events_pending" : "setup_events_unknown");
      setupBar.classList.toggle("setup-complete", hook && events);
      setupBar.querySelector(".sb-msg").hidden = hook;
      setupBar.querySelector(".sb-cmd").hidden = hook && events;
      setupBar.querySelector(".sb-copy").hidden = hook && events;
    }
    setupBar.querySelector(".sb-msg").textContent = T("setup_hook");
    setupBar.querySelector(".sb-cmd").textContent = T("setup_hook_cmd");
    setupBar.querySelector(".sb-copy").textContent = T("setup_hook_copy");
  } else if (setupBar) {
    setupBar.remove();
  }

  // ── 左: 部署→セッション。既存行を再利用し、クリック中のdetachを避ける ──
  const agents = shell.querySelector("#agents");
  const selectedId = sheet.selectedId();
  agents.querySelector(".onboard")?.remove();
  const oldGroups = new Map([...agents.querySelectorAll(".department")]
    .map((n) => [n._groupKey, n]));
  const oldRows = new Map([...agents.querySelectorAll(".arow")]
    .map((n) => [`${n.dataset.project}/${n.dataset.session}`, n]));
  const setText = (node, text) => {
    if (node.textContent !== text) { node.textContent = text; return true; }
    return false;
  };
  let groupCursor = agents.firstElementChild;
  for (const group of board) {
    let department = oldGroups.get(group.key);
    if (!department) {
      department = el("section", "department");
      department._groupKey = group.key; // cwdはDOM属性や表示へ搬送しない
      const head = el("div", "department-head");
      head.append(el("span", "vendors"), el("b", "department-name"),
        el("span", "department-state"), el("span", "department-count"));
      department.append(head, el("div", "department-sessions"));
    }
    oldGroups.delete(group.key);
    if (groupCursor === department) groupCursor = groupCursor.nextElementSibling;
    else agents.insertBefore(department, groupCursor);
    const state = group.sessions.some((a) => a.attention) ? "attention"
      : group.sessions.some((a) => a.state === "working") ? "working"
      : group.sessions.every((a) => a.state === "resting") ? "resting" : "waiting";
    department.className = `department st-${state}`;
    setText(department.querySelector(".department-name"), group.name);
    department.querySelector(".department-name").title = group.name;
    setText(department.querySelector(".department-state"), T(`board_${state}`));
    setText(department.querySelector(".department-count"), T("board_count", group.sessions.length));
    const vendors = department.querySelector(".vendors");
    const vendorList = [...new Set(group.sessions.map((a) => a.vendor))];
    if (vendors.dataset.vendors !== vendorList.join(",")) {
      vendors.replaceChildren(...vendorList.map((vendor) => {
        const dot = el("i", `vendor-dot vendor-${vendor}`);
        dot.title = T(`board_vendor_${vendor}`);
        dot.setAttribute("aria-label", dot.title);
        return dot;
      }));
      vendors.dataset.vendors = vendorList.join(",");
    }
    const host = department.querySelector(".department-sessions");
    let cursor = host.firstElementChild;
    for (const a of group.sessions) {
      const key = `${a.id}/${a.session}`;
      let row = oldRows.get(key);
      const isNew = !row;
      if (!row) {
        row = el("div", "arow");
        row.tabIndex = 0;
        row.setAttribute("role", "button");
        const head = el("div", "arowhead");
        head.append(el("i", "adot"), el("b", "aname"), el("span", "acrew"),
          el("span", "apend", "📨"), el("span", "amute", "📴"));
        const act = el("div", "aact");
        act.append(el("span", "atext"));
        const prog = el("div", "aprog");
        const track = el("div", "abar");
        track.append(el("i", "afill"));
        prog.append(track, el("span", "apct"));
        row.append(head, act, el("i", "aage"), el("span", "alevel"), prog);
      }
      oldRows.delete(key);
      if (cursor === row) cursor = cursor.nextElementSibling;
      else host.insertBefore(row, cursor);
      row.dataset.session = a.session;
      row.dataset.project = a.id;
      row.dataset.zone = a.zone;
      if (!frozen) paintArrivalBadge(row.querySelector(".arowhead"), shell._arrivals?.label(a.session));
      const cls = `arow st-${a.state} zone-${a.zone}${a.id === selectedId ? " sel" : ""}`;
      if (row.className.replace(" fresh", "") !== cls) row.className = cls;
      let changed = setText(row.querySelector(".aname"), a.name || "?");
      row.title = [a.name, a.detail, T(`board_${a.attention ? "attention" : a.state === "working"
        ? "working" : a.state === "resting" ? "resting" : "waiting"}`)].filter(Boolean).join(" · ");
      if (!frozen) {
        const value = shell._growth?.label(a);
        if (value) row.title += `\n${value.text}`;
      }
      row.querySelector(".acrew").hidden = true; // 件数は親のプロジェクト行へ集約
      row.querySelector(".apend").hidden = !a.pending;
      const muteEl = row.querySelector(".amute");
      muteEl.hidden = !isMuted(a);
      if (!muteEl.hidden) muteEl.title = T("listen_off_hint");
      const act = row.querySelector(".aact");
      act.className = "aact" + (a.attention && a.approvalMin >= STARVE_MIN ? " starve" : "");
      changed = setText(act.querySelector(".atext"), a.detail || T("board_no_detail")) || changed;
      setText(row.querySelector(".aage"), agoStr(a.age, w.lang));
      setText(row.querySelector(".alevel"), T("board_level", a.level ?? "—"));
      const c = a.work?.counts || {};
      const done = Number(c.completed) || 0;
      const total = done + (Number(c.in_progress ?? c.inProgress) || 0) + (Number(c.pending) || 0);
      const prog = row.querySelector(".aprog");
      prog.hidden = !(total > 0);
      if (total > 0) {
        prog.querySelector(".afill").style.width = `${Math.round(done / total * 100)}%`;
        setText(prog.querySelector(".apct"), `${done}/${total}`);
      }
      if (changed && !isNew && !frozen) {
        row.classList.add("fresh");
        setTimeout(() => row.classList.remove("fresh"), 120);
      }
    }
  }
  for (const leftover of oldRows.values()) leftover.remove();
  for (const leftover of oldGroups.values()) leftover.remove();
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
  // R86-I: タスクが1件も無いときドーナツは「0のリング」＝永久に空のパネルになる
  // （実測: どのセッションもタスク管理ツールを使っておらず常に 0/0/0 だった）。
  // 空のときは実データで作った「今日のオフィス」に差し替える＝死んだ面積を作らない。
  renderBottomLeft(shell, w);
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

/** 受信済みの本文・ベンダー・成長値をHUD専用の2階層へ。core/3Dのworldは変更しない。 */
function departmentBoard(office, w) {
  const raw = new Map((office.roster || []).map((p) => [p.projectId || p.session, p]));
  const employees = new Map((office.employees || []).map((p) => [p.session, p]));
  const groups = new Map();
  for (const a of w.agents) {
    const p = raw.get(a.id) || employees.get(a.session) || {};
    const name = p.name || p.dept || a.dept || a.name;
    const key = a.external ? a.id : p.cwd || name || a.id;
    if (!groups.has(key)) groups.set(key, { key, name, sessions: [] });
    const members = a.sessions.length ? a.sessions : [{ session: a.session }];
    members.forEach((brief, i) => {
      const lead = brief.session === a.session;
      const member = employees.get(brief.session) || {};
      const data = { ...(lead ? p : {}), ...brief, ...member };
      const model = buildWorld({ employees: [data] }).agents[0];
      const vendor = String(data.vendor || p.vendor || (a.external ? "openclaw" : "claude")).toLowerCase();
      const detail = data.detail || data.bg?.detail || model.question
        || (model.attention ? T("approval_min", model.approvalMin) : "")
        || activityGloss(model, w.lang) || zoneLabel(model.zone);
      // detailは40 Unicode文字。カテゴリ由来の絵文字を箇条書きの印にしない。
      const plainDetail = String(detail).replace(/^[\p{Extended_Pictographic}\uFE0F\s]+/u, "");
      const level = data.level ?? office.growth?.byProject?.[a.id]?.level;
      groups.get(key).sessions.push({ ...model, id: a.id, session: brief.session,
        name: data.title || member.name || brief.name
          || (members.length === 1 ? a.name : T("board_session", i + 1)),
        vendor: ["claude", "codex", "openclaw"].includes(vendor) ? vendor : "other",
        detail: tidyActivity(plainDetail, 40),
        level: typeof level === "number" && Number.isFinite(level) ? level : null });
    });
  }
  return [...groups.values()];
}

/** 左下カード: タスクがあればドーナツ、無ければ「今日のオフィス」。 */
function renderBottomLeft(shell, w) {
  const t = w.tasks || { pending: 0, inProgress: 0, completed: 0 };
  const total = (t.pending || 0) + (t.inProgress || 0) + (t.completed || 0);
  const donutWrap = shell.querySelector(".donutwrap");
  const legend = shell.querySelector("#donutlegend");
  let digest = shell.querySelector("#todaycard");
  if (total > 0) {
    donutWrap.hidden = false;
    legend.hidden = false;
    if (digest) digest.hidden = true;
    shell.querySelector("#title-tasks").textContent = T("card_tasks");
    renderDonut(shell, t);
    return;
  }
  donutWrap.hidden = true;
  legend.hidden = true;
  shell.querySelector("#title-tasks").textContent = T("card_today");
  if (!digest) {
    digest = el("div", "todaycard");
    digest.id = "todaycard";
    shell.querySelector(".donutcard").append(digest);
  }
  digest.hidden = false;
  const agents = w.agents || [];
  const n = (f) => agents.filter(f).length;
  const rows = [
    ["", T("today_sent"), String(w.today?.sent ?? 0) + T("unit_items")],
    ["", T("today_attn"), String(n((a) => a.attention)) + T("unit_items")],
    ["", T("today_working"), String(n((a) => a.state === "working")) + T("unit_people")],
    ["", T("today_resting"), String(n((a) => a.state === "resting")) + T("unit_people")],
  ];
  digest.replaceChildren();
  for (const [icon, label, value] of rows) {
    const row = el("div", "trow");
    row.append(el("i", "ticon", icon), el("span", "tlabel", label), el("b", "tval", value));
    digest.append(row);
  }
  const ago = w.today?.lastSentAgo;
  digest.append(el("div", "tnote", ago == null ? T("today_none") : T("today_last", agoStr(ago))));
}

/** タスクのドーナツ（SVG・実データのみ・アニメ無し＝golden を揺らさない）。 */
function renderDonut(shell, tasks) {
  const svg = shell.querySelector("#donut");
  const total = tasks.pending + tasks.inProgress + tasks.completed;
  const C = 2 * Math.PI * 40;
  const segs = [
    ["var(--iso-accent)", tasks.completed],
    ["var(--iso-warn)", tasks.inProgress],
    ["var(--iso-rest)", tasks.pending],
  ];
  let acc = 0;
  svg.replaceChildren();
  const ns = "http://www.w3.org/2000/svg";
  for (const [color, value] of segs) {
    // タスク0件のときは「未着手」色の1周＝空のドーナツとして描く
    const frac = total ? value / total : (color === "var(--iso-rest)" ? 1 : 0);
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
    [T("leg_done"), tasks.completed, "var(--iso-accent)"],
    [T("leg_prog"), tasks.inProgress, "var(--iso-warn)"],
    [T("leg_todo"), tasks.pending, "var(--iso-rest)"],
  ]) {
    const row = el("div", "lrow");
    const dot = el("i", "ldot");
    dot.style.background = color;
    row.append(dot, el("span", "", label), el("b", "", String(value)));
    legend.append(row);
  }
}
