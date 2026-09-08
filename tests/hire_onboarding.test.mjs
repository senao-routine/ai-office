import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createArrivals } from "../ui/platform/arrivals.js";
import { hireSession, projectIdForPath } from "../ui/platform/api.js";
import { T, setLang, dictKeys, dictStrings } from "../ui/iso/strings.js";

const root = new URL("../", import.meta.url);
async function hud(name, fixed = false) {
  globalThis.location = { search: fixed ? "?t=3.2" : "" };
  const source = (await readFile(new URL(`ui/hud/${name}.js`, root), "utf8"))
    .replace(/from "(\/ui\/[^\"]+)"/g, (_, path) => {
      const url = new URL(path.slice(1), root);
      if (path.endsWith("clock.js")) url.search = `?hire_test=${fixed}`;
      return `from ${JSON.stringify(url.href)}`;
    });
  return import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
}

test("arrivals correlate the returned bg ID, survive reload, and expire at 600 seconds", () => {
  let t = 1000, saved = null;
  const options = { isolated: false, clock: () => t,
    storage: () => ({ getItem: () => saved, setItem: (_, value) => { saved = value; } }) };
  const old = { session: "old", cwd: "/sample/project" };
  const fresh = { session: "new", bg: { id: "abcdef12" }, cwd: "/sample/project/.claude/worktrees/quiet-oak" };
  let arrivals = createArrivals(options);
  arrivals.update({ employees: [old] });
  assert.equal(arrivals.label("old"), null);
  arrivals.hired("abcdef12"); t += 5;
  arrivals.update({ employees: [old, fresh] });
  assert.deepEqual(arrivals.label("new"), { isNew: true, slug: "quiet-oak" });
  arrivals = createArrivals(options);
  arrivals.update({ employees: [old, fresh] });
  t = 1599; assert.equal(arrivals.label("new").isNew, true);
  t = 1600; assert.deepEqual(arrivals.label("new"), { isNew: false, slug: "quiet-oak" });
  arrivals.update({ employees: [old] });
  arrivals.update({ employees: [old, fresh] });
  assert.equal(arrivals.label("new").isNew, false);
  assert.doesNotMatch(saved, /cwd|sample|quiet-oak|prompt|name/);
});

test("live arrivals work without storage; frozen never touches storage or clocks", () => {
  const live = createArrivals({ isolated: false, clock: () => 1000,
    storage() { throw new Error("denied"); } });
  live.update({ employees: [] });
  live.update({ employees: [{ session: "later" }, { session: "external", external: "openclaw" }] });
  assert.equal(live.label("later").isNew, true);
  assert.equal(live.label("external"), null);
  const fixed = createArrivals({ isolated: true,
    clock() { assert.fail("clock"); }, storage() { assert.fail("storage"); } });
  fixed.hired("abcdef12"); fixed.update({ employees: [{ session: "new" }] });
  assert.equal(fixed.label("new"), null);
});

test("hire uses only existing parameters with the established CSRF header; cwd hashing is NFC", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (path, options) => {
    assert.equal(path, "/api/hire");
    assert.equal(options.method, "POST");
    assert.equal(options.headers["X-Office-Local"], "1");
    assert.deepEqual(JSON.parse(options.body), { projectId: "012345abcdef", prompt: "Review README", worktree: true });
    return { ok: true, text: async () => '{"ok":true,"bgId":"abcdef12"}' };
  };
  try { assert.equal((await hireSession("012345abcdef", "Review README", true)).ok, true); }
  finally { globalThis.fetch = original; }
  const cwd = "/sample/cafe\u0301";
  assert.equal(await projectIdForPath(cwd), createHash("sha1").update(cwd.normalize("NFC")).digest("hex").slice(0, 12));
});

test("frozen launch, hint, and WebGL fallback expose no new UI", async () => {
  const forbidden = new Proxy({}, { get() { assert.fail("frozen touched DOM"); } });
  (await hud("hire", true)).init({ shell: forbidden, modals: {} }).dispose();
  (await hud("onboarding", true)).init({ shell: forbidden, enabled: true }).paint(3.2);
  const appended = [];
  globalThis.document = { createElement: () => ({}) };
  const scene = (await hud("scene_null", true)).init({
    shell: { querySelector: () => ({ append: (node) => appended.push(node) }) }, T });
  assert.equal(scene.ready(), true);
  assert.equal(appended.length, 1);
  assert.equal(appended[0].id, "no3d");
  delete globalThis.document;
});

// Minimal DOM harness exercises launch decisions, without a browser or real session.
class Element {
  constructor(tag, cls = "", text = "") {
    this.tag = tag; this.className = cls; this.textContent = text;
    this.children = []; this.handlers = {}; this.value = "";
    this.classList = { toggle() {} };
  }
  append(...nodes) { for (const node of nodes) { this.children.push(node); node.parent = this; } }
  after(node) { this.parent.append(node); }
  replaceChildren(...nodes) { this.children.forEach((node) => { node.parent = null; }); this.children = []; this.append(...nodes); }
  get firstElementChild() { return this.children[0]; }
  get options() { return this.children; }
  setAttribute() {}
  addEventListener(name, handler) { this.handlers[name] = handler; }
  removeEventListener(name) { delete this.handlers[name]; }
  focus() {}
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((n) => n !== this); }
  contains(node) { return this === node || this.children.some((child) => child.contains?.(node)); }
  querySelector(selector) {
    const found = this.children.find((n) => `#${n.id}` === selector);
    return found || this.children.map((n) => n.querySelector?.(selector)).find(Boolean) || null;
  }
  fire(name) { return this.handlers[name]?.({ preventDefault() {} }); }
}

test("launch sheet blocks empty/oversized instructions and double submits; Codex makes no launch request", async () => {
  const originalFetch = globalThis.fetch;
  const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const shell = new Element("div"), modal = new Element("div");
  const anchor = new Element("button"); anchor.id = "btn-newproj"; shell.append(anchor, modal);
  globalThis.document = { createTextNode: (text) => new Element("text", "", text) };
  let copied = "", requested = 0, finish, refreshed = 0;
  Object.defineProperty(globalThis, "navigator", { configurable: true,
    value: { clipboard: { writeText: async (s) => { copied = s; } } } });
  const apiResponse = new Promise((resolve) => { finish = resolve; });
  globalThis.fetch = async (_path, options) => { requested++; assert.equal(JSON.parse(options.body).worktree, true); return apiResponse; };
  try {
    const { init } = await hud("hire");
    const instance = init({ shell, T, lang: () => "en", getWorld: () => ({ launchable: [] }),
      arrivals: { hired: (id) => assert.equal(id, "abcdef12") }, refresh: () => refreshed++, showToast() {},
      modals: { modal, openModal() {}, closeModal: () => modal.replaceChildren(),
        mEl: (tag, cls, text) => new Element(tag, cls, text) } });
    shell.querySelector("#btn-hire").fire("click");
    const form = modal.querySelector("#hire-form"), go = modal.querySelector("#mgo-hire");
    const prompt = modal.querySelector("#hire-prompt"), provider = modal.querySelector("#hire-provider");
    const project = modal.querySelector("#hire-project");
    provider.value = "claude"; project.value = "012345abcdef";
    prompt.value = "   "; await form.fire("submit"); assert.equal(requested, 0);
    prompt.value = "a".repeat(4001); await form.fire("submit"); assert.equal(go.disabled, true);
    provider.value = "codex"; await form.fire("submit");
    assert.equal(requested, 0); assert.equal(copied, "open -a Terminal");
    provider.value = "claude"; prompt.value = "🌱".repeat(4000);
    modal.querySelector("#hire-worktree").checked = true;
    const pending = form.fire("submit"); await form.fire("submit");
    assert.equal(requested, 1); assert.equal(shell.querySelector("#btn-hire").disabled, true);
    finish({ ok: true, text: async () => '{"ok":true,"bgId":"abcdef12"}' });
    await pending; assert.equal(refreshed, 1); assert.equal(modal.children.length, 0);
    instance.dispose();
  } finally {
    globalThis.fetch = originalFetch; delete globalThis.document;
    if (originalNavigator) Object.defineProperty(globalThis, "navigator", originalNavigator);
    else delete globalThis.navigator;
  }
});

test("new strings have ja/en parity and contain no retired UI terminology", () => {
  assert.deepEqual(dictKeys().ja.sort(), dictKeys().en.sort());
  for (const lang of ["ja", "en"]) {
    setLang(lang);
    for (const key of dictKeys()[lang].filter((k) => /^(hire_|onboarding_|setup_|btn_hire|no3d_list)/.test(k))) {
      assert.doesNotMatch(T(key, "sample"), /社員|メンバー|キャラクター|部署|部下/);
    }
  }
  assert.ok(Object.keys(dictStrings().en).length);
});

test("first-use hint tolerates a hidden digest card and persists only after display", async () => {
  let saved = null;
  globalThis.localStorage = { getItem: () => saved, setItem: (_, value) => { saved = value; } };
  const stage = new Element("div"); stage.clientWidth = 800; stage.clientHeight = 600;
  const surfaces = { "#stage": stage, "#modalwrap": { hidden: true },
    "#sheet": { hidden: true }, "#digest-card": { hidden: false } };
  globalThis.document = { createElement: (tag) => {
    const node = new Element(tag); node.style = {}; node.offsetWidth = 260; node.offsetHeight = 44;
    return node;
  } };
  try {
    const module = await hud("onboarding");
    const ctx = { shell: { querySelector: (key) => surfaces[key], classList: { contains: () => false } },
      T, scene: { projectBoss: () => ({ left: 400, top: 200 }) }, enabled: true };
    const hint = module.init(ctx);
    hint.paint(1); assert.equal(stage.children.length, 0); assert.equal(saved, null);
    surfaces["#digest-card"].hidden = true;
    hint.paint(2); assert.equal(stage.children.length, 1); assert.equal(saved, "1");
    stage.children[0].fire("click"); hint.paint(3); assert.equal(stage.children.length, 0);
    module.init(ctx).paint(4); assert.equal(stage.children.length, 0);
    hint.dispose();
  } finally { delete globalThis.document; delete globalThis.localStorage; }
});

test("setup trial defaults to skip, launches in this repository, and fails soft", async () => {
  const setup = await readFile(new URL("setup.sh", root), "utf8");
  const trial = setup.slice(setup.indexOf("  # R90-U4:"), setup.indexOf('\nsay "⚠️'));
  const prefix = 'HERE="$PWD"\nsay() { :; }\ninfo() { :; }\nclaude() { [ "$PWD" = "$HERE" ] || exit 7; printf "%s\\n" "$@"; return 1; }\n';
  // The block follows a setup-success if; wrap it to preserve its closing fi.
  const script = `${prefix}if true; then\n${trial}`;
  for (const input of ["", "\n", "n\n"]) {
    assert.equal(execFileSync("bash", ["-c", script], { input, encoding: "utf8" }), "");
  }
  const args = execFileSync("bash", ["-c", script], { input: "Y\n", encoding: "utf8" });
  assert.equal(args, "--bg\nREADME を1行で要約し、AskUserQuestion で続けるか聞いて\n");
});
