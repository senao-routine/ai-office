// Action selection shares the HUD's vocabulary; callers supply time and seed.
import { activityKind } from "./archetype.js";
import { exprFor } from "./expr.js";

const ACTIONS = Object.freeze({
  resting: ["relax", "mug"], waiting: ["idle", null],
  think: ["think", null], research: ["read", "book"],
  docs: ["read", "book"], write: ["typing", null], code: ["typing", null],
  report: ["read", "tablet"], test: ["read", "tablet"],
  build: ["read", "wrench"], ship: ["run", "tablet"], run: ["run", null],
});

/** agent → one pose key, at most one prop, and an existing V9 expression key. */
export function actFor(agent, t = 0, seed = 0) {
  const expr = exprFor(agent, t, seed);
  if (expr === "question") return { pose: "question", prop: null, expr };
  const [pose, prop] = ACTIONS[activityKind(agent)]
    || (expr === "think" ? ACTIONS.think : ["typing", null]);
  return { pose, prop, expr };
}
