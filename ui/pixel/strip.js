// R98-W2: フロア帯。論理 480×72 を整数倍で描く（側面視・ドット絵）。
//
// 役割は 6 つだけ（プラン §2）: 生きているか／どこに居るか／呼んでいるか／ベンダーと個体色／
// 変化（歩く・入場・退勤）／留守中の成果。**名前も数字も帯には描かない**（それは表の仕事）。
// canvas に文字を描かない掟は tests/pixel_smoke.py が fillText を throw に差し替えて機械で確かめる。
//
// 決定論: 位置は ui/core/stripLayout.js（純関数）、コマは ui/core/pxpose.js（純関数）。
// ここは「受け取った配置とコマを、整数倍で塗る」だけ＝同じ world と同じ t なら必ず同じ絵。
import { CELLS, pxScale, pxpose, vendorCell } from "/ui/core/pxpose.js";
import { STRIP, stripLayout, stripRoom } from "/ui/core/stripLayout.js";
import { INSTANCE, PALETTE, ROOM, shellOf } from "./px/palette.js";
import { CLAUDE } from "./px/claude.js";
import { CODEX } from "./px/codex.js";
import { OPENCLAW } from "./px/openclaw.js";

const SHEETS = { claude: CLAUDE, codex: CODEX, openclaw: OPENCLAW };

/** 個体色: プロジェクト id から決める（同じプロジェクトはいつも同じ色・乱数を使わない）。 */
function tintFor(key) {
  let h = 0;
  for (let i = 0; i < String(key).length; i += 1) h = (h * 31 + String(key).charCodeAt(i)) >>> 0;
  return INSTANCE[h % INSTANCE.length];
}

/** 1 セル（16×24 の文字グリッド）を塗る。flip なら左右反転。 */
function blit(ctx, rows, x, y, s, { shell, tint }) {
  for (let ry = 0; ry < rows.length; ry += 1) {
    const row = rows[ry];
    for (let rx = 0; rx < row.length; rx += 1) {
      const ch = row[rx];
      if (ch === ".") continue;
      const color = ch === "s" ? shell : ch === "a" ? tint : PALETTE[ch];
      if (!color) continue;
      ctx.fillStyle = color;
      ctx.fillRect((x + rx) * s, (y + ry) * s, s, s);
    }
  }
}

/** 動かない部分（壁・窓・床・受付・会議室・サーバー・ラウンジ）。毎フレーム同じ。 */
function paintRoom(ctx, s, ox, full) {
  const room = stripRoom();
  const box = (r, color) => { ctx.fillStyle = color; ctx.fillRect((ox + r.x) * s, r.y * s, r.w * s, r.h * s); };
  // 地（壁・床・窓）は**画面幅いっぱい**に伸ばす＝部屋が続いて見える。什器と人だけ中央の論理 480 に置く。
  ctx.fillStyle = ROOM.wall;
  ctx.fillRect(0, 0, full * s, STRIP.front.y * s);
  ctx.fillStyle = ROOM.floor;
  ctx.fillRect(0, STRIP.front.y * s, full * s, (STRIP.height - STRIP.front.y) * s);
  ctx.fillStyle = ROOM.wallLine;
  ctx.fillRect(0, STRIP.wall.y * s, full * s, STRIP.wall.h * s);
  for (let x = 20 - ox; x < full; x += 116) {              // 窓は画面端まで等間隔で並べる
    if (x + 84 < 0) continue;
    ctx.fillStyle = ROOM.screen;
    ctx.fillRect(x * s, 1 * s, 84 * s, 5 * s);
    ctx.fillStyle = ROOM.deskEdge;
    ctx.fillRect(x * s, 6 * s, 84 * s, s);
  }
  ctx.fillStyle = ROOM.deskEdge;
  ctx.fillRect(0, STRIP.wall.h * s, full * s, s);
  // 奥列の什器（棚・植物）＝空白を「部屋」にする
  for (const sh of room.shelves) {
    box(sh, ROOM.desk);
    ctx.fillStyle = ROOM.deskEdge;
    for (let i = 0; i < 3; i += 1) ctx.fillRect((ox + sh.x) * s, (sh.y + 5 + i * 6) * s, sh.w * s, s);
    ctx.fillRect((ox + sh.x) * s, sh.y * s, sh.w * s, s);
  }
  for (const pl of room.plants) {
    ctx.fillStyle = PALETTE.B;                            // 葉（ティール寄りの緑）
    ctx.fillRect((ox + pl.x) * s, pl.y * s, pl.w * s, (pl.h - 4) * s);
    ctx.fillStyle = ROOM.desk;                            // 白い鉢
    ctx.fillRect((ox + pl.x + 1) * s, (pl.y + pl.h - 4) * s, (pl.w - 2) * s, 4 * s);
  }
  box(room.meeting, ROOM.glass);
  ctx.fillStyle = ROOM.glassRail;                         // 会議室の卓（ガラス箱の中）
  ctx.fillRect((ox + room.meeting.x + 8) * s, (room.meeting.y + room.meeting.h - 8) * s,
    (room.meeting.w - 16) * s, 2 * s);
  box(room.server, ROOM.server);
  ctx.fillStyle = ROOM.lamp;                              // サーバーの LED（位置固定＝ちらつかない）
  for (let i = 0; i < 4; i += 1) ctx.fillRect((ox + room.server.x + 2) * s, (room.server.y + 4 + i * 7) * s, s, s);
  ctx.fillStyle = ROOM.deskEdge;
  ctx.fillRect(0, STRIP.front.y * s, full * s, s);        // 床の境目も端まで
  box(room.reception, ROOM.desk);                         // 受付カウンター
  ctx.fillStyle = ROOM.deskEdge;
  ctx.fillRect((ox + room.reception.x) * s, room.reception.y * s, room.reception.w * s, s);
  box(room.lounge, ROOM.rug);                             // ラウンジのラグ
}

/** 机（1 プロジェクト 1 台）。ロボの足元に天板と小口を置く。 */
function paintDesks(ctx, desks, s, ox) {
  for (const d0 of desks) {
    const d = { ...d0, x: d0.x + ox };
    const w = STRIP.robot.w + 2;
    ctx.fillStyle = ROOM.desk;                      // 天板
    ctx.fillRect(d.x * s, (d.y + 15) * s, w * s, 2 * s);
    ctx.fillStyle = ROOM.deskEdge;                  // 小口（床との境目・無いと机が消える）
    ctx.fillRect(d.x * s, (d.y + 17) * s, w * s, s);
    ctx.fillStyle = ROOM.glassRail ?? ROOM.deskEdge;
    ctx.fillRect(d.x * s, (d.y + 18) * s, s, 5 * s);            // 脚
    ctx.fillRect((d.x + w - 1) * s, (d.y + 18) * s, s, 5 * s);
    ctx.fillStyle = ROOM.monitor;                   // 奥のモニタ（4×4・画面は発光）
    ctx.fillRect((d.x + 11) * s, (d.y + 11) * s, 4 * s, 4 * s);
    ctx.fillStyle = ROOM.screen;
    ctx.fillRect((d.x + 12) * s, (d.y + 12) * s, 2 * s, 2 * s);
  }
}

/**
 * 帯を作る。`ui/pixel/index.js` が mount で 1 回呼び、draw(world, board, t) を毎フレーム呼ぶ。
 * host: #viewport ／ frozen: UI クロックが止まっているか（golden・E2E の `?t=`）。
 * 時刻は**注入**する（clock.js を import しない＝ui-2d.md の掟）。
 */
export function init({ host, frozen = false }) {
  const canvas = document.createElement("canvas");
  canvas.width = STRIP.width;
  canvas.height = STRIP.height;
  host.append(canvas);
  const ctx = canvas.getContext("2d");
  let scale = 0;
  let hover = null;         // { project, session } ／ セッション行なら session が入る

  let logicalW = STRIP.width;
  const resize = () => {
    const hostW = host.clientWidth || STRIP.width;
    const s = pxScale(hostW);
    const lw = Math.max(STRIP.width, Math.floor(hostW / s));   // 画面幅いっぱいの論理幅
    if (s === scale && lw === logicalW) return s;
    scale = s; logicalW = lw;
    canvas.width = lw * s;
    canvas.height = STRIP.height * s;
    canvas.style.width = `${lw * s}px`;
    canvas.style.height = `${STRIP.height * s}px`;
    ctx.imageSmoothingEnabled = false;
    return s;
  };

  // 移動は**注入された時刻 t** で進める（Date.now は書かない＝ui-2d.md の掟）。
  // 目的地が変わった瞬間に「いま居る場所」から線を引き直す＝瞬間移動しない。
  // 持つのは**部屋の座標**（中央寄せの ox を含めない）。ox を混ぜると、窓の幅が変わっただけで
  // 全員が「歩いて追いかける」（別モデルレビュー medium で実測）。
  // 奥列（会議室）へは y も一緒に動く＝机から会議室へ飛び上がらない。
  const WALK_SPEED = 96;                    // 論理 px/秒（机から受付まで約 3 秒）
  const motion = new Map();                 // session → { fx, fy, tx, ty, t0, facing }
  const posOf = (m, time) => {
    const dist = Math.hypot(m.tx - m.fx, m.ty - m.fy);
    if (dist === 0 || time < m.t0) return { x: m.tx, y: m.ty };   // 時刻が戻った（再生の巻き戻し）＝その場へ
    const p = Math.min(1, ((time - m.t0) * WALK_SPEED) / dist);
    return { x: m.fx + (m.tx - m.fx) * p, y: m.fy + (m.ty - m.fy) * p };
  };

  let last = null;          // 直前に**描いた**配置（点の取得と stats に使う）
  let walking = 0;          // いま歩いている人数（スモークが「瞬間移動していない」を見る）
  let hovered = 0;          // いま光っている人数（セッション行なら 1 人だけ）
  let lastT = 0;            // 最後に**描いた時刻**（再生を止めたら帯も止まることの照準）
  const draw = (world, board, t) => {
    const s = resize();
    const time = Number.isFinite(t) ? t : 0;
    lastT = time;
    const ox = Math.floor((logicalW - STRIP.width) / 2);        // 什器と人を中央へ（描くときだけ足す）
    const { desks, actors: raw } = stripLayout(world, board);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    paintRoom(ctx, s, ox, logicalW);
    paintDesks(ctx, desks, s, ox);
    const live = new Set();
    const drawn = [];
    walking = 0;
    hovered = 0;
    for (const a0 of raw) {
      live.add(a0.session);
      let m = motion.get(a0.session);
      // 固定時刻（golden・E2E の `?t=`）では補間しない。時計が進まないので、配置が変わると
      // 旧位置で歩行姿勢のまま永久に止まる（別モデルレビューが inject で実測）。
      if (frozen) {
        m = { fx: a0.x, fy: a0.y, tx: a0.x, ty: a0.y, t0: time, facing: a0.facing };
        motion.set(a0.session, m);
      } else if (!m) {
        m = { fx: a0.x, fy: a0.y, tx: a0.x, ty: a0.y, t0: time, facing: a0.facing };  // 初登場は歩かせない
        motion.set(a0.session, m);
      } else if (m.tx !== a0.x || m.ty !== a0.y) {
        const here = posOf(m, time);
        m = { fx: here.x, fy: here.y, tx: a0.x, ty: a0.y, t0: time,
              facing: a0.x >= here.x ? 1 : -1 };
        motion.set(a0.session, m);
      }
      const pos = posOf(m, time);
      const moving = pos.x !== m.tx || pos.y !== m.ty;
      if (moving) walking += 1;
      // ドットは整数位置にしか置かない。ox は**ここで**足す（移動状態には入れない）
      const a = { ...a0, x: Math.round(pos.x) + ox, y: Math.round(pos.y) };
      drawn.push(a);
      const facing = moving ? m.facing : a0.facing;
      // 歩行の位相は**進んだ距離**で決める（経過秒で回すと歩幅と速度が合わない・R97-G の教訓）
      const walkPhase = moving ? (Math.hypot(pos.x - m.fx, pos.y - m.fy) % 16) / 16 : undefined;
      const pose = pxpose(a, time, { walkPhase, facing });
      const sheet = SHEETS[vendorCell(a.vendor)] || CLAUDE;
      const rows = sheet[pose.cell] || sheet[CELLS[0]];
      const shell = shellOf(a.vendor);
      const tint = tintFor(a.projectKey || a.id);
      if (pose.flip) {
        ctx.save();
        ctx.translate((a.x + STRIP.robot.w) * s, 0);
        ctx.scale(-1, 1);
        blit(ctx, rows, 0, a.y, s, { shell, tint });
        ctx.restore();
      } else {
        blit(ctx, rows, a.x, a.y, s, { shell, tint });
      }
      // ❗は頭の上で点滅（文字ではなく 2×5 の棒と点＝描画だけで伝える）。歩いている間も出る。
      if (pose.blink) {
        ctx.fillStyle = PALETTE.o;
        ctx.fillRect((a.x + 7) * s, (a.y - 6) * s, 2 * s, 4 * s);
        ctx.fillRect((a.x + 7) * s, (a.y - 1) * s, 2 * s, s);
      }
      // ホバー中の相手は足元に線を引く（行 ↔ 帯の対応）。**canvas の中**＝足元の 1px に重ねる。
      // セッション行は**その 1 体だけ**、プロジェクト行は**グループの全員**。
      // board は内訳の全員に同じ id を振り（＝id ではセッションを分けられない）、逆に
      // avatarMode=session では同じフォルダのセッションが別々の id を持つ（＝id ではグループを
      // 引けない）。どちらも別モデルレビューで実測した。だから session と group key で引く。
      const hot = hover && (hover.session ? a.session === hover.session : a.projectKey === hover.key);
      if (hot) {
        hovered += 1;
        ctx.fillStyle = PALETTE.a;
        ctx.fillRect(a.x * s, (a.y + STRIP.robot.h - 1) * s, STRIP.robot.w * s, s);
      }
    }
    for (const key of [...motion.keys()]) if (!live.has(key)) motion.delete(key);   // 退勤した分を捨てる
    last = new Map(drawn.map((a) => [a.session, a]));
  };

  return {
    draw,
    resize,
    setHover: (h) => {
      const v = typeof h === "string" ? { key: h, session: null } : h;
      hover = v && (v.session || v.key) ? v : null;
    },
    /** テストの照準・性能ゲート用 */
    stats: () => ({ drawCalls: 1, materials: 0, scale, logicalW, walking, hovered, t: lastT,
      actors: last ? last.size : 0 }),
    point: (id) => {
      const a = last && [...last.values()].find((x) => x.id === id);
      if (!a) return null;
      const r = canvas.getBoundingClientRect();
      return { left: r.left + (a.x + STRIP.robot.w / 2) * scale, top: r.top + (a.y + 4) * scale };
    },
    dispose: () => canvas.remove(),
  };
}
