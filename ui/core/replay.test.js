import test from "node:test";
import assert from "node:assert/strict";
import { buildWorld } from "./world.js";
import { createReplay, digestSummary, replayFrame, REPLAY_SECONDS } from "./replay.js";

const world = () => buildWorld({ roster: [{ projectId: "p1", session: "s1", name: "Studio",
  state: "working", attention: true, question: "Future question", pending: true,
  work: { done: ["Future task"] }, feed: ["Future feed"], approvalMin: 34 }] });
const event = (ev, ts, extra = {}) => ({ ev, ts, sid: "s1", projectId: "p1", ...extra });
const replay = (events, base = world()) => createReplay(base, events, { since: 100, until: 300 });
const agentAt = (r, seconds) => replayFrame(r, seconds).world.agents.find((a) => a.session === "s1");

test("replay compresses the supplied time range into exactly 20 seconds", () => {
  const r = replay([event("UserPromptSubmit", 200)]);
  assert.equal(r.duration, REPLAY_SECONDS);
  assert.equal(replayFrame(r, 7).at, 170);
  assert.equal(agentAt(r, 9).state, "waiting");
  assert.equal(agentAt(r, 10).state, "working");
  assert.equal(replayFrame(r, 20).done, true);
});

test("permission and input notifications move the robot into the attention queue", () => {
  const r = replay([event("PermissionRequest", 150), event("PostToolUse", 200),
    event("Notification", 250, { nt: "agent_needs_input" })]);
  assert.equal(agentAt(r, 5).zone, "queue");
  assert.equal(agentAt(r, 10).attention, false);
  assert.equal(agentAt(r, 15).zone, "queue");
  assert.equal(replayFrame(r, 15).world.counts.attention, 1);
});

test("AskUserQuestion is attention and a subsequent tool result resumes work", () => {
  const r = replay([event("PreToolUse", 150, { tool: "AskUserQuestion" }), event("PostToolUse", 200)]);
  assert.equal(agentAt(r, 5).attention, true);
  assert.equal(agentAt(r, 10).zone, "desk");
});

test("Stop and Codex turnCompleted wait; SessionEnd rests and clears attention", () => {
  for (const ev of ["Stop", "turnCompleted", "SessionEnd"]) {
    const r = replay([event("PermissionRequest", 140), event(ev, 160)]);
    const a = agentAt(r, 20);
    assert.equal(a.attention, false);
    assert.equal(a.state, ev === "SessionEnd" ? "resting" : "waiting");
  }
});

test("subagents change meeting occupancy without negative counts", () => {
  const r = replay([event("SubagentStart", 120), event("SubagentStart", 130),
    event("SubagentStop", 140), event("SubagentStop", 150), event("SubagentStop", 160)]);
  assert.equal(agentAt(r, 3).minions, 2);
  assert.equal(agentAt(r, 3).zone, "meeting");
  assert.equal(agentAt(r, 20).minions, 0);
  assert.equal(agentAt(r, 20).zone, "desk");
});

test("historical hires appear only at their event, with stable identities and seats", () => {
  const r = replay([event("hire", 200, { sid: "old", name: "Archive" }),
    event("UserPromptSubmit", 210, { sid: "old" })]);
  assert.equal(replayFrame(r, 9).world.agents.length, 1);
  const shown = replayFrame(r, 20).world;
  const a = shown.agents.find((item) => item.session === "old");
  assert.equal(a.name, "Archive");
  assert.equal(a.state, "working");
  assert.equal(typeof shown.seats.get(a.id), "number");
});

test("a current robot with a recorded hire is also absent before that hire", () => {
  const r = replay([event("hire", 200)]);
  assert.equal(replayFrame(r, 5).world.agents.length, 0);
  assert.equal(agentAt(r, 10).id, "p1");
});

test("sort by timestamp/id, ignore duplicate IDs and invalid or out-of-range events", () => {
  const r = replay([event("Stop", 200, { id: 2 }), event("UserPromptSubmit", 200, { id: 1 }),
    event("PermissionRequest", 200, { id: 2 }), event("hire", 99), event("hire", 301),
    event("Unknown", 210), event("Stop", NaN), null]);
  assert.equal(r.eventCount, 2);
  assert.equal(agentAt(r, 20).state, "waiting");
});

test("pause and backwards seeks are deterministic and do not change the source world/events", () => {
  const base = world();
  const rows = [event("UserPromptSubmit", 130), event("PermissionRequest", 210)];
  const before = structuredClone({ base, rows });
  Object.freeze(rows); rows.forEach(Object.freeze); base.agents.forEach(Object.freeze);
  const r = replay(rows, base);
  const first = replayFrame(r, 7);
  replayFrame(r, 19);
  assert.deepEqual(replayFrame(r, 7), first);
  assert.deepEqual({ base, rows }, before);
  assert.equal(replayFrame(r, 7).world.agents[0].question, "");
  assert.equal(replayFrame(r, 7).world.agents[0].pending, false);
  assert.deepEqual(replayFrame(r, 7).world.agents[0].feed, []);
});

test("empty, zero-length ranges and extreme seeks stay finite", () => {
  const r = createReplay(null, null, { since: 10, until: 5 });
  assert.equal(replayFrame(r, -50).at, 10);
  assert.equal(replayFrame(r, NaN).elapsed, 0);
  assert.equal(replayFrame(r, 100).elapsed, 20);
  assert.equal(r.eventCount, 0);
});

test("external robots retain their external zone during replay", () => {
  const base = buildWorld({ roster: [{ session: "s1", projectId: "p1", external: "openclaw" }] });
  assert.equal(agentAt(replay([event("PermissionRequest", 150)], base), 10).zone, "external");
});

test("nonrepresentative session events address that session, not another project", () => {
  const base = buildWorld({ roster: [{ session: "s1", projectId: "p1", crew: 2,
    sessions: [{ session: "s1" }, { session: "s2" }] }] });
  const r = replay([event("PermissionRequest", 150, { sid: "s2" })], base);
  const shown = replayFrame(r, 20).world.agents;
  assert.equal(shown.find((a) => a.session === "s2").attention, true);
  assert.equal(shown.find((a) => a.session === "s1").attention, false);
  assert.equal(shown.find((a) => a.session === "s1").crew, 1);
  assert.equal(new Set(shown.map((a) => a.id)).size, shown.length);
});

test("digest opens at the 20 minute boundary, with no working/attention double count", () => {
  const base = world();
  const digest = { available: true, since: 1000, totals: { tasksDone: 3 } };
  assert.equal(digestSummary(base, digest, 2199).autoOpen, false);
  const summary = digestSummary(base, digest, 2200);
  assert.equal(summary.autoOpen, true);
  assert.deepEqual([summary.stopped, summary.longest, summary.completed, summary.working], [1, 34, 3, 0]);
});

test("empty days, unknown first visits and unavailable data never auto-open", () => {
  const base = buildWorld(null);
  const digest = { available: true, since: 1000, totals: { tasksDone: 0 } };
  assert.equal(digestSummary(base, digest, 5000).autoOpen, false);
  assert.equal(digestSummary(world(), { ...digest, since: 0 }, 5000).autoOpen, false);
  assert.equal(digestSummary(world(), { ...digest, available: false }, 5000), null);
  assert.equal(digestSummary(world(), { ...digest, totals: {} }, 5000), null);
  assert.equal(digestSummary(world(), digest, 10).away, 0);
});
