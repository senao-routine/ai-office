// スタイルの選択と永続化だけを持つ小さなモジュール。
// R50-P6: ドット絵スタイルはユーザー判断で一度撤去（2026-07-30）。R98-W1 で pixel として復活し、R99 で cockpit が加わった
// ＝現行は iso / pixel / cockpit の 3 様式（下の STYLES）。
// 追加スタイルを試すときはここへ登録して boot.html の動的 import に乗せる。
// R90-S1: 方向Cを ui/iso に統合。スタイルは1本、保存キーは既定切替時のまま維持する。
// R98-W1: 第 2 の様式 pixel（フロア帯 × 台帳）。既定は iso のまま・pixel は ?ui=pixel か切替ボタンで。
// R99: 第 3 の様式 cockpit（俯瞰オフィス × 台帳 × 端末 × 一覧）。R105 で既定にした（下の DEFAULT_STYLE）。
// R105（v3.0・本人裁定 2026-10-03「コックピットを含めて v3.0」）: 既定をコックピットへ。保存キーを v3 へ改名した＝
// v2 の保存値は「選んだ」記録ではなく、起動のたびに mount の成功で書いていた値（全員が iso）なので引き継がない。
// 以後の保存は ⚙「画面」で選んだときだけ（switchStyle）。?ui= で開いただけでは保存しない（boot.html）。
export const STYLES = Object.freeze({ APP: "app", ISO: "iso", PIXEL: "pixel", COCKPIT: "cockpit" });
export const DEFAULT_STYLE = STYLES.APP;
export const STYLE_KEY = "aioffice.ui.v3";
const VALID = new Set(Object.values(STYLES));

/** ?ui=<style> > localStorage > 既定(app) の順で解決する。不正値は既定へ落とす。 */
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
  return VALID.has(saved) ? saved : DEFAULT_STYLE;
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
 * R106-15: 設定モーダルの「画面」だけ、テーマ/言語と同じ見た目なのにフルリロードになる（押す前の見分けが無い）。
 * ui/platform は ui/hud（strings.js）を import しない掟＝文言は出さない。押した直後に誰でも分かる、言語に
 * 依らない印（待機カーソル・タブ見出しの「…」）を出す＝ここから先はページが作り直されるまで戻らない。
 */
export function switchStyle(style) {
  if (!VALID.has(style)) return;
  if (typeof document !== "undefined") {
    try { document.documentElement.style.cursor = "wait"; } catch { /* document が無い環境もある */ }
    try { document.title = `… ${document.title}`; } catch { /* 同上 */ }
  }
  const saved = persistStyle(style);
  const url = new URL(location.href);
  if (saved) url.searchParams.delete("ui");
  else url.searchParams.set("ui", style);
  location.replace(url.toString());
}
