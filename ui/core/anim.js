// ──────────────────────────────────────────────────────────────
// 動きの数学。DOM も three.js も知らないので node --test でそのまま検査できる。
//
// 掟: 時刻は必ず引数で受ける（Date.now を読まない）。
//     これが守られている限り「同じ t → 同じポーズ」になり、スクショ回帰が成立する。
// ──────────────────────────────────────────────────────────────

export const TAU = Math.PI * 2;

/** ロボットの体格（メートル）。歩幅や座面の高さの基準にもなる。 */
export const RIG = Object.freeze({
  hipY: 0.54,          // 腰の高さ（立位）
  sitHipY: 0.44,       // 着席時の腰の高さ
  scale: 1.42,
  stride: 0.62,        // 1歩の振り幅（ラジアン）
  speed: 1.25,         // m/s
  cadence: 3.6,        // 1メートル進むあたりの位相（rad）
});

/**
 * 歩行サイクルのポーズ。位相 ph（ラジアン）から各関節の角度を返す。
 * 腰の上下＋左右脚の逆位相＋膝の折れ＋腕の反対振り＝「ちゃんと歩いて見える」最小要素。
 */
export function walkPose(ph) {
  const s = Math.sin(ph);
  return {
    hipY: RIG.hipY + Math.abs(Math.sin(ph * 2)) * 0.042,
    hipYaw: s * 0.09,
    hipRoll: s * 0.04,
    headYaw: -s * 0.06,
    headPitch: 0.03,
    legs: [-1, 1].map((side) => {
      const p = ph + (side > 0 ? Math.PI : 0);
      return {
        side,
        hip: Math.sin(p) * RIG.stride,
        // 後ろへ送った脚だけ膝が折れる（前へ出す脚は伸びる）
        knee: Math.max(0, -Math.sin(p - 0.55)) * 0.95,
      };
    }),
    arms: [-1, 1].map((side) => {
      const p = ph + (side > 0 ? 0 : Math.PI);   // 腕は脚と逆
      return {
        side,
        shoulder: Math.sin(p) * 0.52,
        elbow: -Math.max(0, Math.sin(p)) * 0.40,
      };
    }),
  };
}

/** 自席でタイピング。指を刻む速さで「働いている」が伝わる。
 *  41秒周期で2.6秒だけ「伸び」をする（seedで個体の位相が散る＝全員同時に伸びない）。 */
export function typingPose(t, seed = 0) {
  const pose = {
    hipY: RIG.sitHipY,
    hipYaw: 0,
    hipRoll: 0,
    headYaw: Math.sin(t * 0.4 + seed) * 0.05,
    headPitch: 0.16 + Math.sin(t * 0.8 + seed) * 0.025,
    legs: [-1, 1].map((side) => ({ side, hip: -1.42, knee: 1.36 })),
    arms: [-1, 1].map((side, i) => ({
      side,
      // 「手を動かして働いている」が遠目にも見える振幅（ユーザーFBで増量）
      shoulder: -0.66 + Math.sin(t * 5.5 + i * 1.7 + seed) * 0.08,
      elbow: -0.70 + Math.sin(t * 11 + i * 2.4 + seed) * 0.30,
    })),
  };
  const cyc = (((t + seed * 13) % 41) + 41) % 41;
  if (cyc < 2.6) {
    // 入り0.5秒・戻り0.5秒をなめらかに（急に腕が跳ぶと機械的に見える）
    const k = smoothstep(0, 0.5, cyc) * smoothstep(2.6, 2.1, cyc);
    pose.headPitch = pose.headPitch + (-0.14 - pose.headPitch) * k;
    pose.hipY = pose.hipY + 0.035 * k;
    for (const a of pose.arms) {
      a.shoulder = a.shoulder + (-2.75 - a.shoulder) * k;
      a.elbow = a.elbow + (-0.12 - a.elbow) * k;
    }
  }
  return pose;
}

/** 🤔 自席で考え込む（R56・kind==="think"）。
 *  片手を顎へ・うつむき気味・ゆっくり左右に揺れる＝「思考中」が遠目にも伝わる。 */
export function thinkingPose(t, seed = 0) {
  const sway = Math.sin(t * 0.5 + seed);
  return {
    hipY: RIG.sitHipY,
    hipYaw: Math.sin(t * 0.3 + seed) * 0.05,
    hipRoll: 0,
    headYaw: 0.14 + sway * 0.10,                              // ゆっくり左右へ
    headPitch: 0.24 + Math.sin(t * 0.9 + seed) * 0.03,        // うつむき気味
    legs: [-1, 1].map((side) => ({ side, hip: -1.42, knee: 1.36 })),
    arms: [
      // 左手を顎へ（肘を深く曲げて手が顔の高さへ来る）・右手は机に置く
      { side: -1, shoulder: -0.86 + Math.sin(t * 0.7 + seed) * 0.03, elbow: -1.98 },
      { side: 1, shoulder: -0.30, elbow: -0.55 },
    ],
  };
}

/** 会議で座って聞く・頷く。 */
export function seatedPose(t, seed = 0) {
  return {
    hipY: RIG.sitHipY,
    hipYaw: 0,
    hipRoll: 0,
    headYaw: Math.sin(t * 0.38 + seed) * 0.22,
    headPitch: Math.sin(t * 0.7 + seed) * 0.06,
    legs: [-1, 1].map((side) => ({ side, hip: -1.42, knee: 1.36 })),
    arms: [-1, 1].map((side, i) => ({
      side,
      shoulder: -0.34 + Math.sin(t * 1.6 + i + seed) * 0.12,
      elbow: -0.62 + Math.sin(t * 1.9 + i * 1.3 + seed) * 0.18,
    })),
  };
}

/** 立って待つ（あなたの席の前の列）。そわそわを少しだけ。 */
export function idlePose(t, seed = 0) {
  const breathe = Math.sin(t * 0.9 + seed) * 0.012;
  return {
    hipY: RIG.hipY + breathe,
    hipYaw: Math.sin(t * 0.23 + seed) * 0.06,
    hipRoll: 0,
    headYaw: Math.sin(t * 0.31 + seed) * 0.12,
    headPitch: 0.02,
    legs: [-1, 1].map((side) => ({ side, hip: side * 0.06, knee: 0.04 })),
    arms: [-1, 1].map((side) => ({
      side,
      shoulder: 0.06 + Math.sin(t * 0.7 + seed) * 0.03,
      elbow: -0.22,
    })),
  };
}

/** 立って白板を指して発表する（参考画像の左中央のロボット）。 */
export function presentPose(t, seed = 0) {
  const sway = Math.sin(t * 0.8 + seed) * 0.09;
  return {
    hipY: RIG.hipY,
    hipYaw: 0.12 + Math.sin(t * 0.3 + seed) * 0.05,
    hipRoll: 0,
    headYaw: 0.20,
    headPitch: -0.06,
    legs: [-1, 1].map((side) => ({ side, hip: side * 0.10, knee: 0.05 })),
    // 片手を上げて指し、もう片手は下ろす
    arms: [
      { side: -1, shoulder: -1.32 + sway, elbow: -0.18 },
      { side: 1, shoulder: 0.14, elbow: -0.32 },
    ],
  };
}

/** 座ってタブレットを覗き込む（参考画像のラウンジのロボット）。 */
export function tabletPose(t, seed = 0) {
  return {
    hipY: RIG.sitHipY,
    hipYaw: 0,
    hipRoll: 0,
    headYaw: Math.sin(t * 0.5 + seed) * 0.06,
    headPitch: 0.30,
    legs: [-1, 1].map((side) => ({ side, hip: -1.30, knee: 1.10 })),
    arms: [-1, 1].map((side) => ({ side, shoulder: -0.80, elbow: -1.05 })),
  };
}

/**
 * 状態に応じたポーズを1つ選ぶ。ゾーンが決まればポーズも決まる（場所＝状態）。
 * role は同じゾーン内での役割違い（会議の発表者・ラウンジのタブレット）。
 */
export function poseFor(zone, t, seed = 0, walkPhase = null, role = null) {
  if (walkPhase !== null) return walkPose(walkPhase);
  if (role === "present") return presentPose(t, seed);
  if (role === "tablet") return tabletPose(t, seed);
  if (role === "stand") return idlePose(t * 1.1, seed + 2.3);   // コーヒーバー等で立つ
  switch (zone) {
    case "meeting": return seatedPose(t, seed);
    case "lounge": return tabletPose(t * 0.7, seed + 1.7);
    case "queue": return idlePose(t, seed);
    case "external": return idlePose(t * 1.2, seed + 0.9);   // コンソールの前に立つ
    default: return typingPose(t, seed);
  }
}

/**
 * 2点間の移動。所要時間は距離÷速度なので、遠い席ほど長く歩く（自然）。
 * 戻り値 u は 0..1 で、1 に達したら到着。
 */
export function travel(from, to, startedAt, t, speed = RIG.speed) {
  const dx = to[0] - from[0];
  const dz = to[1] - from[1];
  const dist = Math.hypot(dx, dz);
  if (dist < 1e-6) return { x: to[0], z: to[1], u: 1, yaw: 0, dist: 0 };
  const dur = dist / speed;
  const u = Math.min(1, Math.max(0, (t - startedAt) / dur));
  return {
    x: from[0] + dx * u,
    z: from[1] + dz * u,
    u,
    yaw: Math.atan2(dx, dz),
    dist,
  };
}

/** 歩行位相は「進んだ距離」から決める。速度が変わっても足が滑らない。 */
export function walkPhaseFor(distanceTravelled, seed = 0) {
  return distanceTravelled * RIG.cadence + seed * TAU;
}

/**
 * 折れ線経路の移動（R58・通路ルーティング用）。path=[[x,z],...] を等速でたどる。
 * 戻り値: {x,z,u,yaw,dist,total}。dist=進んだ距離（歩行位相に使う）・u=0..1。
 * 経路が1点なら即到着（u=1）＝frozen 初回描画の「その場に居る」を壊さない。
 */
export function pathTravel(path, startedAt, t, speed = RIG.speed) {
  if (!Array.isArray(path) || path.length === 0) {
    return { x: 0, z: 0, u: 1, yaw: 0, dist: 0, total: 0 };
  }
  const segs = [];
  let total = 0;
  for (let i = 1; i < path.length; i++) {
    const dx = path[i][0] - path[i - 1][0];
    const dz = path[i][1] - path[i - 1][1];
    const len = Math.hypot(dx, dz);
    if (len < 1e-9) continue;
    segs.push({ a: path[i - 1], b: path[i], len, dx, dz });
    total += len;
  }
  const last = path[path.length - 1];
  if (total < 1e-9) {
    return { x: last[0], z: last[1], u: 1, yaw: 0, dist: 0, total: 0 };
  }
  const walked = Math.max(0, (t - startedAt) * speed);
  const dist = Math.min(walked, total);
  const u = dist / total;
  let acc = 0;
  for (const s of segs) {
    if (dist <= acc + s.len || s === segs[segs.length - 1]) {
      const k = Math.min(1, Math.max(0, (dist - acc) / s.len));
      return {
        x: s.a[0] + s.dx * k,
        z: s.a[1] + s.dz * k,
        u, dist, total,
        yaw: Math.atan2(s.dx, s.dz),
      };
    }
    acc += s.len;
  }
  return { x: last[0], z: last[1], u: 1, yaw: 0, dist: total, total };
}

/** チビロボ（会議の部下）の体格。脚が短いので腰も低い。 */
export const CHIBI_HIP_Y = 0.41;

/**
 * 🧒 会議チビロボの所作（R58）。頷き＋呼吸を基本に、23秒周期で
 * 「ピョコンと跳ねる」「挙手する」を織り込む（seedで位相分散＝一斉にやらない）。
 */
export function chibiPose(t, seed = 0) {
  const pose = {
    hipY: CHIBI_HIP_Y + Math.sin(t * 1.1 + seed) * 0.012,
    hipYaw: Math.sin(t * 0.3 + seed) * 0.05,
    hipRoll: 0,
    headYaw: Math.sin(t * 0.5 + seed) * 0.10,
    headPitch: 0.12 + Math.sin(t * 1.7 + seed) * 0.11,        // ゆっくり頷く
    legs: [-1, 1].map((side) => ({ side, hip: side * 0.05, knee: 0.04 })),
    arms: [-1, 1].map((side) => ({
      side,
      shoulder: 0.05 + Math.sin(t * 0.9 + seed + side) * 0.04,
      elbow: -0.30,
    })),
  };
  const cyc = (((t + seed * 9) % 23) + 23) % 23;
  if (cyc < 0.9) {
    // ピョコン（放物線1回・着地で終わる）
    const k = Math.sin((cyc / 0.9) * Math.PI);
    pose.hipY += k * 0.15;
    pose.legs.forEach((l) => { l.knee = 0.04 + k * 0.5; });
  } else if (cyc >= 11 && cyc < 13.2) {
    // 挙手（入り0.4秒・戻り0.4秒をなめらかに）
    const k = smoothstep(11, 11.4, cyc) * smoothstep(13.2, 12.8, cyc);
    const arm = pose.arms[1];
    arm.shoulder = arm.shoulder + (-2.6 - arm.shoulder) * k;
    arm.elbow = arm.elbow + (-0.10 - arm.elbow) * k;
  }
  return pose;
}

/**
 * 💬 休憩中のおしゃべり（R59）。座って相手の方を向き、話し手は身振り・
 * 聞き手は相槌の小頷き。speaking の交代は chatSpeaker が決める（決定論）。
 * R68: speaking は連続係数 k∈0..1 も受ける（交代境界のクロスフェード用。
 * boolean は従来互換＝true→1/false→0）。
 */
export function chatPose(t, seed = 0, speaking = false) {
  const k = typeof speaking === "number"
    ? Math.min(1, Math.max(0, speaking)) : (speaking ? 1 : 0);
  const pose = {
    hipY: RIG.sitHipY,
    hipYaw: Math.sin(t * 0.25 + seed) * 0.04,
    hipRoll: 0,
    headYaw: Math.sin(t * 0.6 + seed) * 0.08,
    headPitch: 0.10 + Math.sin(t * 2.1 + seed) * 0.07,     // 聞き手=相槌の小頷き
    legs: [-1, 1].map((side) => ({ side, hip: -1.42, knee: 1.36 })),
    arms: [-1, 1].map((side) => ({ side, shoulder: -0.28, elbow: -0.50 })),
  };
  if (k > 0) {
    // 話し手成分を k でブレンド（k=1 が従来の speaking ポーズと一致）
    const sHeadYaw = Math.sin(t * 1.3 + seed) * 0.14;
    const sHeadPitch = -0.02 + Math.sin(t * 1.8 + seed) * 0.04;
    pose.headYaw = pose.headYaw + (sHeadYaw - pose.headYaw) * k;
    pose.headPitch = pose.headPitch + (sHeadPitch - pose.headPitch) * k;
    const a = pose.arms[1];
    a.shoulder = a.shoulder + ((-0.90 + Math.sin(t * 2.6 + seed) * 0.28) - a.shoulder) * k;
    a.elbow = a.elbow + ((-1.10 + Math.sin(t * 3.4 + seed) * 0.22) - a.elbow) * k;
  }
  return pose;
}

/** 会話グループの「いま話している人」のindex（6秒交代・seedで組ごとに位相分散）。 */
export function chatSpeaker(t, seed = 0, n = 2) {
  if (!(n > 0)) return 0;
  const idx = Math.floor((((t + seed * 5) % (6 * n)) + 6 * n) % (6 * n) / 6);
  return idx % n;
}

/**
 * R68: 会話交代のクロスフェード係数。myIndex の「話し手らしさ」k∈0..1 を返す。
 * 交代直後0.4秒で新しい話し手が立ち上がり、直前の話し手が同じ窓で降りる＝
 * ポーズ/💬マーカーがワープせず滑らかに入れ替わる。chatSpeaker と同じ時割りの純関数。
 */
export function chatBlend(t, seed = 0, n = 2, myIndex = 0) {
  if (!(n > 0)) return 0;
  const cur = chatSpeaker(t, seed, n);
  const prev = (cur - 1 + n) % n;
  const phase = (((t + seed * 5) % 6) + 6) % 6;      // 現在の6秒区間内の経過
  const rise = smoothstep(0, 0.4, phase);
  if (myIndex === cur) return rise;
  if (myIndex === prev && n > 1) return 1 - rise;
  return 0;
}

/**
 * R68: 2つのポーズの線形補間（座↔立などの状態遷移を滑らかにする）。
 * k=0 で a・k=1 で b。両ポーズは同じ構造（legs/arms 各2・同じside順）である前提。
 */
export function mixPose(a, b, k) {
  if (k >= 1 || !a) return b;
  if (k <= 0) return a;
  const lerp = (x, y) => x + (y - x) * k;
  return {
    hipY: lerp(a.hipY, b.hipY),
    hipYaw: lerp(a.hipYaw, b.hipYaw),
    hipRoll: lerp(a.hipRoll, b.hipRoll),
    headYaw: lerp(a.headYaw, b.headYaw),
    headPitch: lerp(a.headPitch, b.headPitch),
    legs: b.legs.map((l, i) => ({
      side: l.side,
      hip: lerp(a.legs[i]?.hip ?? l.hip, l.hip),
      knee: lerp(a.legs[i]?.knee ?? l.knee, l.knee),
    })),
    arms: b.arms.map((m, i) => ({
      side: m.side,
      shoulder: lerp(a.arms[i]?.shoulder ?? m.shoulder, m.shoulder),
      elbow: lerp(a.arms[i]?.elbow ?? m.elbow, m.elbow),
    })),
  };
}

/** 🛋 ひとり休憩＝背にもたれ脚を投げ出し、窓の方をぼんやり眺める（R59）。 */
export function relaxPose(t, seed = 0) {
  return {
    hipY: RIG.sitHipY,
    hipYaw: 0,
    hipRoll: 0,
    headYaw: 0.35 + Math.sin(t * 0.22 + seed) * 0.15,
    headPitch: -0.14 + Math.sin(t * 0.5 + seed) * 0.03,
    legs: [-1, 1].map((side) => ({ side, hip: -1.15, knee: 0.85 })),
    arms: [-1, 1].map((side) => ({ side, shoulder: 0.30, elbow: -0.18 })),
  };
}

/** 個体ごとの位相差。全員が同じタイミングで動くと機械的に見えるのでズラす。 */
export function seedOf(id) {
  let h = 2166136261 >>> 0;
  const s = String(id ?? "");
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return (h / 4294967296) * TAU;
}

/** 0..1 の滑らかな補間（到着・退場のフェードに使う）。 */
export function smoothstep(edge0, edge1, x) {
  if (edge1 === edge0) return x < edge0 ? 0 : 1;
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}
