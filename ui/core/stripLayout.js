// R98-W2: フロア帯（側面視・論理 480×72）の間取りと配置を決める純関数。
//
// 3D の間取り（ui/core/nav.js の LAYOUT）は**使わない**。あれは俯瞰の床座標で、側面視の帯とは
// 座標系も目的も違う（プラン §5「3D の床の写しは作らない」）。帯は「生きているか・どこに居るか・
// 呼んでいるか」の 3 つだけを 216px で伝えるための、独立した小さな間取りを持つ。
//
// 帯の間取り（論理 px・左上原点）:
//   y 0–6    壁の帯（窓 4 枚）
//   y 6–36   奥列: 会議室ガラス箱 x384–448 ／ サーバー帯 x448–480
//   y 36–72  手前列: 受付 x0–72（ボス＋❗の立ち位置 5）／ 机 x72–432 ／ ラウンジ x432–480
// ロボは 16×24。机は 1 プロジェクト 1 台（pitch は deskPitch が決める）。
import { deskPitch } from "./pxpose.js";   // core 同士は相対（node でそのまま走る）

export const STRIP = Object.freeze({
  width: 480, height: 72,
  wall: Object.freeze({ y: 0, h: 6 }),
  back: Object.freeze({ y: 6, h: 30 }),
  front: Object.freeze({ y: 36, h: 36 }),
  reception: Object.freeze({ x: 0, w: 72 }),
  desks: Object.freeze({ x: 72, w: 360 }),
  lounge: Object.freeze({ x: 432, w: 48 }),
  meeting: Object.freeze({ x: 384, w: 64 }),
  server: Object.freeze({ x: 448, w: 32 }),
  robot: Object.freeze({ w: 16, h: 24 }),
});

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
export function stripLayout(world, board = []) {
  const agents = world?.agents || [];
  const projects = board.length
    ? board.map((g) => ({ key: g.key, sessions: g.sessions }))
    : agents.map((a) => ({ key: a.id, sessions: [a] }));
  const { pitch } = deskPitch(projects.length, STRIP.desks.w);
  const capacity = Math.max(1, Math.floor(STRIP.desks.w / pitch));
  const seated = projects.slice(0, capacity);
  const deskY = STRIP.front.y + STRIP.front.h - STRIP.robot.h;      // 机の足元＝手前列の下端
  const desks = seated.map((p, i) => ({
    projectKey: p.key,
    x: STRIP.desks.x + i * pitch,
    y: deskY,
  }));
  const deskOf = new Map(desks.map((d) => [d.projectKey, d]));

  // 立ち位置の割り当ては**並び順で決める**（乱数を使わない＝毎フレーム同じ場所）。
  let queue = 0, lounge = 0, meeting = 0;
  const actors = [];
  for (const project of projects) {
    for (const a of project.sessions) {
      const zone = a.attention ? "queue" : (a.zone || "desk");
      let x, y, facing = 1;
      if (zone === "queue") {
        const slot = queue++ % QUEUE_SLOTS;
        x = STRIP.reception.x + 8 + slot * 12;
        y = deskY;
        facing = -1;                                   // 受付に向かって立つ＝こちらを向く
      } else if (zone === "lounge" || zone === "rest") {
        const slot = lounge++ % LOUNGE_SLOTS;
        x = STRIP.lounge.x + 4 + slot * 14;
        y = deskY;
      } else if (zone === "meeting") {
        const slot = meeting++ % MEETING_SLOTS;
        x = STRIP.meeting.x + 6 + slot * 14;
        y = STRIP.back.y + STRIP.back.h - STRIP.robot.h + 6;
      } else {
        const desk = deskOf.get(project.key);
        if (!desk) continue;                           // 机にあぶれた＝帯には出さない（表には出る）
        x = desk.x;
        y = desk.y;
      }
      actors.push({
        id: a.id, session: a.session, projectKey: project.key,
        x: clampInt(x, 0, STRIP.width - STRIP.robot.w),
        y: clampInt(y, 0, STRIP.height - STRIP.robot.h),
        zone, facing, vendor: a.vendor || "claude", attention: Boolean(a.attention),
      });
    }
  }
  return { desks, actors, pitch };
}

/**
 * 机・ラウンジ・会議室・サーバーの「動かない部分」。描画側はこれを 1 回だけ描く。
 * 数を world から受け取らない＝帯の地は常に同じ（ちらつかない）。
 */
export function stripRoom() {
  return {
    wall: { x: 0, y: STRIP.wall.y, w: STRIP.width, h: STRIP.wall.h },
    windows: [0, 1, 2, 3].map((i) => ({ x: 24 + i * 96, y: 1, w: 48, h: 4 })),
    floorLine: { x: 0, y: STRIP.front.y, w: STRIP.width, h: 1 },
    reception: { x: STRIP.reception.x, y: STRIP.front.y + 18, w: STRIP.reception.w - 8, h: 18 },
    meeting: { x: STRIP.meeting.x, y: STRIP.back.y + 4, w: STRIP.meeting.w, h: STRIP.back.h - 4 },
    server: { x: STRIP.server.x, y: STRIP.back.y + 2, w: STRIP.server.w, h: STRIP.back.h - 2 },
    lounge: { x: STRIP.lounge.x, y: STRIP.front.y + 20, w: STRIP.lounge.w - 4, h: 16 },
  };
}
