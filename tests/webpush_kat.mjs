// P7 Web Push 暗号の KAT（node のみ・wrangler不要）
//   1) RFC 8291 Appendix A の公式テストベクタで encryptAes128Gcm の出力をバイト一致検査
//      （salt / AS一時鍵を注入 → 出力は決定論）
//   2) VAPID(RFC 8292): 生成した JWT を WebCrypto の ECDSA 検証で自己検証 + aud/exp/形式検査
// 実行: node tests/webpush_kat.mjs   （relay_e2e.sh / RUN_RELAY=1 verify.sh から呼ばれる）
import { b64u, b64uDecode, encryptAes128Gcm, vapidAuthorization, jwkToRawPub }
  from "../relay/src/webpush.js";

let fails = 0;
const ng = (m) => { console.error("  ✗ " + m); fails++; };
const ok = (m) => console.log("  ✓ " + m);

// ---- 1) RFC 8291 Appendix A ----
const V = {
  plaintext: "When I grow up, I want to be a watermelon",
  asPrivD: "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
  asPub: "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
  uaPub: "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
  auth: "BTBZMqHH6r4Tts7J_aSIgg",
  salt: "DGv6ra1nlYgDCS1FRnbzlw",
  expected: "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
};
const asPubBytes = b64uDecode(V.asPub);
const asJwk = {
  kty: "EC", crv: "P-256", d: V.asPrivD,
  x: b64u(asPubBytes.slice(1, 33)), y: b64u(asPubBytes.slice(33, 65)),
};
const body = await encryptAes128Gcm(
  new TextEncoder().encode(V.plaintext), V.uaPub, V.auth,
  { salt: b64uDecode(V.salt), asPrivateJwk: asJwk });
if (b64u(body) === V.expected) ok("RFC8291 Appendix A ベクタ一致 (aes128gcm本文バイト一致)");
else ng(`RFC8291 ベクタ不一致:\n    got ${b64u(body)}\n    exp ${V.expected}`);

// ---- 2) VAPID 自己検証 ----
const kp = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
const jwk = await crypto.subtle.exportKey("jwk", kp.privateKey);
const hdr = await vapidAuthorization("https://web.push.apple.com/QOX9token", jwk, "https://relay.example");
const m = /^vapid t=([^,]+), k=(.+)$/.exec(hdr);
if (!m) { ng("Authorizationヘッダ形式が vapid t=..., k=... でない"); }
else {
  const [h, p, s] = m[1].split(".");
  const dec = (x) => JSON.parse(Buffer.from(x.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString());
  const head = dec(h), pay = dec(p);
  if (head.alg !== "ES256" || head.typ !== "JWT") ng(`JWTヘッダ不正: ${JSON.stringify(head)}`);
  else ok("JWTヘッダ ES256/JWT");
  if (pay.aud !== "https://web.push.apple.com") ng(`aud不正: ${pay.aud}`);
  else ok("aud=push配送先origin");
  const life = pay.exp - Math.floor(Date.now() / 1000);
  if (life <= 0 || life > 24 * 3600) ng(`exp不正(24h超はRFC8292違反): ${life}s`);
  else ok(`exp=+${Math.round(life / 3600)}h (≤24h)`);
  const verified = await crypto.subtle.verify(
    { name: "ECDSA", hash: "SHA-256" }, kp.publicKey,
    b64uDecode(s), new TextEncoder().encode(`${h}.${p}`));
  if (verified) ok("ES256署名の自己検証OK");
  else ng("ES256署名の自己検証NG");
  if (m[2] === b64u(jwkToRawPub(jwk))) ok("k=VAPID公開鍵(65バイト非圧縮)");
  else ng("k がVAPID公開鍵と不一致");
}

if (fails) { console.error(`❌ webpush KAT ${fails}件失敗`); process.exit(1); }
console.log("✅ webpush KAT 全合格");
