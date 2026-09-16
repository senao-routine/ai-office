// R96-D3 項目 6: /ui/** の応答（ETag と本文）を決める純関数。
//
// なぜ切り出すか: worker.js は "cloudflare:workers" を import するので node から読めない。
// ここに置けばテストが**実物を**呼べる（tests/ui_etag.mjs）。worker.js 側は Response を作るだけ。
//
// なぜファイル単位の ETag か: 50 本のモジュールと 14 枚のテクスチャ（計 64 URL）すべてに
// 版全体のハッシュを返していたため、1 行直すたびに全端末が raw 2.4MB（gzip 1.07MB）を払い直していた。
// immutable は付けない（R79: 3D は修正が入る生きたコード＝1 年キャッシュだと直しが届かない）。
import { ASSETS, BUILD, ETAGS, MODULES } from "./modules_data.js";

export const REVALIDATE = "public, max-age=0, must-revalidate";
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

/**
 * @param {string} path  "/ui/..." のパス
 * @param {string|null} ifNoneMatch  リクエストの If-None-Match
 * @returns {{status:number, etag?:string, type?:string, body?:string, b64?:string}}
 *   404 には ETag を付けない（存在しない URL に版を与えない）。
 */
export function uiResponse(path, ifNoneMatch) {
  const src = own(MODULES, path) ? MODULES[path] : "";
  const asset = !src && own(ASSETS, path) ? ASSETS[path] : null;
  if (!src && !asset) return { status: 404 };
  const etag = 'W/"ui-' + (own(ETAGS, path) ? ETAGS[path] : BUILD) + '"';
  if (ifNoneMatch === etag) return { status: 304, etag };
  return src
    ? { status: 200, etag, type: "text/javascript; charset=utf-8", body: src }
    : { status: 200, etag, type: asset[0], b64: asset[1] };
}
