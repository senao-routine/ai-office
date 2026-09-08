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

/** Epoch seconds for absence detection across reloads/sleep; frozen never reads a clock. */
export const epochNow = () => frozen ? 0 : Date.now() / 1000;

export const localDayStart = (epoch) => new Date(epoch * 1000).setHours(0, 0, 0, 0) / 1000;

/** Local calendar days overlapping an absence, bounded by the API's 90-day retention. */
export function absenceDays(since, until) {
  if (!(since > 0) || until < since) return [];
  const first = new Date(since * 1000);
  const cursor = new Date(until * 1000);
  first.setHours(0, 0, 0, 0); cursor.setHours(0, 0, 0, 0);
  const days = [];
  while (cursor >= first && days.length < 90) {
    days.push(`${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}-${String(cursor.getDate()).padStart(2, "0")}`);
    cursor.setDate(cursor.getDate() - 1);
  }
  return days;
}

/** Lighting wall-clock hours. Explicit hour wins over the frozen daytime default. */
export function localHour() {
  const fixed = Number.parseFloat(params.get("hour"));
  if (Number.isFinite(fixed)) return Math.min(24, Math.max(0, fixed));
  if (frozen) return 11;
  const date = new Date();
  return date.getHours() + date.getMinutes() / 60 + date.getSeconds() / 3600;
}

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

/** Rebuild procedural static geometry without consuming the live random stream. */
export const randState = () => _state;
export function withRandState(state, build) {
  const previous = _state;
  _state = state >>> 0;
  try { return build(); }
  finally { _state = previous; }
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
