// R111-A2: コックピット（3 カラム）の窓の配置の操作（DOM 担当）。配置の正本は ui/core/cplayout.js（純関数）・保存と升目の書き込みは
// index.js（setLayout → paintLayout）。ここがやるのは「人の操作を cplayout の操作へ翻訳して setLayout に渡す」ことだけ:
//   - 窓の入れ替え: 題名の帯（.cpsec[data-win]）をつかんで 8px 動かしたら開始 → 題名の写し（.cpghost）が指に付いて回る →
//     落とした先の窓と入れ替え（相手の列が 1 窓・自分の列が 2 窓で、相手の上 25%／下 25% なら「挿入」＝移動）。Esc・pointercancel で取り消し。
//     ドラッグの後の click は capture で止める（ui/iso/index.js のドラッグパンの型）。ドラッグ中も部屋の描画は止めない。
//   - 境目のドラッグ: 列（.cpsplit[data-split=col]）は動かすたびに dragColumnSplit を通し、結果が変わった回だけ setLayout（保存は離したときに 1 回）。
//     段（[data-split=row]）は dragRowSplit。ダブルクリックでその境目だけ既定へ（resetSplit）。
//   - キー／メニュー（a11y）: 3 カラムの間だけ帯に tabindex=0＋aria-label（paintBands）。帯で Alt+矢印＝nudgeWindow・
//     Enter／Space／ContextMenu／右クリック＝#cp-move-menu（左へ／右へ／上へ／下へ／配置を初期化・無変化の向きは disabled）。
// 操作の口（ops）はメニュー・ドラッグ・キー・probe（index.js の cpMove ほか）が同じ関数を呼ぶ（口だけ別経路にしない）。
// import は ui/core・ui/platform・ui/hud と自分（./）だけ（tools/js_layer_lint.py）。
import { ROOM_SCALE_MAX, WINDOWS, columnKind, dragColumnSplit, dragRowSplit, findWindow, moveWindow, nudgeWindow, resetSplit,
  stackedColumn, swapWindows } from "/ui/core/cplayout.js";
import { el } from "/ui/hud/shell.js";

/** 窓の id → 窓の要素（.cpwin） */
const WIN_SEL = Object.freeze({ office: "#win-office", reception: "#win-reception", terminal: "#cp-center", roster: "#cp-right" });
/** 題名の帯をこれだけ動かしたらドラッグを始める（クリック・帯の中のボタンを奪わない） */
const DRAG_START_PX = 8;
/** 1 窓の列へ落とすとき、上／下のこの割合の帯は「挿入」（それ以外は入れ替え） */
const INSERT_EDGE = 0.25;
/** 題名の写しを指からずらす量（指の下の落とし先を隠さない） */
const GHOST_OFFSET = 12;
const ARROW_DIR = Object.freeze({ ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" });
/** 帯の中でドラッグ・メニューを始めない部品（押す・打つ物は本人の操作） */
const CONTROLS = "button, input, textarea, select, a, label, [role=button]";

/**
 * @param {object} ctx
 * @param {HTMLElement} ctx.root 様式のルート（.hud.ui-cockpit＝題名の写しをここに置く・ドラッグ中は cp-dragging）
 * @param {HTMLElement} ctx.shell
 * @param {HTMLElement} ctx.mainEl .main（メニューの包含ブロック）
 * @param {() => object} ctx.getLayout 今の配置
 * @param {(next: object, opts?: {save?: boolean}) => boolean} ctx.setLayout 配置を替える（同じ中身なら false）
 * @param {() => void} ctx.persist 今の配置を保存する（境目のドラッグを離したときの 1 回）
 * @param {() => void} ctx.reset 配置を初期化する（index.js の resetAll＝鍵も消す）
 * @param {{gap: () => number, colH: () => number, chromeH: () => object, termFloor: () => number, roomKMax: () => number|null}} ctx.measure
 *   実測（溝・列の丈・窓ごとの見出しの丈・端末 40 桁の幅・丈が許す部屋の倍率の上限）
 * @param {(key: string, ...args: any[]) => string} ctx.T
 * @param {() => boolean} ctx.enabled 人の操作の入口を開けてよいか（3 カラム・眺めるモードでない・デモでない）
 * @param {() => boolean} ctx.measurable 境目を測れるか（3 カラム）
 */
export function initLayoutUi({ root, shell, mainEl, getLayout, setLayout, persist, reset, measure, T, enabled, measurable }) {
  const winEl = (id) => shell.querySelector(WIN_SEL[id]);
  const bands = [...shell.querySelectorAll(".cpsec[data-win]")];
  const bandOf = (id) => bands.find((b) => b.dataset.win === id) || null;
  const idOfWin = new Map(WINDOWS.map((id) => [winEl(id), id]));
  const menu = shell.querySelector("#cp-move-menu");
  const colHandles = [...mainEl.querySelectorAll(':scope > .cpsplit[data-split="col"]')];
  const rowHandle = mainEl.querySelector(':scope > .cpsplit[data-split="row"]');

  /** 列 c の窓の矩形（その列の先頭の窓で測る＝窓は列の幅いっぱい） */
  const colRect = (c) => {
    const id = getLayout().cols[c]?.[0];
    const e = id && winEl(id);
    return e ? e.getBoundingClientRect() : null;
  };
  /** 境目 between の両側の 2 列の幅（左の列の左端から右の列の右端まで＝溝込み） */
  const pairWidth = (between) => {
    const a = colRect(between);
    const b = colRect(between + 1);
    return a && b ? b.right - a.left : 0;
  };
  /** 2 窓の列の上の窓の外形の丈 */
  const topHeight = () => {
    const l = getLayout();
    const e = winEl(l.cols[stackedColumn(l)][0]);
    return e ? e.getBoundingClientRect().height : 0;
  };

  // ── 操作の口（メニュー・ドラッグ・キー・probe が同じ関数を呼ぶ）────────────────────────
  const ops = {
    /** 窓 id を列 col の段 row へ（moveWindow・元の列が空になる操作は無変化） */
    move: (id, col, row) => setLayout(moveWindow(getLayout(), id, col, row)),
    /** 窓 a と b を入れ替える */
    swap: (a, b) => setLayout(swapWindows(getLayout(), a, b)),
    /** 左へ／右へ／上へ／下へ（nudgeWindow・端は無変化） */
    nudge: (id, dir) => setLayout(nudgeWindow(getLayout(), id, dir)),
    /**
     * 列の境目 between（0|1）の左の列の幅を leftW（px）にしたい＝dragColumnSplit。pairW・termFloor は省略時に今の画面で測る
     *（ドラッグは始めた時の値を渡す）。3 カラムでなければ測れない＝無変化
     */
    splitTo(between, leftW, { save = true, pairW, termFloor } = {}) {
      if (!measurable() || !Number.isFinite(leftW)) return false;
      const l = getLayout();
      const pw = pairW ?? pairWidth(between);
      if (!(pw > 0)) return false;
      const next = dragColumnSplit(l, between, leftW,
        { pairW: pw, gap: measure.gap(), termFloor: termFloor ?? measure.termFloor() });
      // dragColumnSplit は丈を知らない。部屋を丈が許す倍率（roomKMax）より大きくしたい提案（×4 が丈に入らない 1440×900 で右へ引く）を
      // そのまま通すと、部屋は ×3 のまま端末の最低（termMin）だけが下がり、見た目は動かずに端末の字の縮め方が変わった。
      // その提案は「丈が許すいっぱいまで」＝部屋の上限（roomCap）だけを外す（ほかの幅は今のまま）
      const kMax = measure.roomKMax?.();
      const room = columnKind(l, between) === "room" || columnKind(l, between + 1) === "room";
      if (room && Number.isFinite(kMax) && (next.widths.roomCap ?? ROOM_SCALE_MAX) > kMax) {
        return setLayout({ ...l, widths: { ...l.widths, roomCap: undefined } }, { save });
      }
      // 今の幅と丈が選ぶ倍率（kFit）以上の上限は意味が無い＝外す。残すと、見た目の変わらない小さなドラッグ（1440×900 で 20px）で
      // roomCap 3 が保存され、後で 1920 に広げても部屋が ×4 に伸びなかった（A4 の門が見つけた穴）
      const kFit = measure.roomKFit?.();
      if (room && Number.isFinite(kFit) && next.widths.roomCap != null && next.widths.roomCap >= kFit) {
        return setLayout({ ...next, widths: { ...next.widths, roomCap: undefined } }, { save });
      }
      return setLayout(next, { save });
    },
    /** 2 窓の列の上の窓の外形の丈を topH（px）にしたい＝dragRowSplit（部屋の列は 160k にスナップ） */
    rowSplitTo(topH, { save = true } = {}) {
      if (!measurable()) return false;
      return setLayout(dragRowSplit(getLayout(), topH,
        { colH: measure.colH(), gap: measure.gap(), chromeH: measure.chromeH() }), { save });
    },
    /** その境目だけ既定へ（0|1|"row"） */
    resetSplit: (which) => setLayout(resetSplit(getLayout(), which)),
    /** 配置を初期化（鍵を消す） */
    reset: () => reset(),
  };

  // ── 窓の入れ替え（題名の帯のドラッグ）──────────────────────────────────────
  let drag = null;                  // { id, band, pointerId, x0, y0, started, ghost, plan }
  let suppressClick = false;        // ドラッグの直後の click を 1 回だけ止める
  let suppressTimer = 0;
  /** 落とし先の印を付け替える */
  let dropShown = null;
  const paintDrop = (plan) => {
    const next = plan ? winEl(plan.target) : null;
    if (dropShown && dropShown !== next) dropShown.classList.remove("cp-drop", "cp-drop-above", "cp-drop-below");
    if (next) {
      next.classList.add("cp-drop");
      next.classList.toggle("cp-drop-above", plan.insert < 0);
      next.classList.toggle("cp-drop-below", plan.insert > 0);
    }
    dropShown = next;
  };
  /** 指の下の窓から、落としたときの操作を決める（自分の上・窓の外＝null） */
  const planAt = (x, y) => {
    const hit = document.elementFromPoint(x, y)?.closest?.(".cpwin");
    const target = hit ? idOfWin.get(hit) : null;
    if (!target || !drag || target === drag.id) return null;
    const l = getLayout();
    const pt = findWindow(l, target);
    const ps = findWindow(l, drag.id);
    let insert = 0;
    if (l.cols[pt.col].length === 1 && l.cols[ps.col].length === 2) {
      const r = hit.getBoundingClientRect();
      if (y < r.top + r.height * INSERT_EDGE) insert = -1;
      else if (y > r.bottom - r.height * INSERT_EDGE) insert = 1;
    }
    return { target, col: pt.col, insert };
  };
  const startDrag = () => {
    drag.started = true;
    try { drag.band.setPointerCapture(drag.pointerId); } catch { /* 指がもう離れていた */ }
    closeMenu(false);
    root.classList.add("cp-dragging");
    winEl(drag.id)?.classList.add("cp-drag-src");
    const ghost = el("div", "cpghost");
    ghost.setAttribute("aria-hidden", "true");
    for (const sel of [".secno", ".sectitle"]) {
      const c = drag.band.querySelector(sel)?.cloneNode(true);
      if (!c) continue;
      c.removeAttribute("id");
      ghost.append(c);
    }
    root.append(ghost);
    drag.ghost = ghost;
    try { getSelection()?.removeAllRanges(); } catch { /* 選択の無い文書 */ }
  };
  const endDrag = () => {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (!d.started) return;
    paintDrop(null);
    d.ghost?.remove();
    root.classList.remove("cp-dragging");
    winEl(d.id)?.classList.remove("cp-drag-src");
    try { if (d.band.hasPointerCapture?.(d.pointerId)) d.band.releasePointerCapture(d.pointerId); } catch { /* 離れた後 */ }
  };
  const onBandDown = (e) => {
    const band = e.currentTarget;
    if (drag && !drag.started) drag = null;          // 前の押下の離しを取りこぼした（窓の外で離した等）＝捨てる
    if (e.button !== 0 || drag || split || !enabled()) return;
    const ctl = e.target.closest?.(CONTROLS);
    if (ctl && band.contains(ctl)) return;
    drag = { id: band.dataset.win, band, pointerId: e.pointerId, x0: e.clientX, y0: e.clientY, started: false, ghost: null, plan: null };
  };
  const onBandMove = (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    if (!drag.started) {
      if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < DRAG_START_PX) return;
      if (!enabled()) { drag = null; return; }
      startDrag();
    }
    drag.ghost.style.left = `${Math.round(e.clientX + GHOST_OFFSET)}px`;
    drag.ghost.style.top = `${Math.round(e.clientY + GHOST_OFFSET)}px`;
    drag.plan = planAt(e.clientX, e.clientY);
    paintDrop(drag.plan);
  };
  const onBandUp = (e) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    if (!drag.started) { drag = null; return; }   // 動かさずに離した＝ただのクリック（帯の中のボタン・フォーカスはそのまま）
    const { id } = drag;
    const plan = planAt(e.clientX, e.clientY);
    endDrag();
    suppressClick = true;
    clearTimeout(suppressTimer);
    suppressTimer = setTimeout(() => { suppressClick = false; }, 0);
    if (!plan) return;
    if (plan.insert) ops.move(id, plan.col, plan.insert < 0 ? 0 : 1);
    else ops.swap(id, plan.target);
  };
  const onBandCancel = (e) => {
    if (drag && e.pointerId === drag.pointerId) endDrag();
  };
  const onClickCapture = (e) => {
    if (!suppressClick) return;
    suppressClick = false;
    e.stopImmediatePropagation();
    e.preventDefault();
  };
  /** 帯・境目をつかんでいる間は文字の選択を始めさせない（選択は押した瞬間に始まり、動かすと窓の中身まで青く塗られる） */
  const onSelectStart = (e) => { if (drag || split) e.preventDefault(); };

  // ── 境目のドラッグ ──────────────────────────────────────────────────────────
  let split = null;                 // { kind, h, pointerId, between, x0, y0, leftW, pairW, termFloor, topH, start }
  const onSplitDown = (e) => {
    const h = e.currentTarget;
    if (e.button !== 0 || drag || split || !enabled() || !measurable()) return;
    const l = getLayout();
    if (h.dataset.split === "col") {
      const between = Number(h.dataset.between);
      const a = colRect(between);
      const pairW = pairWidth(between);
      if (!a || !(pairW > 0)) return;
      split = { kind: "col", h, pointerId: e.pointerId, between, x0: e.clientX, leftW: a.width, pairW,
        termFloor: measure.termFloor(), start: l };
    } else {
      split = { kind: "row", h, pointerId: e.pointerId, y0: e.clientY, topH: topHeight(), start: l };
    }
    e.preventDefault();
    try { h.setPointerCapture(e.pointerId); } catch { /* 指がもう離れていた */ }
    h.classList.add("on");
    closeMenu(false);
  };
  const onSplitMove = (e) => {
    if (!split || e.pointerId !== split.pointerId) return;
    if (split.kind === "col") {
      ops.splitTo(split.between, split.leftW + (e.clientX - split.x0),
        { save: false, pairW: split.pairW, termFloor: split.termFloor });
    } else {
      ops.rowSplitTo(split.topH + (e.clientY - split.y0), { save: false });
    }
  };
  const endSplit = (commit) => {
    if (!split) return;
    const s = split;
    split = null;
    s.h.classList.remove("on");
    try { if (s.h.hasPointerCapture?.(s.pointerId)) s.h.releasePointerCapture(s.pointerId); } catch { /* 離れた後 */ }
    if (!commit) { setLayout(s.start, { save: false }); return; }
    if (getLayout() !== s.start) persist();
  };
  const onSplitUp = (e) => { if (split && e.pointerId === split.pointerId) endSplit(true); };
  const onSplitCancel = (e) => { if (split && e.pointerId === split.pointerId) endSplit(false); };
  const onSplitDbl = (e) => {
    if (!enabled()) return;
    const h = e.currentTarget;
    ops.resetSplit(h.dataset.split === "row" ? "row" : Number(h.dataset.between));
  };

  // ── キー／メニュー ───────────────────────────────────────────────────────
  let menuFor = null;
  /** 帯 id の下にメニューを開く（x＝右クリックの位置・無ければ帯の左端） */
  const openMenu = (id, x = null) => {
    const band = bandOf(id);
    if (!band || !enabled()) return;
    menuFor = id;
    const l = getLayout();
    for (const b of menu.querySelectorAll("[data-dir]")) {
      const dir = b.dataset.dir;
      b.disabled = dir !== "reset" && nudgeWindow(l, id, dir) === l;
    }
    menu.hidden = false;
    const m = mainEl.getBoundingClientRect();
    const r = band.getBoundingClientRect();
    const left = Math.max(0, Math.min((x ?? r.left) - m.left, mainEl.clientWidth - menu.offsetWidth));
    menu.style.right = "auto";
    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(r.bottom - m.top)}px`;
    menu.querySelector("button:not([disabled])")?.focus();
  };
  /** メニューを閉じる（refocus＝キーで閉じた・項目を選んだ＝帯へフォーカスを戻す） */
  function closeMenu(refocus) {
    if (menu.hidden) return;
    menu.hidden = true;
    const id = menuFor;
    menuFor = null;
    if (refocus && id) bandOf(id)?.focus({ preventScroll: true });
  }
  const onBandKey = (e) => {
    const band = e.currentTarget;
    if (e.target !== band || !enabled()) return;    // 帯の中のボタン・絞り込みの入力欄のキーはそれらのもの
    const dir = ARROW_DIR[e.key];
    if (dir && e.altKey && !e.metaKey && !e.ctrlKey && !e.shiftKey) {
      e.preventDefault();
      e.stopPropagation();
      ops.nudge(band.dataset.win, dir);
      return;
    }
    const plain = !e.altKey && !e.metaKey && !e.ctrlKey;
    if ((plain && (e.key === "Enter" || e.key === " " || e.key === "ContextMenu")) || (e.shiftKey && e.key === "F10")) {
      e.preventDefault();
      e.stopPropagation();
      openMenu(band.dataset.win);
    }
  };
  const onBandContext = (e) => {
    if (!enabled() || e.target.closest?.("input, textarea")) return;   // 入力欄は本来の右クリックのまま
    e.preventDefault();
    openMenu(e.currentTarget.dataset.win, e.button === 2 ? e.clientX : null);   // キーで開いた（ContextMenu・Shift+F10）ときは帯の左端
  };
  const onMenuClick = (e) => {
    const b = e.target.closest?.("[data-dir]");
    if (!b || b.disabled) return;
    const id = menuFor;
    closeMenu(true);
    if (b.dataset.dir === "reset") ops.reset();
    else if (id) ops.nudge(id, b.dataset.dir);
  };
  const onMenuKey = (e) => {
    const items = [...menu.querySelectorAll("button:not([disabled])")];
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closeMenu(true);
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      const i = items.indexOf(document.activeElement);
      const n = items.length;
      if (!n) return;
      items[e.key === "Home" ? 0 : e.key === "End" ? n - 1 : (i + (e.key === "ArrowDown" ? 1 : -1) + n) % n]?.focus();
    } else if (e.key === "Tab") {
      closeMenu(false);
    }
  };
  /** メニューの外を押したら閉じる（帯の右クリックは contextmenu が開き直す） */
  const onDocDown = (e) => { if (!menu.hidden && !menu.contains(e.target)) closeMenu(false); };
  /** ドラッグ中の Esc は取り消し（下の面へ流さない＝指示欄を閉じない） */
  const onKeyCapture = (e) => {
    if (e.key !== "Escape") return;
    if (drag?.started) { e.preventDefault(); e.stopImmediatePropagation(); endDrag(); }
    else if (split) { e.preventDefault(); e.stopImmediatePropagation(); endSplit(false); }
  };

  /**
   * 帯の読み上げとキーの入口（3 カラム・人の操作を受ける間だけ tabindex=0＋aria-label＝題名＋操作の案内）。
   * index.js の paintLayout と文言の貼り直しから呼ぶ。変わったときだけ書く。入口を閉じたらメニューとドラッグも畳む。
   */
  const paintBands = () => {
    const on = enabled();
    for (const band of bands) {
      if (on) {
        const title = [band.querySelector(".secno")?.textContent, band.querySelector(".sectitle")?.textContent]
          .filter(Boolean).join(" ");
        const label = `${title} — ${T("cp_move_hint")}`;
        if (band.getAttribute("tabindex") !== "0") band.setAttribute("tabindex", "0");
        if (band.getAttribute("aria-label") !== label) band.setAttribute("aria-label", label);
      } else if (band.hasAttribute("tabindex") || band.hasAttribute("aria-label")) {
        band.removeAttribute("tabindex");
        band.removeAttribute("aria-label");
      }
    }
    if (!on) {
      closeMenu(false);
      endDrag();
      endSplit(true);
    }
  };

  for (const band of bands) {
    band.addEventListener("pointerdown", onBandDown);
    band.addEventListener("keydown", onBandKey);
    band.addEventListener("contextmenu", onBandContext);
  }
  window.addEventListener("pointermove", onBandMove);
  window.addEventListener("pointerup", onBandUp);
  window.addEventListener("pointercancel", onBandCancel);
  for (const band of bands) band.addEventListener("lostpointercapture", onBandCancel);
  window.addEventListener("click", onClickCapture, true);
  document.addEventListener("selectstart", onSelectStart, true);
  for (const h of [...colHandles, rowHandle].filter(Boolean)) {
    h.addEventListener("pointerdown", onSplitDown);
    h.addEventListener("pointermove", onSplitMove);
    h.addEventListener("pointerup", onSplitUp);
    h.addEventListener("pointercancel", onSplitCancel);
    h.addEventListener("lostpointercapture", onSplitUp);
    h.addEventListener("dblclick", onSplitDbl);
  }
  menu.addEventListener("click", onMenuClick);
  menu.addEventListener("keydown", onMenuKey);
  document.addEventListener("pointerdown", onDocDown, true);
  window.addEventListener("keydown", onKeyCapture, true);

  return {
    ops,
    paintBands,
    /** 今ドラッグ中か（"window"｜"split"｜null） */
    dragging: () => (drag?.started ? "window" : split ? "split" : null),
    dispose() {
      endDrag();
      endSplit(true);
      closeMenu(false);
      clearTimeout(suppressTimer);
      for (const band of bands) {
        band.removeEventListener("pointerdown", onBandDown);
        band.removeEventListener("keydown", onBandKey);
        band.removeEventListener("contextmenu", onBandContext);
        band.removeEventListener("lostpointercapture", onBandCancel);
      }
      window.removeEventListener("pointermove", onBandMove);
      window.removeEventListener("pointerup", onBandUp);
      window.removeEventListener("pointercancel", onBandCancel);
      window.removeEventListener("click", onClickCapture, true);
      document.removeEventListener("selectstart", onSelectStart, true);
      for (const h of [...colHandles, rowHandle].filter(Boolean)) {
        h.removeEventListener("pointerdown", onSplitDown);
        h.removeEventListener("pointermove", onSplitMove);
        h.removeEventListener("pointerup", onSplitUp);
        h.removeEventListener("pointercancel", onSplitCancel);
        h.removeEventListener("lostpointercapture", onSplitUp);
        h.removeEventListener("dblclick", onSplitDbl);
      }
      menu.removeEventListener("click", onMenuClick);
      menu.removeEventListener("keydown", onMenuKey);
      document.removeEventListener("pointerdown", onDocDown, true);
      window.removeEventListener("keydown", onKeyCapture, true);
    },
  };
}
