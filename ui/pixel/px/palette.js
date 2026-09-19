// R98-W2: フロア帯のドットのパレット。文字グリッド 1 文字 = 1 ドット = ここの 1 色。
//
// 色は art-direction v3 §2 の hex をそのまま使う（3D と同じ世界の色＝様式を変えても「同じオフィス」に見える）。
// 殻の色だけベンダーで差し替える（`s` を shellOf(vendor) に読み替える）。
// ここに無い色は使わない＝tools/px_lint.py が凡例外の文字を弾く。

/** 凡例 → 色。`.` は透明（描かない）。 */
export const PALETTE = Object.freeze({
  o: "#23213a",   // 輪郭（ink）
  s: "#e9e0d5",   // 殻（既定 = Claude。ベンダーで差し替え）
  f: "#262626",   // 顔プレート
  e: "#dfe9ff",   // 目・画面の発光
  a: "#7c5cff",   // アクセント（紫）
  h: "#9a9ab4",   // 影・金属（glass-rail）
  A: "#4f8dff",   // アクセント2（青）
  B: "#4fb3a3",   // 胸リング（working のティール）
});

/** ベンダー別の殻（art-direction v3 §2 の shell-claude / codex / openclaw） */
export const SHELL = Object.freeze({
  claude: "#e9e0d5",
  codex: "#787676",
  openclaw: "#bb4838",
});

/** 個体色（背中の 4×2 に差す 8 色）。3D の PROJECT_COLORS と同じ並び。 */
export const INSTANCE = Object.freeze([
  "#7c5cff", "#4f8dff", "#4fb3a3", "#f5a524",
  "#e0538a", "#8a8ee6", "#aab2d8", "#7aa070",
]);

/** ダークでも読めるように、帯の地と線はトークンから引く（CSS 側で上書きする）。 */
export const ROOM = Object.freeze({
  floor: "#e7ecfb",
  floorLine: "#d3d8ee",
  wall: "#f4f4fb",
  wallLine: "#e3e3f2",
  desk: "#f6f7fd",
  deskEdge: "#dcdff0",
  glass: "#b8b4ff",
  monitor: "#1d1b30",
  screen: "#dfe9ff",
  server: "#3a3f66",
  lamp: "#ffd9a0",
});

export function shellOf(vendor) {
  return SHELL[String(vendor || "").toLowerCase()] || SHELL.claude;
}
