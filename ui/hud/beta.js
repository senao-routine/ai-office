import { api } from "/ui/platform/api.js";
import { lang } from "/ui/hud/strings.js";

// R105: メンバー先行βの入口（足元の版と［フィードバック］・設定の「診断」「このアプリ」から開く 1 枚のモーダル）。
// 中身は 3 つ: 報告の窓口（ui/beta.json の feedbackUrl＝配布ごとに書く・空なら β の案内の場所を言う）／
// 診断情報（GET /api/diagnostics＝数と状態だけ＋この画面の形。**見せてから本人がコピーする**＝テレメトリではない）／
// スペシャルサンクス（ui/credits.json＝ニックネームと段だけ・tests/test_credits.py が形を見張る）。
// 外部へは何も送らない（フォームを開くのは本人のクリック＝新しいタブ）。

const FORM_RE = /^https:\/\/[^\s"'<>]+$/;

async function readJson(path) {
  try {
    const r = await fetch(path, { cache: "no-store" });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

/** 名簿を段の重い順（C→B→A）・同じ段は書いた順に並べ、名前だけを返す。形の壊れた行は捨てる */
export function creditNames(data) {
  const people = Array.isArray(data?.people) ? data.people : [];
  const rank = { C: 0, B: 1, A: 2 };
  return people
    .map((p, i) => ({ name: typeof p?.name === "string" ? p.name.trim() : "", tier: p?.tier, i }))
    .filter((p) => p.name && p.name.length <= 24 && p.tier in rank)
    .sort((a, b) => rank[a.tier] - rank[b.tier] || a.i - b.i)
    .map((p) => p.name);
}

/** ctx: shell, T, modals: { modal, openModal, mEl }, getWorld: () => office_json, style: 様式名 */
export function init({ shell, T, modals, getWorld, style }) {
  const { modal, openModal, mEl } = modals;

  const screenLine = () => {
    const theme = document.documentElement.dataset.theme || "auto";
    return T("beta_screen", style, `${innerWidth}×${innerHeight}`, theme);
  };

  const open = async () => {
    const version = getWorld()?.app?.version || "?";
    modal.replaceChildren(mEl("b", "mtitle", T("beta_title")), mEl("p", "mnote", T("beta_lead")));

    // 報告の窓口
    const formRow = mEl("div", "beta-form");
    modal.append(formRow);
    readJson("/ui/beta.json").then((cfg) => {
      if (!modal.contains(formRow)) return;
      const url = typeof cfg?.feedbackUrl === "string" && FORM_RE.test(cfg.feedbackUrl) ? cfg.feedbackUrl : "";
      if (url) {
        const a = mEl("a", "abtn beta-open", T("beta_form"));
        a.href = `${url}${url.includes("?") ? "&" : "?"}v=${encodeURIComponent(version)}`;
        a.target = "_blank"; a.rel = "noopener noreferrer";
        formRow.append(a);
      } else {
        formRow.append(mEl("p", "mnote", T("beta_form_none")));
      }
    });

    // 診断情報（見せてからコピー）
    modal.append(mEl("b", "beta-h", T("beta_diag_title")), mEl("p", "mnote", T("beta_diag_note")));
    const pre = mEl("pre", "beta-diag", T("beta_diag_loading"));
    const copy = mEl("button", "abtn beta-copy", T("beta_diag_copy"));
    copy.type = "button"; copy.disabled = true;
    modal.append(pre, copy);
    let text = "";
    try {
      // plan.md #5: 本文だけ常に日本語で生成されUI表示言語と食い違っていた（_LANG固定）。
      // いま画面に出している言語をクエリで渡す（サーバーは不正値・無指定を既定へ落とす）。
      const r = await api(`/api/diagnostics?lang=${encodeURIComponent(lang())}`);
      text = `${String(r?.text || "").trim()}\n${screenLine()}`;
    } catch (e) {
      text = `${T("beta_diag_fail")}（${String(e?.message || e).slice(0, 80)}）\n${T("beta_version", version)}\n${screenLine()}`;
    }
    if (!modal.contains(pre)) return;
    pre.textContent = text;
    copy.disabled = false;
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text);
        copy.textContent = T("beta_diag_copied");
      } catch {
        // コピーできない環境（権限なし）は中身を選んで本人に任せる
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
        copy.textContent = T("beta_diag_select");
      }
    });

    // スペシャルサンクス
    modal.append(mEl("b", "beta-h", T("beta_thanks_title")));
    const thanks = mEl("p", "mnote beta-thanks", "…");
    modal.append(thanks);
    readJson("/ui/credits.json").then((data) => {
      if (!modal.contains(thanks)) return;
      const names = creditNames(data);
      thanks.textContent = names.length ? names.join(" · ") : T("beta_thanks_empty");
    });
    openModal();
  };

  return { open };
}
