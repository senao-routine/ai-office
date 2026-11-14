import { frozen, now } from "/ui/platform/clock.js";
import { optional } from "/ui/hud/shell.js";

const SEEN = "aioffice.onboarding.questions.v1";

/** One first-use state owns setup, the empty office, and the first question hint. */
export function init({ shell, T, scene, DEMO = false, enabled = true }) {
  if (DEMO || shell._stream?.enabled) return { update() {}, paint() {}, dispose() {} };
  const setupSlot = optional(shell, "#cp-setup-slot");
  const cockpit = Boolean(setupSlot);
  // office_json has no path to the installed app. Do not mistake a project cwd for it.
  const setupCommand = () => T(cockpit ? "cp_setup_cmd" : "setup_hook_cmd");
  const copyLabel = () => T(cockpit ? "cp_setup_copy" : "setup_hook_copy");
  const copiedLabel = () => T(cockpit ? "cp_setup_copied" : "setup_hook_copied");
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
    if (!show) {
      setupBar?.remove(); setupBar = null;
      if (setupSlot) setupSlot.hidden = true;
      return;
    }
    if (!setupBar) {
      setupBar = el("div", "setupbar"); setupBar.id = "setupbar";
      const message = el("span", "sb-msg");
      const command = el("code", "sb-cmd");
      const copy = el("button", "sb-copy");
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(setupCommand());
          if (disposed || !setupBar) return;
          copiedUntil = now() + 1.8;
          copy.textContent = copiedLabel();
        } catch { /* The visible command remains available to copy manually. */ }
      });
      setupBar.append(message, command, copy);
      if (!frozen || cockpit) {
        const checklist = el("div", "setup-checklist");
        checklist.append(el("b", "setup-title"), el("span", "setup-hooks"),
          el("span", "setup-events"), el("span", "setup-approval"));
        setupBar.prepend(checklist);
      }
      if (cockpit) {
        // R105: コックピットでは 1 行に畳む（題名・コマンド・コピー・［詳しく］）。3 項目と説明は開いたときだけ＝
        // 初回の案内が 150px を取って部屋が ×1（184px）まで縮み、台帳が 1 行しか見えなかった（実測 1280×800）
        const more = el("button", "sb-more");
        more.type = "button";
        more.setAttribute("aria-expanded", "false");
        more.addEventListener("click", () => {
          const open = setupBar.dataset.open !== "1";
          setupBar.dataset.open = open ? "1" : "0";
          more.setAttribute("aria-expanded", String(open));
          more.textContent = T(open ? "cp_setup_less" : "cp_setup_more");
        });
        setupBar.append(more);
      }
      // 「オフィスの真上」に出す。**#stage が .main の直接の子とは限らない**（コックピットは
      // 左カラムの中に在る）ので、#stage の実際の親へ入れる。決め打ちすると
      // insertBefore が NotFoundError で落ち、セットアップ案内が永久に出ない（R99 で実測）。
      if (setupSlot) {
        setupSlot.append(setupBar);
      } else {
        const stageEl = shell.querySelector("#stage");
        const host = stageEl?.parentNode || shell.querySelector(".main");
        host.insertBefore(setupBar, stageEl && stageEl.parentNode === host ? stageEl : null);
      }
    }
    const hook = world.setup?.hookInstalled === true;
    const events = world.setup?.eventsWired === true;
    // R97-A: 許可プロンプトに答える hook（PermissionRequest）。未配線なら README に書いてある機能が黙って効かない
    const approval = world.setup?.approvalWired === true;
    if (setupSlot) setupSlot.hidden = false;
    setupBar.querySelector(".sb-msg").textContent = T(cockpit ? "cp_setup_note" : "setup_hook");
    setupBar.querySelector(".sb-cmd").textContent = setupCommand();
    const copy = setupBar.querySelector(".sb-copy");
    copy.textContent = copiedUntil > now() ? copiedLabel() : copyLabel();
    if (cockpit) copy.setAttribute("aria-label", T("cp_setup_copy"));
    const more = setupBar.querySelector(".sb-more");
    if (more) more.textContent = T(setupBar.dataset.open === "1" ? "cp_setup_less" : "cp_setup_more");
    if (!frozen || cockpit) {
      const key = (value) => cockpit ? `cp_${value}` : value;
      setupBar.querySelector(".setup-title").textContent = cockpit
        ? T("cp_setup_checklist", Number(hook) + Number(events) + Number(approval), 3) : T("setup_checklist");
      setupBar.querySelector(".setup-hooks").textContent = T(key(hook ? "setup_hooks_ready" : "setup_hooks_pending"));
      setupBar.querySelector(".setup-events").textContent = T(key(events ? "setup_events_ready"
        : world.setup?.eventsWired === false ? "setup_events_pending" : "setup_events_unknown"));
      setupBar.querySelector(".setup-approval").textContent = T(key(approval ? "setup_approval_ready"
        : world.setup?.approvalWired === false ? "setup_approval_pending" : "setup_approval_unknown"));
      setupBar.querySelector(".sb-msg").hidden = !cockpit && hook;
      setupBar.querySelector(".sb-cmd").hidden = hook && events && approval;
      setupBar.querySelector(".sb-copy").hidden = hook && events && approval;
    }
  };
  const paintEmpty = (show) => {
    // The cockpit owns one empty-state entry in the terminal window.
    if (cockpit) return;
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
      paintSetup(state === "hooks" && (cockpit || !frozen || world.setup?.hookInstalled === false));
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
        setupBar.querySelector(".sb-copy").textContent = copyLabel();
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
      if (setupSlot) setupSlot.hidden = true;
    },
  };
}
