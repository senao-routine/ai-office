import assert from "node:assert/strict";
import { test } from "node:test";
import { decorationsFor, maxSeenFor, specFor, tierFor } from "./tier.js";
import { LAYOUT_SPECS } from "./layout_specs.js";
import { buildLayout } from "./layout.js";
import { assignSeats, assignOverflow, buildWorld } from "./world.js";

test("tierFor: every threshold, including 8→9 and 18→19; levels do not affect capacity", () => {
  for (const [maxSeen, tier] of [[0, "S"], [8, "S"], [9, "M"], [12, "M"], [13, "L"], [18, "L"], [19, "XL"], [22, "XL"]]) {
    for (const officeLevel of [undefined, 0, 3, 8, 20, 100]) {
      assert.equal(tierFor({ agents: 0, maxSeen, officeLevel }), tier);
    }
  }
});

test("tierFor: current population grows a stale peak; 20→10 and empty snapshots never shrink", () => {
  let maxSeen = 0;
  for (const [agents, tier] of [[8, "S"], [9, "M"], [8, "M"], [18, "L"], [19, "XL"], [20, "XL"], [10, "XL"], [0, "XL"]]) {
    const input = Object.freeze({ agents: Object.freeze(Array(agents).fill(null)), maxSeen });
    assert.equal(tierFor(input), tier);
    maxSeen = maxSeenFor(input);
  }
  assert.equal(maxSeen, 20);
});

test("tierFor: invalid persisted counts cannot shrink or inflate a valid population", () => {
  for (const maxSeen of [-1, NaN, Infinity, "100", 9.5, null]) {
    assert.equal(maxSeenFor({ agents: 9, maxSeen }), 9);
    assert.equal(tierFor({ agents: 9, maxSeen }), "M");
  }
});

test("decorationsFor: each unlock is absent below its level and present from that level", () => {
  for (const [key, level] of [["plants", 3], ["coffee", 5], ["meet3", 8], ["lounge", 12], ["meet4", 15], ["cafe", 20]]) {
    assert.equal(decorationsFor(level - 1)[key], false);
    assert.equal(decorationsFor(level)[key], true);
    assert.equal(decorationsFor(level + 1)[key], true);
  }
  for (const bad of [undefined, null, -1, NaN, Infinity, "20"]) {
    assert.ok(Object.values(decorationsFor(bad)).every((unlocked) => !unlocked));
  }
});

for (const tier of Object.keys(LAYOUT_SPECS)) test(`${tier}: locked furniture, rooms, anchors and obstacles are absent`, () => {
  const before = JSON.stringify(LAYOUT_SPECS[tier]);
  for (const level of [0, 3, 5, 8, 12, 15, 20]) {
    const spec = specFor(tier, level), decor = decorationsFor(level), model = buildLayout(spec);
    for (const id of ["meet3", "meet4"]) {
      assert.equal(id in model.anchors.meeting.byRoom, decor[id]);
      assert.equal(model.obstacleRects.some((r) => r.id === id), decor[id]);
      assert.equal(`${id}Zone` in model.LAYOUT, decor[id]);
    }
    assert.equal(model.furnishings.some((f) => f.id === "coffee"), decor.coffee);
    assert.equal(model.idleSpots.some((s) => s.why === "coffee"), decor.coffee);
    assert.equal(model.furnishings.some((f) => f.id === "touchdown"), decor.cafe);
    assert.equal(model.obstacleRects.some((r) => r.id === "touchdown"), decor.cafe);
    assert.equal(model.anchors.lounge.length, decor.lounge ? 3 : 2);
    assert.equal(model.LAYOUT.loungeZone.w, decor.lounge ? 4.8 : 3.6);
    assert.deepEqual(specFor(tier, level), spec);
  }
  assert.equal(JSON.stringify(LAYOUT_SPECS[tier]), before);
  assert.equal(specFor(tier, undefined), LAYOUT_SPECS[tier], "legacy snapshots retain their exact spec");
});

test("22 concurrent workers get distinct XL desks with no overflow, even when all share a project", () => {
  for (const shared of [true, false]) {
    const world = buildWorld({ roster: Array.from({ length: 22 }, (_, i) => ({
      session: `worker-${i}`, state: "working", cwd: shared ? "/project/shared" : `/project/${i}`,
    })) });
    const tier = tierFor({ agents: world.agents, maxSeen: 22 });
    const model = buildLayout(specFor(tier, 0));
    const seats = assignSeats(world.agents, model.anchors.desk.length);
    assert.equal(seats.size, 22);
    assert.equal(new Set(seats.values()).size, 22);
    assert.equal(assignOverflow(world.agents, seats).size, 0);
    for (const slot of seats.values()) assert.ok(model.anchors.desk[slot]);
  }
});
