#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R110（コックピット v3.1「読める・分かる・気配がある」）の見た目の門。

本人の指摘（2026-10-06）「文字が見切れている・境目が分かりづらい・カーソルを合わせないとどのボタンか分からない」を、
計算値と実描画で機械に見させる（計画 scalable-cooking-bentley の S3・決めたこと 1〜5）。

起動・fixture・route の書き方は tests/cockpit_r106_ui_smoke.py を写す（start_server・sync_playwright・
`window.__office.inject(w)`・固定時刻 `?ui=cockpit&t=3.2&seed=11`）。端末（xterm）は通信だけ偽装する
（/api/term/list・/api/term/stream＝tests/cockpit_shapes_smoke.py と同じ偽の端末・本物の pty を起こさない）。
world は固定ワールド tests/fixtures/world/cockpit.json（最近の指示の先頭に長い本文を 1 件足す＝受付に余裕がある形で
最近の指示の clamp を空振りさせない）と、basic.json の名前・質問・選択肢・いま・最近の指示を長くした写し
（tests/cockpit_smoke.py の 29 と同じ with_fields の型）。形は 1920×1080（本人の画面・×4）／1440×900（golden の形・×3）／
1280×800（2 カラム）を dark と light（?theme=light）で。golden は撮らない（見た目を変えない門）。

検査:
  1 見切れ 0（両方の world・全形・両テーマ・文字の大きさ 16 でも）:
    (a) text-overflow: ellipsis で中身が箱に入りきらない（scrollWidth > clientWidth + 1）要素が 0。
    (b) 文字（要素の直下の文字の Range の矩形）が、overflow が hidden/clip の祖先（自分を含む・包含ブロックの鎖をたどる）の
        内寸から 1px 以上はみ出さない。auto/scroll の祖先は「人がスクロールで届く」＝見えている部分に切り詰めてから上へ進む
        （スクロールの箱そのものが hidden の祖先に切られていれば、その先の文字は届かない＝赤）。画面の外も（ページが
        スクロールしない段では）はみ出しに数える。見るのは「一部が見えて一部が切れた字」＝丸ごと箱の外へ押し出された字
        （受付の最近の指示が ❗ に譲った段など）は見切れに数えず、数だけ出す（押せるかは 5 が見る）。
    例外は名指しだけ: 端末タブの名前（.cptab .nm・20em で切る唯一の意図した省略＝title に全文が在ること）・.cpcounts・
    .freshness・足元の留守中の帯（#cp-login-slot .digest-strip）。行の clamp は名簿のいま（.pxrow .c-detail）・最近の指示の
    本文（.hrow > span）・受付の答えのボタン（#attn.tray > button.kbd・2 行まで＝R110-D2）だけで、切れているなら全文が
    title（自分・clamp の箱・その行）に在ることを同時に見る。
  2 全ボタンに枠か地（ホバー無し）: 見えている button と [role=button] の計算値で、どれかの辺の border-width > 0 かつ style ≠ none
    かつ色の α > 0、か background-color の α > 0。例外は名指し: .pxsort（列見出し）・.cptab（自分の罫）。
    文字の先頭も ::before も「[ 」で始まらない（R110 で外した括弧の飾りを戻さない）。
  3 文字の大きさ: root の 4 token（--cp-font-small/base/title/display）を読み、本文（.pname・名簿の「いま」.dtext・.trayhead・
    .trayline・.cpq-text・.dlgmsg・.cpwork-grid .wv・最近の指示の本文）の最小 ≥ base、全部の文字要素 ≥ small。
    名簿の「いま」は R110-D7 の裁定で本文（本人が一番読む文）＝旧 3'（S1 の small と S3 の門の食い違い）はここへ統合した。
    端末（.xterm-rows）は base か、80 桁が入らないときだけ max(11, base − 2)（term.js の掟・base に換算して 82 桁未満のときだけ下げてよい）。
    localStorage aioffice.cockpit.font=16 で開き直して、data-cp-font・token・最小が 16/13 に追従する（1 も同じ形で見直す）。
  4 境目: 隣り合う窓（.cpwin・矩形の間が 0〜24px で向かい合う組）が 3 組以上あり、どの組も間に地の溝（4px 以上）があって、
    両方の窓の枠の色が地（窓の外の背景）と窓の地のどちらとも明度の比 ≥ 1.8。題名の帯（.cpsec）の地と窓の地の比 ≥ 1.15（両テーマ）。
  5 受付: #attn は 04 受付（#win-reception）の子。❗ を J で 1 件ずつ巡回し、どの ❗ でもトレイの全ボタンの中心が
    elementFromPoint でそのボタンに当たり、見えている矩形（祖先と画面で切った矩形）の内にある（3 形＋1440×640・長文の ❗ でも）。
    R110-D2: 受付が低い形ではトレイ全体が中でスクロールする（ボタンの列もスクロールで届く）＝ボタンごとに、人が動かせる箱
    （overflow が auto/scroll）だけを動かして見えている所へ寄せてから測る。scrollIntoView は使わない（overflow:hidden の祖先まで
    動かす＝人ができない操作）。寄せた後に overflow:hidden の祖先が動いていないことも見る。ボタンは中身の丈（grid の伸ばしで
    90〜146px に伸びない＝丈 ≤ 2 行＋余白）。
  6 名札（別の担当が部屋の名札の層 #cpnames を入れたら）: 部屋が ×3 以上で、部屋に居る全員ぶんの札が在り、どれも部屋の canvas の
    矩形の内・互いに重ならない。#cpnames が無ければ「skip」と出す（黙って緑にしない・最後の行にも skip の数を出す）。
  7 2 カラムの名簿（R110-D1）: 1280×800・1366×768・1024×768 で、名簿（#cp-roster）の行が 2 行以上まるごと見える（名簿の箱と
    画面で切った矩形の内に行の矩形が丸ごと在る）。部屋は ×2（譲らない）。❗ のある世界（cockpit.json）では受付の窓が 140px 以上、
    ❗ の無い世界では受付の窓が 140px 以下で INBOX ZERO（#cp-inbox-zero）が丸ごと見える＝❗ が無い間は名簿へ回す。
  8 ❗ の無いときの ⤢（R110-D5）: ❗ の無い世界で名簿の行を押して指示欄を開き、⤢ で広げてもトレイ（#attn）は出ない
    （display:none）。[hidden] の則に ⤢ の則（:has(#sheet.wide)）が勝つと、空のトレイが出た（1 列で指示欄が 443px ずれた）。
  ⓪ console.error / pageerror = 0

使い方: python3 tests/cockpit_r110_ui_smoke.py   （verify.sh ▶7 の run_ui から呼ぶ・Playwright 必要）
"""
import base64
import copy
import json
import pathlib
import sys
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, free_port, start_server  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "world"
SHAPES = [(1920, 1080), (1440, 900), (1280, 800)]
RECEPTION_SHAPES = SHAPES + [(1440, 640)]     # 5 は低い 3 カラム（部屋は ×2・受付は最低 200px）でも
THEMES = ("dark", "light")
FONT_BIG = "16"                               # ⚙ 文字の大きさの最大の段（base 16・small 13）
EDGE_TOL = 1.0                                # 1 の (b)・5 の「内」の許し（CSS px・丸め）
RATIO_LINE = 1.8                              # 4: 窓の枠 × 地（外の地・窓の地）
RATIO_SEC = 1.15                              # 4: 題名の帯 × 窓の地
GROOVE_MIN = 4                                # 4: 隣り合う窓の間の地の溝（決めたこと 3 の .main の gap 8px）
PAIR_GAP_MAX = 24                             # 4: 「隣り合う」とみなす窓の間の上限
PAIRS_MIN = 3                                 # 4: 空振りの番人（3 カラムは 4 組・2 カラムは 5 組）
TERM_REFIT = 82                               # 3: term.js の「下げた後は 82 桁入るまで戻さない」
ROSTER_SHAPES = [(1280, 800), (1366, 768), (1024, 768)]   # 7: 2 カラムの名簿（R110-D1・本人の検収の 3 形）
ROSTER_ROWS_MIN = 2                           # 7: まるごと見える名簿の行の下限
RECEPTION_FLOOR = 140                         # 7: ❗ のある間の受付の窓の下限（❗ の無い間はこれが上限）
WIDE_SHAPES = [(1280, 800), (820, 1000)]      # 8: 2 カラムと 1 列（1 列で指示欄が 443px ずれた）

# 偽の端末（tests/cockpit_shapes_smoke.py と同じ形・sess-cp-09＝固定ワールドで最初に選ばれる ❗ の人）
TERM_INFO = {"id": "5a0e5a0e5a0e5a0e", "kind": "claude", "session": "sess-cp-09", "projectId": "2ffc7d9c6254",
             "cols": 100, "rows": 30, "alive": True, "exitCode": None, "startedAt": 1790199000, "viewers": 0}
TERM_OUT = "r110 ui smoke\r\n$ "

LONG_WORD = "/Users/test/realmeetingmemo/src/storage/adapters/sharedFolderAdapterConfiguration.json"
LONG_NOW = "共有フォルダの権限を全部見直して通知の設定を揃え直し、古い保存先から新しい保存先へ設定ファイルを移し替える作業"
LONG_HIST = ("保存先を共有フォルダへ移して、権限と通知の設定を全部見直してから、古い設定ファイルを消して結果を報告して。"
             "途中で迷ったら手を止めて聞いて")


def copy_world(w):
    return copy.deepcopy(w)


def with_fields(world, session, **fields):
    """session の状態を書き換えた world の写し（roster の代表・要約・employees の 3 か所＝tests/cockpit_smoke.py と同じ型）"""
    w = copy_world(world)
    for r in [*w["roster"], *w.get("employees", [])]:
        if r.get("session") == session:
            r.update(fields)
        for brief in r.get("sessions") or []:
            if isinstance(brief, dict) and brief.get("session") == session:
                brief.update({k: v for k, v in fields.items() if k in ("state", "age", "attention", "minions")})
    return w


def without_attention(world):
    """❗ の居ない写し（受付は INBOX ZERO・トレイは hidden）"""
    w = copy_world(world)
    for r in [*w["roster"], *w.get("employees", [])]:
        if r.get("attention"):
            r.update(attention=False, ask=None, question="", stuckTool="", approvalMin=0, questionOptions=[])
        for brief in r.get("sessions") or []:
            if isinstance(brief, dict) and brief.get("attention"):
                brief["attention"] = False
    return w


def with_long_history(world):
    """最近の指示の先頭に長い本文を 1 件足した写し（受付に余裕がある固定ワールドで、最近の指示の clamp を空振りさせない）"""
    w = copy_world(world)
    first = (w.get("roster") or [{}])[0]
    w["history"] = [{"session": first.get("session", ""), "disp": first.get("disp", ""), "text": LONG_HIST,
                     "ts": (w.get("generatedAt") or 1790200000) - 120, "pending": False}, *(w.get("history") or [])]
    return w


def long_world(basic):
    """basic の名前・質問・選択肢を長くし（cockpit_smoke 29 の「basic 長文」と同じ）、別の人のいま（work.now）と
    最近の指示の本文も長くした写し＝見切れ・clamp・受付の押せる・本文の字の大きさを空振りさせない"""
    w = with_fields(basic, "sess-fixture-0006", disp="議事録アプリ（" + LONG_WORD + "）",
                    question="この設計で確定していいですか？ 保存先は " + LONG_WORD + " にまとめ、"
                    + "古い設定は移し替えてから消します。" * 40,
                    questionOptions=[
                        {"label": "案A で進める（" + LONG_WORD + " を正本にする）", "description": ""},
                        {"label": "案B: 保存先をローカルと共有フォルダの両方に分けて、同期は夜間にまとめて行う",
                         "description": ""},
                        {"label": "もう少し考えてから決めるので、いったん止めて今の状態を報告して", "description": ""}])
    w = with_fields(w, "sess-fixture-0002", state="working", kind="tool",
                    work={"now": [LONG_NOW], "next": [], "done": [], "counts": {}})
    hist = list(w.get("history") or [])
    hist.insert(0, {"session": "sess-fixture-0002", "disp": "制作本部(works)", "text": LONG_HIST,
                    "ts": (w.get("generatedAt") or 1753800000) - 120, "pending": False})
    w["history"] = hist
    return w


def term_routes(ctx):
    """端末の一覧と流れだけを偽装する（本物の pty を起こさない・ほかの /api はテストサーバー）。
    端末への GET 以外（xterm の fit が送る /api/term/resize など）は記録もせず {ok:true}＝テストサーバーの偽の端末 id に
    届けて 404 を出さない（どこにも書かない）"""
    term_list = json.dumps({"terms": [TERM_INFO]})
    hello = json.dumps({**TERM_INFO, "from": 0, "reset": True}, separators=(",", ":"))
    out = base64.b64encode(TERM_OUT.encode()).decode()
    stream = f"event: hello\ndata: {hello}\n\nevent: out\ndata: {out}\n\n"
    # R111-B2: 端末の購読は多重化（/api/term/streams・out は「<id> <base64>」＝JSON でない）に移った
    mux = f"event: hello\ndata: {hello}\n\nevent: out\ndata: {TERM_INFO['id']} {out}\n\n"

    def handler(route):
        path = urlparse(route.request.url).path
        if route.request.method != "GET":
            return route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        if path == "/api/term/list":
            return route.fulfill(status=200, content_type="application/json; charset=utf-8", body=term_list)
        if path == "/api/term/stream":
            return route.fulfill(status=200, content_type="text/event-stream", body=stream)
        if path == "/api/term/streams":
            return route.fulfill(status=200, content_type="text/event-stream", body=mux)
        return route.continue_()

    ctx.route(lambda url: urlparse(url).path.startswith("/api/term/"), handler)


# ── ページの中で測る（1 本の道具箱）──────────────────────────────────
KIT = r"""
  const R = (v) => Math.round(v * 10) / 10;
  const who = (e) => !e ? '?' : e.id ? '#' + e.id
    : e.tagName.toLowerCase() + (e.classList && e.classList.length ? '.' + [...e.classList].slice(0, 3).join('.') : '');
  const path = (e) => { const out = []; for (let p = e; p && out.length < 3 && !p.classList?.contains('ui-cockpit'); p = p.parentElement) out.unshift(who(p)); return out.join(' > '); };
  const shown = (e) => {
    if (!e || e.closest('[hidden]')) return false;
    const cs = getComputedStyle(e);
    if (cs.display === 'none' || cs.visibility !== 'visible') return false;
    if (cs.clip && cs.clip !== 'auto') return false;                     // 読み上げ専用（rect(0 0 0 0)）
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const ownText = (e) => [...e.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim());
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  // 直下の文字の実際の矩形（Range）。ellipsis や clamp の向こうに組まれた字も含む＝はみ出しを測れる
  const textRect = (e) => {
    let L = Infinity, T = Infinity, Rt = -Infinity, B = -Infinity;
    for (const n of ownText(e)) {
      const rg = document.createRange(); rg.selectNodeContents(n);
      for (const q of rg.getClientRects()) {
        if (q.width <= 0 && q.height <= 0) continue;
        L = Math.min(L, q.left); T = Math.min(T, q.top); Rt = Math.max(Rt, q.right); B = Math.max(B, q.bottom);
      }
    }
    return L === Infinity ? null : { left: L, top: T, right: Rt, bottom: B };
  };
  const makesCb = (cs) => cs.transform !== 'none' || cs.filter !== 'none' || (cs.backdropFilter || 'none') !== 'none'
    || /(transform|filter)/.test(cs.willChange) || /(paint|layout|strict|content)/.test(cs.contain);
  // 祖先の鎖（自分を含む・包含ブロックをたどる）。each(p, cs, self) を呼ぶ。absolute/fixed の子は包含ブロックまでの祖先に切られない
  const walk = (e, each) => {
    let need = 'static';
    for (let p = e; p && p !== document.documentElement; p = p.parentElement) {
      const cs = getComputedStyle(p);
      const self = p === e;
      if (!self) {
        if (cs.display === 'contents') continue;
        const cb = makesCb(cs);
        if (need === 'fixed' && !cb) continue;
        if (need === 'absolute' && cs.position === 'static' && !cb) continue;
      }
      if (each(p, cs, self) === false) return;
      need = cs.position === 'absolute' || cs.position === 'fixed' ? cs.position : 'static';
    }
  };
  const inner = (p) => { const q = p.getBoundingClientRect(); const x0 = q.left + p.clientLeft, y0 = q.top + p.clientTop;
    return { left: x0, top: y0, right: x0 + p.clientWidth, bottom: y0 + p.clientHeight }; };
  // 見えている矩形（祖先の内寸と画面で切る）
  const visRect = (e) => {
    const r = e.getBoundingClientRect();
    let v = { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
    const rootVisible = (() => { const s = getComputedStyle(document.documentElement); return s.overflowX === 'visible' && s.overflowY === 'visible'; })();
    walk(e, (p, cs, self) => {
      if (self || cs.display === 'inline') return;
      if (p === document.body && rootVisible) return;
      const k = inner(p);
      if (cs.overflowX !== 'visible') { v.left = Math.max(v.left, k.left); v.right = Math.min(v.right, k.right); }
      if (cs.overflowY !== 'visible') { v.top = Math.max(v.top, k.top); v.bottom = Math.min(v.bottom, k.bottom); }
    });
    v = { left: Math.max(v.left, 0), top: Math.max(v.top, 0), right: Math.min(v.right, innerWidth), bottom: Math.min(v.bottom, innerHeight) };
    return { ...v, right: Math.max(v.left, v.right), bottom: Math.max(v.top, v.bottom) };
  };
  const rgba = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c || '');
    if (!m) return c === 'transparent' ? [0, 0, 0, 0] : null;
    const v = m[1].split(/[ ,\/]+/).filter(Boolean).map(parseFloat);
    return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1];
  };
  const lum = (c) => { const v = rgba(c); if (!v) return null;
    const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(v[0]) + 0.7152 * f(v[1]) + 0.0722 * f(v[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); if (x == null || y == null) return 0;
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const ground = (e) => { for (let p = e.parentElement; p; p = p.parentElement) {
      const c = getComputedStyle(p).backgroundColor, v = rgba(c); if (v && v[3] > 0) return c; }
    return getComputedStyle(document.body).backgroundColor; };
  const root = document.querySelector('.hud.ui-cockpit');
"""

# 1 見切れ（(a) ellipsis・(b) hidden/clip の祖先からのはみ出し）
CLIP_AUDIT = "(named) => {" + KIT + r"""
  const isNamed = (e) => !!e && named.some((s) => e.matches(s) || !!e.closest(s));
  const CLAMP_OK = '.pxrow .c-detail, .hrow > span, #attn.tray > button.kbd';
  const fullElsewhere = (e, box) => {
    const t = norm(e.textContent);
    const row = box.closest('.pxrow, .hrow');
    return !!t && [e.title, box.title, row?.title, row?.getAttribute('aria-label')].some((x) => norm(x).includes(t));
  };
  const pageScrolls = document.documentElement.scrollHeight > innerHeight + 1
    && getComputedStyle(document.documentElement).overflowY !== 'hidden' && getComputedStyle(document.body).overflowY !== 'hidden';
  const all = [root, ...root.querySelectorAll('*')].filter(shown);
  const ell = [], cut = [], clamps = [], hidden = [];
  for (const e of all) {
    const cs = getComputedStyle(e);
    // (a) 省略記号で切っている
    if (cs.textOverflow === 'ellipsis' && cs.overflowX !== 'visible' && cs.display !== 'inline'
        && e.scrollWidth > e.clientWidth + 1) {
      if (e.matches('.cptab .nm')) {
        if (norm(e.title) !== norm(e.textContent)) ell.push({ who: path(e), why: 'title に全文が無い', text: norm(e.textContent).slice(0, 40) });
      } else if (!isNamed(e)) {
        ell.push({ who: path(e), sw: e.scrollWidth, cw: e.clientWidth, text: norm(e.textContent).slice(0, 40) });
      }
    }
    // (b) 直下の文字が hidden/clip の祖先からはみ出す
    if (!ownText(e).length || isNamed(e)) continue;
    const t0 = textRect(e);
    if (!t0) continue;
    let box = { ...t0 };
    let bad = null, gone = null;
    walk(e, (p, pcs, self) => {
      if (pcs.display === 'inline') return;
      const k = inner(p);
      for (const [ax, lo, hi] of [['X', 'left', 'right'], ['Y', 'top', 'bottom']]) {
        const ov = ax === 'X' ? pcs.overflowX : pcs.overflowY;
        if (ov === 'visible') continue;
        if (ov === 'auto' || ov === 'scroll') {            // 人がスクロールで届く＝見えている部分に切り詰めて上へ
          box[lo] = Math.max(box[lo], k[lo]); box[hi] = Math.min(box[hi], k[hi]);
          continue;
        }
        if (box[hi] <= k[lo] || box[lo] >= k[hi]) {         // 丸ごと箱の外＝切れた字ではなく隠れた字（窓が譲った段・押せるかは 5）
          gone = path(p);
          break;
        }
        const over = Math.max(k[lo] - box[lo], box[hi] - k[hi]);
        if (over > 1) {
          const clampBox = p.matches(CLAMP_OK) && getComputedStyle(p).webkitLineClamp !== 'none' ? p : null;
          if (clampBox) {
            clamps.push({ who: path(clampBox), ok: fullElsewhere(e, clampBox) });
            if (!fullElsewhere(e, clampBox)) bad = { by: path(p), axis: ax, px: R(over), why: 'clamp で切れて全文が title に無い' };
          } else if (!isNamed(p)) {
            bad = { by: path(p), axis: ax, px: R(over), ov };
          }
          box[lo] = Math.max(box[lo], k[lo]); box[hi] = Math.min(box[hi], k[hi]);
        }
      }
      // 赤を見つけた・丸ごと隠れた・全部スクロールの先（届く）＝それより上は見ない
      if (bad || gone || box.left >= box.right || box.top >= box.bottom) return false;
    });
    if (gone) { hidden.push({ who: path(e), by: gone }); continue; }
    if (!bad && !pageScrolls && box.left < box.right && box.top < box.bottom) {
      const over = Math.max(-box.left, box.right - innerWidth, -box.top, box.bottom - innerHeight);
      const outside = box.right <= 0 || box.left >= innerWidth || box.bottom <= 0 || box.top >= innerHeight;
      if (over > 1 && !outside) bad = { by: '画面', px: R(over) };
    }
    if (bad) cut.push({ who: path(e), text: norm(e.textContent).slice(0, 40), ...bad });
  }
  return { looked: all.length, ell, cut, clamps, hidden: hidden.length };
}"""

# 2 ボタンの枠と地・括弧の飾り
BUTTONS = "() => {" + KIT + r"""
  const btns = [...root.querySelectorAll('button, [role=button]')].filter(shown);
  const bad = [], brackets = [];
  let looked = 0;
  for (const b of btns) {
    if (!b.matches('.pxsort, .cptab')) {
      looked += 1;
      const cs = getComputedStyle(b);
      const edge = ['Top', 'Right', 'Bottom', 'Left'].some((s) => parseFloat(cs[`border${s}Width`]) > 0
        && !['none', 'hidden'].includes(cs[`border${s}Style`]) && (rgba(cs[`border${s}Color`]) || [0, 0, 0, 1])[3] > 0);
      const fill = (rgba(cs.backgroundColor) || [0, 0, 0, 0])[3] > 0;
      if (!edge && !fill) bad.push({ who: path(b), text: norm(b.textContent).slice(0, 24), border: cs.borderTopWidth + ' ' + cs.borderTopStyle, bg: cs.backgroundColor });
    }
    const before = getComputedStyle(b, '::before').content || '';
    if (/^\[\s/.test(norm(b.textContent)) || /^"\[/.test(before)) brackets.push({ who: path(b), text: norm(b.textContent).slice(0, 24), before });
  }
  return { n: btns.length, looked, bad, brackets };
}"""

# 3 文字の大きさ。本文＝読む文（名前・名簿の「いま」・❗ の見出しと本文・中央の問い・会話・いまの仕事・最近の指示の本文）。
# 名簿の「いま」（.pxrow .dtext）は R110-D7 の裁定で本文（旧 3' を統合）
BODY_SEL = (".pname, .pxrow .dtext, .trayhead, .trayline, .cpq-text, .dlgmsg, .cpwork-grid .wv, "
            ".cphist .hrow > span:not(.hp)")
FONTS = "(bodySel) => {" + KIT + r"""
  const rs = getComputedStyle(root);
  const tok = (k) => parseFloat(rs.getPropertyValue(k));
  const small = tok('--cp-font-small'), base = tok('--cp-font-base');
  const tokens = { small, base, title: tok('--cp-font-title'), display: tok('--cp-font-display') };
  const all = [root, ...root.querySelectorAll('*')].filter((e) => shown(e) && ownText(e).length);
  const fs = (e) => parseFloat(getComputedStyle(e).fontSize);
  const under = all.filter((e) => !e.closest('.xterm') && fs(e) < small - 0.01).map((e) => ({ who: path(e), size: fs(e), text: norm(e.textContent).slice(0, 24) }));
  const body = [...root.querySelectorAll(bodySel)].filter((e) => shown(e) && norm(e.textContent));
  const bodySmall = body.filter((e) => fs(e) < base - 0.01).map((e) => ({ who: path(e), size: fs(e), text: norm(e.textContent).slice(0, 24) }));
  const bodyMin = body.length ? Math.min(...body.map(fs)) : null;
  const rows = [...root.querySelectorAll('.xterm-rows')].find(shown) || null;
  const term = rows ? { size: fs(rows), dims: window.__office.debug.termDims?.() || null } : null;
  return { tokens, cpFont: root.dataset.cpFont || '', texts: all.length, under: under.slice(0, 8), nUnder: under.length,
    body: body.length, bodyMin, bodySmall: bodySmall.slice(0, 8), nBodySmall: bodySmall.length,
    bodyKinds: [...new Set(body.map((e) => (e.className || e.tagName).toString().split(' ')[0]))], term };
}"""

# 4 境目（窓の枠・溝・題名の帯）
SEAMS = "() => {" + KIT + r"""
  const wins = [...root.querySelectorAll('.cpwin')].filter(shown);
  const info = wins.map((w) => {
    const cs = getComputedStyle(w), r = w.getBoundingClientRect();
    const sides = ['Top', 'Right', 'Bottom', 'Left'].map((s) => ({ w: parseFloat(cs[`border${s}Width`]), st: cs[`border${s}Style`], c: cs[`border${s}Color`] }));
    const line = sides[0].c, bg = cs.backgroundColor, out = ground(w);
    return { who: who(w), rect: [R(r.left), R(r.top), R(r.right), R(r.bottom)], r,
      framed: sides.every((s) => s.w >= 1 && s.st !== 'none' && s.st !== 'hidden'),
      line, bg, out, toOut: R(ratio(line, out) * 100) / 100, toBg: R(ratio(line, bg) * 100) / 100,
      opaque: (rgba(bg) || [0, 0, 0, 0])[3] >= 0.99 };
  });
  const pairs = [];
  for (let i = 0; i < info.length; i += 1) for (let j = i + 1; j < info.length; j += 1) {
    const a = info[i].r, b = info[j].r;
    const ovY = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top), ovX = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    let gap = null;
    if (ovY > 8) gap = Math.max(b.left - a.right, a.left - b.right);
    else if (ovX > 8) gap = Math.max(b.top - a.bottom, a.top - b.bottom);
    if (gap !== null && gap <= __GAPMAX__) pairs.push({ a: info[i].who, b: info[j].who, gap: R(gap) });
  }
  const secs = [...root.querySelectorAll('.cpsec')].filter(shown).map((s) => {
    const w = s.closest('.cpwin'); const sb = getComputedStyle(s).backgroundColor, wb = w ? getComputedStyle(w).backgroundColor : '';
    return { who: who(s), win: w ? who(w) : null, bg: sb, winBg: wb, ratio: R(ratio(sb, wb) * 100) / 100 };
  });
  return { wins: info.map(({ r, ...x }) => x), pairs, secs };
}""".replace("__GAPMAX__", str(PAIR_GAP_MAX))

# 5 受付のトレイ（今の ❗）
RECEPTION = "() => {" + KIT + r"""
  const tray = document.querySelector('#attn'), rec = document.querySelector('#win-reception');
  const inRec = !!tray && !!rec && rec.contains(tray);
  if (!tray || tray.hidden) return { inRec, hidden: true, head: '', btns: [] };
  // 人が動かせる箱（overflow が auto/scroll）だけでボタンを見えている所へ寄せる（scrollIntoView は hidden の祖先まで動かす）
  const reveal = (e) => {
    for (let p = e.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (!/(auto|scroll)/.test(cs.overflowY) || p.scrollHeight <= p.clientHeight + 1) continue;
      const a = inner(p), q = e.getBoundingClientRect();
      if (q.top < a.top) p.scrollTop -= a.top - q.top;
      else if (q.bottom > a.bottom) p.scrollTop += Math.min(q.top - a.top, q.bottom - a.bottom);
    }
  };
  // overflow:hidden（clip）の祖先が動いていないか（人が動かせない箱が寄せで動いた＝測り方の嘘）
  const movedHidden = (e) => { const out = []; walk(e, (p, cs, self) => { if (!self && ((p.scrollTop && /(hidden|clip)/.test(cs.overflowY))
      || (p.scrollLeft && /(hidden|clip)/.test(cs.overflowX)))) out.push(who(p)); }); return out; };
  const tall = [];
  const btns = [...tray.querySelectorAll('button')].filter(shown).map((b) => {
    reveal(b);
    const lh = parseFloat(getComputedStyle(b).lineHeight) || 20, pad = parseFloat(getComputedStyle(b).paddingTop) + parseFloat(getComputedStyle(b).paddingBottom);
    const r = b.getBoundingClientRect(), v = visRect(b);
    if (r.height > Math.max(44, 2 * lh + pad + 4) + 0.5) tall.push({ label: norm(b.textContent).slice(0, 18), h: R(r.height) });
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const inside = cx >= v.left - __TOL__ && cx <= v.right + __TOL__ && cy >= v.top - __TOL__ && cy <= v.bottom + __TOL__;
    const at = document.elementFromPoint(cx, cy);
    return { label: norm(b.textContent).slice(0, 18), rect: [R(r.left), R(r.top), R(r.right), R(r.bottom)],
      vis: [R(v.left), R(v.top), R(v.right), R(v.bottom)], inside, hit: !!at && (at === b || b.contains(at)), at: at ? path(at) : null,
      moved: movedHidden(b) };
  });
  tray.scrollTop = 0;
  return { inRec, hidden: false, head: norm(tray.querySelector('.trayhead')?.textContent || ''), btns, tall };
}""".replace("__TOL__", str(EDGE_TOL))
TRAY_N = """async () => (await import('/ui/core/world.js')).attentionQueue(window.__office.debug.world().agents || []).length"""

# 7 2 カラムの名簿（まるごと見える行・受付の丈・INBOX ZERO）
ROSTER = "() => {" + KIT + r"""
  const box = document.querySelector('#cp-roster'); const v = visRect(box);
  const rows = [...document.querySelectorAll('#agents .pxrow')].filter(shown);
  const whole = rows.filter((r) => { const q = r.getBoundingClientRect();
    return q.top >= v.top - 0.5 && q.bottom <= v.bottom + 0.5 && q.left >= v.left - 0.5 && q.right <= v.right + 0.5; });
  // まるごと見える行の名前が、上に重なる物（見出し・札）に覆われていない
  const hit = whole.filter((r) => { const n = r.querySelector('.pname') || r; const q = n.getBoundingClientRect();
    const at = document.elementFromPoint(q.left + Math.min(q.width / 2, 24), q.top + q.height / 2); return !!at && r.contains(at); });
  const rec = document.querySelector('#win-reception').getBoundingClientRect();
  const zero = document.querySelector('#cp-inbox-zero'), tray = document.querySelector('#attn');
  const zr = zero && shown(zero) ? zero.getBoundingClientRect() : null, zv = zr ? visRect(zero) : null;
  return { cols: getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\s+/).length,
    scale: window.__office.stats().scale, rows: rows.length, whole: whole.length, hit: hit.length,
    names: whole.slice(0, 3).map((r) => norm(r.querySelector('.pname')?.textContent).slice(0, 10)),
    rosterH: R(v.bottom - v.top), recH: R(rec.height), attn: !!tray && shown(tray),
    zero: zr ? { h: R(zr.height), whole: zv.top <= zr.top + 0.5 && zv.bottom >= zr.bottom - 0.5 } : null };
}"""

# 6 名札（#cpnames）
PLATES = "() => {" + KIT + r"""
  const layer = document.querySelector('#cpnames');
  if (!layer) return { layer: false };
  const canvas = document.querySelector('.cproom');
  const c = canvas.getBoundingClientRect();
  const scale = window.__office.stats().scale;
  const actors = (window.__office.debug.roomActors() || []).filter((a) => !a.leaving);
  const plates = [...layer.children].filter(shown).map((p) => { const r = p.getBoundingClientRect();
    return { key: p.dataset.session || p.dataset.key || '', text: norm(p.textContent).slice(0, 16), r: { left: r.left, top: r.top, right: r.right, bottom: r.bottom } }; });
  const outside = plates.filter((p) => p.r.left < c.left - 0.5 || p.r.top < c.top - 0.5 || p.r.right > c.right + 0.5 || p.r.bottom > c.bottom + 0.5)
    .map((p) => ({ key: p.key, text: p.text, rect: [R(p.r.left), R(p.r.top), R(p.r.right), R(p.r.bottom)] }));
  const laps = [];
  for (let i = 0; i < plates.length; i += 1) for (let j = i + 1; j < plates.length; j += 1) {
    const a = plates[i].r, b = plates[j].r;
    if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5)
      laps.push(`${plates[i].text}×${plates[j].text}`);
  }
  const keys = new Set(plates.map((p) => p.key).filter(Boolean));
  const missing = keys.size ? actors.filter((a) => !keys.has(a.session) && !keys.has(a.key)).map((a) => a.session) : [];
  return { layer: true, scale, actors: actors.length, plates: plates.length, keyed: keys.size, missing, outside: outside.slice(0, 6), laps: laps.slice(0, 6),
    canvas: [R(c.left), R(c.top), R(c.right), R(c.bottom)] };
}"""

NAMED = [".cptab .nm", ".cpcounts", ".freshness", "#cp-login-slot .digest-strip"]


def settle(page, ms=350):
    """形を替えた後: ResizeObserver → rAF で倍率と列を選び直す。字の読み込みも待つ"""
    page.wait_for_timeout(ms)
    page.evaluate("() => document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))")


# R121: 1 ペインの中央の既定は作業ビュー。この門は本物の端末（xterm）を測る＝「ターミナル派」の保存を入れて開く
# （既定と［ターミナル］⇄［作業］の切替そのものは cockpit_term_smoke が見る）
TERMINAL_PREF = "try { localStorage.setItem('aioffice.cockpit.center', 'terminal'); } catch (e) {}"


def open_page(browser, port, theme, world, errors, label):
    ctx = browser.new_context(viewport={"width": SHAPES[0][0], "height": SHAPES[0][1]}, device_scale_factor=1)
    ctx.add_init_script(TERMINAL_PREF)
    term_routes(ctx)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror({label}): {e}"))
    page.on("console", lambda m: errors.append(
        f"console.error({label}): {m.text}（{(m.location or {}).get('url', '')}）") if m.type == "error" else None)
    q = "" if theme == "dark" else f"&theme={theme}"
    page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2&seed=11{q}", wait_until="networkidle")
    page.wait_for_function("() => window.__office && window.__office.inject", timeout=30000)
    page.evaluate("w => window.__office.inject(w)", world)
    page.wait_for_selector(".cproom", timeout=20000)
    return ctx, page


def wait_term(page, timeout=6000):
    """偽の端末が描かれるまで（固定時刻のページでも term.js は一覧を 1 回取りに来る）"""
    try:
        page.wait_for_function("() => !!document.querySelector('#cp-xterm .xterm-rows') && !!window.__office.debug.termDims?.()",
                               timeout=timeout)
        return True
    except Exception:  # noqa: BLE001 — 出ないこと自体は 3 が空振りの赤にする
        return False


def audit_clip(page, tag, check):
    m = page.evaluate(CLIP_AUDIT, NAMED)
    why = []
    if m["looked"] < 50:
        why.append(f"空振り: 見えている要素が {m['looked']} 個")
    if m["ell"]:
        why.append("(a) 省略記号で切れている: " + " / ".join(
            f"{x['who']}「{x['text']}」" + (f"（{x['why']}）" if x.get("why") else f"（{x.get('sw')} > {x.get('cw')}）")
            for x in m["ell"][:5]))
    if m["cut"]:
        why.append("(b) 文字が切れている: " + " / ".join(
            f"{x['who']}「{x['text']}」が {x['by']} から {x['px']}px（{x.get('axis', '')}{'・' + x['why'] if x.get('why') else ''}）"
            for x in m["cut"][:6]) + (f" ほか {len(m['cut']) - 6} 件" if len(m["cut"]) > 6 else ""))
    check(not why, f"1 {tag}: 見切れ 0（{m['looked']} 要素・省略記号 {len(m['ell'])}・はみ出し {len(m['cut'])}・"
          f"clamp {len(m['clamps'])} か所は全文が title に在る・丸ごと箱の外に隠れた字 {m['hidden']}）", " / ".join(why))
    return m


def audit_buttons(page, tag, check):
    page.mouse.move(1, 1)
    b = page.evaluate(BUTTONS)
    why = []
    if b["looked"] < 5:
        why.append(f"空振り: 見えているボタンが {b['looked']} 個")
    if b["bad"]:
        why.append("枠も地も無い: " + " / ".join(f"{x['who']}「{x['text']}」（{x['border']}・{x['bg']}）" for x in b["bad"][:6]))
    if b["brackets"]:
        why.append("[ ] の飾りが残る: " + " / ".join(f"{x['who']}「{x['text']}」{x['before']}" for x in b["brackets"][:4]))
    check(not why, f"2 {tag}: 見えているボタン {b['looked']} 個に枠か地（.pxsort・.cptab を除く）・[ ] の飾りが無い", " / ".join(why))


def audit_fonts(page, tag, check, want_cp_font, want_tokens, need_term):
    f = page.evaluate(FONTS, BODY_SEL)
    tk = f["tokens"]
    why = []
    if want_tokens and any(abs((tk.get(k) or 0) - v) > 0.01 for k, v in want_tokens.items()):
        why.append(f"token が {tk}（期待 {want_tokens}）")
    if (f["cpFont"] or "") != want_cp_font:
        why.append(f"data-cp-font が「{f['cpFont']}」（期待「{want_cp_font}」）")
    if f["texts"] < 30 or f["body"] < 5:
        why.append(f"空振り: 文字の要素 {f['texts']}・本文 {f['body']}")
    if f["nBodySmall"]:
        why.append(f"本文 {f['nBodySmall']} 個が base {tk['base']}px 未満: " + " / ".join(
            f"{x['who']} {x['size']}px「{x['text']}」" for x in f["bodySmall"][:5]))
    if f["nUnder"]:
        why.append(f"{f['nUnder']} 個が small {tk['small']}px 未満: " + " / ".join(
            f"{x['who']} {x['size']}px「{x['text']}」" for x in f["under"][:5]))
    term = f["term"]
    if need_term and not term:
        why.append("空振り: 端末（.xterm-rows）が見えない（偽の端末が出ていない）")
    elif term:
        base = tk["base"]
        low = max(11, base - 2)
        size, dims = term["size"], term["dims"] or {}
        cols = dims.get("cols") or 0
        if abs(size - base) > 0.01:
            if abs(size - low) > 0.01:
                why.append(f"端末の字 {size}px（base {base} か 80 桁が入らないときの {low} のどちらか）")
            elif cols * low / base >= TERM_REFIT:
                why.append(f"端末の字を {low}px に下げているが base {base}px でも {cols * low / base:.0f} 桁入る（{TERM_REFIT} 桁以上は base）")
    t = f"・端末 {term['size']}px（{(term['dims'] or {}).get('cols')} 桁）" if term else ""
    check(not why, f"3 {tag}: 文字は本文 ≥ base {tk['base']}px（最小 {f['bodyMin']}px・{f['body']} 個）・全部 ≥ small {tk['small']}px{t}",
          " / ".join(why))
    return f


def audit_seams(page, tag, check):
    s = page.evaluate(SEAMS)
    why = []
    if len(s["pairs"]) < PAIRS_MIN:
        why.append(f"空振り: 隣り合う窓の組が {len(s['pairs'])}（{PAIRS_MIN} 組以上のはず・窓 {[w['who'] for w in s['wins']]}）")
    for p in s["pairs"]:
        if p["gap"] < GROOVE_MIN:
            why.append(f"{p['a']} と {p['b']} の間の溝が {p['gap']}px（{GROOVE_MIN}px 以上＝地の溝で区切る）")
    in_pair = {x for p in s["pairs"] for x in (p["a"], p["b"])}
    for w in s["wins"]:
        if w["who"] not in in_pair:
            continue
        if not w["framed"]:
            why.append(f"{w['who']} の 4 辺に枠が無い")
        if not w["opaque"]:
            why.append(f"{w['who']} の地が透けている（{w['bg']}）")
        if w["toOut"] < RATIO_LINE or w["toBg"] < RATIO_LINE:
            why.append(f"{w['who']} の枠 {w['line']} が 地 {w['out']} と {w['toOut']}・窓の地 {w['bg']} と {w['toBg']}（{RATIO_LINE} 以上）")
    for c in s["secs"]:
        if c["win"] and c["ratio"] < RATIO_SEC:
            why.append(f"題名の帯 {c['who']}（{c['bg']}）と窓 {c['win']} の地（{c['winBg']}）の比 {c['ratio']}（{RATIO_SEC} 以上）")
    if not s["secs"]:
        why.append("空振り: 題名の帯（.cpsec）が無い")
    pairs = "・".join(f"{p['a']}|{p['b']} {p['gap']}px" for p in s["pairs"])
    ratios = "・".join(f"{w['who']} {w['toOut']}/{w['toBg']}" for w in s["wins"] if w["who"] in in_pair)
    check(not why, f"4 {tag}: 隣り合う窓 {len(s['pairs'])} 組（{pairs}）の溝 ≥ {GROOVE_MIN}px・枠×地 ≥ {RATIO_LINE}（{ratios}）・"
          f"題名の帯×窓 ≥ {RATIO_SEC}（{len(s['secs'])} 本）", " / ".join(why))


def audit_reception(page, tag, check):
    """❗ を J で巡回（n 回測って n 回 J＝先頭へ戻る）し、どの ❗ でもトレイの全ボタンが見えている矩形の内で当たる"""
    n = page.evaluate(TRAY_N)
    states, bad, tall = [], [], []
    for _ in range(max(n, 1)):
        m = page.evaluate(RECEPTION)
        states.append(m)
        bad += [dict(b, head=m["head"][:16]) for b in m["btns"] if not (b["inside"] and b["hit"] and not b["moved"])]
        tall += m.get("tall") or []
        page.keyboard.press("j")
        page.wait_for_timeout(120)
    vacant = n < 1 or any(s["hidden"] or not s["btns"] for s in states) or not all(s["inRec"] for s in states)
    heads = len({s["head"] for s in states})
    check(not vacant and not bad and not tall and heads == n,
          f"5 {tag}: 受付の ❗ {n} 件を巡回し、どの ❗ でもトレイの全ボタン（{sum(len(s['btns']) for s in states)} 個）の"
          "中心が（トレイの中のスクロールで寄せて）見えている矩形の内で当たる・ボタンは中身の丈・#attn は #win-reception の子",
          ("空振り: " if vacant else "") + f"受付の子 {[s['inRec'] for s in states]}・見出し {heads}/{n} 種・"
          f"伸びたボタン {tall[:4]}・"
          f"押せない {[(b['head'], b['label'], b['rect'], '見える ' + str(b['vis']), b['at'], b['moved']) for b in bad[:5]]}"
          " → ui/cockpit/style.css の受付（#win-reception と #attn.tray の grid）でボタンの行を窓の中に残す")


def audit_roster(page, tag, check, has_attn):
    """7: 2 カラムの名簿の行が 2 行以上まるごと見え、受付は ❗ のある間 140px 以上・無い間 140px 以下で INBOX ZERO が見える"""
    m = page.evaluate(ROSTER)
    why = []
    if m["cols"] != 2:
        why.append(f"空振り: 2 カラムでない（列 {m['cols']}）")
    if m["scale"] != 2:
        why.append(f"部屋が ×{m['scale']}（2 カラムのこの形は ×2＝部屋は譲らない）")
    if m["whole"] < ROSTER_ROWS_MIN or m["hit"] < ROSTER_ROWS_MIN:
        why.append(f"まるごと見える行 {m['whole']}（名前が覆われていない {m['hit']}）・名簿の見えている丈 {m['rosterH']}px"
                   f"（{ROSTER_ROWS_MIN} 行以上）")
    if has_attn and (not m["attn"] or m["recH"] < RECEPTION_FLOOR):
        why.append(f"❗ のある間の受付の窓 {m['recH']}px・トレイ {'出ている' if m['attn'] else '出ていない'}（{RECEPTION_FLOOR}px 以上）")
    if not has_attn and (m["attn"] or m["recH"] > RECEPTION_FLOOR + 0.5 or not (m["zero"] or {}).get("whole")):
        why.append(f"❗ の無い間の受付の窓 {m['recH']}px（{RECEPTION_FLOOR}px 以下）・INBOX ZERO {m['zero']}・トレイ {m['attn']}")
    check(not why, f"7 {tag}: 2 カラムの名簿の行が {m['whole']} 行まるごと見える（{'・'.join(m['names'])}…）・受付 {m['recH']}px"
          f"（{'❗ あり ≥' if has_attn else '❗ なし ≤'} {RECEPTION_FLOOR}）・部屋 ×{m['scale']}",
          " / ".join(why) + " → ui/cockpit/style.css の 2 カラムの段（--cp-roster-min・受付の上限・@container cproster）")


WIDE_TRAY = """() => {
  const t = document.querySelector('#attn'), s = document.querySelector('#sheet');
  return { sheet: !!s && !s.hidden && s.getClientRects().length > 0, wide: !!s && s.classList.contains('wide'),
    display: t ? getComputedStyle(t).display : 'none', box: t ? t.getClientRects().length : 0 };
}"""


def audit_wide_without_attention(page, tag, check):
    """8: ❗ の無い世界で名簿の行から指示欄を開き、⤢ で広げてもトレイは出ない（display:none）"""
    page.locator("#agents .pxrow").first.click()
    try:
        page.wait_for_function("() => { const s = document.querySelector('#sheet'); return !!s && !s.hidden; }", timeout=4000)
    except Exception:  # noqa: BLE001 — 開かないことは下で空振りの赤にする
        pass
    before = page.evaluate(WIDE_TRAY)
    if before["sheet"] and not before["wide"]:
        page.locator("#sheetwide").click()
        page.wait_for_timeout(150)
    after = page.evaluate(WIDE_TRAY)
    ok = before["sheet"] and after["wide"] and after["display"] == "none" and before["display"] == "none" and not after["box"]
    check(ok, f"8 {tag}: ❗ の無い世界で指示欄を ⤢ で広げてもトレイ（#attn）は出ない（display {after['display']}）",
          ("空振り: 名簿の行で指示欄が開かない" if not before["sheet"] else
           "空振り: ⤢ で広がらない" if not after["wide"] else
           f"トレイが出た（⤢ の前 {before['display']}・後 {after['display']}・箱 {after['box']}）")
          + " → ui/cockpit/style.css の #attn.tray の則に :not([hidden])")
    if after["wide"]:
        page.locator("#sheetwide").click()
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)


def audit_plates(page, tag, check, skipped):
    p = page.evaluate(PLATES)
    if not p["layer"]:
        skipped.append(tag)
        print(f"  － 6 {tag}: 名札の層（#cpnames）が無い＝skip（別の担当が入れたら測る・黙って緑にしない）")
        return
    if p["scale"] < 3:
        print(f"  － 6 {tag}: 部屋が ×{p['scale']}（×3 未満は名札を出さない約束）＝名札の検査は対象外")
        return
    why = []
    if p["plates"] != p["actors"] or p["missing"]:
        why.append(f"札 {p['plates']} 枚・部屋の人 {p['actors']} 人（札の無い人 {p['missing'][:5]}）")
    if p["outside"]:
        why.append(f"部屋の canvas {p['canvas']} の外: {p['outside']}")
    if p["laps"]:
        why.append(f"重なる: {p['laps']}")
    check(not why, f"6 {tag}: 名札が部屋の全員ぶん（{p['plates']} 枚・×{p['scale']}）・部屋の内・重ならない", " / ".join(why))


def main():
    from playwright.sync_api import sync_playwright

    cockpit = with_long_history(json.loads((FIXTURES / "cockpit.json").read_text(encoding="utf-8")))
    longw = long_world(json.loads((FIXTURES / "basic.json").read_text(encoding="utf-8")))
    # (名前, world, 主の world か＝偽の端末が紐づく人が居て、境目と名札もここで見る)
    worlds = [("cockpit.json", cockpit, True), ("basic 長文", longw, False)]
    ng, skipped, errors = 0, [], []

    def check(ok, label, detail=""):
        nonlocal ng
        print(f"  {'✓' if ok else '✗'} {label}{'' if ok else f' — {detail}'}")
        if not ok:
            ng += 1

    port = free_port()
    proc = start_server(port)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=SWIFTSHADER)
            for theme in THEMES:
                ctx, page = open_page(browser, port, theme, cockpit, errors, theme)
                try:
                    got_term = wait_term(page)
                    for vw, vh in RECEPTION_SHAPES:
                        page.set_viewport_size({"width": vw, "height": vh})
                        for wname, w, main_world in worlds:
                            page.evaluate("w => window.__office.inject(w)", w)
                            settle(page)
                            tag = f"{theme} {vw}×{vh} {wname}"
                            if (vw, vh) in SHAPES:
                                audit_clip(page, tag, check)
                                audit_buttons(page, tag, check)
                                audit_fonts(page, tag, check, "14", {"small": 12, "base": 14, "title": 16, "display": 20},
                                            need_term=main_world and got_term)
                                if main_world:
                                    audit_seams(page, tag, check)
                                    audit_plates(page, tag, check, skipped)
                            audit_reception(page, tag, check)
                    # 7 2 カラムの名簿（❗ のある世界と無い世界）
                    calm = without_attention(cockpit)
                    for vw, vh in ROSTER_SHAPES:
                        page.set_viewport_size({"width": vw, "height": vh})
                        for wname, w, has_attn in (("cockpit.json", cockpit, True), ("❗ なし", calm, False)):
                            page.evaluate("w => window.__office.inject(w)", w)
                            settle(page)
                            audit_roster(page, f"{theme} {vw}×{vh} {wname}", check, has_attn)
                    # 8 ❗ の無いときの ⤢
                    for vw, vh in WIDE_SHAPES:
                        page.set_viewport_size({"width": vw, "height": vh})
                        page.evaluate("w => window.__office.inject(w)", calm)
                        settle(page)
                        audit_wide_without_attention(page, f"{theme} {vw}×{vh} ❗ なし", check)
                    page.evaluate("() => window.scrollTo(0, 0)")
                    if not got_term:
                        check(False, f"3 {theme}: 偽の端末（/api/term/list・/api/term/stream）が描かれる",
                              "空振り: .xterm-rows が出ない（端末の字の大きさを測れない）")

                    # 文字の大きさ 16（⚙ の最大の段）: 開き直して token と最小が追従し、大きい字でも見切れない
                    page.evaluate(f"() => localStorage.setItem('aioffice.cockpit.font', '{FONT_BIG}')")
                    page.reload(wait_until="networkidle")
                    page.wait_for_function("() => window.__office && window.__office.inject", timeout=30000)
                    page.evaluate("w => window.__office.inject(w)", cockpit)
                    page.wait_for_selector(".cproom", timeout=20000)
                    got_term16 = wait_term(page)
                    for vw, vh in SHAPES:
                        page.set_viewport_size({"width": vw, "height": vh})
                        for wname, w, main_world in worlds:
                            page.evaluate("w => window.__office.inject(w)", w)
                            settle(page)
                            tag = f"{theme} {vw}×{vh} {wname} 文字 {FONT_BIG}"
                            audit_fonts(page, tag, check, FONT_BIG, {"small": 13, "base": 16, "title": 18, "display": 22},
                                        need_term=main_world and got_term16)
                            audit_clip(page, tag, check)
                    page.evaluate("() => localStorage.removeItem('aioffice.cockpit.font')")
                finally:
                    ctx.close()
            check(not errors, "⓪ console.error / pageerror = 0", "; ".join(errors[:3]))
            browser.close()
    finally:
        proc.terminate()

    tail = f"（名札 skip {len(skipped)}）" if skipped else ""
    print(f"✅ R110 コックピットの見た目 green{tail}" if not ng else f"❌ R110 コックピットの見た目 {ng} 件{tail}")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
