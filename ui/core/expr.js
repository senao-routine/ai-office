// Screen expressions are pure: callers supply elapsed seconds and the actor seed.
export const FACE_KEYS = Object.freeze([
  "idle", "blink", "focus", "think", "question", "happy", "sleepy", "alert",
]);

const mod = (value, period) => ((value % period) + period) % period;

/** Agent state → atlas key. No history: seeking backwards yields the same face. */
export function exprFor(agent, t = 0, seed = 0) {
  const time = Number.isFinite(t) ? t : 0;
  const phaseSeed = Number.isFinite(seed) ? seed : 0;
  const state = agent?.state;
  if (agent?.attention || state === "attention") return "question";
  if (state === "alert" || agent?.kind === "alert") return "alert";
  if (state === "resting") {
    return mod(time + phaseSeed * 2.3, 14) < 10 ? "sleepy" : "happy";
  }
  if (state === "thinking" || agent?.kind === "think") return "think";
  if (state === "working" || state === "waiting") {
    // A 160ms blink every 5.5–7 seconds; both period and phase vary by actor.
    const period = 5.5 + mod(phaseSeed, 1) * 1.5;
    if (mod(time + phaseSeed * .91, period) >= period - .16) return "blink";
    return state === "working" ? "focus" : "idle";
  }
  return "idle";
}
