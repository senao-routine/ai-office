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

// ── R110: 環境音（コックピット・既定 OFF・通知音とは別の保存キー）───────────────────────
// 「AI が働いている気配」を音で添える。鳴る根拠は全部**実データ**（Edit/Write の打鍵中・本棚や掲示板に着いた・
// 新しい証拠）で、呼び手（ui/cockpit）が部屋の格子の時計 t を渡す＝この層は時刻を読まない。
// 通知音は「タブが非表示のときだけ」だが、環境音は**見ているときだけ**（hidden なら無音）。
// AudioContext はジェスチャ（unlockAmbient）の中でだけ作る。frozen／isolated（デモ・固定時刻）では完全に無音。
const AMBIENT_KEY = "aioffice.cockpit.ambient";
/** 周波数 Hz・開始の遅れ 秒・長さ 秒。音は合成だけ（音声ファイルは同梱しない）。 */
export const AMBIENT_CUES = Object.freeze({
  keys: [[1320, 0, .03], [1480, .06, .03]],      // 打鍵のカチカチ（Edit/Write の burst の間）
  paper: [[880, 0, .05], [660, .04, .06]],       // 紙をめくる（本棚・掲示板に着いた／読む画面のコマ替わり）
  bell: [[1568, 0, .35]],                        // 小さなベル（新しい証拠＝commit／テスト成功）
});
/** 同じ音の最小間隔と、どの音でも空ける間隔（秒・部屋の時計 t で数える） */
export const AMBIENT_MIN_GAP = Object.freeze({ keys: .5, paper: 3, bell: 10, any: .25 });

export function createAmbient({ isolated = false, storage = () => globalThis.localStorage,
  audio = () => globalThis.AudioContext || globalThis.webkitAudioContext,
  page = () => globalThis.document } = {}) {
  if (frozen || isolated) return { ambientOn: () => false, setAmbient() {}, unlockAmbient() {},
    playAmbient() { return false; } };
  let ctx = null, wave = null, enabled = false;
  const last = { any: -Infinity, keys: -Infinity, paper: -Infinity, bell: -Infinity };
  try { enabled = storage().getItem(AMBIENT_KEY) === "1"; } catch { /* Optional storage. */ }
  const unlockAmbient = () => {
    if (!enabled) return;
    try {
      if (!ctx) {
        const AC = audio();
        if (!AC) return;
        ctx = new AC();
        wave = ctx.createPeriodicWave(new Float32Array(4),
          new Float32Array([0, 1, .10, .03]), { disableNormalization: false });
      }
      if (ctx.state === "suspended") void ctx.resume().catch(() => {});
    } catch { /* Audio availability never blocks office controls. */ }
  };
  const setAmbient = (on) => {
    enabled = Boolean(on);
    try { storage().setItem(AMBIENT_KEY, enabled ? "1" : "0"); } catch { /* Session preference still works. */ }
    if (enabled) unlockAmbient();
    else if (ctx?.state === "running") void ctx.suspend().catch(() => {});
  };
  /** 鳴らせたら true。t は部屋の時計（秒・格子）。鳴らさない理由: OFF・ctx 未確保・非表示・知らない音・間隔。 */
  const playAmbient = (kind, t) => {
    if (!enabled || !ctx || !wave || ctx.state !== "running") return false;
    let hidden = false;
    try { hidden = Boolean(page()?.hidden); } catch { hidden = false; }
    if (hidden) return false;
    if (!Object.hasOwn(AMBIENT_CUES, kind) || !Number.isFinite(t)) return false;
    if (t - last.any < AMBIENT_MIN_GAP.any || t - last[kind] < AMBIENT_MIN_GAP[kind]) return false;
    last.any = t; last[kind] = t;
    for (const [frequency, delay, duration] of AMBIENT_CUES[kind]) {
      const at = ctx.currentTime + delay;
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.setPeriodicWave(wave);
      osc.frequency.setValueAtTime(frequency, at);
      gain.gain.setValueAtTime(.0001, at);
      gain.gain.linearRampToValueAtTime(.03, at + .003);         // 通知音（.055）より小さく＝気配
      gain.gain.exponentialRampToValueAtTime(.0001, at + duration);
      osc.connect(gain).connect(ctx.destination);
      osc.onended = () => { osc.disconnect(); gain.disconnect(); };
      osc.start(at); osc.stop(at + duration);
    }
    return true;
  };
  return { ambientOn: () => enabled, setAmbient, unlockAmbient, playAmbient };
}

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
      // R128 #4: 消音の Bot（world.muted＝様式の apply が ui/hud/board.js の mutedSessions で添える Set）は鳴らさない＝新しい ❗ にも
      // 到着にも数えない。前の ❗ の控え（previous）と見た人（seen）には入れる＝消音を解いた瞬間に、前からの相談・前から居る Bot で鳴らさない
      const quiet = world?.muted instanceof Set ? world.muted : null;
      const alerts = new Map(rows.filter((a) => a.attention).map((a) =>
        [a.session, a.question || a.ask?.title || a.ask?.tool || "approval"]));
      const newAlert = previous && [...alerts].some(([id, key]) => !quiet?.has(id) && previous.get(id) !== key);
      const arrival = previous && rows.some((a) => !a.external && !quiet?.has(a.session) && !seen.has(a.session));
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
