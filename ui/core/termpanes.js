// R111-B1: コックピットの「03 端末」の窓の中を最大 4 ペインに分ける tmux 型の分割木（純関数）。
//
// 固定の 2×2 格子にしないのは、1＋2 の形・境目のドラッグ・「狭ければ畳む」が木なら自然に書けるから（計画「決めたこと 5」）。
// 葉 1 つ＝ペイン 1 つ。葉は端末かセッションを 1 つだけ持つ（sel＝ui/cockpit/index.js の selected と同じ値＝
// "<session id>" か "term:<term id>"・空は null）。**同じ sel は 1 葉だけ**（別の葉で選ぶと移動＝同じ端末を 2 ペインに
// 出して resize を取り合わせない）。
//
// ここが決めるのは「木の形」「どの葉が何を持つか」「どの葉がどの矩形か（どれを畳むか）」「キーで隣へ行くと誰か」
// 「保存の形」だけ。**どう見えるか**（.cppane の DOM・見出し・枠）は ui/cockpit/panes.js、端末の積み替えと購読は
// ui/cockpit/term.js。DOM・通信・時刻・乱数には触らない（tools/js_layer_lint.py）。
//
// 掟:
// - 入力を変えない（返す節は Object.freeze 済み・触っていない部分木は同じ参照のまま共有する）。
// - 無変化の操作は**同じ木（同一参照）**を返す＝呼び手は `next === tree` で保存・再描画を省ける。
// - id は seq で採番する（乱数を使わない）。split のたびに n = seq + 1 で葉 `p<n>` と split `s<n>` を作る。
//
// 型:
//   leaf  = { id: "p1", sel: string|null }
//   split = { id: "s2", dir: "h"|"v", ratio: 0.1..0.9, a, b }   // h = 左右（a が左）・v = 上下（a が上）
//   tree  = { root: leaf|split, focus: leafId, seq: number }

/** 葉（ペイン）の上限＝2×2 相当。サーバーの MAX_TERMS（server/office_pty.py）と同じ数 */
export const MAX_PANES = 4;

const RATIO_MIN = 0.1;
const RATIO_MAX = 0.9;
/** sel の長さの上限（セッション id は UUID・端末は "term:" + 16 hex。保存に紛れた巨大な文字列を弾く） */
const SEL_MAX = 256;
const LEAF_ID = /^p[1-9][0-9]{0,8}$/;
const SPLIT_ID = /^s[1-9][0-9]{0,8}$/;

const isObj = (v) => v !== null && typeof v === "object";
const isSplitNode = (n) => isObj(n) && (n.dir === "h" || n.dir === "v");
const num = (v, d = 0) => (typeof v === "number" && Number.isFinite(v) ? v : d);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

const mkLeaf = (id, sel) => Object.freeze({ id, sel });
const mkSplit = (id, dir, ratio, a, b) => Object.freeze({ id, dir, ratio, a, b });
const mkTree = (root, focus, seq) => Object.freeze({ root, focus, seq });

/** sel の正規化: 空でない文字列 → そのまま・null と "" → null・それ以外（数・長すぎる文字列・undefined）→ undefined（不正） */
function normSel(v) {
  if (v === null || v === "") return null;
  if (typeof v === "string" && v.length <= SEL_MAX) return v;
  return undefined;
}

function rootOf(tree) {
  return isObj(tree) && isObj(tree.root) ? tree.root : null;
}

function collectLeaves(node, out) {
  if (!isObj(node)) return out;
  if (isSplitNode(node)) {
    collectLeaves(node.a, out);
    collectLeaves(node.b, out);
  } else {
    out.push(node);
  }
  return out;
}

function findNode(node, id) {
  if (!isObj(node)) return null;
  if (node.id === id) return node;
  if (!isSplitNode(node)) return null;
  return findNode(node.a, id) ?? findNode(node.b, id);
}

function contains(node, id) {
  return findNode(node, id) !== null;
}

/** 木を写す。fn が別の節を返したらそこで置き換え（その下は辿らない）・同じ節なら子へ降りる。何も変わらなければ同じ参照 */
function mapNode(node, fn) {
  const r = fn(node);
  if (r !== node) return r;
  if (!isSplitNode(node)) return node;
  const a = mapNode(node.a, fn);
  const b = mapNode(node.b, fn);
  return a === node.a && b === node.b ? node : mkSplit(node.id, node.dir, node.ratio, a, b);
}

function maxIdNum(node) {
  if (!isObj(node)) return 0;
  const m = typeof node.id === "string" ? Number(node.id.slice(1)) : 0;
  const here = Number.isInteger(m) && m > 0 ? m : 0;
  return isSplitNode(node) ? Math.max(here, maxIdNum(node.a), maxIdNum(node.b)) : here;
}

function withRoot(tree, root, focus = tree.focus, seq = tree.seq) {
  return root === tree.root && focus === tree.focus && seq === tree.seq ? tree : mkTree(root, focus, seq);
}

/**
 * 葉 1 つ（空）の木。focus はその葉。
 * @returns {{root: {id: string, sel: null}, focus: string, seq: number}}
 */
export function empty() {
  return mkTree(mkLeaf("p1", null), "p1", 1);
}

/**
 * 葉の配列（左→右／上→下の順＝a が先）。木が壊れていれば []。
 * @returns {{id: string, sel: string|null}[]}
 */
export function leaves(tree) {
  return collectLeaves(rootOf(tree), []);
}

/** 葉の数（ペインの数） */
export function leafCount(tree) {
  return leaves(tree).length;
}

/**
 * id の葉（split の id や無い id は null）。
 * @returns {{id: string, sel: string|null}|null}
 */
export function leafOf(tree, id) {
  const n = findNode(rootOf(tree), id);
  return n && !isSplitNode(n) ? n : null;
}

/**
 * sel を持つ葉（無ければ null）。sel が空（null・""）や文字列でなければ null。
 * @param {string} sel "<session id>" か "term:<term id>"
 */
export function find(tree, sel) {
  const s = normSel(sel);
  if (!s) return null;
  return leaves(tree).find((l) => l.sel === s) ?? null;
}

/**
 * id（葉でも split でもよい）の親 split。根・無い id は null。
 * @returns {{id: string, dir: "h"|"v", ratio: number, a: object, b: object}|null}
 */
export function parentOf(tree, id) {
  const walk = (node) => {
    if (!isSplitNode(node)) return null;
    if ((isObj(node.a) && node.a.id === id) || (isObj(node.b) && node.b.id === id)) return node;
    return walk(node.a) ?? walk(node.b);
  };
  return walk(rootOf(tree));
}

/**
 * 葉を 2 つに分ける: その葉を split{ratio: .5, a: 旧葉, b: 新しい空の葉} に置き換え、focus を新しい葉へ。
 * 葉が MAX_PANES ある・葉が無い・dir が "h"/"v" でない → 同じ木。
 * @param {"h"|"v"} dir h = 左右（新しい葉は右）・v = 上下（新しい葉は下）
 */
export function split(tree, leafId, dir) {
  if (dir !== "h" && dir !== "v") return tree;
  const leaf = leafOf(tree, leafId);
  if (!leaf || leafCount(tree) >= MAX_PANES) return tree;
  const n = Math.max(Number.isInteger(tree.seq) ? tree.seq : 0, maxIdNum(tree.root)) + 1;
  const fresh = mkLeaf(`p${n}`, null);
  const root = mapNode(tree.root, (node) => (node === leaf ? mkSplit(`s${n}`, dir, 0.5, leaf, fresh) : node));
  return mkTree(root, fresh.id, n);
}

/**
 * 葉を閉じる: 親 split を兄弟（の部分木）で置き換える。最後の 1 つ・無い葉 → 同じ木。
 * focus が閉じた葉だったら兄弟側の先頭の葉へ移す（それ以外の focus はそのまま）。
 */
export function close(tree, leafId) {
  const leaf = leafOf(tree, leafId);
  if (!leaf || leafCount(tree) <= 1) return tree;
  const parent = parentOf(tree, leafId);
  if (!parent) return tree;
  const sib = parent.a === leaf ? parent.b : parent.a;
  const root = mapNode(tree.root, (node) => (node === parent ? sib : node));
  const focus = tree.focus === leafId || !contains(root, tree.focus) ? collectLeaves(sib, [])[0].id : tree.focus;
  return withRoot(tree, root, focus);
}

/**
 * 葉へ sel を入れる。**同じ sel を持つ他の葉は null にする**（同じ端末を 2 ペインに出さない＝別のペインで選ぶと移動）。
 * sel が null（"" も）なら葉を空に。同じ値・無い葉・sel が文字列でも null でもない → 同じ木。focus は変えない。
 * @param {string|null} sel
 */
export function assign(tree, leafId, sel) {
  const s = normSel(sel);
  if (s === undefined) return tree;
  const leaf = leafOf(tree, leafId);
  if (!leaf) return tree;
  const root = mapNode(tree.root, (node) => {
    if (isSplitNode(node)) return node;
    if (node === leaf) return node.sel === s ? node : mkLeaf(node.id, s);
    return s !== null && node.sel === s ? mkLeaf(node.id, null) : node;
  });
  return withRoot(tree, root);
}

/** sel を持つ葉を空（null）にする（一覧から消えた端末）。持つ葉が無い・sel が空 → 同じ木 */
export function clear(tree, sel) {
  const s = normSel(sel);
  if (!s || !rootOf(tree)) return tree;
  const root = mapNode(tree.root, (node) => (!isSplitNode(node) && node.sel === s ? mkLeaf(node.id, null) : node));
  return withRoot(tree, root);
}

/** focus を葉へ移す。無い葉・既に focus → 同じ木 */
export function setFocus(tree, leafId) {
  if (!leafOf(tree, leafId) || tree.focus === leafId) return tree;
  return mkTree(tree.root, leafId, tree.seq);
}

/** split の ratio を 0.1〜0.9 に clamp して入れる。数でない・無い split・同じ値 → 同じ木 */
export function setRatio(tree, splitId, ratio) {
  if (typeof ratio !== "number" || !Number.isFinite(ratio)) return tree;
  const target = findNode(rootOf(tree), splitId);
  if (!isSplitNode(target)) return tree;
  const r = clamp(ratio, RATIO_MIN, RATIO_MAX);
  if (target.ratio === r) return tree;
  const root = mapNode(tree.root, (node) => (node === target ? mkSplit(node.id, node.dir, r, node.a, node.b) : node));
  return withRoot(tree, root);
}

/**
 * ドラッグの下限・上限: a 側が minA px 以上・b 側が minB px 以上になる ratio に丸め、最後に 0.1〜0.9 へ。
 * 両方は入らない（size < minA + minB + barPx）なら px の条件は捨てて 0.1〜0.9 だけ（layout がその split を畳む）。
 * layout() の bars[] が持つ size・minA・minB をそのまま渡せば、layout が描く位置と一致する。
 * @param {number} size split の方向の長さ（px）
 * @param {number} ratio 置きたい ratio（数でなければ 0.5）
 * @param {number} minA a 側の最低（px）
 * @param {number} minB b 側の最低（px）
 * @param {number} barPx 境目の太さ（px）
 * @returns {number} 0.1〜0.9
 */
export function clampRatio(size, ratio, minA, minB, barPx) {
  let r = num(ratio, 0.5);
  const avail = num(size) - Math.max(0, num(barPx));
  const a = Math.max(0, num(minA));
  const b = Math.max(0, num(minB));
  if (avail > 0 && a + b <= avail) r = clamp(r, a / avail, (avail - b) / avail);
  return clamp(r, RATIO_MIN, RATIO_MAX);
}

/**
 * 葉の矩形・畳んだ葉・境目を決める。
 * 畳む規則: split の方向の長さが「葉 1 つの最低 × 2 ＋ barPx」に足りなければ、その split は focus を含む側（無ければ a）に
 * 全体を渡し、反対側の部分木の葉を全部 hidden にする（入れ子は再帰で同じ規則）。入る split は ratio の位置に境目を置き、
 * 両側が最低を割らないよう位置を clamp する（両方の部分木が畳まずに入るなら、その必要量で clamp＝余計に畳まない）。
 * 整数 px（a 側は floor・b 側は残り）。座標は rect と同じ座標系（根の content box の左上が rect.x, rect.y）。
 * @param {object} tree
 * @param {{x: number, y: number, w: number, h: number}} rect 根の content box
 * @param {{w: number, h: number}} min 葉の最低（40 桁と 8 行相当・呼び手が実測から出す・端数は切り上げ）
 * @param {number} barPx 境目の太さ
 * @returns {{rects: Map<string, {x: number, y: number, w: number, h: number}>, hidden: Set<string>,
 *   bars: {splitId: string, dir: "h"|"v", x: number, y: number, w: number, h: number,
 *          size: number, minA: number, minB: number}[]}}
 *   rects は見えている葉だけ（葉の順）・bars は見えている split だけ（外側が先）。bars の size・minA・minB は
 *   clampRatio(size, ratio, minA, minB, barPx) にそのまま渡すドラッグの範囲。
 */
export function layout(tree, rect, min, barPx) {
  const out = { rects: new Map(), hidden: new Set(), bars: [] };
  const root = rootOf(tree);
  if (!root) return out;
  const r0 = isObj(rect) ? rect : {};
  const base = {
    x: Math.floor(num(r0.x)), y: Math.floor(num(r0.y)),
    w: Math.max(0, Math.floor(num(r0.w))), h: Math.max(0, Math.floor(num(r0.h))),
  };
  const mn = isObj(min) ? min : {};
  const mw = Math.max(0, Math.ceil(num(mn.w)));
  const mh = Math.max(0, Math.ceil(num(mn.h)));
  const bar = Math.max(0, Math.round(num(barPx)));
  const focus = tree.focus;

  // 部分木を畳まずに出すのに要る長さ（horiz＝横方向）
  const need = (node, horiz) => {
    if (!isSplitNode(node)) return horiz ? mw : mh;
    const na = need(node.a, horiz);
    const nb = need(node.b, horiz);
    return (node.dir === "h") === horiz ? na + nb + bar : Math.max(na, nb);
  };

  const place = (node, r) => {
    if (!isSplitNode(node)) {
      out.rects.set(node.id, Object.freeze(r));
      return;
    }
    const horiz = node.dir === "h";
    const size = horiz ? r.w : r.h;
    const m = horiz ? mw : mh;
    const avail = size - bar;
    if (avail < 2 * m) {
      const keep = contains(node.b, focus) ? node.b : node.a;
      const drop = keep === node.a ? node.b : node.a;
      for (const l of collectLeaves(drop, [])) out.hidden.add(l.id);
      place(keep, r);
      return;
    }
    const fa = need(node.a, horiz);
    const fb = need(node.b, horiz);
    const [lo, hi] = fa + fb <= avail ? [fa, avail - fb] : [m, avail - m];
    const ratio = clamp(num(node.ratio, 0.5), RATIO_MIN, RATIO_MAX);
    const pos = clamp(Math.floor(avail * ratio + 1e-9), lo, hi);
    const ra = horiz ? { x: r.x, y: r.y, w: pos, h: r.h } : { x: r.x, y: r.y, w: r.w, h: pos };
    const rb = horiz
      ? { x: r.x + pos + bar, y: r.y, w: avail - pos, h: r.h }
      : { x: r.x, y: r.y + pos + bar, w: r.w, h: avail - pos };
    const br = horiz ? { x: r.x + pos, y: r.y, w: bar, h: r.h } : { x: r.x, y: r.y + pos, w: r.w, h: bar };
    out.bars.push(Object.freeze({ splitId: node.id, dir: node.dir, ...br, size, minA: lo, minB: avail - hi }));
    place(node.a, ra);
    place(node.b, rb);
  };

  place(root, base);
  return out;
}

/**
 * 幾何で隣の葉を決める（木の隣接ではない）: dir の向きの先にあり、直交方向に重なりがある見えている葉のうち、
 * 辺の距離が最小 → 直交方向の重なりが最大 → 上（左）寄りの順で 1 つ。無ければ null。
 * 距離を先に見るのは、遠い列の大きい葉が間の列を飛び越えないため（3 列 A | B/C | D で D から左は B/C、A ではない）。
 * @param {{rects: Map<string, {x: number, y: number, w: number, h: number}>}} layoutResult layout() の結果
 * @param {string} leafId
 * @param {"left"|"right"|"up"|"down"} dir
 * @returns {string|null} 葉の id
 */
export function neighbor(layoutResult, leafId, dir) {
  const rects = isObj(layoutResult) && layoutResult.rects instanceof Map ? layoutResult.rects : null;
  const me = rects?.get(leafId);
  if (!me || !["left", "right", "up", "down"].includes(dir)) return null;
  const horiz = dir === "left" || dir === "right";
  let best = null;
  for (const [id, c] of rects) {
    if (id === leafId) continue;
    let dist;
    if (dir === "left") dist = me.x - (c.x + c.w);
    else if (dir === "right") dist = c.x - (me.x + me.w);
    else if (dir === "up") dist = me.y - (c.y + c.h);
    else dist = c.y - (me.y + me.h);
    if (dist < 0) continue;
    const overlap = horiz
      ? Math.min(me.y + me.h, c.y + c.h) - Math.max(me.y, c.y)
      : Math.min(me.x + me.w, c.x + c.w) - Math.max(me.x, c.x);
    if (overlap <= 0) continue;
    const pos = horiz ? c.y : c.x;
    if (!best || dist < best.dist || (dist === best.dist && (overlap > best.overlap
      || (overlap === best.overlap && pos < best.pos)))) {
      best = { id, dist, overlap, pos };
    }
  }
  return best ? best.id : null;
}

/**
 * 保存の形 `{ v: 1, root, focus }`（素の object・JSON.stringify にそのまま渡せる）。seq は保存しない（parse が数え直す）。
 * @returns {{v: 1, root: object, focus: string}}
 */
export function serialize(tree) {
  const root = rootOf(tree);
  if (!root) return serialize(empty());
  const plain = (n) => (isSplitNode(n)
    ? { id: n.id, dir: n.dir, ratio: n.ratio, a: plain(n.a), b: plain(n.b) }
    : { id: n.id, sel: n.sel ?? null });
  return { v: 1, root: plain(root), focus: tree.focus };
}

/**
 * 保存から木を戻す（JSON 文字列でも object でもよい・入力は変えない）。形を検証し、壊れていれば empty():
 * v が 1・split は id "s<n>"・dir "h"|"v"・ratio 0.1〜0.9 の数・a と b・葉は id "p<n>"・sel は 256 字以下の string か null・
 * 葉 1〜MAX_PANES・id の重複なし。sel の重複は後勝ち（前の葉を null）・focus が葉でなければ先頭の葉・seq は id の最大で数え直す。
 */
export function parse(obj) {
  let o = obj;
  if (typeof o === "string") {
    try { o = JSON.parse(o); } catch { return empty(); }
  }
  if (!isObj(o) || o.v !== 1) return empty();
  const ids = new Set();
  const order = [];
  let ok = true;
  const build = (n, depth) => {
    if (!ok) return null;
    if (!isObj(n) || depth > MAX_PANES || typeof n.id !== "string" || ids.has(n.id)) { ok = false; return null; }
    ids.add(n.id);
    if ("dir" in n || "a" in n || "b" in n || "ratio" in n) {
      const { ratio } = n;
      if (!SPLIT_ID.test(n.id) || (n.dir !== "h" && n.dir !== "v") || typeof ratio !== "number"
        || !Number.isFinite(ratio) || ratio < RATIO_MIN || ratio > RATIO_MAX) { ok = false; return null; }
      const a = build(n.a, depth + 1);
      const b = build(n.b, depth + 1);
      return ok ? { id: n.id, dir: n.dir, ratio, a, b } : null;
    }
    const sel = normSel(n.sel);
    if (!LEAF_ID.test(n.id) || sel === undefined || order.length >= MAX_PANES) { ok = false; return null; }
    const leaf = { id: n.id, sel };
    order.push(leaf);
    return leaf;
  };
  const plain = build(o.root, 0);
  if (!ok || !plain || order.length < 1) return empty();
  const seen = new Set();
  for (let i = order.length - 1; i >= 0; i -= 1) {
    const s = order[i].sel;
    if (s === null) continue;
    if (seen.has(s)) order[i].sel = null;
    else seen.add(s);
  }
  const freeze = (n) => (n.dir ? mkSplit(n.id, n.dir, n.ratio, freeze(n.a), freeze(n.b)) : mkLeaf(n.id, n.sel));
  const root = freeze(plain);
  const focus = typeof o.focus === "string" && order.some((l) => l.id === o.focus) ? o.focus : order[0].id;
  return mkTree(root, focus, maxIdNum(root));
}

/** 葉が 1 つか（sel は問わない）＝見出しも枠も出さない従来の 1 ペイン */
export function isSingle(tree) {
  return leafCount(tree) === 1;
}
