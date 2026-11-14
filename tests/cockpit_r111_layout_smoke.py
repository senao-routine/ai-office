#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R111-A4（コックピット v3.2）: 窓の配置の門＝入れ替え・境目・保存・初期化・キー・段。

本人の依頼（2026-10-07）「それぞれのウィンドウを移動させたり、境目をドラッグしたら画面が広がる」を A2（ui/cockpit/layout.js・
index.js の setLayout/paintLayout・ui/core/cplayout.js）で入れた。ここはそれを live ページ（`?ui=cockpit&life=0`・/api/office は
固定ワールド tests/fixtures/world/cockpit.json）で機械に見させる（計画 scalable-cooking-bentley の A4・「既存のピンへの影響」・「リスク」）。

起動と偽の端末は tests/cockpit_r110_ui_smoke.py の term_routes を import して使う（/api/term/list・/api/term/stream だけ偽装・
本物の pty を起こさない）。r110 の open_page は固定時刻のページを自分の context で開く＝1 の「golden の形（?t=）も既定の升目」にだけ使う
（配置の保存は context ごとの localStorage＝保存・固定時刻・デモの組は同じ context の新しいページで見る）。見切れ・境目・受付は
r110 の audit_clip／audit_seams／audit_reception、部屋の canvas の整数倍は tests/cockpit_smoke.py の ROOM_SCALE_NOW を import する。
配置の掃引は probe（window.__office.debug.cpMove／cpSwap＝メニュー・ドラッグ・キーと同じ ops）で作り、本物の pointer は各 1 回。
形は 1440×900（golden の形・×3）と 1920×1080（×4）・dark。×4 の部屋が丈に入るのは 1920×1080 か 1440×1080（A2 の注意）。

検査:
  1 既定＝不変: .main の計算値の列幅 570/574/280（1440×900）・754/830/320（1920×1080）・.main の inline は --cp-left-w・
    --cp-room-h・--cp-sheet-h だけ・cpLayoutDefault() true・境目の取っ手（.cpsplit）3 本がどれも溝の内（窓の矩形に重ならない）で
    地が透明（α=0）・r110 の audit_seams。固定時刻のページ（r110 の open_page）でも同じ列幅と inline。
  2 入れ替え（本物の pointer）: #sec-office の帯を #cp-center の中央へドラッグ → cols=[[terminal,reception],[office],[roster]]・
    部屋の列 184k+18・canvas の width 属性 184k・CSS 幅＝属性・ragged 0・cpLayout().areas と各窓の計算値の grid-row/column-start が一致・
    指示欄（#sheet）が端末の列の x の内で端末の下端の直下（溝 8）・トーストの中心が端末の列の内（showToast の口が無い＝#toast を一時的に
    出して測る）・audit_clip／audit_seams／audit_reception・ドラッグ中（pointerup の前）も roomMotion().shownT が進む。
  3 配置の掃引: 代表 6 配置 × 2 形を cpMove／cpSwap で作り（既定から最短の手順を cplayout の純関数で探して probe で当てる）、
    3 トラック・窓 4 つが .main の内で重ならない・部屋の列（部屋だけの列）が 184k+18・canvas 整数倍・同じ列の 2 窓の間 8px・
    受付／名簿の窓 ≥120px・端末の窓 ≥200px・audit_clip。
  4 境目: 1440×900 で .cpsplit[data-between=0] を本物の pointer で右へ 30 ステップ → 第 3 列 ±0・部屋の列は {386, 570}・k の変化 ≤1 回・
    端末 ≥40 桁と字の掟（下の FONT）。cpSplitTo(0, 400) で ×2（386）→ 取っ手の dblclick で既定。1440×1080 で cpSplitTo(0, 760) → ×4（754）＝
    端末は 80 桁に足りない幅（narrow）。1402×1080（3 カラムの最も狭い形）で同じ操作＝端末の残りが 40 桁の退避値 352px ちょうど
    （×4 は求めない・40 桁と字の掟だけ）。1920×1080 で ×2 → cpSplitTo(0, 760) → ×4（754）。
    [data-between=1] を本物の pointer で左へ → 名簿の列が 240〜520 で変わり第 1 列 ±0 → dblclick で既定。cpRowSplitTo で部屋が 160k へスナップ。
    字の掟（FONT）: 端末の字は設定の base。例外は R110 の「80 桁が入らないとき max(11, base−2)」だけで、それも境目で端末の最低を
    560 未満へ下げた配置（narrow＝widths.termMin < 560）では使わない（本人の裁定「40 桁まで許す・文字は小さくしない＝桁が減るだけ」）。
    narrow の字は ui/cockpit/term.js の持ち分（A2 は index.js から initTerm に narrow を渡すだけ・80 桁の base−2 を飛ばすのは B2）＝
    term.js が narrow を読まない間だけ違反を「保留」として数え（行頭 －・最後の行に数）、読んだ瞬間に赤にする（TERM_READS_NARROW）。
    1440×900 の既定（端末 574px）は R110 のとおり 80 桁が入らない＝base−2（75 桁・12px）が正しい（narrow ではない）。
  5 保存: 非既定で page.reload() → cpLayout() が同じ・localStorage aioffice.cockpit.layout が JSON（v:1）。同じ context で ?t=3.2 →
    既定・鍵は残っていて書き換わらない。?demo=1 → 既定・鍵を書かない（probe で配置を替えても鍵は不変）。
  6 初期化: ⚙ を開いて「配置」の「初期化」→ 鍵 null・既定の矩形・cpLayoutDefault() true。cpResetLayout() も同じ（変わったら true）。
  7 壊れた値: garbage／{"v":0}／未知の id／重複／空の列 を入れて開く → 既定・鍵が消える。
  8 キー／メニュー: 帯に focus → Alt+ArrowRight が nudgeWindow（純関数）と同じ結果で cpNudge とも同じ・Enter で #cp-move-menu・
    項目の disabled は「その向きが無変化」と一致・Escape で閉じて帯へフォーカスが戻る・右クリックでも開く。
    #composeinput にフォーカスがある間の Alt+ArrowRight は配置を変えない。
  9 2 カラム／1 列では適用しない: 非既定を保存したまま 1280×800・390×844 → applied false・.main の inline に --cp-tpl-* が無い・
    .cpsplit が display:none・帯に tabindex が無い。1440×900 へ戻すと再適用。
 10 眺めるモード: 非既定の配置で F → #cp-center の幅が変わらない（R110-h の型）・Esc で戻る（配置は適用されたまま）。
  ⓪ console.error / pageerror / window の error（ResizeObserver loop を含む）/ unhandledrejection = 0

使い方: python3 tests/cockpit_r111_layout_smoke.py   （verify.sh ▶7 の run_ui から呼ぶ・Playwright 必要）
"""
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from ui_shot import SWIFTSHADER, free_port, start_server  # noqa: E402
from cockpit_r110_ui_smoke import (  # noqa: E402
    audit_clip, audit_reception, audit_seams, open_page, settle, term_routes)
from cockpit_smoke import ROOM_H, ROOM_SCALE_NOW, ROOM_W  # noqa: E402

WORLD = ROOT / "tests" / "fixtures" / "world" / "cockpit.json"
LAYOUT_KEY = "aioffice.cockpit.layout"
ROOM_CHROME = 18                        # 部屋の列 = 184k + 18（ui/core/cplayout.js の ROOM_CHROME_W）
GUTTER = 8                              # 窓の間の溝
TOL = 0.5                               # 矩形の比較の許し（CSS px）
WIDE, FULL, TALL, EDGE = (1440, 900), (1920, 1080), (1440, 1080), (1402, 1080)
DEFAULT_THIRD = 280                     # 4: 1440 幅と 1402 幅の第 3 列（名簿・clamp(280px, 18vw, 320px)）
# 1: 既定の .main の列幅（計算値）。golden の形（×3）と本人の画面（×4）
DEFAULT_TRACKS = {WIDE: (570, 574, 280), FULL: (754, 830, 320)}
INLINE_OK = {"--cp-left-w", "--cp-room-h", "--cp-sheet-h"}
# 3: 代表 6 配置（部屋が 1 窓の列／2 窓の列の上・下／端末と同じ列／端末が 2 窓の列の上・下）
SWEEP = [
    [["roster"], ["office", "reception"], ["terminal"]],
    [["terminal"], ["office", "reception"], ["roster"]],
    [["office", "terminal"], ["reception"], ["roster"]],
    [["reception", "office"], ["terminal"], ["roster"]],
    [["terminal", "reception"], ["office"], ["roster"]],
    [["roster", "terminal"], ["office"], ["reception"]],
]
SWAPPED = [["terminal", "reception"], ["office"], ["roster"]]   # 2 の入れ替えの結果
MIN_H = {"reception": 120, "roster": 120, "terminal": 200}      # 3: 窓の丈の下限
TERM_MIN_COLS = 40
TERM_REFIT = 82                         # R110: 下げた後は 82 桁入るまで戻さない（term.js）
FONT_FLOOR = 11
DRAG_STEPS = 30                         # 4: 境目のドラッグの刻み
DRAG_DX = 7                             # 4: 1 刻みの px（30 刻みで 210px＝×4 を提案する所まで引く）
BROKEN = [
    ("garbage", "garbage"),
    ('{"v":0}', '{"v":0}'),
    ("未知の id", '{"v":1,"cols":[["office","reception"],["terminal"],["bogus"]]}'),
    ("重複", '{"v":1,"cols":[["office","office"],["terminal"],["roster"]]}'),
    ("空の列", '{"v":1,"cols":[["office","reception","roster"],["terminal"],[]]}'),
]

# window の error（ResizeObserver loop は console に出ない＝ここで拾う）と unhandledrejection を数える
# R121: 1 ペインの中央の既定は作業ビュー。この門は本物の端末（xterm）を測る＝「ターミナル派」の保存を入れて開く
# （既定と［ターミナル］⇄［作業］の切替そのものは cockpit_term_smoke が見る）
TERMINAL_PREF = "try { localStorage.setItem('aioffice.cockpit.center', 'terminal'); } catch (e) {}"
ERR_INIT = """
  window.__r111Errors = [];
  addEventListener('error', (e) => window.__r111Errors.push('window.error: ' + String(e.message || e.error || e)));
  addEventListener('unhandledrejection', (e) => window.__r111Errors.push('unhandledrejection: ' + String(e.reason)));
"""

# 配置の今（純データ・升目の計算値・窓の矩形と計算値の grid の位置・inline の変数・取っ手・帯・端末・保存）
LAYOUT_NOW = """() => {
  const d = window.__office.debug, L = d.cpLayout(), m = document.querySelector('.main');
  const R = (v) => Math.round(v * 10) / 10;
  const box = (e) => { if (!e || !e.getClientRects().length) return null; const q = e.getBoundingClientRect();
    return { l: R(q.left), t: R(q.top), r: R(q.right), b: R(q.bottom), w: R(q.width), h: R(q.height) }; };
  const SEL = { office: '#win-office', reception: '#win-reception', terminal: '#cp-center', roster: '#cp-right', sheet: '#sheet' };
  const win = {}, start = {};
  for (const [id, s] of Object.entries(SEL)) {
    const e = document.querySelector(s), cs = getComputedStyle(e);
    win[id] = box(e);
    start[id] = [cs.gridRowStart, cs.gridColumnStart, cs.gridRowEnd, cs.gridColumnEnd].join(' / ');
  }
  let ls = null; try { ls = localStorage.getItem('__KEY__'); } catch { ls = 'ERR'; }
  return { cols: L.cols, widths: L.widths, rows: L.rows, isDefault: L.isDefault, applied: L.applied, k: L.k, areas: L.areas,
    stored: L.stored, dragging: L.dragging, def: d.cpLayoutDefault(),
    tracks: getComputedStyle(m).gridTemplateColumns.trim().split(/\\s+/).map(parseFloat),
    trackStr: getComputedStyle(m).gridTemplateColumns, main: box(m), win, start,
    inline: [...m.style], cpsplit: [...m.querySelectorAll(':scope > .cpsplit')].map((e) => ({ kind: e.dataset.split,
      between: e.dataset.between ?? null, box: box(e), display: getComputedStyle(e).display, bg: getComputedStyle(e).backgroundColor })),
    bands: [...document.querySelectorAll('.cpsec[data-win]')].map((b) => ({ win: b.dataset.win, tabindex: b.getAttribute('tabindex') })),
    scale: window.__office.stats().scale, term: d.termDims(), ls };
}""".replace("__KEY__", LAYOUT_KEY)

# 3: 既定から target（cols）へ、cplayout の純関数で最短の手順（swap／move）を探し、probe（cpSwap／cpMove＝ops）で当てる
APPLY_COLS = """async (target) => {
  const L = await import('/ui/core/cplayout.js');
  const d = window.__office.debug;
  d.cpResetLayout();
  const key = (cols) => JSON.stringify(cols);
  const want = key(target);
  const ops = [];
  for (let i = 0; i < L.WINDOWS.length; i += 1) for (let j = i + 1; j < L.WINDOWS.length; j += 1) ops.push(['swap', L.WINDOWS[i], L.WINDOWS[j]]);
  for (const id of L.WINDOWS) for (let c = 0; c < 3; c += 1) for (let r = 0; r < 2; r += 1) ops.push(['move', id, c, r]);
  const back = new Map([[key(L.DEFAULT_LAYOUT.cols), null]]);
  let q = [L.DEFAULT_LAYOUT];
  while (q.length && !back.has(want)) {
    const nq = [];
    for (const l of q) for (const op of ops) {
      const n = op[0] === 'swap' ? L.swapWindows(l, op[1], op[2]) : L.moveWindow(l, op[1], op[2], op[3]);
      const k = key(n.cols);
      if (back.has(k)) continue;
      back.set(k, [key(l.cols), op]);
      nq.push(n);
    }
    q = nq;
  }
  const path = [];
  for (let k = want; back.get(k); k = back.get(k)[0]) path.unshift(back.get(k)[1]);
  const done = path.map((op) => (op[0] === 'swap' ? d.cpSwap(op[1], op[2]) : d.cpMove(op[1], op[2], op[3])));
  return { found: back.has(want), path, done, cols: d.cpLayout().cols };
}"""

# 2: トーストの中心（showToast の口は probe に無い＝#toast を一時的に出して測り、元に戻す）
TOAST_AT = """() => {
  const t = document.querySelector('#toast'), was = t.hidden, m = document.createElement('div');
  m.className = 'tmsg show'; m.textContent = 'R111 の配置の門（トーストの位置）';
  t.append(m); t.hidden = false;
  const q = t.getBoundingClientRect(), mq = m.getBoundingClientRect();
  const out = { cx: (q.left + q.right) / 2, l: mq.left, r: mq.right, w: mq.width, pos: getComputedStyle(t).position };
  m.remove(); t.hidden = was;
  return out;
}"""

# 8: 純関数の nudgeWindow の結果と、メニューの各向きが「無変化」か（disabled の正解）
NUDGE_WANT = """async ([id, dir]) => {
  const L = await import('/ui/core/cplayout.js');
  const c = window.__office.debug.cpLayout();
  const l = L.normalizeLayout({ v: 1, cols: c.cols, widths: c.widths, rows: c.rows });
  const dirs = {};
  for (const d of ['left', 'right', 'up', 'down']) dirs[d] = L.nudgeWindow(l, id, d) === l;
  return { cols: L.nudgeWindow(l, id, dir).cols, still: dirs };
}"""
# 4: term.js が initTerm の narrow を読むか（A2 は index.js から渡すだけ・80 桁の base−2 を飛ばすのは term.js の B2）。
# 読まない間は narrow の配置で字を下げていても保留（数えて最後の行に出す）・読んだ瞬間に judge_font が赤にする
TERM_READS_NARROW = """async () => /\\bnarrow\\b/.test(await (await fetch('/ui/cockpit/term.js')).text())"""
MENU_NOW = """() => {
  const m = document.querySelector('#cp-move-menu');
  return { shown: !!m && !m.hidden && m.getClientRects().length > 0,
    items: [...m.querySelectorAll('[data-dir]')].map((b) => ({ dir: b.dataset.dir, disabled: b.disabled })),
    focus: document.activeElement ? (document.activeElement.id || document.activeElement.dataset.win || document.activeElement.tagName) : null,
    inMenu: m.contains(document.activeElement) };
}"""


def j(v):
    return json.dumps(v, ensure_ascii=False)


def near(a, b, tol=TOL):
    return a is not None and b is not None and abs(a - b) <= tol


def kind_of(cols, c):
    """列の種類（ui/core/cplayout.js の columnKind と同じ決め方）"""
    col = cols[c]
    return "flex" if "terminal" in col else "room" if "office" in col else "fixed"


def col_of(cols, wid):
    return next(c for c, col in enumerate(cols) if wid in col)


def overlap(a, b):
    return min(a["r"], b["r"]) - max(a["l"], b["l"]) > TOL and min(a["b"], b["b"]) - max(a["t"], b["t"]) > TOL


def is_narrow(widths):
    """narrow＝本人が境目で端末の列の幅を変えている配置（index.js の initTerm の narrow と同じ式）: 部屋×端末の境目で termMin を 560 未満へ
    下げたか、名簿／受付の境目で fixed の幅を保存したか。既定と窓の入れ替えだけの配置は narrow でない（R110 の 80 桁の base−2 のまま）"""
    return (widths.get("termMin") or 560) < 560 or widths.get("roster") is not None or widths.get("reception") is not None


def font_rule(term, base, narrow):
    """端末の字の掟（FONT）: base が基本。R110 の 80 桁の縮小（max(11, base−2)・82 桁入るまで戻さない）は narrow では使わない。
    (ok, 説明, narrow_font)。narrow_font＝桁は足りているが narrow の配置で字を下げている（掟違反の中でこれだけを分けて返す＝
    term.js が narrow を読むまでは呼び手が保留にする・TERM_READS_NARROW）"""
    if not term:
        return False, "端末が描かれていない（termDims() が null）", False
    size, cols = term.get("fontSize"), term.get("cols") or 0
    low = max(FONT_FLOOR, base - 2)
    if cols < TERM_MIN_COLS:
        return False, f"端末 {cols} 桁・{size}px（{TERM_MIN_COLS} 桁以上＝本人の裁定の下限）", False
    if size == base:
        return True, f"{cols} 桁・{size:g}px（base）", False
    if narrow:
        return False, (f"端末の最低を 560 未満へ下げた配置（narrow）で字が {size:g}px・{cols} 桁（base {base:g}px のまま桁が減るはず）"
                       " → ui/cockpit/term.js の fitShown が initTerm の narrow を読んで 80 桁の base−2 を飛ばす"), True
    if size == low and cols * low / base < TERM_REFIT:
        return True, f"{cols} 桁・{size:g}px（80 桁が入らない幅の base−2＝R110）", False
    return False, f"端末の字 {size:g}px・{cols} 桁（base {base:g} か、80 桁が入らないときの {low:g}）", False


def judge_font(term, base, narrow, gate):
    """font_rule を門の文脈で判定する。narrow の字の違反は、term.js がまだ narrow を読まない間（gate["readsNarrow"] が False）
    だけ保留（gate["pend"] に名前と理由を積む＝最後の行に数を出す・黙って緑にしない）。term.js が narrow を読んだ瞬間に赤へ変わる。
    (ok, 行の要約, 赤の理由)"""
    ok, why, narrow_font = font_rule(term, base, narrow)
    short = why.split(" → ")[0]
    if narrow_font and not gate["readsNarrow"]:
        gate["pend"](why)
        return True, short + "（保留）", ""
    return ok, short, why


def wait_layout(page, ms=200):
    """配置を替えた後: setLayout は同じフレームで升目と倍率を書く＝RO → rAF の描き直しと字の実測を待つ"""
    page.wait_for_timeout(ms)
    page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")


def wait_term(page, timeout=8000):
    try:
        page.wait_for_function("() => !!window.__office.debug.termDims?.()", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001 — 出ないことは呼び手が空振りの赤にする
        return False


def term_settled(page, timeout=3000):
    """端末の字と桁が落ち着くまで（fitShown は字を替えた次のフレームで桁を確定する）。2 回続けて同じ値を返す"""
    last, t0 = None, time.time()
    while time.time() - t0 < timeout / 1000:
        cur = page.evaluate("() => window.__office.debug.termDims()")
        if cur and cur == last:
            return cur
        last = cur
        page.wait_for_timeout(120)
    return last


def base_font(page):
    return float(page.evaluate("() => parseFloat(getComputedStyle(document.querySelector('.hud.ui-cockpit'))"
                               ".getPropertyValue('--cp-font-base'))"))


def take_errors(page, errors, label):
    try:
        for e in page.evaluate("() => (window.__r111Errors || []).splice(0)"):
            errors.append(f"{e}（{label}）")
    except Exception:  # noqa: BLE001 — 閉じたページ
        pass


def open_live(ctx, port, world, errors, label, query=""):
    """live ページ（?t= 無し・散歩なし）を ctx の新しいページで開く。/api/office は ctx の route が固定ワールドを返す"""
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror({label}): {e}"))
    page.on("console", lambda m: errors.append(f"console.error({label}): {m.text}") if m.type == "error" else None)
    # networkidle は使わない（live は /api/events を張りっぱなし）
    page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&life=0{query}", wait_until="domcontentloaded")
    page.bring_to_front()                       # 裏のページは rAF が回らない
    ready(page, world)
    return page


def ready(page, world):
    page.wait_for_function("() => window.__office && window.__office.inject && window.__office.debug.cpLayout", timeout=30000)
    page.wait_for_selector(".cproom", timeout=30000)
    if world is not None:
        page.evaluate("w => window.__office.inject(w)", world)
    wait_layout(page, 250)


# ── 1 既定＝不変 ────────────────────────────────────────────────────────────
def check_default(page, shape, check, tag=""):
    page.mouse.move(1, 1)                        # 取っ手のホバー（地が付く）を外す
    wait_layout(page, 100)
    m = page.evaluate(LAYOUT_NOW)
    want = DEFAULT_TRACKS[shape]
    why = []
    if len(m["tracks"]) != 3 or any(not near(a, b, 1) for a, b in zip(m["tracks"], want)):
        why.append(f"列幅 {m['trackStr']}（{want} のはず）")
    extra = sorted(set(m["inline"]) - INLINE_OK)
    if extra:
        why.append(f".main の inline に既定で書かない変数 {extra}")
    if not m["def"] or not m["isDefault"] or m["applied"]:
        why.append(f"cpLayoutDefault {m['def']}・isDefault {m['isDefault']}・applied {m['applied']}")
    wins = [m["win"][w] for w in ("office", "reception", "terminal", "roster")]
    handles = [h for h in m["cpsplit"] if h["display"] != "none" and h["box"]]
    if len(handles) != 3:
        why.append(f"空振り: 見えている取っ手が {len(handles)} 本（3 本のはず）")
    for h in handles:
        name = f".cpsplit[{h['kind']}{'' if h['between'] is None else ' ' + h['between']}]"
        b = h["box"]
        if min(b["w"], b["h"]) < 6:
            why.append(f"{name} が細すぎる（{b}）")
        hit = [w for w in wins if w and overlap(b, w)]
        if hit:
            why.append(f"{name} {b} が窓の矩形に重なる（溝の外）")
        alpha = re.findall(r"[\d.]+", h["bg"])
        if h["bg"] != "transparent" and not (len(alpha) == 4 and float(alpha[3]) == 0):
            why.append(f"{name} の地が透明でない（{h['bg']}）")
    check(not why, f"1 {tag}{shape[0]}×{shape[1]}: 既定の列幅 {'/'.join(str(round(x)) for x in m['tracks'])}・"
          f"inline は {sorted(set(m['inline']))}・cpLayoutDefault・取っ手 {len(handles)} 本が溝の内で透明", " / ".join(why))
    return m


# ── 2 入れ替え（本物の pointer）─────────────────────────────────────────────
def check_swap(page, check):
    band = page.locator("#sec-office").bounding_box()
    center = page.locator("#cp-center").bounding_box()
    sx, sy = band["x"] + band["width"] / 2, band["y"] + band["height"] / 2
    tx, ty = center["x"] + center["width"] / 2, center["y"] + center["height"] / 2
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move(tx, ty, steps=12)
    during = page.evaluate("""() => ({ t: window.__office.debug.roomMotion().shownT, drag: window.__office.debug.cpLayout().dragging,
      ghost: !!document.querySelector('.cpghost'), drop: [...document.querySelectorAll('.cp-drop')].map((e) => e.id) })""")
    try:                                         # 指を離す前に部屋の時計が進むのを待つ（高負荷でも 1 刻みは届く）
        page.wait_for_function("(t) => window.__office.debug.roomMotion().shownT > t", arg=during["t"], timeout=4000)
    except Exception:  # noqa: BLE001 — 進まないことは下で赤にする
        pass
    later = page.evaluate("() => window.__office.debug.roomMotion().shownT")
    page.mouse.up()
    wait_layout(page, 300)
    check(during["drag"] == "window" and during["ghost"] and during["drop"] == ["cp-center"] and later > during["t"],
          f"2 1440×900: 帯のドラッグ中（pointerup の前）も部屋の時計が進む（shownT {during['t']:.2f} → {later:.2f}）・"
          "題名の写し・落とし先は端末の窓", j({**during, "later": later}))

    m = page.evaluate(LAYOUT_NOW)
    k = m["k"]
    room = m["win"]["office"]
    c = page.evaluate(ROOM_SCALE_NOW, [ROOM_W, ROOM_H])
    why = []
    if m["cols"] != SWAPPED:
        why.append(f"cols {m['cols']}（{SWAPPED} のはず）")
    if not m["applied"] or m["def"]:
        why.append(f"applied {m['applied']}・cpLayoutDefault {m['def']}")
    if not near(m["tracks"][1], ROOM_W * k + ROOM_CHROME) or not near(room["w"], ROOM_W * k + ROOM_CHROME):
        why.append(f"部屋の列 {m['tracks'][1]}px・窓 {room['w']}px（184×{k}+18 = {ROOM_W * k + ROOM_CHROME}）")
    if c["attrW"] != ROOM_W * c["scale"] or c["cssW"] != c["attrW"] or c["ragged"] or c["scale"] != k:
        why.append(f"canvas {c['attrW']}（CSS {c['cssW']}）・×{c['scale']}・ragged {c['ragged']}（184×{k}・CSS＝属性・0）")
    bad_area = {w: (m["areas"][w], m["start"][w]) for w in ("office", "reception", "terminal", "roster", "sheet")
                if m["areas"][w] != m["start"][w]}
    if bad_area:
        why.append(f"cpLayout().areas と計算値の grid の位置が食い違う {bad_area}")
    check(not why, f"2 1440×900: 帯を端末へ落とすと {m['cols']}・部屋の列 {round(m['tracks'][1])}px＝184×{k}+18・canvas {c['attrW']}px"
          f"（CSS＝属性・ragged 0）・areas と各窓の grid の位置が一致", " / ".join(why))

    # 指示欄は端末の列の内で端末の直下・トーストの中心は端末の列の内
    page.locator("#agents .pxrow").first.click()
    try:
        page.wait_for_function("() => { const s = document.querySelector('#sheet'); return !!s && !s.hidden && s.getClientRects().length > 0; }",
                               timeout=4000)
    except Exception:  # noqa: BLE001 — 開かないことは下で空振りの赤にする
        pass
    wait_layout(page, 300)
    s = page.evaluate(LAYOUT_NOW)
    term, sheet = s["win"]["terminal"], s["win"]["sheet"]
    why = []
    if not sheet:
        why.append("空振り: 名簿の行を押しても指示欄が開かない")
    else:
        if sheet["l"] < term["l"] - TOL or sheet["r"] > term["r"] + TOL:
            why.append(f"指示欄の x {sheet['l']}〜{sheet['r']} が端末の列 {term['l']}〜{term['r']} の外")
        if not near(sheet["t"] - term["b"], GUTTER, 1):
            why.append(f"指示欄の上端 {sheet['t']} と端末の下端 {term['b']} の間が {round(sheet['t'] - term['b'], 1)}px（{GUTTER}px）")
        if s["areas"]["sheet"] != s["start"]["sheet"]:
            why.append(f"指示欄の grid の位置 {s['start']['sheet']}（{s['areas']['sheet']}）")
    t = page.evaluate(TOAST_AT)
    if not (term["l"] <= t["cx"] <= term["r"]) or t["l"] < term["l"] - TOL or t["r"] > term["r"] + TOL:
        why.append(f"トーストの中心 {round(t['cx'], 1)}・幅 {round(t['l'], 1)}〜{round(t['r'], 1)} が端末の列 {term['l']}〜{term['r']} の外")
    check(not why, f"2 1440×900: 入れ替えた後の指示欄は端末の列の内で直下（溝 {GUTTER}）・トーストの中心 {round(t['cx'])}px は端末の列 "
          f"{term['l']:.0f}〜{term['r']:.0f} の内", " / ".join(why))
    page.keyboard.press("Escape")
    wait_layout(page, 200)
    page.mouse.move(1, 1)
    audit_clip(page, "1440×900 入れ替え後", check)
    audit_seams(page, "1440×900 入れ替え後", check)
    audit_reception(page, "1440×900 入れ替え後", check)


# ── 3 配置の掃引 ────────────────────────────────────────────────────────────
def check_sweep(page, shape, check):
    for target in SWEEP:
        r = page.evaluate(APPLY_COLS, target)
        wait_layout(page, 250)
        tag = f"{shape[0]}×{shape[1]} {target}"
        m = page.evaluate(LAYOUT_NOW)
        c = page.evaluate(ROOM_SCALE_NOW, [ROOM_W, ROOM_H])
        k = m["k"]
        why = []
        if not r["found"] or not all(r["done"]) or m["cols"] != target:
            why.append(f"空振り: 配置を作れない（手順 {r['path']}・結果 {r['done']}・cols {m['cols']}）")
        if len(m["tracks"]) != 3:
            why.append(f"トラック {m['trackStr']}（3 本）")
        main = m["main"]
        wins = {w: m["win"][w] for w in ("office", "reception", "terminal", "roster")}
        for w, b in wins.items():
            if not b or b["l"] < main["l"] - TOL or b["r"] > main["r"] + TOL or b["t"] < main["t"] - TOL or b["b"] > main["b"] + TOL:
                why.append(f"{w} の窓 {b} が .main {main} の外")
        names = list(wins)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if wins[a] and wins[b] and overlap(wins[a], wins[b]):
                    why.append(f"{a} と {b} の窓が重なる")
        oc = col_of(target, "office")
        want_w = ROOM_W * k + ROOM_CHROME
        if not wins["office"]:
            why.append("部屋の窓が見えない")
        elif kind_of(target, oc) == "room":
            if not near(m["tracks"][oc], want_w) or not near(wins["office"]["w"], want_w):
                why.append(f"部屋の列 {m['tracks'][oc]}px・窓 {wins['office']['w']}px（184×{k}+18 = {want_w}）")
        elif wins["office"]["w"] < want_w - TOL:
            why.append(f"端末と同じ列の部屋の窓 {wins['office']['w']}px が 184×{k}+18 = {want_w} より狭い")
        if c["attrW"] != ROOM_W * c["scale"] or c["cssW"] != c["attrW"] or c["ragged"] or c["scale"] != k:
            why.append(f"canvas {c['attrW']}（CSS {c['cssW']}）・×{c['scale']}・ragged {c['ragged']}")
        for col in target:
            if len(col) == 2 and wins[col[0]] and wins[col[1]]:
                up, lo = wins[col[0]], wins[col[1]]
                if not near(lo["t"] - up["b"], GUTTER):
                    why.append(f"同じ列の {col[0]} と {col[1]} の間が {round(lo['t'] - up['b'], 1)}px（{GUTTER}px）")
        for w, lo in MIN_H.items():
            if wins[w] and wins[w]["h"] < lo - TOL:
                why.append(f"{w} の窓の丈 {wins[w]['h']}px（{lo}px 以上）")
        if not m["applied"]:
            why.append("applied が false（非既定の配置が升目に効いていない）")
        check(not why, f"3 {tag}: ×{k}・3 トラック {'/'.join(str(round(x)) for x in m['tracks'])}・窓 4 つが .main の内で重ならない・"
              f"同じ列の溝 {GUTTER}px・丈の下限・canvas 整数倍", " / ".join(why))
        audit_clip(page, tag, check)
    page.evaluate("() => window.__office.debug.cpResetLayout()")
    wait_layout(page)


# ── 4 境目 ──────────────────────────────────────────────────────────────────
def handle_center(page, sel):
    b = page.locator(sel).bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def check_splits(page, check, gate):
    page.set_viewport_size({"width": WIDE[0], "height": WIDE[1]})
    page.evaluate("() => window.__office.debug.cpResetLayout()")
    wait_layout(page, 300)
    base = base_font(page)
    col0 = '.main > .cpsplit[data-between="0"]'
    x0, y0 = handle_center(page, col0)
    start = page.evaluate(LAYOUT_NOW)
    page.mouse.move(x0, y0)
    page.mouse.down()
    seq = []
    for i in range(1, DRAG_STEPS + 1):
        page.mouse.move(x0 + DRAG_DX * i, y0)
        seq.append(page.evaluate("""() => ({ k: window.__office.debug.cpLayout().k, scale: window.__office.stats().scale,
          tracks: getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\\s+/).map(parseFloat),
          drag: window.__office.debug.cpLayout().dragging })"""))
    page.mouse.up()
    wait_layout(page, 300)
    after = page.evaluate(LAYOUT_NOW)
    dims = term_settled(page)
    ks = [start["k"]] + [s["k"] for s in seq]
    flips = sum(1 for a, b in zip(ks, ks[1:]) if a != b)
    third = {round(s["tracks"][2], 1) for s in seq} | {round(after["tracks"][2], 1)}
    rooms = {round(s["tracks"][0]) for s in seq} | {round(after["tracks"][0])}
    narrow = is_narrow(after["widths"])
    ok_f, why_f, full_f = judge_font(dims, base, narrow, gate)
    why = []
    if any(s["drag"] != "split" for s in seq):
        why.append(f"空振り: ドラッグ中に dragging が split でない（{[s['drag'] for s in seq][:4]}）")
    if flips > 1:
        why.append(f"k が {flips} 回変わった（{ks}）")
    if len(third) != 1 or not near(next(iter(third)), start["tracks"][2]):
        why.append(f"第 3 列が動いた {sorted(third)}（{start['tracks'][2]} のまま）")
    if not rooms <= {ROOM_W * 2 + ROOM_CHROME, ROOM_W * 3 + ROOM_CHROME}:
        why.append(f"部屋の列 {sorted(rooms)}（386 か 570＝丈が ×4 を許さない 1440×900 でスナップ）")
    if not ok_f:
        why.append(full_f)
    check(not why, f"4 1440×900: 境目 0 を右へ {DRAG_STEPS} 刻み（{DRAG_DX * DRAG_STEPS}px）→ 第 3 列 ±0・部屋の列 {sorted(rooms)}・"
          f"k の変化 {flips} 回・端末 {why_f}", " / ".join(why))

    # cpSplitTo で ×2 → 取っ手の dblclick で既定
    page.evaluate("() => window.__office.debug.cpSplitTo(0, 400)")
    wait_layout(page, 300)
    small = page.evaluate(LAYOUT_NOW)
    dims2 = term_settled(page)
    ok_f2, why_f2, full_f2 = judge_font(dims2, base, is_narrow(small["widths"]), gate)
    x1, y1 = handle_center(page, col0)
    page.mouse.dblclick(x1, y1)
    wait_layout(page, 300)
    back = page.evaluate(LAYOUT_NOW)
    why = []
    if small["k"] != 2 or not near(small["tracks"][0], ROOM_W * 2 + ROOM_CHROME) or small["def"]:
        why.append(f"cpSplitTo(0, 400) で ×{small['k']}・列 {small['tracks']}（×2・386）")
    if not near(small["tracks"][2], start["tracks"][2]):
        why.append(f"第 3 列 {small['tracks'][2]}（{start['tracks'][2]} のまま）")
    if not ok_f2:
        why.append("×2 で: " + full_f2)
    if not back["def"] or any(not near(a, b, 1) for a, b in zip(back["tracks"], DEFAULT_TRACKS[WIDE])):
        why.append(f"dblclick の後 cpLayoutDefault {back['def']}・列 {back['tracks']}（既定 {DEFAULT_TRACKS[WIDE]}）")
    check(not why, f"4 1440×900: cpSplitTo(0, 400) で ×2（386）・端末 {why_f2} → 取っ手の dblclick で既定 {DEFAULT_TRACKS[WIDE]}",
          " / ".join(why))

    # 見た目の変わらない小さなドラッグ（20px）で部屋の上限（roomCap）を保存しない（A4 が見つけた穴＝1440×900 で roomCap 3 が残り、
    # 後で 1920 に広げても ×4 に伸びなかった）。今の幅と丈が選ぶ倍率以上の上限は外す（layout.js の splitTo・measure.roomKFit）
    x2, y2 = handle_center(page, col0)
    page.mouse.move(x2, y2)
    page.mouse.down()
    for i in range(1, 5):
        page.mouse.move(x2 + 5 * i, y2)
    page.mouse.up()
    wait_layout(page, 300)
    tiny = page.evaluate(LAYOUT_NOW)
    page.set_viewport_size({"width": 1920, "height": 1080})
    wait_layout(page, 400)
    grown = page.evaluate(LAYOUT_NOW)
    why = []
    if tiny["widths"].get("roomCap") is not None or tiny["stored"] not in (None, ""):
        why.append(f"20px のドラッグで保存された widths={tiny['widths']}・stored={tiny['stored']!r}")
    if grown["k"] != 4 or not near(grown["tracks"][0], ROOM_W * 4 + ROOM_CHROME):
        why.append(f"1920×1080 に広げても ×{grown['k']}・列 {grown['tracks']}（×4・754）")
    check(not why, "4 1440×900: 見た目の変わらない 20px のドラッグでは roomCap を保存しない → 1920×1080 で ×4 に伸びる", " / ".join(why))
    page.evaluate("() => window.__office.debug.cpResetLayout()")
    page.set_viewport_size({"width": WIDE[0], "height": WIDE[1]})
    wait_layout(page, 300)

    # 丈が ×4 を許す形で部屋を ×4 へ広げる＝端末の列は 80 桁に足りない（境目は 40 桁まで許す＝narrow・字は下げない）。
    # 1440×1080: 残り 390px（×4 に届く）。1402×1080（3 カラムの最も狭い形・既定は ×2）: 残りが 40 桁の退避値 352px ちょうど＝
    # 40 桁の下限の端（届かないなら ×4 を断ってよい＝×4 は求めず、40 桁と字の掟だけ見る）
    for shape, need4 in ((TALL, True), (EDGE, False)):
        tag = f"{shape[0]}×{shape[1]}"
        page.set_viewport_size({"width": shape[0], "height": shape[1]})
        page.evaluate("() => window.__office.debug.cpResetLayout()")
        wait_layout(page, 300)
        k0 = page.evaluate("() => window.__office.debug.cpLayout().k")
        page.evaluate("() => window.__office.debug.cpSplitTo(0, 760)")
        wait_layout(page, 300)
        tall = page.evaluate(LAYOUT_NOW)
        dims3 = term_settled(page)
        narrow3 = is_narrow(tall["widths"])
        ok_f3, why_f3, full_f3 = judge_font(dims3, base, narrow3, gate)
        why = []
        if need4 and (tall["k"] != 4 or not near(tall["tracks"][0], ROOM_W * 4 + ROOM_CHROME)):
            why.append(f"×{tall['k']}・列 {tall['tracks']}（×4・754）")
        if need4 and not narrow3:
            why.append(f"空振り: 端末の最低が 560 のまま（widths {tall['widths']}）")
        if not near(tall["tracks"][2], DEFAULT_THIRD):
            why.append(f"第 3 列 {tall['tracks'][2]}（{DEFAULT_THIRD} のまま）")
        if not ok_f3:
            why.append(full_f3)
        check(not why, f"4 {tag}: ×{k0} → cpSplitTo(0, 760) で ×{tall['k']}（{round(tall['tracks'][0])}）・端末の列 "
              f"{round(tall['tracks'][1])}px{'（narrow）' if narrow3 else ''}・{why_f3}", " / ".join(why))
    page.evaluate("() => window.__office.debug.cpResetLayout()")

    # 1920×1080: ×2 → cpSplitTo(0, 760) で ×4（754）・端末 ≥ 40 桁
    page.set_viewport_size({"width": FULL[0], "height": FULL[1]})
    wait_layout(page, 300)
    page.evaluate("() => window.__office.debug.cpSplitTo(0, 400)")
    wait_layout(page, 200)
    k2 = page.evaluate("() => window.__office.debug.cpLayout().k")
    page.evaluate("() => window.__office.debug.cpSplitTo(0, 760)")
    wait_layout(page, 300)
    full = page.evaluate(LAYOUT_NOW)
    dims4 = term_settled(page)
    ok_f4, why_f4, full_f4 = judge_font(dims4, base, is_narrow(full["widths"]), gate)
    why = []
    if k2 != 2:
        why.append(f"空振り: cpSplitTo(0, 400) で ×{k2}（×2 のはず）")
    if full["k"] != 4 or not near(full["tracks"][0], ROOM_W * 4 + ROOM_CHROME):
        why.append(f"×{full['k']}・列 {full['tracks']}（×4・754）")
    if not ok_f4:
        why.append(full_f4)
    check(not why, f"4 1920×1080: ×2 → cpSplitTo(0, 760) で ×4（754）・端末 {why_f4}", " / ".join(why))

    # 境目 1 を本物の pointer で左へ → 名簿の列が 240〜520 で変わり、第 1 列は ±0 → dblclick で既定
    page.evaluate("() => window.__office.debug.cpResetLayout()")
    wait_layout(page, 300)
    col1 = '.main > .cpsplit[data-between="1"]'
    before = page.evaluate(LAYOUT_NOW)
    x2, y2 = handle_center(page, col1)
    page.mouse.move(x2, y2)
    page.mouse.down()
    page.mouse.move(x2 - 150, y2, steps=10)
    page.mouse.up()
    wait_layout(page, 300)
    wide = page.evaluate(LAYOUT_NOW)
    dims5 = term_settled(page)
    ok_f5, why_f5, full_f5 = judge_font(dims5, base, is_narrow(wide["widths"]), gate)
    x3, y3 = handle_center(page, col1)
    page.mouse.dblclick(x3, y3)
    wait_layout(page, 300)
    reset1 = page.evaluate(LAYOUT_NOW)
    why = []
    roster_w = wide["tracks"][2]
    if not (240 - TOL <= roster_w <= 520 + TOL) or near(roster_w, before["tracks"][2]):
        why.append(f"名簿の列 {before['tracks'][2]} → {roster_w}（240〜520 で変わるはず）")
    if not near(wide["tracks"][0], before["tracks"][0]):
        why.append(f"第 1 列 {before['tracks'][0]} → {wide['tracks'][0]}（±0）")
    if wide["widths"].get("roster") is None or not near(wide["widths"]["roster"], roster_w, 1):
        why.append(f"widths.roster {wide['widths']}（列の幅 {roster_w}）")
    if not ok_f5:
        why.append(full_f5)
    if not reset1["def"] or any(not near(a, b, 1) for a, b in zip(reset1["tracks"], DEFAULT_TRACKS[FULL])):
        why.append(f"dblclick の後 cpLayoutDefault {reset1['def']}・列 {reset1['tracks']}")
    check(not why, f"4 1920×1080: 境目 1 を左へ 150px → 名簿の列 {before['tracks'][2]:.0f} → {roster_w:.0f}px・第 1 列 ±0・"
          f"端末 {why_f5} → dblclick で既定", " / ".join(why))

    # 段の境目（2 窓の列＝部屋の上・受付の下）: cpRowSplitTo で部屋が 160k へスナップ（部屋の段の丈＝canvas の丈＝160k。
    # 窓の外形は見出しの丈が列の幅で変わる＝×3 の 570px で見出しが 2 段に折れる＝窓の丈の差は 160 の倍数にならない）
    office_h = before["win"]["office"]["h"]
    page.evaluate("(h) => window.__office.debug.cpRowSplitTo(h)", office_h - ROOM_H - 30)
    wait_layout(page, 300)
    row = page.evaluate(LAYOUT_NOW)
    stage_h = page.evaluate("() => document.querySelector('#stage').getBoundingClientRect().height")
    c = page.evaluate(ROOM_SCALE_NOW, [ROOM_W, ROOM_H])
    why = []
    if row["k"] != before["k"] - 1 or row["widths"].get("roomCap") != row["k"]:
        why.append(f"×{before['k']} → ×{row['k']}・widths {row['widths']}（1 段下の倍率へスナップして roomCap）")
    if c["attrH"] != ROOM_H * row["k"] or c["cssH"] != c["attrH"] or c["ragged"]:
        why.append(f"canvas の丈 {c['attrH']}（CSS {c['cssH']}・160×{row['k']}）・ragged {c['ragged']}")
    if not near(stage_h, ROOM_H * row["k"]):
        why.append(f"部屋の段（#stage）の丈 {stage_h}（160×{row['k']}）")
    check(not why, f"4 1920×1080: 段の境目 cpRowSplitTo({office_h - ROOM_H - 30:.0f}) で部屋が ×{before['k']} → ×{row['k']}"
          f"（canvas と部屋の段の丈 {c['attrH']}＝160k）", " / ".join(why))
    page.evaluate("() => window.__office.debug.cpResetLayout()")
    page.set_viewport_size({"width": WIDE[0], "height": WIDE[1]})
    wait_layout(page, 300)


# ── 5 保存 ／ 6 初期化 ／ 7 壊れた値 ───────────────────────────────────────────
def make_custom(page):
    """非既定の配置（入れ替え＋境目の幅）を probe で作り、その配置を返す"""
    page.evaluate("() => { const d = window.__office.debug; d.cpResetLayout(); d.cpSwap('office', 'terminal'); }")
    wait_layout(page, 200)
    page.evaluate("() => window.__office.debug.cpSplitTo(1, 600)")
    wait_layout(page, 300)
    return page.evaluate(LAYOUT_NOW)


def check_save(ctx, page, port, world, errors, check):
    made = make_custom(page)
    take_errors(page, errors, "live")
    page.reload(wait_until="domcontentloaded")
    ready(page, world)
    got = page.evaluate(LAYOUT_NOW)
    try:
        stored = json.loads(got["ls"] or "null")
    except ValueError:
        stored = None
    same = (got["cols"], got["widths"], got["rows"]) == (made["cols"], made["widths"], made["rows"])
    check(same and not got["def"] and got["applied"] and isinstance(stored, dict) and stored.get("v") == 1
          and stored.get("cols") == made["cols"] and got["ls"] == made["ls"],
          f"5 1440×900: 非既定（{made['cols']}・widths {made['widths']}）は page.reload() で戻る・鍵は JSON（v:1）",
          j({"made": [made["cols"], made["widths"], made["rows"]], "got": [got["cols"], got["widths"], got["rows"]],
             "ls": got["ls"], "applied": got["applied"]}))
    saved = got["ls"]

    # 同じ context の固定時刻のページ: 読まない（既定）・書かない（鍵は残る）
    fixed = ctx.new_page()
    fixed.on("pageerror", lambda e: errors.append(f"pageerror(?t=): {e}"))
    fixed.on("console", lambda m: errors.append(f"console.error(?t=): {m.text}") if m.type == "error" else None)
    fixed.goto(f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2", wait_until="networkidle")
    ready(fixed, world)
    f = fixed.evaluate(LAYOUT_NOW)
    take_errors(fixed, errors, "?t=")
    fixed.close()
    check(f["def"] and not f["applied"] and f["stored"] is None and f["ls"] == saved
          and not any(n.startswith("--cp-tpl-") for n in f["inline"]),
          "5 同じ context の ?t=3.2: 既定の配置で開き（golden の決定論）、保存した鍵は残っていて書き換わらない",
          j({"def": f["def"], "cols": f["cols"], "inline": f["inline"], "ls": f["ls"], "saved": saved}))

    # デモ: 読まない・書かない（probe で配置を替えても鍵は不変）
    demo = ctx.new_page()
    demo.on("pageerror", lambda e: errors.append(f"pageerror(demo): {e}"))
    demo.on("console", lambda m: errors.append(f"console.error(demo): {m.text}") if m.type == "error" else None)
    demo.goto(f"http://127.0.0.1:{port}/?ui=cockpit&demo=1", wait_until="domcontentloaded")
    demo.bring_to_front()
    ready(demo, None)
    d0 = demo.evaluate(LAYOUT_NOW)
    demo.evaluate("() => window.__office.debug.cpSwap('office', 'roster')")
    wait_layout(demo, 150)
    d1 = demo.evaluate(LAYOUT_NOW)
    take_errors(demo, errors, "demo")
    demo.close()
    page.bring_to_front()
    check(d0["def"] and d0["stored"] is None and d0["ls"] == saved and not d1["def"] and d1["ls"] == saved
          and all(b["tabindex"] is None for b in d0["bands"]),
          "5 同じ context の ?demo=1: 既定の配置で開き、配置を替えても鍵を書かない（帯にキーの入口も無い）",
          j({"open": [d0["def"], d0["ls"]], "after": [d1["cols"], d1["ls"]], "saved": saved, "bands": d0["bands"]}))


def check_reset(page, default_wide, check):
    """6: ⚙「配置」の「初期化」と cpResetLayout()"""
    if page.evaluate("() => window.__office.debug.cpLayoutDefault()"):
        make_custom(page)
    page.click("#btn-settings")
    page.wait_for_selector("#modalwrap:not([hidden])", timeout=5000)
    row = page.locator("#modal .mledform").filter(has=page.locator(".mledname", has_text=re.compile(r"^\s*(配置|Layout)\s*$")))
    n_rows = row.count()
    if n_rows == 1:
        row.locator("button", has_text=re.compile(r"初期化|Reset")).click()
    wait_layout(page, 300)
    page.click("#modalclose")
    wait_layout(page, 200)
    page.mouse.move(1, 1)
    m = page.evaluate(LAYOUT_NOW)
    off = {w: (m["win"][w], default_wide["win"][w]) for w in ("office", "reception", "terminal", "roster")
           if any(not near(m["win"][w][a], default_wide["win"][w][a]) for a in ("l", "t", "r", "b"))}
    check(n_rows == 1 and m["ls"] is None and m["def"] and not off and not any(n.startswith("--cp-tpl-") for n in m["inline"]),
          "6 ⚙「配置」の「初期化」→ 鍵 null・cpLayoutDefault・窓は既定の矩形・inline に --cp-tpl-* が無い",
          ("空振り: ⚙ に「配置」の行が無い / " if n_rows != 1 else "") + j({"ls": m["ls"], "def": m["def"], "off": off, "inline": m["inline"]}))
    make_custom(page)
    changed = page.evaluate("() => window.__office.debug.cpResetLayout()")
    again = page.evaluate("() => window.__office.debug.cpResetLayout()")
    wait_layout(page, 300)
    m = page.evaluate(LAYOUT_NOW)
    off = {w for w in ("office", "reception", "terminal", "roster")
           if any(not near(m["win"][w][a], default_wide["win"][w][a]) for a in ("l", "t", "r", "b"))}
    check(changed is True and again is False and m["ls"] is None and m["def"] and not off,
          "6 cpResetLayout() も同じ（変わったら true・既定のままなら false）→ 鍵 null・既定の矩形",
          j({"changed": changed, "again": again, "ls": m["ls"], "def": m["def"], "off": sorted(off)}))


def check_broken(page, world, errors, check):
    for name, raw in BROKEN:
        take_errors(page, errors, "live")
        page.evaluate("([k, v]) => localStorage.setItem(k, v)", [LAYOUT_KEY, raw])
        page.reload(wait_until="domcontentloaded")
        ready(page, world)
        m = page.evaluate(LAYOUT_NOW)
        check(m["def"] and m["ls"] is None and not m["applied"],
              f"7 壊れた保存（{name}）で開く → 既定の配置・鍵を消す", j({"def": m["def"], "ls": m["ls"], "cols": m["cols"]}))


# ── 8 キー／メニュー ─────────────────────────────────────────────────────────
def check_keys(page, check):
    page.evaluate("() => window.__office.debug.cpResetLayout()")
    wait_layout(page)
    want = page.evaluate(NUDGE_WANT, ["office", "right"])
    page.focus("#sec-office")
    page.keyboard.press("Alt+ArrowRight")
    wait_layout(page, 200)
    by_key = page.evaluate(LAYOUT_NOW)
    focus_kept = page.evaluate("() => document.activeElement?.id")
    page.evaluate("() => window.__office.debug.cpResetLayout()")
    page.evaluate("() => window.__office.debug.cpNudge('office', 'right')")
    by_probe = page.evaluate("() => window.__office.debug.cpLayout().cols")
    check(want["cols"] != [["office", "reception"], ["terminal"], ["roster"]] and by_key["cols"] == want["cols"] == by_probe,
          f"8 帯（#sec-office）で Alt+ArrowRight → {by_key['cols']}＝nudgeWindow（純関数）＝cpNudge",
          j({"key": by_key["cols"], "pure": want["cols"], "probe": by_probe, "focus": focus_kept}))

    # Enter でメニュー（端の向きは disabled）→ Escape で閉じて帯へ。名簿（右端・1 窓の列）で見る
    page.evaluate("() => window.__office.debug.cpResetLayout()")
    wait_layout(page)
    still = page.evaluate(NUDGE_WANT, ["roster", "left"])["still"]
    page.focus("#sec-roster")
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    menu = page.evaluate(MENU_NOW)
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    closed = page.evaluate(MENU_NOW)
    dis = {it["dir"]: it["disabled"] for it in menu["items"]}
    mismatch = {d: (dis.get(d), s) for d, s in still.items() if dis.get(d) != s}
    check(menu["shown"] and menu["inMenu"] and not mismatch and dis.get("reset") is False and any(still.values())
          and not closed["shown"] and closed["focus"] == "sec-roster",
          f"8 名簿の帯で Enter → #cp-move-menu（無変化の向き {sorted(d for d, s in still.items() if s)} が disabled）→ "
          "Escape で閉じて帯にフォーカスが戻る",
          j({"menu": menu, "still": still, "mismatch": mismatch, "closed": closed}))

    # 右クリックでも開く
    x, y = handle_center(page, "#sec-office")
    page.mouse.click(x, y, button="right")
    page.wait_for_timeout(150)
    rc = page.evaluate(MENU_NOW)
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    check(rc["shown"], "8 帯の右クリックでも #cp-move-menu が開く", j(rc))

    # 指示欄に打っている間の Alt+矢印は配置を変えない
    page.locator("#agents .pxrow").first.click()
    try:
        page.wait_for_selector("#composeinput", state="visible", timeout=4000)
        has_input = True
    except Exception:  # noqa: BLE001
        has_input = False
    before = page.evaluate("() => window.__office.debug.cpLayout().cols")
    if has_input:
        page.focus("#composeinput")
        page.keyboard.press("Alt+ArrowRight")
        page.keyboard.press("Alt+ArrowDown")
        page.wait_for_timeout(150)
    after = page.evaluate("() => window.__office.debug.cpLayout().cols")
    check(has_input and before == after, "8 #composeinput にフォーカスがある間の Alt+ArrowRight／Alt+ArrowDown は配置を変えない",
          ("空振り: 指示欄の入力欄が出ない / " if not has_input else "") + j({"before": before, "after": after}))
    page.evaluate("() => { const e = document.querySelector('#composeinput'); if (e) e.value = ''; document.activeElement?.blur(); }")
    page.keyboard.press("Escape")
    wait_layout(page, 150)


# ── 9 2 カラム／1 列 ／ 10 眺めるモード ──────────────────────────────────────
def check_shapes(page, check):
    made = make_custom(page)
    for vw, vh in ((1280, 800), (390, 844)):
        page.set_viewport_size({"width": vw, "height": vh})
        settle(page, 300)
        m = page.evaluate(LAYOUT_NOW)
        tpl = [n for n in m["inline"] if n.startswith("--cp-tpl-") or n.startswith("--cp-area-")]
        shown = [h for h in m["cpsplit"] if h["display"] != "none"]
        tab = [b["win"] for b in m["bands"] if b["tabindex"] is not None]
        check(not m["applied"] and not tpl and not shown and not tab and len(m["tracks"]) in (1, 2) and m["ls"] == made["ls"],
              f"9 {vw}×{vh}（{len(m['tracks'])} 列）: 保存した非既定の配置を適用しない（applied false・inline の升目なし・取っ手 display:none・"
              "帯に tabindex なし・鍵は残る）",
              j({"applied": m["applied"], "tpl": tpl, "shown": shown, "tabindex": tab, "tracks": m["trackStr"], "ls": m["ls"]}))
    page.set_viewport_size({"width": WIDE[0], "height": WIDE[1]})
    settle(page, 300)
    m = page.evaluate(LAYOUT_NOW)
    tab = [b["win"] for b in m["bands"] if b["tabindex"] == "0"]
    check(m["applied"] and m["cols"] == made["cols"] and "--cp-tpl-cols" in m["inline"] and len(tab) == 4
          and all(near(a, b, 1) for a, b in zip(m["tracks"], made["tracks"])),
          f"9 1440×900 へ戻すと再適用（{m['cols']}・列 {m['trackStr']}・帯 4 本に tabindex=0）",
          j({"applied": m["applied"], "cols": m["cols"], "tracks": [m["tracks"], made["tracks"]], "tabindex": tab}))

    # 10 眺めるモード: 非既定のまま F → 端末の列の幅は変わらない → Esc で戻る
    page.evaluate("() => document.activeElement?.blur()")
    w0 = page.evaluate("() => document.querySelector('#cp-center').getBoundingClientRect().width")
    k0 = page.evaluate("() => window.__office.stats().scale")
    page.keyboard.press("f")
    try:
        page.wait_for_function("(k) => window.__office.stats().scale > k", arg=k0, timeout=8000)
        watched = True
    except Exception:  # noqa: BLE001
        watched = False
    page.wait_for_timeout(200)
    w1 = page.evaluate("""() => ({ w: document.querySelector('#cp-center').getBoundingClientRect().width,
      watch: document.querySelector('.hud.ui-cockpit').classList.contains('cp-watch'), scale: window.__office.stats().scale,
      applied: window.__office.debug.cpLayout().applied })""")
    page.keyboard.press("Escape")
    try:
        page.wait_for_function("(k) => window.__office.stats().scale === k", arg=k0, timeout=8000)
    except Exception:  # noqa: BLE001 — 戻らないことは下で赤
        pass
    wait_layout(page, 200)
    w2 = page.evaluate("""() => ({ w: document.querySelector('#cp-center').getBoundingClientRect().width,
      watch: document.querySelector('.hud.ui-cockpit').classList.contains('cp-watch'), scale: window.__office.stats().scale,
      applied: window.__office.debug.cpLayout().applied, cols: window.__office.debug.cpLayout().cols })""")
    check(watched and w1["watch"] and near(w1["w"], w0) and not w1["applied"]
          and not w2["watch"] and w2["scale"] == k0 and near(w2["w"], w0) and w2["applied"] and w2["cols"] == made["cols"],
          f"10 非既定の配置で F（眺める・×{w1['scale']}）→ 端末の列の幅 {w0:.0f}px のまま → Esc で ×{k0} と同じ配置へ戻る",
          ("空振り: F で眺めるモードに入らない / " if not watched else "") + j({"before": w0, "watch": w1, "back": w2}))
    page.evaluate("() => window.__office.debug.cpResetLayout()")
    wait_layout(page)


def main():
    from playwright.sync_api import sync_playwright

    world = json.loads(WORLD.read_text(encoding="utf-8"))
    body = json.dumps(world, ensure_ascii=False)
    ng, errors = 0, []

    def check(ok, label, detail=""):
        nonlocal ng
        print(f"  {'✓' if ok else '✗'} {label}{'' if ok else f' — {detail}'}")
        if not ok:
            ng += 1

    pending = []
    # narrow の字（FONT）の保留: term.js が narrow を読むまでは数えるだけ（judge_font）
    gate = {"readsNarrow": True, "pend": lambda why: (pending.append(why), print(f"  － 保留: {why}"))}

    t0 = time.time()
    port = free_port()
    proc = start_server(port)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=SWIFTSHADER)
            # 1（固定時刻のページ＝golden の形）: r110 の open_page（自分の context・空の localStorage）
            fctx, fpage = open_page(browser, port, "dark", world, errors, "?t= 既定")
            try:
                for shape in (FULL, WIDE):
                    fpage.set_viewport_size({"width": shape[0], "height": shape[1]})
                    settle(fpage, 300)
                    check_default(fpage, shape, check, tag="?t=3.2 ")
            finally:
                fctx.close()

            ctx = browser.new_context(viewport={"width": WIDE[0], "height": WIDE[1]}, device_scale_factor=1)
            term_routes(ctx)
            ctx.route("**/api/office*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=body))
            ctx.add_init_script(ERR_INIT)
            ctx.add_init_script(TERMINAL_PREF)
            page = open_live(ctx, port, world, errors, "live")
            try:
                got_term = wait_term(page)
                check(got_term, "⓪ 偽の端末（/api/term/list・/api/term/stream）が描かれる（端末の字と桁を測る前提）",
                      "空振り: termDims() が null")
                gate["readsNarrow"] = page.evaluate(TERM_READS_NARROW)
                if not gate["readsNarrow"]:
                    print("  － ui/cockpit/term.js が initTerm の narrow をまだ読まない（A2 は index.js から渡すだけ・term.js は B2 の持ち分）"
                          "＝narrow の配置で字を下げているのは保留として数える（term.js が narrow を読んだ瞬間に赤へ変わる）")
                # 1 既定＝不変（live）
                default_wide = check_default(page, WIDE, check)
                audit_seams(page, "1440×900 既定", check)
                page.set_viewport_size({"width": FULL[0], "height": FULL[1]})
                settle(page, 300)
                check_default(page, FULL, check)
                audit_seams(page, "1920×1080 既定", check)
                page.set_viewport_size({"width": WIDE[0], "height": WIDE[1]})
                settle(page, 300)
                # 2 入れ替え（本物の pointer）
                check_swap(page, check)
                page.evaluate("() => window.__office.debug.cpResetLayout()")
                wait_layout(page)
                # 3 配置の掃引（2 形）
                for shape in (WIDE, FULL):
                    page.set_viewport_size({"width": shape[0], "height": shape[1]})
                    settle(page, 300)
                    check_sweep(page, shape, check)
                # 4 境目
                check_splits(page, check, gate)
                # 8 キー／メニュー
                check_keys(page, check)
                # 9 2 カラム／1 列 ／ 10 眺めるモード
                check_shapes(page, check)
                # 5 保存（再読込・固定時刻・デモ）／ 6 初期化 ／ 7 壊れた値
                check_save(ctx, page, port, world, errors, check)
                check_reset(page, default_wide, check)
                check_broken(page, world, errors, check)
                take_errors(page, errors, "live")
            finally:
                ctx.close()
            check(not errors, "⓪ console.error / pageerror / window の error（ResizeObserver loop を含む）/ unhandledrejection = 0",
                  "; ".join(errors[:4]))
            browser.close()
    finally:
        proc.terminate()

    took = time.time() - t0
    print(f"  － 所要 {took:.0f} 秒")
    tail = f"（保留 {len(pending)}: narrow の配置で端末の字を下げている＝term.js が narrow を読んでいない）" if pending else ""
    print(f"✅ R111 コックピットの配置 green{tail}" if not ng else f"❌ R111 コックピットの配置 {ng} 件{tail}")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
