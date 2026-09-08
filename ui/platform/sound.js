import { frozen } from "./clock.js";

const KEY = "aioffice.iso.sound";
const ASKED = "aioffice.iso.sound.asked";

/** Three synthesized cues, with no audio assets. Context creation requires a gesture. */
export function createSound({ isolated = false, storage = () => globalThis.localStorage,
  audio = () => globalThis.AudioContext || globalThis.webkitAudioContext } = {}) {
  // Keep this before storage access, constructor lookup, or listener registration.
  if (frozen || isolated) return { soundOn: () => false, setSound() {}, unlockSound() {},
    playSound() {}, shouldAsk: () => false, markAsked() {} };
  let ctx = null, wave = null, enabled = false, asked = false;
  try { enabled = storage().getItem(KEY) === "1"; asked = storage().getItem(ASKED) === "1"; } catch { /* Optional storage. */ }
  const markAsked = () => {
    asked = true;
    try { storage().setItem(ASKED, "1"); } catch { /* Keep the session decision. */ }
  };
  const unlockSound = () => {
    if (!enabled) return;
    try {
      if (!ctx) {
        const AC = audio();
        if (!AC) return;
        ctx = new AC();
        // Soft wooden attack: fundamental plus a few quiet overtones.
        wave = ctx.createPeriodicWave(new Float32Array(5),
          new Float32Array([0, 1, .16, .06, .02]), { disableNormalization: false });
      }
      if (ctx.state === "suspended") void ctx.resume().catch(() => {});
    } catch { /* Audio availability never blocks office controls. */ }
  };
  const soundOn = () => enabled;
  const setSound = (on) => {
    enabled = Boolean(on);
    markAsked();
    try { storage().setItem(KEY, enabled ? "1" : "0"); } catch { /* Session preference still works. */ }
    if (enabled) unlockSound();
    else if (ctx?.state === "running") void ctx.suspend().catch(() => {});
  };
  const playSound = (kind) => {
    // Playback never allocates a context, including after a reload with sound enabled.
    if (!enabled || !ctx || !wave || ctx.state !== "running") return;
    const cues = { chime: [[784, 0, .27], [1047, .14, .32]],
      tick: [[1320, 0, .045]], door: [[196, 0, .16], [294, .08, .20]] };
    if (!Object.hasOwn(cues, kind)) return;
    for (const [frequency, delay, duration] of cues[kind]) {
      const at = ctx.currentTime + delay;
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.setPeriodicWave(wave);
      osc.frequency.setValueAtTime(frequency, at);
      gain.gain.setValueAtTime(.0001, at);
      gain.gain.linearRampToValueAtTime(.055, at + .004);
      gain.gain.exponentialRampToValueAtTime(.0001, at + duration);
      osc.connect(gain).connect(ctx.destination);
      osc.onended = () => { osc.disconnect(); gain.disconnect(); };
      osc.start(at); osc.stop(at + duration);
    }
  };
  return { soundOn, setSound, unlockSound, playSound, markAsked,
    shouldAsk: () => !enabled && !asked };
}

export const { soundOn, setSound, unlockSound, playSound, shouldAsk, markAsked } = createSound();

/** Compare live snapshots only; absence/replay never replays a queue of sounds. */
export function createSoundNotifications({ ask, isolated = false,
  sound = { playSound, shouldAsk, markAsked, unlockSound },
  page = globalThis.document } = {}) {
  if (frozen || isolated) return { update() {}, dispose() {} };
  let previous = null, completed = null, awayAlert = false, returned = false;
  const seen = new Set();
  const prompt = () => {
    if (page.hidden || !awayAlert) return;
    awayAlert = false;
    if (sound.shouldAsk()) { sound.markAsked(); ask(); }
  };
  const visibility = () => {
    if (!page.hidden) { returned = true; prompt(); }
  };
  const unlock = () => sound.unlockSound();
  page.addEventListener("visibilitychange", visibility);
  page.addEventListener("pointerdown", unlock);
  page.addEventListener("keydown", unlock);
  return {
    update(world) {
      const rows = world?.agents || [];
      const alerts = new Map(rows.filter((a) => a.attention).map((a) =>
        [a.session, a.question || a.ask?.title || a.ask?.tool || "approval"]));
      const newAlert = previous && [...alerts].some(([id, key]) => previous.get(id) !== key);
      const arrival = previous && rows.some((a) => !a.external && !seen.has(a.session));
      const done = Number(world?.tasks?.completed) || 0;
      if (newAlert && (page.hidden || returned)) awayAlert = true;
      if (previous) {
        if (newAlert) sound.playSound("chime");
        else if (arrival) sound.playSound("door");
        else if (completed !== null && done > completed) sound.playSound("tick");
      }
      rows.forEach((a) => seen.add(a.session));
      previous = alerts; completed = done;
      if (!page.hidden) { prompt(); returned = false; }
    },
    dispose() {
      page.removeEventListener("visibilitychange", visibility);
      page.removeEventListener("pointerdown", unlock);
      page.removeEventListener("keydown", unlock);
    },
  };
}
