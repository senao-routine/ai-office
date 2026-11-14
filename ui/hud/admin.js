// 新プロジェクト・起動・ペアリング・レシピ・設定・リソースの管理画面。
import { STYLES, resolveStyle, switchStyle } from "/ui/platform/style.js";
import { frozen } from "/ui/platform/clock.js";
import { agoStr } from "/ui/core/world.js";
import { renderBotPanel } from "/ui/hud/bots.js";
import {
  budgetApply, fxApply, getKeysStatus, getRecipes, getStatusBoard, getTemplates,
  launchProject, newProject, pairExtend, pairList, pairNew, pairRevoke, pickProjectFolder, setDialogRelay,
  approveStatus, setRemoteApprove, approveTicket, approveRevoke,
  setOfficeKey, setRecipes, setServerLang, setTemplates, spendApply, testProvider,
} from "/ui/platform/api.js";

/** ctx: root, shell, T, lang, setLang, getWorld(), applyStaticStrings(),
 *  showToast(), billingOf(), fmtTok(), modals */
export function init({ root, shell, T, lang, setLang, getWorld, applyStaticStrings, demo = false,
  reapplyWorld = () => {},
  showToast, billingOf, fmtTok, renderStreamSettings = () => {}, renderCustomizationSettings = () => {}, modals: { modal, openModal, closeModal, mEl },
  theme = null, version = () => null,
  // R110-S6: 様式が「表示」の節（テーマ・画面の近く）へ足す行の差し込み口。renderDisplaySettings(seg, repaint, note)＝
  // note(text) はその節に注記の 1 行を足す。渡さない様式は何も足さない（cockpit＝文字の大きさ・環境音）
  renderDisplaySettings = () => {} }) {
  const cockpit = root.classList.contains("ui-cockpit");
  // R82: 定型文エディタ（8件×120字・保存でスマホにも同期）
  let TEMPLATES = [];
  let recipeFolder = "";
  const refreshTemplates = async () => {
    // R97-C: デモ（静的ホスト）ではサーバーが無い。同梱 world の templates を使い、API は叩かない
    if (demo) { TEMPLATES = getWorld()?.templates || []; return; }
    try { TEMPLATES = (await getTemplates())?.templates || []; } catch { /* 未対応サーバーでも動く */ }
  };
  refreshTemplates();
  const openTemplateEditor = (reply = "") => {
    const draft = TEMPLATES.map((tp) => ({ ...tp }));
    const paint = () => {
      modal.replaceChildren(mEl("b", "mtitle", T("tpl_title")),
        mEl("p", "mnote", T("tpl_note")));
      const existing = mEl("details", "tpl-existing");
      existing.append(mEl("summary", "", T("tpl_existing", draft.length)));
      draft.forEach((tp, i2) => {
        const row = mEl("div", "tplrow");
        row.append(mEl("b", "", tp.label), mEl("span", "tpltext", tp.text));
        const del = mEl("button", "tpldel", T("tpl_del"));
        del.type = "button";
        del.addEventListener("click", () => { draft.splice(i2, 1); paint(); });
        row.append(del);
        existing.append(row);
      });
      modal.append(existing);
      const li = mEl("input", "minput");
      li.type = "text"; li.placeholder = T("tpl_label_ph"); li.maxLength = 20;
      const ti = mEl("input", "minput");
      ti.type = "text"; ti.placeholder = T("tpl_text_ph"); ti.maxLength = 120;
      li.value = String(reply).trim().slice(0, 20);
      ti.value = String(reply).trim().slice(0, 120);
      li.setAttribute("aria-label", T("tpl_label_ph")); ti.setAttribute("aria-label", T("tpl_text_ph"));
      const add = mEl("button", "sub tpladdrow", T("tpl_add_row"));
      add.type = "button";
      add.addEventListener("click", () => {
        if (!li.value.trim() || !ti.value.trim() || draft.length >= 8) return;
        draft.push({ label: li.value.trim(), text: ti.value.trim() });
        reply = ""; paint();
      });
      const save = mEl("button", "mgo", T("tpl_save"));
      save.type = "button";
      save.addEventListener("click", async () => {
        try {
          const next = draft.map((tp) => ({ ...tp }));
          if (li.value.trim() || ti.value.trim()) {
            const item = { label: li.value.trim(), text: ti.value.trim() };
            const same = next.findIndex((tp) => tp.text === item.text);
            if (!item.label || !item.text || same < 0 && next.length >= 8) {
              showToast(T("tpl_invalid"), false); return;
            }
            if (same >= 0) next[same] = item;
            else next.push(item);
          }
          await setTemplates(next);
          TEMPLATES = next;
          showToast(T("tpl_saved"));
          closeModal();
        } catch (err) {
          showToast(T("reg_fail", err.message), false);
        }
      });
      modal.append(li, ti, add, save);
    };
    paint();
    openModal();
  };
  shell.querySelector("#btn-newproj").addEventListener("click", async () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_newproj")),
      mEl("p", "mnote", T("np_opening")));
    openModal();
    let picked;
    try {
      picked = await pickProjectFolder();
      recipeFolder = picked.path || recipeFolder;
    } catch (err) {
      modal.replaceChildren(mEl("b", "mtitle", T("btn_newproj")),
        mEl("p", "mnote merr", err.message));
      return;
    }
    modal.replaceChildren();
    modal.append(mEl("b", "mtitle", T("btn_newproj")));
    modal.append(mEl("p", "mpath", picked.path));
    const nameIn = mEl("input", "minput");
    nameIn.type = "text";
    nameIn.value = picked.suggest || "";
    nameIn.placeholder = T("np_name_ph");
    modal.append(nameIn);
    const optLaunch = mEl("label", "mopt");
    const cbLaunch = mEl("input"); cbLaunch.type = "checkbox"; cbLaunch.checked = true;
    optLaunch.append(cbLaunch, document.createTextNode(T("np_launch")));
    modal.append(optLaunch);
    const go = mEl("button", "mgo", T("np_go"));
    go.type = "button";
    go.id = "mgo-newproj";
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        // 2026-09-08: サーバは `launched` を返すのに、UI が結果を見ずに常に
        // 「出勤しました」と言っていた。起動をチェックしても Terminal が上がらないことがある
        // （Automation の TCC 同意待ち・claude 未インストール）ので、**登録できただけ**のときに
        // 出勤したと言うと、空のオフィスを見て行き止まりになる。何が起きたかを分けて伝える。
        const res = await newProject(picked.path, nameIn.value.trim(),
          { launch: cbLaunch.checked });
        const label = nameIn.value.trim() || picked.suggest;
        if (cbLaunch.checked && res && res.launched === false) {
          showToast(`${T("np_registered", label)} — ${T("np_launch_failed")}`, false);
        } else {
          showToast(T(cbLaunch.checked ? "np_joined" : "np_registered", label));
        }
        closeModal();
      } catch (err) {
        go.disabled = false;
        showToast(T("reg_fail", err.message), false);
      }
    });
    modal.append(go);
    nameIn.focus();
  });

  const renderDialogSetting = (target, id, { dialogRelay, dialogAvailable }) => {
    // b4-mac: チェックボックスの行は .mcheck（左寄せ・gap 8）。.mdev（端末の行）の space-between に当たって、
    // 箱が左端・文が右端に分かれていた
    const row = mEl("label", "mcheck");
    const input = mEl("input");
    input.type = "checkbox";
    input.id = id;
    input.checked = dialogRelay === true;
    input.disabled = dialogAvailable === false;
    row.append(input, mEl("span", "", T("pair_dialog_toggle")));
    input.addEventListener("change", async () => {
      const enabled = input.checked;
      input.disabled = true;
      try {
        await setDialogRelay(enabled);
        showToast(T(enabled ? "pair_dialog_on" : "pair_dialog_off"));
      } catch (err) {
        input.checked = !enabled;
        showToast(err.message, false);
      }
      input.disabled = dialogAvailable === false;
    });
    target.append(row, mEl("p", "mnote",
      dialogAvailable === false ? T("pair_dialog_unavailable") : T("pair_dialog_note")));
  };
  // R100-1b: スマホから許可。Mac から ON にするときは Mac の物理確認。
  // R118（本人裁定 2026-10-13「Claude のスマホアプリと同じく、サクッと許可」「常に ON」）: ON は期限なし（OFF にするまで）＝ボタンは 1 つ。
  // 境界は不変条件 7（R118）: ペアリングしたスマホは段を問わずタップ 1 回（危険な種類はカードに朱の 1 行）・読み切れない形は Mac だけ。
  // 許可鍵（パスキー）は使わない（下の renderApproveKey は表示しない）。押した結果はこの欄にその場で出す（トーストは一瞬で消える）。
  // ON は Mac に確認のダイアログ＝押すまでの案内も出す。
  // b4-mac: 日付は画面の言語で整形する（端末のロケールに任せると英語 UI に和文の日付・和文 UI に「10/1, 21:14」が出た）
  const locale = () => (lang() === "en" ? "en-US" : "ja-JP");
  const stamp = (t) => new Date(t * 1000).toLocaleString(locale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  const day = (t) => new Date(t * 1000).toLocaleDateString(locale(), { month: "numeric", day: "numeric" });
  // Astra（4f880e4・medium）: R118 より前に 1/4/12 時間で ON にした値（until が 2100 年＝REMOTE_ALWAYS より前）は移さない＝その期限で OFF になる。
  // 「期限なし」と言わず実際の期限を出す（PWA の apvNowLine と同じ区別）
  const APV_ALWAYS = 4102444800;
  const apvStateText = (until) => (until >= APV_ALWAYS ? T("apv_on") : until > Date.now() / 1000 ? T("apv_on_until", stamp(until)) : T("apv_off"));
  const mmss = (sec) => {
    const s2 = Math.max(0, Math.floor(sec));
    return `${Math.floor(s2 / 60)}:${String(s2 % 60).padStart(2, "0")}`;
  };
  const renderApproveSetting = (target, st) => {
    setApvUntil(st.until);
    const box = mEl("div", "mapprove");
    box.id = "pair-approve";
    box.append(mEl("b", "msub", T("apv_head")));
    const state = mEl("p", "mnote", apvStateText(st.until));
    state.id = "apv-state";
    box.append(state);
    const row = mEl("div", "mrow");
    const usable = () => Boolean(st.relay && st.available);
    // ON は副ボタン（主ボタンの塗りは「＋ 新しいスマホを登録」だけ）・押されているかは aria-pressed
    const on = mEl("button", "mbtn", T("apv_on_btn"));
    on.type = "button";
    on.id = "apv-on";
    on.disabled = !usable();
    const pressed = (until) => on.setAttribute("aria-pressed", String(until > Date.now() / 1000));
    on.addEventListener("click", async () => {
      on.disabled = true;
      state.className = "mnote";
      state.textContent = T("apv_confirming");
      try {
        const res = await setRemoteApprove(true);
        state.textContent = apvStateText(res.until);
        setApvUntil(res.until);
        pressed(res.until);
        showToast(T("apv_turned_on"));
      } catch (err) {
        state.className = "mnote merr";
        state.textContent = `${err.message} ${T("apv_confirm_hint")}`;
        showToast(err.message, false);
      }
      on.disabled = !usable();
    });
    row.append(on);
    const off = mEl("button", "mbtn", T("apv_off_btn"));
    off.type = "button";
    off.id = "apv-off";
    off.addEventListener("click", async () => {
      try {
        await setRemoteApprove(false);
        state.className = "mnote";
        state.textContent = T("apv_off");
        setApvUntil(0);
        pressed(0);
        showToast(T("apv_turned_off"));
      } catch (err) {
        state.className = "mnote merr";
        state.textContent = err.message;
        showToast(err.message, false);
      }
    });
    row.append(off);
    pressed(st.until);
    // 押せない理由をこの欄で言う（中継が無い）。この欄では端末を発行していない＝pair_norelay は流用しない
    box.append(row, mEl("p", "mnote", !st.relay ? T("apv_norelay") : T("apv_note")));
    const td = st.today || {};
    const why = Object.entries(td.mac || {}).sort((a, b) => b[1] - a[1]).slice(0, 4)
      .map(([k, n]) => `${T("apv_reason", k)} ${n}`).join(" · ");
    box.append(mEl("p", "mnote", T("apv_today", td.remote || 0, td.asked || 0, why)));
    target.append(box);
  };
  const renderApproveKey = (row, d, st) => {
    const k = (st.keys || {})[d.device_id];
    if (d.state !== "active") return;
    // R118: 許可鍵（パスキー）は使わない＝端末ごとの「許可鍵 fp」「許可鍵を失効」は出さない（スマホを止めるのは端末の失効か OFF）
    if (k && k.active) return;
    const add = mEl("button", "mgo mgosm apv-key-add", T("apv_key_add"));
    add.type = "button";
    // R116-S0: Mac 側の登録の受付（10 分のチケット・renderEnroll）は R103-1 以降使われない（登録はスマホだけで完結・即時有効）
    // ＝画面から隠す。撤去は R116-S3（API・renderEnroll・ui_admin_smoke の段階の検査は残す）。R118 から許可鍵そのものを使わない。
    add.hidden = true;
    add.addEventListener("click", async () => {
      const prevCreated = Number(k?.created) || 0;
      let t;
      try {
        t = await approveTicket(d.device_id);
      } catch (err) {
        showToast(err.message, false);
        return;
      }
      renderEnroll(d, t, prevCreated);
    });
    row.append(add);
  };
  /**
   * R116-S0: 入口（「許可鍵を登録」）は Mac の画面から隠した＝R103-1 以降は使われない経路（撤去は R116-S3）。
   * b4-mac: 許可鍵の登録中の画面。受付が開いている間は 2 秒ごとに状態を読み、どの段か（受付中・スマホで「登録」待ち・
   * Mac の確認待ち）と結果（登録できた・取り消された・受付が閉じた）を出す。以前は長い 1 段落のまま何も変わらず、
   * 成功したかは「戻る」で一覧を見るまで分からなかった（R102-7 の実機で、確認ダイアログが押されずに閉じて詰んだ一因）。
   * R102-9: QR は出さない（iPhone のカメラで読むと Safari で開き、ホーム画面のアプリには届かない）。リンクのコピーは予備。
   */
  const renderEnroll = (d, t, prevCreated) => {
    pairGen += 1;
    const gen = pairGen;
    const started = Date.now() / 1000;
    const name = d.label || d.device_id;
    modal.replaceChildren(mEl("b", "mtitle", T("apv_enroll_head", name)));
    const steps = mEl("ol", "msteps");
    steps.id = "apv-enroll-steps";
    const s1 = mEl("li", "", "");
    const s2 = mEl("li", "", T("apv_enr_phone"));
    const s3 = mEl("li", "", "");
    steps.append(s1, s2, s3);
    const result = mEl("p", "mnote");
    result.id = "apv-enroll-result";
    result.setAttribute("role", "status");
    const row = mEl("div", "mrow");
    const back = mEl("button", "mbtn", T("apv_key_back"));
    back.type = "button";
    back.addEventListener("click", () => renderPairPanel(false));
    row.append(back);
    if (t.link) {
      const copy = mEl("button", "mbtn", T("apv_key_copy"));
      copy.type = "button";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(t.link);
          showToast(T("pair_copied"));
        } catch {
          showToast(T("pair_copy_fail"), false);
        }
      });
      row.append(copy);
    }
    modal.append(steps, result, row);
    let st = null;            // 直近の approveStatus
    let done = false;
    const alive = () => gen === pairGen && !shell.querySelector("#modalwrap").hidden;
    const stage = (li, cls) => { li.className = cls; };
    const paint = () => {
      const now = Date.now() / 1000;
      const en = st?.enroll?.[d.device_id] || {};
      const exp = Number(en.open?.exp) || Number(t.expires) || 0;
      const k = st?.keys?.[d.device_id];
      const granted = Boolean(k && k.active && Number(k.created) > prevCreated);
      const last = en.last && Number(en.last.at) >= started - 2 ? en.last : null;
      const confirming = Boolean(en.confirming);
      const open = Boolean(en.open) || (!st && exp > now);
      s1.textContent = open ? T("apv_enr_open", mmss(exp - now)) : T("apv_enr_open_closed");
      s3.textContent = T("apv_enr_mac");             // R103-1: Mac での確認は無い（登録した瞬間から有効）
      if (granted) {
        stage(s1, "done"); stage(s2, "done"); stage(s3, "done");
        result.className = "mnote mok";
        result.textContent = T("apv_enr_ok", k.fp || "");
        if (!done) {
          done = true;
          setTimeout(() => { if (alive()) renderPairPanel(false); }, 3000);
        }
        return;
      }
      stage(s1, "");                              // 受付の残り時間は進行中の事実＝淡くしない
      stage(s2, confirming ? "done" : open ? "now" : "");
      stage(s3, confirming ? "now" : "");
      if (confirming) {
        result.className = "mnote";
        result.textContent = T("apv_enr_confirming");
      } else if (last?.reason === "cancelled" && open) {
        const left = Math.max(0, (Number(st?.enrollTries) || 3) - (Number(en.open?.tries) || 0));
        result.className = "mnote merr";
        result.textContent = T("apv_enr_cancelled", left);
      } else if (!open && st) {
        done = true;
        result.className = "mnote merr";
        result.textContent = last && last.state === "refused" && last.reason !== "cancelled"
          ? T("apv_enr_refused", last.reason) : T("apv_enr_closed");
        const again = mEl("button", "mgo mgosm", T("apv_enr_retry"));
        again.type = "button";
        again.addEventListener("click", () => renderPairPanel(false));
        if (!row.querySelector(".mgo")) row.prepend(again);
      } else {
        result.className = "mnote";
        result.textContent = "";
      }
    };
    paint();
    let polls = 0;
    const tick = async () => {
      if (!alive() || done) return;
      polls += 1;
      if (polls % 2 === 1) {                       // 状態は 2 秒ごと・残り時間の表示は 1 秒ごと
        try { st = await approveStatus(); } catch { /* 読めない回は前の状態のまま */ }
        if (!alive()) return;
      }
      paint();
      if (!done) setTimeout(tick, 1000);
    };
    setTimeout(tick, 1000);
  };
  /** デモ（静的ホスト）で、サーバー専用の操作を開いたときの断り書き。 */
  const demoOnly = (title) => {
    modal.replaceChildren(mEl("b", "mtitle", title), mEl("p", "mnote", T("demo_server_only")));
    openModal();
  };
  // R100-0: 開いただけでは発行しない（発行は Mac の物理確認を出す＝開くたびにダイアログが出て、キャンセルすると
  // 一覧・失効・会話共有のトグルに届かなかった・R100-0 のレビュー）。「＋ 新しいスマホを登録」で初めて発行する。
  // b4-mac: 発行の前に名前を付けられる（既定「iPhone 10/1」）＝全部「スマホ」で、どれが今使っている端末か見分けられなかった。
  let pairGen = 0;                               // 描き直しの世代（遅れて返った古い一覧を新しいモーダルに足さない）
  const renderPairPanel = async (issue = false, label = "") => {
    if (demo) return demoOnly(T("btn_pair"));   // /api/pair/* は静的ホストに存在しない
    pairGen += 1;
    modal.replaceChildren(mEl("b", "mtitle", T("btn_pair")));
    openModal();
    if (issue === true) await renderPairIssue(label);
    else {
      const nameIn = mEl("input", "minput");
      nameIn.type = "text";
      nameIn.id = "pair-name";
      nameIn.maxLength = 40;
      nameIn.value = T("pair_name_default", day(Date.now() / 1000));
      nameIn.setAttribute("aria-label", T("pair_name_ph"));
      nameIn.placeholder = T("pair_name_ph");
      const add = mEl("button", "mgo", T("pair_add"));
      add.type = "button";
      add.id = "pair-add";
      const go = () => renderPairPanel(true, nameIn.value.trim() || T("pair_device_label"));
      add.addEventListener("click", go);
      nameIn.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) go(); });
      modal.append(mEl("p", "mnote", T("pair_add_note")), nameIn, add);
    }
    await renderPairList();
  };
  const renderPairIssue = async (label) => {
    const wait = mEl("p", "mnote", T("pair_issuing"));
    modal.append(wait);
    let dev;
    try {
      dev = await pairNew(label || T("pair_device_label"));
    } catch (err) {
      // 中継未設定・Mac の確認でキャンセル などはサーバーの文言をそのまま出す（Pro壁はR84撤去済み）
      wait.replaceWith(mEl("p", "mnote merr", err.message));
      return;
    }
    wait.remove();
    if (dev.pairUrl) {
      if (dev.qrSvg) {
        const img = mEl("img", "mqr");
        img.alt = T("pair_qr_alt");
        img.src = `data:image/svg+xml;utf8,${encodeURIComponent(dev.qrSvg)}`;
        modal.append(img);
      }
      modal.append(mEl("p", "mnote", T("pair_scan")));
      const copy = mEl("button", "mbtn", T("pair_copy"));
      copy.type = "button";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(dev.pairUrl);
          showToast(T("pair_copied"));
        } catch {
          showToast(T("pair_copy_fail"), false);
        }
      });
      modal.append(copy);
    } else {
      modal.append(mEl("p", "mnote", T("pair_norelay")));
    }
  };
  /** 24 時間たっても一度も使われていない発行分（リンクを作っただけ・許可鍵も無い）＝淡く出し、まとめて失効できる */
  const STALE_SEC = 86400;
  const isStale = (d, apv, now) => d.state === "active" && !Number(d.last_used)
    && now - (Number(d.created) || now) > STALE_SEC && !apv?.keys?.[d.device_id]?.active;
  const deviceRow = (d, apv, now) => {
    const stale = isStale(d, apv, now);
    const row = mEl("div", `mdev${stale ? " stale" : ""}${d.state !== "active" ? " dead" : ""}`);
    row.dataset.device = d.device_id;
    // 1 段目: 名前と状態／2 段目: 登録日と最後に使った時刻／3 段目: 操作（失効済み・期限切れには出さない）
    // R86-H: 「どれが今も使える鍵か」を出す（57件・全部「スマホ」表示で見分けられず、有効な鍵が29個ぶら下がっていた・実測）。
    const top = mEl("div", "mdevtop");
    top.append(mEl("b", "mdevname", d.label || d.device_id), mEl("span", "devmeta",
      (d.state === "active" ? T("pair_active", d.daysLeft) : d.state === "expired" ? T("pair_expired") : T("pair_dead"))
      // R116-S1b: 延長の進み具合（wait＝押した・スマホがまだ／pending＝新しい秘密を渡した・受け取りの知らせ待ち）
      + (d.rekey === "wait" ? T("pair_rekey_wait") : d.rekey === "pending" ? T("pair_rekey_pending") : "")));
    const used = Number(d.last_used) || 0;
    const meta = [d.created ? T("pair_registered", day(d.created)) : "",
      used ? T("pair_last_used", agoStr(now - used, lang())) : T("pair_never_used")].filter(Boolean).join(" · ");
    row.append(top, mEl("div", "mdevsub", meta));
    if (d.state !== "active") return row;
    const ops = mEl("div", "mdevops");
    // R116-S1b: 同じ端末のまま 30 日延長（ホーム画面のアプリのまま・QR を撮り直さない）。失効より前に置く（押し間違えない並び）
    const ex = mEl("button", "mbtn pair-extend", T("pair_extend"));
    ex.type = "button";
    ex.addEventListener("click", async () => {
      try {
        await pairExtend(d.device_id);
        showToast(T("pair_extend_armed"));
        renderPairPanel(false);
      } catch (err) {
        showToast(err.message, false);
      }
    });
    ops.append(ex);
    const rv = mEl("button", "mrevoke pair-revoke", T("pair_revoke"));
    rv.type = "button";
    rv.addEventListener("click", async () => {
      try {
        await pairRevoke(d.device_id);
        showToast(T("pair_revoked"));
        renderPairPanel(false);
      } catch (err) {
        showToast(err.message, false);
      }
    });
    ops.append(rv);
    if (apv) renderApproveKey(ops, d, apv);   // 許可鍵の操作は端末の失効の後ろ（押し間違えない並び）
    row.append(ops);
    return row;
  };
  const renderPairList = async () => {
    const gen = pairGen;
    try {
      const data = await pairList();
      if (gen !== pairGen) return;
      const { devices } = data;
      renderDialogSetting(modal, "pair-dialog", data);
      let apv = null;
      try {
        apv = await approveStatus();
        if (gen !== pairGen) return;
        renderApproveSetting(modal, apv);
      } catch { /* 旧い Mac（R100-1b 以前）は出さない */ }
      if (devices?.length) {
        const now = Date.now() / 1000;
        const live = devices.filter((d) => d.state === "active");
        const dead = devices.filter((d) => d.state !== "active");
        const list = mEl("div", "mdevices");
        list.append(mEl("b", "msub", T("pair_devices", live.length)));
        for (const d of live) list.append(deviceRow(d, apv, now));
        const stale = live.filter((d) => isStale(d, apv, now));
        if (stale.length) {
          // まとめて失効は 2 回押し（1 回目で 3 秒だけ「もう一度押すと N 台を失効」）
          const bulk = mEl("button", "mrevoke pair-stale", T("pair_stale_revoke", stale.length));
          bulk.type = "button";
          let armed = 0;
          bulk.addEventListener("click", async () => {
            if (!armed) {
              bulk.textContent = T("pair_stale_confirm", stale.length);
              armed = setTimeout(() => { armed = 0; bulk.textContent = T("pair_stale_revoke", stale.length); }, 3000);
              return;
            }
            clearTimeout(armed);
            bulk.disabled = true;
            let n = 0;
            for (const d of stale) {
              try { await pairRevoke(d.device_id); n += 1; } catch { /* 残りは続ける */ }
            }
            showToast(T("pair_stale_done", n));
            renderPairPanel(false);
          });
          list.append(mEl("p", "mnote", T("pair_stale_note")), bulk);
        }
        if (dead.length) {
          // 失効済み・期限切れは畳む（操作は出さない＝押せても何も起きないボタンを並べない）
          const box = mEl("details", "mdevdead");
          box.append(mEl("summary", "", T("pair_dead_n", dead.length)));
          for (const d of dead) box.append(deviceRow(d, apv, now));
          list.append(box);
        }
        modal.append(list);
      }
    } catch { /* 一覧はベストエフォート */ }
  };
  shell.querySelector("#btn-pair").addEventListener("click", () => renderPairPanel(false));

  // R102-11: スマホからの許可が ON の間は、メイン画面のヘッダーに札を出す（モーダルを開かないと分からなかった）。
  // 状態は loopback の /api/approve/status を 60 秒ごとに読むだけ＝中継には載らない（不変条件 8）。R118 から期限なし
  //（期限つきの古い形だけ 30 分前から警告色）。札を押すと 📱 の該当欄・札の「OFF」はその場で切る。固定時刻・デモでは読まない＝出さない。
  const apvBadge = mEl("span", "apvbadge");
  apvBadge.id = "apvbadge";
  apvBadge.hidden = true;
  const apvGo = mEl("button", "apvgo");
  apvGo.type = "button";
  apvGo.dataset.px = "key";
  const apvOff = mEl("button", "apvoff");
  apvOff.type = "button";
  if (cockpit) {
    apvGo.setAttribute("aria-label", T("apv_head"));
    apvOff.setAttribute("aria-label", T("apv_off_btn"));
  }
  apvBadge.append(apvGo, apvOff);
  shell.querySelector("#btn-pair").before(apvBadge);
  let apvUntil = 0;
  const hhmm = (t) => new Date(t * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit", hour12: false });
  const paintApvBadge = () => {
    const left = apvUntil - Date.now() / 1000;
    apvBadge.hidden = !(left > 0);
    if (apvBadge.hidden) return;
    const timed = apvUntil < APV_ALWAYS;              // 期限つきの古い ON（R118 より前）だけ時刻を出す
    apvGo.textContent = timed ? T("apv_badge_until", hhmm(apvUntil)) : T("apv_badge");
    apvGo.title = apvStateText(apvUntil);
    apvOff.textContent = T("apv_badge_off");
    apvOff.title = T("apv_off_btn");
    if (cockpit) {
      apvGo.setAttribute("aria-label", apvGo.title);
      apvOff.setAttribute("aria-label", apvOff.title);
    }
    apvBadge.classList.toggle("warn", left <= 30 * 60);
  };
  function setApvUntil(until) {
    apvUntil = Number(until) || 0;
    paintApvBadge();
  }
  const refreshApvBadge = async () => {
    if (demo || frozen) return;
    // 読めない（旧い Mac・一時的な失敗）ときは前の値のまま＝期限が来れば paint が自分で畳む
    try { setApvUntil((await approveStatus())?.until); } catch { paintApvBadge(); }
  };
  apvGo.addEventListener("click", async () => {
    await renderPairPanel(false);
    modal.querySelector("#pair-approve")?.scrollIntoView({ block: "nearest" });
  });
  apvOff.addEventListener("click", async () => {
    apvOff.disabled = true;
    try {
      await setRemoteApprove(false);
      setApvUntil(0);
      showToast(T("apv_turned_off"));
    } catch (err) {
      showToast(err.message, false);
    }
    apvOff.disabled = false;
  });
  if (!demo && !frozen) {
    void refreshApvBadge();
    setInterval(refreshApvBadge, 60 * 1000);
  }

  // ── ▶ 遠隔実行の許可リスト（R79-10）─────────────────────────────────
  // **ここが唯一の作成・編集口**（loopback+CSRF＝Macの前の人間だけ）。スマホ側は
  // 登録済み id を参照して実行を依頼できるだけ＝鍵が漏れても未登録コマンドは動かない。
  const renderRunPanel = async () => {
    modal.replaceChildren(mEl("b", "mtitle", T("run_head")), mEl("p", "mnote", T("run_note")));
    openModal();
    let data = null;
    try {
      data = await getRecipes();
    } catch (err) {
      modal.append(mEl("p", "mnote merr", err.message));
      return;
    }
    const recipes = data?.recipes || [];
    const form = mEl("form", "run-form mkeyrow");
    const field = (id, key) => {
      const label = mEl("label", "run-field");
      const input = mEl("input", "minput");
      input.id = id;
      input.type = "text";
      input.required = true;
      label.append(mEl("span", "", T(key)), input);
      form.append(label);
      return input;
    };
    const labelIn = field("run-label", "run_label");
    labelIn.maxLength = 60;
    const folderIn = field("run-folder", "run_folder");
    folderIn.readOnly = true;
    folderIn.value = recipeFolder;
    const pick = mEl("button", "abtn", T("run_pick_folder"));
    pick.type = "button";
    pick.id = "run-pick-folder";
    folderIn.parentElement.append(pick);
    const commandIn = field("run-command", "run_command");
    const add = mEl("button", "mgo", T("run_add"));
    add.type = "submit";
    add.id = "run-add";
    let saving = false;   // 保存中は入力を直しても追加を再有効化しない（二重送信で同じ r_N が上書きし合う・別モデルレビュー）
    const updateAdd = () => {
      add.disabled = saving || !labelIn.value.trim() || !folderIn.value || !commandIn.value.trim();
    };
    form.addEventListener("input", updateAdd);
    pick.addEventListener("click", async () => {
      pick.disabled = true;
      try {
        const picked = await pickProjectFolder();
        if (picked?.path) {
          recipeFolder = picked.path;
          folderIn.value = recipeFolder;
        }
      } catch (err) {
        showToast(err.message, false);
      }
      pick.disabled = false;
      updateAdd();
    });
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (add.disabled || saving || !form.reportValidity()) return;
      saving = true; add.disabled = true;
      // 短い連番なら日本語のラベルでも安定した許可リストIDになる。
      let index = 1;
      while (recipes.some((r) => r.id === `r_${index}`)) index++;
      const recipe = { id: `r_${index}`, label: labelIn.value.trim(),
        cwd: folderIn.value, argv: commandIn.value.trim().split(/\s+/),
        timeoutSec: 600, returnOutput: "none" };
      try {
        const res = await setRecipes([...recipes, recipe]);
        showToast(res?.msg || T("run_saved"));
        renderRunPanel();
      } catch (err) {
        showToast(err.message, false);
        saving = false;
        updateAdd();
      }
    });
    updateAdd();
    form.append(add);
    modal.append(form);
    if (!recipes.length) modal.append(mEl("p", "mnote", T("run_empty")));
    for (const r of recipes) {
      const row = mEl("div", "mkeyrow");
      row.append(mEl("b", null, (r.dangerous ? (T.plain ? "! " : "⚠️ ") : (T.plain ? "" : "▶ ")) + r.label),
                 mEl("code", "mnote", r.argv.join(" ")));
      modal.append(row);
    }
    for (const e of (data?.errors || [])) modal.append(mEl("p", "mnote merr", e));
    const ta = document.createElement("textarea");
    ta.className = "mtextarea";
    ta.rows = 10;
    ta.value = JSON.stringify({ recipes }, null, 1);
    const save = mEl("button", "abtn", T("run_save"));
    save.type = "button";
    save.addEventListener("click", async () => {
      let parsed;
      try {
        parsed = JSON.parse(ta.value);
      } catch (err) {
        showToast(String(err.message || err), false);
        return;
      }
      try {
        const res = await setRecipes(parsed.recipes ?? parsed);
        showToast(res?.msg || T("run_saved"));
        renderRunPanel();
      } catch (err) {
        showToast(err.message, false);
      }
    });
    // R80-A6: 雛形へUIから到達できるようにする（従来は example.json がリポジトリにしか無く、
    // 初見に argv配列・絶対cwd・returnOutput を素手で書かせていた）
    const sample = mEl("button", "abtn", T("run_sample"));
    sample.type = "button";
    sample.addEventListener("click", () => {
      const cur = (() => {
        try { return JSON.parse(ta.value).recipes || []; } catch { return []; }
      })();
      const example = {
        id: "r_status", label: T("run_sample_label"),
        argv: ["git", "status", "--short"],
        cwd: recipeFolder || "/Users/you/path/to/your-project",
        timeoutSec: 30, returnOutput: "tail",
      };
      ta.value = JSON.stringify({ recipes: [...cur, example] }, null, 1);
      showToast(T("run_sample_note"));
    });
    const advanced = mEl("details", "run-advanced");
    advanced.append(mEl("summary", "", T("run_advanced")),
      mEl("p", "mnote", T("run_hint")), ta, sample, save);
    modal.append(advanced);
  };
  shell.querySelector("#btn-run").addEventListener("click", renderRunPanel);

  // ── ⚡リソース（status_board 読み取りビュー） ─────────────────────
  // テストの履歴には言葉だけを保持し、鍵を残さない。
  const providerResults = new Map();
  const providerTestQueue = new Map();
  const keyViews = new WeakMap();
  let providerKeyRevision = 0;
  const reflectProviderConnection = (id, connected) => {
    providerKeyRevision += 1;
    const view = keyViews.get(modal.querySelector(".mkeys"));
    const pr = view?.providers.find((p) => p.id === id);
    if (pr) { pr.connected = connected; view.paint(); }
  };
  const runProviderTest = async (id, value) => {
    const previous = providerTestQueue.get(id) || Promise.resolve();
    let release;
    providerTestQueue.set(id, new Promise((resolve) => { release = resolve; }));
    await previous;
    try {
      const res = await testProvider(id, value);
      if (typeof res?.result?.ok !== "boolean" || typeof res.result.message !== "string") {
        return { ok: false, message: T("keys_test_no_result") };
      }
      return { ok: res.result.ok, message: res.result.message };
    } catch (err) {
      return { ok: false, message: err.message };
    } finally {
      // サーバーは同じプロバイダを 3 秒に 1 回に制限する。保存直後もこの列で待つ。
      setTimeout(release, 3100);
    }
  };
  // R66: 🔑連携はPro未解錠(403)でも使えるべき＝status_board失敗時にも単独描画できる関数
  const renderKeysSection = async (sb) => {
    let ks = null;
    try {
      let revision;
      do {
        revision = providerKeyRevision;
        ks = await getKeysStatus();
      } while (revision !== providerKeyRevision);
    } catch { /* 取得失敗時はセクションごと出さない（嘘の状態を見せない） */ }
    if (ks?.providers?.length) {
      // keyName が無い旧サーバーの行だけ、従来の表で保存先を補う。
      const LEGACY_KEY_NAMES = { openai_key: "OPENAI_API_KEY", x_api: "X_BEARER_TOKEN",
        openai_usage: "OPENAI_ADMIN_KEY", openrouter: "OPENROUTER_API_KEY", moonshot: "MOONSHOT_API_KEY",
        deepseek: "DEEPSEEK_API_KEY", groq: "GROQ_API_KEY" };
      const keyNameOf = (pr) => "keyName" in pr ? pr.keyName : LEGACY_KEY_NAMES[pr.id];
      // R66: 「いま: <アカウント>」ガイド用（statusBoardは既に取得済み=追加fetchしない）
      const acctEmail = (id) => {
        const em = (sb?.providers || []).find((p2) => p2.id === id)?.account?.email || "";
        return em ? em.split("@")[0] : "";
      };
      const sec = mEl("div", "mkeys");
      const button = (cls, text, action) => {
        const btn = mEl("button", cls, text);
        btn.type = "button";
        if (action) btn.addEventListener("click", action);
        return btn;
      };
      const paintResult = (node, result) => {
        node.className = "mkeyresult" + (result?.ok === true ? " ok" : result?.ok === false ? " fail" : "");
        node.textContent = typeof result?.message === "string" ? result.message : T("keys_untested");
      };
      const updateRowResult = (id, result) => {
        providerResults.set(id, result);
        const row = [...modal.querySelectorAll(".mkeys .mkeyrow")].find((r) => r.dataset.provider === id);
        if (!row) return;
        row.querySelector(".mkeydot").className = "mkeydot" +
          (result.ok === true ? " on" : result.ok === false ? " fail" : "");
        paintResult(row.querySelector(".mkeyresult"), result);
        // 自分で作った行のボタンは参照で持つ（class で探すと hud_ids の静的な契約＝様式の shell が持つ class として数えられる）
        if (row.mkeyRetest) row.mkeyRetest.disabled = !!result.pending;
        if (row.mkeyRevoke) row.mkeyRevoke.disabled = !!result.pending;
      };
      const revokeButton = (pr, keyName) => {
        const rv = button("mkeyrevoke", T("keys_revoke"));
        let armed = 0;
        rv.addEventListener("click", async () => {
          if (!armed) {
            armed = setTimeout(() => { armed = 0; rv.textContent = T("keys_revoke"); }, 3000);
            rv.textContent = T("keys_revoke_arm");
            return;
          }
          clearTimeout(armed);
          armed = 0;
          rv.disabled = true;
          try {
            await setOfficeKey(keyName, "");
            providerResults.delete(pr.id);
            showToast(T("keys_revoked"));
            if (pr.kind) { pr.connected = false; reflectProviderConnection(pr.id, false); }
            else shell.querySelector("#btn-res").click();
          } catch (err) {
            rv.disabled = false;
            rv.textContent = T("keys_revoke");
            showToast(err.message, false);
          }
        });
        return rv;
      };
      const openWizard = (group, add) => {
        const wizard = mEl("div", "mkeywizard");
        add.disabled = true;
        group.insertBefore(wizard, add.nextSibling);
        let discard = () => {};
        const cancel = () => { discard(); wizard.remove(); add.disabled = false; add.focus(); };
        const choose = (pr) => {
          wizard.dataset.provider = pr.id;
          const input = mEl("input", "mkeyin");
          input.type = "password";
          input.placeholder = pr.ph || T("keys_ph");
          input.autocomplete = "off";
          input.spellcheck = false;
          input.setAttribute("aria-label", `${pr.label || pr.id} — ${T("keys_key_label")}`);
          let checkedValue = "";
          let result = null;
          let busy = false;
          discard = () => { input.value = ""; checkedValue = ""; };
          const feedback = mEl("p", "mkeyresult", T("keys_test_hint"));
          feedback.setAttribute("role", "status");
          const override = mEl("div", "mkeyoverride");
          const confirm = mEl("div", "mkeyconfirm");
          confirm.hidden = true;
          const back = button("mkeybtn mkeyback", T("keys_back"), () => { discard(); list(); });
          const close = button("mkeybtn mkeycancel", T("keys_cancel"), cancel);
          const sync = () => {
            const matches = !!checkedValue && checkedValue === input.value.trim();
            input.disabled = busy;
            back.disabled = busy;
            close.disabled = busy;
            test.disabled = busy || !input.value.trim();
            save.disabled = busy || !matches || result?.ok !== true;
            override.hidden = !matches || result?.ok !== false;
            force.disabled = busy;
            forceConfirm.disabled = busy;
          };
          const saveKey = async (allowFailed = false) => {
            if (busy || !checkedValue || checkedValue !== input.value.trim() ||
                (allowFailed ? result?.ok !== false : result?.ok !== true)) return;
            busy = true;
            sync();
            try {
              await setOfficeKey(keyNameOf(pr), input.value.trim());
              discard();
              pr.connected = true;
              providerResults.set(pr.id, { ok: null, message: T("keys_testing"), pending: true });
              showToast(T("keys_saved"));
              reflectProviderConnection(pr.id, true);
              // 保存済みの鍵を使うので value は渡さない。保存前の結果を行の状態に流用しない。
              const savedResult = await runProviderTest(pr.id);
              updateRowResult(pr.id, savedResult);
            } catch (err) {
              busy = false;
              sync();
              showToast(err.message, false);
            }
          };
          const test = button("mkeybtn mkeytest", T("keys_test"), async () => {
            if (busy || !input.value.trim()) return;
            busy = true;
            result = null;
            checkedValue = input.value.trim();
            confirm.hidden = true;
            paintResult(feedback, { message: T("keys_testing") });
            sync();
            result = await runProviderTest(pr.id, checkedValue);
            paintResult(feedback, result);
            busy = false;
            sync();
          });
          const save = button("mkeysave", T("keys_save"), () => saveKey());
          const force = button("mkeyforce", T("keys_save_anyway"), () => { confirm.hidden = false; });
          const forceConfirm = button("mkeyforceconfirm", T("keys_save_anyway_confirm"), () => saveKey(true));
          confirm.append(mEl("p", "mkeygrpsub", T("keys_save_anyway_note")), forceConfirm);
          override.append(force, confirm);
          input.addEventListener("input", () => {
            checkedValue = "";
            result = null;
            confirm.hidden = true;
            feedback.className = "mkeyresult";
            feedback.textContent = T("keys_test_hint");
            sync();
          });
          const form = mEl("div", "mkeyform mkeywizardform");
          form.append(input, test, save);
          const nav = mEl("div", "mkeynav");
          nav.append(back, close);
          wizard.replaceChildren(mEl("b", "mkeyname", pr.label || pr.id),
            mEl("p", "mkeygrpsub", pr.hint || ""));
          if (pr.getFrom) wizard.append(mEl("p", "mkeygrpsub mkeyfrom", T("keys_getfrom", pr.getFrom)));
          wizard.append(form, feedback, override, nav);
          sync();
          input.focus();
        };
        const list = () => {
          delete wizard.dataset.provider;
          discard = () => {};
          const choices = mEl("div", "mkeychoices");
          for (const pr of ks.providers.filter((p) => p.kind && !p.connected)) {
            const option = button("mkeychoice", undefined, () => choose(pr));
            option.dataset.provider = pr.id;
            option.disabled = !keyNameOf(pr);
            const measures = (pr.measures || []).map((m) =>
              ["spend", "limit", "balance", "connect"].includes(m) ? T(`keys_measure_${m}`) : "").filter(Boolean);
            option.append(mEl("b", "mkeyname", pr.label || pr.id),
              mEl("span", "mkeygrpsub", measures.join(" / ")));
            choices.append(option);
          }
          wizard.replaceChildren(mEl("p", "mkeygrpsub", T("keys_choose")), choices,
            button("mkeybtn mkeycancel", T("keys_cancel"), cancel));
          choices.querySelector("button")?.focus();
        };
        list();
      };
      const paint = () => {
        sec.replaceChildren(mEl("b", "msub", T("keys_head")));
        const group = (id, title, sub) => {
          const grp = mEl("div", "mkeygrp");
          grp.dataset.keyGroup = id;
          grp.append(mEl("b", "mkeygrph", T(title)), mEl("p", "mkeygrpsub", T(sub)));
          return grp;
        };
        const grpA = group("auto", "keys_grp_auto", "keys_grp_auto_sub");
        const grpB = group("api", "keys_grp_key", "keys_grp_key_sub");
        const grpC = group("other", "keys_grp_other", "keys_grp_other_sub");
        const add = button("mkeybtn mkeyadd", T("keys_add"), () => openWizard(grpB, add));
        add.disabled = !ks.providers.some((p) => p.kind && !p.connected);
        grpB.append(add);
        if (add.disabled) grpB.append(mEl("p", "mkeygrpsub", T("keys_all_connected")));
        for (const pr of ks.providers) {
          if (pr.kind && !pr.connected) continue;
          const row = mEl("div", "mkeyrow");
          row.dataset.provider = pr.id;
          const head = mEl("div", "mkeyhead");
          head.append(mEl("i", "mkeydot" + (pr.connected ? " on" : "")),
            mEl("span", "mkeyname", pr.label || pr.id));
          const keyName = keyNameOf(pr);
          if (pr.kind) {
            const result = providerResults.get(pr.id);
            head.querySelector(".mkeydot").className = "mkeydot" +
              (result?.ok === true ? " on" : result?.ok === false ? " fail" : "");
            const feedback = mEl("p", "mkeyresult");
            feedback.setAttribute("role", "status");
            paintResult(feedback, result);
            const retest = button("mkeybtn mkeyretest", T("keys_retest"), async () => {
              updateRowResult(pr.id, { ok: null, message: T("keys_testing"), pending: true });
              updateRowResult(pr.id, await runProviderTest(pr.id));
            });
            const revoke = revokeButton(pr, keyName);
            retest.disabled = !!result?.pending;
            revoke.disabled = !!result?.pending || !keyName;
            row.mkeyRetest = retest;
            row.mkeyRevoke = revoke;
            head.append(retest, revoke);
            row.append(head, feedback);
            if (pr.budget === true && !(pr.measures || []).includes("limit")) {
              row.append(mEl("p", "mkeyhint", T("keys_budget_hint")));
            }
            grpB.append(row);
            continue;
          }
          if (pr.mode !== "key") {
            head.append(mEl("span", "mkeyauto", T("keys_badge_auto")));
            const email = (pr.id === "claude" || pr.id === "codex") ? acctEmail(pr.id) : "";
            const guide = (pr.hint || "") +
              (pr.connected && email ? `（${T("keys_now", email)}）` : "");
            row.append(head, mEl("i", "mkeyhint", guide));
            grpA.append(row);
            continue;
          }
          if (pr.connected && pr.masked) head.append(mEl("i", "mkeymask", pr.masked));
          // ガイド行: 何が取れるか＋未接続なら発行場所（URL文字列表示のみ）
          const guideParts = [pr.hint || ""];
          if (!pr.connected && pr.getFrom) guideParts.push(T("keys_getfrom", pr.getFrom));
          if (keyName) {
            const btn = mEl("button", "mkeybtn",
              pr.connected ? T("keys_change") : T("keys_connect"));
            btn.type = "button";
            btn.dataset.key = keyName;
            btn.addEventListener("click", () => {
              const open = row.querySelector(".mkeyform");
              if (open) { open.remove(); return; }
              const kform = mEl("span", "mkeyform");
              const input = mEl("input", "mkeyin");
              input.type = "password";
              input.placeholder = pr.ph || T("keys_ph");   // R66: キー形式の例（sk-or-v1-…等）
              input.autocomplete = "off";
              const save = mEl("button", "mkeysave", T("keys_save"));
              save.type = "button";
              save.addEventListener("click", async () => {
                save.disabled = true;
                try {
                  await setOfficeKey(keyName, input.value.trim());
                  input.value = "";
                  showToast(T("keys_saved"));
                  shell.querySelector("#btn-res").click();   // connected/masked を反映
                } catch (err) {
                  save.disabled = false;
                  showToast(err.message, false);
                }
              });
              kform.append(input, save);
              row.append(kform);
              input.focus();
            });
            head.append(btn);
            if (pr.connected) {
              head.append(revokeButton(pr, keyName));
            }
          }
          row.append(head, mEl("i", "mkeyhint", guideParts.filter(Boolean).join(" · ")));
          grpC.append(row);
        }
        sec.append(grpA, grpB, grpC);
      };
      keyViews.set(sec, { providers: ks.providers, paint });
      paint();
      modal.append(sec);
    }
  };
  shell.querySelector("#btn-res").addEventListener("click", async () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_res")),
      mEl("p", "mnote", T("loading")));
    openModal();
    let sb;
    try {
      sb = await getStatusBoard();
    } catch (err) {
      // R66: Pro未解錠(403)等でも🔑アカウント連携には到達できる（キャラ生成キー等は無料機能）
      modal.replaceChildren(mEl("b", "mtitle", T("btn_res")),
        mEl("p", "mnote merr", err.message));
      await renderKeysSection(null);
      return;
    }
    modal.replaceChildren(mEl("b", "mtitle", T("btn_res")),
      mEl("p", "mnote", T("res_note")));
    const jpy = sb.fx?.jpyPerUsd || 155;
    // R72: 「定額のサブスク枠」と「APIキーの従量課金」を混ぜて並べると、どれが
    // 使い放題でどれが使うほど請求されるのか読めない（ユーザーFB）。billing（サーバー正本）で
    // 2グループに割り、見出しに課金の性質を1行で書く。
    const usdJpy = (v) => `$${v.toFixed(2)} ≈ ¥${Math.round(v * jpy).toLocaleString()}`;
    const subs = (sb.providers || []).filter((p) => billingOf(p) === "subscription");
    if (subs.length) {
      modal.append(mEl("b", "msub", T("res_grp_sub")),
        mEl("p", "mnote", T("res_grp_sub_note")));
    }
    for (const pr of subs) {
      const row = mEl("div", "mres");
      const head = mEl("div", "mreshead");
      head.append(mEl("b", "", pr.label || pr.id));
      let sub = "";
      if (pr.kind === "tokens" && pr.tokens?.today) {
        const usd = Object.values(pr.tokens.byModel || {})
          .reduce((a, m) => a + (m.usd || 0), 0);
        sub = T("res_today", fmtTok(pr.tokens.today.total),
          Math.round(usd * jpy).toLocaleString());
      } else if (pr.kind === "gauge") {
        sub = T("res_used", pr.plan || "", Math.round(pr.usedPercent ?? 0));
        if (pr.resetsAt) {
          const d = new Date(pr.resetsAt * 1000);
          sub += T("res_reset", d.getMonth() + 1, d.getDate());
        }
      } else if (pr.kind === "login") {
        sub = pr.loggedIn ? T("res_login_yes") : T("res_login_no");
      }
      head.append(mEl("span", "mressub", sub));
      row.append(head);
      modal.append(row);
    }
    // ── R63: 🔌 APIプロバイダ（消費・残高・上限を1箇所に集約） ──────────
    // 上限が判明しているものだけバー。取れないものは「上限が設定されていません」と
    // 明示して消費/残高だけ出す（嘘の%を作らない掟）。予算はその場で設定できる。
    // R72: external（X API / OpenAI 管理キー）も課金方式は同じ従量なので同じ節に入れる。
    // 旧実装では OpenAI は上の一覧で「接続済み」としか出ず、取得済みの当月額が
    // どこにも出ていなかった（＝キーを入れたのに認識されていないように見える実UX欠陥）。
    const apis = (sb.providers || []).filter((p) => billingOf(p) === "apikey");
    if (apis.length) {
      const sec = mEl("div", "mapis");
      sec.append(mEl("b", "msub", T("api_head")), mEl("p", "mnote", T("res_grp_api_note")));
      for (const pr of apis) {
        const row = mEl("div", "mapi");
        const head = mEl("div", "mreshead");
        head.append(mEl("b", "", pr.label || pr.id));
        if (pr.kind === "external") {
          let sub;
          if (!pr.connected) sub = T("res_nokey");
          else if (pr.cap) sub = `${fmtTok(pr.used || 0)} / ${fmtTok(pr.cap)}`;
          else if (pr.monthUsd != null) {
            sub = pr.sinceDay
              ? T("res_month_since", usdJpy(pr.monthUsd), pr.sinceDay)
              : T("res_month", usdJpy(pr.monthUsd));
          } else sub = T("res_connected");
          head.append(mEl("span", "mressub", sub));
          row.append(head);
          if (pr.connected && pr.pct != null && pr.cap) {
            const track = mEl("div", "gbar big");
            const fill = mEl("i", pr.pct >= 80 ? "gfill warn" : "gfill");
            fill.style.width = `${Math.max(2, Math.round(pr.pct))}%`;
            track.append(fill);
            row.append(track);
          } else if (pr.connected && pr.monthUsd != null) {
            row.append(mEl("span", "gsub gnolimit", T("api_no_limit")));
          }
          sec.append(row);
          continue;
        }
        const money = (v) => (pr.currency === "CNY" ? `CN¥${v.toFixed(2)}`
          : pr.currency === "JPY" ? `¥${Math.round(v).toLocaleString()}`
          : `$${v.toFixed(2)}`);
        let sub = "";
        if (pr.status === "error") sub = pr.error || T("api_err");
        else if (pr.spentMonth != null && pr.limit != null) {
          sub = `${money(pr.spentMonth)} / ${money(pr.limit)}`;
        } else if (pr.spentMonth != null) sub = money(pr.spentMonth);
        else if (pr.balance != null) sub = T("api_balance", money(pr.balance));
        else sub = T("api_nodata");
        head.append(mEl("span", "mressub", sub));
        row.append(head);
        if (pr.pct != null) {
          const track = mEl("div", "gbar big");
          const fill = mEl("i", pr.pct >= 80 ? "gfill warn" : "gfill");
          fill.style.width = `${Math.max(2, Math.round(pr.pct))}%`;
          track.append(fill);
          row.append(track);
          row.append(mEl("span", "gsub",
            `${Math.round(pr.pct)}%${pr.limitSource === "manual" ? T("api_budget_tag") : ""}`));
        } else if (pr.note === "no_limit") {
          row.append(mEl("span", "gsub gnolimit", T("api_no_limit")));
        }
        // 予算の設定（上限がAPIから取れないプロバイダで意味を持つ）
        if (pr.limitSource !== "api") {
          const form = mEl("div", "mapibudget");
          const amt = mEl("input", "minput mnum");
          amt.type = "number";
          amt.min = "0";
          amt.step = "1";
          amt.placeholder = T("api_budget_ph");
          if (pr.limitSource === "manual" && pr.limit != null) amt.value = String(pr.limit);
          const save = mEl("button", "mgo mgosm", T("api_budget_save"));
          save.type = "button";
          save.addEventListener("click", async () => {
            save.disabled = true;
            try {
              await budgetApply(pr.id, Number(amt.value) || 0,
                pr.currency === "CNY" ? "CNY" : "USD");
              showToast(T("api_budget_saved"));
              shell.querySelector("#btn-res").click();     // 再読込
            } catch (err) {
              save.disabled = false;
              showToast(err.message, false);
            }
          });
          form.append(amt, save);
          row.append(form);
        }
        sec.append(row);
      }
      modal.append(sec);
    }
    if (sb.spend) {
      const total = Math.round((sb.spend.totalJpy || 0) +
        (sb.spend.totalUsd || 0) * jpy);
      const row = mEl("div", "mres");
      const head = mEl("div", "mreshead");
      head.append(mEl("b", "", T("res_spend")));
      head.append(mEl("span", "mressub",
        T("res_spend_sub", total.toLocaleString(), sb.spend.items?.length || 0)));
      row.append(head);
      modal.append(row);
    }
    // 💳 台帳の編集（旧UI誘導を廃止しここで完結＝R50-残1「先に移植」の本体）。
    // API は既存 POST /api/status_board/spend をそのまま使う（形は spend_apply が正本）
    const led = mEl("div", "mled");
    led.append(mEl("b", "msub", T("led_head")));
    const list = mEl("div", "mledlist");
    for (const it of sb.spend?.items || []) {
      const row = mEl("div", "mledrow");
      const cur = it.currency === "usd" ? "$" : "¥";
      row.append(mEl("span", "mledname", it.label),
        mEl("b", "", `${cur}${Number(it.amount).toLocaleString()}`),
        mEl("i", "mledkind", it.kind === "payg" ? T("led_kind_payg") : T("led_kind_sub")));
      const del = mEl("button", "mledel", T("led_del"));
      del.type = "button";
      del.dataset.id = it.id || "";
      del.addEventListener("click", async () => {
        del.disabled = true;
        try {
          await spendApply({ op: "delete", id: it.id });
          showToast(T("led_deleted"));
          shell.querySelector("#btn-res").click();     // 最新台帳で描き直す
        } catch (err) {
          del.disabled = false;
          showToast(err.message, false);
        }
      });
      row.append(del);
      list.append(row);
    }
    led.append(list);
    const form = mEl("div", "mledform");
    const nameIn = mEl("input", "mledin mledname-in");
    nameIn.type = "text";
    nameIn.placeholder = T("led_name_ph");
    const amtIn = mEl("input", "mledin mledamt");
    amtIn.type = "number";
    amtIn.min = "0";
    amtIn.placeholder = T("led_amount_ph");
    const curSel = mEl("select", "mledin");
    for (const [v, l] of [["jpy", "¥"], ["usd", "$"]]) {
      const o = mEl("option", "", l);
      o.value = v;
      curSel.append(o);
    }
    const kindSel = mEl("select", "mledin");
    for (const [v, l] of [["sub", T("led_kind_sub")], ["payg", T("led_kind_payg")]]) {
      const o = mEl("option", "", l);
      o.value = v;
      kindSel.append(o);
    }
    const renewIn = mEl("input", "mledin mledrenew");
    renewIn.type = "number";
    renewIn.min = "1";
    renewIn.max = "31";
    renewIn.placeholder = T("led_renew_ph");
    const addBtn = mEl("button", "mgo", T("led_add"));
    addBtn.type = "button";
    addBtn.id = "mgo-ledger";
    addBtn.addEventListener("click", async () => {
      const label = nameIn.value.trim();
      const amount = Number(amtIn.value);
      if (!label || !Number.isFinite(amount) || amount < 0) {
        showToast(T("led_invalid"), false);
        return;
      }
      addBtn.disabled = true;
      const item = { label, amount, currency: curSel.value, kind: kindSel.value, note: "" };
      const rd = Number(renewIn.value);
      if (kindSel.value === "sub" && Number.isInteger(rd) && rd >= 1 && rd <= 31) item.renewDay = rd;
      try {
        await spendApply({ op: "upsert", item });
        showToast(T("led_saved"));
        shell.querySelector("#btn-res").click();
      } catch (err) {
        addBtn.disabled = false;
        showToast(err.message, false);
      }
    });
    form.append(nameIn, amtIn, curSel, kindSel, renewIn, addBtn);
    led.append(form);
    // R85-3: 💱 円換算レート（POST /api/status_board/fx＝実装済みだが呼び手ゼロだったAPIを接続。
    // UIの ≈¥ 表示は全部この1値から導出＝155固定のままだと全額表示が古くなる）
    const fxForm = mEl("div", "mledform");
    fxForm.append(mEl("span", "mledname", T("fx_label")));
    const fxIn = mEl("input", "mledin mledamt");
    fxIn.type = "number";
    fxIn.min = "1";
    fxIn.step = "0.1";
    fxIn.value = String(jpy);
    const fxBtn = mEl("button", "mgo", T("fx_save"));
    fxBtn.type = "button";
    fxBtn.id = "mgo-fx";
    fxBtn.addEventListener("click", async () => {
      fxBtn.disabled = true;
      try {
        await fxApply(Number(fxIn.value));
        showToast(T("fx_saved"));
        shell.querySelector("#btn-res").click();       // 新レートで全額を描き直す
      } catch (err) {
        fxBtn.disabled = false;
        showToast(err.message, false);
      }
    });
    fxForm.append(fxIn, fxBtn);
    led.append(fxForm);
    modal.append(led);
    await renderKeysSection(sb);
  });
  const gaugesMore = mEl("button", "abtn", T("gauges_more"));
  gaugesMore.type = "button";
  gaugesMore.id = "gauges-more";
  gaugesMore.setAttribute("aria-label", T("gauges_more"));   // R105: ヘッダーのボタンは全部 aria-label（読み上げの名前）
  gaugesMore.addEventListener("click", () => {
    shell.querySelector("#btn-res").click();
  });
  shell.querySelector("#gauges").append(gaugesMore);

  // 🧾ライセンスパネルは R84 全機能無料化で撤去（R85-2）。購入導線・鍵登録UIは存在しない。

  // ── R85-3: ▶ プロジェクト起動（launchable[]＝直近に開いたプロジェクトの再起動。
  //    従来は➕新規登録しかなく「昨日のプロジェクトを今日開き直す」導線がスマホにしか無かった） ──
  shell.querySelector("#btn-launch").addEventListener("click", () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_launch")));
    const items = getWorld()?.launchable || [];
    if (!items.length) {
      modal.append(mEl("p", "mnote", T("launch_empty")));
    } else {
      modal.append(mEl("p", "mnote", T("launch_note")));
      for (const it of items) {
        const row = mEl("button", "mpick");
        row.type = "button";
        row.textContent = `${T.plain ? "" : "▶ "}${it.name || it.projectId}`;   // 絵文字なしの T（cockpit）は印を出さない
        row.addEventListener("click", async () => {
          row.disabled = true;
          try {
            await launchProject(it.projectId);
            showToast(T("launch_ok", it.name || it.projectId));
            closeModal();
          } catch (err) {
            row.disabled = false;
            showToast(err.message, false);
          }
        });
        modal.append(row);
      }
    }
    openModal();
  });

  // ── R85-3: ⚙ 設定（PC初の設定パネル＝ダークテーマ・言語。PWAの⚙タブと対） ──
  const THEME_KEY = "aioffice.iso.theme";
  const NOTIFY_KEY = "aioffice.iso.notify";
  const savedTheme = () => {
    try { return localStorage.getItem(THEME_KEY) || "light"; } catch { return "light"; }
  };
  const applyTheme = (t) => root.classList.toggle("th-dark", t === "dark");
  // R101: 様式が自分のテーマの口（theme: {read, apply}）を渡したら、そちらを使う（コックピットの端末の配色・3 択）。
  // iso の .th-dark は持ち込まない（.hud.th-dark が端末の配色をラベンダーに塗り替える）。
  if (!theme) applyTheme(savedTheme());
  const renderSettings = () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_settings")));
    let settingsHost = modal;
    const group = (key) => {
      if (!cockpit) return;
      const section = mEl("section", "cp-settings-group");
      section.append(mEl("h2", "msubtitle", T(key)));
      modal.append(section);
      settingsHost = section;
    };
    const shortcut = (key, selector) => {
      const button = mEl("button", "abtn cp-settings-link", T(key));
      button.type = "button";
      button.setAttribute("aria-label", T(key));
      button.addEventListener("click", () => shell.querySelector(selector)?.click());
      settingsHost.append(button);
    };
    const seg = (label, opts, current, onPick) => {
      const row = mEl("div", "mledform");
      row.append(mEl("span", "mledname", label));
      for (const [v, l] of opts) {
        const b = mEl("button", `mkeybtn${v === current ? " on" : ""}`, l);
        b.type = "button";
        b.addEventListener("click", () => onPick(v));
        row.append(b);
      }
      settingsHost.append(row);
    };
    group("cp_settings_display");
    if (theme) {
      seg(T("set_theme"), [["auto", T("set_theme_auto")], ["dark", T("set_theme_dark")], ["light", T("set_theme_light")]],
        theme.read(), (v) => { theme.apply(v, true); renderSettings(); });
    } else {
      seg(T("set_theme"), [["light", T("set_theme_light")], ["dark", T("set_theme_dark")]],
        savedTheme(), (v) => {
          try { localStorage.setItem(THEME_KEY, v); } catch { /* プライベートモード */ }
          applyTheme(v);
          renderSettings();                                  // 選択状態を描き直す
        });
    }
    // R98: 様式（3D オフィス／台帳）。localStorage を正にして再読み込み（?ui= は消す）
    const styleOptions = [[STYLES.APP, T("set_style_app")], [STYLES.ISO, T("set_style_iso")], [STYLES.PIXEL, T("set_style_pixel")],
      [STYLES.COCKPIT, T("set_style_cockpit")]];
    seg(T("set_style"), cockpit ? styleOptions.filter(([style]) => style !== STYLES.PIXEL) : styleOptions,
      resolveStyle(), (v) => { if (v !== resolveStyle()) switchStyle(v); });
    renderDisplaySettings(seg, renderSettings, (text) => settingsHost.append(mEl("p", "mnote", text)));
    // 🌐 サーバーの lang を切り替える（office_json.lang が正本＝PWA/通知の言語も揃う）
    seg(T("set_lang"), [["ja", "日本語"], ["en", "English"]], lang(), async (v) => {
      try {
        // デモはサーバーを持たないので、この画面の中だけで切り替える（/api/lang は叩かない）。
        // ポーリングが無い＝world 由来の文言は自分で貼り直さないと前の言語のまま残る。
        if (demo) { setLang(v); applyStaticStrings(); reapplyWorld(); renderSettings(); return; }
        await setServerLang(v);
        setLang(v);
        applyStaticStrings();
        renderSettings();
      } catch (err) {
        showToast(err.message, false);
      }
    });
    group("cp_settings_notifications");
    const notifyRow = mEl("label", "mcheck");
    const notify = mEl("input");
    notify.type = "checkbox";
    notify.id = "set-notify";
    notify.disabled = typeof Notification === "undefined";
    try {
      notify.checked = !notify.disabled && Notification.permission === "granted"
        && localStorage.getItem(NOTIFY_KEY) === "1";
    } catch { /* プライベートモード */ }
    const notifyHint = mEl("p", "mnote", T("set_notify_hint"));
    notifyHint.id = "set-notify-hint";
    notify.setAttribute("aria-describedby", notifyHint.id);
    notify.addEventListener("change", async () => {
      notify.disabled = true;
      try {
        const enabled = notify.checked;
        const permission = enabled ? await Notification.requestPermission() : "default";
        notify.checked = enabled && permission === "granted";
        localStorage.setItem(NOTIFY_KEY, notify.checked ? "1" : "0");
      } catch (err) {
        notify.checked = false;
        showToast(err.message, false);
      }
      notify.disabled = false;
    });
    notifyRow.append(notify, mEl("span", "", T("set_notify")));
    settingsHost.append(notifyRow, notifyHint);
    if (cockpit) renderCustomizationSettings(seg, renderSettings);
    group("cp_settings_phone");
    if (cockpit) {
      shortcut("cp_settings_pair", "#btn-pair");
      shortcut("cp_settings_run", "#btn-run");
    }
    const dialog = mEl("div", "setting-dialog");
    settingsHost.append(dialog);
    (demo ? Promise.reject(new Error(T("demo_server_only"))) : pairList()).then((data) => {
      if (modal.contains(dialog)) renderDialogSetting(dialog, "set-dialog", data);
    }).catch((err) => {
      if (modal.contains(dialog)) dialog.append(mEl("p", "mnote merr", err.message));
    });
    if (!cockpit) renderCustomizationSettings(seg, renderSettings);
    renderStreamSettings();
    // R128 #4: Bot の節（出勤の口の Bot の一覧・追加・削除・消音・登録コマンドのコピー＝ui/hud/bots.js）。cockpit は ┤ Bot ├ の群、
    // ほかの様式（app・iso・pixel）は節の見出しと上の罫を自分で出す
    group("cp_settings_bots");
    renderBotPanel(settingsHost, { T, lang, mEl, showToast, demo, separate: !cockpit });
    if (cockpit) {
      group("cp_settings_diagnostics");
      shortcut("cp_settings_feedback", "#cp-feedback");      // R105: β の報告と診断情報のコピー（ui/hud/beta.js）
      shortcut("cp_settings_resources", "#btn-res");
      shortcut("help_title", "#btn-help");
      group("cp_settings_about");
      settingsHost.append(mEl("p", "mnote", T("cp_settings_about_note")));
      const ver = version();
      if (ver) settingsHost.append(mEl("p", "mnote", T("beta_version", ver)));
      shortcut("cp_settings_thanks", "#cp-feedback");
    }
    openModal();
  };
  shell.querySelector("#btn-settings").addEventListener("click", renderSettings);

  return { getTemplates: () => TEMPLATES, openTemplateEditor };
}
