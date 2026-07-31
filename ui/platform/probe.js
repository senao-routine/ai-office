// ──────────────────────────────────────────────────────────────
// テストからの唯一の観測点・注入点。
//
// なぜ必要か（重要）:
//   新UIは <script type="module"> で動くので、旧UIのように
//   page.evaluate("EMPS = [...]; syncActors()") でグローバルへ代入しても
//   **モジュールスコープの変数には届かない**。代入は誰も読まない
//   暗黙グローバルになり、テストは何も検証しないまま green になる。
//   （旧 tests/ui_smoke.py は実際に11個のグローバルへ代入している）
//   だから注入は必ずこの明示APIを通す。
//
// 契約: 両スタイルが同じ形の window.__office を出すこと。
//       tests/ui_contract.py がこれを突き合わせる。
// ──────────────────────────────────────────────────────────────
export { summarizeWorld } from "/ui/core/world.js";

export const PROBE_VERSION = 1;

export function installProbe({ style, dumpWorld, inject, t, isReady, stats, debug }) {
  const probe = {
    version: PROBE_VERSION,
    style,
    /** 現在のワールドを「意味」に落として返す（スタイル非依存の形） */
    dumpWorld,
    /** office_json 相当を直接流し込む。ネットワークを介さない。 */
    inject,
    /** UIクロックの現在時刻（秒） */
    t,
    /** 描画の実測値（drawCalls 等）。性能ゲートが読む。 */
    stats: stats || (() => null),
    /** テスト照準用の追加観測点（スタイル固有・契約外＝ui_contract は比較しない）。
        例: iso の agentPoint(id)＝ロボット胴のスクリーン座標（クリック座標の暗算をしない掟）。 */
    debug: debug || null,
  };
  // ready は「マウント済み **かつ** 最初のデータが届いた」を意味する。
  // 単なる true にすると、テストがデータ到着前に dumpWorld() を読んで
  // null を掴み、何も検証しないまま通ってしまう（実際に踏んだ）。
  Object.defineProperty(probe, "ready", {
    get: () => (typeof isReady === "function" ? Boolean(isReady()) : true),
    enumerable: true,
  });
  window.__office = probe;
  return () => {
    if (window.__office === probe) window.__office = null;
  };
}

// summarizeWorld は純ロジックなので ui/core/world.js が正本（上で再エクスポート）。
