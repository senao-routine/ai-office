// R111-B1: 端末のペインの分割木（ui/core/termpanes.js）の単体テスト。
// 上限 4・最後の 1 つ・同じ端末は 1 葉だけ・畳み・幾何の隣・保存の往復と拒否・immutable（無変化は同一参照）。
import test from "node:test";
import assert from "node:assert/strict";
import {
  MAX_PANES, assign, clampRatio, clear, close, empty, find, isSingle, layout, leafCount, leafOf, leaves,
  neighbor, parentOf, parse, serialize, setFocus, setRatio, split,
} from "./termpanes.js";

const ids = (tree) => leaves(tree).map((l) => l.id);
const sels = (tree) => leaves(tree).map((l) => l.sel);
const clone = (v) => JSON.parse(JSON.stringify(v));

/** 1＋2 の形: 左に大きい葉 L・右に上 TR／下 BR（focus は呼び手が決める） */
function onePlusTwo() {
  let t = empty();
  const L = t.focus;
  t = split(t, L, "h");
  const TR = t.focus;
  t = split(t, TR, "v");
  const BR = t.focus;
  return { t, L, TR, BR };
}

/** 2×2: h(v(TL, BL), v(TR, BR)) */
function twoByTwo() {
  let t = empty();
  const TL = t.focus;
  t = split(t, TL, "h");
  const TR = t.focus;
  t = split(t, TL, "v");
  const BL = t.focus;
  t = split(t, TR, "v");
  const BR = t.focus;
  return { t, TL, TR, BL, BR };
}

/** すべての節が凍っているか */
function deepFrozen(n) {
  if (!Object.isFrozen(n)) return false;
  return n.dir ? deepFrozen(n.a) && deepFrozen(n.b) : true;
}

test("termpanes: empty は空の葉 1 つ・focus はその葉・isSingle", () => {
  const t = empty();
  assert.equal(MAX_PANES, 4);
  assert.equal(leafCount(t), 1);
  assert.equal(t.focus, t.root.id);
  assert.equal(t.root.sel, null);
  assert.equal(isSingle(t), true);
  assert.equal(leafOf(t, t.focus), t.root);
  assert.equal(parentOf(t, t.focus), null);
  assert.ok(deepFrozen(t) && Object.isFrozen(t));
  assert.deepEqual(leaves(null), []);
  assert.equal(leafCount({}), 0);
});

test("termpanes: split は新しい空の葉を b 側に作り focus を移す・葉の順は左→右／上→下", () => {
  let t = assign(empty(), "p1", "sess-a");
  const before = t;
  t = split(t, "p1", "h");
  assert.equal(leafCount(t), 2);
  assert.equal(t.root.dir, "h");
  assert.equal(t.root.ratio, 0.5);
  assert.equal(t.root.a, before.root, "旧葉は同じ参照のまま a に");
  assert.equal(t.root.b.sel, null, "新しい葉は空");
  assert.equal(t.focus, t.root.b.id);
  assert.deepEqual(sels(t), ["sess-a", null]);
  assert.equal(parentOf(t, t.focus), t.root);
  assert.equal(isSingle(t), false);
  // v は上下（a が上）・id は重ならない
  const { t: q, L, TR, BR } = onePlusTwo();
  assert.deepEqual(ids(q), [L, TR, BR]);
  assert.equal(new Set([...ids(q), q.root.id, q.root.b.id]).size, 5, "葉と split の id が重複している");
  assert.equal(q.root.b.dir, "v");
  assert.equal(q.focus, BR);
});

test("termpanes: 5 つ目は拒む（同一参照）・無い葉や不正な dir も同一参照", () => {
  const { t } = twoByTwo();
  assert.equal(leafCount(t), MAX_PANES);
  for (const id of ids(t)) {
    assert.equal(split(t, id, "h"), t, `${id} の分割で 5 つ目ができた`);
    assert.equal(split(t, id, "v"), t);
  }
  const one = empty();
  assert.equal(split(one, "p9", "h"), one);
  assert.equal(split(one, "p1", "x"), one);
  assert.equal(split(one, "p1"), one);
  // split の id は分割の対象にならない
  const two = split(one, "p1", "h");
  assert.equal(split(two, two.root.id, "v"), two);
});

test("termpanes: 最後の 1 つは閉じない（同一参照）・無い葉も同一参照", () => {
  const t = empty();
  assert.equal(close(t, t.focus), t);
  assert.equal(close(t, "p7"), t);
  const two = split(t, "p1", "h");
  assert.equal(close(two, two.root.id), two, "split の id では閉じない");
});

test("termpanes: close で兄弟（の部分木）が昇格し、閉じた葉が focus なら兄弟側の先頭の葉へ", () => {
  const { t, L, TR, BR } = onePlusTwo();
  // 左の大きい葉を閉じる → 右の v split が根へ・focus（BR）はそのまま
  const a = close(t, L);
  assert.equal(a.root, t.root.b, "兄弟の部分木が同じ参照のまま昇格していない");
  assert.deepEqual(ids(a), [TR, BR]);
  assert.equal(a.focus, BR);
  // focus の BR を閉じる → TR が昇格し focus は TR
  const b = close(t, BR);
  assert.deepEqual(ids(b), [L, TR]);
  assert.equal(b.root.b, leafOf(t, TR));
  assert.equal(b.focus, TR);
  // focus を L にしてから L を閉じる → 兄弟側（右の部分木）の先頭＝TR
  const c = close(setFocus(t, L), L);
  assert.equal(c.focus, TR);
  // 閉じても seq は戻らない＝次の分割で id が再利用されない
  const d = split(c, TR, "h");
  assert.equal(new Set(ids(d)).size, leafCount(d));
  assert.ok(!ids(d).includes(L), "閉じた葉の id を再利用した");
  assert.ok(d.seq > t.seq);
});

test("termpanes: assign は同じ sel を持つ他の葉を null にする（移動）・同じ値は同一参照", () => {
  const { t, L, TR, BR } = onePlusTwo();
  let x = assign(t, L, "term:aaaaaaaaaaaaaaaa");
  assert.deepEqual(sels(x), ["term:aaaaaaaaaaaaaaaa", null, null]);
  assert.equal(x.focus, t.focus, "assign が focus を動かした");
  // 別のペインで同じ端末を選ぶ → 移動（2 ペインに同じ sel が無い）
  x = assign(x, TR, "term:aaaaaaaaaaaaaaaa");
  assert.deepEqual(sels(x), [null, "term:aaaaaaaaaaaaaaaa", null]);
  x = assign(x, BR, "sess-1");
  assert.equal(find(x, "sess-1").id, BR);
  assert.equal(find(x, "term:aaaaaaaaaaaaaaaa").id, TR);
  assert.equal(find(x, "nope"), null);
  assert.equal(find(x, null), null);
  // 同じ値・無い葉・不正な sel（数・undefined・長すぎる文字列）は同一参照
  assert.equal(assign(x, BR, "sess-1"), x);
  assert.equal(assign(x, "p99", "sess-1"), x);
  assert.equal(assign(x, BR, 42), x);
  assert.equal(assign(x, BR), x);
  assert.equal(assign(x, BR, "s".repeat(300)), x);
  // null（と ""）は葉を空に・他の葉は触らない
  const y = assign(x, BR, null);
  assert.deepEqual(sels(y), [null, "term:aaaaaaaaaaaaaaaa", null]);
  assert.equal(assign(y, BR, ""), y);
  // 触っていない部分木は同じ参照を共有する
  assert.equal(y.root.a, x.root.a);
});

test("termpanes: clear は sel を持つ葉を空に・持つ葉が無ければ同一参照", () => {
  const { t, TR } = onePlusTwo();
  const x = assign(t, TR, "term:bbbbbbbbbbbbbbbb");
  const y = clear(x, "term:bbbbbbbbbbbbbbbb");
  assert.deepEqual(sels(y), [null, null, null]);
  assert.equal(y.focus, x.focus);
  assert.equal(clear(y, "term:bbbbbbbbbbbbbbbb"), y);
  assert.equal(clear(y, null), y);
  assert.equal(clear(y, 3), y);
});

test("termpanes: setFocus・setRatio（0.1〜0.9 に clamp・同じ値と不正は同一参照）", () => {
  const { t, L } = onePlusTwo();
  const f = setFocus(t, L);
  assert.equal(f.focus, L);
  assert.equal(f.root, t.root);
  assert.equal(setFocus(f, L), f);
  assert.equal(setFocus(f, "p99"), f);
  assert.equal(setFocus(f, t.root.id), f, "split に focus を置いた");

  const sid = t.root.id;
  assert.equal(setRatio(t, sid, 2).root.ratio, 0.9);
  assert.equal(setRatio(t, sid, -1).root.ratio, 0.1);
  assert.equal(setRatio(t, sid, 0.3).root.ratio, 0.3);
  assert.equal(setRatio(t, sid, 0.5), t, "同じ値で新しい木を返した");
  const r9 = setRatio(t, sid, 0.9);
  assert.equal(setRatio(r9, sid, 5), r9, "clamp 後に同じ値なら同一参照");
  assert.equal(setRatio(t, sid, NaN), t);
  assert.equal(setRatio(t, sid, "0.3"), t);
  assert.equal(setRatio(t, "s99", 0.3), t);
  assert.equal(setRatio(t, L, 0.3), t, "葉の id で ratio を入れた");
  // 内側の split だけ変えると外側の a（左の葉）は同じ参照
  const inner = setRatio(t, t.root.b.id, 0.25);
  assert.equal(inner.root.b.ratio, 0.25);
  assert.equal(inner.root.a, t.root.a);
});

test("termpanes: clampRatio は両側の最低を守り、最後に 0.1〜0.9 へ", () => {
  // avail = 1000 − 8 = 992。a ≥ 300・b ≥ 300
  const lo = clampRatio(1000, 0.05, 300, 300, 8);
  const hi = clampRatio(1000, 0.95, 300, 300, 8);
  assert.ok(Math.floor(992 * lo + 1e-9) >= 300, `a 側 ${992 * lo}`);
  assert.ok(992 - Math.floor(992 * hi + 1e-9) >= 300, `b 側 ${992 - 992 * hi}`);
  assert.equal(clampRatio(1000, 0.5, 300, 300, 8), 0.5);
  // 最低が小さくても 0.1〜0.9 は守る
  assert.equal(clampRatio(1000, 0.01, 0, 0, 8), 0.1);
  assert.equal(clampRatio(1000, 0.99, 0, 0, 8), 0.9);
  // 両方は入らない → px の条件は捨てて 0.1〜0.9 だけ
  assert.equal(clampRatio(500, 0.3, 300, 300, 8), 0.3);
  assert.equal(clampRatio(500, 0.95, 300, 300, 8), 0.9);
  // 数でない ratio は 0.5
  assert.equal(clampRatio(1000, NaN, 0, 0, 8), 0.5);
});

test("termpanes: layout の 1 ペインは rect 全体・整数 px", () => {
  const t = empty();
  const L = layout(t, { x: 10.6, y: 4.2, w: 640.9, h: 400.5 }, { w: 300, h: 100 }, 8);
  assert.deepEqual([...L.rects.keys()], ["p1"]);
  assert.deepEqual(L.rects.get("p1"), { x: 10, y: 4, w: 640, h: 400 });
  assert.equal(L.hidden.size, 0);
  assert.deepEqual(L.bars, []);
  // 狭すぎても 1 ペインは畳まない（隠す相手が無い）
  const N = layout(t, { x: 0, y: 0, w: 50, h: 20 }, { w: 300, h: 100 }, 8);
  assert.deepEqual(N.rects.get("p1"), { x: 0, y: 0, w: 50, h: 20 });
});

test("termpanes: layout は左右で入らなければ focus 側だけ・入る幅なら両方（a は floor・b は残り）", () => {
  let t = split(empty(), "p1", "h");
  const [A, B] = ids(t);
  const min = { w: 300, h: 100 };
  // 入る: avail = 701 − 8 = 693
  const wide = layout(t, { x: 0, y: 0, w: 701, h: 400 }, min, 8);
  assert.equal(wide.hidden.size, 0);
  const ra = wide.rects.get(A);
  const rb = wide.rects.get(B);
  assert.deepEqual(ra, { x: 0, y: 0, w: 346, h: 400 });   // floor((701 − 8) × .5)
  assert.deepEqual(rb, { x: 354, y: 0, w: 347, h: 400 }); // 残り
  assert.equal(ra.w + 8 + rb.w, 701);
  assert.equal(wide.bars.length, 1);
  assert.deepEqual({ ...wide.bars[0] }, { splitId: t.root.id, dir: "h", x: 346, y: 0, w: 8, h: 400, size: 701, minA: 300, minB: 300 });
  // ちょうど 2 × 300 + 8 は入る・1 px 足りなければ畳む
  assert.equal(layout(t, { x: 0, y: 0, w: 608, h: 400 }, min, 8).hidden.size, 0);
  const narrow = layout(t, { x: 0, y: 0, w: 607, h: 400 }, min, 8);
  assert.deepEqual([...narrow.rects.keys()], [B], "focus（新しい葉 B）側が残っていない");
  assert.deepEqual(narrow.rects.get(B), { x: 0, y: 0, w: 607, h: 400 });
  assert.deepEqual([...narrow.hidden], [A]);
  assert.deepEqual(narrow.bars, []);
  // focus を a に移すと a が残る
  t = setFocus(t, A);
  const narrowA = layout(t, { x: 0, y: 0, w: 607, h: 400 }, min, 8);
  assert.deepEqual([...narrowA.rects.keys()], [A]);
  assert.deepEqual([...narrowA.hidden], [B]);
  // ratio が最低を割る位置でも px は最低で止まる
  const skew = layout(setRatio(t, t.root.id, 0.1), { x: 0, y: 0, w: 700, h: 400 }, min, 8);
  assert.equal(skew.rects.get(A).w, 300);
  assert.equal(skew.rects.get(B).w, 392);
});

test("termpanes: layout の入れ子（1＋2）＝高さが足りなければ右の列だけ focus 側に畳む・両方入れば 3 つ", () => {
  const { t, L, TR, BR } = onePlusTwo();   // focus = BR
  const min = { w: 300, h: 100 };
  // 高さ 200: 右の列は 100 + 100 + 8 = 208 に届かない → BR だけ・TR は hidden
  const low = layout(t, { x: 0, y: 0, w: 800, h: 200 }, min, 8);
  assert.deepEqual([...low.rects.keys()], [L, BR]);
  assert.deepEqual([...low.hidden], [TR]);
  assert.deepEqual(low.rects.get(BR), { x: 404, y: 0, w: 396, h: 200 });
  assert.equal(low.bars.length, 1, "畳んだ split の境目が残っている");
  // focus が左なら右の列は a（TR）を残す
  const lowL = layout(setFocus(t, L), { x: 0, y: 0, w: 800, h: 200 }, min, 8);
  assert.deepEqual([...lowL.rects.keys()], [L, TR]);
  assert.deepEqual([...lowL.hidden], [BR]);
  // 両方入る
  const tall = layout(t, { x: 0, y: 0, w: 800, h: 400 }, min, 8);
  assert.deepEqual([...tall.rects.keys()], [L, TR, BR]);
  assert.equal(tall.hidden.size, 0);
  assert.deepEqual(tall.rects.get(TR), { x: 404, y: 0, w: 396, h: 196 });
  assert.deepEqual(tall.rects.get(BR), { x: 404, y: 204, w: 396, h: 196 });
  assert.deepEqual(tall.bars.map((b) => b.splitId), [t.root.id, t.root.b.id], "外側の境目が先");
  // 幅も高さも足りない → 外側が focus（BR）側を残し、その中でまた BR
  const tiny = layout(t, { x: 0, y: 0, w: 400, h: 150 }, min, 8);
  assert.deepEqual([...tiny.rects.keys()], [BR]);
  assert.deepEqual(new Set(tiny.hidden), new Set([L, TR]));
  assert.deepEqual(tiny.rects.get(BR), { x: 0, y: 0, w: 400, h: 150 });
});

test("termpanes: layout は畳まずに入るなら部分木の必要量で位置を clamp（3 列が 3 列のまま）", () => {
  // h(A, h(B, C))＝3 列。幅 3 × 300 + 2 × 8 = 916 なら ratio .5 でも全部見える
  let t = split(empty(), "p1", "h");
  const A = "p1";
  const B = t.focus;
  t = split(t, B, "h");
  const C = t.focus;
  const fit = layout(t, { x: 0, y: 0, w: 916, h: 300 }, { w: 300, h: 100 }, 8);
  assert.deepEqual([...fit.rects.keys()], [A, B, C]);
  assert.equal(fit.hidden.size, 0);
  for (const r of fit.rects.values()) assert.ok(r.w >= 300, JSON.stringify(r));
  // 1 px 足りなければ外側は 2 つ入る → 右の部分木が畳まれ focus（C）が残る
  const short = layout(t, { x: 0, y: 0, w: 915, h: 300 }, { w: 300, h: 100 }, 8);
  assert.deepEqual([...short.rects.keys()], [A, C]);
  assert.deepEqual([...short.hidden], [B]);
  // bars の範囲を clampRatio に渡せば layout が描く位置と一致する
  const bar = fit.bars[0];
  const r = clampRatio(bar.size, 0.1, bar.minA, bar.minB, 8);
  const moved = layout(setRatio(t, bar.splitId, r), { x: 0, y: 0, w: 916, h: 300 }, { w: 300, h: 100 }, 8);
  assert.equal(moved.rects.get(A).w, Math.floor((bar.size - 8) * r + 1e-9));
});

test("termpanes: neighbor は木の隣接でなく幾何（1＋2 の右上から左は左の大きい葉）", () => {
  const { t, L, TR, BR } = onePlusTwo();
  const lay = layout(t, { x: 0, y: 0, w: 800, h: 400 }, { w: 300, h: 100 }, 8);
  assert.equal(neighbor(lay, TR, "left"), L);
  assert.equal(neighbor(lay, BR, "left"), L);
  assert.equal(neighbor(lay, TR, "down"), BR);
  assert.equal(neighbor(lay, BR, "up"), TR);
  assert.equal(neighbor(lay, L, "right"), TR, "重なりが同じなら上寄り");
  assert.equal(neighbor(lay, L, "left"), null);
  assert.equal(neighbor(lay, L, "up"), null);
  assert.equal(neighbor(lay, TR, "right"), null);
  assert.equal(neighbor(lay, TR, "sideways"), null);
  assert.equal(neighbor(lay, "p99", "left"), null);
  assert.equal(neighbor(null, TR, "left"), null);
  // 右の列の境目を下げると、左から右は重なりの大きい BR
  const low = layout(setRatio(t, t.root.b.id, 0.2), { x: 0, y: 0, w: 800, h: 400 }, { w: 300, h: 50 }, 8);
  assert.equal(neighbor(low, L, "right"), BR);
  // 畳まれた葉は隣にならないし、そこから動けない
  const folded = layout(t, { x: 0, y: 0, w: 800, h: 200 }, { w: 300, h: 100 }, 8);
  assert.equal(neighbor(folded, L, "right"), BR);
  assert.equal(neighbor(folded, TR, "left"), null);
});

test("termpanes: neighbor は 2×2 で左下から右へ行くと右下（木の兄弟の右上ではない）・3 列は間の列を飛ばさない", () => {
  const { t, TL, TR, BL, BR } = twoByTwo();
  const lay = layout(t, { x: 0, y: 0, w: 800, h: 400 }, { w: 300, h: 100 }, 8);
  assert.equal(neighbor(lay, BL, "right"), BR);
  assert.equal(neighbor(lay, TL, "right"), TR);
  assert.equal(neighbor(lay, BR, "left"), BL);
  assert.equal(neighbor(lay, TR, "down"), BR);
  // 3 列 A | B/C | D: D から左は間の列（重なりの大きい C）であって、遠い A ではない
  let u = split(empty(), "p1", "h");
  const A = "p1";
  const B = u.focus;
  u = split(u, B, "h");
  const D = u.focus;
  u = split(u, B, "v");
  const C = u.focus;
  u = setRatio(u, parentOf(u, C).id, 0.3);
  const lay3 = layout(u, { x: 0, y: 0, w: 1000, h: 400 }, { w: 200, h: 80 }, 8);
  assert.equal(lay3.hidden.size, 0);
  assert.equal(neighbor(lay3, D, "left"), C);
  assert.equal(neighbor(lay3, A, "right"), C);
});

test("termpanes: serialize ⇄ parse の往復（object と JSON 文字列）・seq は数え直す", () => {
  const { t, L, TR, BR } = onePlusTwo();
  let x = assign(t, L, "sess-uuid-1");
  x = assign(x, BR, "term:0123456789abcdef");
  x = setRatio(x, x.root.id, 0.37);
  x = setFocus(x, TR);
  const s = serialize(x);
  assert.equal(s.v, 1);
  assert.equal(s.focus, TR);
  assert.ok(!("seq" in s));
  assert.equal(Object.isFrozen(s.root), false, "保存の形は素の object");
  const back = parse(s);
  assert.deepEqual(serialize(back), s);
  assert.deepEqual(back, x, "往復で木が変わった");
  assert.ok(deepFrozen(back.root) && Object.isFrozen(back));
  const fromJson = parse(JSON.stringify(s));
  assert.deepEqual(fromJson, x);
  // 数え直した seq で次の分割の id が既存と重ならない
  const more = split(back, TR, "h");
  assert.equal(leafCount(more), 4);
  const all = [];
  const walk = (n) => { all.push(n.id); if (n.dir) { walk(n.a); walk(n.b); } };
  walk(more.root);
  assert.equal(new Set(all).size, all.length, `id が重複: ${all}`);
  // 1 ペインの往復
  assert.deepEqual(parse(serialize(empty())), empty());
  assert.equal(isSingle(parse(serialize(assign(empty(), "p1", "x")))), true);
  // 壊れた木の serialize は empty の形
  assert.deepEqual(serialize(null), serialize(empty()));
});

test("termpanes: parse は壊れた保存を empty() にする（ratio 2・葉 5・dir x・sel が数 ほか）", () => {
  const leaf = (id, sel = null) => ({ id, sel });
  const sp = (id, a, b, dir = "h", ratio = 0.5) => ({ id, dir, ratio, a, b });
  const good = { v: 1, root: sp("s2", leaf("p1", "a"), leaf("p2", "b")), focus: "p2" };
  assert.deepEqual(serialize(parse(good)), good, "正しい保存を拒んだ");
  const five = sp("s5", sp("s2", leaf("p1"), leaf("p2")), sp("s4", sp("s3", leaf("p3"), leaf("p4")), leaf("p5")));
  const broken = {
    "ratio 2": { ...good, root: sp("s2", leaf("p1"), leaf("p2"), "h", 2) },
    "ratio 0.05": { ...good, root: sp("s2", leaf("p1"), leaf("p2"), "h", 0.05) },
    "ratio 文字列": { ...good, root: sp("s2", leaf("p1"), leaf("p2"), "h", "0.5") },
    "葉 5": { v: 1, root: five, focus: "p1" },
    'dir "x"': { ...good, root: sp("s2", leaf("p1"), leaf("p2"), "x") },
    "sel が数": { ...good, root: sp("s2", leaf("p1", 7), leaf("p2")) },
    "sel が無い": { ...good, root: sp("s2", { id: "p1" }, leaf("p2")) },
    "sel が長すぎる": { ...good, root: sp("s2", leaf("p1", "x".repeat(300)), leaf("p2")) },
    "id の重複": { ...good, root: sp("s2", leaf("p1"), leaf("p1")) },
    "id の形": { ...good, root: sp("s2", leaf("q1"), leaf("p2")) },
    "split の id の形": { ...good, root: sp("p3", leaf("p1"), leaf("p2")) },
    "b が無い": { ...good, root: { id: "s2", dir: "h", ratio: 0.5, a: leaf("p1") } },
    "v 違い": { ...good, v: 2 },
    "v 無し": { root: good.root, focus: "p1" },
    "root 無し": { v: 1, focus: "p1" },
  };
  for (const [why, obj] of Object.entries(broken)) {
    assert.deepEqual(parse(obj), empty(), why);
  }
  for (const junk of [null, undefined, 3, "garbage{", "[]", [], "null", true]) {
    assert.deepEqual(parse(junk), empty(), String(junk));
  }
  // 深い入れ子（循環）でも止まる
  const cyc = { id: "s2", dir: "h", ratio: 0.5, b: leaf("p2") };
  cyc.a = cyc;
  assert.deepEqual(parse({ v: 1, root: cyc, focus: "p2" }), empty());
});

test("termpanes: parse は sel の重複を後勝ちで解く・focus が葉でなければ先頭の葉・空文字の sel は null", () => {
  const obj = {
    v: 1,
    root: { id: "s3", dir: "v", ratio: 0.5,
      a: { id: "s2", dir: "h", ratio: 0.4, a: { id: "p1", sel: "dup" }, b: { id: "p2", sel: "" } },
      b: { id: "p3", sel: "dup" } },
    focus: "s3",
  };
  const t = parse(obj);
  assert.deepEqual(sels(t), [null, null, "dup"]);
  assert.equal(t.focus, "p1");
  assert.equal(t.seq, 3);
  assert.equal(obj.root.a.a.sel, "dup", "parse が入力を書き換えた");
  assert.equal(parse({ ...obj, focus: "p2" }).focus, "p2");
});

test("termpanes: immutable（入力が変わらない・無変化は同一参照・触っていない部分木は共有）", () => {
  const { t, L, TR, BR } = onePlusTwo();
  const x = assign(assign(t, L, "a"), BR, "b");
  const snap = clone(x);
  const ops = [
    () => split(x, TR, "h"), () => split(x, TR, "v"), () => close(x, L), () => close(x, BR),
    () => assign(x, TR, "a"), () => assign(x, L, null), () => clear(x, "b"), () => setFocus(x, L),
    () => setRatio(x, x.root.id, 0.2), () => layout(x, { x: 0, y: 0, w: 900, h: 500 }, { w: 300, h: 100 }, 8),
    () => serialize(x), () => parse(serialize(x)), () => find(x, "a"), () => parentOf(x, BR),
  ];
  for (const op of ops) {
    op();
    assert.deepEqual(clone(x), snap, `入力が変わった: ${op}`);
  }
  assert.ok(deepFrozen(x.root) && Object.isFrozen(x), "節が凍っていない");
  // 無変化
  assert.equal(assign(x, L, "a"), x);
  assert.equal(clear(x, "zzz"), x);
  assert.equal(setFocus(x, x.focus), x);
  assert.equal(setRatio(x, x.root.id, x.root.ratio), x);
  assert.equal(close(empty(), "p1").root.id, "p1");
  // 構造の共有: 右の列だけ変えると左の葉は同じ参照
  const y = assign(x, TR, "c");
  assert.equal(y.root.a, x.root.a);
  assert.notEqual(y.root.b, x.root.b);
  assert.equal(y.root.b.b, x.root.b.b);
  // layout の結果も凍っている（呼び手が書き換えて次の描画を汚さない）
  const lay = layout(x, { x: 0, y: 0, w: 900, h: 500 }, { w: 300, h: 100 }, 8);
  assert.ok(Object.isFrozen(lay.rects.get(L)) && Object.isFrozen(lay.bars[0]));
});
