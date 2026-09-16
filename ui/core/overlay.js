// R96-D3 項目 4: 生成体ロボの「姿勢オーバーレイ層」。
//
// なぜ: scene3d が作り分ける 20 種類以上の poseKind は 6 本の clip に写像され、うち 16 種が sit と idle に集まる。
// その結果、❗の挙手・承認の頷き・打鍵・コンソール操作・会議チビの所作が、置き換わる前と同じ clip になって消えた。
// clip を増やさずに、clip の上へ**少しだけ足す**層をここに置く（純関数・t と seed だけの関数＝決定論は保たれる）。
//
// 掟: ui/core なので three も DOM も時刻も乱数も触らない。返すのは数値だけで、骨に触るのは ui/iso/rigbot.js。
// 返す差分は 2 種類:
//   { bone, q:[x,y,z,w], w }        … clip の回転から q へ w だけ寄せる（借り姿勢・肘が裏返らない）
//   { bone, axis:[x,y,z], angle }   … 体の rest 軸まわりの加算回転（+x=前・+y=上・+z=右）
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const smoothstep = (a, b, x) => { const t = clamp01((x - a) / (b - a)); return t * t * (3 - 2 * t); };

/**
 * 借り姿勢: 生成済み clip から実測で抜いた**骨のローカル回転**（tools ではなく実データが出どころ）。
 * 手で角度を決めると肘が裏返る（この rig は T ポーズ・腕は ±z 方向）ので、アニメーターが作った姿勢を借りる。
 */
// cheer の手が一番高いフレーム（8/15 秒）の右腕。手のワールド y は 0.844（頭の骨 0.645 より上）。
export const RAISE_R = Object.freeze({
  R_Upperarm: [0.011, -0.0287, -0.5953, 0.8029],
  R_Forearm: [0.1782, 0.7682, -0.1601, 0.5937],
  R_Hand: [-0.0303, -0.0027, 0.1093, 0.9935],
});
// sit の手が一番前に出るフレーム（39/15 秒）の両腕＝「天板に手を置く」姿勢の出どころ。
export const DESK_ARMS = Object.freeze({
  R_Upperarm: [0.3456, 0.0846, 0.6292, 0.691],
  R_Forearm: [0.795, 0.0204, -0.4943, 0.3511],
  R_Hand: [-0.0303, -0.0027, 0.1093, 0.9935],
  L_Upperarm: [0.1566, 0.0341, -0.5295, 0.833],
  L_Forearm: [0.6988, 0.2015, -0.1476, 0.6703],
  L_Hand: [0.0846, 0.001, -0.0114, 0.9964],
});
/** この層が触りうる骨（rigbot が rest の逆回転をここから 1 回だけ作る）。 */
export const OVERLAY_BONES = Object.freeze([
  "NeckTwist01", "R_Upperarm", "R_Forearm", "R_Hand", "L_Upperarm", "L_Forearm", "L_Hand",
]);

const PITCH_AXIS = [0, 0, 1];   // 体の左右軸。負で顔が下を向く（実測: -0.25rad で前方ベクトルの y が -0.257）
const TYPE_AXIS = [1, 0, 0];    // 前腕をこの軸で振ると手が上下する（実測: 0.08rad で手が 2.2cm 動く）

/** 挙手（借り姿勢＋ゆっくりした揺れ）。procedural の questionPose と同じ 1.8rad/s。 */
function raiseHand(t, seed, k = 1) {
  const w = k * (0.94 + 0.06 * Math.sin(t * 1.8 + seed));
  if (w <= 0.001) return [];
  return [
    { bone: "R_Upperarm", q: RAISE_R.R_Upperarm, w },
    { bone: "R_Forearm", q: RAISE_R.R_Forearm, w },
    { bone: "R_Hand", q: RAISE_R.R_Hand, w },
  ];
}
/** 打鍵の小刻み（左右逆位相）。手の振れ幅は片手 1.5cm 前後。 */
function typing(t, seed, amp = 0.11, hz = 3.1) {
  const a = Math.sin(t * hz * Math.PI * 2 + seed) * amp;
  return [
    { bone: "R_Forearm", axis: TYPE_AXIS, angle: a },
    { bone: "L_Forearm", axis: TYPE_AXIS, angle: -a },
  ];
}
/** 天板に手を置く（借り姿勢・両腕）。 */
function handsOnTop(w) {
  return Object.entries(DESK_ARMS).map(([bone, q]) => ({ bone, q, w }));
}
const nod = (angle) => (Math.abs(angle) < 1e-4 ? [] : [{ bone: "NeckTwist01", axis: PITCH_AXIS, angle: -angle }]);

/**
 * poseKind に足す差分を返す（無ければ空配列）。
 * @param {string} poseKind scene3d が組む文字列（"question" / "walk:approval" / "desk:" / "external:" / "chibi" …）
 * @param {number} t   秒（ui/platform/clock.js の時刻）
 * @param {number} seed 個体
 * @param {number} since そのイベントが始まってからの秒数（承認の頷きのような一発芸で使う）
 */
export function overlayFor(poseKind, t, seed = 0, since = Infinity) {
  const k = poseKind || "";
  if (k === "question" || k === "walk:question") return raiseHand(t, seed);
  if (k === "approval" || k === "walk:approval") {
    // procedural の approvalPose と同じ包絡（0.9 秒・2Hz）。首を振るのは NeckTwist01＝頭まで伝わる
    const s = Number.isFinite(since) ? since : 0;
    const env = smoothstep(0, 0.9 * 0.22, s) * (1 - smoothstep(0.9 * 0.68, 0.9, s));
    return nod(env * (0.10 + 0.14 * Math.sin(s * Math.PI * 4)));
  }
  if (k === "chibi") {
    // 会議チビ: ゆっくり頷き、23 秒周期で挙手（ピョコン跳ねは root の y＝scene3d 側）
    const out = nod(0.06 + 0.08 * Math.sin(t * 1.7 + seed));
    const cyc = (((t + seed * 9) % 23) + 23) % 23;
    if (cyc >= 11 && cyc < 13.2) {
      const kk = smoothstep(11, 11.4, cyc) * smoothstep(13.2, 12.8, cyc);
      out.push(...raiseHand(t, seed, kk));
    }
    return out;
  }
  if (k.startsWith("external")) {
    // カウンターの陰で頭しか見えないのを直した R75 の狙い（手が動いて見える）を取り戻す
    return [...handsOnTop(0.8), ...typing(t, seed, 0.04, 1.6)];
  }
  // 打鍵は**実際に作業している席**だけ（scene3d が ":work" を付ける）。指示待ちの席は机の姿勢のまま動かさない。
  // マグを持っている間（"desk::mug"）も右手が塞がっている＝打鍵にしない。
  if (k.startsWith("desk")) return k.endsWith(":work") ? typing(t, seed) : [];
  return [];
}

/** チビのピョコン跳ね（root の y に足す・生成体ごと跳ねる）。procedural の chibiPose と同じ 23 秒周期。 */
export function chibiBounce(t, seed = 0) {
  const cyc = (((t + seed * 9) % 23) + 23) % 23;
  return cyc < 0.9 ? Math.sin((cyc / 0.9) * Math.PI) * 0.15 : 0;
}
