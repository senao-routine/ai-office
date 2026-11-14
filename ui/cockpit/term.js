// R99 Phase 2: コックピットの端末。**画面は xterm.js、中身は本物の pty**（server/office_pty.py）。
// シェルは自作しない。ここがやるのは「どの端末をどこに見せるか」と「打鍵と大きさを渡す」だけ。
//
// R111-B2（ペイン分割）: 端末は**ペインの body（host）へ移して見せる**（xterm は作り直さない＝要素を host.append で移すだけ）。
// どのペインに何を出すかは ui/cockpit/index.js（分割木＝ui/core/termpanes.js）が決め、DOM のペインは ui/cockpit/panes.js が描く。
// 見せていない端末は根（init の host＝#cp-xterm）の直下へ戻して hidden（駐車）。
//
// 掟:
//   - 端末 1 本 = xterm 1 個。タブを切り替えても・別のペインへ移しても作り直さない（隠すだけ・移すだけ）。
//   - **購読するのは見えている端末だけ・接続は常に 1 本**（GET /api/term/streams＝サーバーの多重化）。隠した端末も繋ぎっぱなしに
//     したり、ペインごとに 1 本ずつ繋いだりすると、HTTP/1.1 の同一オリジン 6 本の上限を /api/events と合わせて食い潰し、
//     打鍵の POST が待ち続ける（別モデルレビュー）。見せ直すときは読み終えたところから繋ぎ直す（隠していた間の出力だけ受け取る）。
//   - 出力は stream でしか来ない。**読み終えた通し番号から続きを受け取る**（タブを往復しても reset しない＝TUI が立てた代替画面・
//     貼り付けモードが残る）。頭から流し直すとき（初回・リングから押し出された後）だけ消して、サーバーが渡す画面モードを先に立て直す。
//   - **フォーカスを動かすのは本人の操作のときだけ**（show(id, {focus:true})）。再描画のたびに focus() すると、シートの指示欄に
//     打っている途中で端末へ移り、続きの打鍵が**本物のシェルで実行される**（別モデルレビュー high）。
//   - サーバーの一覧から消えた端末は xterm ごと捨てる（非表示の DOM と 5000 行のバッファを抱えない）。
//   - 打鍵は termInput（platform）が順番を守って送る。ここで並べて投げない。
//   - 出力を xterm の画面より外へ持ち出さない（text() はテスト専用の口・保存もログもしない）。
//   - 色は新しく作らない。パレットのランプとセッション色から 16 色を組む（部屋と同じ光の下に置く）。
import { Terminal } from "/ui/vendor/xterm/xterm.mjs";
import { FitAddon } from "/ui/vendor/xterm/addon-fit.mjs";
import { termClose, termInput, termList, termOpen, termResize, termStreams } from "/ui/platform/api.js";
import { RAMP, SESSION_COLORS, UI_COLORS } from "./palette.js";

const THEME = Object.freeze({
  background: UI_COLORS.bg,
  foreground: UI_COLORS.ink,
  cursor: UI_COLORS.accent,
  cursorAccent: UI_COLORS.bg,
  selectionBackground: RAMP.ink[3],
  // R110: 黒は RAMP.ink[3]（ink[0] は地 #0c0f0c との比 1.03＝ls の色・プロンプトの黒が見えなかった）
  black: RAMP.ink[3], red: RAMP.signal[2], green: RAMP.leaf[3], yellow: RAMP.amber[2],
  blue: SESSION_COLORS[8], magenta: SESSION_COLORS[3], cyan: SESSION_COLORS[0], white: RAMP.shell[2],
  brightBlack: RAMP.shell[0], brightRed: RAMP.signal[3], brightGreen: SESSION_COLORS[1],
  brightYellow: RAMP.amber[3], brightBlue: SESSION_COLORS[8], brightMagenta: SESSION_COLORS[5],
  brightCyan: SESSION_COLORS[6], brightWhite: RAMP.shell[3],
});

const FONT = '"SF Mono", "SFMono-Regular", Menlo, Monaco, "Cascadia Code", "Noto Sans Mono CJK JP", monospace';
/** R110: 文字の大きさの既定（⚙「文字の大きさ」の既定と同じ）・80 桁が入らないときに下げる下限 */
const FONT_DEFAULT = 14;
const FONT_FLOOR = 11;
const LINE_HEIGHT = 1.15;
const LIST_EVERY_MS = 3000;
/** FitAddon が桁から引く縦スクロールバーの幅（addon-fit.mjs の overviewRuler?.width || 14 と同じ数） */
const SCROLLBAR_W = 14;

/**
 * @param {object} opts { host: 端末の根（#cp-xterm・駐車場）, onChange: 一覧が変わったとき（最初の一覧の到着でも 1 回）,
 *   notice: (key, ...args) => 表示, fontSize: 文字の大きさ（数か、数を返す関数＝設定が変わったら setFont() で配り直す・既定 14）,
 *   single: () => 見えているペインが 1 つか（80 桁の自動縮小はそのときだけ・既定 true）,
 *   narrow: () => 端末の列を既定より狭めているか（R111-A2・狭めている間は 80 桁の自動縮小をしない・既定 false）,
 *   focusedId: () => 選んでいるペインの端末の id（text/dims/modes の省略時の相手・無ければ null） }
 */
export function init({ host, onChange = () => {}, onInputError = () => {}, frozen = false, disabled = false, motionStill = () => frozen,
  fontSize = FONT_DEFAULT, single = () => true, narrow = () => false, focusedId = () => null } = {}) {
  const terms = new Map();        // id → { info, xt, fit, el, send, offset, ended, hostEl, resizeTimer }
  let infos = [];                 // /api/term/list の最新（出力は含まない）
  let loaded = false;             // 一覧を 1 回でも受け取ったか（受け取った回は変化が無くても onChange を呼ぶ＝保存の復元の合図）
  let timer = 0;
  let mux = null;                 // 多重化の購読（止める関数・.active）
  let muxIds = [];                // その購読に載せた id
  let syncRaf = 0;
  const enc = new TextEncoder();

  const sig = (list) => list.map((t) => `${t.id}:${t.alive}:${t.session || ""}`).join("|");
  /** いまの文字の大きさ（段の基準）。読めない値は既定 */
  const baseFont = () => {
    const v = Number(typeof fontSize === "function" ? fontSize() : fontSize);
    return Number.isFinite(v) && v > 0 ? v : FONT_DEFAULT;
  };
  /** 見えている（ペインに置いて hidden でない）端末 */
  const visible = () => [...terms.values()].filter((t) => t.hostEl && !t.el.hidden);

  /** xterm と購読を捨てる（一覧から消えた・閉じた） */
  function drop(id) {
    const t = terms.get(id);
    if (!t) return;
    clearTimeout(t.resizeTimer);
    unobserve(t);
    t.xt.dispose();
    t.el.remove();
    terms.delete(id);
    scheduleSync();
  }

  async function refresh() {
    if (disabled) return;
    let next;
    try {
      next = (await termList()).terms || [];
    } catch {
      return;                     // サーバーが一時的に居ない＝前の一覧のまま
    }
    const changed = sig(next) !== sig(infos) || !loaded;
    loaded = true;
    infos = next;
    const alive = new Set(infos.map((x) => x.id));
    for (const id of [...terms.keys()]) if (!alive.has(id)) drop(id);   // 一覧から消えた＝捨てる
    for (const info of infos) {
      const t = terms.get(info.id);
      if (t) t.info = info;
    }
    if (changed) onChange();
  }

  // ── 購読（多重化 1 本）────────────────────────────────────────────────
  /** 見えている端末の集合が変わったら、1 フレームにまとめて購読を張り直す（ペインを描き直すたびに繋ぎ直さない） */
  function scheduleSync() {
    if (!syncRaf) syncRaf = requestAnimationFrame(syncStreams);
  }
  function syncStreams() {
    syncRaf = 0;
    const ids = visible().filter((t) => !t.ended).map((t) => t.info.id).sort();
    if (mux?.active && ids.join(",") === muxIds.join(",")) return;
    mux?.();
    mux = null;
    muxIds = ids;
    if (!ids.length) return;
    mux = termStreams(ids, {
      from: (id) => terms.get(id)?.offset ?? null,   // 読み終えたところから続ける（タブ往復・ペインの移動で reset しない）
      onHello: (id, h) => {
        const t = terms.get(id);
        if (!t) return;
        if (h.reset) {                       // 頭から流し直す＝消してから、画面モードを先に立て直す
          t.xt.reset();
          if (h.modes) t.xt.write(h.modes);
        }
        t.offset = h.from;
      },
      onOut: (id, bytes) => {
        const t = terms.get(id);
        if (!t) return;
        t.xt.write(bytes);
        t.offset += bytes.length;
      },
      onExit: (id, code) => {
        const t = terms.get(id);
        if (!t) return;
        t.xt.write(`\r\n\x1b[2m[${code === null ? "—" : `exit ${code}`}]\x1b[0m\r\n`);
        t.ended = true;                      // 終わった端末は繋ぎ直さない（再描画のたびに backlog を取り直して点滅する）
        refresh();
      },
    });
  }

  function mount(info) {
    const had = terms.get(info.id);
    if (had) return had;
    const el = document.createElement("div");
    el.className = "cpxt";
    el.dataset.term = info.id;
    el.hidden = true;
    host.append(el);
    const xt = new Terminal({
      fontFamily: FONT, fontSize: baseFont(), lineHeight: LINE_HEIGHT, theme: THEME,
      cursorBlink: !motionStill(), scrollback: 5000, allowProposedApi: false,
      convertEol: false, macOptionIsMeta: true,
    });
    const fit = new FitAddon();
    xt.loadAddon(fit);
    xt.open(el);
    const send = termInput(info.id, { onError: (err) => onInputError(err) });
    xt.onData((s) => send(enc.encode(s)));
    xt.onBinary((s) => {                       // マウス報告などの生バイト（1 文字 = 1 バイト）
      const b = new Uint8Array(s.length);
      for (let i = 0; i < s.length; i += 1) b[i] = s.charCodeAt(i) & 0xff;
      send(b);
    });
    const entry = { info, xt, fit, el, send, offset: null, ended: false, hostEl: null, resizeTimer: 0 };
    terms.set(info.id, entry);
    return entry;
  }

  // ── 大きさ ───────────────────────────────────────────────────────────
  /**
   * その端末を置いている host で桁と行を合わせ、サーバーへ送る（id ごとに 120ms まとめる）。
   * R110: 文字は設定の大きさ（base）。**見えているペインが 1 つ かつ 端末の列を狭めていない**ときだけ、80 桁を収める幅が
   * 足りなければ max(11, base − 2) に下げる（桁は今の大きさで測った数を base に換算して見る・下げた後は 82 桁入るまで戻さない
   * ＝境目での往復を止める）。分割中・列を狭めたときは字を変えない（桁が減るだけ・下限 40 桁はペインの畳みが守る）。
   */
  function fit(id) {
    const t = terms.get(id);
    if (!t || !t.hostEl || t.el.hidden || !t.el.getClientRects().length || t.hostEl.clientWidth <= 0) return;
    try {
      t.xt.options.cursorBlink = !motionStill();
      const base = baseFont();
      let fontSize = base;
      const current = t.xt.options.fontSize;
      if (single() && !narrow()) {
        const small = Math.max(FONT_FLOOR, base - 2);
        const proposed = t.fit.proposeDimensions();
        const target = current === small && small !== base ? 82 : 80;
        fontSize = proposed && proposed.cols * current / base >= target ? base : small;
      }
      if (current !== fontSize) {
        t.xt.options.fontSize = fontSize;
        // 字の実測が更新された次のフレームで桁を確定する。
        requestAnimationFrame(() => { if (terms.get(id) === t) fit(id); });
        return;
      }
      t.fit.fit();
    } catch { return; }
    clearTimeout(t.resizeTimer);
    t.resizeTimer = setTimeout(() => {
      // 待っている間に閉じられた端末へは送らない（送ると 404＝閉じた直後に必ず 1 件出た）
      if (terms.get(id) === t && t.info.alive) {
        termResize(id, t.xt.cols, t.xt.rows).catch(() => {});
      }
    }, 120);
  }

  // host（ペインの body）ごとに観測する。callback は target に居る端末を引いて合わせる（1 host に端末は 1 本）
  const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver((entries) => {
    for (const en of entries) {
      for (const t of terms.values()) if (t.hostEl === en.target && !t.el.hidden) fit(t.info.id);
    }
  });
  const observed = new Set();
  function observe(t) {
    if (!ro || !t.hostEl || observed.has(t.hostEl)) return;
    observed.add(t.hostEl);
    ro.observe(t.hostEl);
  }
  function unobserve(t) {
    const h = t.hostEl;
    if (!ro || !h || !observed.has(h)) return;
    if ([...terms.values()].some((o) => o !== t && o.hostEl === h)) return;
    observed.delete(h);
    ro.unobserve(h);
  }

  // ── 置き場所 ─────────────────────────────────────────────────────────
  /** 駐車: 根の直下へ戻して隠す（購読は次のフレームで外れる） */
  function park(id) {
    const t = terms.get(id);
    if (!t) return;
    unobserve(t);
    t.hostEl = null;
    t.el.hidden = true;
    if (t.el.parentNode !== host) host.append(t.el);
    scheduleSync();
  }
  function parkAll() {
    for (const [id, t] of terms) if (t.hostEl || !t.el.hidden) park(id);
  }

  /**
   * 見せる。host＝置くペインの body（省略時は今の host のまま＝フォーカスだけ）。その host に別の端末が居たら駐車する。
   * host が変わった回・隠れていた回だけ次のフレームで fit（再描画のたびに測り直さない）。
   * focus は**本人がこの端末を選んだときだけ** true（再描画から呼ぶときは false）。
   */
  function show(id, { host: dest = null, focus = false } = {}) {
    const info = infos.find((x) => x.id === id);
    if (!info) return false;
    const t = mount(info);
    const target = dest || t.hostEl;
    if (!target) return false;
    let moved = false;
    let keepY = null;
    if (t.hostEl !== target || t.el.parentNode !== target) {
      for (const [oid, o] of terms) if (o !== t && o.hostEl === target) park(oid);
      // 要素を移すと中のスクロールの位置が先頭へ戻る（DOM の移動＝外して付け直し）。読んでいた行を覚えておいて戻す
      const buf = t.xt.buffer.active;
      if (buf.viewportY < buf.baseY) keepY = buf.viewportY;
      unobserve(t);
      target.append(t.el);
      t.hostEl = target;
      observe(t);
      moved = true;
    }
    if (t.el.hidden) { t.el.hidden = false; moved = true; }
    scheduleSync();
    if (moved || focus) {
      requestAnimationFrame(() => {
        if (terms.get(id) !== t || t.hostEl !== target) return;
        if (moved) {
          fit(id);
          if (keepY === null) t.xt.scrollToBottom(); else t.xt.scrollToLine(keepY);
          t.xt.refresh(0, t.xt.rows - 1);
        }
        if (focus) t.xt.focus();
      });
    }
    return true;
  }

  /** 全部を駐車する（端末を出さない画面） */
  function hide() { parkAll(); }

  async function open(kind, projectId) {
    const cols = 100, rows = 30;               // 開いたあと fit が本当の大きさを送る
    const res = await termOpen(kind, projectId, cols, rows);
    await refresh();
    return res.term;
  }

  async function close(id) {
    drop(id);                                  // 先に購読を止めて捨てる（閉じた端末へ繋ぎ直しに行かない）
    try { await termClose(id); } catch { /* 既に無い */ }
    await refresh();
  }

  const tick = () => {
    if (typeof document !== "undefined" && document.hidden) return;
    refresh();
  };
  refresh();
  if (!frozen && !disabled) timer = setInterval(tick, LIST_EVERY_MS);

  /** 省略時の相手＝選んでいるペインの端末（無ければ見えている先頭） */
  const pick = (id) => terms.get(id ?? focusedId() ?? "") || null;
  /** 1 文字の幅・丈（CSS px・**設定の文字の大きさ base に換算**＝1 ペインで base−2 に下げている端末から測っても、分けた後の
   *  base の桁で下限を決める）。見えている端末の実測（FitAddon と同じ値）・無ければ桁と内寸の比・端末が無ければ文字の大きさから推す */
  function cell() {
    const list = visible();
    const t = pick() && !pick().el.hidden && pick().hostEl ? pick() : list[0];
    if (t) {
      const k = baseFont() / (t.xt.options.fontSize || baseFont());
      const c = t.xt._core?._renderService?.dimensions?.css?.cell;
      if (c && c.width > 0 && c.height > 0) return { w: c.width * k, h: c.height * k };
      const p = t.fit.proposeDimensions();
      if (p && p.cols > 0 && p.rows > 0 && t.el.clientWidth > 0) {
        return { w: k * Math.max(1, t.el.clientWidth - SCROLLBAR_W) / p.cols, h: k * t.el.clientHeight / p.rows };
      }
    }
    // 端末がまだ 1 本も見えていない: 等幅の送り幅（約 0.6em）と行の丈を少し大きめに見積もる（下限を甘くしない＝実測で畳み直さない）
    const base = baseFont();
    return { w: base * 0.61, h: Math.ceil(base * LINE_HEIGHT * 1.1) };
  }

  return {
    refresh,
    show,
    hide,
    park,
    parkAll,
    open,
    close,
    fit,
    syncStreams: scheduleSync,
    loaded: () => loaded,
    /** いまペインに置いている端末の id（駐車していないもの） */
    hosted: () => [...terms.values()].filter((t) => t.hostEl).map((t) => t.info.id),
    /** セッション（claude --session-id で起こしたもの）に対応する生きた端末 */
    forSession: (session) => (session ? infos.find((t) => t.alive && t.session === session) : null) || null,
    /** 対応するセッションが（まだ）台帳に無い端末＝shell・codex・起動直後の claude */
    unmapped: (known) => infos.filter((t) => !(t.session && known.has(t.session))),
    get: (id) => infos.find((t) => t.id === id) || null,
    /** テスト用: 端末のバッファを文字で返す（DOM の描画ではなく xterm の中身＝確実・省略時は選んでいるペインの端末） */
    text: (id) => {
      const t = pick(id);
      if (!t) return "";
      const buf = t.xt.buffer.active;
      const out = [];
      for (let i = 0; i < buf.length; i += 1) out.push(buf.getLine(i)?.translateToString(true) ?? "");
      return out.join("\n").replace(/\n+$/, "");
    },
    all: () => infos.slice(),
    /** テスト用: 端末の桁・行・字の大きさ（省略時は選んでいるペインの端末） */
    dims: (id) => {
      const t = pick(id);
      return t ? { cols: t.xt.cols, rows: t.xt.rows, fontSize: t.xt.options.fontSize } : null;
    },
    setMotion: () => { for (const t of terms.values()) t.xt.options.cursorBlink = !motionStill(); },
    /** R110: 文字の大きさの設定が変わった＝全端末を新しい基準へ。見えている端末は字の実測が更新された次のフレームで桁を確定する
     *（見えているペインが 1 つで 80 桁が入らなければ fit が base − 2 へ下げる）。隠れている端末は見せたときの fit が合わせる */
    setFont: () => {
      const base = baseFont();
      for (const t of terms.values()) if (t.xt.options.fontSize !== base) t.xt.options.fontSize = base;
      requestAnimationFrame(() => { for (const t of visible()) fit(t.info.id); });
    },
    /** テスト用: 端末の画面モード（タブ往復で消えていないかを見る・省略時は選んでいるペインの端末） */
    modes: (id) => {
      const t = pick(id);
      if (!t) return null;
      return { bracketedPaste: Boolean(t.xt.modes.bracketedPasteMode),
        appCursor: Boolean(t.xt.modes.applicationCursorKeysMode),
        alternate: t.xt.buffer.active.type === "alternate" };
    },
    /** 1 文字の幅・丈（ペインの下限＝40 桁・8 行の計算に使う） */
    cell,
    /** R111-A2: n 桁を収める端末の窓（.cpwin）の幅 px（窓の罫・根の余白・スクロールバーを足す）。見えている端末が無ければ null */
    widthForCols: (n) => {
      if (!visible().length || host.hidden) return null;
      const win = host.closest(".cpwin");
      if (!win) return null;
      const cs = getComputedStyle(host);
      const content = host.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
      if (!(content > 0)) return null;
      const chrome = win.getBoundingClientRect().width - content;
      return Math.ceil(n * cell().w + SCROLLBAR_W + chrome);
    },
    /** テスト用: いま繋いでいる購読の接続の数（多重化＝見えている端末が何本でも 1 本・無ければ 0） */
    streams: () => (mux?.active ? 1 : 0),
    /** テスト用: その接続で購読している端末の id（exit を受けた端末は除く） */
    subs: () => (mux?.active ? muxIds.filter((id) => !terms.get(id)?.ended) : []),
    disabled,
    dispose() {
      clearInterval(timer);
      if (syncRaf) cancelAnimationFrame(syncRaf);
      mux?.();
      mux = null;
      ro?.disconnect();
      for (const t of terms.values()) { clearTimeout(t.resizeTimer); t.xt.dispose(); t.el.remove(); }
      terms.clear();
    },
  };
}
