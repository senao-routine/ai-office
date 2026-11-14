// 押している間は描き直さない（ui/hud/press_hold.js）。ブラウザ無しで、押す → 描き直しはためる → 離す → 次のタスクで 1 回だけ描く。
import test from "node:test";
import assert from "node:assert/strict";
import { createPressHold, PRESS_HOLD_MAX_MS } from "../ui/hud/press_hold.js";

function fakeWindow() {
  const win = new EventTarget();
  const timers = new Map();
  let next = 1;
  win.setTimeout = (fn, ms) => { const id = next++; timers.set(id, { fn, ms }); return id; };
  win.clearTimeout = (id) => { timers.delete(id); };
  win.flush = (ms = 0) => { for (const [id, t] of [...timers]) if (t.ms <= ms) { timers.delete(id); t.fn(); } };
  win.timers = timers;
  return win;
}
const press = (target, button = 0) => target.dispatchEvent(Object.assign(new Event("pointerdown"), { button }));

test("押していなければ今すぐ描く（defer は false）", () => {
  const win = fakeWindow(), host = new EventTarget();
  const hold = createPressHold(host, { win });
  let n = 0;
  assert.equal(hold.defer(() => n++), false);
  assert.equal(n, 0);
  assert.equal(win.timers.size, 0);
});

test("押している間は最後の 1 回だけためて、離した後のタスクで描く（離した合図の中では描かない＝クリックが先）", () => {
  const win = fakeWindow(), host = new EventTarget();
  const hold = createPressHold(host, { win });
  const drawn = [];
  press(host);
  assert.equal(hold.pressed(), true);
  assert.equal(hold.defer(() => drawn.push("古い")), true);
  assert.equal(hold.defer(() => drawn.push("新しい")), true);
  assert.deepEqual(drawn, []);
  win.dispatchEvent(new Event("pointerup"));
  assert.equal(hold.pressed(), false);
  assert.deepEqual(drawn, [], "pointerup の中で描くとクリックの前に押したボタンが外れる");
  win.flush(0);
  assert.deepEqual(drawn, ["新しい"]);
  assert.equal(hold.defer(() => drawn.push("次")), false, "離した後は今すぐ描く");
});

test("pointercancel と窓の blur でも離したことにする・ためていなければ何も描かない", () => {
  for (const kind of ["pointercancel", "blur"]) {
    const win = fakeWindow(), host = new EventTarget();
    const hold = createPressHold(host, { win });
    let n = 0;
    press(host);
    hold.defer(() => n++);
    win.dispatchEvent(new Event(kind));
    win.flush(0);
    assert.equal(n, 1, kind);
    press(host);
    win.dispatchEvent(new Event(kind));
    win.flush(0);
    assert.equal(n, 1, `${kind}: ためていないのに描いた`);
  }
});

test("右・中ボタンは押下にしない・host の外の押下も数えない", () => {
  const win = fakeWindow(), host = new EventTarget();
  const hold = createPressHold(host, { win });
  press(host, 2);
  press(host, 1);
  press(win);
  assert.equal(hold.defer(() => {}), false);
});

test("離した合図が来なくても maxMs で離したことにする（描き直しが止まり続けない）", () => {
  const win = fakeWindow(), host = new EventTarget();
  const hold = createPressHold(host, { win });
  let n = 0;
  press(host);
  hold.defer(() => n++);
  assert.equal(PRESS_HOLD_MAX_MS, 10000);
  win.flush(PRESS_HOLD_MAX_MS);    // 安全網のタイマー → 離したことにして 0ms の描き直しを置く
  win.flush(0);
  assert.equal(n, 1);
  assert.equal(hold.pressed(), false);
});

test("dispose の後は押下を聞かず、ためた描き直しも捨てる", () => {
  const win = fakeWindow(), host = new EventTarget();
  const hold = createPressHold(host, { win });
  let n = 0;
  press(host);
  hold.defer(() => n++);
  hold.dispose();
  win.dispatchEvent(new Event("pointerup"));
  win.flush(PRESS_HOLD_MAX_MS);
  assert.equal(n, 0);
  press(host);
  assert.equal(hold.defer(() => n++), false);
});
