import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { T, setLang, dictKeys, dictStrings } from "../ui/iso/strings.js";
import { absenceDays, localDayStart } from "../ui/platform/clock.js";

// Load browser absolute imports without a browser, dependencies or files on disk.
const root = new URL("../", import.meta.url);
async function hud(name, fixed) {
  const source = (await readFile(new URL(`ui/hud/${name}.js`, root), "utf8"))
    .replace(/from "(\/ui\/[^"]+)"/g, (_, path) => {
      const url = new URL(path.slice(1), root);
      // Each case gets a separate clock instance so frozen cannot leak between tests.
      if (path.endsWith("/clock.js")) url.search = `?frozen-test=${fixed}`;
      return `from ${JSON.stringify(url.href)}`;
    });
  globalThis.location = { search: fixed ? "?t=3.2" : "" };
  return import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
}

test("frozen digest and growth return before any DOM, API, listener, or timer access", async () => {
  const digest = await hud("digest", true);
  const growth = await hud("growth", true);
  const unexpected = () => { throw new Error("frozen side effect"); };
  const controller = digest.init({ shell: new Proxy({}, { get: unexpected }),
    T: unexpected, getWorld: unexpected });
  const levels = growth.init({ T: unexpected, getGrowth: unexpected, getWorld: unexpected, showToast: unexpected });
  controller.update(); controller.dispose(); levels.update(); levels.paintSheet();
  assert.equal(controller.frame(3.2), null);
  assert.equal(levels.label({ id: "p1" }), null);
});

test("HUD exposes every supplied XP item, exact sum and token exclusion in ja/en", async () => {
  const module = await hud("growth", false);
  const rates = { tasksDone: [12, 120], commits: [8, 40], asksAnswered: [20, 40],
    fastAnswers: [8, 8], activeMin: [340, 68], hires: [1, 5], turnsCompleted: [2, 6] };
  const data = { byProject: { p1: { xp: 287, level: 1, breakdown: Object.fromEntries(
    Object.entries(rates).map(([key, [count, xp]]) => [key, { count, xp }])) } } };
  const controller = module.init({ T, getGrowth: () => data, getWorld: () => ({ agents: [] }), showToast() {} });
  for (const lang of ["ja", "en"]) {
    setLang(lang);
    const value = controller.label({ id: "p1" });
    const sum = [...value.text.matchAll(/= ([\d.]+) XP/g)].reduce((total, match) => total + Number(match[1]), 0);
    assert.equal(sum, 287);
    assert.equal(value.level, "Lv 1");
    assert.equal(value.text.split("\n")[0], "287 XP");
    assert.match(value.text, lang === "ja" ? /トークン量は含みません/ : /Token volume is excluded/);
  }
  setLang("ja");
});

test("iso has matching ja/en keys and no Japanese fallback text in English", () => {
  const keys = dictKeys();
  assert.deepEqual(keys.ja.sort(), keys.en.sort());
  assert.deepEqual(Object.values(dictStrings().en).filter((value) => /[぀-ヿ一-鿿]/.test(value)), []);
});

test("absence day selection includes both sides of midnight and bounds old history", () => {
  const since = new Date(2026, 8, 7, 23, 50).getTime() / 1000;
  const until = new Date(2026, 8, 8, 0, 15).getTime() / 1000;
  assert.deepEqual(absenceDays(since, until), ["2026-09-08", "2026-09-07"]);
  assert.equal(localDayStart(until), new Date(2026, 8, 8).getTime() / 1000);
  assert.deepEqual(absenceDays(0, until), []);
  assert.equal(absenceDays(1, until).length, 90);
});
