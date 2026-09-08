import { growthChanges, projectGrowth } from "/ui/core/growth.js";
import { frozen } from "/ui/platform/clock.js";

const PARTS = new Set(["tasksDone", "commits", "asksAnswered", "fastAnswers", "activeMin", "hires", "turnsCompleted"]);

export function init({ T, getGrowth, getWorld, showToast }) {
  // No DOM, notices, or new label text in golden captures, even with growth injected.
  if (frozen) return { update() {}, label: () => null, paintSheet() {} };
  let levels = null;
  const label = (agent) => {
    const view = projectGrowth(getGrowth(), agent.id);
    if (!view) return null;
    const parts = view.parts?.map((part) => PARTS.has(part.key)
      ? T(`growth_${part.key}`, part.count, part.xp)
      : T("growth_other", part.key, part.count, part.xp));
    return { level: T("board_level", view.level),
      text: [T("growth_total", view.xp), parts?.join(" ／ ") || T("growth_unavailable"),
        T("growth_no_tokens")].join("\n") };
  };
  const paintSheet = (agent, body, name) => {
    const value = label(agent);
    let badge = name.querySelector(".growth-level");
    let details = body.querySelector(".growth-details");
    if (!value) { badge?.remove(); details?.remove(); return; }
    if (!badge) {
      badge = document.createElement("span"); badge.className = "growth-level";
      name.append(badge);
    }
    badge.textContent = ` · ${value.level}`;
    if (!details) {
      details = document.createElement("details"); details.className = "growth-details";
      const summary = document.createElement("summary");
      const text = document.createElement("p");
      details.append(summary, text); body.prepend(details);
    }
    details.firstElementChild.textContent = `${value.level} — ${T("growth_details")}`;
    details.lastElementChild.textContent = value.text;
  };
  return {
    label, paintSheet,
    update() {
      const result = growthChanges(levels, getGrowth());
      levels = result.levels;
      for (const change of result.changes) {
        const agent = getWorld()?.agents.find((a) => a.id === change.id);
        showToast(T("growth_level_up", agent?.name || T("growth_project"), change.level));
      }
    },
  };
}
