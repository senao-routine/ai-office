// Broadcast decisions use supplied facts/time only; rendering and browser I/O stay outside core.
export const SHOT_SECONDS = 12;
const age = (a) => Number.isFinite(a.age) ? a.age : Infinity;
const order = (a, b) => String(a.id) < String(b.id) ? -1 : String(a.id) > String(b.id) ? 1 : 0;

/** Prefer active employees, then the most recently updated activity. Stable ties avoid flicker. */
export function broadcastLead(agents) {
  return [...agents].sort((a, b) => Number(b.state === "working") - Number(a.state === "working")
    || Number(Boolean(b.activity)) - Number(Boolean(a.activity)) || age(a) - age(b) || order(a, b))[0] || null;
}

/** Consume notable changes in priority order, at twelve-second shot boundaries. */
export function createDirector() {
  let previous = null, pending = [], shot = null, started = 0, sequence = 0;
  return (agents, t, { frozen = false, bossWalking = false } = {}) => {
    if (frozen && shot) return shot; // A fixed render never advances or consumes another shot.
    if (t < started) { previous = null; pending = []; shot = null; }
    const current = new Map(agents.map((a) => [a.id, a]));
    for (const a of [...agents].sort(order)) {
      const old = previous?.get(a.id);
      const alert = a.attention && (!old?.attention || a.question !== old.question || a.session !== old.session);
      const kind = alert ? "attention" : old && a.state !== old.state ? "state"
        : previous && !old ? "hire" : null;
      if (!kind) continue;
      pending = pending.filter((e) => e.id !== a.id);
      pending.push({ id: a.id, kind, priority: { attention: 0, state: 1, hire: 2 }[kind],
        at: t, age: age(a) });
    }
    previous = new Map(agents.map((a) => [a.id, { ...a }]));
    pending = pending.filter((e) => current.has(e.id) && t - e.at <= 60
      && (e.kind !== "attention" || current.get(e.id).attention));
    if (!shot || t - started >= SHOT_SECONDS || (shot.id && !current.has(shot.id))) {
      pending.sort((a, b) => a.priority - b.priority || b.at - a.at || a.age - b.age || order(a, b));
      const next = pending.shift() || { kind: bossWalking && shot?.kind !== "boss" ? "boss" : "overview", id: null };
      started = t;
      shot = { kind: next.kind, id: next.id, started, sequence: sequence++ };
    }
    return shot;
  };
}
