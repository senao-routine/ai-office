// R99-P4-6（本人裁定 A・2026-09-26）: スマホ PWA の部屋を 3D からドット絵の俯瞰へ置き換える。
// Mac のコックピットと**同じ** ui/cockpit/room.js を動かす（絵を二重に持たない＝片方だけ古くなる事故を構造的に防ぐ）。
// 3D（three.js・ui/iso）は PWA の束から外した＝開くときに取りに行く量が約 1.1MB → 約 0.1MB。
//
// app.js は classic script（worker.js に焼き込んだ 1 本）なので、ESM であるこのモジュールとは
// `window.__scene3d` で橋渡しする。形は R77 の boot3d.js と同じ（名前は歴史的経緯で 3d のまま＝app.js と
// スモークの呼び口を変えない）: apply / pick / project / anchor / agents / resize / focus / greet / stats /
// gestureMoved / view.{zoomBy, panBy, reset, state}・合図は scene3d-ready / scene3d-failed。
// 部屋の形に合わせて app.js が読む値: plateW（名札の幅）・pinCalls（受付の 2 人目以降の ❗ はピン）・rescuePx。
//
// 部屋の掟（room.js・design/README.md・.claude/rules/ui-cockpit.md が正本）をここでも守る:
//   - 倍率は整数だけ（ui/core/pxroom.js の roomScale・×1〜×4＝R102-1 で PWA だけ ×1 まで）。ピンチは 1 段ずつ整数倍を上げ下げする
//     （割合の縮小・非整数のズームをしない＝ドットがにじまない）。canvas は実寸で置く。
//   - 名前も数字も canvas には描かない。名札は app.js の paintPlates（DOM）が anchor の位置に置く。
// R102-10: 部品は**相対パス**で import する。app.js はこれを版つきの道（/u/<BUILD>/pwa/bootpx.js）で読み、相対の import は全部その下で
// 解決される＝版が変わると全部品の URL が変わる。本人の iPhone は 8 月の 1 年キャッシュ（immutable）の頃の /ui/core/world.js・
// /ui/platform/clock.js を今も手元から使い（cache:"reload" で取り直しても置き換わらなかった＝実機のログ）、新しい部品と結合できなかった。
// 絶対の /ui/... に戻すと、その古い写しに当たる。
import { createRoomLighting, frozen as clockFrozen, loop } from "../platform/clock.js";
import { buildWorld, needsAttention } from "../core/world.js";
import { ROOM, ROOM_SCALE_FLOOR, ROOM_SCALE_MAX, ROOM_SCALE_MIN, SLOTS, roomLayout, roomScale, roomWorldFields, tintHash, tintIndex } from "../core/pxroom.js";
import { init as initRoom } from "../cockpit/room.js";
import { SESSION_COLORS, UI_COLORS } from "../cockpit/palette.js";
import { createActivityView } from "../hud/activity.js";

// 作業ビューは部屋の成否と独立に渡す。classic の app.js が先に開いていても描き直せる。
window.__activity = { createActivityView };
document.dispatchEvent(new CustomEvent("activity-ready"));

// R122: mode changes reuse the ESM cache, but each mount owns and releases its canvas.
let mounted = null;
export function mount() {
if (mounted) mounted.dispose();
const removers = [];
const listen = (target, type, fn, options) => {
  if (!target?.addEventListener) return;
  target.addEventListener(type, fn, options);
  removers.push(() => target.removeEventListener(type, fn, options));
};
let stopLoop = () => {}, observer = null;
const host = document.getElementById("scene3d");
const wrap = document.getElementById("scene3dwrap");
const W = ROOM.w;
const H = ROOM.h;
const ROBOT_W = ROOM.robot.w;
const ROBOT_H = ROOM.robot.h;
// 受付の列の縦の中ほど（論理 px）。❗ の人はここに立つ（pxroom の SLOTS.queue の y ＋ロボの半分）
const QUEUE_MID = Math.round(SLOTS.queue.reduce((m, s) => m + s.y, 0) / SLOTS.queue.length + ROBOT_H / 2);
const reducedMq = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
// ロボの動き（設定シートの 3 択・端末ごとの好み＝localStorage `aioffice.motion`）: auto＝OS の「視差効果を減らす」に従う（既定）／
// on＝動かす（OS の設定が ON でも動かす）／off＝止める（電池の退路）。R99-P4-L1: iPhone で「視差効果を減らす」が ON だと
// 部屋が丸ごと止まり（打鍵も歩行も ✓ も出ない）、それを外す口がスマホに無かった
const motionPref = () => {
  try {
    const v = localStorage.getItem("aioffice.motion");
    return v === "on" || v === "off" ? v : "auto";
  } catch (_) { return "auto"; }
};
let pref = motionPref();
const still = () => clockFrozen || pref === "off" || (pref === "auto" && Boolean(reducedMq && reducedMq.matches));
const roomLighting = createRoomLighting();

// 個体色: 表示中のキーの衝突を tintIndex で解く（コックピットと同じ規則・前回の割り当てを持ち越して色を変えない）
let tints = new Map();
const colorOf = (key) => {
  const i = tints.get(String(key));
  return SESSION_COLORS[Number.isInteger(i) ? i : tintHash(String(key)) % SESSION_COLORS.length];
};

// 2D の canvas が作れない端末（ほぼ無い）でも「空の div が残るだけで退避もしない」にしない（R79 の教訓）。
let room = null;
if (host) {
  try {
    room = initRoom({
      host,
      // 固定時刻（?t=）と「視差効果を減らす」の端末では歩かせず ✓ も出さない（コックピットと同じ扱い）
      frozen: still,
      keepPrev: !clockFrozen,
      colorOf,
      scale: ROOM_SCALE_MIN,
      // 暮らし（散歩・ui/core/life.js）。?life=0 で切る（スモークの口）
      life: new URLSearchParams(location.search).get("life") !== "0",
    });
  } catch (err) {
    document.dispatchEvent(new CustomEvent("scene3d-failed", {
      detail: { renderer: "pixel", reason: String((err && err.message) || err) },
    }));
  }
}

if (host && room) {
  const canvas = host.querySelector("canvas");
  if (wrap) wrap.style.background = UI_COLORS.stage;   // 部屋の外周も部屋の地（色の正本は palette.js・どのテーマでも暗い窓）
  host.style.position = "relative";
  canvas.style.position = "absolute";
  canvas.style.imageRendering = "pixelated";

  let world = null;        // app.js に渡す world（session は本物）
  let roomWorld = null;    // 部屋に渡す world（人のキーを社員 ID に固定）
  let dirty = true;
  let S = ROOM_SCALE_MIN;  // 今の倍率
  let fit = ROOM_SCALE_MIN;// 形から決めた倍率（リセットで戻る先）
  let needFit = true;      // 部屋が隠れていて（寸法 0）全景の倍率を決められていない
  let panX = 0;
  let panY = 0;
  let left = 0;            // canvas の左上（host 基準の CSS px）
  let top = 0;

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  /** 描かれている要素の矩形（position: fixed でも取れる・非表示や寸法 0 は null）。 */
  const shownRect = (n) => {
    if (!n || !n.getClientRects().length) return null;
    const r = n.getBoundingClientRect();
    return r.height > 0 && r.width > 0 ? r : null;
  };
  const hasSize = () => host.clientWidth > 0 && host.clientHeight > 0;

  /**
   * 部屋が見える帯（host 基準の上端と下端）。R102-1（本人裁定 2026-09-30「あなた待ちは部屋に重ねず上下に分ける」）:
   * 部屋の窓（#officewin の中の #scene3dwrap）には何も重ならない＝ロスター帯・あなた待ち・状態の行は窓の外の兄弟の箱。
   * 帯は窓そのもの（ヘッダー・タブは念のため引く＝窓の外なので 0 に落ちる）。
   */
  const band = () => {
    const hr = host.getBoundingClientRect();
    let y0 = 0;
    let y1 = hr.height;
    // R134: 広い画面（開いた Duo・iPad）では見出しは左の列＝部屋と横に重ならない面は引かない（引くと右の列の部屋が ×2 に届かず ×1 に落ちた）
    const across = (r) => r.right > hr.left + 0.5 && r.left < hr.right - 0.5;
    const header = shownRect(document.querySelector(".topbar"));
    if (header && across(header)) y0 = Math.max(y0, header.bottom - hr.top);
    const tabs = shownRect(document.getElementById("tabbar"));
    if (tabs && across(tabs)) y1 = Math.min(y1, tabs.top - hr.top);
    y0 = clamp(y0, 0, hr.height);
    y1 = clamp(y1, y0, hr.height);
    return { hr, y0, y1 };
  };

  // 部屋が見える帯に入らないときの縦の置き方（R99-P4-L4・本人「アバターが表示されなくなっている」2026-09-28）:
  //   1. 居る人全員（頭上の印から足元の名札まで）が帯に収まるなら、その範囲を帯の中央へ
  //   2. 収まらず ❗ の人が居るなら、受付の列（部屋のいちばん下）を帯の中ほどへ（❗ドックの裏に隠さない＝R86-H の教訓）
  //   3. それ以外は部屋の中央
  // 以前は ❗ の間いつも 2 にしていた＝机で作業中の人が上のロスター帯の裏に隠れ、左下の ❗ の 2 体しか見えなかった。
  // 範囲は「直前に描いた位置」と「データから出る行き先」の和（着く前の新しい人も入れる）。求め直すのはデータの到着と
  // 形の変化のときだけ（指でパンしている間に置き方が揺れない）。
  let span = null;                                  // [上, 下]（論理 px）
  let fitsAll = true;                               // 居る人全員が帯に収まる置き方か（スモークが読む）
  const MARK_ROOM = 12;                             // 頭上の印（高さ 8＋間 1＋ぴょこっ 2）＋余白 1
  const PLATE_ROOM_PX = 26;                         // 足元の名札（高さ 20＋間 4）＋余白 2（CSS px）
  const measureSpan = () => {
    const ys = [];
    try { for (const a of room.actors()) if (!a.leaving) ys.push(a.y); } catch (_) { /* 描く前 */ }
    try { if (roomWorld) for (const a of roomLayout(roomWorld, [], null).actors) ys.push(a.y); } catch (_) { /* 壊れた world */ }
    span = ys.length ? [Math.min(...ys) - MARK_ROOM, Math.max(...ys) + ROBOT_H] : null;
  };
  let callMode = false;
  const calling = () => (world?.agents || []).some((a) => {
    try { return needsAttention(a); } catch (_) { return false; }
  });

  /** 倍率と位置を今の形から決め直して canvas を置く。パンは「基準の位置からのずれ」で、部屋が帯の外へ抜けない範囲に収める。 */
  const place = () => {
    const { hr, y0, y1 } = band();
    const cw = W * S;
    const ch = H * S;
    const call = calling();
    if (call !== callMode) { callMode = call; panY = 0; }   // ❗ が出た・消えたら縦の基準へ戻す
    // 横: 入るなら中央・入らなければ中央を基準に左右の端まで
    const baseLeft = Math.round((hr.width - cw) / 2);
    const [xMin, xMax] = cw > hr.width ? [Math.round(hr.width - cw), 0] : [baseLeft, baseLeft];
    // 縦: 入るなら帯の中央。入らなければ上の 1〜3（居る人全員が収まるなら全員・❗ の間は受付の列・それ以外は部屋の中央）
    const bandH = y1 - y0;
    // 入るときは「部屋＋足元の名札の分」を帯の中央へ（下の段の名札が窓の下端で切れない）
    const centerTop = Math.round(y0 + Math.max(0, (bandH - ch - PLATE_ROOM_PX) / 2));
    const fits = ch <= bandH;
    let baseTop = centerTop;
    fitsAll = fits;
    if (!fits) {
      const spanH = span ? (span[1] - span[0]) * S + PLATE_ROOM_PX : Infinity;
      fitsAll = Boolean(span) && spanH <= bandH;
      if (fitsAll) baseTop = Math.round(y0 + (bandH - spanH) / 2 - span[0] * S);
      else if (call) baseTop = Math.round((y0 + y1) / 2 - QUEUE_MID * S);
    }
    const [yMin, yMax] = fits ? [centerTop, centerTop] : [Math.round(y1 - ch), Math.round(y0)];
    left = Math.round(clamp(baseLeft + panX, xMin, xMax));
    top = Math.round(clamp(baseTop + panY, yMin, yMax));
    panX = left - baseLeft;
    panY = top - baseTop;
    canvas.style.left = `${left}px`;
    canvas.style.top = `${top}px`;
    canvas.style.width = `${cw}px`;
    canvas.style.height = `${ch}px`;
    api.plateW = Math.min(128, 40 * S - 4);   // 名札は机の横の間隔（論理 40）に収める＝隣の机の人に掛からない（×1 は 36＝印だけ）
    const plates = document.getElementById("plates");
    if (plates) plates.classList.toggle("tiny", S < 2);
    api.rescuePx = 2 * ROBOT_H * S;           // 選択中の人の近くの空振り＝2 度目のタップとみなす範囲
  };

  // 窓に部屋全体が入る最大の整数倍（×2〜×4）。×2 が入らない窓だけ ×1（R102-1・全員を見せる＝パンで探させない）。
  // あなた待ちのカードが出て窓が縮めば倍率も下がる（ResizeObserver → resize）。指で拡大している間は奪わない。
  const fitScale = () => {
    const { hr, y0, y1 } = band();
    // 横向きの分割では端末を開いても部屋の窓は縮まない。左の窓に入る整数倍を保つ。
    if (document.body.classList.contains("term-open") && !document.body.classList.contains("split-office")) return ROOM_SCALE_FLOOR;
    return roomScale(hr.width - 6, y1 - y0, ROOM_SCALE_FLOOR);    // 左の 6px だけ引く（pxroom の roomScale の約束・375 幅で ×2 が入る）
  };

  const setScale = (k, px, py) => {
    const next = clamp(Math.round(k), ROOM_SCALE_FLOOR, ROOM_SCALE_MAX);
    if (next === S) return false;
    // 指の下の点（論理 px）を動かさない: 替えた後の左上をその点から逆算してパンに戻す
    const lx = (px - left) / S;
    const ly = (py - top) / S;
    S = next;
    room.setScale(S);
    panX = 0; panY = 0;
    place();
    panX += (px - lx * S) - left;
    panY += (py - ly * S) - top;
    place();
    dirty = true;
    return true;
  };

  /** 全景へ（形から倍率を決め直す）。部屋が隠れている（寸法 0）ときは決めずに印だけ立て、見えたときに決める。 */
  const resetView = () => {
    panX = 0; panY = 0;
    if (!hasSize()) { needFit = true; return; }
    needFit = false;
    fit = fitScale();
    S = fit;
    room.setScale(S);
    place();
    dirty = true;
  };

  const actorOf = (id) => {
    if (!id) return null;
    const list = room.actors();
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const a = list[i];
      if (!a.leaving && a.id === id) return a;
    }
    return null;
  };
  /** app.js の選択（SEL＝社員）→ 部屋の人のキー（社員 ID）。❗カード・一覧・ティッカーから開いた人にも角かっこを合わせる */
  const selectedId = () => {
    const sel = window.SEL;
    if (!sel || !sel.session) return null;
    const a = (world?.agents || []).find((x) => x.session === sel.session);
    return a ? a.id : null;
  };

  let greetTimer = 0;
  const api = {
    ready: true,
    mode: "pixel",
    dispose() {
      if (!api.ready) return;
      api.ready = false;
      if (mounted === api) mounted = null;
      stopLoop(); observer?.disconnect();
      clearTimeout(greetTimer); removers.splice(0).forEach((remove) => remove());
      room.dispose(); api.ready = false;
      if (window.__scene3d === api) window.__scene3d = null;
    },
    // 部屋の論理寸法（app.js の fitAttnDock が「×2 の部屋が入る窓の丈」を決めるのに使う＝カードの丈では倍率を決めない）
    roomW: W,
    roomH: H,
    plateW: 128,
    pinCalls: true,
    rescuePx: 180,
    /** /status で来た office_json を反映（app.js から毎ポーリング呼ばれる）。 */
    apply(office) {
      try {
        world = buildWorld(office || {});
        // 部屋の人のキーは社員 ID に固定する（avatarMode=project では代表の session が入れ替わる＝キーが session だと
        // ロボが扉から出入りし直し、名札と角かっこが飛ぶ）。session モードでは ID と session が 1 対 1 なので同じ絵。
        const withRoomFields = roomWorldFields(world, office || {});
        roomWorld = { ...withRoomFields, agents: (withRoomFields.agents || []).map((a) => ({ ...a, session: a.id })) };
        tints = tintIndex(roomWorld.agents.map((a) => a.id), SESSION_COLORS.length, clockFrozen ? null : tints);
        measureSpan();
        place();                                 // 居る人の範囲・❗ の出入りで置き方を決め直す（パンは持ち越す）
        dirty = true;
        if (clockFrozen) draw(0);                // 固定時刻はループが 1 回しか回らない＝データが来たら描く
      } catch (_) { /* 壊れた office で画面を落とさない＝前の絵のまま */ }
      return world;
    },
    /** タップ座標（host 基準 px）→ 社員 ID。当たりは room.js の最寄り方式（描いた矩形の縁から 22px）。 */
    pick(x, y) {
      try {
        const hr = host.getBoundingClientRect();
        const a = room.pick(hr.left + x, hr.top + y);
        return a && !a.leaving ? a.id : null;
      } catch (_) { return null; }
    },
    /** ロボの胸のあたり（host 基準 px）。app.js の「選択中のロボの近くの空振り＝2 度目」の判定に使う。 */
    project(id) {
      const a = actorOf(id);
      if (!a) return null;
      return { left: left + (a.x + ROBOT_W / 2) * S, top: top + (a.y + 4) * S };
    },
    /** 名札アンカー（足元・host 基準 px）。3D と同じく名札はロボの下に置く＝頭上の札で部屋を隠さない。 */
    anchor(id) {
      const a = actorOf(id);
      if (!a) return null;
      return { left: left + (a.x + ROBOT_W / 2) * S, top: top + (a.y + ROBOT_H) * S + 1 };
    },
    agents() {
      return world ? world.agents || [] : [];
    },
    resize() {
      try {
        if (!hasSize()) { needFit = true; return; }
        const was = fit;
        const zoomed = (!document.body.classList.contains("term-open") || document.body.classList.contains("split-office")) && !needFit && S !== was;   // 指で拡大・縮小している間は倍率を奪わない
        needFit = false;
        fit = fitScale();
        if (!zoomed) { S = fit; room.setScale(S); panX = 0; panY = 0; }
        measureSpan();
        place();
        dirty = true;
      } catch (_) { /* 回転直後などは黙って次のフレームへ */ }
    },
    /** 一覧チップ・タップから「その人」を示す: 角かっこは SEL に合わせて描く。その人が帯の外なら見える所までパンする。 */
    focus(id) {
      try {
        dirty = true;
        const a = id && actorOf(id);
        if (!a) return;
        const { hr, y0, y1 } = band();
        const x0 = left + a.x * S;
        const yTop = top + a.y * S;
        const out = x0 < 0 || x0 + ROBOT_W * S > hr.width || yTop < y0 || yTop + ROBOT_H * S > y1;
        if (!out) return;
        panX += hr.width / 2 - (x0 + (ROBOT_W / 2) * S);
        panY += (y0 + y1) / 2 - (yTop + (ROBOT_H / 2) * S);
        place();
      } catch (_) { /* 非対応でも操作は続行 */ }
    },
    /** タップの手応え: その人が 0.9 秒手を振る（万歳と待機・1px 跳ねる）。止めている間は足元の線を 0.9 秒（動かない手応え）。 */
    greet(id) {
      try {
        if (!id) return;
        dirty = true;
        if (room.setGreet(id)) return;
        room.setHover({ session: id });
        clearTimeout(greetTimer);
        greetTimer = setTimeout(() => { room.setHover(null); dirty = true; }, 900);
      } catch (_) { /* 非対応でも操作は続行 */ }
    },
    stats() {
      try {
        const s = room.stats();
        const hr = host.getBoundingClientRect();
        return { ...s, robots: s.actors, fit, panX, panY, still: still(), pref, fitsAll,
          pane: { w: Math.round(hr.width), h: Math.round(hr.height) } };
      } catch (_) { return null; }
    },
  };

  const draw = (t) => {
    room.setSelected(selectedId());
    room.draw(roomWorld, [], t, { lightStage: roomLighting(still()) });
    if (typeof window.__paintPlates === "function") window.__paintPlates();
  };

  // 描くのは部屋の刻み（歩いている人が居れば 12fps・居なければ 6fps＝pxwalk.roomFps）とデータの到着だけ。
  // オフィスタブが隠れている・全画面のシートに覆われている間は描かない（電池と、裏で回して E2E が落ちた R77 の教訓）。
  // R134: 広い画面（開いた Duo・iPad＝body.wide）の会話は列に並ぶ＝部屋を覆わない（覆うのはスマホの全画面の会話だけ）。
  const covered = () => ["sheetwrap", "setwrap", "logwrap", "runwrap", "lnwrap", "reswrap", "termwrap"].some((id) => {
    const n = document.getElementById(id);
    if (id === "termwrap" && document.body.classList.contains("wide")) return false;
    return n && n.classList.contains("open");
  });
  const visible = () => host.offsetParent !== null && host.clientWidth > 0 && !covered();
  // 描く時刻は刻みの格子に揃える（Mac の index.js の gridT と同じ・生の t だと歩行のコマが稀に 1 つ飛ぶ）。止めている間は 0
  let lastDraw = -1;
  let selShown = null;
  stopLoop = loop((t) => {
    if (!roomWorld || !visible()) return;
    if (needFit) api.resize();
    const sel = selectedId();
    if (sel !== selShown) { selShown = sel; dirty = true; }
    const fps = room.fps() || 6;
    const tq = still() ? 0 : Math.max(Math.floor(t * fps) / fps, lastDraw);
    if (!dirty && tq <= lastDraw) return;
    lastDraw = tq;
    dirty = false;
    draw(tq);
  });
  // 設定シートの「ロボの動き」が変わった・OS の設定が変わった → すぐ描き直す（止め絵 ↔ 動き）。値はイベントに載る
  //（localStorage に書けない端末でもその場で効く）。lastDraw は戻さない＝描く時刻を巻き戻さない（動き→止め絵は tq=0 と dirty で
  // 描かれ、止め絵→動きは max(格子, 0) で描かれる）
  listen(document, "aioffice-motion", (e) => {
    const v = e && e.detail;
    pref = v === "on" || v === "off" || v === "auto" ? v : motionPref();
    dirty = true;
  });
  listen(reducedMq, "change", () => { dirty = true; });

  // タッチ操作（R80.6 の型を保つ）: 1 本指ドラッグ＝パン・ピンチ＝倍率を 1 段ずつ・空きのダブルタップ＝全景。
  // 8px 未満の揺れはタップ扱い＝選択を殺さない。gestureMoved() は app.js の click が読む（ドラッグ後の誤爆防止）。
  // ダブルタップは「動かさずに離したタップ」どうしが 320ms 以内・30px 以内のときだけ（素早い 2 回のスワイプを全景に化けさせない）。
  const gs = { lastX: 0, lastY: 0, dist0: 0, midX: 0, midY: 0, moved: 0, tapAt: 0, tapX: 0, tapY: 0, downX: 0, downY: 0 };
  const rebase = (touches) => {
    if (touches.length === 1) {
      gs.lastX = touches[0].clientX; gs.lastY = touches[0].clientY;
    } else if (touches.length >= 2) {
      const [a, b] = touches;
      gs.dist0 = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      gs.midX = (a.clientX + b.clientX) / 2; gs.midY = (a.clientY + b.clientY) / 2;
    }
  };
  listen(host, "touchstart", (ev) => {
    if (ev.touches.length === 1) {
      const t0 = ev.touches[0];
      gs.moved = 0;
      gs.downX = t0.clientX; gs.downY = t0.clientY;
      rebase(ev.touches);
      if (Date.now() - gs.tapAt < 320 && Math.hypot(t0.clientX - gs.tapX, t0.clientY - gs.tapY) < 30) {
        // ロボの上の連続タップは「2 度目＝詳細シート」。直近 4 秒に選択があるときも全景に化けさせない（R80.9・R81-5）
        const hr = host.getBoundingClientRect();
        const hit = api.pick(t0.clientX - hr.left, t0.clientY - hr.top);
        const recentSel = window.SEL && (Date.now() - (window.SEL_AT || 0) < 4000);
        if (!hit && !recentSel) { resetView(); gs.moved = 99; }
        gs.tapAt = 0;
      }
    } else if (ev.touches.length === 2) {
      rebase(ev.touches);
      gs.moved = 99;
    }
  }, { passive: true });
  listen(host, "touchmove", (ev) => {
    if (ev.touches.length === 1) {
      const t0 = ev.touches[0];
      const dx = t0.clientX - gs.lastX;
      const dy = t0.clientY - gs.lastY;
      gs.moved += Math.abs(dx) + Math.abs(dy);
      if (gs.moved > 8) { panX += dx; panY += dy; place(); dirty = true; ev.preventDefault(); }
      gs.lastX = t0.clientX; gs.lastY = t0.clientY;
    } else if (ev.touches.length === 2) {
      const hr = host.getBoundingClientRect();
      const [a, b] = ev.touches;
      const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      const mx = (a.clientX + b.clientX) / 2;
      const my = (a.clientY + b.clientY) / 2;
      panX += mx - gs.midX; panY += my - gs.midY; place();
      // 指の間が 1.35 倍に開いたら 1 段上げ、0.74 倍に閉じたら 1 段下げる。段が変わったとき・倍率の端に当たったときは
      // 基準を取り直す（端で取り直さないと、逆向きに動かしても段が変わらない）
      if (gs.dist0 > 0 && d > 0) {
        const r = d / gs.dist0;
        if (r >= 1.35) { setScale(S + 1, mx - hr.left, my - hr.top); gs.dist0 = d; }
        else if (r <= 0.74) { setScale(S - 1, mx - hr.left, my - hr.top); gs.dist0 = d; }
      }
      gs.midX = mx; gs.midY = my;
      gs.lastX = a.clientX; gs.lastY = a.clientY;   // 片方を離したとき、残った指の最初の動きで跳ばない
      gs.moved = 99;
      dirty = true;
      ev.preventDefault();
    }
  }, { passive: false });
  const touchEnd = (ev) => {
    if (ev.touches.length === 0) {
      // 動かさずに離した 1 本指＝タップ。次のタップがダブルタップかどうかの基準
      if (gs.moved <= 8) { gs.tapAt = Date.now(); gs.tapX = gs.downX; gs.tapY = gs.downY; } else gs.tapAt = 0;
    } else {
      rebase(ev.touches);   // 2 本→1 本・3 本→2 本: 残った指で基準を取り直す
    }
  };
  listen(host, "touchend", touchEnd, { passive: true });
  listen(host, "touchcancel", touchEnd, { passive: true });
  api.gestureMoved = () => gs.moved > 8;
  api.view = {
    /** f > 1 で 1 段上げ・f < 1 で 1 段下げ（整数倍だけ）。x, y は host 基準の支点。 */
    zoomBy: (f, x, y) => {
      try {
        const hr = host.getBoundingClientRect();
        const px = Number.isFinite(x) ? x : hr.width / 2;
        const py = Number.isFinite(y) ? y : hr.height / 2;
        if (f > 1) setScale(S + 1, px, py);
        else if (f < 1) setScale(S - 1, px, py);
      } catch (_) { /* 非対応でも続行 */ }
    },
    panBy: (dx, dy) => { try { panX += dx; panY += dy; place(); dirty = true; } catch (_) { /* 続行 */ } },
    reset: () => { try { resetView(); } catch (_) { /* 続行 */ } },
    /** scale は全景からの拡大率（S / fit＝全景で 1・1 段上げて 1.5 など）。roomScale は部屋の整数倍そのもの。 */
    state: () => ({ scale: S / fit, roomScale: S, fit, panX, panY }),
  };

  resetView();
  // 窓の寸法の変化で倍率から決め直す（あなた待ちのカードの出入り・ロスター帯・オフライン帯・回転・隠れていた部屋が見えた）。
  // 窓の丈はカードの丈で決まり、カードの丈は部屋の倍率に依らない＝行ったり来たりしない。
  if (typeof ResizeObserver === "function") {
    observer = new ResizeObserver(() => {
      api.resize();
      dirty = true;
    });
    observer.observe(host);
  }
  listen(window, "resize", api.resize);
  listen(window, "orientationchange", api.resize);
  window.__scene3d = api;
  mounted = api;
  // app.js が先に走っている場合に備えて、直近の office で一度塗る
  if (window.LAST_OFFICE) api.apply(window.LAST_OFFICE);
  document.dispatchEvent(new CustomEvent("scene3d-ready", { detail: { renderer: "pixel" } }));
  return api;
}

throw new Error("The pixel office could not start");
}
