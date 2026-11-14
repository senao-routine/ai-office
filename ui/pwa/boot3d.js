// R77: スマホPWAの3Dオフィス。デスクトップと**同じ** IsoScene / buildWorld を動かす
// （絵を二重に持たない＝片方だけ古くなる事故を構造的に防ぐ）。
//
// PWA側の app.js は classic script（worker.js に焼き込んだ1本）なので、ESM である
// このモジュールとは `window.__scene3d` で橋渡しする。app.js は「officeを渡す」
// 「タップ座標から社員IDを引く」「名札の位置を聞く」の3つしか知らなくてよい。
import { frozen, now, loop } from "../platform/clock.js";
import { buildWorld } from "../core/world.js";
import { IsoScene } from "../iso/scene3d.js";
import { createActivityView } from "../hud/activity.js";

// 会話の作業ビューは部屋の起動成否から独立。
window.__activity = { createActivityView };
document.dispatchEvent(new CustomEvent("activity-ready"));

// R122: import は部品の取得だけ。切替時は同じ module の mount を再度呼ぶ。
let mounted = null;
export function mount() {
  if (mounted) mounted.dispose();
const host = document.getElementById("scene3d");
if (!host) throw new Error("Office view host is unavailable");
// R79: WebGL不可・GPUドライバ拒否などで IsoScene の生成が投げると、以前は
// window.__scene3d の代入にも通知にも到達せず「空のdivが残るだけで退避もしない」＝
// **永久に白画面**だった（script.onerror は取得失敗でしか鳴らない）。
// 失敗を必ずイベントで知らせ、app.js 側がドット絵へ退避できるようにする。
let scene = null;
if (host) {
  try {
    // isoの現行APIはlocation.searchから品質を読む。同期生成中だけmobileを
    // 指定し、直後にURLを復元する（履歴追加なし・3Dソースと操作系は共通のまま）。
    const originalURL = location.href;
    const mobileURL = new URL(originalURL);
    mobileURL.searchParams.set("quality", "mobile");
    try {
      history.replaceState(history.state, "", mobileURL.href);
      scene = new IsoScene(host, { quality: "mobile" });
    } finally {
      history.replaceState(history.state, "", originalURL);
    }
  } catch (err) {
    document.dispatchEvent(new CustomEvent("scene3d-failed", {
      detail: { renderer: "3d", reason: String((err && err.message) || err) },
    }));
    host.querySelectorAll("canvas").forEach((canvas) => canvas.remove());
    throw err;
  }
}
if (host && scene) {
  let built = null;
  let last = null;
  let disposed = false;
  let dirty = true;
  let stopLoop = () => {};
  let resizeObserver = null;
  const listeners = [];
  const listen = (target, type, handler, options) => {
    target.addEventListener(type, handler, options);
    listeners.push(() => target.removeEventListener(type, handler, options));
  };
  const reducedMq = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
  const motionPref = () => {
    try { const v = localStorage.getItem("aioffice.motion"); return v === "on" || v === "off" ? v : "auto"; }
    catch (_) { return "auto"; }
  };
  let pref = motionPref();
  const still = () => frozen || pref === "off" || (pref === "auto" && Boolean(reducedMq && reducedMq.matches));
  const redraw = () => { dirty = true; if (frozen && built && !disposed) draw(now()); };
  const failed = (error) => {
    if (disposed) return;
    api.dispose();
    document.dispatchEvent(new CustomEvent("scene3d-failed", {
      detail: { renderer: "3d", reason: String((error && error.message) || error) },
    }));
  };
  const draw = (t) => {
    if (!built || disposed) return;
    try {
      scene.update(built, still() ? (frozen ? now() : 0) : t);
      dirty = false;
      if (typeof window.__paintPlates === "function") window.__paintPlates();
    } catch (error) { failed(error); }
  };
  host.style.position = "relative";
  const canvas = scene.renderer.domElement;
  canvas.style.display = "block";
  canvas.style.imageRendering = "auto";
  canvas.style.width = "100%";
  canvas.style.height = "100%";
  const wrap = document.getElementById("scene3dwrap");
  if (wrap) wrap.style.background = "var(--a-selected)";


  // 縦長ほど寄せる。全景フィットのままだと上下に大きな余白が出てロボットが豆粒になる
  // （390x460 で実測）。左右の端＝装飾帯が少し切れるのは許容し、主役を大きく見せる。
  const fitForPortrait = () => {
    const w = host.clientWidth || 1;
    const h = host.clientHeight || 1;
    const aspect = w / h;
    if (aspect >= 1.5) scene.setViewScale(1, "contain");
    else scene.setViewScale(1, "balanced");
  };
  fitForPortrait();

  const api = {
    ready: true,
    mode: "3d",
    plateW: 128,
    pinCalls: false,
    rescuePx: 120,
    /** /status で来た office_json を反映（app.js から毎ポーリング呼ばれる）。 */
    apply(office) {
      try {
        built = buildWorld(office || {});
        last = built;
        redraw();
      } catch (_) { /* 壊れたofficeで画面を落とさない＝前の絵のまま */ }
      return built;
    },
    /** タップ座標（canvas基準px）→ 社員ID。指はマウスより太いので半径を広めに。 */
    pick(x, y) {
      try { return scene.pickAgent(x, y, 54); } catch (_) { return null; }
    },
    /** 名札を置くためのスクリーン座標（canvas基準px・胴のあたり）。 */
    project(id) {
      try { return scene.projectAgent(id); } catch (_) { return null; }
    },
    /** R79-6: 名札アンカー（足元）。デスクトップ paintLabels と同じ labelAnchorFor を再利用
     *  ＝「頭上の大きな札はオフィスを隠す」FBの解をPC/スマホで揃える。 */
    anchor(id) {
      try {
        if (!last) return null;
        const i = (last.agents || []).findIndex((a) => a.id === id);
        if (i < 0) return null;
        return scene.labelAnchorFor(last.agents[i], last, i);
      } catch (_) { return null; }
    },
    agents() {
      return last ? last.agents : [];
    },
    resize() {
      if (disposed) return;
      try { fitForPortrait(); redraw(); } catch (_) { /* 回転直後などは黙って次フレームへ */ }
    },
    /** R78: 一覧チップから「その社員へカメラを寄せる」（誰がどれか一目で分かる）。 */
    focus(id) {
      try { if (id) scene.focusOn(id); else scene.focusOff(); redraw(); } catch (_) { /* 非対応でも操作は続行 */ }
    },
    /** R80.7: タップ挨拶（手を振る）。 */
    greet(id) {
      try { if (id) scene.greet(id); redraw(); } catch (_) { /* 非対応でも操作は続行 */ }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      api.ready = false;
      stopLoop();
      resizeObserver?.disconnect();
      listeners.splice(0).forEach((remove) => remove());
      try { scene.dispose(); } catch (_) { canvas.remove(); }
      if (mounted === api) mounted = null;
      if (window.__scene3d === api) window.__scene3d = null;
    },
    stats() {
      try { return scene.stats ? { ...scene.stats(), mode: "3d", still: still(), pref, pane: { w: host.clientWidth, h: host.clientHeight } } : null; } catch (_) { return null; }
    },
  };

  // R77: スマホは電池が有限。60fpsで回す必要はないので30fpsに間引き、
  // ホームのカード・オフィスが隠れているときや、会話・設定の全画面では描かない。
  // これが無いと rAF がイベントループを飽和させ、他のUI操作の応答まで遅れる（実測）。
  const FPS = 30;
  let lastDraw = -1;
  // 覆われている間は描かない。シート/設定/ログは全画面オーバーレイなので、
  // その裏で回し続けるのは電池の丸損（かつテスト環境ではメインスレッドを飽和させ、
  // 他のUI操作の応答が1秒を超えて落ちる＝実際にこれで relay E2E が落ちた）。
  // R134: 広い画面（開いた Duo・iPad＝body.wide）の会話は列に並ぶ＝部屋を覆わない（覆うのはスマホの全画面の会話だけ）。
  const covered = () => ["sheetwrap", "setwrap", "logwrap", "runwrap", "lnwrap", "reswrap", "termwrap"].some((id) => {
    const n = document.getElementById(id);
    if (id === "termwrap" && document.body.classList.contains("wide")) return false;
    return n && n.classList.contains("open");
  });
  const visible = () => host.offsetParent !== null && host.clientWidth > 0 && !covered();
  const startLoop = () => {
    stopLoop = loop((t) => {
      if (!built || !visible() || disposed) return;
      if (!dirty && still()) return;
      if (!dirty && lastDraw >= 0 && t - lastDraw < 1 / FPS) return;
      lastDraw = t;
      draw(t);
    });
  };
  // R80.6: タッチ操作（ユーザーFB「拡大したり移動させたりできたらいい」）。
  // 1本指ドラッグ=パン・ピンチ=ズーム（中点ピボット・中点移動でパンも）・ダブルタップ=リセット。
  // 8px未満の指の揺れはタップ扱い＝選択操作を殺さない。gestureMoved() は app.js の
  // click ハンドラが読む（ドラッグ後に click が発火してシートが開く誤爆を防ぐ）。
  const gs = { lastX: 0, lastY: 0, lastDist: 0, midX: 0, midY: 0, moved: 0, lastTap: 0 };
  listen(host, "touchstart", (ev) => {
    if (ev.touches.length === 1) {
      const t0 = ev.touches[0];
      gs.lastX = t0.clientX; gs.lastY = t0.clientY; gs.moved = 0;
      const now = Date.now();
      if (now - gs.lastTap < 320) {
        // ロボの上の連続タップは「2度目=詳細シート」の操作。全景リセットに化けさせると
        // タップ動線が丸ごと死ぬ（実機で発覚＝E2Eはタップ間隔900msで見逃した）。
        // さらに: 1度目の選択でカメラが寄る＝2度目の座標からロボが外れる（126px実測）。
        // 選択中の人がいる間もリセットから除く（classic script の SEL は window で読める）。
        const r = host.getBoundingClientRect();
        let hit = null;
        try { hit = scene.pickAgent(t0.clientX - r.left, t0.clientY - r.top, 54); } catch (_) { hit = null; }
        const recentSel = Boolean(window.SEL);
        if (!hit && !recentSel) { scene.viewReset(); redraw(); gs.moved = 99; }
      }
      gs.lastTap = now;
    } else if (ev.touches.length === 2) {
      const [a, b] = ev.touches;
      gs.lastDist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      gs.midX = (a.clientX + b.clientX) / 2; gs.midY = (a.clientY + b.clientY) / 2;
      gs.moved = 99;
    }
  }, { passive: true });
  listen(host, "touchmove", (ev) => {
    if (ev.touches.length === 1) {
      const t0 = ev.touches[0];
      const dx = t0.clientX - gs.lastX, dy = t0.clientY - gs.lastY;
      gs.moved += Math.abs(dx) + Math.abs(dy);
      if (gs.moved > 8) { scene.viewPanBy(dx, dy); redraw(); ev.preventDefault(); }
      gs.lastX = t0.clientX; gs.lastY = t0.clientY;
    } else if (ev.touches.length === 2) {
      const r = host.getBoundingClientRect();
      const [a, b] = ev.touches;
      const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      const mx = (a.clientX + b.clientX) / 2, my = (a.clientY + b.clientY) / 2;
      if (gs.lastDist > 0 && d > 0) scene.viewZoomBy(d / gs.lastDist, mx - r.left, my - r.top);
      scene.viewPanBy(mx - gs.midX, my - gs.midY);
      gs.lastDist = d; gs.midX = mx; gs.midY = my;
      gs.moved = 99;
      redraw();
      ev.preventDefault();
    }
  }, { passive: false });
  api.gestureMoved = () => gs.moved > 8;
  api.view = {
    zoomBy: (f, x, y) => { try { scene.viewZoomBy(f, x, y); redraw(); } catch (_) { /* 非対応でも続行 */ } },
    panBy: (dx, dy) => { try { scene.viewPanBy(dx, dy); redraw(); } catch (_) { /* 非対応でも続行 */ } },
    reset: () => { try { scene.viewReset(); redraw(); } catch (_) { /* 非対応でも続行 */ } },
    state: () => { try { return scene.viewState(); } catch (_) { return null; } },
  };

  listen(window, "resize", api.resize);
  listen(window, "orientationchange", api.resize);
  listen(document, "aioffice-motion", () => { pref = motionPref(); redraw(); });
  if (reducedMq) {
    if (reducedMq.addEventListener) listen(reducedMq, "change", redraw);
    else if (reducedMq.addListener) {
      reducedMq.addListener(redraw);
      listeners.push(() => reducedMq.removeListener(redraw));
    }
  }
  listen(canvas, "webglcontextlost", (event) => {
    event.preventDefault();
    failed(new Error("WebGL context was lost"));
  });
  if (typeof ResizeObserver === "function") {
    resizeObserver = new ResizeObserver(api.resize);
    resizeObserver.observe(host);
  }
  mounted = api;
  window.__scene3d = api;
  // app.js が先に走っている場合に備えて、直近の office で一度塗る。
  if (window.LAST_OFFICE) api.apply(window.LAST_OFFICE);
  if (disposed) throw new Error("3D office could not render");
  startLoop();
  document.dispatchEvent(new CustomEvent("scene3d-ready", { detail: { renderer: "3d" } }));
  return api;
}
return null;
}
