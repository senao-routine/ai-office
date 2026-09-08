import { frozen } from "/ui/platform/clock.js";

const SEEN = "aioffice.onboarding.questions.v1";

export function init({ shell, T, scene, enabled }) {
  if (frozen || !enabled) return { paint() {}, dispose() {} };
  let read = false;
  try { read = localStorage.getItem(SEEN) === "1"; } catch { /* Once per mount without storage. */ }
  let bubble = null;
  let started = null;
  const dismiss = () => { bubble?.remove(); bubble = null; read = true; };
  return {
    paint(t) {
      if (read && !bubble) return;
      const point = scene.projectBoss?.();
      if (!point || !Number.isFinite(point.left) || !Number.isFinite(point.top)) return;
      const covered = !shell.querySelector("#modalwrap").hidden || !shell.querySelector("#sheet").hidden
        || shell.classList.contains("replay-active") || shell.querySelector("#digest-card")?.hidden === false;
      if (covered) { if (bubble) bubble.hidden = true; return; }
      if (started !== null && t - started >= 15) { dismiss(); return; }
      if (!bubble) {
        bubble = document.createElement("button"); bubble.type = "button";
        bubble.id = "boss-onboarding"; bubble.className = "boss-onboarding";
        bubble.addEventListener("click", dismiss);
        shell.querySelector("#stage").append(bubble);
        started = t; read = true;
        try { localStorage.setItem(SEEN, "1"); } catch { /* The hint still works. */ }
      }
      bubble.hidden = false;
      bubble.textContent = T("onboarding_questions");
      bubble.setAttribute("aria-label", T("onboarding_dismiss"));
      const stage = shell.querySelector("#stage");
      const half = bubble.offsetWidth / 2;
      bubble.style.left = `${Math.max(half + 8, Math.min(stage.clientWidth - half - 8, point.left))}px`;
      bubble.style.top = `${Math.max(8, Math.min(stage.clientHeight - bubble.offsetHeight - 8, point.top - 80))}px`;
    },
    dispose: dismiss,
  };
}
