// 3Dアイソメ・レンダラ本体。world（core が作る純データ）を受け取って絵にする。
// 契約は mount/update/dispose の3つだけ。
import {
  chatBlend, chatPose, chatSpeaker, chibiPose, mixPose, pathTravel, poseFor,
  relaxPose, seedOf, smoothstep, thinkingPose, walkPhaseFor, walkPose,
} from "/ui/core/anim.js";
import {
  BOSS_WALK, CLEANER_ROUTE, IDLE_SPOTS, REST_SPOTS, routePath, walkGraph,
} from "/ui/core/nav.js";
import { frozen, rand, resetRand } from "/ui/platform/clock.js";
import * as THREE from "/ui/vendor/three/three.module.min.js";
import {
  LAYOUT, buildMonitors, buildOffice, chibiSeats, externalAnchors, loungeAnchors,
  BOSS_SEAT, COFFEE_STOP, ENTRANCE, HOLO_PANELS, envTexture, floorTexture,
  keyboardTexture, meetingAnchors, queueAnchors, rugTexture,
  screenTexture, seatAnchors, skyTexture, slab, woodTexture,
} from "./office.js";
import {
  RobotBatch, applyPose, makeChibiSkeleton, makeCleanerBot, makeSkeleton,
} from "./robot.js";
import { assignRestSpots, stableIndex } from "/ui/core/world.js";

const CAPACITY = 40;                 // 同時に描けるロボット数の上限（本人32+ボス+会議チビ最大8・R56）
const CHIBI_MAX = 8;                 // 会議チビロボの総数上限（1卓4体×2卓）
const CHIBI_WHITE = new THREE.Color(0xffffff);   // チビのアクセント淡色化（lerp先）
// 胸リングの状態色。HUD側のドットと同じ意味（作業=シアン/待機=琥珀/❗=赤/休憩=灰/外部=青）
const ACCENTS = {
  attention: new THREE.Color(0xff5a7e),
  working: new THREE.Color(0x53e0c4),
  waiting: new THREE.Color(0xffb340),
  resting: new THREE.Color(0xaab2d8),
  external: new THREE.Color(0x5aa2ff),
};
function accentFor(agent) {
  if (agent.attention) return ACCENTS.attention;
  if (agent.zone === "external") return ACCENTS.external;
  if (agent.state === "working") return ACCENTS.working;
  if (agent.state === "waiting") return ACCENTS.waiting;
  return ACCENTS.resting;
}
const std = (o) => new THREE.MeshStandardMaterial(o);
const emis = (c, i) => std({ color: c, emissive: c, emissiveIntensity: i, roughness: 0.35 });

function makeMaterials() {
  return {
    // 床・土台
    base: std({ color: 0xcfd4ea, roughness: 0.7, metalness: 0.05 }),
    // 床はわずかに沈めた白（真っ白は「明るい一様面」に数えられ、実際のっぺり見える）
    floor: std({ map: floorTexture(), color: 0xe7ecfb, roughness: 0.13, metalness: 0.40 }),
    // 台座は床と明確に差を付ける（差が小さいと段差ごと消えて見える・実際に消えた）
    floor2: std({ map: floorTexture(), color: 0xd3dcfa, roughness: 0.16, metalness: 0.34 }),
    woodFloor: std({ color: 0xdcc19a, roughness: 0.6 }),
    darkFloor: std({ color: 0x2e3352, roughness: 0.38, metalness: 0.3 }),
    // 面
    // ロボの殻はクリアコート＝参考画像の「つやのある白いトイ」の質感
    white: new THREE.MeshPhysicalMaterial({ color: 0xeef2fd, roughness: 0.24,
      metalness: 0.10, clearcoat: 0.6, clearcoatRoughness: 0.22 }),
    shell: new THREE.MeshPhysicalMaterial({ color: 0xf4f5fd, roughness: 0.20,
      metalness: 0.06, clearcoat: 0.6, clearcoatRoughness: 0.22 }),
    dark: std({ color: 0x171623, roughness: 0.20, metalness: 0.62 }),
    darker: std({ color: 0x0c0b14, roughness: 0.20, metalness: 0.62 }),
    steel: std({ color: 0x9a9ab4, roughness: 0.18, metalness: 0.78 }),
    wood: std({ map: woodTexture(), color: 0xe3c9a3, roughness: 0.55 }),
    wood2: std({ map: woodTexture(), color: 0xd2b184, roughness: 0.58 }),
    // 椅子・ソファ（大きい面＝色数はここで稼ぐ）
    seat: std({ color: 0x1b2150, roughness: 0.50, metalness: 0.16 }),
    seatB: std({ color: 0x2f5f7a, roughness: 0.52, metalness: 0.12 }),
    seatC: std({ color: 0x4a2f6b, roughness: 0.52, metalness: 0.12 }),
    sofa: std({ color: 0x4f55b4, roughness: 0.78 }),
    sofaB: std({ color: 0x7a6fc8, roughness: 0.78 }),
    sofaC: std({ color: 0x5f8fb8, roughness: 0.78 }),
    cushionA: std({ map: rugTexture(), color: 0x8f94dd, roughness: 0.86 }),
    cushionB: std({ color: 0xffd98a, roughness: 0.86 }),
    cushionC: std({ map: rugTexture(), color: 0xe58aa8, roughness: 0.86 }),
    // ラウンジのデザインラグ（GPT-Imageデカール差し替え口。未ロード時は織り目のまま）
    rugArt: std({ map: rugTexture(), color: 0xb9b4e4, roughness: 0.9 }),
    // エリアラグ（広い床の「明るい一様面」を大きな面で分割する＝品質ゲート対策の本体）
    rug: std({ map: rugTexture(), color: 0xb2bbe8, roughness: 0.92 }),
    rugB: std({ map: rugTexture(), color: 0x9fa9d8, roughness: 0.92 }),
    panelA: std({ color: 0x3d4a80, roughness: 0.7 }),
    panelB: std({ color: 0x5a6bb5, roughness: 0.7 }),
    // 小物
    paper: std({ color: 0xf7f4ea, roughness: 0.9 }),
    mugA: std({ color: 0xe0538a, roughness: 0.5 }),
    mugB: std({ color: 0x4fc9ff, roughness: 0.5 }),
    mugC: std({ color: 0xf5a524, roughness: 0.5 }),
    bookA: std({ color: 0xc0455f, roughness: 0.8 }),
    bookB: std({ color: 0x2f7fbf, roughness: 0.8 }),
    bookC: std({ color: 0xe0a53c, roughness: 0.8 }),
    bookD: std({ color: 0x4b9a72, roughness: 0.8 }),
    bookE: std({ color: 0x8a5cff, roughness: 0.8 }),
    // 植物
    leaf: std({ color: 0x2f7a50, roughness: 0.84 }),
    leaf2: std({ color: 0x63b482, roughness: 0.84 }),
    pot: std({ color: 0xe8eaf6, roughness: 0.48 }),
    // テクスチャ面
    kbd: std({ map: keyboardTexture(), roughness: 0.55, metalness: 0.05 }),
    sky: std({ map: skyTexture(), emissive: 0xffffff, emissiveMap: skyTexture(),
      emissiveIntensity: 1.25, roughness: 0.14, metalness: 0.25 }),
    // 発光
    stage: std({ color: 0xbcd4ff, roughness: 0.25, metalness: 0.25,
      emissive: 0x5a90ff, emissiveIntensity: 0.55 }),
    holo: new THREE.MeshStandardMaterial({
      color: 0xa8c6ff, emissive: 0x7fb0ff, emissiveIntensity: 1.9,
      transparent: true, opacity: 0.66, roughness: 0.2, side: THREE.DoubleSide }),
    screenGlow: std({ color: 0x2a4a9a, emissive: 0x4b7cf0, emissiveIntensity: 2.0,
      roughness: 0.25 }),
    // 胸リング/アンテナ先端: インスタンスカラー（状態色）をそのまま光らせる
    accent: new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false }),
    neon: emis(0x8a5cff, 6.0),
    neonC: emis(0x4fc9ff, 5.2),
    // ロボットの顔
    visor: std({ color: 0x101020, emissive: 0x353564, emissiveIntensity: 0.5,
      roughness: 0.12, metalness: 0.42 }),
    eye: std({ color: 0x9fe8ff, emissive: 0x66d8ff, emissiveIntensity: 3.4,
      roughness: 0.2, toneMapped: false }),
    // ガラス・影
    glass: new THREE.MeshPhysicalMaterial({
      color: 0xa9c8ff, transmission: 0.55, thickness: 0.6, roughness: 0.05,
      ior: 1.45, metalness: 0.12, transparent: true, opacity: 0.34,
      clearcoat: 1, clearcoatRoughness: 0.03,
      emissive: 0x4a78d8, emissiveIntensity: 0.32 }),
    // 接地影は「柔らかいラジアル」1択（硬い角丸板の影は模型っぽさの主因・参考画像は全部ソフト）
    shadow: new THREE.MeshBasicMaterial({
      map: softShadowTexture(), color: 0x2c2647, transparent: true,
      depthWrite: false, opacity: 0.62 }),
    islandShadow: new THREE.MeshBasicMaterial({
      map: softShadowTexture(), color: 0x4a4386, transparent: true,
      depthWrite: false, opacity: 0.30 }),
    // 発光まわりのフェイクブルーム（加算合成のラジアルグロー・ポストプロセス無しで光らせる）
    // 床の擬似映り込み（縦グラデのアルファ・加算）
    reflP: reflMaterial(0x9a7cff),
    reflC: reflMaterial(0xcfdcff),
    glowP: glowMaterial(0x8a5cff),
    glowC: glowMaterial(0x4fc9ff),
    glowW: glowMaterial(0xffb85c),
    crown: std({ color: 0xf5c542, metalness: 0.85, roughness: 0.22,
      emissive: 0x8a6a10, emissiveIntensity: 0.35 }),
    // デスクランプの発光部（暖色。寒色空間への対比＝居心地の色）
    lampWarm: std({ color: 0xffe3b0, emissive: 0xffc87a, emissiveIntensity: 2.8,
      roughness: 0.4, toneMapped: false }),
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
let _reflTex = null;
function reflMaterial(color) {
  if (!_reflTex) {
    const c = document.createElement("canvas");
    c.width = 64; c.height = 256;
    const g = c.getContext("2d");
    const grd = g.createLinearGradient(0, 0, 0, 256);
    grd.addColorStop(0, "rgba(255,255,255,.50)");     // v=1側（壁際）が明るい
    grd.addColorStop(0.55, "rgba(255,255,255,.10)");
    grd.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = grd; g.fillRect(0, 0, 64, 256);
    _reflTex = new THREE.CanvasTexture(c);
    _reflTex.colorSpace = THREE.SRGBColorSpace;
  }
  return new THREE.MeshBasicMaterial({
    map: _reflTex, color, transparent: true, blending: THREE.AdditiveBlending,
    depthWrite: false, toneMapped: false });
}

let _glowTex = null;
function glowMaterial(color) {
  if (!_glowTex) _glowTex = radialTexture(0.85, 0.28);
  return new THREE.MeshBasicMaterial({
    map: _glowTex, color, transparent: true, blending: THREE.AdditiveBlending,
    depthWrite: false, toneMapped: false, side: THREE.DoubleSide });
}

export class IsoScene {
  constructor(container) {
    this.container = container;
    this.actors = new Map();          // id → {nodes, from, to, startedAt, seed}
    this.disposed = false;
    this.seeded = false;              // 初回描画を済ませたか（出勤演出の出し分け）

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    // 解像度: 1に固定するとRetinaで常にぼやける。ヘッドレスの dsf は 1 なので
    // golden は安定したまま、実機だけ2倍の密度で描ける。
    this.renderer.setPixelRatio(Math.min(
      typeof window === "undefined" ? 1 : (window.devicePixelRatio || 1), 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.94;   // 参考画像の平均輝度0.70に寄せる
    container.append(this.renderer.domElement);

    this.scene = new THREE.Scene();
    // 奥ほど色を抜く。参考画像は同じ植物でも奥ほど彩度が落ちて明度が上がる。
    // 色は chroma>=26 側に置く（一様な明面として数えられないため）。
    this.scene.fog = new THREE.Fog(0xdfe8ff, 68, 140);
    // 映り込み。参考画像の床にはラックの紫が、ガラスには窓の白筋が映っている。
    // 追加ドロー0で metalness>0 の全面に色の変化が乗る＝色数にも効く。
    this.envMap = envTexture();
    this.scene.environment = this.envMap;
    this.scene.environmentIntensity = 0.34;
    this.materials = makeMaterials();
    // GPT-Image生成デカール（ui/iso/tex/*.webp・コミット済みアセット）。
    // 非同期ロードなので「全部確定するまで probe.ready を抑え、確定のたび再描画」を守る
    // （守らないと golden が差し替え前後どちらを撮るか不定になりフレークする）。
    // 404 は手続きテクスチャのまま確定＝ファイルが無くても壊れない。
    this.assetsPending = 0;
    const swapTex = (url, apply) => {
      this.assetsPending += 1;
      new THREE.TextureLoader().load(url,
        (t) => {
          t.colorSpace = THREE.SRGBColorSpace;
          apply(t);
          this.assetsPending -= 1;
          this._rerender();
        },
        undefined,
        () => { this.assetsPending -= 1; this._rerender(); });
    };
    swapTex("/ui/iso/tex/city.webp", (t) => {
      const sky = this.materials.sky;
      sky.map = t; sky.emissiveMap = t; sky.emissiveIntensity = 1.05; sky.needsUpdate = true;
    });
    swapTex("/ui/iso/tex/rugart.webp", (t) => {
      const m = this.materials.rugArt;
      m.map = t; m.color.set(0xffffff); m.needsUpdate = true;
    });
    this._swapTex = swapTex;              // モニタ構築後のデカール差し替えで使う

    // カメラ。構図は方位角(az)と仰角(el)の2つだけで決まる。
    //   方位角 45° = 真横からの等角（初期案）
    //          0°  = 入口の正面から見る（初期案から反時計回りに45°）
    //   仰角   高いほど俯瞰（床が面として見える）・低いほど水平（奥行きが潰れる）
    // ?az=<度>&el=<度> で試せる（構図の詰めはブラウザで回して決める）。
    const q = new URLSearchParams(typeof location === "undefined" ? "" : location.search);
    const num = (k, d) => {
      const v = Number.parseFloat(q.get(k));
      return Number.isFinite(v) ? v : d;
    };
    const AZIMUTH = num("az", 45) * Math.PI / 180;
    const ELEVATION = num("el", 40) * Math.PI / 180;   // 上から見下ろす（?el= で調整可）
    // 下端の余分（HUDカードぶんシーンを上へ逃がす量・?pad= で調整可）。
    // 0.17 は「右下ラウンジが見切れない」構図比較でユーザーが選んだ値（2026-07-30）。
    this.botPad = num("pad", 0.17);
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
      new THREE.Vector3(-14.65, 0, -10.35),
      new THREE.Vector3(14.55, 2.8, 9.1),
    );

    this.scene.add(new THREE.HemisphereLight(0xe8f2ff, 0x8a97cc, 0.46));
    const key = new THREE.DirectionalLight(0xffffff, 2.15);
    key.position.set(-14, 20, -18);   // 窓＝奥左から差す（カメラと同象限だと影が見えない）
    key.castShadow = true;
    key.shadow.mapSize.set(3072, 3072);
    Object.assign(key.shadow.camera,
      { left: -33, right: 33, top: 33, bottom: -33, near: 1, far: 105 });
    key.shadow.bias = -0.0005;
    key.shadow.normalBias = 0.018;
    key.shadow.radius = 2.4;   // 参考画像の影は輪郭が溶けるほど柔らかい
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xcfe0ff, 0.45);
    fill.position.set(14, 10, 15);
    this.scene.add(fill);
    const rim = new THREE.DirectionalLight(0x7c5cff, 1.10);
    rim.position.set(-8, 5, -13);
    this.scene.add(rim);

    resetRand();                       // 配置の乱数は毎回同じ（決定論）
    // 画面は自発光。MeshStandard だとライティング次第で沈むので Basic（常に一定）。
    // R68: wrapT=Repeat で UV スクロール（update が offset.y を t の関数で流す）
    this.screenMats = ["code", "chart", "dash", "term"].map((k, i) => {
      const map = screenTexture(k, 3 + i * 7);
      map.wrapS = THREE.RepeatWrapping;
      map.wrapT = THREE.RepeatWrapping;
      return new THREE.MeshBasicMaterial({ map, toneMapped: false, side: THREE.DoubleSide });
    });

    // 画面マテリアルを materials へ入れてバッチに乗せる（12枚が4ドローで済む）
    this.screenMats.forEach((m, i) => { this.materials[`screen${i}`] = m; });
    this.staticMeshes = buildOffice(this.materials, rand);
    // シーン調査用の窓口（隠れた退行はレイキャストで特定できる。埋没バグの発見実績あり）
    if (typeof window !== "undefined") window.__debugScene = this;
    for (const m of this.staticMeshes) this.scene.add(m);
    this.monitors = buildMonitors(this.screenMats, this.materials);
    this.scene.add(this.monitors);
    const swappable = this.monitors.userData.swappable || {};
    if (swappable.signMat) {
      this._swapTex("/ui/iso/tex/sign.webp", (t) => {
        swappable.signMat.map = t; swappable.signMat.needsUpdate = true;
      });
    }
    if (swappable.boardMat) {
      this._swapTex("/ui/iso/tex/board.webp", (t) => {
        swappable.boardMat.map = t; swappable.boardMat.needsUpdate = true;
      });
    }
    this._swapTex("/ui/iso/tex/screen_code.webp", (t) => {
      t.wrapS = THREE.RepeatWrapping; t.wrapT = THREE.RepeatWrapping;   // スクロール継続
      this.screenMats[0].map = t; this.screenMats[0].needsUpdate = true;
    });
    this._swapTex("/ui/iso/tex/screen_dash.webp", (t) => {
      t.wrapS = THREE.RepeatWrapping; t.wrapT = THREE.RepeatWrapping;
      this.screenMats[2].map = t; this.screenMats[2].needsUpdate = true;
    });

    this.robots = new RobotBatch(this.scene, this.materials, CAPACITY);
    // ボスロボ（データ非連動の常駐デコ・王冠つき・クリックで「ボス指令」）
    this.boss = makeSkeleton();
    this.boss.root.scale.setScalar(1.85);
    this.bossAccent = new THREE.Color(0xf5c542);

    // ❗マーカー（承認/質問まちの頭上・ユーザーFB）。スプライト=常にカメラを向く
    const mkAttnTex = () => {
      const c = document.createElement("canvas");
      c.width = 128; c.height = 128;
      const g = c.getContext("2d");
      g.beginPath(); g.arc(64, 64, 56, 0, 7);
      g.fillStyle = "#e0538a"; g.fill();
      g.lineWidth = 8; g.strokeStyle = "#ffffff"; g.stroke();
      g.fillStyle = "#ffffff";
      g.font = "bold 84px -apple-system, sans-serif";
      g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText("!", 64, 70);
      const t = new THREE.CanvasTexture(c);
      t.colorSpace = THREE.SRGBColorSpace;
      return t;
    };
    this.attnMarkers = [];
    const attnMat = new THREE.SpriteMaterial({
      map: mkAttnTex(), transparent: true, depthTest: false, toneMapped: false });
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
    const mkThinkTex = () => {
      const c = document.createElement("canvas");
      c.width = 128; c.height = 128;
      const g = c.getContext("2d");
      const cloud = (fill, stroke) => {
        g.beginPath();
        g.ellipse(66, 52, 46, 32, 0, 0, 7);
        g.fillStyle = fill; g.fill();
        if (stroke) { g.lineWidth = 6; g.strokeStyle = stroke; g.stroke(); }
      };
      cloud("#ffffff", "#8a5cff");
      for (const [x, r] of [[30, 9], [18, 5]]) {           // 尻尾の小円
        g.beginPath(); g.arc(x, 96 + (9 - r), r, 0, 7);
        g.fillStyle = "#ffffff"; g.fill();
        g.lineWidth = 4; g.strokeStyle = "#8a5cff"; g.stroke();
      }
      g.fillStyle = "#6c5cd8";
      for (const dx of [-18, 0, 18]) {                     // 思考中の「…」
        g.beginPath(); g.arc(66 + dx, 54, 6.5, 0, 7); g.fill();
      }
      const t = new THREE.CanvasTexture(c);
      t.colorSpace = THREE.SRGBColorSpace;
      return t;
    };
    this.thinkMarkers = [];
    const thinkMat = new THREE.SpriteMaterial({
      map: mkThinkTex(), transparent: true, depthTest: false, toneMapped: false });
    for (let i = 0; i < 6; i++) {
      const sp = new THREE.Sprite(thinkMat);
      sp.scale.set(0.58, 0.58, 1);
      sp.visible = false;
      sp.renderOrder = 5;
      this.scene.add(sp);
      this.thinkMarkers.push(sp);
    }
    // 💬 おしゃべりドット（R59・休憩の会話の話し手の頭上）。優先順は ❗ > 💭 > 💬
    const mkChatTex = () => {
      const c = document.createElement("canvas");
      c.width = 128; c.height = 128;
      const g = c.getContext("2d");
      g.beginPath();
      g.ellipse(64, 52, 44, 30, 0, 0, 7);
      g.fillStyle = "#ffffff"; g.fill();
      g.lineWidth = 6; g.strokeStyle = "#22a06b"; g.stroke();
      g.beginPath();                                       // 吹き出しの尻尾
      g.moveTo(42, 78); g.lineTo(30, 100); g.lineTo(58, 82); g.closePath();
      g.fillStyle = "#ffffff"; g.fill();
      g.lineWidth = 4; g.strokeStyle = "#22a06b"; g.stroke();
      g.fillStyle = "#1c8a5c";
      for (const dx of [-16, 0, 16]) {                     // 話している「…」
        g.beginPath(); g.arc(64 + dx, 54, 6, 0, 7); g.fill();
      }
      const t = new THREE.CanvasTexture(c);
      t.colorSpace = THREE.SRGBColorSpace;
      return t;
    };
    this.chatMarkers = [];
    const chatMat = new THREE.SpriteMaterial({
      map: mkChatTex(), transparent: true, depthTest: false, toneMapped: false });
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
    this._chibiSeats = chibiSeats();
    this._chibiTint = new THREE.Color();
    // R58: 通路グラフ（机すり抜け根絶）。歩行は必ずこのレーンを経由する
    this.navGraph = walkGraph();

    // ── R68: 生命感の描画状態（すべて「初回=-∞」＝frozen では遷移完了状態で描く） ──
    // ✓マーカー（❗解消の瞬間・0.6秒だけ頭上に出す）
    const mkDoneTex = () => {
      const c = document.createElement("canvas");
      c.width = 128; c.height = 128;
      const g = c.getContext("2d");
      g.beginPath(); g.arc(64, 64, 54, 0, 7);
      g.fillStyle = "#22a06b"; g.fill();
      g.lineWidth = 8; g.strokeStyle = "#ffffff"; g.stroke();
      g.lineWidth = 13; g.lineCap = "round";
      g.beginPath(); g.moveTo(38, 66); g.lineTo(56, 86); g.lineTo(92, 44); g.stroke();
      const t = new THREE.CanvasTexture(c);
      t.colorSpace = THREE.SRGBColorSpace;
      return t;
    };
    this.doneMarkers = [];
    const doneMat = new THREE.SpriteMaterial({
      map: mkDoneTex(), transparent: true, depthTest: false, toneMapped: false });
    for (let i = 0; i < 4; i++) {
      const sp = new THREE.Sprite(doneMat);
      sp.visible = false;
      sp.renderOrder = 5;
      this.scene.add(sp);
      this.doneMarkers.push(sp);
    }
    this.markerSince = new Map();      // "attn:<id>"等 → 初認識t（ポップイン用）
    this.attnResolved = new Map();     // id → {at, x, y, z}（✓演出）
    this.chibiSeen = new Map();        // "table:idx" → 初認識t（登場バウンス）
    this.chibiGone = new Map();        // "table:idx" → {at, seat, seed, tint}（解散シュリンク）

    // ホログラム（静的バッチから独立＝回転＋脈動できる。HOLO_PANELS が配置の正本）
    this.holoPanels = HOLO_PANELS.map(([hx, hy, hz, hw, ry]) => {
      const mesh = new THREE.Mesh(slab(hw, 0.035, hw * 0.62, 0.07), this.materials.holo);
      mesh.position.set(hx, hy, hz);
      mesh.rotation.y = ry;
      mesh.userData.baseYaw = ry;
      this.scene.add(mesh);
      return mesh;
    });

    // 🧹 掃除ロボ（t>=30 で通路を永久巡回・golden の t=3.2 では非表示）
    this.cleaner = makeCleanerBot(this.materials);
    this.cleaner.visible = false;
    this.scene.add(this.cleaner);
    this._cleanerTotal = 0;
    for (let i = 1; i < CLEANER_ROUTE.length; i++) {
      this._cleanerTotal += Math.hypot(
        CLEANER_ROUTE[i][0] - CLEANER_ROUTE[i - 1][0],
        CLEANER_ROUTE[i][1] - CLEANER_ROUTE[i - 1][1]);
    }

    this.anchors = {
      desk: seatAnchors(),
      meeting: meetingAnchors(),
      lounge: loungeAnchors(),
      queue: queueAnchors(),
      external: externalAnchors(),
    };
    this.resize();
  }

  /**
   * 画角の自動フィット。
   * 収めたい範囲の8隅をカメラ空間へ投影し、それが必ず入る最小の画角にする。
   * 手で VIEW を決めると窓の比率が変わるたびに床が余る／見切れるので、計算で出す。
   */
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
    const view = Math.max(hy, hx / aspect) * MARGIN;
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
    this.camera.left = this._frame.left;
    this.camera.right = this._frame.right;
    this.camera.top = this._frame.top;
    this.camera.bottom = this._frame.bottom;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
  }

  /**
   * ときどきコーヒーバーへ歩いて一服する（260秒周期に26秒・seedで位相分散・決定論）。
   * 起動30秒はやらない＝固定tのスクショ/goldenでは全員が持ち場に居る。
   */
  excursionFor(agent, t) {
    // ガードは15秒（R68: 開幕の一番見られる時間帯に誰も動かない空白を短縮。golden=t3.2は不変）
    if (agent.zone !== "desk" || agent.state !== "working" || t < 15) return null;
    const ph = (((t + seedOf(agent.id) * 40) % 260) + 260) % 260;
    if (ph >= 26) return null;
    const slot = stableIndex(agent.id, 3);
    return { x: COFFEE_STOP.x - 0.8 + slot * 0.75, z: COFFEE_STOP.z,
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
    const spot = IDLE_SPOTS[stableIndex(agent.id, IDLE_SPOTS.length)];
    return { x: spot.x, z: spot.z, yaw: spot.yaw, y: 0, role: "stand" };
  }

  /**
   * R68: 表示値の遷移トラック。target が変わったら「いまの表示値」を起点に
   * dur 秒の smoothstep で追従する（座↔立の沈み・段差・向きのスナップ解消）。
   * 初回は at=-Infinity＝遷移完了状態で始まる → frozen(?t=固定) の golden 不変。
   */
  _track(actor, key, target, t, dur = 0.45, angular = false) {
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
      since = this.seeded ? t : -Infinity;
      this.markerSince.set(key, since);
    }
    const dt = t - since;
    return smoothstep(0, 0.35, dt) * (1 + 0.18 * Math.sin(Math.min(dt / 0.6, 1) * Math.PI));
  }

  /** ゾーンと席番号から目的地を決める。席が尽きたら床に立たせる（決定論）。 */
  anchorFor(agent, world, index) {
    const list = this.anchors[agent.zone] || this.anchors.desk;
    if (agent.zone === "desk") {
      const seat = world.seats.get(agent.id);
      if (seat !== undefined && list[seat]) return list[seat];
    }
    const a = list[index % list.length];
    if (a) return a;
    return { x: 0, z: LAYOUT.floor.z, yaw: 0, y: 0 };
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
    // ①スクリーン: UVスクロールでコード/ログが流れる（webpデカール差し替え後もループ）
    this.screenMats.forEach((m, i) => {
      if (m.map) m.map.offset.y = -(((t * 0.045 + i * 0.17) % 1 + 1) % 1);
    });
    // ②ネオン/ランプ/画面グローの呼吸（2系統の位相差で交互に息づく）
    const M = this.materials;
    M.neon.emissiveIntensity = 6.0 + Math.sin(t * 0.9) * 0.35;
    M.neonC.emissiveIntensity = 5.2 + Math.sin(t * 0.9 + Math.PI / 2) * 0.30;
    M.lampWarm.emissiveIntensity = 2.8 + Math.sin(t * 1.3) * 0.12;
    M.screenGlow.emissiveIntensity = 2.0 + Math.sin(t * 0.7 + 1.1) * 0.15;
    M.stage.emissiveIntensity = 0.55 + Math.sin(t * 0.5 + 2.3) * 0.06;
    // ③ホログラム: ゆっくり自転＋脈動（静的バッチ外の個別メッシュ・+3ドロー）
    M.holo.emissiveIntensity = 1.9 + Math.sin(t * 1.1) * 0.25;
    this.holoPanels.forEach((mesh, i) => {
      mesh.rotation.y = mesh.userData.baseYaw + t * 0.15 + i * 0.9;
    });
    // ④カメラの呼吸ドリフト（非frozen限定＝goldenは完全不変。振幅は視野の約1%）
    if (this._frame) {
      const dx = frozen ? 0 : Math.sin(t * 0.045) * this.view * 0.012;
      const dy = frozen ? 0 : Math.sin(t * 0.031 + 1.7) * this.view * 0.007;
      this.camera.left = this._frame.left + dx;
      this.camera.right = this._frame.right + dx;
      this.camera.top = this._frame.top + dy;
      this.camera.bottom = this._frame.bottom + dy;
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
      const cm = pathTravel(CLEANER_ROUTE, 0, ct, speed);
      this.cleaner.position.set(cm.x, 0, cm.z);
      this.cleaner.rotation.y = cm.yaw + Math.sin(t * 2.6) * 0.10;
    }

    const seen = new Set();
    const perZone = {};

    // R59: 休憩スポットの分散割当（決定論）＋エリア別の会話グループ
    const restAssign = assignRestSpots(world.agents, REST_SPOTS);
    const restGroups = {};
    for (const [id, si] of restAssign) {
      const area = REST_SPOTS[si].area;
      (restGroups[area] = restGroups[area] || []).push({ id, si });
    }
    for (const g of Object.values(restGroups)) g.sort((a, b) => a.si - b.si);

    for (const agent of world.agents) {
      seen.add(agent.id);
      const idx = (perZone[agent.zone] = (perZone[agent.zone] ?? -1) + 1);
      const restIdx = restAssign.get(agent.id);
      const target = this.excursionFor(agent, t) || this.idleLifeFor(agent, t)
        || (restIdx !== undefined ? REST_SPOTS[restIdx]
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
          path: this.seeded
            ? routePath([ENTRANCE.x, ENTRANCE.z], [target.x, target.z], this.navGraph)
            : [[target.x, target.z]],
          dest: [target.x, target.z],
          y: target.y, yaw: target.yaw,
          targetY: target.y, targetYaw: target.yaw,
          startedAt: t, seed: seedOf(agent.id),
        };
        actor.nodes.root.scale.setScalar(1.62);   // 主役は大きめ（部屋拡張で負けない）
        this.actors.set(agent.id, actor);
      } else if (actor.dest[0] !== target.x || actor.dest[1] !== target.z) {
        // 目的地が変わった＝ゾーン移動。いまの位置から通路経由で歩き直す
        const cur = pathTravel(actor.path, actor.startedAt, t);
        actor.path = routePath([cur.x, cur.z], [target.x, target.z], this.navGraph);
        actor.dest = [target.x, target.z];
        actor.startedAt = t;
        actor.targetY = target.y;
        actor.targetYaw = target.yaw;
      }

      const m = pathTravel(actor.path, actor.startedAt, t);
      const walking = m.u < 1 && m.total > 0.05;
      actor.walking = walking;                     // 💬マーカーの判定で使う（R59）
      // R56: 思考中（kind==="think"）は自席で考え込むポーズ（ゾーンの優先は変えない）
      const thinking = !walking && agent.zone === "desk"
        && agent.kind === "think" && !target.role;
      // R59: 休憩の社交。同エリアに2体以上=向かい合っておしゃべり・1体=くつろぎ変奏
      let pose = null;
      let poseKind = "";                           // R68: 遷移検出用のポーズ種別キー
      let chatYaw = null;
      if (!walking && restIdx !== undefined) {
        const spot = REST_SPOTS[restIdx];
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
            cx += REST_SPOTS[e.si].x;
            cz += REST_SPOTS[e.si].z;
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
      // R68: ポーズ種別が変わったら 0.45秒かけて前のポーズから補間（座↔立のスナップ根絶）。
      // 初回は poseChangedAt=-Infinity ＝補間完了状態 → frozen の golden 不変。
      if (actor.poseKind !== poseKind) {
        actor.poseFrom = actor.lastPose || null;
        actor.poseChangedAt = actor.poseKind === undefined ? -Infinity : t;
        actor.poseKind = poseKind;
      }
      const blended = mixPose(actor.poseFrom, pose,
        smoothstep(0, 0.45, t - (actor.poseChangedAt ?? -Infinity)));
      actor.lastPose = blended;
      applyPose(actor.nodes, blended);

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
    }

    // R68: 退勤＝即消滅ではなく、入口まで歩いて退場してから消える（出勤と対称）
    for (const [id, actor] of [...this.actors]) {
      if (seen.has(id)) { actor.leavingAt = null; continue; }
      if (!this.seeded) { this.actors.delete(id); continue; }   // 初回シードは従来どおり
      if (!actor.leavingAt) {
        const cur = pathTravel(actor.path, actor.startedAt, t);
        actor.leavingAt = t;
        actor.path = routePath([cur.x, cur.z], [ENTRANCE.x, ENTRANCE.z], this.navGraph);
        actor.dest = [ENTRANCE.x, ENTRANCE.z];
        actor.startedAt = t;
        actor.poseKind = "walk";       // 座り姿勢からの立ち上がりも mixPose で繋ぐ
        actor.poseFrom = actor.lastPose || null;
        actor.poseChangedAt = t;
      }
      const m = pathTravel(actor.path, actor.startedAt, t);
      if (m.u >= 1 || t - actor.leavingAt > 20) {   // 到着（保険=20秒）で退場完了
        this.actors.delete(id);
        continue;
      }
      const blended = mixPose(actor.poseFrom,
        walkPose(walkPhaseFor(m.dist, actor.seed)),
        smoothstep(0, 0.45, t - actor.poseChangedAt));
      actor.lastPose = blended;
      applyPose(actor.nodes, blended);
      const dispY = this._track(actor, "trY", 0, t);
      actor.nodes.root.position.set(m.x, dispY, m.z);
      actor.nodes.root.rotation.y = m.yaw;
    }

    this.robots.begin();
    let n = 0;
    for (const actor of this.actors.values()) {
      if (n++ >= CAPACITY) break;
      this.robots.push(actor.nodes, actor.accent || null);
    }
    // ボス: 普段は壇上で悠然と頷き、300秒周期で20秒だけ北通路を見回る（R68・t>=30）。
    // 巡回路は BOSS_WALK（既存レーン上＝交差0を nav.test がピン）。壇との段差は
    // 経路の始端/終端で滑らかに昇降する。
    let bossWalking = false;
    const bph = ((t % 300) + 300) % 300;
    if (t >= 30 && bph < 20) {
      const route = [[BOSS_SEAT.x, BOSS_SEAT.z], ...BOSS_WALK, [BOSS_SEAT.x, BOSS_SEAT.z]];
      const bm = pathTravel(route, 0, bph, 1.15);
      if (bm.u < 1) {
        bossWalking = true;
        applyPose(this.boss, walkPose(walkPhaseFor(bm.dist, 7.7)));
        const lift = BOSS_SEAT.baseY - 0.35;
        const y = lift * (1 - smoothstep(0.02, 0.10, bm.u))
          + lift * smoothstep(0.90, 0.98, bm.u);
        this.boss.root.position.set(bm.x, y, bm.z);
        this.boss.root.rotation.y = bm.yaw;
      }
    }
    if (!bossWalking) {
      applyPose(this.boss, poseFor("meeting", t * 0.55, 7.7));
      this.boss.root.position.set(BOSS_SEAT.x, BOSS_SEAT.baseY - 0.35, BOSS_SEAT.z);
      this.boss.root.rotation.y = 0;
    }
    this.robots.push(this.boss, this.bossAccent);

    // R56: 会議チビロボ＝minions を親と同じ卓の縁に立たせて頷かせる（上限4/卓・8/全体）。
    // InstancedMesh への行列追加だけ＝drawCalls は増えない。位相は親id+序数で分散。
    // R68: 登場は床から「ぴょこん」と生え（popScale）、解散は0.3秒のシュリンクで消える。
    const tableUsed = { meet: 0, meet2: 0 };
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
      const table = actor.dest[0] < 0 ? "meet" : "meet2";
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
        this._chibiMeta.set(key, { seat, seed, tint: this._chibiTint.clone() });
        this.robots.push(ch, this._chibiTint);
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
      this.robots.push(ch, gone.tint);
    }
    this.robots.end();

    // ❗マーカー: attention のアバター頭上でゆっくり浮く（R68: ポンと弾んで出る）
    let mi = 0;
    const attnNow = new Set();
    for (const agent of world.agents) {
      if (mi >= this.attnMarkers.length) break;
      if (!agent.attention) continue;
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
      const actor = this.actors.get(id);
      const inWorld = world.agents.some((a) => a.id === id);
      if (actor && inWorld && this.seeded) this.attnResolved.set(id, { at: t });
    }
    let di = 0;
    for (const [id, res] of [...this.attnResolved]) {
      const dt = t - res.at;
      const actor = this.actors.get(id);
      if (dt > 0.6 || !actor || di >= this.doneMarkers.length) {
        this.attnResolved.delete(id);
        continue;
      }
      const sp = this.doneMarkers[di++];
      const pos = actor.nodes.root.position;
      const s = 0.60 * smoothstep(0, 0.15, dt) * (1 - smoothstep(0.42, 0.6, dt));
      sp.scale.set(Math.max(0.001, s), Math.max(0.001, s), 1);
      sp.position.set(pos.x, pos.y + 2.55 + dt * 0.5, pos.z);   // ふわっと昇って消える
      sp.visible = true;
    }
    for (; di < this.doneMarkers.length; di++) this.doneMarkers[di].visible = false;

    // 💭マーカー: 思考中（kind==="think"）の頭上。❗が出ている間は❗を優先
    let ti = 0;
    const thinkNow = new Set();
    for (const agent of world.agents) {
      if (ti >= this.thinkMarkers.length) break;
      if (agent.attention || agent.kind !== "think") continue;
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
    this.renderer.render(this.scene, this.camera);
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
    return this.project(BOSS_SEAT.x, 1.5, BOSS_SEAT.z);
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

  labelAnchorFor(agent, world, index) {
    // 名札は足元の下（頭上の大きな札はオフィスを隠す＝ユーザーFBで変更）
    // R59: 休憩の分散割当がある社員は、ロボ本体と同じ休憩スポットを指す
    // （assignRestSpots は純関数＝update() と同じ入力から同じ席が出る）
    const ri = assignRestSpots(world.agents, REST_SPOTS).get(agent.id);
    const a = ri !== undefined ? REST_SPOTS[ri] : this.anchorFor(agent, world, index);
    return this.project(a.x, Math.max(0, (a.y || 0) - 0.02), a.z);
  }

  stats() {
    const info = this.renderer.info;
    return {
      drawCalls: info.render.calls,
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
    for (const m of this.staticMeshes) {
      m.geometry.dispose();
      this.scene.remove(m);
    }
    this.monitors.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
    for (const m of Object.values(this.materials)) m.dispose?.();
    for (const m of this.screenMats) {
      m.map?.dispose();
      m.emissiveMap?.dispose();
      m.dispose();
    }
    this.envMap?.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
    // WebGL コンテキストは明示的に手放す（スタイル切替でリークさせない）
    this.renderer.forceContextLoss?.();
  }
}
