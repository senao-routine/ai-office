// ❗トレイ・巡回・数字キー。描画とキー操作が同じキュー状態を共有する。
import { answerOutcome, attentionQueue, deliveryState } from "/ui/core/world.js";
import { isBot } from "/ui/core/kind.js";
import { attnLine } from "/ui/hud/board.js";
import { frozen, now } from "/ui/platform/clock.js";
import { deliveryChip } from "/ui/hud/delivery.js";

/**
 * ❗への答え方（トレイ・ダイジェスト・シートの唯一の正本）。
 *   allow:true   = 許可そのもの（承認フックへ allow・loopback+CSRF＝Mac の前の人間だけ）
 *   text         = 文章。承認フックが待っている相手には deny＋本文（＝「それはせず、こうして」）で届く
 *   compose:true = 指示欄を開く（comment:true はシートでも出す「差し戻す（コメント）」）
 * ★実バグ（R102-11）: トレイは掲示の種類を見ずに「承認して進める」の**文章**を出していた＝許可待ちに
 *   deny＋「そのまま進めて」が届き、計画（ExitPlanMode）は差し戻されて plan モードから抜けられなかった。
 *   掲示（ask）の無い ❗（推測）は指示ポストへ文章が届く＝従来の 3 本のまま。
 */
export function answerActs(agent, T, max = 3) {
  const ask = agent?.ask;
  if (ask?.tool === "ExitPlanMode") {
    return [{ label: T("opt_plan_approve"), allow: true },
      { label: T("opt_plan_back"), compose: true, comment: true }];
  }
  if (ask?.kind === "permission") {
    return [{ label: T("opt_allow"), allow: true },
      { label: T("opt_refuse"), text: T("opt_refuse_text") },
      { label: T("opt_free"), compose: true }];
  }
  const opts = agent?.questionOptions || [];
  // R80: 承認まちの定型は **スマホと完全に同じ3本・同じ本文**（どちらで答えても同じ文が届く）。
  // R128: 出勤の口の Bot の選択肢の無い相談には 3 本を出さない（スマホの R127 quickForSheet と同じ作法）＝答えは自由な文だけ。
  // 3 本はセッション向けの文（作業を進める・止めて報告）で、Bot の問いへの答えとしては口が選択肢にも解決できない＝聞かれていない「はい」が届く。
  const acts = opts.length
    ? opts.slice(0, max).map((o) => ({ label: o.label ?? o, text: T("opt_text", o.label ?? o) }))
    : isBot(agent) ? []
      : [{ label: T("opt_approve"), text: T("opt_approve_text") },
        { label: T("opt_pause"), text: T("opt_pause_text") },
        { label: T("opt_report"), text: T("opt_report_text") }];
  acts.push({ label: T("opt_free"), compose: true });
  return acts;
}

/**
 * R106-1: 長い許可待ちコマンドの後半が、省略記号もスクロールの手がかりも無いまま文の途中で切れる
 *（real-data-readonly-01・実測 clientHeight 72 / scrollHeight 143）。style.css は触らない（別の組の
 * 持ち場）＝ここでは inline style だけで既定のクランプ（#attn.tray > span の max-height:6.5em;
 * overflow-y:auto）を上書きする。はみ出している行だけ、既定を「視覚的な省略記号（…）付きの畳み」に替え、
 * 普通の <button>（キーボードの Tab→Enter/Space でも押せる）で全文を開く＝シートの「もっと見る」と
 * 同じ、畳む→展開の文法。はみ出していない短い行はここでは何もしない＝従来のまま。
 *
 * ★別モデルレビュー（高・R106-1）: 展開を maxHeight:none にすると、45 個連結のような実運用の長い
 *   コマンドで span が 1400px 超に伸び、後に続く許可/拒否ボタンが画面外へ押し出され、トレイの外側
 *   （#attn.tray）も overflow:visible（ui-cockpit の既定）でページ自体はスクロールしないため、ボタンに
 *   マウスで到達できなくなる（実測 viewport 900px・ボタン top=1580〜1618px）。既定の CSS（6.5em・
 *   overflow-y:auto）が守っていた「答えるボタンは必ず画面内に残す」という取引をここで壊していた。
 *   展開後も高さは無制限にせず、ビューポート基準の上限つきスクロールへ留める（ボタンより先に文が
 *   譲る＝span 自身の中でスクロールさせ、トレイの外へは広げない）。
 */
function paintTrayLineClamp(span, tray, T, el) {
  if (span.scrollHeight <= span.clientHeight + 1) return;      // はみ出していない＝クランプ不要
  let expanded = false;
  const apply = () => {
    if (expanded) {
      span.style.display = "block";
      span.style.webkitLineClamp = "unset";
      span.style.overflow = "auto";
      span.style.maxHeight = "40vh";    // 答えるボタンを画面外へ押し出さない上限（span 自身がスクロール）
    } else {
      span.style.display = "-webkit-box";
      span.style.webkitBoxOrient = "vertical";
      span.style.webkitLineClamp = "4";
      span.style.overflow = "hidden";
      span.style.maxHeight = "none";
    }
  };
  apply();
  const btn = el("button", "kbd trayexpand", T("tray_cmd_more"));
  btn.type = "button";
  btn.setAttribute("aria-expanded", "false");
  btn.addEventListener("click", () => {
    expanded = !expanded;
    apply();
    btn.setAttribute("aria-expanded", String(expanded));
    btn.textContent = T(expanded ? "tray_cmd_less" : "tray_cmd_more");
  });
  tray.append(btn);
}

/** ctx: shell, T, el(), attnKeyFor, getWorld(), render(), delivery, sheet, modals */
export function init({ shell, T, el, attnKeyFor, getWorld, render, DEMO = false,
  delivery: { send, allow, answeredKey, answeredAt, retryAnswer, isStalled }, sheet: { openCompose, closeCompose },
  modals: { closeModal } }) {
  let trayActions = [];
  let trayIndex = 0;
  // その人に合わせた（show・focusSession）ときの相手のセッション。キューの並びは approvalMin の昇格などで入れ替わる
  //＝番号（trayIndex）だけを持つと、合わせた人が黙って別の人に替わり、答えがその人へ届く。居る間は番号をこの人に
  // 追従させる。キューから居なくなったら外す（そこからは従来どおり番号のまま）。巡回（J/K）・先頭へ戻すときも外す。
  let pinned = null;
  const sheetEl = shell.querySelector("#sheet");
  const trayEl = shell.querySelector("#attn");
  const modalEl = shell.querySelector("#modalwrap");
  const syncLock = () => {
    const locked = !modalEl.hidden || !sheetEl.hidden;
    trayEl.classList.toggle("locked", locked);
    let lock = trayEl.querySelector(".traylock");
    if (locked && !lock) {
      lock = el("i", "traylock", T("tray_locked"));
      trayEl.append(lock);
    }
    if (lock) lock.hidden = !locked;
  };
  // モーダルは world の更新を伴わずに開くため、同じフレームで鍵の表示も追随させる。
  const lockObserver = new MutationObserver(syncLock);
  lockObserver.observe(sheetEl, { attributes: true, attributeFilter: ["hidden"] });
  lockObserver.observe(modalEl, { attributes: true, attributeFilter: ["hidden"] });
  let queueIds = new Set();
  const notifications = new Set();
  const notificationEnabled = () => {
    if (frozen || DEMO || shell._stream?.enabled || typeof Notification === "undefined") return false;
    try { return localStorage.getItem("aioffice.iso.notify") === "1" && Notification.permission === "granted"; }
    catch { return false; }
  };
  const notifyNew = (queue, quiet = null) => {
    const nextIds = new Set(queue.map((a) => a.id));
    if (document.hidden && notificationEnabled()) {
      for (const a of queue) {
        // R128 #4: 消音の Bot（world.muted）は画面の通知を出さない。控え（queueIds）には入れる＝解いても前からの相談では出さない
        if (queueIds.has(a.id) || quiet?.has(a.session)) continue;
        try {
          const note = new Notification(T("tray_head", a.name, 1, 1), {
            body: attnLine(T, a, getWorld()?.lang),
            tag: `aioffice-attn-${a.session}`,
          });
          notifications.add(note);
          note.onclose = () => notifications.delete(note);
          note.onclick = () => {
            window.focus();
            const hash = `#attn=${encodeURIComponent(a.session)}`;
            // 同じハッシュの通知をもう一度押しても、対象の会話に戻れる。
            if (location.hash === hash) focusSession(a.session);
            else location.hash = hash;
            note.close();
          };
        } catch { /* OS が通知を使えない場合も画面内の❗は保持する。 */ }
      }
    }
    queueIds = nextIds;
  };
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
      pinned = null;
      if (getWorld()) render();
    }
  };
  document.addEventListener("visibilitychange", onVis);

  // R106-8: 答えた直後に自動で次の❗へ進める 1 本の仕掛け。J/K・ロボ/タブ/一覧での選び直しは「本人がもう動かした」
  // ＝予約していた自動進行を奪わない（cycleTray・pick の先頭で毎回取り消す）。
  let advanceTimer = 0;
  const cancelAdvance = () => { if (advanceTimer) { clearTimeout(advanceTimer); advanceTimer = 0; } };
  const ANSWER_ADVANCE_MS = 700;   // 「回答済み」の一瞬の表示を見せてから進む長さ
  /** 答えた人の次に、まだ答えていない❗（巡回の並びで一番近い）へトレイを進める。他に残っていなければ動かさない
   *（答えた人の「回答済み・反映待ち」はそのまま＝サーバーが確定すればキューから消える・あなた待ち 0 の合図は別が出す）。 */
  const autoAdvance = (session, key) => {
    const w = getWorld();
    if (!w || answeredKey(session) !== key) return;         // 取り消された・答えが成立していない
    const queue = attentionQueue(w.agents || []);
    const at = queue.findIndex((a) => a.session === session);
    if (at < 0) return;                                      // もうキューに居ない（サーバーが先に確定した）
    // その間に本人が J/K・別の選択で動いていたら、予約していた自動進行は奪わない
    if (pinned ? pinned !== session : at !== trayIndex) return;
    const unanswered = (a) => a.session !== session && answeredKey(a.session) !== attnKeyFor(a);
    const ordered = [...queue.slice(at + 1), ...queue.slice(0, at + 1)];
    const next = ordered.find(unanswered);
    if (!next) return;                                       // 他に答えるべき❗は無い
    pinned = null;
    trayIndex = queue.indexOf(next);
    render();
  };
  const scheduleAdvance = (session, key) => {
    cancelAdvance();
    advanceTimer = setTimeout(() => { advanceTimer = 0; autoAdvance(session, key); }, ANSWER_ADVANCE_MS);
  };
  // ❗キューの巡回（J/K・▸次へ）。件数は render 時点の attentionQueue と同期する
  const cycleTray = (delta) => {
    cancelAdvance();
    const q = attentionQueue(getWorld()?.agents || []);
    if (q.length < 2) return;
    trayIndex = ((trayIndex + delta) % q.length + q.length) % q.length;
    pinned = null;
    if (getWorld()) render();
  };
  /**
   * 芯: トレイをその人の ❗ に合わせる（番号をキューのその人へ・居る間は追従）。❗ でない人は何もしない（false）。
   * 描き直し（render）は呼び手が決める＝show は合わせて描くだけ・focusSession は指示欄を開いてから描く。
   */
  const pick = (session) => {
    if (!session) return null;
    cancelAdvance();
    const queue = attentionQueue(getWorld()?.agents || []);
    const index = queue.findIndex((a) => a.session === session);
    if (index < 0) return null;
    trayIndex = index;
    pinned = session;
    return queue[index];
  };
  /**
   * その人の ❗ にトレイを合わせる**だけ**（部屋のロボ・タブ・一覧で選んだとき）。指示欄は開かない＝タッチの端末で
   * キーボードが上がらない・入力欄のフォーカスを奪わない。答えるのはトレイのボタン（数字キー）のまま。
   * 固定時刻・デモでも合わせてよい（送らない・開かない）。❗ でない人なら false（トレイはそのまま）。
   */
  const show = (session) => {
    if (!pick(session)) return false;
    if (getWorld()) render();
    return true;
  };
  /** 通知・#attn= の深リンク: その人に合わせて、指示欄まで開く（本物の操作なので固定時刻・デモ・配信では開かない） */
  const focusSession = (session) => {
    if (frozen || DEMO || shell._stream?.enabled) return false;
    const attn = pick(session);
    if (!attn) return false;
    shell._closeOverlays?.();   // ダイジェスト／リプレイが前面なら先に閉じる（inertOthers の解除）
    closeModal();
    openCompose(attn);
    if (getWorld()) render();
    return true;
  };
  // All entry points, including the digest, share this exact delivery route.
  const activate = (act) => {
    if (!act) return false;
    if (act.compose) { openCompose(act); return true; }
    if (act.allow) return allow(act.agent);       // 許可・計画の承認は文章でなく allow そのもの
    return send(act.session, act.name, act.text, act.attnKey, act.askId);
  };
  /** R106-8: 数字キー・クリック（本物の操作）専用。digest の answerFirst は素の activate のまま
   *（そちらは自分で先頭へ戻す進行を持つ＝二重に進めない）。「自由に書く」は進めない（本人が書く途中）。
   * 固定時刻（?t=）では進めない＝setTimeout は固定時刻の経過秒と無関係に本物の壁時計で動くので、
   * golden・スモークの決まった手順の途中に非同期で割り込んで並びを変えてしまう（実測で踏んだ）。 */
  const activateAndAdvance = (act) => {
    if (!act || act.compose || frozen) return activate(act);
    const { session, attnKey } = act;
    const result = activate(act);
    if (result && typeof result.then === "function") {
      result.then((ok) => { if (ok) scheduleAdvance(session, attnKey); });
    }
    return result;
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
      activateAndAdvance(trayActions[n - 1]);
    }
  };
  window.addEventListener("keydown", onKey);
  shell.querySelector("#attn").addEventListener("click", (e) => {
    if (e.target.closest(".trayprev")) { cycleTray(-1); return; }
    if (e.target.closest(".traynext")) { cycleTray(1); return; }
    const retry = e.target.closest(".trayretry");
    if (retry) { retryAnswer(retry.dataset.session); return; }
    const btn = e.target.closest("button[data-idx]");
    if (!btn) return;
    const act = trayActions[Number(btn.dataset.idx)];
    activateAndAdvance(act);
  });
  const paint = (w) => {
    // ❗キュー: 表示位置はこのインスタンスの状態（J/K・▸次へ で巡回）。縮んだら先頭へ戻す
    const queue = attentionQueue(w.agents);
    notifyNew(queue, w.muted instanceof Set ? w.muted : null);
    if (pinned) {
      const at = queue.findIndex((a) => a.session === pinned);
      if (at >= 0) trayIndex = at;       // 合わせた人が居る間は、並びが入れ替わってもその人を出し続ける
      else pinned = null;                // 答え終わって居なくなった＝ここからは番号のまま（従来どおり）
    }
    let ti = trayIndex;
    if (ti >= queue.length) {
      ti = 0;
      trayIndex = 0;
    }
    const attn = queue[ti] || null;
    // R54-A: タブタイトルに❗件数（別タブ作業中でも視界に入る）
    document.title = queue.length ? `(${queue.length}❗) AI Office` : "AI Office";

    const tray = shell.querySelector("#attn");
    const acts = [];
    // 描き直しは「出す中身」が変わったときだけ（R102-11）。apply は作業中 1 秒に数回走る＝毎回作り直すと
    // 押した瞬間（mousedown〜mouseup の間）にボタンが別ノードへ替わって click が消え、Tab のフォーカスも飛んだ。
    let sig = "";
    let line = "";
    let outcome = "";
    let stalled = false;
    let offline = false;
    if (attn) {
      offline = !!shell.closest(".offline");
      stalled = isStalled(attn.session);
      // 「1」で許可される操作そのもの（最古の掲示の道具と中身）を出す＝並列の呼び出しで掲示が 2 件並ぶときや、サブエージェントの
      // 掲示（stuckTool は親の Task）でも、書いてある操作と許可される操作を食い違わせない（別モデルレビュー）
      const perm = attn.ask?.kind === "permission";
      const many = Number(attn.ask?.askCount) > 1 ? ` (1/${Number(attn.ask.askCount)})` : "";
      // b4-mac: 種類を言う（許可待ち · Bash · deploy.sh staging · 6分 ／ 質問 · 2分 — … ／ 止まっている? · 6分 — 対象: …）
      line = attnLine(T, attn, w.lang) + (perm ? many : "");
      if (answeredKey(attn.session) === attnKeyFor(attn)) {
        // 回答済み・反映待ち: ボタンを出さない＝数字キーも無効（二重送信の窓を閉じる）
        outcome = answerOutcome(answeredAt(attn.session), now());
      } else {
        const base = { session: attn.session, name: attn.name, attnKey: attnKeyFor(attn),
          askId: attn.ask?.askId || "", agent: attn };   // R100-0: 表示した要求にだけ答える
        for (const a of answerActs(attn, T, 3)) acts.push({ ...base, ...a });
      }
      sig = JSON.stringify([attn.id, attn.session, T("tray_head", attn.name, ti + 1, queue.length),
        deliveryState(attn, { offline, stalled }), T("dstate_live"), line, outcome,
        outcome && T(outcome === "waiting" ? "tray_answered" : `tray_answered_${outcome}`), T("tray_retry"),
        acts.map((a) => [a.attnKey, a.askId, a.label]), queue.length > 1, T("tray_prev"), T("tray_next")]);
    }
    tray.hidden = !attn;
    if (sig !== tray._sig) {
      tray._sig = sig;
      tray.replaceChildren();
      if (attn) {
        const head = el("b", "trayhead", T("tray_head", attn.name, ti + 1, queue.length));
        head.append(deliveryChip({ T, agent: attn, offline, stalled }));
        const lineSpan = el("span", "trayline", line);
        tray.append(head, lineSpan);
        paintTrayLineClamp(lineSpan, tray, T, el);
        if (outcome) {
          tray.append(el("i", `trayans ${outcome}`, T(outcome === "waiting"
            ? "tray_answered" : `tray_answered_${outcome}`)));
          if (outcome === "unconfirmed") {
            const retry = el("button", "kbd trayretry", T("tray_retry"));
            retry.type = "button";
            retry.dataset.session = attn.session;
            tray.append(retry);
          }
        }
        acts.forEach((a, i) => {
          const btn = el("button", "kbd", `${i + 1} ${a.label}`);
          // R110-D2: 様式によってはラベルを 2 行で切る（cockpit の受付）＝全文はいつも title に置く
          btn.title = `${i + 1} ${a.label}`;
          btn.classList.toggle("go", !!a.allow);
          btn.type = "button";
          btn.dataset.idx = String(i);
          tray.append(btn);
        });
        if (queue.length > 1) {
          // 巡回導線（回答済み表示中も次へ進める＝残りを捌く手が止まらない）
          const prev = el("button", "kbd trayprev", T("tray_prev"));
          prev.type = "button";
          const next = el("button", "kbd traynext", T("tray_next"));
          next.type = "button";
          tray.append(prev, next);
        }
      }
    }
    trayActions = acts;
    syncLock();

    return attn?.id ?? null;          // 足元チップの強調対象を呼び手へ返す
  };
  return {
    render: paint,
    show,
    focusSession,
    first: () => {
      trayIndex = 0;
      pinned = null;
      const w = getWorld();
      if (!w) return [];
      paint(w);
      return trayActions.map((a) => ({ ...a }));
    },
    answerFirst: (index, expected) => {
      trayIndex = 0;
      pinned = null;
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
      lockObserver.disconnect();
      cancelAdvance();
      for (const note of notifications) note.close();
    },
  };
}
