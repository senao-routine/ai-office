// platform の読み口を data URL で隔離する。core の本番コードは時計を import しない。
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../platform/clock.js", import.meta.url), "utf8");
const isolatedClock = (search = "", prelude = "") => import("data:text/javascript;base64," + Buffer.from(
  "const location = { search: " + JSON.stringify(search) + " };\n" + prelude + "\n" + source).toString("base64"));
const { lightStageForHour, createRoomLighting } = await isolatedClock();

test("clock: 部屋の光は地域の 6/12/17/20 時で切り替わる", () => {
  for (const [hour, want] of [
    [0, "night"], [5.999, "night"], [6, "morning"], [11.999, "morning"],
    [12, "day"], [16.999, "day"], [17, "evening"], [19.999, "evening"],
    [20, "night"], [23.999, "night"], [24, "night"],
  ]) assert.equal(lightStageForHour(hour), want, String(hour));
  for (const hour of [NaN, Infinity, undefined, null]) assert.equal(lightStageForHour(hour), "day");
});

test("clock: 固定ページは時間帯に依らず同じ段（?t は経過秒＝地域の時刻に直さない）", async () => {
  const clock = await isolatedClock("?t=61200", `
    const Date = class { constructor() { throw new Error("frozen page must not read a clock"); } };
  `);
  assert.equal(clock.roomLightStage(), "morning");      // localHour() の固定値 11 時
  clock.setTime(0);
  assert.equal(clock.roomLightStage(), "morning");
  assert.equal(clock.localHour(), 11, "iso の固定時刻の既定値を変えない");
});

test("clock: hour の指定は固定ページでも優先する", async () => {
  const overridden = await isolatedClock("?t=0&hour=20", `
    const Date = class { constructor() { throw new Error("hour should avoid the clock"); } };
  `);
  assert.equal(overridden.roomLightStage(), "night");
  const evening = await isolatedClock("?t=3.2&hour=18", "");
  assert.equal(evening.roomLightStage(), "evening");
});

test("clock: 通常の部屋は地域の現在時刻を読む", async () => {
  const live = await isolatedClock("", `
    const Date = class {
      getHours() { return 12; }
      getMinutes() { return 30; }
      getSeconds() { return 0; }
    };
  `);
  assert.equal(live.roomLightStage(), "day");
});

test("clock: 固定・停止・視差効果を減らす・再生の間は光の段も保つ", () => {
  let next = "morning";
  let reads = 0;
  const lighting = createRoomLighting(() => { reads += 1; return next; });
  assert.equal(lighting(false), "morning");
  const before = reads;
  next = "night";
  for (const mode of ["fixed", "stopped", "reduced", "replay"]) {
    assert.equal(lighting(true), "morning", mode);
  }
  assert.equal(reads, before, "止め絵の再描画で時刻を読まない");
  assert.equal(lighting(false), "night", "live へ戻ったら現在の段に合わせる");
});
