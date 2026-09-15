// 投函・承認返信・配達の手応え・トースト。状態は mount ごとのインスタンスに閉じる。
import { deliveryState, deliveryTransitions, isMuted, stalledSends } from "/ui/core/world.js";
import { approvalReply, postInstruction } from "/ui/platform/api.js";
import { now } from "/ui/platform/clock.js";
import { deliveryChip } from "/ui/hud/delivery.js";

/** ctx: shell, T, DEMO, attnKeyFor, getWorld(), render() */
export function init({ shell, T, DEMO, attnKeyFor, getWorld, render }) {
  let sending = false;
  const composeInput = shell.querySelector("#composeinput");
  const answered = new Map();        // session -> {key: ❗内容キー, at: 回答時刻}
  let prevAgents = null;
  const recentSends = new Map();     // このUIから投函した時刻（単調秒）
  const sendStallNoted = new Set();
  const wakeUntil = new Map();
  const toastEl = shell.querySelector("#toast");
  // R67: トーストは直近2件の縦スタック（連続送信で前のメッセージが無告知で消えるのを根絶）。
  // #toast コンテナの hidden は ops_smoke の判定面なので維持（子0件のときだけ true）
  const showToast = (msg, ok = true, state = "") => {
    const t = document.createElement("div");
    t.className = `tmsg${ok ? "" : " err"}`;
    t.textContent = msg;
    if (state) t.append(" ", deliveryChip({ T, state, hint: state === "stalled" }));
    toastEl.append(t);
    while (toastEl.children.length > 2) toastEl.firstChild.remove();
    toastEl.hidden = false;
    requestAnimationFrame(() => t.classList.add("show"));
    setTimeout(() => {
      t.remove();
      if (!toastEl.children.length) toastEl.hidden = true;
    }, 2600);
  };
  const sendingChip = deliveryChip({ T, state: "sending" });
  sendingChip.id = "compose-state";
  sendingChip.setAttribute("role", "status");
  sendingChip.hidden = true;
  composeInput.parentElement.append(sendingChip);
  // R67: 送信中のUI状態（無反応の800msを可視化・入力とボタンを塞ぐ）
  const sendingUi = (on) => {
    composeInput.disabled = on;
    sendingChip.hidden = !on;
    sendingChip.querySelector(".dstate-label").textContent = T("dstate_sending");
    for (const b of shell.querySelectorAll("#quickdock button, #attn button")) {
      b.disabled = on;
    }
  };
  const send = async (session, name, text, attnKey = "") => {
    if (DEMO) { showToast(T("demo_no_send")); return false; }
    if (!session || !text) return false;
    if (sending) {
      // R67: 飛行中の2通目はサイレント破棄せず「送信中」を知らせる（実測: network到達1件で黙殺していた）
      showToast(T("dstate_sending"), false);
      return false;
    }
    sending = true;
    sendingUi(true);
    try {
      // 宛先はセッション完全一致だけで引く（crewrowで非代表セッションへ切り替えている場合、
      // 代表の listening を流用すると別人の状態で言い分けることになる＝黙って従来文言）
      const tgt = getWorld()?.agents?.find((x) => x.session === session);
      const answerKey = attnKey || (tgt?.attention ? attnKeyFor(tgt) : "");
      if (tgt?.ask) {
        // R86-H: いま人間に聞いていて止まっている相手。指示ポストはターンが終わるまで
        // 届かない（＝これが「承認しても届かない」の正体）ので、承認フックへ直接答える。
        await approvalReply(session, "deny", text);
        showToast(T("dstate_to", name), true, "pending");
      } else {
        await postInstruction(session, text);
        // R86-G: 届かない相手（ターンが終わらず Stop hook が起動していない）に
        // 「配達しました」と言わない。inbox には残るので、そのターミナルを触った瞬間に届く。
        const state = deliveryState(tgt);
        showToast(T("dstate_to", name), true, state === "live" ? "pending" : state);
      }
      recentSends.set(session, now());   // R53.2: 手応え（woke/answered）を出す対象に登録
      sendStallNoted.delete(session);
      if (answerKey) {
        // ❗への回答は「回答済み・反映待ち」を楽観表示し、二重送信の窓を即closeする
        // （実セッションでは transcript 反映まで❗が数十秒残るため）
        answered.set(session, { key: answerKey, at: now() });
        if (getWorld()) render();
      }
      return true;
    } catch (err) {
      showToast(T("deliver_fail", err.message), false);
      return false;
    } finally {
      sending = false;
      sendingUi(false);
    }
  };
  const allow = async (agent) => {
    if (DEMO) { showToast(T("demo_no_send")); return false; }
    if (sending) { showToast(T("dstate_sending"), false); return false; }
    sending = true;
    sendingUi(true);
    try {
      await approvalReply(agent.session, "allow", "");
      showToast(T("dstate_to", agent.name), true, "pending");
      answered.set(agent.session, { key: attnKeyFor(agent), at: now() });
      recentSends.set(agent.session, now());
      sendStallNoted.delete(agent.session);
      if (getWorld()) render();
      return true;
    } catch (e) {
      showToast(T("deliver_fail", String(e?.message || e)), false);
      return false;
    } finally {
      sending = false;
      sendingUi(false);
    }
  };
  // ↻再送は2クリック制（1回目=3秒のアーム表示・2回目で送信）。誤爆で同じ指示が飛ぶのを防ぐ
  let resendArm = { key: "", timer: 0 };
  const disarmResend = () => {
    clearTimeout(resendArm.timer);
    resendArm = { key: "", timer: 0 };
    for (const b of shell.querySelectorAll(".hresend.arm")) {
      b.classList.remove("arm");
      b.textContent = "↻";
    }
  };
  shell.querySelector("#hist").addEventListener("click", (e) => {
    const btn = e.target.closest(".hresend");
    if (!btn || !btn.dataset.session || !btn.dataset.text) return;
    const key = `${btn.dataset.session}|${btn.dataset.text}`;
    if (resendArm.key !== key) {
      disarmResend();
      resendArm = { key, timer: setTimeout(disarmResend, 3000) };
      btn.classList.add("arm");
      btn.textContent = T("resend_arm");
      return;
    }
    disarmResend();
    send(btn.dataset.session, btn.dataset.disp || T("resend_name"), btn.dataset.text);
  });

  const update = (w) => {
    // 回答済み(楽観表示)の解除: サーバーデータでその❗が消えた/内容が変わったら戻す
    for (const [sess, { key }] of answered) {
      const a = w.agents.find((x) => x.session === sess);
      if (!a || !a.attention || attnKeyFor(a) !== key) answered.delete(sess);
    }
    // R53.2 配達の手応え: 自分が最近投函した相手の「📨解消=動き出し」「❗解消=反映」を知らせる
    // （全遷移を鳴らすとノイズ＝このUI発の指示だけ・15分でトラッキング解除）
    for (const [sess, at] of recentSends) {
      if (now() - at > 900) recentSends.delete(sess);
    }
    for (const sess of sendStallNoted) {
      const agent = w.agents.find((a) => a.session === sess);
      if (!recentSends.has(sess) || !agent?.attention || !isMuted(agent)) sendStallNoted.delete(sess);
    }
    for (const tr of deliveryTransitions(prevAgents, w.agents)) {
      if (!recentSends.has(tr.session)) continue;
      showToast(T("dstate_to", tr.name), true,
        deliveryState(w.agents.find((a) => a.session === tr.session)));
      wakeUntil.set(tr.session, now() + 5);
      sendStallNoted.delete(tr.session);        // 動いたので「止まっている」通知の対象から外す
    }
    // R86-G: 送ったのに動かない相手を黙って放置しない（「押しても何も起きない」への回答）
    for (const st of stalledSends(recentSends, w.agents, now(), sendStallNoted)) {
      sendStallNoted.add(st.session);
      showToast(T("dstate_to", st.name), false, "stalled");
    }
    prevAgents = w.agents;
  };
  return {
    send, allow, showToast, update,
    answeredKey: (session) => answered.get(session)?.key || "",
    answeredAt: (session) => answered.get(session)?.at ?? null,
    retryAnswer: (session) => { answered.delete(session); if (getWorld()) render(); },
    isStalled: (session) => sendStallNoted.has(session),
    wakeActive: (session) => now() < (wakeUntil.get(session) || 0),
    dispose: disarmResend,
  };
}
