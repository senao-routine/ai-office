// R121: Mac と封書のスマホで共有する作業ビュー。本文は要素と textContent だけで組み立てる。
import { buildActivity } from "../core/activity.js";

// PWA は hud/strings.js を同梱しない。この 2 辞書のキーは activity_view.test.mjs で照合する。
export const ja = Object.freeze({
  expand: "全文を見る", collapse: "たたむ", images: (n) => `画像 ${n} 枚`,
  // R122（本人「難しいファイル名がつらつら並ぶより、文章で分かりやすく」）: 畳みの行は 1 文。last＝文の最後の句（過去形で結ぶ）
  cmd: (n, last) => `コマンドを ${n} 回実行${last ? "しました" : "し"}`, read: (n, last) => `ファイルを ${n} 件読${last ? "みました" : "み"}`,
  search: (n, last) => `${n} 回検索${last ? "しました" : "し"}`, web: (n, last) => `Web で ${n} 件調べ${last ? "ました" : ""}`,
  edit: (n, last) => `ファイルを ${n} つ直${last ? "しました" : "し"}`, write: (n, last) => `ファイルを ${n} つ作${last ? "りました" : "り"}`,
  agent: (n, last) => `手伝いに ${n} 件頼${last ? "みました" : "み"}`, skill: (n, last) => `スキルを ${n} 回使${last ? "いました" : "い"}`,
  mcp: (n, last) => `外部のツールを ${n} 回使${last ? "いました" : "い"}`, other: (n, last) => `ほかの操作を ${n} 回${last ? "しました" : "し"}`,
  join: (xs) => xs.join("、"),
  readFile: (f) => `${f} を読む`, editFile: (f) => `${f} を編集`, writeFile: (f) => `${f} を作成`,
  searching: (t) => `検索 ${t}`, fetch: (t) => `${t} を取得`,
  interrupted: "中断", background: "裏で", running: "実行中", complete: "完了", failed: "失敗", pending: "未完了",
  thinking: "考え中…", recommended: "おすすめ", plan: "計画", compacted: "会話を圧縮しました",
  interruption: "中断されました", worked: (n) => `${n >= 60 ? `${Math.floor(n / 60)}分${n % 60}秒` : `${n}秒`} 作業`,
  moreLines: (n) => `… +${n} 行（タップで開く）`, leftLines: (n) => `… 残り ${n} 行は Mac で`,
  kids: (n) => `サブエージェントの末尾 ${n} 行`, kidsLeft: (n) => `${n} 件のサブエージェントの行を省略`,
  readLines: (n) => `${n} 行を読み込み`, readImage: "画像を読み込み", results: (n) => `${n} 件`,
  emptyOutput: "（出力なし）", terminal: "ターミナル", command: "命令", output: "出力", diff: "差分",
  // R128: 出勤の口の Bot の記録（Bot の報告の札・人の言葉の札・出勤の変化と相談の閉じ方の 1 行）
  report_done: "報告", report_info: "お知らせ", report_warn: "注意",
  said_answer: "相談への答え", said_choice: (n) => `相談への答え（選択肢 ${n}）`, said_instruct: "指示",
  event_working: "出勤", event_waiting: "出勤（指示待ち）", event_resting: "休憩",
  event_expired: "相談の期限が切れました（答えはありません）", event_withdrawn: "Bot が相談を取り下げました",
});
export const en = Object.freeze({
  expand: "Read full text", collapse: "Collapse", images: (n) => `${n} images`,
  cmd: (n) => `ran ${n} command${n === 1 ? "" : "s"}`, read: (n) => `read ${n} file${n === 1 ? "" : "s"}`,
  search: (n) => `searched ${n} time${n === 1 ? "" : "s"}`, web: (n) => `looked up ${n} page${n === 1 ? "" : "s"}`,
  edit: (n) => `edited ${n} file${n === 1 ? "" : "s"}`, write: (n) => `created ${n} file${n === 1 ? "" : "s"}`,
  agent: (n) => `asked ${n} helper${n === 1 ? "" : "s"}`, skill: (n) => `used ${n} skill${n === 1 ? "" : "s"}`,
  mcp: (n) => `used ${n} connected tool${n === 1 ? "" : "s"}`, other: (n) => `did ${n} other action${n === 1 ? "" : "s"}`,
  join: (xs) => { const t = xs.length > 1 ? `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}` : (xs[0] || "");
    return t.charAt(0).toUpperCase() + t.slice(1); },
  readFile: (f) => `Read ${f}`, editFile: (f) => `Edit ${f}`, writeFile: (f) => `Create ${f}`,
  searching: (t) => `Search ${t}`, fetch: (t) => `Fetch ${t}`,
  interrupted: "Interrupted", background: "Background", running: "Running", complete: "Done", failed: "Failed", pending: "Unfinished",
  thinking: "Thinking…", recommended: "Recommended", plan: "Plan", compacted: "Conversation compacted",
  interruption: "Interrupted", worked: (n) => `Worked for ${formatDuration(n)}`,
  moreLines: (n) => `… +${n} lines (tap to expand)`, leftLines: (n) => `… ${n} more lines on the Mac`,
  kids: (n) => `Last ${n} subagent lines`, kidsLeft: (n) => `${n} subagent lines omitted`,
  readLines: (n) => `Read ${n} lines`, readImage: "Read an image", results: (n) => `${n} results`,
  emptyOutput: "(no output)", terminal: "Terminal", command: "Command", output: "Output", diff: "Diff",
  report_done: "Report", report_info: "Note", report_warn: "Warning",
  said_answer: "Answer", said_choice: (n) => `Answer (option ${n})`, said_instruct: "Instruction",
  event_working: "On duty", event_waiting: "On duty (waiting)", event_resting: "On a break",
  event_expired: "The question expired (no answer)", event_withdrawn: "The bot withdrew its question",
});

const basename = (s) => String(s || "").split(/[\\/]/).pop() || String(s || "");
export const formatDuration = (n) => {
  const sec = Math.max(0, Math.floor(Number(n) || 0));
  return sec >= 60 ? `${Math.floor(sec / 60)}m ${String(sec % 60).padStart(2, "0")}s` : `${sec}s`;
};
const elapsed = formatDuration;
const keyFor = (b, i) => b.id || `${b.type}:${b.ts ?? ""}:${b.text || b.kind || i}`;

/**
 * render の now と行の ts は秒。時計を持たず、呼び出し側の live/frozen の時計に従う。
 * decorate(host, {blocks, summary}, options) は末尾に許可・質問の操作などを足すための口。
 * 同じ view は 1 セッション用（切替時は dispose）。開いた group/step/本文は id で覚える。
 * R128: options.record＝出勤の口の Bot の記録（Bot は考えない＝live の「考え中」を出さない・いつかが分かるように
 * 最初の行にも時刻を置き、日が替わったら日付も添える）。行の種類は ui/core/activity.js の event／報告／答えと指示。
 */
export function createActivityView({ lang = "ja", onOpenTerminal, decorate } = {}) {
  const expanded = new Set();
  const hosts = new Set();
  let lastHost = null;
  const dictionary = () => (typeof lang === "function" ? lang() : lang) === "en" ? en : ja;

  function render(host, rows, options = {}) {
    const T = (key, ...args) => {
      const value = dictionary()[key];
      return typeof value === "function" ? value(...args) : value;
    };
    const doc = host.ownerDocument;
    const clamps = [];
    const el = (tag, cls, text) => {
      const n = doc.createElement(tag);
      if (cls) n.className = cls;
      if (text !== undefined) n.textContent = String(text);
      return n;
    };
    const button = (cls, text, click) => {
      const n = el("button", cls, text);
      n.type = "button";
      n.addEventListener("click", (event) => { event.stopPropagation(); click(); });
      return n;
    };
    const toggle = (key, head, body, build, chevron = null) => {
      let built = false;
      const paint = () => {
        const open = expanded.has(key);
        head.setAttribute("aria-expanded", String(open));
        body.hidden = !open;
        if (open && !built) { build(body); built = true; }
        head.classList.toggle("act-open", open);
        // 開閉の印は字を替える（▸／▾）。回す（transform）と縦長の字の箱が回って行の右へはみ出した（iPhone 393 幅で 6px・pwa3d_smoke）
        if (chevron) chevron.textContent = open ? "▾" : "▸";
      };
      head.addEventListener("click", () => {
        if (expanded.has(key)) expanded.delete(key); else expanded.add(key);
        paint();
      });
      paint();
    };
    const inline = (hostNode, raw) => {
      const text = String(raw || ""), pattern = /(\*\*([^*]+)\*\*|`([^`]+)`)/g;
      let start = 0;
      for (const m of text.matchAll(pattern)) {
        if (m.index > start) hostNode.append(doc.createTextNode(text.slice(start, m.index)));
        hostNode.append(el(m[2] === undefined ? "code" : "strong", "", m[2] ?? m[3]));
        start = m.index + m[0].length;
      }
      if (start < text.length) hostNode.append(doc.createTextNode(text.slice(start)));
    };
    const markdown = (node, text) => {
      const lines = String(text || "").split("\n");
      let i = 0;
      while (i < lines.length) {
        const line = lines[i];
        if (!line.trim()) { i++; continue; }
        if (/^\s*```/.test(line)) {
          const code = [];
          i++;
          while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
          if (i < lines.length) i++;
          node.append(el("pre", "act-pre", code.join("\n")));
          continue;
        }
        if (/^\s*\|/.test(line)) {
          const table = [];
          while (i < lines.length && /^\s*\|/.test(lines[i])) table.push(lines[i++]);
          node.append(el("pre", "act-pre act-table", table.join("\n")));
          continue;
        }
        const heading = /^\s*#{1,6}\s+(.+)$/.exec(line);
        if (heading) {
          const p = el("p", "act-heading"), strong = el("strong");
          inline(strong, heading[1]); p.append(strong); node.append(p); i++; continue;
        }
        const list = /^\s*(?:([-*])\s+|\d+[.)]\s+)(.*)$/.exec(line);
        if (list) {
          const ordered = !list[1], ul = el(ordered ? "ol" : "ul", "act-list");
          if (ordered) {
            const number = /^\s*(\d+)/.exec(line);
            if (number) ul.setAttribute("start", number[1]);
          }
          while (i < lines.length) {
            const item = /^\s*(?:([-*])\s+|\d+[.)]\s+)(.*)$/.exec(lines[i]);
            if (!item || Boolean(item[1]) === ordered) break;
            const li = el("li"); inline(li, item[2]); ul.append(li); i++;
          }
          node.append(ul); continue;
        }
        const paragraph = [line]; i++;
        while (i < lines.length && lines[i].trim() && !/^\s*(?:```|\||#{1,6}\s|[-*]\s|\d+[.)]\s)/.test(lines[i])) paragraph.push(lines[i++]);
        const p = el("p"); inline(p, paragraph.join("\n")); node.append(p);
      }
    };
    const numbers = (target, add, rem) => {
      if (add) target.append(" ", el("span", "act-add", `+${add}`));
      if (rem) target.append(" ", el("span", "act-rem", `−${rem}`));
    };
    const foldText = (raw, total, limit, key) => {
      const lines = String(raw || "").split("\n"), box = el("div", "act-fold");
      const omitted = lines.reduce((n, line) => n + (Number(/^…（中略 (\d+) 行）…$/.exec(line)?.[1]) || 0), 0);
      const shownTotal = lines.length + omitted - lines.filter((line) => /^…（中略(?: \d+ 行)?）…$/.test(line)).length;
      const count = Math.max(shownTotal, Number(total) || 0), left = Math.max(0, count - shownTotal);
      const pre = el("pre", "act-pre"); box.append(pre);
      const paint = () => {
        const open = expanded.has(key);
        pre.textContent = (open ? lines : lines.slice(0, limit)).join("\n");
        return open;
      };
      paint();
      if (lines.length > limit) {
        const b = button("act-more", "", () => {
          if (expanded.has(key)) expanded.delete(key); else expanded.add(key);
          update();
        });
        const update = () => {
          const open = paint();
          b.textContent = open ? T("collapse") : T("moreLines", Math.max(0, count - limit));
          b.setAttribute("aria-expanded", String(open));
          remainder.hidden = !open || !left;
        };
        const remainder = el("div", "act-note", left ? T("leftLines", left) : "");
        box.append(b, remainder); update();
      } else if (left) box.append(el("div", "act-note", T("leftLines", left)));
      return box;
    };
    const description = (step, target) => {
      if (step.ds) { target.append(doc.createTextNode(step.ds)); return; }
      const file = basename(step.file || step.arg);
      switch (step.cat) {
        case "read": target.append(doc.createTextNode(T("readFile", file))); break;
        case "edit": target.append(doc.createTextNode(T("editFile", file))); numbers(target, step.add, step.rem); break;
        case "write": target.append(doc.createTextNode(T("writeFile", file))); numbers(target, step.wl, 0); break;
        case "search": target.append(doc.createTextNode(T("searching", step.arg))); break;
        case "web": target.append(doc.createTextNode(step.tool === "WebFetch" ? T("fetch", step.arg) : T("searching", step.arg))); break;
        case "agent": target.append(doc.createTextNode(step.arg || step.sub || step.tool)); break;
        case "skill": target.append(el("code", "act-name", `/${step.arg.replace(/^\//, "")}`)); break;
        case "mcp": target.append(doc.createTextNode(step.tool)); break;
        case "cmd": target.append(el("code", "act-name", (step.arg || step.tool).split("\n")[0])); break;
        default: target.append(doc.createTextNode(step.arg || step.tool));
      }
    };
    const dots = () => {
      const n = el("span", "act-dots");
      n.setAttribute("aria-hidden", "true");
      n.append(el("i"), el("i"), el("i")); return n;
    };
    const timeSeparator = (ts) => {
      const date = new Date(ts * 1000), hh = String(date.getHours()).padStart(2, "0"), mm = String(date.getMinutes()).padStart(2, "0");
      const sep = el("div", "act-time", `${hh}:${mm}`); sep.setAttribute("data-ts", String(ts)); return sep;
    };
    // R128: Bot の記録は日をまたいで続く＝最初の区切りと日が替わった区切りに日付（M/D）を添える
    let shownDay = "";
    const dayOf = (ts) => { const date = new Date(ts * 1000); return `${date.getMonth() + 1}/${date.getDate()}`; };
    const recordSeparator = (ts) => {
      const sep = timeSeparator(ts), day = dayOf(ts);
      if (day !== shownDay) { sep.textContent = `${day} ${sep.textContent}`; shownDay = day; }
      return sep;
    };
    const stepDetail = (step, target, key, depth) => {
      if (step.arg) {
        const command = foldText(step.arg, null, 8, `${key}:arg`);
        command.classList.add("act-command"); command.setAttribute("aria-label", T("command")); target.append(command);
      }
      const res = step.res;
      if (!res) return;
      if (typeof res.rl === "number") target.append(el("div", "act-note", T("readLines", res.rl)));
      if (res.img) target.append(el("div", "act-note", T("readImage")));
      if (typeof res.nf === "number") target.append(el("div", "act-note", T("results", res.nf)));
      if (res.t || (!res.d?.length && !res.kids?.length && res.rl === undefined && !res.img)) {
        const output = foldText(res.t || T("emptyOutput"), res.nl, 3, `${key}:res`);
        output.classList.add("act-output");
        output.setAttribute("aria-label", T("output")); target.append(output);
      }
      if (Array.isArray(res.d) && res.d.length) {
        const diff = el("div", "act-diff"); diff.setAttribute("aria-label", T("diff"));
        const lines = res.d.filter(Array.isArray);
        const paint = () => {
          diff.replaceChildren();
          const open = expanded.has(`${key}:diff`);
          for (const line of (open ? lines : lines.slice(0, 16))) {
            const sign = String(line[0] || " ");
            const row = el("div", "act-diffline");
            row.append(el("span", "act-lineno", sign === "…" ? "" : String(line[1] || "")), el("span", `act-sign${sign === "+" ? " act-add" : sign === "-" ? " act-rem" : ""}`, sign === "-" ? "−" : sign), el("span", "act-source", line[2] || "")); diff.append(row);
          }
          const changed = lines.filter((line) => line[0] === "+" || line[0] === "-").length;
          const left = Math.max(0, (Number(res.wl) || (Number(res.add) || 0) + (Number(res.rem) || 0)) - changed);
          if (lines.length > 16) {
            const more = button("act-more", open ? T("collapse") : T("moreLines", lines.length - 16 + left), () => {
              if (open) expanded.delete(`${key}:diff`); else expanded.add(`${key}:diff`); paint();
            }); more.setAttribute("aria-expanded", String(open)); diff.append(more);
          }
          if (left && (open || lines.length <= 16)) diff.append(el("div", "act-note", T("leftLines", left)));
        };
        paint(); target.append(diff);
      }
      const kids = Array.isArray(res.kids) ? res.kids : [];
      if (kids.length && depth < 3) {
        const child = el("div", "act-kids"), head = button("act-more", T("kids", kids.length), () => {}), body = el("div", "act-kids-body");
        toggle(`${key}:kids`, head, body, (node) => {
          const blocks = buildActivity(kids, { live: false }).blocks;
          blocks.forEach((block, i) => node.append(blockNode(block, `${key}:kid:${keyFor(block, i)}`, depth + 1)));
        });
        child.append(head, body); target.append(child);
      }
      if (Number(res.kx) > 0) target.append(el("div", "act-note", T("kidsLeft", res.kx)));
    };
    const stepNode = (step, key, depth) => {
      const node = el("div", "act-step"), head = button("act-step-head", "", () => {});
      node.setAttribute("data-step-id", step.id); node.setAttribute("data-state", step.state);
      const status = step.bg ? "background" : step.state === "ok" ? "complete" : step.state === "err" ? "failed" : step.state === "int" ? "interrupted" : step.state === "run" ? "running" : "pending";
      const mark = step.bg ? T("background") : step.state === "ok" ? "✓" : step.state === "err" ? "✗" : step.state === "int" ? T("interrupted") : step.state === "run" ? "···" : "·";
      const statusNode = el("span", `act-mark act-mark-${step.state}`, mark); statusNode.title = T(status);
      const text = el("span", "act-description"); description(step, text);
      head.append(statusNode, text);
      if (step.dur !== null) head.append(el("span", "act-duration", elapsed(step.dur)));
      const chevron = el("span", "act-chevron", "▸");
      head.append(chevron);
      const body = el("div", "act-step-body");
      toggle(key, head, body, (target) => stepDetail(step, target, key, depth), chevron);
      node.append(head, body); return node;
    };
    const blockNode = (b, key, depth = 0) => {
      let node;
      if (b.type === "user") {
        node = el("div", `act-user${b.cmd ? " act-user-command" : ""}`);
        if (b.text) {
          const text = el(b.cmd ? "code" : "div", b.cmd ? "act-command-chip" : "act-user-text", b.text);
          node.append(text);
          if (!b.cmd) {
            const paint = () => {
              const open = expanded.has(key);
              text.classList.toggle("act-clamp", !open);
              more.textContent = open ? T("collapse") : T("expand"); more.setAttribute("aria-expanded", String(open));
            };
            const more = button("act-more", "", () => { if (expanded.has(key)) expanded.delete(key); else expanded.add(key); paint(); });
            node.append(more); paint(); clamps.push({ text, more, key, raw: b.text });
          }
        }
        if (b.images) node.append(el("span", "act-chip", T("images", b.images)));
        // R128: Bot の記録では人の言葉が答えか指示かを札で言う（選んだ選択肢は番号つき）
        if (b.said) {
          node.classList.add(`act-said-${b.said}`);
          node.append(el("span", "act-chip act-said", b.said === "answer"
            ? (Number.isInteger(b.choice) ? T("said_choice", b.choice + 1) : T("said_answer")) : T("said_instruct")));
        }
      } else if (b.type === "text" && b.report) {
        // R128: Bot の報告＝種類の札と題（1 行・文字のまま）の下に本文
        node = el("div", `act-text act-report act-report-${b.report}`);
        const head = el("p", "act-report-head");
        head.append(el("span", `act-chip act-report-kind act-report-${b.report}`, T(`report_${b.report}`)), " ", el("strong", "act-report-title", b.title));
        node.append(head);
        if (b.text) markdown(node, b.text);
      } else if (b.type === "text") {
        node = el("div", `act-text${b.err ? " act-error" : ""}`); markdown(node, b.text);
      } else if (b.type === "event") {
        // R128: Bot の出勤の変化（語と doing）・相談の閉じ方（期限切れ・取り下げ＝答えの吹き出しとは別の 1 行）
        node = el("div", `act-event act-event-${b.event}`);
        node.append(el("span", "act-event-word", T(`event_${b.event}`)));
        if (b.text) node.append(" ", el("span", "act-event-text", b.text));   // 空白は flex の中では描かれない（読み上げとコピーの区切り）
      } else if (b.type === "group") {
        node = el("div", "act-group");
        const head = button("act-group-head", "", () => {});
        // R122: 1 文で言う（ファイル名は出さない＝1 手ずつ開いたときだけ）。直した／作ったファイルは数（重複を除く）・行の +/− は文の後ろにまとめて
        const phrases = b.parts.map((part, i) => {
          const n = part.cat === "edit" || part.cat === "write" ? (part.files.length || part.n) : part.n;
          return T(part.cat, n, i === b.parts.length - 1);
        });
        head.append(doc.createTextNode(T("join", phrases)));
        const touched = b.parts.filter((part) => part.cat === "edit" || part.cat === "write").flatMap((part) => part.files);
        const added = touched.reduce((n, f) => n + (Number(f.add) || 0) + (Number(f.wl) || 0), 0);
        const removed = touched.reduce((n, f) => n + (Number(f.rem) || 0), 0);
        if (touched.length) {
          head.title = touched.map((f) => f.f).join("\n");     // ファイル名は title（マウスを載せたとき）と 1 手ずつの中にだけ
          numbers(head, added, removed);
        }
        const chevron = el("span", "act-chevron", "▸");
        head.append(" ", chevron);
        const body = el("div", "act-steps");
        toggle(key, head, body, (target) => {
          let previous = null;
          b.steps.forEach((step, i) => {
            if (Number.isFinite(step.ts)) {
              if (previous !== null && step.ts - previous >= 120) target.append(timeSeparator(step.ts));
              previous = step.res && Number.isFinite(step.res.ts) ? step.res.ts : step.ts;
            }
            target.append(stepNode(step, `${key}:step:${step.id || i}`, depth));
          });
        }, chevron);
        node.append(head, body);
      } else if (b.type === "run" || b.type === "think") {
        node = el("div", "act-live"); node.append(dots());
        const text = el("span", "act-description");
        if (b.type === "run") description(b.step, text); else text.textContent = T("thinking");
        node.append(text);
        const since = b.type === "run" ? b.step.ts : b.since;
        if (Number.isFinite(since) && Number.isFinite(options.now)) {
          const duration = el("span", "act-duration act-elapsed", elapsed(options.now - since));
          duration.setAttribute("data-since", String(since)); node.append(duration);
        }
      } else if (b.type === "question") {
        node = el("section", "act-card act-question"); node.setAttribute("data-qtext", b.text);
        const text = el("div", "act-question-text act-question-body"); markdown(text, b.text); node.append(text);
        const list = el("div", "act-options");
        b.options.forEach((option, i) => {
          const row = el("div", "act-option"); row.append(el("span", "act-option-number", `${i + 1}.`), el("span", "act-option-label", option.label));
          if (option.rec) row.append(el("span", "act-chip act-recommended", T("recommended"))); list.append(row);
        });
        node.append(list);
      } else if (b.type === "plan") {
        node = el("section", "act-card act-plan"); node.append(el("strong", "act-card-title", T("plan")), foldText(b.text, null, 8, `${key}:plan`));
      } else {
        node = el("div", "act-note", b.kind === "turn" ? T("worked", Math.max(0, Math.round(b.ms / 1000))) : T(b.kind === "sys" ? "compacted" : "interruption"));
      }
      node.setAttribute("data-act-key", key);
      const ts = b.type === "run" ? b.step.ts : b.ts;
      if (Number.isFinite(ts)) node.setAttribute("data-ts", String(ts));
      return node;
    };

    const first = !hosts.has(host), bottom = first || host.scrollHeight - host.scrollTop - host.clientHeight < 28;
    const previousTop = host.scrollTop;
    const bounds = host.getBoundingClientRect();
    let anchor = null;
    if (!bottom) {
      for (const child of host.children) {
        if (!child.hasAttribute("data-act-key")) continue;
        const rect = child.getBoundingClientRect();
        if (rect.bottom > bounds.top) { anchor = { key: child.getAttribute("data-act-key"), offset: rect.top - bounds.top }; break; }
      }
    }
    const record = Boolean(options.record);
    const activity = buildActivity(rows, { live: Boolean(options.live) && !record });
    host.classList.add("act");
    host.replaceChildren();
    let lastTs = null;
    activity.blocks.forEach((block, i) => {
      if (Number.isFinite(block.ts)) {
        if (record && (lastTs === null || block.ts - lastTs >= 120 || dayOf(block.ts) !== dayOf(lastTs))) host.append(recordSeparator(block.ts));
        else if (lastTs !== null && block.ts - lastTs >= 120) {
          host.append(timeSeparator(block.ts));
        }
        lastTs = block.ts;
      }
      host.append(blockNode(block, keyFor(block, i)));
    });
    if (typeof decorate === "function") decorate(host, activity, options);
    if (typeof onOpenTerminal === "function" && options.showTerminal) host.append(button("act-more act-terminal", T("terminal"), onOpenTerminal));
    for (const { text, more, key, raw } of clamps) {
      more.hidden = !expanded.has(key) && !(text.scrollHeight > text.clientHeight + 1) && !(raw.length > 180 || raw.split("\n").length > 3);
    }
    if (bottom) host.scrollTop = host.scrollHeight;
    else if (anchor) {
      const next = [...host.children].find((node) => node.getAttribute("data-act-key") === anchor.key);
      host.scrollTop = next ? previousTop + next.getBoundingClientRect().top - bounds.top - anchor.offset : previousTop;
    } else host.scrollTop = previousTop;
    hosts.add(host); lastHost = host;
    return activity;
  }

  return { render, dispose() { expanded.clear(); hosts.clear(); lastHost?.classList.remove("act"); lastHost = null; } };
}
