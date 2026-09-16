// R96-D3: 生成体ロボ（?rig=1 既定）の「姿勢の写像」と「重ねる部品の集合」を機械で固定する。
// R96-D2 の欠陥（手の小道具が 0 個・着席中に立ち clip・状態ランプ消滅）は全ゲート緑のまま本番へ出た＝ここが門。
import assert from "node:assert/strict";
import { test } from "node:test";
import { registerHooks } from "node:module";

// ブラウザ用の絶対パス（/ui/...）を node から読めるようにする（tests/iso_acts.mjs と同じ流儀）
registerHooks({ resolve(specifier, context, next) {
  if (specifier.startsWith("/ui/")) return next(new URL(`..${specifier}`, import.meta.url).href, context);
  return next(specifier, context);
} });
const { clipFor, transitionTracker, D_PARTS, D_PARTS_CLAW, HYBRID_PARTS } = await import("../ui/iso/rigbot.js");

/** scene3d が実際に組む poseKind の語彙（ui/iso/scene3d.js の poseKind 代入箇所から）。 */
const STANDING = {
  walk: "walk", "walk:question": "walk", "walk:celebrate": "walk", "walk:approval": "walk",
  enter: "walk", run: "walk", exit: "walk",
  greet: "cheer", celebrate: "cheer",
  question: "look_around", think: "wait", chibi: "wait",
  "meeting:present": "idle", "meeting:stand": "idle", "external:": "idle",
};
test("立っているときの poseKind → clip", () => {
  for (const [kind, clip] of Object.entries(STANDING)) {
    assert.equal(clipFor(kind, false), clip, `${kind} が ${clip} にならない`);
  }
});

test("着席中は question / think でも座り clip（立ち clip だと椅子と床を貫く・R96-D3）", () => {
  for (const kind of ["question", "think", "desk:", "meeting:", "lounge:", "relax", "loungeTab", "read:book", "chat", ""]) {
    assert.equal(clipFor(kind, true), "sit", `${kind} が座り clip にならない`);
  }
  // 歩き・一発芸は着席フラグより強い（遷移中に座り clip へ落ちない）
  assert.equal(clipFor("walk", true), "walk");
  assert.equal(clipFor("greet", true), "cheer");
  assert.equal(clipFor("celebrate", true), "cheer");
});

test("重ねる部品の集合（消えると表情・状態・小道具が黙って落ちる）", () => {
  for (const part of ["visorRig", "chest", "antTip", "__acc", "__prop"]) {
    assert.ok(D_PARTS.has(part), `D_PARTS に ${part} が無い`);
    assert.ok(D_PARTS_CLAW.has(part), `D_PARTS_CLAW に ${part} が無い`);
  }
  assert.ok(D_PARTS_CLAW.has("claw"), "OpenClaw のハサミが出ない");
  assert.ok(!D_PARTS.has("claw"), "claude/codex にハサミが付いている");
  // ハイブリッド（?rig=2）は頭ごと procedural
  for (const part of ["head", "visor", "chest", "__acc"]) assert.ok(HYBRID_PARTS.has(part), `HYBRID_PARTS に ${part} が無い`);
});

test("遷移元の clip は補間が終わるまで動かない（1 フレームで遷移先に化けると 0.45 秒の補間が消える）", () => {
  const tr = transitionTracker();
  assert.equal(tr.step("desk:", "sit", 0, -Infinity).a, null);        // 初回は遷移元なし
  assert.equal(tr.step("desk:", "sit", 0, -Infinity).a, null);        // 同じ姿勢のあいだは据え置き
  for (let i = 0; i < 3; i++) {
    const src = tr.step("walk", "walk", 10, 0);                        // 遷移の 1・2・3 フレーム目
    assert.equal(src.a.clip, "sit", "遷移元が遷移先に化けた（今回の回帰そのもの）");
    assert.equal(src.b, null, "十分に経った遷移は 1 本に畳まれる");
  }
  assert.equal(tr.step("greet", "cheer", 30, 10).a.clip, "walk");      // 次の遷移で初めて更新される
});

test("補間の途中で次の遷移が来たら、表示中の混合姿勢を遷移元として引き継ぐ", () => {
  const tr = transitionTracker();
  tr.step("desk:", "sit", 0, -Infinity);
  tr.step("walk", "walk", 10, 0);                                      // 着席 → 歩行
  const src = tr.step("greet", "cheer", 10.2, 10);                     // 0.2 秒で挨拶に割り込まれる
  assert.equal(src.a.clip, "sit");
  assert.equal(src.b.clip, "walk", "中断された遷移先が遷移元の片側に残らない＝そこへ跳ぶ");
  assert.ok(src.mix > 0.3 && src.mix < 0.6, `混合比 ${src.mix} がイベント時刻の差から決まっていない`);
  assert.equal(src.b.at, 10, "一発芸の位相を読むための開始時刻が失われている");
  // 3 本目が来たら一番古いものを落とす（2 本に畳む）
  const s3 = tr.step("walk", "walk", 10.3, 10.2);
  assert.equal(s3.a.clip, "walk");
  assert.equal(s3.b.clip, "cheer");
});

test("同じ clip へ渡る遷移は恒等（kind だけ変わって clip が同じなら混合は無害）", () => {
  const tr = transitionTracker();
  tr.step("walk", "walk", 0, -Infinity);
  assert.equal(tr.step("run", "walk", 10, 0).a.clip, "walk");
});

// ── 姿勢オーバーレイ層（R96-D3 項目 4）。実データの骨で FK を解いて「所作が本当に出ているか」を測る。
// D2 のリグ化で ❗の挙手・承認の頷き・打鍵・コンソール・チビの所作が全ゲート緑のまま消えた＝ここが門。
const { overlayFor, chibiBounce, RAISE_R, DESK_ARMS } = await import("../ui/core/overlay.js");
const { decodeClips, sampleClip, slerp } = await import("../ui/core/clip.js");
const body = (await import("../ui/iso/gen/robot_body.js")).default;
const clipData = decodeClips((await import("../ui/iso/gen/robot_clips.js")).default);
const sk = body.skeleton;
const qmul = (a, b) => [a[3]*b[0]+a[0]*b[3]+a[1]*b[2]-a[2]*b[1], a[3]*b[1]-a[0]*b[2]+a[1]*b[3]+a[2]*b[0],
  a[3]*b[2]+a[0]*b[1]-a[1]*b[0]+a[2]*b[3], a[3]*b[3]-a[0]*b[0]-a[1]*b[1]-a[2]*b[2]];
const qconj = (q) => [-q[0], -q[1], -q[2], q[3]];
const qrot = (q, v) => { const [x, y, z, w] = q, [vx, vy, vz] = v;
  const ix = w*vx + y*vz - z*vy, iy = w*vy + z*vx - x*vz, iz = w*vz + x*vy - y*vx, iw = -x*vx - y*vy - z*vz;
  return [ix*w + iw*-x + iy*-z - iz*-y, iy*w + iw*-y + iz*-x - ix*-z, iz*w + iw*-z + ix*-y - iy*-x]; };
const qaxis = (a, ang) => { const n = Math.hypot(...a) || 1, s = Math.sin(ang / 2); return [a[0]/n*s, a[1]/n*s, a[2]/n*s, Math.cos(ang / 2)]; };
const nodeOf = new Map(sk.joints.map((ni, j) => [sk.nodes[ni].name, { ni, j }]));
const depth = (i) => { let d = 0; for (let j = sk.nodes[i].parent; j >= 0; j = sk.nodes[j].parent) d++; return d; };
const ORDER = sk.nodes.map((_, i) => i).sort((a, b) => depth(a) - depth(b));
function fk(local) {
  const W = [];
  for (const i of ORDER) {
    const l = local[i] || sk.nodes[i], p = sk.nodes[i].parent;
    if (p < 0) { W[i] = { p: [...l.t], q: [...l.r] }; continue; }
    const v = qrot(W[p].q, l.t);
    W[i] = { p: [W[p].p[0]+v[0], W[p].p[1]+v[1], W[p].p[2]+v[2]], q: qmul(W[p].q, l.r) };
  }
  return W;
}
const REST = fk([]);
/** poseKind をオーバーレイ込みで解いて、手・頭のワールド位置と頭の前方ベクトルを返す。 */
function solve(kind, t, seed, seated, since = Infinity, overlay = true) {
  const sample = sampleClip(clipData.clips[clipFor(kind, seated)], clipData.fps, t);
  const local = [];
  for (let j = 0; j < sk.joints.length; j++) {
    const ni = sk.joints[j], r0 = sk.nodes[ni], s = sample[j];
    local[ni] = { r: s?.r ? [...s.r] : [...r0.r],
      t: s?.t ? [r0.t[0]+s.t[0], r0.t[1]+s.t[1], r0.t[2]+s.t[2]] : [...r0.t] };
  }
  if (overlay) for (const e of overlayFor(kind, t, seed, since)) {
    const { ni, j } = nodeOf.get(e.bone);
    if (e.q) local[ni].r = slerp(sample[j]?.r || sk.nodes[ni].r, e.q, e.w);
    else local[ni].r = qmul(local[ni].r, qaxis(qrot(qconj(REST[ni].q), e.axis), e.angle));
  }
  const W = fk(local), head = nodeOf.get("Head").ni;
  return { rHand: W[nodeOf.get("R_Hand").ni].p, lHand: W[nodeOf.get("L_Hand").ni].p,
    head: W[head].p, fwd: qrot(W[head].q, qrot(qconj(REST[head].q), [1, 0, 0])) };
}
const headBoneY = REST[nodeOf.get("Head").ni].p[1];
const pitch = (p) => Math.atan2(p.fwd[1], p.fwd[0]);
const dist = (a, b) => Math.hypot(a[0]-b[0], a[1]-b[1], a[2]-b[2]);

test("❗の挙手: question で右手が頭の骨より上へ上がる（リグ化で腕が体側に垂れたまま＝0.245 だった）", () => {
  for (const seated of [false, true]) for (const t of [0.3, 2.2, 5.9]) {
    const on = solve("question", t, 1.3, seated), off = solve("question", t, 1.3, seated, Infinity, false);
    assert.ok(on.rHand[1] > headBoneY, `seated=${seated} t=${t}: 手 ${on.rHand[1].toFixed(3)} が頭 ${headBoneY.toFixed(3)} を越えない`);
    assert.ok(on.rHand[1] - off.rHand[1] > 0.2, `seated=${seated} t=${t}: 層なし(${off.rHand[1].toFixed(3)})と大差ない`);
  }
  assert.ok(overlayFor("walk:question", 1, 0).length > 0, "歩きながらの ❗ でも挙手する");
});

test("承認の頷き: approval で頭の前方ベクトルが 0.3 秒に 0.05rad 以上振れる（首は followAll に上書きされない位置で足す）", () => {
  let lo = Infinity, hi = -Infinity;
  for (let s = 0; s <= 0.3001; s += 0.02) { const a = pitch(solve("approval", 10 + s, 0.7, true, s)); lo = Math.min(lo, a); hi = Math.max(hi, a); }
  assert.ok(hi - lo >= 0.05, `振れ ${(hi - lo).toFixed(3)}rad`);
  // 0.9 秒で終わる一発芸（包絡の外では差分ゼロ＝clip のまま）
  assert.equal(overlayFor("approval", 10, 0.7, 2.0).length, 0);
});

test("打鍵: desk では手が動き、待機では動かない（マグを持っている間は打鍵しない）", () => {
  const span = (kind) => { const pts = []; for (let t = 10; t < 10.35; t += 0.01) pts.push(solve(kind, t, 0.2, true).rHand);
    return Math.max(...pts.map((p) => dist(p, pts[0]))); };
  // sit clip 自体が呼吸で 0.35 秒に 5mm ほど動く（実測）。打鍵はその 3 倍動くことで区別する
  const work = span("desk:"), idle = span("");
  assert.ok(work >= 0.012, `打鍵の振れ ${work.toFixed(4)}`);
  assert.ok(work > idle * 2.5, `打鍵 ${work.toFixed(4)} と待機 ${idle.toFixed(4)} が区別できない`);
  assert.deepEqual(overlayFor("desk::mug", 10, 0.2), []);
});

test("コンソール: external で両手が天面の方へ上がる（R75 が直した「ハサミが見えない」が戻らないように）", () => {
  const on = solve("external:", 3.0, 0.4, false), off = solve("external:", 3.0, 0.4, false, Infinity, false);
  assert.ok(on.rHand[1] - off.rHand[1] > 0.15, "右手が上がっていない");
  assert.ok(on.lHand[1] - off.lHand[1] > 0.05, "左手が上がっていない");
  assert.ok(on.rHand[0] > off.rHand[0], "手が前に出ていない");
});

test("会議チビ: 頷きが出て、23 秒周期の跳ねと挙手が生きている", () => {
  let lo = Infinity, hi = -Infinity;
  for (let t = 0; t < 4; t += 0.05) { const a = pitch(solve("chibi", t, 2.0, false)); lo = Math.min(lo, a); hi = Math.max(hi, a); }
  assert.ok(hi - lo >= 0.05, `頷きの振れ ${(hi - lo).toFixed(3)}rad`);
  let bmax = 0; for (let t = 0; t < 46; t += 0.02) bmax = Math.max(bmax, chibiBounce(t, 2.0));
  assert.ok(bmax >= 0.1, `跳ねの高さ ${bmax.toFixed(3)}`);
  assert.equal(chibiBounce(5, 0), 0, "跳ねは 23 秒に 1 回（0.9 秒）だけ");
  const raise = solve("chibi", 12 - 2.0 * 9 + 23 * 8, 2.0, false);   // 挙手の窓（cyc 11〜13.2）
  assert.ok(raise.rHand[1] > headBoneY, "挙手の窓で手が頭より上に上がらない");
});

test("4 状態が互いに違う姿勢になる（working / waiting / attention / approval）", () => {
  const at = (kind, t, since) => solve(kind, t, 0.9, true, since);
  const waiting = at("", 12.08), working = at("desk:", 12.08), attention = at("question", 12.08), approval = at("approval", 12.08, 0.2);
  assert.ok(dist(working.rHand, waiting.rHand) > 0.005, "打鍵と待機が同じ手の位置");
  assert.ok(dist(attention.rHand, waiting.rHand) > 0.2, "挙手と待機が同じ手の位置");
  // 承認は首だけ動く所作（procedural の approvalPose と同じ）＝手ではなく頭で違いを測る
  assert.ok(Math.abs(pitch(approval) - pitch(waiting)) > 0.02, "承認と待機で頭の向きが同じ");
});

test("借り姿勢の定数は単位クォータニオン（生成 clip から抜いた実測値）", () => {
  for (const [name, q] of [...Object.entries(RAISE_R), ...Object.entries(DESK_ARMS)]) {
    assert.ok(Math.abs(Math.hypot(...q) - 1) < 1e-3, `${name} が単位でない`);
  }
});
