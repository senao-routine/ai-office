// R98-W1: データの取り方（ポーリング・SSE・オフライン表示・鮮度・#attn= 深リンク・🎬デモ）は
// 様式に依らない。ui/iso/index.js から移設。
import { events, getOffice, poll } from "/ui/platform/api.js";
import { frozen, now } from "/ui/platform/clock.js";

/**
 * ctx: root（.offline を付ける先＝様式のルート。CSS は .hud.offline を見る。shell に付けると
 *      セレクタが永遠にマッチしない＝実際にサイレント故障していた）, shell, T, DEMO,
 *      apply(office), onOffline(offline)＝再描画など様式側の追随, tray, blocked()＝深リンクを無視する条件
 */
export function init({ root, shell, T, DEMO, apply, onOffline, tray, getWorld, blocked }) {
  let lastDataMono = null;
  const dataAge = (t) => (lastDataMono === null ? null : Math.max(0, Math.round(t - lastDataMono)));
  const offBar = shell.querySelector("#offbar");
  const stop = DEMO ? null : poll(
    getOffice, apply,
    (offline) => {
      root.classList.toggle("offline", Boolean(offline));
      if (offline) {
        const age = dataAge(now());
        offBar.textContent = age === null
          ? T("off_noconn")
          : T("off_stale", age < 90 ? T("ago_sec", age) : T("ago_min", Math.round(age / 60)));
      }
      offBar.hidden = !offline;
      onOffline?.(offline);
    },
    frozen ? 1e9 : 3000,          // 固定時刻のときはポーリングしない（スクショが揺れる）
  );
  const stopEvents = frozen || DEMO ? null : events(
    () => stop.refresh(),
    () => stop.setInterval(3000),
    () => {
      stop.setInterval(15000);
      stop.refresh();             // 接続・再接続時も最新のスナップショットを取得する。
    },
  );
  // 通知や URL の #attn=<session> から、その❗へ直接入る（初回データ到着時と hashchange）
  let hashApplied = false;
  const openAttentionHash = () => {
    if (frozen || DEMO || blocked?.() || !getWorld()) return;
    const match = /^#attn=(.+)$/.exec(location.hash);
    if (!match) return;
    let session;
    try { session = decodeURIComponent(match[1]); } catch { return; }
    tray.focusSession(session);
  };
  window.addEventListener("hashchange", openAttentionHash);
  return {
    /** apply() が呼ぶ: 「今更新された」の起点 */
    markData: () => { lastDataMono = now(); },
    dataAge,
    /** 初回データのときだけ #attn= を適用する（apply() の中から） */
    applyHashOnce: () => { if (!hashApplied) { hashApplied = true; openAttentionHash(); } },
    refresh: () => stop?.refresh?.(),
    intervalMs: () => stop?.intervalMs ?? null,
    /**
     * 🎬デモ: 同梱worldを1回だけ読む（ポーリングしない・実セッション不要）。
     * 読めなければライブを1回だけ取得して静かにフォールバック。
     * ※ apply が参照する HUD 初期化より後に呼ぶこと（初回描画のTDZを防ぐ）
     */
    loadDemo: async () => {
      try {
        const res = await fetch("/ui/demo/world.json", { headers: { "X-Office-Local": "1" } });
        if (res.ok) apply(await res.json());
      } catch { /* fallthrough */ }
      if (!getWorld()) {
        try { apply(await getOffice()); } catch { /* オフラインでも空画面のまま起動 */ }
      }
    },
    dispose: () => {
      stopEvents?.(); stop?.();
      window.removeEventListener("hashchange", openAttentionHash);
    },
  };
}
