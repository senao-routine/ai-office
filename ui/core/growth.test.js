import test from "node:test";
import assert from "node:assert/strict";
import { growthChanges, growthView, projectGrowth } from "./growth.js";

// Same seven supplied metrics as D9; token volume is deliberately unrelated.
const entry = () => ({ xp: 287, level: 1, breakdown: {
  tasksDone: { count: 12, xp: 120 }, commits: { count: 8, xp: 40 },
  asksAnswered: { count: 20, xp: 40 }, fastAnswers: { count: 8, xp: 8 },
  activeMin: { count: 340, xp: 68 }, hires: { count: 1, xp: 5 }, turnsCompleted: { count: 2, xp: 6 },
} });

test("every supplied breakdown contributes to the exact displayed XP", () => {
  const value = growthView(entry());
  assert.equal(value.parts.reduce((sum, part) => sum + part.xp, 0), value.xp);
  assert.equal(value.parts.length, 7);
});

test("tokens never affect the displayed XP or the supplied breakdown", () => {
  const value = entry();
  assert.deepEqual(growthView({ ...value, tokens: 999999999 }), growthView(value));
  assert.equal(growthView(value).parts.some((p) => p.key === "tokens"), false);
});

test("mismatching or malformed breakdowns are not presented as true totals", () => {
  assert.equal(growthView({ ...entry(), xp: 288 }), null);
  assert.equal(growthView({ ...entry(), level: NaN }), null);
  assert.equal(growthView({ xp: 1, level: 0, breakdown: { activeMin: { count: -1, xp: 1 } } }), null);
});

test("fractional XP keeps server precision and tolerates only its nine decimal rounding", () => {
  const value = growthView({ xp: 0.3, level: 0,
    breakdown: { a: { count: 1, xp: 0.1 }, b: { count: 1, xp: 0.2 } } });
  assert.equal(value.xp, 0.3);
  assert.equal(growthView({ xp: 0.333333333, level: 0,
    breakdown: { activeMin: { count: 1.666666667, xp: 0.333333333 } } }).xp, 0.333333333);
});

test("missing relay breakdown stays unavailable; project IDs never mix growth", () => {
  assert.equal(growthView({ xp: 7, level: 0 }).parts, null);
  assert.equal(projectGrowth({ byProject: { p1: entry() } }, "p2"), null);
  assert.equal(projectGrowth({ byProject: { p1: entry() } }, "p1").xp, 287);
});

test("level notices occur once after baseline, including jumps, reappearance and lower snapshots", () => {
  const project = (level) => ({ byProject: { p1: { xp: level * 100, level } } });
  let result = growthChanges(null, project(1));
  assert.deepEqual(result.changes, []);
  const previous = result.levels;
  result = growthChanges(previous, project(3));
  assert.deepEqual(result.changes, [{ id: "p1", level: 3 }]);
  assert.equal(previous.get("p1"), 1);
  result = growthChanges(result.levels, project(3));
  assert.deepEqual(result.changes, []);
  result = growthChanges(result.levels, {});
  result = growthChanges(result.levels, project(1));
  result = growthChanges(result.levels, project(3));
  assert.deepEqual(result.changes, []);
});
