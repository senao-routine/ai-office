// ── R121 作業ビュー（Claude Code アプリ型）の組み立て＝端末の行 → 表示のかたまり ─────────────────────
//
// 本人 2026-11-01「ウィンドウが見にくい」「アプリ版 Claude Code を見習って」「コードがつらつら出てきたら分からない」。
// 入力は server/office_term.py の行（Mac は GET /api/session/activity・スマホは封書の term）。ここは**純関数**
// （DOM・通信・時刻・乱数に触らない＝今の時刻は呼び出し側が渡す）。文言は持たない＝数と種類だけ返し、言葉は ui/hud/activity.js が T で作る。
//
// かたまり（blocks）:
//   user  { text, images, cmd }      人の指示（吹き出し）。cmd＝スラッシュコマンド
//   text  { text, err }              AI の文（本文）
//   group { id, steps, parts }       文と文の間の道具の実行を 1 行に畳む（parts＝種類ごとの数・ファイル）
//   run   { step }                   いま走っている 1 手（live の間だけ・末尾）
//   think { since }                  考え中（live で走っている手が無いとき・末尾）
//   question { text, options }       AskUserQuestion（options＝[{label, rec}]）
//   plan  { text }                   計画（ExitPlanMode）
//   note  { kind, ms }               sys（会話の圧縮）／int（中断）／turn（1 ターンの所要）
// R128 出勤の口の Bot の記録（server/source_door_log.py の行・正本= docs/door.md「記録の画面」）:
//   text  { …, report, title }       Bot の報告（行 a に rk）。report＝done／info／warn・title＝1 行目（題）・text＝本文
//   user  { …, said, choice }        人の答え・指示（行 u に as）。said＝answer／instruct・choice＝選んだ選択肢の番号（0 始まり）か null
//   event { event, text }            行 ev。working／waiting／resting（出勤の内容が変わった・text＝doing）・expired（相談の期限切れ）・
//                                    withdrawn（Bot が取り下げた）。知らない event は飛ばす
// 要約（summary）: todo {done, total, now} | null・files [{f, add, rem, wl}]・last（最後の行の ts）。

const CAT = Object.freeze({
  Bash: "cmd", Shell: "cmd", exec: "cmd", BashOutput: "cmd", KillShell: "cmd",
  Read: "read", NotebookRead: "read",
  Grep: "search", Glob: "search",
  WebSearch: "web", WebFetch: "web",
  Edit: "edit", MultiEdit: "edit", NotebookEdit: "edit", Write: "write",
  Task: "agent", Agent: "agent", Skill: "skill",
});

/** 道具の名前 → 種類（cmd・read・search・web・edit・write・agent・skill・mcp・other） */
export function catOf(name) {
  const n = String(name || "");
  if (Object.prototype.hasOwnProperty.call(CAT, n)) return CAT[n];
  return n.startsWith("mcp__") ? "mcp" : "other";
}

const IMG_LINE = /^\s*\[Image(?:: source: [^\]]*| #\d+)\]\s*$/;
const IMG_ANY = /\[Image(?:: source: [^\]]*| #\d+)\]/g;
const PASTE_TAG = /<\/?pasted_content\b[^>]*>/g;
const REC = /\s*[（(](?:Recommended|おすすめ|推奨)[)）]\s*$/i;

/** 人の指示の本文: 画像の印（[Image #2]・[Image: source: …]）は数だけにして本文から外す・貼り付けの囲みは外す */
export function userText(raw) {
  let images = 0;
  const lines = [];
  for (const line of String(raw || "").replace(PASTE_TAG, "").split("\n")) {
    if (IMG_LINE.test(line)) { images += (line.match(IMG_ANY) || []).length; continue; }
    const m = line.match(IMG_ANY);
    if (m) images += m.length;
    lines.push(m ? line.replace(IMG_ANY, "").trimEnd() : line);
  }
  while (lines.length && !lines[0].trim()) lines.shift();
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return { text: lines.join("\n"), images };
}

/** 質問の選択肢 → [{label, rec}]（末尾の (Recommended)・（おすすめ）は印にして外す） */
export function questionOptions(opts) {
  return (Array.isArray(opts) ? opts : []).map((o) => {
    const s = String(o || "");
    return { label: s.replace(REC, ""), rec: REC.test(s) };
  }).filter((o) => o.label);
}

/** 選択肢の 1 本 → {label, rec}（{label, recommended} の形も文字列も受ける・末尾の (Recommended) は印にして外す） */
export function optionLabel(option) {
  const raw = String((option && typeof option === "object" ? option.label : option) ?? "");
  const rec = REC.test(raw) || Boolean(option && typeof option === "object" && option.recommended);
  return { label: raw.replace(REC, ""), rec };
}

const fold = (text) => String(text ?? "").replace(/\s+/g, " ").trim();

/**
 * R123-B: 質問文の後ろに連結された「（選択肢 / 選択肢 …）」を外す。サーバーは通知とスマホのために
 * 質問文＋全選択肢の label を「 / 」でつないで 1 行（空白を畳んで 120 字・超えたら …）にしている＝答えのカードではボタンと二重になる。
 * 外すのはサーバーの形と言い切れるときだけ（別モデルレビュー medium）: 文末が「（label / label）」と完全に一致するか、
 * 120 字で切れた連結（末尾が …・最後の「（」から先が閉じておらず、連結の頭と同じ）。質問そのものの括弧
 *（「実行してよいですか？（本番環境のみ）」と選択肢「本番環境のみを更新」など）は残す。
 */
export function questionStem(question, options) {
  const q = String(question ?? "");
  const labels = (Array.isArray(options) ? options : [])
    .map((o) => fold(o && typeof o === "object" ? o.label : o)).filter(Boolean);
  if (!labels.length) return q;
  const joined = labels.join(" / "), folded = fold(q), suffix = `（${joined}）`;
  if (folded.length > suffix.length && folded.endsWith(suffix)) return folded.slice(0, -suffix.length).trimEnd();
  const at = folded.lastIndexOf("（");
  if (at > 0 && [...folded].length >= 120 && folded.endsWith("…")) {
    const inside = folded.slice(at + 1, -1);
    if (inside && !inside.includes("）") && joined.startsWith(inside)) return folded.slice(0, at).trimEnd();
  }
  return q;
}

/** R123-B: 会話の質問のかたまり（type "question"）が、この人がいま聞いている質問と同じか。本文の全部（サーバーが
 *  120 字で切ったときはその長さまで）と選択肢の並びが両方そろうときだけ true＝先頭が同じ別の質問を同じと見なさない。 */
export function sameQuestion(block, question, options) {
  if (block?.type !== "question") return false;
  const stem = fold(questionStem(question, options)), text = fold(block.text);
  const cut = stem.endsWith("…") ? stem.slice(0, -1) : "";
  if (!(cut ? cut.length >= 40 && text.startsWith(cut) : stem !== "" && text === stem)) return false;
  const mine = (Array.isArray(options) ? options : []).map((o) => fold(optionLabel(o).label));
  const theirs = (Array.isArray(block.options) ? block.options : []).map((o) => fold(o?.label));
  return mine.length === theirs.length && mine.every((label, i) => label === theirs[i]);
}

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** R128: 出勤の口の Bot の記録の印（閉じた集合・server/source_door_log.py と同じ値）。 */
export const DOOR_EVENTS = Object.freeze(["working", "waiting", "resting", "expired", "withdrawn"]);
export const REPORT_KINDS = Object.freeze(["done", "info", "warn"]);
const SAID = Object.freeze(["answer", "instruct"]);

/** R128: 報告の行（題＋空行＋本文）→ {title, text}。題は 1 行目（口が 1 行に畳んでいる）・本文は残り。 */
export function reportParts(raw) {
  const lines = String(raw ?? "").split("\n");
  const title = lines.shift() || "";
  while (lines.length && !lines[0].trim()) lines.shift();
  return { title, text: lines.join("\n") };
}

function newStep(r) {
  return {
    id: r.id ? String(r.id) : "",
    tool: String(r.n || ""),
    cat: catOf(r.n),
    ds: String(r.ds || ""),          // 説明（Bash の description など・伏字済み）
    arg: String(r.t || ""),          // 引数（命令の本文・相対パス・検索語）＝開いたときだけ見せる
    sub: String(r.sub || ""),
    bg: Boolean(r.bg),
    ts: num(r.ts),
    res: null,
    state: "open",                   // ok・err・int・run・open（結果が無いまま＝走っていない）
    dur: null,
    file: "", add: 0, rem: 0, wl: 0,
  };
}

function attach(step, res) {
  step.res = res;
  step.state = res.int ? "int" : res.err ? "err" : "ok";
  const a = num(step.ts), b = num(res.ts);
  if (a !== null && b !== null && b >= a) step.dur = b - a;
  if (res.f) step.file = String(res.f);
  else if (step.cat === "edit" || step.cat === "write" || step.cat === "read") step.file = step.arg;
  step.add = num(res.add) || 0;
  step.rem = num(res.rem) || 0;
  step.wl = num(res.wl) || 0;
  if (res.bg || res.st === "bg") step.bg = true;
}

/** group の parts: 種類ごとの数（初めて出た順）。edit/write はファイルごとの +/−（同じファイルは足す） */
export function groupParts(steps) {
  const order = [];
  const by = new Map();
  for (const s of steps) {
    const key = s.cat;
    let p = by.get(key);
    if (!p) { p = { cat: key, n: 0, files: [] }; by.set(key, p); order.push(p); }
    p.n += 1;
    if ((key === "edit" || key === "write") && (s.file || s.arg)) {
      const f = s.file || s.arg;
      const hit = p.files.find((x) => x.f === f);
      if (hit) { hit.add += s.add; hit.rem += s.rem; hit.wl += s.wl; }
      else p.files.push({ f, add: s.add, rem: s.rem, wl: s.wl });
    }
  }
  return order;
}

/**
 * rows → { blocks, summary }。
 * opts.live＝いまそのセッションが動いている（作業中・考え中）。末尾の結果の無い手を「走っている」にし、
 * 走っている手が無ければ「考え中」を末尾に置く。live でなければ結果の無い手は open のまま（印を付けない）。
 */
export function buildActivity(rows, { live = false } = {}) {
  const blocks = [];
  const list = Array.isArray(rows) ? rows : [];
  const byId = new Map();
  let group = null;
  let todo = null;
  let last = null;
  const files = new Map();

  const close = () => {
    if (group && group.steps.length) {
      group.parts = groupParts(group.steps);
      blocks.push(group);
    }
    group = null;
  };
  const open = (r, i) => {
    if (!group) group = { type: "group", id: r.id ? `g:${r.id}` : `g@${i}`, steps: [], parts: [], ts: num(r.ts) };
    return group;
  };

  list.forEach((r, i) => {
    if (!r || typeof r !== "object") return;
    if (num(r.ts) !== null) last = r.ts;
    switch (r.k) {
      case "tool": {
        const s = newStep(r);
        open(r, i).steps.push(s);
        if (s.id) byId.set(s.id, s);
        return;
      }
      case "res": {
        const s = r.for ? byId.get(String(r.for)) : null;
        if (s) { attach(s, r); return; }
        // 別モデルレビュー（R121-A/B・medium）: Codex のファイル変更（source_codex_rollout._file_rows）は道具の行を持たない
        // 結果の行（for 無し・f あり）＝窓の外ではない。編集／作成の 1 手として畳みと「変更 N ファイル」に入れる
        if (!r.for && r.f) {
          const step = newStep({ n: num(r.wl) ? "Write" : "Edit", t: r.f, ts: r.ts });
          attach(step, r);
          open(r, i).steps.push(step);
        }
        return;                                   // 対の無い結果（窓の外の道具）は出さない
      }
      case "th":
        return;                                   // 考えの印は畳む（末尾の「考え中」は live で決める）
      case "todo": {
        const items = (Array.isArray(r.i) ? r.i : []).filter(Array.isArray);
        const now = items.find((x) => x[0] === "i");
        todo = { done: items.filter((x) => x[0] === "c").length, total: items.length, now: now ? String(now[1] || "") : "" };
        return;                                   // Todo は要約（見出しの札）で見せる＝流れには置かない
      }
      case "u":
      case "cmd": {
        close();
        // R128: Bot の記録の人の言葉（as つき）は書いたままの文（画像の印・貼り付けの囲みは Claude Code の書き方＝読み替えない）
        const said = r.k === "u" && SAID.includes(r.as);
        const u = r.k === "cmd" || said ? { text: String(r.t || ""), images: 0 } : userText(r.t);
        if (!u.text && !u.images) return;
        const block = { type: "user", text: u.text, images: u.images, cmd: r.k === "cmd", ts: num(r.ts) };
        // R128: 答えか指示か（選んだ選択肢の番号つき）。セッションの行には無い＝今までどおり
        if (said) {
          block.said = r.as;
          block.choice = r.as === "answer" && Number.isInteger(r.ch) && r.ch >= 0 ? r.ch : null;
        }
        blocks.push(block);
        return;
      }
      case "a": {
        close();
        // R128: Bot の報告（rk）は題と本文に分ける（題は 1 行目）。セッションの AI の文は今までどおり
        if (REPORT_KINDS.includes(r.rk)) {
          const { title, text } = reportParts(r.t);
          blocks.push({ type: "text", text, title, report: r.rk, err: false, ts: num(r.ts) });
          return;
        }
        blocks.push({ type: "text", text: String(r.t || ""), err: Boolean(r.err), ts: num(r.ts) });
        return;
      }
      case "ev": {
        // R128: Bot の出勤の変化・相談の期限切れ・取り下げ（知らない印は飛ばす＝古い描き手と同じく黙る）
        if (!DOOR_EVENTS.includes(r.e)) return;
        close();
        blocks.push({ type: "event", event: r.e, text: String(r.t || ""), ts: num(r.ts) });
        return;
      }
      case "q":
        close();
        blocks.push({ type: "question", text: String(r.t || ""), options: questionOptions(r.o), ts: num(r.ts) });
        return;
      case "plan":
        close();
        blocks.push({ type: "plan", text: String(r.t || ""), ts: num(r.ts) });
        return;
      case "sys":
      case "int":
        close();
        blocks.push({ type: "note", kind: r.k, ms: 0, ts: num(r.ts) });
        return;
      case "turn":
        close();
        blocks.push({ type: "note", kind: "turn", ms: num(r.ms) || 0, ts: num(r.ts) });
        return;
      default:
    }
  });

  // 末尾の group: live なら結果の無い手は「走っている」＝最後の未完了の 1 つを group の外（run）へ出す（並列の他の手は group の中で run の印）。
  // 別モデルレビュー（R121-A/B・medium）: 末尾の手だけを見ると、後から出した手が先に終わった並列（tool a, tool b, res b）で
  // 走っている a を畳んで「考え中」と言っていた＝末尾から最初の未完了を探す
  let run = null;
  if (group && group.steps.length) {
    if (live) {
      for (const s of group.steps) if (s.state === "open") s.state = "run";
      for (let k = group.steps.length - 1; k >= 0; k--) {
        if (group.steps[k].state === "run") { run = group.steps[k]; group.steps.splice(k, 1); break; }
      }
    }
    close();
  }
  if (run) blocks.push({ type: "run", step: run });
  else if (live) blocks.push({ type: "think", since: last });

  for (const b of blocks) {
    if (b.type !== "group") continue;
    for (const s of b.steps) {
      if (!(s.cat === "edit" || s.cat === "write") || s.state !== "ok") continue;
      const f = s.file || s.arg;
      if (!f) continue;
      const hit = files.get(f) || { f, add: 0, rem: 0, wl: 0 };
      hit.add += s.add; hit.rem += s.rem; hit.wl += s.wl;
      files.set(f, hit);
    }
  }
  return { blocks, summary: { todo, files: [...files.values()], last } };
}
