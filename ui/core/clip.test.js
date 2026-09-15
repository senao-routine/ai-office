import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { decodeClips, sampleClip, blendPoses, slerp, walkTime } from "./clip.js";
const src = readFileSync(new URL("../iso/gen/robot_clips.js", import.meta.url), "utf8");
const mod = JSON.parse(src.split("export default ")[1].trim().replace(/;$/, ""));
const C = decodeClips(mod);
test("decode: 6 clips, tracks carry unit quaternions", () => {
  assert.equal(Object.keys(C.clips).length, 6);
  const idle = C.clips.idle; assert.ok(idle.tracks.length >= 10);
  const p = sampleClip(idle, C.fps, 0.4);
  for (const rec of Object.values(p)) { const n = Math.hypot(...rec.r); assert.ok(Math.abs(n - 1) < 1e-3, `|q|=${n}`); }
});
test("determinism: same t → identical output, loop wraps", () => {
  const w = C.clips.walk; const a = sampleClip(w, C.fps, 1.234), b = sampleClip(w, C.fps, 1.234);
  assert.deepEqual(a, b);
  const dur = (w.frames - 1) / C.fps; const c = sampleClip(w, C.fps, 1.234 + dur * 3);
  for (const k of Object.keys(a)) for (let i = 0; i < 4; i++) assert.ok(Math.abs(a[k].r[i] - c[k].r[i]) < 1e-6);
});
test("blend: w=0/1 identity, w=0.5 unit quaternion", () => {
  const a = sampleClip(C.clips.idle, C.fps, 0), b = sampleClip(C.clips.sit, C.fps, 3);
  assert.equal(blendPoses(a, b, 0), a); assert.equal(blendPoses(a, b, 1), b);
  const m = blendPoses(a, b, 0.5); for (const rec of Object.values(m)) assert.ok(Math.abs(Math.hypot(...rec.r) - 1) < 1e-3);
});
test("slerp handles antipodal quaternions and walkTime phases by distance", () => {
  const q = slerp([0, 0, 0, 1], [0, 0, 0, -1], 0.5); assert.ok(Math.abs(Math.hypot(...q) - 1) < 1e-9);
  const w = C.clips.walk, dur = (w.frames - 1) / C.fps;
  assert.ok(Math.abs(walkTime(0.75, 1.5, w, C.fps) - dur / 2) < 1e-9);
  assert.ok(Math.abs(walkTime(1.5, 1.5, w, C.fps)) < 1e-9);
});
