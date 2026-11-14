// R110-S6: 名札の置き場所（placePlates）の門。足元に置く・重なれば下へ 1 段・部屋の下へはみ出せば頭の上・枠の内・
// 決定論・入力を書き換えない・逃がし切れない札も消さない（重ねて overlap: true）。
import test from "node:test";
import assert from "node:assert/strict";
import { PLATE_GAP, PLATE_TRIES, placePlates } from "./plates.js";

const B = { w: 552, h: 480 };   // ×3 の部屋
/** 2 つの置き場所が隙間 0 でも重なるか（門は「互いに重ならない」＝矩形の交わり） */
const overlaps = (a, b) => a.left < b.left + b.w && a.left + a.w > b.left && a.top < b.top + b.h && a.top + a.h > b.top;
const within = (r, b = B) => r.left >= 0 && r.top >= 0 && r.left + r.w <= b.w && r.top + r.h <= b.h;

test("placePlates: 重ならない札は足元の中心のまま（左右は中心・上端は渡した top）", () => {
  const got = placePlates([{ cx: 100, top: 200, w: 60, h: 18 }, { cx: 300, top: 200, w: 60, h: 18 }], B);
  assert.deepEqual(got.map((r) => [r.left, r.top, r.side, r.overlap]), [[70, 200, "foot", false], [270, 200, "foot", false]]);
});

test("placePlates: 先に置いた札と重なれば、その札の下（下端＋隙間）へ 1 段逃がす", () => {
  const got = placePlates([{ cx: 100, top: 200, w: 80, h: 18 }, { cx: 130, top: 200, w: 80, h: 18 }], B);
  assert.equal(got[1].top, 200 + 18 + PLATE_GAP.y);
  assert.equal(got[1].side, "down");
  assert.ok(!overlaps(got[0], got[1]));
});

test("placePlates: 左右は枠の内へ寄せる（中心をずらす）", () => {
  const got = placePlates([{ cx: 5, top: 10, w: 60, h: 18 }, { cx: 550, top: 10, w: 60, h: 18 }], B);
  assert.equal(got[0].left, 0);
  assert.equal(got[1].left, B.w - 60);
  assert.ok(got.every((r) => within(r)));
});

test("placePlates: 部屋の下へはみ出す札は頭の上へ回し、そこからは上へ逃がす", () => {
  // 受付・待機の列（×3・足元 y=150 → 451・頭 y=134 → 402）。3 人が 51px 間隔＝札 100px は左右で重なる
  const row = [399, 450, 501].map((cx) => ({ cx, top: 451, head: 402, w: 100, h: 18 }));
  const got = placePlates(row, B);
  assert.equal(got[0].side, "foot");
  assert.ok(got.slice(1).some((r) => r.side === "up"), JSON.stringify(got));
  assert.ok(got.every((r) => within(r) && !r.overlap), JSON.stringify(got));
  for (let i = 0; i < got.length; i += 1) {
    for (let j = i + 1; j < got.length; j += 1) assert.ok(!overlaps(got[i], got[j]), `${i} と ${j} が重なる`);
  }
});

test("placePlates: 固定ワールド相当の 18 人（×3・×4）でも全員が枠の内・互いに重ならない", () => {
  // tests/fixtures/world/cockpit.json を ×3 で描いたときの足元（index.js が roomActors から作るのと同じ式）
  const feet = [[71, 18], [10, 19], [27, 19], [138, 27], [21, 45], [61, 45], [101, 45], [21, 72], [61, 72], [101, 72],
    [21, 99], [61, 99], [101, 99], [15, 132], [32, 132], [161, 134], [144, 134], [127, 134]];
  for (const [S, w] of [[3, 104], [4, 150]]) {
    const plates = feet.slice().sort((a, b) => a[1] - b[1] || a[0] - b[0])
      .map(([x, y]) => ({ cx: (x + 6) * S, top: (y + 16) * S + 1, head: y * S, w, h: 18 }));
    const got = placePlates(plates, { w: 184 * S, h: 160 * S });
    assert.equal(got.length, feet.length);
    assert.ok(got.every((r) => r && within(r, { w: 184 * S, h: 160 * S }) && !r.overlap), `×${S}: ${JSON.stringify(got)}`);
    for (let i = 0; i < got.length; i += 1) {
      for (let j = i + 1; j < got.length; j += 1) assert.ok(!overlaps(got[i], got[j]), `×${S}: ${i} と ${j} が重なる`);
    }
  }
});

test("placePlates: 逃がし切れない札は消さずに足元へ重ねる（overlap: true・枠の内）", () => {
  // 高さ 40 の枠に 18px の札を同じ場所へ 4 枚＝2 段しか入らない
  const box = { w: 200, h: 40 };
  const got = placePlates(Array.from({ length: 4 }, () => ({ cx: 100, top: 0, head: 0, w: 80, h: 18 })), box);
  assert.equal(got.length, 4);
  assert.deepEqual(got.map((r) => r.overlap), [false, false, true, true]);
  assert.ok(got.every((r) => within(r, box)));
});

test("placePlates: 段の上限（tries）を超えて遠くへ流さない", () => {
  const stack = Array.from({ length: PLATE_TRIES + 3 }, () => ({ cx: 100, top: 0, w: 80, h: 10 }));
  const got = placePlates(stack, { w: 200, h: 2000 });
  const far = got.filter((r) => !r.overlap);
  assert.equal(far.length, PLATE_TRIES + 1, "足元＋下へ tries 段まで");
  assert.ok(got.slice(PLATE_TRIES + 1).every((r) => r.overlap && r.top === 0));
});

test("placePlates: 壊れた札は null（並びの位置は保つ）・入力を書き換えない・同じ入力なら同じ答え", () => {
  const input = [{ cx: 10, top: 10, w: 20, h: 10 }, null, { cx: NaN, top: 1, w: 1, h: 1 }, { cx: 1, top: 1, w: 0, h: 5 },
    { cx: 50, top: 10, w: 20, h: 10 }];
  const copy = JSON.stringify(input);
  const a = placePlates(input, B);
  const b = placePlates(input, B);
  assert.equal(JSON.stringify(input), copy, "入力を書き換えた");
  assert.deepEqual(a, b);
  assert.deepEqual(a.map((r) => r === null), [false, true, true, true, false]);
  assert.ok(Object.isFrozen(a[0]));
  assert.deepEqual(placePlates(null, B), []);
  assert.deepEqual(placePlates([{ cx: 1, top: 1, w: 1, h: 1 }], null).length, 1, "枠が無くても札は返す（左上へ寄せる）");
});

test("placePlates: 頭上の印（avoid）に掛かる札は札の丈まで上へずらす・ずらせなければ under（薄くする）", () => {
  // 机の段（×4）: 上の段の人の足元 (61+16)… の札が、下の段の人の頭上の 💬（y 63..70 → 252..283）に掛かる
  const plate = { cx: 108, top: 245, head: 180, w: 120, h: 18 };
  const mark = { left: 92, top: 252, w: 36, h: 28 };
  const [r] = placePlates([plate], B, { avoid: [mark] });
  assert.equal(r.under, false);
  assert.equal(r.top, 252 - PLATE_GAP.y - 18, "印の上端の手前で止める");
  assert.ok(!overlaps(r, mark));
  // 印が札の上側に掛かる（ずらす幅が札の丈を超える）＝ずらさずに under
  const [u] = placePlates([{ cx: 108, top: 245, w: 120, h: 18 }], B, { avoid: [{ left: 92, top: 230, w: 36, h: 40 }] });
  assert.equal(u.under, true);
  assert.equal(u.top, 245);
  // ずらすとほかの札に当たる＝ずらさずに under
  const two = placePlates([{ cx: 108, top: 220, w: 120, h: 18 }, plate], B, { avoid: [mark] });
  assert.equal(two[1].under, true);
  assert.ok(!overlaps(two[0], two[1]));
  // 印に掛からない札は素のまま（under: false）
  assert.deepEqual(placePlates([plate], B).map((x) => [x.top, x.under]), [[245, false]]);
});

test("placePlates: 当たった札の横へずらす（持ち主の体の幅に掛かる間だけ）・掛からなければ下へ", () => {
  // 受付の 2 人（×3・17 論理 px おき＝51px）。札 100px は中心のままだと重なる → 右へずらして同じ段に並べる
  const q = [63, 114].map((cx) => ({ cx, top: 445, head: 372, w: 100, h: 18, reach: 18 }));
  const got = placePlates(q, B);
  assert.deepEqual(got.map((r) => [r.top, r.side]), [[445, "foot"], [445, "foot"]]);
  assert.equal(got[1].left, got[0].left + 100 + PLATE_GAP.x);
  assert.ok(got[1].left <= 114 + 18, "ずらした札が持ち主の体に掛かっている");
  assert.ok(!overlaps(got[0], got[1]));
  // reach が無ければ横へはずらさない（従来どおり段を変える＝部屋の下端に近いので頭の上へ回る）
  const old = placePlates(q.map(({ reach, ...rest }) => rest), B);
  assert.equal(old[1].side, "up");
  // 持ち主の体から離れすぎる横ずらしはしない（reach が小さい）
  const far = placePlates([{ cx: 100, top: 100, w: 100, h: 18 }, { cx: 110, top: 100, w: 100, h: 18, reach: 6 }], B);
  assert.equal(far[1].side, "down");
});
