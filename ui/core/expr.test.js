import assert from "node:assert/strict";
import { test } from "node:test";
import { exprFor, FACE_KEYS } from "./expr.js";

test("atlas keys have the documented 4×2 order", () => {
  assert.deepEqual(FACE_KEYS, ["idle", "blink", "focus", "think", "question", "happy", "sleepy", "alert"]);
  assert.ok(Object.isFrozen(FACE_KEYS));
});

test("working normally focuses", () => {
  assert.equal(exprFor({ state: "working" }, 1, 0), "focus");
});

test("working briefly blinks, then returns to focus", () => {
  assert.equal(exprFor({ state: "working" }, 5.33, 0), "focus");
  assert.equal(exprFor({ state: "working" }, 5.4, 0), "blink");
  assert.equal(exprFor({ state: "working" }, 5.5, 0), "focus");
});

test("waiting alternates idle and blink", () => {
  assert.equal(exprFor({ state: "waiting" }, 1, 0), "idle");
  assert.equal(exprFor({ state: "waiting" }, 5.4, 0), "blink");
  assert.equal(exprFor({ state: "waiting" }, 5.5, 0), "idle");
});

test("attention overrides activity and blinking", () => {
  for (const state of ["working", "waiting", "resting", "thinking", "alert"]) {
    assert.equal(exprFor({ state, attention: true }, 5.4, 0), "question");
  }
  assert.equal(exprFor({ state: "attention" }, 1, 0), "question");
});

test("thinking state and the existing think kind use think", () => {
  assert.equal(exprFor({ state: "thinking" }, 1, 0), "think");
  assert.equal(exprFor({ state: "working", kind: "think" }, 5.4, 0), "think");
});

test("resting has both sleepy and happy windows", () => {
  assert.equal(exprFor({ state: "resting" }, 1, 0), "sleepy");
  assert.equal(exprFor({ state: "resting" }, 11, 0), "happy");
  assert.equal(exprFor({ state: "resting" }, 14, 0), "sleepy");
});

test("resting overrides a stale think kind", () => {
  assert.equal(exprFor({ state: "resting", kind: "think" }, 1, 0), "sleepy");
});

test("explicit alert maps to alert", () => {
  assert.equal(exprFor({ state: "alert" }, 1, 0), "alert");
  assert.equal(exprFor({ state: "working", kind: "alert" }, 1, 0), "alert");
});

test("missing, external and unknown states are idle", () => {
  for (const agent of [null, undefined, {}, { state: "external" }, { state: "unknown" }]) {
    assert.equal(exprFor(agent, 5.4, 0), "idle");
  }
});

test("seed distributes blink phases across actors", () => {
  const keys = [0, .25, .5, 1, 2, 3].map(seed => exprFor({ state: "waiting" }, 5.4, seed));
  assert.ok(keys.includes("blink"));
  assert.ok(keys.includes("idle"));
});

test("blink duty cycle stays brief over a minute", () => {
  for (const seed of [0, .25, 2.3, -1.7]) {
    let blinks = 0;
    for (let i = 0; i < 6000; i++) if (exprFor({ state: "working" }, i / 100, seed) === "blink") blinks++;
    assert.ok(blinks > 60 && blinks < 240, `blink samples: ${blinks}`);
  }
});

test("negative time wraps safely and repeating cycles match", () => {
  assert.equal(exprFor({ state: "working" }, -.1, 0), "blink");
  for (const t of [-20, -1, 0, 2, 5.4]) {
    assert.equal(exprFor({ state: "working" }, t, 0), exprFor({ state: "working" }, t + 55, 0));
  }
});

test("nonfinite time and seed have deterministic defaults", () => {
  for (const value of [NaN, Infinity, -Infinity]) {
    assert.equal(exprFor({ state: "working" }, value, value), "focus");
  }
});

test("seeking and state changes do not mutate inputs or retain history", () => {
  const agent = Object.freeze({ state: "working", kind: "tool", attention: false });
  const times = [0, 5.4, -1, 10, 50];
  const expected = times.map(t => exprFor(agent, t, 2.2));
  exprFor({ state: "attention" }, 1000, 7);
  const reversed = [...times].reverse().map(t => exprFor(agent, t, 2.2)).reverse();
  assert.deepEqual(reversed, expected);
  assert.deepEqual(agent, { state: "working", kind: "tool", attention: false });
  assert.ok(expected.every(key => FACE_KEYS.includes(key)));
});
