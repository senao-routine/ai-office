// ──────────────────────────────────────────────────────────────
// 時刻と乱数の唯一の供給源。
//
// これが無いとスクリーンショット回帰テストが全部フレークする。
// アニメーションは「経過時間の関数」として書き、Date.now() や
// requestAnimationFrame の実時刻を直接読まないこと（掟）。
//
//   通常:  now() は起動からの経過秒を返し、rand() は毎回違う値を返す
//   固定:  ?t=3.2&seed=11 を付けると now() は常に 3.2、rand() は seed 由来の決定論列
// ──────────────────────────────────────────────────────────────

const params = new URLSearchParams(typeof location === "undefined" ? "" : location.search);
const tParam = Number.parseFloat(params.get("t"));
const seedParam = Number.parseInt(params.get("seed") ?? "", 10);

/** 時刻が固定されているか（＝回帰テスト実行中か） */
export const frozen = Number.isFinite(tParam);
/** 固定時刻（frozen でなければ null） */
export const frozenAt = frozen ? tParam : null;

const started = typeof performance !== "undefined" ? performance.now() : 0;
const SEED = Number.isFinite(seedParam) ? seedParam >>> 0 : 0x9e3779b9;

let _state = SEED;
let _manual = frozen ? tParam : null;

/** 起動からの経過秒。frozen なら常に固定値。 */
export function now() {
  if (_manual !== null) return _manual;
  return (performance.now() - started) / 1000;
}

/** テストとGIF書き出し用: 時刻を手で進める。 */
export function setTime(seconds) {
  _manual = Number.isFinite(seconds) ? seconds : null;
}

/** 決定論の擬似乱数（線形合同法）。frozen かどうかに関わらず seed から再現できる。 */
export function rand() {
  _state = (Math.imul(_state, 1664525) + 1013904223) >>> 0;
  return _state / 4294967296;
}

/** 乱数列を初期状態へ戻す（シーン再構築時に呼ぶ＝毎回同じ配置になる） */
export function resetRand(seed = SEED) {
  _state = seed >>> 0;
}

/** 文字列から安定したハッシュを作る（個体ごとの位相差などに使う・乱数列を消費しない） */
export function hash(str) {
  let h = 2166136261 >>> 0;
  const s = String(str ?? "");
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h / 4294967296;
}

/**
 * アニメーションループ。frozen のときは1フレームだけ描いて止まる
 * （＝スクショが必ず同じ絵になる）。document.hidden では回さない（電池）。
 */
export function loop(onFrame) {
  let raf = 0;
  let stopped = false;
  if (frozen) {
    onFrame(now(), 0);
    return () => {};
  }
  let last = now();
  const tick = () => {
    if (stopped) return;
    const t = now();
    if (!document.hidden) onFrame(t, t - last);
    last = t;
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  return () => { stopped = true; cancelAnimationFrame(raf); };
}
