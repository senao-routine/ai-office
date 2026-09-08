import test from "node:test";
import assert from "node:assert/strict";
import { broadcastLead, createDirector } from "./broadcast.js";

const employee = (id, extra = {}) => ({ id, state: "working", activity: "verify", age: 0, ...extra });
test("broadcast lead prefers current work, recency and stable identity", () => {
  const agents = [employee("old", { age: 50 }), employee("idle", { state: "waiting" }), employee("recent", { age: 2 })];
  assert.equal(broadcastLead(agents).id, "recent");
  assert.equal(broadcastLead(agents.reverse()).id, "recent");
  assert.equal(broadcastLead([]), null);
});
test("director queues new attention before state changes, hires, patrol and overview", () => {
  const direct = createDirector();
  const first = [employee("a"), employee("b")];
  assert.equal(direct(first, 0).kind, "overview");
  const next = [employee("a", { state: "waiting" }), employee("b", { attention: true }), employee("c")];
  assert.equal(direct(next, 1).kind, "overview");
  assert.equal(direct(next, 12).id, "b");
  assert.equal(direct(next, 24).id, "a");
  assert.equal(direct(next, 36).kind, "hire");
  assert.equal(direct(next, 48, { bossWalking: true }).kind, "boss");
  assert.equal(direct(next, 60, { bossWalking: true }).kind, "overview");
});
test("resolved alerts are removed; changed questions qualify as new alerts", () => {
  const direct = createDirector();
  direct([employee("a")], 0);
  direct([employee("a", { attention: true, question: "one" })], 1);
  assert.equal(direct([employee("a")], 12).kind, "overview");
  assert.equal(direct([employee("a", { attention: true, question: "two" })], 24).kind, "attention");
});
test("frozen picks the same first shot at any fixed time and keeps it across redraws", () => {
  const agents = [employee("a", { attention: true, age: 50 }), employee("b", { attention: true, age: 1 })];
  for (const t of [3.2, 29, 200]) {
    const direct = createDirector();
    const first = direct(agents, t, { frozen: true });
    assert.equal(first.id, "b");
    assert.equal(direct([employee("c")], t + 100, { frozen: true }), first);
  }
});
