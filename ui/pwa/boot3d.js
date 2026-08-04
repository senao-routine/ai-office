// R77: スマホPWAの3Dオフィス。デスクトップと**同じ** IsoScene / buildWorld を動かす
// （絵を二重に持たない＝片方だけ古くなる事故を構造的に防ぐ）。
//
// PWA側の app.js は classic script（worker.js に焼き込んだ1本）なので、ESM である
// このモジュールとは `window.__scene3d` で橋渡しする。app.js は「officeを渡す」
// 「タップ座標から社員IDを引く」「名札の位置を聞く」の3つしか知らなくてよい。
import { loop } from "/ui/platform/clock.js";
import { buildWorld } from "/ui/core/world.js";
import { IsoScene } from "/ui/iso/scene3d.js";

const host = document.getElementById("scene3d");
if (host) {
  const scene = new IsoScene(host);
  let built = null;
  let last = null;

  // 縦長ほど寄せる。全景フィットのままだと上下に大きな余白が出てロボットが豆粒になる
  // （390x460 で実測）。左右の端＝装飾帯が少し切れるのは許容し、主役を大きく見せる。
  const fitForPortrait = () => {
    const w = host.clientWidth || 1;
    const h = host.clientHeight || 1;
    const aspect = w / h;
    if (aspect >= 1.5) scene.setViewScale(1, "contain");
    else scene.setViewScale(1, "balanced");   // 縦長＝余白と見切れの中間で最大に見せる
  };
  fitForPortrait();

  const api = {
    ready: true,
    /** /status で来た office_json を反映（app.js から毎ポーリング呼ばれる）。 */
    apply(office) {
      try {
        built = buildWorld(office || {});
        last = built;
      } catch (_) { /* 壊れたofficeで画面を落とさない＝前の絵のまま */ }
      return built;
    },
    /** タップ座標（canvas基準px）→ 社員ID。指はマウスより太いので半径を広めに。 */
    pick(x, y) {
      try { return scene.pickAgent(x, y, 54); } catch (_) { return null; }
    },
    /** 名札を置くためのスクリーン座標（canvas基準px）。 */
    project(id) {
      try { return scene.projectAgent(id); } catch (_) { return null; }
    },
    agents() {
      return last ? last.agents : [];
    },
    resize() {
      try { fitForPortrait(); } catch (_) { /* 回転直後などは黙って次フレームへ */ }
    },
    /** R78: 一覧チップから「その社員へカメラを寄せる」（誰がどれか一目で分かる）。 */
    focus(id) {
      try { if (id) scene.focusOn(id); else scene.focusOff(); } catch (_) { /* 非対応でも操作は続行 */ }
    },
    stats() {
      try { return scene.stats ? scene.stats() : null; } catch (_) { return null; }
    },
  };

  // R77: スマホは電池が有限。60fpsで回す必要はないので30fpsに間引き、
  // オフィスタブが表示されていないとき（リスト/設定タブ・シート全画面）は描かない。
  // これが無いと rAF がイベントループを飽和させ、他のUI操作の応答まで遅れる（実測）。
  const FPS = 30;
  let lastDraw = -1;
  // 覆われている間は描かない。シート/設定/ログは全画面オーバーレイなので、
  // その裏で回し続けるのは電池の丸損（かつテスト環境ではメインスレッドを飽和させ、
  // 他のUI操作の応答が1秒を超えて落ちる＝実際にこれで relay E2E が落ちた）。
  const covered = () => ["sheetwrap", "setwrap", "logwrap"].some((id) => {
    const n = document.getElementById(id);
    return n && n.classList.contains("open");
  });
  const visible = () => host.offsetParent !== null && host.clientWidth > 0 && !covered();
  loop((t) => {
    if (!built || !visible()) return;
    if (lastDraw >= 0 && t - lastDraw < 1 / FPS) return;
    lastDraw = t;
    scene.update(built, t);
    if (typeof window.__paintPlates === "function") window.__paintPlates();
  });
  window.addEventListener("resize", api.resize);
  window.addEventListener("orientationchange", api.resize);
  window.__scene3d = api;
  // app.js が先に走っている場合に備えて、直近のofficeで一度塗る
  if (window.LAST_OFFICE) api.apply(window.LAST_OFFICE);
  document.dispatchEvent(new CustomEvent("scene3d-ready"));
}
