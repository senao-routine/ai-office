// 配達の意味と見た目を全 HUD で揃える。文言は呼び手の言語に追随する。
import { deliveryState, DELIVERY_STATES, isMuted } from "/ui/core/world.js";

export function paintDeliveryChip(node, { T, agent, offline = false, stalled = false,
  state, aliases = false, hint = false, quietLive = false }) {
  const value = state === "sending" || DELIVERY_STATES.includes(state)
    ? state : deliveryState(agent, { offline, stalled });
  // 右レールの行では「届く」は既定＝黙る（旧 📨/📴 と同じく、届かない・投函済みのときだけ目立たせる）。
  node.hidden = quietLive && value === "live";
  for (const cls of [...node.classList]) {
    if (cls.startsWith("dstate-")) node.classList.remove(cls);
  }
  node.classList.add("dstate", `dstate-${value}`);
  node.title = value === "sending" ? T("dstate_sending") : T(`dstate_${value}_hint`);
  node.replaceChildren();
  const append = (cls, text, hidden = false) => {
    const child = document.createElement("span");
    child.className = cls;
    child.textContent = text;
    child.hidden = hidden;
    node.append(child);
    return child;
  };
  if (aliases) {
    // ops smoke と既存拡張は hidden を読むため、非該当の印も DOM には残す。
    append("amute", "📴", !isMuted(agent));
    append("apend", "📨", !agent?.pending || isMuted(agent));
  } else if (value === "saved" || value === "pending") {
    append("dstate-icon", value === "saved" ? "📴" : "📨");
  }
  append("dstate-label", T(`dstate_${value}`));
  if (hint && value !== "sending") append("dstate-hint", T(`dstate_${value}_hint`));
  return node;
}

export function deliveryChip(options) {
  return paintDeliveryChip(document.createElement("span"), options);
}
