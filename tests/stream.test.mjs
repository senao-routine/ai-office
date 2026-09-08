// Node-only camera, privacy and recording lifecycle checks; no browser smoke or screenshots.
import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
const root = new URL("../", import.meta.url);
registerHooks({ resolve(specifier, context, next) {
  return next(specifier.startsWith("/ui/") ? new URL(specifier.slice(1), root).href : specifier, context);
} });
globalThis.location = { search: "?t=3.2&seed=11&stream=1" };
const { IsoScene } = await import("../ui/iso/scene3d.js");
const { PostProcess } = await import("../ui/iso/post.js");
const THREE = await import("../ui/vendor/three/three.module.min.js");
const { broadcastURL, initStream, streamOptions } = await import("../ui/hud/stream.js");
const { recordWebM, webmType } = await import("../ui/platform/webm.js");

const frame = (scene) => [scene.camera.left, scene.camera.right, scene.camera.top, scene.camera.bottom];
function scene(aspect) {
  const s = Object.create(IsoScene.prototype), actor = new THREE.Object3D();
  actor.position.set(2, 0, 1);
  Object.assign(s, {
    streaming: true, _frame: { left: -10 * aspect, right: 10 * aspect, top: 10, bottom: -10 },
    _lastWorld: { agents: [{ id: "a", attention: true }] }, actors: new Map([["a", { nodes: { root: actor } }]]),
    camera: new THREE.OrthographicCamera(), renderer: { domElement: { clientWidth: 800, clientHeight: 800 / aspect } },
    _userScale: .8, _userPanX: .1, _userPanY: .2,
  });
  return s;
}
test("frozen camera repeats the first shot and preserves user framing at both output ratios", () => {
  for (const aspect of [16 / 9, 9 / 16]) {
    const a = scene(aspect), b = scene(aspect), user = a.viewState();
    a.cinematic(3.2); b.cinematic(123);
    assert.deepEqual(frame(a), frame(b));
    assert.deepEqual(a.viewState(), user);
    assert.ok(Math.abs((a.camera.right - a.camera.left) / (a.camera.top - a.camera.bottom) - aspect) < 1e-12);
    a.stopCinematic(); const stopped = frame(a);
    a.actors.get("a").nodes.root.position.x = 100;
    a.cinematic(400);
    assert.deepEqual(frame(a), stopped);
    a.viewPanBy(10, 10); a.cinematic(401);
    assert.notDeepEqual(frame(a), stopped);
    a.viewZoomBy(1.1); assert.equal(a._cinematicPaused, true);
  }
});
test("off quality allocates no post targets and renders directly without changing clear alpha", () => {
  let calls = 0;
  const renderer = { info: { reset() {} }, extensions: { has: () => false },
    setRenderTarget: (target) => assert.equal(target, null), render: () => calls++,
    setClearAlpha: () => assert.fail("clear alpha changed") };
  const post = new PostProcess(renderer, { quality: "off" });
  post.render({}, {});
  assert.equal(calls, 1); assert.deepEqual(post.targets, []); assert.deepEqual(post.materials, []);
});
test("shared URLs allowlist presentation options and omit private query/fragment content", () => {
  const base = "http://localhost:4797/?t=3.2&seed=11&privacy=1&plates=large&unrelated=hidden#private";
  for (const aspect of ["16:9", "9:16", null]) {
    const url = new URL(broadcastURL(base, aspect));
    assert.equal(url.searchParams.get("stream"), "1");
    assert.equal(url.searchParams.get("aspect"), aspect);
    assert.equal(url.searchParams.get("privacy"), "1");
    assert.equal(url.searchParams.get("plates"), "large");
    assert.equal(url.searchParams.has("unrelated"), false); assert.equal(url.hash, "");
  }
  assert.equal(streamOptions("?aspect=bad").aspect, null);
});
test("privacy subtitle never includes names or activity; normal mode installs no stream DOM", () => {
  const nodes = [], classes = new Set();
  globalThis.document = { documentElement: { classList: { contains: (x) => classes.has(x),
    add: (x) => classes.add(x), remove: (x) => classes.delete(x) } },
    createElement: () => ({ remove() {}, textContent: "" }) };
  const root = { classList: { add() {} } }, shell = { style: {}, querySelector: () => ({ append: (...items) => nodes.push(...items) }) };
  initStream({ root, shell, options: { enabled: false } }); assert.equal(nodes.length, 0);
  const stream = initStream({ root, shell, options: { enabled: true, privacy: true, aspect: "9:16" },
    T: (key, value) => value || key });
  stream.paint({ agents: [{ name: "Private project", projectName: "Private project", activity: "Private work",
    badge: "A", attention: true, state: "waiting" }] });
  assert.equal(nodes[0].textContent, "A › board_attention"); assert.equal(nodes[1].textContent, "❗1");
  stream.dispose(); assert.equal(classes.size, 0);
});
test("unsupported WebM is hidden; successful capture, cancellation and failures release tracks", (t) => {
  let stopped = 0, clicked = 0, done = 0, failed = 0, instance;
  const canvas = { captureStream: () => ({ getTracks: () => [{ stop: () => stopped++ }] }) };
  class Recorder {
    static isTypeSupported = (type) => type === "video/webm";
    constructor() { instance = this; this.state = "inactive"; }
    start() { this.state = "recording"; }
    stop() { this.state = "inactive"; this.ondataavailable({ data: new Blob(["frame"]) }); this.onstop(); }
  }
  t.mock.method(globalThis, "setTimeout", (callback) => { callback(); return 0; });
  globalThis.MediaRecorder = Recorder;
  assert.equal(webmType({}), null); assert.equal(webmType(canvas), "video/webm");
  globalThis.document = { body: { append() {} }, createElement: () => ({ click: () => clicked++, remove() {} }) };
  const options = { done: () => done++, failed: () => failed++ };
  recordWebM(canvas, options).stop();
  assert.equal(clicked, 1); assert.equal(done, 1); assert.equal(stopped, 1);
  recordWebM(canvas, options).cancel();
  assert.equal(clicked, 1); assert.equal(done, 1); assert.ok(stopped >= 2);
  recordWebM(canvas, options); instance.onerror();
  assert.equal(failed, 1);
  Recorder.isTypeSupported = () => false; assert.equal(webmType(canvas), null);
  delete globalThis.MediaRecorder; assert.equal(webmType(canvas), null);
});
