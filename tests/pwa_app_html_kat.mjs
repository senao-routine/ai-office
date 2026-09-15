// R90-P2: 方向Cへの意図的な変更後の配信バイト列を固定する。
// 更新: python3 tools/gen_pwa_modules.py → APP_HTMLのUTF-8 SHA-256/byteLengthを
// node:crypto/node:bufferで取得 → 下の2定数をレビューして手で更新 → このKATを実行。
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { APP_HTML } from "../relay/src/app_html.js";
import { MODULES, ASSETS } from "../relay/src/modules_data.js";

const EXPECT_SHA256 = "5dd5c9374feb07f20ff24dd0759cf364bbdd11e0f9c79d931deb20e0c4396b57";
// 2026-09-10 (R87-H1): 設定シートに「🔐 暗号セルフテスト」を1行と、その実行関数を追加（+2,532B）。
// 固定ベクタ(約11KB・base64は圧縮が効かない)は**シェルに入れず** ui/core/dialog_kat.js へ置き、
// 遅延読み込みの modules_data.js 側へ載せた（起動のたびに払う転送量にしない）。
// 2026-09-11: 封書の入口を 3D から切り離し、必要になった時だけ読む ensureDlg() を追加
// （リスト表示を保存した端末では boot3d.js が読まれず、暗号が正常でも詰んでいた）。
// 2026-09-14 (R87-S5): シートに「💬 会話を見る」と会話ブロック #shdlg、封書の受け口 onDlg/requestDialog、
// 設定シートに正直な安全性の注記を追加（+6140B）。平文はメモリ DLG_PAGES だけ。
// 2026-09-14 (R87-S6 別モデルレビュー): 失敗表示を要求元セッションに限定（dlgFail）・完了判定を reqId に・
// 期限 e を端末でも検算（openBlob に nowSec）。既に開いた会話は残して下に添える（+517B）。
// 2026-09-14 (R87-S6 再レビュー): ボタンは封じられる Claude セッションだけ（canDlgFor）・最新の要求だけが表示と
// キャッシュを更新（DLG_LATEST）・期限は認証済み iat＋90 秒で検算（+665B）。
// 2026-09-14 (R87-S6 3 回目): 「最新の要求か」は復号の完了時に取り直す・60 秒タイマーも最新の要求だけ（+159B）。
// 2026-09-14 (R87-S6 4 回目): 「最新の要求か」は要求時刻 at で判定（再読込後も保留要求から復元）・認証失効で本文 DOM も消し
// シートを閉じ、進行中の復号は世代（DLG_EPOCH）で捨てる（+959B）。
// 2026-09-14 (R87-S6 5 回目): 「最新の結果」は成功・失敗・未着で同じ時刻台帳 DLG_AT に記録（+119B）。
// 2026-09-14 (R87-S6 6 回目): 送信失敗の表示にも最新要求＋認証世代の判定（+148B）。
// 2026-09-14 (R87-S6 7 回目): 保留要求は sessionStorage（タブごと）＝別タブが横取りしない（+328B）。
// 2026-09-14 (R93 着地): R87 の app.js/app.html に R93-V1' の HUD トークン（白×ラベンダー×ガラス）を再生成で合流（+354B）。
// 2026-09-14 (R93-P1'): theme-color を #f4f4fb へ・設定のテーマ説明を「白×ラベンダーのガラス面」へ（+61B）。
// 2026-09-15 (R96): 配達5状態・❗1件巡回・Mac許可の手がかり・通知sessionの深リンク・
// 日次集計の正本化と名札密度を追加し、動かないテーマ行を撤去（+4,215B）。
// キャッシュから深リンクを開く前に会話状態も初期化するため、boot() は末尾へ移動。
// 2026-09-15 (R96 着地): 通知の深リンクはキャッシュでは開かず最新の受信で開く（openAttnLink(false) 撤去・-20B）。
const EXPECT_BYTES = 123118;
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

// R87: 封書の入口は**3Dから独立して**配信されること。3D に相乗りさせると、
// リスト表示を保存した端末では sceneShell3D() が呼ばれず永久に読み込まれない。
assert.ok(MODULES["/ui/pwa/dlg.js"], "dlg.js が同梱されていない");
assert.ok(MODULES["/ui/core/dialog_open.js"] && MODULES["/ui/core/dialog_kat.js"]);
assert.ok(!MODULES["/ui/pwa/boot3d.js"].includes("__dlg"),
  "封書の入口が boot3d(3D) に紛れている＝リスト表示で詰む");

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
