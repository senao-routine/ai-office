// GBパルス波の効果音（R31-L2 の合成レシピを新UIへ移植・外部アセット0）。
// 掟: 既定OFF・AudioContext はユーザー操作後にのみ生成（自動再生ポリシー）。
const KEY = "aioffice.iso.sound";
let ctx = null;
let wave = null;

export function soundOn() {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function setSound(on) {
  try {
    localStorage.setItem(KEY, on ? "1" : "0");
  } catch { /* 保存できなくても続行 */ }
}

function ensureCtx() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    // デューティ比25%のパルス波（フーリエ級数 real[n]=(2/nπ)sin(nπd)＝GBの音色）
    const N = 32;
    const real = new Float32Array(N);
    const imag = new Float32Array(N);
    for (let n = 1; n < N; n++) real[n] = (2 / (n * Math.PI)) * Math.sin(n * Math.PI * 0.25);
    wave = ctx.createPeriodicWave(real, imag, { disableNormalization: false });
  }
  if (ctx.state === "suspended") ctx.resume();
  return ctx;
}

/** タイプライターの1打（Undertale方式=短いブリップ・呼び側で間引く）。 */
export function blip(freq = 523) {
  if (!soundOn()) return;
  const c = ensureCtx();
  if (!c) return;
  const osc = c.createOscillator();
  const gain = c.createGain();
  osc.setPeriodicWave(wave);
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.05, c.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, c.currentTime + 0.055);
  osc.connect(gain).connect(c.destination);
  osc.start();
  osc.stop(c.currentTime + 0.06);
}
