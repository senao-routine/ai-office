// R98-W1: 様式非依存の shell 補助。iso（3D・3 カラム）と pixel（フロア帯 × 台帳）の両方が使う。
//
// テンプレート（DOM の並び）は様式ごとに持つ＝ここには置かない。置くのは「同じ id には同じ文言」
// 「ヘッダーの枠ゲージのピン」「到着バッジ」「ヘルプ」＝どの様式でも同じ振る舞いになるべきもの。
// 文言を貼る先は **無ければ飛ばす**（様式によって持たない部品がある。iso は全部持つ）。
// 契約の番人= tests/hud_ids.test.mjs（literal で掴む id/class は必須・helper 経由は任意）。

/**
 * 様式によっては持たない要素を掴む（無ければ null）。id を literal で直接 querySelector した箇所は
 * 「どの様式にも必須」の契約として tests/hud_ids.test.mjs が数える＝任意の要素はこちらで掴む。
 */
export const optional = (shell, sel) => shell.querySelector(sel);

/** 小さな要素工場。ui/iso/index.js から移設（各 hud 部品は ctx.el で受け取る）。 */
export function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

/** 🆕/⎇ の到着バッジ。変化があれば true（呼び手が寸法キャッシュを捨てる）。 */
export function paintArrivalBadge(host, arrival, T) {
  let badge = host.querySelector(".arrival-badge");
  const text = [arrival?.isNew ? T("hire_new_badge") : "",
    arrival?.slug ? T("hire_branch_badge", arrival.slug) : ""].filter(Boolean).join(" ");
  if (!text) { if (badge) { badge.remove(); return true; } return false; }
  if (!badge) { badge = el("span", "arrival-badge"); host.append(badge); }
  badge.title = text;
  if (badge.textContent === text) return false;
  badge.textContent = text;
  return true;
}

/** 言語で変わる静的クローム（テンプレート直書きだった部分）。mount と言語切替時に貼る。 */
export function applyStaticStrings(shell, T) {
  const put = (sel, key) => { const n = shell.querySelector(sel); if (n) n.textContent = T(key); };
  const title = (sel, key) => { const n = shell.querySelector(sel); if (n) n.title = T(key); };
  put("#gtitle-credits", "gauge_credits");
  put("#gtitle-money", "gauge_money");
  put("#btn-newproj", "btn_newproj");
  put("#btn-hire", "btn_hire");
  put("#btn-launch", "btn_launch");
  put("#btn-pair", "btn_pair");
  put("#btn-run", "btn_run");
  put("#btn-res", "btn_res");
  put("#btn-settings", "btn_settings");
  for (const b of shell.querySelectorAll(".admin .abtn")) b.title = b.textContent;
  put("#greet", "office_fallback");
  put("#sub", "loading");
  title("#sheetsnd", "snd_title");
  title("#sheetterm", "term_title");
  // R91: 追加した2つも同じ経路で貼り直す（init で1度だけ付けると言語切替で置き去りになる）
  for (const [id, key] of [["#sheetarch", "avatar_customize"], ["#sheetwide", "sheet_wide"]]) {
    const b = shell.querySelector(id);
    if (!b) continue;
    b.title = T(key);
    b.setAttribute("aria-label", T(key));
  }
  const compose = shell.querySelector("#composeinput");
  if (compose) compose.placeholder = T("compose_ph");
  // R80-B6: 3D不可の案内は mount 時（＝office_json の lang 到着前）に作られるので、
  // 言語が確定したここで必ず貼り直す（旧: 日本語UIに英語の案内が出ていた）
  put("#no3d", "no3d");
  put("#no3d-list", "no3d_list");
  put("#title-tasks", "card_tasks");
  put("#title-hist", "card_hist");
  put("#title-agents", "board_title");
  put("#gauges-more", "gauges_more");
  put("#rail-toggle", "rail_list");
  const help = shell.querySelector("#btn-help");
  if (help) { help.title = T("help_title"); help.setAttribute("aria-label", T("help_title")); }
  put("#viewreset", "view_reset");
}

/**
 * ヘッダーの枠ゲージ（R91）: 開かなくても残枠の減り方が常に見える。値は gauges.js が描いたのと
 * 同じ実測 pin（onPins）＝DOM を読み直して要約する旧実装（表示が変わると壊れる）をやめた。
 * ctx: shell, T
 */
export function initUsage({ shell, T }) {
  const usage = shell.querySelector(".usage");
  const gaugesEl = shell.querySelector("#gauges");
  const usageSummary = shell.querySelector("#usage-summary");
  const setUsageOpen = (open) => {
    usage.classList.toggle("open", open);
    usageSummary.setAttribute("aria-expanded", String(open));
    gaugesEl.inert = !open;
  };
  setUsageOpen(false);
  usageSummary.addEventListener("click", () => setUsageOpen(!usage.classList.contains("open")));
  shell.addEventListener("click", (e) => {
    if (!usage.contains(e.target)) setUsageOpen(false);
  });
  const paintPins = (pins) => {
    usageSummary.replaceChildren();
    if (!pins.length) {
      usageSummary.textContent = T("gauge_credits");
      usageSummary.title = T("gauge_credits");
      usage.hidden = gaugesEl.hidden;
      return;
    }
    const words = [];
    let prevWho = null;
    for (const p of pins) {
      // 同じプロバイダが続くときは名前を繰り返さない（"Claude Code 5時間枠 / 週間枠"）
      const short = p.who && p.who === prevWho && p.window ? p.window : (p.label || p.who || "");
      prevWho = p.who || null;
      const chip = el("span", "gpin");
      const bar = el("span", "gpbar");
      const fill = el("i", p.warn ? "gpfill warn" : "gpfill");
      fill.style.width = `${Math.max(2, Math.round(p.pct))}%`;
      bar.append(fill);
      chip.append(el("span", "gplab", short), bar,
        el("b", p.warn ? "gppct warn" : "gppct", `${Math.round(p.pct)}%`));
      usageSummary.append(chip);
      words.push(`${p.label || p.who} ${Math.round(p.pct)}%`);
    }
    usageSummary.title = words.join(" · ");
    usage.hidden = gaugesEl.hidden;
  };
  paintPins([]);
  shell.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && usage.classList.contains("open")) {
      setUsageOpen(false);
      usageSummary.focus();
      e.stopPropagation();
    }
  }, true);
  return { paintPins, setUsageOpen };
}

/**
 * ヘルプ（キー一覧・URL）。`?` キーの配線は様式側（Escape の畳み方が様式で違うため）。
 * ctx: shell, T, modals, blocked() ＝ ダイジェスト／リプレイ／配信が前面のときは開かない
 *（ヘルプの裏でダイジェストの数字キーが生き続けて誤送信する・別モデルレビュー）。
 */
export function initHelp({ shell, T, modals: { modal, openModal, mEl }, blocked }) {
  const openHelp = () => {
    if (blocked?.()) return;
    modal.replaceChildren(mEl("b", "mtitle", T("help_title")),
      mEl("b", "msubtitle", T("help_keys_title")), mEl("p", "mnote", T("help_keys")),
      mEl("b", "msubtitle", T("help_urls_title")), mEl("p", "mnote", T("help_urls")));
    modal.dataset.kind = "help";
    openModal();
  };
  shell.querySelector("#btn-help").addEventListener("click", openHelp);
  return openHelp;
}
