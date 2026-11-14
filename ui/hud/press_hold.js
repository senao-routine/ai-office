// 押している間は描き直さない（2026-11-14・R127／R128 の配備の後に本番で自分の Bot を使って見つけた）。
// 中央の記録（ui/hud/activity.js）は /api/office が届くたびに丸ごと作り直す（本番で約 1 秒ごと）。ブラウザは、押した要素が離す前に
// 文書から外れるとクリックを出さない＝押している最中に作り直しが来ると、答えのボタンの押下が黙って消えていた
// （本番で 1.5 秒押して離すと答えが届かない・普通の速さのクリックでも 1 割前後）。
// 押している間（host の中の pointerdown 〜 pointerup／pointercancel／窓の blur）は描き直しを 1 回分だけためて、離したあとのタスクで描く。
// click は pointerup と同じ入力のタスクの中で配られる＝setTimeout(0) の描き直しは必ずその後（クリックが先に届く）。
// 離した合図が来ないとき（窓の外で離した・合図の取りこぼし）に描き直しが止まり続けないよう、maxMs で離したことにする
// （時刻は読まない＝ui/platform/clock.js の掟。タイマーで解く）。
export const PRESS_HOLD_MAX_MS = 10000;

export function createPressHold(host, { win = host?.ownerDocument?.defaultView || globalThis, maxMs = PRESS_HOLD_MAX_MS } = {}) {
  let pressed = false;
  let pending = null;
  let guard = 0;
  const release = () => {
    if (!pressed) return;
    pressed = false;
    win.clearTimeout(guard);
    guard = 0;
    const run = pending;
    pending = null;
    if (run) win.setTimeout(run, 0);
  };
  const down = (event) => {
    if (event && Number.isFinite(event.button) && event.button !== 0) return;   // 右・中ボタンは押下にしない（メニューで離す合図が来ないことがある）
    pressed = true;
    win.clearTimeout(guard);
    guard = win.setTimeout(release, maxMs);
  };
  host.addEventListener("pointerdown", down);
  win.addEventListener("pointerup", release, true);
  win.addEventListener("pointercancel", release, true);
  win.addEventListener("blur", release);
  return {
    /** 押している間なら run を最後の 1 回としてためて true（呼び手は描かずに戻る）。押していなければ false＝今すぐ描く */
    defer(run) {
      if (!pressed) return false;
      pending = run;
      return true;
    },
    pressed: () => pressed,
    dispose() {
      host.removeEventListener("pointerdown", down);
      win.removeEventListener("pointerup", release, true);
      win.removeEventListener("pointercancel", release, true);
      win.removeEventListener("blur", release);
      win.clearTimeout(guard);
      pressed = false;
      pending = null;
    },
  };
}
