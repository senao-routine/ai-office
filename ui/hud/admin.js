// 新プロジェクト・起動・ペアリング・レシピ・設定・リソースの管理画面。
import {
  budgetApply, fxApply, getKeysStatus, getRecipes, getStatusBoard, getTemplates,
  launchProject, newProject, pairList, pairNew, pairRevoke, pickProjectFolder,
  setOfficeKey, setRecipes, setServerLang, setTemplates, spendApply,
} from "/ui/platform/api.js";

/** ctx: root, shell, T, lang, setLang, getWorld(), applyStaticStrings(),
 *  showToast(), billingOf(), fmtTok(), modals */
export function init({ root, shell, T, lang, setLang, getWorld, applyStaticStrings,
  showToast, billingOf, fmtTok, renderStreamSettings = () => {}, renderCustomizationSettings = () => {}, modals: { modal, openModal, closeModal, mEl } }) {
  // R82: 定型文エディタ（8件×120字・保存でスマホにも同期）
  let TEMPLATES = [];
  const refreshTemplates = async () => {
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
        await newProject(picked.path, nameIn.value.trim(),
          { launch: cbLaunch.checked });
        showToast(T("np_joined", nameIn.value.trim() || picked.suggest));
        closeModal();
      } catch (err) {
        go.disabled = false;
        showToast(T("reg_fail", err.message), false);
      }
    });
    modal.append(go);
    nameIn.focus();
  });

  const renderPairPanel = async () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_pair")),
      mEl("p", "mnote", T("pair_issuing")));
    openModal();
    let dev;
    try {
      dev = await pairNew(T("pair_device_label"));
    } catch (err) {
      // 中継未設定などはサーバーの文言をそのまま出す（Pro壁はR84撤去済み）
      modal.replaceChildren(mEl("b", "mtitle", T("btn_pair")),
        mEl("p", "mnote merr", err.message));
      return;
    }
    modal.replaceChildren(mEl("b", "mtitle", T("btn_pair")));
    if (dev.pairUrl) {
      if (dev.qrSvg) {
        const img = mEl("img", "mqr");
        img.alt = T("pair_qr_alt");
        img.src = `data:image/svg+xml;utf8,${encodeURIComponent(dev.qrSvg)}`;
        modal.append(img);
      }
      modal.append(mEl("p", "mnote", T("pair_scan")));
      const copy = mEl("button", "mgo", T("pair_copy"));
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
    try {
      const { devices } = await pairList();
      if (devices?.length) {
        const list = mEl("div", "mdevices");
        list.append(mEl("b", "msub", T("pair_devices", devices.length)));
        for (const d of devices) {
          const row = mEl("div", "mdev");
          // R86-H: 「どれが今も使える鍵か」を出す。57件・全部「スマホ」表示で
          // 見分けられず、有効な鍵が29個ぶら下がっていた（実測）。
          const name = mEl("span", "", d.label || d.device_id);
          name.append(mEl("span", "devmeta", d.state === "active"
            ? T("pair_active", d.daysLeft) : T("pair_dead")));
          row.append(name);
          const rv = mEl("button", "mrevoke", T("pair_revoke"));
          rv.type = "button";
          rv.addEventListener("click", async () => {
            try {
              await pairRevoke(d.device_id);
              showToast(T("pair_revoked"));
              renderPairPanel();
            } catch (err) {
              showToast(err.message, false);
            }
          });
          row.append(rv);
          list.append(row);
        }
        modal.append(list);
      }
    } catch { /* 一覧はベストエフォート */ }
  };
  shell.querySelector("#btn-pair").addEventListener("click", renderPairPanel);

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
    if (!recipes.length) modal.append(mEl("p", "mnote", T("run_empty")));
    for (const r of recipes) {
      const row = mEl("div", "mkeyrow");
      row.append(mEl("b", null, (r.dangerous ? "⚠️ " : "▶ ") + r.label),
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
        cwd: "/Users/you/path/to/your-project",
        timeoutSec: 30, returnOutput: "tail",
      };
      ta.value = JSON.stringify({ recipes: [...cur, example] }, null, 1);
      showToast(T("run_sample_note"));
    });
    modal.append(mEl("p", "mnote", T("run_hint")), ta, sample, save);
  };
  shell.querySelector("#btn-run").addEventListener("click", renderRunPanel);

  // ── ⚡リソース（status_board 読み取りビュー） ─────────────────────
  // R66: 🔑連携はPro未解錠(403)でも使えるべき＝status_board失敗時にも単独描画できる関数
  const renderKeysSection = async (sb) => {
    // 🔑 アカウント連携（R54: 旧UIの連携設定を移植）。Claude/Codex/Gemini は接続状態と
    // 手順ヒント・key型（OpenAI/X等）は行内フォームで保存（/api/keys/set・値はマスク入力）
    let ks = null;
    try {
      ks = await getKeysStatus();
    } catch { /* 取得失敗時はセクションごと出さない（嘘の状態を見せない） */ }
    if (ks?.providers?.length) {
      const NAME_BY_ID = { openai_key: "OPENAI_API_KEY", x_api: "X_BEARER_TOKEN",
                           openai_usage: "OPENAI_ADMIN_KEY",
                           // R65: R63のAPIプロバイダが未登録で接続ボタンが出なかった実バグ修正
                           openrouter: "OPENROUTER_API_KEY", moonshot: "MOONSHOT_API_KEY",
                           deepseek: "DEEPSEEK_API_KEY", groq: "GROQ_API_KEY" };
      // R66: 「いま: <アカウント>」ガイド用（statusBoardは既に取得済み=追加fetchしない）
      const acctEmail = (id) => {
        const em = (sb?.providers || []).find((p2) => p2.id === id)?.account?.email || "";
        return em ? em.split("@")[0] : "";
      };
      const sec = mEl("div", "mkeys");
      sec.append(mEl("b", "msub", T("keys_head")));
      // R66ユーザーFB「接続方法が分かりづらい」: 方式別2グループ＝
      // 🅰 自動（ターミナルでログインするだけ・キー入力UIを出さない） / 🅱 APIキーを貼る
      const grpA = mEl("div", "mkeygrp");
      grpA.append(mEl("b", "mkeygrph", T("keys_grp_auto")),
        mEl("p", "mkeygrpsub", T("keys_grp_auto_sub")));
      const grpB = mEl("div", "mkeygrp");
      grpB.append(mEl("b", "mkeygrph", T("keys_grp_key")),
        mEl("p", "mkeygrpsub", T("keys_grp_key_sub")));
      for (const pr of ks.providers) {
        const row = mEl("div", "mkeyrow");
        const head = mEl("div", "mkeyhead");
        head.append(mEl("i", "mkeydot" + (pr.connected ? " on" : "")),
          mEl("span", "mkeyname", pr.label || pr.id));
        const keyName = NAME_BY_ID[pr.id];
        if (pr.mode !== "key") {
          // 🅰: バッジ「自動」＋ガイド1行（Claude/Codexは今のアカウントも見せる）
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
            // R65: 解除（value=""で行削除）。↻再送と同じ2クリック制＝誤爆ガード
            const rv = mEl("button", "mkeyrevoke", T("keys_revoke"));
            rv.type = "button";
            let armed = 0;
            rv.addEventListener("click", async () => {
              if (!armed) {
                armed = setTimeout(() => { armed = 0; rv.textContent = T("keys_revoke"); }, 3000);
                rv.textContent = T("keys_revoke_arm");
                return;
              }
              clearTimeout(armed);
              rv.disabled = true;
              try {
                await setOfficeKey(keyName, "");
                showToast(T("keys_revoked"));
                shell.querySelector("#btn-res").click();
              } catch (err) {
                rv.disabled = false;
                showToast(err.message, false);
              }
            });
            head.append(rv);
          }
        }
        row.append(head, mEl("i", "mkeyhint", guideParts.filter(Boolean).join(" · ")));
        grpB.append(row);
      }
      sec.append(grpA, grpB);
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
  // クレジットのゲージをクリック→⚡（アカウント連携・台帳がある画面）を開く（R54ユーザーFB）
  shell.querySelector("#gauges").addEventListener("click", () => {
    shell.querySelector("#btn-res").click();
  });

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
        row.textContent = `▶ ${it.name || it.projectId}`;
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
  const savedTheme = () => {
    try { return localStorage.getItem(THEME_KEY) || "light"; } catch { return "light"; }
  };
  const applyTheme = (t) => root.classList.toggle("th-dark", t === "dark");
  applyTheme(savedTheme());
  const renderSettings = () => {
    modal.replaceChildren(mEl("b", "mtitle", T("btn_settings")));
    const seg = (label, opts, current, onPick) => {
      const row = mEl("div", "mledform");
      row.append(mEl("span", "mledname", label));
      for (const [v, l] of opts) {
        const b = mEl("button", `mkeybtn${v === current ? " on" : ""}`, l);
        b.type = "button";
        b.addEventListener("click", () => onPick(v));
        row.append(b);
      }
      modal.append(row);
    };
    seg(T("set_theme"), [["light", T("set_theme_light")], ["dark", T("set_theme_dark")]],
      savedTheme(), (v) => {
        try { localStorage.setItem(THEME_KEY, v); } catch { /* プライベートモード */ }
        applyTheme(v);
        renderSettings();                                  // 選択状態を描き直す
      });
    // 🌐 サーバーの lang を切り替える（office_json.lang が正本＝PWA/通知の言語も揃う）
    seg(T("set_lang"), [["ja", "日本語"], ["en", "English"]], lang(), async (v) => {
      try {
        await setServerLang(v);
        setLang(v);
        applyStaticStrings();
        renderSettings();
      } catch (err) {
        showToast(err.message, false);
      }
    });
    renderCustomizationSettings(seg, renderSettings);
    renderStreamSettings();
    openModal();
  };
  shell.querySelector("#btn-settings").addEventListener("click", renderSettings);

  return { getTemplates: () => TEMPLATES, openTemplateEditor };
}
