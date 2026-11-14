// R96-D3 項目 6: /ui/** の応答（ETag と本文）を決める純関数。
//
// なぜ切り出すか: worker.js は "cloudflare:workers" を import するので node から読めない。
// ここに置けばテストが**実物を**呼べる（tests/ui_etag.mjs）。worker.js 側は Response を作るだけ。
//
// なぜファイル単位の ETag か: 50 本のモジュールと 14 枚のテクスチャ（計 64 URL）すべてに
// 版全体のハッシュを返していたため、1 行直すたびに全端末が raw 2.4MB（gzip 1.07MB）を払い直していた。
// 版の無い /ui/... に immutable は付けない（R79: 3D は修正が入る生きたコード＝1 年キャッシュだと直しが届かない）。版つきの道 /u/ は下の uiVersioned。
import { ASSETS, BUILD, ETAGS, MODULES } from "./modules_data.js";
import { ASSETS as ASSETS_3D, BUILD as BUILD_3D, ETAGS as ETAGS_3D, MODULES as MODULES_3D } from "./modules_3d.js";

const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

export const REVALIDATE = "public, max-age=0, must-revalidate";
// R102-10: 版つきの道 /u/<BUILD>/<rest> は /ui/<rest> と同じ本文。BUILD は全部品の内容から決まる＝今の版の道の本文は二度と変わらない
// ＝今の版に限り immutable（開くたびの再検証 15 本が要らない）。古い版の道は今の本文を返すので再検証（REVALIDATE）にする。
// 版の無い /ui/... には付けない（8 月に付けた写しが本人の iPhone に 2 か月残り、部屋が起動しなかった）。
export const IMMUTABLE = "public, max-age=31536000, immutable";
/** @returns {{ui:string, current:boolean}|null} */
export function uiVersioned(path) {
  const m = /^\/u\/([0-9a-f]{12})\/([A-Za-z0-9_./-]+)$/.exec(path);
  if (!m || m[2].split("/").some((seg) => seg === "" || seg === "." || seg === "..")) return null;
  const ui = "/ui/" + m[2];
  const current = (m[1] === BUILD && (own(MODULES, ui) || own(ASSETS, ui))) ||
    (m[1] === BUILD_3D && (own(MODULES_3D, ui) || own(ASSETS_3D, ui)));
  return { ui, current };
}

/**
 * @param {string} path  "/ui/..." のパス
 * @param {string|null} ifNoneMatch  リクエストの If-None-Match
 * @param {string} version /u/ の版。共通の依存でも各束の本文を取り違えない。
 * @returns {{status:number, etag?:string, type?:string, body?:string, b64?:string}}
 *   404 には ETag を付けない（存在しない URL に版を与えない）。
 */
export function uiResponse(path, ifNoneMatch, version = "") {
  // 版のない道・古い版は通常の束を先に探す。3D の現行版は、相対 import 化した 3D の本文を使う。
  const three = version === BUILD_3D || (!own(MODULES, path) && !own(ASSETS, path));
  const modules = three ? MODULES_3D : MODULES;
  const assets = three ? ASSETS_3D : ASSETS;
  const etags = three ? ETAGS_3D : ETAGS;
  const build = three ? BUILD_3D : BUILD;
  const src = own(modules, path) ? modules[path] : "";
  const asset = !src && own(assets, path) ? assets[path] : null;
  if (!src && !asset) return { status: 404 };
  const etag = 'W/"ui-' + (own(etags, path) ? etags[path] : build) + '"';
  if (ifNoneMatch === etag) return { status: 304, etag };
  return src
    ? { status: 200, etag, type: "text/javascript; charset=utf-8", body: src }
    : { status: 200, etag, type: asset[0], b64: asset[1] };
}
