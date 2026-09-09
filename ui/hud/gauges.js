// 左サイドバーの経費ゲージ。取得時にも最新の world を参照する。
import { agoStr } from "/ui/core/world.js";
import { getStatusBoard } from "/ui/platform/api.js";
import { frozen } from "/ui/platform/clock.js";

// R72: 課金方式の正本はサーバー（status_board の billing）。ただし app/ 未更新など
// 版ズレで billing 欠落のときにグループが空になると「全部消えた」ように見えるので、
// kind から同じ規則で補う（旧server後方互換の掟＝サーバー正本＋クライアント補完）。
const BILLING_FALLBACK = { tokens: "subscription", gauge: "subscription",
  login: "subscription", external: "apikey", api: "apikey", ledger: "manual" };
export const billingOf = (pr) => pr?.billing || BILLING_FALLBACK[pr?.kind] || "";
export const fmtTok = (v) => v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` :
  v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : String(v);

/** ctx: shell, T, lang, getWorld() */
export function init({ shell, T, lang, getWorld, onPins = () => {} }) {
  let kicked = false;
  // ── 経費ゲージ（左サイドバー常設・status_board 60秒ポーリング） ──────
  const gaugesEl = shell.querySelector("#gauges");
  const creditsCard = shell.querySelector("#gcredits");
  const creditsBody = shell.querySelector("#gcreditbody");
  const moneyCard = shell.querySelector("#gmoney");
  const moneyBody = shell.querySelector("#gmoneybody");
  const gEl = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const gaugeRow = (label, ratio, sub, warn) => {
    const row = gEl("div", "grow");
    const head = gEl("div", "ghead");
    head.append(gEl("span", "", label), gEl("b", warn ? "gwarn" : "", sub));
    const track = gEl("div", "gbar");
    const fill = gEl("i", warn ? "gfill warn" : "gfill");
    fill.style.width = `${Math.round(Math.min(1, Math.max(0, ratio)) * 100)}%`;
    track.append(fill);
    row.append(head, track);
    return row;
  };
  // R55: 旧UIのCodexバー式リッチゲージ（プロバイダ名+planチップ+%大表示+太バー+窓/リセット残）
  const fmtRemain = (resetsAt, nowEpoch) => {
    if (!resetsAt || !nowEpoch) return "";
    const s = Math.max(0, resetsAt - nowEpoch);
    if (s >= 172800) return T("g_remain_d", Math.round(s / 86400));
    if (s >= 5400) return T("g_remain_h", Math.round(s / 3600));
    return T("g_remain_m", Math.max(1, Math.round(s / 60)));
  };
  const winLabel = (minutes) => {
    if (!minutes) return "";
    if (minutes <= 600) return T("g_win_5h");
    if (minutes >= 9000) return T("g_win_week");
    return T("g_win_h", Math.round(minutes / 60));
  };
  const provBar = (pct, warn) => {
    const track = gEl("div", "gbar big");
    const fill = gEl("i", warn ? "gfill warn" : "gfill");
    // 3%や0%でも「ゲージが存在する」ことが見えるように最小フィルを敷く
    fill.style.width = `${Math.max(2, Math.round(Math.min(100, Math.max(0, pct))))}%`;
    track.append(fill);
    return track;
  };
  const provBlock = (name, plan, pct) => {
    const box = gEl("div", "gprov");
    const head = gEl("div", "gprovhead");
    head.append(gEl("b", "gname", name));
    if (plan) head.append(gEl("span", "gplan", plan));
    if (pct !== null) {
      head.append(gEl("b", `gpct${pct >= 80 ? " gwarn" : ""}`, `${Math.round(pct)}%`));
    }
    box.append(head);
    return box;
  };
  // R57: ログイン中アカウントのチップ（emailローカル部・title=フル。ローカル表示専用）。
  // ヘッダ行は名前+plan+%で幅が尽きる＝チップは専用行に置く（詰め込むと縦書き潰れ・実測1敗）
  const acctChip = (box, email) => {
    if (!email) return;
    const chip = gEl("span", "gacct", String(email).split("@")[0]);
    chip.title = String(email);
    const line = gEl("div", "gacctline");
    line.append(chip);
    box.append(line);
  };
  const refreshGauges = async () => {
    if (document.hidden) return;
    if (!getWorld()) return;
    let sb;
    try {
      sb = await getStatusBoard();
    } catch {
      gaugesEl.hidden = true;                  // エラーは黙って畳む
      onPins([]);
      return;
    }
    const jpy = sb.fx?.jpyPerUsd || 155;
    const usdJpy = (v) => `$${v.toFixed(2)} ≈ ¥${Math.round(v * jpy).toLocaleString()}`;
    // クレジット消費（サブスク枠の使用率＝%）とコスト（¥）は別物なので分けて描く。
    // R55: 旧UIのCodexバー式＝プロバイダごとのブロック（planチップ・%大表示・太バー・
    // 窓ラベル+リセット残時間・secondary窓は2本目）。実データが無い数値は出さない掟のまま
    // （Claudeのサブスク枠%はAPIが無い＝バー化しない・トークン実測だけを見せる）。
    const nowEpoch = Number(sb.generatedAt) || getWorld()?.generatedAt || 0;
    creditsBody.replaceChildren();
    moneyBody.replaceChildren();
    // R91: ヘッダーへ出す常設ピン。ドロワーを開かなくても残枠が見える（本人要望
    // 「どれぐらいゲージが減ってきたかが随時表示された方が、動かすときの目安になる」）。
    // rank が小さいほど左＝実測のサブスク枠を先頭に、推定と従量課金は後ろ。
    const pins = [];
    const pin = (rank, who, window, pct) => {
      if (pct == null || !Number.isFinite(Number(pct))) return;
      const v = Math.max(0, Math.min(100, Number(pct)));
      pins.push({ rank, who, window, label: [who, window].filter(Boolean).join(" "),
                  pct: v, warn: v >= 80 });
    };
    const renderProv = (pr) => {
      if (pr.kind === "gauge" && pr.status === "ok") {
        const pct = pr.usedPercent ?? 0;
        const box = provBlock(pr.label || pr.id, pr.plan || "", pct);
        acctChip(box, pr.account?.email);
        pin(1, pr.label || pr.id, winLabel(pr.windowMinutes), pct);
        box.append(provBar(pct, pct >= 80));
        const sub = [winLabel(pr.windowMinutes), fmtRemain(pr.resetsAt, nowEpoch)]
          .filter(Boolean).join(" · ");
        if (sub) box.append(gEl("span", "gsub", sub));
        const sec = pr.secondary;
        if (sec && sec.usedPercent != null) {
          box.append(provBar(sec.usedPercent, sec.usedPercent >= 80));
          const sub2 = [winLabel(sec.windowMinutes), fmtRemain(sec.resetsAt, nowEpoch),
            `${Math.round(sec.usedPercent)}%`].filter(Boolean).join(" · ");
          box.append(gEl("span", "gsub", sub2));
        }
        // R57: 別アカウントの前回確認スナップショット＝2アカウント運用でも両方の残枠が見える
        for (const ac of (pr.accounts || []).filter((a) => !a.active).slice(0, 2)) {
          if (ac.usedPercent == null) continue;
          const bar = provBar(ac.usedPercent, ac.usedPercent >= 80);
          bar.classList.add("pale");
          box.append(bar);
          const who = String(ac.email || ac.id || "?").split("@")[0];
          const ago = (nowEpoch && ac.seenAt)
            ? agoStr(Math.max(0, nowEpoch - ac.seenAt), lang()) : "";
          box.append(gEl("span", "gsub",
            [who, ago ? `${T("g_prev_seen")} ${ago}` : T("g_prev_seen"),
             `${Math.round(ac.usedPercent)}%`].join(" · ")));
        }
        creditsBody.append(box);
      } else if (pr.kind === "external" && pr.connected && pr.cap) {
        const pct = pr.pct ?? 0;
        const box = provBlock(pr.label || pr.id, "", pct);
        pin(3, pr.label || pr.id, "", pct);
        box.append(provBar(pct, pct >= 80));
        box.append(gEl("span", "gsub",
          `${fmtTok(pr.used || 0)} / ${fmtTok(pr.cap)}`));
        creditsBody.append(box);
      } else if (pr.kind === "external" && pr.connected && pr.monthUsd != null) {
        // R72: 枠(cap)を持たない従量プロバイダ（OpenAI 管理キー）。旧実装は cap 必須の
        // 分岐しか無く、当月額を取得できていてもドロワーから丸ごと消えていた。
        const box = provBlock(pr.label || pr.id, "", null);
        box.append(gEl("span", "gsub",
          `${pr.sinceDay ? T("res_month_since", usdJpy(pr.monthUsd), pr.sinceDay)
            : T("res_month", usdJpy(pr.monthUsd))}`));
        box.append(gEl("span", "gsub gnolimit", T("api_no_limit")));
        creditsBody.append(box);
        const mrow = gEl("div", "ghead");
        mrow.append(gEl("span", "", T("g_month", pr.label || pr.id)),
          gEl("b", "", `≈¥${Math.round(pr.monthUsd * jpy).toLocaleString()}`));
        moneyBody.append(mrow);
      } else if (pr.kind === "tokens" && pr.tokens?.byModel) {
        // Claude: R61=statusLine capture の実測枠%（rate_limits）が新鮮(15分以内)なら
        // それを主役にし、推定のペースゲージは隠す（実測>推定・両方出すと二重表示）。
        // 実測が無い/古いときだけ従来の「直近7日の5hピーク比」ペース（R55.1）へ戻す。
        const sq = pr.subscription;
        const live = (sq && sq.staleSec != null && sq.staleSec < 900
          && (sq.fiveHour || sq.sevenDay)) ? sq : null;
        const pace = !live && pr.pace && pr.pace.pct != null ? pr.pace : null;
        const headPct = live ? (live.fiveHour?.pct ?? live.sevenDay?.pct)
          : (pace ? pace.pct : null);
        const box = provBlock(pr.label || pr.id,
          live ? T("g_live_chip") : (pace ? T("g_pace_chip") : ""), headPct);
        acctChip(box, live?.account?.email || pr.account?.email);
        if (live) {
          for (const [w, lab] of [[live.fiveHour, T("g_win_5h")],
            [live.sevenDay, T("g_win_week")]]) {
            if (!w) continue;
            pin(0, pr.label || pr.id, lab, w.pct);
            box.append(provBar(w.pct, w.pct >= 80));
            box.append(gEl("span", "gsub",
              [lab, fmtRemain(w.resetsAt, nowEpoch), `${Math.round(w.pct)}%`]
                .filter(Boolean).join(" · ")));
          }
        } else if (pace) {
          pin(2, pr.label || pr.id, T("g_pace_chip"), pace.pct);
          box.append(provBar(pace.pct, pace.pct >= 90));
          box.append(gEl("span", "gsub", T("g_pace_sub", fmtTok(pace.peak5h || 0))));
        }
        const today = pr.tokens.today?.total || 0;
        const last5h = pr.tokens.last5h?.total || 0;
        box.append(gEl("span", "gsub",
          `${T("g_tok_today", fmtTok(today))} · ${T("g_tok_5h", fmtTok(last5h))}`));
        // R57: 2アカウント以上を観測している日は、アカウント別の当日消費ミニ行を出す
        if ((pr.accounts || []).length >= 2) {
          for (const ac of pr.accounts.slice(0, 3)) {
            const row2 = gEl("div", `gacctrow${ac.active ? " on" : ""}`);
            row2.append(gEl("i", "gadot"),
              gEl("span", "galab", String(ac.email || ac.id || "?").split("@")[0]),
              gEl("b", "", T("g_tok_today", fmtTok(ac.todayTok || 0))));
            box.append(row2);
          }
        }
        creditsBody.append(box);
        const usd = Object.values(pr.tokens.byModel).reduce((a, m) => a + (m.usd || 0), 0);
        const row = gEl("div", "ghead");
        row.append(gEl("span", "", T("g_today", pr.label)),
          gEl("b", "", `≈¥${Math.round(usd * jpy).toLocaleString()}`));
        moneyBody.append(row);
      } else if (pr.kind === "login" && pr.status === "ok") {
        // R72: loggedIn を見ずに常に「ログイン済み」と出していた（未ログインでも
        // 繋がって見える誤報＝モーダル側の表示とも食い違っていた）
        const box = provBlock(pr.label || pr.id, "", null);
        box.append(gEl("span", pr.loggedIn ? "gsub gok" : "gsub",
          pr.loggedIn ? T("res_login_yes") : T("res_login_no")));
        creditsBody.append(box);
      } else if (pr.kind === "api" && pr.status === "ok") {
        // R63: 上限が判明しているものだけバー。取れないものは金額テキストのみ
        //（推測の%を作らない＝実測と推定を混ぜない掟の系）
        const money = (v) => (pr.currency === "CNY" ? `CN¥${v.toFixed(2)}`
          : pr.currency === "JPY" ? `¥${Math.round(v).toLocaleString()}`
          : `$${v.toFixed(2)}`);
        const box = provBlock(pr.label || pr.id,
          pr.limitSource === "manual" ? T("api_budget_chip") : "",
          pr.pct != null ? pr.pct : null);
        if (pr.pct != null) {
          pin(3, pr.label || pr.id, "", pr.pct);
          box.append(provBar(pr.pct, pr.pct >= 80));
          box.append(gEl("span", "gsub",
            `${money(pr.spentMonth || 0)} / ${money(pr.limit)}`));
        } else if (pr.spentMonth != null) {
          box.append(gEl("span", "gsub", money(pr.spentMonth)));
          box.append(gEl("span", "gsub gnolimit", T("api_no_limit")));
        } else if (pr.balance != null) {
          box.append(gEl("span", "gsub", T("api_balance", money(pr.balance))));
        } else {
          box.append(gEl("span", "gsub gnolimit", T("api_no_limit")));
        }
        creditsBody.append(box);
      }
    };
    // R72: 定額サブスクの枠と、APIキーの従量課金を見出しで分ける（ユーザーFB
    // 「サブスクプランなのかAPIキー消費なのか分かるようにしてほしい」）。
    const byBilling = (b) => (sb.providers || []).filter((p) => billingOf(p) === b);
    for (const [billing, head] of [["subscription", "res_grp_sub"], ["apikey", "api_head"]]) {
      const group = byBilling(billing);
      if (!group.length) continue;
      creditsBody.append(gEl("div", "gbillhead", T(head)));
      group.forEach(renderProv);
    }
    if (sb.spend) {
      const total = Math.round((sb.spend.totalJpy || 0) + (sb.spend.totalUsd || 0) * jpy);
      const row = gEl("div", "ghead");
      row.append(gEl("span", "", T("g_fixed")), gEl("b", "", `≈¥${total.toLocaleString()}`));
      moneyBody.append(row);
    }
    // R85-3: 📡 中継使用量（office_json.relay＝R80の自己防衛メーター。従来はPWAだけが
    // 表示し、Cloudflare無料枠を管理する当人がMacで見えなかった）。閾値はPWAと同じ70/90%。
    const rl = getWorld()?.relay;
    if (rl && typeof rl.pct === "number") {
      const lvl = Number(rl.level) || 0;
      const sub = `${Math.round(rl.pct)}%` +
        (lvl >= 2 ? T("relay_throttled") : lvl >= 1 ? T("relay_slowed") : "");
      creditsBody.append(gEl("div", "gbillhead", T("relay_head")),
        gaugeRow(T("relay_today"), (Number(rl.pct) || 0) / 100, sub, rl.pct >= 70));
      pin(4, T("relay_head"), "", rl.pct);
    }
    creditsCard.hidden = creditsBody.children.length === 0;
    moneyCard.hidden = moneyBody.children.length === 0;
    gaugesEl.hidden = creditsCard.hidden && moneyCard.hidden;
    pins.sort((a, b) => a.rank - b.rank);
    onPins(pins.slice(0, 4));
  };
  const gaugeTimer = frozen ? 0 : setInterval(refreshGauges, 60000);

  return {
    start: () => { if (!kicked) { kicked = true; refreshGauges(); } },
    dispose: () => clearInterval(gaugeTimer),
  };
}
