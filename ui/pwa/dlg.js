// R87: 封書を開く口だけを積んだ**小さな入口**。
//
// ★部屋の起動部品（bootpx.js・R99-P4-6 までは 3D の boot3d.js）に相乗りさせない。理由は2つ:
//   ① リスト表示は localStorage で永続する。その状態で開くと sceneShell3D() が呼ばれず、
//      部屋の起動部品自体が読み込まれない＝暗号が正常な端末でも「読み込み待ち」のまま詰む。
//   ② 会話を読むのに部屋の一式は要らない。ここは dialog_open + 固定ベクタだけ。
import { openBlob } from "/ui/core/dialog_open.js";
import { DIALOG_KAT } from "/ui/core/dialog_kat.js";

window.__dlg = { openBlob, kat: DIALOG_KAT };
window.dispatchEvent(new CustomEvent("dlg-ready"));
