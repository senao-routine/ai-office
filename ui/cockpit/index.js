// ── 様式 cockpit: 俯瞰のドット絵オフィス × 台帳 × 端末 × 一覧（R99）─────────────
//
// 本人の要望（2026-09-22）: 「ターミナルと AI オフィスが合体したようなベース」
// 「画面とターミナルのセッションが両方開いている状態」。モックアップの読み方は
// **ピクセルはオフィスとロボだけ・周りは落ち着いた開発者 UI**（全画面ゲームにしない）。
//
// 中央は R121 の作業ビュー。本物の pty は［ターミナル］で切替（分割ペインでは従来どおり端末）。
//
// 操作系（❗トレイ・シート・送信・管理・ゲージ・雇う・初回体験・ダイジェスト）は ui/hud を
// iso / pixel と共有する。3D は持たない＝scene は「何もしないシーン」。
import { activityGloss, agoStr, attentionQueue, buildWorld, summarizeWorld } from "/ui/core/world.js";
import { createRoomLighting, epochNow, frozen, loop, now, setTime } from "/ui/platform/clock.js";
import { getActivity } from "/ui/platform/api.js";
import { createArrivals } from "/ui/platform/arrivals.js";
import { installProbe } from "/ui/platform/probe.js";
import { STYLES } from "/ui/platform/style.js";
import { ROOM, ROOM_SCALE_MAX, ROOM_SCALE_MIN, ROOM_SCALE_WATCH_MAX, SEATS, roomScale, roomWorldFields, tintHash, tintIndex }
  from "/ui/core/pxroom.js";
import { ROOM_FPS } from "/ui/core/pxwalk.js";
import { DEFAULT_LAYOUT, GUTTER, PARTNER_MIN_H, ROOM_CHROME_W, TERM_MIN_COLS, TERM_MIN_W_DEFAULT, TERM_MIN_W_FLOOR, flexColumn,
  gridTemplate, isDefaultLayout, normalizeLayout, parseLayout, roomAvail, serializeLayout, stackedColumn } from "/ui/core/cplayout.js";
import { EVIDENCE_ERRAND, toolClassOf } from "/ui/core/errand.js";
import { placePlates } from "/ui/core/plates.js";
import { init as initSend } from "/ui/hud/send.js";
import { init as initSheet } from "/ui/hud/sheet.js";
import { init as initDialog } from "/ui/hud/dialog.js";
import { createActivityView, formatDuration } from "/ui/hud/activity.js";
import { activityMotionSignature, activityPaintKey, botRecordDue, samePaint } from "/ui/hud/activity_feed.js";
import { init as initAdmin } from "/ui/hud/admin.js";
import { init as initGauges, billingOf, fmtTok } from "/ui/hud/gauges.js";
import { answerActs, init as initTray } from "/ui/hud/tray.js";
import { init as initModals } from "/ui/hud/modals.js";
import { init as initDigest } from "/ui/hud/digest.js";
import { init as initGrowth } from "/ui/hud/growth.js";
import { init as initHire } from "/ui/hud/hire.js";
import { init as initFirstRun } from "/ui/hud/firstrun.js";
import { init as initTable } from "/ui/hud/table.js";
import { init as initBeta } from "/ui/hud/beta.js";
import { applyStaticStrings, el, initHelp, initUsage } from "/ui/hud/shell.js";
import { attnKeyFor, attnLine, boardFromWorld, departmentBoard, mutedSessions, renderChrome, renderHistory } from "/ui/hud/board.js";
import { init as initSession } from "/ui/hud/session.js";
import { T as rawT, lang, setLang } from "/ui/hud/strings.js";
import { ATTN_KINDS, attnKind, errOf, isBot, permModeOf } from "/ui/core/kind.js";
import { createAmbient, createSoundNotifications, markAsked, playSound, setSound, soundOn, unlockSound } from "/ui/platform/sound.js";
import * as panes from "/ui/core/termpanes.js";
import { applyCockpitStrings, template } from "./shell.js";
import { init as initRoom } from "./room.js";
import { init as initTerm } from "./term.js";
import { initLayoutUi } from "./layout.js";
import { init as initPanes } from "./panes.js";
import { SESSION_COLORS } from "./palette.js";
import { plainT, plainText } from "./plain.js";
import { createPressHold } from "/ui/hud/press_hold.js";

export const STYLE = STYLES.COCKPIT;

// b4-mac: hud の部品へ渡す T は絵文字を落とした T（./plain.js・iso / pixel は従来の T のまま＝iso の golden は変わらない）
const T = plainT(rawT);

const DEMO = new URLSearchParams(
  typeof location === "undefined" ? "" : location.search).get("demo") === "1"
  || (typeof document !== "undefined"
    && document.querySelector('meta[name="office-demo"]')?.getAttribute("content") === "1");

/**
 * 個体色の割り当て（プロジェクトのキー → SESSION_COLORS の添字）。**ここが唯一の持ち主**:
 * 部屋（room.js へ colorOf として渡す）・タブ・一覧・端末の印が同じ 1 本を読む。
 * 台帳（hud.board）に並んでいるプロジェクトの集合から tintIndex で衝突を解く（9 件までは重ならない）。
 * live は前回の割り当てを tintIndex に渡し続ける＝新しいプロジェクトが来ても、表示中の人の色は変わらない
 *（色が黙って入れ替わると、誰がどれかが入れ替わって見える）。固定時刻（?t=）は前回を持たない＝
 * 同じ world なら必ず同じ色（golden）。集合の外のキー（再生にだけ居たプロジェクト）はハッシュの剰余のまま。
 */
let tints = new Map();
function tintFor(key) {
  const i = tints.get(String(key));
  return SESSION_COLORS[i ?? tintHash(key) % SESSION_COLORS.length];
}

/**
 * 部屋の倍率は幅と実際の窓の丈から選ぶ。上限 480px（×3）にし、接続案内や低い窓でも
 * 部屋全体が見える整数倍にする。描画の内容は変えない。
 */
const ROOM_AVAIL_H = 480;
/** R105: 多段で部屋の倍率を選ぶとき、台帳に残す最低の丈。R110: 2 カラムの名簿（左の列の下）だけが使う＝詰めた行 2 つ
 *（R110-D1・style.css の --cp-roster-min の行の分） */
const LEDGER_MIN_H = 120;
// R110 骨格 A の 3 カラムの端末の列に残す最低の幅（旧 CENTER_MIN_W＝560＝80 桁・部屋の倍率をこれより先に取らない）は、R111-A2 から
// ui/core/cplayout.js の TERM_MIN_W_DEFAULT が正本（境目のドラッグで 40 桁の実測まで下げた値は配置の widths.termMin＝termMinNow）。
/** R110 骨格 A: 部屋の下の受付（04）に残す最低の丈。3 カラムは 200（受付を 120 に詰めれば ×4 が入る丈だけ 120＝RECEPTION_MIN_H_TIGHT）＝
 *  R111-A2 から正本は ui/core/cplayout.js の PARTNER_MIN_H（roomAvail が配置に従って部屋の相方の最低を選ぶ・相方が名簿なら 120・端末なら 200）。
 *  2 カラムは 140（R110-D1・名簿の 2 行を先に残す・ここが持つ） */
const RECEPTION_MIN_H_2COL = 140;
/** 部屋の段の丈の上限（×4＝640） */
const ROOM_MAX_H = ROOM.h * ROOM_SCALE_MAX;
/** 左の列の幅のうち部屋の絵の外＝窓の罫（1×2）＋部屋の左右の余白（8×2）。3 カラムの --cp-left-w は 184k ＋ これ（×3＝570・×4＝754）。
 *  style.css の `.cpwin` の罫と `.cpstage .viewport` の余白を変えたら、cplayout.js の ROOM_CHROME_W（正本）を変える */
const LEFT_CHROME = ROOM_CHROME_W;
/** R111-A2: 窓の配置の保存キー（既定なら鍵を消す・固定時刻とデモは読まない書かない＝テーマ・文字の大きさと同じ型） */
const LAYOUT_KEY = "aioffice.cockpit.layout";

/** 3D を持たない様式の scene（hud の部品が要求する契約だけ満たす） */
const nullScene = (table, room, fit) => ({
  ready: () => true, update() {}, resize: () => fit?.(), dispose() {},
  pickAgent: () => null, projectAgent: () => null, project: () => null, projectBoss: () => null,
  labelAnchorFor: () => null, focusOn() {}, focusOff() {},
  stats: () => ({ ...(room?.stats() || { drawCalls: 0, materials: 0 }), rows: table.rows() }),
});

export async function mount(root) {
  root.replaceChildren();
  root.className = frozen ? "hud ui-cockpit no-anim" : "hud ui-cockpit";
  const shell = document.createElement("div");
  shell.className = "cpshell";
  shell.innerHTML = template();
  root.append(shell);
  // 動きの状態は 1 つ（stopMotion）。OS の「視差効果を減らす」はその初期値と change に流し込む＝ボタンの文言と
  // aria-pressed が部屋の実際（止まっているか）と食い違わない。本人は押して OS の設定を上書きできる（design/cockpit.html の見本と同じ）。
  // 読むのはここだけ。reduceMotion は OS の今の値の控え（debug.roomMotion の reduced）で、部屋の時計には効かせない。
  const reducedMq = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
  let reduceMotion = Boolean(reducedMq?.matches);
  let stopMotion = reduceMotion;
  /** ボタンの文言と aria-pressed を stopMotion に合わせる（言語切替で文言を貼り直した後も）。
   *  固定時刻（?t=）のページは部屋の時計が動かない＝押しても何も変わらない。「動きを止める」と言わず
   *  「固定表示」で押せなくする（aria-pressed も止まっている側・design/cockpit.html の見本と同じ）。 */
  const paintMotion = () => {
    const b = shell.querySelector("#cp-motion");
    if (!b) return;
    b.disabled = frozen;
    b.setAttribute("aria-pressed", String(frozen || stopMotion));
    b.textContent = T(frozen ? "cp_motion_fixed" : stopMotion ? "cp_motion_start" : "cp_motion_stop");
  };
  /** R111-A2: 窓の配置の操作（./layout.js・作るのは倍率の節の後）。言語を替えたら帯の読み上げ（題名＋操作の案内）も貼り直す */
  let layoutUi = null;
  const strings = () => {
    root.dataset.cpLang = lang(); applyStaticStrings(shell, T); applyCockpitStrings(shell, T); paintMotion();
    layoutUi?.paintBands();
  };
  strings();
  // R101: 端末の配色のテーマ（既定はダーク＝OS に従わない・golden の決定論。?theme= で 1 回だけ上書き・保存しない）。
  // CSS を読む前に印を付ける（読み込み直後の 1 フレームを違う配色で描かない）。
  const THEME_KEY = "aioffice.theme";
  const themeParam = new URLSearchParams(location.search).get("theme");
  const readTheme = () => {
    if (["auto", "dark", "light"].includes(themeParam)) return themeParam;
    try { const v = localStorage.getItem(THEME_KEY); return v === "auto" || v === "light" ? v : "dark"; } catch { return "dark"; }
  };
  const applyTermTheme = (v, save = false) => {
    const d = document.documentElement;
    v = v === "auto" || v === "light" ? v : "dark";
    if (v === "auto") { d.removeAttribute("data-theme"); d.setAttribute("data-theme-auto", ""); }
    else { d.setAttribute("data-theme", v); d.removeAttribute("data-theme-auto"); }
    if (save) { try { if (v === "dark") localStorage.removeItem(THEME_KEY); else localStorage.setItem(THEME_KEY, v); } catch { /* 保存できない端末 */ } }
    window.dispatchEvent(new CustomEvent("aioffice-theme", { detail: v }));
  };
  applyTermTheme(readTheme());
  // R110: 文字の大きさ（⚙「文字の大きさ」12/13/14/16・既定 14）。保存は既定でないときだけ（テーマと同じ型＝既定は鍵を消す）。
  // 段の値（--cp-font-base/small/title/display）は style.css の [data-cp-font] が持つ＝ここは印を付けるだけ。
  // 端末（xterm）は CSS を読まない＝設定の行が termUi.setFont() で全端末へ配る（80 桁が入らない幅は term.js が base−2 へ下げる・
  // 端末は後で作る＝ここでは呼ばない）。
  // テーマと同じく CSS を読む前に印を付ける（読み込み直後の 1 フレームを違う大きさで組まない）
  const FONT_KEY = "aioffice.cockpit.font";
  const FONT_SIZES = [12, 13, 14, 16];
  const FONT_DEFAULT = 14;
  const fontOf = (v) => (FONT_SIZES.includes(Number(v)) ? Number(v) : FONT_DEFAULT);
  const readFont = () => { try { return fontOf(localStorage.getItem(FONT_KEY)); } catch { return FONT_DEFAULT; } };
  let cpFont = FONT_DEFAULT;
  const applyFont = (v, save = false) => {
    cpFont = fontOf(v);
    root.dataset.cpFont = String(cpFont);
    if (save) {
      try { if (cpFont === FONT_DEFAULT) localStorage.removeItem(FONT_KEY); else localStorage.setItem(FONT_KEY, String(cpFont)); }
      catch { /* 保存できない端末 */ }
    }
  };
  applyFont(readFont());
  // R111-A2: 窓の配置（3 カラムだけに効く・正本の純関数は ui/core/cplayout.js）。保存はテーマ・文字の大きさと同じ型（既定なら鍵を消す）。
  // 固定時刻（?t=）とデモは読まない・書かない＝golden と公開デモは常に既定の配置。壊れた保存（JSON でない・v 違い・未知の id・重複）は
  // parseLayout が既定へ戻す＝ここで鍵も消す（範囲外の数を丸めただけの値は正した形で書き直す）
  const readLayout = () => {
    if (frozen || DEMO) return DEFAULT_LAYOUT;
    try {
      const raw = localStorage.getItem(LAYOUT_KEY);
      const l = parseLayout(raw);
      const canon = serializeLayout(l);
      if (raw !== null && raw !== canon) {
        if (canon === null) localStorage.removeItem(LAYOUT_KEY);
        else localStorage.setItem(LAYOUT_KEY, canon);
      }
      return l;
    } catch { return DEFAULT_LAYOUT; }
  };
  const saveLayout = (l) => {
    if (frozen || DEMO) return;
    try {
      const s = serializeLayout(l);
      if (s === null) localStorage.removeItem(LAYOUT_KEY);
      else localStorage.setItem(LAYOUT_KEY, s);
    } catch { /* 保存できない端末 */ }
  };
  let layout = readLayout();
  for (const href of ["/ui/hud/hud.css", "/ui/vendor/xterm/xterm.css", "/ui/cockpit/style.css", "/ui/cockpit/icons.css"]) {
    const css = document.createElement("link");
    css.rel = "stylesheet"; css.href = href;
    await new Promise((res) => { css.onload = res; css.onerror = res; document.head.append(css); });
  }
  const { paintPins } = initUsage({ shell, T });

  let world = null;
  let built = null;
  let roomWorld = null;             // 部屋だけへ渡す err/minionsLive（DOM の world は変えない）
  let digest = null;
  let freshShown = -1;
  let selected = null;              // 中央の端末が映しているセッション（R111-B2: 選んでいるペインの葉の sel の写し＝書くのは commit だけ）
  let termUi = null;               // 本物の端末（mount の後半で作る。それまでは会話ビューだけ）
  let panesUi = null;              // R111-B2: 端末の窓の中のペイン（DOM・./panes.js）。デモには作らない
  const arrivals = createArrivals({ isolated: frozen, demo: DEMO });
  shell._arrivals = arrivals;
  const freshEl = shell.querySelector("#freshness");
  const q = (sel) => shell.querySelector(sel);
  // R110 骨格 A: 2 カラム（900〜1400）の「端末／一覧」の切替タブ（#cp-view-tabs）は撤去した＝名簿は左の列の下に常に出る
  const addMenu = q("#cp-add-menu");
  const closeAdd = () => { addMenu.hidden = true; q("#cp-add").setAttribute("aria-expanded", "false"); };
  q("#cp-add").addEventListener("click", () => {
    const open = addMenu.hidden;
    addMenu.hidden = !open;
    q("#cp-add").setAttribute("aria-expanded", String(open));
    if (open) addMenu.querySelector("button:not([hidden])")?.focus();
  });
  addMenu.addEventListener("click", (e) => { if (e.target.closest("button")) closeAdd(); });
  addMenu.addEventListener("keydown", (e) => {
    const items = [...addMenu.querySelectorAll("button:not([hidden]):not([disabled])")];
    if (e.key === "Escape") { closeAdd(); q("#cp-add").focus(); }
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      const index = items.indexOf(document.activeElement);
      items[e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (index + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
    }
  });
  shell.addEventListener("click", (e) => { if (!e.target.closest(".cpadd")) closeAdd(); });

  // ── 描画の刻み（C4）───────────────────────────────────────────────
  // 部屋は毎 rAF ではなく**刻み**で描く: 誰も歩いていなければ 6fps（design/README.md の約束）、歩いている人（入退場を含む）が
  // 居る間だけ 12fps（ui/core/pxwalk.js の ROOM_FPS＝12fps の描画 1 回で歩行コマがちょうど 1 つ進む）。時刻は刻みの格子に
  // 揃えてから渡す（t = floor(clock×fps)/fps）。格子の上の時刻は戻さない（12→6 に切り替わった直後の格子は前の時刻より
  // 手前になりうる＝そこは描かずに次の格子を待つ。時刻が戻ると帳簿が「未来の記録」と読んで歩行も ✓ も捨てる）。
  let shownT = -Infinity;           // 直近に描いた時刻（格子に揃えた値）
  let dirty = false;                // 同じ格子のうちに描き直す（ホバー・動きの設定の変更）
  let drawnSel = null;              // 直近に部屋へ渡した選択（端末が映しているセッション）

  let replayWorld = null;
  let replayKeys = null;
  let tableSel;                     // R110: 直近に名簿を描いたときの選択（roomSelection＝端末で選んでいる人のセッション）
  /** 名簿を描く（選んでいる人を控える＝選択だけが変わったときに描き直すかの判定に使う） */
  function renderTable(w, board) {
    tableSel = roomSelection();
    table.render(w, board);
    paintSince();                   // R110-S6: 行の「いま」に経過（table.js の DOM は変えない・.c-detail の末尾に足すだけ）
  }
  /** 選択だけが変わった（タブ・ロボ・＋・閉じる・端末の対応付け）ら名簿の印を合わせる。固定時刻のページは rAF が
   *  1 回しか回らない＝draw を待たずにここで描く。再生中は draw が再生の名簿で描き直す（live の名簿を被せない） */
  function syncRosterSel() {
    if (built && !replayWorld && roomSelection() !== tableSel) renderTable(built);
  }
  const roomLighting = createRoomLighting();

  // ── R110-S6: 常時の名札（DOM）─────────────────────────────────────────────
  // 本人「カーソルを合わせないとどれが誰か分からない」→ 部屋に貼った人の足元に名前の札を常に出す（退場中・貼れなかった人は除く）。
  // 基準は PWA の名札と同じ足元（bootpx.js の anchor＝(x+6, y+16)・1px 下）。中身＝名前の先頭 ×3 以上で 8 字・×2 で 6 字、
  // 状態の語は ×4 以上だけ（×1 は出さない）。重なりは ui/core/plates.js の placePlates（横へ・下へ 1 段・部屋の下へはみ出せば頭の上）。
  // 固定時刻でも出す（名前は事実）。canvas には描かない（tests/cockpit_smoke.py の fillText の門）。ホバーの札（.cphover）は残す。
  // 札が他の人の頭上の印（❗ ⚠ ✓ 💬 💭）に掛かるときは、札の丈まで上へずらして印を見せる（机の段は下の段の人の印が足元の
  // すぐ下にある）。ずらせない札だけ薄くする（.under＝印が勝つ・印は canvas なので札の下から透ける）。
  const namesEl = el("div", "cpnames");
  namesEl.id = "cpnames";
  namesEl.setAttribute("aria-hidden", "true");   // 名前と状態は名簿が読み上げる（ここは目で見る札）
  q("#viewport").append(namesEl);
  const plateNodes = new Map();     // 人の key → 札
  let plateBox = null;              // 札の層の寸法（部屋の canvas と同じ・倍率と形が変わったら測り直す）
  /** 名簿の board（再生中は再生の board）から、部屋に貼った人の足元へ札を置く。draw のたびに呼ぶ（変わった所だけ DOM に書く） */
  function paintNames(board) {
    const S = room.stats().scale;
    if (!(S >= ROOM_SCALE_MIN)) { if (!namesEl.hidden) namesEl.hidden = true; return; }
    if (!plateBox || plateBox.S !== S) {
      const c = q("#viewport canvas.cproom");
      plateBox = { S, w: ROOM.w * S, h: ROOM.h * S };
      const box = { left: `${c ? c.offsetLeft : 0}px`, top: `${c ? c.offsetTop : 0}px`, width: `${plateBox.w}px`, height: `${plateBox.h}px` };
      for (const [k, v] of Object.entries(box)) if (namesEl.style[k] !== v) namesEl.style[k] = v;
    }
    if (namesEl.hidden) namesEl.hidden = false;
    const who = new Map();
    for (const g of board || []) {
      for (const s of g.sessions || []) {
        const m = { ...s, projectName: g.name };
        if (s.session && !who.has(`s:${s.session}`)) who.set(`s:${s.session}`, m);
        if (s.id && !who.has(`i:${s.id}`)) who.set(`i:${s.id}`, m);
      }
    }
    const marks = new Map(room.marks().filter((p) => p.markAt).map((p) => [p.key, p.markAt]));
    const chars = S >= 3 ? 8 : 6;
    const withState = S >= 4;
    const fontKey = `${cpFont}|${watching ? "w" : ""}`;
    const list = [];
    for (const a of room.actors()) {
      if (a.leaving) continue;
      const m = who.get(`s:${a.session}`) || who.get(`i:${a.id}`);
      const full = String(m?.name || m?.projectName || "").trim();
      if (!full) continue;
      const cs = [...full];
      list.push({ a, m, name: cs.length > chars ? `${cs.slice(0, chars).join("")}…` : full, state: withState ? stateWord(m) : "" });
    }
    // 上の人から置く（同じ段は左から・key で決める＝同じ world なら同じ並び）
    list.sort((p, r) => p.a.y - r.a.y || p.a.x - r.a.x || (p.a.key < r.a.key ? -1 : p.a.key > r.a.key ? 1 : 0));
    // 1) 中身（変わった札だけ書く）→ 2) 寸法（変わった札だけ測る＝読むのは 1 回のレイアウト）→ 3) 置き場所（純関数）→ 位置を書く
    const seen = new Set();
    for (const it of list) {
      const key = it.a.key;
      seen.add(key);
      let n = plateNodes.get(key);
      if (!n) {
        n = el("span", "cpname");
        n.append(el("i", "dot"), el("span", "nm"), el("span", "st"));
        plateNodes.set(key, n);
        namesEl.append(n);
      }
      const sig = `${it.name}|${it.state}|${fontKey}`;
      if (n._sig !== sig) {
        n._sig = sig;
        n._w = null;
        n.children[1].textContent = it.name;
        n.children[2].textContent = it.state ? `· ${it.state}` : "";
        n.children[2].hidden = !it.state;
      }
      if (n.hidden) { n.hidden = false; n._w = null; }
      if (n.style.getPropertyValue("--session") !== it.a.tint) n.style.setProperty("--session", it.a.tint);
      it.n = n;
    }
    for (const [key, n] of plateNodes) if (!seen.has(key)) { n.remove(); plateNodes.delete(key); }
    for (const it of list) if (it.n._w == null) { it.n._w = it.n.offsetWidth; it.n._h = it.n.offsetHeight; }
    const R = ROOM.robot;
    // 頭上の印（canvas）は札で隠さない: 印の矩形を避ける（掛かる札は札の丈まで上へ・ずらせなければ薄くする）
    const avoid = [...marks.values()].map((m) => ({ left: m.x * S, top: m.y * S, w: m.w * S, h: m.h * S }));
    const spots = placePlates(list.map((it) => {
      const at = marks.get(it.a.key);
      // reach＝横へずらしても札が持ち主の体（幅 12 の真ん中 ± 6）に掛かっている範囲
      return { cx: (it.a.x + R.w / 2) * S, top: (it.a.y + R.h) * S + 1, reach: (R.w / 2) * S,
        head: (it.a.y - (at ? at.h + 1 : 0)) * S, w: it.n._w, h: it.n._h };
    }), plateBox, { avoid });
    list.forEach((it, i) => {
      const r = spots[i];
      const n = it.n;
      if (!r) { n.hidden = true; return; }   // 測れなかった（描かれていない）＝次の描画で測り直す
      const left = `${Math.round(r.left)}px`, top = `${Math.round(r.top)}px`;
      if (n.style.left !== left) n.style.left = left;
      if (n.style.top !== top) n.style.top = top;
      const cls = `cpname${it.m.attention || errOf(it.m) ? " attn" : ""}${r.under ? " under" : ""}`;
      if (n.className !== cls) n.className = cls;
    });
  }

  // ── R110-S6: 眺めるモード（F・Esc で戻る）─────────────────────────────────
  // 部屋だけを画面いっぱいに（×6 まで・ROOM_SCALE_WATCH_MAX）。ほかの面は visibility: hidden（レイアウトは保つ＝隠れた端末の
  // 列の幅を変えない＝本物の端末の桁数が変わらない）。下端のティッカーが作業中の人の「いま」（activityGloss）を 6 秒ごとに送る
  //（部屋の時計 t で floor(t/6) % n＝決定論・固定時刻は 1 件目）。キー起点だけ＝golden（既定のビュー）は変わらない。
  /** 眺めるモードの下端の帯に残す丈（部屋の倍率をこれを引いた丈から選ぶ＝帯が部屋の下端を隠さない） */
  const WATCH_TICKER_H = 40;
  /** ティッカーが次の人へ送る間隔（秒・部屋の時計） */
  const TICKER_SEC = 6;
  let watching = false;
  const tickerEl = el("div", "cpticker");
  tickerEl.id = "cpticker";
  tickerEl.hidden = true;
  const tkWho = el("b", "tk-who"), tkText = el("span", "tk-text"), tkN = el("span", "tk-n");
  const tkExit = el("button", "tk-exit");
  tkExit.type = "button";
  tkExit.addEventListener("click", () => setWatch(false));
  tickerEl.append(tkWho, tkText, tkN, tkExit);
  root.append(tickerEl);
  let tickerShown = null;
  /** ティッカー（live の名簿の作業中の人＝「いま」・再生中も今の人を流す）。変わったときだけ DOM に書く */
  function paintTicker(t) {
    const list = members().filter((m) => m.state === "working" && !m.attention);
    const n = list.length;
    const i = n ? Math.floor(Math.max(0, Number(t) || 0) / TICKER_SEC) % n : 0;
    const m = list[i] || null;
    const a = m && ((built?.agents || []).find((x) => x.session === m.session) || m);
    const who = m ? (m.name || m.projectName || "") : "";
    const text = a ? plainText(activityGloss(a, built?.lang || lang())).trim() : T("cp_ticker_none");
    const count = n > 1 ? `${i + 1} / ${n}` : "";
    const sig = JSON.stringify([who, text, count, T("cp_watch_exit"), m ? tintFor(m.projectKey) : ""]);
    if (sig === tickerShown) return;
    tickerShown = sig;
    tkWho.textContent = who; tkWho.hidden = !who;
    tkText.textContent = text;
    tkN.textContent = count; tkN.hidden = !count;
    tkExit.textContent = T("cp_watch_exit");
    if (m) tickerEl.style.setProperty("--session", tintFor(m.projectKey));
  }
  /** 入る・戻る。倍率を選び直し（×6 まで）、同じ倍率でも名札と部屋を描き直す */
  function setWatch(on) {
    on = Boolean(on);
    if (on === watching) return;
    watching = on;
    root.classList.toggle("cp-watch", on);
    tickerEl.hidden = !on;
    tickerShown = null;
    if (on) {
      // 隠れる面にフォーカスを残さない（打鍵が隠れた指示欄・本物の端末へ流れない）
      const act = document.activeElement;
      if (act && act !== document.body && shell.contains(act)) act.blur?.();
    }
    plateBox = null;
    fitRoom();
    redraw();
  }
  /** F で入ってよいか（モーダル・再生・ダイジェストのカードが前に居る間は入らない） */
  const canWatch = () => q("#modalwrap").hidden && !shell.classList.contains("replay-active")
    && q("#digest-card")?.hidden !== false;

  // ── R110-S6: 環境音（打鍵・紙・ベル）─────────────────────────────────────
  // 鳴らす根拠は全部実データ（ui/platform/sound.js の createAmbient が間隔の上限と「見ているときだけ」を持つ・既定 OFF）:
  //   keys  = Edit/Write（errand.js の toolClassOf が edit）で作業中の人が 1 人以上いる間、描画のたびに（0.5 秒に 1 回まで）
  //   paper = 用事で本棚・掲示板に着いた瞬間（room の actors の errand）と、読む人の画面（screenPage）のコマ替わり（3 秒に 1 回まで）
  //   bell  = 名簿の証拠（commit・テストの成功）の出来事の時刻が前より新しい（10 秒に 1 回まで）
  // 部屋の時計 t（格子）を渡す。再生・止めている間（動きを止める・視差効果を減らす）は打鍵と紙を鳴らさない（絵が止まっている）。
  /** 紙の音を鳴らす用事の行き先 */
  const PAPER_SPOTS = new Set(["shelf", "bulletin"]);
  let typists = 0;                  // Edit/Write で作業中の人数（apply で数える）
  let ambErrand = new Map();        // 人の key → 着いている用事の行き先（前の描画）
  let ambScreen = new Map();        // 人の key → 読む画面のコマ（前の描画）
  let ambPrimed = false;            // 前の描画を控えたか（開いた直後・ON にした直後は鳴らさずに控えるだけ）
  let evidencePrimed = false;
  let lastEvidenceAt = -Infinity;   // 名簿の証拠の一番新しい出来事の時刻（generatedAt − ago）
  function playRoomAmbient(t, replay) {
    if (replay || motionStill() || !ambient.ambientOn()) { ambPrimed = false; return; }
    const errands = new Map();
    for (const a of room.actors()) {
      if (!a.leaving && !a.walking && PAPER_SPOTS.has(a.errand)) errands.set(a.key, a.errand);
    }
    const screens = new Map();
    for (const l of room.layers()) {
      if (!l.startsWith("screen:")) continue;
      const cut = l.lastIndexOf(":");
      const tile = l.slice(cut + 1);
      if (tile.startsWith("screenPage")) screens.set(l.slice("screen:".length, cut), tile);
    }
    let paper = false;
    if (ambPrimed) {
      for (const [k, v] of errands) if (ambErrand.get(k) !== v) paper = true;
      for (const [k, v] of screens) if (ambScreen.has(k) && ambScreen.get(k) !== v) paper = true;
    }
    ambErrand = errands; ambScreen = screens; ambPrimed = true;
    if (paper) ambient.playAmbient("paper", t);
    if (typists > 0) ambient.playAmbient("keys", t);
  }
  /** 新しい証拠が届いたか（live だけ・最初の 1 回は控えるだけ）。出来事の時刻＝generatedAt − ago（ago は整数秒＝jitter まで揺れる） */
  function noteEvidence(w) {
    if (frozen) return false;
    let at = -Infinity;
    for (const a of w?.agents || []) {
      const ev = a?.evidence;
      if (!ev || !EVIDENCE_ERRAND.kinds.includes(ev.kind) || !(ev.ago <= EVIDENCE_ERRAND.fresh)) continue;
      at = Math.max(at, (Number(w.generatedAt) || 0) - ev.ago);
    }
    const fresh = evidencePrimed && at > lastEvidenceAt + EVIDENCE_ERRAND.jitter;
    lastEvidenceAt = Math.max(lastEvidenceAt, at);
    evidencePrimed = true;
    return fresh;
  }

  // ── R110-S6: 名簿の「いま」の経過（この作業を見始めてから）────────────────────
  // work.now に開始時刻は無い（サーバーは now/next/done/counts だけ）＝「いま」の語が変わった時刻をこのページで控える（live だけ）。
  // 開いた時にもう始まっていた作業は始まりを知らない＝「N 分以上前から」（嘘にしない）。出すのは作業中の人の行だけ（待ちの人は
  // 経過の列が待った長さを言う・❗ の人は「質問 · 3分」が言う）。再生・固定時刻・デモでは出さない。table.js の DOM は変えない
  //（.c-detail の末尾に <i class="since"> を足すだけ＝pixel の台帳は描かない）。
  const sinceBook = new Map();      // session → { gloss, t, first }
  let sinceStarted = false;
  let sinceTick = null;             // 直近に経過を描いた秒（1 秒に 1 回まで描き直す）
  const sinceLive = () => !frozen && !DEMO;
  function noteSince() {
    if (!sinceLive()) return;
    const tNow = now();
    const seen = new Set();
    for (const g of hud.board) {
      for (const s of g.sessions) {
        if (!s.session) continue;
        seen.add(s.session);
        const gloss = String(s.detail || "");
        const prev = sinceBook.get(s.session);
        if (!prev) sinceBook.set(s.session, { gloss, t: tNow, first: !sinceStarted });
        else if (prev.gloss !== gloss) sinceBook.set(s.session, { gloss, t: tNow, first: false });
      }
    }
    for (const k of [...sinceBook.keys()]) if (!seen.has(k)) sinceBook.delete(k);
    sinceStarted = true;
  }
  function paintSince() {
    const tNow = now();
    sinceTick = Math.floor(tNow);
    const live = sinceLive() && !replayWorld;
    const bySession = live ? new Map(members().map((m) => [m.session, m])) : null;
    for (const row of q("#agents").querySelectorAll(".pxrow")) {
      const det = row.querySelector(".c-detail");
      if (!det) continue;
      let node = det.querySelector(".since");
      const m = bySession?.get(row.dataset.session);
      const book = m && m.state === "working" && !m.attention && !errOf(m) ? sinceBook.get(m.session) : null;
      const min = book && book.gloss === String(m.detail || "") ? Math.floor((tNow - book.t) / 60) : 0;
      const text = min >= 1 ? T("cp_since", min, book.first) : "";
      if (!text) { node?.remove(); continue; }
      if (!node) { node = el("i", "since"); det.append(node); }
      if (node.textContent !== text) node.textContent = text;
    }
  }

  /**
   * 1 回描く（部屋＋毎フレームの DOM の用事）。t は描画の刻みに揃えた時刻（下の gridT）。**同じ t で呼び直してよい**
   *（部屋は前の t の帳簿から計算し直す＝冪等）。非表示のタブでは部屋が描かない（room.js の draw が document.hidden を
   * 読む＝持ち主は 1 か所。隠れている間の遷移を、戻った瞬間に遅れて演じない＝部屋の帳簿の古さの門に任せる）。
   * rAF は非表示の間そもそも回らない（ui/platform/clock.js の loop）＝ここに来るのはデータの到着（apply）と再生の後始末だけ。
   */
  const draw = (t) => {
    if (!built) return;
    shownT = t;
    dirty = false;
    drawnSel = roomSelection();
    room.setSelected(drawnSel);
    const frame = digest?.frame(t);
    // R110: 名簿の選択の印（.sel）は端末で選んでいる人＝選択が変わったら名簿も描き直す（再生中は再生の名簿を）
    if (frame) {
      if (!replayKeys) replayKeys = liveKeys();
      if (frame.world !== replayWorld || drawnSel !== tableSel) {
        replayWorld = frame.world;
        renderTable(frame.world, boardFromWorld(frame.world, T, replayKeys, frame.eventSids));
      }
    } else if (replayWorld || replayKeys) {
      replayWorld = null; replayKeys = null;
      renderTable(built);
    } else if (drawnSel !== tableSel) renderTable(built);
    // 再生中は**再生の時計**で描く。live の t を渡すと、再生を止めても部屋だけ動き続ける。
    // 席の割り当ては live と再生で別々（replay）＝再生を閉じても live の席は元のまま。
    // 過去の再生には今の office の err/minionsLive を補わない（未来の状態を過去へ持ち込まない）。
    const roomBoard = frame ? boardFromWorld(frame.world, T, replayKeys || liveKeys(), frame.eventSids) : hud.board;
    const placed = room.draw(frame ? frame.world : roomWorld, roomBoard,
      frame?.t ?? t, { replay: Boolean(frame), lightStage: roomLighting(motionStill() || Boolean(frame)) });
    paintSeats(placed);
    // R110-S6: 部屋に貼った人の足元の名札・環境音（live だけ）・眺めるモードのティッカー・名簿の「いま」の経過（1 秒に 1 回）
    paintNames(roomBoard);
    playRoomAmbient(t, Boolean(frame));
    if (watching) paintTicker(t);
    if (Math.floor(now()) !== sinceTick) paintSince();
    firstrun.paint(t);
    const s = frozen ? null : session.dataAge(t);
    if (s !== null && s !== freshShown) {
      freshShown = s;
      freshEl.textContent = T("updated_ago", s);
      freshEl.hidden = false;
    }
  };

  /**
   * 「席 n/9・受付 m・外 k」＝部屋に**実際に置いた**人数（room.draw の戻り値）。台帳の人数から引き算すると、
   * ❗・休憩・会議・外部社員の人まで席に数えて事実とずれる。受付 m は受付に**描いた**人（確認待ち＋standby）。
   * 部屋に入りきらなかった人（overflow・台帳には居る）は受付に混ぜず、1 人以上のときだけ「外 k」と別に書く
   *（受付に描いた人数より大きい数を「受付」と書かない）。変わったときだけ DOM に書く。
   */
  let seatsShown = null;
  function paintSeats(placed) {
    if (!placed) return;
    const text = T("cp_seats", placed.desk, SEATS, placed.queue + placed.standby)
      + (placed.overflow > 0 ? T("cp_seats_out", placed.overflow) : "");
    if (text === seatsShown) return;
    seatsShown = text;
    for (const id of ["#cp-foot-seats", "#cp-office-meta"]) q(id).textContent = text;
  }
  /** b4-mac: 03 端末の見出しは端末の数（「席 9/9 · 受付 4」は端末と関係が無かった） */
  function paintTermMeta() {
    const all = termUi?.all?.() || [];
    const text = all.length ? T("cp_term_meta", all.length, all.filter((t) => t.alive).length) : "";
    const n = q("#cp-term-meta");
    if (n.textContent !== text) n.textContent = text;
  }

  /** セッション一覧（並びは台帳と同じ＝どこを見ても同じ順番） */
  const members = () => hud.board.flatMap((g) =>
    g.sessions.map((s) => ({ ...s, projectKey: g.key, projectName: g.name })));

  /** 中央に映す相手を決める。❗が居ればそちらを既定にする。
   *  selected は「セッション id」か「term:<id>」（台帳にまだ居ない端末＝shell・codex・起動直後の claude）。 */
  const TERM_PREFIX = "term:";

  // ── R111-B2: 端末のペイン（tmux 型の分割木）────────────────────────────────
  // 木の正本は ui/core/termpanes.js（純関数）・DOM は ./panes.js・端末の積み替えと購読は ./term.js。ここは「どの葉に何を出すか」と配線。
  // 葉の sel は selected と同じ値（"<session>" か "term:<id>"・空は null）。**同じ sel は 1 葉だけ**（別の葉で選ぶと移動）。
  // selected は「選んでいる葉の sel の写し」＝読み手（タブ・足元・部屋の選択・名簿・＋端末の行き先…）は今までどおり selected を読む。
  // 書くのは commit だけ（ensureSelected・select・＋端末・閉じる・ペインの操作がみな commit を通る）。
  let tree = panes.empty();
  let paneShown = 1;                // 直近に見えていたペインの数（term.js の 80 桁の自動縮小は 1 のときだけ）
  let lastSession = null;           // 直近に選んでいた台帳のセッション（空のペインで ＋端末 を開くフォルダ）
  // R121: 1 ペインの中央の既定（作業ビュー／ターミナル）。［ターミナル］⇄［作業］で選んだ側を保存する（端末派は次も端末）＝
  // 保存するのは表示の選び方だけ（端末の出力は保存しない掟のまま）。デモは読まない書かない・固定時刻（?t=）は読むだけ（書かない）
  //（golden の固定ページは端末を持たない＝読んでも絵は同じ。端末の形を測る門は「ターミナル派」の保存を入れて開く）。
  // 会話の記録が無い端末（素のシェル・名簿に居ないセッション）は作業ビューに出すものが無い＝いつも端末。
  const CENTER_KEY = "aioffice.cockpit.center";
  let centerPref = "activity";
  if (!DEMO) { try { if (localStorage.getItem(CENTER_KEY) === "terminal") centerPref = "terminal"; } catch { /* 保存できない端末 */ } }
  const setCenterPref = (v) => {
    centerPref = v === "terminal" ? "terminal" : "activity";
    if (frozen || DEMO) return;
    try { if (centerPref === "terminal") localStorage.setItem(CENTER_KEY, "terminal"); else localStorage.removeItem(CENTER_KEY); }
    catch { /* 保存できない端末 */ }
  };
  /** この生きた端末を（1 ペインのとき）端末として出すか: 記録の無い端末はいつも・ほかは選んだ既定 */
  const hasRecord = (live) => Boolean(live) && live.kind !== "shell" && Boolean(live.session)
    && members().some((m) => m.session === live.session);
  const wantsTerminal = (live) => Boolean(live) && (centerPref === "terminal" || !hasRecord(live));
  // 保存（ブラウザごと・木の形と各葉の sel だけ＝端末の出力は載せない）。固定時刻（?t=）とデモでは読まない書かない。
  // 単葉（分けていない）は鍵を消す。読むのは mount の今・当てるのは端末の一覧が初めて届いた後（restorePanes）
  const PANES_KEY = "aioffice.cockpit.panes";
  const panesStore = !frozen && !DEMO;
  let panesSaved = null;
  if (panesStore) {
    try { const raw = localStorage.getItem(PANES_KEY); if (raw) panesSaved = panes.parse(raw); } catch { /* 読めない端末 */ }
  }
  let panesReady = false;           // 保存の復元を済ませた（これより前は書かない＝復元前の単葉で鍵を消さない）
  /** 選んでいる葉の sel */
  const focusSel = (t = tree) => panes.leafOf(t, t.focus)?.sel ?? null;
  function savePanes() {
    if (!panesStore || !panesReady) return;
    try {
      if (panes.isSingle(tree)) localStorage.removeItem(PANES_KEY);
      else localStorage.setItem(PANES_KEY, JSON.stringify(panes.serialize(tree)));
    } catch { /* 保存できない端末 */ }
  }
  /**
   * 木を替える唯一の口。同じ木（同一参照＝無変化）なら何もしない。保存し、selected を選んでいる葉の sel に合わせる
   *（変わったら会話を読み直す）。paint＝タブとペインを描き直す（呼び手がこの後で描くときは false）。
   * user＝本人の操作（選んだ人が ❗ ならトレイもその人に合わせる＝select と同じ）。
   */
  function commit(next, { paint = true, user = false } = {}) {
    if (!next || next === tree) return false;
    tree = next;
    savePanes();
    const sel = focusSel();
    if (sel !== selected) selected = sel;
    if (selected && members().some((m) => m.session === selected)) lastSession = selected;
    if (paint && built) {
      ensureSelected();
      paintPanes();
      paintTabs();
    }
    if (user && built && selected) tray.show(selected);
    return true;
  }
  /** 端末の一覧が初めて届いた: 保存しておいた木を当てる（もう無い端末の葉は空に・台帳に無いセッションは「もう居ません」のまま待つ） */
  function restorePanes() {
    if (panesReady) return;
    panesReady = true;
    const saved = panesSaved;
    panesSaved = null;
    // 壊れた・単葉の保存は鍵を消す。一覧が届く前に本人がもう分けていたら、その形を残す（上書きしない）
    if (!saved || panes.isSingle(saved) || !panes.isSingle(tree)) { savePanes(); return; }
    let next = saved;
    for (const leaf of panes.leaves(next)) {
      if (leaf.sel?.startsWith(TERM_PREFIX) && !termUi?.get(leaf.sel.slice(TERM_PREFIX.length))) next = panes.assign(next, leaf.id, null);
    }
    commit(next, { paint: false });
  }

  const ensureSelected = () => {
    const all = members();
    const known = new Set(all.map((m) => m.session));
    let next = tree;
    // claude が起動して台帳にセッションが現れたら、その葉をそのセッションへ移す（同じ端末のまま・どの葉でも）
    for (const leaf of panes.leaves(next)) {
      if (!leaf.sel?.startsWith(TERM_PREFIX)) continue;
      const info = termUi?.get(leaf.sel.slice(TERM_PREFIX.length));
      if (info?.session && known.has(info.session)) next = panes.assign(next, leaf.id, info.session);
    }
    // 空いた葉を自動で埋めるのは葉が 1 つのときだけ（❗が居ればそちら・無ければ先頭）。分けたペインは勝手に埋めない
    if (panes.isSingle(next)) {
      const sel = focusSel(next);
      let want = sel;
      const keepTerm = sel?.startsWith(TERM_PREFIX) && termUi?.get(sel.slice(TERM_PREFIX.length));
      if (!keepTerm) {
        if (!all.length) want = null;
        else if (!(sel && known.has(sel))) want = (all.find((m) => m.attention) || all[0]).session;
      }
      next = panes.assign(next, next.focus, want);
    }
    commit(next, { paint: false });
  };

  /** 本人が選んだ（タブ・一覧・部屋のロボ・台帳の行）。**フォーカスを端末へ動かしてよいのはここだけ**。
   *  選んだ人が ❗ なら ❗トレイもその人に合わせる（tray.show＝合わせるだけ・指示欄は開かない＝タッチでキーボードが
   *  上がらない）。❗ でない人を選んでもトレイはそのまま（今の ❗ を隠さない）。
   *
   *  R106-10: 開いている指示シート（#sheet）は、以前は選択が変わっても古い相手のままだった（power-user-03・
   *  sheet.js は別の組の持ち場なので触らず、index.js から既存の公開関数 openCompose/closeCompose だけで直す）。
   *  下書きが無ければ新しい相手へ黙って追従させる（実害が無い＝シートを開き直すだけ）。下書きが有るときは黙って
   *  新しい相手へ流用しない（Enter で誤爆する芽）＝正直に閉じてトーストで知らせる。
   *
   *  戻り値: ここでシート（開き直し／閉じる）を済ませたら true。呼び手（台帳の行クリック＝onPick）は
   *  これが true のとき自分では openCompose を呼び直さない＝別モデルレビュー（中）が突いた「閉じましたの
   *  トーストを出した直後に、同じ呼び出し元が無条件にまた openCompose して実際には開き直っていた」矛盾の芽。
   *  ロボ／タブ／一覧のクリックは戻り値を見ない素の select() のままでよい（元々この後 openCompose を呼ばない）。 */
  const select = (sessionId, { focus = true } = {}) => {
    if (!sessionId) return false;
    let sheetHandled = false;
    if (sessionId !== selected) {
      const prevSelected = selected;
      const sheetEl = q("#sheet");
      if (!sheetEl.hidden && prevSelected && prevSelected !== sessionId) {
        sheetHandled = true;
        const draft = q("#composeinput");
        if (draft && draft.value.trim()) {
          sheet.closeCompose();
          delivery.showToast(T("sheet_target_switched"), false);
        } else {
          const next = members().find((x) => x.session === sessionId);
          if (next) {
            const a = (built?.agents || []).find((x) => x.session === next.session);
            openCompose(a || { ...next, crew: 1, sessions: [] });
          }
        }
      }
      // R111-B2: 選んでいるペインへ入れる（同じ相手を別のペインが持っていたら、そのペインは空になる＝移動）
      commit(panes.assign(tree, tree.focus, sessionId), { paint: false });
      paintPanes();
      paintTabs();
      revealTab();
    }
    tray.show(sessionId);
    const live = liveTerm();
    if (live && showTerminal()) termUi.show(live.id, { focus });
    return sheetHandled;
  };

  function paintTabs() {
    const host = q("#cp-tabs");
    const all = members();
    const known = new Set(all.map((m) => m.session));
    const extra = termUi ? termUi.unmapped(known) : [];
    // R102-11: 同じ中身なら DOM に触らない（apply ごとの作り直しで、押した瞬間のクリックとフォーカスが消えた）
    const sig = JSON.stringify([selected, all.map((m) => [m.session, tintFor(m.projectKey), m.name || m.projectName,
      !!termUi?.forSession(m.session), kindTag(m)]),
      extra.map((info) => [info.id, info.alive, T("cp_term_kind", info.kind), T("cp_term_booting")])]);
    // タブの選択と名簿の選択の印は同じ 1 本（selected）＝タブを描き直さない回も名簿の印は合わせる
    if (host._sig === sig) { paintTabEdges(); syncRosterSel(); return; }
    host._sig = sig;
    host.replaceChildren(...all.map((m) => {
      const b = el("button", `cptab${m.session === selected ? " sel" : ""}`);
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(m.session === selected));
      b.dataset.session = m.session;
      b.style.setProperty("--session", tintFor(m.projectKey));
      b.append(el("i", "dot"), tabName(m.name || m.projectName));
      if (termUi?.forSession(m.session)) b.append(el("i", "live", "▸"));   // 打てる端末を持っている
      const tag = kindTag(m);
      if (tag) b.append(el("i", m.attention ? "attn" : "attn err", tag));
      return b;
    }));
    for (const info of extra) {
      const key = TERM_PREFIX + info.id;
      const b = el("button", `cptab term${key === selected ? " sel" : ""}${info.alive ? "" : " dead"}`);
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(key === selected));
      b.dataset.session = key;
      b.append(el("i", "live", "▸"), tabName(T("cp_term_kind", info.kind)
        + (info.kind === "claude" && info.alive ? ` · ${T("cp_term_booting")}` : "")));
      host.append(b);
    }
    paintTabEdges();
    syncRosterSel();
  }
  /** R110: タブの名前（20em で切る唯一の意図した省略＝全文は title に置く） */
  function tabName(text) {
    const n = el("span", "nm", text);
    n.title = text;
    return n;
  }

  /**
   * b4-mac: タブが列に収まらないとき、左右の端に隠れている本数（「‹ N」「N ›」）を出す。スクロールバーは隠している
   *（scrollbar-width: none）＝17 本並ぶと残りのタブがあることに気づけなかった。
   * R110: 縁で**途中まで切れている**タブも隠れている側に数える。以前は丸ごと外に出たタブだけを数えていて、最後の 1 本が
   * 半分だけ切れている形では「N ›」が出ず、縁のフェード（mask・style.css は #cp-tabs-r が見えている間だけ掛ける）も掛からなかった。
   */
  function paintTabEdges() {
    const host = q("#cp-tabs");
    const lEl = q("#cp-tabs-l");
    const rEl = q("#cp-tabs-r");
    if (!lEl || !rEl) return;
    const put = (n, text) => {
      n.hidden = !text;
      if (n.textContent !== text) n.textContent = text;
    };
    // 端の「‹ N」「N ›」が出入りすると列の幅が変わり、切れるタブも変わる＝出入りがあった回だけもう 1 度数える（2 回で止める）
    for (let pass = 0; pass < 2; pass += 1) {
      const shown = `${lEl.hidden}|${rEl.hidden}`;
      const box = host.getBoundingClientRect();
      let left = 0;
      let right = 0;
      if (host.scrollWidth > host.clientWidth + 1) {
        for (const t of host.children) {
          const r = t.getBoundingClientRect();
          if (r.left < box.left - 1) left += 1;
          if (r.right > box.right + 1) right += 1;
        }
      }
      put(lEl, left ? `‹ ${left}` : "");
      put(rEl, right ? `${right} ›` : "");
      if (`${lEl.hidden}|${rEl.hidden}` === shown) break;
    }
  }
  /** 選んだタブを見える位置へ送る（部屋・一覧・台帳から選んだとき。選択が変わったときだけ＝本人のスクロールを奪わない） */
  function revealTab() {
    q("#cp-tabs .cptab.sel")?.scrollIntoView({ block: "nearest", inline: "nearest" });
    paintTabEdges();
  }

  /** ❗の種類の札（「! 許可」「! 質問」…）・エラーは「! エラー（上限）」。どちらでもない人は "" */
  function kindTag(m) {
    if (m?.attention) {
      const k = attnKind(m);
      return `! ${T(ATTN_KINDS.includes(k) ? `kind_${k}` : "kind_stuck")}`;
    }
    const er = errOf(m);
    return er ? `! ${T("kind_error", T(`err_${er.k}`))}` : "";
  }

  /**
   * R110 骨格 A: 04 受付（部屋の下）。❗ が居れば hud/tray.js が #attn に 1 件を大きく描く（ここでは描かない）。
   * 居なければ（tray.render が null＝renderChrome が shell._traySel に置く）安心の合図 INBOX ZERO を 1 行（R106-16 の文言・
   * 作業中／指示待ちの数・休憩中は数えない）。セッションが 1 つも無いときは出さない（空のオフィスは中央の入口が言う）。
   * 見出しの右は「i / N」＝いま出している ❗ が待ち行列の何件目か（J/K と ◂ ▸ で巡る位置・1 件でも出す）。
   */
  function paintReception(w) {
    const queue = attentionQueue(w.agents);
    const at = queue.findIndex((a) => a.id === shell._traySel);
    const meta = at >= 0 ? `${at + 1} / ${queue.length}` : "";
    const metaEl = q("#cp-reception-meta");
    if (metaEl.textContent !== meta) metaEl.textContent = meta;
    const all = members();
    const zeroEl = q("#cp-inbox-zero");
    const zero = shell._traySel == null && all.length > 0;
    const text = zero ? T("cp_inbox_zero", all.filter((m) => m.state === "working").length,
      all.filter((m) => m.state === "waiting").length) : "";
    if (zeroEl.textContent !== text) zeroEl.textContent = text;
    zeroEl.hidden = !zero;
  }

  /**
   * R110: 名簿（02）の内スクロールの下に隠れている行の数＝「▾ あと N 件」。1 px でも下に切れている行を数える
   *（途中で切れた行も「見えていない」側）。0 件は出さない。押すと名簿を 1 画面ぶん下へ送る。
   * 数え直すのは名簿のスクロール・名簿と行の束の丈の変化（ResizeObserver＝絞り込み・行の増減）・render。
   */
  const rosterEl = q("#cp-roster");
  const rosterMoreEl = q("#cp-roster-more");
  // R110-D1: 名簿の窓が低い 2 カラムでは同じ数を見出しの右の札で出す（どちらを見せるかは style.css の @container cproster）
  const rosterMoreChip = q("#cp-roster-more-chip");
  function paintRosterMore() {
    const box = rosterEl.getBoundingClientRect();
    let below = 0;
    if (rosterEl.scrollHeight > rosterEl.clientHeight + 1) {
      for (const r of rosterEl.querySelectorAll("#agents .pxrow")) {
        if (r.getBoundingClientRect().bottom > box.bottom + 1) below += 1;
      }
    }
    const text = below ? T("cp_more_rows", below) : "";
    for (const b of [rosterMoreEl, rosterMoreChip]) {
      b.hidden = !below;
      if (b.textContent !== text) b.textContent = text;
    }
  }
  let moreRaf = 0;
  const scheduleRosterMore = () => {
    if (!moreRaf) moreRaf = requestAnimationFrame(() => { moreRaf = 0; paintRosterMore(); });
  };
  rosterEl.addEventListener("scroll", scheduleRosterMore, { passive: true });
  for (const b of [rosterMoreEl, rosterMoreChip]) {
    b.addEventListener("click", () => {
      rosterEl.scrollBy({ top: Math.max(80, rosterEl.clientHeight * 0.8), behavior: frozen ? "auto" : "smooth" });
    });
  }
  const rosterRo = typeof ResizeObserver === "function" ? new ResizeObserver(scheduleRosterMore) : null;
  rosterRo?.observe(rosterEl);
  rosterRo?.observe(q("#agents"));

  /** R121: 回答の操作は作業ビューの末尾。実際の送り先確認はトレイの同じボタンが行う。 */
  /** 中央の問いのカードの材料（decorateActivity と、描き直すかの印が同じものを見る） */
  function cardOf(m) {
    const a = (built?.agents || []).find((x) => x.session === m.session)
      || (built?.agents || []).find((x) => x.id === m.id);
    const src = m.attention || errOf(m) ? m : (a?.ask ? a : null);
    const asked = src ? (attnLine(T, src, built?.lang || lang()) || T("ask_live")) : "";
    return { a, src, asked };
  }
  function decorateActivity(host, _activity, options) {
    const page = options.page;
    if (page?.beg && !page.top) {
      const more = el("button", "cpactivity-before", T("cp_activity_before"));
      more.type = "button"; more.disabled = Boolean(dlgBusy);
      more.addEventListener("click", () => loadActivity(options.member, { before: true }));
      host.prepend(more);
    } else if (page?.loaded && page.top) host.prepend(el("p", "act-note", T(isBot(options.member) ? "bot_record_start" : "cp_activity_start")));
    if (options.gap) host.prepend(el("p", "act-note", T("cp_activity_gap")));
    const m = options.member;
    if (!m) return;
    const { a, src, asked } = cardOf(m);
    if (!asked) return;
    const box = el("div", "cpq act-pending");
    box.dataset.ask = src.question ? "question" : src.ask?.kind === "permission" ? "permission"
      : errOf(src) && !src.attention ? "error" : "ask";
    box.append(el("b", "cpq-head", kindTag(src) || "!"), el("span", "cpq-text", asked));
    if (a && a.session === m.session && a.attention && !DEMO) {
      const acts = el("div", "cpq-acts");
      answerActs(a, T, 3).forEach((act, i) => {
        const b = el("button", `cpq-btn${act.allow ? " go" : ""}`, `${i + 1} ${act.label}`);
        b.type = "button";
        b.addEventListener("click", () => answerFromCenter(a, i, act.label));
        acts.append(b);
      });
      box.append(acts);
    }
    host.append(box);
  }

  const activityPages = new Map();
  let dlgMark = null;
  let dlgBusy = null;
  let activityDisposed = false;
  // 取り直しの印は app と同じ関数（ui/hud/activity_feed.js）。R128: Bot は未読の印・最後に動いた時刻の替わり目と、選んでいる間の定期の差分でも取り直す
  const motionSig = activityMotionSignature;
  function activityPage(session) {
    if (!activityPages.has(session)) activityPages.set(session, { rows: [], cur: null, beg: null, top: false,
      gap: false, loaded: false, lang: lang(), view: createActivityView({ lang: lang(), decorate: decorateActivity }) });
    const page = activityPages.get(session);
    if (page.lang !== lang()) {
      page.view.dispose();
      page.lang = lang();
      page.view = createActivityView({ lang: lang(), decorate: decorateActivity });
    }
    return page;
  }
  // 押している間は会話の欄を描き直さない（作り直しが来ると押したボタンのクリックが消える＝ui/hud/press_hold.js）
  const hold = createPressHold(q("#cp-dlg"));
  let painted = null;   // 前回描いた { host, rows, key, first, last, result }＝同じなら会話の欄は描き直さない（activity_feed.js の activityPaintKey）
  function paintActivity(m) {
    if (hold.defer(() => paintActivity(m))) return;
    if (!m) { painted = null; q("#cp-work").replaceChildren(); return; }
    const page = activityPage(m.session);
    const host = q("#cp-dlg");
    // 同じ rows でも秒の刻み・状態の変化は renderer へ渡す。本文は端末出力ではなく既存の transcript 行だけ。
    // R128: 出勤の口の Bot は記録（record＝考え中を出さない・日時を添える）
    const bot = isBot(m);
    // 同じ人・同じ行・同じ問いの材料なら会話の欄は描き直さない（文の選択と押したボタンを保つ・経過秒は毎秒のループが書き換える）。足元の要約は毎回
    const card = cardOf(m);
    const key = activityPaintKey(m, [lang(), built?.lang || "", m.state === "working" && !m.attention, bot, page.loaded, page.top,
      page.beg, page.gap, Boolean(dlgBusy), DEMO, card.asked, card.src ? kindTag(card.src) : "", JSON.stringify(errOf(m) || null),
      card.a ? activityPaintKey(card.a, [card.a.session === m.session]) : ""]);
    const same = samePaint(painted, host, page.rows, key) && painted.result;
    const result = same ? painted.result : page.view.render(host, page.rows, { live: m.state === "working" && !m.attention,
      now: epochNow(), state: m.state, ask: m.ask, member: m, gap: page.gap, page, record: bot });
    const summary = q("#cp-work");
    summary.replaceChildren();
    summary.classList.add("act");
    // 経過: 動いている間は「いまの指示から」（最後の人の指示の時刻・Claude Code アプリの作業時間と同じ考え）、止まっていれば「最後に動いた時」。
    // 最後に書いた時からの秒（age）を作業時間のように見せない（動いている間はほぼ常に 0〜数秒＝意味が無かった）。
    // 「裏で N 件」は出さない＝行から裏の仕事が終わったかを知る手段が無い（終わった仕事まで数えると嘘になる）。
    const live = m.state === "working" && !m.attention && !bot;
    const turn = [...result.blocks].reverse().find((b) => b.type === "user" && Number.isFinite(b.ts));
    const t = epochNow();
    const when = live ? (turn && t >= turn.ts ? formatDuration(t - turn.ts) : "")
      : agoStr(Math.max(0, Number(m.age) || 0), lang());
    summary.append(el("span", `cpactivity-state${live ? " live" : ""}`, [stateWord(m), when].filter(Boolean).join(" · ")));
    if (result.summary.todo) summary.append(el("span", "act-chip", `Todo ${result.summary.todo.done}/${result.summary.todo.total}`));
    if (result.summary.files.length) summary.append(el("span", "act-chip", T("cp_activity_files", result.summary.files.length)));
    summary.append(el("span", "cpactivity-perm", permWord(m)));
    const head = q("#cp-term-head");
    head.replaceChildren();
    if (!same && page.loaded && !page.rows.length && !host.querySelector(".cpq") && (bot || m.state !== "working"))
      host.append(el("p", "act-note", T(bot ? "bot_record_empty" : "cp_term_empty")));
    painted = { host, rows: page.rows, key, result, first: host.firstElementChild, last: host.lastElementChild };
  }
  async function loadActivity(m, { before = false } = {}) {
    if (frozen || DEMO || dlgBusy || activityDisposed) return;
    const page = activityPage(m.session);
    if (before && (!page.beg || page.top)) return;
    const scroll = q("#cp-dlg"), height = scroll.scrollHeight, position = scroll.scrollTop;
    dlgMark = { session: m.session, sig: motionSig(m), age: Number(m.age) || 0, at: now() };
    const pending = getActivity(m.session, before ? { before: page.beg } : { after: page.cur });
    dlgBusy = pending;
    try {
      const data = await pending;
      if (activityDisposed || !data?.ok) return;
      const rows = Array.isArray(data.rows) ? data.rows : [];
      if (before && !data.reset) page.rows = rows.concat(page.rows);
      else page.rows = data.reset || !page.loaded ? rows : page.rows.concat(rows);
      if (!before || data.reset) page.cur = data.cur || page.cur;
      if (before || !page.loaded || data.reset) {
        page.beg = data.beg || null;
        page.top = Boolean(data.top);
      }
      page.gap = data.reset ? Boolean(data.gap) : page.gap || Boolean(data.gap);
      page.loaded = true;
      if (selected === m.session && !showTerminal()) {
        paintActivity(members().find((s) => s.session === m.session) || m);
        if (before) scroll.scrollTop = position + scroll.scrollHeight - height;
      }
    } catch { /* 次の更新で再試行。既に届いた本文は残す */ }
    finally {
      if (dlgBusy === pending) dlgBusy = null;
      if (!activityDisposed && selected === m.session && !showTerminal())
        paintActivity(members().find((s) => s.session === m.session) || m);
    }
  }
  function refreshDialog(m) {
    if (frozen || DEMO || dlgBusy) return;
    const page = activityPage(m.session);
    const sig = motionSig(m), age = Number(m.age) || 0;
    const moved = !page.loaded || !dlgMark || dlgMark.session !== m.session || dlgMark.sig !== sig || age + 5 < dlgMark.age
      || botRecordDue(m, dlgMark, now());
    if (!moved) { if (dlgMark) dlgMark.age = age; return; }
    if (dlgMark && dlgMark.session === m.session && now() - dlgMark.at < 3) return;
    loadActivity(m);
  }

  /** 中央の答えのボタン: トレイをその人に合わせ、同じ番号・同じ文言のボタンを押す（文言が違えば押さない＝別の要求に答えない） */
  const answerFromCenter = (a, i, label) => {
    if (!tray.show(a.session)) { openCompose(a); return; }
    const b = q(`#attn button[data-idx="${i}"]`);
    if (b && b.textContent === `${i + 1} ${label}`) b.click();
    else openCompose(a);
  };

  /** その sel（"<session>" か "term:<id>"）に出すべき端末（無ければ null） */
  const liveFor = (sel) => {
    if (!termUi || !sel) return null;
    if (sel.startsWith(TERM_PREFIX)) return termUi.get(sel.slice(TERM_PREFIX.length));
    return termUi.forSession(sel);
  };
  /** いま中央（選んでいるペイン）に出すべき生きた端末（無ければ null） */
  const liveTerm = () => liveFor(selected);
  const showTerminal = () => Boolean(liveTerm()) && (!panes.isSingle(tree) || wantsTerminal(liveTerm()));
  q("#cp-activity-toggle").addEventListener("click", () => {
    const live = liveTerm();
    if (!live || !panes.isSingle(tree)) return;
    setCenterPref(showTerminal() ? "activity" : "terminal");
    blurTerm();
    paintPanes();
    if (showTerminal()) termUi.show(live.id, { focus: true });
  });

  // ── R111-B2: 葉ごとの中身を決めて、端末と会話の組を置き場所へ移す ────────────────────
  // 中身: term（端末を出す）／dialog（会話の組 #cp-work・#cp-dlg を出す＝選んでいるペインだけ）／label（端末の無いセッションを
  // 選んでいないペイン＝名前と状態 1 行と「会話は選んでいるペインに出ます」・台帳から消えた人は「もう居ません」）／empty（空のペイン）。
  // 見えているペインが 1 つで中身が会話なら、従来の 1 枚の会話ビュー（#cp-xterm は hidden・会話の組は .cpterm の元の場所）＝
  // 分けていない既定の画面の画素は R110 のまま（golden 不変）。見えているペインが 1 つで端末なら、ペイン 1 枚（見出しも枠も無し）。
  const xtermEl = q("#cp-xterm");
  const termEl = q("#cp-term");
  const workEl = q("#cp-work");
  const dlgEl = q("#cp-dlg");
  const dlgParkEl = q("#cp-dlg-park");
  /** 会話の組を移す: "home"＝.cpterm の元の場所（#cp-xterm の前）／"park"＝駐車場（hidden）／要素＝そのペインの body */
  function pairTo(dest) {
    if (dest === "home") {
      if (workEl.parentNode === termEl && dlgEl.parentNode === termEl) return;
      termEl.insertBefore(workEl, xtermEl);
      termEl.insertBefore(dlgEl, xtermEl);
      return;
    }
    const host = dest === "park" ? dlgParkEl : dest;
    if (!host || (workEl.parentNode === host && dlgEl.parentNode === host)) return;
    host.append(workEl, dlgEl);
  }
  /** 分割の入口（＋端末のメニュー・キー・見出し）を開けてよいか＝3 カラムだけ・デモ／眺めるモードでは閉じる */
  const paneSplitOk = () => !DEMO && !watching && Boolean(panesUi) && colCount() === 3;
  function hostPanes() {
    if (DEMO || !termUi || !panesUi) { pairTo("home"); return; }   // デモ: #cp-xterm は hidden のまま・会話の組は元の場所
    const all = members();
    const known = new Map(all.map((m) => [m.session, m]));
    const single = panes.isSingle(tree);
    const kindOf = (leaf) => {
      if (!leaf?.sel) return single ? "dialog" : "empty";
      const live = liveFor(leaf.sel);
      if (live && (!single || wantsTerminal(live))) return "term";
      if (single || (known.has(leaf.sel) && leaf.id === tree.focus)) return "dialog";
      return "label";
    };
    // 2 カラム・1 列では分割を見せない（選んでいるペインだけ・木は持ったまま＝3 カラムへ戻れば元の形）
    const lay = panesUi.layout(tree, { collapse: colCount() !== 3 });
    paneShown = Math.max(1, lay.rects.size);
    const focusKind = kindOf(panes.leafOf(tree, tree.focus));
    if (lay.rects.size <= 1 && focusKind === "dialog") {
      termUi.parkAll();
      pairTo("home");
      panesUi.reset();
      xtermEl.hidden = true;
      return;
    }
    // 見えている葉の端末だけ見せる（購読もその分だけ＝term.js）。ほかは駐車
    const shows = new Map();
    for (const leaf of panes.leaves(tree)) {
      if (!lay.rects.has(leaf.id) || kindOf(leaf) !== "term") continue;
      shows.set(leaf.id, liveFor(leaf.sel).id);
    }
    const keep = new Set(shows.values());
    for (const id of termUi.hosted()) if (!keep.has(id)) termUi.park(id);
    // 会話の組: ペイン 1 枚の端末＝元の場所（.cpterm.live が隠す・R110 と同じ）／会話を出すペイン＝その body（描いた後で移す）／ほか＝駐車
    const pairBody = focusKind === "dialog" ? panesUi.bodyOf(tree.focus) : null;
    if (lay.rects.size <= 1 && focusKind === "term") pairTo("home");
    else if (!pairBody || workEl.parentNode !== pairBody) pairTo("park");
    xtermEl.hidden = false;
    const metaFor = (leaf) => {
      const kind = kindOf(leaf);
      const live = liveFor(leaf.sel);
      const m = leaf.sel ? (known.get(leaf.sel) || (live?.session ? known.get(live.session) : null)) : null;
      const name = m ? (m.name || m.projectName) : live ? T("cp_term_kind", live.kind) : "";
      const tint = m ? tintFor(m.projectKey) : live ? "var(--cp-accent)" : "";
      if (kind === "empty") return { kind, name: T("cp_pane_empty_name"), tint, note: T("cp_pane_empty") };
      if (kind === "label") {
        return m ? { kind, name, title: name, tint, who: [name, stateWord(m)].filter(Boolean).join(" · "), note: T("cp_pane_label") }
          : { kind, name: T("cp_pane_gone"), tint, who: T("cp_pane_gone"), note: "" };
      }
      return { kind, name, title: name, tint };
    };
    const bodies = panesUi.render(tree, lay, metaFor);
    for (const [leafId, id] of shows) {
      const body = bodies.get(leafId);
      if (body) termUi.show(id, { host: body });   // 再描画からは focus しない（指示欄の打鍵を奪わない）
    }
    if (focusKind === "dialog" && bodies.get(tree.focus)) pairTo(bodies.get(tree.focus));
  }

  function paintPanes() {
    const list = q("#cp-dlg");
    const all = members();
    const live = liveTerm();
    const terminal = showTerminal();
    const toggle = q("#cp-activity-toggle");
    toggle.hidden = !live || !panes.isSingle(tree) || DEMO || !hasRecord(live);   // 記録の無い端末は切り替える先が無い
    toggle.textContent = T(terminal ? "cp_activity_tab" : "cp_activity_terminal");
    toggle.setAttribute("aria-pressed", String(terminal));
    const closeBtn = q("#cp-term-close");
    q("#cp-term").classList.toggle("live", terminal);
    closeBtn.hidden = !live;
    q("#cp-term-acts").hidden = terminal || DEMO;
    // 会話の件数・もっと見る は会話ビューの道具＝端末を出している間は隠す（前の相手の「0」が残った）。
    // R111-B2: 分けたペインで選んでいるのが空・もう居ない人のときも隠す（会話の組は駐車中＝前の相手の件数を見せない）
    q("#cp-term-head").hidden = terminal
      || (!panes.isSingle(tree) && !all.some((x) => x.session === selected));
    hostPanes();
    if (live) {
      if (closeBtn.dataset.term !== live.id) disarmClose();
      closeBtn.dataset.term = live.id;
      closeBtn.dataset.alive = live.alive ? "1" : "0";
      paintClose(live);
    }
    if (terminal) {
      // ターミナル表示の間は作業ビューを畳む。
      const m = all.find((x) => x.session === live.session);
      q("#cp-term-who").textContent = m ? (m.name || m.projectName) : T("cp_term_kind", live.kind);
      q("#cp-term-state").textContent = live.alive ? permWord(m) : T("cp_term_exited");
      q("#cp-term-note").textContent = T("cp_term_live");
      q("#cp-term-dot").style.setProperty("--session", m ? tintFor(m.projectKey) : "var(--cp-accent)");
      q("#cp-pick").textContent = T("cp_pick", q("#cp-term-who").textContent);
      return;
    }
    const member = all.find((x) => x.session === selected || live?.session === x.session);
    const m = member || (live ? { session: selected, name: T("cp_term_kind", live.kind),
      state: live.alive && live.kind !== "shell" ? "working" : "waiting", age: 0 } : null);
    q("#cp-term-who").textContent = m ? (m.name || m.projectName) : "";
    q("#cp-term-state").textContent = m ? [permWord(m), m.now || m.verb || ""].filter(Boolean).join(" · ") : "";
    q("#cp-term-state").title = m && permWord(m) ? T("perm_mode_title") : "";
    q("#cp-compose").hidden = !member;
    // R128: Bot にはターミナルが無い＝「ターミナルへ移動」を出さない（押すと失敗の文で終わっていた）
    q("#cp-jump").hidden = !member || Boolean(m.external) || String(m.session || "").startsWith("oc-") || isBot(m);
    // デモには端末が無い＝足元は理由だけ（「＋ から起こしたセッションだけ打てます」はデモに ＋ が無いので嘘）
    q("#cp-term-note").textContent = m && DEMO ? T("cp_term_demo") : "";
    q("#cp-term-dot").style.setProperty("--session", m ? tintFor(m.projectKey) : "transparent");
    q("#cp-pick").textContent = m ? T("cp_pick", m.name || m.projectName) : T("cp_pick_none");
    if (!m) paintActivity(null);
    if (!m) {
      // 空のオフィスの入口は分けていないときだけ（分けたペインの空・もう居ない人は、そのペインの案内が言う＝会話の組は駐車中）
      if (!panes.isSingle(tree)) return;
      const copy = JSON.stringify([T("cp_empty_title"), T("cp_empty_body"), T("cp_empty_start"), T("cp_empty_demo"), DEMO]);
      if (list.querySelector("#cp-empty-entry")?.dataset.copy === copy) return;
      const entry = el("div", "cpempty-entry");
      entry.dataset.copy = copy;
      entry.id = "cp-empty-entry";
      entry.append(el("b", "cpempty-title", T("cp_empty_title")), el("p", "cpempty-body", T("cp_empty_body")));
      const acts = el("div", "cpempty-acts");
      if (!DEMO) {
        const start = el("button", "cpempty-start", T("cp_empty_start"));
        start.type = "button"; start.id = "cp-empty-start";
        start.addEventListener("click", () => q("#btn-newproj").click());
        acts.append(start);
      }
      const demo = el("button", "cpempty-demo", T("cp_empty_demo"));
      demo.type = "button"; demo.id = "cp-empty-demo";
      demo.addEventListener("click", () => { const url = new URL(location.href); url.searchParams.set("demo", "1"); location.href = url.href; });
      acts.append(demo); entry.append(acts); list.replaceChildren(entry); return;
    }
    paintActivity(m);
    if (member) refreshDialog(m);
  }

  /** 権限モードの語（plan・自動で編集・確認なし…）。default・不明は "" */
  function permWord(m) {
    const pm = permModeOf(m);
    return pm && pm !== "default" ? T("perm_mode", pm) : "";
  }

  // ── b4-mac: 「閉じる」は生きている端末ではプロセスごと止める＝2 回押し（1 回目で 3 秒「もう一度押すと終了」）──
  let closeArmed = 0;
  function disarmClose() {
    if (closeArmed) clearTimeout(closeArmed);
    closeArmed = 0;
  }
  function paintClose(live) {
    const b = q("#cp-term-close");
    const key = !live?.alive ? "cp_term_close" : closeArmed ? "cp_term_end_confirm" : "cp_term_end";
    if (b.textContent !== T(key)) b.textContent = T(key);
    b.classList.toggle("armed", Boolean(closeArmed));
  }

  const render = (w, { table: withTable = true } = {}) => {
    renderChrome(shell, w, T, tray);
    q("#pxcounts").textContent = T("px_counts", hud.board.length,
      hud.board.reduce((n, g) => n + g.sessions.length, 0));
    // R110: 端末で選ぶ人を先に決める（名簿の選択の印はその人に付く＝描いた後に決めると 1 回の apply ぶん印が遅れる）
    ensureSelected();
    if (withTable) renderTable(w);
    renderHistory(shell, w, T, { limit: 3 });
    const all = members();
    q("#pxtoday").textContent = T("px_today", w.today?.sent ?? 0, w.today?.answered ?? 0);
    // b4-mac: 名簿（旧 台帳）の見出しは「要対応・エラー」の数（プロジェクト数・セッション数はヘッダーが言う＝3 か所に同じ数を出さない）
    q("#cp-roster-meta").textContent = T("cp_ledger_meta", all.filter((m) => m.attention).length,
      all.filter((m) => !m.attention && errOf(m)).length);
    const waiting = all.filter((m) => m.attention).length;
    attnChip.hidden = waiting === 0;
    attnChip.textContent = T("cp_attn_chip", waiting);
    attnChip.setAttribute("aria-label", T("cp_attn_chip", waiting));
    paintBeta();
    paintTabs();
    paintReception(w);
    paintPanes();
    scheduleRosterMore();
  };

  const apply = (office) => {
    world = office;
    built = buildWorld(office);
    built.muted = mutedSessions(office);   // R128 #4: 消音の Bot（音＝sounds・画面の通知＝tray で鳴らさない）
    roomWorld = roomWorldFields(built, office);
    arrivals.update(office);
    if (built.lang !== lang()) { setLang(built.lang); strings(); }
    session.markData();
    delivery.update(built);
    sounds.update(built);
    hud.board = departmentBoard(office, built, T);
    tints = tintIndex(hud.board.map((g) => g.key), SESSION_COLORS.length, frozen ? null : tints);
    // R110-S6: 打鍵の音の根拠（Edit/Write で作業中の人数）・新しい証拠（ベル）・「いま」の経過の控え
    typists = (roomWorld?.agents || []).filter((a) => a && a.state === "working" && !a.attention
      && toolClassOf(a) === "edit").length;
    const bell = noteEvidence(built);
    noteSince();
    gauges.start();
    render(built, { table: !replayWorld });
    firstrun.update(built);
    session.applyHashOnce();
    growth.update();
    sheet.refreshGrowth();
    digest?.update();
    redraw();
    if (bell) ambient.playAmbient("bell", shownT);
  };

  const getWorld = () => built;
  const liveKeys = () => new Map(hud.board.flatMap(
    (g) => g.sessions.map((s) => [s.session, {
      key: g.key, name: g.name, sessionName: s.name, vendor: s.vendor,
      brief: { state: s.state, age: s.age, minions: s.minions, pending: false,
        attention: Boolean(s.attention), approvalMin: Number(s.approvalMin) || 0 },
    }])));
  const repaint = () => { if (built) render(built, { table: !replayWorld }); };
  const common = { shell, T, getWorld, render: repaint };
  const modals = initModals({ shell });
  // R105: メンバー先行βの入口（足元の版と［フィードバック］・設定の「診断」「このアプリ」）
  const beta = initBeta({ shell, T, modals, getWorld: () => world, style: STYLES.COCKPIT });
  const footEnd = q("#cp-foot-end");
  const verEl = el("span", "cp-ver", "");
  const fbBtn = el("button", "cpfb", "");
  fbBtn.type = "button"; fbBtn.id = "cp-feedback";
  fbBtn.addEventListener("click", () => beta.open());
  footEnd.replaceChildren(verEl, fbBtn);
  // ヘッダーの「あなた待ち N」（スマホのタブ 1 つ分の核心を Mac でも主役に）。押すと先頭の ❗ の答えへ
  const attnChip = el("button", "cp-attn-chip", "");
  attnChip.type = "button"; attnChip.id = "cp-attn-chip"; attnChip.hidden = true;
  attnChip.addEventListener("click", () => {
    const tray = q("#attn");   // R110: 受付（部屋の下）はどの段でも常に見えている＝切り替えるタブは無い
    tray.scrollIntoView({ block: "nearest" });
    tray.querySelector("button:not([disabled])")?.focus({ preventScroll: true });
  });
  q("#cp-attn-slot").replaceChildren(attnChip);
  const paintBeta = () => {
    const v = world?.app?.version;
    verEl.textContent = v ? T("beta_version", v) : "β";   // 版の分からない world（古い daemon・fixture）でも β の印は出す
    fbBtn.textContent = T("beta_feedback");
    fbBtn.setAttribute("aria-label", T("cp_settings_feedback"));
  };
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
  // b4-mac: 音（以前はシートの音のボタンを押せるのに鳴らなかった＝音の配線が iso にしか無い）。新しい ❗ で chime。
  // Mac の前（このタブが見えている）では鳴らさない（R102-5 と同じ考え・見ている人に音は要らない）。同意の問いは出さない
  //（音はシートのボタンか設定の「通知音」で本人が入れる）。固定時刻・デモは鳴らさない（createSoundNotifications が止める）。
  const sounds = createSoundNotifications({ isolated: DEMO, ask: () => {},
    sound: { playSound: (kind) => { if (document.hidden) playSound(kind); }, shouldAsk: () => false, markAsked, unlockSound } });
  // R110-S6: 環境音（既定 OFF・見ているときだけ）。通知音と同じ型＝AudioContext はジェスチャ（pointerdown・keydown）の中でだけ作る。
  // 固定時刻・デモは無音（createAmbient が何もしない口を返す）
  const ambient = createAmbient({ isolated: DEMO });
  const unlockAmbient = () => ambient.unlockAmbient();
  document.addEventListener("pointerdown", unlockAmbient);
  document.addEventListener("keydown", unlockAmbient);
  const tray = initTray({ ...common, el, attnKeyFor, delivery, sheet, modals, DEMO });
  const hud = { tray, sheet, board: [] };
  const table = initTable({
    shell, T, el, getWorld, getBoard: () => hud.board, selectedId: sheet.selectedId, arrivals,
    sortKey: "aioffice.cockpit.sort",
    // R110 名簿: 行の個体色（タブ・端末の点・部屋と同じ tintFor の 1 本）・証拠は短い語（全文は title）・
    // 選択の印は端末で選んでいる人（指示欄の対象ではない＝タブ・部屋の角かっこと同じ人に付く）
    colorOf: tintFor,
    shortEvidence: true,
    selectedSession: () => roomSelection(),
    preview: (s) => {
      if (s.attention) return { text: s.detail, live: false };
      if (s.state === "working") {
        const item = s.feed?.[0];
        const text = typeof item === "string" ? item : item?.text || item?.t || "";
        // feed の発言の行は「💬 本文」（server の recent_feed）＝絵文字の行頭を出さず、最後の返事と同じ「」で
        const said = /^💬\s*/.test(text) ? text.replace(/^💬\s*/, "") : "";
        return { text: said ? T("cp_activity_quote", said) : text || s.detail, live: true };
      }
      if (replayWorld) return { text: s.detail, live: false };
      const source = [...(world?.employees || []), ...(world?.roster || []).flatMap((p) => [p, ...(p.sessions || [])])]
        .find((p) => p.session === s.session && p.lastSaid);
      const said = String(source?.lastSaid || "").split("\n")[0];
      return { text: said ? T("cp_activity_quote", said) : s.detail, live: false };
    },
    stalled: (s) => !replayWorld && delivery.isStalled(s),
    onPick: (projectId, sessionId) => {
      const a = (built?.agents || []).find((x) => x.id === projectId);
      if (!a) return;
      // 行のクリックは「指示を書く」シートを開く＝端末へはフォーカスしない。ここで端末へ寄せると、
      // 次のフレームで指示欄のフォーカスを奪い、続きの打鍵が本物のシェルで実行される
      // ★別モデルレビュー（中・R106-10）: select() が「下書きが有るので閉じてトースト」側へ入っていた
      //   ときも、ここで無条件に openCompose し直すと実際にはシートが開き直り、トースト文言（閉じました）
      //   と矛盾する。select() がシートの追従／終了を済ませたとき（true）は、ここで開き直さない。
      if (select(sessionId || a.session, { focus: false })) return;
      const member = hud.board.flatMap((g) => g.sessions).find((m) => m.id === a.id && m.session === sessionId);
      if (member && member.session !== a.session) openCompose({ ...member, crew: 1, sessions: [] });
      else openCompose(a);
    },
  });
  let stepping = false;
  /** 部屋の動きの時計が止まっているか＝t が進まない間（固定時刻・「動きを止める」＝視差効果を減らすもここへ流し込む）。
   *  止まっている間、部屋は動きの時計を固定し、歩かせず・✓ を出さない（再生は room.draw の replay が同じことをする）。 */
  const motionStill = () => (frozen && !stepping) || stopMotion;
  // ── 部屋の倍率（P4-2a → R110 骨格 A）: 形で選ぶ整数倍（roomScale が正本・×2〜×4）─────────────────
  const viewportEl = q("#viewport");
  const mainEl = q(".main");
  const rightEl = q(".cpright");
  const receptionEl = q("#win-reception");
  /** 今の段の列の数（1 列・2 カラム・3 カラム）。幅の段は CSS の @media だけが決める＝ここは計算値を読むだけ（段の数字を 2 か所に書かない） */
  const colCount = () => getComputedStyle(mainEl).gridTemplateColumns.trim().split(/\s+/).length;
  const gapOf = (e, axis) => parseFloat(getComputedStyle(e)[axis]) || 0;
  const heightOf = (sel) => {
    const e = q(sel);
    // R106-2: #cp-setup-slot は overflow:auto ＝見えている丈が中身より小さいことがある＝scrollHeight（中身の素の丈）も見る
    return e && e.getClientRects().length ? Math.max(e.offsetHeight, e.scrollHeight) : 0;
  };
  /** R111-A2: 窓ごとの部屋以外の丈（見出し・接続の案内・タブ列）＝cplayout の roomAvail／dragRowSplit の chromeH */
  const chromeH = () => ({
    office: heightOf("#sec-office") + heightOf("#cp-setup-slot"),
    reception: heightOf("#sec-reception"),
    roster: heightOf("#sec-roster"),
    terminal: heightOf("#cp-center > .cpsec") + heightOf("#cp-center > .cptabbar"),
  });
  /** R111-A2: 端末の列の最低の幅（配置の widths.termMin・既定 560＝80 桁） */
  const termMinNow = () => layout.widths.termMin ?? TERM_MIN_W_DEFAULT;
  /**
   * R111-A2: 3 カラムの部屋に使ってよい幅と丈＝cplayout の roomAvail（配置に従う・既定の配置では R110 の式と同じ数＝
   * 1440×900 ×3・1920×1080 ×4・1440×640 ×2）。幅＝画面の幅から fixed の列（名簿・受付だけの列）の実幅・溝 2 本・端末の最低幅
   *（端末が別の列のとき）・部屋の窓の外（ROOM_CHROME_W）を引いた残り。部屋の列の今の幅からは測らない（幅が倍率で決まる＝堂々巡り）。
   * 丈＝.main の丈から部屋の窓と相方の見出し・罫・溝と、相方の最低（受付 200・詰めれば ×4 が入る丈だけ 120／名簿 120／端末 200）を引いた残り。
   */
  const roomAvail3 = () => roomAvail(layout, {
    mainW: mainEl.clientWidth, mainH: mainEl.clientHeight, gap: gapOf(mainEl, "columnGap"), termMin: termMinNow(),
    fixedW: { roster: rightEl.offsetWidth, reception: receptionEl.offsetWidth }, chromeH: chromeH(),
  });
  /**
   * 部屋に使ってよい幅（左の余白を除く）。3 カラムは roomAvail3（列の幅を倍率から決める）。2 カラムと 1 列は列の幅を CSS が決める
   *（2 カラム＝端末 80 桁が先）＝部屋の箱の内寸から。
   */
  const roomAvailW = () => {
    if (colCount() === 3) return roomAvail3().availW;
    const pad = parseFloat(getComputedStyle(viewportEl).paddingLeft) || 0;
    return viewportEl.clientWidth - pad;
  };
  /**
   * 部屋に使ってよい丈。3 カラムは roomAvail3。2 カラムは左の列が中身の丈（auto の行）＝自分の丈では測れないので、.main の丈から
   * 部屋以外の段（01 の見出し・接続の案内・04 受付の見出し・窓の罫と溝）と受付の最低（RECEPTION_MIN_H_2COL）と名簿の窓（溝・罫・見出し・
   * 最低 LEDGER_MIN_H）を引く。部屋の段（#stage）自身の丈から測らない（堂々巡り）。
   * 丈は ×2 の 320px を下回らせない（丈で ×1 へ落とさない・足りない分は部屋の段の中のスクロール）。1 列は従来どおり丈 480 で ×2〜×3。
   */
  const roomAvailH = () => {
    const cols = colCount();
    if (cols === 1) return ROOM_AVAIL_H;
    if (cols === 3) return roomAvail3().availH;
    // 01 と 04 の窓の間の溝 8（2 カラムは左の列の row-gap）＋2 枚の窓の上下の罫
    let used = 8 + 4;
    for (const sel of ["#sec-office", "#cp-setup-slot", "#sec-reception"]) used += heightOf(sel);
    used += (parseFloat(getComputedStyle(rightEl).marginTop) || 0) + 2 + heightOf("#sec-roster");
    const reserve = RECEPTION_MIN_H_2COL + LEDGER_MIN_H;
    return Math.max(ROOM.h * ROOM_SCALE_MIN, Math.min(ROOM_MAX_H, mainEl.clientHeight - used - reserve));
  };
  // 下限: 2 カラムだけ ×1（900〜1021 の左の列は ×2 の 386px に足りない＝端末 80 桁が先）・1 列と 3 カラムは ×2。
  // R110-S6: 眺めるモードは画面いっぱい（下端のティッカーの帯を引いた丈）から ×2〜×6（1920×1080＝×6・1440×900＝×5）
  // R111-A2: 3 カラムは配置の roomCap（境目のドラッグで部屋を小さくした上限・2|3）で頭を押さえる（2 カラム・1 列には配置を効かせない）
  const roomScaleNow = () => {
    if (watching) return roomScale(innerWidth, innerHeight - WATCH_TICKER_H, ROOM_SCALE_MIN, ROOM_SCALE_WATCH_MAX);
    if (colCount() !== 3) return roomScale(roomAvailW(), roomAvailH(), colCount() === 2 ? 1 : ROOM_SCALE_MIN);
    const a = roomAvail3();
    return Math.min(layout.widths.roomCap ?? ROOM_SCALE_MAX, roomScale(a.availW, a.availH, ROOM_SCALE_MIN));
  };
  const room = initRoom({
    host: viewportEl,
    frozen: motionStill,
    // 固定時刻（?t=）では前回の席を持ち越さない＝同じ world なら必ず同じ配置（golden・demo_clip）
    keepPrev: !frozen,
    colorOf: tintFor,
    scale: roomScaleNow(),
    // 暮らし（散歩）。?life=0 で切る（スモークの「他の人は動かない」を前提にする検査の口）
    life: new URLSearchParams(typeof location === "undefined" ? "" : location.search).get("life") !== "0",
  });
  // 形が変わったら倍率を選び直す。替わったときだけ焼き置きを作り直して（帳簿は持ち越す）、同じ t で描き直す
  //（部屋は冪等＝固定時刻でも同じ絵）。観測の中で canvas の寸法を替えると観測がもう一周する＝次のフレームへ送る
  // 今の倍率は #stage の data-room-scale にも出す（CSS が部屋の箱の丈を倍率に合わせる口＝×2 は 320px。
  // 1 列の .cpstage は min-height 480 のまま＝×2 だと下が 160px 空く。直すのは第 B 波の CSS）
  const stageEl = q("#stage");
  // 部屋の段の丈＝絵の丈（多段の CSS が --cp-room-h で段を合わせる＝部屋の下に空きを作らない）。
  // R110: 3 カラムの左の列の幅も倍率から書く（--cp-left-w = 184k + 18 を .main に・×3＝570・×4＝754）。2 カラムと 1 列では消す
  //（2 カラムは style.css の clamp＝端末 80 桁が先・1 列は使わない）。段をまたいでも倍率が同じなら setScale は false を返す＝毎回呼ぶ
  // R111-A2: 窓の配置も同じ所で書く（paintLayout）。既定の配置では今どおり --cp-left-w だけ（＋--cp-room-h・--cp-sheet-h）＝
  // --cp-tpl-*／--cp-area-* は書かない＝CSS の静的な既定の升目が描く（golden の前提）。既定でないときだけ gridTemplate の結果を
  // .main の inline に書く（升目・各窓の grid-area・トーストの左右＝端末の列の左右）。2 カラム・1 列では inline を全部消す（CSS の段が勝つが保険）。
  // 前回と同じ文字列なら書かない（RO の往復を止める）
  /** 既定でないときに .main へ書く変数（2 カラム・1 列・既定では全部消す） */
  const LAYOUT_VARS = ["--cp-tpl-cols", "--cp-tpl-rows", "--cp-tpl-areas", "--cp-area-office", "--cp-area-reception",
    "--cp-area-terminal", "--cp-area-roster", "--cp-area-sheet", "--cp-area-rowsplit", "--cp-toast-left", "--cp-toast-right"];
  const colHandles = [...mainEl.querySelectorAll(':scope > .cpsplit[data-split="col"]')];
  let layoutSig = "";               // 直近に .main へ書いた配置の変数（JSON・空＝何も書いていない）
  /** grid-template-columns の値を列ごとに割る（minmax(…, 1fr)・clamp(…) の中の空白では割らない） */
  const splitTracks = (str) => {
    const out = [];
    let depth = 0;
    let cur = "";
    for (const ch of str) {
      if (ch === "(") depth += 1;
      if (ch === ")") depth -= 1;
      if (/\s/.test(ch) && depth === 0) { if (cur) out.push(cur); cur = ""; } else cur += ch;
    }
    if (cur) out.push(cur);
    return out;
  };
  /** 既定でない配置の .main の変数（升目・各窓・トーストの左右）。端末の列（flex）の外の列は長さが決まっている（px・clamp）＝calc で足す */
  const layoutVars = (k) => {
    const g = gridTemplate(layout, { k, mainW: mainEl.clientWidth, termMin: termMinNow() });
    const tracks = splitTracks(g.cols);
    const fc = flexColumn(layout);
    const side = (list) => (list.length ? `calc(${list.map((t) => `${t} + ${GUTTER}px`).join(" + ")})` : "0px");
    return {
      g,
      vars: {
        "--cp-tpl-cols": g.cols, "--cp-tpl-rows": g.rows, "--cp-tpl-areas": g.areas,
        "--cp-area-office": g.area.office, "--cp-area-reception": g.area.reception, "--cp-area-terminal": g.area.terminal,
        "--cp-area-roster": g.area.roster, "--cp-area-sheet": g.area.sheet, "--cp-area-rowsplit": g.area.rowsplit,
        "--cp-toast-left": side(tracks.slice(0, fc)), "--cp-toast-right": side(tracks.slice(fc + 1)),
      },
    };
  };
  const paintLayout = () => {
    const k = room.stats().scale;
    if (stageEl.dataset.roomScale !== String(k)) stageEl.dataset.roomScale = String(k);
    plateBox = null;                 // R110-S6: 名札の層を canvas に合わせ直す（次の描画で測る）
    // R111-A2: 帯のキーの入口（tabindex・読み上げ）は 3 カラムで眺めるモードでない間だけ（入口を閉じたらメニューとドラッグも畳む）
    layoutUi?.paintBands();
    // R110-S6: 眺めるモードの間は列の幅と段の丈を書かない（隠れている端末の列を縮めると、本物の端末が桁数を変える）
    if (watching) return;
    // R111-S0: 部屋の段の丈は .main に書く（#win-office は継承で読む）。3 カラムの左の列は display: contents＝窓の置き場が
    // 列の箱に縛られない（窓を入れ替えても部屋の窓が丈を受け取れる）
    if (mainEl.style.getPropertyValue("--cp-room-h") !== `${ROOM.h * k}px`) mainEl.style.setProperty("--cp-room-h", `${ROOM.h * k}px`);
    const three = colCount() === 3;
    const leftW = three ? `${ROOM.w * k + LEFT_CHROME}px` : "";
    if (mainEl.style.getPropertyValue("--cp-left-w") !== leftW) {
      if (leftW) mainEl.style.setProperty("--cp-left-w", leftW);
      else mainEl.style.removeProperty("--cp-left-w");
    }
    const lv = three && !isDefaultLayout(layout) ? layoutVars(k) : null;
    const sig = lv ? JSON.stringify(lv.vars) : "";
    if (sig !== layoutSig) {
      layoutSig = sig;
      for (const name of LAYOUT_VARS) {
        const v = lv?.vars[name];
        if (v) mainEl.style.setProperty(name, v);
        else mainEl.style.removeProperty(name);
      }
      // 列の境目の取っ手は第 2・第 3 列の左の溝（colsplits＝0 始まりの列の番号・どの配置でも [1, 2]）
      const at = lv?.g.colsplits || [1, 2];
      for (const h of colHandles) {
        const col = String((at[Number(h.dataset.between)] ?? Number(h.dataset.between) + 1) + 1);
        if (h.style.gridColumnStart !== col) h.style.gridColumn = col;
      }
    }
    // 端末が 2 窓の列に入った配置では、端末の升目は列の丈の一部＝指示欄（既定 45dvh）の上限をその升目から決める
    //（升目の丈 − 溝 8 − 端末の最低 200・下限 SHEET_CAP_MIN）。升目の丈＝端末の窓の外形＋margin-bottom（指示欄の開閉で変わらない）
    const cap = lv && layout.cols[stackedColumn(layout)].includes("terminal") ? sheetCap() : "";
    if (mainEl.style.getPropertyValue("--cp-sheet-cap") !== cap) {
      if (cap) mainEl.style.setProperty("--cp-sheet-cap", cap);
      else mainEl.style.removeProperty("--cp-sheet-cap");
    }
  };
  /** 指示欄の丈の下限（見出し・定型 1 段・入力欄が入る丈＝端末の升目が低くても書ける） */
  const SHEET_CAP_MIN = 160;
  const sheetCap = () => {
    const e = q("#cp-center");
    const area = e.getBoundingClientRect().height + (parseFloat(getComputedStyle(e).marginBottom) || 0);
    return `${Math.floor(Math.max(SHEET_CAP_MIN, area - GUTTER - PARTNER_MIN_H.terminal))}px`;
  };
  let fitRaf = 0;
  /**
   * 倍率を選び直して、列の幅と配置を書く。R111-A2: 配置を替えた直後は、書いた升目で測り直すと倍率が変わることがある
   *（fixed の列に来た窓の幅・部屋の相方の見出し）＝変わらなくなるまで（3 回まで）同じフレームで繰り返す。既定の配置では測る値が
   * 倍率に依らない＝1 回で止まる（R110 と同じ）
   */
  const fitScale = () => {
    let changed = false;
    for (let i = 0; i < 3; i += 1) {
      const c = room.setScale(roomScaleNow());
      paintLayout();
      if (!c) break;
      changed = true;
    }
    return changed;
  };
  const fitRoom = () => {
    fitRaf = 0;
    if (fitScale()) redraw();
  };
  paintLayout();
  if (!isDefaultLayout(layout)) fitScale();   // 保存した配置で開いた: 書いた升目で倍率を選び直す（最初のフレームから正しい幅・まだ描かない）
  const roomRo = typeof ResizeObserver === "function"
    ? new ResizeObserver(() => { if (!fitRaf) fitRaf = requestAnimationFrame(fitRoom); }) : null;
  roomRo?.observe(viewportEl);
  roomRo?.observe(stageEl);
  // 接続の案内を開閉すると残りの丈が変わる。R111-S0: 3 カラムの左の列は display: contents（箱が無く観測が鳴らない）＝
  // 4 つの窓そのものを見る（右の列＝名簿の幅の変化も部屋の幅の残りを変えるのに見ていなかった穴も塞ぐ）
  for (const id of ["#win-office", "#win-reception", "#cp-center", "#cp-right"]) {
    const e = q(id);
    if (e) roomRo?.observe(e);
  }
  roomRo?.observe(mainEl);   // R110: 3 カラムは左の列の幅が倍率で固定＝画面の幅だけが変わっても部屋の箱は鳴らない
  // R106-2: 窓の外形だけでは「詳しく」展開で起きる変化を捕まえない（first-run-01・実測 clientH 72/scrollH 143 相当の
  // 固着）＝接続の案内（#cp-setup-slot）自身の丈もここで見る（開閉で丈が変わる＝RO が直接発火する）
  const setupSlotEl = q("#cp-setup-slot");
  if (setupSlotEl) roomRo?.observe(setupSlotEl);
  // R111-S0: 指示欄（#sheet）は行の丈を取らない（丈 0px の行から上へはみ出す・style.css）＝開いている間の丈を .main に書き
  // （--cp-sheet-h）、CSS が端末の窓をその丈＋溝 8 だけ持ち上げる。書くのは次のフレーム（観測の中で書くと、同じ深さの
  // #cp-center の観測が RO の一周を越えて loop のエラーになる）。書くまでの 1 フレームは CSS の既定（45dvh／⤢ 70dvh）
  const sheetEl = q("#sheet");
  let sheetH = 0, sheetRaf = 0;
  const paintSheetH = () => {
    sheetRaf = 0;
    const v = sheetH > 0 ? `${sheetH}px` : "";
    if (mainEl.style.getPropertyValue("--cp-sheet-h") === v) return;
    if (v) mainEl.style.setProperty("--cp-sheet-h", v);
    else mainEl.style.removeProperty("--cp-sheet-h");
  };
  const sheetRo = typeof ResizeObserver === "function" && sheetEl
    ? new ResizeObserver(([entry]) => {
      sheetH = entry.borderBoxSize?.[0]?.blockSize ?? entry.target.getBoundingClientRect().height;
      if (!sheetRaf) sheetRaf = requestAnimationFrame(paintSheetH);
    }) : null;
  sheetRo?.observe(sheetEl);
  // ── R111-A2: 窓の配置の操作（題名の帯のドラッグ・境目のドラッグ・Alt+矢印・メニュー・⚙・probe が同じ口を通る）──────────
  /** 配置を替える（同じ中身なら何もしない＝false）。save=false は境目のドラッグの途中（離したときに persist で 1 回だけ保存） */
  const setLayout = (next, { save = true } = {}) => {
    const n = normalizeLayout(next);
    if (serializeLayout(n) === serializeLayout(layout)) return false;
    layout = n;
    if (save) saveLayout(layout);
    paintLayout();                   // 新しい升目を先に書く（倍率の測り直しが新しい配置の窓の幅・見出しを読む）
    fitRoom();
    return true;
  };
  /** 配置を初期化（配置・境目を既定へ・鍵を消す）。⚙「配置を初期化」・メニュー・probe（cpResetLayout）が呼ぶ 1 本の口
   *（R111-B2: ペインの初期化はここに足す） */
  const layoutApi = {
    reset() {
      const changed = setLayout(DEFAULT_LAYOUT);
      saveLayout(DEFAULT_LAYOUT);    // 既定のまま鍵だけ残っていた（読めない古い値など）ときも消す
      const panesChanged = resetPanes();   // R111-B2: 端末のペインも 1 枚へ（鍵 aioffice.cockpit.panes も消す）
      return changed || panesChanged;
    },
  };
  layoutUi = initLayoutUi({
    root, shell, mainEl, T,
    getLayout: () => layout,
    setLayout,
    persist: () => saveLayout(layout),
    reset: () => layoutApi.reset(),
    // 人の操作の入口（帯のドラッグ・キー・メニュー・境目）は 3 カラムの間だけ・眺めるモードとデモでは閉じる
    enabled: () => colCount() === 3 && !watching && !DEMO,
    measurable: () => colCount() === 3,
    measure: {
      gap: () => gapOf(mainEl, "columnGap") || GUTTER,
      colH: () => mainEl.clientHeight,
      chromeH,
      // 端末 40 桁の幅（見えている端末の字の幅から・term.js の widthForCols が無い／測れないときは退避値）。ドラッグを始めたときに 1 回測る
      termFloor: () => termUi?.widthForCols?.(TERM_MIN_COLS) ?? TERM_MIN_W_FLOOR,
      // 丈が許す部屋の倍率の上限（列の境目で部屋を広げても、丈が足りない倍率へはスナップさせない＝1440×900 は ×3 まで）
      roomKMax: () => (colCount() === 3 ? Math.floor(roomAvail3().availH / ROOM.h) : null),
      // 上限（roomCap）を掛けずに今の幅と丈が選ぶ倍率。これ以上の上限は「上限」として意味が無い（A4 の門が見つけた穴＝
      // 1440×900 で境目を 20px 引くと見た目は動かないのに roomCap 3 が保存され、後で 1920 に広げても ×4 に伸びなかった）
      roomKFit: () => {
        if (colCount() !== 3) return null;
        const a = roomAvail3();
        return roomScale(a.availW, a.availH, ROOM_SCALE_MIN);
      },
    },
  });
  layoutUi.paintBands();
  const scene = nullScene(table, room, fitRoom);
  /** 部屋に角かっこで示す選択＝中央の端末が映しているセッション（台帳にまだ居ない端末は、その端末のセッション） */
  const roomSelection = () => {
    if (!selected) return null;
    if (!selected.startsWith(TERM_PREFIX)) return selected;
    return termUi?.get(selected.slice(TERM_PREFIX.length))?.session || null;
  };
  /** 刻みの格子に揃えた時刻。固定時刻（?t=）は揃えない（時計が動かない・debug.step が格子を決める） */
  const gridT = (t) => {
    if (frozen) return t;
    const fps = room.fps();
    return Math.floor(t * fps) / fps;
  };
  /**
   * 今の格子で描き直す（新しいデータ・ボタン・再生を閉じた）。格子が進んでいなければ同じ t で描き直す（部屋は冪等）。
   * R110-S4 の残課題（S6）: データの到着で歩き出す人（❗・退場・用事）の立ち上がり（pxwalk の RISE_SEC＝1/6 秒）は、この描き直しの
   * 時刻から数える。誰も歩いていない間の格子（6fps）に揃えると時刻が最大 1/6 秒手前になり、次の刻み（12fps＝stats().walking が
   * 立ち上がりの人も数える）が来たときには立ち上がりが終わっていた（食われた）。描き直しは常に歩く人の格子（12fps）に揃える＝
   * 手前にずれるのは 1/12 秒まで＝立ち上がりの 2 刻みのうち少なくとも 1 刻みは画面に出る。誰も歩き出さない描き直しが 1/12 の格子に
   * 乗っても害は無い（格子の上の時刻は戻さない・次の 6fps の格子へは onFrame が進める）。固定時刻は揃えない（gridT と同じ）
   */
  const redraw = () => draw(Math.max(frozen ? now() : Math.floor(now() * ROOM_FPS.walk) / ROOM_FPS.walk, shownT));
  /** rAF: 格子が進んだとき・描き直しを頼まれたとき・選択が変わったときだけ描く */
  let activityTick = -1;
  const onFrame = (t) => {
    // R101: プロンプトのカーソル（530ms）。固定時刻・動きを止める設定では点いたまま＝golden は決まった絵
    const cur = motionStill() || Math.floor(t / 0.53) % 2 === 0 ? "on" : "off";
    if (root.dataset.cursor !== cur) root.dataset.cursor = cur;
    if (!built) return;
    if (activityTick !== Math.floor(t)) {
      activityTick = Math.floor(t);
      if (!showTerminal()) for (const n of q("#cp-dlg").querySelectorAll("[data-since]"))
        n.textContent = formatDuration(epochNow() - Number(n.dataset.since));
    }
    const tq = Math.max(gridT(t), shownT);
    if (tq > shownT || dirty || roomSelection() !== drawnSel) draw(tq);
  };
  // OS の設定が変わったら、動きの状態そのものを合わせる（ボタンで上書きしていても、OS の新しい値が勝つ＝見本と同じ）
  const onReduced = (e) => { reduceMotion = Boolean(e.matches); stopMotion = reduceMotion; paintMotion(); termUi?.setMotion(); redraw(); };
  reducedMq?.addEventListener?.("change", onReduced);

  // 部屋のロボをクリック → 中央の端末がそのセッションに切り替わる（本人の要望の芯）
  q("#viewport").addEventListener("click", (e) => {
    const a = room.pick(e.clientX, e.clientY);
    if (a) {
      // 眺めるモードでは端末へフォーカスを動かさない（隠れた端末へ打鍵が流れない）＝選択の角かっこと挨拶だけ
      select(a.session, { focus: !watching });
      // クリックの手応え: その人が 0.9 秒手を振る（止めている間は room が受けない）
      if (room.setGreet(a.session || a.id)) { dirty = true; redraw(); }
    }
  });
  // b4-mac: 部屋のロボに乗せたら、名前と状態の札（DOM・canvas には描かない）と指のカーソル・台帳の該当行に .hov。
  // 以前は乗せても何も出ず、クリックして中央が切り替わるまで誰か分からなかった。固定時刻（?t=）では出さない（golden は変わらない）
  const hoverTip = el("div", "cphover");
  hoverTip.hidden = true;
  hoverTip.setAttribute("role", "tooltip");
  root.append(hoverTip);
  let hoverRaf = 0;
  let hoverAt = null;
  let hoverSess = null;
  const stateWord = (m) => {
    if (m.attention || errOf(m)) return kindTag(m).replace(/^! /, "");
    return T(m.state === "working" ? "cp_kind_working" : m.state === "resting" ? "cp_kind_resting" : "cp_kind_waiting");
  };
  const paintHover = () => {
    hoverRaf = 0;
    const a = hoverAt && room.pick(hoverAt.x, hoverAt.y);
    const m = a && members().find((x) => x.session === a.session);
    const sess = m ? m.session : null;
    viewportEl.style.cursor = m ? "pointer" : "";
    if (sess !== hoverSess) {
      for (const r of q("#agents").querySelectorAll(".pxrow.hov")) r.classList.remove("hov");
      if (sess) for (const r of q("#agents").querySelectorAll(`.pxrow[data-session="${CSS.escape(sess)}"]`)) r.classList.add("hov");
      hoverSess = sess;
    }
    if (!m) { hoverTip.hidden = true; return; }
    const pt = room.point(m.session) || room.point(m.id);
    if (!pt) { hoverTip.hidden = true; return; }
    const text = `${m.name || m.projectName} · ${stateWord(m)}`;
    if (hoverTip.textContent !== text) hoverTip.textContent = text;
    hoverTip.style.left = `${Math.round(pt.left)}px`;
    hoverTip.style.top = `${Math.round(pt.top - 6)}px`;
    hoverTip.hidden = false;
  };
  viewportEl.addEventListener("pointermove", (e) => {
    if (frozen || e.pointerType === "touch") return;
    hoverAt = { x: e.clientX, y: e.clientY };
    if (!hoverRaf) hoverRaf = requestAnimationFrame(paintHover);
  });
  viewportEl.addEventListener("pointerleave", () => {
    hoverAt = null;
    if (!hoverRaf) hoverRaf = requestAnimationFrame(paintHover);
  });
  // 行 ↔ 部屋の対応（行に乗せると、その人の足元に線＝選択の角かっことは描き分ける）。次の rAF で描き直す
  q("#agents").addEventListener("mouseover", (e) => {
    const row = e.target.closest?.(".pxrow");
    const proj = row?.classList.contains("proj");
    room.setHover(row
      ? { key: proj ? row.dataset.group : null, session: proj ? null : row.dataset.session }
      : null);
    dirty = true;
  });
  q("#agents").addEventListener("mouseleave", () => { room.setHover(null); dirty = true; });
  q("#agents").addEventListener("dblclick", (e) => {
    const row = e.target.closest(".pxrow");
    const a = row && (built?.agents || []).find((x) => x.id === row.dataset.project);
    if (a && !a.external) jumpTerminal(row.dataset.session || a.session, a.name);
  });
  // タブとロボは同じ「選ぶ」1 本に流す（同じことをする口が複数あっても挙動は 1 つ）
  // ── 本物の端末 ───────────────────────────────────────────────
  termUi = initTerm({
    host: q("#cp-xterm"),
    frozen,
    motionStill,
    // R110: 端末の文字の大きさ＝⚙「文字の大きさ」（既定 14）。80 桁が入らない幅では term.js が max(11, これ − 2) へ下げる
    fontSize: () => cpFont,
    // R111-A2: 本人が境目で端末の列の幅を変えている間（部屋×端末の境目＝termMin・名簿／受付の境目＝fixed の幅）は字を下げない
    //（桁が減るだけ・term.js が 80 桁の base−2 を飛ばす＝B2）。既定の配置と窓の入れ替えだけの配置は R110 の規則（80 桁が入らなければ base−2）のまま
    narrow: () => (layout.widths.termMin ?? TERM_MIN_W_DEFAULT) < TERM_MIN_W_DEFAULT
      || layout.widths.roster != null || layout.widths.reception != null,
    // デモ（?demo=1・公開デモ）では本物の端末に触らない。一覧も取らない・＋も出さない
    // （送信・雇うも DEMO で止めている。デモに本物のセッションを混ぜない＝別モデルレビュー）
    disabled: DEMO,
    // R111-B2: 80 桁の自動縮小は見えているペインが 1 つのときだけ・text/dims/modes の省略時の相手は選んでいるペインの端末
    single: () => paneShown <= 1,
    focusedId: () => liveTerm()?.id ?? null,
    onChange: () => {
      paintTermMeta();
      restorePanes();                // 最初の一覧の後で保存の木を当てる（一覧に無い端末の葉は空に）
      // 一覧から消えた端末の葉は空に（同じ id の端末はもう戻らない）
      let next = tree;
      for (const leaf of panes.leaves(next)) {
        if (leaf.sel?.startsWith(TERM_PREFIX) && !termUi.get(leaf.sel.slice(TERM_PREFIX.length))) next = panes.clear(next, leaf.sel);
      }
      commit(next, { paint: false });
      if (built) { ensureSelected(); paintTabs(); paintPanes(); }
    },
    onInputError: () => delivery.showToast(T("cp_term_input_err")),
  });
  // ── R111-B2: ペインの操作（＋端末のメニュー・キー・見出しのボタン・probe が同じ関数を呼ぶ）────────────
  /** 分ける（新しい葉は空で、選んでいるペインになる）。3 カラムだけ。今の窓に 2 つとも入らない（40 桁・8 行を割る）なら分けない
   *（分けると畳みの規則で新しい空のペインだけが見え、今の端末が黙って隠れる＝1440×900 の ×3 の部屋だと左右は入らない）＝理由をトーストで */
  function splitPane(leafId, dir) {
    if (!paneSplitOk()) return false;
    const from = leafId ?? tree.focus;
    const next = panes.split(tree, from, dir);
    if (next === tree) return false;
    if (!splitFits(next, from)) {
      delivery.showToast(T(dir === "h" ? "cp_pane_no_room_h" : "cp_pane_no_room_v"), false);
      return false;
    }
    return commit(next, { user: true });
  }
  /** 分けた木（next）で、分けた元と新しい葉が両方とも今の窓に見えるか */
  function splitFits(next, from) {
    const lay = panesUi?.measure(next);
    return Boolean(lay && lay.rects.has(next.focus) && lay.rects.has(from));
  }
  /** ペインを閉じる（端末は止めない＝タブに残る）。最後の 1 つは閉じない */
  function closePane(leafId) {
    if (DEMO || !panesUi) return false;
    return commit(panes.close(tree, leafId ?? tree.focus), { user: true });
  }
  /** 選んでいるペインを替える（押した・キーで隣へ） */
  function focusPane(leafId) {
    if (DEMO || !panesUi) return false;
    return commit(panes.setFocus(tree, leafId), { user: true });
  }
  /** 境目の位置（0.1〜0.9） */
  function setPaneRatio(splitId, ratio) {
    if (DEMO || !panesUi) return false;
    return commit(panes.setRatio(tree, splitId, ratio));
  }
  /** 葉へ相手を入れる（同じ相手を持つ別の葉は空になる） */
  function assignPane(leafId, sel) {
    if (DEMO || !panesUi) return false;
    return commit(panes.assign(tree, leafId, sel), { user: true });
  }
  /** 配置を初期化の口（⚙「表示」・R111-A2 の配置の初期化からも呼ぶ）: 分割を畳んで 1 枚に戻し、保存の鍵を消す（選んでいる相手は残す） */
  function resetPanes() {
    if (DEMO) return false;
    const sel = focusSel();
    let changed = false;
    // 既に 1 枚で同じ相手なら木は作り直さない（毎回 true を返すと「配置を初期化」の 2 回目が「変わった」になる＝A4 の門）
    if (!panes.isSingle(tree) || panes.leaves(tree)[0]?.sel !== sel) {
      const one = panes.empty();
      changed = commit(panes.assign(one, one.focus, sel), { user: true });
    }
    try { if (panesStore) localStorage.removeItem(PANES_KEY); } catch { /* 保存できない端末 */ }
    return changed;
  }
  /** 本人のキー操作で隣のペインへ: 端末ならそこへ打てるように、端末でなければ前のペインの端末からフォーカスを外す */
  function moveFocus(dir) {
    const lay = panesUi?.lastLayout();
    const next = lay && panes.neighbor(lay, tree.focus, dir);
    if (!next || !focusPane(next)) return false;
    const live = liveTerm();
    if (live && showTerminal()) termUi.show(live.id, { focus: true });
    else blurTerm();
    return true;
  }
  /** 端末（xterm の入力欄）に残ったフォーカスを外す（選んでいないペインの端末へ打鍵が流れない） */
  function blurTerm() {
    const act = document.activeElement;
    if (act && xtermEl.contains(act)) act.blur?.();
  }
  panesUi = DEMO ? null : initPanes({
    root: xtermEl, T,
    onFocus: (leafId) => { focusPane(leafId); },
    onSplit: (leafId, dir) => { if (splitPane(leafId, dir)) blurTerm(); },
    onClose: (leafId) => { closePane(leafId); },
    onRatio: (splitId, ratio) => { setPaneRatio(splitId, ratio); },
    cell: () => termUi.cell(),
    // 窓の大きさが変わった（列の幅・指示欄・画面の段・2 カラムとの行き来）＝置き場所だけ描き直す（会話は読み直さない）
    onResize: () => { if (built) hostPanes(); },
  });
  // 端末を出すかの軸は「デモかどうか」だけ（統括の裁定）。幅・タッチ・pointer では決めない＝本番の cockpit は
  // 127.0.0.1 でしか開けない＝常に Mac の前（窓が細くても・タッチでも端末は出す）。デモ（?demo=1・公開デモ）は
  // 端末の画面（xterm）とタブ列（＋ を含む）を畳み、足元に理由を書く（paintPanes）。許可待ちの表示（#cp-work・#cp-dlg）は
  // 畳まない＝何を待っているかはデモでも読める
  q("#cp-term-new").hidden = DEMO;
  if (DEMO) { q(".cptabbar").hidden = true; q("#cp-xterm").hidden = true; }
  const menu = q("#cp-term-menu");
  const newBtn = q("#cp-term-new");
  const closeMenu = () => { menu.hidden = true; newBtn.setAttribute("aria-expanded", "false"); };
  /** ＋ で起こす先＝いま選んでいるセッションのフォルダ（無ければホーム・shell だけ）。R111-B2: 分けたばかりの空のペインは
   *  直前に選んでいたセッションのフォルダ（分けた元の仕事の続きを開く） */
  const whereNow = () => {
    const m = members().find((x) => x.session === (selected || lastSession));
    return m ? { projectId: m.id || m.projectKey, name: m.projectName } : { projectId: null, name: "" };
  };
  /** メニューの中身を今に合わせる（起こす先・claude/codex の可否・分割の項目は 3 カラムだけ・葉が上限なら押せない） */
  const paintMenu = () => {
    const w = whereNow();
    q("#cp-term-where").textContent = T("cp_term_where", w.name);
    // claude / codex はプロジェクトの中でだけ起こす（ホームで起こすと、どの仕事か分からない）
    for (const b of menu.querySelectorAll("[data-kind]")) b.disabled = b.dataset.kind !== "shell" && !w.projectId;
    const canSplit = paneSplitOk();
    menu.querySelector(".cpmenu-sep").hidden = !canSplit;
    for (const b of menu.querySelectorAll("[data-pane]")) {
      b.hidden = !canSplit;
      b.disabled = panes.leafCount(tree) >= panes.MAX_PANES;
    }
  };
  newBtn.addEventListener("click", () => {
    const open = menu.hidden;
    menu.hidden = !open;
    newBtn.setAttribute("aria-expanded", String(open));
    if (open) {
      paintMenu();
      menu.querySelector("[data-kind]:not([disabled])")?.focus();
    }
  });
  menu.addEventListener("click", async (e) => {
    // R111-B2: 「右に／下に分割して開く」＝選んでいるペインを分ける（新しい空のペインが選ばれる）。メニューは開いたまま＝
    // 続けて上の Claude・Codex・シェルを押すと新しいペインに入る
    const sp = e.target.closest("[data-pane]");
    if (sp) {
      if (sp.disabled || sp.hidden) return;
      if (splitPane(tree.focus, sp.dataset.pane)) blurTerm();
      paintMenu();
      menu.querySelector("[data-kind]:not([disabled])")?.focus();
      return;
    }
    const b = e.target.closest("[data-kind]");
    if (!b || b.disabled) return;
    closeMenu();
    try {
      const term = await termUi.open(b.dataset.kind, whereNow().projectId);
      // 選んでいるペインへ入れる（ensureSelected が claude の起動後にセッションへ付け替える）
      commit(panes.assign(tree, tree.focus, TERM_PREFIX + term.id), { paint: false });
      ensureSelected();
      paintTabs();
      paintPanes();
      if (showTerminal()) termUi.show(term.id, { focus: true });
      else blurTerm();
    } catch (err) {
      delivery.showToast(T("cp_term_err", err?.message));
    }
  });
  document.addEventListener("click", (e) => {
    if (!menu.hidden && !e.target.closest(".cptabbar")) closeMenu();
  });
  menu.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeMenu(); newBtn.focus(); } });
  q("#cp-term-close").addEventListener("click", async (e) => {
    const id = e.currentTarget.dataset.term;
    if (!id) return;
    if (e.currentTarget.dataset.alive === "1" && !closeArmed) {
      closeArmed = setTimeout(() => { closeArmed = 0; paintClose(liveTerm()); }, 3000);
      paintClose(liveTerm());
      return;
    }
    disarmClose();
    await termUi.close(id);
    commit(panes.clear(tree, TERM_PREFIX + id), { paint: false });   // その端末を出していた葉は空に（1 枚なら ensureSelected が埋める）
    ensureSelected();
    paintTabs();
    paintPanes();
  });

  // b4-mac: 読み取り専用の端末の足元から「指示を書く」「ターミナルを前へ」（以前は文言だけ＝台帳の行のダブルクリックしか口が無かった）
  q("#cp-compose").addEventListener("click", () => {
    const m = members().find((x) => x.session === selected);
    const a = m && ((built?.agents || []).find((x) => x.session === m.session) || null);
    if (m) openCompose(a || { ...m, crew: 1, sessions: [] });
  });
  q("#cp-jump").addEventListener("click", () => {
    const m = members().find((x) => x.session === selected);
    if (m) jumpTerminal(m.session, m.name || m.projectName);
  });
  // タブ列: 縦のホイールを横へ回す（スクロールバーは隠している＝縦ホイールでは動かなかった）・端の本数を描き直す
  q("#cp-tabs").addEventListener("wheel", (e) => {
    const host = e.currentTarget;
    if (host.scrollWidth <= host.clientWidth || Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
    host.scrollLeft += e.deltaY;
    e.preventDefault();
  }, { passive: false });
  q("#cp-tabs").addEventListener("scroll", () => paintTabEdges(), { passive: true });
  for (const [sel, dir] of [["#cp-tabs-l", -1], ["#cp-tabs-r", 1]]) {
    q(sel).addEventListener("click", () => {
      const host = q("#cp-tabs");
      host.scrollBy({ left: dir * Math.max(120, host.clientWidth * 0.8), behavior: frozen ? "auto" : "smooth" });
    });
  }

  // R110: 名簿（#cp-roster）の行のクリックは table.js の onPick の 1 本だけ（ここでも select すると、onPick が開いた指示欄の
  // 直後に端末へフォーカスを奪い、続きの打鍵が本物のシェルへ流れる）
  q("#cp-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-session]");
    if (b) select(b.dataset.session);
  });
  q("#cp-motion").addEventListener("click", () => {
    stopMotion = !stopMotion;
    paintMotion();
    termUi?.setMotion();
    redraw();
  });

  const firstrun = initFirstRun({ shell, T, scene, DEMO, enabled: true });
  const openHelp = initHelp({ shell, T, modals, keys: "help_keys_cockpit", urls: "help_urls_cockpit",
    blocked: () => shell.classList.contains("replay-active")
      || q("#digest-card")?.hidden === false });
  const isF = (e) => (e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey && !e.altKey && !e.repeat;
  // R111-B2: ペインのキー。⌘\ 左右に分割・⌘⇧\（別名 ⌘⌥\＝Safari の「すべてのタブ」を避ける）上下に分割・⌘⌥X ペインを閉じる・
  // ⌘⇧←→↑↓ 隣のペインへ。⌘⇧W・⌥⌘←→ はブラウザが使う（窓・タブを閉じる／タブの切替）＝使わない。判定は e.code（JIS には
  // Backslash が無い＝¥ の IntlYen・ろ の IntlRo と key の \ ¥ | も受ける・⌥ を押すと key が別の字になる＝X も code で見る）。
  // 端末（xterm の入力欄）の中でも効かせる（window の capture で xterm より先に受け取り、止める＝打鍵として pty へ流れない）。
  // ほかの入力欄（指示欄・絞り込み）・眺めるモード・デモ・3 カラム以外・モーダルの間は受け取らない（ブラウザ・入力欄へ素通し）
  const PANE_DIRS = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
  const isBackslash = (e) => ["Backslash", "IntlYen", "IntlRo"].includes(e.code) || ["\\", "¥", "|"].includes(e.key);
  function paneKey(e) {
    if (!e.metaKey || e.ctrlKey) return false;
    const field = e.target.closest?.("input, textarea, select, [contenteditable=true]");
    if (field && !field.classList.contains("xterm-helper-textarea")) return false;
    let act = null;
    if (isBackslash(e)) {
      const dir = e.shiftKey || e.altKey ? "v" : "h";
      act = () => { if (splitPane(tree.focus, dir)) blurTerm(); };
    } else if (e.code === "KeyX" && e.altKey && !e.shiftKey) {
      act = () => closePane(tree.focus);
    } else if (PANE_DIRS[e.code] && e.shiftKey && !e.altKey && panes.leafCount(tree) > 1) {
      act = () => moveFocus(PANE_DIRS[e.code]);
    }
    if (!act || !paneSplitOk() || !q("#modalwrap").hidden) return false;
    e.preventDefault();
    e.stopPropagation();
    if (!e.repeat) act();
    return true;
  }
  const onHudKey = (e) => {
    // R110-S6: 眺めるモードの間は F と Esc（戻る）のほかのキーを下の面へ通さない（隠れた ❗トレイの 1〜9・⌘K の絞り込み・
    // 本物の端末へ打鍵が流れない）。ブラウザの既定（⌘R など）は止めない＝stopPropagation だけ
    if (watching) {
      e.stopPropagation();
      if (e.key === "Escape" || isF(e)) { e.preventDefault(); setWatch(false); }
      return;
    }
    if (paneKey(e)) return;
    if (e.target.closest?.("input, textarea, select, [contenteditable=true]")) return;
    if (e.key === "?" && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); openHelp(); }
    else if (isF(e) && canWatch()) { e.preventDefault(); setWatch(true); }
  };
  window.addEventListener("keydown", onHudKey, true);
  digest = initDigest({ ...common, DEMO, tray, showToast: delivery.showToast, enabled: true, canvas: null,
    host: q(".cpleft"),
    replay: true,
    beforeOpen: () => { modals.closeModal(); sheet.closeCompose(); }, restore: () => redraw() });
  shell._closeOverlays = () => digest?.close?.();
  const admin = initAdmin({
    version: () => world?.app?.version || null,   // R105: 設定の「このアプリ」に版
    demo: DEMO,
    reapplyWorld: () => { if (world) apply({ ...world, lang: lang() }); },
    ...common, root, lang, setLang, modals, billingOf, fmtTok,
    showToast: delivery.showToast, applyStaticStrings: strings,
    theme: { read: readTheme, apply: applyTermTheme },
    // R110-S6: 「表示」の節（テーマ・画面の下）に「文字の大きさ」と「環境音」。文字の大きさは画面の段（data-cp-font）と
    // 全端末（xterm）を同時に替える。環境音は既定 OFF（保存 aioffice.cockpit.ambient・デモには鳴らすものが無い＝出さない）
    renderDisplaySettings: (seg, repaint, note) => {
      seg(T("set_font"), FONT_SIZES.map((n) => [n, T("set_font_px", n)]), cpFont, (v) => {
        applyFont(v, true);
        termUi?.setFont();
        // タブの幅が字で変わる＝縁の本数を数え直す（タブ列は観測していない・名簿の「あと N 件」と部屋の倍率は ResizeObserver が拾う）
        paintTabEdges();
        redraw();                    // 名札の寸法を測り直す
        repaint();
      });
      if (!DEMO) {
        seg(T("set_ambient"), [["off", T("sound_off")], ["on", T("sound_on")]], ambient.ambientOn() ? "on" : "off",
          (v) => { ambient.setAmbient(v === "on"); ambPrimed = false; repaint(); });
        note(T("set_ambient_hint"));
        // R111-A2: 窓の配置を初期化（配置・境目を既定へ・鍵を消す）。押すボタンなので選択中の印（.on）は付けない（current＝null）。
        // デモは配置を変える入口も保存も無い（常に既定）＝出さない
        seg(T("set_layout"), [["reset", T("set_layout_reset")]], null, () => { layoutApi.reset(); repaint(); });
      }
    },
    // 「通知」の節に「通知音」の行（シートの音のボタンと同じ 1 つの状態）
    renderCustomizationSettings: (seg, repaint) => {
      seg(T("snd_title"), [["off", T("sound_off")], ["on", T("sound_on")]],
        soundOn() ? "on" : "off", (v) => { setSound(v === "on"); sheet.paintSound?.(); repaint(); });
    },
  });
  const hire = initHire({ ...common, lang, DEMO, modals, arrivals,
    refresh: () => session.refresh(), showToast: delivery.showToast });
  shell._fx = { wakeActive: delivery.wakeActive, stalled: delivery.isStalled, hoverId: null };
  const session = initSession({ root, shell, T, DEMO, apply, tray, getWorld: () => world, lang,
    onOffline: () => { repaint(); sheet.refreshGrowth(); } });
  // オフライン帯（#offbar）の丈を CSS へ渡す（--cp-offbar-h）。1 列の段では、帯が出ている間トーストを帯の上から積む
  // （style.css の .ui-cockpit.offline #toast）。帯の丈は折り返しで変わる（1 行 34px・390 は 2 行 52px）が、CSS は兄弟の丈を
  // 読めない＝ここで実測する。帯が hidden の間は 0（display:none の箱は丈 0・出入りも ResizeObserver が知らせる＝描く前に届く）
  // R105: ヘッダーの丈（--cp-head-h）＝モーダルをヘッダーの下から出す（スマホの段では 2 段に折れる）
  const headEl = q(".cphead");
  const headRo = typeof ResizeObserver === "function"
    ? new ResizeObserver(() => root.style.setProperty("--cp-head-h", `${Math.ceil(headEl.offsetHeight)}px`)) : null;
  headRo?.observe(headEl);
  const offbarEl = q("#offbar");
  const offbarRo = typeof ResizeObserver === "function"
    ? new ResizeObserver(() => root.style.setProperty("--cp-offbar-h", `${Math.ceil(offbarEl.offsetHeight)}px`)) : null;
  offbarRo?.observe(offbarEl);
  const gauges = initGauges({ ...common, lang, onPins: paintPins, demo: DEMO });
  if (DEMO) await session.loadDemo();
  const stopLoop = loop(onFrame);
  const uninstall = installProbe({
    style: STYLE,
    t: () => now(),
    isReady: () => world !== null,
    dumpWorld: () => summarizeWorld(world),
    inject: apply,
    stats: () => scene.stats(),
    pollMs: () => session.intervalMs(),
    debug: { agentPoint: (id) => table.point(id), rows: () => table.rows(),
      // 部屋の中のロボの位置（スモークの照準）
      roomPoint: (id) => room.point(id),
      // 直近のフレームで部屋に貼った人（論理 px の今の左上・区分・貼ったコマ・印・歩行の旗 entering/leaving）
      roomActors: () => room.actors(),
      // 直近のフレームの全員（貼れなかった人も）のコマ・向き・印と印の置き場所・歩行（missing＝そのベンダーにコマが無い）
      roomMarks: () => room.marks(),
      // 歩行の帳簿の要約（live だけが進める・path は経路の点の数か null）
      roomWalkers: () => room.walkers(),
      // 直近のフレームで貼った順（"person:<key>"・"band:<段>"・"mark:<key>:<印>"・"select:<key>"・"hover:<key>"・"print:<key>"）
      roomLayers: () => room.layers(),
      // 描画の刻みと動きの時計（fps＝次の刻み・still＝止まっている・reduced＝OS の視差効果を減らす（今の値）・
      // stopped＝動きの状態（「動きを止める」と reduce を流し込んだ 1 つ＝ボタンの aria-pressed と同じ））
      roomMotion: () => ({ fps: room.fps(), shownT, still: motionStill(), reduced: reduceMotion, stopped: stopMotion,
        selected: drawnSel, greeting: room.stats().greeting }),
      // 台帳のグループ（部屋の配置の入力）。スモークが roomLayout を自分で呼んで、フッターと突き合わせる
      board: () => hud.board,
      world: () => built,
      selected: () => selected,
      select,
      // 端末の中身・画面モード・桁（id を省くと選んでいるペインの端末）。テスト専用（出力をここより外へ持ち出さない）
      termText: (id) => termUi?.text(id) ?? "",
      termStreams: () => termUi?.streams() ?? 0,
      termModes: (id) => termUi?.modes(id) ?? null,
      termDims: (id) => termUi?.dims(id) ?? null,
      // R111-A2: 窓の配置。cpLayout＝純データ（v・cols・widths・rows）＋既定か（isDefault）・3 カラムで升目に効いているか（applied）・
      // 今の倍率（k）・gridTemplate の計算値（grid＝{cols, rows, areas, area, colsplits}・areas＝grid.area＝各窓の "r1 / c1 / r2 / c2"）・
      // 保存した文字列（stored・固定時刻／デモでは読まない＝null）。操作の口はメニュー・ドラッグ・キーと同じ関数（layout.js の ops）:
      // cpMove(id, col, row)＝moveWindow・cpSwap(a, b)・cpNudge(id, "left"|"right"|"up"|"down")・
      // cpSplitTo(between 0|1, 左の列の幅 px)＝dragColumnSplit（2 列の幅と 40 桁は今の画面で測る）・cpRowSplitTo(上の窓の外形の丈 px)＝dragRowSplit・
      // cpResetLayout()＝⚙「配置を初期化」（鍵を消す）。どれも配置が変わったら true
      cpLayout: () => {
        const k = room.stats().scale;
        const grid = gridTemplate(layout, { k, mainW: mainEl.clientWidth, termMin: termMinNow() });
        let stored = null;
        if (!frozen && !DEMO) { try { stored = localStorage.getItem(LAYOUT_KEY); } catch { stored = null; } }
        return { v: layout.v, cols: layout.cols.map((c) => [...c]), widths: { ...layout.widths }, rows: { ...layout.rows },
          isDefault: isDefaultLayout(layout), applied: colCount() === 3 && !isDefaultLayout(layout) && !watching,
          k, grid, areas: grid.area, stored, dragging: layoutUi?.dragging() ?? null };
      },
      cpLayoutDefault: () => isDefaultLayout(layout),
      cpMove: (id, col, row) => layoutUi.ops.move(id, col, row),
      cpSwap: (a, b) => layoutUi.ops.swap(a, b),
      cpNudge: (id, dir) => layoutUi.ops.nudge(id, dir),
      cpSplitTo: (between, px) => layoutUi.ops.splitTo(between, px),
      cpRowSplitTo: (px) => layoutUi.ops.rowSplitTo(px),
      cpResetLayout: () => layoutUi.ops.reset(),
      // R111-B2: ペインの木（保存の形）・各葉（sel・出している端末・桁・行・字の大きさ・見えているか）・選んでいる葉・
      // 購読の接続の数（多重化＝0 か 1）と購読している端末の id
      panes: () => {
        const lay = panesUi?.lastLayout();
        return {
          tree: panes.serialize(tree), focused: tree.focus, count: panes.leafCount(tree), shown: paneShown,
          leaves: panes.leaves(tree).map((l) => {
            const live = liveFor(l.sel);
            const d = live ? termUi?.dims(live.id) : null;
            return { id: l.id, sel: l.sel, term: live?.id ?? null, cols: d?.cols ?? null, rows: d?.rows ?? null,
              fontSize: d?.fontSize ?? null, visible: Boolean(lay?.rects.has(l.id)) };
          }),
          streams: termUi?.streams() ?? 0, subs: termUi?.subs() ?? [],
          cell: termUi?.cell() ?? null,       // 1 文字の幅・丈（ペインの下限＝40 桁・8 行の計算の元）
        };
      },
      // メニュー・キー・見出しのボタンと同じ関数（戻り値＝木が変わったか）
      cpSplit: (leafId, dir) => splitPane(leafId, dir),
      cpClosePane: (leafId) => closePane(leafId),
      cpFocusPane: (leafId) => focusPane(leafId),
      cpSetRatio: (splitId, ratio) => setPaneRatio(splitId, ratio),
      cpAssign: (leafId, sel) => assignPane(leafId, sel),
      cpResetPanes: () => resetPanes(),
      // 固定時刻のまま 1 コマずつ進める（tools/demo_clip.py が毎回同じ動画を撮るための唯一の入口）。
      // 渡した時刻をそのまま使う（刻みの格子に揃えない＝格子は呼び手が決める）。部屋は同じ時刻の描き直しを前の時刻の
      // 帳簿から計算し直す（冪等）＝step(t)→inject は「t に届いた world」として歩行と ✓ を t から始める（もう一度 step(t) しても同じ絵）
      step: (seconds) => { stepping = true; setTime(seconds); draw(now()); } },
  });
  return () => {
    activityDisposed = true;
    hold.dispose();
    for (const page of activityPages.values()) page.view.dispose();
    activityPages.clear();
    session.dispose(); stopLoop(); uninstall();
    reducedMq?.removeEventListener?.("change", onReduced);
    window.removeEventListener("keydown", onHudKey, true);
    roomRo?.disconnect(); if (fitRaf) cancelAnimationFrame(fitRaf);
    sheetRo?.disconnect(); if (sheetRaf) cancelAnimationFrame(sheetRaf);
    layoutUi?.dispose();             // R111-A2: 帯・境目・メニューの listener と題名の写し（ドラッグ中なら）を外す
    rosterRo?.disconnect(); if (moreRaf) cancelAnimationFrame(moreRaf);
    offbarRo?.disconnect(); root.style.removeProperty("--cp-offbar-h");
    headRo?.disconnect(); root.style.removeProperty("--cp-head-h");
    document.removeEventListener("pointerdown", unlockAmbient);
    document.removeEventListener("keydown", unlockAmbient);
    room.dispose(); panesUi?.dispose(); termUi?.dispose(); hire.dispose(); firstrun.dispose(); sounds.dispose(); hoverTip.remove();
    tickerEl.remove(); namesEl.remove();
    digest.dispose(); tray.dispose(); sheet.dispose(); delivery.dispose(); gauges.dispose();
    document.title = "AI Office";
  };
}
