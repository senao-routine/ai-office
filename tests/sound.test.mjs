import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createSound, createSoundNotifications } from "../ui/platform/sound.js";

const memory = (initial = {}) => {
  const values = new Map(Object.entries(initial));
  return { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) };
};
test("default off, gesture allocation, PeriodicWave, three cues and persistent refusal", () => {
  const storage = memory(); let contexts = 0, notes = 0, waves = 0;
  class Audio {
    state = "running"; currentTime = 10;
    constructor() { contexts++; }
    createPeriodicWave() { waves++; return {}; }
    createOscillator() { notes++; return { setPeriodicWave() {}, frequency: { setValueAtTime() {} },
      connect: () => ({ connect() {} }), start() {}, stop() {} }; }
    createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} } }; }
    suspend() { this.state = "suspended"; return Promise.resolve(); }
  }
  const sound = createSound({ storage: () => storage, audio: () => Audio });
  sound.playSound("chime"); sound.unlockSound(); assert.equal(contexts, 0);
  sound.setSound(true); assert.equal(contexts, 1); assert.equal(waves, 1);
  for (const kind of ["chime", "tick", "door", "blip", "toString"]) sound.playSound(kind);
  assert.equal(notes, 5);
  sound.setSound(false); sound.playSound("chime"); assert.equal(notes, 5);
  assert.equal(createSound({ storage: () => storage }).shouldAsk(), false);
  const enabled = createSound({ storage: () => memory({ "aioffice.iso.sound": "1" }), audio: () => Audio });
  enabled.playSound("chime"); assert.equal(contexts, 1, "saved ON cannot allocate during playback");
});

test("return prompts once for hidden alerts and the first refreshed snapshot; no arrival replay", () => {
  for (const pollingWhileHidden of [true, false]) {
    const listeners = new Map(), page = { hidden: false,
      addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: (name) => listeners.delete(name) };
    const played = []; let asked = 0, eligible = true;
    const sound = { playSound: (kind) => played.push(kind), shouldAsk: () => eligible,
      markAsked: () => { eligible = false; }, unlockSound() {} };
    const control = createSoundNotifications({ page, sound, ask: () => { asked++; } });
    const world = (attention = false, done = 0, extra = []) => ({ agents: [{ session: "a", attention }, ...extra], tasks: { completed: done } });
    control.update(world()); assert.deepEqual(played, []);
    page.hidden = true; listeners.get("visibilitychange")();
    if (pollingWhileHidden) control.update(world(true));
    assert.equal(asked, 0); assert.deepEqual(played, pollingWhileHidden ? ["chime"] : []);
    page.hidden = false; listeners.get("visibilitychange")(); control.update(world(true));
    assert.equal(asked, 1);
    control.update(world(false, 1)); control.update(world(false, 1, [{ session: "b" }]));
    control.update(world(false, 1)); control.update(world(false, 1, [{ session: "b" }]));
    assert.deepEqual(played.slice(-2), ["tick", "door"]);
    page.hidden = true; control.update(world(true)); page.hidden = false; listeners.get("visibilitychange")();
    assert.equal(asked, 1); control.dispose(); assert.equal(listeners.size, 0);
  }
});

test("?t= freezes before storage, AudioContext lookup, listeners or playback even with saved ON", async () => {
  globalThis.location = { search: "?t=3.2" };
  const clock = new URL("../ui/platform/clock.js?frozen-sound", import.meta.url).href;
  const source = (await readFile(new URL("../ui/platform/sound.js", import.meta.url), "utf8"))
    .replace('"./clock.js"', JSON.stringify(clock));
  const module = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
  const unexpected = () => { throw new Error("frozen side effect"); };
  const sound = module.createSound({ isolated: false, storage: unexpected, audio: unexpected });
  sound.setSound(true); sound.unlockSound(); sound.playSound("chime");
  module.setSound(true); module.playSound("door"); assert.equal(sound.soundOn(), false);
  module.createSoundNotifications({ page: new Proxy({}, { get: unexpected }), ask: unexpected }).update({});
  delete globalThis.location;
});
