import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createSound, createSoundNotifications, createAmbient } from "../ui/platform/sound.js";

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

test("R128 #4: 消音の Bot（world.muted）は新しい ❗ でも到着でも鳴らさない・解いた瞬間に前からの相談で鳴らさない", () => {
  const page = { hidden: false, addEventListener() {}, removeEventListener() {} };
  const played = [];
  const sound = { playSound: (kind) => played.push(kind), shouldAsk: () => false, markAsked() {}, unlockSound() {} };
  const control = createSoundNotifications({ page, sound, ask: () => {} });
  const quiet = new Set(["bot-quiet1"]);
  const s = { session: "s1" }, q = { session: "bot-quiet1" }, loud = { session: "bot-loud01" };
  const asking = (a, question) => ({ ...a, attention: true, question });
  const world = (agents, muted = quiet) => ({ agents, muted, tasks: { completed: 0 } });
  control.update(world([s]));
  control.update(world([s, q]));                                                    // 消音の Bot が出勤＝door を鳴らさない
  control.update(world([s, asking(q, "静か？")]));                                  // 消音の Bot の新しい ❗＝chime を鳴らさない
  assert.deepEqual(played, []);
  control.update(world([s, asking(q, "静か？"), loud]));                            // 消音でない Bot の出勤＝door
  control.update(world([s, asking(q, "静か？"), asking(loud, "鳴る？")]));           // 消音でない Bot の ❗＝chime
  control.update(world([asking(s, "どっち？"), asking(q, "静か？"), asking(loud, "鳴る？")]));   // セッション＝今までどおり
  assert.deepEqual(played, ["door", "chime", "chime"]);
  // 解く: 前からの相談では鳴らさない → 次の新しい相談で鳴る
  control.update(world([asking(s, "どっち？"), asking(q, "静か？"), asking(loud, "鳴る？")], new Set()));
  assert.equal(played.length, 3, "解いた瞬間に前からの相談で鳴った");
  control.update(world([asking(s, "どっち？"), asking(q, "次の相談"), asking(loud, "鳴る？")], new Set()));
  assert.deepEqual(played.slice(3), ["chime"], "解いた後の新しい相談で鳴らない");
  // world.muted が無い・Set でない（古い world・デモ・リプレイ）は今までどおり鳴る
  const plain = createSoundNotifications({ page, sound, ask: () => {} });
  plain.update({ agents: [s], tasks: { completed: 0 } });
  plain.update({ agents: [s, asking(q, "静か？")], muted: ["bot-quiet1"], tasks: { completed: 0 } });
  assert.deepEqual(played.slice(4), ["chime"]);
  control.dispose(); plain.dispose();
});

test("R110 ambient: default off, own key, gesture allocation, three cues, rate limits, hidden is silent", () => {
  const storage = memory(); let contexts = 0, notes = 0;
  const page = { hidden: false };
  class Audio {
    state = "running"; currentTime = 10;
    constructor() { contexts++; }
    createPeriodicWave() { return {}; }
    createOscillator() { notes++; return { setPeriodicWave() {}, frequency: { setValueAtTime() {} },
      connect: () => ({ connect() {} }), start() {}, stop() {} }; }
    createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} } }; }
    suspend() { this.state = "suspended"; return Promise.resolve(); }
    resume() { this.state = "running"; return Promise.resolve(); }
  }
  const amb = createAmbient({ storage: () => storage, audio: () => Audio, page: () => page });
  assert.equal(amb.ambientOn(), false, "既定 OFF");
  assert.equal(amb.playAmbient("keys", 1), false); amb.unlockAmbient(); assert.equal(contexts, 0, "OFF では ctx を作らない");
  amb.setAmbient(true);
  assert.equal(storage.getItem("aioffice.cockpit.ambient"), "1", "通知音（aioffice.iso.sound）とは別の鍵");
  assert.equal(storage.getItem("aioffice.iso.sound"), undefined);
  assert.equal(contexts, 1, "ON にした（ジェスチャ）ときだけ ctx を作る");
  // 3 音＝keys 2 音・paper 2 音・bell 1 音。知らない音は鳴らない。間隔: 同じ音は keys .5／paper 3／bell 10 秒・どの音でも .25 秒
  assert.equal(amb.playAmbient("keys", 100), true); assert.equal(notes, 2);
  assert.equal(amb.playAmbient("keys", 100.4), false, "keys は 0.5 秒あける");
  assert.equal(amb.playAmbient("keys", 100.6), true); assert.equal(notes, 4);
  assert.equal(amb.playAmbient("paper", 100.7), false, "直前の音から 0.25 秒あける");
  assert.equal(amb.playAmbient("paper", 101), true); assert.equal(notes, 6);
  assert.equal(amb.playAmbient("bell", 102), true); assert.equal(notes, 7);
  assert.equal(amb.playAmbient("blip", 103), false); assert.equal(amb.playAmbient("toString", 103), false); assert.equal(notes, 7);
  assert.equal(amb.playAmbient("paper", 103), false, "paper は 3 秒");
  assert.equal(amb.playAmbient("paper", 104.1), true);
  assert.equal(amb.playAmbient("bell", 111), false, "bell は 10 秒"); assert.equal(amb.playAmbient("bell", 112.1), true);
  assert.equal(amb.playAmbient("keys", NaN), false, "時計が無いときは鳴らさない");
  // 見ていないときは無音・OFF にすると止まる
  page.hidden = true; assert.equal(amb.playAmbient("keys", 120), false); page.hidden = false;
  const before = notes; amb.setAmbient(false); assert.equal(amb.playAmbient("keys", 130), false); assert.equal(notes, before);
  // 保存 ON で開き直しても、再生では ctx を確保しない（ジェスチャ待ち）
  const saved = createAmbient({ storage: () => memory({ "aioffice.cockpit.ambient": "1" }), audio: () => Audio, page: () => page });
  assert.equal(saved.ambientOn(), true); assert.equal(saved.playAmbient("bell", 1), false); assert.equal(contexts, 1);
  // isolated（デモ）は完全な no-op
  const demo = createAmbient({ isolated: true, storage: () => { throw new Error("no"); }, audio: () => { throw new Error("no"); } });
  demo.setAmbient(true); demo.unlockAmbient(); assert.equal(demo.playAmbient("keys", 1), false); assert.equal(demo.ambientOn(), false);
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
  const ambient = module.createAmbient({ isolated: false, storage: unexpected, audio: unexpected, page: unexpected });
  ambient.setAmbient(true); ambient.unlockAmbient();
  assert.equal(ambient.playAmbient("keys", 3.2), false); assert.equal(ambient.ambientOn(), false);
  delete globalThis.location;
});
