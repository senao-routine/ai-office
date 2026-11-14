// R111-B2: 「03 端末」の窓の中のペイン（DOM 担当）。木の形・畳み・隣・保存の形は ui/core/termpanes.js（純関数）が正本＝
// ここは「その木をどう描くか」だけ: 葉ごとの .cppane（見出し・body）・境目 .cpsplit-bar とそのドラッグ。
// 何を body に入れるか（端末・会話・案内）は ui/cockpit/index.js が決め、端末の積み替えは ui/cockpit/term.js。
//
// 掟:
// - 根は #cp-xterm（id 据置＝shapes/R105/term_smoke の参照が生きる）。葉の矩形は根の content box の中（根の padding は触らない）。
// - **葉が 1 つ（見えているペインが 1 つ）のときは見出しも選択の枠も出さない**＝従来の 1 枚の端末と同じ画素（golden 不変）。
// - 葉の body から自分の作った物（案内の p）以外を消さない（端末の .cpxt と会話の #cp-work/#cp-dlg は持ち主が移す）。
// - 境目のドラッグはゴーストだけ動かし、離したときに 1 回だけ ratio を渡す（ドラッグ中に端末の大きさを毎フレーム変えない）。
// - 色・罫・見出しの体裁は style.css（ここは class と寸法だけ）。
import * as panes from "/ui/core/termpanes.js";
import { el } from "/ui/hud/shell.js";

/** 境目の当たりの太さ（px）。layout() の barPx と同じ数を使う */
export const BAR_PX = 7;
/** ペインの最低＝40 桁と 8 行（＋スクロールバーと余白・見出しの丈）。termpanes.layout がこれを割る split を畳む */
const MIN_COLS = 40;
const MIN_ROWS = 8;
const MIN_W_EXTRA = 16;
const MIN_H_EXTRA = 36;

const px = (v) => `${Math.round(v)}px`;

/**
 * @param {object} o
 * @param {HTMLElement} o.root ペインの根（#cp-xterm）
 * @param {Function} o.T 文言
 * @param {(leafId: string) => void} o.onFocus ペインを押した（pointerdown）
 * @param {(leafId: string, dir: "h"|"v") => void} o.onSplit 見出しの ⫿ ⫽
 * @param {(leafId: string) => void} o.onClose 見出しの ✕
 * @param {(splitId: string, ratio: number) => void} o.onRatio 境目を離した
 * @param {() => {w: number, h: number}} o.cell 1 文字の幅・丈（term.js の cell）
 * @param {() => void} o.onResize 窓の大きさが変わった（次のフレームで描き直してもらう）
 */
export function init({ root, T, onFocus, onSplit, onClose, onRatio, cell, onResize = () => {} }) {
  const nodes = new Map();          // leafId → { pane, head, body, hint, sig }
  const bars = new Map();           // splitId → 要素
  let lastTree = null;
  let lastLayout = null;
  let drag = null;

  /** 根の content box（根の padding box を原点にした座標）。根が隠れていても測れるよう、根と同じ箱（.cpterm の padding box）で測る */
  function contentBox() {
    const box = root.parentElement || root;
    const cs = getComputedStyle(root);
    const pl = parseFloat(cs.paddingLeft) || 0, pr = parseFloat(cs.paddingRight) || 0;
    const pt = parseFloat(cs.paddingTop) || 0, pb = parseFloat(cs.paddingBottom) || 0;
    return { x: pl, y: pt, w: Math.max(0, box.clientWidth - pl - pr), h: Math.max(0, box.clientHeight - pt - pb) };
  }
  /** ペインの最低（40 桁・8 行）。collapse＝分割を見せない段（2 カラム・1 列）＝どの split も入らない大きさを渡して focus の葉だけにする */
  function minFor(rect, collapse) {
    if (collapse) return { w: rect.w + 1, h: rect.h + 1 };
    const c = cell();
    return { w: MIN_COLS * c.w + MIN_W_EXTRA, h: MIN_ROWS * c.h + MIN_H_EXTRA };
  }

  /** 木の矩形を今の窓で測る（DOM にも控えにも書かない＝分ける前に「入るか」を見る） */
  function measure(tree, { collapse = false } = {}) {
    const rect = contentBox();
    return panes.layout(tree, rect, minFor(rect, collapse), BAR_PX);
  }
  /** 木の矩形を決める（DOM には書かない・控える）。見えている葉の数で index.js が出し方を決めてから render を呼ぶ */
  function layout(tree, opts) {
    lastLayout = measure(tree, opts);
    return lastLayout;
  }

  function findSplit(node, id) {
    if (!node || !node.dir) return null;
    if (node.id === id) return node;
    return findSplit(node.a, id) || findSplit(node.b, id);
  }

  function button(cls, glyph, key, fn) {
    const b = el("button", cls, glyph);
    b.type = "button";
    b.title = T(key);
    b.setAttribute("aria-label", T(key));
    b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
    return b;
  }

  function makeNode(leafId) {
    const pane = el("div", "cppane");
    pane.dataset.pane = leafId;
    const body = el("div", "cppane-body");
    pane.append(body);
    // 押したペインが「選んでいるペイン」（capture＝中の xterm や見出しのボタンより先に）
    pane.addEventListener("pointerdown", () => onFocus(leafId), true);
    const n = { pane, head: null, body, hint: null, sig: "", hintSig: "" };
    nodes.set(leafId, n);
    root.append(pane);
    return n;
  }

  /** 見出し（葉が 2 つ以上見えているときだけ DOM に置く） */
  function paintHead(n, leafId, meta, count, max) {
    if (!n.head) {
      const head = el("div", "cppane-head");
      const dot = el("i", "dot");
      const nm = el("span", "nm");
      const acts = el("span", "cpacts");
      acts.append(
        button("cppane-split-h", "⫿", "cp_pane_split_h", () => onSplit(leafId, "h")),
        button("cppane-split-v", "⫽", "cp_pane_split_v", () => onSplit(leafId, "v")),
        button("cppane-close", "✕", "cp_pane_close", () => onClose(leafId)));
      head.append(dot, nm, acts);
      n.head = head;
      n.pane.prepend(head);
      n.sig = "";
    }
    const sig = JSON.stringify([meta.name, meta.title, meta.tint, count, max, T("cp_pane_close")]);
    if (n.sig === sig) return;
    n.sig = sig;
    const [dot, nm, acts] = n.head.children;
    dot.style.setProperty("--session", meta.tint || "transparent");
    nm.textContent = meta.name || "";
    nm.title = meta.title || meta.name || "";
    for (const b of acts.querySelectorAll(".cppane-split-h, .cppane-split-v")) b.disabled = count >= max;
    acts.querySelector(".cppane-close").hidden = count <= 1;     // 最後の 1 つは閉じない
    for (const [cls, key] of [[".cppane-split-h", "cp_pane_split_h"], [".cppane-split-v", "cp_pane_split_v"],
      [".cppane-close", "cp_pane_close"]]) {
      const b = acts.querySelector(cls);
      b.title = T(key);
      b.setAttribute("aria-label", T(key));
    }
  }

  /** body の案内（空のペイン・会話を出していないペイン）。ほかの子（端末・会話）には触らない */
  function paintHint(n, meta) {
    const want = meta.kind === "empty" || meta.kind === "label";
    if (!want) {
      n.hint?.remove();
      n.hint = null;
      n.hintSig = "";
      return;
    }
    const sig = JSON.stringify([meta.who || "", meta.note || ""]);
    if (n.hint && n.hintSig === sig && n.hint.parentNode === n.body) return;
    if (!n.hint) n.hint = el("p", "cppane-hint");
    n.hintSig = sig;
    const kids = [];
    if (meta.who) kids.push(el("b", "who", meta.who));
    if (meta.note) kids.push(el("span", "note", meta.note));
    n.hint.replaceChildren(...kids);
    if (n.hint.parentNode !== n.body) n.body.append(n.hint);
  }

  /**
   * 描く。lay＝layout() の結果・info(leaf)＝{ kind: "term"|"dialog"|"empty"|"label", name, title, tint, who, note }。
   * 戻り値＝見えている葉の body（Map: leafId → 要素）。
   */
  function render(tree, lay, info) {
    lastTree = tree;
    lastLayout = lay;
    const all = panes.leaves(tree);
    const count = all.length;
    const shownN = lay.rects.size;
    const multi = shownN >= 2;
    if (root.dataset.panes !== String(shownN)) root.dataset.panes = String(shownN);
    const bodies = new Map();
    const seen = new Set();
    for (const leaf of all) {
      seen.add(leaf.id);
      const n = nodes.get(leaf.id) || makeNode(leaf.id);
      const r = lay.rects.get(leaf.id);
      if (!r) {
        if (!n.pane.hidden) n.pane.hidden = true;
        continue;
      }
      if (n.pane.hidden) n.pane.hidden = false;
      for (const [k, v] of [["left", r.x], ["top", r.y], ["width", r.w], ["height", r.h]]) {
        if (n.pane.style[k] !== px(v)) n.pane.style[k] = px(v);
      }
      const meta = info(leaf) || { kind: "empty" };
      const sel = multi && leaf.id === tree.focus;
      if (n.pane.classList.contains("sel") !== sel) n.pane.classList.toggle("sel", sel);
      const cls = `cppane-body ${meta.kind}`;
      if (n.body.className !== cls) n.body.className = cls;
      if (multi) paintHead(n, leaf.id, meta, count, panes.MAX_PANES);
      else if (n.head) { n.head.remove(); n.head = null; n.sig = ""; }
      paintHint(n, meta);
      bodies.set(leaf.id, n.body);
    }
    for (const [id, n] of nodes) {
      if (seen.has(id)) continue;
      n.pane.remove();
      nodes.delete(id);
    }
    // 境目（見えている split だけ）
    const want = new Set();
    for (const b of lay.bars) {
      want.add(b.splitId);
      let bar = bars.get(b.splitId);
      if (!bar) {
        bar = el("div", "cpsplit-bar");
        bar.dataset.split = b.splitId;
        bar.setAttribute("role", "separator");
        bar.addEventListener("pointerdown", onBarDown);
        bars.set(b.splitId, bar);
        root.append(bar);
      }
      if (bar.dataset.dir !== b.dir) {
        bar.dataset.dir = b.dir;
        bar.setAttribute("aria-orientation", b.dir === "h" ? "vertical" : "horizontal");
      }
      for (const [k, v] of [["left", b.x], ["top", b.y], ["width", b.w], ["height", b.h]]) {
        if (bar.style[k] !== px(v)) bar.style[k] = px(v);
      }
    }
    for (const [id, bar] of bars) {
      if (want.has(id)) continue;
      if (drag?.bar === bar) finishDrag(false);
      bar.remove();
      bars.delete(id);
    }
    return bodies;
  }

  /** ペインの DOM を全部外す（端末の根を出さない形＝従来の 1 枚の会話ビュー・デモ）。中身は持ち主が先に退かしておく */
  function reset() {
    finishDrag(false);
    for (const n of nodes.values()) n.pane.remove();
    nodes.clear();
    for (const bar of bars.values()) bar.remove();
    bars.clear();
    delete root.dataset.panes;
    lastTree = null;
  }

  // ── 境目のドラッグ（ゴーストだけ動かし、離したときに 1 回 ratio を渡す・Esc で取り消し）──
  function onBarDown(e) {
    if (e.button !== 0 || drag || !lastTree || !lastLayout) return;
    const bar = e.currentTarget;
    const b = lastLayout.bars.find((x) => x.splitId === bar.dataset.split);
    const node = b && findSplit(lastTree.root, b.splitId);
    if (!node) return;
    e.preventDefault();
    e.stopPropagation();
    const horiz = b.dir === "h";
    const avail = b.size - BAR_PX;
    if (!(avail > 0)) return;
    const at = horiz ? b.x : b.y;
    const pos = Math.min(avail - b.minB, Math.max(b.minA, Math.floor(avail * node.ratio + 1e-9)));
    const ghost = el("div", "cpsplit-ghost");
    ghost.dataset.dir = b.dir;
    for (const [k, v] of [["left", b.x], ["top", b.y], ["width", b.w], ["height", b.h]]) ghost.style[k] = px(v);
    root.append(ghost);
    try { bar.setPointerCapture(e.pointerId); } catch { /* 合成のイベント */ }
    bar.classList.add("on");
    drag = { bar, b, ghost, horiz, avail, origin: at - pos, at, start: horiz ? e.clientX : e.clientY,
      ratio: node.ratio, pointerId: e.pointerId };
    bar.addEventListener("pointermove", onBarMove);
    bar.addEventListener("pointerup", onBarUp);
    bar.addEventListener("pointercancel", onBarCancel);
    bar.addEventListener("lostpointercapture", onBarCancel);
    window.addEventListener("keydown", onDragKey, true);
  }
  function onBarMove(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    const d = (drag.horiz ? e.clientX : e.clientY) - drag.start;
    const r = panes.clampRatio(drag.b.size, (drag.at + d - drag.origin) / drag.avail, drag.b.minA, drag.b.minB, BAR_PX);
    drag.ratio = r;
    drag.ghost.style[drag.horiz ? "left" : "top"] = px(drag.origin + Math.floor(drag.avail * r + 1e-9));
  }
  function onBarUp(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    finishDrag(true);
  }
  function onBarCancel() { finishDrag(false); }
  function onDragKey(e) {
    if (!drag || e.key !== "Escape") return;
    e.preventDefault();
    e.stopPropagation();
    finishDrag(false);
  }
  function finishDrag(commit) {
    const d = drag;
    if (!d) return;
    drag = null;
    window.removeEventListener("keydown", onDragKey, true);
    d.bar.removeEventListener("pointermove", onBarMove);
    d.bar.removeEventListener("pointerup", onBarUp);
    d.bar.removeEventListener("pointercancel", onBarCancel);
    d.bar.removeEventListener("lostpointercapture", onBarCancel);
    try { if (d.bar.hasPointerCapture?.(d.pointerId)) d.bar.releasePointerCapture(d.pointerId); } catch { /* 外れている */ }
    d.bar.classList.remove("on");
    d.ghost.remove();
    if (commit) onRatio(d.b.splitId, d.ratio);
  }

  // 窓の大きさが変わったら（列の幅・指示欄・画面の段）、次のフレームで描き直してもらう（観測の中で DOM を書かない＝RO の一周を越えない）
  let raf = 0;
  const ro = typeof ResizeObserver === "function" && root.parentElement
    ? new ResizeObserver(() => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; onResize(); }); }) : null;
  ro?.observe(root.parentElement);

  return {
    measure,
    layout,
    render,
    reset,
    /** その葉の今の body（まだ描いていなければ null） */
    bodyOf: (leafId) => nodes.get(leafId)?.body || null,
    /** 直近の layout（キーで隣のペインへ動くときの幾何） */
    lastLayout: () => lastLayout,
    dragging: () => Boolean(drag),
    dispose() {
      finishDrag(false);
      ro?.disconnect();
      if (raf) cancelAnimationFrame(raf);
      reset();
    },
  };
}
