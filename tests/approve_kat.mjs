// R100-1b: スマホから許可の固定値（JS 側）。tests/test_approve_kat.py（Python・Mac 側）と同じ値に凍結する。
// ui/pwa/app.js の apvCanonical（署名対象の並び）と apvB64u を**実ファイルから**読み、固定の入力で challenge を作って比べる
// （tests/js_sign_kat.mjs と同じ型＝書き写した関数を試して安心しない）。値を変えるなら Python・JS・docs を同時に。
import assert from "node:assert/strict";
import { createHash, webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const src = readFileSync(new URL("../ui/pwa/app.js", import.meta.url), "utf8");
const grab = (name) => {
  const i = src.indexOf(`function ${name}(`);
  assert.ok(i >= 0, `${name} が app.js に無い`);
  let depth = 0;
  for (let j = src.indexOf("{", i); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(i, j + 1);
  }
  throw new Error(`${name} の終わりが見つからない`);
};
const ctx = { btoa: (s) => Buffer.from(s, "binary").toString("base64"), Uint8Array, String };
runInNewContext(`${grab("apvCanonical")}\n${grab("apvB64u")}\n${grab("apvRemoteCanonical")}\n${grab("apvTapPayload")}\nthis.apvCanonical = apvCanonical; this.apvB64u = apvB64u; this.apvRemoteCanonical = apvRemoteCanonical; this.apvTapPayload = apvTapPayload;`, ctx);

const KAT = {
  tool: "Bash", input: { command: "npm test", description: "テストを実行" },
  device: "d_0123456789ab", session: "3f2a9c1e-7b4d-4e21-9a0c-5d6e7f809102",
  askId: "0123456789abcdef0123456789abcdef", reqId: "fedcba9876543210fedcba9876543210", iat: 1790000000,
  canon: '{"input":{"command":"npm test","description":"テストを実行"},"tool":"Bash"}',
  sha: "83ed35af0246956ae019212ff07d19ff5b8c9a03cf0dedd1ba9f2eadc72afea1",
  challenge: "1c17f070a8687eb882e67fd738b2cab81b06d78655bf08a3691fd22b12c5cf75",
  challengeB64u: "HBfwcKhofriC5n_XOLLKuBsG14ZVvwijaR_SKxLFz3U",
};

// 端末は Mac が封じた inputCanon を画面の <pre> に置き、その文字列から SHA-256 を作り直す（JS の UTF-8 が Python と同じ）
const sha = createHash("sha256").update(KAT.canon, "utf8").digest("hex");
assert.equal(sha, KAT.sha, "inputCanon の SHA-256 が Mac と違う");
const line = ctx.apvCanonical(KAT.device, KAT.session, KAT.askId, KAT.tool, sha, KAT.iat, KAT.reqId);
assert.equal(line.split("\n").length, 10, "canonical は 10 行（区切りは改行・iat は封じた時刻）");
const chal = Buffer.from(await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(line)));
assert.equal(chal.toString("hex"), KAT.challenge, "challenge が Mac と違う（apvCanonical の並びか区切りが変わった）");
assert.equal(ctx.apvB64u(chal), KAT.challengeB64u, "clientDataJSON に入る challenge（base64url）が違う");
// JSON の並び（キーは整列・区切りは詰める）は Mac の json.dumps(sort_keys, separators) と同じ＝Python 側の KAT が担う
// R103-1: スマホから ON（rst-）の内側の署名対象。Mac の office_approve.remote_canonical と同じ並び（tests/test_approve_kat.py の固定値と同じ）
const rline = ctx.apvRemoteCanonical("d_0123456789ab", "a".repeat(32), true, 4, 1790000000);
assert.equal(rline, "aioffice-remote\nv1\nd_0123456789ab\n" + "a".repeat(32) + "\n1\n4\n1790000000", "ON の署名対象の並びが Mac と違う");
assert.equal(ctx.apvRemoteCanonical("d_0123456789ab", "a".repeat(32), false, 4, 1790000000).split("\n").slice(4, 6).join(","), "0,0",
  "OFF の署名対象は on=0・hours=0");
// R114-P（本人裁定 2026-10-08「ON の間はタップだけ」）: ON の間の remote・review は WebAuthn を呼ばず、端末のペアリング鍵の HMAC 封筒（外側の
// canonical は不変＝text を sha256hex に畳む）だけで送る。本文の形は Mac の R114-S と同じ（mode:"tap"・キーの並びはこの順・WebAuthn の 4 項目は無い）。
// mode を省いた本文は従来の sig（上の challenge の KAT）。ここは app.js の apvTapPayload を実ファイルから読んで固定値と比べる
const TAP_KEYS = ["aioffice", "kind", "mode", "reqId", "session", "askId", "tool", "inputSha", "iat", "decision"];
const TAP_TEXT = '{"aioffice":1,"kind":"approve","mode":"tap","reqId":"fedcba9876543210fedcba9876543210",' +
  '"session":"3f2a9c1e-7b4d-4e21-9a0c-5d6e7f809102","askId":"0123456789abcdef0123456789abcdef","tool":"Bash",' +
  '"inputSha":"83ed35af0246956ae019212ff07d19ff5b8c9a03cf0dedd1ba9f2eadc72afea1","iat":1790000000,"decision":"allow"}';
const TAP_TEXT_SHA = "3ab03135d3b12647ecd58571df0c1afcaa8bd7ea92bff216231d4d667d025691";
const tap = ctx.apvTapPayload(KAT.reqId, KAT.session, KAT.askId, KAT.tool, sha, KAT.iat);
assert.deepEqual(Object.keys(tap), TAP_KEYS, "タップの本文のキーの集合・並びが Mac（R114-S）と違う");
for (const k of ["credId", "authData", "clientDataJSON", "signature"]) assert.ok(!(k in tap), `タップの本文に WebAuthn の ${k} がある`);
assert.equal(tap.mode, "tap");
assert.equal(tap.decision, "allow");
assert.ok(Number.isInteger(tap.iat), "iat は整数（Mac は int で見る）");
const tapText = JSON.stringify(tap);
assert.equal(tapText, TAP_TEXT, "タップの本文（封筒の text）が固定値と違う");
assert.equal(createHash("sha256").update(tapText, "utf8").digest("hex"), TAP_TEXT_SHA, "外側の canonical に畳まれる sha256hex(text) が違う");
console.log(`approve KAT 一致: challenge ${KAT.challenge.slice(0, 16)}…・tap の本文 ${TAP_TEXT_SHA.slice(0, 16)}…（apvCanonical・apvB64u・apvTapPayload は app.js の実物）`);
