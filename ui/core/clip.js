// R96-D2 S3: 骨アニメの純関数サンプラ（three 非依存・時計を持たない）。ui/core/clip.js の原型。
// モジュール形（tools/glb_rig.py clips）: {fps, joints:[name…], clips:{name:{duration, frames, tracks:{"<jointIndex>":{r:<b64 i16×4×frames>, t?:<b64 i16×3×frames>, tScale}}}}}

const B64 = typeof atob === "function" ? atob : (s) => Buffer.from(s, "base64").toString("binary");
function i16(b64) {
  const bin = B64(b64), out = new Int16Array(bin.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = (bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8)) << 16 >> 16;
  return out;
}

/** モジュールを一度だけ typed array へ展開する。 */
export function decodeClips(mod) {
  const clips = {};
  for (const [name, c] of Object.entries(mod.clips)) {
    const tracks = [];
    for (const [ji, t] of Object.entries(c.tracks)) {
      tracks.push({ joint: Number(ji), r: t.r ? i16(t.r) : null, t: t.t ? i16(t.t) : null, tScale: t.tScale || 0,
        r0: t.r0 || null, t0: t.t0 || null });   // 定数だが rest と違う値（in_place の Root の高さ等）
    }
    clips[name] = { duration: c.duration, frames: c.frames, tracks };
  }
  return { fps: mod.fps, joints: mod.joints, clips };
}

/** 四元数の球面線形補間（符号を揃える）。 */
export function slerp(a, b, t, out = [0, 0, 0, 1]) {
  let dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  let s = 1;
  if (dot < 0) { dot = -dot; s = -1; }
  if (dot > 0.9995) {
    for (let i = 0; i < 4; i++) out[i] = a[i] + (s * b[i] - a[i]) * t;
  } else {
    const th = Math.acos(Math.min(1, dot)), sn = Math.sin(th), wa = Math.sin((1 - t) * th) / sn, wb = Math.sin(t * th) / sn;
    for (let i = 0; i < 4; i++) out[i] = wa * a[i] + wb * s * b[i];
  }
  const n = Math.hypot(out[0], out[1], out[2], out[3]) || 1;
  for (let i = 0; i < 4; i++) out[i] /= n;
  return out;
}

/**
 * clip を時刻 t（秒・ループ）でサンプルし、{joint: {r:[x,y,z,w], t?:[x,y,z]}} を返す。同じ t → 同じ配列（純関数）。
 * loop=false なら末尾で止まる。
 */
export function sampleClip(clip, fps, time, loop = true) {
  const frames = clip.frames, dur = (frames - 1) / fps;
  let tt = loop && dur > 0 ? ((time % dur) + dur) % dur : Math.max(0, Math.min(dur, time));
  const f = tt * fps, i0 = Math.min(frames - 1, Math.floor(f)), i1 = Math.min(frames - 1, i0 + 1), u = f - i0;
  const out = {};
  for (const tr of clip.tracks) {
    const rec = {};
    if (tr.r) {
      const a = [tr.r[i0 * 4] / 32767, tr.r[i0 * 4 + 1] / 32767, tr.r[i0 * 4 + 2] / 32767, tr.r[i0 * 4 + 3] / 32767];
      const b = [tr.r[i1 * 4] / 32767, tr.r[i1 * 4 + 1] / 32767, tr.r[i1 * 4 + 2] / 32767, tr.r[i1 * 4 + 3] / 32767];
      rec.r = i0 === i1 ? a : slerp(a, b, u);
    }
    if (tr.t) {
      rec.t = [0, 1, 2].map((k) => (tr.t[i0 * 3 + k] * (1 - u) + tr.t[i1 * 3 + k] * u) * tr.tScale);
    }
    if (!rec.r && tr.r0) rec.r = tr.r0;
    if (!rec.t && tr.t0) rec.t = tr.t0;
    out[tr.joint] = rec;
  }
  return out;
}

/** 2 つのサンプルを重み w（0=a・1=b）で混ぜる（座↔立の 0.45s 遷移用）。片方に無い骨はある方をそのまま。 */
export function blendPoses(a, b, w) {
  if (w <= 0) return a; if (w >= 1) return b;
  const out = {};
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) {
    const pa = a[k], pb = b[k];
    if (!pa) { out[k] = pb; continue; } if (!pb) { out[k] = pa; continue; }
    const rec = {};
    if (pa.r && pb.r) rec.r = slerp(pa.r, pb.r, w); else rec.r = pa.r || pb.r;
    if (pa.t && pb.t) rec.t = pa.t.map((v, i) => v * (1 - w) + pb.t[i] * w); else if (pa.t || pb.t) rec.t = pa.t || pb.t;
    out[k] = rec;
  }
  return out;
}

/** 歩行は距離で位相を決める（足が滑らない）: 1 周期あたり stride メートル。 */
export function walkTime(distance, stride, clip, fps) {
  const dur = (clip.frames - 1) / fps;
  return ((distance / stride) % 1 + 1) % 1 * dur;
}
