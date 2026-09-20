// R98-W2: フロア帯（側面視・論理 480×72）の間取りと配置を決める純関数。
//
// 3D の間取り（ui/core/nav.js の LAYOUT）は**使わない**。あれは俯瞰の床座標で、側面視の帯とは
// 座標系も目的も違う（プラン §5「3D の床の写しは作らない」）。帯は「生きているか・どこに居るか・
// 呼んでいるか」の 3 つだけを 216px で伝えるための、独立した小さな間取りを持つ。
//
// 帯の間取り（論理 px・左上原点）:
//   y 0–6    壁の帯（窓 4 枚）
//   y 6–36   奥列: 会議室ガラス箱（右から 96）／ サーバー帯（右から 32）
//   y 36–72  手前列: 受付 x0–72（ボス＋❗の立ち位置 5）／ 机 72..W-48 ／ ラウンジ 右端 48
// 横は**可変**（W >= STRIP.min）。伸び縮みするのは机の帯だけで、受付と右端の家具は端に張り付く。
// ロボは 16×24。机は 1 プロジェクト 1 台（pitch は deskPitch が決める）。
import { deskPitch } from "./pxpose.js";   // core 同士は相対（node でそのまま走る）

export const STRIP = Object.freeze({
  width: 480, height: 72,          // width は**基準**（golden と単体テストの既定）。実際は可変。
  min: 400,                        // これより狭い帯は CSS が隠す（部屋が切れて人が見えなくなる）
  wall: Object.freeze({ y: 0, h: 6 }),
  back: Object.freeze({ y: 6, h: 30 }),
  front: Object.freeze({ y: 36, h: 36 }),
  reception: Object.freeze({ x: 0, w: 72 }),
  robot: Object.freeze({ w: 16, h: 24 }),
});

/**
 * 間取りの横位置。**部屋は画面の幅いっぱいに伸びる**（余白に家具を置かないと、右 4 割が
 * 空の床になって「窓の中の小さな絵」に見える＝実測）。受付は左端・ラウンジとサーバーは右端で、
 * 伸び縮みするのは机の帯だけ。width=480 のとき R98 設計どおりの値になる。
 * @param {number} width 論理幅（>= STRIP.min）
 */
export function stripAnchors(width = STRIP.width) {
  const w = Math.max(STRIP.min, Math.round(Number.isFinite(width) ? width : STRIP.width));
  return {
    width: w,
    reception: { x: 0, w: STRIP.reception.w },
    desks: { x: STRIP.reception.x + STRIP.reception.w, w: w - STRIP.reception.w - 48 },
    lounge: { x: w - 48, w: 48 },
    meeting: { x: w - 96, w: 64 },
    server: { x: w - 32, w: 32 },
  };
}

/** 受付の待ち位置（❗が立つ）。左から 5 つ。 */
const QUEUE_SLOTS = 5;
/** ラウンジの座り位置。 */
const LOUNGE_SLOTS = 3;
/** 会議室の立ち位置。 */
const MEETING_SLOTS = 4;

const clampInt = (v, lo, hi) => Math.min(hi, Math.max(lo, Math.round(Number.isFinite(v) ? v : lo)));

/**
 * 帯に出す配置を決める。**同じ入力なら必ず同じ配置**（golden の決定論）。
 * @param {object} world ui/core/world.js の world（agents / seats）
 * @param {object[]} board 台帳のグループ（プロジェクトの並び＝机の並びに使う）
 * @returns {{desks: Array, actors: Array, pitch: number}}
 *   desks: [{x, y, projectKey}] ／ actors: [{id, session, x, y, zone, facing, vendor, attention, projectKey}]
 */
export function stripLayout(world, board = [], width = STRIP.width) {
  const A = stripAnchors(width);
  const agents = world?.agents || [];
  const projects = board.length
    ? board.map((g) => ({ key: g.key, sessions: g.sessions }))
    : agents.map((a) => ({ key: a.id, sessions: [a] }));
  const { pitch } = deskPitch(projects.length, A.desks.w);
  const capacity = Math.max(1, Math.floor(A.desks.w / pitch));
  const seated = projects.slice(0, capacity);
  const deskY = STRIP.front.y + STRIP.front.h - STRIP.robot.h;      // 机の足元＝手前列の下端
  // 机の島は**帯の中央へ寄せる**。左詰めにすると、プロジェクトが少ないとき右半分が空の床になり
  // 「作りかけの部屋」に見える（実測）。pitch は変えない＝密度の意味は同じ。
  const span = seated.length * pitch;
  const left = A.desks.x + Math.max(0, Math.floor((A.desks.w - span) / 2));
  const desks = seated.map((p, i) => ({
    projectKey: p.key,
    x: left + i * pitch,
    y: deskY,
  }));
  const deskOf = new Map(desks.map((d) => [d.projectKey, d]));

  // 立ち位置の割り当ては**並び順で決める**（乱数を使わない＝毎フレーム同じ場所）。
  let queue = 0, lounge = 0, meeting = 0;
  const actors = [];
  // 同じ座標に 2 体を置かない。机は 1 プロジェクト 1 台なので、同じプロジェクトの 2 本目以降は
  // そのままだと**完全に重なって消える**（別モデルレビューが basic.json の (120,48) で実測）。
  // 空いている方へ 4px ずつ逃がす＝「机に 2 人ぶら下がっている」が絵で分かる。純関数のまま。
  const taken = new Set();
  const place = (x, y) => {
    const lo = 0, hi = A.width - STRIP.robot.w;
    const home = clampInt(x, lo, hi);
    for (let d = 0; d <= A.width; d += 4) {
      for (const cand of d === 0 ? [home] : [home + d, home - d]) {
        if (cand < lo || cand > hi || taken.has(`${cand},${y}`)) continue;
        taken.add(`${cand},${y}`);
        return cand;
      }
    }
    return home;                                   // 帯が埋まった＝重ねるしかない
  };
  for (const project of projects) {
    for (const a of project.sessions) {
      const zone = a.attention ? "queue" : (a.zone || "desk");
      let x, y, facing = 1;
      if (zone === "queue") {
        const slot = queue++ % QUEUE_SLOTS;
        x = A.reception.x + 8 + slot * 12;
        y = deskY;
        facing = -1;                                   // 受付に向かって立つ＝こちらを向く
      } else if (zone === "lounge" || zone === "rest") {
        const slot = lounge++ % LOUNGE_SLOTS;
        x = A.lounge.x + 4 + slot * 14;
        y = deskY;
      } else if (zone === "meeting") {
        const slot = meeting++ % MEETING_SLOTS;
        x = A.meeting.x + 6 + slot * 14;
        y = STRIP.back.y + STRIP.back.h - STRIP.robot.h + 6;
      } else {
        const desk = deskOf.get(project.key);
        if (!desk) continue;                           // 机にあぶれた＝帯には出さない（表には出る）
        x = desk.x;
        y = desk.y;
      }
      const row = clampInt(y, 0, STRIP.height - STRIP.robot.h);
      actors.push({
        id: a.id, session: a.session, projectKey: project.key,
        x: place(x, row),
        y: row,
        zone, facing, vendor: a.vendor || "claude", attention: Boolean(a.attention),
        // コマを決めるのは pxpose だが、材料（本人の状態）はここが運ぶ。
        // これを落とすと働いている人も待っている人も同じ idle の絵になる（別モデルレビューで実測）。
        state: a.state, kind: a.kind,
        // 証拠（直近 24h の hook 記録）。帯は「新しい結果が着いた瞬間」だけを ✓ で祝う。
        evidence: a.evidence || null,
      });
    }
  }
  return { desks, actors, pitch, width: A.width };
}

/**
 * 机・ラウンジ・会議室・サーバーの「動かない部分」。描画側はこれを 1 回だけ描く。
 * 数を world から受け取らない＝帯の地は常に同じ（ちらつかない）。幅だけが可変。
 */
export function stripRoom(width = STRIP.width) {
  const A = stripAnchors(width);
  const w = A.width;
  const windows = [];
  for (let x = 20; x + 84 <= w - 8; x += 116) windows.push({ x, y: 1, w: 84, h: 5 });
  const shelves = [];
  for (let x = 16; x + 40 <= A.meeting.x - 8; x += 60) shelves.push({ x, y: STRIP.back.y + 12, w: 40, h: 18 });
  const plants = [];
  for (let i = 0; i < shelves.length - 1; i += 1) {
    plants.push({ x: shelves[i].x + 48, y: STRIP.back.y + 18, w: 8, h: 12 });
  }
  return {
    // 壁は上端 6px。窓は大きく取り、下端に枠線を引く（明度差が無いと「空白」に見える）
    wall: { x: 0, y: STRIP.wall.y, w, h: STRIP.wall.h },
    windows,
    windowSill: { x: 0, y: STRIP.wall.h, w, h: 1 },
    // 奥列（y6–36）: 左から 棚 → 植物 → 会議室ガラス箱 → サーバー帯
    shelves,
    plants,
    meeting: { x: A.meeting.x, y: STRIP.back.y + 2, w: A.meeting.w, h: STRIP.back.h - 2 },
    server: { x: A.server.x, y: STRIP.back.y, w: A.server.w, h: STRIP.back.h },
    // 手前列の床と、受付カウンター・ラウンジのラグ
    floorLine: { x: 0, y: STRIP.front.y, w, h: 1 },
    reception: { x: A.reception.x + 2, y: STRIP.front.y + 20, w: A.reception.w - 10, h: 16 },
    lounge: { x: A.lounge.x, y: STRIP.height - 3, w: A.lounge.w - 2, h: 3 },
    width: w,
  };
}
