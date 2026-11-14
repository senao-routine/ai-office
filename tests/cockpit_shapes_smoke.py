#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99 Phase 4（P4-3）: 形状の門。タッチの 5 形状で「❗ に答えられるか」を実タッチで確かめる（マウスの対照 3 形状・デモ 2 形状）。

なぜ新しく作るか:
  tests/cockpit_smoke.py の形状の検査（6 形状）が見ているのは「ページの横スクロールが無い」と「台帳と端末の丈」だけ。
  2026-09-25 の棚卸し（shapes の読み手の実測）では、4 形状すべてで次の問題が緑のまま通った。
  - ❗トレイが 848〜1139px 下にあり、答えるボタンの丈が 21px
  - スマホで部屋の右 1/3 が、部屋の中の横スクロールの先に隠れる
  - 台帳の「いま」列が 35px になり、1 文字ずつ縦に積まれる（行の丈 285px）
  - ゲージの詳細を開いても 1 画素も見えない
  - 部屋のロボをタップしても、トレイがその人に移らない
  この門は、それらを全部 touchscreen.tap（マウスではなく実タッチ）で見る。

形状（形ごとに新しいコンテキストで開き直す＝has_touch・is_mobile・device_scale_factor）:
  live（?ui=cockpit）: iPad 縦 834×1112（dsf 2）・iPad 横 1112×834（dsf 2）・折りたたみ 890×626（dsf 2）・スマホ 390×844（dsf 3）・
    iPad 分割 612×900（dsf 2・Split View の半分＝1 列の段の一番狭い形: 2 段のヘッダー＋6 列の台帳。スマホの段の境は
    ヘッダーも台帳も画面 600 の 1 本＝境の両側を 390 とここで見る。1 本かは ⑭ が実描画で見る）。
    対照はマウスの 3 形状: 1440×900（dsf 1）・細い窓 390×844（dsf 2・has_touch=False）・3 カラムの下端 1194×834（dsf 1＝hud の
    max-width:1279 の指示欄の則が効く帯。1440 には写らない）。対照にはタッチの寸法（44px）を課さない。
  デモ（?ui=cockpit&demo=1）: スマホ 390×844（dsf 3・英語のまま）と iPad 縦 834×1112（dsf 2・日本語に切り替えて）。⑨ と ⓪ だけを見る。

端末を出すかの軸（統括の裁定・2026-09-25）: **「デモかどうか」だけ**。幅・タッチ・pointer では決めない。
  コックピットの本番は 127.0.0.1 からしか開けない＝常に Mac の前。窓が細くてもタッチでも端末は出す。
  デモ（?demo=1 と meta office-demo）では端末の画面（#cp-xterm とタブ列）を畳み、足元（#cp-term-note）に理由を書く。
  許可待ちの表示（#cp-work・#cp-dlg）はデモでも残す。＋ から起こす案内（「読み取り専用（＋ から…）」）はデモで出さない。
  → live の全形状で「端末が出る」を課し（マウスの細い窓も＝幅で畳む実装はここで赤）、デモの 2 形状で「畳んで理由を書く」を課す。

世界と通信（どこにも書かない）:
  - live ページ（?t= 無し）。/api/office は固定ワールド tests/fixtures/world/cockpit.json を返し、inject も同じ world。
  - ゲージは tests/fixtures/status_board/basic.json。会話・ダイジェスト・タイムライン・/api/events は空の応答。
  - 端末は /api/term/list（claude 1 本・sess-cp-09 に紐づけ）と /api/term/stream（hello＋out）を偽装する＝本物の pty を起こさない。
  - **GET 以外は全部 route で横取りし、path と本文を記録して {ok:true} を返すだけ**（テストサーバーにも ~/.claude にも届かない）。
  - わざと落とすのは 2 つだけ: ⑧'' の答えの POST（500）と ⑮ の /api/office（503・落ちている間は /api/events が poke も送る＝
    ページが自分のポーリングで続けて取りに来る）。⓪ はこの 2 つの資源の失敗だけを出どころの path と status で見分けて数えない。
  - デモは静的ホストと同じ条件にする: /api/* は全部 404（叩いたこと自体を記録する）。world はデモ同梱の ui/demo/world.json。

見えている矩形（①②⑥⑨ の共通の物差し・VIS_JS の clipRect）:
  要素の矩形を、包含ブロックの鎖に居て overflow が visible でない祖先の内寸で切ったもの（absolute の子は包含ブロックまでの
  祖先に切られない＝飛ばす）。中心の 1 点が当たっても、祖先に一部を切られたボタンは ① で赤、② は見えている寸法で測る。
  畳まれていること（デモの ⑨）も同じ物差しで見る＝hidden 属性ではなく「箱が無い・visibility・見えている面積 0」。
  （hidden 属性を CSS の display が上書きすると、属性だけ見る判定は「畳んだ」と嘘をつく）

見ること（形ごと・番号は統括の P4-3 の表と同じ）:
  ① ❗トレイが最初の画面内: スクロール 0 で、最初の答えのボタンが祖先にも画面にも切られずに収まり中心が当たる（答えのボタンが
     1 つ以上ある）。トレイのボタン（答え・前へ・次へ）は全部、ページを動かさずトレイの中のスクロール（R110-D2＝受付が低い
     2 カラムではボタンの列もスクロールで届く）で寄せれば、祖先にも画面にも切られずに収まり中心が elementFromPoint で当たる
  ② トレイのボタンが 44×44 以上で、中心が当たる（対照は当たりだけ）。置き場所は ① が見るので、ここは人が指で動かせる
     スクロール（overflow が auto/scroll の容れ物とページ）だけで画面に入れてから、見えている寸法と見えている中心で測る。
     scrollIntoView は使わない（overflow:hidden の祖先まで動かす＝人が指でできない操作）。
     ※ この固定ワールドでは reveal と scrollIntoView で結果に差は出ない。切れて押せないボタンを捕まえるのは ①（祖先の切れ）
  ③ 部屋の中に横スクロールが無い（canvas から .cpstage までの容れ物）。canvas が画面にも、切り抜く祖先にも切られていない
  ③' ページ（文書・body・#app）に横スクロールが無い。部屋の外の原因（閉じたゲージの詳細が translateX で右へ漏れる等）は
     ③ に混ぜない＝はみ出している要素を名指しして別に赤にする
  ④ 部屋の canvas の CSS 寸法が、その形の期待の倍率 k で 184k×160k。期待は門に書いた値（実装の式を写さない）:
     コックピットは丈の上限 480 ＝ 390 幅は ×2、834・1112・890・1194 以上は ×3。
     canvas の画素数は CSS 寸法の整数倍で、pixelated
  ④' 部屋の箱（.cpstage）の丈: 1 列（.main の列が 1 本）では canvas の丈と同じ（×2 に ×3 の 480 を課すと下に 160px の空き）。
     3 カラムは canvas の丈以下（低い画面では中でスクロールしてよい）。段は .main の列の数で見る（形の幅から決めない）
  ⑤ 台帳の行（見えている .pxrow）の丈が全部 100px 以下（R110 の 2 段の行の最大 97px＋3・配達の札の段は引く・ROW_MAX の注記）で、
     1 行以上見えている。
     **いま（c-detail）の見えている幅**が全部の行で
     max(96px, 行の中身の幅 × 1/5) 以上（全形状）。丈で縦書き化を見られるのは、いまを折り返しで伸ばす 3 カラムだけ。
     1 列とスマホの段はいまを -webkit-line-clamp: 2 で切る＝いまの列を 35px に潰しても行は 42px のまま（質問は数字しか
     読めない）で、丈の検査は緑だった（2026-09-25 の第 C 波の検収: 834・612 で 35px、390 で 24px、2px）。下限の実測は
     定数 DETAIL_FRAC の注記（612 の 0.23 と 3 カラムの 0.22 が最小＝比は 1/5）
  ⑤' 幅 600 以下の形（裁定: スマホの台帳は中身を消さずに畳む）: 祖先に切られずに見えている行ごとに、記号・プロジェクト・状態・
     いま・証拠・経過（c-mono・c-project・c-state・c-detail・c-evd・c-age）が箱を持ち visible で、見えている丈 > 0・
     **見えている幅 ≥ min(scrollWidth, 32px)**（読める幅）・文字が空でない。いま・経過・証拠の上端がプロジェクトの下端より下
     （2 段目）。見出しの並べ替えのボタン（.pxsort）が 3 個見える。行の丈だけでは「いま・状態・証拠を display:none にして
     畳んだことにする」が緑だった（hide_detail）。「見えている面積 > 0」では幅 2px のセルも緑だった。折り返すセル（いま）は
     scrollWidth が箱の幅に従う＝細い列はここでは捕まらない（⑤ のいまの幅の下限が見る）
  ⑥ ヘッダーのゲージの要約をタップする → 詳細（#gauges）が**スクロール 0 で画面内に、中身まで**見える
     （裁定: 詳細は overflow の外へ出して画面内に収める。「画面内」はスクロール 0 で見る＝縦も横も。人が寄せれば見える、は違う）。
     撮る間だけ部屋を止める（reduced_motion を emulate＝本人の「動きを止める」と同じ口。止まったことを debug.roomMotion().still で
     確かめ、止まらなければ空振りの赤）。開く前も開いた後もスクロール 0 のまま・寄せない（reveal しない）。次を全部課す:
     - 閉じている間の命中が 0（基準＝開く操作の効果を測れること・格子は下と同じく元の矩形全体へ打つ）
     - 箱: #gauges の元の矩形が 0 ≤ left・right ≤ 画面の幅・0 ≤ top・bottom ≤ 画面の丈（丈が画面の丈を超える詳細は「収まらない」
       の赤＝画面基準の max-height で中をスクロールさせる）。見えている矩形（clipRect）が元の矩形と一致（祖先に切られない）。
       ③' はページの横スクロールしか見ない＝左の外へ出た分と、#app が切って隠した右の分は ③' では捕まらない
       （2026-09-25 のレビュー: 390 で詳細が x −61〜297 に出ても ③' と旧い ⑥ が緑）＝ここで見る
     - 見えている丈（見えている矩形を画面で切った丈）≥ min(scrollHeight, 画面の丈の 1/3)。max-height を包含ブロックの % で
       書き違えると 1440 で 380×34 の帯に潰れるが、格子（25/25）も箱全体の画素の比も満たした（2026-09-25 の検収の pct）
     - 命中: 5×5 の格子を**元の矩形全体**へ打ち、24/25 以上が当たる（画面の外の点は外れ）
     - #gauges と祖先の opacity の積 ≥ 0.9・visibility が visible
     - 中身: 最初のゲージ行＝見出し（.gtitle）・プロバイダの行（.gprovhead か .grow）・最初の棒（.gbar）がそれぞれ、祖先（#gauges
       自身のスクロール箱を含む）に切られず・画面内で・見えている中心が elementFromPoint で当たり・visible で opacity の積 ≥ 0.9。
       箱の検査だけでは、子を visibility:hidden にした空のガラスの箱と、細い帯に潰れた箱が緑だった（検収の kids_hidden・collapsed）
     - 画素: 開く前に 2 回・開いた後に 1 回、スクロール 0 で撮る。**中身の 3 つの見えている矩形を合わせた矩形**（箱の背景の寄与を
       外す）の止まっている画素（開く前の 2 枚で動かない画素）のうち、開いた後に変わった割合が 2% 以上。
       これは「透明な詳細（背景も文字も透明）」だけを捕まえる番人（中身の有無は上の中身の検査が見る）。
       2026-09-25 実測（第 B 波の CSS）: 本物はタッチの形で 8.0〜12.9%・細い窓 14.6%・1194 は 76.9%・1440 は 94.3%（3 カラムは
       詳細の背景と下の面の明るさの差まで入る）。透明（背景・文字・棒を透明）は 0.0%。止めても 0 とは限らない（端末のカーソルの点滅・時計の
       文字）＝「> 0」ではなく比で見る。かつての「> 0」は、1 列の形で開いた詳細が部屋に重なり、ロボの歩きだけで満たされた
  ⑥' 撮り終えて reduced_motion を戻すと部屋が動きに戻る（戻らないまま進むと、後の ⑦⑧ を止まった部屋で測ったことになる）
  ⑦（指）部屋の ❗ でない止まっているロボの、描いた矩形の 6px 外を touchscreen.tap → その人が選ばれる
     （当たりは最寄り・半径 22 CSS px。描いた矩形だけの当たりに戻すと赤。ほかの人とは 14px 以上離れた点を選ぶ）
  ⑦ 部屋の ❗ のロボ（トレイがいま出していない方）を touchscreen.tap → トレイがその人になる。
     ⑦' そのとき指示欄（#sheet）は開かない（tray.focusSession は指示欄まで開き、実機ではキーボードが上がる。
     合わせるだけの口 tray.show(session) を使う＝p4gap §3）
  ⑧ トレイの最初の答えを touchscreen.tap → 記録した答えの POST が 1 通。
     /api/approval/reply（ask がある ❗）か /api/instruct・session はその人（⑦ でタップした人）・
     許可待ち（ask.kind=permission）の 1 番は allow・本文なし（loopback＝Mac の前の人間＝掟 7 の範囲・R102-11）／
     質問は deny＋固定ワールドの期待の文（門に直書き＝辞書のキー名への化けも赤）
  ⑧' 答えた直後の配達の手応え（#toast の新しい .tmsg「… への配達 📨投函済み」）が、**その時のスクロール位置のまま**画面内
     （元の矩形が 0〜画面の幅・丈）・祖先に切られず・中心が当たり・opacity の積 ≥ 0.9。
     **長い名前**でも（③'' の後、全角 42 字の名前の世界でトレイの答えをもう 1 回押す＝⑧'（長い名前））。固定ワールドの短い名前
     （幅 240px）だけでは横の条件が一度も試されず、折り返しの則を消す退行（hud の nowrap に戻る）が緑だった（第 D 波の検収の
     toast_nowrap: 390 で x −146〜536）。トーストの文言に長い名前が無ければ空振りの赤
  ⑧'' ⑧ の前に、答えの POST を 500 にして同じ答えを押す → 失敗のトースト（.tmsg.err「配達失敗: …」・500 の error を載せる）が
     同じ物差しで画面内（失敗ではトレイは変わらない＝続けて ⑧ を押せる）。トーストは 2.6 秒で消える＝押す前にページの中に
     見張り（MutationObserver）を付けて、出る遷移の後に測る。1 列では hud の .toast（.main 基準の absolute・bottom 180px）が
     ページの丈まで伸びた .main の下端に出て、答えた手応えも失敗もスクロール 0 の画面に出なかった（第 C 波の検収・390 で y 1700）。
     **長い error** でも（⑧''（長い error）: 長い名前の世界で 500＋空白の無い和文 53 字を足した error＝LONG_ERROR）
  ⑨ 端末:
     - live（全 8 形状）: #cp-xterm が見え、xterm の中身（.xterm-screen／.xterm-rows）が見えている矩形で 80px 以上、
       その中心が当たり、端末の面（#cp-term）からはみ出さない（面を丈 0 に畳んで中身だけ外へ描く、は赤）。
       タブ列と ＋ も見える。足元はデモの理由ではない
     - デモ（2 形状）: #cp-xterm・その中の .xterm・タブ列・＋ が見えない／/api/term/* を叩かない。
       足元（#cp-term-note）が理由を書く＝日本語は裁定の文「デモには端末がありません（手元の Mac で ＋ から起こせます）」、
       英語は demo と Mac を含む文。「読み取り専用（＋ から…）」「打てます」は出さない。
       許可待ちの表示は畳まない＝#cp-work が見えて ❗ の問い（.cpq）がある・#cp-dlg の箱が消されていない
  ⑩ トレイの「自由に書く」をタップ → 指示欄（#sheet）がスクロール 0 で画面内に出て（上端が 0〜画面の丈−44）、画面の横に収まり、
     部屋の箱（#stage）の丈が開く前と変わらない（±1px）。閉じて元へ戻す（閉じなければ赤）。
     1 列では hud の grid-row:2 の則でトレイの直下に出る（第 B 波の display:contents で変わった・意図として書かれていなかった）。
     hud の @media (max-width: 820px) の .sheet-open #stage { height: 75vh } は部屋の箱を伸ばし（下に空き）、
     @media (max-width: 1279px) の .sheet-open .sheet は 3 カラムの 1181〜1279 で部屋を 52px まで潰した（2026-09-25 の検収）
  ⑩' 最後に（③'' の後）❗ の無い世界（トレイが hidden）で台帳の行をタップ → 指示欄が開き、選択がその行の人・トレイは hidden の
     まま（空振りの番人）・部屋の箱の丈が変わらない（±1px）・多段（.main の列が 1 本でない）では #sheet が ⑩（❗ がある形）と
     同じ置き方（R110 骨格 A＝3 カラムは端末の下の升目に static で左右と下端が ±1px・2 カラムは端末の升目の下端に absolute で
     矩形全体が ±1px・滑り込む遷移の後）・今のスクロール位置で指示欄が 44px 以上見える。
     /api/office の応答も ❗ の無い世界へ差し替える（3 秒ごとのポーリングが ❗ を入れ直すと、測る間にトレイが戻る）。
     ⑩ は ❗ がある形しか見ていない＝❗ が無いときにだけ効く hud の :has(#attn[hidden]) の則（grid-row: 1 / -1・
     margin-top: 12px・詳細度が 1 段高い）に負ける退行が緑で通った（第 C 波の変異 band_hidden＝cockpit の 2 本目の
     セレクタを消す→ 1194 で指示欄が 12px 下がる。置き方をトレイが出ているときだけに狭める→部屋が 464→104px）
  ⑪ ヘッダー（スクロール 0）: ボタン（管理・?・要約・詳細の中を除く）が祖先にも画面にも切られない（.admin の横スクロールの先に
     隠れない）。ゲージのピンが互いに重ならず・中身が箱をはみ出さず・切られず、名前（.gplab）の見えている幅 ≥ min(24, 全幅)
  ⑫ 色付きの左右の帯を立てない（デザイン方針）: 選んだ名簿の行（.pxrow.sel・R110 で一覧のカードを統合・同じセッションの .sel が
     必ず在る＝開いたプロジェクトはプロジェクト行と子の行の 2 つ）と .sel・#attn・.cpq・.cpwork・
     .dlgmsg の計算値で、左右の罫が上の罫と違う（1px の中立の罫＝--cp-line・--cp-line-soft は除く）・inset の影の x が 0 でない・
     ::before/::after の幅 8px 以下の色つきの棒・横向きのグラデーション、のどれも無い（静的な同じ検査= tests/cockpit_smoke.py 29b）
  ⑬ 管理のモーダル: スクロール 0 で ⚙（設定）をタップ → #modal が**その時のスクロール位置のまま**、上端 ≥ 0・左右が画面内・
     見えている丈 ≥ min(scrollHeight, 画面の丈 − 40)・中心が当たる・opacity の積 ≥ 0.9。中身（見出し .mtitle と最初の操作部品＝
     #modal の中の最初の button・input・select・textarea）がそれぞれ、祖先（#modal 自身のスクロール箱を含む）に切られず・
     画面内で・見えている中心が当たり・visible で opacity の積 ≥ 0.9。閉じて元へ戻す（閉じなければ赤）。
     1 列では hud の .modalwrap（.main 基準の absolute; inset 0）が、ページの丈まで伸びた .main の縦の中央にモーダルを置いた
     （第 C 波の検収: 890×626 と 1024×768 で見えている丈 0・右上の ✕ と薄い暗幕だけが出る）。
     下限を元の丈（箱自身の丈）で取っていたときは、34〜50px の帯に潰れた箱が自分の丈を満たし、中身を visibility:hidden に
     した空の箱も緑だった（第 D 波の検収の modal_34・modal_wrap_short・modal_body_hidden）
  ⑭ 段の一致（スクロール 0）: 「ヘッダーがスマホの段か（管理 .admin がゲージ .usage より下の段）」と「画面の幅
     （documentElement.clientWidth）≤ 600 か」が一致する。29a（静的）は問い合わせの文字列しか見ない＝問い合わせ先が余白 12px の
     .cphead に置かれて、文字列は 600 のまま 601〜624 が「ヘッダーはスマホ・台帳は 6 列」に混ざった形（第 B 波）は 29a でも ⑤ ⑪ でも
     緑だった（第 D 波の検収）。R110: 名簿の行は幅に依らず 2 段（見えている行のいま .c-detail がプロジェクト .c-project より下の段）
     ＝どの形でも 2 段であることを同じ項目で見る（台帳の段を幅で切り替える定義は撤去）
  ⑮ オフライン帯: 最後に（⑩' の後）/api/office を 503 にする → #offbar が出る（ポーリングが 2 回続けて落ちる）→ ページを
     600 までスクロール（足りなければ一番下・3 カラムはスクロールしない）して、その位置のまま帯の元の矩形が画面内・祖先に
     切られず・中心が当たり・opacity の積 ≥ 0.9・文言が空でない。1 列でページがスクロールしなければ空振りの赤。
     1 列では hud の .offbar（.main 基準の absolute; top 12px）がページの先頭に残り、スクロールすると画面の上の外へ出た
     （第 D 波の実測: 390 で y −475・834 で y −524）
  ⑮（ヘッダー）同じ帯が出ている間、スクロール 0 でヘッダー（.cphead）の見えている button と .gpin の中心が帯に当たらない
     （中心の 1 点を elementFromPoint で引いて、帯かその中身なら赤・見て測った部品が 0 個なら空振りの赤）。
     1 列で帯を画面の上（fixed; top 12px）に固定したとき、スクロール 0 で帯がヘッダーに重なりタップを吸った（第 E 波の検収:
     612 で管理のボタン 5 個・390 で ? と要約とピン 2 個・834 で ➕）。スクロールするとヘッダーのほうが帯の下へ流れる＝
     オフラインの間はヘッダーを押す位置が無い。帯が見えるか（上の ⑮）とは別の項目
  ⑯ ブラウザ既定の枠: 読み込み後に見えている .ui-cockpit の button と、⑬ で開いたモーダルの中の見えている button の、
     computed の border-style（太さ > 0 の辺）に outset／inset が無い。UA の既定（2px outset）が一覧のカード・端末のタブ・
     「動きを止める」に残り、灰色の額縁の積み重ねに見えた（第 D 波の着手前: 3 種 35 個）。⑫ は色のない対称の枠を帯としない
  ⑰ モーダルの暗幕（⑬ と同じ開閉・部屋は止める）: 開いたままの同じ組みで、暗幕（#modalwrap）を隠した 2 枚と戻した 1 枚を撮り、
     #modal と ✕ と動いた画素を外した背面で、隠した画面の明るい 5% と暗い 5% の**同じ画素**の平均の差が、戻すと 50% 以下に縮む
     （＝暗幕の不透明度 0.5 以上。色に依らない）。hud の rgba(46,45,44,.14) は明るい紙の地のための薄さで、暗い cockpit では 86% 残った
     （背面の文字も部屋も読めたまま）。開く前の画面と比べないのは、390 で開くとトレイに「Esc で戻ると…」の行が出てページが
     24px ずれ、画素の組が崩れて薄い暗幕でも 46% に見えたため
  ⑱ スクロールのロック（⑬ と同じ開閉）: 1 列では開く前にヘッダーの無地（.brand）で指（タッチの形）かホイール（マウスの形）を
     下へ動かし、ページが動くこと（陽性対照）→ 開いている間は、同じ点・暗幕だけが当たる点・モーダルの中心（中を一番下まで寄せて
     から）で同じ動き → ページも #modalwrap の外の箱も動かない → 閉じた後は同じ動きでページがまた動く（ロックが残らない）。
     動きは CDP の Input.synthesizeScrollGesture（ブラウザの入力の経路）。3 カラムはページがもとから動かない＝開いている間だけ見る
  ⑲ モーダルの ✕（スクロール 0）: 画面内・中心が当たる・ヘッダーの押せる部品（button・.gpin）と矩形が重ならない・見出し
     （.mtitle）の文字から 4px 以上離れ（CLOSE_TITLE_GAP）、最初の操作部品に重ならない。見出しを全角 42 字に差し替えても同じ
     （開いている間だけ差し替えて戻す）。1 列では hud の置き方（暗幕＝画面の右上 18px）のままだと、スクロール 0 で ? と要約の
     ボタンに重なった（834 で ✕ x 784〜816）。重なり（0.5px 超）だけを見ていたときは、タッチの ✕（44px）で見出しの右の余白を
     36px に戻しても、字が ✕ の左端から 0〜2px に貼り付いたまま緑だった（2026-09-26 の変異）
  ⑳ オフライン帯とトースト（⑮ の帯が出ている間）: 長い名前の世界（sess-cp-09 の質問文だけ替える＝答え済みの印が外れる）で ❗ に
     2 回答え、2 回とも 500＋長い error（1 列は BAND_ERROR＝1112 でも 2 行以上に折り返す）→ 失敗のトーストが 2 件そろった瞬間に、
     どのトーストも帯と矩形が重ならず・画面内で・中心が当たる。1 列では帯の上端と一番下のトーストの隙間が 0〜16px（トーストは帯の
     すぐ上に積む＝帯の丈の実測が止まって既定の丈で積むと、1 行の帯の形で隙間が 26px に開く）。帯の位置 108px の根拠は「1 行の
     トースト 2 件」で、折り返したトーストは帯の下からはみ出して帯に掛かった（390 で丈 107px）
  ㉑ 指示欄を開いている間の ❗トレイ（⑩ で開いた指示欄・スクロール 0）: トレイの見えているボタンが全部、画面内で中心が当たる・
     指示欄とトレイの矩形が重ならない。3 カラムでは指示欄が右カラムに掛かってトレイを覆った（1440 で指示欄 x 704〜1424・トレイ
     x 1141〜1440）。3 カラムの 1280×640 と 1920 は tests/cockpit_smoke.py の 29c が見る
  ㉒ スクロールバーの溝（⑬ と同じ開閉・1 列だけ）: html の scrollbar-gutter の計算値が開く前も開いている間も stable・背面の幅
     （clientWidth）が開く前後で同じ・開いている間の html の overflow-y が hidden（ロックが掛かっていないなら空振りの赤）。
     スクロールバーが常に見える環境（Mac にマウス・Windows）では、ロック（html の overflow:hidden）でスクロールバーが消え、背面の
     幅が約 15px 変わる。ヘッドレスの Chromium（Mac）は重ねて出すスクロールバーで溝は 0＝幅の比較だけでは直す前も後も緑。
     ::-webkit-scrollbar で常に見えるスクロールバーを作っても、html の溝は overflow:hidden の間に取られない（div では取られる・
     2026-09-26 の実測）＝計算値を課す。溝の実際の幅は実機（スクロールバーを常に表示）で見る
  ㉓ タッチの形（pointer: coarse）のモーダルの ✕ が 44×44 以上（hud の 32×32 のままだった）。見出しとの離れは ⑲ が見る
  ㉗ ゲージの詳細を開いたまま ? でモーダルを開いても、詳細がモーダルの上に出ない（暗幕より上の z-index 10）。
  ㉔ モーダルの暗幕（⑬ と同じ開閉・スクロール 0・どの段でも）: #modalwrap の矩形が画面の 4 辺に届く・ヘッダー（.cphead）と
     フッター（.cpfoot）の画面に入っている帯の左・中・右の 3 点が暗幕（かその中）に当たる・✕ がモーダルの矩形の外へ 12px 以上
     出ない。3 カラムでは hud の置き方（.main 基準の absolute）のまま、暗幕が .main だけに掛かり、明るいのに inertOthers で押せない
     ヘッダーとフッターが残った。✕ は .main の右上の角（1194 で x 1144〜1176）でモーダル（x 387〜807）から遠かった
  ㉕ 3 カラムの指示欄と部屋（⑩ で開いた指示欄・スクロール 0）: 指示欄が部屋の canvas（部屋の箱で切った見えている矩形）と
     重なる幅が canvas の幅の 1/5 以下・トレイに重ならず全ボタンが押せる。形の幅に加えて窓の幅を 1181・1279・1920 にも変えて
     見る（丈は形のまま・最後に戻す）。下限 480 のときは 1181〜1279 で部屋の右の 3 割（1181 で 183px・1194 で 170px）と台帳の
     証拠・経過の列が指示欄の下に入った
  ㉖ ⤢（指示欄を広げる・#sheetwide）（⑩ で開いた指示欄・3 カラムは ㉕ と同じ窓の幅でも）: ⤢ を押した後、部屋の箱・名簿の箱の丈と
     トレイの矩形が ⤢ の前と同じ（±1px・名簿の丈 > 0）・トレイの全ボタンが押せて指示欄と重ならない・指示欄が画面の横に収まる。
     R110 骨格 A: 3 カラムは端末の下の升目（static）・2 カラムは端末の升目の下端（absolute）のまま丈だけ上へ広がる（左右と下端は
     ⤢ の前と同じ）、1 列は ⤢ の前と同じ矩形（広げる余地が無い＝狭めない）。⤢ を押して戻す。
     hud の ⤢ の則のままだと 3 カラムでは指示欄が左の列の 2 行目に流れ込み、1440・1920 は台帳の丈が 0・1181〜1279 は部屋の箱が
     50px に潰れた。トレイは hud の :has(.sheet.wide) #attn で余白 16px・丈の上限 28vh になり（1 列で幅が 32px 縮む・長い ❗ は
     箱の外へ描かれる）、1 列の指示欄は余白 16px で「広げる」と 8px 狭まった
  ㉖' 同じ ⤢ を ⑩' の ❗ の無い世界（台帳の行から開いた指示欄・形の幅だけ）でも見る（トレイは hidden のまま）。❗ が無いときだけ
     効く hud の :has(.sheet.wide):has(#attn[hidden]) の則（grid-row: 1 / -1・詳細度 (1,6,0)）に負けないかの番人
  ③'' 最後に（⑧ の後）全員に全角 42 字の名前（/rename の title）を付けて入れ直し、ページに横スクロールが無い。固定ワールドの
     名前は短いので、端末の足元の nowrap の名前が #app を横へ押し広げる退行（第 B 波）が ③' に写らなかった
  ⓪ pageerror / console.error = 0（⑧'' の 500 と ⑮ の 503＝わざと返した資源の失敗だけは数えない）

所要: 10 形状で約 80 秒（2026-09-26 の第 G2 波の実測: 負荷平均 7〜15 で 79.7〜80.0 秒・live 1 形状 9.7〜9.9 秒・デモ 1 形状 0.6 秒。
㉒〜㉖（⤢ の開閉・3 カラムは窓の幅 4 つ）で第 G1 波の 76 秒（負荷平均 13）から数秒延びた。以下は第 G1 波までの経緯。
2026-09-25 第 E 波の 50〜52 秒（負荷平均 4〜10）から、⑰〜㉑（暗幕の撮影 3 枚・指の合成スクロール 4〜5 回・帯の上の
トースト 2 件＝形ごとに 3〜4 秒）のぶん延びた。第 D 波の 32 秒（負荷平均 4）からは、⑮ のオフライン帯（形ごとに 1.6〜2.0 秒
＝/api/events の再接続 3 秒ごとの poke を待つ・8 形状で 14 秒）と ⑧' ⑧''（長い名前・長い error・8 形状で 5 秒）。⑭ ⑯ はほぼ 0 秒）。
最後の行に毎回の実測、最初の行に負荷平均を出す。run_ui が 1 回だけ再試行するので、赤のあいだの verify では約 2 倍。
形状の途中で例外（読み込み待ちの TimeoutError 等）が出たら、その形状を「✗ 読み込みが終わらない」として数え、
次の形状へ進む（素の traceback で残りを捨てない）。

使い方: python3 tests/cockpit_shapes_smoke.py   （verify.sh ▶7 から呼ぶ・Playwright と Pillow が必要）
"""
import base64
import io
import json
import os
import pathlib
import re
import sys
import time
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, free_port, start_server  # noqa: E402

WORLD = ROOT / "tests" / "fixtures" / "world" / "cockpit.json"
STATUS_BOARD = ROOT / "tests" / "fixtures" / "status_board" / "basic.json"
DEMO_WORLD = ROOT / "ui" / "demo" / "world.json"

# (名前, 幅, 丈, タッチ, dsf, 部屋の期待の倍率 k)。タッチの形は is_mobile も立てる（meta viewport を効かせる＝実機と同じ段で組む）。
# k は統括の裁定の値を書く（コックピットは丈の上限 480 ＝ 390 幅は ×2、834・1112・890・1194 以上は ×3）
SHAPES = [
    ("iPad 縦", 834, 1112, True, 2, 3),
    ("iPad 横・2 カラム", 1112, 834, True, 2, 2),   # R105: 2 カラムの左の列 440px＝×2
    ("折りたたみ", 890, 626, True, 2, 3),
    ("スマホ", 390, 844, True, 3, 2),
    # iPad の Split View の半分＝1 列の段の一番狭い形（2 段のヘッダー＋6 列の台帳）。スマホの段の境はヘッダー（.cpshell）も
    # 台帳（.cptable）も画面 600 の 1 本（第 C 波で揃えた。前はヘッダーだけ中身の幅 ≤600＝画面 ≤624 で、601〜624 が混ざった）。
    # 揃っているかは ⑭ が実描画で見る（29a は文字列だけ）
    ("iPad 分割", 612, 900, True, 2, 3),
    ("対照（マウス）", 1440, 900, False, 1, 3),
    ("対照（マウス・細い窓）", 390, 844, False, 2, 2),   # Mac の細い窓＝幅で端末を畳む実装はここで赤
    # 3 カラムの下端（1181〜1279）かつ低い画面: hud の max-width:1279 の指示欄の則が効く帯（1440 には写らない）
    ("対照（マウス・2 カラム）", 1194, 834, False, 1, 2),
]
# デモ（?demo=1）: (名前, 幅, 丈, dsf, 言語)。日本語は同梱の world に lang だけ替えて入れ直す（設定で言語を替えたのと同じ）
DEMO_SHAPES = [
    ("デモ・スマホ", 390, 844, 3, "en"),
    ("デモ・iPad 縦", 834, 1112, 2, "ja"),
]
TOUCH_MIN = 44             # 指で押せる最小の寸法（CSS px）
# 名簿の 1 行の丈の上限（縦書き化は 285px だった）。R110 で行は幅に依らず 2 段: 上段＝名前（14px × 行間 1.35・折り返して 2 行まで
# ＝38）／下段＝いま（R110-D7 の裁定で本文 14px × 1.35 の clamp 2 行＝38）・余白 8＋8・段の間 4・罫 1 ＝最大 97px（いつもの行は
# 78px・いま 1 行は 59px・2026-10-07 の実測）。字形の差と丸めに 3px を足して 100（2 段を超えて伸びた行＝列が潰れて折り返しが
# 増えた行を捕まえる。いまの幅は下の DETAIL_* が見る）。配達の札（.dstate）が出ている行は、札の段（R110-D6＝行の 3 段目・下の
# 余白を札の丈ぶん広げる）を引いて測る（札は届かない間だけ出る 3 段目＝列の潰れではない）
ROW_MAX = 100
TERM_MIN = 80              # 端末を「出す」なら、見えている xterm に要る寸法（cockpit_smoke の TERM_MIN と同じ値）
ROOM_W, ROOM_H = 184, 160  # 部屋の論理寸法
K_MIN, K_MAX = 1, 4        # R105-A: 狭い 2 カラムでは ×1 も使う（整数だけ）
GRID = 5                   # ⑥ の命中を数える格子（5×5 点・元の矩形全体へ打つ）
GRID_MIN = 24              # ⑥ 開いた詳細の格子の命中の下限（25 点中）
OPACITY_MIN = 0.9          # ⑥ #gauges と祖先の opacity の積の下限
PIX_TOL = 24               # ⑥ 画素が「変わった」とみなすチャンネル差（文字の縁のゆらぎを数えない）
PIX_RATIO_MIN = 0.02       # ⑥ 中身の行の矩形の止まっている画素のうち、開いて変わった割合の下限（透明な詳細の番人。
                           #   2026-09-25: 本物 8.0〜94.3%・透明 0.0%＝下限は本物の最小の 1/4）
EDGE_TOL = 0.5             # ⑥ 画面の端に対する許し（CSS px）
CLIP_TOL = 1.0             # ⑥ 見えている矩形と元の矩形の差の許し（clientWidth の整数丸め）
FINGER_OUT = 6             # ⑦（指）描いた矩形の外へずらす CSS px（半径 22 の内側）
FINGER_CLEAR = 14          # ⑦（指）ずらした点からほかの人の矩形までに要る CSS px（その人が一番近いままにする）

# 裁定の文（デモの足元・日本語）。辞書のキー名や実装の式ではなく、統括が決めた文そのものを書く
DEMO_REASON_JA = "デモには端末がありません（手元の Mac で ＋ から起こせます）"

# ⑧ 固定ワールドで、トレイの最初の答えが送る本文（門に直書き＝辞書からキーが消えて T がキー名を返しても一致しない）
EXPECTED_BODY = {
    "sess-cp-09": "選択肢「ローカル」でお願いします。",     # AskUserQuestion・最初の選択肢「ローカル」
    "sess-cp-07": "",                                        # 許可待ち（Bash）＝1 番は allow・本文なし（R102-11）
}

# 偽の端末（本物の pty を起こさない）。sess-cp-09＝固定ワールドで最初に選ばれる ❗ の人
TERM_ID = "5a0e5a0e5a0e5a0e"
TERM_SESSION = "sess-cp-09"
TERM_INFO = {"id": TERM_ID, "kind": "claude", "session": TERM_SESSION, "projectId": "2ffc7d9c6254",
             "cols": 100, "rows": 30, "alive": True, "exitCode": None, "startedAt": 1790199000, "viewers": 0}
TERM_OUT = "shapes smoke\r\n$ "
ANSWER_PATHS = ("/api/approval/reply", "/api/instruct")


def is_demo_reason(note, lang):
    """デモの足元の理由か。日本語は裁定の文と一致・英語は demo と Mac を含む（英語の文は裁定に無い＝意味で見る）"""
    if lang == "ja":
        return note == DEMO_REASON_JA
    if lang == "en":
        return bool(re.search(r"\bdemo\b", note, re.I)) and "Mac" in note
    return False


# ── ページの中で測る ──────────────────────────────────────────────
# 見えているか（display・visibility・寸法・hidden 属性の祖先）・見えている矩形・人が動かせるスクロール。どの測定もこの 1 本
VIS_JS = """
  const vis = (e) => {
    if (!e || e.closest('[hidden]')) return false;
    const cs = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  const hits = (e, x, y) => { const at = document.elementFromPoint(x, y); return !!at && (at === e || e.contains(at)); };
  const R = (v) => Math.round(v);
  const who = (e) => (e.id ? '#' + e.id : e.classList && e.classList[0] ? '.' + e.classList[0] : e.tagName.toLowerCase());
  // 見えている矩形: e の矩形を、包含ブロックの鎖に居て overflow が visible でない祖先の内寸で切る（画面では切らない）。
  // absolute の子は包含ブロック（position が static でない祖先・変形の祖先）までの祖先に切られない＝飛ばす。
  // fixed は変形の祖先まで飛ばす（transform・filter・backdrop-filter・will-change・contain が包含ブロックを作る）。
  // html の overflow が visible なら body の overflow は画面へ移る＝body では切らない
  const clipRect = (e) => {
    const r = e.getBoundingClientRect();
    let L = r.left, T = r.top, Rt = r.right, B = r.bottom;
    const by = [];
    const root = getComputedStyle(document.documentElement);
    const rootVisible = root.overflowX === 'visible' && root.overflowY === 'visible';
    let need = getComputedStyle(e).position;
    for (let p = e.parentElement; p && p !== document.documentElement; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.display === 'contents' || cs.display === 'inline') continue;   // 箱を持たない・overflow が効かない
      const cb = cs.transform !== 'none' || cs.filter !== 'none' || (cs.backdropFilter || 'none') !== 'none'
        || /(transform|filter)/.test(cs.willChange) || /(paint|layout|strict|content)/.test(cs.contain);
      if (need === 'fixed' && !cb) continue;
      if (need === 'absolute' && cs.position === 'static' && !cb) continue;
      const cx = cs.overflowX !== 'visible', cy = cs.overflowY !== 'visible';
      if ((cx || cy) && !(p === document.body && rootVisible)) {
        const q = p.getBoundingClientRect();
        const x0 = q.left + p.clientLeft, y0 = q.top + p.clientTop;
        const was = [L, T, Rt, B].join();
        if (cx) { L = Math.max(L, x0); Rt = Math.min(Rt, x0 + p.clientWidth); }
        if (cy) { T = Math.max(T, y0); B = Math.min(B, y0 + p.clientHeight); }
        if ([L, T, Rt, B].join() !== was) by.push(who(p));
      }
      need = cs.position === 'absolute' || cs.position === 'fixed' ? cs.position : 'static';
    }
    return { left: L, top: T, right: Math.max(L, Rt), bottom: Math.max(T, B), by };
  };
  const onScreen = (v) => ({ left: Math.max(v.left, 0), top: Math.max(v.top, 0),
    right: Math.max(Math.max(v.left, 0), Math.min(v.right, innerWidth)),
    bottom: Math.max(Math.max(v.top, 0), Math.min(v.bottom, innerHeight)), by: v.by });
  const area = (v) => Math.max(0, v.right - v.left) * Math.max(0, v.bottom - v.top);
  // 出ているか（畳まれていないか）: 箱がある（自分にも祖先にも display:none が無い）・visible・見えている面積 > 0。
  // hidden 属性は見ない（CSS の display が上書きすると、属性があっても出ている）
  const shown = (e) => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility === 'visible'
    && area(clipRect(e)) > 0.25;
  // 人が指で動かせるものだけで e を画面に入れる: overflow が auto/scroll の容れ物とページ。
  // scrollIntoView は使わない（overflow:hidden の祖先まで動かす＝人が指でできない操作で「見える」に化かす）
  // 寄せる先は箱の内寸（clientTop/Left から client 幅・丈）＝枠の内側（外寸で寄せると、下に罫のある箱（受付のトレイ）では
  // 罫の 1px ぶん切れたままになった＝R110-D2 でトレイが中でスクロールするようになって表に出た）
  const reveal = (e) => {
    for (let p = e.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
      const cs = getComputedStyle(p);
      const o = p.getBoundingClientRect(), b = e.getBoundingClientRect();
      const a = { top: o.top + p.clientTop, left: o.left + p.clientLeft,
        bottom: o.top + p.clientTop + p.clientHeight, right: o.left + p.clientLeft + p.clientWidth };
      if (/(auto|scroll)/.test(cs.overflowY) && p.scrollHeight > p.clientHeight + 1) {
        if (b.top < a.top) p.scrollTop -= a.top - b.top;
        else if (b.bottom > a.bottom) p.scrollTop += Math.min(b.top - a.top, b.bottom - a.bottom);
      }
      if (/(auto|scroll)/.test(cs.overflowX) && p.scrollWidth > p.clientWidth + 1) {
        if (b.left < a.left) p.scrollLeft -= a.left - b.left;
        else if (b.right > a.right) p.scrollLeft += Math.min(b.left - a.left, b.right - a.right);
      }
    }
    const b = e.getBoundingClientRect();
    if (b.top < 0 || b.bottom > innerHeight) window.scrollBy(0, b.top - Math.max(0, (innerHeight - b.height) / 2));
  };
"""

# ① スクロール 0 のトレイ（ボタンが画面にも祖先にも切られずに収まるか）
TRAY_FIRST = "() => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const tray = document.querySelector('#attn');
  const vw = innerWidth, vh = innerHeight;
  if (!tray) return { exists: false, vw, vh };
  const r = tray.getBoundingClientRect();
  const answers = [...tray.querySelectorAll('button')].filter(vis).map((b) => {
    const q = b.getBoundingClientRect();
    const v = clipRect(b);
    const cx = q.left + q.width / 2, cy = q.top + q.height / 2;
    const inView = q.top >= 0 && q.bottom <= vh && q.left >= 0 && q.right <= vw;
    const cut = v.left > q.left + 0.5 || v.top > q.top + 0.5 || v.right < q.right - 0.5 || v.bottom < q.bottom - 0.5;
    const at = inView ? document.elementFromPoint(cx, cy) : null;
    return { label: b.textContent.trim(), answer: b.hasAttribute('data-idx'),
      top: R(q.top), bottom: R(q.bottom), left: R(q.left), right: R(q.right), w: R(q.width), h: R(q.height),
      inView, cut, by: v.by.join(' '), visW: R(v.right - v.left), visH: R(v.bottom - v.top),
      hit: !!at && (at === b || b.contains(at)), over: at && !(at === b || b.contains(at)) ? who(at) : '' };
  });
  // R110-D2: 受付が低い形ではトレイ全体が中でスクロールする（ボタンの列もスクロールで届く）。ページは動かさず（スクロール 0 のまま）、
  // ページ以外の人が動かせる箱（overflow が auto/scroll）だけでボタンを寄せて、同じ物差しで測り直す（寄せた箱は 0 へ戻す）
  const moved = new Set();
  const inner = [...tray.querySelectorAll('button')].filter(vis).map((b) => {
    for (let p = b.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (!/(auto|scroll)/.test(cs.overflowY) || p.scrollHeight <= p.clientHeight + 1) continue;
      const a = p.getBoundingClientRect(), q = b.getBoundingClientRect();
      const top = a.top + p.clientTop, bottom = top + p.clientHeight;
      if (q.top < top) p.scrollTop -= top - q.top;
      else if (q.bottom > bottom) p.scrollTop += Math.min(q.top - top, q.bottom - bottom);
      moved.add(p);
    }
    const q = b.getBoundingClientRect(), v = clipRect(b);
    const cx = q.left + q.width / 2, cy = q.top + q.height / 2;
    const inView = q.top >= 0 && q.bottom <= vh && q.left >= 0 && q.right <= vw;
    const cut = v.left > q.left + 0.5 || v.top > q.top + 0.5 || v.right < q.right - 0.5 || v.bottom < q.bottom - 0.5;
    const at = inView ? document.elementFromPoint(cx, cy) : null;
    return { label: b.textContent.trim(), inView, cut, by: v.by.join(' '), visW: R(v.right - v.left), visH: R(v.bottom - v.top),
      top: R(q.top), bottom: R(q.bottom), left: R(q.left), right: R(q.right),
      w: R(q.width), h: R(q.height), hit: !!at && (at === b || b.contains(at)), over: at && !(at === b || b.contains(at)) ? who(at) : '' };
  });
  for (const p of moved) p.scrollTop = 0;
  return { exists: true, shown: vis(tray), top: R(r.top), bottom: R(r.bottom), vw, vh, scrollY: R(scrollY),
    docH: document.documentElement.scrollHeight, answers, inner, pageMoved: R(scrollY),
    head: tray.querySelector('.trayhead')?.textContent.trim() || '' };
}"""

# ② トレイのボタンの寸法と当たり（1 つずつ、人が動かせるスクロールだけで画面に入れてから、見えている矩形で測る）
TRAY_BUTTONS = "() => {" + VIS_JS + """
  const out = [];
  const tray = document.querySelector('#attn');
  for (const b of tray ? [...tray.querySelectorAll('button')].filter(vis) : []) {
    reveal(b);
    const q = b.getBoundingClientRect();
    const v = onScreen(clipRect(b));
    const cx = (v.left + v.right) / 2, cy = (v.top + v.bottom) / 2;
    const seen = area(v) > 0;
    const at = seen ? document.elementFromPoint(cx, cy) : null;
    out.push({ label: b.textContent.trim(), w: R(v.right - v.left), h: R(v.bottom - v.top),
      fullW: R(q.width), fullH: R(q.height), by: v.by.join(' '), cx: R(cx), cy: R(cy), seen,
      hit: !!at && (at === b || b.contains(at)), over: at && !(at === b || b.contains(at)) ? who(at) : '' });
  }
  window.scrollTo(0, 0);
  return out;
}"""

# ③④ 部屋
ROOM = "() => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const c = document.querySelector('.cproom');
  if (!c) return { exists: false };
  const r = c.getBoundingClientRect();
  // 部屋の中の横スクロール＝canvas から .cpstage までの容れ物（overflow-x が auto/scroll）が横にあふれている。
  // .cpstage より外（#app・body）の横スクロールは部屋のせいとは限らない＝③' がページとして別に見る
  // 切り抜き＝overflow-x が hidden/clip の祖先の枠から canvas がはみ出す（スクロールもできずに隠れる）
  const scrollers = [], clips = [];
  let inRoom = true;
  for (let e = c.parentElement; e && e !== document.documentElement; e = e.parentElement) {
    const ox = getComputedStyle(e).overflowX;
    if (inRoom && /(auto|scroll)/.test(ox) && e.scrollWidth > e.clientWidth + 1) {
      scrollers.push({ who: who(e), sw: e.scrollWidth, cw: e.clientWidth });
    } else if (/(hidden|clip)/.test(ox)) {
      const p = e.getBoundingClientRect();
      if (r.left < p.left - 0.5 || r.right > p.right + 0.5) clips.push({ who: who(e), left: R(p.left), right: R(p.right) });
    }
    if (e.classList.contains('cpstage')) inRoom = false;
  }
  // ④' 部屋の箱（.cpstage）の丈と、段（.main の列の数＝1 列か 3 カラムか。形の幅から決めない＝実装の境目を写さない）
  const st = document.querySelector('.cpstage');
  const cols = getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\\s+/).length;
  return { exists: true, shown: vis(c), attrW: c.width, attrH: c.height, cssW: r.width, cssH: r.height,
    left: R(r.left), right: R(r.right), vw: innerWidth, scrollers, clips,
    rendering: getComputedStyle(c).imageRendering,
    stageH: st ? st.getBoundingClientRect().height : 0, stageScale: st ? st.dataset.roomScale || '' : '', cols };
}"""

# ③' ページの横スクロール（文書・body・#app）と、画面の右へはみ出してページまで漏れている要素（中で切られていないもの）
PAGE_X = "() => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const vw = innerWidth;
  const pages = [document.documentElement, document.body, document.querySelector('#app')].filter(Boolean)
    .filter((e) => e.scrollWidth > (e === document.documentElement ? vw : e.clientWidth) + 1)
    .map((e) => ({ who: who(e), sw: e.scrollWidth }));
  const leak = [];
  if (pages.length) {
    const top = new Set([document.body, document.documentElement, document.querySelector('#app')]);
    for (const e of document.querySelectorAll('body *')) {
      const r = e.getBoundingClientRect();
      if (r.width <= 0 || r.right <= vw + 1) continue;   // 左へのはみ出し（xterm の計測用の要素など）は横スクロールを作らない
      let contained = false;
      for (let p = e.parentElement; p && !top.has(p); p = p.parentElement) {
        if (getComputedStyle(p).overflowX !== 'visible') { contained = true; break; }
      }
      if (!contained && !leak.some((x) => x.el.contains(e))) leak.push({ el: e, who: who(e), left: R(r.left), right: R(r.right) });
    }
  }
  return { pages, vw, leak: leak.sort((a, b) => b.right - a.right).slice(0, 3).map(({ el, ...x }) => x) };
}"""

# ⑪ ヘッダー（スクロール 0）: 管理のボタン・? ・要約が、祖先にも画面にも切られない（.admin の横スクロールの先に隠れない）。
#   ゲージのピンが互いに重ならず、ピンの中身がピンの箱をはみ出さず、ピンの名前（.gplab）が潰れていない
#   （見えている幅 ≥ min(24, 名前の全幅)＝名前が 1 文字に潰れた・「61%」が隣の棒に重なった、を捕まえる）。
#   ゲージの詳細（#gauges）の中のボタンは数えない（閉じている間は畳まれている）
HEADER = "(minLab) => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const head = document.querySelector('.cphead');
  if (!head) return { exists: false };
  const g = document.querySelector('#gauges');
  const vw = Math.min(innerWidth, document.documentElement.clientWidth || innerWidth);
  const btns = [...head.querySelectorAll('button')].filter((b) => vis(b) && !(g && g.contains(b)));
  const bad = [];
  for (const b of btns) {
    const q = b.getBoundingClientRect(), k = clipRect(b);
    const cut = k.left > q.left + 0.5 || k.top > q.top + 0.5 || k.right < q.right - 0.5 || k.bottom < q.bottom - 0.5;
    const out = q.left < -0.5 || q.right > vw + 0.5 || q.top < -0.5;
    if (cut || out) bad.push(`${who(b)}（x ${R(q.left)}〜${R(q.right)}・見えている ${R(Math.max(0, k.right - k.left))}×${R(Math.max(0, k.bottom - k.top))}`
      + `${cut ? '・' + (k.by.join(' ') || '?') + ' に切られている' : ''}${out ? '・画面の外' : ''}）`);
  }
  const pins = [...document.querySelectorAll('#usage-summary .gpin')].filter(vis);
  const rs = pins.map((e) => e.getBoundingClientRect());
  const lap = [];
  for (let i = 0; i < pins.length; i += 1) for (let j = i + 1; j < pins.length; j += 1) {
    const a = rs[i], b = rs[j];
    const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left), oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    if (ox > 0.5 && oy > 0.5) lap.push(`「${pins[i].textContent.trim()}」と「${pins[j].textContent.trim()}」が ${R(ox)}×${R(oy)} 重なる`);
  }
  const spill = pins.filter((e) => e.scrollWidth > e.clientWidth + 1)
    .map((e) => `「${e.textContent.trim()}」の中身 ${e.scrollWidth} > 箱 ${e.clientWidth}`);
  const pinCut = pins.filter((e, i) => { const k = clipRect(e); return k.left > rs[i].left + 0.5 || k.right < rs[i].right - 0.5
    || k.top > rs[i].top + 0.5 || k.bottom < rs[i].bottom - 0.5 || rs[i].left < -0.5 || rs[i].right > vw + 0.5; })
    .map((e) => `「${e.textContent.trim()}」が切られている（${clipRect(e).by.join(' ') || '画面'}）`);
  const labs = pins.map((e) => e.querySelector('.gplab')).filter((e) => e && vis(e));
  const thin = labs.filter((e) => e.getBoundingClientRect().width < Math.min(minLab, e.scrollWidth) - 0.5)
    .map((e) => `「${e.textContent.trim()}」の名前が ${R(e.getBoundingClientRect().width)}px（全幅 ${e.scrollWidth}）`);
  return { exists: true, h: R(head.getBoundingClientRect().height), btns: btns.length, pins: pins.length, labs: labs.length,
    bad, lap, spill, pinCut, thin };
}"""
LAB_MIN = 24               # ⑪ ゲージのピンの名前の見えている幅の下限（名前がそれより短ければ全幅）

# ⑫ 色付きの左右の帯（デザイン方針: カード・引用・callout の色付き border-left バーを立てない）。
#   選んだ名簿の行（.pxrow.sel・同じセッションの行が必ず在る）と、選択・❗・引用の箱（.sel・#attn・.cpq・.cpwork・.dlgmsg）の計算値で見る:
#   左右の罫が見えていて上の罫と違う（太さ・線種・色）＝帯。inset の box-shadow で x が 0 でない色つきの影＝帯。
#   ::before／::after が幅 8px 以下で丈が箱の半分以上の色つきの棒＝帯。横向きのグラデーションの背景＝帯
SIDE_BANDS = "() => {" + VIS_JS + """
  const alpha = (c) => { const m = /rgba?\\(([^)]+)\\)/.exec(c || ''); if (!m) return c === 'transparent' ? 0 : 1;
    const v = m[1].split(/[ ,\\/]+/).filter(Boolean); return v.length > 3 ? parseFloat(v[3]) : 1; };
  const side = (cs, s) => ({ w: parseFloat(cs[`border${s}Width`]) || 0, st: cs[`border${s}Style`], c: cs[`border${s}Color`] });
  const shown2 = (b) => b.w > 0 && b.st !== 'none' && b.st !== 'hidden' && alpha(b.c) > 0;
  // 中立の罫の色（--cp-line・--cp-line-soft）。1px の中立の罫は面の区切り（タブの右の罫など）＝帯ではない
  const rootCs = getComputedStyle(document.querySelector('.ui-cockpit') || document.body);
  const rgb = (hex) => { const h = hex.trim().replace('#', ''); if (!/^[0-9a-f]{6}$/i.test(h)) return hex.trim();
    return `rgb(${parseInt(h.slice(0, 2), 16)}, ${parseInt(h.slice(2, 4), 16)}, ${parseInt(h.slice(4, 6), 16)})`; };
  const neutral = new Set(['--cp-line', '--cp-line-soft'].map((k) => rgb(rootCs.getPropertyValue(k))).filter(Boolean));
  const bands = (e) => {
    const cs = getComputedStyle(e);
    const out = [];
    const top = side(cs, 'Top');
    for (const [s, name] of [['Left', '左'], ['Right', '右']]) {
      const b = side(cs, s);
      const hairline = b.w <= 1 && neutral.has(b.c);
      if (shown2(b) && !hairline && (b.w !== top.w || b.st !== top.st || b.c !== top.c || !shown2(top))) {
        out.push(`${name}の罫 ${b.w}px ${b.st} ${b.c}（上は ${top.w}px ${top.st} ${top.c}）`);
      }
    }
    // box-shadow: 計算値は「色 x y ぼかし 広がり inset」。色の関数の中のカンマで割らない
    const sh = cs.boxShadow;
    if (sh && sh !== 'none') {
      for (const one of sh.split(/,(?![^(]*\\))/)) {
        if (!/\\binset\\b/.test(one)) continue;
        const color = (/(rgba?\\([^)]*\\)|#[0-9a-f]+|[a-z]+(?=\\s))/i.exec(one) || [''])[0];
        const lens = (one.replace(/rgba?\\([^)]*\\)/g, '').match(/-?[\\d.]+px/g) || []).map(parseFloat);
        if (lens.length && lens[0] !== 0 && alpha(color) > 0) out.push(`inset の影の x ${lens[0]}px（${one.trim()}）`);
      }
    }
    for (const pe of ['::before', '::after']) {
      const p = getComputedStyle(e, pe);
      if (!p.content || p.content === 'none' || p.content === 'normal') continue;
      const w = parseFloat(p.width), h = parseFloat(p.height), eh = e.getBoundingClientRect().height;
      const paint = alpha(p.backgroundColor) > 0 || p.backgroundImage !== 'none' || shown2(side(p, 'Left'));
      if (paint && w > 0 && w <= 8 && h >= eh / 2) out.push(`${pe} の棒（${w}×${h}・${p.backgroundColor}）`);
    }
    if (/linear-gradient\\((to (left|right)|-?(90|270)deg)/.test(cs.backgroundImage)) out.push(`横向きのグラデーション（${cs.backgroundImage.slice(0, 60)}）`);
    return out;
  };
  // R110: 一覧のカードは名簿へ統合＝選んだ名簿の行（.pxrow.sel）。複数セッションのプロジェクトは開いて、プロジェクト行と
  // 子の行の 2 つに .sel が付く＝行の数ではなく「.sel の行が指すセッション」が 1 人であることを見る
  const sel = [...document.querySelectorAll('#agents .pxrow.sel')].filter(vis);
  const selSessions = [...new Set(sel.map((r) => r.dataset.session))];
  const els = [...new Set([...sel, ...document.querySelectorAll('.ui-cockpit .sel, .ui-cockpit #attn, .ui-cockpit .cpq, '
    + '.ui-cockpit .cpwork, .ui-cockpit .dlgmsg')])].filter(vis);
  const found = [];
  for (const e of els) for (const b of bands(e)) found.push(`${who(e)}${e.classList.contains('sel') ? '.sel' : ''}: ${b}`);
  return { rows: sel.length, sessions: selSessions, looked: els.length, found };
}"""

# ⑤ 台帳の行。全形状で、行の丈（ROW_MAX 以下）と、いま（c-detail）の見えている幅の下限を見る:
#   見えている行ごとに、いまの見えている幅（祖先で切った矩形の幅・display:none や visibility:hidden は 0）が
#   max(DETAIL_MIN, 行の中身の幅 × DETAIL_FRAC) 以上。いまは 1 列とスマホの段で -webkit-line-clamp: 2 に切られる
#   ＝列が潰れても行の丈は伸びない（35px に潰して行 42px のまま・丈の検査は緑だった＝2026-09-25 の第 C 波の検収）。
#   narrow（幅 600 以下の形）では、畳んだ台帳の中身も見る:
#   祖先に切られずに見えている行（台帳の中スクロールの外の行は数えない）ごとに、記号・プロジェクト・状態・いま・証拠・経過の
#   6 つが、箱があり visible で見えている丈 > 0・見えている幅 ≥ min(scrollWidth, READ_MIN)・文字が空でない。
#   いま・経過・証拠はプロジェクトの下端より下（2 段目）。見出しの並べ替えのボタン（.pxsort）が見えている数。
#   「中身を消して畳んだことにする」は丈だけの検査では緑だった。「見えている面積 > 0」だけでは幅 2px のセルも緑だった
LEDGER_CELLS = ("c-mono", "c-project", "c-state", "c-detail", "c-evd", "c-age")
LEDGER_SECOND = ("c-detail", "c-age", "c-evd")
LEDGER = "([narrow, cells, second, detailMin, detailFrac, readMin]) => {" + VIS_JS + """
  const rows = [...document.querySelectorAll('#agents .pxrow')].filter(vis);
  // R110-D6: 配達の札が出ている行は、札の段（下の余白の上乗せ＝padding-bottom − padding-top）を引いた 2 段の丈で見る
  const chipBand = (row) => { const d = row.querySelector('.dstate');
    if (!d || !d.getClientRects().length) return 0;
    const cs = getComputedStyle(row); return Math.max(0, parseFloat(cs.paddingBottom) - parseFloat(cs.paddingTop)); };
  const hs = rows.map((row) => row.getBoundingClientRect().height - chipBand(row));
  let worst = null;
  if (rows.length) {
    const i = hs.indexOf(Math.max(...hs));
    const row = rows[i];
    worst = { h: R(hs[i]), text: row.textContent.replace(/\\s+/g, ' ').trim().slice(0, 40),
      cells: [...row.children].filter(vis).map((x) => `${String(x.className).split(' ')[0]}=${R(x.getBoundingClientRect().width)}`) };
  }
  // 見えている幅（祖先で切った矩形の幅）。箱が無い・visibility が visible でないなら 0
  const visW = (e) => (!e || !e.getClientRects().length || getComputedStyle(e).visibility !== 'visible') ? 0
    : (() => { const k = clipRect(e); return Math.max(0, k.right - k.left); })();
  // いまの見えている幅の下限（全形状）。行の中身の幅＝行の clientWidth から左右の padding を引いた幅
  let thin = null, nthin = 0, dmin = Infinity, dneed = 0;
  for (const row of rows) {
    const cs = getComputedStyle(row);
    const inner = row.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const need = Math.max(detailMin, inner * detailFrac);
    const d = row.querySelector(':scope > .c-detail');
    const w = visW(d);
    if (w < dmin) { dmin = w; dneed = need; }
    if (w < need - 0.5) {
      nthin += 1;
      if (!thin) thin = { name: (row.querySelector('.c-project')?.textContent || '?').trim().slice(0, 12), w: R(w),
        need: R(need), inner: R(inner), sw: d ? d.scrollWidth : 0, h: R(row.getBoundingClientRect().height),
        text: d ? d.textContent.trim().slice(0, 24) : '', disp: d ? getComputedStyle(d).display : '無い' };
    }
  }
  const out = { rows: rows.length, max: rows.length ? R(Math.max(...hs)) : 0, worst,
    detailMin: rows.length ? R(dmin) : 0, detailNeed: R(dneed), thin, nthin };
  if (!narrow) return out;
  const whole = rows.filter((row) => {
    const q = row.getBoundingClientRect(), k = clipRect(row);
    return k.top <= q.top + 1 && k.bottom >= q.bottom - 1 && k.right - k.left > 0;
  });
  const bad = [];
  for (const row of whole) {
    const name = (row.querySelector('.c-project')?.textContent || '?').trim().slice(0, 12);
    const box = {};
    for (const c of cells) {
      const e = row.querySelector(':scope > .' + c);
      if (!e) { bad.push(`「${name}」.${c} が無い`); continue; }
      const k = clipRect(e);
      const boxed = e.getClientRects().length > 0;
      const w = boxed ? Math.max(0, k.right - k.left) : 0, h = boxed ? Math.max(0, k.bottom - k.top) : 0;
      const cs = getComputedStyle(e);
      if (!w || !h || cs.visibility !== 'visible') {
        bad.push(`「${name}」.${c} が見えない（display ${cs.display}・visibility ${cs.visibility}・見えている ${R(w)}×${R(h)}）`);
        continue;
      }
      // 読める幅: 中身の幅（scrollWidth）か読める下限の小さい方。折り返すセル（いま）は scrollWidth が箱に従う
      // ＝ここでは細い列を捕まえきれない（いまの幅の下限は上の全形状の検査が見る）
      const needW = Math.min(e.scrollWidth, readMin);
      if (w < needW - 0.5) {
        bad.push(`「${name}」.${c} の見えている幅 ${R(w)}px（読むのに ${R(needW)}px＝min(中身 ${e.scrollWidth}, ${readMin}) が要る）`);
        continue;
      }
      if (!e.textContent.trim()) bad.push(`「${name}」.${c} の文字が空`);
      box[c] = e.getBoundingClientRect();
    }
    const p = box['c-project'];
    for (const c of second) {
      if (p && box[c] && box[c].top < p.bottom - 0.5) {
        bad.push(`「${name}」.${c} が 2 段目に無い（上端 y ${R(box[c].top)}・プロジェクトの下端 ${R(p.bottom)}）`);
      }
    }
  }
  const sorts = [...document.querySelectorAll('.pxhrow .pxsort')].filter(shown).map((b) => b.textContent.trim());
  return { ...out, whole: whole.length, bad: bad.slice(0, 4), nbad: bad.length, sorts };
}"""
SORT_BUTTONS = 3           # ⑤ 幅 600 以下で見える並べ替えのボタン（プロジェクト・経過・状態）
NARROW_MAX = 600           # ⑤ 台帳を畳む形（統括の裁定の「スマホ（≤600）」）
# ⑤ いま（c-detail）の見えている幅の下限＝max(DETAIL_MIN, 行の中身の幅 × DETAIL_FRAC)。2026-09-25 の実測（第 C 波の CSS）:
#   834 は 268/806（0.33）・1112 は 434/1084（0.40）・890 は 301/862（0.35）・390 は 168/362（0.46）・612 は 134/584（0.23）・
#   1440 と 1194（3 カラムの左の列）は 122/555（0.22）。裁定の例の 1/4 では 612 と 3 カラムが今の木で赤になる
#   （どちらも 2 行で読めている）＝比は 1/5（612 は 117・3 カラムは 111 を要る）。96px は 11px の字で全角 8 字 ×2 行。
#   R110（2 段の行）で見直した: いまは下段で 記号の列（24）の右から 証拠（✓ 14分前）・経過の列の左まで＝3 カラムの名簿の最小の幅
#   280 で 256 − 24 − 段の溝 8×3 − 証拠 ≈ 55 − 経過 ≈ 45 ≈ 108px（12px の字で全角 9 字 ×2 行）。2026-10-06 の実測でいつもの行は
#   1112・1194（2 カラムの左の列 440）で 187・390 で 137・834 で 581＝下限 96・比 1/5 のまま足りる（下げない）
DETAIL_MIN = 96
DETAIL_FRAC = 1 / 5
READ_MIN = 32              # ⑤' 読める下限（10〜12px の字で全角 2〜3 字＝省略記号込みで意味が残る幅）

# ⑨ live の端末: #cp-xterm・xterm の中身（見えている矩形と中心の当たり）・タブ列・＋・足元
TERM_LIVE = "() => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const box = document.querySelector('#cp-xterm');
  const note = document.querySelector('#cp-term-note');
  const screens = [...document.querySelectorAll('#cp-xterm .xterm-screen, #cp-xterm .xterm-rows')];
  const scr = screens.find(shown) || null;
  let v = null, hit = false, over = '';
  if (scr) {
    reveal(scr);
    v = onScreen(clipRect(scr));
    if (area(v) > 0) {
      const at = document.elementFromPoint((v.left + v.right) / 2, (v.top + v.bottom) / 2);
      hit = !!at && !!box && box.contains(at);
      over = at && !hit ? who(at) : '';
    }
  }
  const r = scr ? scr.getBoundingClientRect() : null;
  // 端末の面（#cp-term）からはみ出して描かれていないか（面を丈 0 に畳んでも xterm は外へ描かれ続ける＝「出ている」に化ける）
  const pane = document.querySelector('#cp-term');
  const p = pane ? pane.getBoundingClientRect() : null;
  const outside = !!(r && p) && (r.top < p.top - 1 || r.bottom > p.bottom + 1 || r.left < p.left - 1 || r.right > p.right + 1);
  const bb = box ? box.getBoundingClientRect() : null;
  const out = { box: shown(box), boxH: bb ? R(bb.height) : 0, boxBy: box ? clipRect(box).by.join(' ') : '',
    screens: screens.length, screen: !!scr, outside,
    pane: p ? [R(p.top), R(p.bottom)] : null, scrY: r ? [R(r.top), R(r.bottom)] : null,
    w: r ? R(r.width) : 0, h: r ? R(r.height) : 0,
    visW: v ? R(v.right - v.left) : 0, visH: v ? R(v.bottom - v.top) : 0, by: v ? v.by.join(' ') : '', hit, over,
    tabs: shown(document.querySelector('#cp-tabs')), plus: shown(document.querySelector('#cp-term-new')),
    note: note ? note.textContent.trim() : '', selected: window.__office.debug.selected() };
  window.scrollTo(0, 0);
  return out;
}"""

# ⑨ デモの端末: 畳んだもの（#cp-xterm・.xterm・タブ列・＋）・足元の理由・残すもの（#cp-work の ❗・#cp-dlg の箱）
TERM_DEMO = "async () => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const { T: rawT, lang } = await import('/ui/hud/strings.js'); const T = (await import('/ui/cockpit/plain.js')).plainT(rawT);
  const d = window.__office.debug;
  const agents = d.world()?.agents || [];
  const sel = d.selected();
  const a = agents.find((x) => x.session === sel);
  const tabs = document.querySelector('#cp-tabs');
  const note = document.querySelector('#cp-term-note');
  const work = document.querySelector('#cp-work');
  const dlg = document.querySelector('#cp-dlg');
  const center = document.querySelector('.cpcenter');
  // 箱が消されていない（自分にも祖先にも display:none が無く、visible）。空の #cp-dlg は面積 0 でも「残っている」
  const kept = (e) => { if (!e) return false; for (let p = e; p; p = p.parentElement) if (getComputedStyle(p).display === 'none') return false;
    return getComputedStyle(e).visibility === 'visible'; };
  return { lang: lang(), readonly: T('cp_term_readonly'), live: T('cp_term_live'),
    agents: agents.length, sel, selName: a ? a.name : '', selAsked: !!(a && (a.question || a.ask)),
    box: shown(document.querySelector('#cp-xterm')),
    xterms: [...document.querySelectorAll('#cp-xterm .xterm, #cp-xterm .xterm-screen, #cp-xterm .xterm-rows')].filter(shown).length,
    tabsRow: shown(tabs), tabBtns: tabs ? [...tabs.querySelectorAll('button')].filter(shown).length : 0,
    plus: shown(document.querySelector('#cp-term-new')),
    noteShown: shown(note), note: note ? note.textContent.trim() : '',
    readonlyInCenter: !!center && center.textContent.includes(T('cp_term_readonly')),
    work: shown(work), workText: work ? work.textContent.replace(/\\s+/g, ' ').trim().slice(0, 40) : '',
    // R121: 許可待ち・質問のカードは作業ビューの末尾（#cp-dlg）へ移った＝要約の段（#cp-work）か #cp-dlg のどちらかに見えていればよい
    cpq: [work, dlg].filter(Boolean).reduce((n, box) => n + [...box.querySelectorAll('.cpq')].filter(shown).length, 0),
    dlg: kept(dlg) };
}"""

# ⑥ 要約の押せる点（要約・.usage・画面の交わりの中心）
GAUGE_POINT = "() => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const s = document.querySelector('#usage-summary');
  const u = document.querySelector('.usage');
  if (!vis(s)) return { ok: false, why: '要約（#usage-summary）が見えない', pins: document.querySelectorAll('#usage-summary .gpin').length };
  const a = s.getBoundingClientRect(), b = u.getBoundingClientRect();
  const x0 = Math.max(a.left, b.left, 0), x1 = Math.min(a.right, b.right, innerWidth);
  const y0 = Math.max(a.top, b.top, 0), y1 = Math.min(a.bottom, b.bottom, innerHeight);
  if (x1 <= x0 || y1 <= y0) return { ok: false, why: '要約が画面の外' };
  const x = (x0 + x1) / 2, y = (y0 + y1) / 2;
  return { ok: hits(s, x, y), x, y, why: hits(s, x, y) ? '' : '要約の中心に別の要素が重なる' };
}"""

# ⑥ 詳細の状態。呼ぶのは閉じている間（基準）と、開いた直後（スクロール 0 のまま撮った後）の 2 回。**どちらもスクロールしない**
#   （裁定: 「詳細は画面内に収める」はスクロール 0 で見る＝縦も横も。人が寄せれば見える、は「画面内」ではない）。
#   箱: 元の矩形（top/bottom/left/right）と、見えている矩形（clipRect＝祖先で切ったもの・画面では切らない）。
#       祖先に切られていないか・画面の外へ出ていないか（左へ出ても横スクロールは生まれない＝③' では捕まらない）
#   見えている丈（visH）: 見えている矩形を画面で切った丈。scrollHeight（sh）と比べる（中身が入る丈か）
#   命中: 5×5 の格子を**元の矩形全体**へ打つ（画面の外の点は elementFromPoint が null＝外れ）
#   opacity の積・visibility
#   中身（content）: 最初のゲージ行＝見出し（.gtitle）・プロバイダの行（.gprovhead か .grow）・最初の棒（.gbar）。
#       それぞれ祖先（#gauges 自身のスクロール箱を含む）に切られていない・画面内・中心が当たる・visible・opacity の積。
#       箱が描かれていても中身が見えない（細い帯に潰れた・子を hidden にした空の箱）は、箱の検査だけでは緑になった
#   画素を比べる範囲（rowBox）: 中身の 3 つの見えている矩形（画面で切る）を合わせた矩形＝箱の背景の寄与を外す
GAUGE_HITS = "(n) => {" + VIS_JS + """
  const g = document.querySelector('#gauges');
  const u = document.querySelector('.usage');
  const open = !!u && u.classList.contains('open');
  if (!g) return { open, exists: false, hit: 0, total: n * n };
  const vw = Math.min(innerWidth, document.documentElement.clientWidth || innerWidth), vh = innerHeight;
  const r = g.getBoundingClientRect();
  const c = clipRect(g);
  const v = onScreen(c);
  let hit = 0;
  if (r.width > 0 && r.height > 0) {
    for (let i = 0; i < n; i += 1) for (let j = 0; j < n; j += 1) {
      if (hits(g, r.left + r.width * (i + 0.5) / n, r.top + r.height * (j + 0.5) / n)) hit += 1;
    }
  }
  const opacityOf = (e) => { let o = 1; for (let p = e; p; p = p.parentElement) o *= parseFloat(getComputedStyle(p).opacity); return o; };
  const first = (sel) => [...g.querySelectorAll(sel)].find((e) => e.getClientRects().length > 0) || null;
  const content = [['見出し', '.gtitle'], ['ゲージの行', '.gprovhead, .grow'], ['棒', '.gbar']].map(([name, sel]) => {
    const e = first(sel);
    if (!e) return { name, sel, found: false };
    const q = e.getBoundingClientRect();
    const k = clipRect(e);
    const cut = k.left > q.left + 1 || k.top > q.top + 1 || k.right < q.right - 1 || k.bottom < q.bottom - 1;
    const inView = q.width > 0 && q.height > 0 && q.left >= -0.5 && q.top >= -0.5 && q.right <= vw + 0.5 && q.bottom <= vh + 0.5;
    const s = onScreen(k);
    const seen = area(s) > 0;
    const at = seen ? document.elementFromPoint((s.left + s.right) / 2, (s.top + s.bottom) / 2) : null;
    return { name, sel, found: true, who: who(e), text: e.textContent.trim().slice(0, 16),
      rect: [R(q.left), R(q.top), R(q.right), R(q.bottom)], seen: [s.left, s.top, s.right, s.bottom],
      visH: R(k.bottom - k.top), by: k.by.join(' '), cut, inView,
      hit: !!at && (at === e || e.contains(at)), over: at && !(at === e || e.contains(at)) ? who(at) : '',
      visibility: getComputedStyle(e).visibility, opacity: opacityOf(e) };
  });
  const got = content.filter((x) => x.found && x.seen[2] > x.seen[0] && x.seen[3] > x.seen[1]);
  const rowBox = got.length ? [Math.min(...got.map((x) => x.seen[0])), Math.min(...got.map((x) => x.seen[1])),
    Math.max(...got.map((x) => x.seen[2])), Math.max(...got.map((x) => x.seen[3]))] : [0, 0, 0, 0];
  return { open, exists: true, hidden: g.hidden, hit, total: n * n, opacity: opacityOf(g),
    visibility: getComputedStyle(g).visibility, vw, vh, scrollY: R(scrollY),
    left: r.left, right: r.right, top: r.top, bottom: r.bottom,
    visL: c.left, visR: c.right, visT: c.top, visB: c.bottom, by: c.by.join(' '),
    visH: R(v.bottom - v.top), sh: g.scrollHeight, maxH: getComputedStyle(g).maxHeight,
    rect: [R(r.left), R(r.top), R(r.width), R(r.height)], content, rowBox,
    usageOx: u ? getComputedStyle(u).overflowX : '' };
}"""

# ⑥ 撮る間だけ部屋を止める口（本人の「動きを止める」と同じ流れ＝OS の視差効果を減らすを emulate する）。
# 止まったこと・戻ったことは debug.roomMotion().still で確かめる（止まらなければ ⑥ は空振りの赤・戻らなければ ⑥' の赤）
MOTION_STILL = "() => !!window.__office.debug.roomMotion?.().still"

# ⑦ トレイがいま出している人と、部屋でタップする相手（キューの別の人）
TRAY_PEOPLE = """async () => {
  const { attentionQueue } = await import('/ui/core/world.js');
  const q = attentionQueue(window.__office.debug.world().agents)
    .map((a) => ({ session: a.session, name: a.name, ask: !!a.ask,
      options: (a.questionOptions || []).map((o) => o.label ?? o) }));
  const head = document.querySelector('#attn .trayhead')?.textContent || '';
  const shown = q.filter((a) => head.includes(a.name)).sort((a, b) => b.name.length - a.name.length)[0] || null;
  return { queue: q, head: head.trim(), shown };
}"""

# ⑦ ロボの点を画面に入れる（部屋の中の横スクロール・ページの縦スクロールで。人が指で寄せるのと同じ）
ROBOT_POINT = "(sid) => {" + VIS_JS + """
  const c = document.querySelector('.cproom');
  let p = window.__office.debug.roomPoint(sid);
  if (!p || !c) return { ok: false, why: '部屋にその人が居ない（debug.roomPoint が null）' };
  const stage = document.querySelector('.cpstage');
  if (stage) {
    const s = stage.getBoundingClientRect();
    if (p.left < s.left + 8 || p.left > s.right - 8) stage.scrollLeft += p.left - (s.left + s.right) / 2;
  }
  p = window.__office.debug.roomPoint(sid);
  if (p.top < 8 || p.top > innerHeight - 8) window.scrollBy(0, p.top - innerHeight / 2);
  p = window.__office.debug.roomPoint(sid);
  const inView = p.left >= 0 && p.left <= innerWidth && p.top >= 0 && p.top <= innerHeight;
  const on = inView && hits(c, p.left, p.top);
  return { ok: on, x: p.left, y: p.top, why: on ? '' : (inView ? 'ロボの点に別の要素が重なる' : 'ロボの点を画面に入れられない') };
}"""

# ⑦（指）叩く相手と点を選ぶ（論理 px）: ❗ でない・止まっている・選ばれていない人の、描いた矩形（12×16）の外 FINGER_OUT px。
# ほかの人の矩形からは FINGER_CLEAR px 以上離れた点だけ（最寄りの当たりでも、その人が一番近いままにする）
FINGER_PLAN = "([out, clear]) => {" + VIS_JS + """
  const c = document.querySelector('.cproom');
  const d = window.__office.debug;
  if (!c || !d.roomActors) return { ok: false, why: '部屋（.cproom）か debug.roomActors が無い' };
  const S = c.getBoundingClientRect().width / 184;
  const RW = 12, RH = 16;
  const agents = d.world()?.agents || [];
  const asked = new Set(agents.filter((a) => a.attention || a.question || a.ask).map((a) => a.session));
  const sel = d.selected();
  const all = d.roomActors().filter((a) => !a.leaving && Number.isFinite(a.x) && Number.isFinite(a.y));
  const edge = (a, px, py) => Math.hypot(Math.max(a.x - px, 0, px - (a.x + RW)), Math.max(a.y - py, 0, py - (a.y + RH))) * S;
  const o = out / S;
  for (const a of all) {
    if (!a.session || asked.has(a.session) || a.session === sel || a.walking || a.entering) continue;
    for (const [dir, px, py] of [['右', a.x + RW + o, a.y + RH / 2], ['左', a.x - o, a.y + RH / 2],
                                 ['上', a.x + RW / 2, a.y - o], ['下', a.x + RW / 2, a.y + RH + o]]) {
      if (px < 1 || py < 1 || px > 183 || py > 159) continue;
      const near = Math.min(Infinity, ...all.filter((b) => b !== a).map((b) => edge(b, px, py)));
      if (near < out + clear) continue;
      const g = agents.find((x) => x.session === a.session);
      return { ok: true, session: a.session, name: g ? g.name : a.session, dir, lx: px, ly: py, ax: a.x, ay: a.y,
        out: edge(a, px, py), near, scale: S };
    }
  }
  return { ok: false, why: `空振り: ❗ でない止まっている人のどの向きにも、${out}px ずらしてほかの人から ${out + clear}px 離れた点が無い`
    + `（部屋の人 ${all.length}・❗ ${asked.size}）` };
}"""

# ⑦（指）論理 px の点を画面に入れて、画面の座標を返す（canvas に当たることも確かめる）
FINGER_AT = "([lx, ly]) => {" + VIS_JS + """
  const c = document.querySelector('.cproom');
  let r = c.getBoundingClientRect();
  const S = r.width / 184;
  const stage = document.querySelector('.cpstage');
  if (stage) {
    const s = stage.getBoundingClientRect(), x = r.left + lx * S;
    if (x < s.left + 8 || x > s.right - 8) stage.scrollLeft += x - (s.left + s.right) / 2;
  }
  r = c.getBoundingClientRect();
  const y0 = r.top + ly * S;
  if (y0 < 8 || y0 > innerHeight - 8) window.scrollBy(0, y0 - innerHeight / 2);
  r = c.getBoundingClientRect();
  const x = r.left + lx * S, y = r.top + ly * S;
  const inView = x >= 0 && x <= innerWidth && y >= 0 && y <= innerHeight;
  const on = inView && hits(c, x, y);
  return { ok: on, x, y, why: on ? '' : (inView ? 'ずらした点に別の要素が重なる' : 'ずらした点を画面に入れられない') };
}"""

# ⑩ トレイの「自由に書く」を画面に入れて、押す点を返す（文言は strings の opt_free＝今の言語）
FREE_POINT = "async () => {" + VIS_JS + """
  const { T: rawT } = await import('/ui/hud/strings.js'); const T = (await import('/ui/cockpit/plain.js')).plainT(rawT);
  const want = T('opt_free');
  const b = [...document.querySelectorAll('#attn button[data-idx]')].filter(vis).find((x) => x.textContent.trim().endsWith(want));
  if (!b) return { ok: false, why: `トレイに「${want}」のボタンが無い` };
  reveal(b);
  const v = onScreen(clipRect(b));
  const x = (v.left + v.right) / 2, y = (v.top + v.bottom) / 2;
  const on = area(v) > 0 && hits(b, x, y);
  return { ok: on, x, y, label: b.textContent.trim(), why: on ? '' : '「自由に書く」の中心に別の要素が重なるか、画面に入らない' };
}"""

# ⑩ シート（指示欄）の置き場所: 開いた後のスクロールを控えてからスクロール 0 に戻して測る。部屋の箱の丈も（潰れていないか）
SHEET_STATE = "() => {" + VIS_JS + """
  const sy = R(scrollY);
  window.scrollTo(0, 0);
  const sh = document.querySelector('#sheet');
  const st = document.querySelector('.cpstage');
  const open = !!sh && !sh.hidden && vis(sh);
  const r = sh ? sh.getBoundingClientRect() : null;
  return { open, scrollAfter: sy, vw: Math.min(innerWidth, document.documentElement.clientWidth || innerWidth), vh: innerHeight,
    top: r ? R(r.top) : 0, bottom: r ? R(r.bottom) : 0, left: r ? R(r.left) : 0, right: r ? R(r.right) : 0,
    by: sh ? clipRect(sh).by.join(' ') : '', stageH: st ? R(st.getBoundingClientRect().height) : 0,
    pos: sh ? getComputedStyle(sh).position : '', row: sh ? getComputedStyle(sh).gridRowStart : '' };
}"""
SHEET_MIN = 44             # ⑩ スクロール 0 で見えていてほしいシートの丈（指 1 本ぶん）
# ⑩ ⑩' 開いた指示欄の遷移（translateX で滑り込む）が終わったか。測る矩形に遷移の途中の位置を混ぜない
SHEET_SETTLED = "() => { const s = document.querySelector('#sheet'); return !!s && !s.hidden && getComputedStyle(s).transform === 'none'; }"

# ③'' 長い名前（人が /rename で付ける名前＝title。全角 LONG_N 字）。固定ワールドの名前は短いので、端末の足元の名前が
# 折れも切れもせずに #app を横へ押し広げる退行（第 B 波の .cptermfoot > span { white-space: nowrap }）が ③' に写らない
LONG_N = 42
LONG_NAME = ("議事録アプリの保存先を共有フォルダへ移して権限と通知の設定を全部見直す作業の係です" * 2)[:LONG_N]

# ⑧ トレイの最初の答えを画面に入れて、押す点（見えている矩形の中心）を返す
ANSWER_POINT = "() => {" + VIS_JS + """
  const b = document.querySelector('#attn button[data-idx="0"]');
  if (!vis(b)) return { ok: false, why: 'トレイに答えのボタンが無い' };
  reveal(b);
  const v = onScreen(clipRect(b));
  const x = (v.left + v.right) / 2, y = (v.top + v.bottom) / 2;
  const seen = area(v) > 0;
  const on = seen && hits(b, x, y);
  return { ok: on, x, y, label: b.textContent.trim(),
    why: on ? '' : (seen ? '中心に別の要素が重なる' : `ボタンが画面に入らない（切っている祖先 ${v.by.join(' ') || '無し'}）`) };
}"""


# ⑧' ⑧'' 配達の手応えのトースト（#toast .tmsg）。押す前に見張りを付け、新しい .tmsg が出たら、出る遷移（show・120ms）の
#   後に**その時のスクロール位置のまま**測って記録する（トーストは 2.6 秒で消える＝高負荷で測り損ねないよう、ページの中で測る）。
#   画面内（元の矩形が 0〜画面の幅・丈）・祖先に切られない・中心が当たる・opacity の積と visibility。
#   1 列では hud の .toast（.main 基準の absolute・bottom 180px）が、ページの丈まで伸びた .main の下端に出た（第 C 波の検収:
#   390 で y 1700・画面の丈 844）＝答えた手応えも配達の失敗も、スクロール 0 の画面に出ない
TOAST_WATCH = "() => {" + VIS_JS + """
  window.__shapesToasts = [];
  const box = document.querySelector('#toast');
  if (!box) return false;
  window.__shapesToastObs?.disconnect();
  const measure = (m) => {
    const r = m.getBoundingClientRect();
    const k = clipRect(m);
    const vw = Math.min(innerWidth, document.documentElement.clientWidth || innerWidth), vh = innerHeight;
    const inView = r.width > 0 && r.height > 0 && r.left >= -0.5 && r.top >= -0.5 && r.right <= vw + 0.5 && r.bottom <= vh + 0.5;
    const cut = k.left > r.left + 0.5 || k.top > r.top + 0.5 || k.right < r.right - 0.5 || k.bottom < r.bottom - 0.5;
    let o = 1;
    for (let p = m; p; p = p.parentElement) o *= parseFloat(getComputedStyle(p).opacity);
    const at = inView ? document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2) : null;
    return { text: m.textContent.trim(), err: m.classList.contains('err'), connected: m.isConnected,
      top: R(r.top), bottom: R(r.bottom), left: R(r.left), right: R(r.right), vw, vh, scrollY: R(scrollY),
      docH: document.documentElement.scrollHeight, inView, cut, by: k.by.join(' '), opacity: o,
      visibility: getComputedStyle(m).visibility, pos: getComputedStyle(box).position,
      hit: !!at && (at === m || m.contains(at)), over: at && !(at === m || m.contains(at)) ? who(at) : '' };
  };
  // show が付いてから（requestAnimationFrame の後）遷移の 120ms を待って測る。付かないまま 2 秒なら、そのまま測る
  const settle = (m, t0) => {
    if (!m.classList.contains('show') && performance.now() - t0 < 2000) { setTimeout(() => settle(m, t0), 40); return; }
    setTimeout(() => window.__shapesToasts.push(measure(m)), 180);
  };
  const obs = new MutationObserver((recs) => {
    for (const rec of recs) for (const n of rec.addedNodes) {
      if (n instanceof Element && n.classList.contains('tmsg')) settle(n, performance.now());
    }
  });
  obs.observe(box, { childList: true });
  window.__shapesToastObs = obs;
  return true;
}"""
TOAST_TAKE = "() => window.__shapesToasts || []"
FAIL_ERROR = "shapes-smoke-500"   # ⑧'' 500 の本文の error（トーストの文言に載る＝このテストの失敗だと見分ける）
# ⑧''（長い error）: サーバーの error は長さの上限が無い（例外の文がそのまま載る）。空白の無い和文 53 字を足して、
# 390 で「配達失敗: …」が画面の幅を超える長さにする（折り返さなければ画面の外へ切れる）
LONG_ERROR = FAIL_ERROR + "：配達先のセッションが応答しないため投函できませんでした時間をおいて再試行するか端末から直接指示してください"

# モーダル（管理のフロー・⚙ 設定）: スクロール 0 で ⚙ を押した後、**その時のスクロール位置のまま**測る。
#   箱: #modal の上端 ≥ 0・左右が画面内・見えている丈（祖先と画面で切った丈）≥ min(scrollHeight, 画面の丈 − MODAL_MARGIN)・
#   見えている中心が当たる・opacity の積。下限の基準は中身の丈（scrollHeight）＝⑥ と同じ物差し。元の丈（箱自身の丈）を
#   基準にすると、34px の帯に潰れた箱は自分の丈を満たして緑だった（第 D 波の検収の modal_34・modal_wrap_short）。
#   中身（content）: 見出し（.mtitle）と最初の操作部品（#modal の中の最初の button・input・select・textarea）が、それぞれ
#   祖先（#modal 自身のスクロール箱を含む）に切られず・画面内で・見えている中心が当たり・visible で opacity の積 ≥ 0.9。
#   箱だけの検査では、中身を visibility:hidden にした空の箱が緑だった（同 modal_body_hidden）。
#   1 列では hud の .modalwrap（.main 基準の absolute; inset 0）がページの丈まで伸びた .main の縦の中央にモーダルを置いた
#   （第 C 波の検収: 890×626 で ⚙ は見えている丈 0・右上の ✕ と暗幕だけが出る）
MODAL_STATE = "() => {" + VIS_JS + """
  const wrap = document.querySelector('#modalwrap'), m = document.querySelector('#modal');
  if (!wrap || !m) return { exists: false };
  const vw = Math.min(innerWidth, document.documentElement.clientWidth || innerWidth), vh = innerHeight;
  const r = m.getBoundingClientRect();
  const k = clipRect(m);
  const v = onScreen(k);
  const opacityOf = (e) => { let o = 1; for (let p = e; p; p = p.parentElement) o *= parseFloat(getComputedStyle(p).opacity); return o; };
  const seen = area(v) > 0;
  const at = seen ? document.elementFromPoint((v.left + v.right) / 2, (v.top + v.bottom) / 2) : null;
  const firstOf = (sel) => [...m.querySelectorAll(sel)].find((e) => e.getClientRects().length > 0) || null;
  const content = [['見出し', '.mtitle'], ['最初の操作部品', 'button, input, select, textarea']].map(([name, sel]) => {
    const e = firstOf(sel);
    if (!e) return { name, sel, found: false };
    const q = e.getBoundingClientRect();
    const c = clipRect(e);
    const cut = c.left > q.left + 1 || c.top > q.top + 1 || c.right < q.right - 1 || c.bottom < q.bottom - 1;
    const inView = q.width > 0 && q.height > 0 && q.left >= -0.5 && q.top >= -0.5 && q.right <= vw + 0.5 && q.bottom <= vh + 0.5;
    const s = onScreen(c);
    const at2 = area(s) > 0 ? document.elementFromPoint((s.left + s.right) / 2, (s.top + s.bottom) / 2) : null;
    return { name, sel, found: true, who: who(e), text: (e.textContent || e.value || '').trim().slice(0, 16),
      rect: [R(q.left), R(q.top), R(q.right), R(q.bottom)], visH: R(Math.max(0, c.bottom - c.top)), by: c.by.join(' '),
      cut, inView, hit: !!at2 && (at2 === e || e.contains(at2)), over: at2 && !(at2 === e || e.contains(at2)) ? who(at2) : '',
      visibility: getComputedStyle(e).visibility, opacity: opacityOf(e) };
  });
  return { exists: true, open: !wrap.hidden, shown: wrap.classList.contains('show'), pos: getComputedStyle(wrap).position,
    top: R(r.top), bottom: R(r.bottom), left: R(r.left), right: R(r.right), h: R(r.height), sh: m.scrollHeight,
    visH: Math.max(0, v.bottom - v.top), by: k.by.join(' '), vw, vh, scrollY: R(scrollY),
    docH: document.documentElement.scrollHeight, opacity: opacityOf(m), visibility: getComputedStyle(m).visibility,
    title: m.querySelector('.mtitle')?.textContent.trim() || '', content,
    headB: R(document.querySelector('.cphead')?.getBoundingClientRect().bottom || 0),
    hit: !!at && (at === m || m.contains(at)), over: at && !(at === m || m.contains(at)) ? who(at) : '' };
}"""
MODAL_MARGIN = 40          # モーダルの上下の余白（hud の .modal の max-height: calc(100% - 40px) と同じ）
# スクロール 0 で押す点（押せる＝中心が当たる）
BUTTON_POINT = "(sel) => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const b = document.querySelector(sel);
  if (!vis(b)) return { ok: false, why: `${sel} が見えない` };
  const v = onScreen(clipRect(b));
  const x = (v.left + v.right) / 2, y = (v.top + v.bottom) / 2;
  const on = area(v) > 0 && hits(b, x, y);
  return { ok: on, x, y, why: on ? '' : `${sel} の中心に別の要素が重なるか、スクロール 0 の画面に無い` };
}"""

# ⑩' ❗ の無い世界で台帳の行を押す点（人が指で動かせるスクロールだけで画面に入れる）
ROW_POINT = "() => {" + VIS_JS + """
  const row = [...document.querySelectorAll('#agents .pxrow[data-session]')].find(vis);
  if (!row) return { ok: false, why: '台帳にセッションの行が無い' };
  reveal(row);
  const v = onScreen(clipRect(row));
  const x = (v.left + v.right) / 2, y = (v.top + v.bottom) / 2;
  const seen = area(v) > 0;
  const at = seen ? document.elementFromPoint(x, y) : null;
  const on = seen && !!at && (at === row || row.contains(at));
  return { ok: on, x, y, session: row.dataset.session,
    name: (row.querySelector('.c-project')?.textContent || '').trim().slice(0, 12),
    why: on ? '' : (seen ? `行の中心に ${at ? who(at) : '何も無い'} が重なる` : '行を画面に入れられない') };
}"""
# ⑩' 開いた指示欄を**その時のスクロール位置のまま**測る（部屋の箱の丈・置き方・段・見えている丈）
SHEET_NOW = "() => {" + VIS_JS + """
  const sh = document.querySelector('#sheet');
  const st = document.querySelector('.cpstage');
  const open = !!sh && !sh.hidden && vis(sh);
  const v = sh ? onScreen(clipRect(sh)) : null;
  const cols = getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\\s+/).length;
  const r = sh ? sh.getBoundingClientRect() : null;
  return { open, pos: sh ? getComputedStyle(sh).position : '', row: sh ? getComputedStyle(sh).gridRowStart : '',
    rect: r ? { top: R(r.top), bottom: R(r.bottom), left: R(r.left), right: R(r.right) } : null,
    visH: v ? R(Math.max(0, v.bottom - v.top)) : 0, scrollY: R(scrollY), vh: innerHeight,
    stageH: st ? R(st.getBoundingClientRect().height) : 0, cols,
    attnHidden: !!document.querySelector('#attn')?.hidden, selected: window.__office.debug.selected() };
}"""

# ⑭ 段の一致（スクロール 0）: 「ヘッダーがスマホの段か」「台帳がスマホの段か」「画面の幅 ≤ NARROW_MAX か」の 3 つを実描画で測る。
#   ヘッダーのスマホの段＝管理（.admin）がゲージ（.usage）より下の段に居る（1 列は 1 段目・3 カラムはゲージと同じ行）。
#   台帳のスマホの段＝見えている行の「いま」（.c-detail）がプロジェクト（.c-project）より下の段に居る（6 列は同じ段）。
#   R110: 名簿の行は幅に依らず 2 段＝check_stages は「どの形でも 2 段」を見る（台帳の段を画面の幅と揃える定義は撤去）。
#   画面の幅＝ページが組まれる幅（documentElement.clientWidth）。29a（静的）は問い合わせの**文字列**しか見ない＝問い合わせ先が
#   余白 12px を持つ .cphead に置かれて 601〜624 が「ヘッダーはスマホ・台帳は 6 列」に混ざった形（第 B 波）は、文字列が
#   600 のままでも起きた（第 D 波の検収）＝境が 1 本かは描いた結果で見る
STAGES = "() => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const admin = document.querySelector('.cphead .admin'), usage = document.querySelector('.cphead .brand');
  const a = vis(admin) ? admin.getBoundingClientRect() : null, u = vis(usage) ? usage.getBoundingClientRect() : null;
  const row = [...document.querySelectorAll('#agents .pxrow')].find(vis) || null;
  const p = row ? row.querySelector(':scope > .c-project') : null, d = row ? row.querySelector(':scope > .c-detail') : null;
  const pr = vis(p) ? p.getBoundingClientRect() : null, dr = vis(d) ? d.getBoundingClientRect() : null;
  const head = document.querySelector('.cphead');
  return { vw: document.documentElement.clientWidth, iw: innerWidth,
    head: !!(a && u), headPhone: !!(a && u) && a.top >= u.bottom - 1,
    adminY: a ? R(a.top) : null, usageY: u ? [R(u.top), R(u.bottom)] : null,
    adminRow: admin ? getComputedStyle(admin).gridRowStart : '', headH: head ? R(head.getBoundingClientRect().height) : 0,
    ledger: !!(pr && dr), ledgerPhone: !!(pr && dr) && dr.top >= pr.bottom - 0.5,
    detailY: dr ? R(dr.top) : null, projectY: pr ? [R(pr.top), R(pr.bottom)] : null,
    areas: row ? getComputedStyle(row).gridTemplateAreas : '',
    cols: row ? getComputedStyle(row).gridTemplateColumns.trim().split(/\\s+/).length : 0 };
}"""

# ⑮ オフライン帯（#offbar）: /api/office を落として帯が出た後、ページを y までスクロールして**その位置のまま**測る。
#   画面内（元の矩形が 0〜画面の幅・丈）・祖先に切られない・中心が当たる・opacity の積と visibility。
#   1 列では hud の .offbar（.main 基準の absolute; top 12px）がページの先頭に残り、スクロールすると画面の上の外へ出た
#   （第 D 波の実測: 390 で y −475・834 で y −524）＝凍結データを「今」と誤読させないための帯が、読んでいる位置に無い
OFFBAR_STATE = "(y) => {" + VIS_JS + """
  window.scrollTo(0, y);
  const e = document.querySelector('#offbar');
  if (!e) return { exists: false };
  const vw = Math.min(innerWidth, document.documentElement.clientWidth || innerWidth), vh = innerHeight;
  const r = e.getBoundingClientRect(), k = clipRect(e);
  const inView = r.width > 0 && r.height > 0 && r.left >= -0.5 && r.top >= -0.5 && r.right <= vw + 0.5 && r.bottom <= vh + 0.5;
  const cut = k.left > r.left + 0.5 || k.top > r.top + 0.5 || k.right < r.right - 0.5 || k.bottom < r.bottom - 0.5;
  let o = 1;
  for (let p = e; p; p = p.parentElement) o *= parseFloat(getComputedStyle(p).opacity);
  const at = inView ? document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2) : null;
  const cols = getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\\s+/).length;
  return { exists: true, shown: vis(e), text: e.textContent.trim(), top: R(r.top), bottom: R(r.bottom), left: R(r.left),
    right: R(r.right), vw, vh, scrollY: R(scrollY), docH: document.documentElement.scrollHeight, inView, cut,
    by: k.by.join(' '), opacity: o, visibility: getComputedStyle(e).visibility, pos: getComputedStyle(e).position,
    hit: !!at && (at === e || e.contains(at)), over: at && !(at === e || e.contains(at)) ? who(at) : '', cols };
}"""
# ⑮（ヘッダー）: 帯が出ている間、スクロール 0 でヘッダー（.cphead）の見えている button と .gpin の中心を 1 点ずつ引いて、
#   帯（かその中身）に当たるものを集める。見えている＝箱がある・visible・opacity の積 ≥ OPACITY_MIN（閉じたゲージの詳細は
#   opacity 0 と clip-path で畳まれている＝数えない）・中心が画面内。1 列で帯を画面の上（fixed; top 12px）に固定したとき、
#   612 で管理のボタン 5 個・390 で ? と要約とピン 2 個が帯に吸われた（第 E 波の検収）
OFFBAR_HEAD = "(omin) => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const e = document.querySelector('#offbar');
  const head = document.querySelector('.cphead');
  if (!e || !head) return { n: 0, bad: [], head: !!head };
  const r = e.getBoundingClientRect();
  const bad = [];
  let n = 0;
  for (const b of head.querySelectorAll('button, .gpin')) {
    if (!vis(b)) continue;
    let o = 1;
    for (let p = b; p; p = p.parentElement) o *= parseFloat(getComputedStyle(p).opacity);
    if (o < omin) continue;
    const q = b.getBoundingClientRect();
    const x = (q.left + q.right) / 2, y = (q.top + q.bottom) / 2;
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue;
    n += 1;
    const at = document.elementFromPoint(x, y);
    if (at && (at === e || e.contains(at))) {
      bad.push(`${who(b)}${b.id ? '' : '「' + b.textContent.trim().slice(0, 10) + '」'}（中心 ${R(x)},${R(y)}）`);
    }
  }
  return { n, bad, head: true, bar: `x ${R(r.left)}〜${R(r.right)}・y ${R(r.top)}〜${R(r.bottom)}`,
    pos: getComputedStyle(e).position, vw: innerWidth, vh: innerHeight };
}"""
OFFBAR_SCROLL = 600        # ⑮ 帯を測るスクロールの位置（ページの丈が足りなければ一番下＝3 カラムはスクロールしない）
OFFBAR_WAIT = 15.0         # ⑮ /api/office を落としてから帯が出るまで待つ上限（2 回続けて落ちると出る・poke ありで実測 1.6〜2.0 秒）

# ⑯ ブラウザ既定の枠: scope（CSS セレクタ）の中の見えている button の computed の border-style に outset／inset が
#   あるか（太さ 0 の辺は描かれない＝数えない）。UA の既定（2px outset）が一覧のカード・端末のタブ・「動きを止める」に残り、
#   灰色の額縁の積み重ねに見えた（第 D 波の着手前: 3 種 35 個）。⑫ は色のない対称の枠を帯としない＝ここで見る
UA_BORDERS = "(scope) => {" + VIS_JS + """
  const out = [];
  let n = 0;
  for (const b of document.querySelectorAll(`${scope} button`)) {
    if (!vis(b)) continue;
    n += 1;
    const cs = getComputedStyle(b);
    const ua = ['Top', 'Right', 'Bottom', 'Left'].filter((s) => /^(outset|inset)$/.test(cs[`border${s}Style`])
      && parseFloat(cs[`border${s}Width`]) > 0).map((s) => `${s.toLowerCase()} ${cs[`border${s}Width`]} ${cs[`border${s}Style`]}`);
    if (ua.length) out.push({ who: who(b) + (b.id ? '' : [...b.classList].slice(1).map((c) => '.' + c).join('')), ua: ua.join('・'),
      text: b.textContent.trim().slice(0, 12) });
  }
  return { n, bad: out };
}"""

# ⑧'（長い名前）⑧''（長い error）: トレイの答えのボタンが無い（回答済みの人を出している）ときに「次へ」を押す点
TRAY_NEXT_POINT = "() => {" + VIS_JS + """
  const b = [...document.querySelectorAll('#attn .traynext')].find(vis);
  if (!b) return { ok: false, why: 'トレイに「次へ」が無い' };
  reveal(b);
  const v = onScreen(clipRect(b));
  const x = (v.left + v.right) / 2, y = (v.top + v.bottom) / 2;
  const on = area(v) > 0 && hits(b, x, y);
  return { ok: on, x, y, why: on ? '' : '「次へ」の中心に別の要素が重なるか、画面に入らない' };
}"""

# ⑰ モーダルの暗幕: 開いた後の暗幕の矩形（#modalwrap の見えている矩形を画面で切ったもの）と、画素から外す矩形（#modal・✕）。
#   CSS px。画素は scrim_contrast が dsf を掛けて読む
SCRIM_GEO = "() => {" + VIS_JS + """
  const wrap = document.querySelector('#modalwrap'), m = document.querySelector('#modal'), x = document.querySelector('#modalclose');
  if (!vis(wrap) || !vis(m)) return { ok: false, why: '暗幕（#modalwrap）かモーダル（#modal）が見えない' };
  const box = (e) => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
  const w = onScreen(clipRect(wrap));
  return { ok: true, wrap: [w.left, w.top, w.right, w.bottom], cut: [box(m), ...(vis(x) ? [box(x)] : [])],
    bg: getComputedStyle(wrap).backgroundColor, opacity: parseFloat(getComputedStyle(wrap).opacity) };
}"""
# ⑰ 暗幕（#modalwrap とその中のモーダル・✕）を隠す／戻す（visibility）。2 フレーム待って画面に載せてから撮る
SCRIM_HIDE = """(hide) => { const w = document.querySelector('#modalwrap'); if (w) w.style.visibility = hide ? 'hidden' : '';
  return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); }"""
SCRIM_MAX = 0.5            # ⑰ 暗幕を戻した背面の明暗の差 ÷ 隠した差の上限（＝1 − 暗幕の不透明度。0.5 は不透明度 0.5 以上）。
                           #   hud の rgba(46,45,44,.14) は明るい紙の地のための薄さ＝暗い cockpit の上では 0.86（背面が読めたまま）
SCRIM_BASE_MIN = 40        # ⑰ 暗幕を隠した背面の明暗の差（明るい 5% と暗い 5% の平均の差・輝度 0〜255）の下限（平らな背面は測れない＝空振りの赤）
SCRIM_EDGE = 3             # ⑰ 画素から外す #modal・✕ の矩形を広げる CSS px（縁の滲み・hairline を数えない）

# ⑱ スクロールのロック: 今のページと、#modalwrap の外で今スクロールしている箱の位置（変わったら背面が動いた）
SCROLL_SNAPSHOT = "() => {" + VIS_JS + """
  const wrap = document.querySelector('#modalwrap');
  const els = [];
  for (const e of document.querySelectorAll('body *')) {
    if (wrap && wrap.contains(e)) continue;
    if (e.scrollTop || e.scrollLeft) els.push(`${who(e)} ${R(e.scrollTop)},${R(e.scrollLeft)}`);
  }
  return { y: R(scrollY), x: R(scrollX), els, docH: document.documentElement.scrollHeight, vh: innerHeight,
    cols: getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\\s+/).length,
    rootOverflow: getComputedStyle(document.documentElement).overflowY };
}"""
# ⑱ 指（ホイール）を置く点: ヘッダーの無地（ブランド）＝押せる部品でもスクロールの箱でもない（開く前は陽性対照＝ページが動く）。
#   モーダルを開いた後は、同じ点（暗幕かモーダルの上）・暗幕だけが当たる点（#modalwrap の縁を 8 点探す）・モーダルの中心
#   （中を一番下までスクロールしてから＝モーダルの中のスクロールが背面へ連なるか）
SWIPE_POINTS = "() => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const brand = document.querySelector('.cphead .brand');
  const out = {};
  if (vis(brand)) { const r = brand.getBoundingClientRect(); out.brand = [(r.left + r.right) / 2, (r.top + r.bottom) / 2]; }
  const wrap = document.querySelector('#modalwrap'), m = document.querySelector('#modal');
  if (vis(wrap)) {
    const w = onScreen(clipRect(wrap));
    const xs = [w.left + 10, (w.left + w.right) / 2, w.right - 10], ys = [w.top + 10, (w.top + w.bottom) / 2, w.bottom - 10];
    for (const y of ys) for (const x of xs) {
      if (document.elementFromPoint(x, y) === wrap) { out.scrim = [x, y]; break; }
    }
  }
  if (vis(m)) {
    m.scrollTop = m.scrollHeight;                   // 中を一番下まで＝この先のスクロールは背面へ連なるかどうか
    const r = m.getBoundingClientRect();
    const v = onScreen(r);
    out.modal = [(v.left + v.right) / 2, (v.top + v.bottom) / 2];
  }
  return out;
}"""
SWIPE_DY = 360             # ⑱ 1 回のスクロールの距離（CSS px・下へ）

# ⑲ モーダルの ✕（#modalclose）: 画面内・中心が当たる・ヘッダーの押せる部品（button・.gpin）と矩形が重ならない・
#   モーダルの見出しの文字（.mtitle の文字の矩形）から CLOSE_TITLE_GAP px 以上離れ、最初の操作部品に重ならない。スクロール 0 のまま測る
CLOSE_TITLE_GAP = 4        # ⑲ ✕ と見出しの字の間に要る px。タッチの ✕（44px）で見出しの右の余白を 36px のままにすると、長い見出しの
                           #   字が ✕ の左端から 0〜2px（重なりは 0.5px 以下＝重ならない判定は緑）＝字が ✕ に貼り付いた（2026-09-26 の変異）
CLOSE_STATE = "() => {" + VIS_JS + """
  const x = document.querySelector('#modalclose'), m = document.querySelector('#modal');
  if (!vis(x) || !m) return { ok: false, why: '✕（#modalclose）が見えない' };
  const vw = Math.min(innerWidth, document.documentElement.clientWidth || innerWidth), vh = innerHeight;
  const r = x.getBoundingClientRect();
  const lap = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5
    && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5;
  const at = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
  const head = [...document.querySelectorAll('.cphead button, .cphead .gpin')].filter(vis);
  const heads = head.filter((h) => lap(r, h.getBoundingClientRect())).map((h) => {
    const q = h.getBoundingClientRect();
    return `${who(h)}${h.id ? '' : '「' + h.textContent.trim().slice(0, 8) + '」'}（x ${R(q.left)}〜${R(q.right)}・y ${R(q.top)}〜${R(q.bottom)}）`;
  });
  const t = m.querySelector('.mtitle');
  let title = false;
  // 見出しの字とは __TG__px 以上離す（字の送りの端数で、余白を削っても「重ならないが ✕ に字が貼り付く」が緑になった）
  const rt = { left: r.left - __TG__, right: r.right + __TG__, top: r.top - __TG__, bottom: r.bottom + __TG__ };
  if (t) { const rg = document.createRange(); rg.selectNodeContents(t); title = [...rg.getClientRects()].some((q) => lap(rt, q)); }
  const first = [...m.querySelectorAll('button, input, select, textarea')].find((e) => e.getClientRects().length > 0);
  return { ok: true, rect: `x ${R(r.left)}〜${R(r.right)}・y ${R(r.top)}〜${R(r.bottom)}`, vw, vh, scrollY: R(scrollY),
    w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10,
    inView: r.left >= -0.5 && r.top >= -0.5 && r.right <= vw + 0.5 && r.bottom <= vh + 0.5,
    hit: !!at && (at === x || x.contains(at)), over: at && !(at === x || x.contains(at)) ? who(at) : '',
    head: head.length, heads, title, first: first ? lap(r, first.getBoundingClientRect()) : false,
    firstWho: first ? who(first) : '', pos: getComputedStyle(x).position };
}""".replace("__TG__", str(CLOSE_TITLE_GAP))

# ⑳ オフライン帯と配達の手応えのトースト: 新しい .tmsg が出る遷移の後に、**その時に出ている全部のトーストと帯**を同じ瞬間に
#   測って記録する（トーストは 2.6 秒で消える＝2 件そろった瞬間をページの中で掴む）。重なりは矩形の交わり（0.5px を超える）
TOAST_BAND_WATCH = "() => {" + VIS_JS + """
  window.__shapesBand = [];
  const box = document.querySelector('#toast');
  if (!box) return false;
  window.__shapesBandObs?.disconnect();
  const lap = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5
    && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5;
  const measure = () => {
    const bar = document.querySelector('#offbar');
    const b = bar && vis(bar) ? bar.getBoundingClientRect() : null;
    const vw = Math.min(innerWidth, document.documentElement.clientWidth || innerWidth), vh = innerHeight;
    const toasts = [...box.querySelectorAll('.tmsg')].filter((m) => m.isConnected && vis(m)).map((m) => {
      const r = m.getBoundingClientRect();
      const inView = r.left >= -0.5 && r.top >= -0.5 && r.right <= vw + 0.5 && r.bottom <= vh + 0.5;
      const at = inView ? document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2) : null;
      return { text: m.textContent.trim().slice(0, 24), err: m.classList.contains('err'), bottom: r.bottom,
        rect: `y ${R(r.top)}〜${R(r.bottom)}・x ${R(r.left)}〜${R(r.right)}`, h: R(r.height),
        inView, lap: !!b && lap(r, b), hit: !!at && (at === m || m.contains(at)),
        over: at && !(at === m || m.contains(at)) ? who(at) : '' };
    });
    window.__shapesBand.push({ toasts, band: b ? `y ${R(b.top)}〜${R(b.bottom)}・x ${R(b.left)}〜${R(b.right)}` : '',
      bandTop: b ? b.top : null,
      bandShown: !!b, vw, vh, pos: getComputedStyle(box).position, bottom: getComputedStyle(box).bottom,
      cols: getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\\s+/).length });
  };
  const settle = (m, t0) => {
    if (!m.classList.contains('show') && performance.now() - t0 < 2000) { setTimeout(() => settle(m, t0), 40); return; }
    setTimeout(measure, 180);
  };
  const obs = new MutationObserver((recs) => {
    for (const rec of recs) for (const n of rec.addedNodes) {
      if (n instanceof Element && n.classList.contains('tmsg')) settle(n, performance.now());
    }
  });
  obs.observe(box, { childList: true });
  window.__shapesBandObs = obs;
  return true;
}"""
TOAST_BAND_TAKE = "() => window.__shapesBand || []"
# ⑳ 帯が出ている間に答える ❗: sess-cp-09 の質問文だけを替える（⑧ で答え済みの印＝attnKey は質問文で決まる＝替えると
#   答えのボタンが戻る）。承認まち（sess-cp-07）の印は session で決まる＝答え済みのまま（トレイは「次へ」で 09 へ進める）
BAND_SID = "sess-cp-09"
BAND_QUESTION_TAIL = "（オフラインの間にもう一度確かめます）"
# ⑳ 1 列で出す失敗の error（LONG_ERROR の和文を 2 回＝約 120 字）。1 列の一番広い形（1112）でも 2 行以上に折り返す長さ
#   （LONG_ERROR だけでは 1112 で 1 行に収まり、折り返したトーストを試せない）。3 カラムは hud の nowrap のまま＝LONG_ERROR
BAND_ERROR = LONG_ERROR + "・" + LONG_ERROR[len(FAIL_ERROR) + 1:]
BAND_GAP_MAX = 16          # ⑳ 1 列で帯の上端と一番下のトーストの下端の隙間の上限（CSS は 8px・丸めの余裕 8px）

# ㉑ 指示欄を開いている間の ❗トレイ（スクロール 0）: トレイの見えているボタンが全部、画面内で中心が当たる・指示欄の矩形と
#   トレイの矩形が重ならない（3 カラムでは指示欄が .main の右に重なって右カラムのトレイを覆った＝答えるのに指示欄を閉じる要があった）
TRAY_WITH_SHEET = "() => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const tray = document.querySelector('#attn'), sh = document.querySelector('#sheet');
  if (!vis(tray)) return { ok: false, why: 'トレイ（#attn）が見えない' };
  if (!vis(sh)) return { ok: false, why: '指示欄（#sheet）が開いていない' };
  const vw = Math.min(innerWidth, document.documentElement.clientWidth || innerWidth), vh = innerHeight;
  const t = tray.getBoundingClientRect(), s = sh.getBoundingClientRect();
  const lap = Math.min(t.right, s.right) - Math.max(t.left, s.left) > 0.5 && Math.min(t.bottom, s.bottom) - Math.max(t.top, s.top) > 0.5;
  const btns = [...tray.querySelectorAll('button')].filter(vis).map((b) => {
    const q = b.getBoundingClientRect();
    const inView = q.left >= -0.5 && q.top >= -0.5 && q.right <= vw + 0.5 && q.bottom <= vh + 0.5;
    const at = inView ? document.elementFromPoint((q.left + q.right) / 2, (q.top + q.bottom) / 2) : null;
    return { label: b.textContent.trim().slice(0, 12), inView, hit: !!at && (at === b || b.contains(at)),
      over: at && !(at === b || b.contains(at)) ? who(at) : '', rect: `x ${R(q.left)}〜${R(q.right)}・y ${R(q.top)}〜${R(q.bottom)}` };
  });
  return { ok: true, lap, btns, tray: `x ${R(t.left)}〜${R(t.right)}・y ${R(t.top)}〜${R(t.bottom)}`,
    sheet: `x ${R(s.left)}〜${R(s.right)}・y ${R(s.top)}〜${R(s.bottom)}`, pos: getComputedStyle(sh).position,
    cols: getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\\s+/).length };
}"""

# ㉒ スクロールバーの溝: html の scrollbar-gutter の計算値・背面の幅（clientWidth）・html の overflow（ロックが掛かったか＝空振りの番人）
GUTTER_STATE = """() => ({ gutter: getComputedStyle(document.documentElement).scrollbarGutter,
  cw: document.documentElement.clientWidth, overflow: getComputedStyle(document.documentElement).overflowY,
  cols: getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\\s+/).length })"""

# ㉔ 暗幕が画面の全体を覆う（開いている間・スクロール 0）: #modalwrap の矩形が画面の 4 辺に届き、ヘッダー（.cphead）とフッター（.cpfoot）の
#   画面に入っている帯の左・中・右の 3 点が暗幕（かその中のモーダル）に当たる。✕ とモーダルの矩形の離れ（✕ がモーダルの矩形の外へ
#   出ている最大の px・中に居れば 0）。3 カラムでは hud の置き方（.main 基準の absolute）のまま、暗幕が .main だけに掛かり、
#   明るいまま押せないヘッダーとフッターが残った・✕ は .main の右上の角（1194 で x 1144〜1176）でモーダル（x 387〜807）から遠かった
SCRIM_COVER = "() => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const wrap = document.querySelector('#modalwrap'), m = document.querySelector('#modal'), x = document.querySelector('#modalclose');
  if (!vis(wrap) || !vis(m)) return { ok: false, why: '暗幕（#modalwrap）かモーダル（#modal）が見えない' };
  const vw = Math.min(innerWidth, document.documentElement.clientWidth || innerWidth), vh = innerHeight;
  const w = wrap.getBoundingClientRect();
  const cover = w.left <= 0.5 && w.top <= 0.5 && w.right >= vw - 0.5 && w.bottom >= vh - 0.5;
  const pts = [];
  for (const sel of ['.cphead', '.cpfoot']) {
    const e = document.querySelector(sel);
    if (!e || !e.getClientRects().length) continue;
    const r = e.getBoundingClientRect();
    const top = Math.max(r.top, 0), bottom = Math.min(r.bottom, vh);
    if (bottom - top < 2) continue;                       // 画面の外（1 列のフッターはページの一番下）
    const y = (top + bottom) / 2;
    for (const fx of [0.08, 0.5, 0.92]) {
      const px = r.left + (r.right - r.left) * fx;
      const at = document.elementFromPoint(px, y);
      pts.push({ sel, x: R(px), y: R(y), under: !!at && (at === wrap || wrap.contains(at)), at: at ? who(at) : '無し' });
    }
  }
  const mr = m.getBoundingClientRect();
  const c = vis(x) ? x.getBoundingClientRect() : null;
  const gap = c ? Math.max(mr.left - c.left, c.right - mr.right, mr.top - c.top, c.bottom - mr.bottom, 0) : null;
  return { ok: true, cover, pts, gap: gap === null ? null : R(gap),
    wrap: `x ${R(w.left)}〜${R(w.right)}・y ${R(w.top)}〜${R(w.bottom)}`, vw, vh, pos: getComputedStyle(wrap).position,
    close: c ? `x ${R(c.left)}〜${R(c.right)}・y ${R(c.top)}〜${R(c.bottom)}` : '',
    modal: `x ${R(mr.left)}〜${R(mr.right)}・y ${R(mr.top)}〜${R(mr.bottom)}` };
}"""
CLOSE_GAP = 12             # ㉔ ✕ がモーダルの矩形の外へ出てよい上限（CSS px）。今の置き方は角の内側 10px＝0

# ㉕ ㉖ 開いた指示欄のまわり（スクロール 0）: 指示欄の矩形・⤢ か・部屋の箱と台帳の箱の丈・部屋の canvas と重なる幅
#   （canvas は部屋の箱で切った見えている矩形・縦に重なるときの横の重なり）・トレイの矩形と見えているボタンの当たり
SHEET_LAYOUT = "() => {" + VIS_JS + """
  window.scrollTo(0, 0);
  const q = (s) => document.querySelector(s);
  const sh = q('#sheet'), st = q('#stage'), cv = q('.cproom'), tb = q('#cp-roster'), tray = q('#attn');
  const vw = Math.min(innerWidth, document.documentElement.clientWidth || innerWidth), vh = innerHeight;
  const box = (e) => { const r = e.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }; };
  const txt = (b) => b ? `x ${R(b.left)}〜${R(b.right)}・y ${R(b.top)}〜${R(b.bottom)}` : '無し';
  const lapW = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
  const lapH = (a, b) => Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  const s = vis(sh) ? box(sh) : null;
  const c = cv && vis(cv) ? clipRect(cv) : null;
  const t = vis(tray) ? box(tray) : null;
  const btns = t ? [...tray.querySelectorAll('button')].filter(vis).map((b) => {
    const r = b.getBoundingClientRect();
    const inView = r.left >= -0.5 && r.top >= -0.5 && r.right <= vw + 0.5 && r.bottom <= vh + 0.5;
    const at = inView ? document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2) : null;
    return { label: b.textContent.trim().slice(0, 10), ok: inView && !!at && (at === b || b.contains(at)),
      over: at && !(at === b || b.contains(at)) ? who(at) : (inView ? '' : '画面の外') };
  }) : [];
  return { vw, vh, open: !!s, wide: !!sh && sh.classList.contains('wide'), pos: sh ? getComputedStyle(sh).position : '',
    sheet: s ? [s.left, s.top, s.right, s.bottom] : null, sheetTxt: txt(s), sheetW: s ? R(s.right - s.left) : 0,
    sheetH: s ? R(s.bottom - s.top) : 0, sheetMore: !!s && sh.scrollHeight > sh.clientHeight + 1,
    stageH: st ? R(st.getBoundingClientRect().height) : 0, tableH: tb ? R(tb.getBoundingClientRect().height) : 0,
    canvasW: c ? R(c.right - c.left) : 0, canvasTxt: txt(c),
    roomCover: s && c && lapH(s, c) > 0.5 ? R(lapW(s, c)) : 0,
    tray: t ? [t.left, t.top, t.right, t.bottom] : null, trayTxt: txt(t),
    trayLap: !!(s && t && lapW(s, t) > 0.5 && lapH(s, t) > 0.5), btns,
    cols: getComputedStyle(q('.main')).gridTemplateColumns.trim().split(/\\s+/).length };
}"""
# ㉕ ㉖ 3 カラムの形では、自分の幅に加えて、窓の幅だけを変えて測る（丈は形のまま）。1181＝3 カラムの下端・1279＝hud の
#   幅 1279 以下の則が効く上端・1920＝広い画面（⤢ の上限 1040 に当たる）
SHEET_SWEEP = (1401, 1440, 1920)
ROOM_COVER_MAX = 0.34      # ㉕ 指示欄が部屋の canvas と重なってよい幅（canvas の幅に対する比）。指示欄の下限は R95 の 480 を保つ
                           #   （本人の「380 は狭い」→ R95 で 480。下げるのは本人の裁定）＝1181 で 183px・1194 で 170px（3 割）・
                           #   1440 以上 0。これより広がったら赤（幅の式か置き方が変わった）


def sse(*events):
    return "".join(f"event: {k}\ndata: {v}\n\n" for k, v in events)


def fake_api(world_body, sb_body, posts, seen, fail=None):
    """/api/* の横取り。GET は固定の応答、GET 以外は記録して {ok:true}（どこにも書かない）。
    world_body は文字列か {"body": 文字列}（後者は途中で差し替えられる＝⑩' の ❗ の無い世界をポーリングでも保つ）。
    {"down": True} の間は /api/office だけを 503 で返す（⑮ のオフライン帯＝ポーリングが 2 回続けて落ちると出る）。
    fail={"on": True} の間は答えの POST（ANSWER_PATHS）だけを 500＋{"error": fail["error"] か FAIL_ERROR} で返す
    （記録には status 500 を付ける＝⑧'' の配達失敗。ほかの POST は巻き込まない＝⓪ が数えない 500 はこの 2 つの path だけ）"""
    office = world_body if isinstance(world_body, dict) else {"body": world_body}
    term_list = json.dumps({"terms": [TERM_INFO]})
    hello = json.dumps({**TERM_INFO, "from": 0, "reset": True}, separators=(",", ":"))
    out = base64.b64encode(TERM_OUT.encode()).decode()
    # R111-B2: 端末の購読は多重化（/api/term/streams・out は「<id> <base64>」＝JSON でない）に移った
    mux = sse(("hello", hello), ("out", f"{TERM_INFO['id']} {out}"))

    def handler(route):
        req = route.request
        path = urlparse(req.url).path
        seen[path] = seen.get(path, 0) + 1
        if req.method != "GET":
            try:
                body = json.loads(req.post_data or "null")
            except ValueError:
                body = req.post_data
            if fail and fail.get("on") and path in ANSWER_PATHS:
                posts.append({"path": path, "method": req.method, "body": body, "status": 500})
                return route.fulfill(status=500, content_type="application/json",
                                     body=json.dumps({"error": fail.get("error") or FAIL_ERROR}, ensure_ascii=False))
            posts.append({"path": path, "method": req.method, "body": body})
            return route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        js = "application/json; charset=utf-8"
        if path == "/api/office":
            if office.get("down"):
                return route.fulfill(status=503, content_type="text/plain", body="shapes smoke: office down")
            return route.fulfill(status=200, content_type=js, body=office["body"])
        if path == "/api/status_board":
            return route.fulfill(status=200, content_type=js, body=sb_body)
        if path == "/api/session/dialog":
            return route.fulfill(status=200, content_type=js, body=json.dumps(
                {"ok": True, "messages": [], "depth": 0, "maxDepth": 0, "hasMore": False, "total": 0}))
        if path == "/api/digest":
            return route.fulfill(status=200, content_type=js,
                                 body='{"available":false,"since":0,"totals":{"tasksDone":0}}')
        if path == "/api/timeline":
            return route.fulfill(status=200, content_type=js, body='{"events":[]}')
        if path == "/api/events":
            # /api/office が落ちている間は poke も送る（サーバーは生きていて office だけが落ちている形）＝ページが自分の
            # ポーリング（poll の refresh）で続けて 2 回取りに来て落ちる。poke が無いと再接続（3 秒ごと）を 2 回待つ（4〜5 秒）
            down = (("poke", '{"seq":1}'),) if office.get("down") else ()
            return route.fulfill(status=200, content_type="text/event-stream",
                                 body=sse(("hello", '{"seq":0,"v":2}'), *down))
        if path == "/api/term/list":
            return route.fulfill(status=200, content_type=js, body=term_list)
        if path == "/api/term/stream":
            return route.fulfill(status=200, content_type="text/event-stream",
                                 body=sse(("hello", hello), ("out", out)))
        if path == "/api/term/streams":
            return route.fulfill(status=200, content_type="text/event-stream", body=mux)
        return route.continue_()                     # 残りの GET はテストサーバー（OFFICE_HOME は .ui_shot_home）

    return handler


def wait_until(page, fn, timeout=6.0, step=100):
    """fn() が真になるまで待つ（ページのイベントを回しながら）。間に合わなければ False"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        page.wait_for_timeout(step)
    return bool(fn())


def changed_pixels(shots, box, dsf):
    """⑥ 開く前 2 枚（b1・b2）と開いた後（a）の画面から、box（CSS px の [左, 上, 右, 下]）の中で数える。
    (変わった画素, 比べた画素, 開く前に動いた画素)。比べた画素＝b1→b2 で動かない（止まっている）画素、
    変わった画素＝そのうち b2→a で変わった画素。門は「変わった ÷ 比べた」の比を PIX_RATIO_MIN と比べる（0 か否かではない）。
    部屋は撮る間だけ止めてある（run_shape の ⑥）。それでも止めきれない遅い動き（端末のカーソルの点滅・時計の文字）は
    b1→b2 のマスクをすり抜けて数画素〜数千画素が「変わった」に入る＝透明の詳細でも 0 にはならない。
    部屋を止めないと、1 列の形では開いた詳細が部屋に重なり、ロボの動きだけで「> 0」が満たされた（2026-09-25 のレビュー）"""
    from PIL import Image, ImageChops

    b1, b2, after = (Image.open(io.BytesIO(s)).convert("RGB") for s in shots)
    width, height = after.size
    lim = (width, height, width, height)
    x0, y0, x1, y1 = (max(0, min(lim[i], round(v * dsf))) for i, v in enumerate(box))
    if x1 <= x0 or y1 <= y0:
        return 0, 0, 0
    crop = (x0, y0, x1, y1)

    def moved(p, q):
        r, g, b = ImageChops.difference(p.crop(crop), q.crop(crop)).split()
        return ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v > PIX_TOL else 0)

    before = moved(b1, b2)
    moved_n = before.histogram()[255]
    still_changed = ImageChops.subtract(moved(b2, after), before)
    return still_changed.histogram()[255], (x1 - x0) * (y1 - y0) - moved_n, moved_n


def scrim_contrast(shots, geo, dsf):
    """⑰ 暗幕の効き。暗幕を隠した 2 枚（b1・b2）と戻した 1 枚（a）の輝度（L）で、暗幕の矩形（geo["wrap"]）から #modal と ✕ の矩形
    （SCRIM_EDGE だけ広げる）と、開く前に動いた画素（b1→b2 が PIX_TOL を超える）を外した画素を数える。
    **同じ画素**で、隠した画面で明るい 5%（b2 の p95 以上）と暗い 5%（p5 以下）の平均の差を、隠した画面（base）と戻した画面（dim）で比べる。
    不透明度 α の一様な暗幕は、どの画素も a = α・暗幕の色 + (1 − α)・b にする＝差はちょうど (1 − α) 倍に縮む（暗幕の色に依らない）。
    (base, dim, 数えた画素)。暗い UI では明暗の幅の分位（p95 − p5）は地の色に埋もれて平らになり、390 では測れなかった
    （2026-09-26 の着手時の実測: 幅 44）＝明るい少数の画素（文字・部屋）を同じ位置で追う。
    隠した画面と戻した画面は同じ組み（開いたまま）＝画素の組が崩れない（開く前の画面と比べると、開いたときのずれが混ざる）"""
    from PIL import Image, ImageChops, ImageDraw, ImageStat

    b1, b2, after = (Image.open(io.BytesIO(s)).convert("L") for s in shots)
    width, height = after.size
    lim = (width, height, width, height)
    x0, y0, x1, y1 = (max(0, min(lim[i], round(v * dsf))) for i, v in enumerate(geo["wrap"]))
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0.0, 0
    crop = (x0, y0, x1, y1)
    mask = Image.new("L", (x1 - x0, y1 - y0), 255)
    draw = ImageDraw.Draw(mask)
    for left, top, right, bottom in geo["cut"]:
        draw.rectangle([round((left - SCRIM_EDGE) * dsf) - x0, round((top - SCRIM_EDGE) * dsf) - y0,
                        round((right + SCRIM_EDGE) * dsf) - x0, round((bottom + SCRIM_EDGE) * dsf) - y0], fill=0)
    moved = ImageChops.difference(b1.crop(crop), b2.crop(crop)).point(lambda v: 255 if v > PIX_TOL else 0)
    mask = ImageChops.subtract(mask, moved)
    before, dimmed = b2.crop(crop), after.crop(crop)
    hist = before.histogram(mask=mask)
    n = sum(hist)
    if not n:
        return 0.0, 0.0, 0
    acc, lo, hi = 0, None, None
    for level, c in enumerate(hist):
        acc += c
        if lo is None and acc >= n * 0.05:
            lo = level
        if hi is None and acc >= n * 0.95:
            hi = level
    bright = ImageChops.multiply(mask, before.point(lambda v: 255 if v >= hi else 0))
    dark = ImageChops.multiply(mask, before.point(lambda v: 255 if v <= lo else 0))
    if not ImageStat.Stat(bright).sum[0] or not ImageStat.Stat(dark).sum[0]:
        return 0.0, 0.0, n
    mean = lambda img, m: ImageStat.Stat(img, m).mean[0]  # noqa: E731
    base = mean(before, bright) - mean(before, dark)
    dim = mean(dimmed, bright) - mean(dimmed, dark)
    return base, dim, n


def swipe(page, point, touch, dy=SWIPE_DY):
    """⑱ 指 1 本（タッチの形）かホイール（マウスの形）で、point（CSS px）から下へ dy px スクロールする。
    CDP の合成ジェスチャ（Input.synthesizeScrollGesture）＝ブラウザの入力の経路をそのまま通る（scrollBy で代用しない）。
    慣性（fling）は切る＝止まった位置で測る"""
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("Input.synthesizeScrollGesture", {
            "x": point[0], "y": point[1], "yDistance": -dy, "speed": 2400, "preventFling": True,
            "gestureSourceType": "touch" if touch else "mouse"})
    finally:
        cdp.detach()
    page.wait_for_timeout(120)


def hold_room(page, still):
    """⑥ 撮る間だけ部屋を止める（still=True）／戻す（False）。止まった・戻ったかを返す（debug.roomMotion().still）"""
    page.emulate_media(reduced_motion="reduce" if still else "no-preference")
    ok = wait_until(page, lambda: page.evaluate(MOTION_STILL) == still, 3, step=30)
    if still:                                                # 止めた描き直し（change の中で同期に描く）を画面に載せてから撮る
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
    return ok


def console_where(m):
    """console のメッセージの出どころ（資源の読み込みの失敗なら、その URL の path）。⓪ で ⑧'' の 500 を見分けるため"""
    try:
        url = (m.location or {}).get("url") or ""
    except Exception:  # noqa: BLE001 — 出どころが取れなくても、メッセージそのものは残す
        url = ""
    return f" @ {urlparse(url).path}" if url else ""


def induced(err):
    """このテストがわざと返した失敗の資源の読み込みか: ⑧'' の答えの POST の 500 と、⑮ の /api/office の 503。
    ⓪ はこの 2 つだけを数えない（出どころの path と status の両方で見分ける＝ほかの 500／503 は数える）"""
    if "status of 500" in err:
        return any(err.endswith(f" @ {p}") for p in ANSWER_PATHS)
    return "status of 503" in err and err.endswith(" @ /api/office")


TERMINAL_PREF = "try { localStorage.setItem('aioffice.cockpit.center', 'terminal'); } catch (e) {}"


def open_page(browser, port, shape, query, handler):
    """形ごとに新しいコンテキストで開く。(ctx, page, errors)"""
    _, w, h, touch, dsf = shape[:5]
    errors = []
    ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=dsf,
                              is_mobile=touch, has_touch=touch)
    # R121: 1 ペインの中央の既定は作業ビュー。この門は本物の端末（xterm）の形と桁を測る＝「ターミナル派」の保存を入れて開く
    # （既定と［ターミナル］⇄［作業］の切替そのものは cockpit_term_smoke が見る）
    ctx.add_init_script(TERMINAL_PREF)
    ctx.route(lambda url: urlparse(url).path.startswith("/api/"), handler)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.error: {m.text}{console_where(m)}") if m.type == "error" else None)
    page.goto(f"http://127.0.0.1:{port}/?ui=cockpit{query}", wait_until="domcontentloaded")
    page.bring_to_front()
    page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
    page.wait_for_selector(".cproom", state="attached", timeout=20000)
    return ctx, page, errors


# R105-A: 実測で崩れた Mac の形と、2 カラムの両端を含む。
# setup 未完 + 留守中 + 本物の xterm（通信だけ偽装）を同時に出す。
R105_SHAPES = [(900, 768), (1024, 768), (1112, 834), (1181, 834), (1280, 800), (1366, 768),
               (1400, 900), (1401, 900), (1440, 900), (1512, 982), (1920, 1080)]
R105_LAYOUT = "() => {" + VIS_JS + """
  const panel = (sel) => {
    const e = document.querySelector(sel);
    if (!vis(e)) return { shown: false, who: sel };
    const r = e.getBoundingClientRect(), c = onScreen(clipRect(e));
    return { shown: true, who: sel, w: r.width, h: r.height, visW: c.right - c.left, visH: c.bottom - c.top,
      top: r.top, bottom: r.bottom, left: r.left, right: r.right };
  };
  const setup = document.querySelector('#setupbar'), digest = document.querySelector('#digest-strip');
  // R110-D1: 名簿の窓が低い 2 カラム（@container cproster）は列見出し（並べ替え）を畳んで行を見せる＝畳んだ列見出しは
  // 「潰れた」に数えず、代わりに名簿の最初の行が見えることを課す（3 カラムと 1 列では列見出しは畳まない＝見る）
  const cols = getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\\s+/).length;
  const hrow = document.querySelector('.cptable .pxhrow');
  const folded = cols === 2 && !!hrow && getComputedStyle(hrow).display === 'none';
  return { panels: [panel('#stage'), panel('#cp-t-roster'),
      folded ? { shown: true, who: '.cptable .pxhrow（畳んだ）', visW: 1, visH: 1, folded: true } : panel('.cptable .pxhrow'),
      panel('#agents .pxrow'), panel('#cp-term'), panel('#cp-xterm .xterm-screen')],
    setup: vis(setup), setupSlot: setup?.parentElement?.id || '',
    setupArea: setup?.parentElement ? getComputedStyle(setup.parentElement).gridArea : '',
    digest: vis(digest), digestSlot: digest?.parentElement?.id || '',
    digestFoot: !!digest?.closest('.cpfoot'),
    cols: getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\\s+/).length,
    dims: window.__office.debug.termDims?.() || null,
    xscroll: document.documentElement.scrollWidth > innerWidth + 1,
    vw: document.documentElement.clientWidth, vh: innerHeight };
}"""


def check_r105_layout(browser, port, world, check):
    for w, h in R105_SHAPES:
        fixture = json.loads(json.dumps(world))
        fixture["setup"] = {"hookInstalled": False, "eventsWired": False, "approvalWired": False}
        posts, seen = [], {}
        base = fake_api(json.dumps(fixture, ensure_ascii=False), STATUS_BOARD.read_text(encoding="utf-8"), posts, seen)
        digest = json.dumps({"available": True, "since": int(time.time()) - 1800, "totals": {"tasksDone": 2}})

        def handler(route):
            if urlparse(route.request.url).path == "/api/digest":
                return route.fulfill(status=200, content_type="application/json", body=digest)
            return base(route)

        shape = ("R105-A 未接続・留守中", w, h, False, 1)
        ctx, page, errors = open_page(browser, port, shape, "&life=0", handler)
        try:
            page.evaluate("w => window.__office.inject(w)", fixture)
            ready = wait_until(page, lambda: page.evaluate("""() => {
              const d = document.querySelector('#digest-strip');
              return !!d && !d.hidden && !!window.__office.debug.termDims?.();
            }"""), 6)
            page.wait_for_timeout(250)
            geo = page.evaluate(R105_LAYOUT)
            bad = [p for p in geo["panels"] if not p["shown"] or p.get("visW", 0) <= 0 or p.get("visH", 0) <= 0]
            want_cols = 2 if 900 <= w <= 1400 else 3
            check(ready and geo["setup"] and geo["digest"] and not bad and not geo["xscroll"]
                  and geo["setupSlot"] == "cp-setup-slot" and geo["setupArea"] != "auto"
                  and geo["digestSlot"] == "cp-login-slot" and geo["digestFoot"] and geo["cols"] == want_cols,
                  f"R105-A {w}×{h}: setup 全部 false + 留守中でも部屋・台帳の見出し・端末が画面内で潰れない（{want_cols} 列）",
                  json.dumps(geo, ensure_ascii=False))
            if 900 <= w <= 1400:
                dims = geo["dims"]
                resized = [p["body"] for p in posts if p["path"] == "/api/term/resize" and isinstance(p["body"], dict)]
                # R110: xterm は ⚙ 文字の大きさ（12/13/14/16・既定 14）か、80 桁が入らないときの max(11, それ − 2)
                check(dims is not None and dims["cols"] >= 80 and dims["rows"] > 0
                      and dims["fontSize"] in (11, 12, 13, 14, 16)
                      and any(b.get("cols") == dims["cols"] for b in resized),
                      f"R105-A {w}×{h}: 見せている実 xterm は 80 桁以上（fixture の cols を読まない・resize にも同じ値）",
                      json.dumps({"dims": dims, "resize": resized}, ensure_ascii=False))
                # R110 骨格 A: 2 カラムの名簿（02）は左の列（部屋＋受付）の下に常に見える（一覧タブ #cp-view-tabs は撤去）。
                # 名簿の窓が左の列の下端より下・左の列と同じ横の位置で、名簿の箱の見えている丈に行が 1 行以上入っている
                roster = page.evaluate("() => {" + VIS_JS + """
                  const left = document.querySelector('.cpleft'), win = document.querySelector('#cp-right');
                  const box = document.querySelector('#cp-roster');
                  if (!left || !win || !box) return { found: false };
                  const L = left.getBoundingClientRect(), W = win.getBoundingClientRect(), k = onScreen(clipRect(box));
                  const rows = [...box.querySelectorAll('#agents .pxrow')].filter(vis).filter((r) => {
                    const q = r.getBoundingClientRect(); return q.bottom > k.top + 1 && q.top < k.bottom - 1; });
                  return { found: true, shown: vis(win), below: W.top >= L.bottom - 0.5,
                    alignedLeft: Math.abs(W.left - L.left) <= 1, visH: R(k.bottom - k.top), rows: rows.length,
                    tabs: document.querySelectorAll('#cp-view-tabs').length,
                    left: [R(L.left), R(L.top), R(L.right), R(L.bottom)], win: [R(W.left), R(W.top), R(W.right), R(W.bottom)] };
                }""")
                check(roster.get("found") and roster["shown"] and roster["below"] and roster["alignedLeft"]
                      and roster["visH"] > 0 and roster["rows"] > 0 and roster["tabs"] == 0,
                      f"R110 {w}×{h}: 2 カラムの名簿は左の列の下に常に見え、行が {roster.get('rows', 0)} 行見える（一覧タブは無い）",
                      json.dumps(roster, ensure_ascii=False))
            check(not errors, f"R105-A {w}×{h}: console.error / pageerror = 0", "; ".join(errors[:3]))
        finally:
            ctx.close()


def check_gauges(page, tag, tap, gp, dsf, held, check):
    """⑥ の本体: 閉じて基準を測り、開く前に 2 回撮り、要約を押して開き、スクロール 0 のまま撮ってから測る（寄せない）"""
    page.evaluate("() => window.scrollTo(0, 0)")
    base = page.evaluate(GAUGE_HITS, GRID)                   # 閉じている間（基準・スクロール 0）
    b1 = page.screenshot()
    page.wait_for_timeout(250)
    b2 = page.screenshot()
    tap(gp["x"], gp["y"])
    wait_until(page, lambda: page.evaluate(
        "() => !!document.querySelector('.usage')?.classList.contains('open')"), 2)
    page.wait_for_timeout(300)                               # 開く遷移（120ms）を待ってから当てる
    page.evaluate("() => window.scrollTo(0, 0)")             # 開くときにページが動いても、スクロール 0 で見る（裁定）
    after = page.screenshot()
    gh = page.evaluate(GAUGE_HITS, GRID)
    changed, compared, moved = changed_pixels((b1, b2, after), gh.get("rowBox", [0, 0, 0, 0]), dsf)
    ratio = changed / compared if compared else 0.0
    why = []
    if not held:
        why.append("空振り: 撮る間に部屋を止められない（reduced_motion を emulate しても debug.roomMotion().still が"
                   " true にならない）＝部屋のロボの動きが画素の差に混ざる")
    if base["open"] or base["hit"]:
        why.append(f"閉じている間も詳細が当たる（命中 {base['hit']}/{base['total']}・open={base['open']}）"
                   "＝開く操作の効果を測れない → 閉じている間は pointer-events も切る")
    if not gh["open"]:
        why.append("要約を押しても開かない（.usage に open が付かない）")
    elif not gh["exists"]:
        why.append("開いたが詳細（#gauges）が無い")
    else:
        vw, vh = gh["vw"], gh["vh"]
        L, Rt, Tp, Bt = gh["left"], gh["right"], gh["top"], gh["bottom"]
        span = f"x {round(L)}〜{round(Rt)}・y {round(Tp)}〜{round(Bt)}・画面 {vw}×{vh}・スクロール 0"
        if gh["opacity"] < OPACITY_MIN or gh["visibility"] != "visible":
            why.append(f"opacity の積 {gh['opacity']:.2f}・visibility {gh['visibility']}（{OPACITY_MIN} 以上・visible が要る）")
        # 箱: 開いた直後（スクロール 0・寄せない）の元の矩形が画面に収まり、祖先に切られていない（縦も横も同じ物差し）
        n_why = len(why)
        if L < -EDGE_TOL:
            why.append(f"詳細が画面の左外へ {round(-L)}px 出ている（{span}）→ right 基準の置き方を画面基準にするか、"
                       "幅を .usage の右端から画面の左端までに収める")
        if Rt > vw + EDGE_TOL:
            why.append(f"詳細が画面の右外へ {round(Rt - vw)}px 出ている（{span}）→ 幅か置き場所を画面に収める")
        if Bt - Tp > vh + EDGE_TOL:
            why.append(f"詳細の丈 {round(Bt - Tp)}px が画面の丈 {vh} に収まらない（max-height {gh['maxH']}）"
                       " → 画面基準の max-height で中をスクロールさせる")
        else:
            if Tp < -EDGE_TOL:
                why.append(f"開いた直後、詳細の上 {round(-Tp)}px が画面の上の外（{span}）")
            if Bt > vh + EDGE_TOL:
                why.append(f"開いた直後、詳細の下 {round(Bt - vh)}px が画面の下の外（{span}）＝人が寄せないと見えない"
                           " → 置き場所（top）と max-height を画面の丈から決める")
        if abs(gh["visL"] - L) > CLIP_TOL or abs(gh["visR"] - Rt) > CLIP_TOL \
                or gh["visT"] > Tp + CLIP_TOL or gh["visB"] < Bt - CLIP_TOL:
            why.append(f"祖先 {gh['by'] or '?'} に切られている（見えているのは x {round(gh['visL'])}〜{round(gh['visR'])}"
                       f"・y {round(gh['visT'])}〜{round(gh['visB'])}・元 {span}）"
                       + (f"→ 詳細を .usage（overflow-x {gh['usageOx']}）の overflow の外へ出す"
                          if gh["usageOx"] in ("auto", "scroll", "hidden", "clip") else "→ 切っている祖先の外へ出す"))
        boxed = len(why) > n_why
        # 見えている丈: 中身が入る丈か（細い帯に潰れた箱は、格子も比も満たしてしまう）
        need_h = min(gh["sh"], vh / 3)
        if gh["visH"] < need_h - EDGE_TOL:
            why.append(f"開いた詳細の見えている丈 {gh['visH']}px に中身が入らない（scrollHeight {gh['sh']}・"
                       f"要 {round(need_h)}px＝min(scrollHeight, 画面の丈 {vh} の 1/3)・max-height {gh['maxH']}）"
                       " → max-height が包含ブロックの % になっていないか（画面の丈を基準に）")
        # 命中: 元の矩形全体へ 5×5（スクロール 0 のまま）
        if gh["hit"] < GRID_MIN:
            why.append(f"開いた詳細の格子の命中 {gh['hit']}/{gh['total']}（{GRID_MIN} 以上が要る・#gauges の矩形 {gh['rect']}）"
                       + ("＝画面の外か祖先に切られた分の点が外れる（上の赤を直せば直る）" if boxed else
                          "→ 重なり（z-index）・clip-path・pointer-events を疑う"))
        # 中身: 最初のゲージ行（見出し・プロバイダの行・棒）が切られず・画面内で・当たり・見える
        for c in gh["content"]:
            if not c["found"]:
                why.append(f"中身の{c['name']}（{c['sel']}）が詳細の中に無い（箱だけ描かれている）")
                continue
            bad = []
            if c["cut"]:
                bad.append(f"祖先 {c['by'] or '?'} に切られている（見えている丈 {c['visH']}px・元 {c['rect']}）")
            if not c["inView"]:
                bad.append(f"画面の外（{c['rect']}）")
            if c["visibility"] != "visible" or c["opacity"] < OPACITY_MIN:
                bad.append(f"visibility {c['visibility']}・opacity の積 {c['opacity']:.2f}")
            if not c["hit"]:
                bad.append(f"中心に {c['over'] or '何も無い'} が当たる")
            if bad:
                why.append(f"中身の{c['name']}（{c['who']}「{c['text']}」）が見えない: " + "・".join(bad))
        # 画素: 中身の行の矩形の中で、止まっている画素のうち開いて変わった割合（箱の背景の寄与を外す）
        if ratio < PIX_RATIO_MIN:
            why.append(f"開く前と後で、中身の行の矩形 {[round(v) for v in gh['rowBox']]}（スクロール 0）の止まっている画素 "
                       f"{compared} のうち変わったのは {changed}（{ratio:.1%}・{PIX_RATIO_MIN:.0%} 以上が要る）"
                       + ("＝中身の行が画面に無い" if not compared else "＝中身の文字と棒が描かれていない（透明）"))
    ok = not why
    shown = (f"x {round(gh['left'])}〜{round(gh['right'])}・y {round(gh['top'])}〜{round(gh['bottom'])}/{gh['vw']}×{gh['vh']}・"
             f"見えている丈 {gh['visH']}/{gh['sh']}・" if gh.get("exists") and gh.get("open") else "")
    check(ok, f"⑥ {tag}: ヘッダーのゲージの詳細を開くと、スクロール 0 で画面内に中身まで見える（閉じて命中 {base['hit']}→"
          f"開いて {gh['hit']}/{gh['total']}・{shown}opacity {gh.get('opacity', 0):.2f}・"
          f"中身の行の変わった画素 {ratio:.1%}＝{changed}/{compared}・開く前に動いた {moved}）",
          " / ".join(why))
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)


def check_settings_resources(page, tag, check):
    """R105-A: 以前のヘッダー要約に代わり、設定の診断群からリソースを開く。"""
    page.click("#btn-settings")
    page.wait_for_selector('#modalwrap:not([hidden])')
    link = page.locator("#modal .cp-settings-link").filter(has_text=re.compile("リソースを見る|View resources"))
    groups = page.locator("#modal .cp-settings-group").count()
    # R128 #4: 群は 6 つ（表示・通知・スマホ・Bot・診断・このアプリ）
    check(groups == 6 and link.count() == 1,
          f"R105-A {tag}: 設定の 6 群にリソースの入口が 1 個", f"群 {groups}・リソース入口 {link.count()}")
    if link.count() != 1:
        page.keyboard.press("Escape")
        return
    link.click()
    ready = wait_until(page, lambda: page.locator("#modal .mres").count() > 0, 4)
    geo = page.evaluate("() => {" + VIS_JS + """
      const modal = document.querySelector('#modal'), row = modal?.querySelector('.mres');
      const r = modal?.getBoundingClientRect(), c = row && vis(row) ? onScreen(clipRect(row)) : null;
      return { shown: vis(modal), inView: !!r && r.left >= -0.5 && r.right <= innerWidth + 0.5
        && r.top >= -0.5 && r.bottom <= innerHeight + 0.5,
        row: !!c && c.right > c.left && c.bottom > c.top,
        title: modal?.querySelector('.mtitle')?.textContent || '' };
    }""")
    check(ready and geo["shown"] and geo["inView"] and geo["row"] and geo["title"].strip(),
          f"R105-A {tag}: 設定から開くリソースは画面内で中身が見える", json.dumps(geo, ensure_ascii=False))
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)


def run_shape(browser, port, shape, world, check, after_load=None):
    """live の 1 形状ぶん。after_load は実験用の差し込み口（門では使わない＝None）"""
    name, w, h, touch, dsf, want_k = shape
    tag = f"{name} {w}×{h}"
    tap = None
    posts, seen, fail = [], {}, {"on": False}
    office = {"body": json.dumps(world, ensure_ascii=False)}   # /api/office が返す world（⑩' で差し替える）
    ctx, page, errors = open_page(browser, port, shape, "",
                                  fake_api(office, STATUS_BOARD.read_text(encoding="utf-8"), posts, seen, fail))
    try:
        tap = page.touchscreen.tap if touch else page.mouse.click
        page.evaluate("w => window.__office.inject(w)", world)
        page.wait_for_function("() => window.__office.stats().frames > 0", timeout=10000)
        # トレイ・ゲージ・端末の一覧が揃うまで（揃わないこと自体は下の検査が赤にする＝ここでは待つだけ）
        wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#attn .trayhead')"), 8)
        wait_until(page, lambda: page.evaluate("() => document.querySelectorAll('#usage-summary .gpin').length > 0"), 8)
        wait_until(page, lambda: seen.get("/api/term/list", 0) >= 1, 6)
        wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#cp-xterm .xterm-screen')"), 3)
        page.evaluate("() => document.fonts.ready")
        page.wait_for_timeout(400)
        if after_load:
            after_load(page)
        coarse = page.evaluate("() => matchMedia('(pointer: coarse)').matches")
        print(f"  ─ {tag}（{'タッチ' if touch else 'マウス'}・dsf {dsf}・pointer:coarse={coarse}）")
        ua_load = page.evaluate(UA_BORDERS, ".ui-cockpit")      # ⑯ の材料（読み込み後・操作の前に見えている button）

        # ① スクロール 0 のトレイ
        f = page.evaluate(TRAY_FIRST)
        if not f["exists"] or not f["shown"]:
            check(False, f"① {tag}: ❗トレイが最初の画面内", "トレイ（#attn）が出ていない（固定ワールドには ❗ が 2 人居る）")
        else:
            # R110-D2: スクロール 0 で最初の答え（1 番）が丸ごと見えて当たる。残りのボタンは、ページを動かさずにトレイの中の
            # スクロールで届けば足りる（以前は全部をスクロール 0 で課した＝2 カラムの受付は名簿の 2 行を残すと 1112×834 の
            # 指の 44px のボタン 5 個が入らない）。届いた後も祖先に切られず・中心が当たる
            first = next((a for a in f["answers"] if a["answer"]), None)
            out = [a for a in f["inner"] if not a["inView"]]
            cut = [a for a in f["inner"] if a["inView"] and a["cut"]]
            miss = [a for a in f["inner"] if a["inView"] and not a["cut"] and not a["hit"]]
            first_bad = first and not (first["inView"] and not first["cut"] and first["hit"])
            has_answer = first is not None
            ok = f["top"] >= 0 and has_answer and not first_bad and not out and not cut and not miss and not f["pageMoved"]
            why = []
            if f["top"] >= f["vh"] and f["answers"]:
                need = max(a["bottom"] for a in f["answers"]) - f["vh"]
                why.append(f"トレイの上端が y={f['top']}（画面の丈 {f['vh']}）＝ボタンが全部見えるまで {need}px の"
                           f"スクロールが要る → 1 列の形では #attn をヘッダーの直後（部屋より前）へ出す")
            elif out:
                why.append("ボタンが画面の外: " + " / ".join(
                    f"「{a['label']}」x{a['left']}〜{a['right']}・y{a['top']}〜{a['bottom']}" for a in out)
                    + f"（画面 {f['vw']}×{f['vh']}）→ トレイを最初の画面に収める（右が切れるなら列の幅を直す＝P4-1）")
            if first_bad:
                why.append(f"スクロール 0 で最初の答え「{first['label']}」が丸ごと見えない（{first['by'] or '画面'} に切られて "
                           f"{first['visW']}×{first['visH']}・元 {first['w']}×{first['h']}・当たり {first['hit']}）"
                           " → 受付の丈とトレイの見出し・本文の丈を直す")
            if cut:
                why.append("トレイの中のスクロールで寄せても祖先に一部を切られている: " + " / ".join(
                    f"「{a['label']}」が {a['by']} に切られて見えているのは {a['visW']}×{a['visH']}（元 {a['w']}×{a['h']}）"
                    for a in cut) + " → トレイの丈・列の幅を直す（overflow:hidden の箱に隠さない）")
            if f["pageMoved"]:
                why.append(f"寄せるときにページが動いた（scrollY {f['pageMoved']}）")
            if miss:
                why.append("ボタンの中心に別の要素が重なる: " + " / ".join(f"「{a['label']}」に {a['over']}" for a in miss))
            if not has_answer:
                why.append(f"答えのボタンが無い（トレイの見出し「{f['head']}」）")
            check(ok, f"① {tag}: ❗トレイが最初の画面内（スクロール 0 で最初の答えが見えて押せ、ボタン {len(f['inner'])} 個が"
                  "トレイの中のスクロールで全部届いて押せる）",
                  " / ".join(why))

        # ③④ 部屋（操作の前・スクロール 0 で測る）
        r = page.evaluate(ROOM)
        if not r["exists"]:
            check(False, f"③ {tag}: 部屋の中に横スクロールが無い", "部屋の canvas（.cproom）が無い")
            check(False, f"④ {tag}: 部屋の canvas が ×{want_k}", "部屋の canvas（.cproom）が無い")
        else:
            cutx = r["left"] < 0 or r["right"] > r["vw"]
            ok = not r["scrollers"] and not r["clips"] and not cutx
            why = []
            for s in r["scrollers"]:
                why.append(f"{s['who']} の中で横スクロール（{s['sw']} > {s['cw']}）")
            for c in r["clips"]:
                why.append(f"{c['who']}（x {c['left']}〜{c['right']}）が canvas を切っている")
            if cutx:
                why.append(f"canvas（x {r['left']}〜{r['right']}）が画面の幅 {r['vw']} をはみ出す"
                           f"（{max(r['right'] - r['vw'], -r['left'])}px が隠れる）")
            check(ok, f"③ {tag}: 部屋の中に横スクロールが無い（部屋全体が見える）",
                  " / ".join(why) + f" → 倍率を形で選ぶ（roomScale(availW, availH)・×{K_MIN}〜×{K_MAX}の整数）")
            k = r["cssW"] / ROOM_W
            kk = round(k)
            integral = abs(k - kk) < 0.01 and abs(r["cssH"] - ROOM_H * kk) < 0.01 and K_MIN <= kk <= K_MAX
            backing = (r["cssW"] > 0 and r["attrW"] % round(r["cssW"]) == 0 and r["cssH"] > 0
                       and r["attrH"] % round(r["cssH"]) == 0 and r["attrW"] // round(r["cssW"]) == r["attrH"] // round(r["cssH"]))
            ok = integral and backing and r["rendering"] == "pixelated" and kk == want_k
            why = f"CSS {r['cssW']:.1f}×{r['cssH']:.1f}・画素 {r['attrW']}×{r['attrH']}・{r['rendering']}"
            if integral and kk != want_k:
                why += f"・倍率が ×{kk}（この形の期待は ×{want_k}）"
            check(ok, f"④ {tag}: 部屋の canvas が ×{want_k}（{ROOM_W * want_k}×{ROOM_H * want_k}・今 k={k:.2f}）",
                  why + " → 割合で縮めず、形で選んだ整数倍（roomScale）で描く")
            # ④' 部屋の箱の丈: 1 列（ページがスクロールする段）では canvas の丈と同じ（×2 に ×3 の 480 を課すと下に 160px の
            # 空きが出た）。3 カラムは低い画面で箱が canvas より低くなってよい（中でスクロール）が、canvas より高くはしない
            one = r["cols"] == 1
            gap = r["stageH"] - r["cssH"]
            ok = abs(gap) <= 1 if one else gap <= 1
            cols = "1 列" if one else f"{r['cols']} 列"
            check(ok, f"④' {tag}: 部屋の箱（.cpstage）の丈が canvas の丈{'と同じ' if one else '以下'}"
                  f"（{cols}・箱 {r['stageH']:.0f}・canvas {r['cssH']:.0f}）",
                  (f"部屋の下に {gap:.0f}px の空き" if gap > 0 else f"箱が canvas より {-gap:.0f}px 低い（1 列で中スクロール）")
                  + f"（data-room-scale={r['stageScale'] or '無し'}）→ 1 列の段の .cpstage の min-height を倍率ごとに合わせる")

        # ③' ページの横スクロール（部屋の外。直し方を探すため、はみ出している要素を名指しする）
        px = page.evaluate(PAGE_X)
        check(not px["pages"], f"③' {tag}: ページに横スクロールが無い",
              " / ".join(f"{p['who']} の横幅 {p['sw']} > 画面 {px['vw']}" for p in px["pages"])
              + ("・はみ出している要素: " + " / ".join(f"{x['who']}（x {x['left']}〜{x['right']}）" for x in px["leak"])
                 if px["leak"] else "") + " → 閉じた詳細やパネルが画面の外へ漏れないように（容れ物で切るか、閉じている間は出さない）")

        # ⑪ ヘッダー（管理のボタンが切れない・ゲージのピンが重ならない・名前が潰れない）
        hd = page.evaluate(HEADER, LAB_MIN)
        why = []
        if not hd["exists"]:
            why.append("ヘッダー（.cphead）が無い")
        else:
            # R105: ［＋ 新しいセッション］［スマホ］⚙ の 3 つ＋あなた待ちが居るときだけ「あなた待ち N」（この世界は ❗ 有り）
            want_btns = 3 + (1 if page.evaluate("() => { const c = document.querySelector('#cp-attn-chip'); return !!c && !c.hidden; }") else 0)
            if hd["btns"] != want_btns or hd["pins"] != 0:
                why.append(f"新仕様とのずれ: ヘッダーのボタン {hd['btns']}（期待{want_btns}）・ゲージのピン {hd['pins']}（期待0・設定内）")
            if hd["bad"]:
                why.append("ボタンが切れている: " + " / ".join(hd["bad"][:4]) + " → 管理のボタンを段に収める（横スクロールの先に隠さない）")
            if hd["lap"]:
                why.append("ピンが重なる: " + " / ".join(hd["lap"][:3]))
            if hd["spill"] or hd["pinCut"]:
                why.append("ピンがはみ出す: " + " / ".join((hd["spill"] + hd["pinCut"])[:3]))
            if hd["thin"]:
                why.append(f"ピンの名前が {LAB_MIN}px 未満に潰れている: " + " / ".join(hd["thin"][:3]))
        check(not why, f"⑪ {tag}: ヘッダーのボタン {hd.get('btns', 0)} 個が切れず、ゲージのピン {hd.get('pins', 0)} 個が重ならず"
              f"名前が潰れない（丈 {hd.get('h', 0)}px）", " / ".join(why) + " → 1 列の段とスマホの段（@container cphead）のヘッダーの格子")

        # ⑫ 色付きの左右の帯を立てない（選んだカードは背景の明度差と中立の罫で示す＝デザイン方針）
        sb = page.evaluate(SIDE_BANDS)
        why = []
        if not sb["rows"] or len(sb["sessions"]) != 1:
            why.append(f"空振り: 選んだ名簿の行（.pxrow.sel）が {sb['rows']} 行・指すセッション {sb['sessions']}（1 人のはず）")
        if sb["found"]:
            why.append(" / ".join(sb["found"][:4]))
        check(not why, f"⑫ {tag}: 選んだ名簿の行（{sb['rows']} 行・1 人）と ❗・引用の箱に色付きの左右の帯が無い"
              f"（{sb['looked']} 個を計算値で）",
              " / ".join(why) + " → 選択は背景の明度差と 1px の中立の罫（inset 0 0 0 1px）で示す")

        # ⑬ 管理のモーダル: スクロール 0 で ⚙（設定）を押す → #modal が今のスクロール位置で中身まで画面に出る。閉じて元へ戻す
        ua_modal = check_modal(page, tag, tap, touch, dsf, check)
        # ㉗ ゲージの詳細を開いたまま ? でモーダルを開いても、詳細がモーダルの上に出ない
        check_gauges_under_modal(page, tag, check)
        # ⑯ ブラウザ既定の枠（読み込み後のページと、⑬ のモーダルの中の見えている button）
        check_ua(check, tag, ua_load, ua_modal)

        # ⑤ 台帳の行の丈（幅 600 以下の形では、畳んだ台帳の中身も）
        narrow = w <= NARROW_MAX
        g = page.evaluate(LEDGER, [narrow, list(LEDGER_CELLS), list(LEDGER_SECOND), DETAIL_MIN, DETAIL_FRAC, READ_MIN])
        ok = g["rows"] > 0 and g["max"] <= ROW_MAX and not g["nthin"]
        why = []
        if not g["rows"]:
            why.append("台帳の行が 1 行も見えない")
        else:
            if g["max"] > ROW_MAX:
                why.append(f"最も高い行 {g['max']}px（「{g['worst']['text']}」・列の幅 {' '.join(g['worst']['cells'])}）"
                           " → 名簿の行は 2 段（上段＝記号・名前・状態／下段＝いま・証拠・経過）のまま、列が潰れて折り返しが"
                           "増えていないか（状態の列に長い札が入って名前・いまの列を押し潰していないか）を見る")
            if g["nthin"]:
                t = g["thin"]
                why.append(f"{g['nthin']} 行で「いま」（.c-detail）の見えている幅が下限未満: 「{t['name']}」の「いま」が {t['w']}px"
                           f"（要 {t['need']}px＝max({DETAIL_MIN}, 行の中身の幅 {t['inner']} × {DETAIL_FRAC:.2f})・display {t['disp']}・"
                           f"行の丈 {t['h']}px・「{t['text']}」）＝2 行で切られて質問が数字しか読めない（丈は伸びない）"
                           " → 台帳の列の比（grid-template-columns）で「いま」に幅を渡す")
        check(ok, f"⑤ {tag}: 台帳の行の丈が全部 {ROW_MAX}px 以下で、「いま」が読める幅（{g['rows']} 行・最大 {g['max']}px・"
              f"「いま」の見えている幅の最小 {g.get('detailMin', 0)}px ≥ {g.get('detailNeed', 0)}px）", " / ".join(why))
        if narrow:
            why = []
            if not g["whole"]:
                why.append("空振り: 祖先に切られずに見えている行が 1 行も無い")
            if g["nbad"]:
                why.append(f"{g['nbad']} 件: " + " / ".join(g["bad"]))
            if len(g["sorts"]) != SORT_BUTTONS:
                why.append(f"見出しの並べ替えのボタンが {len(g['sorts'])} 個見える（{SORT_BUTTONS} 個が要る・{g['sorts']}）")
            check(not why, f"⑤' {tag}: 畳んだ台帳でも中身を消さない（{g['whole']} 行で 記号・プロジェクト・状態・いま・証拠・経過が"
                  f"読める幅（min(中身, {READ_MIN}px) 以上）で見えて空でない・いま／経過／証拠は 2 段目・並べ替え {len(g['sorts'])} 個）",
                  " / ".join(why) + " → @container cpledger の段で display:none にせず、grid-area で 2 段目へ置く"
                  "（列の幅は読める幅を残す）")

        # ⑭ 段の一致（ヘッダー・台帳・画面の幅 ≤600 の 3 つが揃う＝スマホの段の境は画面 600 の 1 本）
        check_stages(page, tag, check)

        # ⑨ 端末（live＝Mac の前）: 形によらず出す（⑦ で選択が変わる前に測る＝偽の端末は sess-cp-09 に紐づく）
        t = page.evaluate(TERM_LIVE)
        why = []
        if seen.get("/api/term/list", 0) < 1:
            why.append("端末の一覧（/api/term/list）を取りに来ない（端末を無効にしている？）")
        if t["selected"] != TERM_SESSION:
            why.append(f"空振り: 選択が {t['selected']}（偽の端末は {TERM_SESSION} に紐づく）")
        if not t["box"]:
            why.append(f"#cp-xterm が見えない（丈 {t['boxH']}px・切っている祖先 {t['boxBy'] or '無し'}）")
        if not t["screen"]:
            why.append(f"xterm の中身（.xterm-screen／.xterm-rows）が見えない（{t['screens']} 個・どれも寸法 0 か畳まれている）")
        elif t["visW"] < TERM_MIN or t["visH"] < TERM_MIN:
            why.append(f"見えている xterm が {t['visW']}×{t['visH']}（元 {t['w']}×{t['h']}・切っている祖先 {t['by'] or '無し'}）"
                       f"＝{TERM_MIN}px 未満")
        elif t["outside"]:
            why.append(f"xterm が端末の面（#cp-term・y {t['pane'][0]}〜{t['pane'][1]}）の外へはみ出して描かれている"
                       f"（xterm y {t['scrY'][0]}〜{t['scrY'][1]}）＝面を畳んだまま中身だけ描いている")
        elif not t["hit"]:
            why.append(f"xterm の中心に {t['over'] or '何か'} が重なる（外へはみ出して描かれているだけ）")
        if not t["tabs"]:
            why.append("タブ列（#cp-tabs）が見えない")
        if not t["plus"]:
            why.append("＋ 端末（#cp-term-new）が見えない")
        if is_demo_reason(t["note"], "ja") or is_demo_reason(t["note"], "en"):
            why.append(f"足元がデモの理由を書いている（「{t['note']}」）")
        check(not why, f"⑨ {tag}: live（Mac の前）では端末を出す（xterm が {TERM_MIN}px 以上見えて当たる・タブ列と ＋ も出る）",
              " / ".join(why) + " → 端末を出すかの軸は「デモかどうか」だけ（幅・タッチ・pointer で畳まない）")

        # ② トレイのボタンの寸法と当たり（画面に入れてから・見えている寸法）
        btns = page.evaluate(TRAY_BUTTONS)
        small = [b for b in btns if touch and (b["w"] < TOUCH_MIN or b["h"] < TOUCH_MIN)]
        miss = [b for b in btns if not b["hit"]]
        ok = bool(btns) and not small and not miss
        why = []
        if not btns:
            why.append("トレイにボタンが無い")
        if small:
            why.append(f"{TOUCH_MIN}×{TOUCH_MIN} 未満: " + " / ".join(
                f"「{b['label']}」{b['w']}×{b['h']}"
                + (f"（元 {b['fullW']}×{b['fullH']}・{b['by']} に切られている）" if (b["w"], b["h"]) != (b["fullW"], b["fullH"]) else "")
                for b in small) + " → (pointer: coarse) のとき #attn のボタンを 44px 以上に")
        if miss:
            why.append("中心が当たらない: " + " / ".join(
                f"「{b['label']}」" + (f"中心 ({b['cx']}, {b['cy']}) に {b['over']}" if b["over"] else
                                     "画面に入らない" if not b["seen"] else f"中心 ({b['cx']}, {b['cy']})")
                for b in miss) + " → 受付の窓（#win-reception）の切れ（列の指定・トレイの grid・P4-1）か重なりを直す")
        check(ok, f"② {tag}: トレイのボタン {len(btns)} 個が"
              + (f" {TOUCH_MIN}×{TOUCH_MIN} 以上見えて" if touch else "") + "中心が当たる", " / ".join(why))

        # ⑩ トレイの「自由に書く」→ 指示欄（#sheet）がスクロール 0 で画面内に出て、部屋の箱が潰れない。閉じて元へ戻す
        #    （1 列では hud の grid-row:2 の則でトレイの直下に出る。3 カラムの 1181〜1279 では同じ則が .main の 2 行目へ
        #    入れて部屋を潰した＝2026-09-25 の検収）
        st0 = page.evaluate("() => Math.round(document.querySelector('.cpstage')?.getBoundingClientRect().height || 0)")
        sheet_ref = None                                     # ⑩ で開いた指示欄の矩形（⑩' が ❗ の有無で比べる）
        fp = page.evaluate(FREE_POINT)
        label = f"⑩ {tag}: トレイの「自由に書く」→ 指示欄がスクロール 0 で画面内に出て、部屋の箱の丈が変わらない"
        if not fp["ok"]:
            check(False, label, fp["why"])
        else:
            tap(fp["x"], fp["y"])
            wait_until(page, lambda: page.evaluate("() => !document.querySelector('#sheet')?.hidden"), 2)
            page.wait_for_timeout(250)
            wait_until(page, lambda: page.evaluate(SHEET_SETTLED), 1.5)   # 横に滑り込む遷移が終わってから（⑩' と矩形を比べる）
            sh = page.evaluate(SHEET_STATE)
            if sh["open"]:
                sheet_ref = {k: sh[k] for k in ("top", "bottom", "left", "right")}
            why = []
            if not sh["open"]:
                why.append("指示欄（#sheet）が開かない")
            else:
                if sh["top"] < 0 or sh["top"] > sh["vh"] - SHEET_MIN:
                    why.append(f"スクロール 0 で指示欄の上端が y {sh['top']}（画面の丈 {sh['vh']}・{SHEET_MIN}px 以上見えていてほしい）"
                               f"・開いた後のスクロール {sh['scrollAfter']}")
                if sh["left"] < -EDGE_TOL or sh["right"] > sh["vw"] + EDGE_TOL:
                    why.append(f"指示欄が画面の横の外（x {sh['left']}〜{sh['right']}・画面の幅 {sh['vw']}）")
                if sh["stageH"] < st0 - 1:
                    why.append(f"指示欄を開くと部屋の箱が {st0}→{sh['stageH']}px に潰れる（#sheet は position {sh['pos']}・"
                               f"grid-row {sh['row']}＝hud の @media (max-width: 1279px) の .sheet-open .sheet が .main の 2 行目へ入れる）"
                               " → ui/cockpit/style.css の 3 カラムの段（1 列の段の補集合）で .ui-cockpit .sheet-open .sheet を"
                               " 1280 以上と同じ absolute（top/right/bottom 16px・grid-row auto・margin 0）に戻す")
                elif sh["stageH"] > st0 + 1:
                    why.append(f"指示欄を開くと部屋の箱が {st0}→{sh['stageH']}px に伸びて、部屋の下に {sh['stageH'] - st0}px の空き"
                               "（hud の @media (max-width: 820px) の .sheet-open #stage { height: 75vh } が部屋の箱＝#stage に効く）"
                               " → ui/cockpit/style.css の 1 列の段で .ui-cockpit .sheet-open #stage { height: auto }")
            check(not why, label + (f"（上端 y {sh['top']}・部屋の箱 {st0}→{sh['stageH']}px）" if sh["open"] else ""),
                  " / ".join(why))
            # ㉑ 指示欄を開いている間も ❗トレイのボタンが全部、画面内で押せる（指示欄がトレイを覆わない）。3 カラムでは
            #    指示欄が .main の右に重なって右カラムのトレイを覆い、答えるには指示欄を閉じる要があった（ROADMAP のバックログ）。
            #    R110 骨格 A: トレイは左の列の 04 受付・指示欄は端末の下（3 カラム＝static の升目・2 カラム＝端末の升目の下端に
            #    absolute・1 列＝受付の直後）＝どの段でも重ならず押せる（2 カラムの一覧タブの特例は撤去）
            tw = page.evaluate(TRAY_WITH_SHEET)
            why = []
            if not tw["ok"]:
                why.append("空振り: " + tw["why"])
            else:
                if not tw["btns"]:
                    why.append("空振り: トレイにボタンが無い")
                if tw["lap"]:
                    why.append(f"指示欄（{tw['sheet']}・position {tw['pos']}）がトレイ（{tw['tray']}）に重なる")
                bad = [b for b in tw["btns"] if not (b["inView"] and b["hit"])]
                if bad:
                    why.append("押せないボタン: " + " / ".join(
                        f"「{b['label']}」{b['rect']}" + (f" に {b['over']}" if b["over"] else "・画面の外" if not b["inView"] else "")
                        for b in bad[:4]))
                if tw["lap"] or bad:
                    why.append("→ ui/cockpit/style.css で指示欄（#sheet）を端末の下（3 カラム＝grid-area: sheet・2 カラム＝端末の升目の"
                               "下端）に置く＝受付の窓（左の列）に掛けない")
            check(not why, f"㉑ {tag}: 指示欄を開いている間も、受付のトレイに重ならず全ボタンが押せる"
                  + (f"（{tw['cols']} 列・指示欄 {tw['sheet']}・トレイ {tw['tray']}）"
                                                    if tw["ok"] and not why else ""), " / ".join(why))
            # ㉕ 狭い 3 カラムで指示欄が部屋を覆う幅・㉖ ⤢（広げる）の置き方（開いたままの指示欄で・3 カラムは窓の幅も変えて）
            check_sheet_modes(page, tag, tap, shape, check)
            page.keyboard.press("Escape")
            closed = wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#sheet')?.hidden"), 1.5)
            if not closed:
                page.evaluate("() => document.querySelector('#sheetclose')?.click()")
                closed = wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#sheet')?.hidden"), 1.5)
            if not closed:
                check(False, f"⑩ {tag}: 指示欄を閉じる", "Escape でも ✕ でも閉じない（後の ⑥〜⑧ は開いたまま測る）")
            page.evaluate("() => window.scrollTo(0, 0)")

        # ⑥ ゲージの詳細（要約をタップ → 画面内に見える）。撮る間だけ部屋を止める（止めないと 1 列の形では
        # 開いた詳細が部屋に重なり、ロボの動きが画素の差に混ざる）。止める口の切り替えで #cp-motion の文言が替わる
        # ＝ヘッダーの並びが動きうるので、要約の点は止めた後に測る
        held = hold_room(page, True)
        try:
            check_settings_resources(page, tag, check)
        finally:
            resumed = hold_room(page, False)
        # 止めた部屋が動きに戻らないまま進むと、後の ⑦（指）・⑦・⑧ は止まった部屋で測ったことになる（黙って進ませない）
        check(resumed, f"⑥' {tag}: 撮り終えて reduced_motion を戻すと、部屋が動きに戻る（debug.roomMotion().still が false）",
              "空振り: 止めた部屋が動きに戻らない（後の ⑦⑧ は止まった部屋で測った）"
              " → index.js の reduced_motion の change の購読が、戻す向きでも部屋を動かすか")

        # ⑦（指）❗ でない止まっているロボの、描いた矩形の外を叩く → その人が選ばれる（最寄りの当たり）
        plan = page.evaluate(FINGER_PLAN, [FINGER_OUT, FINGER_CLEAR])
        label = f"⑦（指） {tag}: ロボの描いた矩形の {FINGER_OUT}px 外を叩いても、その人が選ばれる（最寄りの当たり・半径 22px）"
        if not plan["ok"]:
            check(False, label, plan["why"])
        else:
            picked, at, detail = False, None, ""
            for attempt in range(2):                          # 叩く前に歩き出したら（位置が動いたら）1 回だけ測り直す
                at = page.evaluate(FINGER_AT, [plan["lx"], plan["ly"]])
                if not at["ok"]:
                    detail = at["why"]
                    break
                tap(at["x"], at["y"])
                picked = wait_until(page, lambda: page.evaluate(
                    "() => window.__office.debug.selected()") == plan["session"], 2)
                if picked:
                    break
                moved = page.evaluate("""([sid, x, y]) => { const a = window.__office.debug.roomActors()
                  .find((q) => q.session === sid); return !a || a.x !== x || a.y !== y; }""",
                                      [plan["session"], plan["ax"], plan["ay"]])
                if not moved or attempt:
                    now = page.evaluate("() => window.__office.debug.selected()")
                    detail = (f"選択は {now}（期待 {plan['session']}・{plan['name']}）"
                              + ("・叩く間にその人が動いた" if moved else "")
                              + " → 当たりを描いた矩形だけに戻していないか（room.pick → pickNearest・PICK_RADIUS_PX÷倍率）")
                    break
                plan = page.evaluate(FINGER_PLAN, [FINGER_OUT, FINGER_CLEAR])
                if not plan["ok"]:
                    detail = plan["why"]
                    break
            check(picked, label.replace("ロボの", f"ロボ（{plan.get('name', '?')}・{plan.get('dir', '?')}）の"), detail)

        # ⑦ 部屋の ❗ のロボをタップ → トレイがその人になる
        people = page.evaluate(TRAY_PEOPLE)
        others = [a for a in people["queue"] if not people["shown"] or a["session"] != people["shown"]["session"]]
        target = None
        if len(people["queue"]) < 2 or not others:
            check(False, f"⑦ {tag}: 部屋の ❗ のロボをタップ → トレイがその人になる",
                  f"❗ のキューが {len(people['queue'])} 人（固定ワールドには 2 人居るはず）・トレイ「{people['head']}」")
        else:
            target = others[0]
            rp = page.evaluate(ROBOT_POINT, target["session"])
            if not rp["ok"]:
                check(False, f"⑦ {tag}: 部屋の ❗ のロボ（{target['name']}）をタップ → トレイがその人になる", rp["why"])
            else:
                before = page.evaluate("""() => ({ sheet: !document.querySelector('#sheet')?.hidden,
                  selected: window.__office.debug.selected() })""")
                tap(rp["x"], rp["y"])
                moved = wait_until(page, lambda: target["name"] in page.evaluate(
                    "() => document.querySelector('#attn .trayhead')?.textContent || ''"), 3)
                after = page.evaluate("""() => ({ head: document.querySelector('#attn .trayhead')?.textContent.trim() || '',
                  selected: window.__office.debug.selected(), sheet: !document.querySelector('#sheet')?.hidden })""")
                sel_note = (f"選択は {before['selected']} → {after['selected']} に替わった"
                            if after["selected"] != before["selected"] else
                            f"選択も {after['selected']} のまま（タップが部屋に届いていないか、当たりが外れた）")
                check(moved, f"⑦ {tag}: 部屋の ❗ のロボ（{target['name']}）をタップ → トレイがその人になる",
                      f"トレイは「{after['head']}」のまま（{sel_note}）"
                      " → 部屋のロボの選択で tray.show(session) を呼ぶ（trayIndex を選択と合わせる）")
                check(not after["sheet"] or before["sheet"],
                      f"⑦' {tag}: ロボのタップで指示欄（#sheet）は開かない",
                      "指示欄が開いた（focusSession を流用している＝実機ではキーボードが上がる）"
                      " → 合わせるだけの tray.show(session) を使う")

        # ⑧'' 先に、答えの POST を 500 にして同じ答えを押す → 「配達失敗」のトーストが今のスクロール位置で画面内。
        #     失敗ではトレイは変わらない（回答済みにならない）＝続けて ⑧ で同じ答えを押せる
        ap = page.evaluate(ANSWER_POINT)
        want_sid = target["session"] if target else None
        if ap["ok"]:
            page.evaluate(TOAST_WATCH)
            nf = len(posts)
            fail["on"] = True
            try:
                tap(ap["x"], ap["y"])
                wait_until(page, lambda: any(t["err"] for t in page.evaluate(TOAST_TAKE)), 3)
            finally:
                fail["on"] = False
            failed = [p for p in posts[nf:] if p.get("status") == 500 and p["path"] in ANSWER_PATHS]
            check_toast(check, f"⑧'' {tag}: 答えの POST が 500 のとき、「配達失敗」のトーストが今のスクロール位置で画面内",
                        page.evaluate(TOAST_TAKE), True, bool(failed))
            # 測り終えた失敗のトーストは下げる（2.6 秒待たない。答えのボタンに重なって ⑧ の押す点を塞がないように）
            page.evaluate("() => document.querySelectorAll('#toast .tmsg.err').forEach((n) => n.remove())")
            wait_until(page, lambda: page.evaluate(
                "() => !document.querySelector('#attn button[data-idx=\"0\"]')?.disabled"), 2)
            ap = page.evaluate(ANSWER_POINT)

        # ⑧ トレイの最初の答えをタップ → POST の本文。⑧' 答えた直後の「投函済み」のトーストが今のスクロール位置で画面内
        if not ap["ok"]:
            check(False, f"⑧ {tag}: トレイの答えをタップ → その人へ POST", ap["why"])
        else:
            now_people = page.evaluate(TRAY_PEOPLE)
            shown = now_people["shown"]
            page.evaluate(TOAST_WATCH)
            n0 = len([p for p in posts if p["path"] in ANSWER_PATHS])
            tap(ap["x"], ap["y"])
            wait_until(page, lambda: len([p for p in posts if p["path"] in ANSWER_PATHS]) > n0, 3)
            got = [p for p in posts if p["path"] in ANSWER_PATHS][n0:]
            wait_until(page, lambda: any(not t["err"] for t in page.evaluate(TOAST_TAKE)), 3)
            toasts = page.evaluate(TOAST_TAKE)
            why = []
            if not got:
                why.append(f"「{ap['label']}」をタップしても答えの POST が出ない（記録した POST: "
                           f"{[p['path'] for p in posts][-4:]}）")
            elif len(got) > 1:
                why.append(f"答えの POST が {len(got)} 通（二重送信）")
            if got and shown:
                p = got[0]
                body = p["body"] if isinstance(p["body"], dict) else {}
                text = EXPECTED_BODY.get(shown["session"])
                want_path = "/api/approval/reply" if shown["ask"] else "/api/instruct"
                if p["path"] != want_path:
                    why.append(f"POST 先 {p['path']}（期待 {want_path}）")
                if body.get("session") != want_sid:
                    why.append(f"session {body.get('session')}（期待＝部屋でタップした {want_sid}）"
                               + (" ＝⑦ が直れば直る" if body.get("session") == shown["session"] else ""))
                want_beh = "allow" if text == "" else "deny"       # 許可待ちの 1 番は allow（R102-11）・質問は deny＋文面
                if p["path"] == "/api/approval/reply" and body.get("behavior") != want_beh:
                    why.append(f"behavior {body.get('behavior')}（期待 {want_beh}）")
                msg = body.get("message") if p["path"] == "/api/approval/reply" else body.get("text")
                msg = msg or ""
                if text is None:
                    why.append(f"固定ワールドの期待の文が無い人（{shown['session']}）＝門の EXPECTED_BODY を直す")
                elif msg != text:
                    why.append(f"本文「{msg}」（押したボタン「{ap['label']}」の定型は「{text}」）")
            elif got:
                why.append(f"トレイの見出しから人を引けない（「{now_people['head']}」）")
            check(not why, f"⑧ {tag}: トレイの答えを touchscreen.tap → その人（{want_sid}）へ定型の POST"
                  if touch else f"⑧ {tag}: トレイの答えをクリック → その人（{want_sid}）へ定型の POST",
                  " / ".join(why))
            check_toast(check, f"⑧' {tag}: 答えた直後の配達の手応え（「投函済み」のトースト）が今のスクロール位置で画面内",
                        toasts, False, bool(got))

        # ③'' 長い名前（全角 42 字の title を全員に）でもページに横スクロールが無い。⑧ の後＝他の検査の世界を変えない
        long_world = with_long_names(world)
        office["body"] = json.dumps(long_world, ensure_ascii=False)   # ポーリングで短い名前へ戻さない
        page.evaluate("w => window.__office.inject(w)", long_world)
        named = wait_until(page, lambda: len(page.evaluate(
            "() => document.querySelector('#cp-term-who')?.textContent || ''")) >= LONG_N, 3)
        page.wait_for_timeout(200)
        px = page.evaluate(PAGE_X)
        why = []
        if not named:
            why.append("空振り: 端末の足元（#cp-term-who）に長い名前が出ない")
        if px["pages"]:
            why.append(" / ".join(f"{p['who']} の横幅 {p['sw']} > 画面 {px['vw']}" for p in px["pages"])
                       + ("・はみ出している要素: " + " / ".join(f"{x['who']}（x {x['left']}〜{x['right']}）" for x in px["leak"])
                          if px["leak"] else ""))
        check(not why, f"③'' {tag}: 長い名前（全角 {LONG_N} 字）でもページに横スクロールが無い",
              " / ".join(why) + " → .cpcenter の列を minmax(0, 1fr) で明示する（書かないと暗黙の auto 列が、端末の足元の"
              " nowrap の名前の min-content に押し広げられる＝.cpright と同じ穴）。名前は 1 行で切る（#cp-term-who に"
              " min-width:0・overflow:hidden・text-overflow:ellipsis）")

        # ⑧''（長い error）⑧'（長い名前）: 長い名前の世界のまま、トレイの答えをもう 1 回（500＋長い error → 成功）。
        #     トーストが画面の幅の中で折り返して収まる（短い名前だけでは横の条件が一度も試されなかった）
        check_long_toasts(page, tag, tap, posts, fail, check)

        # ⑩' ❗ の無い世界（トレイが hidden）で名簿の行を押す → 指示欄が開いても部屋の箱の丈が変わらない・多段では
        #    指示欄が端末の下（R110 骨格 A）で ❗ があるときと同じ置き方・指示欄が今のスクロール位置で見える。⑩ は ❗ がある（トレイの
        #    「自由に書く」から開く）形しか見ていなかった＝hud の :has(#attn[hidden]) の則（詳細度が 1 段高い）に負ける
        #    3 カラムの 1181〜1279 の穴が緑のまま通った（第 C 波の変異 band_hidden）
        check_quiet_sheet(page, tag, tap, world, office, sheet_ref, check, shape)

        # ⑮ オフライン帯: /api/office を 503 にして帯を出し、スクロールした位置のまま画面内に見える（最後＝他の検査の世界を変えない）
        check_offbar(page, tag, tap, office, world, posts, fail, check)

        errs = [e for e in errors if not induced(e)]
        check(not errs, f"⓪ {tag}: console.error / pageerror = 0（⑧'' の 500 と ⑮ の 503＝わざと返した {len(errors) - len(errs)} 件は"
              "数えない）", "; ".join(errs[:3]))
    finally:
        ctx.close()


TOAST_FIXED_HINT = (" → ui/cockpit/style.css の 1 列の段で .ui-cockpit #toast を画面に固定する（position: fixed・bottom は画面基準）。"
                    "hud の .toast は .main 基準の absolute・bottom 180px＝1 列では .main がページの丈まで伸びて、ページの一番下に出る")
TOAST_WRAP_HINT = (" → ui/cockpit/style.css の 1 列の段の .ui-cockpit #toast .tmsg で折り返す（white-space: normal・"
                   "overflow-wrap: anywhere・max-width: 100%）。hud の .tmsg は nowrap＝長い名前・長い error は 1 行のまま"
                   "画面の両側へはみ出す（第 D 波の検収の toast_nowrap: 390 で x −146〜536）")


def check_toast(check, label, toasts, err, posted, must=()):
    """⑧' ⑧'' トーストの判定。toasts＝TOAST_WATCH が記録した測り（出た順）。err＝失敗のトーストを見るか。
    must＝トーストの文言に要る文字列（長い名前・長い error＝その長さを本当に試した証拠。無ければ空振りの赤）。
    直し方の案内は測った形で選ぶ: 横にはみ出したら折り返し（TOAST_WRAP_HINT）、それ以外は画面への固定（TOAST_FIXED_HINT）"""
    pick = [t for t in toasts if t["err"] == err]
    why = []
    hint = TOAST_FIXED_HINT
    if not posted:
        why.append("空振り: 答えの POST が出ていない" + ("（500 を返す形）" if err else ""))
    if not pick:
        why.append(f"{'失敗の' if err else ''}トースト（#toast .tmsg{'.err' if err else ''}）が出ない"
                   f"（記録したトースト {[t['text'][:20] for t in toasts]}）")
    else:
        t = pick[-1]
        where = (f"y {t['top']}〜{t['bottom']}・x {t['left']}〜{t['right']}・画面 {t['vw']}×{t['vh']}・"
                 f"スクロール {t['scrollY']}・ページの丈 {t['docH']}・#toast は position {t['pos']}")
        if err and FAIL_ERROR not in t["text"]:
            why.append(f"文言が「{t['text']}」（500 の error「{FAIL_ERROR}」を載せた配達失敗のはず）")
        lack = [m for m in must if m not in t["text"]]
        if lack:
            why.append(f"空振り: トーストの文言「{t['text'][:40]}」に「{lack[0][:16]}…」が無い（長い文言を試せていない）")
        if not t["connected"]:
            why.append(f"測る前に消えた（{where}）")
        elif not t["inView"]:
            wide = t["left"] < -EDGE_TOL or t["right"] > t["vw"] + EDGE_TOL
            why.append(f"今のスクロール位置で画面の外（{where}{'・横にはみ出す' if wide else ''}）")
            if wide:
                hint = TOAST_WRAP_HINT
        elif t["cut"]:
            why.append(f"祖先 {t['by'] or '?'} に切られている（{where}）")
        elif not t["hit"]:
            why.append(f"中心に {t['over'] or '何も無い'} が重なる（{where}）")
        if t["connected"] and (t["opacity"] < OPACITY_MIN or t["visibility"] != "visible"):
            why.append(f"opacity の積 {t['opacity']:.2f}・visibility {t['visibility']}")
    shown = (f"（「{pick[-1]['text'][:24]}」y {pick[-1]['top']}〜{pick[-1]['bottom']}/{pick[-1]['vh']}・"
             f"x {pick[-1]['left']}〜{pick[-1]['right']}/{pick[-1]['vw']}）" if pick else "")
    check(not why, label + shown, " / ".join(why) + hint)


def answer_point_any(page, tap):
    """トレイの最初の答えを押す点。今の人が回答済み（答えのボタンが無い）なら「次へ」で答えられる人まで進める（2 回まで）"""
    ap = page.evaluate(ANSWER_POINT)
    for _ in range(2):
        if ap["ok"]:
            return ap
        nx = page.evaluate(TRAY_NEXT_POINT)
        if not nx["ok"]:
            return {**ap, "why": f"{ap['why']}（{nx['why']}）"}
        tap(nx["x"], nx["y"])
        wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#attn button[data-idx=\"0\"]')"), 1.5)
        ap = page.evaluate(ANSWER_POINT)
    return ap


def check_long_toasts(page, tag, tap, posts, fail, check):
    """⑧''（長い error）⑧'（長い名前）: ③'' の長い名前の世界で、トレイの答えをもう 1 回押す（先に 500＋長い error、続けて成功）。
    トーストの元の矩形が 0〜画面の幅に収まり（画面の中で折り返す）、中心が当たる。固定ワールドの短い名前（「インフラ 2号 への
    配達 📨投函済み」＝幅 240px）だけでは横の条件が一度も試されず、折り返しの則を消す退行（hud の nowrap に戻る）が緑だった
    （第 D 波の検収の toast_nowrap: 390 で x −146〜536）。POST は記録するだけ（どこにも届かない）"""
    label_f = (f"⑧''（長い error） {tag}: 答えの POST が 500＋長い error のとき、「配達失敗」のトーストが画面の幅の中に収まり"
               "中心が当たる")
    label_s = f"⑧'（長い名前） {tag}: 全角 {LONG_N} 字の名前の人に答えた直後の「投函済み」のトーストが画面の幅の中に収まり中心が当たる"
    ap = answer_point_any(page, tap)
    if not ap["ok"]:
        check(False, label_f, "空振り: " + ap["why"])
        check(False, label_s, "空振り: " + ap["why"])
        return
    page.evaluate(TOAST_WATCH)
    nf = len(posts)
    fail.update(on=True, error=LONG_ERROR)
    try:
        tap(ap["x"], ap["y"])
        wait_until(page, lambda: any(t["err"] for t in page.evaluate(TOAST_TAKE)), 3)
    finally:
        fail.update(on=False, error=None)
    failed = [p for p in posts[nf:] if p.get("status") == 500 and p["path"] in ANSWER_PATHS]
    check_toast(check, label_f, page.evaluate(TOAST_TAKE), True, bool(failed), must=(LONG_ERROR,))
    page.evaluate("() => document.querySelectorAll('#toast .tmsg.err').forEach((n) => n.remove())")
    wait_until(page, lambda: page.evaluate(
        "() => !document.querySelector('#attn button[data-idx=\"0\"]')?.disabled"), 2)
    ap = page.evaluate(ANSWER_POINT)
    if not ap["ok"]:
        check(False, label_s, "空振り: 失敗の後に答えのボタンを押せない（" + ap["why"] + "）")
        return
    page.evaluate(TOAST_WATCH)
    n0 = len([p for p in posts if p["path"] in ANSWER_PATHS])
    tap(ap["x"], ap["y"])
    wait_until(page, lambda: len([p for p in posts if p["path"] in ANSWER_PATHS]) > n0, 3)
    wait_until(page, lambda: any(not t["err"] for t in page.evaluate(TOAST_TAKE)), 3)
    got = len([p for p in posts if p["path"] in ANSWER_PATHS]) > n0
    check_toast(check, label_s, page.evaluate(TOAST_TAKE), False, got, must=(LONG_NAME[:12],))


def check_stages(page, tag, check):
    """⑭ 段の一致: 「ヘッダーがスマホの段か」と「画面の幅 ≤ NARROW_MAX か」が揃う（境は画面 600 の 1 本）。
    R110 決めたこと 5: 名簿の行は**幅に依らず 2 段**（上段＝記号・プロジェクト・状態／下段＝いま・証拠・経過）＝台帳の段は幅で
    切り替えない。どの形でも見えている行の「いま」がプロジェクトより下の段にあること（1 段へ戻す退行・幅の段を足す退行の番人）"""
    s = page.evaluate(STAGES)
    narrow = s["vw"] <= NARROW_MAX
    say = lambda phone: "スマホの段" if phone else "スマホでない段"  # noqa: E731
    why = []
    if not s["head"]:
        why.append("空振り: ヘッダーの管理（.admin）かプロンプト（.brand）が見えない")
    if not s["ledger"]:
        why.append("空振り: 名簿の見えている行に「いま」（.c-detail）かプロジェクト（.c-project）が見えない")
    if s["head"] and s["headPhone"] != narrow:
        why.append(f"ヘッダーの段が画面の幅と揃わない: 画面の幅 {s['vw']}（{'≤' if narrow else '>'} {NARROW_MAX}）・"
                   f"ヘッダーは{say(s['headPhone'])}（管理 y {s['adminY']}・プロンプト y {s['usageY']}・.admin の grid-row {s['adminRow']}・"
                   f"丈 {s['headH']}px）")
    if s["ledger"] and not s["ledgerPhone"]:
        why.append(f"名簿の行が 2 段でない（「いま」y {s['detailY']}・プロジェクト y {s['projectY']}・{s['cols']} 列・"
                   f"areas {s['areas']}）")
    shown = (f"画面の幅 {s['vw']}・ヘッダー {say(s['headPhone'])}（丈 {s['headH']}px）・名簿の行 "
             f"{'2 段' if s['ledgerPhone'] else '1 段'}（{s['cols']} 列）" if s["head"] and s["ledger"] else "")
    check(not why, f"⑭ {tag}: ヘッダーは画面 {NARROW_MAX}px 以下でだけスマホの段・名簿の行はどの幅でも 2 段（{shown}）",
          " / ".join(why) + " → ヘッダーのスマホの段の問い合わせ先は余白を持たない画面の幅の箱（1 列の .cpshell）に置く"
          "（余白 12px の .cphead に置くと、画面 601〜624 でヘッダーだけがスマホの段になる）。名簿の行は"
          " grid-template-areas の 2 段を幅の段で上書きしない")


def check_offbar(page, tag, tap, office, world, posts, fail, check):
    """⑮ /api/office を 503 にする → #offbar が出る（ポーリングが 2 回続けて落ちる）→ ページを OFFBAR_SCROLL までスクロールして、
    その位置のまま帯が画面内・祖先に切られず・中心が当たる。⑮（ヘッダー）帯が出ている間、スクロール 0 でヘッダーの見えている
    button と .gpin の中心が帯に当たらない。⑳ 帯が出ている間に、折り返す長さのトーストを 2 件出しても帯に掛からない
    （check_band_toasts）。測り終えたら /api/office を戻す"""
    label = f"⑮ {tag}: /api/office が落ちるとオフライン帯（#offbar）が出て、スクロールした位置のまま画面内に見える"
    label_head = f"⑮（ヘッダー） {tag}: オフライン帯が出ている間、スクロール 0 でヘッダーの押せる部品（.cphead の button・.gpin）の中心に帯が重ならない"
    office["down"] = True
    try:
        t0 = time.monotonic()
        up = wait_until(page, lambda: page.evaluate(
            "() => { const e = document.querySelector('#offbar'); return !!e && !e.hidden; }"), OFFBAR_WAIT, step=200)
        took = time.monotonic() - t0
        if not up:
            why = f"空振り: /api/office を 503 にして {OFFBAR_WAIT:.0f} 秒待っても #offbar が出ない（hidden のまま）"
            check(False, label, why)
            check(False, label_head, why)
            check(False, f"⑳ {tag}: オフライン帯が出ている間の折り返したトースト 2 件が帯に掛からない", why)
            return
        hd = page.evaluate(OFFBAR_HEAD, OPACITY_MIN)
        doc_h, vh = page.evaluate("() => [document.documentElement.scrollHeight, innerHeight]")
        o = page.evaluate(OFFBAR_STATE, max(0, min(OFFBAR_SCROLL, doc_h - vh)))
        check_band_toasts(page, tag, tap, world, posts, fail, check)
    finally:
        office["down"] = False
    page.evaluate("() => window.scrollTo(0, 0)")
    head_why = []
    if not hd["head"]:
        head_why.append("空振り: ヘッダー（.cphead）が無い")
    elif not hd["n"]:
        head_why.append("空振り: ヘッダーに見えている button・.gpin が 1 つも無い（測った部品 0 個）")
    if hd["bad"]:
        head_why.append(f"帯（{hd.get('bar')}・position {hd.get('pos')}・画面 {hd.get('vw')}×{hd.get('vh')}）が中心を吸う: "
                        + " / ".join(hd["bad"]) + " → ui/cockpit/style.css の 1 列の段で .ui-cockpit #offbar を画面の下端に"
                        "固定する（top: auto; bottom は配達の手応えのトースト 2 件の上）。上に固定すると、スクロール 0 でヘッダーに"
                        "重なり、スクロールするとヘッダーのほうが帯の下へ流れる＝オフラインの間はヘッダーを押す位置が無い")
    check(not head_why, label_head + (f"（{hd['n']} 個・帯 {hd.get('bar')}）" if not head_why else ""),
          " / ".join(head_why))
    why = []
    where = (f"y {o.get('top')}〜{o.get('bottom')}・x {o.get('left')}〜{o.get('right')}・画面 {o.get('vw')}×{o.get('vh')}・"
             f"スクロール {o.get('scrollY')}・ページの丈 {o.get('docH')}・#offbar は position {o.get('pos')}")
    if not o["exists"] or not o["shown"]:
        why.append("#offbar が見えない（hidden は外れたが箱が無いか visibility が visible でない）")
    else:
        if not o["text"]:
            why.append("空振り: 帯の文言が空")
        if o["cols"] == 1 and o["scrollY"] <= 0:
            why.append(f"空振り: 1 列なのにページがスクロールしない（{where}）＝スクロールした位置を試せていない")
        if not o["inView"]:
            why.append(f"スクロールした位置で画面の外（{where}）")
        elif o["cut"]:
            why.append(f"祖先 {o['by'] or '?'} に切られている（{where}）")
        elif not o["hit"]:
            why.append(f"中心に {o['over'] or '何も無い'} が重なる（{where}）")
        if o["opacity"] < OPACITY_MIN or o["visibility"] != "visible":
            why.append(f"opacity の積 {o['opacity']:.2f}・visibility {o['visibility']}")
    check(not why, label + (f"（{took:.1f} 秒で出た・スクロール {o['scrollY']} で y {o['top']}〜{o['bottom']}/{o['vh']}）"
                            if o.get("exists") else ""),
          " / ".join(why) + " → ui/cockpit/style.css の 1 列の段で .ui-cockpit #offbar を画面に固定する（position: fixed・"
          "画面の下端が基準＝上に置くと ⑮（ヘッダー）が赤）。hud の .offbar は .main 基準の absolute; top 12px＝1 列ではページの"
          "先頭に残り、スクロールすると上の外へ出る")


def check_band_toasts(page, tag, tap, world, posts, fail, check):
    """⑳ オフライン帯が出ている間（/api/office は 503 のまま）に、長い名前の世界で ❗ に 2 回答え、2 回とも 500＋長い error
    （LONG_ERROR＝390 で 3〜4 行に折り返す）にする → 「配達失敗」のトーストが 2 件そろった瞬間に、どのトーストも帯（#offbar）と
    矩形が重ならず・画面内で・中心が当たる。帯の位置（1 列は画面の下から 108px）の根拠は「1 行のトースト 2 件」だった＝
    折り返したトーストは帯の下から上へはみ出して帯に掛かった（ROADMAP R99-Phase4-A のバックログ）。
    答え済みの印を外すため、sess-cp-09 の質問文だけを替えた世界を inject する（帯は inject では消えない＝ポーリングだけが消す）"""
    label = f"⑳ {tag}: オフライン帯が出ている間に、折り返す長さのトースト 2 件が出ても帯（#offbar）に掛からない"
    w = with_long_names(world)
    for key in ("roster", "employees"):
        for p in w.get(key) or []:
            if p.get("session") == BAND_SID and p.get("question"):
                p["question"] += BAND_QUESTION_TAIL
    page.evaluate("w => window.__office.inject(w)", w)
    wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#attn .trayhead')"), 3)
    page.wait_for_timeout(150)
    band_up = page.evaluate("() => { const e = document.querySelector('#offbar'); return !!e && !e.hidden; }")
    ap = answer_point_any(page, tap)
    if not band_up or not ap["ok"]:
        check(False, label, "空振り: " + ("inject の後に帯が消えた" if not band_up else ap["why"]))
        return
    cols = page.evaluate("() => getComputedStyle(document.querySelector('.main')).gridTemplateColumns.trim().split(/\\s+/).length")
    nf = n0 = len(posts)
    pair = lambda shots:[s for s in shots if len([t for t in s["toasts"] if t["err"]]) >= 2]  # noqa: E731
    enabled = "() => !document.querySelector('#attn button[data-idx=\"0\"]')?.disabled"
    fail.update(on=True, error=BAND_ERROR if cols == 1 else LONG_ERROR)
    try:
        # トーストは 2.6 秒で消える＝2 回目は、1 回目の 500 が返って答えのボタンが戻ったらすぐ押す（トーストの遷移は待たない）。
        # 重い機械で 2 件がそろう前に 1 件目が消えたら、組ごと 1 回だけやり直す（負荷で空振りの赤を出さない）
        for _attempt in range(2):
            page.evaluate(TOAST_BAND_WATCH)
            for i in range(2):
                if i:
                    wait_until(page, lambda: len([p for p in posts[n0:] if p.get("status") == 500]) >= 1, 3)
                    wait_until(page, lambda: page.evaluate(enabled), 2)
                    ap = page.evaluate(ANSWER_POINT)
                    if not ap["ok"]:
                        break
                n0 = len(posts)
                tap(ap["x"], ap["y"])
            wait_until(page, lambda: bool(pair(page.evaluate(TOAST_BAND_TAKE))), 3)
            if pair(page.evaluate(TOAST_BAND_TAKE)) or not ap["ok"]:
                break
            page.evaluate("() => document.querySelectorAll('#toast .tmsg').forEach((n) => n.remove())")
            wait_until(page, lambda: page.evaluate(enabled), 2)
            ap = page.evaluate(ANSWER_POINT)
            if not ap["ok"]:
                break
    finally:
        fail.update(on=False, error=None)
    shots = page.evaluate(TOAST_BAND_TAKE)
    failed = [p for p in posts[nf:] if p.get("status") == 500 and p["path"] in ANSWER_PATHS]
    both = pair(shots)
    why = []
    if len(failed) < 2:
        why.append(f"空振り: 500 を返した答えの POST が {len(failed)} 通（2 通が要る）")
    if not both:
        why.append(f"空振り: 失敗のトーストが 2 件そろった瞬間が無い（測った瞬間 {[len(s['toasts']) for s in shots]} 件）")
        snap = shots[-1] if shots else None
    else:
        snap = both[-1]
    if snap:
        if not snap["bandShown"]:
            why.append("空振り: 測った瞬間に帯（#offbar）が出ていない")
        tall = [t for t in snap["toasts"] if t["h"] < 40]
        if tall and both and snap["cols"] == 1:          # 3 カラムの .tmsg は hud の nowrap のまま（画面が広く 1 行に収まる）
            why.append(f"空振り: 折り返していないトーストがある（丈 {[t['h'] for t in tall]}px）＝長い文言を試せていない")
        where = f"帯 {snap['band']}・画面 {snap['vw']}×{snap['vh']}・#toast は position {snap['pos']}・bottom {snap['bottom']}"
        for t in snap["toasts"]:
            if t["lap"]:
                why.append(f"トースト「{t['text']}」（{t['rect']}・丈 {t['h']}px）が帯に掛かる（{where}）"
                           " → 1 列の段で、帯が出ている間（.ui-cockpit.offline）はトーストを帯の上から積む"
                           "（帯の丈は index.js が --cp-offbar-h へ実測で渡す）")
            elif not t["inView"]:
                why.append(f"トースト「{t['text']}」（{t['rect']}）が画面の外（{where}）")
            elif not t["hit"]:
                why.append(f"トースト「{t['text']}」の中心に {t['over'] or '何も無い'} が重なる（{where}）")
        # 1 列: トーストは帯のすぐ上に積む（帯の上端と一番下のトーストの下端の隙間 0〜BAND_GAP_MAX px）。帯の丈は index.js が
        #   実測して --cp-offbar-h へ渡す＝実測が止まると CSS の既定の丈（52px）で積み、1 行の帯（34px）の形では隙間が 26px に開く
        if snap["cols"] == 1 and snap["toasts"] and snap["bandTop"] is not None and not any(t["lap"] for t in snap["toasts"]):
            gap = snap["bandTop"] - max(t["bottom"] for t in snap["toasts"])
            if not 0 <= gap <= BAND_GAP_MAX:
                why.append(f"帯の上端と一番下のトーストの隙間が {gap:.1f}px（0〜{BAND_GAP_MAX}px のはず・{where}）"
                           " → トーストは帯のすぐ上から積む（帯の丈の実測＝index.js の ResizeObserver が --cp-offbar-h を渡しているか）")
    check(not why, label + (f"（{snap['band']}・トースト {' / '.join(t['rect'] for t in snap['toasts'])}）" if snap and not why else ""),
          " / ".join(why))
    page.evaluate("() => document.querySelectorAll('#toast .tmsg').forEach((n) => n.remove())")


def check_gauges_under_modal(page, tag, check):
    """㉗ ゲージの詳細（#gauges・hud の z-index 10）を開いたまま、キーボードの ? でモーダル（ヘルプ）を開く。
    詳細は暗幕（9）より上に描かれ、モーダルの右半分を覆っていた（押せない＝見えるが押せない）。開いている間は出さない。"""
    st = page.evaluate("""() => {
        const u = document.querySelector('.usage'), s = document.querySelector('#usage-summary');
        if (!u || !s) return {ok: false, why: '.usage か #usage-summary が無い'};
        if (!u.classList.contains('open')) s.click();
        return {ok: u.classList.contains('open')};
    }""")
    if not st.get("ok"):
        check(False, f"㉗ {tag}: ゲージの詳細を開けない（空振り）", st.get("why", "要約を押しても .usage が .open にならない"))
        return
    page.keyboard.press("?")
    opened = wait_until(page, lambda: page.evaluate(
        "() => { const w = document.querySelector('#modalwrap'); return !!w && !w.hidden; }"), 2)
    if not opened:
        page.evaluate("() => document.querySelector('#btn-help')?.click()")
        opened = wait_until(page, lambda: page.evaluate(
            "() => { const w = document.querySelector('#modalwrap'); return !!w && !w.hidden; }"), 2)
    g = page.evaluate("""() => {
        const g = document.querySelector('#gauges');
        if (!g) return {has: false};
        const cs = getComputedStyle(g), r = g.getBoundingClientRect();
        return {has: true, display: cs.display, vis: cs.visibility, area: Math.round(r.width * r.height)};
    }""")
    shown = g.get("has") and g.get("display") != "none" and g.get("vis") != "hidden" and g.get("area", 0) > 0
    check(opened and not shown, f"㉗ {tag}: ゲージの詳細を開いたままモーダルを開いても、詳細はモーダルの上に出ない",
          ("モーダルが開かない（? と #btn-help の両方）" if not opened else
           f"詳細が見えている（display {g.get('display')}・面積 {g.get('area')}）→ ui/cockpit/style.css の "
           ".ui-cockpit:has(#modalwrap:not([hidden])) .usage .gauges を確かめる"))
    # 閉じる（⑬ と同じ: Escape が効かなければ ✕）。開いたままだと後の検査が全部暗幕に塞がれる
    page.keyboard.press("Escape")
    closed = wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#modalwrap')?.hidden"), 1.5)
    if not closed:
        page.evaluate("() => document.querySelector('#modalclose')?.click()")
        closed = wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#modalwrap')?.hidden"), 1.5)
    if not closed:
        check(False, f"㉗ {tag}: モーダルを閉じる", "Escape でも ✕ でも閉じない（後の検査は開いたまま測る）")
    page.evaluate("""() => { const u = document.querySelector('.usage');
        if (u && u.classList.contains('open')) document.querySelector('#usage-summary')?.click(); }""")
    page.evaluate("() => window.scrollTo(0, 0)")


def check_ua(check, tag, load, modal):
    """⑯ 見えている button（読み込み後のページ全体と、⑬ で開いたモーダルの中）に、ブラウザ既定の枠（outset／inset）が無い"""
    why = []
    if not load["n"]:
        why.append("空振り: 見えている button が 0 個")
    if modal is None:
        why.append("空振り: ⑬ でモーダルが開けず、モーダルの中の button を測れない")
    bad = load["bad"] + (modal["bad"] if modal else [])
    if bad:
        why.append(f"{len(bad)} 個: " + " / ".join(f"{b['who']}「{b['text']}」（{b['ua']}）" for b in bad[:4]))
    check(not why, f"⑯ {tag}: 見えている button にブラウザ既定の枠（outset／inset）が無い（ページ {load['n']} 個・"
          f"モーダル {modal['n'] if modal else '—'} 個）",
          " / ".join(why) + " → ui/cockpit/style.css で border の一括（border: 0）で UA の枠を消してから、要る辺だけ hairline"
          "（1px の --cp-line／--cp-line-soft）を引く（片側だけ書くと残りの辺に 2px の outset が残る）")


def check_modal(page, tag, tap, touch, dsf, check):
    """⑬ スクロール 0 で ⚙（設定）を押す → #modal が今のスクロール位置で、中身まで画面に出る。閉じて元へ戻す（閉じなければ赤）。
    開いている間に ⑯ の材料（モーダルの中の見えている button のブラウザ既定の枠）も測って返す（開けなければ None）。
    同じ開閉で ⑰ 暗幕の効き（開く前 2 枚と開いた後 1 枚の画素・部屋は止める）・⑲ ✕ の置き場所・⑱ スクロールのロック
    （開く前の陽性対照 → 開いている間 → 閉じた後）も見る"""
    label = f"⑬ {tag}: スクロール 0 で ⚙（設定）を押すと、モーダル（#modal）が今のスクロール位置で中身まで画面に出る"
    label_scrim = (f"⑰ {tag}: モーダルの暗幕が暗い地の上でも背面を沈める（同じ画素の明暗の差が暗幕なしの "
                   f"{SCRIM_MAX:.0%} 以下）")
    label_lock = f"⑱ {tag}: モーダルを開いている間は、指（ホイール）で背面のページも面も動かず、閉じると元どおり動く"
    label_close = (f"⑲ {tag}: モーダルの ✕ が画面内で押せて、ヘッダーのボタン（? など）に重ならず、モーダルの見出しの文字から"
                   f" {CLOSE_TITLE_GAP}px 以上離れる")
    label_gutter = (f"㉒ {tag}: 1 列でモーダルを開閉しても背面の幅が変わらない（html の scrollbar-gutter が開く前も開いている間も"
                    " stable・clientWidth が同じ）")
    label_touch = f"㉓ {tag}: タッチの形でモーダルの ✕ が {TOUCH_MIN}×{TOUCH_MIN} 以上"
    label_cover = (f"㉔ {tag}: モーダルの暗幕が画面の全体を覆い（ヘッダーとフッターも暗幕の下）、✕ がモーダルの矩形に接する"
                   f"（外へ {CLOSE_GAP}px 以内）")
    bp = page.evaluate(BUTTON_POINT, "#btn-settings")
    if not bp["ok"]:
        for lb in (label, label_scrim, label_lock, label_close, label_cover) + ((label_touch,) if touch else ()):
            check(False, lb, bp["why"])
        return None
    g0 = page.evaluate(GUTTER_STATE)                         # ㉒ 開く前（スクロール 0）
    # ⑱ 陽性対照（1 列＝ページがスクロールする段だけ）: 開く前に、ヘッダーの無地で同じ指の動き → ページが動く
    snap0 = page.evaluate(SCROLL_SNAPSHOT)
    one = snap0["cols"] == 1
    pts0 = page.evaluate(SWIPE_POINTS)
    pre = None
    if one and pts0.get("brand"):
        swipe(page, pts0["brand"], touch)
        pre = page.evaluate(SCROLL_SNAPSHOT)
        page.evaluate("() => window.scrollTo(0, 0)")
    # ⑰ 部屋を止めて開き、開いたままの同じ組み（スクロール 0）で、暗幕（#modalwrap）を隠した 2 枚と戻した 1 枚を撮る。
    #   開く前の画面と比べない: 390 ではモーダルを開くとトレイに「Esc で戻ると…」の行が出てページが 24px 下へずれ、画素の組が
    #   崩れて、hud の薄い暗幕でも差が 46% に縮んで見えた（着手時の変異の実測）。止めると「動きを止める」の文言が替わる
    #   ＝⚙ の点は止めた後に測り直す
    held = hold_room(page, True)
    try:
        bp = page.evaluate(BUTTON_POINT, "#btn-settings")
        tap(bp["x"], bp["y"])
        wait_until(page, lambda: page.evaluate(
            "() => !!document.querySelector('#modalwrap')?.classList.contains('show')"), 2)
        page.wait_for_timeout(250)                           # 開くフェード（120ms）の後に測る
        geo = page.evaluate(SCRIM_GEO)
        page.evaluate(SCRIM_HIDE, True)
        b1 = page.screenshot()
        page.wait_for_timeout(250)
        b2 = page.screenshot()
        page.evaluate(SCRIM_HIDE, False)
        after = page.screenshot()
    finally:
        hold_room(page, False)
    m = page.evaluate(MODAL_STATE)
    ua = page.evaluate(UA_BORDERS, "#modalwrap") if m.get("open") else None
    g1 = page.evaluate(GUTTER_STATE) if m.get("open") else None   # ㉒ 開いている間（ロックが掛かっている）
    cov = page.evaluate(SCRIM_COVER) if m.get("open") else {"ok": False, "why": "モーダルが開いていない"}
    why = []
    if not m["exists"]:
        why.append("#modalwrap か #modal が無い")
    elif not m["open"]:
        why.append("⚙ を押してもモーダルが開かない（#modalwrap が hidden のまま）")
    else:
        # R105: モーダルはヘッダーの下から出す（ヘッダーの右の広いボタンに ✕ が重なった＝⑲）＝使える丈は画面からヘッダーを引いた分
        need = min(m["sh"], m["vh"] - MODAL_MARGIN - max(0, m.get("headB", 0) - 12))   # 上＝ヘッダー＋8・下 20
        where = (f"y {m['top']}〜{m['bottom']}・x {m['left']}〜{m['right']}・画面 {m['vw']}×{m['vh']}・スクロール {m['scrollY']}・"
                 f"ページの丈 {m['docH']}・#modalwrap は position {m['pos']}")
        if not m["title"]:
            why.append("空振り: モーダルに見出し（.mtitle）が無い＝設定の面が描かれていない")
        if m["top"] < -EDGE_TOL:
            why.append(f"モーダルの上 {-m['top']}px が画面の上の外（{where}）")
        if m["left"] < -EDGE_TOL or m["right"] > m["vw"] + EDGE_TOL:
            why.append(f"モーダルが画面の横の外（{where}）")
        if m["visH"] < need - CLIP_TOL:
            why.append(f"見えている丈 {round(m['visH'])}px に中身が入らない（要 {need}px＝min(scrollHeight {m['sh']}, 画面の丈 "
                       f"{m['vh']} − {MODAL_MARGIN})・箱の丈 {m['h']}・{where}{'・切っている祖先 ' + m['by'] if m['by'] else ''}）"
                       "＝右上の ✕ と暗幕と細い帯だけが出て、中身は中でスクロールしないと読めない")
        elif not m["hit"]:
            why.append(f"モーダルの中心に {m['over'] or '何も無い'} が重なる")
        if m["opacity"] < OPACITY_MIN or m["visibility"] != "visible":
            why.append(f"opacity の積 {m['opacity']:.2f}・visibility {m['visibility']}")
        # 中身: 見出しと最初の操作部品が、祖先（#modal 自身のスクロール箱を含む）に切られず・画面内で・当たり・見える
        for c in m["content"]:
            if not c["found"]:
                why.append(f"中身の{c['name']}（{c['sel']}）がモーダルの中に無い（箱だけ描かれている）")
                continue
            bad = []
            if c["cut"]:
                bad.append(f"祖先 {c['by'] or '?'} に切られている（見えている丈 {c['visH']}px・元 {c['rect']}）")
            if not c["inView"]:
                bad.append(f"画面の外（{c['rect']}）")
            if c["visibility"] != "visible" or c["opacity"] < OPACITY_MIN:
                bad.append(f"visibility {c['visibility']}・opacity の積 {c['opacity']:.2f}")
            if not c["hit"]:
                bad.append(f"中心に {c['over'] or '何も無い'} が当たる")
            if bad:
                why.append(f"中身の{c['name']}（{c['who']}「{c['text']}」）が見えない: " + "・".join(bad))
    check(not why, label + (f"（「{m['title']}」y {m['top']}〜{m['bottom']}/{m['vh']}・見えている丈 {round(m['visH'])}/{m['sh']}・"
                            f"中身 {'と'.join(c['who'] for c in m['content'] if c.get('found'))}）"
                            if m.get("open") else ""),
          " / ".join(why) + " → ui/cockpit/style.css の 1 列の段で .ui-cockpit #modalwrap を画面に固定する（position: fixed;"
          " inset: 0）。hud の .modalwrap は .main 基準の absolute＝1 列では .main がページの丈まで伸びて、モーダルがページの縦の"
          "中央に出る（.modal の max-height: calc(100% − 40px) も画面の丈基準になる）。max-height を潰す・中身を隠す則を足さない")

    # ⑰ 暗幕: 背面の明暗の幅（#modal と ✕ を外し、開く前に動いた画素も外す）が開いた後にどれだけ縮んだか
    why = []
    base = dim = n = 0
    if not held:
        why.append("空振り: 撮る間に部屋を止められない（debug.roomMotion().still が true にならない）")
    if not geo.get("ok"):
        why.append("空振り: " + geo.get("why", "暗幕の矩形が取れない"))
    else:
        base, dim, n = scrim_contrast((b1, b2, after), geo, dsf)
        if base < SCRIM_BASE_MIN:
            why.append(f"空振り: 暗幕を隠した背面の明暗の差が {base:.0f}（{SCRIM_BASE_MIN} 以上が要る・{n} 画素）＝暗幕の効きを測れない")
        elif dim > base * SCRIM_MAX:
            why.append(f"暗幕を重ねても背面の明暗の差が {dim:.0f}（暗幕なし {base:.0f} の {dim / base:.0%}・{n} 画素・暗幕の色 {geo['bg']}・"
                       f"opacity {geo['opacity']}）＝背面の文字も部屋もほとんど沈まず、モーダルが背面から浮かない"
                       " → ui/cockpit/style.css で .ui-cockpit #modalwrap の背景を様式の地（--cp-bg）の半分以上の不透明度に"
                       "（hud の rgba(46,45,44,.14) は明るい紙の地のための薄さ）")
    check(not why, label_scrim + (f"（暗幕なしの差 {base:.0f} → ありの差 {dim:.0f}＝{dim / base:.0%}・{n} 画素）" if base else ""),
          " / ".join(why))

    # ⑲ ✕: 画面内・中心が当たる・ヘッダーのボタンと重ならない・見出しの文字と最初の操作部品に重ならない
    cs = page.evaluate(CLOSE_STATE) if m.get("open") else {"ok": False, "why": "モーダルが開いていない"}
    # 長い見出し（どのモーダルでも先頭は .mtitle・名前入りの見出しもある）でも ✕ の下に潜らない: 開いている間だけ見出しの文字を
    # LONG_NAME に差し替えて測り、元へ戻す（⚙ の見出し「⚙ 設定」は短く、見出しの右の余白を消しても重ならない＝門にならない）
    long_title = None
    if cs.get("ok"):
        long_title = page.evaluate("""(t) => { const e = document.querySelector('#modal .mtitle'); if (!e) return null;
          const was = e.textContent; e.textContent = t; return was; }""", LONG_NAME)
        if long_title is not None:
            cs["longTitle"] = page.evaluate(CLOSE_STATE)["title"]
            page.evaluate("(t) => { document.querySelector('#modal .mtitle').textContent = t; }", long_title)
    why = []
    if not cs["ok"]:
        why.append("空振り: " + cs["why"])
    else:
        if not cs["head"]:
            why.append("空振り: ヘッダーに見えているボタンが無い（重なりを測れない）")
        if not cs["inView"]:
            why.append(f"✕（{cs['rect']}）が画面の外（画面 {cs['vw']}×{cs['vh']}）")
        elif not cs["hit"]:
            why.append(f"✕ の中心に {cs['over'] or '何も無い'} が重なる（{cs['rect']}）")
        if cs["heads"]:
            why.append(f"✕（{cs['rect']}・position {cs['pos']}）がヘッダーのボタンに重なる: " + " / ".join(cs["heads"][:3])
                       + " → 1 列の段で ✕ を画面の右上の角ではなく、モーダル（#modal）の右上の角に付ける"
                       "（#modalwrap の 1 本の列と中身の丈の 1 行に #modal と ✕ を重ねて置く）")
        if cs["title"]:
            why.append(f"✕（{cs['rect']}）がモーダルの見出し（.mtitle）の文字に重なる（{CLOSE_TITLE_GAP}px 以内）"
                       " → 見出しの右に ✕ のぶんの余白を取る")
        if long_title is None:
            why.append("空振り: モーダルに見出し（.mtitle）が無い＝長い見出しを試せない")
        elif cs.get("longTitle"):
            why.append(f"見出しが長い（全角 {LONG_N} 字）と、✕（{cs['rect']}）の下に見出しの文字が潜るか {CLOSE_TITLE_GAP}px 以内に"
                       "貼り付く → 見出し（#modal > .mtitle:first-child）の右に ✕ のぶんの余白を取る（タッチの ✕ 44px は"
                       " (pointer: coarse) の段で余白も広げる）")
        if cs["first"]:
            why.append(f"✕（{cs['rect']}）がモーダルの最初の操作部品（{cs['firstWho']}）に重なる")
    check(not why, label_close + (f"（{cs['rect']}・スクロール {cs['scrollY']}・ヘッダーのボタン {cs['head']} 個）"
                                  if cs.get("ok") else ""), " / ".join(why))

    # ㉓ タッチの形の ✕ の寸法（hud の 32×32 では指で押し損じる）
    if touch:
        why = []
        if not cs["ok"]:
            why.append("空振り: " + cs["why"])
        elif cs["w"] < TOUCH_MIN or cs["h"] < TOUCH_MIN:
            why.append(f"✕ が {cs['w']}×{cs['h']}（{cs['rect']}）→ ui/cockpit/style.css の (pointer: coarse) で #modalclose を"
                       f" {TOUCH_MIN}×{TOUCH_MIN} に（見出しの右の余白も ✕ に合わせて広げる＝⑲ の長い見出しが見る）")
        check(not why, label_touch + (f"（{cs['w']}×{cs['h']}）" if cs.get("ok") else ""), " / ".join(why))

    # ㉔ 暗幕が画面の全体を覆う・ヘッダーとフッターが暗幕の下・✕ がモーダルに接する（どの段でも）
    why = []
    if not cov["ok"]:
        why.append("空振り: " + cov["why"])
    else:
        if not cov["cover"]:
            why.append(f"暗幕（#modalwrap・position {cov['pos']}・{cov['wrap']}）が画面（{cov['vw']}×{cov['vh']}）の全体を覆わない")
        if not any(p["sel"] == ".cphead" for p in cov["pts"]):
            why.append("空振り: ヘッダー（.cphead）が画面に無い（スクロール 0 で測れない）")
        out = [p for p in cov["pts"] if not p["under"]]
        if out:
            why.append("暗幕の外で押せそうに見える点: " + " / ".join(f"{p['sel']} ({p['x']}, {p['y']}) に {p['at']}" for p in out[:4])
                       + "（暗幕が .main だけに掛かり、明るいまま inertOthers で押せない）")
        if cov["gap"] is None:
            why.append("空振り: ✕（#modalclose）が見えない")
        elif cov["gap"] > CLOSE_GAP:
            why.append(f"✕（{cov['close']}）がモーダル（{cov['modal']}）から {cov['gap']}px 離れている")
        if why:
            why.append("→ ui/cockpit/style.css のモーダルの節（両方の段）で #modalwrap を画面に固定（position: fixed; inset: 0）し、"
                       "1 本の列 × 中身の丈の 1 行に #modal と ✕ を重ねる")
    check(not why, label_cover + (f"（暗幕 {cov['wrap']}・当てた点 {len(cov['pts'])} 個・✕ の離れ {cov['gap']}px）"
                                  if cov.get("ok") and not why else ""), " / ".join(why))

    # ㉒ スクロールバーの溝（1 列＝ページがスクロールする段だけ）。ヘッドレスの Chromium（Mac）のスクロールバーは重ねて出す＝
    #   溝の幅は 0 で、clientWidth の比較だけでは直す前も後も同じ（常に見えるスクロールバーを ::-webkit-scrollbar で作っても、
    #   html の溝は overflow:hidden の間に取られない＝2026-09-26 の実測）。だから計算値（stable）を課し、幅の比較は重ねて出す形の
    #   番人として添える。ロックが掛かっていない（開いている間の html の overflow が hidden でない）なら空振りの赤
    if one:
        why = []
        if g1 is None:
            why.append("空振り: モーダルが開いていない")
        else:
            if g1["overflow"] != "hidden":
                why.append(f"空振り: 開いている間の html の overflow-y が {g1['overflow']}（ロックが掛かっていない＝⑱）")
            bad = [f"{when} {g['gutter']}" for when, g in (("開く前", g0), ("開いている間", g1)) if g["gutter"] != "stable"]
            if bad:
                why.append("html の scrollbar-gutter が " + "・".join(bad) + "（スクロールバーが常に見える環境では、ロックで"
                           "スクロールバーが消えて背面の幅が約 15px 変わる）→ ui/cockpit/style.css の 1 列の段で"
                           " [data-ui-style=\"cockpit\"] { scrollbar-gutter: stable }")
            if g0["cw"] != g1["cw"]:
                why.append(f"開く前後で背面の幅（clientWidth）が {g0['cw']}→{g1['cw']}px に変わる")
        check(not why, label_gutter + (f"（{g0['gutter']}→{g1['gutter']}・幅 {g0['cw']}→{g1['cw']}px）" if g1 and not why else ""),
              " / ".join(why))

    # ⑱ スクロールのロック（開いている間）: ヘッダーの無地・暗幕だけの点・モーダルの中心（中を一番下まで寄せてから）で
    #    同じ指の動き → ページも、#modalwrap の外の箱も動かない
    lock_why = []
    moved = []
    if m.get("open"):
        pts = page.evaluate(SWIPE_POINTS)
        s_before = page.evaluate(SCROLL_SNAPSHOT)
        tried = []
        for key, name in (("brand", "ヘッダーの無地の上"), ("scrim", "暗幕の上"), ("modal", "モーダルの中（一番下まで寄せた後）")):
            if not pts.get(key):
                continue
            tried.append(name)
            swipe(page, pts[key], touch)
            s_now = page.evaluate(SCROLL_SNAPSHOT)
            if (s_now["y"], s_now["x"], s_now["els"]) != (s_before["y"], s_before["x"], s_before["els"]):
                moved.append(f"{name}: ページ y {s_before['y']}→{s_now['y']}"
                             + (f"・箱 {sorted(set(s_now['els']) ^ set(s_before['els']))[:3]}"
                                if s_now["els"] != s_before["els"] else ""))
                page.evaluate("() => window.scrollTo(0, 0)")
                s_before = page.evaluate(SCROLL_SNAPSHOT)
        if len(tried) < 2:
            lock_why.append(f"空振り: 指を置く点が {len(tried)} 個（{tried}）＝暗幕の上とモーダルの中の両方を試せていない")
        if moved:
            lock_why.append("開いている間に背面が動いた: " + " / ".join(moved)
                            + f"（html の overflow-y {s_before['rootOverflow']}）→ 1 列の段でモーダルを開いている間は"
                            " html と body の overflow を hidden にする（:has(#modalwrap:not([hidden]))）")
    else:
        lock_why.append("空振り: モーダルが開いていない")
    if one:
        if not pts0.get("brand"):
            lock_why.append("空振り: ヘッダーの無地（.brand）が見えない＝陽性対照を置けない")
        elif not pre or pre["y"] <= 0:
            lock_why.append(f"空振り: 開く前の陽性対照で、同じ指の動きでもページが動かない（y {pre and pre['y']}・ページの丈 "
                            f"{snap0['docH']}・画面の丈 {snap0['vh']}）＝指の動きが届いていない")

    page.keyboard.press("Escape")
    closed = wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#modalwrap')?.hidden"), 1.5)
    if not closed:
        page.evaluate("() => document.querySelector('#modalclose')?.click()")
        closed = wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#modalwrap')?.hidden"), 1.5)
    if not closed:
        check(False, f"⑬ {tag}: モーダルを閉じる", "Escape でも ✕ でも閉じない（後の検査は開いたまま測る）")
    page.evaluate("() => window.scrollTo(0, 0)")
    # ⑱ 閉じた後（1 列）: ロックが外れて、同じ指の動きでページがまた動く（ロックが残るとページが二度と動かない）
    post = None
    if one and closed and pts0.get("brand"):
        swipe(page, pts0["brand"], touch)
        post = page.evaluate(SCROLL_SNAPSHOT)
        if post["y"] <= 0:
            lock_why.append(f"閉じた後も同じ指の動きでページが動かない（y {post['y']}・html の overflow-y {post['rootOverflow']}）"
                            "＝ロックが外れていない → ロックはモーダルが開いている間（#modalwrap が hidden でない間）だけ")
        page.evaluate("() => window.scrollTo(0, 0)")
    check(not lock_why, label_lock + (f"（開く前 y 0→{pre['y']}・開いている間 動かない・閉じた後 y 0→{post['y']}）"
                                      if one and pre and post else "（3 カラム: ページはもとからスクロールしない）"
                                      if not one else ""), " / ".join(lock_why))
    return ua


def settle_frames(page, ms=150):
    """描き直しを 2 フレーム待ってから ms 待つ（窓の幅を変えた後・⤢ を押した後の配り直しを測る前に）"""
    page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
    page.wait_for_timeout(ms)


def check_sheet_modes(page, tag, tap, shape, check, quiet=False):
    """㉕ ㉖ 開いた指示欄（⑩ の「自由に書く」で開いたまま・スクロール 0）で測る。3 カラムの形では窓の幅を SHEET_SWEEP にも
    変えて（丈は形のまま）同じことを見て、最後に形の幅へ戻す。
    quiet=True は ⑩' の ❗ の無い世界（トレイが hidden・台帳の行から開いた指示欄）＝㉖' だけを形の幅で見る（トレイは hidden の
    まま）。❗ が無いときだけ効く hud の :has(.sheet.wide):has(#attn[hidden]) の則（grid-row: 1 / -1・詳細度 (1,6,0)）の番人
    ㉕（3 カラム）: 指示欄が部屋の canvas と重なる幅 ≤ canvas の幅 × ROOM_COVER_MAX・トレイにも重ならない
      （下限 480 では 1181〜1279 で部屋の右の 3 割と台帳の証拠・経過が指示欄の下に入った）
    ㉖（全形状）: ⤢（#sheetwide）を押して .wide にした後、部屋の箱（#stage）と名簿の箱（#cp-roster）の丈・トレイの矩形が
      ⤢ の前と同じ（±1px・名簿の丈 > 0）・トレイの見えているボタンが全部押せて指示欄と重ならない・指示欄が画面の横に収まる。
      R110 骨格 A（指示欄は端末の下・重ねない）: 3 カラムは端末の下の升目（static）・2 カラムは端末の升目の下端（absolute）の
      まま、左右と下端は ⤢ の前と同じで丈だけ上へ広がる（2 カラムは丈が固定＝いつも・3 カラムは丈の上限だけ＝中身が箱に
      入りきっていないとき。広がらない ⤢ は黙った空振り）。1 列では指示欄がもとから中身の丈＝⤢ の前と同じ矩形
      （±1px・hud の ⤢ の余白 16px のままだと「広げる」で 8px 狭まった）。⤢ をもう 1 度押して戻す。
      hud の ⤢ の則（relative・grid-row: 2）のままだと 3 カラムの左の列の 2 行目に流れ込み、1440・1920 では台帳の丈が 0、
      1181〜1279 では部屋の箱が 50px に潰れた。トレイも hud の :has(.sheet.wide) #attn（余白 16px・丈の上限 28vh）で形が変わった"""
    _, w, h = shape[:3]
    base = page.evaluate(SHEET_LAYOUT)
    three = base["cols"] == 3
    widths = [w] + ([x for x in SHEET_SWEEP if x != w] if three and not quiet else [])
    cover_bad, cover_seen, wide_bad, wide_seen = [], [], [], []
    try:
        for vw in widths:
            if vw != w:
                page.set_viewport_size({"width": vw, "height": h})
                settle_frames(page, 250)
            n = page.evaluate(SHEET_LAYOUT)
            at = f"{vw}×{h}"
            if not n["open"] or n["wide"]:
                wide_bad.append(f"{at}: 空振り: ⤢ の前の指示欄が{'開いていない' if not n['open'] else '既に ⤢ になっている'}")
                continue
            if three and not quiet:
                cap = n["canvasW"] * ROOM_COVER_MAX
                cover_seen.append(f"{vw}: {n['roomCover']}px")
                if not n["canvasW"]:
                    cover_bad.append(f"{at}: 空振り: 部屋の canvas（.cproom）が見えない")
                elif n["roomCover"] > cap:
                    cover_bad.append(f"{at}: 指示欄（{n['sheetTxt']}・幅 {n['sheetW']}）が部屋の canvas（{n['canvasTxt']}）と"
                                     f" {n['roomCover']}px 重なる（上限 {cap:.0f}px＝canvas の幅 {n['canvasW']} × {ROOM_COVER_MAX}）")
                if n["trayLap"] or not all(b["ok"] for b in n["btns"]):
                    cover_bad.append(f"{at}: 指示欄（{n['sheetTxt']}）がトレイ（{n['trayTxt']}）に重なるか、押せないボタンがある")
            wp = page.evaluate(BUTTON_POINT, "#sheetwide")
            if not wp["ok"]:
                wide_bad.append(f"{at}: ⤢（#sheetwide）を押せない（{wp['why']}）")
                continue
            tap(wp["x"], wp["y"])
            if not wait_until(page, lambda: page.evaluate(
                    "() => !!document.querySelector('#sheet')?.classList.contains('wide')"), 2):
                wide_bad.append(f"{at}: ⤢ を押しても指示欄が .wide にならない")
                continue
            settle_frames(page)
            m = page.evaluate(SHEET_LAYOUT)
            why = []
            if not m["open"]:
                why.append("⤢ で指示欄が閉じた・見えない")
            else:
                if abs(m["stageH"] - n["stageH"]) > 1:
                    why.append(f"部屋の箱の丈が {n['stageH']}→{m['stageH']}px")
                if abs(m["tableH"] - n["tableH"]) > 1 or m["tableH"] <= 0:
                    why.append(f"名簿の箱（#cp-roster）の丈が {n['tableH']}→{m['tableH']}px")
                # R110 骨格 A: トレイは左の列の 04 受付＝どの段でも見えている（2 カラムの一覧タブの特例は撤去）
                if quiet:
                    if n["tray"] or m["tray"]:
                        why.append("空振り: ❗ の無い世界なのにトレイ（#attn）が見える")
                elif not n["tray"] or not m["tray"]:
                    why.append("空振り: トレイ（#attn）が見えない")
                elif any(abs(a - b) > 1 for a, b in zip(n["tray"], m["tray"])):
                    why.append(f"トレイの矩形が {n['trayTxt']}→{m['trayTxt']}")
                if m["trayLap"]:
                    why.append(f"指示欄（{m['sheetTxt']}）がトレイ（{m['trayTxt']}）に重なる")
                bad = [b for b in m["btns"] if not b["ok"]]
                if not quiet and (bad or not m["btns"]):
                    why.append("押せないトレイのボタン: " + (" / ".join(f"「{b['label']}」に {b['over']}" for b in bad[:3])
                                                         if bad else "空振り（ボタンが 0 個）"))
                if m["sheet"][0] < -EDGE_TOL or m["sheet"][2] > m["vw"] + EDGE_TOL:
                    why.append(f"指示欄（{m['sheetTxt']}）が画面の横の外（画面の幅 {m['vw']}）")
                # R110 骨格 A: 指示欄は端末の下（重ねない）。⤢ は丈を広げる（幅は端末の列のまま・下端は動かず上へ伸びる）。
                #   3 カラム＝端末の下の升目（grid-area: sheet・static・丈の上限 45dvh→70dvh＝中身が入りきらないときだけ伸びる）
                #   2 カラム＝端末の升目の下端に absolute（丈 45dvh→70dvh＝いつも伸びる・端末の窓はその丈だけ持ち上がる）
                #   1 列＝受付の直後（もとから中身の丈・広げる余地は無い＝⤢ の前と同じ矩形）
                want_pos = {3: "static", 2: "absolute"}.get(m["cols"])
                grow = m["cols"] == 2 or (m["cols"] == 3 and n["sheetMore"])
                if want_pos and m["pos"] != want_pos:
                    why.append(f"{m['cols']} カラムで ⤢ の指示欄が position {m['pos']}（{want_pos} のはず・{m['sheetTxt']}）")
                elif m["cols"] > 1 and any(abs(m["sheet"][i] - n["sheet"][i]) > 1 for i in (0, 2, 3)):
                    why.append(f"{m['cols']} カラムで ⤢ を押すと指示欄の左右か下端が動く（{n['sheetTxt']}→{m['sheetTxt']}・"
                               "⤢ は丈だけを上へ広げる＝幅は端末の列のまま）")
                elif m["sheetH"] < n["sheetH"] - 1:
                    why.append(f"⤢ を押すと指示欄が {n['sheetH']}→{m['sheetH']}px に縮む")
                elif grow and m["sheetH"] <= n["sheetH"] + 1:
                    why.append(f"{m['cols']} カラムで ⤢ を押しても指示欄の丈が広がらない（{n['sheetH']}→{m['sheetH']}px"
                               + ("・中身は箱に入りきっていない" if n["sheetMore"] else "") + "）")
                elif m["cols"] == 1 and any(abs(a - b) > 1 for a, b in zip(n["sheet"], m["sheet"])):
                    why.append(f"1 列で ⤢ を押すと指示欄が {n['sheetTxt']}→{m['sheetTxt']} に変わる（広げる余地は無い＝"
                               "⤢ の前と同じ置き方のはず）")
            if why:
                wide_bad.append(f"{at}: " + "・".join(why))
            else:
                wide_seen.append(f"{vw}: 丈 {n['sheetH']}→{m['sheetH']}")
            # ⤢ を戻す（戻さないと ⑩' の指示欄が ⤢ のまま開く＝localStorage に残る）
            wp = page.evaluate(BUTTON_POINT, "#sheetwide")
            if wp["ok"]:
                tap(wp["x"], wp["y"])
            if not wait_until(page, lambda: page.evaluate(
                    "() => !document.querySelector('#sheet')?.classList.contains('wide')"), 2):
                page.evaluate("() => document.querySelector('#sheetwide')?.click()")
                wide_bad.append(f"{at}: ⤢ をもう 1 度押しても元の幅に戻らない")
    finally:
        if page.viewport_size != {"width": w, "height": h}:
            page.set_viewport_size({"width": w, "height": h})
            settle_frames(page, 250)
        if page.evaluate("() => !!document.querySelector('#sheet')?.classList.contains('wide')"):
            page.evaluate("() => document.querySelector('#sheetwide')?.click()")
        page.evaluate("() => window.scrollTo(0, 0)")
    if three and not quiet:
        check(not cover_bad, f"㉕ {tag}: 3 カラムで指示欄が部屋の canvas と重なる幅が canvas の {ROOM_COVER_MAX:.0%} 以下・"
              f"トレイにも重ならない（窓の幅 {' / '.join(cover_seen)}）", " / ".join(cover_bad)
              + " → ui/cockpit/style.css の 3 カラムの段の指示欄の幅（clamp の下限 480＝R95・中央の列に合わせる幅＝画面 − 916px）"
              "と置き方（右カラムの左で止める）を見直す。下限を 480 より下げるのは本人の裁定")
    cols = base["cols"]
    check(not wide_bad, (f"㉖' {tag}: ❗ の無い世界でも" if quiet else f"㉖ {tag}:")
          + " ⤢ で広げても部屋の箱・名簿の箱" + ("（トレイは hidden のまま）は ⤢ の前のまま" if quiet else "・トレイは ⤢ の前のまま、トレイは押せる")
          + ("・3 カラムは端末の下の升目（static）で丈だけ上へ広がる" if cols == 3 else
             "・2 カラムは端末の升目の下端（absolute）で丈だけ上へ広がる" if cols == 2 else "・1 列は ⤢ の前と同じ矩形")
          + (f"（{' / '.join(wide_seen)}）" if not wide_bad else ""),
          " / ".join(wide_bad) + " → ui/cockpit/style.css で ⤢（#sheet.wide）は丈の上限だけを広げる（3 カラム＝grid-area: sheet の"
          " max-height・2 カラム＝height と端末の窓の margin-bottom・❗ の無いときの :has(#attn[hidden]) #sheet.wide も同じ）、"
          "1 列は ⤢ の前と同じ余白に、トレイは .sheet-open:has(#sheet.wide) #attn.tray で流し込みの則（static・余白 0）へ戻す")


def check_quiet_sheet(page, tag, tap, world, office, sheet_ref, check, shape):
    """⑩' ❗ の無い世界で名簿の行を押す → 部屋の箱の丈が変わらない・指示欄は ⑩（❗ がある形）と同じ置き方（R110 骨格 A:
    3 カラム＝端末の下の升目に static・左右と下端が sheet_ref と ±1px／2 カラム＝端末の升目の下端に absolute・矩形全体が ±1px）・
    今のスクロール位置で見える。
    /api/office の応答も ❗ の無い世界に差し替える（3 秒ごとのポーリングが ❗ のある固定ワールドを入れ直すと、測る間に
    トレイが戻って :has(#attn[hidden]) の形を測れない＝空振り）。測り終えたら戻す"""
    label = (f"⑩' {tag}: ❗ の無い世界で名簿の行を押して指示欄を開いても、部屋の箱の丈が変わらず"
             "（多段では指示欄が端末の下に ❗ があるときと同じ置き方）、指示欄が今のスクロール位置で見える")
    quiet_world = without_attention(world)
    kept = office["body"]
    office["body"] = json.dumps(quiet_world, ensure_ascii=False)
    try:
        _quiet_sheet(page, tag, tap, quiet_world, label, sheet_ref, check, shape)
    finally:
        office["body"] = kept


def _quiet_sheet(page, tag, tap, quiet_world, label, sheet_ref, check, shape):
    page.evaluate("w => window.__office.inject(w)", quiet_world)
    quiet = wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#attn')?.hidden"), 3)
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    st0 = page.evaluate("() => Math.round(document.querySelector('.cpstage')?.getBoundingClientRect().height || 0)")
    rp = page.evaluate(ROW_POINT)
    if not quiet or not rp["ok"]:
        check(False, label, "空振り: " + ("❗ を消した世界でもトレイ（#attn）が hidden にならない" if not quiet else rp["why"]))
        return
    tap(rp["x"], rp["y"])
    wait_until(page, lambda: page.evaluate("() => !document.querySelector('#sheet')?.hidden"), 2)
    page.wait_for_timeout(250)
    wait_until(page, lambda: page.evaluate(SHEET_SETTLED), 1.5)
    sh = page.evaluate(SHEET_NOW)
    why = []
    if not sh["open"]:
        why.append(f"行（「{rp['name']}」）を押しても指示欄（#sheet）が開かない（選択 {sh['selected']}）")
    elif not sh["attnHidden"]:
        why.append("空振り: 指示欄を開いて測る時点でトレイ（#attn）が出ている＝❗ の無い世界を保てていない")
    else:
        if sh["selected"] != rp["session"]:
            why.append(f"選択が {sh['selected']}（押した行は {rp['session']}）")
        if abs(sh["stageH"] - st0) > 1:
            why.append(f"部屋の箱が {st0}→{sh['stageH']}px（#sheet は position {sh['pos']}・grid-row {sh['row']}）"
                       + ("＝hud の @media (max-width: 1279px) の .sheet-open:has(#attn[hidden]) .sheet（grid-row: 1 / -1）が"
                          ".main の列に流し込む" if sh["cols"] > 1 else ""))
        # R110 骨格 A: 指示欄は端末の下（3 カラム＝端末の下の升目に static・丈は中身まで＝上端は中身で変わる／
        # 2 カラム＝端末の升目の下端に absolute・丈は固定）。❗ の有無で置き方が変わらない＝3 カラムは左右と下端、2 カラムは矩形全体
        want_pos = {3: "static", 2: "absolute"}.get(sh["cols"])
        keys = ("left", "right", "bottom") if sh["cols"] == 3 else ("top", "bottom", "left", "right")
        if want_pos and sh["pos"] != want_pos:
            why.append(f"{sh['cols']} カラムで指示欄が position {sh['pos']}（{want_pos} で端末の下に置くはず）")
        elif want_pos and sheet_ref and any(abs(sh["rect"][k] - sheet_ref[k]) > 1 for k in keys):
            why.append(f"{sh['cols']} カラムで ❗ の有無により指示欄の置き方が変わる（❗ あり {sheet_ref}・❗ 無し {sh['rect']}・"
                       f"比べた辺 {keys}）＝hud の :has(#attn[hidden]) の則（grid-row: 1 / -1・margin-top: 12px）が cockpit の"
                       "置き方に勝っている")
        if sh["visH"] < SHEET_MIN:
            why.append(f"今のスクロール位置（{sh['scrollY']}）で指示欄の見えている丈が {sh['visH']}px（{SHEET_MIN}px 以上）")
    check(not why, label + (f"（{sh['cols']} 列・部屋の箱 {st0}→{sh['stageH']}px・position {sh['pos']}・見えている丈 {sh['visH']}px）"
                            if sh["open"] else ""),
          " / ".join(why) + " → ui/cockpit/style.css の 3 カラムの段（1 列の段の補集合）で、❗ が無いときのセレクタ"
          " .ui-cockpit .sheet-open:has(#attn[hidden]) #sheet も ❗ があるときと同じ置き方（absolute・top/right/bottom"
          " 16px・grid-row auto・margin 0）に揃える（1 列は .sheet-open #stage の height: auto）")
    # ㉖' ❗ の無い世界でも ⤢ の置き方（hud の :has(.sheet.wide):has(#attn[hidden]) の則に負けない）
    if sh["open"] and sh["attnHidden"]:
        check_sheet_modes(page, tag, tap, shape, check, quiet=True)
    page.keyboard.press("Escape")
    closed = wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#sheet')?.hidden"), 1.5)
    if not closed:
        page.evaluate("() => document.querySelector('#sheetclose')?.click()")
        closed = wait_until(page, lambda: page.evaluate("() => !!document.querySelector('#sheet')?.hidden"), 1.5)
    if not closed:
        check(False, f"⑩' {tag}: 指示欄を閉じる", "Escape でも ✕ でも閉じない")


def without_attention(world):
    """⑩' ❗ の無い世界の写し（全員の attention・question・ask・承認まちを落とし、待ちの状態を idle に）"""
    out = json.loads(json.dumps(world))
    for key in ("roster", "employees"):
        for p in out.get(key) or []:
            p.update({"attention": False, "question": "", "ask": None, "stuckTool": "", "approvalMin": 0})
            p.pop("questionOptions", None)
            if p.get("state") == "waiting":
                p["state"] = "idle"
            for s in p.get("sessions") or []:
                s["attention"] = False
                s.pop("ask", None)
                if s.get("state") == "waiting":
                    s["state"] = "idle"
    return out


def with_long_names(world):
    """③'' 全員に長い title（/rename の名前）を付けた写し。名前は人ごとに末尾だけ変える"""
    out = json.loads(json.dumps(world))
    for key in ("roster", "employees"):
        for i, p in enumerate(out.get(key) or []):
            p["title"] = LONG_NAME[:LONG_N - 2] + f"{i:02d}"
    return out


def run_demo(browser, port, shape, demo_world, check):
    """デモ（?demo=1）の 1 形状ぶん: ⑨（畳む・理由・残す）と ⓪。/api は静的ホストと同じく全部 404（叩いたら記録）"""
    name, w, h, dsf, want_lang = shape
    tag = f"{name} {w}×{h}"
    api = []

    def no_server(route):
        api.append(urlparse(route.request.url).path)
        return route.fulfill(status=404, content_type="text/plain", body="the demo has no server")

    ctx, page, errors = open_page(browser, port, (name, w, h, True, dsf), "&demo=1", no_server)
    try:
        page.wait_for_function("() => window.__office.stats().frames > 0", timeout=10000)
        wait_until(page, lambda: page.evaluate("() => (window.__office.debug.world()?.agents || []).length > 0"), 8)
        if want_lang == "ja":                            # 設定で言語を替えたのと同じ（同梱の world の lang だけ替える）
            page.evaluate("w => window.__office.inject(w)", {**demo_world, "lang": "ja"})
        wait_until(page, lambda: page.evaluate(
            "async () => (await import('/ui/hud/strings.js')).lang()") == want_lang, 4)
        page.evaluate("() => document.fonts.ready")
        page.wait_for_timeout(400)
        print(f"  ─ {tag}（デモ・タッチ・dsf {dsf}・{want_lang}）")
        d = page.evaluate(TERM_DEMO)
        vacuous = []
        if d["agents"] < 1:
            vacuous.append("空振り: デモの world が入っていない（部屋の人 0）")
        if d["lang"] != want_lang:
            vacuous.append(f"空振り: 言語が {d['lang']}（{want_lang} で見るはず）")

        term_api = [p for p in api if p.startswith("/api/term")]
        why = list(vacuous)
        if d["box"]:
            why.append("#cp-xterm が出ている")
        if d["xterms"]:
            why.append(f"#cp-xterm の中に見える xterm が {d['xterms']} 個")
        if d["tabsRow"] or d["tabBtns"]:
            why.append(f"タブ列（#cp-tabs）が出ている（見えるタブ {d['tabBtns']} 個）")
        if d["plus"]:
            why.append("＋ 端末（#cp-term-new）が見える")
        if term_api:
            why.append(f"デモなのに端末の API を叩いた（{sorted(set(term_api))}）")
        check(not why, f"⑨ {tag}: デモでは端末の画面を畳む（#cp-xterm・タブ列・＋ が見えない・端末の API を叩かない）",
              " / ".join(why) + " → DEMO（?demo=1・meta office-demo）のときだけ畳む（幅・タッチでは畳まない）")

        want_text = (f"「{DEMO_REASON_JA}」" if want_lang == "ja" else "demo と Mac を含む文")
        why = list(vacuous)
        if not d["noteShown"]:
            why.append(f"足元（#cp-term-note）が見えない（中身「{d['note']}」）")
        elif not is_demo_reason(d["note"], d["lang"]):
            why.append(f"足元が「{d['note']}」（期待は {want_text}）")
        if d["note"] in (d["readonly"], d["live"]) or d["readonlyInCenter"]:
            why.append(f"デモで ＋ から起こす案内か「打てます」を出している（「{d['readonly'] if d['readonlyInCenter'] else d['note']}」）")
        check(not why, f"⑨ {tag}: デモの足元（#cp-term-note）に理由を書く（{want_text}・＋ の案内は出さない）",
              " / ".join(why) + f" → DEMO のとき #cp-term-note に理由（ui/hud/strings.js に ja/en のキー）を書く")

        why = list(vacuous)
        if not d["selAsked"]:
            why.append(f"空振り: 選ばれている人（{d['sel']}）が ❗ でない＝許可待ちの表示を確かめられない")
        if not d["work"]:
            why.append("#cp-work が見えない（端末と一緒に畳んだ？）")
        elif d["selAsked"] and not d["cpq"]:
            why.append(f"#cp-work・#cp-dlg に ❗ の問い（.cpq）が無い（要約「{d['workText']}」）")
        if not d["dlg"]:
            why.append("#cp-dlg の箱が消されている（display:none）")
        check(not why, f"⑨ {tag}: デモでも許可待ちの表示は畳まない（#cp-work の ❗・#cp-dlg）",
              " / ".join(why) + " → 畳むのは #cp-xterm とタブ列だけ（#cp-term ごと畳まない）")

        check(not errors, f"⓪ {tag}: console.error / pageerror = 0", "; ".join(errors[:3]))
    finally:
        ctx.close()


def broke(check, tag, err):
    """形状の途中で例外（読み込み待ちの TimeoutError など）→ その形状を 1 件の赤にして、次の形状へ進む"""
    kind = type(err).__name__
    first = (str(err).strip().splitlines() or [""])[0][:160]
    load = os.getloadavg()[0]
    check(False, f"{tag}: {'読み込みが終わらない' if 'Timeout' in kind else '途中で止まった'}（この形の残りは測れていない）",
          f"{kind}: {first}・負荷平均 {load:.0f} → 先に uptime を見る（高負荷なら落ち着いてから再実行。"
          "低負荷でも再現するならコードを疑う）")


def main():
    from playwright.sync_api import sync_playwright

    t0 = time.monotonic()
    world = json.loads(WORLD.read_text(encoding="utf-8"))
    demo_world = json.loads(DEMO_WORLD.read_text(encoding="utf-8"))
    ng = 0

    def check(ok, label, detail=""):
        nonlocal ng
        print(f"  {'✓' if ok else '✗'} {label}{'' if ok else f' — {detail}'}")
        if not ok:
            ng += 1

    print(f"  負荷平均 {os.getloadavg()[0]:.1f}（高いと読み込み待ちが延びる）")
    port = free_port()
    proc = start_server(port)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=SWIFTSHADER)
            check_r105_layout(browser, port, world, check)
            for shape in SHAPES:
                s0 = time.monotonic()
                try:
                    run_shape(browser, port, shape, world, check)
                except Exception as err:  # noqa: BLE001 — 形状ごとに赤にして続ける
                    broke(check, f"{shape[0]} {shape[1]}×{shape[2]}", err)
                print(f"    （{time.monotonic() - s0:.1f} 秒）")
            for shape in DEMO_SHAPES:
                s0 = time.monotonic()
                try:
                    run_demo(browser, port, shape, demo_world, check)
                except Exception as err:  # noqa: BLE001
                    broke(check, f"{shape[0]} {shape[1]}×{shape[2]}", err)
                print(f"    （{time.monotonic() - s0:.1f} 秒）")
            browser.close()
    finally:
        proc.terminate()

    took = time.monotonic() - t0
    print(f"✅ cockpit 形状の門 green（{took:.1f} 秒）" if not ng
          else f"❌ cockpit 形状の門 {ng} 件（{took:.1f} 秒）")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
