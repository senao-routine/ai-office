// R98-W2: フロア帯のドット絵が「どのコマを、どの向きで、何フレーム目で描くか」を決める純関数。
//
// なぜ core に置くか: 帯は見た目だが、**どのコマを出すかは世界の状態から決まる**。ここを純関数に
// しておくと ①node でユニットに掛けられる ②golden が決定論になる（時刻は引数・乱数を使わない）
// ③3D 側（poseFor）と同じ語彙で議論できる。DOM・時刻・乱数には触らない（tools/js_layer_lint.py）。
//
// コマ表（1 ベンダーあたり 14 コマ = 42 セル）:
//   idle 2 ／ type 2 ／ walk 4 ／ raise 2 ／ think 1 ／ rest 2 ／ done 1
// 側面視なので歩行は 1 方向だけ描き、左向きは X 反転で作る（コマ数が 1/4 で済む）。

/** セルの名前（ui/pixel/px/robots.js の文字グリッドのキーと 1:1） */
export const CELLS = Object.freeze([
  "idle0", "idle1", "type0", "type1", "walk0", "walk1", "walk2", "walk3",
  "raise0", "raise1", "think0", "rest0", "rest1", "done0",
]);

/** 1 ベンダーあたりのコマ数（tools/px_lint.py が「全ベンダー揃っているか」を見る） */
export const CELL_COUNT = CELLS.length;

/** ベンダー 3 種（それ以外は claude の殻に落とす＝「知らない相手」を描かない） */
export const VENDORS = Object.freeze(["claude", "codex", "openclaw"]);

const FPS = Object.freeze({ idle: 1, type: 2, walk: 4, raise: 3, think: 1, rest: 0.5, done: 2 });

/** アニメの種別 → その並び（ループする） */
const SEQ = Object.freeze({
  idle: ["idle0", "idle1"],
  type: ["type0", "type1"],
  walk: ["walk0", "walk1", "walk2", "walk3"],
  raise: ["raise0", "raise1"],
  think: ["think0"],
  rest: ["rest0", "rest1"],
  done: ["done0"],
});

/** 整数へ落とす（NaN・Infinity・負は 0） */
const nat = (v) => (Number.isFinite(v) && v > 0 ? Math.floor(v) : 0);

/**
 * 帯に出す 1 体の見た目を決める。
 * @param {object} agent ui/core/world.js の agent（state / zone / attention / pending / kind / verb / vendor / badge）
 * @param {number} t 秒（UI クロック。frozen なら固定＝golden が動かない）
 * @param {object} [opts] { walkPhase: 0..1 移動の位相（歩いている間だけ）, facing: -1|1, doneUntil: 秒 }
 * @returns {{cell: string, kind: string, flip: boolean, blink: boolean}}
 */
export function pxpose(agent, t, opts = {}) {
  const a = agent || {};
  const time = Number.isFinite(t) ? t : 0;
  const walking = Number.isFinite(opts.walkPhase);
  // 種別は「見ればわかる 7 つ」だけ。3D の 20 種類を持ち込まない（16×24 では描き分けられない）。
  // **歩いている間は walk が勝つ**（❗で受付へ歩く間も歩かせる）。挙手のコマのまま床を滑ると
  // 壊れて見えるため。「呼んでいる」は消えない＝頭上の点滅（blink）が attention に従って出続ける。
  let kind = "idle";
  if (walking) kind = "walk";
  else if (a.attention) kind = "raise";
  else if (Number.isFinite(opts.doneUntil) && time < opts.doneUntil) kind = "done";
  else if (a.state === "resting" || a.zone === "lounge") kind = "rest";
  else if (a.kind === "think") kind = "think";
  else if (a.state === "working") kind = "type";
  const seq = SEQ[kind];
  const rate = FPS[kind];
  const phase = kind === "walk"
    ? Math.floor(opts.walkPhase * seq.length)          // 歩行は距離で駆動（滑らない）
    : Math.floor(time * rate);
  const cell = seq[((phase % seq.length) + seq.length) % seq.length];
  return {
    cell,
    kind,
    // 側面視は 1 方向だけ描き、左へ歩くときは反転する
    flip: opts.facing === -1,
    // ❗は 2Hz で点滅（歩いている最中も点滅は続く＝「呼んでいる」が途切れない）
    blink: Boolean(a.attention) && Math.floor(time * 2) % 2 === 0,
  };
}

/** ベンダー名 → 描き分けのキー（未知は claude）。scene3d の判定と同じ語彙。 */
export function vendorCell(vendor) {
  const v = String(vendor || "").toLowerCase();
  return VENDORS.includes(v) ? v : "claude";
}

/**
 * 机の間隔（論理 px）。プロジェクトが増えても帯からはみ出さない純関数。
 * 机の帯は x=72..432（360px）。1 プロジェクト 1 机。
 * @returns {{pitch: number, capacity: number}} pitch は 16..24 に丸めた整数
 */
export function deskPitch(projects, width = 360, min = 16, max = 24) {
  const n = nat(projects);
  if (n <= 0) return { pitch: max, capacity: Math.floor(width / max) };
  const pitch = Math.min(max, Math.max(min, Math.floor(width / n)));
  return { pitch, capacity: Math.floor(width / pitch) };
}

/**
 * 帯の拡大率（整数倍のみ＝ドットが滲まない）。
 * @param {number} cssWidth 帯の CSS 幅
 * @param {number} logical 論理幅（既定 480）
 */
export function pxScale(cssWidth, logical = 480, max = 3) {
  const w = Number.isFinite(cssWidth) && cssWidth > 0 ? cssWidth : 0;
  return Math.min(max, Math.max(1, Math.floor(w / logical)));
}
