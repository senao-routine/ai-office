#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99: 様式 cockpit（俯瞰オフィス × 台帳 × 端末 × 一覧）のスモーク。

「描けた」ではなく、**4 つの面が同時に生きている**ところまでを機械で確かめる。
コックピットの芯は「オフィスとターミナルが両方開いている」ことなので、
どれか 1 つが潰れたら（それが起きやすい）この門が落ちる。

検査:
  1. 4 面が在る（部屋の canvas・名簿の行・端末のタブ・受付の窓）・console.error = 0。R110 骨格 A で一覧のカードは名簿へ統合した
     ＝4 面目は受付（#win-reception の中の ❗トレイ #attn か INBOX ZERO）
  2. 部屋は論理 184×160 の**整数倍**・pixelated・割合で縮んでいない
  3. canvas に文字を描かない（fillText / strokeText を throw に差し替えても mount が通る）
  4. ロボをクリック → 中央の端末がそのセッションに切り替わる（本人の要望の芯）
  5. 台帳の行にホバー → 部屋のその人の足元に線（表と部屋が同じ個体を指している・本体は live の 12）
  6. 形状 6 つで、横スクロールが無く、**台帳と端末が潰れない**
     （design/ で実測した穴: 横が広くて縦が低い画面で台帳が 1px になる）。R110: 台帳は 02 名簿の箱（#cp-roster・中でスクロール）
  7. 色の正本が 1 本（ui/cockpit/style.css の --cp-* → --t-* == ui/cockpit/palette.js の UI_COLORS → TERM.dark）
  8. 確認待ち（❗）が最初に選ばれている・部屋では受付に立っている
  9. 生成物が生成器と一致（gen_cells.py / gen_tiles.py --check で design/ と ui/cockpit/ の両方を書かずに突き合わせる）
  --- 固定時刻のページ（?t=・golden と同じ描き方）---
 18. 固定時刻では歩かない: 1 人の目的地を変えても walking=0・その場で目的地に居る・画素は「その world だけを見た」
     新しいページと同じ（履歴で golden が動かない）
 19. 頭上の印（固定ワールド tests/fixtures/world/cockpit.json）: ❗ ⚠ 💬 💭 の数が、fixture の JSON を
     ここで読んで仕様の条件で数えた期待値と一致し、どれも 1 以上。印のタイルの画素が 1 ドットも欠けずに見えている
     （手前の什器に隠れない）。描けないコマ（missingCells）0・部屋からあふれていない。
     💬 の 600 秒の境: 報告して 600 秒以上待っている人（fixture の sess-cp-13）は部屋に居て 💬 が出ない（申告と画素の両方）
 28. 固定時刻のページの「動きを止める」ボタンは『固定表示』で押せない（aria-pressed=true・見本 design/cockpit.html と同じ）
 29. 3 カラム（幅 1181 以上: 1194×834・1280×640・1440×900・1920×1080）の ❗トレイ: basic・cockpit.json・basic の名前と
     質問と選択肢を長くした写しで ❗ を J で 1 件ずつ巡回し、どの ❗ でも**全部のボタンの中心が elementFromPoint で当たり、
     画面内にある**（R86-H の型）。トレイを入れた窓（R110 骨格 A＝左の列の 04 受付 #win-reception）の子（見出し・トレイ・
     INBOX ZERO・最近の指示）とトレイの子は窓の幅に収まる（暗黙の auto 列がカードの min-content に押し広げられて右が切れた
     ＝body は overflow:hidden なので 6 の横スクロールの門には写らない）。R110 S1 でトレイは grid（本文だけが中でスクロール・
     答えのボタンは本文の下の行）＝長い ❗ でもボタンが受付の窓の中に見えて押せることをここで見る（窓に切られたボタンは
     中心が窓の外の要素に当たって赤）。
     ボタンの数は ❗ ごとに fixture から独立に決めた数（basic 6＋6・cockpit.json 5＋6・長文 6＋6）で、毎回 前へ・次へ が在る。
     ページは 1 枚で world を差し替える（差し替えのたびにトレイが先頭から出ることも見出しで確かめる）
 29c. 同じ 3 カラムの 4 形で、トレイの「自由に書く」から指示欄（#sheet）を開いている間も、トレイの全ボタンの中心が当たり画面内・
     指示欄の矩形がトレイの矩形に重ならない（basic・固定時刻）。hud の置き方（right 16px・幅 .main の 58%）のままだと指示欄が
     右カラムに掛かり、答えるには指示欄を閉じる要があった（1440 で指示欄 x 704〜1424・トレイ x 1141〜1440）。
     1440 と 1194 は形状の門 ㉑（実タッチ）も見る＝ここは低い 1280×640 と、幅の上限 720 に当たる 1920 を足す
 29a. 文字列の段: 幅の段（@media の width）の文字列は 1 列 (max-width: 1180px) とその補集合だけ（静的・1180〜1181 の小数幅で
     どちらの段にも当たらない穴を作らない）。スマホの段のコンテナ問い合わせ（@container）の文字列も数える: cphead と cpledger の
     (max-width: 600px) だけ。ui/cockpit/*.css のコメントを先に除いて読む（注記の中の綴りを段に数えない）。
     見るのは文字列だけ＝問い合わせ先がどの箱か（余白を持つ箱に置くと、文字列が 600 のまま境が画面 624 にずれる）は見ない。
     境が画面 600 の 1 本に揃っているかは tests/cockpit_shapes_smoke.py の ⑭（段の一致・実描画）が見る
 29b. 色付きの左右の帯を置かない（静的・デザイン方針: カード・引用・callout の色付き border-left バー）。コメントを除いた
     ui/cockpit/*.css の宣言で、左右の罫（border-left/right/inline・border-width/color の左右）が 2px 以上か中立の罫の色
     （--cp-line・--cp-line-soft）でない・inset の box-shadow の x が 0 でない・横向きのグラデーション・::before/::after の
     幅 6px 以下の色つきの棒。ui/cockpit/*.js が左右の罫・影を style で差す口も無い（CSS に書いてこの門を通す）
 30. 配線（固定時刻・cockpit.json・タッチ可の 1 枚。どれも route で配った変異で赤になることを確かめた）:
     ① 1440→390→1440 で stats().scale 3→2→3・canvas 552→368→552・#stage[data-room-scale]・論理 px の絵が同じ・1440 へ戻すと
        inject し直さずに原寸の画素が元と一致。続けて 1 列・3 カラムの形を一巡して期待の倍率（390=×2・834 以上=×3・統括の裁定）
     ② 承認まちの ❗（question が無く ask がある sess-cp-07）を選ぶと中央に .cpq[data-ask=permission] と許可待ちのツール名
     ⑤ 部屋のロボを選ぶと（tray.show）次のデータを待たずにトレイがその人になる
     ④ 合わせた人（固定）は approvalMin の昇格で並びが入れ替わってもトレイに出続け、J で固定が外れる
     ③ 390 で台帳の行を実タッチ（touchscreen.tap）→ シートの質問文が画面の中（revealHead）
  --- live ページ（?t= 無し＝刻みで描く）---
 10. 描画コストの実測: CanvasRenderingContext2D の fillRect/drawImage を外から包み、部屋の canvas に
     1 フレームで呼んだ数が stats().drawCalls と一致し、ui_shot の DRAW_CALL_LIMIT 以下（basic・歩行中・xl22・cockpit_peak）。
     描けないコマ・タイル・届かない歩行が 0。world を入れ替えた入退場の山（xl22・cockpit_peak）でも 1 フレームの最大が上限以下
 11. 焼き置きが効いている: bgBakes === 1・fgBakes >= 1・spriteBakes >= 1（0 件＝焼き置きを使っていない）。
     しかも焼き直しの無い 12 フレームでは、**全 canvas** へ塗った数＝部屋の canvas へ塗った数
     （オフスクリーンで毎フレーム塗り直すコストは drawCalls に写らない＝外側の全数で見る）
 12. ホバー（#5 の本体）: 行に乗ると stats().hovered（実際に線を描いた人数）が行の人数になり、
     変わった画素が**全部、行の人の足元の窓**に入り、行の人それぞれに 1 画素以上ある。頭上（選択の角かっこの場所）は変わらない。
     離れると 0・元の画素。セッション行（展開した子の行）に乗ると hovered === 1 で、その人の足元だけが変わる
 13. フッターの「席 n/9・受付 m・外 k」: 席 n と受付 m は部屋に描いた人（roomActors の区分を数えた desk・queue＋standby。
     退場中の人は数えない。数えた人は貼った画素でも居ることを確かめ、stats().placed の申告と食い違っても赤）、
     外 k は roomLayout を自分で呼んだ overflow（1 人以上のときだけ出る）。basic・xl22・cockpit_peak（外 ≥ 1）
 14. 席が安定: 机 0 の人が ❗ になっても、他の人の部屋の位置が動かない。在席の集合が変わるので
     fgBakes が +1 し、机 0 のモニタの画素が変わる（消える）。戻すと fgBakes がまた +1、モニタは元の画素。
     入れ替え（❗ で席を立つ人と新しく座る人が同じポーリング＝在席の人数は同じで集合が変わる）でも焼き直し、
     空いた机の画面は消え（❗ だけのときと同じ画素）、座った机の画面が点き、他の画面は変わらない
 15. R105-A: 足元の Last login を閉じても live の席は同じ・部屋を覆うカードは自動で開かない。
     再生の席の分離は tests/cockpit_room.test.mjs が draw の呼び出し口ごと見る
 16. 個体色が前回を引き継ぐ: 表示中の人の色を奪う新しいプロジェクトが来ても、タブの色が変わらない。
     部屋のロボ（**貼った画素から当て直した色**）・タブ・名簿の行（--session）が同じ色（割り当ては index.js の 1 本）
 17. 受付の standby（あふれて立っている人）は挙手（raise）も打鍵（type）もしない（xl22・**貼った画素から当て直したコマ**）
 20. 開いた瞬間の全員は扉から入らない（どの live ページも、最初に描いた人数 > 0 で entering・walking が一度も立たない）
 21. 描画の刻み: 誰も歩いていないと 6fps・歩いている人（入退場）が居る間だけ 12fps。stats().fps と、rAF の中で部屋に
     描いた回数を外から数えた実測の両方で見る（1 秒あたりの rAF の数も出す＝マシンが遅いときはそれと分かる）。
     歩いている窓（入場・退場・入退場の山）でも手前の段を焼き直さない（C3 の裁定: 在席の集合が変わったときだけ）:
     fgBakes の増分が窓の中の inject の回数以下、rAF の中で部屋以外へ塗った数がスプライトの焼き（1 枚＝ドットの数まで）で
     説明できる分を超えない（11 は誰も歩いていない窓しか見ない＝12fps の間に毎フレーム焼き直す退行は 11 に写らない）。
     ②は焼いた枚数が増えると上限も上がる（自分基準）＝スプライトの焼き置きは 21b で見る
 22. 選択: 端末のタブでセッションを選ぶと、部屋のその人の頭上に角かっこ（左右 2 つの鉤・その人の個体色）。
     変わった画素は前の選択と今の選択の頭上だけ・足元（ホバーの線の場所）は変わらない。戻すと元の画素
  --- live ページで時計を手で進める（debug.step・1/12 秒ずつ）---
 23. 歩行: 机の人を ❗ にすると、席で立ち上がって（R110-S4・最初の 2 刻み＝RISE_SEC・歩行コマなし）から受付まで歩く。
     瞬間移動しない（1 刻み 4px 以下）・全部の位置が経路（planPath）の上・
     全部の位置で足（ROBOT_FOOT）が家具の足元（FURNITURE の foot/solid・自分の枠と行き先の枠の家具を除く）と壁に重ならない・
     向きは今いる区間の主軸（W は E の鏡）・12fps の 1 刻みで歩行コマがちょうど 1 つ進む・着いたら止まる（fps 6 に戻る）・
     他の人は動かない
 24. 入退場: 新しいセッションは扉（DOOR の上）に現れて席まで歩く（entering）。消したセッションは扉まで歩いて消える
     （leaving・印も挙手もしない・消えたら帳簿からも居なくなる）。どちらも 23 と同じ経路の門を通す。退場の最後に描かれる
     位置は、入場で現れた扉の点へ入る最後の区間の上で、1 刻みの上限（MAX_STEP）以内（経路の長さを歩幅で割った余りで
     扉の 1〜2px 手前に決まる＝座る机が変わっても通る許容）・次の刻みで消えている。席から消えた人は席で立ち上がってから
     （最初の 2 刻みだけ rise・挙手ではない）歩く。扉から入る人は立ち上がらない
 25. 重なり順: 歩いている人が手前の什器の天板の前を通るとき、天板の画素が人の上に来ない（画素で・家具の足元の下端と
     人の足元の y だけで前後を決める）。席に着くと机が腰から下を隠す（門が空振りしていない）
 26. ✓: working → 報告して待機（waiting・kind said）に変えると、同じ刻みで ✓（タイルの画素まで）と万歳のコマ・8 秒で 💬 に替わる
 21b. 焼き置きの上限は「初めて貼った鍵の数」: 帰った人と同じ入場（同じセッション・同じ world）をもう一度歩かせると、
     spriteBakes の増分はその窓で初めて貼った鍵（ロボの vendor・cell・色・左右／印／足跡）の数まで（本人の分は 0）
  --- live ページ・視差効果を減らす（emulate_media reduced_motion=reduce）---
 27. 部屋の画素が t に依らない（コマが送られない）・fps 6・報告に変わっても ✓ が出ない・新しい人は歩かずに席に居る。
     「動きを止める」ボタンも止まっている側（aria-pressed=true・文言は「動きを戻す」＝reduce とボタンは 1 つの状態）。
     reduce を掛けたままボタンを押すと部屋が動き（still=false・コマが送られる）、ボタンも動いている側。もう一度押すと止まる。
     reduced を外すと（change を購読している）コマが動き出し、ボタンも動いている側に戻る。
     止めている間の報告を遅れて祝わない: **live で working を描かせて（記録させて）から**止め、止めている間に報告へ変え、
     STALE_WAIT 秒待って戻す。戻した後に遅れた ✓（万歳）も扉からの入場も出ない（reduce・ボタン・非表示タブの 3 経路）。
     非表示タブの間はデータが届いても部屋を描かない（stats().frames が進まない）

使い方: python3 tests/cockpit_smoke.py   （verify.sh ▶7 から呼ぶ・Playwright 必要）
"""
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import DRAW_CALL_LIMIT, SWIFTSHADER, VIEWPORT, free_port, start_server  # noqa: E402

WORLD = ROOT / "tests" / "fixtures" / "world" / "basic.json"
FIXTURES = ROOT / "tests" / "fixtures" / "world"
ROOM_W, ROOM_H = 184, 160
# 形状 6 つ。890×626 は折りたたみを開いた形、1280×640 は「横に広くて縦が低い」＝事故が起きた形。
SHAPES = [(1440, 900), (1280, 640), (1024, 768), (834, 1112), (890, 626), (390, 844)]
LEDGER_MIN = 110          # 名簿の箱（#cp-roster）の丈の下限: 見出し 32 ＋ 2 段の行（R110・約 77）が 1 行まるごと見える高さ
TERM_MIN = 80
# R105-A: 3 カラムは 1400px 超。2 カラムの一覧タブは形状の門で別に見る。
TRAY_SHAPES = [(1401, 834), (1440, 640), (1440, 900), (1920, 1080)]
# 描画の刻み（統括の裁定: 誰も歩いていない間 6fps・歩いている人が居る間 12fps）と、1 刻み（1/12 秒）に動いてよい距離。
# 歩く速さは 24px/秒（R110-S4）＝1 刻み 2px。4px を超えたら「歩いた」ではなく「跳んだ」
IDLE_FPS, WALK_FPS = 6, 12
MAX_STEP = 4
# ✓ を出す長さ（秒）と、💬 を出す報告からの秒数の上限（どちらも統括の既定・本人が覆せる）
DONE_SEC, WAIT_MAX_AGE = 8, 600
# 「止めている間の報告を遅れて祝わない」（gap.md D-1「停止ボタンの間は判定も止め、再開したときに遅れて祝わない」）を
# 確かめる停止の長さ（秒）。実装の MARK_STALE_SEC（今は 4）は読まない＝独立の数字（読むと、古さの門を壊す変異が
# 期待値ごと動いて緑のまま）。実装がこれより長い古さを「続き」として扱うようにしたら、この長さの停止で遅れた ✓ が出る＝赤が正しい
STALE_WAIT = 5.5


# **全 canvas**（オフスクリーンを含む）への fillRect/drawImage を外から数え、そのうち部屋の canvas
# （class=cproom）への分を別に数える。1 フレーム＝rAF のコールバック 1 回で、部屋に 1 回以上描いたものだけを
# 「部屋のフレーム」として記録する（apply() からの描画は rAF の外で起きるので、フレームの数には混ざらない）。
# off ＝rAF の中で部屋以外へ塗った数の累計（毎フレームのオフスクリーン作業は drawCalls に写らない＝ここで見る）。
# stamps＝部屋のフレームの時刻（描画の刻みの実測）・ticks＝rAF の数（マシンの速さ）・max＝1 フレームの最大（入退場の山）・
# first / enterMax / walkMax＝最初に描いたフレームの人数と、入場・歩行の最大（開いた瞬間に扉から入る嘘を捕まえる）。
ROOM_CALL_COUNTER = """
(() => {
  const c = { n: 0, all: 0, frame: -1, frameAll: -1, frames: 0, off: 0, max: 0, stamps: [], ticks: 0, lastTs: -1,
    first: null, enterMax: 0, walkMax: 0 };
  Object.defineProperty(window, '__roomCalls', { value: c });
  const protos = [window.CanvasRenderingContext2D, window.OffscreenCanvasRenderingContext2D]
    .filter(Boolean).map((k) => k.prototype);
  for (const P of protos) {
    for (const k of ['fillRect', 'drawImage']) {
      const orig = P[k];
      P[k] = function (...args) {
        c.all += 1;
        if (this.canvas && this.canvas.classList && this.canvas.classList.contains('cproom')) c.n += 1;
        return orig.apply(this, args);
      };
    }
  }
  const raf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (cb) => raf((ts) => {
    if (ts !== c.lastTs) { c.lastTs = ts; c.ticks += 1; }
    const n0 = c.n, a0 = c.all;
    cb(ts);
    const room = c.n - n0, all = c.all - a0;
    c.off += all - room;
    if (room > 0) {
      c.frame = room; c.frameAll = all; c.frames += 1; c.max = Math.max(c.max, room);
      c.stamps.push(performance.now());
      if (c.stamps.length > 600) c.stamps.shift();
      const st = window.__office && window.__office.stats && window.__office.stats();
      if (st) {
        if (c.first === null) c.first = { actors: st.actors, entering: st.entering, walking: st.walking };
        c.enterMax = Math.max(c.enterMax, st.entering || 0);
        c.walkMax = Math.max(c.walkMax, st.walking || 0);
      }
    }
  });
})();
"""

# 部屋が rAF の中で次に描くまで待ち、外から数えた数と stats を同じタスクで読む（6fps なので「次の rAF」では足りない）。
# rAF のコールバックは登録順に同じフレームで走る＝描画のループの tick が先、この待ちが後＝両方が同じ 1 フレームを指す。
MEASURE = """async () => {
  const c = window.__roomCalls, f0 = c.frames;
  for (let i = 0; i < 600; i += 1) {
    await new Promise((r) => requestAnimationFrame(() => r()));
    if (c.frames !== f0) return { ext: c.frame, st: window.__office.stats() };
  }
  return { ext: -1, st: window.__office.stats() };
}"""
# 部屋のフレームを n 回待つ（rAF の中の描画だけを数える）
ROOM_FRAMES = """async (n) => {
  const c = window.__roomCalls, f0 = c.frames;
  for (let i = 0; i < 60 * (n + 2) && c.frames - f0 < n; i += 1) {
    await new Promise((r) => requestAnimationFrame(() => r()));
  }
  return c.frames - f0;
}"""
# 部屋のフレーム n 回の窓: その間に焼き直し（bg/fg/sprite の累計）が起きたか、部屋以外へ何回塗ったか。
# 焼き直しの無い窓で off > 0 ＝毎フレームのオフスクリーン作業（焼き置きを使っていない）。
BAKE_WINDOW = """async (n) => {
  const k = ['bgBakes', 'fgBakes', 'spriteBakes'];
  const c = window.__roomCalls;
  const s0 = window.__office.stats(), off0 = c.off, f0 = c.frames;
  for (let i = 0; i < 60 * (n + 2) && c.frames - f0 < n; i += 1) {
    await new Promise((r) => requestAnimationFrame(() => r()));
  }
  const s1 = window.__office.stats();
  return { off: c.off - off0, frames: c.frames - f0,
    baked: k.some((x) => s1[x] !== s0[x]), bakes: Object.fromEntries(k.map((x) => [x, s1[x]])) };
}"""
# sec 秒のあいだ、rAF の中で部屋に描いた回数（毎秒）・rAF の数（毎秒）・見えた stats().fps・歩いている人数の最小
RATE = """async (sec) => {
  const c = window.__roomCalls, f0 = c.frames, k0 = c.ticks, t0 = performance.now();
  const fps = new Set();
  let walkMin = Infinity;
  while (performance.now() - t0 < sec * 1000) {
    await new Promise((r) => requestAnimationFrame(() => r()));
    const st = window.__office.stats();
    fps.add(st.fps);
    walkMin = Math.min(walkMin, st.walking);
  }
  const ms = performance.now() - t0;
  return { rate: (c.frames - f0) * 1000 / ms, raf: (c.ticks - k0) * 1000 / ms, fps: [...fps], walkMin };
}"""
# 焼き直しの窓（21）の材料: 焼き置きの累計と、rAF の中で部屋以外へ塗った数の累計（reset で歩いた最大を 0 に戻す）
BAKES = """(reset) => { const s = window.__office.stats(), c = window.__roomCalls;
  if (reset) c.walkMax = 0;
  return { fg: s.fgBakes, sprite: s.spriteBakes, bg: s.bgBakes, off: c.off, walked: c.walkMax }; }"""
# 焼き置き 1 枚でオフスクリーンへ塗る数の上限＝1 ドット 1 回 × 外形（焼き置きのスプライトはロボのセルと、印・足跡のタイル）。
# rAF の中で部屋以外へ塗った数のうち「スプライトの焼きで説明できる分」の上限を出す（手前の段の焼き直しは 1 回で数千ドット）
SPRITE_DOTS = """async () => {
  const { PX_CELL_W, PX_CELL_H } = await import('/ui/cockpit/cells.js');
  const { PX_TILES } = await import('/ui/cockpit/tiles.js');
  const { TOP_MARK_TILES } = await import('/ui/core/pxtop.js');
  const area = (n) => { const r = PX_TILES[n];
    return Array.isArray(r) && r.length ? r.length * Math.max(...r.map((x) => String(x).length)) : 0; };
  return Math.max(PX_CELL_W * PX_CELL_H, ...[...Object.values(TOP_MARK_TILES), 'footstep'].map(area));
}"""
# 「動きを止める」ボタンの状態（aria-pressed・文言・押せるか）と、止める／戻す／固定表示の文言（今の言語の strings から）
MOTION_BTN = """async () => { const { T: rawT } = await import('/ui/hud/strings.js'); const T = (await import('/ui/cockpit/plain.js')).plainT(rawT);
  const b = document.querySelector('#cp-motion');
  return { pressed: b.getAttribute('aria-pressed'), text: b.textContent, disabled: b.disabled,
    start: T('cp_motion_start'), stop: T('cp_motion_stop'), fixed: T('cp_motion_fixed') }; }"""
# 直近のフレームで貼ったスプライトの鍵（焼き置きの門の材料）: ロボは (vendor, cell, tint, flip)、頭上の印は (タイル, 印)、
# 足跡はタイルの名前 1 つ。貼った結果（roomActors・roomMarks・roomLayers）から作る（room.js の焼き置きの鍵は読まない）。
# 印は room.js の鍵（タイル＋❗の色かどうか）より細かく分ける＝上限が緩む側にしか外れない。
# [鍵, 貼った人の session（足跡は ""）] の列で返す（誰の分の鍵かを門が分けられる）
SPRITE_KEYS = """() => { const d = window.__office.debug, actors = d.roomActors();
  const out = actors.map((a) => [`robot|${a.vendor}|${a.cell}|${a.tint}|${a.flip ? 1 : 0}`, a.session || a.key]);
  const pasted = new Map(actors.map((a) => [a.key, a.session || a.key]));
  for (const m of d.roomMarks()) if (m.markAt && pasted.has(m.key)) out.push([`mark|${m.markAt.tile}|${m.mark}`, pasted.get(m.key)]);
  if (d.roomLayers().some((l) => l.startsWith('print:'))) out.push(['tile|footstep', '']);
  for (const l of d.roomLayers()) if (l.startsWith('screen:')) out.push([`tile|${l.split(':').pop()}`, l.split(':')[1]]);
  return out; }"""
# 非表示タブの代わり: document.hidden（と visibilityState）を差し替えて visibilitychange を発火する（on=false で本物に戻す）。
# ページは前面のまま＝rAF は回る（部屋が「隠れている間は描かない」を自分で守っているかを見る）
HIDE = """(on) => {
  if (on) {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
  } else {
    delete document.hidden;
    delete document.visibilityState;
  }
  document.dispatchEvent(new Event('visibilitychange'));
  return document.hidden;
}"""
ROOM_PIXELS = "() => document.querySelector('.cproom').toDataURL()"
# 部屋の画素を控える／控えとの差を論理 px（整数倍の 1 マス）の座標で返す
SNAP = """() => { const c = document.querySelector('.cproom');
  window.__snap = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; return true; }"""
DIFF = """() => { const c = document.querySelector('.cproom');
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data, a = window.__snap;
  const S = window.__office.stats().scale, W = c.width, out = new Set();
  for (let i = 0; i < d.length; i += 4) {
    if (d[i] !== a[i] || d[i + 1] !== a[i + 1] || d[i + 2] !== a[i + 2] || d[i + 3] !== a[i + 3]) {
      const p = i / 4; out.add(`${Math.floor((p % W) / S)},${Math.floor(Math.floor(p / W) / S)}`);
    }
  }
  return [...out].map((k) => k.split(',').map(Number)); }"""
# 論理 px の点の今の色（#rrggbb）
COLORS_AT = """(pts) => { const c = document.querySelector('.cproom'), S = window.__office.stats().scale;
  const x = c.getContext('2d');
  return pts.map(([px, py]) => { const d = x.getImageData(px * S, py * S, 1, 1).data;
    return '#' + [d[0], d[1], d[2]].map((v) => v.toString(16).padStart(2, '0')).join(''); }); }"""
# 机 i のモニタ（配置表で lit を持つ席 i の行のタイル＋照り返し）の画素。座標は表から取る（ここで数字を打たない）
MONITOR = """async (seat) => {
  const { FURNITURE } = await import('/ui/core/pxroom.js');
  const { PX_TILES } = await import('/ui/cockpit/tiles.js');
  const f = FURNITURE.find((x) => x.lit && x.seat === seat);
  const rows = PX_TILES[f.tile];
  const rects = [[f.x, f.y, rows[0].length, rows.length],
    ...(f.glow || []).map(([dx, dy, w, h]) => [f.x + dx, f.y + dy, w, h])];
  const x0 = Math.min(...rects.map((r) => r[0])), y0 = Math.min(...rects.map((r) => r[1]));
  const x1 = Math.max(...rects.map((r) => r[0] + r[2])), y1 = Math.max(...rects.map((r) => r[1] + r[3]));
  const S = window.__office.stats().scale;
  const c = document.querySelector('.cproom');
  const d = c.getContext('2d').getImageData(x0 * S, y0 * S, (x1 - x0) * S, (y1 - y0) * S).data;
  return { rect: [x0, y0, x1 - x0, y1 - y0], px: Array.from(d).join(',') };
}"""
# 台帳・部屋・fixture から独立に出した「部屋に入りきらない人数」（room.draw の戻り値は使わない）
LAYOUT_OVERFLOW = """async () => {
  const { roomLayout } = await import('/ui/core/pxroom.js');
  const d = window.__office.debug;
  return roomLayout(d.world(), d.board()).overflow;
}"""
ACTORS = "() => window.__office.debug.roomActors()"
DESKS_NOW = ("() => window.__office.debug.roomActors().filter(a => a.zone === 'desk' && !a.leaving)"
             ".map(a => a.seat).sort((x, y) => x - y)")
TAB_COLOR = "(s) => document.querySelector(`.cptab[data-session=\"${s}\"]`)?.style.getPropertyValue('--session') || null"
STATS = "() => window.__office.stats()"
MOTION = "() => window.__office.debug.roomMotion()"
# 部屋の canvas の画素から、1 体ずつ「どのベンダーのどのコマを・どの個体色で・左右どちらで貼ったか」を当て直す
#（room.actors() が控えた vendor / cell / tint は読まない＝控えと貼る処理が分かれても嘘をつけない）。
# 候補は PX_CELLS の全ベンダー × 全コマ × 左右 × SESSION_COLORS の全色。人の 12×16 の矩形で、候補の不透明なドットのうち
# 画素が食い違う数（miss）が最小の候補を全部返す（手前の什器・隣の人・印に隠れたドットは、どの候補でも
# 同じだけ食い違う＝順位を変えない）。位置（左上）だけは roomActors から借りる（嘘なら全候補が外れる＝緑にならない）。
# 退場中の人（扉へ歩いている・台帳に居ない）は当て直さない。
ROBOT_PIXELS = """async (sessions) => {
  const { PX_CELLS, PX_CELL_W, PX_CELL_H } = await import('/ui/cockpit/cells.js');
  const { PALETTE, SESSION_COLORS, dimTint } = await import('/ui/cockpit/palette.js');
  const rgb = (h) => { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
  const S = window.__office.stats().scale;
  const ctx = document.querySelector('.cproom').getContext('2d');
  const sheets = [];
  for (const [vendor, book] of Object.entries(PX_CELLS)) {
    for (const [cell, rows] of Object.entries(book)) {
      const dots = [];
      rows.forEach((row, y) => [...row].forEach((ch, x) => { if (ch !== '.') dots.push([x, y, ch, row.length]); }));
      sheets.push({ vendor, cell, dots });
    }
  }
  const out = {};
  for (const a of window.__office.debug.roomActors()) {
    if (a.leaving || !sessions.includes(a.session)) continue;
    // 挨拶の跳ね・立ち上がり（a.lift）は描く y だけを上げる（R110-S4）
    const d = ctx.getImageData(a.x * S, (a.y - (a.lift || 0)) * S, PX_CELL_W * S, PX_CELL_H * S).data;
    const cands = [];
    for (const tint of SESSION_COLORS) {
      const ink = { ...PALETTE, a: tint, c: dimTint(tint) };
      const want = Object.fromEntries(Object.entries(ink).map(([k, v]) => [k, rgb(v)]));
      for (const { vendor, cell, dots } of sheets) {
        for (const flip of [false, true]) {
          let miss = 0, n = 0;
          for (const [x, y, ch, w] of dots) {
            const c = want[ch];
            if (!c) continue;
            n += 1;
            const i = (y * S * PX_CELL_W * S + (flip ? w - 1 - x : x) * S) * 4;
            if (d[i] !== c[0] || d[i + 1] !== c[1] || d[i + 2] !== c[2]) miss += 1;
          }
          cands.push({ vendor, cell, flip, tint, miss, dots: n });
        }
      }
    }
    const min = Math.min(...cands.map((k) => k.miss));
    const best = cands.filter((k) => k.miss === min);
    out[a.session] = { min, dots: best[0].dots, cells: [...new Set(best.map((k) => k.cell))],
      vendors: [...new Set(best.map((k) => k.vendor))], tints: [...new Set(best.map((k) => k.tint))] };
  }
  return out;
}"""
# 頭上の印のタイルが画素で 1 ドットも欠けずに見えているか（印＝roomMarks の markAt・タイルの凡例の色で。
# ❗ だけは確認待ちの色＝台帳の ❗ と同じ UI_COLORS.alert で塗る決まり: 's' のドットは alert、ほかは凡例か RAMP.signal）
MARK_PIXELS = """async () => {
  const { PX_TILES } = await import('/ui/cockpit/tiles.js');
  const { TILE_PALETTE, RAMP, UI_COLORS } = await import('/ui/cockpit/palette.js');
  const S = window.__office.stats().scale, c = document.querySelector('.cproom'), ctx = c.getContext('2d');
  const lo = (h) => String(h).toLowerCase();
  const signal = RAMP.signal.map(lo);
  return window.__office.debug.roomMarks().filter((m) => m.markAt).map((m) => {
    const { tile, x, y } = m.markAt;
    const rows = PX_TILES[tile];
    if (!rows) return { key: m.key, session: m.session, mark: m.mark, tile, miss: -1, dots: 0 };
    const w = Math.max(...rows.map((r) => r.length));
    const d = ctx.getImageData(x * S, y * S, w * S, rows.length * S).data;
    let miss = 0, dots = 0;
    rows.forEach((row, ry) => [...row].forEach((ch, rx) => {
      const base = TILE_PALETTE[ch];
      if (!base) return;
      dots += 1;
      const i = (ry * S * w * S + rx * S) * 4;
      const got = '#' + [d[i], d[i + 1], d[i + 2]].map((v) => v.toString(16).padStart(2, '0')).join('');
      const ok = m.mark === 'call' ? (ch === 's' ? got === lo(UI_COLORS.alert) : got === lo(base) || signal.includes(got))
        : got === lo(base);
      if (!ok) miss += 1;
    }));
    return { key: m.key, session: m.session, mark: m.mark, tile, miss, dots };
  });
}"""
# 19 の 💬 の境: sessions の人の頭上（💬 を出すなら置く場所＝pxtop.topMarkAt）に 💬 のタイルのドットが凡例の色で
# 何個見えているか（hit）。全部（dots）見えていたら 💬 が貼られている＝roomMarks の申告（mark）だけで「出ていない」を決めない
WAIT_PIXELS = """async (sessions) => {
  const { PX_TILES } = await import('/ui/cockpit/tiles.js');
  const { TILE_PALETTE } = await import('/ui/cockpit/palette.js');
  const { topMarkAt } = await import('/ui/core/pxtop.js');
  const S = window.__office.stats().scale, ctx = document.querySelector('.cproom').getContext('2d');
  const lo = (h) => String(h).toLowerCase();
  return window.__office.debug.roomMarks().filter((m) => sessions.includes(m.session)).map((m) => {
    const at = topMarkAt({ x: m.x, y: m.y }, 'wait');
    const rows = at ? PX_TILES[at.tile] : null;
    const out = { session: m.session, mark: m.mark, missing: m.missing, zone: m.zone, tile: at && at.tile, dots: 0, hit: 0 };
    if (!rows) return out;
    const w = Math.max(...rows.map((r) => r.length));
    const d = ctx.getImageData(at.x * S, at.y * S, w * S, rows.length * S).data;
    rows.forEach((row, ry) => [...row].forEach((ch, rx) => {
      if (!TILE_PALETTE[ch]) return;
      out.dots += 1;
      const i = (ry * S * w * S + rx * S) * 4;
      const got = '#' + [d[i], d[i + 1], d[i + 2]].map((v) => v.toString(16).padStart(2, '0')).join('');
      if (got === lo(TILE_PALETTE[ch])) out.hit += 1;
    }));
    return out;
  });
}"""
# 間取り（正本 ui/core/pxroom.js の家具の足元・扉・ロボの足、pxwalk の壁と枠）。経路の門の材料
GEO = """async () => {
  const P = await import('/ui/core/pxroom.js');
  const W = await import('/ui/core/pxwalk.js');
  return { foot: [...P.ROBOT_FOOT], door: { ...P.DOOR }, walls: W.WALLS.map((r) => [...r]),
    rows: P.FURNITURE.map((f) => ({ id: f.id, layer: f.layer, rects: P.footRects(f).map((r) => [...r]) })),
    spots: Object.fromEntries(Object.entries(W.roomGraph().spots)
      .map(([k, s]) => [k, { foot: [...s.foot], at: [...s.at] }])) };
}"""
PLAN = "async ([a, b]) => (await import('/ui/core/pxwalk.js')).planPath(a, b)"
# 時計を t へ進めて（t が null なら進めずに）1 フレームを読む（sprites＝そのフレームで貼ったスプライトの鍵・SPRITE_KEYS）
STEP = """(t) => { const d = window.__office.debug;
  if (t !== null) d.step(t);
  const st = window.__office.stats();
  return { t: window.__office.t(), fps: st.fps, walking: st.walking, entering: st.entering, leaving: st.leaving,
    unreachable: st.unreachable, missingCells: st.missingCells, missingTiles: st.missingTiles, marks: st.marks,
    spriteBakes: st.spriteBakes, sprites: (""" + SPRITE_KEYS + """)(),
    people: d.roomMarks().map((m) => ({ key: m.key, session: m.session, x: m.x, y: m.y, zone: m.zone, cell: m.cell,
      kind: m.kind, dir: m.dir, mirror: m.mirror, mark: m.mark, markAt: m.markAt, walk: Boolean(m.walk),
      entering: m.entering, leaving: m.leaving, missing: m.missing })) }; }"""
# 重なり順（25）: key の人の貼った画素を、控えのコマ（vendor・cell・mirror）と個体色（タブの色）で突き合わせる。
# 手前の層で足元を持つ家具（天板＝机・カウンター・ソファの座面・会議卓）のタイルのドットと重なる人のドットのうち、
# 画素が人の色でないもの（hidden）を数える。前後は家具の足元の下端と人の足元の y だけで決める（段の実装は読まない）。
# 他の人の矩形（影の行を含む）・全員の印・選択の角かっこの窓に落ちるドットは数えない（天板以外が被る場所）。
FRONT_CHECK = """async ([key, tint]) => {
  const { PX_CELLS } = await import('/ui/cockpit/cells.js');
  const { PX_TILES } = await import('/ui/cockpit/tiles.js');
  const { FURNITURE, ROBOT_FOOT, footRects } = await import('/ui/core/pxroom.js');
  const { PALETTE, TILE_PALETTE, dimTint } = await import('/ui/cockpit/palette.js');
  const d = window.__office.debug, S = window.__office.stats().scale;
  const all = d.roomMarks(), me = all.find((m) => m.key === key);
  if (!me || me.missing) return null;
  const rows = PX_CELLS[me.vendor][me.cell];
  const ink = { ...PALETTE, a: tint, c: dimTint(tint) };
  const c = document.querySelector('.cproom');
  const img = c.getContext('2d').getImageData(0, 0, c.width, c.height).data, W = c.width;
  const hex = (x, y) => { const i = (y * S * W + x * S) * 4;
    return '#' + [img[i], img[i + 1], img[i + 2]].map((v) => v.toString(16).padStart(2, '0')).join(''); };
  const sel = d.roomMotion().selected;
  const block = [];
  for (const m of all) {
    if (m.markAt) block.push([m.markAt.x, m.markAt.y, m.markAt.w, m.markAt.h]);
    if (m.key !== key && !m.missing) block.push([m.x, m.y, 12, 17]);
    if (sel && m.session === sel && !m.leaving) block.push([m.x - 2, m.y - 3, 16, 3]);
  }
  const blocked = (x, y) => block.some(([bx, by, bw, bh]) => x >= bx && x < bx + bw && y >= by && y < by + bh);
  const dots = [];
  rows.forEach((row, y) => [...row].forEach((ch, x) => {
    if (!ink[ch]) return;
    const X = me.x + (me.mirror ? row.length - 1 - x : x), Y = me.y + y;
    if (!blocked(X, Y)) dots.push([X, Y, String(ink[ch]).toLowerCase()]);
  }));
  const footY = me.y + ROBOT_FOOT[1] + ROBOT_FOOT[3] / 2;
  const tops = [];
  for (const f of FURNITURE) {
    if (f.layer !== 'front' || !footRects(f).length) continue;
    const t = PX_TILES[f.tile];
    if (!t) continue;
    const over = dots.filter(([X, Y]) => { const ry = Y - f.y, rx = X - f.x;
      return ry >= 0 && ry < t.length && rx >= 0 && rx < t[ry].length && TILE_PALETTE[t[ry][rx]]; });
    if (!over.length) continue;
    tops.push({ id: f.id, bottom: Math.max(...footRects(f).map((r) => r[1] + r[3])), over: over.length,
      hidden: over.filter(([X, Y, want]) => hex(X, Y) !== want).length });
  }
  return { footY, x: me.x, y: me.y, cell: me.cell, dots: dots.length, tops };
}"""
# 「席 n/9 · 受付 m · 外 k」（受付・外は 0 のとき出ない）
SEATS_TEXT = re.compile(r"(\d+)\s*/\s*(\d+)(?:\D+(\d+))?(?:\D+(\d+))?")


# 29b の材料: 色付きの左右の帯（デザイン方針）。コメントを除いた CSS の宣言を読む（コメントの中の綴りは数えない）
SIDE_NEUTRAL = {"var(--cp-line)", "var(--cp-line-soft)", "var(--iso-line)", "var(--iso-hairline)", "transparent"}
SIDE_STYLES = {"none", "hidden", "solid", "dashed", "dotted", "double", "groove", "ridge", "inset", "outset",
               "initial", "inherit", "unset", "revert"}
SIDE_WIDTHS = {"thin": 1, "medium": 3, "thick": 5}


def _css_px(tok):
    """長さの字句を CSS px に（em/rem は 16 倍・長さでなければ None）"""
    if tok in SIDE_WIDTHS:
        return SIDE_WIDTHS[tok]
    m = re.fullmatch(r"(-?[\d.]+)(px|em|rem)?", tok)
    if not m:
        return None
    v = float(m.group(1))
    return v * 16 if m.group(2) in ("em", "rem") else v


def _css_tokens(val):
    return re.findall(r"[a-z-]+\([^)]*\)|[^\s]+", val.lower())


def _side_says(val):
    """左右 1 辺ぶんの値（border-left の一括・-width・-color）が帯か。帯なら理由、違えば空"""
    toks = _css_tokens(val)
    widths = [w for w in (_css_px(t) for t in toks) if w is not None]
    colors = [t for t in toks if _css_px(t) is None and t not in SIDE_STYLES]
    if "none" in toks or "hidden" in toks or (widths and max(widths) == 0):
        return ""
    if widths and max(widths) >= 2:
        return f"太さ {max(widths):g}px"
    bad = [c for c in colors if c not in SIDE_NEUTRAL]
    return f"色 {bad[0]}" if bad else ""


def _four(val):
    """border-width / border-color の 1〜4 値を [上, 右, 下, 左] へ"""
    t = _css_tokens(val) or [""]
    if len(t) == 1:
        return t * 4
    if len(t) == 2:
        return [t[0], t[1], t[0], t[1]]
    if len(t) == 3:
        return [t[0], t[1], t[2], t[1]]
    return t[:4]


def side_bands(css, where):
    """色付きの左右の帯を置いている宣言の一覧（[where] セレクタ: 理由）"""
    body = re.sub(r"/\*[\s\S]*?\*/", "", css)
    out = []
    for m in re.finditer(r"([^{};]+)\{([^{}]*)\}", body):
        sel = " ".join(m.group(1).split())
        decls = {}
        for d in m.group(2).split(";"):
            if ":" in d:
                k, v = d.split(":", 1)
                decls[k.strip().lower()] = " ".join(v.replace("!important", "").split())
        why = []
        for k, v in decls.items():
            if re.fullmatch(r"border-(left|right|inline-start|inline-end|inline)(-width|-color)?", k):
                said = _side_says(v)
                if said:
                    why.append(f"{k}: {v}（{said}）")
            elif k in ("border-width", "border-color"):
                t, r, _, l = _four(v)
                for side, x in (("右", r), ("左", l)):
                    said = _side_says(x) if x != t else ""
                    if said:
                        why.append(f"{k}: {v}（{side}だけ {said}）")
            elif k == "box-shadow":
                for one in re.split(r",(?![^(]*\))", v):
                    if "inset" not in one.split():
                        continue
                    lens = [x for x in (_css_px(t) for t in _css_tokens(re.sub(r"[a-z-]+\([^)]*\)", " ", one))
                                        if t != "inset") if x is not None]
                    if lens and lens[0] != 0:
                        why.append(f"box-shadow: {one.strip()}（inset の x {lens[0]:g}px＝{'左' if lens[0] > 0 else '右'}の帯）")
            elif k in ("background", "background-image") and re.search(
                    r"linear-gradient\(\s*(to\s+(left|right)|-?(90|270)deg)", v):
                why.append(f"{k}: {v[:60]}（横向きのグラデーション）")
        if re.search(r"::?(before|after)", sel):
            w = _css_px(decls.get("width", "").lower())
            paint = [decls.get(k, "") for k in ("background", "background-color") if decls.get(k)]
            if w is not None and 0 < w <= 6 and any(x.lower() not in ("none", "transparent") for x in paint):
                why.append(f"幅 {w:g}px の色つきの棒（{'・'.join(paint)}）")
        out += [f"[{where}] {sel}: {x}" for x in why]
    return out


def js_side_styles(js, where):
    """ui/cockpit の JS が左右の罫・影を style で差す口（コメントを除く）"""
    body = re.sub(r"/\*[\s\S]*?\*/", "", js)
    body = re.sub(r"(?m)^\s*//.*$|(?<=[;{}),\s])//[^\n]*", "", body)
    pat = r"border(Left|Right|InlineStart|InlineEnd)\w*|border-(left|right|inline)[\w-]*\s*[:\"']|boxShadow|box-shadow"
    return [f"[{where}] {m.group(0)}" for m in re.finditer(pat, body)]


# 29a の材料: 文字列の段。R105-A の画面の幅の段は 900 未満・900〜1400・1400 超、
# コンテナ問い合わせ（@container）はスマホの段の cphead と cpledger の (max-width: 600px) だけ（文字列だけを見る＝
# 境が画面 600 の 1 本かは問い合わせ先の箱で決まる＝形状の門 ⑭ が実描画で見る）
ONE_COL = "(width < 900px)"
MEDIA_OK = (ONE_COL, "(width >= 900px) and (width <= 1400px)", "(width > 1400px)",
            "(max-width: 899px)", "(min-width: 900px) and (max-width: 1400px)",
            "(min-width: 900px)", "not all and (max-width: 1400px)")
# R110-D1: 2 カラムの問い合わせ 2 つ（幅の段は増やさない＝@media の width は 3 つのまま）。cproster＝名簿の窓が低い間は
# 列見出しを畳み・いまを 1 行に詰め・「▾ あと N 件」を見出しへ上げる（丈）／cpsec＝部屋と名簿の見出しが 400px 以下なら数の要約
# （足元・ヘッダーと同じ数）を畳んで見出しを 1 段に保つ（幅）
CONTAINER_OK = ("cphead (max-width: 600px)", "cpledger (max-width: 600px)",
                "cproster (max-height: 18.2em)", "cpsec (max-width: 400px)")


def width_stages(css):
    """コメントを除いた CSS の幅の段 → (@media の width の条件, @container の条件)。空白は 1 つに詰め、「:」の前後は
    「: 」に揃える（書き方の揺れで赤にしない）。コメントを先に消す（注記に「@media (min-width: 1181px) {」と書いただけで
    段に数えた＝注記の言い換えを強いた）"""
    body = re.sub(r"/\*[\s\S]*?\*/", "", css)
    norm = lambda q: " ".join(re.sub(r"\s*:\s*", ": ", q).split())  # noqa: E731
    media = [norm(q) for q in re.findall(r"@media([^{]+)\{", body) if "width" in q]
    containers = [norm(q) for q in re.findall(r"@container([^{]+)\{", body)]
    return media, containers


def head_window(a, x, y):
    """選択の印（頭上の角かっこ）が乗ってよい窓: 人の左上 (x, y) から x-2..x+13・y-3..y-1（論理 px）"""
    return a["x"] - 2 <= x <= a["x"] + 13 and a["y"] - 3 <= y <= a["y"] - 1


def foot_window(a, x, y):
    """ホバーの印（足元の線）が乗ってよい窓: 人の左上 (x, y) から x-1..x+12・y+15..y+17（論理 px）"""
    return a["x"] - 1 <= x <= a["x"] + 12 and a["y"] + 15 <= y <= a["y"] + 17


def marks_on(diff, people, window):
    """変わった画素が全部 people の window に入り、全員に 1 画素以上あるか → (ok, 窓の外, 人ごとの画素数)"""
    stray = [(x, y) for x, y in diff if not any(window(a, x, y) for a in people)]
    per = [sum(1 for x, y in diff if window(a, x, y)) for a in people]
    return bool(people) and bool(diff) and not stray and all(n >= 1 for n in per), stray, per


def copy(world):
    return json.loads(json.dumps(world))


def with_attention(world, session):
    """session を確認待ちにした world の写し（roster の代表・roster[].sessions の要約・employees の 3 か所）"""
    w = copy(world)
    for r in [*w["roster"], *w.get("employees", [])]:
        if r.get("session") == session:
            r.update(question="スモーク: 確認待ちにする", attention=True)
        for brief in r.get("sessions") or []:
            if isinstance(brief, dict) and brief.get("session") == session:
                brief["attention"] = True
    return w


def with_fields(world, session, **fields):
    """session の状態を書き換えた world の写し（roster の代表・要約・employees の 3 か所）"""
    w = copy(world)
    for r in [*w["roster"], *w.get("employees", [])]:
        if r.get("session") == session:
            r.update(fields)
        for brief in r.get("sessions") or []:
            if isinstance(brief, dict) and brief.get("session") == session:
                brief.update({k: v for k, v in fields.items() if k in ("state", "age", "attention", "minions")})
    return w


def with_member(world, sid, name, cwd=None, **fields):
    """1 人だけのプロジェクト（セッション sid）を足した world の写し"""
    w = copy(world)
    cwd = cwd or f"/Users/test/{sid}"
    body = {"state": "working", "kind": "tool", "verb": "編集中", "age": 5, **fields}
    w["roster"].append({"projectId": f"smoke-{sid}", "session": sid, "name": name, "cwd": cwd, "vendor": "claude",
                        "crew": 1, **body, "sessions": [{"session": sid, "state": body["state"]}]})
    w.setdefault("employees", []).append({"session": sid, "dept": name, "cwd": cwd, "vendor": "claude", **body})
    return w


def expected_marks(world):
    """頭上の印が付くべき人数を fixture の roster から数える。**実装（pxtop.roomMark）の定数・関数は読まない**
    （ここに条件を書き下ろす）。ただし条件の出どころは 3 種類あり、仕様の文言だけでは決まらない所は統括の既定と
    Core の解釈で埋めている＝この期待値は「今の既定での正解」。本人が既定・解釈を覆したら、ここも一緒に直す。
    固定時刻（?t=）では ✓ は出ない。
      仕様の文言（wave2-plan D-1・gap.md D-1・.claude/rules/ui-cockpit.md の「場所の意味」）:
        ❗ call   確認待ち（attention）＝受付に立って呼んでいる
        ⚠ warn   stuckTool が在る（wave2-plan）／kind tool かつ waiting＝ツールが止まっている（gap.md）
        💬 wait   報告して返事待ち（waiting かつ kind said）
        💭 think  考えている（kind think）
      統括の既定（本人が覆せる・裁定一覧に載せる）:
        ⚠ は上の 2 つの**和**（stuckTool だけだと、stuckTool を持つ人は全員 ❗ が勝つ＝本番で 0 件）
        💬 は報告から WAIT_MAX_AGE（600）秒未満だけ
        優先は ❗ > ⚠ > ✓ > 💬 > 💭（1 人に 1 つ）
      Core の解釈（本人の裁定待ち・wave2core の【裁定の確認】(a)）:
        💭 は休憩中（state resting）には出さない（仕様の文言は kind think だけ）
    💬 の 600 秒の境は cockpit.json の sess-cp-13（報告して 1200 秒待っている人）が踏む＝上限を上げると 💬 が 1 つ増えて
    赤（門 19 はその人に 💬 が出ていないことも名指しで見る: wait_over）。あふれて受付に立つ standby の人を ❗ にしない掟
    （ui-cockpit.md）は、この fixture の standby に確認待ちが居ないので数えに入れていない（部屋の外＝overflow は 0）。
    roster は server の group_by_project(mode="session") の出力＝1 行 1 セッション（crew 1）であることを前提にする。"""
    n = {"call": 0, "warn": 0, "wait": 0, "think": 0}
    for r in world["roster"]:
        assert len(r.get("sessions") or [r]) == 1, f"1 行 1 セッションでない: {r.get('session')}"
        state, kind = r.get("state"), r.get("kind")
        if r.get("attention"):
            n["call"] += 1
        elif str(r.get("stuckTool") or "").strip() or (state == "waiting" and kind == "tool"):
            n["warn"] += 1
        elif state == "waiting" and kind == "said" and 0 <= float(r.get("age", -1)) < WAIT_MAX_AGE:
            n["wait"] += 1
        elif kind == "think" and state != "resting":
            n["think"] += 1
    return n


def wait_over(world):
    """報告して返事を待っている（waiting かつ kind said）のに、報告から WAIT_MAX_AGE 秒以上たった人＝💬 を出さない人。
    ❗・⚠ が勝つ人（attention・stuckTool）は除く（その人に 💬 が無いのは 600 秒の境のせいではない）。expected_marks と同じく
    fixture の roster から数える（実装の定数は読まない）"""
    return sorted(r["session"] for r in world["roster"]
                  if r.get("state") == "waiting" and r.get("kind") == "said" and not r.get("attention")
                  and not str(r.get("stuckTool") or "").strip() and float(r.get("age", -1)) >= WAIT_MAX_AGE)


# ── 経路の門（23・24）───────────────────────────────────────────────────
def foot_rect(geo, x, y):
    fx, fy, fw, fh = geo["foot"]
    return (x + fx, y + fy, fw, fh)


def foot_center(geo, x, y):
    fx, fy, fw, fh = geo["foot"]
    return (x + fx + fw / 2, y + fy + fh / 2)


def overlaps(a, b):
    """矩形 [x, y, w, h] どうしが面積で重なるか（辺が接するだけなら重ならない）"""
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def at_door(geo, x, y):
    """左上 (x, y) の人の足元の中心が扉（pxroom の DOOR の矩形）の上か（入場の最初の位置・止めている間に来た人が
    扉に居ないこと）。退場の最後の位置はこれで見ない（扉へ入る手前で消える＝door_reached）"""
    cx, cy = foot_center(geo, x, y)
    d = geo["door"]
    return d["x"] <= cx < d["x"] + d["w"] and d["y"] <= cy < d["y"] + d["h"]


def spot_furniture(geo, x, y):
    """左上 (x, y) に立つ枠（足元の中心が一致）の家具（座る机と椅子・カウンター等）。枠でなければ空"""
    cx, cy = foot_center(geo, x, y)
    for s in geo["spots"].values():
        if abs(s["foot"][0] - cx) < 0.01 and abs(s["foot"][1] - cy) < 0.01:
            return set(s["at"])
    return set()


def segments_at(path, x, y):
    """折れ線 path（左上の点の列・区間は x か y に平行）のうち (x, y) を含む区間の添字（角なら 2 つ）"""
    out = set()
    for i in range(len(path) - 1):
        (ax, ay), (bx, by) = path[i], path[i + 1]
        if min(ax, bx) <= x <= max(ax, bx) and min(ay, by) <= y <= max(ay, by) and (ax == bx or ay == by):
            out.add(i)
    return out


def door_reached(path, p, door):
    """退場の最後に描かれた位置 p（左上）が、扉の点 door（入場で人が現れた左上）へ入る最後の区間の上で、door から
    1 刻みの上限 MAX_STEP 以内か。問題の文言（空＝緑）。
    許容の出どころ: 歩く人は 1 刻み（1/12 秒）に 2px 進み、扉に着いた刻みで消える＝最後に描かれる位置は経路の長さを
    2 で割った余りで決まり、扉の 1〜2px 手前（余り 0 なら 2px 手前）。座る机・通路・fixture が変わっても余りが変わる
    だけなので、この許容なら不具合が無い限り赤にならない（扉の上の 1 行だけを許すと、余り 1 の机でしか通らない）。"""
    if not path or len(path) < 2 or p is None or door is None:
        return f"経路・最後の位置・扉の点のどれかが無い（経路 {path}・最後 {p}・扉 {door}）"
    if tuple(path[-1]) != tuple(door):
        return f"経路が扉の点で終わらない（{path[-1]} ≠ {door}）"
    if len(path) - 2 not in segments_at(path, p["x"], p["y"]):
        return f"最後の位置 ({p['x']},{p['y']}) が扉へ入る最後の区間 {path[-2]}→{path[-1]} の上に無い"
    d = abs(p["x"] - door[0]) + abs(p["y"] - door[1])
    if d > MAX_STEP:
        return f"最後の位置 ({p['x']},{p['y']}) が扉の点 {tuple(door)} から {d}px（1 刻みの上限 {MAX_STEP}px より遠い＝扉の手前で消えた）"
    return ""


def seg_dir(a, b):
    """区間の主軸の向き（S 手前へ・N 奥へ・E 右へ・W 左へ）"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx) >= abs(dy):
        return "E" if dx >= 0 else "W"
    return "S" if dy >= 0 else "N"


def walk_problems(traj, path, geo, exempt):
    """1/12 秒ごとの位置の列 traj が、経路 path に沿って歩いているか。問題の文言の列（空＝緑）"""
    bad = []
    if not path or len(path) < 2:
        return [f"経路が引けない（planPath={path}）"]
    off = [(k, p["x"], p["y"]) for k, p in enumerate(traj) if not segments_at(path, p["x"], p["y"])]
    if off:
        bad.append(f"経路の外に居た {off[:4]}（経路 {path}）")
    jumps = [(k, (traj[k - 1]["x"], traj[k - 1]["y"]), (p["x"], p["y"])) for k, p in enumerate(traj)
             if k and abs(p["x"] - traj[k - 1]["x"]) + abs(p["y"] - traj[k - 1]["y"]) > MAX_STEP]
    if jumps:
        bad.append(f"1 刻みで {MAX_STEP}px より動いた（瞬間移動）{jumps[:3]}")
    hits = []
    for k, p in enumerate(traj):
        fr = foot_rect(geo, p["x"], p["y"])
        hits += [(k, row["id"]) for row in geo["rows"] if row["id"] not in exempt
                 for r in row["rects"] if overlaps(fr, r)]
        hits += [(k, f"壁#{i}") for i, r in enumerate(geo["walls"]) if overlaps(fr, r)]
    if hits:
        bad.append(f"足が家具の足元か壁に重なった {sorted(set(hits))[:6]}（除外 {sorted(exempt)}）")
    wrong, prev = [], None
    for k, p in enumerate(traj):
        if not p["walk"]:
            prev = None
            continue
        want = {seg_dir(path[i], path[i + 1]) for i in segments_at(path, p["x"], p["y"])}
        side = "E" if p["dir"] in ("E", "W") else p["dir"]
        if p["dir"] not in want or p["mirror"] != (p["dir"] == "W") or not p["cell"].startswith("walk" + side):
            wrong.append((k, p["x"], p["y"], p["dir"], sorted(want), p["cell"], p["mirror"]))
        n = int(p["cell"][-1]) if p["cell"][-1:].isdigit() else -1
        if prev is not None and n != (prev + 1) % 4:
            wrong.append((k, f"歩行コマ {prev}→{n}"))
        prev = n
    if wrong:
        bad.append(f"向き・鏡・歩行コマが区間と合わない {wrong[:4]}")
    return bad


def person(frame, key):
    return next((p for p in frame["people"] if p["key"] == key), None)


def open_live(browser, port, world, errors, label, reduced=False, life=False):
    """live ページ（?t= 無し）を開く。/api/office は body[0] を返す（ポーリングが注入した世界を実データへ巻き戻さない）。
    散歩（R99-P4-L2・ui/core/life.js）は既定で切る（&life=0）＝「他の人は動かない」「席が安定」「6fps の窓」を前提にする
    検査が、返事を待つ人の散歩（開いて 30 秒後から）で揺れない。散歩そのものは check_life が life=True で見る"""
    body = [json.dumps(world, ensure_ascii=False)]
    page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1, reduced_motion="reduce" if reduced else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror({label}): {e}"))
    page.on("console", lambda m: errors.append(f"console.error({label}): {m.text}") if m.type == "error" else None)
    page.route("**/api/office*", lambda route: route.fulfill(
        status=200, content_type="application/json; charset=utf-8", body=body[0]))
    page.add_init_script(ROOM_CALL_COUNTER)
    # networkidle は使わない（live は /api/events を張りっぱなし＝idle にならない）
    page.goto(f"http://127.0.0.1:{port}/?ui=cockpit" + ("" if life else "&life=0"), wait_until="domcontentloaded")
    page.bring_to_front()                          # 裏のページは rAF が回らない（描画の刻みを測れない）
    page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
    page.wait_for_selector(".cproom", timeout=20000)

    def inject(w, frames=1):
        body[0] = json.dumps(w, ensure_ascii=False)
        page.evaluate("w => window.__office.inject(w)", w)
        if frames:
            page.evaluate(ROOM_FRAMES, frames)

    inject(world, 2)
    return page, inject


def settle(page, timeout=30000):
    """誰も歩かなくなるまで待つ（入退場は実時間で歩く）。待ちきれなければ False（呼び手が赤にする）"""
    try:
        page.wait_for_function("() => window.__office.stats().walking === 0", timeout=timeout, polling=100)
    except Exception:                                  # noqa: BLE001 - playwright の TimeoutError
        return False
    page.evaluate(ROOM_FRAMES, 1)
    return True


def check_opened(page, label, check):
    """20: 開いた瞬間の全員は扉から入らない（最初に描いた部屋に人が居て、entering・walking が一度も立たない）"""
    page.evaluate(ROOM_FRAMES, 4)
    c = page.evaluate("() => { const c = window.__roomCalls; return { first: c.first, enterMax: c.enterMax,"
                      " walkMax: c.walkMax, frames: c.frames }; }")
    first = c["first"] or {}
    check(first.get("actors", 0) > 0 and c["enterMax"] == 0 and c["walkMax"] == 0,
          f"開いた瞬間の全員は扉から入らない（{label}: 最初のフレームに {first.get('actors')} 人・"
          f"{c['frames']} フレームで entering・walking の最大 {c['enterMax']}・{c['walkMax']}）",
          json.dumps(c, ensure_ascii=False))


def check_live_room(browser, port, world, check, errors):
    """10〜14・16・17・20〜22: live ページ（刻みで描く）でしか確かめられない部屋の門。"""
    xl22 = json.loads((FIXTURES / "xl22.json").read_text(encoding="utf-8"))
    peak_path = FIXTURES / "cockpit_peak.json"
    live, inject = open_live(browser, port, world, errors, "live")

    def seats_match(label, need_outside=False):
        m = live.evaluate("""() => ({
          foot: document.querySelector('#cp-foot-seats').textContent,
          office: document.querySelector('#cp-office-meta').textContent,
          term: document.querySelector('#cp-term-meta').textContent,
          placed: window.__office.stats().placed,
          zones: window.__office.debug.roomActors().filter(a => !a.leaving).map(a => [a.session, a.zone]) })""")
        outside = live.evaluate(LAYOUT_OVERFLOW)
        p = m["placed"] or {}
        drawn = {z: sum(1 for _, x in m["zones"] if x == z) for z in ("desk", "queue", "standby")}
        # 数えた人は本当に貼られている: 画素から当て直すと色が 1 色に決まり、インクの半分以上が合う
        #（roomActors に行があるのに put していない＝数だけ合う嘘を捕まえる）
        counted = [sid for sid, z in m["zones"] if z in drawn]
        px = live.evaluate(ROBOT_PIXELS, counted)
        unseen = [sid for sid in counted
                  if not (sid in px and len(px[sid]["tints"]) == 1 and px[sid]["min"] * 2 < px[sid]["dots"])]
        g = SEATS_TEXT.search(m["foot"] or "")
        got = tuple(None if x is None else int(x) for x in g.groups()) if g else None
        # 席 n と受付 m は部屋に**描いた**人（roomActors＝1 体貼るごとに 1 行・退場中は除く）を区分で数えた数、外 k は
        # roomLayout を自分で呼んだ overflow。フッターが読む placed（実装の申告）は期待値に使わず、描いた人数と突き合わせるだけ。
        # 0 のときは書かない（None）＝「外 0」「受付 0」を出したら赤
        want = (drawn["desk"], 9, (drawn["queue"] + drawn["standby"]) or None, outside or None)
        told = {z: p.get(z, 0) for z in drawn}
        # b4-mac: 席の数は足元と 01 の見出しの 2 か所（03 端末の見出しは端末の本数＝席を書かない）
        check(got == want and told == drawn and not unseen and m["foot"] == m["office"] and m["term"] != m["foot"]
              and (outside >= 1 or not need_outside),
              f"フッターの席・受付・外が部屋に描いた人数と一致（{label}: {m['foot']}・数えた {len(counted)} 人を画素で確かめる）",
              f"フッター {got} / 期待 {want}（描いた {drawn}・placed の申告 {told}・roomLayout の overflow={outside}）/ "
              f"数えたのに画素に居ない {unseen[:4]} / "
              f"足元と 01 {m['foot']!r} {m['office']!r}・03 端末 {m['term']!r}"
              + ("／この world は部屋からあふれていない（外 k を試せない）" if need_outside and outside < 1 else ""))

    def calls_match(label):
        r = live.evaluate(MEASURE)
        st = r["st"]
        check(r["ext"] == st["drawCalls"] and 0 < st["drawCalls"] <= DRAW_CALL_LIMIT
              and st["missingCells"] == 0 and st["missingTiles"] == 0 and st["unreachable"] == 0,
              f"drawCalls は実測（{label}: 外から数えて {r['ext']} = stats {st['drawCalls']}・上限 {DRAW_CALL_LIMIT}）・"
              "描けないコマ／タイル・届かない歩行 0",
              f"外 {r['ext']} / stats {st['drawCalls']} / 上限 {DRAW_CALL_LIMIT} / missingCells {st['missingCells']}"
              f" / missingTiles {st['missingTiles']} / unreachable {st['unreachable']}")
        return st

    dots = live.evaluate(SPRITE_DOTS)

    def bake_window(b0, b1, label, injects=1):
        """21（C3 の裁定）: 歩いている窓（12fps）でも手前の段を焼き直さない。b0・b1 は窓の前後の BAKES。
        ① fgBakes の増分 ≤ 窓の中で在席の集合が変わりうる回数（inject の回数。歩いている間に集合は変わらない＝
           目的地は roomLayout が決める）・背景は焼き直さない
        ② rAF の中で部屋以外（オフスクリーン）へ塗った数 ≤ スプライトの焼きの増分 × 1 枚の最大ドット数
           （それで説明できないオフスクリーンの塗り＝毎フレームの焼き直しは 0。drawCalls は部屋の canvas しか数えない）
        窓の中で誰かが実際に歩いたことも見る（歩かない窓なら 11 と同じ＝空振り）"""
        fg, sp, bg, off = (b1[k] - b0[k] for k in ("fg", "sprite", "bg", "off"))
        check(fg <= injects and bg == 0 and off <= sp * dots and b1["walked"] >= 1,
              f"歩いている窓でも手前の段を焼き直さない（{label}: fgBakes +{fg}＝在席の変化 {injects} 回まで・"
              f"部屋以外への塗り {off}＝スプライトの焼き +{sp} 枚 × {dots} ドットまで・歩いた最大 {b1['walked']} 人）",
              f"fgBakes +{fg}（上限 {injects}）/ bgBakes +{bg} / rAF の中で部屋以外へ塗った数 {off}"
              f"（説明できる上限 {sp * dots}）/ 歩いた最大 {b1['walked']}")

    def transition(w, label):
        """world を入れ替える（入退場の山）。1 フレームの最大 drawCalls が上限以下・誰かが実際に歩いた・全員が着く。
        山の間も手前の段を焼き直さない（bake_window）"""
        live.evaluate("() => { const c = window.__roomCalls; c.max = 0; c.walkMax = 0; }")
        b0 = live.evaluate(BAKES, False)
        inject(w)
        calm = settle(live)
        bake_window(b0, live.evaluate(BAKES, False), label)
        r = live.evaluate("() => ({ max: window.__roomCalls.max, walked: window.__roomCalls.walkMax,"
                          " st: window.__office.stats() })")
        st = r["st"]
        check(calm and 0 < r["max"] <= DRAW_CALL_LIMIT and r["walked"] >= 1 and st["unreachable"] == 0
              and st["missingCells"] == 0 and st["missingTiles"] == 0,
              f"入退場の山でも 1 フレームの drawCalls が上限以内（{label}: 最大 {r['max']}・同時に歩いた最大 {r['walked']} 人）",
              f"30 秒で歩き終わった {calm} / 最大 {r['max']} / 上限 {DRAW_CALL_LIMIT} / 歩いた {r['walked']} / "
              f"unreachable {st['unreachable']} / "
              f"missingCells {st['missingCells']} / missingTiles {st['missingTiles']}")

    def hover_diff(target):
        """target（行の要素）に乗る前後の差（論理 px）と、乗った／離れた後の hovered・離れた後の差"""
        live.evaluate(SNAP)
        before = live.evaluate("() => window.__office.stats().hovered")
        target.hover()
        live.evaluate(ROOM_FRAMES, 2)
        on = live.evaluate("() => window.__office.stats().hovered")
        diff = live.evaluate(DIFF)
        live.mouse.move(2, 2)
        live.evaluate(ROOM_FRAMES, 2)
        off = live.evaluate("() => window.__office.stats().hovered")
        back = live.evaluate(DIFF)
        return before, on, diff, off, back

    def drawn_of(sessions):
        return [a for a in live.evaluate(ACTORS) if a["session"] in sessions and not a.get("leaving")]

    def rate(sec):
        return live.evaluate(RATE, sec)

    try:
        # 20. 開いた瞬間の全員は扉から入らない
        check_opened(live, "live", check)
        # 10. 描画コストの実測（basic）
        calls_match("basic")
        # 11. 焼き置き: 背景は 1 回・手前もロボも 1 回以上は焼いている（0 件＝焼き置きを使っていない）。
        #     ポーズが一巡するまでは初めてのコマを焼くので、焼き直しの無い 12 フレーム（6fps で 2 秒）の窓を探し、
        #     その窓で部屋以外（オフスクリーン）へ塗った数が 0 であることを見る
        windows = []
        for _ in range(8):
            w = live.evaluate(BAKE_WINDOW, 12)
            windows.append(w)
            if not w["baked"]:
                break
        calm = windows[-1] if not windows[-1]["baked"] else None
        bakes = windows[-1]["bakes"]
        check(bakes["bgBakes"] == 1 and bakes["fgBakes"] >= 1 and bakes["spriteBakes"] >= 1
              and calm is not None and calm["frames"] > 0 and calm["off"] == 0,
              f"焼き置きが効いている（bgBakes=1・fgBakes {bakes['fgBakes']}・spriteBakes {bakes['spriteBakes']}・"
              f"焼き直しの無い {calm and calm['frames']} フレームでオフスクリーンへ塗った数 {calm and calm['off']}）",
              f"窓 {[{k: w[k] for k in ('frames', 'off', 'baked')} for w in windows]} / 累計 {bakes}")
        # 13. フッター（basic・外は 0 ＝出ない）
        seats_match("basic")

        # 21. 描画の刻み: 誰も歩いていない 6fps → 扉から 1 人入ると 12fps → 着くと 6fps（実測と stats().fps の両方）
        idle = rate(2.0)
        walker = "sess-smoke-fps"
        bk0 = live.evaluate(BAKES, True)
        inject(with_member(world, walker, "スモーク刻み"), 0)
        walking = rate(1.0)
        mid = calls_match("basic＋歩行中（足跡・入場）")
        calm = settle(live)
        bk1 = live.evaluate(BAKES, False)
        after = rate(1.5)
        # rAF 自体が遅いマシンでは刻みを出し切れない＝rAF の数を上限にして見る（どちらも出す）
        cap_i, cap_w = min(IDLE_FPS, idle["raf"]), min(WALK_FPS, walking["raf"])
        ok_idle = idle["fps"] == [IDLE_FPS] and 0.6 * cap_i <= idle["rate"] <= IDLE_FPS * 1.25
        ok_walk = (walking["fps"] == [WALK_FPS] and walking["walkMin"] >= 1 and mid["walking"] >= 1
                   and 0.75 * cap_w <= walking["rate"] <= WALK_FPS * 1.15 and walking["rate"] >= 1.4 * idle["rate"])
        ok_back = calm and after["fps"] == [IDLE_FPS] and after["rate"] <= IDLE_FPS * 1.25
        check(ok_idle and ok_walk and ok_back,
              f"描画の刻み: 誰も歩かない {idle['rate']:.1f}fps（stats {idle['fps']}）→ 歩いている間 {walking['rate']:.1f}fps"
              f"（stats {walking['fps']}）→ 着いたら {after['rate']:.1f}fps（stats {after['fps']}）",
              f"idle {idle} / 歩行 {walking} / 着いた後 {after}（rAF が毎秒 {idle['raf']:.0f}・{walking['raf']:.0f} 回＝"
              "少なすぎるならマシンの負荷を先に疑う・uptime）")
        bake_window(bk0, bk1, "basic＋1 人が扉から机へ歩く")
        bk2 = live.evaluate(BAKES, True)
        inject(world, 0)                                # 帰す（扉まで歩いて消える）
        check(settle(live), "帰したセッションが歩き終わる（30 秒以内）", "walking が 0 に戻らない")
        bake_window(bk2, live.evaluate(BAKES, False), "1 人が机から扉へ帰る")

        # ここから先は動きを止めて比べる（コマと歩行が変わらない＝差は操作の結果だけ）
        live.click("#cp-motion")
        live.evaluate(ROOM_FRAMES, 2)
        # 12. ホバー: 行に乗る前後の canvas を比べる。人数のいちばん多いプロジェクト行（×N）に乗る＝
        #     「線を描いた人数」が 1 でなく行の人数になるかまで見る
        rows = live.query_selector_all(".pxrow.proj")
        crews = [live.evaluate("r => { const t = r.querySelector('.scount').textContent;"
                               " return t ? Number(t.replace(/\\D/g, '')) : 1; }", r) for r in rows]
        crew = max(crews)
        row = rows[crews.index(crew)]
        group = row.get_attribute("data-group")
        # 行の人＝台帳のそのグループのセッション（部屋が印を付けた相手ではなく、表の側から引く）
        members = set(live.evaluate("k => (window.__office.debug.board().find(g => g.key === k)?.sessions || [])"
                                    ".map(s => s.session)", group))
        people = drawn_of(members)
        pre = live.evaluate(ROOM_PIXELS)
        before, on, diff, off, back = hover_diff(row)
        after_px = live.evaluate(ROOM_PIXELS)
        ok, stray, per = marks_on(diff, people, foot_window)
        heads = [(x, y) for x, y in diff if any(head_window(a, x, y) for a in people)]
        check(before == 0 and on == len(people) == crew and ok and not heads and off == 0 and not back
              and after_px == pre,
              f"ホバー: 行に乗ると {on} 体の足元に線（変わった {len(diff)} 画素が全部その人たちの足元・"
              f"1 人ずつ {per}・頭上は変わらない）・離れると元の画素に戻る",
              f"前 {before} / 乗る {on}（行の人数 {crew}・部屋に居る {len(people)}）/ 離れる {off} / "
              f"窓の外 {stray[:6]} / 頭上が変わった {heads[:4]} / 人ごと {per} / 離れた後の差 {len(back)}")

        # 12b. セッション行: 3 人のプロジェクトの 1 人を ❗ にして行を展開し、机に居る別の 1 人の行に乗る
        big = sorted(members)
        caller = next((s for s in big if (drawn_of({s}) or [{}])[0].get("zone") != "desk"), big[0])
        inject(with_attention(world, caller))
        sub = next((r for r in live.query_selector_all(".pxrow:not(.proj)")
                    if r.get_attribute("data-session") in members
                    and r.get_attribute("data-session") != caller
                    and (drawn_of({r.get_attribute("data-session")}) or [{}])[0].get("zone") == "desk"), None)
        if sub is None:
            check(False, "ホバー（セッション行）: その人の足元だけに線", f"展開したセッション行が無い（❗ にした {caller}）")
        else:
            who = sub.get_attribute("data-session")
            one = drawn_of({who})
            before, on, diff, off, back = hover_diff(sub)
            ok, stray, per = marks_on(diff, one, foot_window)
            check(before == 0 and on == 1 and ok and off == 0 and not back,
                  f"ホバー（セッション行）: {who} の足元だけに線（{len(diff)} 画素）・離れると元に戻る",
                  f"前 {before} / 乗る {on} / 離れる {off} / 窓の外 {stray[:6]} / 人ごと {per} / 離れた後の差 {len(back)}")
        inject(world)

        # 22. 選択: 端末のタブで別のセッションを選ぶ → その人の頭上に角かっこ（ホバーの線とは別の場所・形）
        sel0 = live.evaluate("() => window.__office.debug.selected()")
        here = {a["session"]: a for a in live.evaluate(ACTORS) if not a.get("leaving")}
        tabs = set(live.evaluate("() => [...document.querySelectorAll('.cptab[data-session]')].map(t => t.dataset.session)"))
        pick = next((s for s, a in sorted(here.items()) if s != sel0 and s in tabs and a["zone"] == "desk"), None)
        if pick is None:
            check(False, "選択: 端末で選んだ人の頭上に角かっこ", f"机に居てタブのある別の人が居ない（選択 {sel0}）")
        else:
            a1 = here[pick]
            live.evaluate(SNAP)
            live.click(f'.cptab[data-session="{pick}"]')
            live.evaluate(ROOM_FRAMES, 2)
            diff = live.evaluate(DIFF)
            motion = live.evaluate(MOTION)
            nsel = live.evaluate(STATS)["selected"]
            tint = live.evaluate(TAB_COLOR, pick)
            mine = [(x, y) for x, y in diff if head_window(a1, x, y)]
            cols = live.evaluate(COLORS_AT, [list(p) for p in mine])
            painted = [p for p, c in zip(mine, cols) if c == (tint or "").lower()]
            left = [(x, y) for x, y in painted if x <= a1["x"]]
            right = [(x, y) for x, y in painted if x >= a1["x"] + 11]
            middle = [(x, y) for x, y in painted if a1["x"] < x < a1["x"] + 11]

            def hook(part):
                """鉤（横に 2 ドット以上の行と、縦に 2 ドット以上の列を持つ）"""
                if not part:
                    return False
                top = min(y for _, y in part)
                cols_ = {}
                for x, y in part:
                    cols_.setdefault(x, set()).add(y)
                return sum(1 for _, y in part if y == top) >= 2 and max(len(v) for v in cols_.values()) >= 2

            prev = here.get(sel0)
            stray = [(x, y) for x, y in diff if not (head_window(a1, x, y) or (prev and head_window(prev, x, y)))]
            feet = [(x, y) for x, y in diff if foot_window(a1, x, y)]
            live.click(f'.cptab[data-session="{sel0}"]')
            live.evaluate(ROOM_FRAMES, 2)
            back = live.evaluate(DIFF)
            check(motion["selected"] == pick and nsel == 1 and hook(left) and hook(right) and not middle
                  and len(painted) == len(mine) and not stray and not feet and not back,
                  f"選択: 端末で {pick} を選ぶと部屋のその人の頭上に角かっこ（左右の鉤・個体色 {tint}・{len(painted)} 画素）・"
                  "変わったのは前後の選択の頭上だけ・足元は変わらない・戻すと元の画素",
                  f"部屋の選択 {motion['selected']} / 角かっこを描いた人数 {nsel} / 左 {sorted(left)} / 右 {sorted(right)} / "
                  f"間 {middle[:4]} / 色の違う画素 {len(mine) - len(painted)} / 窓の外 {stray[:6]} / 足元 {feet[:4]} / "
                  f"戻した後の差 {len(back)}")

        # 14. 席の安定: 机 0 に座っている人を ❗ にしても、他の人の位置は動かない（動きを止めている＝本人はその場で受付へ）
        sessions = live.evaluate("() => [...document.querySelectorAll('.cptab[data-session]')]"
                                 ".map(t => t.dataset.session).filter(s => !s.startsWith('term:'))")
        where = "ss => Object.fromEntries(ss.map(s => [s, window.__office.debug.roomPoint(s)]))"
        pts = live.evaluate(where, sessions)
        # 机 0 の座る位置は配置表から（roomPoint と同じ照準＝セルの横の中央・上から 4px）
        desk0 = live.evaluate("""async () => {
          const { DESKS, ROOM } = await import('/ui/core/pxroom.js');
          const r = document.querySelector('.cproom').getBoundingClientRect();
          const S = window.__office.stats().scale;
          return [r.left + (DESKS[0].robot.x + ROOM.robot.w / 2) * S, r.top + (DESKS[0].robot.y + 4) * S];
        }""")
        target = next((s for s, p in pts.items()
                       if p and abs(p["left"] - desk0[0]) < 1 and abs(p["top"] - desk0[1]) < 1), None)
        if target is None:
            check(False, "席の安定（live で 1 人が ❗ になっても他は動かない）", f"机 0 に誰も居ない {pts}")
        else:
            fg0 = live.evaluate("() => window.__office.stats().fgBakes")
            mon0 = live.evaluate(MONITOR, 0)
            inject(with_attention(world, target))
            pts2 = live.evaluate(where, sessions)
            fg1 = live.evaluate("() => window.__office.stats().fgBakes")
            mon1 = live.evaluate(MONITOR, 0)
            moved = [s for s in sessions if s != target and pts.get(s) != pts2.get(s)]
            check(not moved and pts2.get(target) != pts.get(target),
                  f"席の安定（{target} が ❗ で受付へ・他 {len(sessions) - 1} 人は同じ場所）",
                  f"動いた {moved} / 本人 {pts.get(target)} → {pts2.get(target)}")
            inject(world)
            fg2 = live.evaluate("() => window.__office.stats().fgBakes")
            mon2 = live.evaluate(MONITOR, 0)
            check(fg1 == fg0 + 1 and fg2 == fg1 + 1 and mon1["px"] != mon0["px"] and mon2["px"] == mon0["px"],
                  f"在席が変わると手前を焼き直す（fgBakes {fg0}→{fg1}→{fg2}・机 0 のモニタ {mon0['rect']} が"
                  " 席を立つと変わり、戻ると元の画素）",
                  f"fgBakes {fg0}→{fg1}→{fg2} / モニタ 変わった={mon1['px'] != mon0['px']}"
                  f" 戻った={mon2['px'] == mon0['px']}")

            # 14b. 入れ替え: 机 0 の人の ❗ と新しい人の到着が同じポーリングに入る＝在席の人数は同じで集合が変わる
            #      （焼き直しの鍵を人数に落とすと、空いた机の画面が点いたまま・座った机の画面は消えたまま＝絵の掟 6）
            seats_n = live.evaluate("async () => (await import('/ui/core/pxroom.js')).DESKS.length")
            occ0 = live.evaluate(DESKS_NOW)
            mons0 = [live.evaluate(MONITOR, i)["px"] for i in range(seats_n)]
            fg3 = live.evaluate("() => window.__office.stats().fgBakes")
            inject(with_member(with_attention(world, target), "sess-smoke-swap", "スモーク入れ替え"))
            occ1 = live.evaluate(DESKS_NOW)
            mons1 = [live.evaluate(MONITOR, i)["px"] for i in range(seats_n)]
            fg4 = live.evaluate("() => window.__office.stats().fgBakes")
            inject(world)
            mons2 = [live.evaluate(MONITOR, i)["px"] for i in range(seats_n)]
            fg5 = live.evaluate("() => window.__office.stats().fgBakes")
            left_, came = sorted(set(occ0) - set(occ1)), sorted(set(occ1) - set(occ0))
            lit = [i for i in range(seats_n) if mons1[i] != mons0[i]]
            check(len(occ1) == len(occ0) and left_ == [0] and len(came) == 1
                  and fg4 == fg3 + 1 and fg5 == fg4 + 1
                  and lit == sorted(left_ + came) and mons1[0] == mon1["px"] and mons2 == mons0,
                  f"在席の人数が同じで集合が変わっても焼き直す（机 {occ0}→{occ1}・fgBakes {fg3}→{fg4}→{fg5}・"
                  f"画面が変わった机 {lit}＝空いた机と座った机だけ）",
                  f"在席 {occ0}→{occ1}（空いた {left_}・座った {came}）/ fgBakes {fg3}→{fg4}→{fg5} / "
                  f"画面が変わった机 {lit} / 空いた机 0 が ❗ だけのときと同じ={mons1[0] == mon1['px']} / "
                  f"戻すと全部元の画素={mons2 == mons0}")

        # 16. 個体色: 表示中の人の色を（前回なしなら）奪う新しいプロジェクトを探して足す → 既存の色は変わらない。
        #     R110: 一覧のカードは名簿へ統合＝名簿の行の --session（色はプロジェクトの key ごと＝プロジェクト行 data-group）を、
        #     その key のセッション全員へ引く（畳んだプロジェクトの子は行を持たない＝行の data-session だけでは引けない）
        colors = """() => {
          const rows = new Map([...document.querySelectorAll('#agents .pxrow.proj[data-group]')]
            .map(r => [r.dataset.group, r.style.getPropertyValue('--session')]));
          const cards = {};
          for (const g of window.__office.debug.board()) {
            if (!rows.has(g.key)) continue;
            for (const s of g.sessions) cards[s.session] = rows.get(g.key);
          }
          return {
            tabs: Object.fromEntries([...document.querySelectorAll('.cptab[data-session]')]
              .map(t => [t.dataset.session, t.style.getPropertyValue('--session')])),
            cards };
        }"""
        thief = live.evaluate("""async () => {
          const { tintIndex } = await import('/ui/core/pxroom.js');
          const { SESSION_COLORS } = await import('/ui/cockpit/palette.js');
          const keys = window.__office.debug.board().map(g => g.key), n9 = SESSION_COLORS.length;
          const cur = tintIndex(keys, n9);
          for (let i = 0; i < 2000; i += 1) {
            const n = '/Users/test/!smoke-new-' + i;
            const next = tintIndex([...keys, n], n9);
            const moved = keys.filter(k => next.get(k) !== cur.get(k));
            if (moved.length) return { key: n, moved };
          }
          return null; }""")
        c0 = live.evaluate(colors)
        if not thief:
            check(False, "個体色が前回を引き継ぐ", "既存の色を奪う新しいキーが見つからない（門が空振り）")
        else:
            new_sid = "sess-smoke-new"
            inject(with_member(world, new_sid, "スモーク新規", cwd=thief["key"]))
            c1 = live.evaluate(colors)
            # 部屋の色は貼った画素から当て直す（控えの tint は読まない）。いちばんよく合う候補の色が 1 色に決まり、
            # それがタブ・名簿の行の色と同じこと（決まらない＝その人の個体色のドットが 1 つも見えていない＝赤）
            px = live.evaluate(ROBOT_PIXELS, list(c1["tabs"]))
            split = [(s, r["tints"], c1["tabs"].get(s), c1["cards"].get(s)) for s, r in px.items()
                     if not (r["tints"] == [c1["tabs"].get(s)] and c1["tabs"].get(s) == c1["cards"].get(s))]
            changed = [s for s, v in c0["tabs"].items() if c1["tabs"].get(s) != v]
            check(new_sid in c1["tabs"] and not changed and px and new_sid in px and not split,
                  f"個体色が前回を引き継ぐ（新しいプロジェクトが来ても {len(c0['tabs'])} 人の色はそのまま・"
                  f"部屋のロボ {len(px)} 体の画素の色＝タブ＝名簿の行）",
                  f"新しいタブ {new_sid in c1['tabs']} / 色が変わった {changed}（前回なしなら動く {thief['moved']}）/ "
                  f"部屋の画素で当て直した {len(px)} 体 / 画素の色とタブ・名簿の行が違う {split[:4]}")
            inject(world)
        live.click("#cp-motion")                          # 動きを戻す
        live.evaluate(ROOM_FRAMES, 2)

        # 10・13 を人数の多い世界でも（受付の standby・席の満杯を含む）。入れ替えは入退場の山（歩き終わるまで待つ）
        transition(xl22, "basic→xl22")
        calls_match("xl22")
        seats_match("xl22")
        # 17. standby は挙手も打鍵もしない（本人が working でも、机が無いので立って待つ絵）。
        #     コマは貼った画素から当て直す（控えの cell は読まない）。本人の状態は world から引く。
        #     動かしたまま打鍵の 1 周期（2fps×2 コマ）より長く見続け、どの瞬間も「挙手・打鍵のコマでしか説明できない」
        #     絵になっていないことを見る
        standby = [a["session"] for a in live.evaluate(ACTORS) if a["zone"] == "standby" and not a.get("leaving")]
        busy = live.evaluate("ss => (window.__office.debug.world().agents || [])"
                             ".filter(a => ss.includes(a.session) && a.state === 'working').map(a => a.session)", standby)
        t0 = live.evaluate("() => window.__office.stats().t")
        samples, span = [], 0.0
        while span < 1.5 and len(samples) < 60:
            samples.append(live.evaluate(ROBOT_PIXELS, standby))
            live.evaluate(ROOM_FRAMES, 1)
            span = live.evaluate("() => window.__office.stats().t") - t0
        bad = sorted({(s, tuple(r["cells"]), r["min"]) for px in samples for s, r in px.items()
                      if len(r["tints"]) != 1 or all(c.startswith(("raise", "type")) for c in r["cells"])})
        seen = sorted({c for px in samples for r in px.values() for c in r["cells"]})
        whole = all(sorted(px) == sorted(standby) for px in samples)
        check(standby and busy and whole and span >= 1.5 and not bad,
              f"受付の standby {len(standby)} 人は挙手も打鍵もしない（うち working {len(busy)} 人・"
              f"{len(samples)} 枚 {span:.1f} 秒ぶん貼った画素のコマ {seen}）",
              f"standby {len(standby)} / working {busy} / 全員を画素で当て直せた {whole} / 見た {span:.1f} 秒 / "
              f"挙手・打鍵にしか見えない（または色が決まらない） {bad[:4]}")

        # 13. 部屋からあふれる世界: 「外 k」が出て、受付 m に混ざらない
        if not peak_path.is_file():
            check(False, "フッターの外 k（部屋からあふれる世界）", f"{peak_path.relative_to(ROOT)} が無い")
        else:
            transition(json.loads(peak_path.read_text(encoding="utf-8")), "xl22→cockpit_peak")
            calls_match("cockpit_peak")
            seats_match("cockpit_peak", need_outside=True)
    finally:
        live.close()


def check_motion(browser, port, world, check, errors):
    """20・23〜26: live ページで時計を手で進める（debug.step・1/12 秒ずつ＝歩いている間の刻みちょうど）。

    clock[0]＝最後に描いた時刻。inject は「その時刻に届いた world」として同じ時刻で描き直される（部屋は冪等）＝
    入れた直後の絵（歩き出す前の位置）は時計を進めずに読み、そこから 1/12 秒ずつ進める。
    """
    page, inject = open_live(browser, port, world, errors, "step")
    try:
        check_opened(page, "時計を手で進めるページ", check)
        geo = page.evaluate(GEO)
        pasted = set()                                 # このページで貼ったのを見たスプライトの鍵（21b の材料）

        def step(t):
            fr = page.evaluate(STEP, t)
            pasted.update(k for k, _ in fr["sprites"])
            if t is not None:
                clock[0] = t
            return fr

        def plan(a, b):
            return page.evaluate(PLAN, [list(a), list(b)])

        def record(key, done, most=12 * 20, each=None):
            """入れた直後（進めない）から 1/12 秒ずつ進めて控える。done(frame, me) が真になってから 12 刻み（1 秒）見て止める"""
            t0, frames, end = clock[0], [], None
            for k in range(most):
                fr = step(t0 + k / 12 if k else None)
                frames.append(fr)
                if each:
                    each(k, fr)
                if end is None and k and done(fr, person(fr, key)):
                    end = k + 12
                if end is not None and k >= end:
                    break
            return frames

        # 描いた最後の時刻より少し先から手で進める（帳簿は 4 秒より古いと続きを演じない＝飛ばしすぎない）
        clock = [page.evaluate("() => window.__office.t()") + 0.25]
        f = step(clock[0])

        # 23. 歩行: 机に居る 1 人（1 人のプロジェクトの代表）を ❗ にする → 受付まで歩く
        reps = {r["session"] for r in world["roster"] if len(r.get("sessions") or []) == 1}
        target = next((p["key"] for p in f["people"] if p["session"] in reps and p["zone"] == "desk"), None)
        if target is None:
            check(False, "歩行（live で ❗ にした人が受付まで歩く）", f"机に居る 1 人のプロジェクトが無い {f['people']}")
            return
        seat = person(f, target)
        w2 = with_attention(world, target)
        inject(w2, 0)
        # 席に立っていた人は最初の 2 刻み（RISE_SEC）立ち上がる（歩行コマなし・kind rise）＝着いたの判定に数えない
        frames = record(target, lambda fr, me: me and not me["walk"] and me["kind"] != "rise")
        traj = [person(fr, target) for fr in frames]
        walked = [p for p in traj if p and p["walk"]]
        rises = [k for k, p in enumerate(traj) if p and p["kind"] == "rise"]
        end = traj[-1]
        dest = (end["x"], end["y"])
        path = plan((seat["x"], seat["y"]), dest)
        exempt = spot_furniture(geo, seat["x"], seat["y"]) | spot_furniture(geo, *dest)
        probs = walk_problems([p for p in traj if p], path, geo, exempt)
        others = {p["key"]: (p["x"], p["y"]) for p in f["people"] if p["key"] != target}
        moved = sorted({p["key"] for fr in frames for p in fr["people"]
                        if p["key"] in others and (p["x"], p["y"]) != others[p["key"]]})
        still = frames[-12:]
        fps_walk = {fr["fps"] for fr in frames if fr["walking"]}
        check(not probs and len(walked) >= 10 and len(path or []) >= 3
              and (traj[0]["x"], traj[0]["y"]) == (seat["x"], seat["y"]) and rises == [0, 1]
              and all((traj[k]["x"], traj[k]["y"]) == (seat["x"], seat["y"]) for k in rises)
              and len(traj) > 2 and traj[2] and traj[2]["walk"]
              and end["zone"] == "queue" and not moved and fps_walk == {WALK_FPS}
              and all(fr["fps"] == IDLE_FPS and fr["walking"] == 0 for fr in still)
              and all((person(fr, target)["x"], person(fr, target)["y"]) == dest for fr in still)
              and frames[-1]["unreachable"] == 0,
              f"歩行: {target} が机 ({seat['x']},{seat['y']}) で 2 刻み立ち上がり、受付 {dest} まで {len(walked)} 刻み・"
              f"{len(path or []) - 1} 区間を"
              "経路の上だけ歩く（瞬間移動なし・家具と壁に足が掛からない・向き＝区間の主軸・歩行コマは 1 刻みに 1 つ）・"
              "着いたら止まって 6fps・他の人は動かない",
              f"{' / '.join(probs)} / 歩いた刻み {len(walked)} / 立ち上がりの刻み {rises} / 経路 {path} / 最初 {traj[0]} / "
              f"着いた区分 {end['zone']} / 動いた他の人 {moved[:4]} / 歩行中の fps {fps_walk} / "
              f"着いた後 {[(fr['fps'], fr['walking']) for fr in still[:3]]} / unreachable {frames[-1]['unreachable']}")

        # 24. 入場＋25. 重なり順: 新しいセッションは扉から入って席まで歩く。歩く間、手前の天板が被らないかを画素で見る
        sid = "sess-smoke-walk"
        w3 = with_member(w2, sid, "スモーク入場")
        inject(w3, 0)
        tint = page.evaluate(TAB_COLOR, sid)
        tally = {"front": 0, "behind": 0, "bad": []}

        def front(k, fr):
            chk = page.evaluate(FRONT_CHECK, [sid, tint]) if tint else None
            for top in (chk or {}).get("tops", []):
                if chk["footY"] >= top["bottom"]:
                    tally["front"] += 1
                    if top["hidden"]:
                        tally["bad"].append((k, chk["x"], chk["y"], chk["cell"], top["id"], f"前なのに {top['hidden']} ドット被った"))
                else:
                    tally["behind"] += 1
                    if top["hidden"] < top["over"]:
                        tally["bad"].append((k, chk["x"], chk["y"], top["id"],
                                             f"奥なのに {top['over'] - top['hidden']} ドット上に乗った"))

        frames = record(sid, lambda fr, me: me and not me["walk"], each=front)
        traj = [person(fr, sid) for fr in frames]
        first, end = traj[0], traj[-1]
        dest = (end["x"], end["y"]) if end else None
        path = plan((first["x"], first["y"]), dest) if first and end else None
        probs = walk_problems([p for p in traj if p], path, geo, spot_furniture(geo, *dest)) if path else ["入った人が居ない"]
        entering = [p for p in traj if p and p["walk"]]
        check(first is not None and at_door(geo, first["x"], first["y"]) and first["entering"] and not probs
              and len(entering) >= 10 and all(p["entering"] for p in entering) and not end["entering"]
              and end["zone"] == "desk" and all(fr["entering"] == 1 for fr in frames if person(fr, sid)["walk"]),
              f"入場: {sid} は扉 ({first and first['x']},{first and first['y']}) から現れ、机 {dest} まで {len(entering)} 刻みで歩く"
              "（経路の門は歩行と同じ）・着いたら entering が下りる",
              f"{' / '.join(probs)} / 最初 {first} / 着いた {end} / 経路 {path}")
        entry = {"first": (first["x"], first["y"]) if first else None, "dest": dest, "walked": len(entering)}
        check(tint and tally["front"] >= 3 and tally["behind"] >= 1 and not tally["bad"],
              f"重なり順: 歩く人が天板の前を通る {tally['front']} 場面で天板が人に被らない・"
              f"席では机が腰から下を隠す（{tally['behind']} 場面）",
              f"タブの色 {tint} / 前 {tally['front']}・奥 {tally['behind']} / 逆 {tally['bad'][:4]}")

        # 26. ✓: 着いた人（working）が報告して待機に変わる → 同じ刻みで ✓（タイルの画素まで）・万歳のコマ → 8 秒で 💬
        t_done = clock[0] + 1 / 12
        step(t_done)
        inject(with_fields(w3, sid, state="waiting", kind="said", age=30, verb="指示待ち"), 0)
        fr0 = step(None)
        me0 = person(fr0, sid)
        pix0 = {m["key"]: m for m in page.evaluate(MARK_PIXELS)}.get(sid)
        body0 = page.evaluate(ROBOT_PIXELS, [sid]).get(sid) or {}
        during = [(dt, person(step(t_done + dt), sid)["mark"]) for dt in (1, 2, 3, 4, 5, 6, 7, 7.9)]
        fr_after = step(t_done + DONE_SEC + 0.1)
        after = person(fr_after, sid)
        check(me0["mark"] == "done" and me0["cell"] == "done0" and fr0["marks"]["done"] == 1
              and pix0 and pix0["tile"] == "markDone" and pix0["miss"] == 0 and pix0["dots"] > 0
              and "done0" in body0.get("cells", [])
              and all(m == "done" for _, m in during) and after["mark"] == "wait" and after["cell"] != "done0"
              and fr_after["marks"]["done"] == 0,
              f"✓: 報告して待機に変わった同じ刻みで ✓（{pix0 and pix0['dots']} ドットが画素どおり）と万歳・"
              f"{DONE_SEC} 秒で 💬 に替わる",
              f"直後 {me0} / 印の画素 {pix0} / ロボの画素 {body0} / 途中 {during} / {DONE_SEC}.1 秒後 {after}")

        # 24. 退場: 消したセッションは扉まで歩いて消える（印も挙手もしない・消えたら帳簿からも居ない）。
        #     行き先は入場で現れた扉の点（上の門で扉の上だと確かめた位置）＝経路はそこまで引いて、全部の位置をその上で見る
        door = (first["x"], first["y"]) if first else None
        inject(w2, 0)
        frames = record(sid, lambda fr, me: me is None)
        traj = [person(fr, sid) for fr in frames]
        seen = [p for p in traj if p]
        last = seen[-1] if seen else None
        # 描かれていたのは最初から続けて・最後に描いた刻みの次でもう居ない（途中で消えて戻る・最後まで消えない、を落とす）
        k_last = max((k for k, p in enumerate(traj) if p), default=-1)
        solid = k_last >= 0 and all(traj[:k_last + 1]) and k_last + 1 < len(traj) and traj[k_last + 1] is None
        path = plan((seen[0]["x"], seen[0]["y"]), door) if seen and door else None
        probs = (walk_problems(seen, path, geo, spot_furniture(geo, seen[0]["x"], seen[0]["y"]))
                 if seen and path else ["退場を描いていない（か入場で扉の点が取れていない）"])
        reached = door_reached(path, last, door)
        gone = page.evaluate("s => ({ actors: window.__office.debug.roomActors().some(a => a.session === s),"
                             " walkers: window.__office.debug.roomWalkers().some(w => w.key === s) })", sid)
        # 席から消えた人は最初の 2 刻みだけ立ち上がる（rise）・その後は歩く（どちらも挙手・印なし）
        lead = next((k for k, p in enumerate(seen) if p["kind"] != "rise"), len(seen))
        check(seen and len(seen) >= 10 and lead <= 2
              and all(p["leaving"] and p["mark"] is None and p["kind"] == ("rise" if k < lead else "walk")
                      for k, p in enumerate(seen))
              and not reached and not probs and solid
              and not gone["actors"] and not gone["walkers"] and all(fr["leaving"] <= 1 for fr in frames),
              f"退場: {sid} は席から扉の点 {door} へ {len(seen)} 刻み歩き、最後は扉の手前 "
              f"{last and abs(last['x'] - door[0]) + abs(last['y'] - door[1]) if last and door else '?'}px（1 刻み以内）で"
              "次の刻みに消える（印・挙手なし・消えたら帳簿からも居ない）",
              f"{' / '.join(probs)} / {reached} / 最後 {last} / 続けて描いて次で消えた {solid} / 退場中でない・印がある "
              f"{[p for p in seen if not p['leaving'] or p['mark']][:2]} / 消えた後 {gone}")
        fin = step(None)
        check(fin["unreachable"] == 0 and fin["missingCells"] == 0 and fin["missingTiles"] == 0,
              "時計を手で進めたページ: 届かない歩行・描けないコマ／タイル 0",
              f"unreachable {fin['unreachable']} / missingCells {fin['missingCells']} / missingTiles {fin['missingTiles']}")

        # 21b. 焼き置きの上限は「初めて貼った鍵の数」（焼いた回数を上限にしない＝自分を基準にしない）: 同じ入場
        #      （同じセッション＝同じ個体色・同じ world＝同じ机と経路・1/12 秒ずつ＝同じ歩行の位相）をもう一度歩かせる。
        #      1 回目に貼った鍵を貼り直すだけ＝spriteBakes の増分は、2 回目の窓で初めて貼った鍵の数まで（歩いている本人の分は
        #      0＝前提として見る）。21 の窓の②（部屋以外への塗り ≤ 焼いた枚数 × ドット数）は焼いた回数が増えると上限も
        #      上がる＝歩いている間だけ焼き置きが効かない退行はここで捕まえる。時計を手で進めるページで見る（rAF の live
        #      ページは重いマシンで刻みを飛ばし、1 回目に見えなかった位相が 2 回目に初めて出うる＝正しい焼きで赤になる）
        known = set(pasted)
        bakes0 = page.evaluate(STATS)["spriteBakes"]
        inject(w3, 0)
        frames = record(sid, lambda fr, me: me and not me["walk"])
        traj = [person(fr, sid) for fr in frames]
        walked2 = [p for p in traj if p and p["walk"]]
        win = {(k, who) for fr in frames for k, who in fr["sprites"]}
        new = sorted({k for k, _ in win} - known)
        # 机の画面の模様（tile|screen*・R110-S5 で道具ごとに 2 コマ）は本人の机に貼るが、コマは歩きの位相ではなく時計
        # （pxtop.screenFrame(t + 位相)）で決まる＝着いた刻みの t が 1 回目と違えば、1 回目に出なかったコマが初めて出うる
        # （正しい焼きでも赤になった）。本人の分の前提（「同じ歩きは同じ鍵」）からは外し、焼いた回数の上限（new）には数える
        mine_new = sorted({k for k, who in win if who == sid and not k.startswith("tile|screen")} - known)
        baked = frames[-1]["spriteBakes"] - bakes0
        same = (traj[0] is not None and (traj[0]["x"], traj[0]["y"]) == entry["first"] and traj[-1] is not None
                and (traj[-1]["x"], traj[-1]["y"]) == entry["dest"] and len(walked2) == entry["walked"])
        check(same and not mine_new and baked <= len(new),
              f"同じ入場をもう一度歩かせると焼き置きを貼り直すだけ（{sid} が扉から机 {entry['dest']} まで {len(walked2)} 刻み・"
              f"spriteBakes +{baked}＝初めて貼った鍵 {len(new)} 個まで）",
              f"1 回目と同じ入場 {same}（最初 {traj[0]}・着いた {traj[-1]}・1 回目 {entry}）/ 本人の鍵で初めて貼ったもの "
              f"{mine_new[:4]} / spriteBakes +{baked}（上限 {len(new)}: {new[:6]}）")
    finally:
        page.close()


def button_says(b, stopped):
    """「動きを止める」ボタンが stopped の側を言っているか（aria-pressed と文言。止まっていれば「動きを戻す」）。
    live のページでは押せる（disabled は固定時刻のページだけ＝button_fixed）"""
    return (b["pressed"] == ("true" if stopped else "false") and b["start"] != b["stop"]
            and b["text"] == (b["start"] if stopped else b["stop"]) and b["disabled"] is False)


def button_fixed(b):
    """固定時刻（?t=）のページのボタン: 部屋は動かない＝「固定表示」で押せない・aria-pressed は止まっている側
    （design/cockpit.html の見本と同じ。「動きを止める」と言って押せるのは嘘）"""
    return (b["disabled"] is True and b["pressed"] == "true" and b["text"] == b["fixed"]
            and b["fixed"] not in ("", "cp_motion_fixed", b["start"], b["stop"]))


def check_reduced(browser, port, world, check, errors):
    """20・27: 視差効果を減らす（prefers-reduced-motion: reduce）では部屋の時計が止まる。「動きを止める」ボタンと
    1 つの状態（ボタンの aria-pressed と文言が部屋の実際と食い違わない）。

    止めていた間の報告を戻した後に遅れて祝わない門は、reduce・ボタン・非表示タブの 3 経路で **live で working を
    描かせて（記録させて）から**止める。開いた瞬間から止まっているページでは部屋が who を working として一度も記録しない
    ＝戻した後の最初の描画は必ず「初めて見た」になり、古さの門（実装の MARK_STALE_SEC）を壊しても遅れた ✓ が出ようがない
    （0 件で ✓ の形。別モデルレビューが MARK_STALE_SEC=1e9 の写しで実証）。
    """
    page, inject = open_live(browser, port, world, errors, "reduced", reduced=True)
    try:
        check_opened(page, "視差効果を減らすページ", check)
        rows = {r["session"]: r for r in world["roster"] if len(r.get("sessions") or []) == 1}
        here = {a["session"]: a for a in page.evaluate(ACTORS) if not a.get("leaving")}
        # 報告に変える人: 机で working の 1 人のプロジェクトの代表
        who = next((s for s, a in sorted(here.items()) if s in rows and a["zone"] == "desk"
                    and rows[s].get("state") == "working"), None)
        a0 = page.evaluate(ROOM_PIXELS)
        m0 = page.evaluate(MOTION)
        b0 = page.evaluate(MOTION_BTN)
        st0 = page.evaluate(STATS)
        page.wait_for_timeout(1300)
        page.evaluate(ROOM_FRAMES, 2)
        a1 = page.evaluate(ROOM_PIXELS)
        st1 = page.evaluate(STATS)
        check(m0["reduced"] and m0["still"] and a0 == a1 and st1["t"] - st0["t"] >= 1.0 and st1["fps"] == IDLE_FPS,
              f"視差効果を減らす: t が {st1['t'] - st0['t']:.1f} 秒進んでも部屋の画素が同じ（コマを送らない）・fps {st1['fps']}",
              f"roomMotion {m0} / 画素が同じ {a0 == a1} / t {st0['t']}→{st1['t']} / fps {st1['fps']}")
        check(m0["stopped"] and button_says(b0, True),
              f"視差効果を減らす: 「動きを止める」ボタンも止まっている側（aria-pressed={b0['pressed']}・『{b0['text']}』）",
              f"roomMotion {m0} / ボタン {b0}（止まっている側の文言は『{b0['start']}』）")
        # 27b. reduce を掛けたまま本人がボタンで上書きする（見本と同じ＝押せば動く）。reduce（OS の今の値）と動きの状態が
        #      食い違う唯一の場面: 部屋が動き（still=false・コマが送られる）、ボタンも動いている側を言う。もう一度押すと
        #      止まっている側へ戻る（部屋の画素も止まる）。reduce で開いた直後・reduce を外した後は両者が同じ値＝そこだけ
        #      見ていると、部屋の時計に reduce を直接効かせる退行（ボタンは「動きを止める」なのに部屋は止まったまま）が緑になる
        page.click("#cp-motion")
        page.evaluate(ROOM_FRAMES, 1)
        mo, bo = page.evaluate(MOTION), page.evaluate(MOTION_BTN)
        moved = set()
        for _ in range(10):
            page.evaluate(ROOM_FRAMES, 2)
            moved.add(tuple(sorted((m["key"], m["cell"]) for m in page.evaluate("() => window.__office.debug.roomMarks()"))))
        page.click("#cp-motion")
        page.evaluate(ROOM_FRAMES, 1)
        ms, bs = page.evaluate(MOTION), page.evaluate(MOTION_BTN)
        s0 = page.evaluate(ROOM_PIXELS)
        page.wait_for_timeout(1300)
        page.evaluate(ROOM_FRAMES, 2)
        s1 = page.evaluate(ROOM_PIXELS)
        check(mo["reduced"] and not mo["stopped"] and not mo["still"] and button_says(bo, False) and len(moved) >= 2
              and ms["reduced"] and ms["stopped"] and ms["still"] and button_says(bs, True) and s0 == s1,
              f"視差効果を減らすのまま「動きを止める」ボタンで上書きできる: 押すと部屋が動き（{len(moved)} 通りの絵）"
              f"ボタンも動いている側『{bo['text']}』・もう一度押すと止まる『{bs['text']}』（reduce は掛けたまま）",
              f"押した後 roomMotion {mo} / ボタン {bo} / 絵 {len(moved)} 通り / もう一度押した後 roomMotion {ms} / "
              f"ボタン {bs} / 止めた後の画素が同じ {s0 == s1}")
        if who is None:
            check(False, "視差効果を減らす: ✓ が出ない", "机で working の 1 人のプロジェクトが無い")
            return
        page.wait_for_timeout(400)
        inject(with_member(with_fields(world, who, state="waiting", kind="said", age=30, verb="指示待ち"),
                           "sess-smoke-reduced", "スモーク reduced"), 2)
        samples = []
        for _ in range(8):
            page.evaluate(ROOM_FRAMES, 2)
            marks = {m["key"]: m for m in page.evaluate("() => window.__office.debug.roomMarks()")}
            st = page.evaluate(STATS)
            samples.append((marks.get(who), marks.get("sess-smoke-reduced"), st["marks"]["done"], st["walking"], st["entering"]))
        geo = page.evaluate(GEO)
        done_seen = [s for s in samples if (s[0] or {}).get("mark") == "done" or (s[0] or {}).get("cell") == "done0" or s[2]]
        new = samples[-1][1]
        walked = [s for s in samples if s[3] or s[4]]
        check(not done_seen and samples[-1][0] and samples[-1][0]["mark"] == "wait" and new and not walked
              and not at_door(geo, new["x"], new["y"]) and new["zone"] == "desk",
              f"視差効果を減らす: {who} が報告に変わっても ✓ も万歳も出ない（💬 のまま）・新しい人は歩かずに席に居る",
              f"✓ が出た {done_seen[:2]} / 最後 {samples[-1]}")
        # reduced を外す: change を購読している＝コマが動き出す・ボタンも動いている側へ戻る
        page.emulate_media(reduced_motion="no-preference")
        try:
            page.wait_for_function("() => !window.__office.debug.roomMotion().reduced", timeout=5000)
        except Exception:                                  # noqa: BLE001 - playwright の TimeoutError
            check(False, "視差効果を減らすを外すとコマが動き出す",
                  f"matchMedia の change を購読していない（5 秒たっても reduced のまま {page.evaluate(MOTION)}）")
            return
        cells = set()
        for _ in range(10):
            page.evaluate(ROOM_FRAMES, 2)
            ms = page.evaluate("() => window.__office.debug.roomMarks()")
            cells.add(tuple(sorted((m["key"], m["cell"]) for m in ms)))
        mv = page.evaluate(MOTION)
        bv = page.evaluate(MOTION_BTN)
        check(not mv["reduced"] and not mv["still"] and not mv["stopped"] and len(cells) >= 2 and button_says(bv, False),
              f"視差効果を減らすを外すとコマが動き出す（{len(cells)} 通りの絵）・ボタンも動いている側"
              f"（aria-pressed={bv['pressed']}・『{bv['text']}』）",
              f"roomMotion {mv} / 絵 {len(cells)} 通り / ボタン {bv}")

        # 止めていた間の報告を、戻した後に遅れて祝わない（3 経路）。筋書きは同じ:
        #   live で who を working のまま描かせる（記録させる）→ 止める → 止めている間に who を報告へ・新しい人を 1 人足す
        #   → STALE_WAIT 秒待つ → 戻す → 戻した後の数フレームで who に ✓・万歳が出ない（💬 になっている＝報告は届いている）・
        #   新しい人は扉から歩かずにその場に居る（入場・歩行が一度も立たない）
        stay = [("sess-smoke-reduced", "スモーク reduced")]

        def motion_still(want):
            try:
                page.wait_for_function("w => window.__office.debug.roomMotion().still === w", arg=want, timeout=5000)
                return True
            except Exception:                              # noqa: BLE001 - playwright の TimeoutError
                return False

        def pause_reduce():
            page.emulate_media(reduced_motion="reduce")
            return motion_still(True) and button_says(page.evaluate(MOTION_BTN), True)

        def resume_reduce():
            page.emulate_media(reduced_motion="no-preference")
            return motion_still(False) and button_says(page.evaluate(MOTION_BTN), False)

        def pause_button():
            page.click("#cp-motion")
            return motion_still(True) and button_says(page.evaluate(MOTION_BTN), True)

        def resume_button():
            page.click("#cp-motion")
            return motion_still(False) and button_says(page.evaluate(MOTION_BTN), False)

        def pause_hidden():
            return page.evaluate(HIDE, True) is True

        def resume_hidden():
            return page.evaluate(HIDE, False) is False

        def late_after_pause(label, pause, resume, hidden=False, lang=None):
            """lang を渡すと、止めている間に届く world で言語も切り替える（文言を貼り直した後もボタンが止まっている側を言う）"""
            base = world
            for sid, name in stay:
                base = with_member(base, sid, name)
            new_sid = f"sess-smoke-late{len(stay)}"
            later = with_member(with_fields(base, who, state="waiting", kind="said", age=30, verb="指示待ち"),
                                new_sid, f"スモーク {label}")
            if lang:
                later["lang"] = lang
            # ① live で working を描かせる（部屋に who を working として記録させる）
            t0 = page.evaluate(STATS)["t"]
            inject(base, 3)
            t1 = page.evaluate(STATS)["t"]
            me = next((a for a in page.evaluate(ACTORS) if a["session"] == who and not a.get("leaving")), None)
            recorded = bool(me and me["state"] == "working" and not page.evaluate(MOTION)["still"] and t1 - t0 >= 2 / IDLE_FPS)
            # ② 止める → 止めている間に報告・新しい人
            paused = pause()
            f0 = page.evaluate(STATS)["frames"]
            inject(later, 0 if hidden else 2)
            during = page.evaluate(STEP, None)
            # 言語を切り替えた後もボタンは止まっている側（文言を貼り直す処理が「止める」に戻さない）・文言は新しい言語
            relabel = page.evaluate(MOTION_BTN) if lang else None
            relabeled = not lang or (button_says(relabel, True) and relabel["start"] != b0["start"])
            page.wait_for_timeout(int(STALE_WAIT * 1000))
            f1 = page.evaluate(STATS)["frames"]
            # 止めている間: 非表示タブなら 1 度も描かない・ほかは描くが ✓ を出さない
            quiet = (f1 == f0) if hidden else ((person(during, who) or {}).get("mark") == "wait"
                                              and (person(during, who) or {}).get("cell") != "done0")
            # ③ 戻す → 数フレーム見る
            page.evaluate("() => { const c = window.__roomCalls; c.enterMax = 0; c.walkMax = 0; }")
            resumed = resume()
            seen = []
            for _ in range(6):
                page.evaluate(ROOM_FRAMES, 1)
                seen.append(page.evaluate(STEP, None))
            c = page.evaluate("() => ({ enterMax: window.__roomCalls.enterMax, walkMax: window.__roomCalls.walkMax })")
            late = [(k, (person(fr, who) or {}).get("mark"), (person(fr, who) or {}).get("cell")) for k, fr in enumerate(seen)
                    if person(fr, who) and (person(fr, who)["mark"] == "done" or person(fr, who)["cell"] == "done0")]
            end = person(seen[-1], who)
            came = person(seen[-1], new_sid)
            check(recorded and paused and quiet and relabeled and resumed and not late and end and end["mark"] == "wait"
                  and came and not came["missing"] and not at_door(geo, came["x"], came["y"])
                  and c["enterMax"] == 0 and c["walkMax"] == 0,
                  f"{label}: live で working を記録させてから止め、止めている間に報告・新しい人 → {STALE_WAIT} 秒後に戻しても"
                  f"遅れて祝わない（{who} は 💬）・扉から歩かせない"
                  + ("・隠れている間は描かない" if hidden else "")
                  + (f"・言語を切り替えてもボタンは止まっている側（『{relabel['text']}』）" if lang else ""),
                  f"止める前に working を記録 {recorded}（{me and me['state']}・t {t0}→{t1}）/ 止まった {paused} / "
                  f"止めている間 {'描いた回数 ' + str(f1 - f0) if hidden else person(during, who)} / "
                  f"言語の切り替え後のボタン {relabel} / 戻った {resumed} / "
                  f"遅れた ✓ {late[:3]} / 戻した後の {who} {end} / 新しい人 {came} / 入場・歩行の最大 {c}")
            stay.append((new_sid, f"スモーク {label}"))

        late_after_pause("視差効果を減らす", pause_reduce, resume_reduce)
        late_after_pause("「動きを止める」ボタン", pause_button, resume_button, lang="en")
        late_after_pause("非表示タブ", pause_hidden, resume_hidden, hidden=True)
    finally:
        page.close()



def check_greet(browser, port, world, check, errors):
    """31（R99-P4-L1・本人「しっかりアバターが動くように」）: 部屋のロボをクリックすると、その人が 0.9 秒手を振る
    （万歳と待機のコマ・1px 跳ねる）。当たり・座標（drawn の x,y）は動かさない・0.9 秒で終わる・他の人は振らない。
    視差効果を減らす（止まっている）ページでは振らない（止め絵は t に依らず同じ＝27 と同じ約束）。"""
    for reduced in (False, True):
        label = "止めているページ" if reduced else "live ページ"
        page, _inject = open_live(browser, port, world, errors, "greet-reduced" if reduced else "greet", reduced=reduced)
        try:
            check_opened(page, f"挨拶の{label}", check)
            here = [a for a in page.evaluate(ACTORS) if not a.get("leaving") and a.get("zone") == "desk"]
            if not here:
                check(False, "", f"挨拶: 机の人が居ない（{label}）")
                continue
            me = sorted(here, key=lambda a: a["session"])[0]
            pt = page.evaluate("s => window.__office.debug.roomPoint(s)", me["session"])
            page.mouse.click(pt["left"], pt["top"] + 6)
            if not reduced:
                try:
                    page.wait_for_function("() => window.__office.debug.roomMotion().greeting >= 1", timeout=3000)
                    on = True
                except Exception:
                    on = False
                ms = {m["key"]: m for m in page.evaluate("() => window.__office.debug.roomMarks()")}
                mine = next((m for m in ms.values() if m["session"] == me["session"]), None)
                others = [m for m in ms.values() if m["session"] != me["session"] and m.get("kind") == "greet"]
                check(on and mine and mine.get("kind") == "greet" and not others
                      and [mine["x"], mine["y"]] == [me["x"], me["y"]],
                      f"挨拶: クリックした人（{me['session']}）だけが手を振る・座標は動かない",
                      f"greeting の出現 {on} / 本人 {mine} / 他に振った人 {[m['key'] for m in others]} / 前 {me['x']},{me['y']}")
                page.wait_for_timeout(1400)
                mo = page.evaluate(MOTION)
                again = next((m for m in page.evaluate("() => window.__office.debug.roomMarks()")
                              if m["session"] == me["session"]), None)
                check(mo.get("greeting") == 0 and again and again.get("kind") != "greet",
                      "挨拶: 0.9 秒で終わる（1.4 秒後に振っている人 0）",
                      f"roomMotion {mo} / 本人 {again}")
            else:
                page.wait_for_timeout(700)
                mo = page.evaluate(MOTION)
                check(mo.get("still") and mo.get("greeting") == 0,
                      "挨拶: 止めているページでは振らない（止め絵のまま）",
                      f"roomMotion {mo}")
        finally:
            page.close()


def check_life(browser, port, world, check, errors):
    """32（R99-P4-L2・本人「しっかりアバターが動くように」）: 返事を 1 分以上待っている机の人が散歩先（給湯台・ボード・植物の前）へ
    歩き、同じ机へ戻る。打鍵中の人は動かない・届かない（瞬間移動）は 0・台帳の席の数（フッター）は変わらない。
    時計は debug.step で 30 秒から 5 分ぶん 0.5 秒ずつ進める（1 人あたり 240 秒に 1 回・24 秒）。"""
    # 固定ワールドの「返事待ち」は全員 ❗（受付に並ぶ確認待ち）＝机で返事を待っている人が居ない。
    # 机の 2 人を書き換える: 0003 → 返事を 2 分待っている（散歩の対象）・0007 → 打鍵中（動かない側の対照）
    w = json.loads(json.dumps(world))
    for r in w["roster"]:
        if r["session"] == "sess-fixture-0003":
            r.update(state="waiting", kind="said", verb="", age=120)
        if r["session"] == "sess-fixture-0007":
            r.update(state="working", kind="tool", verb="編集中", age=5)
        for b in r.get("sessions") or []:
            if b.get("session") == "sess-fixture-0003":
                b.update(state="waiting", age=120)
            if b.get("session") == "sess-fixture-0007":
                b.update(state="working", age=5)
    for e in w.get("employees") or []:                 # 台帳（部屋の人の元）は employees から作られる
        if e.get("session") == "sess-fixture-0003":
            e.update(state="waiting", verb="", age=120)
        if e.get("session") == "sess-fixture-0007":
            e.update(state="working", verb="編集中", age=5)
    page, _inject = open_live(browser, port, w, errors, "life", life=True)
    try:
        check_opened(page, "散歩のページ", check)
        here = {a["session"]: a for a in page.evaluate(ACTORS) if not a.get("leaving")}
        # 散歩の対象は部屋と同じ判定で決める（台帳の行と部屋の人は名前が違う＝内訳の行は sess-…b のように分かれる）
        who = page.evaluate("""async () => {
            const d = window.__office.debug;
            const { roomLayout } = await import('/ui/core/pxroom.js');
            const { canStroll } = await import('/ui/core/life.js');
            const { actors } = roomLayout(d.world(), d.board(), null);
            return { walkers: actors.filter((a) => canStroll(a)).map((a) => a.session),
                     typing: actors.filter((a) => a.zone === 'desk' && a.state === 'working').map((a) => a.session) }; }""")
        walkers = [s for s in who["walkers"] if s in here]
        typing = [s for s in who["typing"] if s in here]
        spots = page.evaluate("async () => (await import('/ui/core/pxroom.js')).LIFE_SPOTS.map(s => [s.x, s.y])")
        spot = {tuple(x) for x in spots}
        home = {s: (here[s]["x"], here[s]["y"]) for s in walkers + typing}
        arrived, back, moved, away_max, unreach = set(), set(), set(), 0, 0
        for k in range(0, 541):
            fr = page.evaluate(STEP, 30 + k * 0.5)
            unreach = max(unreach, fr["unreachable"])
            away_max = max(away_max, page.evaluate("() => window.__office.stats().away || 0"))
            for m in fr["people"]:
                s = m["session"]
                if s not in home or m.get("leaving"):
                    continue
                xy = (m["x"], m["y"])
                if xy != home[s]:
                    moved.add(s)
                if xy in spot:
                    arrived.add(s)
                if s in arrived and xy == home[s]:
                    back.add(s)
        check(walkers and set(walkers) <= arrived and set(walkers) <= back and not (set(typing) & moved)
              and unreach == 0 and away_max >= 1,
              f"散歩: 返事待ち {walkers} が散歩先に着いて同じ机へ戻る・打鍵中 {len(typing)} 人は動かない・届かない 0",
              f"返事待ち {walkers} / 着いた {sorted(arrived)} / 戻った {sorted(back)} / 動いた打鍵中 {sorted(set(typing) & moved)} "
              f"/ 届かない {unreach} / away の最大 {away_max}")
    finally:
        page.close()

def check_replay(browser, port, world, check, errors):
    """R105-A: Last login の帯を閉じても live の席が動かず、部屋を覆うカードを開かない。"""
    since = int(time.time()) - 1800
    page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
    page.on("pageerror", lambda e: errors.append(f"pageerror(last login): {e}"))
    page.route("**/api/office*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(world, ensure_ascii=False)))
    page.route("**/api/digest*", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"available": True, "since": since, "totals": {"tasksDone": 2}})))
    page.route("**/api/seen*", lambda route: route.fulfill(status=200, content_type="application/json", body="{}"))
    try:
        page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&life=0", wait_until="domcontentloaded")
        page.bring_to_front()
        page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
        page.evaluate("w => window.__office.inject(w)", world)
        page.wait_for_selector('#cp-login-slot #digest-strip:not([hidden])', timeout=8000)
        before = page.evaluate(ACTORS)
        login = page.evaluate("""() => ({ text: document.querySelector('#digest-strip')?.textContent || '',
          card: document.querySelector('#digest-card')?.hidden,
          replay: document.querySelector('.cpshell').classList.contains('replay-active') })""")
        page.click("#digest-strip")
        page.wait_for_function("() => document.querySelector('#digest-strip').hidden", timeout=2000)
        after = page.evaluate(ACTORS)
        seats = lambda actors: {a["session"]: (a["x"], a["y"], a["zone"]) for a in actors}
        check(login["text"].startswith("Last login") and login["card"] and not login["replay"]
              and seats(before) == seats(after),
              "R105-A 留守中は足元の Last login・クリックで閉じても live の席は同じ・カードは自動で開かない",
              json.dumps(login, ensure_ascii=False))
    finally:
        page.close()


def check_fixed_room(browser, port, page, world, check):
    """18・19: 固定時刻のページ（?t=）。page は mount 済み（basic を入れてある）。"""
    url = f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2"
    # 18. 固定時刻では歩かない: 机の 1 人を ❗ にしても、その場で受付に居る（walking=0）。
    #     画素は「その world だけを見た」新しいページと同じ（履歴で golden が動かない）。選択は両方で同じ人にそろえる
    reps = {r["session"] for r in world["roster"] if len(r.get("sessions") or []) == 1}
    page.evaluate("w => window.__office.inject(w)", world)
    page.wait_for_timeout(200)
    marks = page.evaluate("() => window.__office.debug.roomMarks()")
    target = next((m["key"] for m in marks if m["session"] in reps and m["zone"] == "desk"), None)
    if target is None:
        check(False, "固定時刻では歩かない", f"机に居る 1 人のプロジェクトが無い {marks}")
        return
    w2 = with_attention(world, target)
    page.evaluate("w => window.__office.inject(w)", w2)
    page.wait_for_timeout(200)
    st = page.evaluate(STATS)
    me = next((m for m in page.evaluate("() => window.__office.debug.roomMarks()") if m["key"] == target), None)
    want = page.evaluate("""async (k) => { const { roomLayout } = await import('/ui/core/pxroom.js');
      const d = window.__office.debug; const a = roomLayout(d.world(), d.board(), null).actors.find((x) => x.key === k);
      return a ? [a.x, a.y] : null; }""", target)
    page.evaluate("s => window.__office.debug.select(s)", target)
    page.evaluate("w => window.__office.inject(w)", w2)
    page.wait_for_timeout(200)
    a = page.evaluate(ROOM_PIXELS)
    fresh = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
    try:
        fresh.goto(url, wait_until="networkidle")
        fresh.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
        fresh.evaluate("w => window.__office.inject(w)", w2)
        fresh.wait_for_selector(".cproom", timeout=20000)
        fresh.evaluate("s => window.__office.debug.select(s)", target)
        fresh.evaluate("w => window.__office.inject(w)", w2)
        fresh.wait_for_timeout(300)
        b = fresh.evaluate(ROOM_PIXELS)
    finally:
        fresh.close()
    check(st["walking"] == 0 and st["entering"] == 0 and st["leaving"] == 0 and me and not me["walk"]
          and me["zone"] == "queue" and [me["x"], me["y"]] == want and a == b,
          f"固定時刻では歩かない（{target} を ❗ にするとその場で受付 {want}・walking 0）・画素はその world だけを見たページと同じ",
          f"walking {st['walking']} entering {st['entering']} leaving {st['leaving']} / 本人 {me} / 目的地 {want} / "
          f"新しいページと同じ画素 {a == b}")

    # 19. 頭上の印（固定ワールド cockpit.json）: 期待値は fixture の JSON をここで数える（実装の式を写さない）
    fx = json.loads((FIXTURES / "cockpit.json").read_text(encoding="utf-8"))
    want = expected_marks(fx)
    page.evaluate("w => window.__office.inject(w)", fx)
    page.wait_for_timeout(300)
    st = page.evaluate(STATS)
    ms = page.evaluate("() => window.__office.debug.roomMarks()")
    pix = page.evaluate(MARK_PIXELS)
    got = {k: sum(1 for m in ms if m["mark"] == k) for k in want}
    seen = {k: sum(1 for p in pix if p["mark"] == k and p["miss"] == 0 and p["dots"] > 0) for k in want}
    hidden = [p for p in pix if p["miss"] != 0 or p["dots"] == 0]
    sessions = sorted(r["session"] for r in fx["roster"])
    drawn = sorted(m["session"] for m in ms if not m["missing"])
    check(got == want and seen == want and all(v >= 1 for v in want.values()) and not hidden
          and {k: st["marks"][k] for k in want} == want and st["marks"]["done"] == 0
          and st["missingCells"] == 0 and st["missingTiles"] == 0 and (st["placed"] or {}).get("overflow") == 0
          and drawn == sessions,
          f"頭上の印（cockpit.json）: fixture から数えた {want} と部屋の印が一致・全部のタイルが画素どおりに見えている"
          f"（{sum(seen.values())} 個）・描けないコマ 0",
          f"部屋 {got} / 画素で見えた {seen} / stats {st['marks']} / 欠けた印 {hidden[:3]} / missingCells {st['missingCells']} / "
          f"overflow {(st['placed'] or {}).get('overflow')} / 描いていない {sorted(set(sessions) - set(drawn))[:4]}")
    # 💬 の境（統括の既定 WAIT_MAX_AGE 秒）: 報告して WAIT_MAX_AGE 秒以上待っている人は部屋に居ても 💬 が出ない。
    # 申告（roomMarks の mark）と画素（💬 を出すなら置く場所に 💬 のドットが全部は見えていない）の両方で見る。
    # 数の一致（上）だけだと、境の外の人の 💬 と境の内の人の 💬 の取り違えが素通りする＝名指しで見る
    over = wait_over(fx)
    wp = {p["session"]: p for p in page.evaluate(WAIT_PIXELS, over)}
    loud = [(s, wp.get(s)) for s in over
            if not wp.get(s) or wp[s]["missing"] or wp[s]["mark"] is not None or not wp[s]["dots"] or wp[s]["hit"] >= wp[s]["dots"]]
    check(over and not loud,
          f"💬 の {WAIT_MAX_AGE} 秒の境: 報告して {WAIT_MAX_AGE} 秒以上待っている {len(over)} 人（{', '.join(over)}）は"
          f"部屋に居て 💬 が出ない（区分 {[wp[s]['zone'] for s in over if s in wp]}・画素でも 💬 のドットが揃わない）",
          f"境の外の人 {over or '0 人（fixture に足す＝tools/gen_cockpit_fixture.py）'} / 💬 が出た・描かれていない {loud[:3]}")


# 29 の材料: 3 カラムの形・❗トレイの見出し・トレイの全ボタン（中心で elementFromPoint が当たるか・画面内か）・
# トレイを入れた窓の枠からはみ出した子とトレイの子（列の幅を越えて押し広げられた箱）。hit の相手（at）は外れたときの手がかり。
# R110 骨格 A: トレイは左の列の 04 受付（#win-reception）＝窓は .cpright の決め打ちでなく #attn を囲む .cpwin から引く
TRAY_REACH = """() => {
  const tray = document.querySelector('#attn');
  const col = tray.closest('.cpwin');
  const cr = col.getBoundingClientRect();
  const name = (n) => !n ? null : n.id ? '#' + n.id : (n.className && String(n.className)) || n.tagName;
  // R110-D2: 受付が低い形ではトレイ全体が中でスクロールする（ボタンの列もスクロールで届く）＝人が動かせる箱（overflow が
  // auto/scroll）だけを動かしてボタンを見えている所へ寄せてから測る（scrollIntoView は overflow:hidden の祖先まで動かす）
  const reveal = (e) => {
    for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (!/(auto|scroll)/.test(cs.overflowY) || p.scrollHeight <= p.clientHeight + 1) continue;
      const a = p.getBoundingClientRect(), q = e.getBoundingClientRect();
      const top = a.top + p.clientTop, bottom = top + p.clientHeight;
      if (q.top < top) p.scrollTop -= top - q.top;
      else if (q.bottom > bottom) p.scrollTop += Math.min(q.top - top, q.bottom - bottom);
    }
  };
  const btns = [...tray.querySelectorAll('button')].filter((b) => !b.hidden).map((b) => {
    reveal(b);
    const r = b.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return { label: b.textContent.trim().slice(0, 16), x: Math.round(r.left), y: Math.round(r.top),
      w: Math.round(r.width), h: Math.round(r.height),
      inside: r.width > 0 && r.height > 0 && r.left >= -0.5 && r.top >= -0.5
        && r.right <= innerWidth + 0.5 && r.bottom <= innerHeight + 0.5,
      hit: !!(hit && (hit === b || b.contains(hit))), at: name(hit) };
  });
  const wide = [...col.children, ...(tray.hidden ? [] : tray.children)].filter((e) => !e.hidden).map((e) => {
    const r = e.getBoundingClientRect();
    return { el: name(e), left: Math.round(r.left), right: Math.round(r.right) };
  }).filter((e) => e.right > cr.right + 0.5 || e.left < cr.left - 0.5);
  const shown = (sel) => { const b = tray.querySelector(sel); return !!b && !b.hidden && b.getClientRects().length > 0; };
  tray.scrollTop = 0;
  return { cols: getComputedStyle(document.querySelector('.main')).gridTemplateColumns.split(' ').length,
    col: [Math.round(cr.left), Math.round(cr.right)], hidden: tray.hidden,
    head: tray.querySelector('.trayhead')?.textContent.trim() || '', btns, wide,
    prev: shown('.trayprev'), next: shown('.traynext') };
}"""
# 29 の巡回の起点: world を差し替えた直後のトレイは先頭（attentionQueue の 1 件目）を出す。その見出し（今の言語の文言）と件数
TRAY_FIRST = """async () => {
  const { attentionQueue } = await import('/ui/core/world.js');
  const { T: rawT } = await import('/ui/hud/strings.js'); const T = (await import('/ui/cockpit/plain.js')).plainT(rawT);
  const q = attentionQueue(window.__office.debug.world().agents || []);
  return { n: q.length, first: q.length ? T('tray_head', q[0].name, 1, q.length) : '' };
}"""
# 29 のボタンの数（❗ ごと・巡回の順）。**fixture から独立に決めた数**（描かれたボタンを数えるだけだと、tray.js が
# ボタンを出さなくなっても緑のまま）。内訳: 選択肢（最大 3）か承認まちの定型 3 本＋「自由に書く」1＋前へ・次へ 2。
# R102-11: 許可待ち（掲示 ask.kind=permission）は 許可する／許可しない／自由に書く＋前へ・次へ＝5。
# basic＝議事録アプリ（選択肢 3）→ xpost製品化（承認まち）／cockpit.json＝議事録アプリ（選択肢 2）→ インフラ（承認まち）／
# basic 長文＝basic の選択肢を 3 本の長い文に替えた写し
# R106-1: 「議事録アプリ（長文）」の質問文は箱（6.5em）からはみ出す＝省略の展開ボタンが1本増える（[7, …]）。
# 「xpost製品化」の方は短い定型（実行中 git push）のままなので変わらない
TRAY_BUTTONS = {"basic": [6, 6], "cockpit.json": [5, 5], "basic 長文": [7, 6]}


def check_tray_reach(browser, port, check):
    """29: 3 カラム（幅 1401 以上）の ❗トレイは、❗ を 1 件ずつ巡回して、どの ❗ でも全部のボタンの中心が
    elementFromPoint で当たり、画面内にある（R86-H の型）。R110-D2: 受付が低い形（1440×640）ではトレイ全体が中でスクロール
    する＝ボタンごとに、人が動かせる箱（overflow が auto/scroll）だけを動かして寄せてから測る（中心が当たる＝祖先に切られて
    いない・覆われていない）。トレイを入れた窓（R110＝04 受付）の子は窓の幅に収まる。
    窓が grid なのに列の指定が無いと、暗黙の auto 列がカードの min-content に押し広げられて右が切れる
    （basic で「2 案B」が x=1448・body は overflow:hidden なので 6 の横スクロールの門は緑のまま＝実測）。
    world は basic と cockpit.json（承認まち型の ❗ が居る＝定型の 3 本＋自由に書く）の両方と、basic の名前・質問・選択肢を
    長くした写し（区切りの無い長い語を含む＝見出しとボタンの折り返し・トレイと質問文の丈を試す）。固定時刻のページ。
    ボタンの数は TRAY_BUTTONS（fixture から独立に決めた数）と ❗ ごとに一致し、❗ が 2 件以上なら毎回 前へ・次へ が在る。
    ページは 1 枚で、world は inject で差し替え、形は set_viewport_size で替える（開き直すと重い機械で起動待ちが切れる）。
    差し替えるたびに、トレイが先頭（attentionQueue の 1 件目）から出ることを見出しで確かめる"""
    basic = json.loads(WORLD.read_text(encoding="utf-8"))
    long_word = "/Users/test/realmeetingmemo/src/storage/adapters/sharedFolderAdapterConfiguration.json"
    worlds = [("basic", basic), ("cockpit.json", json.loads((FIXTURES / "cockpit.json").read_text(encoding="utf-8"))),
              ("basic 長文", with_fields(basic, "sess-fixture-0006", disp="議事録アプリ（" + long_word + "）",
                                          question="この設計で確定していいですか？ 保存先は " + long_word + " にまとめ、"
                                          # 40 回＝約 700 字。質問文の丈を決めないと 1280×640 でボタンが画面の下へ出る長さ
                                          + "古い設定は移し替えてから消します。" * 40,
                                          questionOptions=[
                                              {"label": "案A で進める（" + long_word + " を正本にする）", "description": ""},
                                              {"label": "案B: 保存先をローカルと共有フォルダの両方に分けて、同期は夜間にまとめて行う",
                                               "description": ""},
                                              {"label": "もう少し考えてから決めるので、いったん止めて今の状態を報告して",
                                               "description": ""}]))]
    vw0, vh0 = TRAY_SHAPES[0]
    page = browser.new_page(viewport={"width": vw0, "height": vh0}, device_scale_factor=1)
    errs = []
    page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    try:
        page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2", wait_until="networkidle")
        page.wait_for_function("() => window.__office && window.__office.inject", timeout=30000)
        for wname, w in worlds:
            page.set_viewport_size({"width": vw0, "height": vh0})
            page.evaluate("w => window.__office.inject(w)", w)
            page.wait_for_selector(".cproom", timeout=20000)
            page.wait_for_timeout(200)
            tf = page.evaluate(TRAY_FIRST)
            n, want = tf["n"], TRAY_BUTTONS[wname]
            for vw, vh in TRAY_SHAPES:
                label = f"❗トレイ {wname} {vw}×{vh}"
                page.set_viewport_size({"width": vw, "height": vh})
                page.wait_for_timeout(300)
                states, bad, wide, heads, cols = [], [], [], [], set()
                # n 回測って n 回 J を押す＝巡回して先頭へ戻る（次の形も先頭から）
                for _ in range(max(n, 1)):
                    m = page.evaluate(TRAY_REACH)
                    states.append(m)
                    cols.add(m["cols"])
                    heads.append(m["head"])
                    bad += [dict(b, head=m["head"]) for b in m["btns"] if not (b["inside"] and b["hit"])]
                    wide += [x for x in m["wide"] if x not in wide]
                    page.keyboard.press("j")                # 次の ❗ へ（トレイの巡回＝数字キーと同じ経路）
                    page.wait_for_timeout(120)
                counts = [len(s["btns"]) for s in states]
                # 空振りしない: ❗ が 2 件以上・どの ❗ でもトレイが出て、ボタンの数が決めた数どおり・前へ/次へが毎回在る・
                # 巡回で見出しが変わった・起点が先頭（差し替え・形の切り替えで巡回の位置が残っていない）
                vacant = (n < 2 or n != len(want) or any(s["hidden"] or not s["prev"] or not s["next"] for s in states)
                          or counts != want or len(set(heads)) != n or not heads[0].startswith(tf["first"])
                          or cols != {3})
                check(not vacant and not bad and not wide and not errs,
                      f"{label}: 3 カラムで ❗ {n} 件を巡回し、全ボタン {sum(counts)} 個（{'＋'.join(map(str, want))}）の"
                      "中心が当たり画面内・前へ/次へが毎回在る・受付の窓とトレイの子は窓の幅に収まる",
                      f"列の数 {sorted(cols)} / ❗ {n} 件・見出し {heads}（起点 {tf['first']!r}）/ "
                      f"ボタンの数 {counts}（決めた数 {want}）/ 前へ・次へ {[(s['prev'], s['next']) for s in states]} / "
                      f"受付の窓 {states[0]['col'] if states else None} / "
                      f"押せない {[(b['head'][:10], b['label'], b['x'], b['y'], b['w'], b['h'], b['at']) for b in bad[:6]]} / "
                      f"列からはみ出した子 {wide[:4]} / {errs[:2]}")
    finally:
        page.close()


# 29c の材料: トレイの「自由に書く」の中心（押す点）。文言は今の言語の辞書から引く（ボタンの並びの番号に頼らない）
FREE_BTN = """async () => {
  const { T: rawT } = await import('/ui/hud/strings.js'); const T = (await import('/ui/cockpit/plain.js')).plainT(rawT);
  const want = T('opt_free');
  const b = [...document.querySelectorAll('#attn button[data-idx]')]
    .find((x) => !x.hidden && x.getClientRects().length && x.textContent.trim().endsWith(want));
  if (!b) return null;
  const r = b.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, label: b.textContent.trim() };
}"""
# 29c: 開いた指示欄とトレイの矩形・重なり
SHEET_TRAY = """() => {
  const s = document.querySelector('#sheet'), t = document.querySelector('#attn');
  const open = !!s && !s.hidden && s.getClientRects().length > 0;
  const a = open ? s.getBoundingClientRect() : null, b = t && !t.hidden ? t.getBoundingClientRect() : null;
  const R = (r) => r ? `x ${Math.round(r.left)}〜${Math.round(r.right)}・y ${Math.round(r.top)}〜${Math.round(r.bottom)}` : '';
  const lap = !!a && !!b && Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5
    && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5;
  return { open, lap, sheet: R(a), tray: R(b), pos: open ? getComputedStyle(s).position : '' };
}"""


def check_tray_with_sheet(browser, port, check):
    """29c: 3 カラム（幅 1181 以上）の 4 形で、トレイの「自由に書く」から指示欄（#sheet）を開いている間も、❗トレイの全ボタンの
    中心が elementFromPoint で当たり画面内・指示欄の矩形がトレイの矩形に重ならない（29 と同じ物差し TRAY_REACH）。
    指示欄は .main の右に重ねる置き方（absolute）で、hud のまま right 16px・幅 .main の 58% だと右カラムに掛かり、トレイの答え・
    前へ・次へが全部その下に隠れた（1440 で指示欄 x 704〜1424・トレイ x 1141〜1440＝答えるには指示欄を閉じる要があった）。
    形状の門 ㉑（tests/cockpit_shapes_smoke.py）は 1440 と 1194 だけ＝低い 1280×640 と、幅の上限 720 に当たる 1920 はここで見る。
    R110 骨格 A: 指示欄は端末の下の行（static・重ねない）・トレイは左の列の 04 受付＝矩形が重ならないのは置き方の帰結だが、
    hud の幅の則（absolute・grid-row）に負けて指示欄が受付へ流れ込む退行をここで捕まえる。
    固定時刻のページ 1 枚（basic）で、形は set_viewport_size で替える。開くたびに Escape で閉じる（閉じなければ赤）"""
    basic = json.loads(WORLD.read_text(encoding="utf-8"))
    vw0, vh0 = TRAY_SHAPES[0]
    page = browser.new_page(viewport={"width": vw0, "height": vh0}, device_scale_factor=1)
    errs = []
    page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    try:
        page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2", wait_until="networkidle")
        page.wait_for_function("() => window.__office && window.__office.inject", timeout=30000)
        page.evaluate("w => window.__office.inject(w)", basic)
        page.wait_for_selector(".cproom", timeout=20000)
        for vw, vh in TRAY_SHAPES:
            label = f"29c ❗トレイ basic {vw}×{vh}: 3 カラムで指示欄を開いている間も、トレイの全ボタンの中心が当たり画面内・指示欄がトレイに重ならない"
            page.set_viewport_size({"width": vw, "height": vh})
            page.wait_for_timeout(300)
            fb = page.evaluate(FREE_BTN)
            if not fb:
                check(False, label, "空振り: トレイに「自由に書く」のボタンが無い")
                continue
            page.mouse.click(fb["x"], fb["y"])
            try:
                page.wait_for_function("() => !document.querySelector('#sheet').hidden", timeout=3000)
            except Exception:  # noqa: BLE001 — 開かないこと自体を下の判定で赤にする
                pass
            page.wait_for_timeout(200)
            m = page.evaluate(TRAY_REACH)
            g = page.evaluate(SHEET_TRAY)
            bad = [b for b in m["btns"] if not (b["inside"] and b["hit"])]
            vacant = not g["open"] or m["hidden"] or not m["btns"] or m["cols"] != 3
            check(not vacant and not bad and not g["lap"] and not errs,
                  label + (f"（指示欄 {g['sheet']}・トレイ {g['tray']}・ボタン {len(m['btns'])} 個）" if g["open"] else ""),
                  f"指示欄 {'開いた' if g['open'] else '開かない'}（{g['sheet']}・position {g['pos']}）/ トレイ {g['tray']}"
                  f"（hidden={m['hidden']}）/ 列の数 {m['cols']} / 重なり {g['lap']} / "
                  f"押せない {[(b['label'], b['x'], b['y'], b['at']) for b in bad[:4]]} / {errs[:2]}"
                  " → ui/cockpit/style.css の 3 カラムの段で、指示欄（#sheet）を端末の下の升目（grid-area: sheet・static）に置く")
            page.keyboard.press("Escape")
            try:
                page.wait_for_function("() => document.querySelector('#sheet').hidden", timeout=1500)
            except Exception:  # noqa: BLE001
                page.evaluate("() => document.querySelector('#sheetclose')?.click()")
            if not page.evaluate("() => document.querySelector('#sheet').hidden"):
                check(False, f"29c {vw}×{vh}: 指示欄を閉じる", "Escape でも ✕ でも閉じない（次の形は開いたまま測る）")
    finally:
        page.close()


# ── 30. 配線の門（P4-2a: 部屋の倍率の選び直し・承認まちの中央表示・シートの質問文・トレイの固定と即時の描き直し）──
# 部屋の倍率の期待値（統括の裁定・p4gap §3 の表から書く＝実装の式を写さない）: コックピットは丈の上限 480 なので
# R105: 900〜1400 の左の列＝clamp(280px, 100vw − 636px, 440px)＝1024 以上は ×2（1024 で 388px）・1024 未満は ×1。
# 丈では ×1 に落とさない（足りない丈は部屋の中のスクロール）
# R110 骨格 A: 3 カラムの左の列は倍率から幅を決める（184k＋18）＝1920×1080 は ×4（端末 560px と名簿を引いても入る）・
# 1440×900 は ×3 のまま（golden の形・端末 80 桁）。×4 は幅 ≥ 約 1650 から（計画 scalable-cooking-bentley の決めたこと 1）
ROOM_SCALE_WANT = {390: (2,), 834: (3,), 890: (3,), 1024: (2,), 1112: (2,), 1194: (2,),
                   1280: (2,), 1440: (3,), 1920: (4,)}
# 同じページで形を替える順: 1440→390→1440 が芯（×3→×2→×3）。そのあと 1 列・3 カラムの形を一巡して 1440 へ戻る
WIRING_WALK = [(1440, 900), (390, 844), (1440, 900), (834, 1112), (1112, 834), (890, 626), (1024, 768),
               (1194, 834), (1280, 640), (1920, 1080), (390, 844), (1440, 900)]
# 部屋の倍率と寸法・#stage の data-room-scale・画素の要約。logical＝論理 px ごとに 1 画素（S×S のマスの左上）を読んだ絵の
# ハッシュ（倍率に依らない＝×2 と ×3 で同じ絵なら一致）。full＝原寸の全画素のハッシュ。ragged＝S×S のマスの中で色が
# 揃わないマスの数（整数倍で描き直せていれば 0）。ink＝不透明な論理 px の数（描き直さずに空になった canvas を捕まえる）
ROOM_SCALE_NOW = """([LW, LH]) => {
  const c = document.querySelector('.cproom'), S = window.__office.stats().scale, r = c.getBoundingClientRect();
  const out = { scale: S, attrW: c.width, attrH: c.height, cssW: Math.round(r.width), cssH: Math.round(r.height),
    data: document.querySelector('#stage').dataset.roomScale || null, logical: 0, full: 0, ink: 0, ragged: 0 };
  if (c.width !== LW * S || c.height !== LH * S) return out;
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  const px = (o) => ((d[o] << 24) | (d[o + 1] << 16) | (d[o + 2] << 8) | d[o + 3]) >>> 0;
  const mix = (h, v) => Math.imul(h ^ v, 16777619) >>> 0;
  let hl = 2166136261, hf = 2166136261;
  for (let o = 0; o < d.length; o += 4) hf = mix(hf, px(o));
  for (let y = 0; y < LH; y++) for (let x = 0; x < LW; x++) {
    const o = ((y * S) * c.width + x * S) * 4, v = px(o);
    hl = mix(hl, v);
    if (d[o + 3]) out.ink += 1;
    let odd = false;
    for (let dy = 0; dy < S && !odd; dy++) for (let dx = 0; dx < S; dx++) {
      if (px(((y * S + dy) * c.width + x * S + dx) * 4) !== v) { odd = true; break; }
    }
    if (odd) out.ragged += 1;
  }
  out.logical = hl; out.full = hf;
  return out;
}"""
# トレイの見出しと、s の人がキューの何番目か（今の言語の見出しの文言）・キューの並び・中央が映している人
TRAY_WHO = """async (s) => {
  const { attentionQueue } = await import('/ui/core/world.js');
  const { T: rawT } = await import('/ui/hud/strings.js'); const T = (await import('/ui/cockpit/plain.js')).plainT(rawT);
  const q = attentionQueue(window.__office.debug.world().agents || []);
  const i = q.findIndex((a) => a.session === s);
  return { head: document.querySelector('#attn .trayhead')?.textContent.trim() || '',
    want: i < 0 ? null : T('tray_head', q[i].name, i + 1, q.length), order: q.map((a) => a.session),
    selected: window.__office.debug.selected() };
}"""
# 中央の面（#cp-work）の「聞かれていること」の箱
CPQ = """() => [...document.querySelectorAll('#cp-dlg .cpq')].map((b) => ({ ask: b.dataset.ask || null,
  text: (b.querySelector('.cpq-text')?.textContent || '').trim(), shown: b.getClientRects().length > 0 }))"""
# 開いたシートの見出しと質問文の矩形（画面の座標）
SHEET_Q = """() => { const s = document.querySelector('#sheet'), q = s.querySelector('.sheetq');
  const r = q ? q.getBoundingClientRect() : null;
  return { open: !s.hidden, selected: window.__office.debug.selected(), vh: innerHeight, scrollY: Math.round(scrollY),
    q: r ? { top: Math.round(r.top * 10) / 10, bottom: Math.round(r.bottom * 10) / 10, h: Math.round(r.height) } : null,
    text: q ? q.textContent.trim().slice(0, 80) : '' }; }"""


def check_term_theme(browser, port, check, errors):
    """R101（本人「ダーク/ライト切替」「ヘッダー・ベルもドット絵風に」）: 端末の配色のテーマ・アイコン・カーソル。
    ① 既定はダーク（OS に従わない＝golden の決定論）・?theme=light でライトの地・部屋と端末の窓（.t-dark）は暗いまま
    ② iso の .th-dark を root に付けても hud の部品の色（--iso-accent）が変わらない（ラベンダーを持ち込まない）
    ③ ヘッダーの管理ボタンは絵文字でなくドット絵（data-px・::before の box-shadow）・ブランドとボタンの字形に絵文字が無い
       （ゲージのピンの 💳/🔌 などは iso と共用の hud の文言＝iso を変えない裁定のため今回は対象外・バックログ）
    ④ 固定時刻ではカーソルが点いたまま（golden に決まった絵）"""
    import re as _re
    pal = (ROOT / "ui/cockpit/palette.js").read_text(encoding="utf-8")
    term = json.loads(_re.search(r"export const TERM = Object\.freeze\((\{.*?\})\);\n", pal, _re.S).group(1))
    rgb = lambda h: "rgb(" + ", ".join(str(int(h[i:i + 2], 16)) for i in (1, 3, 5)) + ")"   # noqa: E731
    got = {}
    for theme in ("", "light"):
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, color_scheme="light")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror(テーマ): {e}"))
        try:
            page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2" + (f"&theme={theme}" if theme else ""), wait_until="networkidle")
            page.wait_for_selector(".cproom", timeout=20000)
            got[theme or "dark"] = page.evaluate(r"""() => {
              const root = document.querySelector('.ui-cockpit'), cs = (n) => getComputedStyle(n);
              const acc0 = cs(root).getPropertyValue('--iso-accent').trim();
              root.classList.add('th-dark');
              const acc1 = cs(root).getPropertyValue('--iso-accent').trim();
              root.classList.remove('th-dark');
              const btns = [...document.querySelectorAll('.cphead .admin .abtn')].filter((b) => {
                const r = b.getBoundingClientRect(); return !b.closest('[hidden]') && r.width > 0 && r.height > 0; });
              const emoji = /\p{Extended_Pictographic}/u;
              return { bg: cs(root).backgroundColor, stage: cs(document.querySelector('.cpstage')).backgroundColor,
                       acc0, acc1, attr: document.documentElement.getAttribute('data-theme'),
                       icons: btns.filter((b) => b.dataset.px && getComputedStyle(b, '::before').boxShadow !== 'none').length, btns: btns.length,
                       emoji: ((document.querySelector('.cphead .brand').innerText + btns.map((b) => b.innerText).join('')).replace(/⚙/g, '').match(/\p{Extended_Pictographic}/gu) || []).join(''),
                       cursor: root.dataset.cursor || '', curVis: (() => { const c = document.querySelector('.cphead .cur');
                         return !!c && cs(c).visibility !== 'hidden' && c.getBoundingClientRect().width > 0; })() };
            }""")
        finally:
            ctx.close()
    dk, lt = got["dark"], got["light"]
    check(dk["bg"] == rgb(term["dark"]["bg"]) and lt["bg"] == rgb(term["light"]["bg"]) and dk["attr"] == "dark"
          and lt["attr"] == "light" and dk["stage"] == lt["stage"] == rgb(term["dark"]["bg"]),
          "R101 テーマ: 既定ダーク（OS がライトでも）・?theme=light でライトの地・部屋の窓は両方で暗いまま",
          f"dark={dk} light={lt}")
    check(dk["acc0"] and dk["acc0"] == dk["acc1"] and lt["acc0"] == lt["acc1"],
          "R101 iso の .th-dark を付けても hud の部品の色が変わらない（ラベンダーを持ち込まない）", f"{dk['acc0']} → {dk['acc1']}")
    check(dk["btns"] == 3 and not dk["emoji"],
          f"R105-A ヘッダーの管理ボタンは新しいセッション・スマホ・設定の 3 個（指定の ⚙ を除き絵文字無し）", f"{dk}")
    check(dk["curVis"] and dk["cursor"] in ("", "on"), "R101 固定時刻ではプロンプトのカーソルが点いたまま（golden は決まった絵）", f"{dk}")


# b4-mac: コックピットの面に絵文字が残っていないか（トレイ・タブ・一覧・中央・足元・ヘッダー・シート・モーダル）
NO_EMOJI = """(sels) => sels.map((sel) => { const ns = [...document.querySelectorAll(sel)];
  const text = ns.map((n) => n.innerText || '').join(' ').replace(/⚙/g, '');
  return { sel, n: ns.length, len: text.trim().length, emoji: (text.match(/\\p{Extended_Pictographic}/gu) || []).join('') }; })"""


# R105-A: 小さな丸い点と、個体を表すモノグラムだけが角 0 の例外。
# 例外は名前で列挙する（丸い要素を動的に全部許すと、ピルを戻した退行まで通る）。
CORNER_EXCEPTIONS = (".dot, .swatch, .mono, .traymono, .zdot, .gadot, .sq-ish, .ldot, .adot, "
                     ".mkeydot, .vendor-dot, "
                     # R121 §13.11: 作業ビュー（.act の中）だけの角丸＝吹き出し・カード・許可待ち・札・点・指示の入力欄・名簿の点
                     ".act .act-user, .act .act-card, .act .act-pending, .act .act-chip, .act .act-dots > i, "
                     ".act #cp-compose, .cppreview-dots > i")
# R110: 文字の段は 4 つの token（決めたこと 2・既定 14/12/16/20・⚙ 文字の大きさで段ごと替わる）。xs/sm/md/lg は廃止。
# 計算値は root の 4 token の値の集合に入り、最小は small 以上（ページは既定の大きさで開く＝token の値もここで固定する）
FONT_TOKENS = {"--cp-font-small": 12, "--cp-font-base": 14, "--cp-font-title": 16, "--cp-font-display": 20}
POLISH = r"""(exceptions) => {
  const root = document.querySelector('.ui-cockpit');
  const shown = (e) => { const s = getComputedStyle(e), r = e.getBoundingClientRect();
    return !e.closest('[hidden]') && s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
  const who = (e) => e.id ? '#' + e.id : e.tagName.toLowerCase() + '.' + [...e.classList].join('.');
  const corners = [], fonts = [], sizes = new Set(); let count = 0, texts = 0;
  // 文字の段は root の 4 token から読む（値を門に写さない＝⚙ で段が替わっても同じ物差し）
  const rs = getComputedStyle(root), tokenNames = ['--cp-font-small', '--cp-font-base', '--cp-font-title', '--cp-font-display'];
  const tokens = Object.fromEntries(tokenNames.map((k) => [k, parseFloat(rs.getPropertyValue(k))]));
  const steps = Object.values(tokens).filter(Number.isFinite), small = tokens['--cp-font-small'];
  for (const e of [root, ...root.querySelectorAll('*')].filter(shown)) {
    count += 1;
    if (!e.matches(exceptions)) {
      for (const pseudo of [null, '::before', '::after']) {
        const s = getComputedStyle(e, pseudo);
        if (pseudo && (s.content === 'none' || s.content === 'normal' || s.display === 'none')) continue;
        const radii = ['borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomLeftRadius', 'borderBottomRightRadius']
          .map((k) => s[k]);
        if (radii.some((r) => r.split(/\s+/).some((v) => parseFloat(v) !== 0)))
          corners.push({ who: who(e) + (pseudo || ''), radii });
      }
    }
    // 文字を持つ要素の計算値を見る。canvas は文字の段に混ぜない。
    if ([...e.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim())) {
      const size = parseFloat(getComputedStyle(e).fontSize); texts += 1; sizes.add(size);
      if (!steps.includes(size) || !(size >= small)) fonts.push({ who: who(e), size });
    }
  }
  return { count, texts, corners, fonts, sizes: [...sizes].sort((a,b) => a-b), tokens };
}"""


def check_r105_polish(page, world, check):
    """角・文字の段を閉じた画面だけでなく指示欄・設定・ヘルプでも測る。送信はしない。"""
    def audit(label):
        p = page.evaluate(POLISH, CORNER_EXCEPTIONS)
        check(p["count"] > 30 and not p["corners"],
              f"R105-A {label}: cockpit の計算後の角は 0（例外 {CORNER_EXCEPTIONS}）",
              json.dumps(p["corners"][:12], ensure_ascii=False))
        check(p["texts"] > 10 and p["tokens"] == FONT_TOKENS and not p["fonts"],
              f"R110 {label}: 文字は small/base/title/display の 4 段の token（12/14/16/20）の値だけ・最小は small（実際 {p['sizes']}）",
              json.dumps({"tokens": p["tokens"], "fonts": p["fonts"][:12]}, ensure_ascii=False))

    audit("通常画面")
    header = page.evaluate("""() => ({
      buttons: [...document.querySelectorAll('.cphead button')].map((b) => ({ id: b.id, aria: b.getAttribute('aria-label') || '' })),
      attn: document.querySelectorAll('#cp-attn-slot').length,
      foot: document.querySelectorAll('#cp-foot-end').length,
      attnKids: [...(document.querySelector('#cp-attn-slot')?.children || [])].map((e) => e.id),
      footKids: [...(document.querySelector('#cp-foot-end')?.children || [])].map((e) => e.id || e.className)
    })""")
    check(len(header["buttons"]) >= 3 and all(b["aria"].strip() for b in header["buttons"]),
          "R105-A ヘッダーの全ボタンに aria-label（閉じたメニューも含む）", json.dumps(header, ensure_ascii=False))
    check(header["attn"] == header["foot"] == 1 and header["attnKids"] == ["cp-attn-chip"]
          and header["footKids"] == ["cp-ver", "cp-feedback"],
          "R105 あなた待ちと足元の差し込み口は 1 個ずつ（あなた待ち＝#cp-attn-chip・足元＝版と［フィードバック］）",
          json.dumps(header, ensure_ascii=False))
    row = page.locator('#agents .pxrow[data-session]').first
    row.click()
    page.wait_for_selector('#sheet:not([hidden])')
    audit("指示欄")
    page.keyboard.press("Escape")
    for button, label in (("#btn-settings", "設定"), ("#btn-help", "ヘルプ")):
        page.locator(button).dispatch_event("click")
        page.wait_for_selector('#modalwrap:not([hidden])')
        audit(label)
        page.keyboard.press("Escape")
    # R128 #4: ⚙ の Bot の節（ui/hud/bots.js）は cockpit の設定からも届く＝┤ Bot ├ の群の中に一覧の案内と追加の欄（id・名前・役＋登録）。
    # 一覧を読み終えた後の節も角 0・文字の段の門を通る（読み込みの前の 1 枚だけを測らない）。追加・消音・削除の往復は app_smoke（同じ部品）
    page.locator("#btn-settings").dispatch_event("click")
    page.wait_for_selector('#modal .cp-settings-group .mbots .mbotform', timeout=10000)
    page.wait_for_function("""() => { const t = document.querySelector('#modal .mbots > .mnote')?.textContent || '';
        return t && !/読み込み中|Loading/.test(t); }""", timeout=10000)
    bots = page.evaluate("""() => { const b = document.querySelector('#modal .mbots'), g = b?.closest('.cp-settings-group');
      return { head: g?.querySelector('.msubtitle')?.textContent || '', groups: document.querySelectorAll('#modal .cp-settings-group').length,
        own: b ? b.querySelectorAll('.msub').length : -1, lead: b?.querySelector(':scope > .mnote')?.textContent || '',
        inputs: b ? b.querySelectorAll('.mbotform input').length : 0, submit: b ? b.querySelectorAll('.mbotform button[type=submit]').length : 0,
        err: b?.querySelector('.mbotform ~ .mboterr')?.hidden }; }""")
    check(bots["head"] == "┤ Bot ├" and bots["groups"] == 6 and bots["own"] == 0 and bots["inputs"] == 3 and bots["submit"] == 1
          and bots["err"] is True and ("Bot はまだいません" in bots["lead"] or "体（64 体まで）" in bots["lead"]),
          "R128 #4 cockpit の ⚙ に ┤ Bot ├ の群（一覧の案内・追加の欄 3 つと登録・見出しは群の 1 つだけ・設定は 6 群）",
          json.dumps(bots, ensure_ascii=False))
    audit("設定の Bot の節")
    page.keyboard.press("Escape")

    empty = copy(world)
    empty["roster"] = []; empty["employees"] = []
    page.evaluate("w => window.__office.inject(w)", empty)
    page.wait_for_timeout(100)
    entry = page.evaluate("""() => {
      const root = document.querySelector('.ui-cockpit'), group = document.querySelector('#cp-empty-entry');
      const shown = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
        return !e.closest('[hidden]') && s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
      return { groups: root.querySelectorAll('#cp-empty-entry').length, central: !!group?.closest('#cp-dlg'),
        start: group ? [...group.querySelectorAll('#cp-empty-start')].filter(shown).length : 0,
        demo: group ? [...group.querySelectorAll('#cp-empty-demo')].filter(shown).length : 0,
        demoLabel: group?.querySelector('#cp-empty-demo')?.textContent || '',
        // R106-4: 名簿の空の案内（R110 で一覧を名簿へ統合＝#cp-roster の .pxempty・cp_roster_empty と同じ文）はこの
        // 「空オフィス」状態では中央の入口と常に同じ条件（members() が空＝roster/employees が空）で出る＝旧版の別の入口の
        // 重複ではなく、名簿自身の案内（copy-consistency-03）。重複扱いの対象から外す（.cpempty は旧 一覧の案内の名残の番人）
        oldEntrances: [...root.querySelectorAll('.onboard, .stagehint, #agents .cpempty')].filter(shown).length };
    }""")
    check(entry["groups"] == 1 and entry["central"] and entry["start"] == entry["demo"] == 1
          and entry["demoLabel"].strip() and entry["oldEntrances"] == 0,
          "R105-A 空の入口は中央の 1 群（Claude を起こす・デモを見る）", json.dumps(entry, ensure_ascii=False))
    audit("空の画面")
    page.evaluate("w => window.__office.inject(w)", world)


def check_b4mac(browser, port, check, errors):
    """b4-mac（Mac 側の磨き）: どれも「壊しても golden と既存の門が緑のまま」だった所。
    ① 絵文字が無い: トレイ・タブ・一覧・中央・端末の足元・ヘッダー・シート・設定とヘルプのモーダルの innerText に
       \\p{Extended_Pictographic} が 0（対象の要素の数と文字数も見る＝0 件で緑にしない）
    ② ヘッダー: 「新しいプロジェクト」と「セッションを増やす」は別の印（plus と robot）・? は help の印・閉じるの aria-label は言語の語
    ③ シートの道具（端末・音・閉じる）は印（data-px・::before の影）
    ④ ヘルプはコックピットの操作（?theme=・ロボのクリック）を案内し、3D の操作（ホイール・?tilt）を案内しない
    ⑤ ❗の種類: 許可待ち（sess-cp-07）のタブ・名簿の行の状態（.stext）に「許可」、質問（sess-cp-09）に「質問」・中央の箱にコマンド（ask.title）と
       答えのボタン 3 本（トレイと同じ番号と文言）
    ⑥ 最後のタブ（17 本目）を選ぶと、そのタブが #cp-tabs の見える範囲に入り、左の端に隠れている本数「‹ N」が出る
    ⑦ live: 中央の会話は同じ相手でも動いたら読み直す（/api/session/activity の差分が呼ばれ、新しい発言が出る）
    ⑧ live: 部屋のロボに乗せると名前と状態の札（DOM）・台帳の該当行に .hov・指のカーソル"""
    fx = json.loads((FIXTURES / "cockpit.json").read_text(encoding="utf-8"))
    who = {e["session"]: e for e in fx["employees"]}
    ask_s, q_s = "sess-cp-07", "sess-cp-09"
    title = ((who.get(ask_s) or {}).get("ask") or {}).get("title") or ""
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page.on("pageerror", lambda e: errors.append(f"pageerror(b4-mac): {e}"))
    try:
        page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2", wait_until="networkidle")
        page.wait_for_function("() => window.__office && window.__office.inject", timeout=30000)
        page.evaluate("w => window.__office.inject(w)", fx)
        page.wait_for_selector(".cproom", timeout=20000)
        page.wait_for_timeout(300)
        page.evaluate("(s) => window.__office.debug.select(s, { focus: false })", ask_s)
        page.wait_for_timeout(200)
        center = page.evaluate("""() => { const b = document.querySelector('#cp-dlg .cpq');
          return b ? { head: b.querySelector('.cpq-head')?.textContent || '', text: b.querySelector('.cpq-text')?.textContent || '',
            btns: [...b.querySelectorAll('.cpq-acts button')].map((x) => x.textContent),
            tray: [...document.querySelectorAll('#attn button[data-idx]')].map((x) => x.textContent) } : null; }""")
        # R110: 一覧のカードは名簿へ統合＝❗ の種類の語は名簿の行の状態（.stext）が言う
        tags = page.evaluate("""(ss) => ss.map((s) => ({ s,
          tab: document.querySelector(`#cp-tabs .cptab[data-session="${s}"] .attn`)?.textContent || '',
          card: document.querySelector(`#agents .pxrow[data-session="${s}"] .stext`)?.textContent || '' }))""", [ask_s, q_s])
        perm_ok = (center and ("許可" in center["head"]) and title and title in center["text"]
                   and len(center["btns"]) == 3 and all(b in center["tray"] for b in center["btns"])
                   and tags[0]["tab"].startswith("!") and "許可" in tags[0]["tab"] and "許可" in tags[0]["card"]
                   and "質問" in tags[1]["tab"] and "質問" in tags[1]["card"])
        check(bool(perm_ok), f"b4-mac⑤ ❗の種類: 許可待ちのタブ「{tags[0]['tab']}」・名簿「{tags[0]['card']}」・"
              f"質問「{tags[1]['tab']}」・名簿「{tags[1]['card']}」・中央の箱に"
              f"コマンド（{title[:16]}…）と答え {len(center['btns']) if center else 0} 本（トレイと同じ）",
              json.dumps({"center": center, "tags": tags}, ensure_ascii=False))
        # ⑥ 最後のタブ
        last = page.evaluate("""() => { const t = [...document.querySelectorAll('#cp-tabs .cptab')]; return t.length ? t[t.length - 1].dataset.session : null; }""")
        page.evaluate("(s) => window.__office.debug.select(s, { focus: false })", last)
        page.wait_for_timeout(400)
        tab = page.evaluate("""() => { const h = document.querySelector('#cp-tabs'), t = h.querySelector('.cptab.sel');
          const a = h.getBoundingClientRect(), b = t ? t.getBoundingClientRect() : null;
          const l = document.querySelector('#cp-tabs-l');
          return { n: h.children.length, over: h.scrollWidth > h.clientWidth, inView: !!b && b.left >= a.left - 1 && b.right <= a.right + 1,
                   left: l && !l.hidden ? l.textContent : '' }; }""")
        check(tab["inView"] and (not tab["over"] or tab["left"].startswith("‹")),
              f"b4-mac⑥ 最後のタブ（{tab['n']} 本目）を選ぶと見える範囲に入る・左に隠れた本数「{tab['left']}」",
              json.dumps(tab, ensure_ascii=False))
        # ② ③ ヘッダーとシートの道具の印
        icons = page.evaluate("""() => { const px = (sel) => { const n = document.querySelector(sel);
            return n ? { px: n.dataset.px || '', drawn: getComputedStyle(n, '::before').boxShadow !== 'none',
                         label: n.getAttribute('aria-label') || n.title || '' } : null; };
          return { add: px('#cp-add'), newproj: px('#btn-newproj'), hire: px('#btn-hire'), help: px('#btn-help'), term: px('#sheetterm'),
                   menu: [...document.querySelectorAll('#cp-add-menu [role=menuitem]')].map((b) => b.id),
                   snd: px('#sheetsnd'), close: px('#sheetclose'), modalclose: document.querySelector('#modalclose')?.getAttribute('aria-label') || '' }; }""")
        hdr_ok = (icons["add"] and icons["add"]["label"] and icons["help"] and icons["help"]["label"]
                  and set(icons["menu"]) == {"btn-newproj", "btn-hire", "btn-launch"}
                  and icons["modalclose"] in ("閉じる", "Close"))
        check(hdr_ok, "R105-A ヘッダー: 3 つの起動動作は 1 つのメニュー・ヘルプと閉じるに読み上げの語",
              json.dumps(icons, ensure_ascii=False))
        sheet_ok = all(icons[k] and icons[k]["px"] and icons[k]["drawn"] for k in ("term", "snd", "close"))
        check(sheet_ok, "b4-mac③ シートの道具（端末・音・閉じる）は印（data-px）", json.dumps(icons, ensure_ascii=False))
        # ① 絵文字: 面 → シートを開く → 設定のモーダル → ヘルプのモーダル
        page.evaluate("(s) => window.__office.debug.select(s, { focus: false })", q_s)
        page.wait_for_timeout(200)
        faces = page.evaluate(NO_EMOJI, ["#attn", "#cp-tabs", "#cp-roster", "#cp-dlg", ".cptermfoot", ".cphead", "#agents"])
        page.click(f'#agents .pxrow[data-session="{q_s}"]')
        page.wait_for_timeout(300)
        faces += page.evaluate(NO_EMOJI, ["#sheet"])
        page.keyboard.press("Escape")
        page.click("#btn-settings")
        page.wait_for_selector("#modalwrap:not([hidden])", timeout=5000)
        faces += [dict(f, sel="設定 " + f["sel"]) for f in page.evaluate(NO_EMOJI, ["#modal"])]
        page.keyboard.press("Escape")
        page.locator("#btn-help").dispatch_event("click")
        page.wait_for_selector("#modalwrap:not([hidden])", timeout=5000)
        faces += [dict(f, sel="ヘルプ " + f["sel"]) for f in page.evaluate(NO_EMOJI, ["#modal"])]
        help_text = page.evaluate("() => document.querySelector('#modal').innerText")
        # R105: URL の引数（?theme= 等）は「上級」の折りたたみ（details）の中＝innerText には出ない。閉じる前に中身ごと textContent で取る
        help_all = page.evaluate("() => document.querySelector('#modal').textContent")
        adv = page.evaluate("() => !!document.querySelector('#modal details.cp-help-advanced')")
        page.keyboard.press("Escape")
        bad = [f for f in faces if f["emoji"] or not f["n"] or not f["len"]]
        check(not bad, f"b4-mac① 絵文字が無い（{len(faces)} 面: トレイ・タブ・一覧・中央・足元・ヘッダー・台帳・シート・設定・ヘルプ）",
              json.dumps(bad, ensure_ascii=False))
        check(adv and "?theme=" in help_all and "?theme=" not in help_text and "?tilt" not in help_all and "ホイール" not in help_all,
              "b4-mac④ ヘルプはコックピットの操作を案内する（キーの表・URL の引数 ?theme= は「上級」に畳む・3D のホイール/?tilt は無い）",
              help_all[:240])
        # R105-B: メンバー先行βの入口。足元に版と［フィードバック］→ 診断情報（数と状態だけ）を見せてからコピー・スペシャルサンクス。
        # ヘッダーの「あなた待ち N」は ❗ の数と同じで、押すと答えのボタン（#attn の中）へ。?ui= で開いただけでは様式を保存しない
        beta = page.evaluate("""() => {
          const chip = document.querySelector('#cp-attn-chip'), ver = document.querySelector('#cp-foot-end .cp-ver');
          const fb = document.querySelector('#cp-feedback');
          const n = window.__office.dumpWorld().agents.filter((a) => a.attention).length;
          return { chipShown: !!chip && !chip.hidden, chipText: chip?.textContent || '', n,
            ver: ver?.textContent || '', fb: fb?.textContent || '', fbLabel: fb?.getAttribute('aria-label') || '',
            saved: localStorage.getItem('aioffice.ui.v3') };
        }""")
        page.click("#cp-attn-chip")
        page.wait_for_timeout(200)
        beta["focusInTray"] = page.evaluate("() => !!document.activeElement?.closest('#attn')")
        page.click("#cp-feedback")
        page.wait_for_selector("#modal .beta-diag", timeout=5000)
        page.wait_for_function("() => !document.querySelector('#modal .beta-copy').disabled", timeout=8000)
        beta.update(page.evaluate("""() => { const m = document.querySelector('#modal');
          return { title: m.querySelector('.mtitle')?.textContent || '', diag: m.querySelector('.beta-diag')?.textContent || '',
            thanks: m.querySelector('.beta-thanks')?.textContent || '', heads: [...m.querySelectorAll('.beta-h')].map((h) => h.textContent) }; }"""))
        page.keyboard.press("Escape")
        diag = beta.get("diag", "")
        check(beta["chipShown"] == (beta["n"] > 0) and (beta["n"] == 0 or str(beta["n"]) in beta["chipText"])
              and beta["focusInTray"] and "β" in beta["ver"] and beta["fb"] and beta["fbLabel"]
              and beta["saved"] is None and "診断" in beta.get("title", "")
              and ("AI Office 診断情報" in diag or "診断情報を取れませんでした" in diag) and "画面: cockpit" in diag
              and "/Users/" not in diag and len(beta.get("heads", [])) == 2 and beta.get("thanks") not in ("", "…"),
              "R105-B β の入口: ヘッダーの「あなた待ち N」（❗ の数・押すと答えへ）・足元の版と［フィードバック］→ 診断（パスなし・画面の形つき）"
              "とスペシャルサンクス・?ui= で開いただけでは保存しない",
              json.dumps({k: (v[:160] if isinstance(v, str) else v) for k, v in beta.items()}, ensure_ascii=False))
    finally:
        page.close()

    # ⑦ ⑧ live
    calls = []
    lp, inject = open_live(browser, port, fx, errors, "b4-mac live")
    try:
        said = {"n": 2}

        def activity_route(route):
            calls.append(route.request.url)
            after = "after=" in route.request.url
            start = 2 if after else 0
            rows = [{"k": "u" if i % 2 == 0 else "a", "t": f"発言 {i}", "ts": 100 + i}
                    for i in range(start, said["n"])]
            route.fulfill(status=200, content_type="application/json; charset=utf-8",
                          body=json.dumps({"ok": True, "rows": rows, "cur": {"off": said["n"], "h": "a" * 16},
                                           "beg": None, "top": True, "gap": False, "reset": not after}, ensure_ascii=False))
        lp.route("**/api/session/activity*", activity_route)
        lp.evaluate("(s) => window.__office.debug.select(s, { focus: false })", "sess-cp-01")
        lp.wait_for_function("() => document.querySelectorAll('#cp-dlg .act-user, #cp-dlg .act-text').length === 2", timeout=8000)
        first = len(calls)
        lp.wait_for_timeout(3300)                       # 読み直しは 3 秒に 1 回まで
        said["n"] = 3
        moved = copy(fx)
        for e in moved["employees"] + moved.get("roster", []):
            if e.get("session") == "sess-cp-01":
                e["feed"] = ["編集中 router_v2.ts"] + list(e.get("feed") or [])[1:]
                e["verb"], e["target"] = "編集中", "router_v2.ts"
        inject(moved)
        try:
            lp.wait_for_function("() => [...document.querySelectorAll('#cp-dlg .act-user-text, #cp-dlg .act-text')].some((n) => n.textContent === '発言 2')", timeout=8000)
            fresh = True
        except Exception:                               # noqa: BLE001
            fresh = False
        check(fresh and len(calls) >= first + 1,
              f"b4-mac⑦ 中央の会話は同じ相手でも動いたら読み直す（取得 {first}→{len(calls)} 回・新しい発言が出る）",
              f"calls={calls}")
        # ⑦b 同じデータがもう一度来ても会話の欄を作り直さない（2026-11-14 本番: 毎秒の作り直しで、選んだ文が 1 秒で外れた）・状態が替われば描き直す
        lp.evaluate("""() => { const n = [...document.querySelectorAll('#cp-dlg .act-user-text, #cp-dlg .act-text')].find((x) => x.textContent === '発言 2');
          window.__cpNode = n; if (n) getSelection().selectAllChildren(n); }""")
        chosen = lp.evaluate("() => getSelection().toString()")
        inject(moved)
        inject(moved)
        lp.wait_for_timeout(300)
        kept = lp.evaluate("() => ({ alive: !!window.__cpNode && window.__cpNode.isConnected, sel: getSelection().toString() })")
        idle = copy(moved)
        for e in idle["employees"] + idle.get("roster", []):
            if e.get("session") == "sess-cp-01":
                e["state"] = "idle"
        inject(idle)
        lp.wait_for_timeout(300)
        redrawn = not lp.evaluate("() => !!window.__cpNode && window.__cpNode.isConnected")
        lp.evaluate("() => getSelection().removeAllRanges()")
        inject(moved)
        check(chosen == "発言 2" and kept["alive"] and kept["sel"] == chosen and redrawn,
              "b4-mac⑦b 同じデータがもう一度来ても会話の欄を作り直さない（選んだ文が残る）・状態が替われば描き直す",
              f"選んだ {chosen!r} / 同じデータの後 {kept} / 状態の替わりで描き直した {redrawn}")
        # ⑧ ホバー
        pt = lp.evaluate("(s) => window.__office.debug.roomPoint(s)", "sess-cp-12")
        hov = None
        if pt:
            lp.mouse.move(pt["left"], pt["top"] + 20)
            lp.wait_for_timeout(250)
            hov = lp.evaluate("""() => { const t = document.querySelector('.cphover');
              return { shown: !!t && !t.hidden, text: t ? t.textContent : '', rows: document.querySelectorAll('#agents .pxrow.hov').length,
                       cursor: getComputedStyle(document.querySelector('#viewport')).cursor }; }""")
        name = (who.get("sess-cp-12") or {}).get("title") or (who.get("sess-cp-12") or {}).get("dept") or ""
        check(bool(hov and hov["shown"] and name and name in hov["text"] and hov["rows"] >= 1 and hov["cursor"] == "pointer"),
              f"b4-mac⑧ 部屋のロボに乗せると名前と状態の札「{(hov or {}).get('text', '')}」・台帳の行に .hov・指のカーソル",
              json.dumps({"pt": pt, "hov": hov, "name": name}, ensure_ascii=False))
    finally:
        lp.close()


def check_wiring(browser, port, check, errors):
    """30: P4-2a の配線を 1 枚のページ（固定時刻・cockpit.json・タッチ可）で確かめる。どれも「壊すと verify が緑のまま」
    だった配線（別モデルの検収で route の変異を配って実測）で、固定時刻のページ＝データの更新が来ない形で見る
    （live だと次のデータで描き直されて、描き直しを忘れた変異が緑になる）。
    ① 形が変わると部屋の倍率を選び直して描き直す: 1440→390→1440 で stats().scale 3→2→3・canvas 552→368→552・
       #stage[data-room-scale]・論理 px の絵が同じ（×2 の画素も同じ絵）・1440 へ戻すと inject し直さずに原寸の画素が
       元と一致。続けて 1 列・3 カラムの形を一巡し、どの形も ROOM_SCALE_WANT の倍率
    ② 承認まちの ❗（question が無く ask がある人＝sess-cp-07）を選ぶと、中央に .cpq[data-ask=permission] と
       許可待ちのツール名。対照: 質問の ❗（sess-cp-09）は .cpq[data-ask=question] と質問文
    ⑤ 部屋のロボを選ぶと（tray.show）、次のデータを待たずにトレイがその人になる
    ④ 合わせた人（固定）は、承認まちの昇格（approvalMin）で並びが入れ替わってもトレイに出続け、J で固定が外れる
    ③ スマホ 390 で台帳の行を実タッチ（touchscreen.tap）→ シートの質問文が画面の中（revealHead）"""
    fx = json.loads((FIXTURES / "cockpit.json").read_text(encoding="utf-8"))
    who = {e["session"]: e for e in fx["employees"]}
    ask_s, q_s = "sess-cp-07", "sess-cp-09"
    ask_p, q_p = who.get(ask_s) or {}, who.get(q_s) or {}
    ask_tool = (ask_p.get("ask") or {}).get("tool") or ""
    # 前提（fixture が変わったら空振りではなく赤）: 07 は質問文が無く承認フックの掲示（permission）がある・昇格の手前。
    # 09 は質問文がある
    pre = (not ask_p.get("question") and (ask_p.get("ask") or {}).get("kind") == "permission" and ask_tool
           and 0 < (ask_p.get("approvalMin") or 0) < 15 and q_p.get("question"))
    check(bool(pre), "30 の前提: cockpit.json の sess-cp-07 は質問文の無い承認まち（ask=permission）・sess-cp-09 は質問",
          f"07 {ask_p} / 09 {q_p}")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, has_touch=True)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror(配線): {e}"))
    page.on("console", lambda m: errors.append(f"console.error(配線): {m.text}") if m.type == "error" else None)
    try:
        page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2", wait_until="networkidle")
        page.bring_to_front()
        page.wait_for_function("() => window.__office && window.__office.inject", timeout=30000)
        page.evaluate("w => window.__office.inject(w)", fx)
        page.wait_for_selector(".cproom", timeout=20000)
        page.wait_for_timeout(300)

        # ① 形 → 倍率 → 描き直し
        base, rows, bad = None, [], []
        for i, (vw, vh) in enumerate(WIRING_WALK):
            allowed = ROOM_SCALE_WANT[vw]
            want = max(allowed)
            page.set_viewport_size({"width": vw, "height": vh})
            try:        # ResizeObserver → 次の rAF で選び直す。替わらなければ 3 秒で諦めて、実際の値で赤にする
                page.wait_for_function(f"() => window.__office.stats().scale === {want}", timeout=3000, polling=50)
            except Exception:                              # noqa: BLE001 - playwright の TimeoutError
                pass
            page.wait_for_timeout(250)
            m = page.evaluate(ROOM_SCALE_NOW, [ROOM_W, ROOM_H])
            if base is None:
                base = m
            same_back = vw != 1440 or m["full"] == base["full"]
            scale = m["scale"]
            ok = (scale in allowed and m["attrW"] == ROOM_W * scale and m["cssW"] == ROOM_W * scale
                  and m["attrH"] == ROOM_H * scale and m["data"] == str(scale) and m["ragged"] == 0
                  and m["ink"] > ROOM_W * ROOM_H // 2 and m["logical"] == base["logical"] and same_back)
            rows.append(f"{vw}:×{m['scale']}/{m['cssW']}px")
            if not ok:
                bad.append({"step": i, "shape": f"{vw}×{vh}", "want": allowed, **{k: m[k] for k in (
                    "scale", "attrW", "cssW", "data", "ragged", "ink")},
                    "sameLogical": m["logical"] == base["logical"], "sameFull": m["full"] == base["full"]})
        core = rows[:3]
        check(not bad and base["scale"] == 3,
              f"30① 形が変わると部屋の倍率を選び直して描き直す（{' → '.join(core)}・論理の絵は同じ・1440 へ戻すと"
              f"原寸の画素が元と一致）。続けて {len(WIRING_WALK) - 3} 形も期待の整数倍率（2 カラムは×1/×2）",
              f"食い違い {bad[:4]} / 歩いた形 {rows}")
        page.set_viewport_size({"width": 1440, "height": 900})
        page.wait_for_timeout(200)

        # ② ⑤ 部屋のロボを選ぶ → 中央の問い・トレイがその人（固定時刻＝次のデータは来ない）
        def click_robot(s):
            pt = page.evaluate("(s) => window.__office.debug.roomPoint(s)", s)
            if pt:
                page.mouse.click(pt["left"], pt["top"])
                page.wait_for_timeout(150)
            return bool(pt)

        start = page.evaluate(TRAY_WHO, q_s)
        clicked_q = click_robot(q_s)
        on_q = page.evaluate(TRAY_WHO, q_s)
        cpq_q = page.evaluate(CPQ)
        clicked_a = click_robot(ask_s)
        on_a = page.evaluate(TRAY_WHO, ask_s)
        cpq_a = page.evaluate(CPQ)
        want_q = q_p.get("question") or "?"
        ok_q = (clicked_q and on_q["selected"] == q_s and len(cpq_q) == 1 and cpq_q[0]["ask"] == "question"
                and cpq_q[0]["shown"] and want_q in cpq_q[0]["text"])
        ok_a = (clicked_a and on_a["selected"] == ask_s and len(cpq_a) == 1 and cpq_a[0]["ask"] == "permission"
                and cpq_a[0]["shown"] and ask_tool in cpq_a[0]["text"]
                and ("許可" in cpq_a[0]["text"] or "permission" in cpq_a[0]["text"].lower()))
        check(ok_q and ok_a,
              f"30② 承認まちの ❗（{ask_s}・質問文なし）を選ぶと中央に .cpq[data-ask=permission]「{cpq_a[0]['text'] if cpq_a else ''}」"
              f"（対照: 質問の ❗ は data-ask=question と質問文）",
              f"質問の人 {on_q['selected']}: {cpq_q} / 承認まちの人 {on_a['selected']}: {cpq_a}（ツール {ask_tool!r}）")
        # 押している間は会話の欄を描き直さない（ui/hud/press_hold.js・2026-11-14 本番で見つけた: データのたびに作り直す＝押した
        # ボタンが離す前に外れるとクリックが消える）。押す → データ → 同じボタンのまま → ボタンの外で離す（押さずに済ませる）→ 描き直しが走る
        pressed = page.evaluate("""() => { const b = document.querySelector('#cp-dlg .cpq-acts button'); if (!b) return null;
          window.__pressed = b; const r = b.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; }""")
        held = gone = False
        if pressed:
            page.mouse.move(pressed["x"], pressed["y"])
            page.mouse.down()
            page.evaluate("w => window.__office.inject(w)", with_fields(fx, ask_s, approvalMin=1))
            page.wait_for_timeout(250)
            held = page.evaluate("() => window.__pressed.isConnected")
            page.mouse.move(4, 4)
            page.mouse.up()
            try:
                page.wait_for_function("() => !window.__pressed.isConnected", timeout=5000)
                gone = True
            except Exception:
                gone = False
        check(bool(pressed) and held and gone, "30⑥ 押している間に次のデータが来ても会話の欄の答えのボタンは外れない・離すと描き直す",
              f"ボタン {pressed} / 押している間に残った {held} / 離した後に描き直した {gone}")
        page.evaluate("w => window.__office.inject(w)", fx)
        page.wait_for_timeout(150)

        # 起点が 09（先頭）で、07 を選んだ瞬間にトレイが 07 へ替わる（描き直しを次のデータに任せない）
        ok5 = (start["want"] and start["head"].startswith(start["want"]) and start["order"] == [q_s, ask_s]
               and on_a["want"] and on_a["head"].startswith(on_a["want"]))
        check(ok5, f"30⑤ 部屋のロボを選ぶと、次のデータを待たずにトレイがその人になる（{start['head'][:24]} → {on_a['head'][:24]}）",
              f"起点 {start} / 選んだ後 {on_a}")

        # ④ 固定: 07 を選んだまま、07 の approvalMin を昇格の値へ上げて並びを入れ替える → トレイは 07 のまま。J で外れる
        page.evaluate("w => window.__office.inject(w)", with_fields(fx, ask_s, approvalMin=30))
        page.wait_for_timeout(250)
        moved = page.evaluate(TRAY_WHO, ask_s)
        page.keyboard.press("j")
        page.wait_for_timeout(150)
        after_j = page.evaluate(TRAY_WHO, q_s)
        ok4 = (moved["order"] == [ask_s, q_s] and moved["want"] and moved["head"].startswith(moved["want"])
               and after_j["want"] and after_j["head"].startswith(after_j["want"]))
        check(ok4, f"30④ 合わせた人は並びが入れ替わってもトレイに出続け（{moved['head'][:24]}）、J で固定が外れる"
                   f"（→ {after_j['head'][:24]}）",
              f"入れ替え後 {moved}（並びは {[ask_s, q_s]} のはず）/ J の後 {after_j}")
        page.evaluate("w => window.__office.inject(w)", fx)
        page.wait_for_timeout(200)

        # ③ スマホ 390 で台帳の行を実タッチ → シートの質問文が画面の中
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(400)
        pt = page.evaluate("""(s) => { const r0 = document.querySelector(`#agents .pxrow[data-session="${s}"]`);
          if (!r0) return null; r0.scrollIntoView({ block: 'center' }); const r = r0.getBoundingClientRect();
          return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }""", q_s)
        if pt:
            page.wait_for_timeout(150)
            page.touchscreen.tap(pt["x"], pt["y"])
            page.wait_for_timeout(500)
        sq = page.evaluate(SHEET_Q)
        q = sq["q"] or {}
        ok3 = (bool(pt) and sq["open"] and sq["selected"] == q_s and q.get("h", 0) > 0 and want_q in sq["text"]
               and q["top"] >= -0.5 and q["bottom"] <= sq["vh"] + 0.5)
        check(ok3, f"30③ 390 で台帳の行を実タッチ → シートの質問文が画面の中（y {q.get('top')}〜{q.get('bottom')}・画面の丈 {sq['vh']}）",
              f"行 {pt} / シート {sq}")
    finally:
        ctx.close()


def main():
    from playwright.sync_api import sync_playwright

    world = json.loads(WORLD.read_text(encoding="utf-8"))
    ng, errors = 0, []

    def check(ok, label, detail=""):
        nonlocal ng
        print(f"  {'✓' if ok else '✗'} {label}{'' if ok else f' — {detail}'}")
        if not ok:
            ng += 1

    # ── 7. 色の正本が 1 本 ────────────────────────────────────────────
    # R101: 色は端末の配色（palette.js の TERM）。UI_COLORS は TERM.dark から引き、style.css の --cp-* は --t-* を指す。
    # **値ではなく名前で**突き合わせる（--cp-x: var(--t-y) と UI_COLORS.x: TERM.dark.y の y が同じ）＋ 0 件で緑にしない
    # （旧: RAMP.x[n] の正規表現で拾う形のまま UI_COLORS を直接の値にすると、拾える物が 0 件で黙って緑になった）。
    css = (ROOT / "ui/cockpit/style.css").read_text(encoding="utf-8")
    pal = (ROOT / "ui/cockpit/palette.js").read_text(encoding="utf-8")
    ui_block = pal[pal.index("export const UI_COLORS"):pal.index("export const SESSION_COLORS")]
    want = dict(re.findall(r'"?([\w-]+)"?:\s*TERM\.dark(?:\.|\[")([\w-]+)', ui_block))
    got = dict(re.findall(r"--cp-([\w-]+):\s*var\(--t-([\w-]+)\);", css))
    alias = {"alert": "attn"}                                  # UI_COLORS.alert ＝ TERM の attn（❗）
    bad = [f"{k}: css=--t-{got.get(k)} palette=TERM.dark.{v}" for k, v in want.items()
           if k in got and got[k] != v]
    missing = [k for k in want if k not in got]
    raw = re.findall(r"--cp-(?:bg|panel|panel-2|line|line-soft|ink|ink-soft|ink-faint|accent|ok|alert|warn|danger|stage):\s*#", css)
    check(len(want) >= 12 and not bad and not missing and not raw and alias["alert"] == want.get("alert"),
          f"色の正本が 1 本（style.css の --cp-* → --t-* == palette.js の UI_COLORS → TERM.dark・{len(want)} 色）",
          f"ずれ {bad} / CSS に無い {missing} / 生の値 {raw} / UI_COLORS から拾えた {len(want)} 色")

    # ── 29a. R105-A: 幅の段は 900 未満・900〜1400・1400 超 ──────────────────
    # 3 カラムだけに効かせる段は、1 列の段 (max-width: 1180px) の**補集合**で書く。(min-width: 1181px) と書くと、
    # CSS 幅が 1180〜1181 の小数（ブラウザのズーム・高 DPI）でどちらの段にも当たらず、画面は 3 カラムのまま
    # トレイだけが 96px の丈と中スクロールに戻る（P4-1 の検収）。境目の数字が 2 か所に分かれるのも同じ穴。
    # スマホの段（1 列の中の幅 600 以下）はコンテナ問い合わせで書く＝@container も数える。許すのはヘッダー（cphead）と
    # 台帳（cpledger）の (max-width: 600px) の文字列と、R110-D1 の 2 カラムの 2 つ（名簿の窓の丈 cproster・部屋と名簿の見出しの幅
    # cpsec＝どちらも 2 カラムの段の中でだけ問い合わせ先を置く）だけ。コメントは先に消して読む。
    # ここは文字列の段しか見ない: 第 B 波で 601〜624 がヘッダーはスマホ・台帳は 6 列に混ざった原因は、文字列ではなく
    # 問い合わせ先（余白 12px の .cphead）だった＝文字列が 600 のままでも起きた（第 D 波の検収）。境が画面 600 の 1 本かは
    # 形状の門 ⑭（tests/cockpit_shapes_smoke.py・実描画で「ヘッダーの段・台帳の段・幅 ≤600」の一致）が見る
    media, containers = [], []
    for f in sorted((ROOT / "ui" / "cockpit").glob("*.css")):
        m, c = width_stages(f.read_text(encoding="utf-8"))
        media += m
        containers += c
    stray = [q for q in media if q not in MEDIA_OK]
    stray_c = [q for q in containers if q not in CONTAINER_OK]
    check(ONE_COL in media and any("1400px" in q for q in media) and not stray and not stray_c,
          f"文字列の段: 幅の段は 1 列 {ONE_COL}・900〜1400 の 2 カラム・1400 超の 3 カラム・@container は "
          f"{' と '.join(CONTAINER_OK)} だけ（900/1400 の境で小数幅の隙間なし・コメントは読まない・@media {len(media)}・"
          f"@container {len(containers)}。境が画面 600 の 1 本かは形状の門 ⑭）",
          f"補集合でない幅の段 {stray} / 許していないコンテナ問い合わせ {stray_c} / 幅の段 {media} / @container {containers}")

    # ── 29b. 色付きの左右の帯を置かない（デザイン方針・静的）──────────────────────
    # 選んだカードの `box-shadow: inset 2px 0 0 var(--session)` は実質の色付きの左バーだった（P4-2b で撤去）。
    # 戻しても見た目の門（golden）は「前の絵に戻った」だけで、方針を見る機械が無かった。形状の門 ⑫ は計算値で同じことを見る
    bands = []
    for f in sorted((ROOT / "ui" / "cockpit").glob("*.css")):
        bands += side_bands(f.read_text(encoding="utf-8"), f.name)
    for f in sorted((ROOT / "ui" / "cockpit").glob("*.js")):
        if f.name not in ("cells.js", "tiles.js"):             # 生成物（ドットの矩形のデータ）
            bands += js_side_styles(f.read_text(encoding="utf-8"), f.name)
    check(not bands, "色付きの左右の帯を置かない（ui/cockpit の CSS: 左右の罫は 1px の中立の罫だけ・inset の影は x 0・"
          "横向きのグラデーションと細い棒の ::before/::after 無し／JS は左右の罫・影を差さない）",
          " / ".join(bands[:4]) + " → 選択・❗・引用は背景の明度差と中立の 1px の罫（inset 0 0 0 1px）で示す")

    # ── 9. 生成物が生成器と一致 ─────────────────────────────────────────
    # --check＝書かずに突き合わせる（design/ と ui/cockpit/ の両方）。生成器を実行して上書きする
    # 形だと、手で直した生成物をこの門が自分で消し、verify のリトライで緑に戻る＝何も見ていない。
    stale = []
    # design/ は公開物に無い（tools/_publish_excludes.sh）。開発ツリー（tools/publish_snapshot.sh が在る＝公開物・配布物から除外している。CLAUDE.md は受け取った人が /init で作りうるので印にしない）では必須
    gens = ("gen_cells.py", "gen_tiles.py") if (ROOT / "design").is_dir() else ()
    if not gens and (ROOT / "tools/publish_snapshot.sh").is_file():
        stale.append("design/ が無い（開発ツリーでは生成器が正本）")
    elif not gens:
        print("  ⚠ design/ は公開物に含めない＝生成物と生成器の突き合わせは省略")
    for gen in gens:
        r = subprocess.run([sys.executable, str(ROOT / "design" / gen), "--check"],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            stale.append(f"{gen} --check exit {r.returncode}: "
                         + " / ".join((r.stdout + r.stderr).strip().splitlines()[:4]))
    check(not stale, "ドットは生成器が正本（手で書き換えていない）", "; ".join(stale))

    port = free_port()
    proc = start_server(port)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=SWIFTSHADER)
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console",
                    lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
            url = f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2"
            page.goto(url, wait_until="networkidle")
            page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
            page.evaluate("w => window.__office.inject(w)", world)
            page.wait_for_selector(".cproom", timeout=20000)
            page.wait_for_timeout(500)

            check_r105_polish(page, world, check)

            # 1. 4 面が同時に生きている（R110: 一覧のカードは名簿の行へ統合＝4 面目は受付の窓の ❗トレイか INBOX ZERO）
            faces = page.evaluate("""() => {
              const shown = (e) => !!e && !e.closest('[hidden]') && e.getClientRects().length > 0;
              const rec = document.querySelector('#win-reception');
              return {
                room: !!document.querySelector('.cproom'),
                rows: document.querySelectorAll('#agents .pxrow').length,
                tabs: document.querySelectorAll('.cptab').length,
                reception: shown(rec) && (shown(rec.querySelector('#attn')) || shown(rec.querySelector('#cp-inbox-zero'))),
              };
            }""")
            check(faces["room"] and faces["rows"] > 0 and faces["tabs"] > 0 and faces["reception"],
                  "4 面が同時に生きている（部屋・名簿・端末・受付）", json.dumps(faces))

            # 2. 整数倍・原寸
            geo = page.evaluate("""() => {
              const c = document.querySelector('.cproom');
              const r = c.getBoundingClientRect();
              return { attrW: c.width, attrH: c.height, cssW: Math.round(r.width),
                cssH: Math.round(r.height),
                rendering: getComputedStyle(c).imageRendering };
            }""")
            integral = (geo["attrW"] % ROOM_W == 0 and geo["attrH"] % ROOM_H == 0
                        and geo["cssW"] == geo["attrW"] and geo["cssH"] == geo["attrH"])
            check(integral and geo["rendering"] == "pixelated",
                  "部屋は整数倍・原寸・pixelated（割合で縮めない）", json.dumps(geo))

            # 8. 確認待ちが最初に選ばれ、部屋では受付に立っている
            attn = page.evaluate("""() => {
              const sel = window.__office.debug.selected();
              const tab = [...document.querySelectorAll('.cptab')].find(t => t.dataset.session === sel);
              return { sel, selHasAttn: !!tab?.querySelector('.attn') };
            }""")
            check(attn["selHasAttn"], "確認待ち（❗）が最初に選ばれている", json.dumps(attn))

            # 28. 固定時刻のページの「動きを止める」ボタン: 部屋は動かない＝「固定表示」で押せない（見本と同じ）。
            #     押せる「動きを止める」・aria-pressed=false のままだと、止まっている部屋について嘘を言う
            fb = page.evaluate(MOTION_BTN)
            check(button_fixed(fb),
                  f"固定時刻のページのボタンは『{fb['fixed']}』で押せない（aria-pressed={fb['pressed']}・disabled={fb['disabled']}）",
                  f"ボタン {fb}（『{fb['fixed']}』・disabled・aria-pressed=true のはず）")

            # 4. 部屋のロボをクリック → 中央が切り替わる
            picked = page.evaluate("""() => {
              const before = window.__office.debug.selected();
              const other = [...document.querySelectorAll('.cptab')]
                .map(t => t.dataset.session).find(s => s !== before);
              const pt = window.__office.debug.roomPoint(other);
              return { before, other, pt };
            }""")
            if picked["pt"]:
                page.mouse.click(picked["pt"]["left"], picked["pt"]["top"])
                page.wait_for_timeout(250)
                after = page.evaluate("() => window.__office.debug.selected()")
                check(after == picked["other"],
                      "部屋のロボをクリック → 中央の端末が切り替わる",
                      f"{picked['before']} → {after}（期待 {picked['other']}）")
            else:
                check(False, "部屋のロボをクリック → 中央の端末が切り替わる", "座標が取れない")

            # 5. 台帳の行にホバー → 部屋が光る は live ページの節（12）で見る。このページは固定時刻＝
            #    1 回しか描かないので、ホバーしても絵が変わらない（前は「ホバーの状態を持ったか」だけを見て
            #    緑だった＝光が手前の什器に隠れて見えていなくても通っていた）。

            # 6. 形状 6 つ
            for w, h in SHAPES:
                page.set_viewport_size({"width": w, "height": h})
                page.wait_for_timeout(300)
                m = page.evaluate("""() => {
                  const box = s => { const e = document.querySelector(s); if (!e) return 0;
                    return Math.round(e.getBoundingClientRect().height); };
                  return { ledger: box('#cp-roster'), term: box('.cpterm'),
                    rows: document.querySelectorAll('.pxrow').length,
                    xscroll: document.documentElement.scrollWidth > window.innerWidth + 1 };
                }""")
                ok = (not m["xscroll"] and m["ledger"] >= LEDGER_MIN and m["term"] >= TERM_MIN
                      and m["rows"] > 0)
                check(ok, f"形状 {w}×{h}: 横スクロール無し・台帳と端末が潰れない", json.dumps(m))
            page.set_viewport_size({"width": VIEWPORT["width"], "height": VIEWPORT["height"]})
            # 29. 3 カラムの ❗トレイは全部のボタンが押せる（受付の窓が切れない）
            check_tray_reach(browser, port, check)
            # 29c. 3 カラムで指示欄を開いている間も ❗トレイが押せる（指示欄が右カラムに掛からない）
            check_tray_with_sheet(browser, port, check)
            # 30. 配線（倍率の選び直し・承認まちの中央表示・シートの質問文・トレイの固定と即時の描き直し）
            check_wiring(browser, port, check, errors)
            check_term_theme(browser, port, check, errors)
            check_b4mac(browser, port, check, errors)

            # 3. canvas に文字を描かない（fillText を throw に差し替えても組み上がる）
            page2 = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            hard = []
            page2.on("pageerror", lambda e: hard.append(str(e)))
            page2.add_init_script("""
              const P = CanvasRenderingContext2D.prototype;
              for (const k of ['fillText', 'strokeText']) {
                P[k] = function () { throw new Error('canvas に文字を描いている: ' + k); };
              }
            """)
            page2.goto(url, wait_until="networkidle")
            page2.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
            page2.evaluate("w => window.__office.inject(w)", world)
            page2.wait_for_selector(".cproom", timeout=20000)
            page2.wait_for_timeout(400)
            drew = page2.evaluate("() => document.querySelectorAll('.pxrow').length")
            check(not hard and drew > 0,
                  "canvas に文字を描かない（名前も数字も DOM の仕事）", "; ".join(hard[:2]))
            page2.close()

            check_fixed_room(browser, port, page, world, check)
            check_live_room(browser, port, world, check, errors)
            check_motion(browser, port, world, check, errors)
            check_reduced(browser, port, world, check, errors)
            check_greet(browser, port, world, check, errors)
            check_life(browser, port, world, check, errors)
            check_replay(browser, port, world, check, errors)

            check(not errors, "console.error / pageerror = 0", "; ".join(errors[:3]))
            browser.close()
    finally:
        proc.terminate()

    print("✅ cockpit スモーク green" if not ng else f"❌ cockpit スモーク {ng} 件")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
