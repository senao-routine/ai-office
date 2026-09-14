// R94: 間取り変更後に採取した独立の座標ピン。意図した移設時だけ更新する。
import assert from "node:assert/strict";
import { test } from "node:test";
import { buildLayout } from "./layout.js";
import { DEFAULT_SPEC, LAYOUT_SPECS } from "./layout_specs.js";
import * as nav from "./nav.js";

// R94: captured after the intentional room/lounge relocation. Desk coordinates stay unchanged.
const EXPECTED_M = {"LAYOUT":{"floor":{"w":28.8,"d":19,"z":-0.6},"deskZone":{"x":-0.6,"z":0.35,"w":15.8,"d":11.4,"lift":0.12},"meetZone":{"x":-9.2,"z":-7.7,"w":7.2,"d":4.4,"lift":0.22},"stageZone":{"x":-12.55,"z":0.5,"w":2.9,"d":3.4,"lift":0.12},"meet2Zone":{"x":10.85,"z":0.35,"w":5.2,"d":3.2,"lift":0.22},"loungeZone":{"x":10.75,"z":5.875,"w":6,"d":4.15,"lift":0.12},"meet3Zone":{"x":-12.8,"z":-3.35,"w":2.5,"d":2.4,"lift":0.22},"meet4Zone":{"x":10.7,"z":-5.3,"w":6.6,"d":4.5,"lift":0.22},"serverZone":{"x":10.2,"z":-9.25},"queueZone":{"x":-2.2,"z":6}},"WALL":{"left":-14.4,"right":14.4,"back":-10.1,"front":8.9},"PODS":[[-6.05,-2.35],[-0.6,-2.35],[4.85,-2.35],[-6.05,3.05],[-0.6,3.05],[4.85,3.05]],"REST_SPOTS":[{"area":"lounge","x":12.65,"z":5.255,"yaw":-1.5707963267948966,"y":0.12,"role":"tablet"},{"area":"lounge","x":10.2,"z":4.795,"yaw":0,"y":0.12},{"area":"lounge","x":10.2,"z":6.955,"yaw":3.141592653589793,"y":0.12},{"area":"sofa","x":-12.75,"z":-0.44999999999999996,"yaw":0,"y":0.12},{"area":"sofa","x":-12.15,"z":1.65,"yaw":3.141592653589793,"y":0.12},{"area":"bench","x":-13.5,"z":3.95,"yaw":1.5707963267948966,"y":0},{"area":"bench","x":-13.5,"z":4.9,"yaw":1.5707963267948966,"y":0},{"area":"bench","x":-13.5,"z":5.85,"yaw":1.5707963267948966,"y":0}],"IDLE_SPOTS":[{"x":3.8,"z":-7.3,"yaw":3.141592653589793,"why":"coffee"},{"x":-12.4,"z":2.6,"yaw":-1.5707963267948966,"why":"plant"},{"x":-13.3,"z":7.15,"yaw":1.5707963267948966,"why":"window"},{"x":7.1,"z":5.875,"yaw":1.5707963267948966,"why":"lounge"}],"CLEANER_ROUTE":[[-8.8,-5],[-3.3,-5],[2.1,-5],[3.9,-5],[6.95,-5],[6.95,0.35],[2.1,0.35],[2.1,5.3],[5.9,5.3],[2.1,5.3],[-3.3,5.3],[-8.8,5.3],[-8.8,7.6],[-8.8,0.35],[-8.8,-5]],"BOSS_WALK":[[-0.6,-6.6499999999999995],[-0.6,-5],[-3.3,-5],[2.1,-5],[-0.6,-5],[-0.6,-6.6499999999999995]]};

const EXPECTED_ANCHORS = {"desk":[{"x":-6.05,"z":-1.03,"yaw":3.141592653589793,"y":0.12},{"x":-6.05,"z":-3.67,"yaw":0,"y":0.12},{"x":-0.6,"z":-1.03,"yaw":3.141592653589793,"y":0.12},{"x":-0.6,"z":-3.67,"yaw":0,"y":0.12},{"x":4.85,"z":-1.03,"yaw":3.141592653589793,"y":0.12},{"x":4.85,"z":-3.67,"yaw":0,"y":0.12},{"x":-6.05,"z":4.37,"yaw":3.141592653589793,"y":0.12},{"x":-6.05,"z":1.7299999999999998,"yaw":0,"y":0.12},{"x":-0.6,"z":4.37,"yaw":3.141592653589793,"y":0.12},{"x":-0.6,"z":1.7299999999999998,"yaw":0,"y":0.12},{"x":4.85,"z":4.37,"yaw":3.141592653589793,"y":0.12},{"x":4.85,"z":1.7299999999999998,"yaw":0,"y":0.12}],"meeting":{"byRoom":{"meet":[{"x":-10.6,"z":-9,"yaw":0,"y":0.22},{"x":-9.2,"z":-9,"yaw":0,"y":0.22},{"x":-7.799999999999999,"z":-9,"yaw":0,"y":0.22},{"x":-10.6,"z":-6.4,"yaw":3.141592653589793,"y":0.22},{"x":-9.2,"z":-6.4,"yaw":3.141592653589793,"y":0.22},{"x":-7.799999999999999,"z":-6.4,"yaw":3.141592653589793,"y":0.22},{"x":-11.92,"z":-7.7,"yaw":1.5707963267948966,"y":0.22},{"x":-6.479999999999999,"z":-7.7,"yaw":-1.5707963267948966,"y":0.22}],"meet2":[{"x":10.1,"z":1.38,"yaw":3.141592653589793,"y":0.22},{"x":11.6,"z":1.38,"yaw":3.141592653589793,"y":0.22},{"x":10.1,"z":-0.68,"yaw":0,"y":0.22},{"x":11.6,"z":-0.68,"yaw":0,"y":0.22}],"meet3":[{"x":-13.15,"z":-2.5300000000000002,"yaw":3.141592653589793,"y":0.22},{"x":-12.450000000000001,"z":-4.17,"yaw":0,"y":0.22}],"meet4":[{"x":9.35,"z":-6.6,"yaw":0,"y":0.22},{"x":10.7,"z":-6.6,"yaw":0,"y":0.22},{"x":12.049999999999999,"z":-6.6,"yaw":0,"y":0.22},{"x":9.35,"z":-4,"yaw":3.141592653589793,"y":0.22},{"x":10.7,"z":-4,"yaw":3.141592653589793,"y":0.22},{"x":12.049999999999999,"z":-4,"yaw":3.141592653589793,"y":0.22},{"x":8.049999999999999,"z":-5.3,"yaw":1.5707963267948966,"y":0.22},{"x":13.35,"z":-5.3,"yaw":-1.5707963267948966,"y":0.22}]}},"lounge":[{"x":12.65,"z":5.255,"yaw":-1.5707963267948966,"y":0.12,"role":"tablet"},{"x":10.2,"z":4.795,"yaw":0,"y":0.12},{"x":10.2,"z":6.955,"yaw":3.141592653589793,"y":0.12}],"queue":[{"x":-2.2,"z":6,"yaw":0,"y":0},{"x":-1.1,"z":6,"yaw":0,"y":0},{"x":0,"z":6,"yaw":0,"y":0},{"x":1.1,"z":6,"yaw":0,"y":0},{"x":2.2,"z":6,"yaw":0,"y":0},{"x":3.3,"z":6,"yaw":0,"y":0},{"x":-2.2,"z":4.9,"yaw":0,"y":0},{"x":-1.1,"z":4.9,"yaw":0,"y":0},{"x":0,"z":4.9,"yaw":0,"y":0},{"x":1.1,"z":4.9,"yaw":0,"y":0},{"x":2.2,"z":4.9,"yaw":0,"y":0},{"x":3.3,"z":4.9,"yaw":0,"y":0}],"external":[{"x":-9.100000000000001,"z":2.55,"yaw":-1.5707963267948966,"y":0},{"x":-9.100000000000001,"z":3.5,"yaw":-1.5707963267948966,"y":0},{"x":-9.100000000000001,"z":4.449999999999999,"yaw":-1.5707963267948966,"y":0},{"x":-9.100000000000001,"z":5.3999999999999995,"yaw":-1.5707963267948966,"y":0},{"x":-9.100000000000001,"z":6.35,"yaw":-1.5707963267948966,"y":0}],"chibi":{"meet":[{"x":-11.799999999999999,"z":-9,"yaw":0,"y":0.22},{"x":-6.6,"z":-9,"yaw":0,"y":0.22},{"x":-11.799999999999999,"z":-6.4,"yaw":3.141592653589793,"y":0.22},{"x":-6.6,"z":-6.4,"yaw":3.141592653589793,"y":0.22}],"meet2":[{"x":9.1,"z":-0.30000000000000004,"yaw":1.5707963267948966,"y":0.22},{"x":12.6,"z":-0.30000000000000004,"yaw":-1.5707963267948966,"y":0.22},{"x":9.1,"z":1,"yaw":1.5707963267948966,"y":0.22},{"x":12.6,"z":1,"yaw":-1.5707963267948966,"y":0.22}],"meet3":[{"x":-13.82,"z":-3.7,"yaw":1.5707963267948966,"y":0.22},{"x":-11.780000000000001,"z":-3.7,"yaw":-1.5707963267948966,"y":0.22},{"x":-13.82,"z":-3,"yaw":1.5707963267948966,"y":0.22},{"x":-11.780000000000001,"z":-3,"yaw":-1.5707963267948966,"y":0.22}],"meet4":[{"x":8.149999999999999,"z":-6.6,"yaw":0,"y":0.22},{"x":13.25,"z":-6.6,"yaw":0,"y":0.22},{"x":8.149999999999999,"z":-4,"yaw":3.141592653589793,"y":0.22},{"x":13.25,"z":-4,"yaw":3.141592653589793,"y":0.22}]}};

const layouts = Object.fromEntries(Object.entries(LAYOUT_SPECS).map(([id, spec]) => [id, buildLayout(spec)]));

const pinnedKeys = {
  LAYOUT: "LAYOUT", WALL: "WALL", PODS: "PODS", REST_SPOTS: "restSpots",
  IDLE_SPOTS: "idleSpots", CLEANER_ROUTE: "cleanerRoute", BOSS_WALK: "bossWalk",
};
for (const [key, builtKey] of Object.entries(pinnedKeys)) {
  test(`M: ${key} はR94の間取りと一致`, () => {
    assert.equal(JSON.stringify(layouts.M[builtKey]), JSON.stringify(EXPECTED_M[key]));
    assert.equal(JSON.stringify(nav[key]), JSON.stringify(EXPECTED_M[key]));
  });
}

test("M: 全種類のアンカーがR94の座標・姿勢と一致（机は従来どおり）", () => {
  assert.deepEqual(layouts.M.anchors, EXPECTED_ANCHORS);
});

test("互換窓口: 障害物とグラフを変更しても次の呼び出しに漏れない", () => {
  const rects = nav.obstacleRects();
  const graph = nav.walkGraph();
  assert.deepEqual(rects, layouts.M.obstacleRects);
  assert.deepEqual(graph, layouts.M.walkGraph);
  rects[0].x1 = 100;
  graph.nodes[0][0] = 100;
  graph.edges[0][0] = 100;
  assert.deepEqual(nav.obstacleRects(), layouts.M.obstacleRects);
  assert.deepEqual(nav.walkGraph(), layouts.M.walkGraph);
});

for (const [id, spec] of Object.entries(LAYOUT_SPECS)) {
  const layout = layouts[id];
  const allAnchors = [
    ...layout.anchors.desk, ...Object.values(layout.anchors.meeting.byRoom).flat(),
    ...layout.anchors.lounge, ...layout.anchors.queue, ...layout.anchors.external,
    ...Object.values(layout.anchors.chibi).flat(),
  ];
  test(`${id}: spec を変更せず、繰り返し構築しても完全に同じ`, () => {
    const before = JSON.stringify(spec);
    assert.deepEqual(buildLayout(spec), layout);
    assert.equal(JSON.stringify(spec), before);
    const other = buildLayout(spec);
    other.LAYOUT.deskZone.x = 100;
    other.PODS[0][0] = 100;
    other.anchors.desk[0].x = 100;
    other.walkGraph.nodes[0][0] = 100;
    assert.deepEqual(buildLayout(spec), layout);
  });
  test(`${id}: 島・部屋・列の容量と順序は spec から導出`, () => {
    assert.equal(layout.PODS.length, spec.desks.rows.length * spec.desks.columns.length);
    assert.equal(layout.anchors.desk.length, layout.PODS.length * 2);
    assert.deepEqual(Object.keys(layout.anchors.meeting.byRoom), spec.rooms.map((room) => room.id));
    assert.deepEqual(Object.keys(layout.anchors.chibi), spec.rooms.map((room) => room.id));
    assert.equal(layout.anchors.queue.length, spec.queue.rows * spec.queue.columns);
    assert.equal(layout.anchors.external.length, spec.external.count);
    assert.deepEqual(layout.zones, Object.entries(layout.LAYOUT)
      .filter(([key]) => key !== "floor").map(([key, zone]) => ({ id: key, ...zone })));
  });
  test(`${id}: 全アンカーは有限で床の内側`, () => {
    const w = layout.WALL;
    for (const a of allAnchors) {
      assert.ok([a.x, a.z, a.y, a.yaw].every(Number.isFinite), JSON.stringify(a));
      assert.ok(a.x > w.left && a.x < w.right && a.z > w.back && a.z < w.front, JSON.stringify(a));
    }
  });
  test(`${id}: 全アンカーが机の天板を避け、待機列・外部席は全障害物の外`, () => {
    for (const r of layout.obstacleRects) {
      const points = r.id.startsWith("pod:") ? allAnchors : [...layout.anchors.queue, ...layout.anchors.external];
      for (const a of points) {
        assert.ok(!(a.x > r.x1 && a.x < r.x2 && a.z > r.z1 && a.z < r.z2),
          `${JSON.stringify(a)} overlaps ${r.id}`);
      }
    }
  });
  test(`${id}: 会議席・チビ席は自室の内側で別の障害物を避ける`, () => {
    for (const room of spec.rooms) {
      const own = layout.obstacleRects.find((r) => r.id === room.id);
      for (const a of [...layout.anchors.meeting.byRoom[room.id], ...layout.anchors.chibi[room.id]]) {
        assert.ok(a.x >= own.x1 && a.x <= own.x2 && a.z >= own.z1 && a.z <= own.z2);
        for (const r of layout.obstacleRects.filter((r) => r.id !== room.id)) {
          assert.ok(!(a.x > r.x1 && a.x < r.x2 && a.z > r.z1 && a.z < r.z2), room.id);
        }
      }
    }
  });
}

test("S/M/L/XL: 島は2×2 / 3×2 / 3×3 / 4×3、会議室は2 / 4 / 4 / 4", () => {
  assert.equal(DEFAULT_SPEC, LAYOUT_SPECS.M);
  assert.deepEqual(Object.values(layouts).map((l) => l.PODS.length), [4, 6, 9, 12]);
  assert.deepEqual(Object.values(layouts).map((l) => Object.keys(l.anchors.meeting.byRoom).length), [2, 4, 4, 4]);
});

test("XL: 24席・等間隔4.5m・東壁と会議室/受付はLの位置を維持", () => {
  assert.equal(layouts.XL.anchors.desk.length, 24);
  const columns = LAYOUT_SPECS.XL.desks.columns;
  for (let i = 1; i < columns.length; i++) assert.ok(Math.abs(columns[i] - columns[i - 1] - 4.5) < 1e-9);
  assert.equal(layouts.XL.WALL.right, layouts.L.WALL.right);
  assert.ok(Math.abs(layouts.L.WALL.left - layouts.XL.WALL.left - 2.2) < 1e-9);
  for (const name of ["meeting", "queue"]) assert.deepEqual(layouts.XL.anchors[name], layouts.L.anchors[name]);
});

test("カスタム spec: 机の移動と床幅の変更が席・矩形・壁際の席に伝わる", () => {
  const spec = JSON.parse(JSON.stringify(DEFAULT_SPEC));
  spec.desks.columns[0] += 0.25;
  spec.layout.floor.w += 2;
  const changed = buildLayout(spec);
  assert.equal(changed.PODS[0][0], layouts.M.PODS[0][0] + 0.25);
  assert.equal(changed.anchors.desk[0].x, changed.PODS[0][0]);
  assert.equal(changed.obstacleRects[0].x1, layouts.M.obstacleRects[0].x1 + 0.25);
  assert.equal(changed.anchors.external[0].x, layouts.M.anchors.external[0].x - 1);
  assert.equal(changed.restSpots.find((s) => s.area === "bench").x,
    layouts.M.restSpots.find((s) => s.area === "bench").x - 1);
  assert.notDeepEqual(changed.walkGraph, layouts.M.walkGraph);
});

test("不正な床・グリッド・0.9m未満の通路設定は拒否する", () => {
  for (const navigation of [{ grid: 0 }, { grid: -0.5 }, { margin: 0.44 }, { grid: Infinity }, { margin: NaN }]) {
    assert.throws(() => buildLayout({ ...DEFAULT_SPEC, navigation }), RangeError);
  }
  assert.throws(() => buildLayout({ ...DEFAULT_SPEC,
    layout: { ...DEFAULT_SPEC.layout, floor: { w: 0, d: 19, z: 0 } },
  }), RangeError);
});
