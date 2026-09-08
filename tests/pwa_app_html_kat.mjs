// R90-P2: 方向Cへの意図的な変更後の配信バイト列を固定する。
// 更新: python3 tools/gen_pwa_modules.py → APP_HTMLのUTF-8 SHA-256/byteLengthを
// node:crypto/node:bufferで取得 → 下の2定数をレビューして手で更新 → このKATを実行。
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { APP_HTML } from "../relay/src/app_html.js";
import { MODULES, ASSETS } from "../relay/src/modules_data.js";

const EXPECT_SHA256 = "a12d3bc40b70579882c0758fa32d65d24341c94533b80941689ac5659828c9e3";
const EXPECT_BYTES = 106072;   // R90-P2: ▶実行タブの id 属性修正（tb_run hidden_lb → tb_run_lb）
const actual = createHash("sha256").update(APP_HTML, "utf8").digest("hex");
assert.equal(actual, EXPECT_SHA256, "APP_HTML が方向Cの配信バイト列と不一致");
assert.equal(Buffer.byteLength(APP_HTML, "utf8"), EXPECT_BYTES);

// gloss_parity が読む Worker 側の関数と、実際に配信する関数の同期も守る。
const worker = readFileSync(new URL("../relay/src/worker.js", import.meta.url), "utf8");
const begin = worker.indexOf("// PWA_GLOSS_BEGIN");
const end = worker.indexOf("// PWA_GLOSS_END");
assert.ok(begin >= 0 && end > begin, "PWA_GLOSS markers not found in worker.js");
const gloss = runInNewContext(worker.slice(begin, end) +
  '\ntidyActivityPWA.toString() + "\\n" + activityGlossPWA.toString();', {}, { timeout: 1000 });
assert.ok(APP_HTML.includes(gloss), "配信HTMLとPWA_GLOSS関数が不一致");
console.log(`APP_HTML SHA-256 一致: ${actual} (${EXPECT_BYTES} bytes)`);

// 配信する閉包に旧シーン/旧テクスチャが戻るとサイズと見た目が両方退行する。
assert.ok(MODULES["/ui/iso/scene3d.js"]);
assert.ok(MODULES["/ui/core/tier.js"] && MODULES["/ui/platform/growth.js"]);
assert.ok(ASSETS["/ui/iso/tex/oak_floor.webp"]);
assert.ok([...Object.keys(MODULES), ...Object.keys(ASSETS)].every((url) => !/^\/ui\/iso[^/]+\//.test(url)));

// isoの既存URL APIに渡す品質と、正常時/例外時のURL復元。WebGLは不要。
const boot = MODULES["/ui/pwa/boot3d.js"];
const init = boot.slice(0, boot.indexOf("if (host && scene)"))
  .replace(/^import .*;$/gm, "");
for (const fail of [false, true]) {
  const location = new URL("https://office.example/app?quality=high&t=3.2#fixture");
  const original = location.href;
  const events = [];
  let constructed = false;
  const state = { fixture: true };
  runInNewContext(init, {
    location, URL, history: { state, replaceState(saved, _, href) {
      assert.equal(saved, state);
      location.href = href;
    } },
    document: { getElementById: () => ({}), dispatchEvent: (event) => events.push(event.type) },
    CustomEvent: class { constructor(type) { this.type = type; } },
    IsoScene: class { constructor(_, options) {
      constructed = true;
      assert.equal(options.quality, "mobile");
      assert.equal(location.searchParams.get("quality"), "mobile");
      assert.equal(location.searchParams.get("t"), "3.2");
      if (fail) throw new Error("WebGL fixture failure");
    } },
  });
  assert.ok(constructed);
  assert.equal(location.href, original);
  assert.deepEqual(events, fail ? ["scene3d-failed"] : []);
}

// 受信値だけが変わる更新、欠けた成長値、レシピ追加/削除を固定する。
const source = readFileSync(new URL("../ui/pwa/app.js", import.meta.url), "utf8");
const helpers = source.slice(source.indexOf("function hudData("), source.indexOf("function updateAttnCards("));
const runTab = {};
const office = { employees: [{ session: "s1", detail: "受信した詳細", vendor: "codex" }],
  growth: { byProject: { p1: { level: 0 } } }, actions: {} };
const hud = runInNewContext(gloss + "\n" + helpers +
  "\n({detailOf,vendorOf,levelOf,askSummary,syncRunTab})", {
    LANG: "ja", T: (ja) => ja, LAST_OFFICE: office, actionsView: () => office.actions,
    document: { getElementById: () => runTab },
  });
const agent = { session: "s1", projectId: "p1", approvalMin: 3,
  ask: { kind: "permission", tool: "Bash" } };
assert.equal(hud.askSummary(agent), "Bash の許可・3分");
assert.equal(hud.askSummary({ ask: { kind: "question" } }), "質問への回答・0分");
assert.equal(hud.vendorOf(agent), "codex");
assert.equal(hud.vendorOf({ vendor: "unrecognized" }), "other");
assert.equal(hud.detailOf(agent), "受信した詳細");
assert.equal(hud.detailOf({ ...agent, detail: "最新の詳細" }), "最新の詳細");
assert.equal(hud.levelOf(agent), 0);
assert.equal(hud.levelOf({ ...agent, level: 7 }), 7);
assert.equal(hud.levelOf({ session: "missing" }), null);
for (const recipes of [undefined, [], [{ id: "r1" }], []]) {
  office.actions.recipes = recipes;
  hud.syncRunTab();
  assert.equal(runTab.hidden, !recipes?.length);
}
console.log("PWA方向C: isoのみ・mobile品質/URL復元・detail/Lv/レシピ表示 OK");
