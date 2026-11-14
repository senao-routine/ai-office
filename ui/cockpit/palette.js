// Generated from design/palette.js by gen_tiles.py. Do not edit by hand.
// R99: 色の正本。**ランプ（濃→淡の階段）で持つ**のが、ドット絵がのっぺりしない唯一のコツ。
//
// なぜランプか: 物ごとに好きな色を足していくと（最初そうした）、隣り合う面の関係がバラバラになり、
// 絵が「塗り分けた図」に見える。同じ色相・同じ明度差で刻んだ階段を全部の物が共有すると、
// 同じ光の下に在るように見える。16 ビットのゲームが少ない色数で立体に見えるのはこれのため。
//
// 光は**左上から**。どの面も「上と左が明るく、下と右が暗い」で統一する。これを混ぜると破綻する。
//
// 墨 × 琥珀。家具は暖色、窓から入る光だけを青灰色にする。

/** 4 段のランプ: [いちばん暗い（輪郭・影）, 暗い面, 基準の面, 明るい面] */
export const RAMP = {
  // 部屋の地。床・壁を**中くらいの暗さ**に置くのが要（ここを黒に寄せると、影も家具も
  // 全部が黒の帯に潰れて、何も立体に見えなくなる。最初それをやって実際に潰れた）。
  ink:   ["#111310", "#1a1d19", "#292d26", "#3c4137"],
  // 石の床。目地は基準色のすぐ下に置き、床の格子が家具より目立たないようにする。
  stone: ["#272a25", "#30342e", "#373b34", "#42473e"],
  // 木（机・カウンター・卓）＝床よりはっきり明るく、暖かい
  wood:  ["#3b2d20", "#59432f", "#806143", "#ad8859"],
  // ロボの体。**暗い体に明るい縁**（本人の参考 2026-09-22）。暗い床の上で縁が効いて浮き上がる。
  // 明るい体にすると、暗い部屋では逆に「白い塊」になって個体色が読めなくなる。
  bot:   ["#12141c", "#1f2231", "#2e3245", "#b6bdcd"],   // [面頬/バイザー, 体, 上面, 縁]
  // 紙・書類など、部屋の中の明るい小物
  shell: ["#858779", "#b2b2a2", "#d2cdb9", "#eee5ce"],
  // 琥珀（画面・ランプ・アクセント）＝室内の暖かい光
  amber: ["#69502d", "#a78045", "#d8ae67", "#f3d79a"],
  // 布（ソファ・ラグ）＝木より彩度を落として、木と区別する
  cloth: ["#262f2c", "#35423b", "#4c5f51", "#70816a"],
  // ガラス（会議室）＝唯一の寒色。部屋の中で「別の部屋」に見せる
  glass: ["#1b262c", "#2c3d46", "#3f5763", "#5d7c8a"],
  // 金属（サーバー・脚・椅子・モニタ筐体）＝床より暗いが黒ではない
  steel: ["#151310", "#26221d", "#3a342c", "#554d42"],
  // 緑（植物・LED）
  leaf:  ["#22331d", "#3c5c31", "#588044", "#7bad5e"],
  // 確認を待つ状態。UI とオフィスの印で同じ色を使う。
  signal: ["#603a32", "#985d48", "#ce8a6c", "#f0bb91"],
  // R103-6: 時刻で変えるのは壁と窓からの光だけ。室内の材質・状態の色は既存のランプを保つ。
  wallLight: ["#171b1c", "#20251f", "#292e27", "#32382f"],
  dawn: ["#28353b", "#435661", "#6f8790", "#a6b4ac"],
  dusk: ["#342b30", "#604445", "#926452", "#c29370"],
  moon: ["#151e29", "#222f40", "#34475c", "#566d82"],
  red: ["#522b2c", "#8d4342", "#bf655d", "#e5967c"],
};

/** platform から渡される光の段。家具の色をまとめて染めるフィルターは使わない。 */
export const ROOM_LIGHT = Object.freeze({
  morning: Object.freeze({ wall: RAMP.wallLight[1], wallLit: RAMP.wallLight[2], beam: RAMP.dawn[2], beamShade: RAMP.dawn[0], alpha: 0.20 }),
  day: Object.freeze({ wall: RAMP.wallLight[2], wallLit: RAMP.wallLight[3], beam: RAMP.glass[2], beamShade: RAMP.glass[0], alpha: 0.24 }),
  evening: Object.freeze({ wall: RAMP.wallLight[1], wallLit: RAMP.wallLight[2], beam: RAMP.dusk[2], beamShade: RAMP.dusk[0], alpha: 0.15 }),
  night: Object.freeze({ wall: RAMP.wallLight[0], wallLit: RAMP.wallLight[1], beam: RAMP.moon[2], beamShade: RAMP.moon[0], alpha: 0.04 }),
});

/** R101（本人裁定 2026-09-29・燐光グリーン）: 画面の枠（スマホ PWA とコックピット）の端末の配色。部屋の canvas は描かない
 *  （部屋は RAMP のまま＝どちらのテーマでも暗い窓）。**厳密な JSON の書き方**（キーは二重引用符・末尾のカンマ無し）＝
 *  tools/gen_pwa_modules.py が json.loads で読み、`--t-<キー>` にして ui/pwa/app.css と ui/cockpit/style.css へ書き出す
 *  （コントラストの下限もそこで確かめる）。値を変えたら `python3 tools/gen_pwa_modules.py`。
 *  attn（❗）は dark で部屋の ❗ と同じ RAMP.signal[2]・stage は部屋の地 RAMP.ink[1]（design/check.py が突き合わせる）。 */
export const TERM = Object.freeze({"dark": {"bg": "#0c0f0c", "panel": "#111511", "panel-2": "#1c261c", "line": "#2a342a", "line-strong": "#3a463a", "line-soft": "#1c241c", "ink": "#d8e0d8", "ink-soft": "#a9b5a9", "ink-faint": "#869286", "accent": "#39d353", "on-accent": "#0c0f0c", "ok": "#39d353", "attn": "#ce8a6c", "warn": "#d8ae67", "danger": "#ff6b5e", "stage": "#1a1d19"}, "light": {"bg": "#f3f1e8", "panel": "#eeebe0", "panel-2": "#ddd8c8", "line": "#c4bfad", "line-strong": "#a9a391", "line-soft": "#dcd8cb", "ink": "#1d231d", "ink-soft": "#465046", "ink-faint": "#535c51", "accent": "#1a6b2e", "on-accent": "#f3f1e8", "ok": "#1a6b2e", "attn": "#8a4f3b", "warn": "#69502d", "danger": "#b3261e", "stage": "#1a1d19"}});

/** 等幅の並び（本人のモック design/cockpit.html の --mono を正本に）。和文は Hiragino へ落ちる。 */
export const MONO_FONT = "\"SF Mono\", \"Cascadia Code\", ui-monospace, Menlo, Monaco, Consolas, \"Hiragino Kaku Gothic ProN\", \"Hiragino Sans\", monospace";

/** UI も同じ色の正本を参照する（R101: 端末の配色 TERM のダーク）。canvas と別の色定義を増やさない。
 *  alert（❗）は部屋の ❗ と同じ RAMP.signal[2]＝TERM.dark.attn（design/check.py が突き合わせる）。 */
export const UI_COLORS = {
  bg: TERM.dark.bg, panel: TERM.dark.panel, "panel-2": TERM.dark["panel-2"],
  line: TERM.dark.line, "line-strong": TERM.dark["line-strong"], "line-soft": TERM.dark["line-soft"], ink: TERM.dark.ink,
  "ink-soft": TERM.dark["ink-soft"], "ink-faint": TERM.dark["ink-faint"],
  accent: TERM.dark.accent, ok: TERM.dark.ok, alert: TERM.dark.attn, warn: TERM.dark.warn,
  danger: TERM.dark.danger, stage: TERM.dark.stage,
};

/** セッション色。ロボ・台帳・端末タブ・会話ペインで同じ 1 本を使う。
 *  暗い部屋で読めるよう明度を揃え、彩度は少し落としてある（木と喧嘩しないため）。 */
export const SESSION_COLORS = [
  "#5fb6c4", "#8fbf5f", "#e09a4f", "#a889d6", "#dcc255", "#d67f9f",
  "#6fb096", "#c98268", "#7f9fd6",
];

/** ロボのドットの凡例 → 色。`a` はセッション色で差し替わる。 */
export const PALETTE = {
  o: RAMP.bot[0],      // いちばん暗い面（バイザー・関節の陰）
  b: RAMP.bot[1],      // 体
  B: RAMP.bot[2],      // 体の上面（光の当たる面）
  r: RAMP.bot[3],      // 縁（これが「暗い床で浮き上がる」の本体）
  s: "#5d76a8",       // 常駐 Bot の殻（shell-bot）
  a: null,             // 個体色（描画時に差し替え）
  c: null,             // 個体色の暗いほう（胸・足）
};

/** 家具のドットの凡例 → 色。材質ごとに 4 段の階段（a が暗く d が明るい）。 */
export const TILE_PALETTE = {
  a: RAMP.wood[0],  b: RAMP.wood[1],  c: RAMP.wood[2],  d: RAMP.wood[3],
  e: RAMP.steel[0], f: RAMP.steel[1], g: RAMP.steel[2], h: RAMP.steel[3],
  i: RAMP.cloth[0], j: RAMP.cloth[1], k: RAMP.cloth[2], l: RAMP.cloth[3],
  m: RAMP.leaf[0],  n: RAMP.leaf[1],  o: RAMP.leaf[2],  p: RAMP.leaf[3],
  q: RAMP.amber[0], r: RAMP.amber[1], s: RAMP.amber[2], t: RAMP.amber[3],
  u: RAMP.ink[0],   v: RAMP.ink[1],   w: RAMP.ink[2],   x: RAMP.ink[3],
  y: RAMP.shell[3],
};

/** 個体色を暗くする（胸のパネルや足に使う。目と耳あては明るいまま）。 */
export function dimTint(hex, k = 0.62) {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.round(((n >> 16) & 255) * k);
  const g = Math.round(((n >> 8) & 255) * k);
  const b = Math.round((n & 255) * k);
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}
