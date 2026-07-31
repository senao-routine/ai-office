// スタイルの選択と永続化だけを持つ小さなモジュール。
// R50-P6: ドット絵スタイルはユーザー判断で撤去（2026-07-30）＝現行は iso のみ。
// 追加スタイルを試すときはここへ登録して boot.html の動的 import に乗せる。
export const STYLES = Object.freeze({ ISO: "iso" });
export const STYLE_KEY = "aioffice.ui";
const VALID = new Set(Object.values(STYLES));

/** ?ui=<style> > localStorage > 既定(iso) の順で解決する。不正値は既定へ落とす。 */
export function resolveStyle(search = location.search) {
  const q = new URLSearchParams(search).get("ui");
  if (q && VALID.has(q)) return q;
  let saved = null;
  try {
    saved = localStorage.getItem(STYLE_KEY);
  } catch {
    saved = null;                       // プライベートモード等で localStorage が使えない
  }
  return VALID.has(saved) ? saved : STYLES.ISO;
}

export function persistStyle(style) {
  if (!VALID.has(style)) return;
  try {
    localStorage.setItem(STYLE_KEY, style);
  } catch {
    /* 保存できなくても起動は続ける */
  }
}

/**
 * スタイルを切り替える。3D の WebGL コンテキストを確実に破棄するため、
 * その場で差し替えるのではなくリロードする（GPUメモリのリークを避ける唯一確実な手段）。
 */
export function switchStyle(style) {
  if (!VALID.has(style)) return;
  persistStyle(style);
  const url = new URL(location.href);
  url.searchParams.delete("ui");        // 明示指定を消して localStorage を正にする
  location.replace(url.toString());
}
