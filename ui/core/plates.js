// R110-S6: 部屋の名札（DOM）の置き場所＝純関数（DOM・通信・時刻・乱数に触らない＝tools/js_layer_lint.py）。
// 本人「どのロボが誰か、カーソルを合わせないと分からない」（2026-10-06）→ 全員の足元に名前の札を常に出す（コックピット）。
// 札の中身と寸法は呼び手（ui/cockpit/index.js）が DOM で測って渡す。ここは「どこに置くか」だけを決める:
//   1. 札の中心は足元の中心（cx）・上端は足元の 1px 下（top）＝スマホの PWA の名札（ui/pwa/bootpx.js の anchor・app.js の
//      paintPlates）と同じ基準。左右は部屋の枠（bounds）の内へ寄せる（中心をずらす・はみ出さない）。
//   2. 並びの順に置く。先に置いた札と重なれば、まず横へずらす（当たった札の右か左へ・札が持ち主の体の幅に掛かっている間だけ
//      ＝reach。受付・待機の列は 17 論理 px おきに並ぶ）。横で収まらなければ、その札の下へ 1 段ずつ逃がす（app.js の drop と同じ考え）。
//   3. 部屋の下へはみ出す札は頭の上（head）へ回し、そこからは上へ 1 段ずつ逃がす（受付・待機の列は部屋の下端に近い）。
//   4. どちらでも逃がし切れない（段の上限 tries を超えた・枠の外）札は、足元に重ねて置く＝**札を消さない**（全員分ある）。
//      重ねた札は overlap: true で返す（呼び手とテストが数える）。
//   5. 避けたい矩形（avoid＝他の人の頭上の印。canvas に描くので札が上に乗ると隠れる）に掛かる札は、札の丈までなら上へずらして
//      印の上端の手前で止める（机の段では下の段の人の印が足元のすぐ下にある）。ずらせない札は under: true（呼び手が薄くする）。
// 同じ入力なら同じ置き場所（決定論）。入力は書き換えない。
//
// 寸法は CSS px（呼び手が論理 px × 倍率で渡す）。部屋の枠は 0..bounds.w × 0..bounds.h。

/** 札と札の間に空ける隙間（CSS px）。左右 x・上下 y（app.js の drop の 3・2 に近い値）。 */
export const PLATE_GAP = Object.freeze({ x: 2, y: 2 });
/** 1 方向（下・上）へ逃がす段の上限（app.js の drop と同じ 6）。超えたら足元に重ねる＝遠くへ流して誰の札か分からなくしない。 */
export const PLATE_TRIES = 6;
/** 印を避けて上へずらしてよい幅（札の丈に対する割合）。札 1 枚ぶんまで＝それより上は人の胴に掛かる。 */
export const PLATE_NUDGE = 1;

const finite = (v) => typeof v === "number" && Number.isFinite(v);

/** 2 つの矩形が隙間（gx, gy）込みで重なるか */
function hits(a, b, gx, gy) {
  return a.left < b.left + b.w + gx && a.left + a.w + gx > b.left
    && a.top < b.top + b.h + gy && a.top + a.h + gy > b.top;
}

/**
 * 名札の置き場所を決める。
 * @param {Array<{cx:number, top:number, head?:number, w:number, h:number}|null>} plates
 *   cx＝足元の中心の x・top＝札を置きたい上端の y（足元の 1px 下）・head＝頭の上端の y（無ければ上へは回さない）・w×h＝札の寸法・
 *   reach＝横へずらしてよい範囲（札が [cx − reach, cx + reach]＝持ち主の体の幅に掛かっている間だけ・無ければ横へはずらさない）。
 *   数でない・0 以下の寸法の札は null を返す（置かない＝並びの位置は保つ）。
 * @param {{w:number, h:number}} bounds 部屋の枠（CSS px）
 * @param {{gap?: {x:number, y:number}, tries?: number, avoid?: Array<{left:number, top:number, w:number, h:number}>}} [opts]
 *   avoid＝札で隠したくない矩形（頭上の印）。札どうしの置き場所を決めた後で、掛かる札だけを上へずらす（札の丈まで）
 * @returns {Array<{left:number, top:number, w:number, h:number, side:"foot"|"down"|"up", overlap:boolean, under:boolean}|null>}
 *   left・top＝札の左上（枠の内・整数に丸めない＝呼び手が丸める）。side＝足元のまま／下へ逃がした／頭の上へ回した。
 *   under＝避けたい矩形に掛かったまま（ずらせなかった）。
 */
export function placePlates(plates, bounds, { gap = PLATE_GAP, tries = PLATE_TRIES, avoid = [] } = {}) {
  const list = Array.isArray(plates) ? plates : [];
  const W = finite(bounds?.w) && bounds.w > 0 ? bounds.w : 0;
  const H = finite(bounds?.h) && bounds.h > 0 ? bounds.h : 0;
  const gx = finite(gap?.x) && gap.x >= 0 ? gap.x : PLATE_GAP.x;
  const gy = finite(gap?.y) && gap.y >= 0 ? gap.y : PLATE_GAP.y;
  const n = Number.isInteger(tries) && tries >= 0 ? tries : PLATE_TRIES;
  const placed = [];
  const out = [];
  /** 上端 top の候補が枠の内で、置いた札のどれとも重ならないか。重なれば最初に当たった札を返す */
  const firstHit = (cand) => placed.find((q) => hits(cand, q, gx, gy)) || null;
  for (const p of list) {
    if (!p || !finite(p.cx) || !finite(p.top) || !finite(p.w) || !finite(p.h) || p.w <= 0 || p.h <= 0) {
      out.push(null);
      continue;
    }
    const w = p.w, h = p.h;
    const left = Math.min(Math.max(p.cx - w / 2, 0), Math.max(0, W - w));
    const inside = (top) => top >= 0 && top + h <= H;
    const reach = finite(p.reach) && p.reach > 0 ? p.reach : null;
    /** 同じ段で、当たった札の右か左へずらした候補（枠の内・持ち主の体の幅に掛かる・ほかの札に当たらない）。無ければ null */
    const aside = (top, hit) => {
      if (reach === null) return null;
      for (const l of [hit.left + hit.w + gx, hit.left - gx - w]) {
        if (l < 0 || l + w > W || l > p.cx + reach || l + w < p.cx - reach) continue;
        const cand = { left: l, top, w, h };
        if (!firstHit(cand)) return cand;
      }
      return null;
    };
    let got = null;
    // 足元から下へ（先に置いた札に当たれば、横へずらすか、その札の下端＋隙間へ）
    let top = p.top;
    for (let k = 0; k <= n && inside(top); k += 1) {
      const cand = { left, top, w, h };
      const hit = firstHit(cand);
      if (!hit) { got = { ...cand, side: k === 0 ? "foot" : "down" }; break; }
      const side = aside(top, hit);
      if (side) { got = { ...side, side: k === 0 ? "foot" : "down" }; break; }
      top = hit.top + hit.h + gy;
    }
    // 部屋の下へはみ出す（か逃がし切れない）札は頭の上から上へ
    if (!got && finite(p.head)) {
      top = p.head - gy - h;
      for (let k = 0; k <= n && inside(top); k += 1) {
        const cand = { left, top, w, h };
        const hit = firstHit(cand);
        if (!hit) { got = { ...cand, side: "up" }; break; }
        const side = aside(top, hit);
        if (side) { got = { ...side, side: "up" }; break; }
        top = hit.top - gy - h;
      }
    }
    // 逃がし切れない札は足元に重ねて置く（枠の内へ寄せる）＝消さない
    const entry = got ? { ...got, overlap: false }
      : { left, top: Math.min(Math.max(p.top, 0), Math.max(0, H - h)), w, h, side: "foot", overlap: true };
    placed.push(entry);
    out.push(entry);
  }
  // 避けたい矩形（頭上の印）に掛かる札は、札の丈まで上へずらして印の上端の手前で止める（ほかの札・印・枠に当たらない所だけ）
  const marks = (Array.isArray(avoid) ? avoid : []).filter((r) => r && finite(r.left) && finite(r.top)
    && finite(r.w) && finite(r.h) && r.w > 0 && r.h > 0);
  for (const e of placed) {
    e.under = false;
    if (!marks.length) continue;
    const covered = marks.filter((m) => hits(e, m, 0, 0));
    if (!covered.length) continue;
    const top = Math.min(...covered.map((m) => m.top)) - gy - e.h;
    const moved = { ...e, top };
    const free = top >= 0 && e.top - top <= e.h * PLATE_NUDGE
      && !marks.some((m) => hits(moved, m, 0, 0)) && !placed.some((q) => q !== e && hits(moved, q, gx, gy));
    if (free) e.top = top;
    else e.under = true;
  }
  return out.map((e) => (e ? Object.freeze(e) : e));
}
