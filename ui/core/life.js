// R99-P4-L2: 部屋の「暮らし」＝散歩（本人「しっかりアバターが動くように」2026-09-28）。
// 返事を 1 分以上待っている机の人が、ときどき机を離れて給湯台・ボード・植物の前に立ち、また同じ机へ戻る。
// 部屋は事実だけを伝える（design/README.md「場所の意味」）: 散歩先に立つ＝「返事を 1 分以上待っている」。机は予約のまま。
//
// 純関数（DOM・時刻・乱数に触らない＝時刻は引数・個体差はキーのハッシュ）。roomLayout の結果（actors）を受けて、
// 散歩中の人の目的地（x, y）だけを散歩先へ差し替えた写しを返す。席の割り当て（assign）・人数（counts）・在席（occupied）・
// 区分（zone）・席（seat）は変えない＝フッターと台帳の数字が動かず、机の画面も点いたまま。歩く・戻るは pxwalk.stepWalkers
// （目的地が変われば今いる場所から経路を引き直す既存の掟）に任せる。固定時刻・停止・再生（live でない）は誰も動かさない。
import { LIFE_SPOTS, tintHash } from "./pxroom.js";

/**
 * 散歩の時間割（秒）。period 秒を slots 枠に割り、人ごとの枠（キーのハッシュの剰余）の先頭 span 秒だけ机を離れる。
 * start 秒より前（ページを開いた直後）は歩かせない。minAge 秒以上返事を待っている人だけ（事実に結び付ける）。
 * 既定: 240 秒ごとに 24 秒（往路 3〜10 秒＝R110-S4 の 24px/秒と立ち上がり込み・立ち 14〜21 秒・復路）＝1 人あたり 4 分に 1 回。
 */
export const LIFE = Object.freeze({ start: 30, period: 240, slots: 6, span: 24, minAge: 60 });

/**
 * 散歩してよい人か（机に座って返事を待っている・呼んでいない・退場中でない・age が minAge 以上）。
 * ツールで止まっている人（⚠＝kind tool か stuckTool）と考え中（kind think）は机に残す＝「返事を待っている」ではない
 *（台帳の行は代表以外の kind を空にする＝空は返事待ちとして扱う）。
 */
export function canStroll(a, minAge = LIFE.minAge) {
  return Boolean(a) && typeof a === "object" && a.zone === "desk" && a.state === "waiting" && !a.attention
    && a.leaving !== true && Number.isFinite(a.age) && a.age >= minAge
    && a.kind !== "tool" && a.kind !== "think" && !String(a.stuckTool ?? "").trim();
}

/**
 * key の人の散歩の窓。t が数でない・start より前は閉じている。
 * @returns {{open: boolean, slot: number, cycle: number}} slot＝その人の枠・cycle＝何周目か（行き先を周ごとに替える）
 */
export function strollWindow(key, t, o = LIFE) {
  const time = Number(t);
  if (!Number.isFinite(time) || time < o.start || !(o.period > 0) || !(o.slots >= 1)) return { open: false, slot: -1, cycle: -1 };
  const slot = tintHash(String(key)) % o.slots;
  const len = o.period / o.slots;
  const u = ((time % o.period) + o.period) % o.period;
  return { open: u >= slot * len && u < slot * len + o.span, slot, cycle: Math.floor(time / o.period) };
}

/**
 * 散歩中の人の目的地を散歩先へ差し替える。同じ散歩先に 2 人を立たせない（キーの昇順で先に取った人が勝ち、
 * 次の人は空いている別の散歩先へ・全部埋まっていれば机に残る）。入力は書き換えない。
 * @param {object[]} actors roomLayout().actors（key・zone・state・attention・age・x・y を読む）
 * @param {number} t 秒（動きの時計）
 * 割り当ては前のフレームから持ち越す（opts.prev＝前回の assign）: まだ散歩してよい人はその散歩先を先に確定し、新しく資格を得た人は
 * 空いている散歩先だけを取る＝1 人の状態が変わっても、散歩中の他の人の行き先は変わらない（roomLayout の「他の人は動かない」と同じ）。
 * @param {{live?: boolean, spots?: {x:number,y:number}[], life?: object, prev?: Map<string, number>|null, max?: number}} [opts]
 *   live は t が実時間で進んでいるときだけ true。max＝散歩に出してよい人数の上限（R110-S5: 部屋全体で机を離れる人数は
 *   用事が先・散歩は残り＝呼び手が `3 − 用事の人数` を渡す。省略は散歩先の数まで）。上限に掛かった人は机に残る
 *  （前のフレームから散歩していた人が先＝窓の途中の人から机へ戻さない）
 * @returns {{actors: object[], away: number, assign: Map<string, number>}} away＝散歩先へ差し替えた人数・assign＝key → 散歩先の添字
 */
export function lifeTargets(actors, t, opts = {}) {
  const o = opts && typeof opts === "object" ? opts : {};
  const list = Array.isArray(actors) ? actors : [];
  const spots = Array.isArray(o.spots) ? o.spots : LIFE_SPOTS;
  const life = o.life && typeof o.life === "object" ? o.life : LIFE;
  const assign = new Map();
  if (o.live !== true || !Number.isFinite(Number(t)) || !spots.length) return { actors: list, away: 0, assign };
  const prev = o.prev instanceof Map ? o.prev : null;
  const max = typeof o.max === "number" && Number.isFinite(o.max) ? Math.max(0, Math.floor(o.max)) : Infinity;
  const wants = [];
  list.forEach((a, i) => {
    if (!canStroll(a, life.minAge) || typeof a.key !== "string" || !a.key) return;
    const w = strollWindow(a.key, t, life);
    if (!w.open) return;
    const had = prev && Number.isInteger(prev.get(a.key)) && prev.get(a.key) >= 0 && prev.get(a.key) < spots.length
      ? prev.get(a.key) : -1;
    wants.push({ i, key: a.key, had, first: (tintHash(a.key) + w.cycle) % spots.length });
  });
  if (!wants.length) return { actors: list, away: 0, assign };
  // 前のフレームから散歩していた人を先に（キーの昇順）・次に新しく資格を得た人（キーの昇順）
  wants.sort((p, q) => (p.had >= 0) === (q.had >= 0) ? (p.key < q.key ? -1 : p.key > q.key ? 1 : 0) : (p.had >= 0 ? -1 : 1));
  const taken = new Set();
  const out = list.slice();
  let away = 0;
  for (const w of wants) {
    if (away >= max) break;                      // 上限（用事に出ている人の残り）＝残りの人は机に残る
    let pick = w.had >= 0 && !taken.has(w.had) ? w.had : -1;
    for (let d = 0; pick < 0 && d < spots.length; d += 1) {
      const j = (w.first + d) % spots.length;
      if (!taken.has(j)) pick = j;
    }
    if (pick < 0) continue;                      // 散歩先が全部埋まっている＝机に残る
    taken.add(pick);
    assign.set(w.key, pick);
    const s = spots[pick];
    out[w.i] = Object.freeze({ ...list[w.i], x: s.x, y: s.y, away: true });
    away += 1;
  }
  return { actors: out, away, assign };
}
