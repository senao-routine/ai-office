// ❗トレイ・巡回・数字キー。描画とキー操作が同じキュー状態を共有する。
import { attentionQueue, tidyActivity } from "/ui/core/world.js";

/** ctx: shell, T, el(), attnKeyFor, getWorld(), render(), delivery, sheet, modals */
export function init({ shell, T, el, attnKeyFor, getWorld, render,
  delivery: { send, answeredKey }, sheet: { openCompose, closeCompose },
  modals: { closeModal } }) {
  let trayActions = [];
  let trayIndex = 0;
  const sheetEl = shell.querySelector("#sheet");
  // R54-A: デスクトップ通知→タブへ戻ってきた瞬間、❗集合が変わっていれば最優先の1件を
  // トレイへ出し直す（「通知を見て開いたら該当❗が待っている」）。入力中の誤リセット無し
  let hiddenAttnIds = null;
  const onVis = () => {
    if (document.hidden) {
      hiddenAttnIds = new Set(attentionQueue(getWorld()?.agents || []).map((a) => a.id));
      return;
    }
    if (!hiddenAttnIds) return;
    const cur = attentionQueue(getWorld()?.agents || []);
    const changed = cur.length !== hiddenAttnIds.size ||
      cur.some((a) => !hiddenAttnIds.has(a.id));
    hiddenAttnIds = null;
    if (changed && cur.length) {
      trayIndex = 0;
      if (getWorld()) render();
    }
  };
  document.addEventListener("visibilitychange", onVis);

  // ❗キューの巡回（J/K・▸次へ）。件数は render 時点の attentionQueue と同期する
  const cycleTray = (delta) => {
    const q = attentionQueue(getWorld()?.agents || []);
    if (q.length < 2) return;
    trayIndex = ((trayIndex + delta) % q.length + q.length) % q.length;
    if (getWorld()) render();
  };
  // All entry points, including the digest, share this exact delivery route.
  const activate = (act) => {
    if (!act) return false;
    if (act.compose) { openCompose(act); return true; }
    return send(act.session, act.name, act.text, act.attnKey);
  };
  const onKey = (e) => {
    if (e.target.closest?.("input, textarea")) return;
    if (e.key === "Escape") {
      // R86-C: ⤢全画面（会話モーダル）はシートの上に重なるので、Escape は**上の一枚だけ**
      // 閉じる。両方閉じると「戻るつもりが会話ごと消える」（従来モーダルはサイドバー起点で
      // シートに重ならなかったため露見しなかった）。閉じ方は closeModal に一本化する。
      if (!shell.querySelector("#modalwrap").hidden) { closeModal(); return; }
      closeCompose();
      return;
    }
    // モーダル/シート表示中のキーは背後のトレイへ流さない（誤投函ガード）
    if (!shell.querySelector("#modalwrap").hidden || !sheetEl.hidden) return;
    if (e.key === "j" || e.key === "J") { cycleTray(1); return; }
    if (e.key === "k" || e.key === "K") { cycleTray(-1); return; }
    const n = Number.parseInt(e.key, 10);
    if (Number.isInteger(n) && n >= 1 && n <= trayActions.length) {
      activate(trayActions[n - 1]);
    }
  };
  window.addEventListener("keydown", onKey);
  shell.querySelector("#attn").addEventListener("click", (e) => {
    if (e.target.closest(".traynext")) { cycleTray(1); return; }
    const btn = e.target.closest("button[data-idx]");
    if (!btn) return;
    const act = trayActions[Number(btn.dataset.idx)];
    activate(act);
  });
  const paint = (w) => {
    // ❗キュー: 表示位置はこのインスタンスの状態（J/K・▸次へ で巡回）。縮んだら先頭へ戻す
    const queue = attentionQueue(w.agents);
    let ti = trayIndex;
    if (ti >= queue.length) {
      ti = 0;
      trayIndex = 0;
    }
    const attn = queue[ti] || null;
    // R54-A: タブタイトルに❗件数（別タブ作業中でも視界に入る）
    document.title = queue.length ? `(${queue.length}❗) AI Office` : "AI Office";

    const tray = shell.querySelector("#attn");
    tray.replaceChildren();
    tray.hidden = !attn;
    const acts = [];
    if (attn) {
      tray.append(el("b", "", T("tray_head", attn.name, ti + 1, queue.length)),
        el("span", "", attn.question
          || T("approval_min", attn.approvalMin)
            + (attn.stuckTool ? ` — ${T("attn_target", tidyActivity(attn.stuckTool, 40))}` : "")));
      const isAnswered = answeredKey(attn.session) === attnKeyFor(attn);
      if (isAnswered) {
        // 回答済み・反映待ち: ボタンを出さない＝数字キーも無効（二重送信の窓を閉じる）
        tray.append(el("i", "trayans", T("tray_answered")));
      } else {
        const base = { session: attn.session, name: attn.name, attnKey: attnKeyFor(attn) };
        if ((attn.questionOptions || []).length) {
          attn.questionOptions.slice(0, 3).forEach((o) => {
            const label = o.label ?? o;
            acts.push({ ...base, label, text: T("opt_text", label) });
          });
        } else {
          // R80: 承認まちの定型は **スマホと完全に同じ3本・同じ本文**。
          // 従来はMac 2本/スマホ 3本で、同じ「承認」でも送られる日本語が違った
          // （どちらで答えたかによってセッションが受け取る文が変わる状態だった）。
          acts.push({ ...base, label: T("opt_approve"), text: T("opt_approve_text") });
          acts.push({ ...base, label: T("opt_pause"), text: T("opt_pause_text") });
          acts.push({ ...base, label: T("opt_report"), text: T("opt_report_text") });
        }
        acts.push({ ...base, label: T("opt_free"), compose: true });
        acts.forEach((a, i) => {
          const btn = el("button", "kbd", `${i + 1} ${a.label}`);
          btn.type = "button";
          btn.dataset.idx = String(i);
          tray.append(btn);
        });
      }
      if (queue.length > 1) {
        // 巡回導線（回答済み表示中も次へ進める＝残りを捌く手が止まらない）
        const next = el("button", "kbd traynext", T("tray_next"));
        next.type = "button";
        tray.append(next);
      }
    }
    trayActions = acts;

    return attn?.id ?? null;          // 足元チップの強調対象を呼び手へ返す
  };
  return {
    render: paint,
    first: () => {
      trayIndex = 0;
      const w = getWorld();
      if (!w) return [];
      paint(w);
      return trayActions.map((a) => ({ ...a }));
    },
    answerFirst: (index, expected) => {
      trayIndex = 0;
      const w = getWorld();
      if (!w) return false;
      paint(w);
      const act = trayActions[index];
      // A question can change between painting and keydown: never answer its replacement.
      if (!act || !expected || act.session !== expected.session || act.attnKey !== expected.attnKey
          || act.label !== expected.label) return false;
      return activate(act);
    },
    dispose: () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("visibilitychange", onVis);
    },
  };
}
