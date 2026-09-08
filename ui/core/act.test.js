import assert from "node:assert/strict";
import { test } from "node:test";
import { actFor } from "./act.js";
import { activityKind } from "./archetype.js";
import { activityGloss, buildWorld } from "./world.js";
import { FACE_KEYS, exprFor } from "./expr.js";
import {
  approvalPose, celebratePose, enterPose, leavePose, questionPose, readPose, runPose,
  mixPose, walkPose, celebrationFlash,
} from "./anim.js";

const cases = [
  ["Reading", "README.md", "research", "read", "book"],
  ["検索中", "資料", "research", "read", "book"],
  ["Editing", "docs/guide.md", "docs", "read", "book"],
  ["編集中", "scene.js", "code", "typing", null],
  ["Writing", "guide.md", "docs", "read", "book"],
  ["執筆中", "原稿", "write", "typing", null],
  ["Running", "node --test core.test.js", "test", "read", "tablet"],
  ["実行中", "npm install", "build", "read", "wrench"],
  ["実行中", "git push", "ship", "run", "tablet"],
  ["Running", "task", "run", "run", null],
  ["報告中", "結果", "report", "read", "tablet"],
  ["Thinking", "", "think", "think", null],
  ["指示待ち", "", "waiting", "idle", null],
];
test("HUD and acts share ja/en verb/target classification without parsing display text", () => {
  for (const [verb, target, kind, pose, prop] of cases) {
    const a = { verb, target, state: "working" };
    assert.equal(activityKind(a), kind);
    assert.deepEqual(actFor(a, 3.2, 11), { pose, prop, expr: exprFor(a, 3.2, 11) });
    assert.ok(activityGloss(a));
    // work.now remains the display override; actions still use factual tool activity.
    assert.equal(activityGloss({ ...a, work: { now: ["作業メモ"] } }), "📋 作業メモ");
    assert.deepEqual(actFor({ ...a, work: { now: ["作業メモ"] } }), actFor(a));
  }
});
test("attention beats all activities and removes the prop", () => {
  for (const [verb, target] of cases) {
    assert.deepEqual(actFor({ verb, target, attention: true }), { pose: "question", prop: null, expr: "question" });
  }
  assert.equal(actFor({ state: "attention" }).pose, "question");
});
test("rest holds one mug; empty/unknown data has no invented prop", () => {
  assert.equal(actFor({ state: "resting", verb: "Reading" }).prop, "mug");
  for (const a of [null, undefined, {}, { verb: "点検中" }]) {
    assert.equal(actFor(a).prop, null);
    assert.ok(FACE_KEYS.includes(actFor(a).expr));
  }
});
test("acts preserve V9 expression keys, blinking and deterministic immutable inputs", () => {
  for (const state of ["working", "waiting", "resting", "thinking", "alert", "external"]) {
    const agent = Object.freeze({ state, verb: "Reading", target: "book" });
    for (const t of [-10, 0, 3.2, 5.4, 50]) {
      const act = actFor(agent, t, 11);
      assert.deepEqual(act, actFor(agent, t, 11));
      assert.equal(act.expr, exprFor(agent, t, 11));
      assert.ok(FACE_KEYS.includes(act.expr));
    }
  }
});
test("buildWorld carries activity and server growth through to renderer selection", () => {
  const growth = { byProject: { p: { xp: 100, level: 1 } } };
  const world = buildWorld({ roster: [{ projectId: "p", state: "working", verb: "Reading", target: "guide.md" }], growth });
  assert.equal(actFor(world.agents[0]).prop, "book");
  assert.equal(world.growth, growth);
});

const poses = { readPose, runPose, questionPose, approvalPose, enterPose, leavePose, celebratePose };
const values = pose => [pose.hipY, pose.hipYaw, pose.hipRoll, pose.headYaw, pose.headPitch,
  ...pose.legs.flatMap(l => [l.side, l.hip, l.knee]), ...pose.arms.flatMap(a => [a.side, a.shoulder, a.elbow])];
for (const [name, fn] of Object.entries(poses)) {
  test(`${name}: finite pose contract, reversible seeking and continuous boundaries`, () => {
    for (const seed of [0, 1.7, 11]) {
      for (const t of [-1, 0, .001, .3, .7, .9, 1.2, 3.2, 80]) {
        const pose = fn(t, seed);
        assert.deepEqual(Object.keys(pose).sort(), Object.keys(walkPose(0)).sort());
        assert.deepEqual(pose.legs.map(l => l.side), [-1, 1]);
        assert.deepEqual(pose.arms.map(a => a.side), [-1, 1]);
        assert.ok(values(pose).every(Number.isFinite));
        assert.ok(pose.legs.every(l => l.knee >= 0));
        fn(t + 37, seed); // no retained history when seeking backwards
        assert.deepEqual(pose, fn(t, seed));
        assert.ok(values(mixPose(walkPose(0), pose, .5)).every(Number.isFinite));
        const next = values(fn(t + 1e-6, seed));
        assert.ok(values(pose).every((value, i) => Math.abs(value - next[i]) < .001));
      }
    }
  });
}
test("arrival is exactly a regular walk at both ends of its .9 second gesture", () => {
  for (const t of [0, .9, 2]) assert.deepEqual(enterPose(t, 2, 1.3), walkPose(1.3));
  assert.notDeepEqual(enterPose(.4, 2, 1.3), walkPose(1.3));
});
test("celebration reflection is bounded, continuous, seekable and limited to .65 seconds", () => {
  for (const t of [-1, 0, .65, 1, 100]) assert.equal(celebrationFlash(t), 0);
  assert.ok(celebrationFlash(.2) > 0);
  for (let t = -.1; t < .8; t += .002) {
    const value = celebrationFlash(t);
    assert.ok(value >= 0 && value <= .16);
    celebrationFlash(t + 1);
    assert.equal(celebrationFlash(t), value);
    assert.ok(Math.abs(celebrationFlash(t + 1e-6) - value) < 1e-5);
  }
});
