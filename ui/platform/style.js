// スタイルの選択と永続化だけを持つ小さなモジュール。
// R50-P6: ドット絵スタイルはユーザー判断で撤去（2026-07-30）＝現行は iso のみ。
// 追加スタイルを試すときはここへ登録して boot.html の動的 import に乗せる。
// R90-S1: 方向Cを ui/iso に統合。スタイルは1本、保存キーは既定切替時のまま維持する。
// R98-W1: 第 2 の様式 pixel（フロア帯 × 台帳）。既定は iso のまま・pixel は ?ui=pixel か切替ボタンで。
export const STYLES = Object.freeze({ ISO: "iso", PIXEL: "pixel" });
export const STYLE_KEY = "aioffice.ui.v2";
const VALID = new Set(Object.values(STYLES));

/** ?ui=<style> > localStorage > 既定(iso) の順で解決する。不正値は既定へ落とす。 */
export function resolveStyle(search = location.search) {
  const params = new URLSearchParams(search);
  // R98: 配信 URL（?stream=1）は様式の保存値も ?ui= も無視して iso。匿名化（privacy）・字幕帯・
  // balanced フィットは 3D 側の実装で、台帳を選んだブラウザで配信 URL を開くとプロジェクト名が
  // そのまま画面に出る（別モデルレビュー high）。配信は常に「対応している様式」で起動する。
  if (params.get("stream") === "1") return STYLES.ISO;
  const q = params.get("ui");
  if (q && VALID.has(q)) return q;
  let saved = null;
  try {
    saved = localStorage.getItem(STYLE_KEY);
    if (saved === "iso2") saved = STYLES.ISO; // R90-S1: 改名前の保存値を移行
  } catch {
    saved = null;                       // プライベートモード等で localStorage が使えない
  }
  return VALID.has(saved) ? saved : STYLES.ISO;
}

/**
 * 解決した様式を保存する。保存できたら true。
 * 配信 URL（?stream=1）で起動したときは保存しない＝配信は iso を**強制**しているだけで、
 * 普段の表示設定（台帳）を上書きしてはいけない（別モデルレビュー）。
 */
export function persistStyle(style, search = location.search) {
  if (!VALID.has(style)) return false;
  if (new URLSearchParams(search).get("stream") === "1") return false;
  try {
    localStorage.setItem(STYLE_KEY, style);
    return true;
  } catch {
    return false;                       // プライベートモード等。起動は続ける
  }
}

/**
 * 様式を切り替えて再起動する（R85-2 で撤去・R98-W1 で復活）。
 * 保存できたら ?ui= を消して localStorage を正に。保存できない環境では ?ui=<style> を付けて再読み込み
 *（黙って同じ画面に戻る＝「押しても何も起きない」を作らない・別モデルレビュー）。
 */
export function switchStyle(style) {
  if (!VALID.has(style)) return;
  const saved = persistStyle(style);
  const url = new URL(location.href);
  if (saved) url.searchParams.delete("ui");
  else url.searchParams.set("ui", style);
  location.replace(url.toString());
}
