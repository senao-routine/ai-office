// 会話のオンデマンド取得・深掘り・全画面表示。
import { getDialog } from "/ui/platform/api.js";

/** ctx: T, el(), modals */
export function init({ T, el: sEl, modals: { modal, openModal } }) {
  // 💬 R86-B/C: 会話のオンデマンド取得。stale ガード＝取得中に宛先が切り替わったら破棄。
  // R86-C の教訓: 「もっと見る」をリスト内に置くと、最新へのピン留め
  // （scrollTop=scrollHeight）でボタンが可視域の遥か上へ流れ**恒常的に押せない**（実測 -706px）。
  // ボタンは必ず**見出し行**に置く＝スクロール位置に依存せず常に見える。
  let dlgSeq = 0;
  const dlgMsgEl = (m) =>
    sEl("div", `dlgmsg ${m.role === "user" ? "user" : "ai"}`, m.text);

  const openDialogModal = (msgs) => {
    modal.replaceChildren(sEl("b", "mtitle", T("dialog_modal_head", msgs.length)));
    const box = sEl("div", "dlgfull");
    for (const m of msgs) box.append(dlgMsgEl(m));
    modal.append(box);
    openModal();
    box.scrollTop = box.scrollHeight;
  };

  const keyOf = (m) => `${m.role === "user" ? "u" : "a"}${m.text}`;
  /** 「↓ 新着 N」（読み直しで新しい発言が来たが、本人が上を読んでいる＝位置を奪わない）。押すか下端まで来たら消える */
  const showNew = (listEl, n) => {
    listEl.querySelector(".dlgnew")?.remove();
    if (!(n > 0)) return;
    const b = sEl("button", "dlgnew", T("dialog_new", n));
    b.type = "button";
    b.addEventListener("click", () => { listEl.scrollTop = listEl.scrollHeight; b.remove(); });
    listEl.append(b);
    if (!listEl.dataset.dlgnewWired) {
      listEl.dataset.dlgnewWired = "1";
      listEl.addEventListener("scroll", () => {
        if (listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight < 24) listEl.querySelector(".dlgnew")?.remove();
      }, { passive: true });
    }
  };

  /**
   * b4-mac: 読み直し（同じ相手・いまの深さのまま）。新しい発言が無ければ DOM に触らない。下端を見ていたら下端へ追従し、
   * 上を読んでいたら位置を保って「↓ 新着 N」を出す。失敗は黙る（読めている会話を消さない）。
   */
  const refreshDialog = async (session, listEl, headEl) => {
    const seq = ++dlgSeq;
    const depth = Number(listEl.dataset.depth) || 0;
    const atBottom = listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight < 24;
    const top = listEl.scrollTop;
    const before = [...listEl.querySelectorAll(".dlgmsg")].map(
      (n) => keyOf({ role: n.classList.contains("user") ? "user" : "ai", text: n.textContent }));
    let res;
    try {
      res = await getDialog(session, depth);
    } catch {
      return;
    }
    if (seq !== dlgSeq) return;
    const msgs = res.messages || [];
    const after = msgs.map(keyOf);
    if (after.length === before.length && after.every((k, i) => k === before[i])) return;
    const last = before[before.length - 1];
    const at = last === undefined ? -1 : after.lastIndexOf(last);
    const fresh = at >= 0 ? after.length - 1 - at : after.length;
    listEl.replaceChildren();
    if (!msgs.length) listEl.append(sEl("div", "dlgnote", T("dialog_empty")));
    else for (const m of msgs) listEl.append(dlgMsgEl(m));
    listEl.dataset.depth = String(res.depth ?? depth);
    paintDlgHead(headEl, listEl, session, res);
    if (atBottom) listEl.scrollTop = listEl.scrollHeight;
    else {
      listEl.scrollTop = top;
      showNew(listEl, fresh);
    }
  };

  const loadDialog = async (session, listEl, headEl, depth = 0, { refresh = false } = {}) => {
    if (refresh) return refreshDialog(session, listEl, headEl);
    const seq = ++dlgSeq;
    // R86-C: 復元アンカーは**件数索引**で取る。本文一致だと「はい」「ok」や 400字クランプで
    // 先頭が同じ長文が古い側にもあると誤爆して会話の最古部へ飛ぶ（実測）。
    // サーバーは「深い応答は浅い応答の suffix」を保証（test_deeper_depth_is_superset_suffix）。
    const prevCount = depth > 0 ? listEl.querySelectorAll(".dlgmsg").length : 0;
    const btn = headEl?.querySelector(".dlgmore");
    if (btn) { btn.disabled = true; btn.textContent = T("loading"); }   // 連打ガード
    if (!prevCount) listEl.replaceChildren(sEl("div", "dlgnote", T("loading")));
    let res;
    try {
      res = await getDialog(session, depth);
    } catch {
      if (seq !== dlgSeq) return;
      if (prevCount) {
        // 深掘りの失敗は**読めている会話を消さない**（daemon再起動中や瞬断で踏む。
        // 消すと閉じて開き直す以外に戻る手段が無くなる）。ボタンを押せる状態へ戻すだけ。
        if (btn) { btn.disabled = false; btn.textContent = T("dialog_older"); }
        return;
      }
      listEl.replaceChildren(sEl("div", "dlgnote", T("dialog_err")));
      paintDlgHead(headEl, listEl, session, { messages: [], hasMore: false, depth });
      return;
    }
    if (seq !== dlgSeq) return;                     // 取得中に宛先が変わった＝破棄
    const msgs = res.messages || [];
    listEl.replaceChildren();
    listEl.dataset.depth = String(res.depth ?? depth);
    if (!msgs.length) {
      listEl.append(sEl("div", "dlgnote", T("dialog_empty")));
    } else {
      for (const m of msgs) listEl.append(dlgMsgEl(m));
    }
    paintDlgHead(headEl, listEl, session, res);
    if (prevCount) {
      // 読んでいた位置を失わない: 押す前に先頭だった1件（=末尾から prevCount 番目）を上端へ
      const nodes = [...listEl.querySelectorAll(".dlgmsg")];
      const anchor = nodes[nodes.length - prevCount];
      if (anchor) {
        listEl.scrollTop +=
          anchor.getBoundingClientRect().top - listEl.getBoundingClientRect().top;
      }
    } else {
      listEl.scrollTop = listEl.scrollHeight;       // 初回は最新（下端）を見せる
    }
  };

  /** 見出し行（件数バッジ・もっと見る・全画面）を今の状態で貼り直す。 */
  function paintDlgHead(headEl, listEl, session, res) {
    if (!headEl) return;
    const msgs = res.messages || [...listEl.querySelectorAll(".dlgmsg")].map(
      (n) => ({ role: n.classList.contains("user") ? "user" : "ai", text: n.textContent }));
    for (const n of [...headEl.children]) n.remove();   // 件数/ボタンを一旦落とす（B4: 残留防止）
    // 件数 0 は出さない（足元に「0」だけが残っていた）
    if (msgs.length) headEl.append(sEl("i", "seccount", String(msgs.length)));
    if (msgs.length) {
      const exp = sEl("button", "dlgexpand", T("dialog_expand"));
      exp.type = "button";
      exp.addEventListener("click", () => openDialogModal(msgs));
      headEl.append(exp);
    }
    if (res.hasMore && (res.depth ?? 0) < (res.maxDepth ?? 0)) {
      const more = sEl("button", "dlgmore", T("dialog_older"));
      more.type = "button";
      more.addEventListener("click", () =>
        loadDialog(session, listEl, headEl, (res.depth ?? 0) + 1));
      headEl.append(more);
    } else if (res.hasMore) {
      headEl.append(sEl("i", "dlgend", T("dialog_oldest")));   // 最深＝これ以上は無い
    }
  }

  return { loadDialog };
}
