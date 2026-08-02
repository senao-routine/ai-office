// オフィスの静的な造作。R50-P3(8) で白紙から設計し直した一枚の間取り。
// R50-P3(9) 「狭い」FBを受けて床を 19.5×13 → 23.4×15.6（+44%）へ拡張。
// 壁に付く什器は WALL 定数から導出する＝床サイズを変えても壁から剥がれない。
//
// ── 間取り図（上=奥/-z・右=+x。座標はメートル）─────────────────────
//
//   ┌──────────────────── 窓の壁 (z=-8.3) ────────────────────┐
//   │ ◇会議プラットフォーム  ◇本棚×2 ◇コーヒーバー ◇サーバーウォール │
//   │   卓+椅子5+白板                          ラック5本・紫LED    │
//   │                                          ▒ガラス仕切り      │
// 窓│ ◇発表ステージ    ◇デスクプラットフォーム        (通路)        │
// の│   白板+光る壇      机の島 3×2（12席・大モニタ）               │
// 壁│                                          ◇コンソール(右壁)  │
// (x│ ◇ソファヌック                              =OpenClaw席      │
// =-│   紫サイン+長ソファ                                          │
// 11.7)│             ◇受付+待機列（❗の列）   ◇ラウンジ            │
//   │                                       L字ソファ+プーフ      │
//   └──────────────────────────────────────────────────────┘
//
// 設計原則（8ラウンドの教訓）:
//   1. 物は減らして大きく。ただし床を広げたら「島の間隔」で広さを見せ、
//      壁際は本棚・バー・ラックで埋める（空白の壁際が殺風景の主因）。
//   2. 全ゾーンは「角丸の台座＋ネオン縁」で統一。床の余白は台座で覆う。
//   3. テクスチャを貼る面は必ず PlaneGeometry（slab の UV はメートル単位＝貼ると壊れる）。
//   4. 立てる板は upright()（Y回転だけだと平置きになる）。
//   5. すべて buildStaticBatches で畳む＝何個置いても drawCalls はマテリアル数まで。
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { LAYOUT, PODS, WALL } from "/ui/core/nav.js";
import { buildStaticBatches } from "./merge.js";

// ── レイアウト定数 ─────────────────────────────────────────────
// R58: LAYOUT/WALL/PODS の正本は ui/core/nav.js（歩行ナビの障害物矩形と
// 描画物を同じ数字から導出する＝二重管理でズレると机すり抜けが再発する）。
// ここは互換のため再エクスポートする。
export { LAYOUT, WALL };

export function seatAnchors() {
  const out = [];
  for (const [cx, cz] of PODS) {
    for (const front of [1, -1]) {
      out.push({
        x: cx, z: cz + front * 1.32,
        yaw: front > 0 ? Math.PI : 0,
        y: LAYOUT.deskZone.lift,
      });
    }
  }
  return out;                                   // 12席
}

/** 会議アンカーを部屋別に返す（R70: 3室分散の割当= core assignMeetingRooms が使う）。 */
export function meetingAnchorsByRoom() {
  const m = LAYOUT.meetZone;
  const n = LAYOUT.meet2Zone;
  const k = LAYOUT.meet3Zone;
  return {
    meet: [
      // 発表者はメイン会議室の白板（奥壁）の前に立つ
      { x: m.x - 1.4, z: m.z - 1.55, yaw: Math.PI, y: m.lift, role: "present" },
      { x: m.x - 1.6, z: m.z + 1.15, yaw: Math.PI, y: m.lift },
      { x: m.x, z: m.z + 1.15, yaw: Math.PI, y: m.lift },
      { x: m.x + 1.6, z: m.z + 1.15, yaw: Math.PI, y: m.lift },
      { x: m.x + 0.8, z: m.z - 1.15, yaw: 0, y: m.lift },
    ],
    meet2: [
      { x: n.x - 1.1, z: n.z + 1.25, yaw: Math.PI, y: n.lift },
      { x: n.x + 0.45, z: n.z + 1.25, yaw: Math.PI, y: n.lift },
      { x: n.x - 0.3, z: n.z - 1.25, yaw: 0, y: n.lift },
    ],
    meet3: [
      // 第3会議室（小・右手前）: 小卓の南北に1席ずつ
      { x: k.x - 0.35, z: k.z + 0.95, yaw: Math.PI, y: k.lift },
      { x: k.x + 0.35, z: k.z - 0.95, yaw: 0, y: k.lift },
    ],
  };
}

/** 会議ゾーンのflat一覧（互換・部屋順= meet→meet2→meet3）。 */
export function meetingAnchors() {
  const rooms = meetingAnchorsByRoom();
  return [...rooms.meet, ...rooms.meet2, ...rooms.meet3];
}

export function loungeAnchors() {
  const l = LAYOUT.loungeZone;
  return [
    { x: l.x + 1.5, z: l.z + 0.7, yaw: -1.7, y: l.lift + 0.05, role: "tablet" },
    { x: l.x - 1.5, z: l.z - 0.35, yaw: 0.15, y: l.lift },
    { x: l.x - 0.3, z: l.z - 0.35, yaw: -0.15, y: l.lift },
  ];
}

/** ❗の待機列＝受付の前。顔がカメラを向く（yaw 0 = +z）。
 *  R70: 2列×6=12席（机12台ぶん・❗大量発生でも整列）。2列目は受付から見て後ろ（北側）。 */
export function queueAnchors() {
  const q = LAYOUT.queueZone;
  const out = [];
  for (let row = 0; row < 2; row++) {
    for (let i = 0; i < 6; i++) {
      out.push({ x: q.x + 1.1 * i, z: q.z - row * 1.1, yaw: 0, y: 0 });
    }
  }
  return out;                                   // 12席（前列0..5・後列6..11）
}

/** R56: 会議チビロボ（部下）の立ち位置。親アンカーが使わない卓の縁に置く。
 *  座標は各会議卓（meet=4.2×1.6 / meet2=3.0×1.35）の縁からの相対で決め、
 *  最終位置は __debugScene.project のスクショ照準で確認する（暗算しない型）。 */
export function chibiSeats() {
  const m = LAYOUT.meetZone;
  const n = LAYOUT.meet2Zone;
  return {
    meet: [
      { x: m.x - 2.55, z: m.z - 0.2, yaw: Math.PI / 2, y: m.lift },   // 西端→東向き
      { x: m.x + 2.55, z: m.z - 0.2, yaw: -Math.PI / 2, y: m.lift },  // 東端→西向き
      { x: m.x - 0.6, z: m.z - 1.2, yaw: 0, y: m.lift },              // 北側の空き
      { x: m.x + 2.2, z: m.z + 1.15, yaw: Math.PI, y: m.lift },       // 南側東の空き
    ],
    meet2: [
      { x: n.x - 1.85, z: n.z, yaw: Math.PI / 2, y: n.lift },
      { x: n.x + 1.7, z: n.z, yaw: -Math.PI / 2, y: n.lift },
      { x: n.x + 0.85, z: n.z - 1.25, yaw: 0, y: n.lift },
      { x: n.x + 1.5, z: n.z + 1.25, yaw: Math.PI, y: n.lift },
    ],
    meet3: (() => {
      const k = LAYOUT.meet3Zone;
      return [
        { x: k.x - 1.05, z: k.z, yaw: Math.PI / 2, y: k.lift },   // 西端→東向き
        { x: k.x + 1.05, z: k.z, yaw: -Math.PI / 2, y: k.lift },  // 東端→西向き
        { x: k.x + 0.75, z: k.z + 0.95, yaw: Math.PI, y: k.lift },
        { x: k.x - 0.75, z: k.z - 0.95, yaw: 0, y: k.lift },
      ];
    })(),
  };
}

/** コーヒーバー前の立ち位置（所作エンジン用・バー位置と一緒に動く）。 */
export const COFFEE_STOP = Object.freeze({ x: 3.9, z: WALL.back + 1.85 });

/** エントランス（右手前の角）。出勤してくるロボはここから入る。 */
export const ENTRANCE = Object.freeze({ x: -8.3, z: WALL.front - 0.85 });

/** ボスの席（椅子の座面位置・ボスロボが常駐する）。 */
export const BOSS_SEAT = Object.freeze({ x: -0.6, z: WALL.back + 1.25, baseY: 0.26 });

/** OpenClaw 等の外部社員＝右壁のコンソール席（壁に向かって立つ）。 */
export function externalAnchors() {
  return [0, 1, 2].map((i) => ({
    x: WALL.right - 1.35, z: -4.4 + i * 1.8, yaw: Math.PI / 2, y: 0,
  }));
}

// ── 手続きテクスチャ（全部決定論。Math.random / Date.now 禁止） ──────
/** キーボードのキー面。のっぺりした板は安く見える。 */
export function keyboardTexture() {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 96;
  const g = c.getContext("2d");
  g.fillStyle = "#e9ecf6"; g.fillRect(0, 0, 256, 96);
  for (let row = 0; row < 4; row++) {
    for (let col = 0; col < 14; col++) {
      const x = 8 + col * 17.2 + (row === 3 ? 6 : row * 3);
      g.fillStyle = "#c6cddf"; g.fillRect(x, 10 + row * 19, 14, 15);
      g.fillStyle = "#dfe4f0"; g.fillRect(x + 1, 11 + row * 19, 12, 12);
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/**
 * 環境マップ（equirect）。床にラックの紫が、ガラスに窓の白筋が映る。
 * scene.environment に入れるだけで内部で PMREM される。追加ドロー0。
 */
export function envTexture() {
  const c = document.createElement("canvas");
  c.width = 512; c.height = 256;
  const g = c.getContext("2d");
  const sky = g.createLinearGradient(0, 0, 0, 128);
  sky.addColorStop(0, "#ffffff"); sky.addColorStop(1, "#d7e2fb");
  g.fillStyle = sky; g.fillRect(0, 0, 512, 128);
  const flr = g.createLinearGradient(0, 128, 0, 256);
  flr.addColorStop(0, "#c3cde8"); flr.addColorStop(1, "#8f9ac0");
  g.fillStyle = flr; g.fillRect(0, 128, 512, 128);
  g.fillStyle = "rgba(255,255,255,.92)";
  for (const x of [40, 96, 152, 300, 356, 412]) g.fillRect(x, 34, 34, 92);
  const nz = g.createLinearGradient(210, 60, 210, 150);
  nz.addColorStop(0, "rgba(124,92,255,.85)"); nz.addColorStop(1, "rgba(124,92,255,0)");
  g.fillStyle = nz; g.fillRect(196, 60, 52, 90);
  g.fillStyle = "rgba(79,201,255,.65)"; g.fillRect(258, 74, 22, 60);
  g.fillStyle = "rgba(70,150,105,.45)"; g.fillRect(452, 104, 46, 40);
  const t = new THREE.CanvasTexture(c);
  t.mapping = THREE.EquirectangularReflectionMapping;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** 床タイル。市松＋中心の明かり＋微ノイズ＋目地（1.6m角・slabのメートルUVを利用）。 */
export function floorTexture() {
  const c = document.createElement("canvas");
  c.width = 512; c.height = 512;
  const g = c.getContext("2d");
  let s2 = 20260729 >>> 0;
  const r = () => (s2 = (Math.imul(s2, 1664525) + 1013904223) >>> 0) / 4294967296;
  g.fillStyle = "#d2daf1"; g.fillRect(0, 0, 512, 512);
  g.fillStyle = "#c9d3ee"; g.fillRect(0, 0, 256, 256); g.fillRect(256, 256, 256, 256);
  const grd = g.createRadialGradient(256, 210, 40, 256, 256, 340);
  grd.addColorStop(0, "rgba(255,255,255,.30)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd; g.fillRect(0, 0, 512, 512);
  for (let i = 0; i < 600; i++) {
    g.fillStyle = `rgba(${r() < 0.5 ? 130 : 255},${r() < 0.5 ? 140 : 255},190,.05)`;
    g.fillRect((r() * 512) | 0, (r() * 512) | 0, 3, 3);
  }
  g.fillStyle = "#9dabd6"; g.fillRect(0, 0, 512, 5); g.fillRect(0, 0, 5, 512);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(1 / 1.6, 1 / 1.6);
  return t;
}

/** 木目（デスク天板用・ほぼ白地に淡い杢目＝マテリアル色に乗算される）。 */
export function woodTexture() {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 256;
  const g = c.getContext("2d");
  g.fillStyle = "#ffffff"; g.fillRect(0, 0, 256, 256);
  let s2 = 20260730 >>> 0;
  const r = () => (s2 = (Math.imul(s2, 1664525) + 1013904223) >>> 0) / 4294967296;
  for (let i = 0; i < 26; i++) {
    const y = i * 10 + r() * 6;
    g.strokeStyle = `rgba(120, 82, 40, ${0.05 + r() * 0.07})`;
    g.lineWidth = 1 + r() * 1.6;
    g.beginPath();
    g.moveTo(0, y);
    for (let x = 0; x <= 256; x += 32) g.lineTo(x, y + Math.sin(x * 0.03 + i) * 3 + r() * 2);
    g.stroke();
  }
  for (let i = 0; i < 5; i++) {                      // 節
    const x = r() * 256, y = r() * 256;
    g.strokeStyle = "rgba(110, 74, 36, .10)";
    g.lineWidth = 1.4;
    g.beginPath(); g.ellipse(x, y, 5 + r() * 7, 2.5 + r() * 3, 0, 0, 7); g.stroke();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(0.8, 0.8);
  return t;
}

/** カーペットの織り目（ラグ・ランナー用・白地の点綴り＝色は材質側）。 */
export function rugTexture() {
  const c = document.createElement("canvas");
  c.width = 128; c.height = 128;
  const g = c.getContext("2d");
  g.fillStyle = "#ffffff"; g.fillRect(0, 0, 128, 128);
  let s2 = 926 >>> 0;
  const r = () => (s2 = (Math.imul(s2, 1664525) + 1013904223) >>> 0) / 4294967296;
  for (let y = 0; y < 128; y += 4) {
    for (let x = 0; x < 128; x += 4) {
      const v = 0.88 + r() * 0.12;
      g.fillStyle = `rgba(${(v * 255) | 0}, ${(v * 255) | 0}, ${(v * 250) | 0}, 1)`;
      g.fillRect(x, y, 4, 4);
    }
  }
  g.fillStyle = "rgba(255,255,255,.25)";
  for (let y = 0; y < 128; y += 8) g.fillRect(0, y, 128, 1);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(2.4, 2.4);
  return t;
}

/** 窓の外。グラデーションの空＋遠景ビル＋雲（固定配列＝決定論）。 */
export function skyTexture() {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 256;
  const g = c.getContext("2d");
  const grd = g.createLinearGradient(0, 0, 0, 256);
  grd.addColorStop(0, "#8f9ff0");
  grd.addColorStop(0.4, "#b9c8fb");
  grd.addColorStop(0.75, "#dfe9ff");
  grd.addColorStop(1, "#cbb8ff");
  g.fillStyle = grd; g.fillRect(0, 0, 256, 256);
  const towers = [[10, 150, 26], [42, 176, 18], [66, 132, 30], [104, 168, 22],
    [132, 118, 34], [172, 158, 24], [202, 140, 28], [232, 172, 20]];
  for (const [x, top, w] of towers) {
    g.fillStyle = "rgba(124,92,255,.38)"; g.fillRect(x, top, w, 256 - top);
    g.fillStyle = "rgba(255,255,255,.35)";
    for (let y = top + 8; y < 240; y += 12) {
      for (let k = 3; k < w - 4; k += 8) g.fillRect(x + k, y, 3, 5);
    }
  }
  g.fillStyle = "rgba(255,255,255,.55)";
  for (const [cx, cy, r] of [[60, 46, 20], [78, 40, 26], [188, 62, 22], [208, 56, 16]]) {
    g.beginPath(); g.arc(cx, cy, r, 0, 7); g.fill();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** モニタの画面（コード/グラフ/ダッシュ/端末）＝働いている証拠。 */
export function screenTexture(kind, seed) {
  const c = document.createElement("canvas");
  c.width = 512; c.height = 320;
  const g = c.getContext("2d");
  let s = seed >>> 0;
  const r = () => (s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296;
  g.fillStyle = "#10162e"; g.fillRect(0, 0, 512, 320);
  g.fillStyle = "#1c2450"; g.fillRect(0, 0, 512, 30);
  ["#ff5f57", "#febc2e", "#28c840"].forEach((col, i) => {
    g.fillStyle = col; g.beginPath(); g.arc(20 + i * 20, 15, 6, 0, 7); g.fill();
  });
  if (kind === "code") {
    const cols = ["#7aa2ff", "#c792ea", "#89ddff", "#c3e88d", "#ffcb6b", "#f78c6c"];
    for (let i = 0; i < 15; i++) {
      let x = 22 + Math.floor(r() * 3) * 22;
      const y = 48 + i * 17;
      for (let k = 0, n = 2 + Math.floor(r() * 4); k < n && x < 470; k++) {
        const w = 26 + r() * 90;
        g.fillStyle = cols[Math.floor(r() * cols.length)];
        g.fillRect(x, y, w, 8);
        x += w + 10;
      }
    }
    g.fillStyle = "#8ee06a"; g.fillRect(22, 303, 10, 10);
  } else if (kind === "chart") {
    g.fillStyle = "#9db3ff"; g.fillRect(22, 48, 120, 10);
    for (let i = 0; i < 9; i++) {
      const h = 30 + r() * 150;
      const grd = g.createLinearGradient(0, 300 - h, 0, 300);
      grd.addColorStop(0, "#8a6bff"); grd.addColorStop(1, "#4f8dff");
      g.fillStyle = grd; g.fillRect(38 + i * 50, 300 - h, 32, h);
    }
  } else if (kind === "dash") {
    for (let i = 0; i < 3; i++) {
      g.fillStyle = "#1c2450"; g.fillRect(22 + i * 160, 46, 148, 78);
      g.fillStyle = ["#8a6bff", "#62d9ff", "#5fd39b"][i];
      g.fillRect(34 + i * 160, 60, 60, 9);
    }
    g.fillStyle = "#1c2450"; g.fillRect(22, 138, 468, 160);
    g.strokeStyle = "#8a6bff"; g.lineWidth = 4; g.beginPath();
    for (let i = 0; i <= 20; i++) {
      const y = 220 + Math.sin(i * 0.55 + seed) * 46 - i * 1.5;
      i ? g.lineTo(30 + i * 22, y) : g.moveTo(30, y);
    }
    g.stroke();
  } else {
    g.fillStyle = "#0a0f24"; g.fillRect(0, 30, 512, 290);
    for (let i = 0; i < 13; i++) {
      const y = 52 + i * 20;
      g.fillStyle = "#5fd39b"; g.fillRect(22, y, 12, 8);
      g.fillStyle = ["#cfd7ff", "#7aa2ff", "#ffcb6b"][Math.floor(r() * 3)];
      g.fillRect(42, y, 60 + r() * 300, 8);
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** 紫のネオンサイン（参考画像の "Think Big, Automate Smart" の位置）。 */
function signTexture() {
  const c = document.createElement("canvas");
  c.width = 512; c.height = 224;
  const g = c.getContext("2d");
  const grd = g.createLinearGradient(0, 0, 512, 224);
  grd.addColorStop(0, "#7c5cff"); grd.addColorStop(1, "#a06bff");
  g.fillStyle = grd;
  g.beginPath();
  const rr = 36;
  g.moveTo(rr, 0); g.lineTo(512 - rr, 0); g.quadraticCurveTo(512, 0, 512, rr);
  g.lineTo(512, 224 - rr); g.quadraticCurveTo(512, 224, 512 - rr, 224);
  g.lineTo(rr, 224); g.quadraticCurveTo(0, 224, 0, 224 - rr);
  g.lineTo(0, rr); g.quadraticCurveTo(0, 0, rr, 0);
  g.fill();
  g.fillStyle = "#ffffff";
  g.font = "italic bold 58px 'Hiragino Kaku Gothic ProN', sans-serif";
  g.fillText("Think Big,", 48, 92);
  g.fillText("Ship Fast", 48, 168);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** ホワイトボードの中身（紫のグラフと付箋）。 */
function boardTexture() {
  const c = document.createElement("canvas");
  c.width = 512; c.height = 320;
  const g = c.getContext("2d");
  g.fillStyle = "#fdfdff"; g.fillRect(0, 0, 512, 320);
  g.strokeStyle = "#8a6bff"; g.lineWidth = 6; g.beginPath();
  for (let i = 0; i <= 10; i++) {
    const y = 240 - i * 14 + Math.sin(i * 1.1) * 24;
    i ? g.lineTo(48 + i * 42, y) : g.moveTo(48, y);
  }
  g.stroke();
  g.strokeStyle = "#c9c6e6"; g.lineWidth = 3;
  g.beginPath(); g.moveTo(48, 270); g.lineTo(470, 270);
  g.moveTo(48, 60); g.lineTo(48, 270); g.stroke();
  g.fillStyle = "#5b5878"; g.fillRect(48, 26, 150, 12);
  for (const [x, y, col] of [[380, 40, "#ffd98a"], [430, 40, "#a9e5ff"], [405, 90, "#ffb3c7"]]) {
    g.fillStyle = col; g.fillRect(x, y, 42, 36);
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// R68: ホログラムパネルの配置正本 [x, y, z, 幅, yaw]。
// 本体は scene3d が個別メッシュ（回転＋脈動）・ネオン縁は buildOffice の静的バッチ。
export const HOLO_PANELS = Object.freeze([
  [-0.6, 2.05, -2.6, 1.05, 0.45],
  [LAYOUT.meetZone.x + 1.0, 2.3, LAYOUT.meetZone.z + 0.4, 1.3, 0.35],
  [WALL.right - 1.6, 2.35, -2.6, 0.95, -1.1],
]);

// ── ジオメトリと行列のヘルパ ────────────────────────────────────
const slabCache = new Map();
/** 角丸スラブ。※UVはメートル単位＝テクスチャを貼る面には使わない（床は例外）。 */
export function slab(w, h, d, r) {
  const key = `${w}|${h}|${d}|${r}`;
  if (slabCache.has(key)) return slabCache.get(key);
  const rr = Math.min(r, w / 2 - 0.002, d / 2 - 0.002);
  const sh = new THREE.Shape();
  const x = -w / 2, y = -d / 2;
  sh.moveTo(x + rr, y);
  sh.lineTo(x + w - rr, y); sh.quadraticCurveTo(x + w, y, x + w, y + rr);
  sh.lineTo(x + w, y + d - rr); sh.quadraticCurveTo(x + w, y + d, x + w - rr, y + d);
  sh.lineTo(x + rr, y + d); sh.quadraticCurveTo(x, y + d, x, y + d - rr);
  sh.lineTo(x, y + rr); sh.quadraticCurveTo(x, y, x + rr, y);
  const bev = Math.min(0.02, h / 5, rr / 3);
  const g = new THREE.ExtrudeGeometry(sh, {
    depth: Math.max(h - bev * 2, 0.002), bevelEnabled: true,
    bevelThickness: bev, bevelSize: bev, bevelSegments: 3, curveSegments: 12,
  });
  g.rotateX(-Math.PI / 2);
  // y=中心 に揃える（回転直後は y∈[-bev, h-bev]）。
  // ここが +h/2 だと全オブジェクトが自分の高さぶん浮く。厚い土台(h=0.62)だけが
  // 突出して床・台座・ラグを丸ごと飲み込み、椅子の背もたれとサーバーラックが
  // 宙に浮いていた（レイキャストで特定した実バグ。見た目の「平坦で安っぽい」の主因）。
  g.translate(0, bev - h / 2, 0);
  g.computeVertexNormals();
  slabCache.set(key, g);
  return g;
}

/** 水平プレーン（UV 0..1）。ラジアル影・床グローなどテクスチャの1枚貼りに使う。 */
function flat(w, d) {
  return new THREE.PlaneGeometry(w, d).rotateX(-Math.PI / 2);
}
/** 垂直プレーン（UV 0..1・+z向き）。グロー板・バックドロップに使う。 */
function vplane(w, h) {
  return new THREE.PlaneGeometry(w, h);
}

const at = (x, y, z, ry = 0) => new THREE.Matrix4().makeRotationY(ry).setPosition(x, y, z);
/** 垂直に立てる行列。Y回転だけだと板は平置きになる（実際に踏んだ）。 */
const upright = (x, y, z, yaw = 0) => new THREE.Matrix4()
  .makeRotationY(yaw)
  .multiply(new THREE.Matrix4().makeRotationX(Math.PI / 2))
  .setPosition(x, y, z);

// ── 本体 ──────────────────────────────────────────────────────
export function buildOffice(materials, rand) {
  const P = [];
  const put = (geometry, material, x, y, z, ry = 0) =>
    P.push({ geometry, material, matrix: at(x, y, z, ry) });

  const L = LAYOUT;
  const W = WALL;

  // 浮島の落ち影（ソフトなラジアル1枚。硬い板の重ねはやめた＝模型っぽさの主因）
  P.push({ geometry: flat(L.floor.w + 6, L.floor.d + 6), material: "islandShadow",
    matrix: at(0.35, -0.72, L.floor.z + 0.35) });
  put(slab(L.floor.w + 0.6, 0.62, L.floor.d + 0.6, 0.7), "base", 0, -0.34, L.floor.z);
  put(slab(L.floor.w, 0.07, L.floor.d, 0.55), "floor", 0, 0, L.floor.z);
  neonRing(P, L.floor.w, L.floor.d, 0, 0.05, L.floor.z, "neon", 0.18);

  // ゾーンの台座（全ゾーン統一の文法: 角丸台座＋ネオン縁）
  for (const [zone, mat, neonMat] of [
    [L.deskZone, "floor2", "neonC"],
    [L.meetZone, "floor2", "neon"],
    [L.stageZone, "woodFloor", "neon"],   // ソファコーナー（第2会議室と入替）
    [L.meet2Zone, "floor2", "neon"],
    [L.meet3Zone, "floor2", "neon"],   // R70: 第3会議室（小・右手前）
    [L.loungeZone, "woodFloor", "neon"],
  ]) {
    put(slab(zone.w, zone.lift, zone.d, 0.34), mat, zone.x, zone.lift / 2, zone.z);
    neonRing(P, zone.w, zone.d, zone.x, zone.lift - 0.004, zone.z, neonMat, 0.15);
  }

  // 窓の壁（奥と左）
  wallOfWindows(P, L.floor.w, 0, W.back + 0.1, 0);
  wallOfWindows(P, L.floor.d, W.left + 0.05, L.floor.z, Math.PI / 2);
  // 右辺と前辺の一部は低いガラス手すり（切り欠き壁の文法＝輪郭は出すが視界は塞がない）
  glassWall(P, 13.6, W.right - 0.12, 1.4, Math.PI / 2, 0.85);
  glassWall(P, 2.9, 12.85, W.front - 0.12, 0, 0.85);

  // ── 机の島（3×2）────────────────────────────────────────────
  // 列ラグ: 明るい台座を大きな面で覆う（広げた床が「余白」として採点されるのを防ぐ）
  // 薄物は「面の実トップ + h/2」に置く（slab の y は中心）
  put(slab(15.0, 0.012, 4.0, 0.5), "rug", L.deskZone.x, L.deskZone.lift + 0.005, -2.35);
  put(slab(15.0, 0.012, 4.0, 0.5), "rugB", L.deskZone.x, L.deskZone.lift + 0.005, 3.05);
  const MUG = ["mugA", "mugB", "mugC"];
  let podI = 0;
  for (const [cx, cz] of PODS) {
    podI += 1;
    const y = L.deskZone.lift;
    // 天板2枚重ね＋パネル脚（ずんぐりモダン）
    put(slab(3.0, 0.05, 2.3, 0.12), podI % 2 ? "wood" : "wood2", cx, y + 0.745, cz);
    put(slab(2.92, 0.04, 2.22, 0.10), "white", cx, y + 0.705, cz);
    for (const sx of [-1, 1]) {
      put(slab(0.09, 0.66, 1.9, 0.045), "white", cx + sx * 1.38, y + 0.34, cz);
    }
    put(slab(2.6, 0.42, 0.14, 0.05), podI % 2 ? "panelA" : "panelB", cx, y + 0.30, cz);
    // 中央のプライバシーガラス
    put(slab(2.8, 0.34, 0.05, 0.04), "glass", cx, y + 0.97, cz);
    // 席まわり（キーボード・マグ・書類だけ。小物はこれ以上増やさない）
    for (const front of [1, -1]) {
      chair(P, cx + 0.15, y, cz + front * 1.30, front > 0 ? Math.PI : 0,
        ["seat", "seatB", "seatC"][podI % 3]);
      put(slab(0.68, 0.024, 0.24, 0.02), "kbd", cx - 0.30, y + 0.782, cz + front * 0.42);
      // 小物は絞る（書類は全廃・マグは対面の片側だけ＝ごちゃつきの引き算）
      if (front > 0) {
        put(slab(0.12, 0.15, 0.12, 0.04), MUG[podI % 3], cx + 0.78, y + 0.80, cz + front * 0.52);
      }
    }
    // デスクランプ（島ごとに1灯・交互の角）: 暖色の点在が寒色空間を締める
    const lx = cx + (podI % 2 ? 1.08 : -1.08);
    const lz2 = cz + (podI % 2 ? -0.72 : 0.72);
    put(slab(0.15, 0.03, 0.15, 0.05), "dark", lx, y + 0.80, lz2);
    put(slab(0.035, 0.36, 0.035, 0.012), "steel", lx, y + 0.83, lz2);
    put(slab(0.24, 0.09, 0.13, 0.05), "white", lx - 0.07, y + 1.16, lz2);
    put(slab(0.18, 0.025, 0.09, 0.03), "lampWarm", lx - 0.07, y + 1.14, lz2);
    P.push({ geometry: flat(0.78, 0.6), material: "glowW",
      matrix: at(lx - 0.08, y + 0.815, lz2) });
    // 接地影
    P.push({ geometry: flat(3.4 * 1.35, 3.4 * 1.5), material: "shadow",
      matrix: at(cx, y + 0.005, cz) });
  }

  // ── 会議ゾーン ──────────────────────────────────────────────
  const m = L.meetZone;
  put(slab(5.8, 0.012, 3.2, 0.5), "rugB", m.x, m.lift + 0.005, m.z);
  put(slab(4.2, 0.12, 1.6, 0.35), "wood2", m.x, m.lift + 0.90, m.z);
  put(slab(0.45, 0.62, 1.05, 0.12), "white", m.x, m.lift + 0.53, m.z);
  meetingAnchors().slice(1).forEach((a, i) =>
    chair(P, a.x, m.lift, a.z, a.yaw, ["seat", "seatB", "sofa"][i % 3]));
  // ガラス会議室（参考画像の署名＝ガラス面の存在感）。前面は入口ギャップを空けて2枚
  glassWall(P, 2.3, m.x - 2.35, m.z + 2.15, 0, 2.35);
  glassWall(P, 2.7, m.x + 2.15, m.z + 2.15, 0, 2.35);
  glassWall(P, 4.3, m.x + 3.55, m.z, Math.PI / 2, 2.35);
  P.push({ geometry: flat(5.2 * 1.35, 2.9 * 1.5), material: "shadow",
    matrix: at(m.x, m.lift + 0.005, m.z) });
  // 会議白板の受け（奥の窓壁の前・中身は buildMonitors がプレーンで貼る）
  put(slab(2.9, 0.09, 1.75, 0.07), "white", m.x + 0.2, 1.72, W.back + 1.55, 0);

  // ── ソファコーナー（左壁・白板つき＝ブレスト用。第2会議室と場所を入替） ──
  const st = L.stageZone;
  put(slab(0.10, 1.35, 0.10, 0.03), "steel", W.left + 0.35, 0.85, st.z - 0.95);
  put(slab(0.10, 1.35, 0.10, 0.03), "steel", W.left + 0.35, 0.85, st.z + 0.95);
  put(slab(0.14, 0.09, 2.3, 0.05), "white", W.left + 0.35, 1.56, st.z);   // 白板の受け
  put(slab(2.2, 0.42, 0.95, 0.18), "sofaB", st.x + 0.1, st.lift + 0.24, st.z - 0.95);
  put(slab(2.2, 0.5, 0.28, 0.12), "sofaB", st.x + 0.1, st.lift + 0.43, st.z - 1.38);
  put(slab(2.2, 0.42, 0.95, 0.18), "sofa", st.x + 0.1, st.lift + 0.24, st.z + 1.15);
  put(slab(2.2, 0.5, 0.28, 0.12), "sofa", st.x + 0.1, st.lift + 0.43, st.z + 1.58);
  put(slab(0.5, 0.18, 0.6, 0.1), "cushionB", st.x - 0.45, st.lift + 0.54, st.z - 0.92);
  put(slab(0.5, 0.18, 0.6, 0.1), "cushionA", st.x + 0.65, st.lift + 0.54, st.z + 1.12);
  put(slab(1.5, 0.34, 0.8, 0.14), "wood", st.x + 0.1, st.lift + 0.17, st.z + 0.1);
  put(slab(0.42, 0.04, 0.3, 0.03), "dark", st.x - 0.1, st.lift + 0.53, st.z + 0.1);
  put(slab(0.3, 0.2, 0.24, 0.03), "dark", st.x - 0.1, st.lift + 0.57, st.z - 0.03);

  // ── サーバーウォール（黒いラック5本・紫LED・手前にガラス） ─────────
  for (let i = 0; i < 6; i++) {
    const x = L.serverZone.x - 3.375 + i * 1.35;
    put(slab(1.14, 2.7, 1.0, 0.10), "darker", x, 1.38, L.serverZone.z);
    for (let k = 0; k < 8; k++) {
      // 引きのカメラでも見えるよう厚めに（0.05 だと1px相当で消える）
      put(slab(0.90, 0.09, 0.05, 0.02), (k + i) % 4 === 0 ? "neonC" : "neon",
        x, 0.44 + k * 0.29, L.serverZone.z + 0.55);
    }
  }
  P.push({ geometry: flat(8.2 * 1.35, 1.8 * 1.5), material: "shadow",
    matrix: at(L.serverZone.x, 0.04, L.serverZone.z + 0.2) });
  glassWall(P, 9.4, L.serverZone.x, L.serverZone.z + 1.45, 0, 2.3);
  glassWall(P, 2.6, L.serverZone.x - 4.9, L.serverZone.z + 0.2, Math.PI / 2, 2.3);

  // ── 外部接続ベイ（右辺・OpenClaw席）: 台座+長カウンター+柱で「場所」にする ──
  put(slab(3.0, 0.10, 5.6, 0.30), "darkFloor", W.right - 1.6, 0.05, -2.6);   // 暗色の台座
  neonRing(P, 3.0, 5.6, W.right - 1.6, 0.096, -2.6, "neonC", 0.12);
  put(slab(0.7, 0.08, 5.1, 0.10), "white", W.right - 1.05, 0.92, -2.6);      // 長カウンター
  put(slab(0.7, 0.5, 5.1, 0.10), "dark", W.right - 1.05, 0.42, -2.6);
  for (let i = 0; i < 3; i++) {
    const z = -4.4 + i * 1.8;
    put(slab(0.16, 2.0, 0.16, 0.05), "white", W.right - 0.5, 1.0, z - 0.9);  // 柱
    put(slab(0.05, 0.78, 1.1, 0.03), "screenGlow", W.right - 0.62, 1.35, z);
    put(slab(0.05, 0.05, 1.1, 0.02), "neonC", W.right - 0.62, 1.82, z);
  }
  put(slab(0.16, 2.0, 0.16, 0.05), "white", W.right - 0.5, 1.0, 0.1);

  // ── ソファヌック（左壁・紫サインの下） ────────────────────────────
  put(slab(0.35, 0.72, 3.4, 0.14), "sofa", W.left + 0.45, 0.52, 4.9);      // 背
  put(slab(0.95, 0.40, 3.4, 0.16), "sofa", W.left + 0.85, 0.24, 4.9);      // 座
  for (let i = 0; i < 3; i++) {
    put(slab(0.55, 0.20, 0.72, 0.10), ["cushionA", "cushionB", "cushionC"][i],
      W.left + 0.85, 0.51, 3.95 + i * 0.95);
  }
  put(slab(0.9, 0.36, 0.9, 0.3), "wood", W.left + 2.1, 0.18, 4.9);          // ローテーブル
  // フロアランプ（暖色・ソファ脇）
  put(slab(0.34, 0.04, 0.34, 0.16), "dark", W.left + 0.75, 0.02, 7.0);
  put(slab(0.045, 1.45, 0.045, 0.015), "steel", W.left + 0.75, 0.06, 7.0);
  put(slab(0.34, 0.30, 0.34, 0.17), "white", W.left + 0.75, 1.48, 7.0);
  put(slab(0.26, 0.03, 0.26, 0.12), "lampWarm", W.left + 0.75, 1.46, 7.0);
  P.push({ geometry: flat(1.6, 1.5), material: "glowW",
    matrix: at(W.left + 0.95, 0.06, 6.85) });
  P.push({ geometry: flat(2.6 * 1.35, 3.6 * 1.5), material: "shadow",
    matrix: at(W.left + 1.1, 0.04, 4.9) });

  // ── 第2会議室（右中・左奥会議室と同格の文法） ─────────────────────
  const n2 = L.meet2Zone;
  put(slab(3.6, 0.012, 2.4, 0.4), "rugB", n2.x, n2.lift + 0.005, n2.z);
  put(slab(3.0, 0.12, 1.35, 0.32), "wood2", n2.x, n2.lift + 0.80, n2.z);
  put(slab(0.42, 0.6, 0.95, 0.12), "white", n2.x, n2.lift + 0.42, n2.z);
  put(slab(0.4, 0.03, 0.28, 0.02), "paper", n2.x - 0.6, n2.lift + 0.98, n2.z + 0.1);
  put(slab(0.12, 0.15, 0.12, 0.04), "mugB", n2.x + 0.85, n2.lift + 1.0, n2.z - 0.2);
  // ガラス（西面＋北面はドアギャップ付き2枚）
  glassWall(P, 3.8, n2.x - 2.45, n2.z, Math.PI / 2, 2.35);
  glassWall(P, 2.0, n2.x - 1.4, n2.z - 1.95, 0, 2.35);
  glassWall(P, 1.3, n2.x + 1.8, n2.z - 1.95, 0, 2.35);
  // 自立白板（東縁・板面は buildMonitors が貼る）
  put(slab(0.10, 1.3, 0.10, 0.03), "steel", n2.x + 2.2, n2.lift + 0.85, n2.z - 0.85);
  put(slab(0.10, 1.3, 0.10, 0.03), "steel", n2.x + 2.2, n2.lift + 0.85, n2.z + 0.85);
  put(slab(0.14, 0.08, 2.0, 0.05), "white", n2.x + 2.2, n2.lift + 1.52, n2.z);
  P.push({ geometry: flat(5.6, 4.6), material: "shadow",
    matrix: at(n2.x, n2.lift + 0.005, n2.z) });

  // ── 第3会議室（R70・小・右手前＝ラウンジ東の空床。位置はオーバーレイ実測で確定） ──
  const k3 = L.meet3Zone;
  put(slab(2.3, 0.012, 2.0, 0.35), "rugB", k3.x, k3.lift + 0.005, k3.z);
  put(slab(1.7, 0.12, 1.0, 0.28), "wood2", k3.x, k3.lift + 0.80, k3.z);      // 小卓
  put(slab(0.36, 0.6, 0.7, 0.10), "white", k3.x, k3.lift + 0.42, k3.z);      // 卓脚
  put(slab(0.34, 0.03, 0.24, 0.02), "paper", k3.x - 0.35, k3.lift + 0.98, k3.z + 0.05);
  put(slab(0.12, 0.15, 0.12, 0.04), "mugB", k3.x + 0.5, k3.lift + 1.0, k3.z - 0.15);
  // ガラス（西面＋北面・南=開口が入口・東=外壁ガラスに任せる）
  glassWall(P, 2.6, k3.x - 1.3, k3.z + 0.2, Math.PI / 2, 2.35);
  glassWall(P, 2.4, k3.x - 0.1, k3.z - 1.5, 0, 2.35);
  // 観葉植物（北東の角）
  put(slab(0.34, 0.35, 0.34, 0.08), "white", k3.x + 0.95, k3.lift + 0.18, k3.z - 1.15);
  put(slab(0.4, 0.5, 0.4, 0.16), "plant", k3.x + 0.95, k3.lift + 0.6, k3.z - 1.15);
  P.push({ geometry: flat(3.1, 3.4), material: "shadow",
    matrix: at(k3.x, k3.lift + 0.005, k3.z) });

  // ── ラウンジ（右前・L字ソファ＋プーフ） ──────────────────────────
  const lz = L.loungeZone;
  put(slab(3.1, 0.42, 1.05, 0.20), "sofa", lz.x - 0.85, lz.lift + 0.24, lz.z - 0.6);
  put(slab(3.1, 0.55, 0.30, 0.14), "sofa", lz.x - 0.85, lz.lift + 0.44, lz.z - 1.12);
  put(slab(1.05, 0.42, 1.9, 0.20), "sofaB", lz.x - 2.25, lz.lift + 0.24, lz.z + 0.4);
  for (let i = 0; i < 2; i++) {
    put(slab(0.5, 0.18, 0.6, 0.10), ["cushionA", "cushionC"][i],
      lz.x - 1.65 + i * 1.4, lz.lift + 0.52, lz.z - 0.8);
  }
  P.push({ geometry: flat(5.4, 3.3), material: "rugArt",
    matrix: at(lz.x - 0.2, lz.lift + 0.008, lz.z + 0.25) });
  put(slab(1.15, 0.40, 1.15, 0.55), "sofaC", lz.x + 1.5, lz.lift + 0.22, lz.z + 0.7); // プーフ
  put(slab(1.0, 0.34, 1.0, 0.5), "white", lz.x + 0.3, lz.lift + 0.19, lz.z + 0.8);    // 丸テーブル
  put(slab(0.34, 0.05, 0.26, 0.03), "dark", lz.x + 0.3, lz.lift + 0.39, lz.z + 0.8);  // タブレット
  P.push({ geometry: flat(5.2 * 1.35, 3.2 * 1.5), material: "shadow",
    matrix: at(lz.x - 0.3, lz.lift + 0.005, lz.z) });

  // ── 受付（前中央・あなたの席）＋待機列 ────────────────────────────
  const q = L.queueZone;
  put(slab(5.0, 0.012, 1.7, 0.5), "cushionB", q.x + 1.6, 0.041, q.z - 0.1);  // 列のラグ
  put(slab(3.2, 0.98, 0.9, 0.20), "white", q.x + 1.6, 0.49, q.z + 1.2);
  put(slab(3.4, 0.08, 1.0, 0.12), "wood", q.x + 1.6, 1.02, q.z + 1.2);
  put(slab(3.0, 0.05, 0.05, 0.02), "neon", q.x + 1.6, 0.20, q.z + 0.73);
  P.push({ geometry: flat(3.8 * 1.35, 1.6 * 1.5), material: "shadow",
    matrix: at(q.x + 1.6, 0.04, q.z + 1.2) });

  // ── 本棚×2（奥壁・会議とサーバーの間） ───────────────────────────
  for (const bx of [-4.5]) {
    put(slab(2.3, 1.9, 0.5, 0.08), "white", bx, 0.95, W.back + 0.65);
    for (let k = 0; k < 4; k++) {
      for (let b = 0; b < 5; b++) {
        put(slab(0.30, 0.34, 0.36, 0.02),
          ["bookA", "bookB", "bookC", "bookD", "bookE"][(k + b) % 5],
          bx - 0.8 + b * 0.42, 0.42 + k * 0.44, W.back + 0.65);
      }
    }
  }

  // ── コーヒーバー（奥壁・本棚の右隣。旧位置はボスの席に譲った） ────────
  put(slab(1.9, 0.95, 0.75, 0.12), "wood2", 3.9, 0.48, W.back + 0.75);
  put(slab(2.05, 0.06, 0.85, 0.10), "white", 3.9, 0.99, W.back + 0.75);
  put(slab(0.42, 0.34, 0.34, 0.05), "dark", 3.6, 1.19, W.back + 0.72);   // エスプレッソマシン
  for (let i = 0; i < 3; i++) {
    put(slab(0.11, 0.13, 0.11, 0.04), MUG[i], 4.17 + (i % 2) * 0.24, 1.09,
      W.back + 0.62 + Math.floor(i / 2) * 0.24);
  }
  put(slab(1.7, 0.04, 0.05, 0.02), "neonC", 3.9, 0.16, W.back + 1.16);

  // ── ボスの席（奥の真ん中にドーンと・一段高い台座＝オフィスを見渡す） ────
  put(slab(5.4, 0.26, 3.5, 0.4), "woodFloor", -0.6, 0.13, W.back + 1.95);
  neonRing(P, 5.4, 3.5, -0.6, 0.256, W.back + 1.95, "neon", 0.14);
  put(slab(4.0, 0.012, 2.2, 0.4), "rugB", -0.6, 0.266, W.back + 2.0);
  put(slab(2.9, 0.065, 1.15, 0.16), "wood2", -0.6, 1.0, W.back + 2.1);      // 大机
  put(slab(2.72, 0.055, 1.0, 0.14), "white", -0.6, 0.94, W.back + 2.1);
  for (const sx of [-1, 1]) {
    put(slab(0.14, 0.66, 0.95, 0.05), "wood2", -0.6 + sx * 1.25, 0.47, W.back + 2.1);
  }
  put(slab(0.68, 0.024, 0.24, 0.02), "kbd", -0.75, 1.07, W.back + 2.32);
  put(slab(0.12, 0.15, 0.12, 0.04), "mugC", 0.35, 1.08, W.back + 1.95);
  put(slab(0.3, 0.03, 0.22, 0.02), "paper", -1.55, 1.07, W.back + 1.98);
  // ハイバックチェア＋金クッション（ボスの記号）
  chair(P, -0.6, 0.26, W.back + 1.25, 0, "seat");
  // 王冠（ボスロボの頭上・座席は固定なので静的でよい）
  put(slab(0.56, 0.13, 0.56, 0.26), "crown", -0.6, 2.42, W.back + 1.22);
  for (const [cx2, cz2] of [[-0.78, 1.22], [-0.6, 1.06], [-0.42, 1.22], [-0.6, 1.38]]) {
    put(slab(0.09, 0.17, 0.09, 0.035), "crown", cx2, 2.55, W.back + cz2 - 0);
  }
  put(slab(0.72, 0.9, 0.17, 0.07), "seat", -0.6, 0.95, W.back + 0.92);
  put(slab(0.34, 0.2, 0.12, 0.05), "cushionB", -0.6, 0.78, W.back + 1.02);
  // 両脇のライトピラー（暖色）
  for (const sx of [-1, 1]) {
    put(slab(0.16, 1.7, 0.16, 0.05), "white", -0.6 + sx * 2.35, 0.26 + 0.85, W.back + 1.2);
    put(slab(0.2, 0.08, 0.2, 0.06), "lampWarm", -0.6 + sx * 2.35, 1.98, W.back + 1.2);
  }
  P.push({ geometry: flat(6.2, 4.0), material: "shadow",
    matrix: at(-0.6, 0.27, W.back + 2.0) });

  // ── ホログラム（作業机の上に浮く半透明パネル） ─────────────────────
  // R68: パネル本体は静的バッチから外し scene3d が個別メッシュで持つ
  // （update() で回転＋脈動させるため。HOLO_PANELS が正本）。ネオン縁は静的のまま。
  for (const [hx, hy, hz, hw, ry] of HOLO_PANELS) {
    put(slab(hw * 0.94, 0.045, 0.04, 0.02), "neonC", hx, hy - 0.02, hz, ry);
  }

  // ── 通路（デスク島⇔ラウンジ/サーバー間のランナー） ─────────────────
  put(slab(1.8, 0.014, 14.0, 0.6), "cushionA", 7.85, 0.042, -0.9);
  put(slab(1.6, 0.03, 0.05, 0.02), "neonC", 7.85, 0.055, 6.05);
  put(slab(1.6, 0.03, 0.05, 0.02), "neonC", 7.85, 0.055, -7.85);
  // 左の通路（ステージ/会議⇔デスク島の間）。色は静かなラベンダー
  // （ピンクは目に刺さって「ごちゃごちゃ感」の一因だった。キオスクも撤去=引き算）
  put(slab(1.7, 0.014, 11.6, 0.6), "rugB", -9.85, 0.042, 0.6);
  put(slab(1.5, 0.03, 0.05, 0.02), "neonC", -9.85, 0.055, 6.2);
  put(slab(1.5, 0.03, 0.05, 0.02), "neonC", -9.85, 0.055, -5.2);

  // ── 観葉植物（大きめを要所に・壁際と角を埋める） ───────────────────
  for (const [x, z, sc] of [
    [-5.4, W.back + 0.75, 1.3], [5.7, -7.0, 1.2], [W.right - 0.7, 2.1, 1.25],
    [-5.6, 8.25, 1.15], [4.6, 8.2, 1.1], [-11.3, 8.3, 1.3],
    [7.85, 4.8, 1.05], [W.left + 0.75, -4.2, 1.2], [W.right - 0.7, 7.7, 1.25],
  ]) plant(P, x, z, sc, rand);

  // ── エントランス（左手前＝ユーザー指定。ロボはここから出勤してくる） ────
  put(slab(3.4, 0.016, 2.0, 0.4), "dark", -8.3, 0.05, W.front - 1.7);        // 玄関マット
  put(slab(3.1, 0.03, 0.06, 0.02), "neonC", -8.3, 0.062, W.front - 2.65);
  for (const gx of [-9.8, -6.8]) {                                            // ゲートポスト
    put(slab(0.24, 1.15, 0.24, 0.07), "white", gx, 0.6, W.front - 1.95);
    put(slab(0.18, 0.06, 0.18, 0.05), "neonC", gx, 1.22, W.front - 1.95);
  }
  P.push({ geometry: flat(4.2, 2.6), material: "shadow",
    matrix: at(-8.3, 0.03, W.front - 1.75) });

  // ── タッチダウンデスク（前中央右・「机を置いたら」のユーザー指定） ──────
  put(slab(2.7, 0.07, 0.95, 0.10), "white", 4.2, 0.98, W.front - 1.9);       // 立ちテーブル
  for (const sx of [-1, 1]) {
    put(slab(0.10, 0.95, 0.7, 0.04), "steel", 4.2 + sx * 1.15, 0.5, W.front - 1.9);
  }
  for (const lx of [3.5, 4.9]) {                                              // ノートPC×2
    put(slab(0.42, 0.04, 0.3, 0.03), "dark", lx, 1.08, W.front - 1.95);
    put(slab(0.3, 0.2, 0.24, 0.03), "dark", lx, 1.12, W.front - 2.08);
  }
  for (const sx of [-1.0, -0.35, 0.35, 1.0]) {                                // スツール×4
    put(slab(0.4, 0.55, 0.4, 0.2), "sofaC", 4.2 + sx, 0.28, W.front - 1.15);
  }
  P.push({ geometry: flat(3.0, 2.0), material: "shadow",
    matrix: at(4.2, 0.03, W.front - 1.7) });

  // ── 床の擬似映り込み（v=1側が壁際で明るいグラデ板・参考画像の「床に映る」を作る） ──
  P.push({ geometry: flat(9.6, 3.4), material: "reflP",
    matrix: at(L.serverZone.x, 0.072, L.serverZone.z + 2.15) });          // ラックの紫
  P.push({ geometry: flat(2.8, 2.2).rotateY(-Math.PI / 2), material: "reflP",
    matrix: at(W.left + 1.15, 0.072, 4.9) });                              // ネオンサイン
  P.push({ geometry: flat(L.floor.w - 3, 1.5), material: "reflC",
    matrix: at(0, 0.068, W.back + 1.05) });                                // 奥窓の白筋
  P.push({ geometry: flat(L.floor.d - 4, 1.3).rotateY(-Math.PI / 2), material: "reflC",
    matrix: at(W.left + 0.95, 0.066, -0.6) });                             // 左窓の白筋

  // ── フェイクブルーム（加算グロー板・ポストプロセス無しで「光っている」を作る） ──
  // 紫サインのハロー（左壁・面は+xへ向ける）
  P.push({ geometry: vplane(4.2, 2.6), material: "glowP",
    matrix: new THREE.Matrix4().makeRotationY(Math.PI / 2).setPosition(W.left + 0.3, 1.95, 4.9) });
  // サーバーウォールの照り返し（ラック前面へ）
  P.push({ geometry: vplane(7.6, 3.0), material: "glowP",
    matrix: at(L.serverZone.x, 1.45, L.serverZone.z + 0.75) });
  P.push({ geometry: vplane(4.0, 1.6), material: "glowC",
    matrix: at(L.serverZone.x - 1.2, 0.9, L.serverZone.z + 0.8) });
  // ホログラムの下のこぼれ光
  for (const [hx, hz] of [[-0.6, -2.6],
    [L.meetZone.x + 1.0, L.meetZone.z + 0.4], [W.right - 1.6, -2.6]]) {
    P.push({ geometry: flat(1.6, 1.2), material: "glowC", matrix: at(hx, 1.95, hz) });
  }

  return buildStaticBatches(P, materials);
}

/** 窓の連なりの壁。白い一枚壁は置かない（明るい一様面＝殺風景の主因）。 */
function wallOfWindows(P, w, x, z, ry = 0, h = 2.55) {
  const put = (g, m, dx, dy, dz) => P.push({
    geometry: g, material: m,
    matrix: new THREE.Matrix4().makeRotationY(ry).setPosition(
      x + Math.cos(ry) * dx - Math.sin(ry) * dz, dy, z + Math.sin(ry) * dx + Math.cos(ry) * dz),
  });
  const n = Math.max(3, Math.round(w / 0.82));
  const pitch = w / n;
  put(slab(w, 0.34, 0.20, 0.05), "white", 0, 0.17, 0);
  put(slab(w, 0.22, 0.24, 0.06), "white", 0, h - 0.11, 0);
  put(slab(w * 0.97, 0.05, 0.05, 0.02), "neonC", 0, 0.36, 0.11);
  for (let i = 0; i < n; i++) {
    const cx = -w / 2 + pitch * (i + 0.5);
    // 窓ガラス: 壁全体で1枚の都市パノラマを連続サンプリングする
    // （窓ごとに同じ絵をタイルすると「壁紙」に見える。隣の窓と絵が繋がるのが本物の窓）
    const pw = pitch - 0.14;
    const ph = h - 0.66;
    const pane = new THREE.PlaneGeometry(pw, ph);
    const uv = pane.getAttribute("uv");
    const pos = pane.getAttribute("position");
    for (let k = 0; k < uv.count; k++) {
      uv.setX(k, (cx + w / 2 + pos.getX(k)) / w);
      uv.setY(k, 0.06 + uv.getY(k) * 0.88);
    }
    P.push({ geometry: pane, material: "sky",
      matrix: new THREE.Matrix4().makeRotationY(ry).setPosition(
        x + Math.cos(ry) * cx - Math.sin(ry) * 0.02, h / 2 + 0.04,
        z + Math.sin(ry) * cx + Math.cos(ry) * 0.02) });
    put(slab(0.09, h - 0.55, 0.14, 0.03), "white", cx - pitch / 2, h / 2 + 0.04, 0.03);
    put(slab(pitch - 0.10, 0.08, 0.16, 0.03), "white", cx, 0.36, 0.03);
  }
  put(slab(0.09, h - 0.55, 0.14, 0.03), "white", w / 2, h / 2 + 0.04, 0.03);
}

/** ガラス仕切り（白枠＋青みガラス＋足元ネオン）。 */
function glassWall(P, w, x, z, ry = 0, h = 2.3) {
  const put = (g, m, dx, dy, dz) => P.push({
    geometry: g, material: m,
    matrix: new THREE.Matrix4().makeRotationY(ry).setPosition(
      x + Math.cos(ry) * dx - Math.sin(ry) * dz, dy, z + Math.sin(ry) * dx + Math.cos(ry) * dz),
  });
  put(slab(w, h, 0.06, 0.02), "glass", 0, h / 2 + 0.12, 0);
  put(slab(w, 0.08, 0.15, 0.04), "white", 0, h + 0.14, 0);
  put(slab(w, 0.14, 0.18, 0.05), "white", 0, 0.07, 0);
  put(slab(w * 0.97, 0.045, 0.05, 0.02), "neonC", 0, 0.165, 0.09);
  const n = Math.max(1, Math.round(w / 2.0));
  for (let i = 1; i < n; i++) {
    put(slab(0.05, h, 0.08, 0.02), "white", -w / 2 + (w / n) * i, h / 2 + 0.12, 0);
  }
}

function neonRing(P, w, d, x, y, z, mat, t = 0.12) {
  const put = (ww, dd, ox, oz) =>
    P.push({ geometry: slab(ww, t, dd, t / 2), material: mat, matrix: at(x + ox, y, z + oz) });
  put(w, t * 1.5, 0, d / 2); put(w, t * 1.5, 0, -d / 2);
  put(t * 1.5, d, w / 2, 0); put(t * 1.5, d, -w / 2, 0);
}

/** オフィスチェア。座も背もぶ厚く丸く＝参考画像のチェア。 */
function chair(P, x, baseY, z, yaw, mat = "seat") {
  const put = (g, m, dx, dy, dz, ry = 0) => P.push({
    geometry: g, material: m,
    matrix: new THREE.Matrix4().makeRotationY(yaw + ry)
      .setPosition(x + Math.cos(yaw) * dx - Math.sin(yaw) * dz, baseY + dy,
        z + Math.sin(yaw) * dx + Math.cos(yaw) * dz),
  });
  put(slab(0.56, 0.14, 0.54, 0.20), mat, 0, 0.40, 0);              // 厚い座面
  put(slab(0.54, 0.62, 0.13, 0.06), mat, 0, 0.78, -0.27);          // 立った背もたれ
  put(slab(0.11, 0.26, 0.11, 0.05), "steel", 0, 0.16, 0);          // ガスシリンダー
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2;
    put(slab(0.28, 0.05, 0.08, 0.03), "dark",
      Math.cos(a) * 0.15, 0.045, Math.sin(a) * 0.15, -a);
  }
}

function plant(P, x, z, sc, rand) {
  P.push({ geometry: slab(0.42 * sc, 0.46 * sc, 0.42 * sc, 0.12 * sc),
    material: "pot", matrix: at(x, 0.23 * sc, z) });
  const n = 8;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 + rand() * 0.6;
    const rad = (0.10 + rand() * 0.26) * sc;
    const g = new THREE.SphereGeometry(0.20 + rand() * 0.09, 10, 8);
    g.scale(1, 0.36, 0.74);
    const mtx = new THREE.Matrix4().makeRotationFromEuler(
      new THREE.Euler(rand() * 0.7 - 0.35, a, 0.35 + rand() * 0.55));
    mtx.setPosition(x + Math.cos(a) * rad, (0.60 + rand() * 0.52) * sc, z + Math.sin(a) * rad);
    P.push({ geometry: g, material: i % 2 ? "leaf" : "leaf2", matrix: mtx });
  }
}

/**
 * テクスチャ付きの立て板（モニタ画面・ネオンサイン・白板の中身）。
 * ここだけは PlaneGeometry（UV 0..1）でないと絵が壊れる。
 * ベゼル等の無地部分は materials のバッチに乗せる。
 */
export function buildMonitors(screenMats, materials) {
  const group = new THREE.Group();
  const parts = [];
  const screens = [];
  let i = 0;
  for (const [cx, cz] of PODS) {
    for (const front of [1, -1]) {
      const y = LAYOUT.deskZone.lift + 1.36;
      const z = cz - front * 0.52;
      const yaw = front > 0 ? 0 : Math.PI;
      const mx = cx - 0.30;
      parts.push({ geometry: slab(1.42, 0.04, 0.86, 0.05), material: "dark",
        matrix: upright(mx, y, z, yaw) });
      parts.push({ geometry: slab(0.66, 0.06, 0.40, 0.05), material: "darker",
        matrix: upright(mx, y, z - front * 0.045, yaw) });
      parts.push({ geometry: slab(0.09, 0.38, 0.07, 0.02), material: "steel",
        matrix: at(mx, y - 0.52, z) });
      parts.push({ geometry: slab(0.44, 0.03, 0.26, 0.05), material: "dark",
        matrix: at(mx, y - 0.70, z) });
      const scr = new THREE.Mesh(new THREE.PlaneGeometry(1.32, 0.76),
        screenMats[i % screenMats.length]);
      scr.position.set(mx, y, z + (front > 0 ? 0.05 : -0.05));
      scr.rotation.y = yaw;
      screens.push(scr);
      i++;
    }
  }
  // 紫のネオンサイン（左壁・ソファヌックの上）
  const signMat = new THREE.MeshBasicMaterial({ map: signTexture(), toneMapped: false, transparent: true });
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(2.4, 1.35), signMat);   // 生成サインのアスペクトに合わせる
  sign.position.set(WALL.left + 0.13, 1.86, 4.9);
  sign.rotation.y = Math.PI / 2;
  screens.push(sign);
  // 発表ステージの白板（左壁・中身つき）
  const boardMat = new THREE.MeshBasicMaterial({ map: boardTexture(), toneMapped: false });
  const board1 = new THREE.Mesh(new THREE.PlaneGeometry(2.5, 1.55), boardMat);
  board1.position.set(WALL.left + 0.15, 1.35, LAYOUT.stageZone.z);
  board1.rotation.y = Math.PI / 2;
  screens.push(board1);
  // 会議白板（奥壁・中身つき）
  const board2 = new THREE.Mesh(new THREE.PlaneGeometry(2.7, 1.6), boardMat);
  board2.position.set(LAYOUT.meetZone.x + 0.2, 1.72, WALL.back + 1.62);
  screens.push(board2);
  // 第2会議室の自立白板（東縁・西向き）
  const n2 = LAYOUT.meet2Zone;
  const board3 = new THREE.Mesh(new THREE.PlaneGeometry(2.0, 1.25), boardMat);
  board3.position.set(n2.x + 2.13, n2.lift + 1.05, n2.z);
  board3.rotation.y = -Math.PI / 2;
  screens.push(board3);

  for (const b of buildStaticBatches(parts, materials)) group.add(b);
  for (const sc of screens) group.add(sc);
  group.userData.swappable = { signMat, boardMat };   // GPT-Imageデカールの差し替え口
  return group;
}
