// Replay is a projection of supplied facts. No live world, clock or I/O is mutated.
import { assignOverflow, assignSeats, buildWorld, countByZone, zoneOf } from "./world.js";

export const REPLAY_SECONDS = 20;
export const AWAY_SECONDS = 20 * 60;
const nonnegative = (n) => Number.isFinite(n) && n >= 0;
const clearAttention = { attention: false, approvalMin: 0, question: "", questionOptions: [], ask: null };
const EVENTS = new Set(["hire", "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
  "PostToolUse", "PostToolUseFailure", "PermissionRequest", "PermissionDenied", "Notification",
  "Stop", "StopFailure", "turnCompleted", "TaskCreated", "TaskCompleted", "SubagentStart", "SubagentStop"]);

/** Exactly the card's three facts: current robots and observed task completions. */
export function digestSummary(world, digest, at) {
  if (!world || digest?.available !== true || !nonnegative(at)
      || !nonnegative(digest.since) || !nonnegative(digest.totals?.tasksDone)) return null;
  const agents = world.agents || [];
  const stopped = agents.filter((a) => a.attention);
  const summary = {
    since: digest.since, until: at,
    away: digest.since > 0 ? Math.max(0, at - digest.since) : 0,
    stopped: stopped.length,
    longest: Math.floor(Math.max(0, ...stopped.map((a) => a.approvalMin || 0))),
    completed: digest.totals.tasksDone,
    working: agents.filter((a) => a.state === "working" && !a.attention).length,
  };
  summary.hasActivity = summary.stopped + summary.completed + summary.working > 0;
  summary.autoOpen = summary.since > 0 && summary.away >= AWAY_SECONDS && summary.hasActivity;
  return summary;
}

function eventAgent(agent, event) {
  const a = { ...agent, age: 0 };
  const working = () => Object.assign(a, clearAttention, { state: "working", kind: "tool" });
  switch (event.ev) {
    case "hire": case "SessionStart":
      Object.assign(a, clearAttention, { state: "waiting", kind: "idle" }); break;
    case "SessionEnd":
      Object.assign(a, clearAttention, { state: "resting", kind: "idle", minions: 0 }); break;
    case "Stop": case "turnCompleted":
      Object.assign(a, clearAttention, { state: "waiting", kind: "idle", minions: 0 }); break;
    case "PermissionRequest":
      Object.assign(a, { state: "waiting", attention: true, kind: "idle" }); break;
    case "Notification":
      if (["permission_prompt", "idle_prompt", "agent_needs_input"].includes(event.nt)) {
        Object.assign(a, { state: "waiting", attention: true, kind: "idle" });
      }
      break;
    case "PreToolUse":
      working();
      if (event.tool === "AskUserQuestion") Object.assign(a, { attention: true, state: "waiting" });
      break;
    case "SubagentStart": working(); a.minions += 1; break;
    case "SubagentStop": a.minions = Math.max(0, a.minions - 1); break;
    case "UserPromptSubmit": working(); a.kind = "think"; break;
    case "PostToolUse": case "PostToolUseFailure": case "PermissionDenied":
    case "StopFailure": case "TaskCreated": case "TaskCompleted": working(); break;
  }
  a.zone = zoneOf(a);
  return a;
}

function snapshot(base, agents, ts) {
  const list = [...agents.values()];
  const seats = assignSeats(list);
  return { ...base, agents: list, seats, overflow: assignOverflow(list, seats),
    counts: countByZone(list), generatedAt: ts };
}

/** Compile at most the API's event batch into seekable world keyframes once.
 * Current avatars supply identity/appearance only for affected sessions: future
 * questions, feed, work and pending instructions are never replayed backwards.
 * Unaffected avatars remain context. Missing historical sessions get their own ID.
 */
export function createReplay(world, events, { since = 0, until = since } = {}) {
  const start = nonnegative(since) ? since : 0;
  const end = Math.max(start, nonnegative(until) ? until : start);
  const seen = new Set();
  const rows = (Array.isArray(events) ? events : []).filter((e) => {
    if (!e || !nonnegative(e.ts) || e.ts < start || e.ts > end || !e.sid || !EVENTS.has(e.ev)) return false;
    const key = e.id != null ? `id:${e.id}` : e.seq != null ? `seq:${e.seq}` : null;
    if (key && seen.has(key)) return false;
    if (key) seen.add(key);
    return true;
  }).slice().sort((a, b) => a.ts - b.ts || (a.id ?? a.seq ?? 0) - (b.id ?? b.seq ?? 0));
  const base = world || buildWorld(null);
  const agents = new Map(base.agents.map((a) => [a.id, a]));
  const templates = new Map();
  for (const e of rows) {
    if (templates.has(e.sid)) continue;
    const current = base.agents.find((a) => a.session === e.sid
      || a.sessions?.some((member) => member.session === e.sid));
    const template = current || buildWorld({ roster: [{ session: e.sid,
      projectId: `replay:${e.sid}`, name: e.name || e.sid, vendor: e.vendor }] }).agents[0];
    const a = { ...template, ...clearAttention,
      id: current?.session === e.sid ? current.id : `replay:${e.sid}`,
      session: e.sid, crew: 1, sessions: [], state: "waiting", kind: "idle",
      activity: "", pending: false, feed: [], work: null, stuckTool: "", minions: 0,
    };
    a.zone = zoneOf(a);
    templates.set(e.sid, a);
    if (current && current.session !== e.sid) {
      const group = agents.get(current.id);
      if (group?.crew > 1) agents.set(current.id, { ...group, crew: group.crew - 1,
        sessions: group.sessions.filter((member) => member.session !== e.sid) });
    }
    // A hire after the start must not appear before it happened.
    if (e.ev !== "hire" && e.ev !== "SessionStart") agents.set(a.id, a);
    else agents.delete(a.id);
  }
  const frames = [{ ts: start, world: snapshot(base, agents, start) }];
  for (const e of rows) {
    const template = templates.get(e.sid);
    const a = eventAgent(agents.get(template.id) || template, e);
    agents.set(a.id, a);
    frames.push({ ts: e.ts, world: snapshot(base, agents, e.ts) });
  }
  return { since: start, until: end, frames, eventCount: rows.length, duration: REPLAY_SECONDS };
}

/** Elapsed seconds are supplied by the caller, so pause and backward seeks agree. */
export function replayFrame(replay, elapsed) {
  const seconds = Math.min(REPLAY_SECONDS, Math.max(0, Number.isFinite(elapsed) ? elapsed : 0));
  const at = replay.since + (replay.until - replay.since) * seconds / REPLAY_SECONDS;
  let low = 0, high = replay.frames.length;
  while (low + 1 < high) {
    const mid = (low + high) >>> 1;
    if (replay.frames[mid].ts <= at) low = mid;
    else high = mid;
  }
  return { world: replay.frames[low].world, at, elapsed: seconds, done: seconds === REPLAY_SECONDS };
}
