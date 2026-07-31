// ===== P7: Web Push（RFC 8291 aes128gcm 暗号化 + RFC 8292 VAPID）=====
// 暗号は全部ここ（Worker/WebCrypto）で行う＝Mac側 server/ は無変更（stdlib不変条件を守る）。
// ECDH P-256 / HKDF-SHA256 / AES-128-GCM / ECDSA ES256 は全て crypto.subtle のネイティブ。
// 回帰固定: tests/webpush_kat.mjs が RFC 8291 Appendix A の公式テストベクタで
// encryptAes128Gcm の出力をバイト一致検査する（salt/ephemeral鍵はテスト注入可能にしてある）。

const te = new TextEncoder();

export function b64u(bytes) {
  let s = "";
  const u = new Uint8Array(bytes);
  for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64uDecode(str) {
  const s = String(str).replace(/-/g, "+").replace(/_/g, "/");
  const pad = s.length % 4 ? "=".repeat(4 - (s.length % 4)) : "";
  const bin = atob(s + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// JWK(P-256) → 非圧縮公開鍵65バイト（0x04 || x || y）
export function jwkToRawPub(jwk) {
  const x = b64uDecode(jwk.x), y = b64uDecode(jwk.y);
  const out = new Uint8Array(65);
  out[0] = 0x04; out.set(x, 1); out.set(y, 33);
  return out;
}

async function hkdf(salt, ikm, info, bits) {
  const key = await crypto.subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
  return new Uint8Array(await crypto.subtle.deriveBits(
    { name: "HKDF", hash: "SHA-256", salt, info }, key, bits));
}

// RFC 8291: payload を購読者の鍵(p256dh/auth)で aes128gcm 暗号化し、
// RFC 8188 のボディ（salt|rs|idlen|as_public|ciphertext）を返す。
// opts.salt / opts.asPrivateJwk は KAT 用の注入口（本番は毎回ランダム生成）。
export async function encryptAes128Gcm(payloadBytes, p256dhB64u, authB64u, opts = {}) {
  const uaPub = b64uDecode(p256dhB64u);            // 65バイト非圧縮
  const authSecret = b64uDecode(authB64u);         // 16バイト
  if (uaPub.length !== 65 || uaPub[0] !== 4) throw new Error("bad p256dh");
  if (authSecret.length !== 16) throw new Error("bad auth");

  // AS(送信側)一時鍵: KAT時は注入・本番は使い捨て生成
  let asPriv, asPubRaw;
  if (opts.asPrivateJwk) {
    asPriv = await crypto.subtle.importKey("jwk", opts.asPrivateJwk,
      { name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
    asPubRaw = jwkToRawPub(opts.asPrivateJwk);
  } else {
    const kp = await crypto.subtle.generateKey(
      { name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
    asPriv = kp.privateKey;
    asPubRaw = new Uint8Array(await crypto.subtle.exportKey("raw", kp.publicKey));
  }
  const salt = opts.salt ? new Uint8Array(opts.salt) : crypto.getRandomValues(new Uint8Array(16));

  const uaKey = await crypto.subtle.importKey("raw", uaPub,
    { name: "ECDH", namedCurve: "P-256" }, false, []);
  const ecdh = new Uint8Array(await crypto.subtle.deriveBits(
    { name: "ECDH", public: uaKey }, asPriv, 256));

  // RFC 8291 §3.3-3.4 の鍵導出チェーン
  const keyInfo = new Uint8Array([...te.encode("WebPush: info"), 0, ...uaPub, ...asPubRaw]);
  const ikm = await hkdf(authSecret, ecdh, keyInfo, 256);
  const cek = await hkdf(salt, ikm, te.encode("Content-Encoding: aes128gcm\0"), 128);
  const nonce = await hkdf(salt, ikm, te.encode("Content-Encoding: nonce\0"), 96);

  // 単一レコード: payload || 0x02（最終レコードのデリミタ・追加パディング無し）
  const record = new Uint8Array(payloadBytes.length + 1);
  record.set(payloadBytes, 0);
  record[payloadBytes.length] = 0x02;
  const aesKey = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  const ct = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce }, aesKey, record));

  // RFC 8188 ヘッダブロック: salt(16) | rs(4=4096) | idlen(1=65) | keyid(as_public 65)
  const body = new Uint8Array(16 + 4 + 1 + 65 + ct.length);
  body.set(salt, 0);
  new DataView(body.buffer).setUint32(16, 4096);
  body[20] = 65;
  body.set(asPubRaw, 21);
  body.set(ct, 86);
  return body;
}

// RFC 8292 VAPID: 対象push配送先originに対する ES256 JWT を作り Authorization ヘッダ値を返す。
// sub は連絡先（https: or mailto:）。本人情報を出さないため Worker の origin URL を渡す運用。
export async function vapidAuthorization(endpoint, vapidJwk, subContact) {
  const aud = new URL(endpoint).origin;
  const header = b64u(te.encode(JSON.stringify({ typ: "JWT", alg: "ES256" })));
  const payload = b64u(te.encode(JSON.stringify({
    aud, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: subContact })));
  const signingKey = await crypto.subtle.importKey("jwk",
    { ...vapidJwk, key_ops: ["sign"], ext: true },
    { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" }, signingKey, te.encode(`${header}.${payload}`)));
  const jwt = `${header}.${payload}.${b64u(sig)}`;   // WebCryptoのECDSA出力=r||s 64バイト=JOSE形式そのまま
  return `vapid t=${jwt}, k=${b64u(jwkToRawPub(vapidJwk))}`;
}

// 1購読へ送信。成功/失敗のHTTP statusを返す（404/410=購読失効→呼び出し側が台帳から削除）。
// ttlSec: push サービス側の保持秒。❗通知は既定3600（端末が圏外/スリープでも1時間内に届く）。
// /push/test だけ 60 を渡す（テストが1時間後に化けて届くと紛らわしい）。ヘッダのみ＝暗号化は無改変。
export async function sendWebPush(subscription, payloadObj, vapidJwk, subContact, ttlSec = 3600) {
  const body = await encryptAes128Gcm(
    te.encode(JSON.stringify(payloadObj)),
    subscription.keys.p256dh, subscription.keys.auth);
  const resp = await fetch(subscription.endpoint, {
    method: "POST",
    headers: {
      "Content-Encoding": "aes128gcm",
      "Content-Type": "application/octet-stream",
      "TTL": String(ttlSec),
      "Urgency": "high",
      "Authorization": await vapidAuthorization(subscription.endpoint, vapidJwk, subContact),
    },
    body,
  });
  return resp.status;
}
