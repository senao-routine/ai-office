// R87 両側KAT: Python(CommonCrypto) が封じた**固定の**封書を、
// **PWA 本番と同じ ui/core/dialog_open.js** が開けることを固定する。
// これが「片側だけ直して静かに復号できなくなる」窓を塞ぐ。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { openBlob, FRAME_LEN, MAX_BUNDLE_AGE } from "../ui/core/dialog_open.js";

const K = JSON.parse(readFileSync(new URL("./fixtures/dialog_seal_kat.json", import.meta.url)));
const open = (bundle = K.bundle, dev = K.deviceId, sess = K.targetSession, secret = K.secretHex) =>
  openBlob(bundle, secret, dev, sess);

test("固定ベクタを本番コードが開く（Mac が封じ、端末が読む）", async () => {
  assert.deepEqual(await open(), K.page);
});

test("フレーム長は 3+12+8192+16 で固定（長さは 8KB 粒度までしか漏れない）", () => {
  const raw = Buffer.from(K.bundle.b.replace(/-/g, "+").replace(/_/g, "/"), "base64");
  assert.equal(FRAME_LEN, 8223);
  assert.equal(raw.length, K.bundle.n * FRAME_LEN);
  assert.equal(raw.subarray(0, 3).toString(), "AO1");
});

test("暗号文・タグ・salt のどれを1文字変えても開かない", async () => {
  const flip = (s, i) => s.slice(0, i) + (s[i] === "A" ? "B" : "A") + s.slice(i + 1);
  for (const [label, bundle] of [
    ["暗号文", { ...K.bundle, b: flip(K.bundle.b, 40) }],
    ["末尾のタグ", { ...K.bundle, b: flip(K.bundle.b, K.bundle.b.length - 2) }],
    ["salt", { ...K.bundle, s: flip(K.bundle.s, 3) }],
  ]) {
    await assert.rejects(() => open(bundle), undefined, label);
  }
});

test("AAD が縛るもの — 端末・要求・対象セッション・番号・時刻", async () => {
  await assert.rejects(() => open(K.bundle, "dev-other-0001"), undefined, "別端末では開かない");
  await assert.rejects(() => open(K.bundle, K.deviceId, "sess-other"), undefined,
    "別セッションのふりでは開かない");
  await assert.rejects(() => open({ ...K.bundle, id: "f".repeat(32) }), undefined,
    "別要求への転用は開かない");
  await assert.rejects(() => open({ ...K.bundle, i: K.iat + 1 }), undefined,
    "時刻を書き換えると開かない");
  await assert.rejects(() => open({ ...K.bundle, n: 2 }), undefined, "フレーム数の詐称は開かない");
});

test("別の端末秘密では開かない（紛失していない端末は無関係）", async () => {
  await assert.rejects(() => open(K.bundle, K.deviceId, K.targetSession, "aa".repeat(32)));
});

test("形が違う封書は理由つきで断る（黙って空を返さない）", async () => {
  for (const [bundle, re] of [
    [null, /no bundle/],
    [{ err: "denied" }, /refused:denied/],
    [{ ...K.bundle, v: 2 }, /unsupported/],
    [{ ...K.bundle, c: "gz" }, /unsupported/],
    [{ ...K.bundle, n: 99 }, /bad n/],
    [{ ...K.bundle, i: "いつか" }, /bad iat/],
    [{ ...K.bundle, b: K.bundle.b.slice(0, 100) }, /frame length/],
  ]) {
    await assert.rejects(() => open(bundle), re);
  }
});

test("期限（e）は呼び手が渡す時刻で見る（渡さなければ固定ベクタのまま開く＝過去の e でも KAT は生きる）", async () => {
  // 別モデルレビュー（2026-09-14）: 中継の行が Mac の後続 sync 無しに残ると、端末が 5 分の要求窓から
  // 期限切れの会話を開けた。core は時計を持たないので、PWA が Date.now() を秒で渡す。
  assert.deepEqual(await openBlob(K.bundle, K.secretHex, K.deviceId, K.targetSession, K.bundle.e - 1), K.page);
  await assert.rejects(
    () => openBlob(K.bundle, K.secretHex, K.deviceId, K.targetSession, K.bundle.e + 1), /refused:expired/);
  await assert.rejects(
    () => openBlob({ ...K.bundle, e: "later" }, K.secretHex, K.deviceId, K.targetSession, 1), /refused:expired/);
});

test("期限は認証済みの iat に結び付く（AAD 外の e を未来へ書き換えても、iat＋最長寿命を過ぎていれば開かない）", async () => {
  assert.equal(K.bundle.e - K.bundle.i, MAX_BUNDLE_AGE);                 // Mac 側 BUNDLE_TTL と同じ寿命
  const forged = { ...K.bundle, e: K.bundle.i + 10 * 365 * 86400 };      // 中継の輸送トークン保持者が e だけ延ばした
  await assert.rejects(
    () => openBlob(forged, K.secretHex, K.deviceId, K.targetSession, K.bundle.i + MAX_BUNDLE_AGE + 1), /refused:expired/);
  assert.deepEqual(await openBlob(forged, K.secretHex, K.deviceId, K.targetSession, K.bundle.i + MAX_BUNDLE_AGE), K.page);
});
