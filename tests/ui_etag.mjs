// R96-D3 項目 6: /ui/** の ETag がファイル単位であることの機械ピン。
// 版全体のハッシュを 64 URL 全部に返していたため、1 行直すたびに全端末が raw 2.4MB（gzip 1.07MB）を払い直していた。
// ここが落ちたら「1 本直すと全部再送」に戻っている。
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const { ASSETS, BUILD, ETAGS, MODULES } = await import(join(ROOT, "relay/src/modules_data.js"));
const three = await import(join(ROOT, "relay/src/modules_3d.js"));
const { uiResponse, uiVersioned, IMMUTABLE, REVALIDATE } = await import(join(ROOT, "relay/src/ui_etag.js"));

const paths = [...Object.keys(MODULES), ...Object.keys(ASSETS)];
// 辿り方が壊れて空振りしていないかの下限（R99-P4-6 で 3D を外し 17 本＝部屋・ワールド・封書）
assert.ok(paths.length >= 12, `/ui/ の URL が ${paths.length} 本しかない`);

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
// 6) R102-10: 版つきの道 /u/<BUILD>/<rest> は /ui/<rest> と同じ本文。今の版だけ immutable（本文は BUILD で決まる）・古い版は再検証。
//    本人の iPhone は 8 月の immutable の /ui/core/world.js を 2 か月使い続けた＝版の無い道には付けない（REVALIDATE のまま）。
for (const p of Object.keys(MODULES)) {
  const rest = p.slice("/ui/".length);
  const v = uiVersioned(`/u/${BUILD}/${rest}`);
  assert.deepEqual(v, { ui: p, current: true }, `${p} が今の版の道で引けない`);
  assert.equal(uiResponse(v.ui, null).body, MODULES[p]);
}
const old = BUILD === "000000000000" ? "111111111111" : "000000000000";
assert.deepEqual(uiVersioned(`/u/${old}/pwa/bootpx.js`), { ui: "/ui/pwa/bootpx.js", current: false }, "古い版の道が今の版扱い");
for (const bad of ["/u/", `/u/${BUILD}`, `/u/${BUILD}/`, `/u/${BUILD.toUpperCase()}X/pwa/bootpx.js`, "/u/abc/pwa/bootpx.js",
  `/u/${BUILD}/../app/`, `/u/${BUILD}/pwa//bootpx.js`, `/u/${BUILD}/./pwa/bootpx.js`, `/u/${BUILD}/pwa/boot px.js`]) {
  assert.equal(uiVersioned(bad), null, `${bad} を版つきの道として通した`);
}
assert.equal(IMMUTABLE, "public, max-age=31536000, immutable");
assert.equal(REVALIDATE, "public, max-age=0, must-revalidate");

// R122: 3D は独立した版。共通 URL でも各束の本文と ETag を返し、両者を混ぜない。
assert.ok(three.MODULES["/ui/pwa/boot3d.js"]);
assert.ok(Object.keys(three.ASSETS).length > 0);
for (const [path, source] of Object.entries(three.MODULES)) {
  assert.equal(three.ETAGS[path], fileId(path, source));
  const route = `/u/${three.BUILD}/${path.slice(4)}`;
  assert.deepEqual(uiVersioned(route), { ui: path, current: true });
  const response = uiResponse(path, null, three.BUILD);
  assert.equal(response.status, 200); assert.equal(response.body, source);
  assert.equal(response.etag, `W/"ui-${three.ETAGS[path]}"`);
  assert.equal(uiResponse(path, response.etag, three.BUILD).status, 304);
  const legacy = uiResponse(path, null);
  assert.equal(legacy.body, Object.hasOwn(MODULES, path) ? MODULES[path] : source);
  if (Object.hasOwn(MODULES, path)) {
    assert.equal(uiResponse(path, null, BUILD).body, MODULES[path]);
    assert.equal(uiResponse(path, null, BUILD).etag, `W/"ui-${ETAGS[path]}"`);
  }
}
for (const [path, asset] of Object.entries(three.ASSETS)) {
  assert.equal(three.ETAGS[path], fileId(path, asset[1]));
  assert.equal(uiVersioned(`/u/${three.BUILD}/${path.slice(4)}`).current, true);
  const response = uiResponse(path, null, three.BUILD);
  assert.equal(response.status, 200); assert.equal(response.type, asset[0]);
  assert.equal(response.b64, asset[1]); assert.equal(response.etag, `W/"ui-${three.ETAGS[path]}"`);
  assert.equal(uiResponse(path, response.etag, three.BUILD).status, 304);
  assert.equal(uiResponse(path, null).b64, asset[1]);
}
assert.equal(uiVersioned(`/u/${BUILD}/pwa/boot3d.js`).current, false, "通常束の版で3D本文をimmutableにした");
assert.equal(uiVersioned(`/u/${three.BUILD}/pwa/bootpx.js`).current, false, "3D束の版でドット絵本文をimmutableにした");
console.log(`✓ /ui/ の ETag はファイル単位（${paths.length} URL・1 本直しても他は 304）・版つきの道 /u/<BUILD>/ は今の版だけ immutable`);
console.log(`✓ 3D の別束（${Object.keys(three.MODULES).length} JS・${Object.keys(three.ASSETS).length} assets）は独立した版・ETag・本文で配信`);
