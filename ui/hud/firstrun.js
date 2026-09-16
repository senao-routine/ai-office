import { frozen, now } from "/ui/platform/clock.js";

const SEEN = "aioffice.onboarding.questions.v1";

/** One first-use state owns setup, the empty office, and the first question hint. */
export function init({ shell, T, scene, DEMO = false, enabled = true }) {
  if (DEMO || shell._stream?.enabled) return { update() {}, paint() {}, dispose() {} };
  let read = false;
  if (!frozen) {
    try { read = localStorage.getItem(SEEN) === "1"; } catch { /* The hint still works without storage. */ }
  }
  let state = "ready", world = null, disposed = false;
  let setupBar = null, card = null, stageHint = null, bubble = null;
  let started = null, copiedUntil = 0;
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    if (tag === "button") node.type = "button";
    return node;
  };
  const dismiss = () => {
    bubble?.remove(); bubble = null;
    read = true;
    if (state === "first_alert") state = "ready";
  };
  const paintSetup = (show) => {
    if (!show) { setupBar?.remove(); setupBar = null; return; }
    if (!setupBar) {
      setupBar = el("div", "setupbar"); setupBar.id = "setupbar";
      const message = el("span", "sb-msg");
      const command = el("code", "sb-cmd");
      const copy = el("button", "sb-copy");
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(T("setup_hook_cmd"));
          if (disposed || !setupBar) return;
          copiedUntil = now() + 1.8;
          copy.textContent = T("setup_hook_copied");
        } catch { /* The visible command remains available to copy manually. */ }
      });
      setupBar.append(message, command, copy);
      if (!frozen) {
        const checklist = el("div", "setup-checklist");
        checklist.append(el("b", "setup-title"), el("span", "setup-hooks"),
          el("span", "setup-events"), el("span", "setup-approval"));
        setupBar.prepend(checklist);
      }
      shell.querySelector(".main").insertBefore(setupBar, shell.querySelector("#stage"));
    }
    const hook = world.setup?.hookInstalled === true;
    const events = world.setup?.eventsWired === true;
    // R97-A: 許可プロンプトに答える hook（PermissionRequest）。未配線なら README に書いてある機能が黙って効かない
    const approval = world.setup?.approvalWired === true;
    setupBar.querySelector(".sb-msg").textContent = T("setup_hook");
    setupBar.querySelector(".sb-cmd").textContent = T("setup_hook_cmd");
    setupBar.querySelector(".sb-copy").textContent = T(copiedUntil > now() ? "setup_hook_copied" : "setup_hook_copy");
    if (!frozen) {
      setupBar.querySelector(".setup-title").textContent = T("setup_checklist");
      setupBar.querySelector(".setup-hooks").textContent = T(hook ? "setup_hooks_ready" : "setup_hooks_pending");
      setupBar.querySelector(".setup-events").textContent = T(events ? "setup_events_ready"
        : world.setup?.eventsWired === false ? "setup_events_pending" : "setup_events_unknown");
      setupBar.querySelector(".setup-approval").textContent = T(approval ? "setup_approval_ready"
        : world.setup?.approvalWired === false ? "setup_approval_pending" : "setup_approval_unknown");
      setupBar.querySelector(".sb-msg").hidden = hook;
      setupBar.querySelector(".sb-cmd").hidden = hook && events && approval;
      setupBar.querySelector(".sb-copy").hidden = hook && events && approval;
    }
  };
  const paintEmpty = (show) => {
    if (!show) {
      card?.remove(); card = null;
      stageHint?.remove(); stageHint = null;
      return;
    }
    if (!card) {
      card = el("div", "onboard");
      const demo = el("a", "odemo"); demo.href = "?demo=1";
      card.append(el("b", "ob-title"), el("p", "ob-start"), el("p", "ob-project"), demo);
    }
    card.querySelector(".ob-title").textContent = T("ob_title");
    card.querySelector(".ob-start").textContent = T("ob_p1");
    card.querySelector(".ob-project").textContent = T("ob_p2");
    card.querySelector(".odemo").textContent = T("ob_demo");
    shell.querySelector("#agents").append(card);
    const viewport = shell.querySelector("#viewport");
    if (viewport && !shell.querySelector("#no3d")) {
      if (!stageHint) {
        stageHint = el("div", "stagehint"); stageHint.id = "stagehint";
        viewport.append(stageHint);
      }
      stageHint.textContent = T("ob_title");
    }
  };
  return {
    update(next) {
      if (disposed) return;
      world = next;
      const empty = !world.agents.length;
      const missingHooks = world.setup?.hookInstalled === false || world.setup?.eventsWired === false
        || world.setup?.approvalWired === false;
      const attention = world.agents.some((agent) => agent.attention);
      state = missingHooks ? "hooks" : empty ? "empty"
        : enabled && !frozen && attention && (!read || bubble) ? "first_alert" : "ready";
      paintSetup(state === "hooks" && (!frozen || world.setup?.hookInstalled === false));
      // R97-A: 「誰も居ない」なら**常に**空オフィスのカード（🎬 デモを見る）を出す。
      // 以前は frozen（= ?t= 付き＝回帰テスト専用）のときだけ出す枝があり、
      // 「hook 未配線かつ誰も居ない」という**初見でいちばん普通の状態**で導線が消えていた
      // （スモークは frozen 枝しか踏まないので緑のままだった）。
      paintEmpty(empty);
      if (state !== "first_alert") { bubble?.remove(); bubble = null; }
    },
    paint(t) {
      if (disposed) return;
      if (setupBar && copiedUntil > 0 && t >= copiedUntil) {
        copiedUntil = 0;
        setupBar.querySelector(".sb-copy").textContent = T("setup_hook_copy");
      }
      if (state !== "first_alert") return;
      const point = scene.projectBoss?.();
      if (!point || !Number.isFinite(point.left) || !Number.isFinite(point.top)) return;
      const covered = !shell.querySelector("#modalwrap").hidden || !shell.querySelector("#sheet").hidden
        || shell.classList.contains("replay-active") || shell.querySelector("#digest-card")?.hidden === false;
      if (covered) { if (bubble) bubble.hidden = true; return; }
      if (started !== null && t - started >= 15) { dismiss(); return; }
      if (!bubble) {
        bubble = el("button", "boss-onboarding"); bubble.id = "boss-onboarding";
        bubble.addEventListener("click", dismiss);
        shell.querySelector("#stage").append(bubble);
        started = t; read = true;
        try { localStorage.setItem(SEEN, "1"); } catch { /* Persist only after the hint is visible. */ }
      }
      bubble.hidden = false;
      bubble.textContent = T("onboarding_questions");
      bubble.setAttribute("aria-label", T("onboarding_dismiss"));
      const stage = shell.querySelector("#stage");
      const half = bubble.offsetWidth / 2;
      bubble.style.left = `${Math.max(half + 8, Math.min(stage.clientWidth - half - 8, point.left))}px`;
      bubble.style.top = `${Math.max(8, Math.min(stage.clientHeight - bubble.offsetHeight - 8, point.top - 80))}px`;
    },
    dispose() {
      disposed = true;
      setupBar?.remove(); card?.remove(); stageHint?.remove(); bubble?.remove();
    },
  };
}
