// R99-P4-L2: 散歩（ui/core/life.js）の単体テスト。決定論・事実への結び付け・席を変えない・散歩先を重ねない。
import test from "node:test";
import assert from "node:assert/strict";
import { LIFE, canStroll, lifeTargets, strollWindow } from "./life.js";
import { ERRAND_SPOTS, FURNITURE, LIFE_SPOTS, ROBOT_FOOT, SLOTS, footRects, roomLayout } from "./pxroom.js";
import { DOOR_XY, planPath } from "./pxwalk.js";
import { ERRAND_SPOT_OF, EVIDENCE_ERRAND } from "./errand.js";

const waiter = (i, over = {}) => ({ id: `p${i}`, session: `s${i}`, state: "waiting", kind: "said", age: 120,
  zone: "desk", vendor: "claude", ...over });

test("life: 散歩してよいのは机で返事を 1 分以上待っている人だけ（呼んでいる・休憩・会議・受付・退場中・age 不明は出ない）", () => {
  assert.equal(LIFE.minAge, 60);
  const base = { zone: "desk", state: "waiting", age: 60 };
  assert.equal(canStroll(base), true);
  for (const over of [{ age: 59 }, { age: null }, { age: NaN }, { attention: true }, { state: "working" },
    { kind: "tool" }, { kind: "think" }, { stuckTool: "Bash" },
    { state: "resting" }, { zone: "queue" }, { zone: "lounge" }, { zone: "meeting" }, { zone: "standby" },
    { zone: "external" }, { leaving: true }]) {
    assert.equal(canStroll({ ...base, ...over }), false, JSON.stringify(over));
  }
  assert.equal(canStroll(null), false);
});

test("life: 窓は key と t だけで決まる（決定論）・start より前は閉じている・1 周に span 秒だけ開く", () => {
  for (const key of ["s0", "s1", "abc", "sess-cp-13"]) {
    let open = 0;
    for (let k = 0; k < LIFE.period * 12; k += 1) {
      const t = LIFE.start + LIFE.period + k / 12;
      const w = strollWindow(key, t);
      assert.deepEqual(w, strollWindow(key, t), "同じ key と t で違う窓");
      if (w.open) open += 1;
    }
    assert.equal(open, LIFE.span * 12, `${key}: 1 周で開いている長さが span 秒でない`);
    assert.equal(strollWindow(key, LIFE.start - 0.01).open, false);
    assert.equal(strollWindow(key, NaN).open, false);
  }
});

test("life: live でなければ誰も動かさない（固定時刻・停止・再生＝golden 不変）・入力を書き換えない", () => {
  const w = { agents: [0, 1, 2, 3, 4, 5].map((i) => waiter(i)) };
  const { actors } = roomLayout(w, [], null);
  const frozen = JSON.stringify(actors);
  for (let t = 0; t < 600; t += 7) {
    const r = lifeTargets(actors, t, { live: false });
    assert.equal(r.actors, actors, "live でないのに写しを作った");
    assert.equal(r.away, 0);
    lifeTargets(actors, t, { live: true });
  }
  assert.equal(JSON.stringify(actors), frozen, "入力を書き換えた");
});

test("life: 散歩中は目的地だけ散歩先へ（席・区分・key は同じ）・同じ散歩先に 2 人を立たせない・全員が 1 周のうちに 1 回は出る", () => {
  const w = { agents: [0, 1, 2, 3, 4, 5, 6, 7, 8].map((i) => waiter(i)) };
  const { actors } = roomLayout(w, [], null);
  const spotKey = new Set(LIFE_SPOTS.map((s) => `${s.x},${s.y}`));
  const went = new Set();
  for (let k = 0; k < LIFE.period * 6; k += 1) {
    const t = LIFE.start + k / 6;
    const r = lifeTargets(actors, t, { live: true });
    const at = new Map();
    r.actors.forEach((a, i) => {
      const src = actors[i];
      assert.equal(a.key, src.key);
      assert.equal(a.seat, src.seat);
      assert.equal(a.zone, src.zone);
      if (a.away) {
        assert.ok(spotKey.has(`${a.x},${a.y}`), `${a.key} の行き先 ${a.x},${a.y} が散歩先でない`);
        assert.ok(!at.has(`${a.x},${a.y}`), `t=${t}: ${a.key} と ${at.get(`${a.x},${a.y}`)} が同じ散歩先`);
        at.set(`${a.x},${a.y}`, a.key);
        went.add(a.key);
      } else {
        assert.deepEqual([a.x, a.y], [src.x, src.y], "散歩していない人の目的地が変わった");
      }
    });
    assert.ok(r.away <= LIFE_SPOTS.length);
  }
  assert.equal(went.size, actors.length, `1 周のうちに散歩しなかった人が居る（${actors.length - went.size} 人）`);
});

test("life: 散歩先は枠・家具の足元・散歩先同士と重ならない（外部社員の立ち位置とも）", () => {
  const R = (x, y) => ({ x1: x, y1: y, x2: x + 12, y2: y + 16 });
  const ov = (a, b) => a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1;
  LIFE_SPOTS.forEach((s, i) => {
    for (const [zone, list] of Object.entries(SLOTS)) {
      list.forEach((t, j) => assert.ok(!ov(R(s.x, s.y), R(t.x, t.y)), `散歩先 ${i} が ${zone}#${j} と重なる`));
    }
    LIFE_SPOTS.forEach((t, j) => { if (j !== i) assert.ok(!ov(R(s.x, s.y), R(t.x, t.y)), `散歩先 ${i} と ${j} が重なる`); });
  });
});


test("life: 割り当ては前のフレームから持ち越す＝窓の途中で別の人の資格が変わっても、散歩中の人の行き先は変わらない", () => {
  // 同じ枠に入る 4 人を探す（窓が同時に開く）
  const keys = [];
  for (let i = 0; keys.length < 4 && i < 400; i += 1) if (strollWindow(`s${i}`, LIFE.start + 1000).slot === strollWindow("s0", LIFE.start + 1000).slot) keys.push(i);
  assert.equal(keys.length, 4);
  const mk = (ages) => roomLayout({ agents: keys.map((i, n) => waiter(i, { age: ages[n] })) }, [], null).actors;
  const t0 = (() => { for (let t = LIFE.start; ; t += 1) if (strollWindow(`s${keys[0]}`, t).open) return t; })();
  // 最初は 3 人が資格あり・1 人（最後）は age 30
  let prev = null;
  const where = (r) => Object.fromEntries(r.actors.filter((a) => a.away).map((a) => [a.key, `${a.x},${a.y}`]));
  let r = lifeTargets(mk([120, 120, 120, 30]), t0, { live: true, prev });
  const first = where(r);
  prev = r.assign;
  assert.equal(Object.keys(first).length, 3);
  // 窓の途中で 4 人目が資格を得る・1 人目が仕事に戻る → 残りの 2 人の行き先は変わらない
  const later = keys.map((i, n) => waiter(i, { age: [120, 120, 120, 120][n], ...(n === 0 ? { state: "working" } : {}) }));
  r = lifeTargets(roomLayout({ agents: later }, [], null).actors, t0 + 5, { live: true, prev });
  const second = where(r);
  for (const k of Object.keys(first)) if (k !== `s${keys[0]}`) assert.equal(second[k], first[k], `${k} の行き先が他の人の変化で変わった`);
  assert.equal(second[`s${keys[0]}`], undefined, "仕事に戻った人が散歩先のまま");
  assert.ok(second[`s${keys[3]}`], "空いた散歩先を新しく資格を得た人が取らない");
});

test("life: 散歩先は扉から全部の枠への通り道に掛からない（入退場の人が散歩中の人に重ならない）", () => {
  const R = (x, y) => ({ x1: x, y1: y, x2: x + 12, y2: y + 16 });
  const ov = (a, b) => a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1;
  for (const [zone, list] of Object.entries(SLOTS)) {
    list.forEach((s, j) => {
      const p = planPath(DOOR_XY, [s.x, s.y]);
      assert.ok(p, `扉 → ${zone}#${j} に経路が無い`);
      for (let i = 1; i < p.length; i += 1) {
        const [x0, y0] = p[i - 1], [x1, y1] = p[i];
        const n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0));
        for (let k = 0; k <= n; k += 1) {
          const me = R(Math.round(x0 + (x1 - x0) * k / n), Math.round(y0 + (y1 - y0) * k / n));
          LIFE_SPOTS.forEach((sp, q) => assert.ok(!ov(me, R(sp.x, sp.y)), `扉 → ${zone}#${j} の通り道が散歩先 ${sp.name}（${q}）に掛かる`));
        }
      }
    });
  }
});

// ── R110-S5: 用事の行き先（ERRAND_SPOTS）も散歩先と同じ契約 ─────────────────────────────
const R = (x, y) => ({ x1: x, y1: y, x2: x + 12, y2: y + 16 });
const ov = (a, b) => a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1;

test("errand spots: 名前は用事の行き先の種類ちょうど（本棚・掲示板・ボード・カウンター）・座標は整数", () => {
  const want = new Set([...Object.values(ERRAND_SPOT_OF), EVIDENCE_ERRAND.spot]);
  assert.deepEqual(ERRAND_SPOTS.map((s) => s.name).sort(), [...want].sort(), "行き先の種類と ERRAND_SPOTS の名前が食い違う");
  for (const s of ERRAND_SPOTS) assert.ok(Number.isInteger(s.x) && Number.isInteger(s.y), `${s.name} の座標が整数でない`);
  assert.ok(Object.isFrozen(ERRAND_SPOTS) && ERRAND_SPOTS.every((s) => Object.isFrozen(s)));
});

test("errand spots: 枠・散歩先・ほかの行き先と重ならない・足元は家具の足元と重ならない", () => {
  const [fx, fy, fw, fh] = ROBOT_FOOT;
  ERRAND_SPOTS.forEach((s, i) => {
    for (const [zone, list] of Object.entries(SLOTS)) {
      list.forEach((t, j) => assert.ok(!ov(R(s.x, s.y), R(t.x, t.y)), `行き先 ${s.name} が ${zone}#${j} と重なる`));
    }
    LIFE_SPOTS.forEach((t) => assert.ok(!ov(R(s.x, s.y), R(t.x, t.y)), `行き先 ${s.name} が散歩先 ${t.name} と重なる`));
    ERRAND_SPOTS.forEach((t, j) => { if (j !== i) assert.ok(!ov(R(s.x, s.y), R(t.x, t.y)), `行き先 ${s.name} と ${t.name} が重なる`); });
    const foot = { x1: s.x + fx, y1: s.y + fy, x2: s.x + fx + fw, y2: s.y + fy + fh };
    for (const f of FURNITURE) {
      for (const [x, y, w, h] of footRects(f)) {
        assert.ok(!ov(foot, { x1: x, y1: y, x2: x + w, y2: y + h }), `行き先 ${s.name} の足元が ${f.id} の足元に重なる（めり込む）`);
      }
    }
  });
});

test("errand spots: 扉から全部の枠への通り道に掛からない（入退場の人が用事の人に重ならない）", () => {
  for (const [zone, list] of Object.entries(SLOTS)) {
    list.forEach((s, j) => {
      const p = planPath(DOOR_XY, [s.x, s.y]);
      assert.ok(p, `扉 → ${zone}#${j} に経路が無い`);
      for (let i = 1; i < p.length; i += 1) {
        const [x0, y0] = p[i - 1], [x1, y1] = p[i];
        const n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0));
        for (let k = 0; k <= n; k += 1) {
          const me = R(Math.round(x0 + (x1 - x0) * k / n), Math.round(y0 + (y1 - y0) * k / n));
          ERRAND_SPOTS.forEach((sp) => assert.ok(!ov(me, R(sp.x, sp.y)), `扉 → ${zone}#${j} の通り道が行き先 ${sp.name} に掛かる`));
        }
      }
    });
  }
});

test("errand spots: 全部の机から歩いて届き、同じ机へ戻れる（扉からも）", () => {
  for (const s of ERRAND_SPOTS) {
    SLOTS.desk.forEach((d, i) => {
      assert.ok(planPath([d.x, d.y], [s.x, s.y]), `机 ${i} → ${s.name} が届かない`);
      assert.ok(planPath([s.x, s.y], [d.x, d.y]), `${s.name} → 机 ${i} が届かない`);
    });
    assert.ok(planPath(DOOR_XY, [s.x, s.y]), `扉 → ${s.name} が届かない`);
  }
});

test("life（R110-S5）: max＝散歩に出してよい人数の上限（用事の残り）・前から散歩していた人が先・0 なら誰も出ない", () => {
  const keys = [];
  for (let i = 0; keys.length < 3 && i < 400; i += 1) if (strollWindow(`s${i}`, LIFE.start + 1000).slot === strollWindow("s0", LIFE.start + 1000).slot) keys.push(i);
  const t0 = (() => { for (let t = LIFE.start; ; t += 1) if (strollWindow(`s${keys[0]}`, t).open) return t; })();
  const actors = roomLayout({ agents: keys.map((i) => waiter(i)) }, [], null).actors;
  const full = lifeTargets(actors, t0, { live: true });
  assert.equal(full.away, 3, "前提: 同じ窓の 3 人が散歩に出る");
  assert.equal(lifeTargets(actors, t0, { live: true, max: 0 }).away, 0);
  const one = lifeTargets(actors, t0 + 1, { live: true, max: 1, prev: full.assign });
  assert.equal(one.away, 1);
  // 前のフレームから出ていた人のうちキーの昇順で先の人が残る（窓の途中の人を入れ替えない）
  const stayed = [...one.assign.keys()];
  assert.deepEqual(stayed, [[...full.assign.keys()].sort()[0]]);
  assert.equal(lifeTargets(actors, t0, { live: true, max: 2.9 }).away, 2, "端数は切り捨て");
  assert.equal(lifeTargets(actors, t0, { live: true, max: NaN }).away, 3, "数でない max は上限なし（散歩先の数まで）");
});
