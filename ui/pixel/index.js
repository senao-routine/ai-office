// ── 様式 pixel: フロア帯 × 台帳（R98）──────────────────────────────
// 主= 台帳（1 画面で全員を読める密度）。従= 上のフロア帯（W2: ui/pixel/strip.js・W1 は畳む）。
// 操作系（❗トレイ・シート・送信・管理・ゲージ・雇う・初回体験・ダイジェスト）は ui/hud を iso と共有する。
// 3D は持たない＝scene は「何もしないシーン」。数値は全部 world（実データ）から。
import { buildWorld, summarizeWorld } from "/ui/core/world.js";
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
import { init as initDigest } from "/ui/hud/digest.js";
import { init as initGrowth } from "/ui/hud/growth.js";
import { init as initHire } from "/ui/hud/hire.js";
import { init as initFirstRun } from "/ui/hud/firstrun.js";
import { applyStaticStrings, el, initHelp, initUsage } from "/ui/hud/shell.js";
import { attnKeyFor, boardFromWorld, departmentBoard, renderChrome, renderHistory } from "/ui/hud/board.js";
import { init as initSession } from "/ui/hud/session.js";
import { T, lang, setLang } from "/ui/hud/strings.js";
import { applyPixelStrings, template } from "./shell.js";
import { init as initTable } from "./table.js";

export const STYLE = STYLES.PIXEL;

const DEMO = new URLSearchParams(
  typeof location === "undefined" ? "" : location.search).get("demo") === "1"
  || (typeof document !== "undefined"
    && document.querySelector('meta[name="office-demo"]')?.getAttribute("content") === "1");

/** 3D を持たない様式の scene（hud の部品が要求する契約だけ満たす） */
const nullScene = (table) => ({
  ready: () => true, update() {}, resize() {}, dispose() {},
  pickAgent: () => null, projectAgent: () => null, project: () => null, projectBoss: () => null,
  labelAnchorFor: () => null, focusOn() {}, focusOff() {},
  stats: () => ({ drawCalls: 0, materials: 0, robots: 0, rows: table.rows() }),
});

export async function mount(root) {
  root.replaceChildren();
  root.className = frozen ? "hud ui-pixel no-anim" : "hud ui-pixel";
  const shell = document.createElement("div");
  shell.className = "pxshell";
  shell.innerHTML = template();
  root.append(shell);
  const strings = () => { applyStaticStrings(shell, T); applyPixelStrings(shell, T); };
  strings();
  for (const href of ["/ui/hud/hud.css", "/ui/pixel/style.css"]) {
    const css = document.createElement("link");
    css.rel = "stylesheet"; css.href = href;
    await new Promise((res) => { css.onload = res; css.onerror = res; document.head.append(css); });
  }
  const { paintPins } = initUsage({ shell, T });

  let world = null;
  let built = null;
  let digest = null;
  let freshShown = -1;
  const arrivals = createArrivals({ isolated: frozen, demo: DEMO });
  shell._arrivals = arrivals;
  const freshEl = shell.querySelector("#freshness");
  // リプレイ（留守中のまとめ → 過去の状態の再生）: 3D の「舞台」に当たるのは台帳＝キーフレームが
  // 変わったときだけ表を描き直す（毎フレーム departmentBoard を回さない）。終わったら live に戻す。
  let replayWorld = null;
  let replayKeys = null;      // 再生開始時に固定する（再生中の live 更新を過去へ混ぜない）
  const draw = (t) => {
    if (!built) return;
    const frame = digest?.frame(t);
    if (frame) {
      if (!replayKeys) replayKeys = liveKeys();   // 開始時の「誰が居たか」を 1 回だけ写す
      if (frame.world !== replayWorld) {
        replayWorld = frame.world;
        // 行は**そのフレームの世界だけ**から作る（live の roster で状態を上書きしない）。
        // プロジェクト分け・名前・ベンダーだけ live から借りる（過去の絵の「誰が」は変わらない）。
        table.render(frame.world, boardFromWorld(frame.world, T, replayKeys, frame.eventSids));
      }
    } else if (replayWorld || replayKeys) {
      replayWorld = null; replayKeys = null;
      table.render(built);
    }
    firstrun.paint(t);
    const s = frozen ? null : session.dataAge(t);
    if (s !== null && s !== freshShown) {
      freshShown = s;
      freshEl.textContent = T("updated_ago", s);
      freshEl.hidden = false;
    }
  };
  // 再生中は台帳がリプレイの面＝live のデータで上書きしない（ポーリングは止めない）。
  const render = (w, { table: withTable = true } = {}) => {
    renderChrome(shell, w, T, tray);
    // プロジェクト数は台帳のグループ数（サーバー既定の avatarMode=session では w.agents はセッション単位）
    shell.querySelector("#pxcounts").textContent = T("px_counts", hud.board.length,
      hud.board.reduce((n, g) => n + g.sessions.length, 0));
    if (withTable) table.render(w);
    renderHistory(shell, w, T, { limit: 3 });
    shell.querySelector("#pxtoday").textContent = T("px_today", w.today?.sent ?? 0, w.today?.answered ?? 0);
  };
  const apply = (office) => {
    world = office;
    built = buildWorld(office);
    arrivals.update(office);
    if (built.lang !== lang()) { setLang(built.lang); strings(); }
    session.markData();
    delivery.update(built);
    hud.board = departmentBoard(office, built, T);
    gauges.start();
    render(built, { table: !replayWorld });
    firstrun.update(built);
    session.applyHashOnce();
    growth.update();
    sheet.refreshGrowth();
    digest?.update();
    draw(now());
  };

  const getWorld = () => built;
  /** session → {key, name, vendor}（リプレイの行を live と同じプロジェクトへ畳むため） */
  const liveKeys = () => new Map(hud.board.flatMap(
    (g) => g.sessions.map((s) => [s.session, {
      key: g.key, name: g.name, sessionName: s.name, vendor: s.vendor,
      // イベントの無いセッションを再生の台帳へ戻すときの素（状態と経過だけ・本文は持たない）
      // 本人の ❗は保つ（代表の質問文は配らないが、非代表自身の待ちは消さない）
      // 配達の状態（📨 pending）は「いまの事実」＝過去の行には運ばない（他の再生行と揃える）
      brief: { state: s.state, age: s.age, minions: s.minions, pending: false,
        attention: Boolean(s.attention), approvalMin: Number(s.approvalMin) || 0 },
    }])));
  const repaint = () => { if (built) render(built, { table: !replayWorld }); };
  const common = { shell, T, getWorld, render: repaint };
  const modals = initModals({ shell });
  const delivery = initSend({ ...common, DEMO, attnKeyFor });
  const growth = initGrowth({ ...common, getGrowth: () => world?.growth, showToast: delivery.showToast });
  shell._growth = growth;
  const dialog = initDialog({ T, el, modals });
  const sheet = initSheet({
    ...common, lang, DEMO, attnKeyFor, delivery, dialog,
    focusOn() {}, focusOff() {},
    getTemplates: () => admin.getTemplates(),
    openTemplateEditor: (text) => admin.openTemplateEditor(text),
    paintGrowth: growth.paintSheet,
    openCustomize() {},                       // 3D の殻色はこの様式に無い（🎨 は hidden）
  });
  const { openCompose, jumpTerminal } = sheet;
  const tray = initTray({ ...common, el, attnKeyFor, delivery, sheet, modals, DEMO });
  const hud = { tray, sheet, board: [] };
  const table = initTable({
    shell, T, el, getWorld, getBoard: () => hud.board, selectedId: sheet.selectedId, arrivals,
    // 再生中は「いまの配達が止まっている」も出さない（過去の行に現在の事実を貼らない）
    stalled: (s) => !replayWorld && delivery.isStalled(s),
    onPick: (projectId, sessionId) => {
      const a = (built?.agents || []).find((x) => x.id === projectId);
      if (!a) return;
      const member = hud.board.flatMap((g) => g.sessions).find((m) => m.id === a.id && m.session === sessionId);
      // 非代表も自身の会話・質問・宛先で開く（iso の右レールと同じ規則）
      if (member && member.session !== a.session) openCompose({ ...member, crew: 1, sessions: [] });
      else openCompose(a);
    },
  });
  const scene = nullScene(table);
  const firstrun = initFirstRun({ shell, T, scene, DEMO, enabled: true });
  const openHelp = initHelp({ shell, T, modals,
    // 再生中とダイジェスト前面では開かない（ヘルプの裏で数字キーが生き、Escape も取り合う）
    blocked: () => shell.classList.contains("replay-active")
      || shell.querySelector("#digest-card")?.hidden === false });
  const onHudKey = (e) => {
    if (e.target.closest?.("input, textarea, select, [contenteditable=true]")) return;
    if (e.key === "?" && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); openHelp(); }
  };
  window.addEventListener("keydown", onHudKey, true);
  digest = initDigest({ ...common, DEMO, tray, showToast: delivery.showToast, enabled: true, canvas: null,
    host: shell.querySelector(".pxbody"),      // #stage は W1 では畳んでいる＝カードは台帳の面に出す
    replay: true,                              // draw が digest.frame(t) を消費する（上）
    beforeOpen: () => { modals.closeModal(); sheet.closeCompose(); }, restore: () => draw(now()) });
  shell._closeOverlays = () => digest?.close?.();
  shell.querySelector("#agents").addEventListener("dblclick", (e) => {
    const row = e.target.closest(".pxrow");
    const a = row && (built?.agents || []).find((x) => x.id === row.dataset.project);
    if (a && !a.external) jumpTerminal(row.dataset.session || a.session, a.name);
  });
  const admin = initAdmin({
    demo: DEMO,
    reapplyWorld: () => { if (world) apply({ ...world, lang: lang() }); },
    ...common, root, lang, setLang, modals, billingOf, fmtTok,
    showToast: delivery.showToast, applyStaticStrings: strings,
  });
  const hire = initHire({ ...common, lang, DEMO, modals, arrivals,
    refresh: () => session.refresh(), showToast: delivery.showToast });
  shell._fx = { wakeActive: delivery.wakeActive, stalled: delivery.isStalled, hoverId: null };
  const session = initSession({ root, shell, T, DEMO, apply, tray, getWorld: () => world,
    onOffline: () => { repaint(); sheet.refreshGrowth(); } });
  const gauges = initGauges({ ...common, lang, onPins: paintPins, demo: DEMO });
  if (DEMO) await session.loadDemo();
  const stopLoop = loop(draw);
  const uninstall = installProbe({
    style: STYLE,
    t: () => now(),
    isReady: () => world !== null,
    dumpWorld: () => summarizeWorld(world),
    inject: apply,
    stats: () => scene.stats(),
    pollMs: () => session.intervalMs(),
    debug: { agentPoint: (id) => table.point(id), rows: () => table.rows() },
  });
  return () => {
    session.dispose(); stopLoop(); uninstall();
    window.removeEventListener("keydown", onHudKey, true);
    hire.dispose(); firstrun.dispose();
    digest.dispose(); tray.dispose(); sheet.dispose(); delivery.dispose(); gauges.dispose();
    document.title = "AI Office";
  };
}
