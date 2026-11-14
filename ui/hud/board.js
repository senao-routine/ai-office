// R98-W1: 「誰が・何を」の 2 階層（プロジェクト → セッション）と、様式に依らないクロームの貼り替え。
// ui/iso/index.js から移設。描画（右レールの行／台帳の行）は様式側＝ここはデータ整形と共通部だけ。
import { activityGloss, agoStr, buildWorld, tidyActivity, zoneOf } from "/ui/core/world.js";
import { attnKind, errOf, waitMinutes, waitWord, withKindFields } from "/ui/core/kind.js";
import { deliveryChip } from "/ui/hud/delivery.js";
import { el, optional } from "/ui/hud/shell.js";

export const ZONES = ["desk", "meeting", "queue", "lounge", "external"];
export const zoneLabel = (T, z) => (ZONES.includes(z) ? T(`zone_${z}`) : "");

/** ❗の内容キー。質問文が変われば別の❗として扱う（回答済み楽観表示の解除判定に使う）。
 *  R100-0: 承認フックの 1 件ごとの番号（ask.askId）も含める＝同じセッションで 2 件並んだとき、1 件目に答えた後の
 *  2 件目が「回答済み・反映待ち」に化けてボタンが消えない（R100-0 のレビュー）。 */
export const attnKeyFor = (a) => {
  const id = a?.ask?.askId ? `#${a.ask.askId}` : "";
  return a?.question ? `q:${a.question}${id}` : `approval:${a?.session || ""}${id}`;
};

/**
 * b4-mac: ❗（とエラー）の 1 行。種類を必ず言う（許可待ち・計画の確認・質問・止まっている?・エラーで停止）＋待っている長さ。
 * 以前は質問文が無いと全部「N分 止まっています」＝本物の許可待ちも推定の停止も同じ文で、スマホの札とも食い違っていた。
 * 語はスマホと同じ（ui/core/kind.js・tests/ismute_parity.mjs）。トレイ・台帳・シート・通知・コックピットが使う。
 */
export function attnLine(T, a, lang = "ja") {
  if (!a) return "";
  const k = attnKind(a);
  const wait = waitWord(waitMinutes(a), lang);
  if (k === "perm") return T("attn_perm", a.ask?.tool || "", a.ask?.title || "", wait);
  if (k === "plan") return T("attn_plan", wait);
  if (k === "question") return T("attn_question", a.question || "", wait);
  if (k === "error") return T("attn_error", T(`err_${errOf(a).k}`), wait);
  if (k === "stuck") return T("attn_stuck", wait, a.stuckTool ? tidyActivity(a.stuckTool, 40) : "");
  return "";
}

/**
 * R128 #4: 消音の Bot（⚙ の Bot パネル＝台帳の muted・サーバーの employees の bot- の行が持つ）の session の集合。
 * Mac の音（ui/platform/sound.js）と画面の通知（ui/hud/tray.js）は、この Bot の相談・出勤では鳴らさない＝**鳴らさないだけ**
 *（一覧・あなたの番・記録・受付には今までどおり出る）。bot- の行の muted が true のものだけ（ほかの行に muted があっても効かない）。
 * buildWorld（ui/core・スマホの束と共有）には足さない＝様式の apply が world に添える（built.muted＝この Set）。
 */
export function mutedSessions(office) {
  const out = new Set();
  for (const e of Array.isArray(office?.employees) ? office.employees : []) {
    const sid = typeof e?.session === "string" ? e.session : "";
    if (e?.muted === true && /^bot-[a-z0-9][a-z0-9-]{3,31}$/.test(sid)) out.add(sid);
  }
  return out;
}

/** 受信済みの本文・ベンダー・成長値をHUD専用の2階層へ。core/3Dのworldは変更しない。 */
export function departmentBoard(office, w, T) {
  const raw = new Map((office.roster || []).map((p) => [p.projectId || p.session, p]));
  const employees = new Map((office.employees || []).map((p) => [p.session, p]));
  const groups = new Map();
  for (const a of w.agents) {
    const p = raw.get(a.id) || employees.get(a.session) || {};
    const name = p.name || p.dept || a.dept || a.name;
    const key = a.external ? a.id : p.cwd || name || a.id;
    // R123: 組の名前は Claude のプロジェクト名を優先する。同じフォルダで動く Codex（名前「Codex」）が先に来ると、
    // 並び順しだいで「制作本部(works)」の組が「Codex」と名乗っていた（本番の一覧で実測）。
    const fromClaude = String(p.vendor || a.vendor || "claude").toLowerCase() === "claude";
    const group = groups.get(key);
    if (!group) groups.set(key, { key, name, sessions: [], fromClaude });
    else if (fromClaude && !group.fromClaude) Object.assign(group, { name, fromClaude });
    const members = a.sessions.length ? a.sessions : [{ session: a.session }];
    members.forEach((brief, i) => {
      const lead = brief.session === a.session;
      const member = employees.get(brief.session) || {};
      const data = { ...(lead ? p : {}), ...brief, ...member };
      // b4-mac: Mac の画面だけが読む err・permMode・minionsLive を足す（buildWorld はスマホと共有＝そこでは捨てている）
      const model = withKindFields(buildWorld({ employees: [data] }).agents[0], data);
      const vendor = String(data.vendor || p.vendor || (a.external ? "openclaw" : "claude")).toLowerCase();
      const detail = data.detail || data.bg?.detail
        || (model.attention || errOf(model) ? attnLine(T, model, w.lang) : "") || model.question
        || activityGloss(model, w.lang) || zoneLabel(T, model.zone);
      // detailは40 Unicode文字。カテゴリ由来の絵文字を箇条書きの印にしない。
      const plainDetail = String(detail).replace(/^[\p{Extended_Pictographic}\uFE0F\s]+/u, "");
      const level = data.level ?? office.growth?.byProject?.[a.id]?.level;
      groups.get(key).sessions.push({ ...model, id: a.id, session: brief.session,
        // R123-A: /rename の名前 → Claude Code の自動タイトル（Mac だけ・中継には載らない）→ これまでの名前
        name: data.title || data.aiTitle || member.name || brief.name
          || (members.length === 1 ? a.name : T("board_session", i + 1)),
        vendor: ["claude", "codex", "openclaw", "bot"].includes(vendor) ? vendor : "other",
        detail: tidyActivity(plainDetail, 40),
        level: typeof level === "number" && Number.isFinite(level) ? level : null });
    });
  }
  return [...groups.values()].map(({ fromClaude: _, ...group }) => group);
}

/**
 * R98: **その world だけ**から 2 階層を作る（過去の再生用）。departmentBoard は live の roster / employees
 * から各行を組み直すので、リプレイのフレームに渡すと状態・質問・作業内容が「いまの値」に戻ってしまう
 *（別モデルレビューで実測: 過去の Stop で waiting にしたセッションが working のままだった）。
 * 名前だけは live から補える（`names`: id → 表示名）。
 */
export function boardFromWorld(w, T, live = new Map(), eventSids = null) {
  const groups = new Map();
  // イベントの無かったプロジェクトは createReplay が「代表＋sessions[]」のまま残す。
  // 代表だけ出すと非代表セッションが台帳から消えるので、live と同じように内訳へ開く。
  // ★内訳が 1 件でも開く（代表だけ残ると**集約時の状態**が本人の状態を上書きする）。
  // ★代表固有のもの（質問・承認待ち分・ゾーン）は引き継がない＝休憩中の非代表に代表の質問が出ない。
  const rows = (w?.agents || []).flatMap((a) => (
    Array.isArray(a.sessions) && a.sessions.length
      ? a.sessions.map((brief) => {
        // 代表本人の行だけは自分の様子を保つ。ただし質問は**そのとき ❗だった**ときだけ
        //（brief が attention:false なら、いまの質問文を過去に貼らない）。
        const lead = brief.session === a.session && Boolean(brief.attention);
        const row = {
          ...a, ...brief, crew: 1, sessions: [],
          name: "", vendor: brief.vendor || "",
          state: brief.state || a.state,
          attention: Boolean(brief.attention),
          question: lead ? a.question : "", questionOptions: lead ? a.questionOptions : [],
          approvalMin: lead ? a.approvalMin : Number(brief.approvalMin) || 0,
          ...(lead ? {} : { kind: "", activity: "", verb: "", target: "" }),
          stuckTool: lead ? a.stuckTool : "",
          // 配達の状態は過去には出さない（現在の 📴/📨 が過去のフレームに残る）
          ask: null, listening: true, pending: false,
        };
        row.zone = zoneOf(row);
        return row;
      })
      : [a]));
  for (const a of rows) {
    // live と同じプロジェクト分けを使う（`live`: session → {key, name, vendor}）。
    // セッション単位（サーバー既定 avatarMode=session）だと a.id はセッションごとに違うので、
    // これが無いと再生を始めた瞬間に 1 プロジェクトが同名の 2 行へ割れる（別モデルレビュー）。
    const known = live.get(a.session) || {};
    const key = known.key || a.id || a.session;
    const name = known.name || a.projectName || a.name || key;
    if (!groups.has(key)) groups.set(key, { key, name, sessions: [] });
    // createReplay は「代表セッションの姿」を複製して過去のセッションを作る＝**いまの作業内容**
    // （verb / target / work / feed）がくっついてくる。過去の絵に現在の文言を貼らないよう、
    // 再生で分かるもの（state / kind / zone / attention）だけから説明を作る（別モデルレビュー）。
    // 配達の状態（listening / pending / ask）も「いまの事実」＝過去の行には出さない
    const past = { ...a, verb: "", target: "", activity: "", work: null, feed: [], detail: "",
      listening: true, pending: false, ask: null };
    groups.get(key).sessions.push({
      ...past,
      // 名前とベンダーは**そのセッション本人**のものを live から借りる（代表の名前を配らない）
      name: known.sessionName || a.name || name,
      vendor: known.vendor || (a.vendor && a.vendor !== "other" ? a.vendor : "other"),
      detail: tidyActivity(a.question || activityGloss(past, w.lang) || zoneLabel(T, a.zone), 40),
      level: null,
      // 証拠は「いま」の事実なので過去の再生には出さない（嘘をつかない）
      evidence: null,
    });
  }
  // createReplay は「代表にイベントが在る」プロジェクトを代表単体（sessions: []）へ置き換える＝
  // イベントの無い非代表セッションが台帳から消える。live に居て、そのプロジェクトが画面に在るなら戻す
  //（リプレイの約束＝「イベントの無いセッションは変わっていない」と同じ扱い・別モデルレビュー）。
  const seen = new Set([...groups.values()].flatMap((g) => g.sessions.map((s) => s.session)));
  for (const [session, known] of live) {
    if (seen.has(session) || !known.brief) continue;
    // このフレームに居ないのが「イベントで出入りした結果」なら補わない（途中で始まった/終わった人）
    if (eventSids && eventSids.has(session)) continue;
    // グループごと消えている場合（代表が再生の途中で参加＝プロジェクトごと未登場）でも、
    // イベントの無い既存メンバーは居たはず＝グループを作って戻す（別モデルレビュー）。
    let group = groups.get(known.key);
    if (!group) {
      group = { key: known.key, name: known.name || known.key, sessions: [] };
      groups.set(known.key, group);
    }
    const brief = known.brief;
    const row = {
      ...(group.sessions[0] || {}), ...brief, session, crew: 1, sessions: [],
      id: group.sessions[0]?.id || known.key,
      name: known.sessionName || session, vendor: known.vendor || "other",
      state: brief.state || "waiting", attention: Boolean(brief.attention),
      question: "", questionOptions: [], approvalMin: Number(brief.approvalMin) || 0,
      // 代表の「いま何をしているか」（kind / activity / verb / target）は本人のものではない
      kind: "", activity: "", verb: "", target: "",
      stuckTool: "", ask: null, work: null, feed: [], evidence: null, level: null,
    };
    row.zone = zoneOf(row);
    row.detail = tidyActivity(activityGloss(row, w?.lang) || zoneLabel(T, row.zone), 40);
    group.sessions.push(row);
  }
  return [...groups.values()];
}

/** 様式に依らないクローム: ▶ボタンの文言・❗トレイ・オフィス名。毎 render の先頭で呼ぶ。 */
export function renderChrome(shell, w, T, tray) {
  // Missing recipe metadata is unknown (older servers/fixtures), not a confirmed empty list.
  const runButton = shell.querySelector("#btn-run");
  runButton.hidden = false;
  runButton.textContent = T(Array.isArray(w.actions?.recipes) && w.actions.recipes.length === 0 ? "btn_run_empty" : "btn_run");
  runButton.title = runButton.textContent;
  shell._traySel = tray.render(w);           // 足元チップ／行の強調用（様式側の paint が読む）
  shell.querySelector("#brandoffice").textContent = w.officeName || T("office_fallback");
  const sub = optional(shell, "#sub");             // iso の見出し行だけが持つ
  if (sub) sub.textContent = w.officeName || T("office_fallback");
}

/** 最近の指示（#hist）。件数は様式が決める（iso は 3 件＋展開で 12 件）。 */
export function renderHistory(shell, w, T, { limit = 3 } = {}) {
  const hist = shell.querySelector("#hist");
  const rows = (w.history || []).slice(0, limit).map((h) => ({ h,
    ago: w.generatedAt && h.ts ? agoStr(w.generatedAt - h.ts, w.lang) : null }));
  // R102-11: 同じ中身なら DOM に触らない（apply ごとの作り直しで ↻ の押下・「もう一度押すと再送」の表示が消えた）
  const sig = JSON.stringify([T("resend_title"), T("hist_empty"), T("dstate_live"), T("dstate_pending"),
    rows.map(({ h, ago }) => [h.session || "", h.text || "", h.disp || "", ago, !!h.pending])]);
  if (hist._sig === sig) return;
  hist._sig = sig;
  hist.replaceChildren();
  for (const { h, ago } of rows) {
    const row = el("div", "hrow");
    const resend = el("button", "hresend", "↻");
    resend.type = "button";
    resend.title = T("resend_title");
    resend.dataset.session = h.session || "";
    resend.dataset.text = h.text || "";
    resend.dataset.disp = h.disp || "";
    // R110-D4: 本文は様式によって 2 行で切る（cockpit の受付）＝全文はいつも title に置く（切れた先がどこにも無い、を作らない）
    const body = el("span", "", h.text || "");
    if (h.text) body.title = h.text;
    row.append(el("b", "", h.disp || ""), body);
    if (ago !== null) row.append(el("i", "hago", ago));
    const status = deliveryChip({ T, state: h.pending ? "pending" : "live" });
    status.classList.add("hp");
    row.append(status, resend);
    hist.append(row);
  }
  if (!hist.children.length) hist.append(el("div", "hempty", T("hist_empty")));
}
