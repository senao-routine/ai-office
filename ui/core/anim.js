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
