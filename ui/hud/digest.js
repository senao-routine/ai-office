import { createReplay, digestSummary, replayFrame, REPLAY_SECONDS } from "/ui/core/replay.js";
import { getDigest, getTimeline, postSeen } from "/ui/platform/api.js";
import { absenceDays, epochNow, frozen, localDayStart, now } from "/ui/platform/clock.js";
import { recordWebM, webmType } from "/ui/platform/webm.js";

/** Unread summary and a separate, disposable playback world. Live polling stays live. */
export function init({ shell, T, getWorld, tray, showToast, beforeOpen, restore, canvas,
  enabled = true, DEMO = false }) {
  // Structural golden guard: not even listeners, timers, requests or hidden UI are installed.
  if (frozen || DEMO || !enabled) return { update() {}, frame: () => null, canRecord: () => false, dispose() {} };
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    node.className = cls;
    if (text !== undefined) node.textContent = text;
    if (tag === "button") node.type = "button";
    return node;
  };
  const stage = shell.querySelector("#stage");
  const strip = el("button", "digest-strip"); strip.id = "digest-strip"; strip.hidden = true;
  const card = el("aside", "digest-card"); card.id = "digest-card"; card.hidden = true;
  card.setAttribute("role", "dialog"); card.setAttribute("aria-labelledby", "digest-title");
  const head = el("header", "digest-head");
  const title = el("b", ""); title.id = "digest-title";
  const close = el("button", "digest-close", "✕");
  head.append(title, close);
  const lines = [el("p", "digest-stopped"), el("p", "digest-completed"), el("p", "digest-working")];
  const answers = el("div", "digest-answers");
  const details = el("button", "digest-details"); details.id = "digest-details";
  card.append(head, ...lines, answers, details);
  const controls = el("div", "replay-controls"); controls.id = "replay-controls"; controls.hidden = true;
  const progress = el("output", "replay-progress"); progress.id = "replay-progress";
  const pause = el("button", "replay-pause"); pause.id = "replay-pause";
  const exit = el("button", "digest-close", "✕");
  controls.append(progress, pause, exit);
  shell.querySelector(".main").append(strip);
  stage.append(card, controls);

  let disposed = false, started = false, pendingAuto = false, busy = false;
  let data = null, summary = null, replay = null, actions = [];
  let elapsed = 0, lastTick = 0, origin = 0, paused = false;
  let request = null, version = 0, previousFocus = null, actionKey = "";
  let lastRefresh = -Infinity;
  let seenRequest = null;
  let recording = null, saving = false;
  const inertBefore = new Map();
  const isPresent = () => !document.hidden && document.hasFocus();
  const formatAway = (seconds) => {
    const minutes = Math.floor(seconds / 60);
    return minutes >= 60 ? `${Math.floor(minutes / 60)}h${minutes % 60}m` : `${minutes}m`;
  };
  const markSeen = async (report = false) => {
    if (disposed) return;
    try {
      seenRequest ||= postSeen().finally(() => { seenRequest = null; });
      await seenRequest;
    } catch { if (report && !disposed) showToast(T("digest_seen_failed"), false); }
  };
  const setReplay = (on) => {
    shell.classList.toggle("replay-active", on);
    controls.hidden = !on;
    if (on) {
      for (const node of shell.querySelectorAll(".side, .head, #viewport, #labels, #attn, #sheet, .bottom")) {
        inertBefore.set(node, node.inert); node.inert = true;
      }
    } else {
      for (const [node, value] of inertBefore) node.inert = value;
      inertBefore.clear();
    }
  };
  const closeAll = () => {
    if (card.hidden && !replay && !saving) return;
    recording?.cancel(); recording = null; saving = false;
    version += 1; request?.abort(); request = null; busy = false;
    card.hidden = true; replay = null; pendingAuto = false;
    setReplay(false);
    restore();
    void markSeen(true);
    paint();
    if (previousFocus?.isConnected && !previousFocus.closest?.("[hidden]")) previousFocus.focus();
    else if (!strip.hidden) strip.focus();
  };
  const answer = async (index) => {
    if (busy || !actions[index]) return;
    busy = true; paint();
    const ok = await tray.answerFirst(index, actions[index]);
    busy = false;
    if (disposed) return;
    if (ok) closeAll();
    else paint();
  };
  const paint = () => {
    const present = Boolean(summary);
    strip.hidden = !present || Boolean(replay);
    strip.disabled = !summary?.hasActivity;
    if (present) strip.textContent = T("digest_strip", summary.since > 0
      ? formatAway(summary.away) : T("digest_today"), summary.stopped, summary.completed, summary.working);
    if (card.hidden || !summary) return;
    title.textContent = T(summary.away > 90 * 86400 ? "digest_retained" : "digest_title");
    close.setAttribute("aria-label", T("digest_close"));
    lines[0].textContent = T("digest_stopped", summary.stopped, summary.longest);
    lines[1].textContent = T("digest_completed", summary.completed);
    lines[2].textContent = T("digest_working", summary.working);
    details.textContent = T(busy ? "loading" : "digest_details");
    details.disabled = busy;
    if (!busy) actions = tray.first();
    const key = JSON.stringify(actions.map((a) => [a.session, a.attnKey, a.label]));
    if (key !== actionKey) {
      actionKey = key;
      answers.replaceChildren(...actions.map((action, i) => {
        const button = el("button", "digest-answer", `[${i + 1}] ${action.label}`);
        button.title = `${action.name} — ${shell.querySelector("#attn span")?.textContent || ""}`;
        button.addEventListener("click", () => { void answer(i); });
        return button;
      }));
    }
    for (const button of answers.children) button.disabled = busy;
  };
  const canAutoOpen = () => !saving && isPresent() && shell.querySelector("#sheet").hidden
    && shell.querySelector("#modalwrap").hidden
    && !document.activeElement?.closest?.("input, textarea, [contenteditable=true]");
  const open = () => {
    if (disposed || replay || !summary?.hasActivity) return;
    previousFocus = document.activeElement;
    beforeOpen();
    pendingAuto = false; card.hidden = false;
    paint(); close.focus();
  };
  const refresh = async () => {
    if (disposed || saving || !isPresent() || replay || !card.hidden) return;
    lastRefresh = now();
    const ticket = ++version;
    request?.abort(); request = new AbortController();
    try {
      // A preceding visibility/pagehide acknowledgement must finish before reading its cursor.
      if (seenRequest) await seenRequest.catch(() => {});
      const signal = request.signal;
      const result = await getDigest(signal);
      const at = epochNow();
      // /digest is day-scoped. Include yesterday (or earlier retained days) so
      // crossing midnight does not silently discard completed work from the absence.
      const days = absenceDays(result.since, at).filter((day) => day !== result.day);
      const earlier = [];
      for (const day of days) earlier.push(await getDigest(signal, day, result.since));
      if (disposed || ticket !== version) return;
      data = { ...result, available: [result, ...earlier].some((day) => day.available),
        totals: { ...result.totals,
          tasksDone: [result, ...earlier].reduce((sum, day) => sum + day.totals?.tasksDone, 0) } };
      summary = digestSummary(getWorld(), data, at);
      pendingAuto = Boolean(summary?.autoOpen);
      paint();
      if (pendingAuto && canAutoOpen()) open();
      else if (!pendingAuto && isPresent()) void markSeen();
    } catch {
      // Offline/old server: silence, never guessed counts or an error card.
    }
  };
  const advance = (t) => {
    if (!paused) elapsed = Math.min(REPLAY_SECONDS, elapsed + Math.max(0, t - lastTick));
    lastTick = t;
    if (elapsed >= REPLAY_SECONDS) paused = true;
    progress.textContent = `00:${String(Math.floor(elapsed)).padStart(2, "0")} / 00:20`;
    progress.setAttribute("aria-label", T("replay_progress"));
    pause.textContent = T(paused && elapsed < REPLAY_SECONDS ? "replay_resume" : "replay_pause");
    pause.disabled = saving || elapsed >= REPLAY_SECONDS;
    pause.setAttribute("aria-pressed", String(paused));
    exit.setAttribute("aria-label", T("replay_close"));
  };
  const recordingFailed = () => {
    if (disposed) return;
    closeAll(); showToast(T("replay_record_failed"), false);
  };
  const startReplay = async (save = false) => {
    if (busy || replay || (!save && !summary) || (save && !webmType(canvas))) return;
    busy = true; saving = save; paint();
    const ticket = ++version;
    request?.abort(); request = new AbortController();
    try {
      const until = save ? epochNow() : summary.until;
      const since = save ? localDayStart(until) : summary.since || localDayStart(until);
      const result = await getTimeline(since, request.signal);
      if (disposed || ticket !== version) return;
      const next = createReplay(getWorld(), result.events, { since, until });
      if (!next.eventCount) { showToast(T("replay_empty")); return; }
      if (result.events.length >= 500) showToast(T("replay_latest", 500));
      replay = next; elapsed = 0; paused = false; origin = lastTick = now();
      if (save) { previousFocus = document.activeElement; beforeOpen(); }
      card.hidden = true;
      setReplay(true); advance(lastTick); pause.focus();
      if (save) {
        // Draw the initial historical world before starting capture, never the live world.
        paused = true; restore();
        recording = recordWebM(canvas, {
          done: () => { if (!disposed) { recording = null; closeAll(); showToast(T("replay_saved")); } },
          failed: recordingFailed,
        });
        paused = false; origin = lastTick = now();
      }
    } catch (err) {
      if (!disposed && err.name !== "AbortError") {
        if (save) recordingFailed();
        else showToast(T("replay_failed"), false);
      }
    } finally {
      if (!disposed && ticket === version) { busy = false; if (!replay) saving = false; paint(); }
    }
  };
  const onKey = (e) => {
    if (card.hidden && !replay) return;
    if (e.target.closest?.("input, textarea, [contenteditable=true]")) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); closeAll(); return; }
    // Capture before the existing tray's bubble listener: exactly one delivery per key.
    if (/^[1-9]$/.test(e.key) || /^[jJkK]$/.test(e.key)) {
      e.stopImmediatePropagation(); e.preventDefault();
      if (!replay && !e.repeat && !e.ctrlKey && !e.metaKey && !e.altKey) void answer(Number(e.key) - 1);
    }
  };
  const onVisibility = () => {
    if (saving && document.hidden) {
      closeAll(); showToast(T("replay_record_cancelled"), false); return;
    }
    if (!isPresent()) {
      if (replay && !saving) { advance(now()); paused = true; }
      else if (!saving && card.hidden && data && !pendingAuto) void markSeen();
    } else if (!replay) void refresh();
  };
  const onPageHide = () => {
    if (saving) { closeAll(); return; }
    if (data && card.hidden && !replay && !pendingAuto) void markSeen();
  };
  strip.addEventListener("click", open);
  close.addEventListener("click", closeAll);
  exit.addEventListener("click", closeAll);
  details.addEventListener("click", () => { void startReplay(); });
  pause.addEventListener("click", () => { if (!saving) { advance(now()); paused = !paused; advance(now()); } });
  window.addEventListener("keydown", onKey, true);
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("pagehide", onPageHide);
  window.addEventListener("focus", onVisibility);
  window.addEventListener("blur", onVisibility);
  // Keep the server's last-seen cursor current during a long visible visit as well.
  const heartbeat = setInterval(() => {
    if (started && data && isPresent() && card.hidden && !replay && !pendingAuto) void markSeen();
  }, 60000);
  return {
    canRecord: () => Boolean(webmType(canvas)),
    saveToday: () => startReplay(true),
    afterDraw() { if (recording && elapsed >= REPLAY_SECONDS) recording.stop(); },
    update() {
      if (!started) { started = true; void refresh(); return; }
      if (!summary && now() - lastRefresh >= 30) { void refresh(); return; }
      if (summary) summary = digestSummary(getWorld(), data, summary.until);
      paint();
      if (pendingAuto && canAutoOpen()) open();
    },
    frame(t) {
      if (!replay) return null;
      advance(t);
      const frame = replayFrame(replay, elapsed);
      return { world: frame.world, t: origin + frame.elapsed };
    },
    dispose() {
      recording?.cancel(); recording = null;
      disposed = true; version += 1; request?.abort(); clearInterval(heartbeat);
      window.removeEventListener("keydown", onKey, true);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("focus", onVisibility);
      window.removeEventListener("blur", onVisibility);
      setReplay(false); strip.remove(); card.remove(); controls.remove();
    },
  };
}
