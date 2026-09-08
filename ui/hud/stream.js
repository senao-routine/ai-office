import { broadcastLead } from "/ui/core/broadcast.js";

export const streamOptions = (search) => {
  const q = new URLSearchParams(search);
  return { enabled: q.get("stream") === "1", privacy: q.get("privacy") === "1",
    large: q.get("plates") === "large", aspect: ["16:9", "9:16"].includes(q.get("aspect")) ? q.get("aspect") : null };
};

export const privateStatus = (a, T) => T(a.attention ? "board_attention"
  : a.state === "working" ? "today_working" : a.state === "waiting" ? "stream_waiting" : "stream_resting");
export const privateBadge = (a) => [...String(a.badge || "?")][0];

/** Only explicitly requested broadcast UI is mounted; the default DOM stays identical. */
export function initStream({ root, shell, options, T }) {
  if (!options.enabled) return { paint() {}, dispose() {} };
  root.classList.add("stream-mode");
  if (options.large) root.classList.add("stream-large");
  if (options.privacy) root.classList.add("stream-private");
  const prior = document.documentElement.classList.contains("iso-stream");
  document.documentElement.classList.add("iso-stream");
  if (options.aspect) {
    const [w, h] = options.aspect.split(":").map(Number);
    shell.style.width = `min(100vw, ${100 * w / h}dvh)`;
    shell.style.height = `min(100dvh, ${100 * h / w}vw)`;
  }
  const stage = shell.querySelector("#stage");
  const subtitle = document.createElement("div"); subtitle.id = "stream-subtitle";
  const badge = document.createElement("div"); badge.id = "stream-attention";
  stage.append(subtitle, badge);
  return {
    paint(world) {
      const lead = broadcastLead(world.agents);
      const identity = lead ? options.privacy ? privateBadge(lead) : lead.projectName || lead.name : "";
      const activity = lead ? options.privacy ? privateStatus(lead, T)
        : lead.activity || privateStatus(lead, T) : T("stream_empty");
      const text = T("stream_now", identity ? `${identity} › ${activity}` : activity);
      if (subtitle.textContent !== text) subtitle.textContent = text;
      const count = world.agents.filter((a) => a.attention).length;
      badge.textContent = `❗${count}`;
    },
    dispose() {
      if (!prior) document.documentElement.classList.remove("iso-stream");
      subtitle.remove(); badge.remove();
    },
  };
}

/** Keep only presentation parameters: a shared URL must never inherit credentials or fragments. */
export function broadcastURL(href, aspect) {
  const url = new URL(href), opts = streamOptions(url.search);
  url.username = ""; url.password = ""; url.search = ""; url.hash = "";
  url.searchParams.set("stream", "1");
  if (aspect) url.searchParams.set("aspect", aspect);
  if (opts.large) url.searchParams.set("plates", "large");
  if (opts.privacy) url.searchParams.set("privacy", "1");
  return url.href;
}

export function streamSettings({ modal, mEl, T, showToast, replay }) {
  modal.append(mEl("b", "msubtitle", T("stream_copy")));
  const row = mEl("div", "mledform");
  for (const [key, aspect] of [["stream_obs", "16:9"], ["stream_short", "9:16"], ["stream_monitor", null]]) {
    const button = mEl("button", "mkeybtn", T(key)); button.type = "button";
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(broadcastURL(location.href, aspect));
        showToast(T("stream_copied"));
      } catch { showToast(T("stream_copy_failed"), false); }
    });
    row.append(button);
  }
  modal.append(row);
  if (replay?.canRecord()) {
    const save = mEl("button", "mkeybtn", T("replay_save")); save.type = "button";
    save.addEventListener("click", () => { void replay.saveToday(); });
    modal.append(save);
  }
}
