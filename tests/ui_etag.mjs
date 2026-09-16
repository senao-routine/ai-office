// R96-D3 項目 6: /ui/** の ETag がファイル単位であることの機械ピン。
// 版全体のハッシュを 64 URL 全部に返していたため、1 行直すたびに全端末が raw 2.4MB（gzip 1.07MB）を払い直していた。
// ここが落ちたら「1 本直すと全部再送」に戻っている。
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const { ASSETS, BUILD, ETAGS, MODULES } = await import(join(ROOT, "relay/src/modules_data.js"));
const { uiResponse } = await import(join(ROOT, "relay/src/ui_etag.js"));

const paths = [...Object.keys(MODULES), ...Object.keys(ASSETS)];
assert.ok(paths.length > 20, `/ui/ の URL が ${paths.length} 本しかない`);

// 1) 全 URL に ETag が在り、版全体（BUILD）ではない
for (const p of paths) {
  assert.ok(ETAGS[p], `${p} に ETag が無い`);
  assert.notEqual(ETAGS[p], BUILD, `${p} が版全体の ETag のまま`);
}
// 2) ETag は「URL＋本文」の sha256 先頭 12 桁（生成器と同じ規則＝片方だけ直すとここが落ちる）
const fileId = (url, body) => {
  const h = createHash("sha256");
  h.update(Buffer.from(url, "utf8")); h.update(Buffer.from([0]));
  h.update(Buffer.from(body, "utf8"));
  return h.digest("hex").slice(0, 12);
};
for (const [p, src] of Object.entries(MODULES)) assert.equal(ETAGS[p], fileId(p, src), `${p} の ETag が内容と食い違う`);
for (const [p, a] of Object.entries(ASSETS)) assert.equal(ETAGS[p], fileId(p, a[1]), `${p} の ETag が内容と食い違う`);

// 3) 1 本だけ内容を変えたら、その 1 本の ETag だけが変わる（残りは 304 のまま）
const target = Object.keys(MODULES)[0];
const changed = new Map(paths.map((p) => [p, ETAGS[p]]));
changed.set(target, fileId(target, MODULES[target] + "\n// 1 行足した\n"));
const moved = paths.filter((p) => changed.get(p) !== ETAGS[p]);
assert.deepEqual(moved, [target], `1 本直したのに ${moved.length} 本の ETag が動いた`);

// 4) 応答の契約: 同じ ETag なら 304・版全体の ETag では 304 にしない・404 に ETag を付けない
for (const p of paths.slice(0, 5)) {
  const fresh = uiResponse(p, null);
  assert.equal(fresh.status, 200);
  assert.equal(fresh.etag, `W/"ui-${ETAGS[p]}"`);
  assert.equal(uiResponse(p, fresh.etag).status, 304);
  assert.equal(uiResponse(p, `W/"ui-${BUILD}"`).status, 200, "版全体の ETag で 304 を返している");
}
const missing = uiResponse("/ui/does-not-exist.js", null);
assert.equal(missing.status, 404);
assert.equal(missing.etag, undefined, "404 に ETag を付けている");

// 5) 本文は取り違えない（モジュールは JS・テクスチャは base64＋その MIME）
const modPath = Object.keys(MODULES)[1], modRes = uiResponse(modPath, null);
assert.equal(modRes.body, MODULES[modPath]);
assert.equal(modRes.type, "text/javascript; charset=utf-8");
const texPath = Object.keys(ASSETS)[0];
if (texPath) {
  const texRes = uiResponse(texPath, null);
  assert.equal(texRes.b64, ASSETS[texPath][1]);
  assert.equal(texRes.type, ASSETS[texPath][0]);
}
console.log(`✓ /ui/ の ETag はファイル単位（${paths.length} URL・1 本直しても他は 304）`);
