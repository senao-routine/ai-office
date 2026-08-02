import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CHIBI_HIP_Y, RIG, TAU, chatBlend, chatPose, chatSpeaker, chibiPose, mixPose, idlePose, pathTravel, poseFor, relaxPose, seatedPose, seedOf, smoothstep, thinkingPose, travel, typingPose, walkPhaseFor, walkPose,
} from "./anim.js";

const ALL_POSES = [walkPose(1.1), typingPose(3.2), seatedPose(3.2), idlePose(3.2)];

test("すべてのポーズが同じ形を返す（描画側が分岐せずに済む）", () => {
  for (const p of ALL_POSES) {
    assert.deepEqual(Object.keys(p).sort(),
      ["arms", "headPitch", "headYaw", "hipRoll", "hipY", "hipYaw", "legs"].sort());
    assert.equal(p.legs.length, 2);
    assert.equal(p.arms.length, 2);
    for (const l of p.legs) assert.deepEqual(Object.keys(l).sort(), ["hip", "knee", "side"]);
    for (const a of p.arms) assert.deepEqual(Object.keys(a).sort(), ["elbow", "shoulder", "side"]);
  }
});

test("ポーズの数値は全部有限（NaN が混ざると体が消える）", () => {
  for (const p of ALL_POSES) {
    const nums = [p.hipY, p.hipYaw, p.hipRoll, p.headYaw, p.headPitch,
      ...p.legs.flatMap((l) => [l.hip, l.knee]),
      ...p.arms.flatMap((a) => [a.shoulder, a.elbow])];
    for (const n of nums) assert.ok(Number.isFinite(n), `非有限値: ${n}`);
  }
});

test("歩行: 左右の脚は逆位相（同時に同じ側へ出さない）", () => {
  for (const ph of [0, 0.7, 1.9, 3.3, 5.1]) {
    const p = walkPose(ph);
    const [l, r] = p.legs;
    assert.ok(Math.abs(l.hip + r.hip) < 1e-9, `位相 ${ph} で脚が同相`);
  }
});

test("歩行: 腕は脚と逆に振る", () => {
  const p = walkPose(0.9);
  // 左脚(side=-1)が前なら、左腕(side=-1)は後ろ
  assert.ok(Math.sign(p.legs[0].hip) !== Math.sign(p.arms[0].shoulder));
});

test("歩行: 膝は決して逆に折れない", () => {
  for (let ph = 0; ph < TAU * 2; ph += 0.05) {
    for (const l of walkPose(ph).legs) {
      assert.ok(l.knee >= 0, `位相 ${ph.toFixed(2)} で膝が逆折れ (${l.knee})`);
    }
  }
});

test("歩行: 腰は必ず立位の高さ以上で上下する（地面にめり込まない）", () => {
  let min = Infinity;
  let max = -Infinity;
  for (let ph = 0; ph < TAU; ph += 0.02) {
    const y = walkPose(ph).hipY;
    min = Math.min(min, y);
    max = Math.max(max, y);
  }
  assert.ok(min >= RIG.hipY - 1e-9, `腰が沈みすぎ: ${min}`);
  assert.ok(max - min > 0.01, "上下動が無い（歩いて見えない）");
  assert.ok(max - min < 0.12, "上下動が大きすぎる（跳ねて見える）");
});

test("歩行: 1周期でポーズが元に戻る（継ぎ目が出ない）", () => {
  const a = walkPose(0.4);
  const b = walkPose(0.4 + TAU);
  assert.ok(Math.abs(a.hipY - b.hipY) < 1e-9);
  assert.ok(Math.abs(a.legs[0].hip - b.legs[0].hip) < 1e-9);
});

test("着席ポーズは腰が低く、脚が畳まれている", () => {
  for (const p of [typingPose(3.2), seatedPose(3.2)]) {
    assert.equal(p.hipY, RIG.sitHipY);
    for (const l of p.legs) {
      assert.ok(l.hip < -1, "腿が前に出ていない（立って見える）");
      assert.ok(l.knee > 1, "膝が曲がっていない");
    }
  }
});

test("タイピングは肘が動く＝指を刻んで見える", () => {
  const e1 = typingPose(3.20).arms[0].elbow;
  const e2 = typingPose(3.35).arms[0].elbow;
  assert.notEqual(e1, e2);
});

test("poseFor: ゾーンからポーズが決まる（場所＝状態）", () => {
  assert.deepEqual(poseFor("desk", 3.2, 0), typingPose(3.2, 0));
  assert.deepEqual(poseFor("meeting", 3.2, 0), seatedPose(3.2, 0));
  assert.deepEqual(poseFor("queue", 3.2, 0), idlePose(3.2, 0));
  // 歩行位相が渡されたらゾーンより優先（移動中）
  assert.deepEqual(poseFor("desk", 3.2, 0, 1.1), walkPose(1.1));
});

test("poseFor は決定論（同じ t/seed なら必ず同じ）", () => {
  for (const zone of ["desk", "meeting", "lounge", "queue", "external"]) {
    assert.deepEqual(poseFor(zone, 7.5, 2.2), poseFor(zone, 7.5, 2.2));
  }
});

// ── 移動 ──────────────────────────────────────────────────────
test("travel: 開始点から終了点へ、u は 0→1", () => {
  const from = [0, 0];
  const to = [3, 4];                       // 距離5
  const dur = 5 / RIG.speed;
  assert.deepEqual(travel(from, to, 10, 10).u, 0);
  assert.equal(travel(from, to, 10, 10 + dur).u, 1);
  const mid = travel(from, to, 10, 10 + dur / 2);
  assert.ok(Math.abs(mid.x - 1.5) < 1e-9);
  assert.ok(Math.abs(mid.z - 2.0) < 1e-9);
});

test("travel: 到着後は行き過ぎない", () => {
  const p = travel([0, 0], [1, 0], 0, 9999);
  assert.equal(p.u, 1);
  assert.equal(p.x, 1);
});

test("travel: 出発前でも戻らない（u は 0 未満にならない）", () => {
  assert.equal(travel([0, 0], [1, 0], 100, 50).u, 0);
});

test("travel: 同一地点は距離0で即到着（0除算しない）", () => {
  const p = travel([2, 3], [2, 3], 0, 0);
  assert.equal(p.u, 1);
  assert.equal(p.dist, 0);
  assert.ok(Number.isFinite(p.x) && Number.isFinite(p.z));
});

test("travel: 向きは進行方向を向く", () => {
  assert.ok(Math.abs(travel([0, 0], [0, 5], 0, 0).yaw - 0) < 1e-9);          // +z
  assert.ok(Math.abs(travel([0, 0], [5, 0], 0, 0).yaw - Math.PI / 2) < 1e-9); // +x
});

test("walkPhaseFor: 進んだ距離で位相が決まる＝足が滑らない", () => {
  assert.equal(walkPhaseFor(0, 0), 0);
  assert.ok(walkPhaseFor(1, 0) > walkPhaseFor(0.5, 0));
  // 同じ距離なら必ず同じ位相
  assert.equal(walkPhaseFor(2.5, 1.1), walkPhaseFor(2.5, 1.1));
});

test("seedOf: 決定論で 0..TAU の範囲", () => {
  assert.equal(seedOf("abc"), seedOf("abc"));
  assert.notEqual(seedOf("abc"), seedOf("abd"));
  for (const s of ["", "a", "とても長い日本語のプロジェクト名", "x".repeat(300)]) {
    const v = seedOf(s);
    assert.ok(v >= 0 && v < TAU && Number.isFinite(v));
  }
});

test("smoothstep: 端で 0/1・範囲外でも飽和", () => {
  assert.equal(smoothstep(0, 1, -5), 0);
  assert.equal(smoothstep(0, 1, 5), 1);
  assert.equal(smoothstep(0, 1, 0.5), 0.5);
  assert.equal(smoothstep(1, 1, 2), 1);          // 0除算しない
});

// ── R56: thinkingPose（考え込む所作） ─────────────────────────
test("thinkingPose: 決定論・着席・片手が顎（肘の深い曲げ）・ゆっくり揺れる", () => {
  const a = thinkingPose(3.2, 1.0);
  const b = thinkingPose(3.2, 1.0);
  assert.deepEqual(a, b, "同じt・seedなら同じポーズ（golden前提）");
  assert.equal(a.hipY, RIG.sitHipY, "着席の腰高");
  assert.ok(a.headPitch > 0.15, "うつむき気味");
  assert.ok(a.arms[0].elbow < -1.5, "顎の手＝肘が深く曲がる");
  assert.ok(a.arms[0].elbow < a.arms[1].elbow, "顎の手はもう片方より深く曲がる");
  const c = thinkingPose(6.0, 1.0);
  assert.notEqual(a.headYaw, c.headYaw, "tで揺れる（静止画ではない）");
});

// ── R58: pathTravel（折れ線移動）と chibiPose（2頭身の所作） ─────────
test("pathTravel: 折れ線を等速でたどり、distが単調に増える", () => {
  const path = [[0, 0], [4, 0], [4, 3]];          // 総距離7
  const a = pathTravel(path, 0, 1, 1);            // 1秒後=1m地点
  assert.ok(Math.abs(a.x - 1) < 1e-9 && Math.abs(a.z) < 1e-9);
  assert.ok(a.u > 0 && a.u < 1);
  const b = pathTravel(path, 0, 5, 1);            // 5m=角を曲がって(4,1)
  assert.ok(Math.abs(b.x - 4) < 1e-9 && Math.abs(b.z - 1) < 1e-9);
  assert.ok(Math.abs(b.yaw) < 1e-9 || b.yaw !== a.yaw, "セグメントでyawが変わる");
  const c = pathTravel(path, 0, 100, 1);
  assert.equal(c.u, 1);
  assert.ok(Math.abs(c.x - 4) < 1e-9 && Math.abs(c.z - 3) < 1e-9);
  assert.equal(c.total, 7);
});

test("pathTravel: 1点経路は即到着（frozen初回描画の掟）・空でも落ちない", () => {
  const p = pathTravel([[2, 5]], 0, 0);
  assert.deepEqual([p.x, p.z, p.u], [2, 5, 1]);
  assert.equal(pathTravel([], 0, 10).u, 1);
  assert.equal(pathTravel(null, 0, 10).u, 1);
});

test("chibiPose: 決定論・腰は低い・跳ね/挙手の窓が存在する", () => {
  const a = chibiPose(5, 1);
  const b = chibiPose(5, 1);
  assert.deepEqual(a, b, "同じt+seed→同じポーズ");
  assert.ok(Math.abs(a.hipY - CHIBI_HIP_Y) < 0.2, "腰高はチビ基準");
  // 跳ね窓（cyc<0.9）: seed=0, t=0.45 → 空中
  const hop = chibiPose(0.45, 0);
  assert.ok(hop.hipY > CHIBI_HIP_Y + 0.05, "跳ねで腰が上がる");
  // 挙手窓（cyc 11..13.2）: seed=0, t=12.1 → 右肩が大きく上がる
  const raise = chibiPose(12.1, 0);
  assert.ok(raise.arms[1].shoulder < -2.0, `挙手 shoulder=${raise.arms[1].shoulder}`);
  // 窓の外は通常の頷き
  const calm = chibiPose(6, 0);
  assert.ok(calm.arms[1].shoulder > -1 && calm.hipY < CHIBI_HIP_Y + 0.05);
});

// ── R59: おしゃべり・くつろぎ ─────────────────────────────────
test("chatSpeaker: 6秒交代で全員に回る・決定論・範囲内", () => {
  assert.equal(chatSpeaker(1, 0, 2), chatSpeaker(1, 0, 2));
  const seen = new Set();
  for (let t = 0; t < 12; t += 1) seen.add(chatSpeaker(t, 0, 2));
  assert.deepEqual([...seen].sort(), [0, 1], "2人とも話す番が来る");
  assert.notEqual(chatSpeaker(0, 0, 2), chatSpeaker(6.5, 0, 2), "6秒で交代");
  for (let t = 0; t < 30; t += 2.5) {
    const i = chatSpeaker(t, 1.2, 3);
    assert.ok(i >= 0 && i < 3);
  }
  assert.equal(chatSpeaker(5, 0, 0), 0);
});

test("chatPose: 話し手は身振り（腕が上がる）・聞き手は静か・純関数", () => {
  const listen = chatPose(2.0, 0.5, false);
  const speak = chatPose(2.0, 0.5, true);
  assert.ok(speak.arms[1].shoulder < listen.arms[1].shoulder - 0.3,
    "話し手の腕が聞き手より上がっている");
  assert.deepEqual(chatPose(2.0, 0.5, true), speak, "同じ入力→同じポーズ");
  assert.equal(listen.hipY, RIG.sitHipY, "着席");
});

test("relaxPose: 着席・脚を投げ出す・頭は上向き寄り", () => {
  const p = relaxPose(1.0, 0.3);
  assert.equal(p.hipY, RIG.sitHipY);
  assert.ok(p.legs[0].knee < 1.0, "膝が伸び気味（投げ出し）");
  assert.ok(p.headPitch < 0, "視線が上向き");
});

// ── R68: 状態遷移の補間（座↔立スナップ根絶の数学） ─────────────────
test("mixPose: k=0で前ポーズ・k=1で次ポーズ・中間は線形", () => {
  const a = typingPose(1.0, 0);          // 着席（hipY=sitHipY）
  const b = walkPose(0.5);               // 歩行（hipY≈hipY+bounce）
  assert.deepEqual(mixPose(a, b, 0), a);
  assert.deepEqual(mixPose(a, b, 1), b);
  const mid = mixPose(a, b, 0.5);
  assert.ok(Math.abs(mid.hipY - (a.hipY + b.hipY) / 2) < 1e-9);
  assert.ok(Math.abs(mid.arms[0].shoulder
    - (a.arms[0].shoulder + b.arms[0].shoulder) / 2) < 1e-9);
  assert.equal(mid.legs.length, 2);
  assert.equal(mid.legs[0].side, b.legs[0].side);
});

test("mixPose: 前ポーズ無し(null)は次ポーズをそのまま返す（初回シード=-∞の経路）", () => {
  const b = seatedPose(2.0, 1);
  assert.deepEqual(mixPose(null, b, 0.3), b);
});

test("chatBlend: 交代境界で新話者が0.4秒かけて立ち上がり・前話者が同時に降りる", () => {
  const seed = 0;
  const n = 2;
  // seed=0: 区間[0,6)=話者0・[6,12)=話者1。境界 t=6 の直後を検査
  assert.equal(chatSpeaker(5.9, seed, n), 0);
  assert.equal(chatSpeaker(6.1, seed, n), 1);
  assert.ok(chatBlend(6.05, seed, n, 1) > 0 && chatBlend(6.05, seed, n, 1) < 1);  // 立ち上がり中
  assert.ok(chatBlend(6.05, seed, n, 0) > 0 && chatBlend(6.05, seed, n, 0) < 1);  // 降り中
  assert.ok(Math.abs(chatBlend(6.05, seed, n, 0) + chatBlend(6.05, seed, n, 1) - 1) < 1e-9);
  assert.equal(chatBlend(8.0, seed, n, 1), 1);   // 区間の中盤は完全に話し手
  assert.equal(chatBlend(8.0, seed, n, 0), 0);
});

test("chatPose: k=1がspeaking=true・k=0がfalseと一致（後方互換）", () => {
  assert.deepEqual(chatPose(2.5, 1.1, 1), chatPose(2.5, 1.1, true));
  assert.deepEqual(chatPose(2.5, 1.1, 0), chatPose(2.5, 1.1, false));
  // 中間kは両者の間
  const k5 = chatPose(2.5, 1.1, 0.5);
  const off = chatPose(2.5, 1.1, false);
  const on = chatPose(2.5, 1.1, true);
  assert.ok(Math.abs(k5.arms[1].shoulder - (off.arms[1].shoulder + on.arms[1].shoulder) / 2) < 1e-9);
});
