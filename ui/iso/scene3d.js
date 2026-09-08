// 3Dアイソメ・レンダラ本体。world（core が作る純データ）を受け取って絵にする。
// 契約は mount/update/dispose の3つだけ。
import {
  chatBlend, chatPose, chatSpeaker, chibiPose, mixPose, pathTravel, poseFor,
  relaxPose, seedOf, smoothstep, thinkingPose, walkPhaseFor, walkPose,
  approvalPose, celebratePose, enterPose, leavePose, questionPose, readPose, runPose,
  ENTER_SECONDS, LEAVE_SECONDS, CELEBRATE_SECONDS,
  celebrationFlash,
} from "/ui/core/anim.js";
import { actFor } from "/ui/core/act.js";
import { exprFor } from "/ui/core/expr.js";
import { growthChanges } from "/ui/core/growth.js";
import { createDirector } from "/ui/core/broadcast.js";
import { routePath } from "/ui/core/nav.js";
import { buildLayout } from "/ui/core/layout.js";
import { DEFAULT_SPEC } from "/ui/core/layout_specs.js";
import { decorationsFor, specFor, tierFor } from "/ui/core/tier.js";
import { createGrowth } from "/ui/platform/growth.js";
import { frozen, localHour, rand, resetRand, randState, withRandState } from "/ui/platform/clock.js";
import * as THREE from "/ui/vendor/three/three.module.min.js";
import {
  buildMonitors, buildOffice, officeStops, projectSignAnchors,
  floorTexture, floorNormalTexture, updateClock, keyboardTexture, rugTexture, skyTexture, woodTexture,
} from "./office.js";
import {
  LOBSTER_TINT, GRAPHITE_TINT, RobotBatch, applyPose, makeChibiSkeleton, makeCleanerBot, makeSkeleton,
} from "./robot.js";
import { assignMeetingRooms, assignRestSpots, assignSeats, assignOverflow, stableIndex } from "/ui/core/world.js";

import { ActivityScreens, boardTexture } from "./screens.js";
import { leafAtlasTexture } from "./plants.js";
import { PostProcess } from "./post.js";
import { roomEnvironment } from "./env.js";
import { markerTexture } from "./markers.js";

const CAPACITY = 40;                 // 同時に描けるロボット数の上限（本人32+ボス+会議チビ最大8・R56）
const CHIBI_MAX = 8;                 // 会議チビロボの総数上限（席は4室×4だが描画予算で全体8体）
// R74: 会議に使うのは主要3室（第1/第2/第4）。2席しかない第3会議室は**予備**＝
// 主要3室が満席のときだけ開く（ユーザー仕様「合計3つの会議室が使われる」）。
const RESERVE_ROOMS = ["meet3"];
const CHIBI_WHITE = new THREE.Color(0xffffff);   // チビのアクセント淡色化（lerp先）
// Linear RGB stays <= .85, below bloom's 1.0 threshold; no additive/emissive light.
const CELEBRATION_REFLECTION = new THREE.Color(.85, .80, .70);
// 胸リングの状態色。HUD側のドットと同じ意味（作業=セージ/待機=琥珀/❗=赤/休憩=灰/外部=茶）
const ACCENTS = {
  attention: new THREE.Color(0xc0483a),
  working: new THREE.Color(0x7a9469),
  waiting: new THREE.Color(0xc28a3a),
  resting: new THREE.Color(0xb4aca0),
  external: new THREE.Color(0x8a6a45),
};
function accentFor(agent) {
  if (agent.attention) return ACCENTS.attention;
  if (agent.zone === "external") return ACCENTS.external;
  if (agent.state === "working") return ACCENTS.working;
  if (agent.state === "waiting") return ACCENTS.waiting;
  return ACCENTS.resting;
}
const std = (o) => new THREE.MeshStandardMaterial(o);
// Guide specifies daylight values; the morning/evening anchors are 07/11/18h.
// Outside that range hold the nearest daylight preset (no night scene).
const DAY_KEY = new THREE.Vector3(-15, 17, -19);
const HOUR_PRESETS = [
  { hour: 7, color: new THREE.Color(0xffe6c8), intensity: 1.3,
    position: new THREE.Vector3(-15, Math.hypot(15, 19) * Math.tan(28 * Math.PI / 180), -19), lamp: 2.8 },
  { hour: 11, color: new THREE.Color(0xfff3e0), intensity: 1.6, position: DAY_KEY, lamp: 2.8 },
  { hour: 18, color: new THREE.Color(0xffd2a6), intensity: 1.2, position: DAY_KEY, lamp: 4.2 },
];

/** Guide §3: four Physical instances (white/shell/visor/linen); glass stays Standard. */
export function makeMaterials(quality = "high") {
  const tile = (file, texture = rugTexture()) => { texture.userData.file = file; return texture; };
  const oak = tile("oak_floor.webp", floorTexture());
  const wood = tile("oak_floor.webp", woodTexture());
  const crossOak = tile("oak_floor.webp", floorTexture()); crossOak.rotation = Math.PI / 2;
  const linenMap = tile("linen.webp"), feltMap = tile("felt.webp"), juteMap = tile("jute.webp");
  const rattanMap = tile("rattan.webp");
  const sky = tile("window_day.webp", skyTexture());
  const rugArt = rugTexture(); rugArt.repeat.set(1, 1);
  rugArt.wrapS = rugArt.wrapT = THREE.ClampToEdgeWrapping; tile("rugart.webp", rugArt);
  const cloth = { color: 0xffffff, roughness: .95, map: linenMap, vertexColors: true };
  const linen = quality === "mobile" ? std(cloth) : new THREE.MeshPhysicalMaterial({ ...cloth,
    sheen: .5, sheenColor: 0xfff6e8, sheenRoughness: .85 });
  const leafCard = std({ color: 0xffffff, roughness: .65, map: leafAtlasTexture(),
    alphaTest: .5, side: THREE.DoubleSide, vertexColors: true });
  const artCanvas = document.createElement("canvas"); artCanvas.width = 512; artCanvas.height = 320;
  const ctx = artCanvas.getContext("2d");
  ctx.fillStyle = "#f1ede7"; ctx.fillRect(0, 0, 512, 320);
  ctx.strokeStyle = "#5f7d59"; ctx.lineWidth = 3;
  for (const x of [128, 384]) {
    ctx.beginPath(); ctx.moveTo(x, 280); ctx.lineTo(x, 40);
    for (let y = 75; y < 260; y += 35) {
      ctx.moveTo(x, y + 20); ctx.lineTo(x - 35, y); ctx.moveTo(x, y + 30); ctx.lineTo(x + 35, y + 10);
    }
    ctx.stroke();
  }
  const artMap = new THREE.CanvasTexture(artCanvas); artMap.colorSpace = THREE.SRGBColorSpace;
  return {
    base: std({ color: 0xe6ded2, roughness: .85 }),
    floor: std({ color: 0xf0e6d8, roughness: .55, map: oak,
      normalMap: floorNormalTexture(), normalScale: new THREE.Vector2(.35, .35) }),
    floor2: std({ color: 0xf0eae1, roughness: .80 }),
    woodFloor: std({ color: 0xffffff, roughness: .60, map: crossOak }),
    darkFloor: std({ color: 0x8e8880, roughness: .85 }),
    white: new THREE.MeshPhysicalMaterial({ color: 0xf7f4ef, roughness: .80 }),
    // Neutral material color: the palette is supplied once by instanceColor.
    shell: new THREE.MeshPhysicalMaterial({ color: 0xffffff, roughness: .32, clearcoat: .70,
      clearcoatRoughness: .18, envMapIntensity: 1.0, vertexColors: true }),
    dark: std({ color: 0x2e2d2c, roughness: .55, metalness: .10 }),
    darker: std({ color: 0x232120, roughness: .60, metalness: .10 }),
    steel: std({ color: 0xb3b0aa, roughness: .35, metalness: .85 }),
    wood: std({ color: 0xffffff, roughness: .45, map: wood }),
    wood2: std({ color: 0xffffff, roughness: .48, map: wood }),
    seat: std({ color: 0xb2da9c, roughness: .90, map: feltMap }),
    seatB: std({ color: 0xa2d393, roughness: .90, map: feltMap }),
    seatC: std({ color: 0xc0e6ab, roughness: .90, map: feltMap }),
    sofa: linen, sofaB: linen, linen,
    sofaC: std({ color: 0xece4d8, roughness: .92, map: linenMap }),
    cushionA: std({ color: 0xb2da9c, roughness: .95, map: linenMap }),
    cushionB: std({ color: 0xeee6da, roughness: .95, map: linenMap }),
    cushionC: std({ color: 0xc0e6ab, roughness: .95, map: linenMap }),
    rug: std({ color: 0xffffff, roughness: 1, map: juteMap }),
    rugB: std({ color: 0xf0e2cc, roughness: 1, map: juteMap }),
    rugArt: std({ color: 0xffffff, roughness: .95, map: rugArt }),
    panelA: std({ color: 0xb2da9c, roughness: .95, map: feltMap }),
    panelB: std({ color: 0xa2d393, roughness: .95, map: feltMap }),
    felt: std({ color: 0xb2da9c, roughness: .95, map: feltMap }),
    paper: std({ color: 0xf7f4ea, roughness: .92 }),
    mugA: std({ color: 0xd8cfc2, roughness: .40 }),
    mugB: std({ color: 0x7a9469, roughness: .40 }),
    mugC: std({ color: 0xc28a3a, roughness: .40 }),
    bookA: std({ color: 0xa8574a, roughness: .85 }),
    bookB: std({ color: 0x5f7d59, roughness: .85 }),
    bookC: std({ color: 0xc9a86a, roughness: .85 }),
    bookD: std({ color: 0x7d766c, roughness: .85 }),
    bookE: std({ color: 0x8a6a45, roughness: .85 }),
    leaf: leafCard, leaf2: leafCard, leafCard,
    pot: std({ color: 0xe0d6c6, roughness: .35 }),
    potTerra: std({ color: 0x8a6a45, roughness: .80 }),
    kbd: std({ color: 0xe8e3db, roughness: .60, map: keyboardTexture() }),
    sky: std({ color: 0xffffff, roughness: .20, map: sky, emissiveMap: sky,
      emissive: 0xffffff, emissiveIntensity: 1.15 }),
    accent: new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false }),
    visor: new THREE.MeshPhysicalMaterial({ color: 0x262626, roughness: .12,
      clearcoat: 1, clearcoatRoughness: .08 }),
    glass: std({ color: 0xffffff, roughness: .04, transparent: true, opacity: .10,
      envMapIntensity: 1.4, depthWrite: false, side: THREE.DoubleSide }),
    glassPane: std({ color: 0xffffff, roughness: .04, transparent: true, opacity: .08,
      envMapIntensity: 1.4, depthWrite: false, side: THREE.DoubleSide }),
    joint: std({ color: 0x5e5a55, roughness: .70, vertexColors: true }),
    shadow: new THREE.MeshBasicMaterial({ map: softShadowTexture(), color: 0x3a2e20,
      transparent: true, depthWrite: false, opacity: .55 }),
    islandShadow: new THREE.MeshBasicMaterial({ map: softShadowTexture(), color: 0x4a3d2c,
      transparent: true, depthWrite: false, opacity: .22 }),
    glowW: glowMaterial(0xffd9a0),
    crown: std({ color: 0xd8b45c, metalness: .70, roughness: .30 }),
    lampWarm: std({ color: 0xffe3b0, emissive: 0xffc87a, emissiveIntensity: 1.3,
      roughness: .40, toneMapped: false }),
    rattan: std({ color: 0xffffff, roughness: .75, map: rattanMap }),
    wallart: new THREE.MeshBasicMaterial({ color: 0xffffff, map: artMap }),
    signWood: std({ color: 0xc9a86a, roughness: .70 }),
    board: new THREE.MeshBasicMaterial({ map: boardTexture() }),
  };
}

/** 白のラジアルグラデーション（影・グローで共用する版の素）。 */
function radialTexture(inner, mid) {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 256;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(128, 128, 8, 128, 128, 128);
  grd.addColorStop(0, `rgba(255,255,255,${inner})`);
  grd.addColorStop(0.55, `rgba(255,255,255,${mid})`);
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd; g.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
let _softShadowTex = null;
function softShadowTexture() {
  if (!_softShadowTex) _softShadowTex = radialTexture(1.0, 0.45);
  return _softShadowTex;
}
let _glowTex = null;
function glowMaterial(color) {
  if (!_glowTex) _glowTex = radialTexture(0.85, 0.28);
  return new THREE.MeshBasicMaterial({
    map: _glowTex, color, transparent: true, blending: THREE.AdditiveBlending,
    depthWrite: false, toneMapped: false, opacity: .18, side: THREE.DoubleSide });
}

export class IsoScene {
  constructor(container) {
    this.container = container;
    this.actors = new Map();          // id → {nodes, from, to, startedAt, seed}
    this.disposed = false;
    this.growth = createGrowth();
    this._setLayoutModel(DEFAULT_SPEC);
    this.layoutKey = "M:legacy";
    this.seeded = false;              // 初回描画を済ませたか（出勤演出の出し分け）

    const q = new URLSearchParams(typeof location === "undefined" ? "" : location.search);
    this.streaming = q.get("stream") === "1";
    const requestedQuality = this.streaming ? "off" : q.get("quality") || "high";
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.post = new PostProcess(this.renderer, {
      quality: requestedQuality, tilt: q.get("tilt") === "1",
    });
    this.renderer.setPixelRatio(Math.min(
      typeof window === "undefined" ? 1 : (window.devicePixelRatio || 1),
      this.post.quality === "mobile" ? 1.5 : 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = q.get("shadow") === "vsm" ? THREE.VSMShadowMap : THREE.PCFShadowMap;
    this.renderer.toneMapping = THREE.NeutralToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    if (this.streaming) {
      this.renderer.setClearColor(0x000000, 0);
    }
    container.append(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(0xf4efe7, 70, 150);
    this.environments = roomEnvironment(this.renderer);
    this.scene.environment = this.environments.day;
    // Environment windows already face the office's -X and -Z window walls.
    this.scene.environmentRotation.y = 0;
    this.scene.environmentIntensity = 0.60;
    resetRand(); // All procedural textures and plants consume clock.rand in construction order.
    this.materials = makeMaterials(this.post.quality);
    // GPT-Image生成デカール（ui/iso/tex/*.webp・コミット済みアセット）。
    // 非同期ロードなので「全部確定するまで probe.ready を抑え、確定のたび再描画」を守る
    // （守らないと golden が差し替え前後どちらを撮るか不定になりフレークする）。
    // 404 は手続きテクスチャのまま確定＝ファイルが無くても壊れない。
    this.assetsPending = 0;
    const swapTex = (url, apply) => {
      this.assetsPending += 1;
      new THREE.TextureLoader().load(url,
        (t) => {
          if (this.disposed) { t.dispose(); this.assetsPending -= 1; return; }
          t.colorSpace = THREE.SRGBColorSpace;
          apply(t);
          this.assetsPending -= 1;
          this._rerender();
        },
        undefined,
        () => { this.assetsPending -= 1; this._rerender(); });
    };
    // Update the existing texture images: repeat/rotation/anisotropy survive async loading.
    const texturesByFile = new Map();
    for (const mat of Object.values(this.materials)) {
      const texture = mat.map, file = texture?.userData.file;
      if (!file) continue;
      if (!texturesByFile.has(file)) texturesByFile.set(file, new Set());
      texturesByFile.get(file).add(texture);
    }
    for (const [file, textures] of texturesByFile) swapTex(`/ui/iso/tex/${file}`, (loaded) => {
      for (const texture of textures) { texture.image = loaded.image; texture.needsUpdate = true; }
      loaded.dispose();
    });
    for (const [index, file] of ["wallart_a.webp", "wallart_b.webp"].entries()) {
      swapTex(`/ui/iso/tex/${file}`, (loaded) => {
        const map = this.materials.wallart.map;
        map.image.getContext("2d").drawImage(loaded.image, index * 256, 0, 256, 320);
        map.needsUpdate = true; loaded.dispose();
      });
    }
    this._swapTex = swapTex;

    // カメラ。構図は方位角(az)と仰角(el)の2つだけで決まる。
    //   方位角 45° = 真横からの等角（初期案）
    //          0°  = 入口の正面から見る（初期案から反時計回りに45°）
    //   仰角   高いほど俯瞰（床が面として見える）・低いほど水平（奥行きが潰れる）
    // ?az=<度>&el=<度> で試せる（構図の詰めはブラウザで回して決める）。
    const num = (k, d) => {
      const v = Number.parseFloat(q.get(k));
      return Number.isFinite(v) ? v : d;
    };
    const AZIMUTH = num("az", 45) * Math.PI / 180;
    const ELEVATION = num("el", 40) * Math.PI / 180;   // 上から見下ろす（?el= で調整可）
    // 下端の余分（HUDカードぶんシーンを上へ逃がす量・?pad= で調整可）。
    // 0.17 は「右下ラウンジが見切れない」構図比較でユーザーが選んだ値（2026-07-30）。
    this.botPad = this.streaming ? 0 : num("pad", 0.17);
    const DIST = 52;
    this.target = new THREE.Vector3(-0.2, 0.7, -0.6);
    this.camera = new THREE.OrthographicCamera(-10, 10, 6, -6, 0.1, 260);
    this.camera.position.set(
      this.target.x + Math.sin(AZIMUTH) * Math.cos(ELEVATION) * DIST,
      this.target.y + Math.sin(ELEVATION) * DIST,
      this.target.z + Math.cos(AZIMUTH) * Math.cos(ELEVATION) * DIST,
    );
    this.camera.lookAt(this.target);
    // 収めたい範囲（床＋壁の高さ）。画角はここから自動で決めるので余白が出ない。
    this.contentBox = new THREE.Box3(
      new THREE.Vector3(this.model.WALL.left - 0.25, 0, this.model.LAYOUT.floor.z - this.model.LAYOUT.floor.d / 2 - 0.25),
      new THREE.Vector3(this.model.WALL.right + 0.15, 2.8, this.model.LAYOUT.floor.z + this.model.LAYOUT.floor.d / 2 + 0.20),
    );

    this.hemi = new THREE.HemisphereLight(0xf0f4fa, 0xd9c9b0, 0.60);
    this.scene.add(this.hemi);
    this.key = new THREE.DirectionalLight(0xfff3e0, 1.60);
    this.key.position.set(-15, 17, -19);
    this.key.castShadow = true;
    this.key.shadow.mapSize.set(2048, 2048);
    this.key.shadow.intensity = 0.55;
    this.key.shadow.bias = -0.0004;
    this.key.shadow.normalBias = 0.02;
    this.scene.add(this.key, this.key.target);
    this.fill = new THREE.DirectionalLight(0xe9efe6, 0.45);
    this.fill.position.set(14, 10, 15);
    this.scene.add(this.fill);
    this.rim = new THREE.DirectionalLight(0xffe9cf, 0.35);
    this.rim.position.set(-8, 5, -13);
    this.scene.add(this.rim);
    this._manualHour = null;
    this._applyHour(localHour());

    this.displays = new ActivityScreens(this.materials.board.map);
    this._staticSeed = randState();
    this.staticMeshes = buildOffice(this.materials, this.spec, this.model);
    // シーン調査用の窓口（隠れた退行はレイキャストで特定できる。埋没バグの発見実績あり）
    if (typeof window !== "undefined") window.__debugScene = this;
    for (const m of this.staticMeshes) this.scene.add(m);
    this.monitors = buildMonitors(this.displays, this.materials, this.model);
    this.scene.add(this.monitors);

    this.robots = new RobotBatch(this.scene, this.materials, CAPACITY);
    // ボスロボ（データ非連動の常駐デコ・王冠つき・クリックで「ボス指令」）
    this.boss = makeSkeleton();
    this.boss.root.scale.setScalar(1.85);
    this.bossAccent = new THREE.Color(0xd8b45c);

    // ❗マーカー（承認/質問まちの頭上・ユーザーFB）。スプライト=常にカメラを向く
    this.attnMarkers = [];
    const attnMat = new THREE.SpriteMaterial({
      map: markerTexture("attention"), transparent: true, depthTest: false, toneMapped: false });
    for (let i = 0; i < 6; i++) {
      const sp = new THREE.Sprite(attnMat);
      sp.scale.set(0.62, 0.62, 1);
      sp.visible = false;
      sp.renderOrder = 5;
      this.scene.add(sp);
      this.attnMarkers.push(sp);
    }

    // 💭マーカー（kind==="think" の頭上・R56）。❗と同じスプライトプールの流儀。
    // 絵文字フォント依存を避け、雲形＋3点を手で描く（決定論）。
    this.thinkMarkers = [];
    const thinkMat = new THREE.SpriteMaterial({
      map: markerTexture("think"), transparent: true, depthTest: false, toneMapped: false });
    for (let i = 0; i < 6; i++) {
      const sp = new THREE.Sprite(thinkMat);
      sp.scale.set(0.58, 0.58, 1);
      sp.visible = false;
      sp.renderOrder = 5;
      this.scene.add(sp);
      this.thinkMarkers.push(sp);
    }
    // 💬 おしゃべりドット（R59・休憩の会話の話し手の頭上）。優先順は ❗ > 💭 > 💬
    this.chatMarkers = [];
    const chatMat = new THREE.SpriteMaterial({
      map: markerTexture("chat"), transparent: true, depthTest: false, toneMapped: false });
    for (let i = 0; i < 4; i++) {
      const sp = new THREE.Sprite(chatMat);
      sp.scale.set(0.5, 0.5, 1);
      sp.visible = false;
      sp.renderOrder = 5;
      this.scene.add(sp);
      this.chatMarkers.push(sp);
    }
    // 会議チビロボ（部下）の骨格プール（メッシュ無し＝スケルトンだけ・遅延生成）
    this.chibiPool = [];
    this._chibiTint = new THREE.Color();
    // R58: 通路グラフ（机すり抜け根絶）。歩行は必ずこのレーンを経由する

    // ── R68: 生命感の描画状態（すべて「初回=-∞」＝frozen では遷移完了状態で描く） ──
    // ✓マーカー（❗解消の瞬間・0.6秒だけ頭上に出す）
    this.doneMarkers = [];
    const doneMat = new THREE.SpriteMaterial({
      map: markerTexture("done"), transparent: true, depthTest: false, toneMapped: false });
    for (let i = 0; i < 4; i++) {
      const sp = new THREE.Sprite(doneMat);
      sp.visible = false;
      sp.renderOrder = 5;
      this.scene.add(sp);
      this.doneMarkers.push(sp);
    }
    this.markerSince = new Map();      // "attn:<id>"等 → 初認識t（ポップイン用）
    this.attnResolved = new Map();     // id → {at, x, y, z}（✓演出）
    this.levelUps = new Map();        // live-only event timestamps; level baseline is separate
    this.levels = new Map();
    this.chibiSeen = new Map();        // "table:idx" → 初認識t（登場バウンス）
    this.chibiGone = new Map();        // "table:idx" → {at, seat, seed, tint}（解散シュリンク）

    // 🧹 掃除ロボ（t>=30 で通路を永久巡回・golden の t=3.2 では非表示）
    this.cleaner = makeCleanerBot(this.materials);
    this.cleaner.visible = false;
    this.scene.add(this.cleaner);
    this.viewScale = 1;
    this.fitMode = "contain";
    this.resize();
  }

  _setLayoutModel(spec) {
    this.spec = spec;
    this.model = buildLayout(spec);
    this.stops = officeStops(spec, this.model);
    this.navGraph = this.model.walkGraph;
    this._meetingRooms = this.model.anchors.meeting.byRoom;
    this._chibiSeats = this.model.anchors.chibi;
    this.anchors = { ...this.model.anchors, meeting: Object.values(this._meetingRooms).flat() };
    this._meetAssignWorld = null;
    this._cleanerTotal = this.model.cleanerRoute.slice(1).reduce((sum, point, i) =>
      sum + Math.hypot(point[0] - this.model.cleanerRoute[i][0], point[1] - this.model.cleanerRoute[i][1]), 0);
  }

  /** Share the actual seat assignment with desktop/PWA labels without mutating core worlds. */
  prepareWorld(world) {
    if (world === this._preparedWorld || world === this._sourceWorld) return this._preparedWorld;
    this.maxSeen = this.growth.observe(world.agents);
    const level = world.growth?.office?.level;
    const tier = tierFor({ agents: world.agents, maxSeen: this.maxSeen, officeLevel: level });
    const key = `${tier}:${level == null ? "legacy" : JSON.stringify(decorationsFor(level))}`;
    if (key !== this.layoutKey) {
      this._disposeStatic();
      this._setLayoutModel(specFor(tier, level));
      this.layoutKey = key;
      this._sourceWorld = this._preparedWorld = null;
      this.displays.resize(this.anchors.desk.length);
      withRandState(this._staticSeed, () => {
        this.staticMeshes = buildOffice(this.materials, this.spec, this.model);
        this.monitors = buildMonitors(this.displays, this.materials, this.model);
      });
      for (const mesh of this.staticMeshes) this.scene.add(mesh);
      this.scene.add(this.monitors);
      const { WALL } = this.model;
      this.contentBox.min.set(WALL.left - .25, 0, WALL.back - .25);
      this.contentBox.max.set(WALL.right + .15, 2.8, WALL.front + .20);
      this._fitShadowCamera();
      // Initial fit includes the selected tier; later growth preserves pan/zoom/focus.
      if (!this.seeded) this.resize();
      for (const actor of this.actors.values()) actor.reroute = true;
    }
    this._sourceWorld = world;
    const seats = assignSeats(world.agents, this.anchors.desk.length);
    this._preparedWorld = { ...world, seats, overflow: assignOverflow(world.agents, seats) };
    return this._preparedWorld;
  }

  projectSignAnchors() { return projectSignAnchors(this.model); }

  /** Dispose only geometry and the layout-owned light map. Shared materials/actors survive. */
  _disposeStatic() {
    for (const mesh of this.staticMeshes) {
      mesh.geometry.dispose();
      this.scene.remove(mesh);
    }
    this.monitors.traverse((object) => { if (object.geometry) object.geometry.dispose(); });
    this.scene.remove(this.monitors);
    this.materials.floor.lightMap?.dispose();
    this.materials.floor.lightMap = null;
  }

  /** Explicit scene override, also usable while ?t= freezes the animation clock. */
  setHour(h) {
    if (!Number.isFinite(h)) return;
    this._manualHour = Math.min(24, Math.max(0, h));
    this._applyHour(this._manualHour);
    this._rerender();
  }

  _applyHour(hour) {
    const h = Math.min(18, Math.max(7, hour));
    if (this._lightingHour === h) return;
    this._lightingHour = h;
    const [a, b] = h <= 11 ? HOUR_PRESETS.slice(0, 2) : HOUR_PRESETS.slice(1, 3);
    const k = smoothstep(a.hour, b.hour, h);
    this.key.color.copy(a.color).lerp(b.color, k);
    this.key.intensity = THREE.MathUtils.lerp(a.intensity, b.intensity, k);
    this.key.position.copy(a.position).lerp(b.position, k);
    this._lampIntensity = THREE.MathUtils.lerp(a.lamp, b.lamp, k);
    this.materials.lampWarm.emissiveIntensity = this._lampIntensity;
    this.scene.environment = h >= 16 ? this.environments.evening : this.environments.day;
    this._fitShadowCamera();
  }

  /** Fit all eight content corners in light space, including the base below y=0. */
  _fitShadowCamera() {
    this.key.updateMatrixWorld();
    this.key.target.updateMatrixWorld();
    this.key.shadow.updateMatrices(this.key);
    const camera = this.key.shadow.camera;
    const bounds = this.contentBox.clone();
    bounds.min.y = Math.min(bounds.min.y, -0.72);
    const lightBounds = bounds.applyMatrix4(camera.matrixWorldInverse);
    const pad = 0.24; // Two penumbra widths beyond the projected content AABB.
    Object.assign(camera, {
      left: lightBounds.min.x - pad, right: lightBounds.max.x + pad,
      bottom: lightBounds.min.y - pad, top: lightBounds.max.y + pad,
      near: Math.max(0.1, -lightBounds.max.z - pad),
      far: Math.max(1, -lightBounds.min.z + pad),
    });
    camera.updateProjectionMatrix();
    const texel = Math.max((camera.right - camera.left) / 2048, (camera.top - camera.bottom) / 2048);
    this.key.shadow.radius = 0.12 / texel;
    this.key.shadow.updateMatrices(this.key);
  }

  /**
   * 画角の自動フィット。
   * 収めたい範囲の8隅をカメラ空間へ投影し、それが必ず入る最小の画角にする。
   * 手で VIEW を決めると窓の比率が変わるたびに床が余る／見切れるので、計算で出す。
   */
  /** User-selected views leave the default camera and frozen captures untouched. */
  setCameraView({ az, el, zoom }) {
    if (![az, el, zoom].every(Number.isFinite) || zoom <= 0) return;
    this.stopCinematic();
    const a = az * Math.PI / 180, e = el * Math.PI / 180;
    this.camera.position.set(this.target.x + Math.sin(a) * Math.cos(e) * 52,
      this.target.y + Math.sin(e) * 52, this.target.z + Math.cos(a) * Math.cos(e) * 52);
    this.camera.lookAt(this.target);
    this.camera.updateMatrixWorld();
    this._userScale = 1 / zoom;
    this._userPanX = 0; this._userPanY = 0;
    this.resize();
    if (this._cinematicPaused) this._cinematicFrame = { ...this._frame };
    this._rerender();
  }

  /** R77: カメラの寄り（1=全景フィット・小さいほど寄る）。縦長画面向け。 */
  setViewScale(k, fitMode) {
    const v = Number(k);
    this.viewScale = Number.isFinite(v) && v > 0.2 ? Math.min(1, v) : 1;
    if (fitMode) this.fitMode = fitMode;
    this.resize();
  }

  /** R86-H: 下部UIに隠れないよう、シーンを上へ px 単位で寄せる（0=従来どおり）。 */
  setViewShiftPx(px) {
    const v = Number(px) || 0;
    if (v === (this.viewShiftPx || 0)) return;
    this.viewShiftPx = v;
    this.resize();
  }

  resize() {
    const w = Math.max(1, this.container.clientWidth);
    const h = Math.max(1, this.container.clientHeight);
    const aspect = w / h;

    this.camera.updateMatrixWorld();
    const inv = new THREE.Matrix4().copy(this.camera.matrixWorld).invert();
    const b = this.contentBox;
    // min/max を別々に取り、フレームを内容の中心に寄せる。
    // |max| だけで決めると内容が軸から外れているぶん片側に余白・逆側に見切れが出る
    // （実際に右端のサーバーラックが切れ、左に余白が出た）。
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const v = new THREE.Vector3();
    for (const x of [b.min.x, b.max.x]) {
      for (const y of [b.min.y, b.max.y]) {
        for (const z of [b.min.z, b.max.z]) {
          v.set(x, y, z).applyMatrix4(inv);       // カメラ空間（右:+x 上:+y）
          minX = Math.min(minX, v.x); maxX = Math.max(maxX, v.x);
          minY = Math.min(minY, v.y); maxY = Math.max(maxY, v.y);
        }
      }
    }
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const hx = (maxX - minX) / 2;
    const hy = (maxY - minY) / 2;
    // 小さいほどズームイン＝端が切れる。参考画像は部屋の角がフレーム外へ
    // 切れるほど寄っている（余白の方が什器の見切れより悪い）。
    const MARGIN = 0.86;
    // R77: 既定は contain（全景が入る＝デスクトップ従来どおり・golden不変）。
    // スマホの縦長では contain だと上下が大きく余りロボットが豆粒になるので、
    // balanced（contain と cover の中間）を選べるようにした。cover は左右が切れる。
    const contain = Math.max(hy, hx / aspect);
    const cover = Math.min(hy, hx / aspect);
    const base = this.fitMode === "cover" ? cover
      : this.fitMode === "balanced" ? (contain + cover) / 2
      : contain;
    const view = base * MARGIN * (this.viewScale || 1);
    this.view = view;
    // 画面の下端には HUD カードが浮くので、下側にだけ少し余分を見せて
    // シーンの主役（ロボット）がカードの裏に沈まないようにする（?pad= で調整可）。
    const BOT_PAD = this.botPad ?? 0.05;
    // R68: フレームの基準を保存（update のカメラ呼吸ドリフトはこの基準に対する
    // 微オフセット＝frozen では常にゼロで golden 完全不変）
    this._frame = {
      left: cx - view * aspect,
      right: cx + view * aspect,
      top: cy + view,
      bottom: cy - view * (1 + BOT_PAD * 2),
    };
    // R86-H: 下部にUI（スマホの❗ドック等）が重なるぶん、可視域を上へずらす。
    // フレームの形は変えずに平行移動するだけなので歪まない（botPad を大きくすると
    // 縦だけ伸びて絵が潰れる）。既定 0＝デスクトップは完全に従来どおり＝golden不変。
    if (this.viewShiftPx) {
      const perPx = (this._frame.top - this._frame.bottom) / h;
      const d = this.viewShiftPx * perPx;
      this._frame.top -= d;
      this._frame.bottom -= d;
    }
    this.camera.left = this._frame.left;
    this.camera.right = this._frame.right;
    this.camera.top = this._frame.top;
    this.camera.bottom = this._frame.bottom;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
    this.post.resize();
    this._fitShadowCamera();
  }

  /**
   * ときどきコーヒーバーへ歩いて一服する（260秒周期に26秒・seedで位相分散・決定論）。
   * 起動30秒はやらない＝固定tのスクショ/goldenでは全員が持ち場に居る。
   */
  excursionFor(agent, t) {
    // ガードは15秒（R68: 開幕の一番見られる時間帯に誰も動かない空白を短縮。golden=t3.2は不変）
    if (!this.stops.coffee || agent.zone !== "desk" || agent.state !== "working" || t < 15) return null;
    const ph = (((t + seedOf(agent.id) * 40) % 260) + 260) % 260;
    if (ph >= 26) return null;
    const slot = stableIndex(agent.id, 3);
    return { x: this.stops.coffee.x - 0.8 + slot * 0.75, z: this.stops.coffee.z,
      yaw: Math.PI, y: 0, role: "stand" };
  }

  /**
   * R58: 待機エージェントの生活感。待機（❗なし）のロボは、たまに席を立って
   * オフィスの立ち寄り先（コーヒー・植物・窓際・ラウンジ口）まで歩き、佇んで戻る。
   * 240秒周期を7スロットに割り、自分のスロットの34秒窓だけ離席＝
   * 同時に居なくなるのは高々1スロット分（オフィスが無人化しない）。
   * 起動40秒はやらない＝固定tのgolden/スクショでは全員が持ち場に居る。
   */
  idleLifeFor(agent, t) {
    if (agent.zone !== "desk" || agent.state !== "waiting"
      || agent.attention || t < 15) return null;
    const ph = ((t % 238) + 238) % 238;
    const slot = stableIndex(agent.id, 7);
    if (ph < slot * 34 || ph >= slot * 34 + 34) return null;
    const spot = this.model.idleSpots[stableIndex(agent.id, this.model.idleSpots.length)];
    return { x: spot.x, z: spot.z, yaw: spot.yaw, y: 0, role: "stand" };
  }

  /**
   * R68: 表示値の遷移トラック。target が変わったら「いまの表示値」を起点に
   * dur 秒の smoothstep で追従する（座↔立の沈み・段差・向きのスナップ解消）。
   * 初回は at=-Infinity＝遷移完了状態で始まる → frozen(?t=固定) の golden 不変。
   */
  _track(actor, key, target, t, dur = 0.45, angular = false) {
    if (frozen) return target;
    let tr = actor[key];
    if (!tr) {
      tr = actor[key] = { from: target, to: target, at: -Infinity };
    }
    if (tr.to !== target) {
      const k = smoothstep(0, dur, t - tr.at);
      let delta = tr.to - tr.from;
      if (angular) delta = Math.atan2(Math.sin(delta), Math.cos(delta));
      tr.from = tr.from + delta * k;             // 現在の表示値から繋ぐ（跳ばない）
      tr.to = target;
      tr.at = t;
    }
    const k = smoothstep(0, dur, t - tr.at);
    let delta = tr.to - tr.from;
    if (angular) delta = Math.atan2(Math.sin(delta), Math.cos(delta));
    return tr.from + delta * k;
  }

  /** R68: マーカーのポップイン倍率（初回=-∞ → 常に1＝frozen golden 不変）。 */
  _popScale(key, t) {
    let since = this.markerSince.get(key);
    if (since === undefined) {
      since = this.seeded && !frozen ? t : -Infinity;
      this.markerSince.set(key, since);
    }
    const dt = t - since;
    return smoothstep(0, 0.35, dt) * (1 + 0.18 * Math.sin(Math.min(dt / 0.6, 1) * Math.PI));
  }

  /** ゾーンと席番号から目的地を決める。席が尽きたら床に立たせる（決定論）。 */
  anchorFor(agent, world, index) {
    const list = this.anchors[agent.zone] || this.anchors.desk;
    if (agent.zone === "meeting") {
      // R70: 3室へ「いちばん空いている部屋」分散（純関数＝labelAnchorForとも一致）
      const as = this._roomAssign(world).get(agent.id);
      if (as) {
        const room = this._meetingRooms[as.room] || [];
        const a = room[Math.min(as.seat, room.length - 1)];
        if (a) return a;
      }
    }
    if (agent.zone === "desk") {
      const seat = world.seats.get(agent.id);
      if (seat !== undefined && list[seat]) return list[seat];
      // R86-K: 席が尽きた人は**立ち作業**（待機列の空きスペースに立つ）。
      // ここが無いと index%12 で既存の席に重なり、椅子1脚に2体が同居する
      // （実測: 20セッションで8体が誰かの中に居た）。role:"stand" で座らせない。
      const ov = world.overflow?.get(agent.id);
      const stand = this.anchors.queue;
      if (ov !== undefined && stand && stand[ov]) return { ...stand[ov], role: "stand" };
    }
    const a = list[index % list.length];
    if (a) return a;
    return { x: 0, z: this.model.LAYOUT.floor.z, yaw: 0, y: 0 };
  }

  _roomAssign(world) {
    if (this._meetAssignWorld !== world) {
      const caps = {};
      for (const [k, v] of Object.entries(this._meetingRooms)) caps[k] = v.length;
      this._meetAssign = assignMeetingRooms(world.agents, caps, RESERVE_ROOMS);
      this._meetAssignWorld = world;
    }
    return this._meetAssign;
  }

  /** 画像ロード確定時に最後の world で描き直す（frozen ではループが回らないため）。 */
  _rerender() {
    if (this._lastWorld && !this.disposed) this.update(this._lastWorld, this._lastT);
  }

  /** 非同期アセットが確定したか（probe.ready の条件）。 */
  ready() {
    return this.assetsPending === 0;
  }

  /** world を反映する。位置が変わったアクターは歩いて移動する。 */
  update(world, t) {
    if (this.disposed) return;
    world = this.prepareWorld(world);
    this._lastWorld = world;
    this._lastT = t;
    // レイアウトが確定してから初回 resize が走るとは限らないので、変化を見て追従する
    const cw = this.container.clientWidth;
    const ch = this.container.clientHeight;
    if (cw !== this._w || ch !== this._h) {
      this._w = cw; this._h = ch;
      this.resize();
    }

    // ── R68: 環境に t を流す（全て t の純関数＝同じ t なら同じ絵・決定論のまま） ──
    this.displays.update(world);
    const hour = localHour();
    updateClock(this.monitors, frozen ? 11 : hour);
    this._applyHour(this._manualHour ?? hour);
    this.materials.lampWarm.emissiveIntensity = this._lampIntensity + Math.sin(t * 1.3) * 0.06;
    // ④カメラの呼吸ドリフト（非frozen限定＝goldenは完全不変。振幅は視野の約1%）
    //   ＋R70 フォーカスズーム: シートで選んだロボへ 0.5s で寄る（0.90倍・操作イベント起点。
    //   golden はシート閉じ=フォーカス無しで不変。frozen 中の openCompose は1フレームで完了状態）
    if (this._frame) {
      let k = this._focusK ?? 0;
      if (this._focusAnim) {
        const a = this._focusAnim;
        if (a.t0 === null) a.t0 = t;                     // イベント後の最初のフレームで開始
        const u = frozen ? 1 : Math.min(1, (t - a.t0) / 0.5);
        const s = u * u * (3 - 2 * u);                   // smoothstep
        k = a.k0 + (a.k1 - a.k0) * s;
        if (u >= 1) {
          this._focusK = a.k1;
          this._focusAnim = null;
          if (a.k1 === 0) this._focusId = null;
        } else { this._focusK = k; }
      }
      let f = this._frame;
      const target = this._focusId != null ? this.actors.get(this._focusId) : null;
      if (k > 0 && target) {
        // 対象ロボのカメラ空間位置を寄り先フレームの中心に（毎フレーム追従＝歩行中もOK）
        this.camera.updateMatrixWorld();
        this._focusInv = (this._focusInv || new THREE.Matrix4());
        this._focusInv.copy(this.camera.matrixWorld).invert();
        const p = target.nodes.root.position;
        this._focusV = (this._focusV || new THREE.Vector3());
        this._focusV.set(p.x, p.y + 0.9, p.z).applyMatrix4(this._focusInv);
        const zoom = 0.90;
        const aspect = (f.right - f.left) / (f.top - f.bottom);
        const halfH = (f.top - f.bottom) / 2 * zoom;
        const halfW = halfH * aspect;
        const g = { left: this._focusV.x - halfW, right: this._focusV.x + halfW,
          top: this._focusV.y + halfH, bottom: this._focusV.y - halfH };
        // R86-H: 寄り先も可視域（下部UIの上）に合わせる。ここを揃えないと、
        // タップで寄った瞬間に対象ロボが**ドックの裏へ移動**し、2度目のタップが
        // ドックのボタンに当たる＝「タップしても詳細が開かない」になる（実測）。
        if (this.viewShiftPx) {
          const perPx = (g.top - g.bottom) / (this.renderer.domElement.clientHeight || 1);
          const d = this.viewShiftPx * perPx;
          g.top -= d;
          g.bottom -= d;
        }
        f = { left: f.left + (g.left - f.left) * k,
          right: f.right + (g.right - f.right) * k,
          top: f.top + (g.top - f.top) * k,
          bottom: f.bottom + (g.bottom - f.bottom) * k };
      }
      // R80.6: ピンチ/ドラッグのユーザービュー（スマホ）。既定(scale=1,pan=0)は完全no-op
      // ＝goldenとデスクトップはビット不変。focusの寄り先に対して相対＝寄った先でもパンできる。
      const us = this._userScale ?? 1;
      const upx = this._userPanX || 0;
      const upy = this._userPanY || 0;
      if (us !== 1 || upx || upy) {
        const cx = (f.left + f.right) / 2 + upx;
        const cy = (f.top + f.bottom) / 2 + upy;
        const hw = (f.right - f.left) / 2 * us;
        const hh = (f.top - f.bottom) / 2 * us;
        f = { left: cx - hw, right: cx + hw, top: cy + hh, bottom: cy - hh };
      }
      const dx = frozen ? 0 : Math.sin(t * 0.045) * this.view * 0.012 * (1 - k * 0.6);
      const dy = frozen ? 0 : Math.sin(t * 0.031 + 1.7) * this.view * 0.007 * (1 - k * 0.6);
      this.camera.left = f.left + dx;
      this.camera.right = f.right + dx;
      this.camera.top = f.top + dy;
      this.camera.bottom = f.bottom + dy;
      this.camera.updateProjectionMatrix();
    }
    // ⑤🧹掃除ロボ: t>=30 で通路を永久巡回（golden t=3.2 は非表示・首振りで掃除感）
    if (t < 30) {
      this.cleaner.visible = false;
    } else {
      this.cleaner.visible = true;
      const speed = 0.55;
      const period = this._cleanerTotal / speed;
      const ct = (((t - 30) % period) + period) % period;
      const cm = pathTravel(this.model.cleanerRoute, 0, ct, speed);
      this.cleaner.position.set(cm.x, 0, cm.z);
      this.cleaner.rotation.y = cm.yaw + Math.sin(t * 2.6) * 0.10;
    }

    const seen = new Set();
    const perZone = {};
    let welcome = false;
    const growth = growthChanges(this.levels, world.growth);
    this.levels = growth.levels;
    if (this.seeded && !frozen) {
      for (const change of growth.changes) this.levelUps.set(change.id, { at: t });
    }
    for (const [id, event] of this.levelUps) {
      if (frozen || t - event.at >= CELEBRATE_SECONDS || !world.agents.some(a => a.id === id)) this.levelUps.delete(id);
    }

    // R59: 休憩スポットの分散割当（決定論）＋エリア別の会話グループ
    const restAssign = assignRestSpots(world.agents, this.model.restSpots);
    const restGroups = {};
    for (const [id, si] of restAssign) {
      const area = this.model.restSpots[si].area;
      (restGroups[area] = restGroups[area] || []).push({ id, si });
    }
    for (const g of Object.values(restGroups)) g.sort((a, b) => a.si - b.si);

    for (const agent of world.agents) {
      seen.add(agent.id);
      const idx = (perZone[agent.zone] = (perZone[agent.zone] ?? -1) + 1);
      const restIdx = restAssign.get(agent.id);
      const target = this.excursionFor(agent, t) || this.idleLifeFor(agent, t)
        || (restIdx !== undefined ? this.model.restSpots[restIdx]
          : this.anchorFor(agent, world, idx));

      let actor = this.actors.get(agent.id);
      if (!actor) {
        // 初回は最初から席に居る（起動した瞬間にオフィスが埋まっている状態にする）。
        // 2回目以降に現れた社員だけが入口から歩いてくる＝「出勤してきた」が伝わる。
        // ※ frozen（?t=固定）だと時間が進まないので、入口から歩かせると永遠に
        //   入口で固まる（実際にこれで全員が画面外に消えた）。
        // R58: 歩行は通路グラフ経由＝机・部屋を突き抜けない（routePath）。
        actor = {
          nodes: makeSkeleton(),
          path: this.seeded && !frozen
            ? routePath([this.stops.entrance.x, this.stops.entrance.z], [target.x, target.z], this.navGraph)
            : [[target.x, target.z]],
          dest: [target.x, target.z],
          y: target.y, yaw: target.yaw,
          targetY: target.y, targetYaw: target.yaw,
          startedAt: t, seed: seedOf(agent.id),
          enteringAt: this.seeded && !frozen ? t : null,
        };
        actor.nodes.root.scale.setScalar(1.62);   // 主役は大きめ（部屋拡張で負けない）
        this.actors.set(agent.id, actor);
        welcome ||= this.seeded && !frozen;
      } else if (actor.reroute || actor.leavingAt != null || actor.dest[0] !== target.x || actor.dest[1] !== target.z) {
        // 目的地が変わった＝ゾーン移動。いまの位置から通路経由で歩き直す
        const cur = pathTravel(actor.path, actor.startedAt, t);
        actor.path = frozen ? [[target.x, target.z]]
          : routePath([cur.x, cur.z], [target.x, target.z], this.navGraph);
        actor.reroute = false;
        actor.dest = [target.x, target.z];
        actor.startedAt = t;
        actor.targetY = target.y;
        actor.targetYaw = target.yaw;
        actor.leavingAt = null;
      }

      const act = actFor(agent, t, actor.seed);
      actor.act = act;
      const attention = act.expr === "question";
      if (!frozen && actor.attention && !attention) this.attnResolved.set(agent.id, { at: t });
      actor.attention = attention;

      const provider = agent.provider || agent.vendor
        || agent.sessions?.find((session) => session.session === agent.session)?.vendor
        || agent.source || agent.external?.provider || agent.external;
      // Re-evaluate when a project's active session changes vendor. Legacy cx- IDs are Codex.
      actor.graphite = provider === "codex" || (!provider && agent.session?.startsWith("cx-"));
      actor.lobster = !actor.graphite && (provider === "openclaw" || Boolean(agent.external));
      actor.vendor = actor.graphite ? "codex" : actor.lobster ? "openclaw" : "claude";
      const m = pathTravel(actor.path, actor.startedAt, t);
      const walking = m.u < 1 && m.total > 0.05;
      actor.walking = walking;                     // 💬マーカーの判定で使う（R59）
      // R56: 思考中（kind==="think"）は自席で考え込むポーズ（ゾーンの優先は変えない）
      const thinking = !walking && agent.zone === "desk"
        && act.pose === "think" && !target.role;
      // R59: 休憩の社交。同エリアに2体以上=向かい合っておしゃべり・1体=くつろぎ変奏
      let pose = null;
      let poseKind = "";                           // R68: 遷移検出用のポーズ種別キー
      let chatYaw = null;
      if (!walking && restIdx !== undefined) {
        const spot = this.model.restSpots[restIdx];
        const group = restGroups[spot.area] || [];
        if (group.length >= 2) {
          const my = group.findIndex((e) => e.id === agent.id);
          // R68: 交代境界±0.4秒のクロスフェード（話し手成分が連続値で入れ替わる）
          const k = chatBlend(t, seedOf(spot.area), group.length, my);
          pose = chatPose(t, actor.seed, k);
          poseKind = "chat";
          // 相手（自分以外の重心）の方を向く＝「会話している」が構図で伝わる
          let cx = 0;
          let cz = 0;
          for (const e of group) {
            if (e.id === agent.id) continue;
            cx += this.model.restSpots[e.si].x;
            cz += this.model.restSpots[e.si].z;
          }
          const n1 = group.length - 1;
          const dx = cx / n1 - spot.x;
          const dz = cz / n1 - spot.z;
          // 一列3席（ベンチ）だと中央の人は「相手の重心＝自分の位置」になり向きが壊れる。
          // 実質ゼロなら席本来の向きのまま会話させる（R62）
          chatYaw = Math.hypot(dx, dz) > 0.05 ? Math.atan2(dx, dz) : null;
        } else if (spot.role !== "tablet") {
          // ソロ: くつろぎ（背もたれ+窓をぼんやり）と タブレット読み はhashで交代
          const solo = stableIndex(agent.id, 2) === 0;
          pose = solo
            ? relaxPose(t * 0.8, actor.seed)
            : poseFor("lounge", t, actor.seed, null, null);
          poseKind = solo ? "relax" : "loungeTab";
        }
      }
      if (!pose) {
        pose = thinking
          ? thinkingPose(t, actor.seed)
          : poseFor(agent.zone, t, actor.seed,
            walking ? walkPhaseFor(m.dist, actor.seed) : null, target.role || null);
        poseKind = thinking ? "think"
          : (walking ? "walk" : `${agent.zone}:${target.role || ""}`);
      }
      // Keep location-specific legs/hip height; gestures only change the upper body.
      const upper = (gesture) => ({ ...pose, headYaw: gesture.headYaw,
        headPitch: gesture.headPitch, arms: gesture.arms });
      const levelUp = this.levelUps.get(agent.id);
      const approved = this.attnResolved.get(agent.id);
      if (walking) {
        if (actor.enteringAt != null && t - actor.enteringAt < ENTER_SECONDS) {
          pose = enterPose(t - actor.enteringAt, actor.seed, walkPhaseFor(m.dist, actor.seed));
          poseKind = "enter";
        } else if (act.pose === "run") {
          pose = runPose(t, actor.seed, walkPhaseFor(m.dist, actor.seed));
          poseKind = "run";
        }
      }
      if (attention) {
        pose = upper(questionPose(t, actor.seed)); poseKind = walking ? "walk:question" : "question";
      } else if (!frozen && levelUp) {
        const gesture = celebratePose(t - levelUp.at, actor.seed);
        pose = { ...upper(gesture), hipY: pose.hipY + gesture.hipY - .44 };
        poseKind = walking ? "walk:celebrate" : "celebrate";
      } else if (!frozen && approved && t - approved.at < .9) {
        const nod = approvalPose(t - approved.at, actor.seed);
        pose = { ...pose, headYaw: nod.headYaw, headPitch: nod.headPitch };
        poseKind = walking ? "walk:approval" : "approval";
      } else if (!walking && act.pose === "read") {
        pose = upper(readPose(t, actor.seed)); poseKind = `read:${act.prop || ""}`;
      } else if (!walking && act.prop === "mug") {
        // Keep lounge conversation/relaxation; only the hand holding the cup changes.
        pose = { ...pose, arms: [pose.arms[0], { side: 1, shoulder: -.65,
          elbow: -1.2 + Math.sin(t * .6 + actor.seed) * .15 }] };
        poseKind += ":mug";
      }
      actor.prop = attention || poseKind === "enter" || (!frozen && (levelUp || (approved && t - approved.at < .9)))
        ? null : act.prop;
      // R68: ポーズ種別が変わったら 0.45秒かけて前のポーズから補間（座↔立のスナップ根絶）。
      // 初回は poseChangedAt=-Infinity ＝補間完了状態 → frozen の golden 不変。
      if (actor.poseKind !== poseKind) {
        actor.poseFrom = actor.lastPose || null;
        actor.poseChangedAt = actor.poseKind === undefined || frozen ? -Infinity : t;
        actor.poseKind = poseKind;
      }
      const blended = mixPose(actor.poseFrom, pose,
        smoothstep(0, 0.45, t - (actor.poseChangedAt ?? -Infinity)));
      actor.lastPose = blended;
      applyPose(actor.nodes, blended);
      // R80.7: タップ挨拶＝腕を上げて振る＋ぴょこ＋首かしげ（0.9秒・タップ起点のみ
      // ＝goldenは不変）。ポーズ適用の直後・root配置の前に上書きする。
      actor.agentArch = agent.arch || null;
      actor.expression = !attention && !frozen && (levelUp || (approved && t - approved.at < .9))
        ? "happy" : act.expr;
      if (this._greet && this._greet.has(agent.id)) {
        let gt = this._greet.get(agent.id);
        if (gt === null) { gt = t; this._greet.set(agent.id, t); }
        const u = (t - gt) / 0.9;
        if (u >= 1) {
          this._greet.delete(agent.id);
        } else if (u >= 0) {
          const arm = actor.nodes.arms[1];
          arm.shoulder.rotation.x = -2.5;
          arm.shoulder.rotation.z = arm.side * 0.07 + Math.sin(u * Math.PI * 5) * 0.38;
          arm.elbow.rotation.x = -0.45;
          actor.nodes.neck.rotation.z = 0.14 * Math.sin(Math.min(u * 1.4, 1) * Math.PI);
          actor.nodes.hip.position.y += 0.06 * Math.sin(Math.min(u * 2, 1) * Math.PI);
        }
      }

      const SIT_DROP = 0.24;          // 腰が座面に載る高さ（scale 1.62 に合わせ再調整）
      const standingRole = target.role === "present" || target.role === "stand";
      const seated = !walking && !standingRole
        && (agent.zone === "desk" || agent.zone === "meeting" || agent.zone === "lounge");
      const baseY = walking ? 0 : (actor.targetY ?? actor.y);
      // R68: 表示y/表示yawは遷移トラック経由（段差・着席の沈み・向き直りが滑らかに繋がる）
      const dispY = this._track(actor, "trY", baseY - (seated ? SIT_DROP : 0), t);
      const rawYaw = walking ? m.yaw : (chatYaw ?? actor.targetYaw ?? actor.yaw);
      const dispYaw = walking ? rawYaw
        : this._track(actor, "trYaw", rawYaw, t, 0.45, true);
      if (walking && actor.trYaw) { actor.trYaw.from = m.yaw; actor.trYaw.to = m.yaw; }
      actor.nodes.root.position.set(m.x, dispY, m.z);
      actor.nodes.root.rotation.y = dispYaw;
      if (!walking) {
        actor.y = actor.targetY ?? actor.y;
        actor.yaw = actor.targetYaw ?? actor.yaw;
      }
      // R68: 胸リングの状態色は 0.4秒で補間（❗発生/解消の瞬間色が跳ばない）
      const targetAccent = accentFor(agent);
      if (!actor.accentCur) {
        actor.accentCur = targetAccent.clone();
        actor.accentFrom = targetAccent.clone();
        actor.accentTo = targetAccent;
        actor.accentAt = -Infinity;
      } else if (actor.accentTo !== targetAccent) {
        actor.accentFrom.copy(actor.accentCur);
        actor.accentTo = targetAccent;
        actor.accentAt = t;
      }
      actor.accentCur.copy(actor.accentFrom)
        .lerp(actor.accentTo, smoothstep(0, 0.4, t - (actor.accentAt ?? -Infinity)));
      actor.accent = actor.accentCur;
      if (!frozen && levelUp && !attention) {
        actor.flashAccent ||= new THREE.Color();
        actor.accent = actor.flashAccent.copy(actor.accentCur)
          .lerp(CELEBRATION_REFLECTION, celebrationFlash(t - levelUp.at));
      }
    }

    // R68: 退勤＝即消滅ではなく、入口まで歩いて退場してから消える（出勤と対称）
    for (const [id, actor] of [...this.actors]) {
      if (seen.has(id)) { actor.leavingAt = null; continue; }
      if (!this.seeded || frozen) { this.actors.delete(id); continue; }
      if (actor.leavingAt == null) {
        const pos = actor.nodes.root.position;
        actor.leavingAt = t;
        actor.path = routePath([pos.x, pos.z], [this.stops.entrance.x, this.stops.entrance.z], this.navGraph);
        actor.dest = [this.stops.entrance.x, this.stops.entrance.z];
        actor.startedAt = t + LEAVE_SECONDS;
        actor.poseKind = "leave";
        actor.poseFrom = actor.lastPose || null;
        actor.poseChangedAt = t;
      }
      const dt = t - actor.leavingAt;
      const m = pathTravel(actor.path, actor.startedAt, t);
      if (dt >= LEAVE_SECONDS && m.u >= 1) {
        this.actors.delete(id);
        continue;
      }
      const preparing = dt < LEAVE_SECONDS;
      const next = preparing ? leavePose(dt, actor.seed) : walkPose(walkPhaseFor(m.dist, actor.seed));
      if (!preparing && actor.poseKind !== "exit") {
        actor.poseFrom = actor.lastPose;
        actor.poseChangedAt = t;
        actor.poseKind = "exit";
      }
      const blended = mixPose(actor.poseFrom, next, smoothstep(0, .45, t - actor.poseChangedAt));
      actor.lastPose = blended;
      applyPose(actor.nodes, blended);
      actor.prop = null;
      const dispY = this._track(actor, "trY", preparing ? (actor.targetY ?? 0) : 0, t);
      actor.expression = exprFor({ state: "waiting" }, t, actor.seed);
      actor.nodes.root.position.set(m.x, dispY, m.z);
      actor.nodes.root.rotation.y = this._track(actor, "trYaw", m.yaw, t, .45, true);
    }

    this.robots.begin();
    let n = 0;
    for (const [aid, actor] of this.actors) {
      if (n++ >= CAPACITY) break;
      this.robots.push(actor.nodes, actor.accent || null,
        actor.lobster ? LOBSTER_TINT : actor.graphite ? GRAPHITE_TINT : null,
        this._archFor(actor.agentArch, aid), actor.vendor, actor.expression, actor.prop);
    }
    // Reuse this.model.bossWalk's north lane for patrol and an out-and-back welcome trip.
    // A trip starts at the displayed position, so an arrival during patrol cannot teleport the boss.
    const bph = ((t % 300) + 300) % 300;
    const patrol = t >= 30 && bph < 20;
    if (this.seeded && !frozen && ((welcome && !this.bossTrip?.welcome)
      || (patrol && !this.bossPatrol && !this.bossTrip))) {
      const pos = this.boss.root.position;
      const lane = welcome ? this.model.bossWalk.slice(0, 2) : this.model.bossWalk;
      const outbound = routePath([pos.x, pos.z], lane[0], this.navGraph);
      outbound.push(...lane.slice(1));
      if (welcome) outbound.push(...routePath(lane.at(-1), [this.stops.entrance.x, this.stops.entrance.z], this.navGraph).slice(1));
      const route = [...outbound, ...routePath(outbound.at(-1), [this.stops.boss.x, this.stops.boss.z], this.navGraph).slice(1)];
      this.bossTrip = { route, at: t, welcome, poseFrom: this.bossLastPose, yFrom: pos.y };
    }
    this.bossPatrol = patrol;
    const trip = !frozen && this.seeded ? this.bossTrip : null;
    const bm = trip ? pathTravel(trip.route, trip.at, t, 1.15) : null;
    const bossWalking = bm && bm.u < 1;
    let bossPose = poseFor("meeting", t * .55, 7.7);
    if (bossWalking) {
      bossPose = mixPose(trip.poseFrom, walkPose(walkPhaseFor(bm.dist, 7.7)), smoothstep(0, .45, t - trip.at));
      const y = trip.yFrom * (1 - smoothstep(0, .5, t - trip.at));
      this.boss.root.position.set(bm.x, y, bm.z);
      this.boss.root.rotation.y = this._track(this.boss, "trYaw", bm.yaw, t, .3, true);
    } else {
      if (trip) {
        this.bossReturn = { at: t, pose: this.bossLastPose, y: this.boss.root.position.y };
        this.bossTrip = null;
      }
      const back = !frozen ? this.bossReturn : null;
      const k = back ? smoothstep(0, .45, t - back.at) : 1;
      bossPose = mixPose(back?.pose, bossPose, k);
      const y = (back?.y ?? 0) * (1 - k) + (this.stops.boss.baseY - .35) * k;
      this.boss.root.position.set(this.stops.boss.x, y, this.stops.boss.z);
      this.boss.root.rotation.y = this._track(this.boss, "trYaw", 0, t, .45, true);
    }
    applyPose(this.boss, bossPose);
    this.bossLastPose = bossPose;
    this.robots.push(this.boss, this.bossAccent, null, null, "claude", exprFor({ state: "waiting" }, t, 7.7));

    // R56: 会議チビロボ＝minions を親と同じ卓の縁に立たせて頷かせる（上限4/卓・8/全体）。
    // InstancedMesh への行列追加だけ＝drawCalls は増えない。位相は親id+序数で分散。
    // R68: 登場は床から「ぴょこん」と生え（popScale）、解散は0.3秒のシュリンクで消える。
    const tableUsed = { meet: 0, meet2: 0, meet3: 0, meet4: 0 };   // R73: 4室
    const chibiKeys = new Set();
    let chibiN = 0;
    const borrowChibi = () => {
      let ch = this.chibiPool[chibiN];
      if (!ch) {
        // R58: 2頭身のチビ骨格（大きな頭・短い手足）＝「部下のチビ感」はデザインで出す
        ch = makeChibiSkeleton();
        this.chibiPool[chibiN] = ch;
      }
      chibiN += 1;
      return ch;
    };
    this._chibiMeta = this._chibiMeta || new Map();
    for (const agent of world.agents) {
      if (chibiN >= CHIBI_MAX) break;
      if (agent.zone !== "meeting" || !(agent.minions > 0)) continue;
      const actor = this.actors.get(agent.id);
      if (!actor) continue;
      const as = this._roomAssign(world).get(agent.id);
      const table = as ? as.room : (actor.dest[0] < 0 ? "meet" : "meet2");
      const seats = this._chibiSeats[table];
      const count = Math.min(agent.minions, 4);
      for (let i = 0; i < count && tableUsed[table] < seats.length
        && chibiN < CHIBI_MAX; i++) {
        const seat = seats[tableUsed[table]++];
        const key = `chibi:${table}:${tableUsed[table] - 1}`;
        chibiKeys.add(key);
        const ch = borrowChibi();
        const seed = actor.seed + (i + 1) * 1.9;
        applyPose(ch, chibiPose(t, seed));       // 頷き＋たまにピョコン跳ね・挙手
        ch.root.scale.setScalar(0.95 * Math.max(0.001, this._popScale(key, t)));
        ch.root.position.set(seat.x, seat.y, seat.z);
        ch.root.rotation.y = seat.yaw;
        // アクセントは親の淡色版＝「同じチームの部下」が色で伝わる
        this._chibiTint.copy(actor.accent || ACCENTS.resting).lerp(CHIBI_WHITE, 0.45);
        this._chibiMeta.set(key, { seat, seed, tint: this._chibiTint.clone(), vendor: actor.vendor });
        this.robots.push(ch, this._chibiTint, null, null, actor.vendor, exprFor({ state: "waiting" }, t, seed));
      }
    }
    // 解散したチビ（前フレームまで居た席）は0.3秒縮んで消える
    for (const key of [...this.markerSince.keys()]) {
      if (!key.startsWith("chibi:") || chibiKeys.has(key)) continue;
      this.markerSince.delete(key);
      const meta = this._chibiMeta.get(key);
      if (meta && this.seeded) this.chibiGone.set(key, { ...meta, at: t });
      this._chibiMeta.delete(key);
    }
    for (const [key, gone] of [...this.chibiGone]) {
      const k = 1 - smoothstep(0, 0.3, t - gone.at);
      if (k <= 0 || chibiN >= CHIBI_MAX) { this.chibiGone.delete(key); continue; }
      const ch = borrowChibi();
      applyPose(ch, chibiPose(t, gone.seed));
      ch.root.scale.setScalar(0.95 * Math.max(0.001, k));
      ch.root.position.set(gone.seat.x, gone.seat.y, gone.seat.z);
      ch.root.rotation.y = gone.seat.yaw;
      this.robots.push(ch, gone.tint, null, null, gone.vendor, exprFor({ state: "waiting" }, t, gone.seed));
    }
    this.robots.end();

    // ❗マーカー: attention のアバター頭上でゆっくり浮く（R68: ポンと弾んで出る）
    let mi = 0;
    const attnNow = new Set();
    for (const agent of world.agents) {
      if (mi >= this.attnMarkers.length) break;
      if (!this.actors.get(agent.id)?.attention) continue;
      attnNow.add(agent.id);
      const actor = this.actors.get(agent.id);
      if (!actor) continue;
      const sp = this.attnMarkers[mi++];
      const pos = actor.nodes.root.position;
      const s = 0.62 * this._popScale(`attn:${agent.id}`, t);
      sp.scale.set(Math.max(0.001, s), Math.max(0.001, s), 1);
      sp.position.set(pos.x, pos.y + 2.55 + Math.sin(t * 2.2 + actor.seed) * 0.07, pos.z);
      sp.visible = true;
    }
    for (; mi < this.attnMarkers.length; mi++) this.attnMarkers[mi].visible = false;
    // R68: ❗解消の瞬間（worldに居るのに attention でなくなった）＝0.6秒だけ緑✓
    for (const key of [...this.markerSince.keys()]) {
      if (!key.startsWith("attn:")) continue;
      const id = key.slice(5);
      if (attnNow.has(id)) continue;
      this.markerSince.delete(key);
      // Answer events are detected per actor, independently of the six-sprite pool.
    }
    for (const [id, event] of this.attnResolved) {
      if (frozen || t - event.at >= .9 || !seen.has(id)) this.attnResolved.delete(id);
    }
    let di = 0;
    // Share the existing four ✓ sprites; attention retains priority over completion.
    const done = new Map([...this.attnResolved, ...this.levelUps]);
    for (const [id, res] of done) {
      const dt = t - res.at, actor = this.actors.get(id);
      const duration = this.levelUps.has(id) ? CELEBRATE_SECONDS : .6;
      if (frozen || dt < 0 || dt >= duration || !actor || actor.attention || di >= this.doneMarkers.length) continue;
      const sp = this.doneMarkers[di++], pos = actor.nodes.root.position;
      const s = .60 * smoothstep(0, .15, dt) * (1 - smoothstep(duration - .18, duration, dt));
      sp.scale.set(Math.max(.001, s), Math.max(.001, s), 1);
      sp.position.set(pos.x, pos.y + 2.55 + dt * .5, pos.z);
      sp.visible = true;
    }
    for (; di < this.doneMarkers.length; di++) this.doneMarkers[di].visible = false;

    // 💭マーカー: 思考中（kind==="think"）の頭上。❗が出ている間は❗を優先
    let ti = 0;
    const thinkNow = new Set();
    for (const agent of world.agents) {
      if (ti >= this.thinkMarkers.length) break;
      if (this.actors.get(agent.id)?.attention || this.actors.get(agent.id)?.act.pose !== "think") continue;
      const actor = this.actors.get(agent.id);
      if (!actor) continue;
      thinkNow.add(agent.id);
      const sp = this.thinkMarkers[ti++];
      const pos = actor.nodes.root.position;
      const s = 0.58 * this._popScale(`think:${agent.id}`, t);
      sp.scale.set(Math.max(0.001, s), Math.max(0.001, s), 1);
      sp.position.set(pos.x + 0.30,
        pos.y + 2.62 + Math.sin(t * 1.6 + actor.seed) * 0.06, pos.z);
      sp.visible = true;
    }
    for (; ti < this.thinkMarkers.length; ti++) this.thinkMarkers[ti].visible = false;
    for (const key of [...this.markerSince.keys()]) {
      if (key.startsWith("think:") && !thinkNow.has(key.slice(6))) this.markerSince.delete(key);
    }

    // 💬マーカー: 休憩の会話グループの「いま話している人」の頭上（❗>💭>💬）。
    // R68: キーに話者idを含める＝交代のたび新キーでポップイン（ワープでなく弾んで出る）
    let ci = 0;
    const chatNow = new Set();
    for (const [area, group] of Object.entries(restGroups)) {
      if (ci >= this.chatMarkers.length || group.length < 2) continue;
      const sIdx = chatSpeaker(t, seedOf(area), group.length);
      const speakerId = group[sIdx]?.id;
      const agent = world.agents.find((a) => a.id === speakerId);
      const actor = this.actors.get(speakerId);
      if (!agent || !actor || actor.walking || agent.attention || agent.kind === "think") continue;
      const key = `chat:${area}:${speakerId}`;
      chatNow.add(key);
      const sp = this.chatMarkers[ci++];
      const pos = actor.nodes.root.position;
      const s = 0.5 * this._popScale(key, t);
      sp.scale.set(Math.max(0.001, s), Math.max(0.001, s), 1);
      sp.position.set(pos.x + 0.26,
        pos.y + 2.45 + Math.sin(t * 1.9 + actor.seed) * 0.05, pos.z);
      sp.visible = true;
    }
    for (; ci < this.chatMarkers.length; ci++) this.chatMarkers[ci].visible = false;
    for (const key of [...this.markerSince.keys()]) {
      if (key.startsWith("chat:") && !chatNow.has(key)) this.markerSince.delete(key);
    }
    this.seeded = true;            // 次に現れた社員からは入口から歩かせる
    if (this.streaming) this.cinematic(t);
    this.robots.faces.render(this.renderer, () => this.post.render(this.scene, this.camera));
  }

  /** 画面座標へ投影（ガラスのフローティングラベルを貼るため）。 */
  project(x, y, z) {
    const v = new THREE.Vector3(x, y, z).project(this.camera);
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    return { left: (v.x * 0.5 + 0.5) * w, top: (-v.y * 0.5 + 0.5) * h, z: v.z };
  }

  /** ボスロボのスクリーン座標（クリック判定用・胸のあたり）。 */
  projectBoss() {
    return this.project(this.stops.boss.x, 1.5, this.stops.boss.z);
  }

  /**
   * スクリーン座標に最も近いロボットの agent id（半径px内・ボスは対象外）。
   * InstancedMesh への実レイキャストではなく「胴の投影点への距離」で判定する:
   * ロボットは画面上で小さく、細い手足の隙間をレイが抜けると当たらない＝
   * 太い当たり判定の方がクリックUXが良い（projectBoss と同じ流儀・描画に一切触れない）。
   */
  pickAgent(px, py, radius = 46) {
    let best = null;
    let bestD = radius;
    for (const [id, actor] of this.actors) {
      const p = actor.nodes.root.position;
      const s = this.project(p.x, p.y + 1.1, p.z);   // 胴のあたり
      const d = Math.hypot(s.left - px, s.top - py);
      if (d < bestD) { bestD = d; best = id; }
    }
    return best;
  }

  /** 指定agentのロボット胴のスクリーン座標（テストのクリック照準用・pickAgentと同じ点）。 */
  projectAgent(id) {
    const actor = this.actors.get(id);
    if (!actor) return null;
    const p = actor.nodes.root.position;
    return this.project(p.x, p.y + 1.1, p.z);
  }

  /** R80.7: タップ挨拶。次のupdateで0.9秒の手振りアクションが走る（タップ起点のみ）。 */
  greet(agentId) {
    if (!agentId) return;
    (this._greet = this._greet || new Map()).set(agentId, null);
  }

  /** core/archetype.js の {kind,tint,acc} を THREE.Color 化してキャッシュ（毎フレーム確保しない）。 */
  _archFor(arch, cacheKey) {
    if (!arch) return null;
    this._archCache = this._archCache || new Map();
    const hit = this._archCache.get(cacheKey);
    if (hit && hit.kind === arch.kind) return hit;
    const KIND_PART = { video: "phones", audio: "phones", dev: "cap",
      design: "beret", writer: "pencil", ops: "bowtie", research: "mortar",
      support: "headset", infra: "hardhat", finance: "eyeshade" };
    const palette = { video: 0x5e5a55, audio: 0x5f7d59, dev: 0x5f7d59, design: 0x8a6a45,
      writer: 0xc9a86a, ops: 0x2e2d2c, research: 0x5e5a55, support: 0x8a6a45,
      infra: 0xc9a86a, finance: 0x5f7d59 };
    const built = {
      kind: arch.kind,
      part: KIND_PART[arch.kind] || null,
      tintC: new THREE.Color(...[...arch.tint].sort((a, b) => b - a)), // warm ordering preserves tint variation
      accC: arch.acc ? new THREE.Color(palette[arch.kind] || 0x5e5a55) : null,
    };
    this._archCache.set(cacheKey, built);
    return built;
  }

  /** Broadcast framing is separate from the user's zoom/pan and never writes them. */
  cinematic(t) {
    if (!this._frame || !this._lastWorld) return;
    this._director ||= createDirector();
    if (!this._cinematicPaused) {
      const shot = this._director(this._lastWorld.agents, t, { frozen, bossWalking: Boolean(this.bossTrip) });
      const base = this._frame;
      let goal = { ...base };
      const target = shot.kind === "boss" ? this.boss.root
        : shot.id ? this.actors.get(shot.id)?.nodes.root : null;
      if (target) {
        this.camera.updateMatrixWorld();
        const point = target.position.clone(); point.y += 0.9;
        point.applyMatrix4(this.camera.matrixWorldInverse);
        const hw = (base.right - base.left) * .46, hh = (base.top - base.bottom) * .46;
        goal = { left: point.x - hw, right: point.x + hw, top: point.y + hh, bottom: point.y - hh };
      }
      if (this._cinematicShot !== shot.sequence) {
        this._cinematicFrom = this._cinematicFrame || goal;
        this._cinematicShot = shot.sequence;
      }
      // Frozen renders always use the first shot, with no interpolation or wall-clock drift.
      const k = frozen ? 1 : smoothstep(0, .9, t - shot.started);
      this._cinematicFrame = Object.fromEntries(Object.keys(goal).map((key) =>
        [key, this._cinematicFrom[key] + (goal[key] - this._cinematicFrom[key]) * k]));
    }
    const f = this._cinematicFrame || this._frame, scale = this._userScale ?? 1;
    const cx = (f.left + f.right) / 2 + (this._userPanX || 0);
    const cy = (f.top + f.bottom) / 2 + (this._userPanY || 0);
    const hw = (f.right - f.left) / 2 * scale, hh = (f.top - f.bottom) / 2 * scale;
    Object.assign(this.camera, { left: cx - hw, right: cx + hw, top: cy + hh, bottom: cy - hh });
    this.camera.updateProjectionMatrix();
  }

  stopCinematic() {
    if (!this.streaming && !this._director) return;
    this._cinematicPaused = true;
    this._cinematicFrame ||= { ...this._frame };
  }

  /** R80.6: ピンチズーム。factor>1=寄る。pxX/pxY=ピボット（canvas px・省略時は中央）。
   *  指の下の点が動かないよう、スケール変化ぶんをパンへ繰り込む。 */
  viewZoomBy(factor, pxX, pxY) {
    if (!this._frame || !isFinite(factor) || factor <= 0) return;
    this.stopCinematic();
    const el = this.renderer.domElement;
    const W = el.clientWidth || 1;
    const H = el.clientHeight || 1;
    const s0 = this._userScale ?? 1;
    const s1 = Math.min(1.12, Math.max(0.34, s0 / factor));
    const curW = this.camera.right - this.camera.left;
    const curH = this.camera.top - this.camera.bottom;
    const px = ((pxX ?? W / 2) / W - 0.5) * curW;
    const py = (0.5 - (pxY ?? H / 2) / H) * curH;
    const r = s1 / s0;
    this._userPanX = (this._userPanX || 0) + px * (1 - r);
    this._userPanY = (this._userPanY || 0) + py * (1 - r);
    this._userScale = s1;
    this._clampUserPan();
  }

  /** R80.6: ドラッグでパン（canvas pxで受け、カメラ座標へ換算）。 */
  viewPanBy(dxPx, dyPx) {
    if (!this._frame) return;
    this.stopCinematic();
    const el = this.renderer.domElement;
    const W = el.clientWidth || 1;
    const H = el.clientHeight || 1;
    const curW = this.camera.right - this.camera.left;
    const curH = this.camera.top - this.camera.bottom;
    this._userPanX = (this._userPanX || 0) - dxPx / W * curW;
    this._userPanY = (this._userPanY || 0) + dyPx / H * curH;
    this._clampUserPan();
  }

  viewReset() {
    this.stopCinematic();
    if (this._cinematicPaused) this._cinematicFrame = { ...this._frame };
    this._userScale = 1;
    this._userPanX = 0;
    this._userPanY = 0;
  }

  viewState() {
    return { scale: this._userScale ?? 1,
      panX: this._userPanX || 0, panY: this._userPanY || 0 };
  }

  /** オフィスの外へ飛んで迷子にならない範囲（ズームで生じた余白＋12%まで）。 */
  _clampUserPan() {
    const f = this._frame;
    if (!f) return;
    const hw = (f.right - f.left) / 2;
    const hh = (f.top - f.bottom) / 2;
    const s = this._userScale ?? 1;
    const mx = hw * (1 - s) + hw * 0.12;
    const my = hh * (1 - s) + hh * 0.12;
    this._userPanX = Math.min(mx, Math.max(-mx, this._userPanX || 0));
    this._userPanY = Math.min(my, Math.max(-my, this._userPanY || 0));
  }

  /** R70: フォーカスズーム＝シートで選んだロボへ0.5sで寄る（update④が補間を描く）。 */
  focusOn(agentId) {
    if (!agentId || !this.actors.has(agentId)) return;
    this.stopCinematic();
    this._focusId = agentId;
    this._focusAnim = { t0: null, k0: this._focusK ?? 0, k1: 1 };
  }

  focusOff() {
    if ((this._focusK ?? 0) === 0 && !this._focusAnim) return;
    this._focusAnim = { t0: null, k0: this._focusK ?? 0, k1: 0 };
    // _focusId は k が 0 に落ちるまで保持（寄り先の中心が要る）
  }

  labelAnchorFor(agent, world, index) {
    // 名札は足元の下（頭上の大きな札はオフィスを隠す＝ユーザーFBで変更）
    // R59: 休憩の分散割当がある社員は、ロボ本体と同じ休憩スポットを指す
    // （assignRestSpots は純関数＝update() と同じ入力から同じ席が出る）
    const ri = assignRestSpots(world.agents, this.model.restSpots).get(agent.id);
    const a = ri !== undefined ? this.model.restSpots[ri] : this.anchorFor(agent, world, index);
    return this.project(a.x, Math.max(0, (a.y || 0) - 0.02), a.z);
  }

  stats() {
    const info = this.renderer.info;
    const materials = new Set(Object.values(this.materials));
    this.scene.traverse((o) => {
      for (const mat of Array.isArray(o.material) ? o.material : [o.material]) if (mat) materials.add(mat);
    });
    return {
      tier: this.spec.id,
      deskSeats: this.anchors.desk.length,
      maxSeen: this.maxSeen ?? 0,
      drawCalls: info.render.calls,
      materials: materials.size,
      quality: this.post.quality,
      triangles: info.render.triangles,
      geometries: info.memory.geometries,
      textures: info.memory.textures,
      robots: this.actors.size,
      chibis: this.chibiPool.length,
    };
  }

  dispose() {
    this.disposed = true;
    this.robots.dispose();
    this._disposeStatic();
    const textures = new Set();
    for (const m of new Set(Object.values(this.materials))) {
      for (const key of ["map", "normalMap", "emissiveMap", "lightMap"]) if (m[key]) textures.add(m[key]);
      m.dispose();
    }
    for (const texture of textures) texture.dispose();
    this.displays.dispose();
    this.environments.dispose();
    this.post.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
    // WebGL コンテキストは明示的に手放す（スタイル切替でリークさせない）
    this.renderer.forceContextLoss?.();
  }
}
