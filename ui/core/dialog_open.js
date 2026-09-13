// R87「封書」— 端末側で会話を開く。server/dialog_seal.py と**必ず一対**。
//
// ここは ui/core なので DOM も通信も触らない。使うのは WebCrypto（decrypt は決定論）と
// DecompressionStream だけ。node からもそのまま import できる＝KAT が「本番と同じコード」を検証する。
//
// ★JS 側の HKDF は deriveBits({name:"HKDF"}) を使わず sign("HMAC") で手書きする。
//   理由は性能ではなくリスク集中: HMAC-SHA256 は本番の sign() が iOS 実機で毎日動かしている
//   プリミティブ。新たに賭ける対象を decrypt({name:"AES-GCM"}) の1個だけに絞る。

const MAGIC = [0x41, 0x4f, 0x31];        // "AO1"
const IV_LEN = 12, TAG_LEN = 16, BLOCK = 8192;
export const FRAME_LEN = MAGIC.length + IV_LEN + BLOCK + TAG_LEN;   // 8223
export const MAX_FRAMES = 4;
const LABEL = "aioffice-dialog\nv1\n";
const ROOT_LABEL = "aioffice-dialog-root/v1";
const COMPRESSION = "zl";

const enc = new TextEncoder();

function hexToBytes(hex) {
  if (typeof hex !== "string" || hex.length % 2 || /[^0-9a-fA-F]/.test(hex)) {
    throw new Error("bad secret");
  }
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

export function unb64u(text) {
  const s = String(text).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(s + "=".repeat((4 - (s.length % 4)) % 4));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function hmac(keyBytes, msgBytes) {
  const k = await crypto.subtle.importKey("raw", keyBytes, { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, msgBytes));
}

function concat(...parts) {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

/** RFC5869。**1ブロック(32B)だけ**＝keystream 用途に流用しない（Python 側と同じ制限）。 */
export async function deriveKey(secretHex, salt, deviceId, reqId) {
  const root = await hmac(hexToBytes(secretHex), enc.encode(ROOT_LABEL));
  const prk = await hmac(salt, root);
  const info = enc.encode(`${LABEL}${deviceId}\n${reqId}`);
  const okm = await hmac(prk, concat(info, new Uint8Array([1])));
  return okm.slice(0, 32);
}

export function aadFor(deviceId, reqId, targetSession, index, frames, iat) {
  return enc.encode(
    `${LABEL}${deviceId}\n${reqId}\n${targetSession}\n${index}\n${frames}\n${iat}\n${COMPRESSION}`);
}

async function inflate(bytes) {
  // ★zlib.compress は RFC1950。"gzip" ではなく "deflate" を指定する（間違えると静かに壊れる）
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/**
 * 封書を開く。失敗は理由つきで throw する（黙って空を返さない＝画面が嘘をつかない）。
 * bundle は中継から来る他人の入力なので、形は全部ここで確かめてから使う。
 */
/** 封書が生きていられる最長秒数（Mac 側 `dialog_seal.BUNDLE_TTL` の既定 90。env で短くはできるが長くはできない）。 */
export const MAX_BUNDLE_AGE = 90;

export async function openBlob(bundle, secretHex, deviceId, targetSession, nowSec = null) {
  if (!bundle || typeof bundle !== "object") throw new Error("no bundle");
  if (bundle.err) throw new Error(`refused:${bundle.err}`);
  // 期限（e）は中継や Mac の後続 sync に頼らず端末でも見る。時計は呼び手が渡す（core は時刻を持たない）。
  if (nowSec !== null) {
    const exp = Number(bundle.e);
    if (!Number.isInteger(exp) || exp < nowSec) throw new Error("refused:expired");
  }
  if (bundle.v !== 1 || bundle.c !== COMPRESSION) throw new Error("unsupported bundle");
  const frames = Number(bundle.n);
  if (!Number.isInteger(frames) || frames < 1 || frames > MAX_FRAMES) throw new Error("bad n");
  const iat = Number(bundle.i);
  if (!Number.isInteger(iat)) throw new Error("bad iat");

  const key = await crypto.subtle.importKey(
    "raw", await deriveKey(secretHex, unb64u(bundle.s), deviceId, bundle.id),
    { name: "AES-GCM" }, false, ["decrypt"]);
  const raw = unb64u(bundle.b);
  if (raw.length !== frames * FRAME_LEN) throw new Error("frame length mismatch");

  const parts = [];
  for (let i = 0; i < frames; i++) {
    const f = raw.subarray(i * FRAME_LEN, (i + 1) * FRAME_LEN);
    for (let m = 0; m < MAGIC.length; m++) {
      if (f[m] !== MAGIC[m]) throw new Error("bad magic");
    }
    const iv = f.subarray(MAGIC.length, MAGIC.length + IV_LEN);
    const body = f.subarray(MAGIC.length + IV_LEN);          // 暗号文 + tag（WebCrypto は連結を期待）
    const aad = aadFor(deviceId, bundle.id, targetSession, i, frames, iat);
    parts.push(new Uint8Array(await crypto.subtle.decrypt(
      { name: "AES-GCM", iv, additionalData: aad, tagLength: TAG_LEN * 8 }, key, body)));
  }
  const plain = concat(...parts);
  const size = new DataView(plain.buffer, plain.byteOffset, 4).getUint32(0);
  if (size > plain.length - 4) throw new Error("bad length prefix");
  // 期限の本体は**認証済み**の iat（AAD に入っている）＋最長寿命。`e` は AAD 外なので、中継の輸送トークン
  // 保持者が e だけ未来に書き換えて再投稿しても、ここで落ちる（別モデルレビュー 2026-09-14）。
  if (nowSec !== null && iat + MAX_BUNDLE_AGE < nowSec) throw new Error("refused:expired");
  const json = await inflate(plain.subarray(4, 4 + size));
  return JSON.parse(new TextDecoder().decode(json));
}
