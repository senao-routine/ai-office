// R90-P2: 方向Cへの意図的な変更後の配信バイト列を固定する。
// 更新: python3 tools/gen_pwa_modules.py → APP_HTMLのUTF-8 SHA-256/byteLengthを
// node:crypto/node:bufferで取得 → 下の2定数をレビューして手で更新 → このKATを実行。
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { APP_HTML, APP_VER } from "../relay/src/app_html.js";
import { BUILD, MODULES, ASSETS } from "../relay/src/modules_data.js";
import { BUILD as BUILD3, MODULES as MODULES3 } from "../relay/src/modules_3d.js";   // R122-P3: 3D の部屋の別の束（初回に 3D を出すときだけ読む）

const EXPECT_SHA256 = "7832ba88f88e5b9025bc56e3eb75599b6656f1776d0efe4eee3827ac7a86b470";
// 2026-09-10 (R87-H1): 設定シートに「🔐 暗号セルフテスト」を1行と、その実行関数を追加（+2,532B）。
// 固定ベクタ(約11KB・base64は圧縮が効かない)は**シェルに入れず** ui/core/dialog_kat.js へ置き、
// 遅延読み込みの modules_data.js 側へ載せた（起動のたびに払う転送量にしない）。
// 2026-09-11: 封書の入口を 3D から切り離し、必要になった時だけ読む ensureDlg() を追加
// （リスト表示を保存した端末では boot3d.js が読まれず、暗号が正常でも詰んでいた）。
// 2026-09-14 (R87-S5): シートに「💬 会話を見る」と会話ブロック #shdlg、封書の受け口 onDlg/requestDialog、
// 設定シートに正直な安全性の注記を追加（+6140B）。平文はメモリ DLG_PAGES だけ。
// 2026-09-14 (R87-S6 別モデルレビュー): 失敗表示を要求元セッションに限定（dlgFail）・完了判定を reqId に・
// 期限 e を端末でも検算（openBlob に nowSec）。既に開いた会話は残して下に添える（+517B）。
// 2026-09-14 (R87-S6 再レビュー): ボタンは封じられる Claude セッションだけ（canDlgFor）・最新の要求だけが表示と
// キャッシュを更新（DLG_LATEST）・期限は認証済み iat＋90 秒で検算（+665B）。
// 2026-09-14 (R87-S6 3 回目): 「最新の要求か」は復号の完了時に取り直す・60 秒タイマーも最新の要求だけ（+159B）。
// 2026-09-14 (R87-S6 4 回目): 「最新の要求か」は要求時刻 at で判定（再読込後も保留要求から復元）・認証失効で本文 DOM も消し
// シートを閉じ、進行中の復号は世代（DLG_EPOCH）で捨てる（+959B）。
// 2026-09-14 (R87-S6 5 回目): 「最新の結果」は成功・失敗・未着で同じ時刻台帳 DLG_AT に記録（+119B）。
// 2026-09-14 (R87-S6 6 回目): 送信失敗の表示にも最新要求＋認証世代の判定（+148B）。
// 2026-09-14 (R87-S6 7 回目): 保留要求は sessionStorage（タブごと）＝別タブが横取りしない（+328B）。
// 2026-09-14 (R93 着地): R87 の app.js/app.html に R93-V1' の HUD トークン（白×ラベンダー×ガラス）を再生成で合流（+354B）。
// 2026-09-14 (R93-P1'): theme-color を #f4f4fb へ・設定のテーマ説明を「白×ラベンダーのガラス面」へ（+61B）。
// 2026-09-15 (R96): 配達5状態・❗1件巡回・Mac許可の手がかり・通知sessionの深リンク・
// 日次集計の正本化と名札密度を追加し、動かないテーマ行を撤去（+4,215B）。
// キャッシュから深リンクを開く前に会話状態も初期化するため、boot() は末尾へ移動。
// 2026-09-15 (R96 着地): 通知の深リンクはキャッシュでは開かず最新の受信で開く（openAttnLink(false) 撤去・-20B）。
// 2026-09-25 (R99-P4-5): ExitPlanMode（計画の承認）をスマホで「✅ 承認」に見せない（中継からは deny＋言葉にしかならない）。
// 許可と同じく承認ボタンを外し「承認は Mac で」と待っている事実の 1 行を出す（isPlanApproval・+1,098B）。
// 2026-09-25 (R99-P4-5 第 C 波): 計画の承認はシート・リストのカード・ログでも「❓ 質問待ち」と質問の箱を出さず、
// ❗ドックと同じ事実の行（planLine・ここから送った言葉は計画への修正の指示）に揃える。折りたたみ 890×626 など
// 横長で低い画面では ❗ カードの付属行（配達チップ・detail）を畳み、選択肢を横に並べて箱の中へ収める（+1,711B）。
// 2026-09-25 (R99-P4-5 第 D 波): 横長で低い画面で横に並べた ❗ の選択肢は、ラベルを 2 行まで折り返す（1 行で「…」に切ると
// 前半が同じ選択肢が見分けられない。答えはタップ 1 回で送られ取り消せない）（+525B）。
// 2026-09-25 (R99-P4-5 第 F 波): 横長で低い画面で「次へ ▸」で送った先の ❗ の本人の配達状態が画面のどこにも出なかった
// （ロスター帯が本人のチップまで送られない）。❗ カード・ロスター帯を描くたびと画面の形が変わるたびに、本人のチップ（鍵は session）が
// 帯に全部入るよう scrollLeft だけを動かす（revealAttnChip）。指で帯を動かした後は同じ ❗ の間は奪わない（holdRoster）（+1,483B）。
// 2026-09-25 (R99-P4-5 第 F 波の検収): ❗ が 0 件になっても指の保持が外れず、同じ人が再び ❗ を出すと帯を送らなかった。
// ❗ が無くなったら保持を外す（+35B）。
// 2026-09-25 (R99-P4-5 別モデルレビュー): 横長で低い画面の ❗ の選択肢は横並びをやめ 2 列の格子・ラベルは全文折り返し（別モデルレビューの指摘）。「次へ ▸」はカードの頭の右へ重ねる（+1,005B）。
// 2026-09-25 (R99-P4-6): 縦のスマホ（390）でも ❗ の選択肢のラベルは全文折り返し（約 27 字で「…」＝末尾の「残す／消す」が読めず 4 件目は箱の外）。detail を畳み種別を名前の行へ。入らないときだけドックをロスター帯の下端まで伸ばし、箱の下端で半分に切れる「ほか N 件」は隠す（fitAttnDock）（+3,626B）。
// 2026-09-25 (R99-P4-7・統括の裁定＝選択肢が切れたり一部が隠れたりした状態を全部に見える形で出さない): ドックを伸ばし切っても
// 選択肢が全部は入らない画面（375×667 で 40 字 × 4 件 等）は、カードに選択肢を出さず質問と「選択肢 N 件を開く」（シートを選択肢の
// 位置で開く＝sheetToOptions）に差し替える（選択肢は隠さず DOM から外す・fitAttnDock が描いた後の実測で判定・#infodock の丈の
// 変化でも測り直す）。シートと実行／起動の一覧の選択肢のラベルも全文折り返し（シートは約 27 字で「…」だった）（+3,350B）。
// 2026-09-25 (R99-P4-7 の検収): 「開く」から開いたシートは最後の選択肢まで入るかで送る位置を決める（1 件目だけで決めると
// 残りが見た目の上で消えた）・別の ❗ に替わったらドックの箱のスクロールを先頭へ・選択肢のある質問のシートには汎用の
// 承認・停止・報告を出さない（❗ドックの R86-H と同じ規則）（+416B）。
// 2026-09-25 (R99-P4 別モデルレビュー): 横長のカードの種別の列に幅の上限（14em・「…」）。長い MCP のツール名が列を押し広げ、
// 568px 級の横向きで右端の操作ボタンが切れた（種別は説明であって選択肢ではない＝省略してよい）（+295B）。
// 2026-09-26 (R99-P4-8): スマホの横向き（高さ 500 以下＝844×390・667×375）は ❗ の間、下の帯（ティッカー・ゲージ帯）を畳み、
// 「選択肢 N 件を開く」と「✍️ 自由に」を 1 行に並べ、タブを 1 段に詰める（ヘッダー・ロスター帯・下の帯・タブに挟まれて ❗ の箱が
// 60〜77px しか無く、質問も操作も 1 つも見えなかった）。fitAttnDock の上限を符号つきに（ドックがロスター帯に重なるなら縮める）・
// カードの操作（自由に・開く・汎用の操作）は箱の下端で半分に切れて覗かない＝見えるなら全部・見えないなら全部隠す
// （360×780 の長いラベル 4 件で「✍️ 自由に」が半分に切れていた）（+2,163B）。
// 2026-09-26 (R99-P4-9): 高さ 340 未満の横向き（568×320＝SE 第 1 世代のホーム画面・667×325＝SE 第 2/3 世代の Safari）は ❗ の間、
// ロスター帯を 1 行（44px・名前と配達状態を横に並べる）に詰め、質問を 1 行（「…」）にする（❗ の箱が 69〜87px しか無く、
// 「開く／自由に」・許可待ち・計画の承認の操作の行が丸ごと箱の下に隠れ、押せる操作が 1 つも見えなかった）。fitAttnDock の
// 「見えるなら全部・見えないなら全部隠す」に「📨 送信済み」の行も入れる（操作の行を箱に入れた分、その下で上半分だけ覗いた）（+1,590B）。
// 2026-09-26 (R99-P4-7): ホーム画面のアプリとして名乗る。apple-touch-icon を絵文字の SVG から PNG（/app/icon-180.png・コードで描いた
// claude の idle0）へ・rel=icon を足す・meta theme-color を明／暗の media 付き 2 本（どちらもヘッダーの地 #f4f4fb）へ・
// applyPrefs の theme-color 上書き（色の 3 つ目の写し）を撤去・設定の「AI Office PWA v5」を Mac 側の版（office_json.app.version）へ（+219B）。
// 2026-09-26 (R99-P4-9・統括の裁定): 詰める段（ロスター帯を 1 行・質問を 1 行）を高さのしきい値（横向きの 340 未満）から中身で決める形へ。
// fitAttnDock が詰めずに測り、押せる操作（選択肢・「開く」・汎用の操作の行）が 1 つも箱に入らないときだけ #dock に .attn-tight を立てて
// 測り直す（しきい値のすぐ上＝568×340 の計画の承認・852×343 の 2 行の質問・英語の 740×345 で、押せる操作が 1 つも無い形が残っていた）。
// 設定の版の表示は PWA 自身の版（「AI Office PWA v5」）へ戻す（R99-P4-7 の Mac 側の版の表示は撤回＝版は中継に載せない・R97-D）。詰めない形で測る間に切り詰められたロスター帯の
// scrollLeft は測る前の位置へ戻す（指で送った帯を受信のたびに奪わない）（+1,978B）。
// 2026-09-26 (R99-P4-9 の検収): 詰めた形（.attn-tight）では質問が 1 行で切れる＝選択肢は必ず「選択肢 N 件を開く」へ畳む
// （問いの後半を読まずに取り消せない 1 タップを送らせない・安全上の約束）（+9B）。
// 2026-09-26 (R99-P4-6・本人裁定 A): 部屋を 3D からドット絵の俯瞰（ui/cockpit/room.js）へ。起動部品を boot3d.js → bootpx.js・
// 文言の「3D表示」を「部屋の表示」へ・canvas を画面いっぱいに引き伸ばす CSS を整数倍の実寸で置く形へ（+359B）。
// 同日（検収のレビュー）: 名札の幅を部屋の机の間隔に合わせる（plateW）・受付の 2 人目以降の ❗ はピン（pinCalls）・
// 選択中の人の近くの空振りを 2 度目とみなす範囲を部屋の縮尺に（rescuePx）（+153B）。ピンは識別バッジ（1〜3 文字・R86-I）の
// 文字数に合わせて伸ばす（20px 固定では「E2E」がはみ出した）（+54B）。
// 2026-09-28 (R99-P4-L1・本人「しっかりアバターが動くように」): 設定シートに「🤖 ロボの動き（端末の設定／動かす／止める）」
//（iPhone の「視差効果を減らす」が ON だと部屋が丸ごと止まり、外す口がスマホに無かった・localStorage aioffice.motion を bootpx.js が読む）（+1,600B）。
// 同日（R99-P4-L2 の検収のレビュー）: 選んだ値を控え（MOTION）に持ち、イベントに載せて bootpx.js へ渡す（localStorage に書けない
// 端末・プライベートモードでも、その場で効く）（+82B）。
// 2026-09-28 (R99-P4-L4・本人「アバターが表示されなくなっている」): 狭い名札（ドット絵の ×2）はレベルを外して名前を残す・
// 縦のスマホは ❗ の間ティッカーを畳む（部屋がロスター帯と ❗ドックに挟まれ、机の人がロスター帯の裏に隠れていた）。
// (R99-P4-L5・本人「使用量が減っていく方・残り何%の表示に」): ゲージ帯・リソースのシート・設定の数字を「残り N%」へ
//（バーの長さも残り・色は残り 30% 以下で注意／10% 以下で危険・「残り」は数字の前に小さく・ゲージ帯の見出しは折り返さない）（+2,267B）。
// 2026-09-29 (R101＋R100-1a・本人「ターミナル×ドット絵に」「ダーク/ライト」「オフィスとセッション列が被る」「ターミナル画面」):
// 色を端末の配色（palette.js の TERM → --t-*）へ・ドット絵のアイコン（icons.css を中へ）・テーマの早期適用（head のスクリプト）と
// meta 2 本・プロンプト行とカーソル・部屋の窓（#officewin）＝ロスター帯は窓の外の行・❗ドックは窓の下部に重ねる（横の分割は
// ❗ の裁定に合わず外した）・ターミナル画面（#termwrap・封書の term）（+33,216 B）。
// 2026-09-29 (R100-1b・本人「スマホからも許可を出せるように」): 許可の確認の窓（#apvwrap・全キーを textContent・
// 押す瞬間に画面の文字列から SHA-256 を作り直す・パスキーで署名）と設定の「スマホから許可」（登録リンクの貼り付け）（+23,281B）。
// 別モデルレビュー（同日）: 署名に封じた時刻（iat）を入れる・一時的な拒否（続けすぎ・時間切れ）の後にボタンを戻す／取り直す（+313B）。
// 別モデルレビュー 2 回目: 再試行のタイマーを同じ許可画面・処理中でないときだけに（別の画面のボタンを戻さない・二重の Face ID を止める）（+139B）。
// 別モデルレビュー 3 回目: 処理中に届いた一時的な拒否の再試行を捨てずに待つ（apvLater）（+410B）。
// 2026-09-29 夜（本人「スマホから送っても Mac に届かない」）: 端末の登録の期限切れを自分で言う（上の案内・送る操作を止める・
// 3 日前の予告）（+2677B）。
// 同夜（本人「スマホのエージェント画面が表示されなくなっている」）: 縦画面の ❗ドックは部屋の帯（150px・詰める段 96px）を残す（+896B）。
// 2026-09-30（R102-1・本人裁定「部屋を主役に残し、あなた待ちは部屋に重ねず上下に分ける」「質問は 1 タップ＋2 秒の取り消し」）:
// ❗ドックの重ね合わせと測り直し（attnFitPass）を撤去し、部屋の窓の下の別の箱へ・右上は状態の語のチップ・あなた待ちのカードを
// 作り直し（状態の札・番号つきの選択肢・推奨は語の札・2 秒の取り消し）・ロスター帯を 1 行・表示の保存はタブだけ・登録の期限は 1 行・
// 古い版の読み直し（data-build＝APP_VER と status の shell）（+5,250B）。
// 2026-09-30（R102-2・本人裁定「危険なもの以外は全部」）: 許可の窓に review の段（警告のタグを言葉で・全文を最後まで読むと押せる
// 「内容を確認した」・印が無いと許可を送らない）・Mac だけの理由を言葉に・カードの行頭の ⏺（絵文字系）を ● に（+6,990B）。
// 2026-09-30（R102-3・本人「Claude Code のセッションみたいに中身が見えるように」）: ターミナル画面の行を Claude Code の文法へ
// （❯・● の点の色・⎿ の結果と「… +N 行」・行番号と ± の差分・Todo・1 ターンの所要・時刻の区切り・最下行の「作業中…」・配達の予告）・
// 「最近の動き」も同じ文法（古い順）・見出し・トースト・定型の行頭の絵文字をやめ、リストのカードは状態の語の札（+13,356B）。
// 2026-09-30（R102-4〜9）: 状態の正確さ・鳴らさない・INBOX ZERO・許可鍵の登録のやり直しとリンク無しの登録・部屋の起動の理由の送信。
// 同夜（R102-10・本人「スマホの AI Office が映ってません」）: 版が変わって初めて開いたら部屋の部品を cache:"reload" で取り直して
// 8 月の 1 年キャッシュの頃の写しを置き換えてから起動する（uiparts）・WebKit の結合の失敗（filename 空）も理由として拾う（+2,227B）。
// 2026-10-01（R102-10 の続き・本人の iPhone の実機ログ「Importing binding name 'vendorOf' is not found.」）: cache:"reload" の取り直しは
// 実機で古い写しを置き換えなかった → 部屋は版つきの道 /u/<BUILD>/pwa/bootpx.js で読み、部品どうしは相対 import（uiBoot・-861B）。
// 2026-10-01（B1 スマホ監査の修正）: ❗カードは本人で覚え差し替え直後 600ms は送らない・✓ は要求ごと・minionsLive・状態の語と文の一致・
// 質問の待ち分・承認ボタンは質問だけ／停止は輪郭・複数の問いは 1 タップで送らない・2 秒の取り消し中に隠れたら送らない・WS 半開き・Last login・送信失敗で文を残す（+11,888B）。
// 同日（R102-11・別モデルレビューの指摘）: 答えは表示していた要求の印（ask.id8）を添えて署名（{aioffice:1,kind:"answer"}）・✓ の照合も id8・
// 隠れて取り消した選択は次の起動でも言う・押し止めの鍵は要求の中身・送った人のシートだけ閉じる・半開きの再接続の待ちを戻す（+2,465B）。
// 2026-10-01（B2 スマホの見た目の磨き・実写 27 件）: 右上は名前の幅を先に取りチップは語つきのまま「他 N」へ畳む・部屋は ×2 の丈を確保してカードで倍率を変えない・名札は部屋の中・シートは先頭から開く・絵文字を表示から外す・空の案内の文字色・トースト・横画面・端末と許可の待ち（+16,216B）。
// 同日（B2 の取り込み）: 使われなくなった .gesture のスタイルを削除（-131B）。
// 2026-10-02（R103-2a）: 四つの固定タブ・種類別あなた待ち一覧・一度押すミニカード・部屋の帯と状態つき端末ドロワー・次の一手入力欄へ。
// 2026-10-02（R103-2b）: オフィスの先頭待ち・次の一手の候補/検索/個別確認・横向き三列を追加。
// 2026-10-02（R103-1・本人裁定「登録も ON もスマホだけで完結」「Mac だけの操作も全文確認＋Face ID」）: 設定から登録（Mac の受付不要）・
// ON（1/4/12 時間・Face ID の署名を封筒の時刻に結ぶ rst-）・OFF・「Mac だけ」の段は危険の理由を出して 2 段（+3,362B）。
// 同日（R103-1 の別モデルレビュー 2 巡目）: 登録の完了を反映した後で ON/OFF の欄を描き直す・rst- の結果（拒否の理由）を追って出す（+1,234B）。
// 2026-10-02（R103-3）: 状態4行・6グループ・通知行・設定の点3条件・登録済み行と暗号説明の折りたたみへ（+8,691B）。
// 2026-10-02（R103-4）: 拒否と2秒取り消し・許可全文の先読み・端末の回答と送信行・固定入力欄と低い横画面へ。
// 同日（R103-4 の検収・反証つきレビュー）: 要求が変わっても書きかけを残してそう言う・届いた送信行は本文の ❯ 行と二重にしない・低い横画面の 1.5px（+1,718B）。
// 同日（R103-4/5 の別モデルレビュー）: 届いた ✓ は証拠（取り込み・本文の ❯ 行）があるときだけ・要求の終了は「要求は終わりました」・期限切れ・q 行の書きかけはセッションごとに保持（+1332B）。
// 2026-10-02（R103-6）: 部屋の外観（時刻の光・仕事別の小物・助手・赤い紙）＝部屋の部品の束（BUILD）だけが変わった（本文の長さは同じ）。
// 2026-10-03（R104-1）: 許可の文脈・更新待ち・単一入口・横定型・×2の部屋・状態名札・行内返信・状態1行の残り8件を反映。
// 同日（R104-2）: 端末の遡り（いちばん上まで巻き戻すと before で前の行）・計画の本文の行（先頭 8 行・続きを読む）・「計画を読む」（+3794B）。部屋の背景は最初の draw で焼く（R103-6 の焼き直しの修正＝部品の束の指紋も変わる）。
// 同日（R104-3・別モデルレビュー）: 遡りの再試行は同じ before・読んでいる間は削らない・削るときは遡った頁を丸ごと外してカーソルを戻す・先頭の印は top のときだけ（+1597B）。
// 2026-10-04（R106・PC 版の dogfood）: 共有の ui/core/world.js（「いま」の英語の生文を出さない）で部品の束の版（BUILD）だけが変わった（本文の長さは同じ）。
// 2026-10-06（R110-S4・歩行のテンポ）: 共有の ui/core/pxwalk.js・pxtop.js と ui/cockpit/room.js（24px/秒・立ち上がり・到着の一拍）で部品の束の版（BUILD）だけが変わった（本文の長さは同じ）。
// 同日（R110-S5・S2・用事と小道具）: 部屋の束に ui/core/errand.js が入り、pxroom・pxtop・pxwalk・life・kind・archetype と ui/cockpit/room.js（用事・証拠の紙・画面の模様・打鍵の速さ・考え中の点・職業の小道具）で部品の束の版（BUILD）だけが変わった（本文の長さは同じ）。
// 2026-10-07（R112-P・本人「スマホで許可待ちに指示が出せない・1、2、3 の選択肢でも出せない」・意図した変更）: 2 問以上・複数選択を問いごとに選んで
// 1 通で答える（ask.questions・QPICK・「1. 問い: 答え」＋補足）・古い Mac の注記の文言・許可できない理由と導線（apvWhy・apvGoSettings・期限切れの控え）・
// 設定の状態行（OFF／期限切れ／未登録は朱・ON は残り）・先頭の 1 件の主操作「答える／指示する」・更新直後に押した操作を言う（attnHeld）・
// アプリの JS エラーと送信の失敗を /diag へ（diagPost・kind:app／send）（+20,000B 前後＝日本語の注釈を含む）。
// 同日（R112-P2・別モデルレビューの medium 3 件・意図した変更）: 答えの文を Mac の受け手の 2000 字（コードポイント）にも収め、補足を削ったら言う・
// 選択の鍵を答える相手の session＋askId に揃え、刈り込みは束の中の全セッション（employees）も見る（代表が替わっても残す）・
// 未送信の選択を sessionStorage（aioffice.qpick）に控えて再読み込みの後に戻す（30 分・送った／消えたら控えも消す）（+3,962B）。
// 2026-10-08（R113-A・本人「スマホの登録がめちゃくちゃ回りくどい・何回も処理しないといけない」・意図した変更＝手順・封筒・署名・Mac の判定は不変）:
// 設定の ┤ 許可 ├ を状態機械に（いまの状態の 1 行・主ボタン 1 つ＝未登録は「このスマホで許可できるようにする」で登録→granted→ON 12 時間を続けて・
// 案内の 1 行・小さな「詳細」に既存の行）・登録成功の誤案内（Mac で「登録」を押す 45 秒）を撤去・登録と ON の連打の守り（APV_BUSY）・使ったチケットを
// 送り直さない・許可鍵の控えに端末 id（apvKeyState＝ok/moved/unknown/none）・再ペアリングの警告・Mac の理由を本人の言葉に・許可の結果のトースト・
// 先に終わっていた要求の正直な言い方（+21,588B＝日本語の注釈を含む）。
// 2026-10-08（R113-B-P・本人裁定「Claude Code や Codex とかのやり取りが、そのまま表示される設計に」・意図した変更）: 端末の入口を会話の入口から分けて
// Codex（cx-）も開ける（canTermFor）・考えの印に字数・Bash（Shell・exec）の複数行／長い入力と長い結果は等幅の pre ブロック・差分は行頭の +／− だけ
// （地の帯を撤去・行番号の列は桁で広げる）・続きの案内を「… +N 行（タップで開く）」⇄「残り N 行は Mac で」に統一・失敗の中略は薄い 1 行・
// サブエージェントの行（kids）の折りたたみと省いた数（kx）・一覧／シート／端末の見出しに道具の印・「最近の動き」の履歴にも会話を見せる OFF の理由（+10,878B）。
// 2026-10-08（R113-B-S3・別モデルレビュー high・意図した変更）: 端末の初期表示（届くまでの経過秒の行と手元の「最近の動き」）の印を tpre から tinit へ
// 分けた（R113-B-P で本文の pre ブロックにも tpre を使い、本文の罫・折りたたみ・見切れ検査が初期表示に効き、差分の頁で termPaint が本文の
// pre ブロックまで消していた）。termPaint が消すのも経過秒の行を探すのも tinit だけ（+265B＝日本語の注釈を含む）。
// 2026-10-08（R114-P・本人裁定「スマホで確認して許可すると Face ID を求められたり、QR コードをスキャンすることになったりする。全く許可できない」
// → ON の間はタップだけ・意図した変更＝sig の canonical・ON・登録は不変）: ON の間の remote・review は Face ID を呼ばずタップの封筒（apvTapPayload・
// mode:"tap"）・mac は要点（理由の札・道具・入力の先頭 200 字）＋「確認して許可（Face ID）」1 回・全文を最後まで読む義務と「内容を確認した」の印を撤去
// （#apvack を削除・全文は「全文を見る」の折りたたみ）・OFF は送らず「設定で ON にする」・結果は端末が覚えた reqId で「タップで許可しました」・
// 拒否の理由 tap-mac / bad-format・許可の失敗を /diag（kind:approve）へ・ペアリングの無いブラウザは「ホーム画面から開いて」（#su_home・/diag nocred）・
// 再ペアリングの説明は「通常は不要」（+11,261B＝日本語の注釈を含む）。
// 2026-10-09（R115-S4・R114-P の別モデルレビュー medium・意図した変更）: diagPost は認証（Bearer）が無ければ kind に依らず /diag を送らない
// （Worker は Bearer の無い /diag を 401 で捨てる＝届かない経路を叩かない）・数える前に見る（ペアリングの前の呼び出しで 60 秒の枠を使わない）・
// cred の無い文脈の kind:"nocred" の報告（showSetup）と referrer の種類（setupRefKind）を撤去（−63B＝日本語の注釈を含む）。
// 2026-10-09（R116-S0 レーン B・本人「bash の許可を与えるのもごちゃごちゃしすぎている」「回りくどい」・意図した変更＝署名 canonical・
// apvAllow・apvTapPayload・apvRender（隠した pre・1.2 秒）・apvLater（5 秒）は 1 バイトも変えない）:
//   着地（+251B）: 許可待ちの通知の深リンクはシートを挟まず reviewWait（許可の画面・許可できなければ今までどおりシート）へ・
//     質問の着地は今のまま（通知の題と本文の絵文字・「（0分）」は Worker 側）
//   自動で閉じる（+1,675B）: 受け付けられたら［閉じる］を押させず閉じ、状態の行に「✓ 許可 — <名前>（Mac が受け付けました）」を 3 秒（tkFlash）→
//     3 秒後に次の許可待ちへ（apvNextLater・送った許可は 2 分は開き直さない）。granted の文 4 本を撤去・名前は closeApprove で控える
//   拒否して送る（+582B）: 許可待ちのシートに定型と定型チップを出さず、送信口は「拒否して送る」（shSendLabel・status ごとに直す）
//   先読み（+368B）: 捨てた控えは消して次の機会に取り直す（OFF→ON・150 秒の鮮度切れ）・失敗は 150 秒の印（apvPrefetchFail）
//   （T( は 610→609・計 +2,876B＝日本語の注釈を含む）
// 2026-10-09（R116-S0b 反証レビューの修正・意図した変更＝署名 canonical・apvAllow・apvTapPayload・apvRender・1.2 秒・5 秒は 1 バイトも変えない）:
//   シート: status で相手の要求（attnSig）が替わったらいまの相手で組み直す（sheetSync＝定型と定型チップの黙った拒否・#1 の印のまま #2 へ
//     「拒否して送る」を止める）・押した時点に相手が替わっていれば送らない（sheetFresh）
//   着地: 開いている許可の窓は同じ要求で終わっていないときだけ開き直さない・シートは許可の窓を閉じてから（openAttnLink・reviewWait）
//   自動で次へ: 操作の途中（uiBusy＝reloadSafe と共通・2 秒の取り消し等）・閉じた後に画面へ触れた（TOUCH_AT）なら開かない・2 分の印は
//     受け付けられた（granted）ときだけ（APV_SENT_SIG）
//   受け皿（#tapcatch・tapCatch）: 自動で閉じた・開いた・シートを組み直した直後 ATTN_GUARD_MS は押下を受けない
//   先読み: 鮮度（封書の寿命）と取り直し（150 秒）を要求からスマホの時計で測る（rec.fresh・apvPrefetchUsable）
//   （T( は 609→611・計 +4,610B＝日本語の注釈を含む）
// 2026-10-09（R116-S1 決裁カード・本人「bash の許可を与えるのもごちゃごちゃしすぎている」「回りくどい」・意図した変更＝署名 canonical・
//   apvCanonical・apvTapPayload・apvTapAllowed・apvLevelOk・隠した pre の照合・1.2 秒・5 秒・150 秒は変えない）:
//   許可はカード 1 種類（#apvwrap を置き場 #apvslot_* へ移す・全画面の窓・ゴースト・［閉じる］・設定へ飛ばす導線を撤去）・状態は apvState 1 本・
//   ON と登録はカードの中（#apv_fix）・中継が預かったら次の件へ・失敗は 8 行・5 秒は端末が待って送る・先読みは焦点の件と次の件を順に・
//   設定の ┤ 許可 ├ は 1 行＋主ボタン 1 つ＋1h・4h・登録の受付（#apvcode・#enr=）の PWA 側を撤去・allow の本文に監査の ux（canonical の外）
//   （−23,666B＝許可まわりの文言 約 175 → 33 本）
// 2026-10-13（R118・本人裁定「Claude のスマホアプリと同じく、サクッと許可」「常に ON」・意図した変更＝署名 canonical・apvCanonical・
//   apvTapPayload・apvLevelOk・隠した pre の照合・1.2 秒・5 秒・150 秒は変えない）: 許可は段を問わず 1 タップ（apvTapAllowed は mac も・
//   ペアリングだけ）・カードの状態から need-setup と ready-faceid を撤去（mac の段は危険の朱の 1 行）・ON も OFF も封筒だけ（期限なし）・
//   設定の ┤ 許可 ├ は「ON にする／OFF にする」のボタン 1 つ（1h・4h・登録し直すを撤去）・許可鍵の登録と控え・WebAuthn の残りを撤去・
//   再ペアリングの説明から許可鍵を外す（-11,844B）
// 2026-10-25（R119・本人裁定「iPhone Duo の開いて横長は 3 列のまま左を広く」・意図した変更＝app.css だけ・署名 canonical と許可まわりは
//   1 バイトも変えない）: 横分割で部屋の ×2 が縦に入る画面（幅 860 以上・高さ 600 以上）は左の列を max(34%, 380px)・高さ 700 未満は
//   先頭の 1 件（#officewait）を左に出さない CSS の 2 段（+1,200B）
// 2026-10-26（R116-S1b・本人裁定「延長の仕組みを作る」・意図した変更＝署名 canonical（sign の並び）・apvCanonical・apvTapPayload・
//   封書の開き方（openBlob の鍵と AAD）は 1 バイトも変えない）: つながりの延長＝office の actions.approve.rk に自分の端末があれば
//   rky- の求め（今の秘密）→ 封書で新しい秘密 → 新しい秘密へ替えて受領（rky- ack）を中継が預かるまで送り直す・封書を開く 3 か所は
//   延長の直前の秘密（sp・1 時間）でも試す（openSealed）・期限の予告は 5 日前から「Mac で［延長］」（+4,144B）
// 2026-10-26（R116-S1b の別モデルレビュー high 2・同じく canonical と封書の開き方は不変）: 受領は中継が預かった時点で終わりにせず、
//   Mac の状態の rkp（受領待ち）と rk（窓）から自分が消え、その状態が受領の後に作られた（LAST_ST.ts）と分かるまで 30 秒おきに送り直す・
//   受領待ちの間は sp の期限を切らない（+644B）
// 2026-10-26（R116-S1b の別モデルレビュー 2 巡目 medium 2・3／low・canonical と封書の開き方は不変）: 受領待ちを終える判定は同じ時計どうし
//   （受領待ちを見た状態より新しい状態＝中継の時計／受領を預けてから 15 秒＝スマホの時計）・終えたら新しい窓へそのまま求めに進む・
//   送り直しは Mac がつながっている間だけ（+586B）
// 2026-10-26（R116-S1b の別モデルレビュー 3 巡目 medium 2 件・canonical と封書の開き方は不変）: 受領待ちは Mac の「済み」の封書
//   （新しい秘密で封じた {done, e, rk}・受領の reqId に縛る）を開けたときだけ終える＝状態や時刻から推さない（rkp を撤去）・送り直しは Mac が
//   最近見えているとき（WS か agentSeenAgo が 120 秒未満＝HTTP でも）だけ（-215B）
// 2026-10-26（R116-S1b の別モデルレビュー 4 巡目 medium・canonical と封書の開き方は不変）: 延長の答え（新しい秘密・「済み」）は今の秘密だけで
//   開く＝sp（古い秘密）で封じた偽の答えを受けない・開けない封書では待ちを消さない（本物の答えを捨てない）・
//   古い秘密（sp）で会話・端末・許可の封書を開ける窓は受領待ちの間と「済み」の後 2 分だけ（+820B）
// 2026-11-02（R121・本人 2026-11-01「アプリ版 Claude Code を見習って」「コードがつらつら出てきたら分からない」・意図した変更＝署名 canonical・封書の開き方・許可の封筒は不変）:
//   セッション画面の行の描画を作業ビュー（ui/hud/activity.js を bootpx の橋 window.__activity で共有・Mac と同じ描画）へ。
//   要約の 1 段（#termsummary）・作業ビューの CSS（ui/hud/activity.css の展開・--t-sans）・送った言葉／質問の答え／遡りを作業ビューの要素へ掛け替え（+17,769B）
// 2026-11-02（R121-C の別モデルレビュー medium・canonical と封書の開き方は不変）: 同じ要求の状態更新で回答欄を付け直さない・動かすときは打っている欄のフォーカスと位置を戻す（+804B）
// 2026-11-02（R122-P0・canonical と封書の開き方は不変）: 束（modules_data）の版の印だけが変わった＝ui/hud/activity.js の畳みの行を 1 文に（+0B）
// 2026-11-02（R122-P2/P3・本人「画面の構成から変えて」「明るめの UI に」「スマホにも 3D」・canonical と封書の開き方と許可の封筒は不変）:
//   タブを 会話（ホーム）／オフィス／設定 へ・明るい配色を既定・ホームのオフィスのカード 230px＋あなたの番＋全セッションの文章の 1 行・3D の部屋を既定（別の束を初回だけ読む・失敗はドット絵へ）（+18,227B）
// 2026-11-03（R122-P2/P3 の差し戻し 1 回目・canonical と封書の開き方と許可の封筒は不変）: 横向きで部屋の窓をタブの上で切る・カードを部屋と別の面へ・最近の行に保存の札・下の情報の 1 行を戻す・通知と許可の着地面を戻す（+8,575B）
// 2026-11-03（R122-P2・canonical と封書は不変）: 低い横向きのホームで最近を入れ子のスクロールにしない（+316B）
// 2026-11-03（R122-P2・canonical と封書は不変）: 低い横向きで部屋の窓の丈を守る（下の情報を右の列へ・見た目の切替を窓の上の帯に）（+1,191B）
// 2026-11-03（R122-P2・canonical と封書は不変）: 起動時の「接続中…」の置き札を状態が届いたら外す・低い横向きの格子を固定（+574B）
// 2026-11-09（R123・canonical と封書の開き方と許可の封筒は不変）: 束（modules_data・modules_3d）の版の印だけが変わった＝ui/core/activity.js の questionStem・ui/hud/board.js の組の名前・strings の app_recommended（+0B）
// 2026-11-09（R123 の別モデルレビュー medium 2 件の直し・canonical と封書は不変）: 束の版の印だけ（ui/core/activity.js の questionStem を厳しく・sameQuestion）（+0B）
// 2026-11-13（R133・本人「スマホ用のレイアウトがちゃんと整っていない」・canonical と封書の開き方と許可の封筒は不変）: 名札は選んだ人＋あなたの番だけ（最大 4 枚・白い丸い札・重ねない）・
//   題の飾り（┤ ├）と等幅の状態行を外す・許可のカードと名簿と設定をカードの作法へ・3 択の設定は見出しの下に並べる・ドット絵の部屋は窓を画面幅いっぱいに・会話の見出しの名前を一覧と同じに（+10,265B）
// 2026-11-14（R127・出勤の口の Bot の行が中継に載る＝不変条件 8 の改定・canonical と封書の開き方と許可の封筒は不変）: Bot（bot-／vendor bot）には会話・端末・起動・
//   セッション向けの定型の入口を出さない（開くと Mac が「記録が見つかりません」と返すだけだった）・一覧の行はシートを開く・返信欄の案内文（+1,076B）
// 2026-11-14（R127 の別モデルレビュー medium・canonical と封書は不変）: Bot の相談への答えが長すぎるときは、印（id8）を外して送らずに止める（+566B）
// 2026-11-14（R128 #3 Bot の記録の画面・canonical と封書の開き方と許可の封筒は不変）: Bot の入口を「記録を見る」で開け直す（端末の画面に口の記録＝kind:"term"）・
//   入力欄と届き方の案内を Bot の言葉に・記録のはじまり／見つからない／見えない理由・旧い描き方にも記録の 1 行。束は作業ビューの描き手（event・報告の札・答え／指示の札・record）（+3,728B）
// 2026-11-14（R128 #1・#2 種類に bot を足す・canonical と封書の開き方と許可の封筒は不変）: vendorOf が bot を「その他」に畳まない・一覧の種類の印に Bot の色（.vendor-bot）（+43B）
// 2026-11-14（R128 #3 の別モデルレビューの回・canonical と封書の開き方と許可の封筒は不変）: Bot を「セッション」と呼ばない（端末画面の宛先の行）・Bot への送るボタンは「この答えを送る／この言葉を送る」（+131B）
// 2026-11-14（R134・本人「iPhone Duo を開いたらパソコン版が表示されてほしい」「配色も PC と同じに」・canonical と封書の開き方と許可の封筒は不変）:
//   広い画面（開いた Duo・iPad＝app.css の --wide）は PC 版と同じ並び（3 列＝一覧・会話・オフィス／2 列＝一覧・会話＋［オフィス］で重ねる・下のタブ無し・足元に［設定］）・いつも明るい（Mac の --a-* を生成・テーマの 3 択を撤去）・要約の行も作業ビューの作法（+2,727B）
const EXPECT_BYTES = 485630;
const actual = createHash("sha256").update(APP_HTML, "utf8").digest("hex");
assert.equal(actual, EXPECT_SHA256, "APP_HTML が方向Cの配信バイト列と不一致");
assert.equal(Buffer.byteLength(APP_HTML, "utf8"), EXPECT_BYTES);

// R102-1: 版（APP_VER）＝版の置き場を空にした HTML＋\0＋モジュール束の BUILD の SHA-256 の先頭 12 桁。HTML の <html data-build> と一致。
assert.match(APP_VER, /^[0-9a-f]{12}$/, "APP_VER は 12 桁の 16 進");
assert.equal(APP_HTML.split(`data-build="${APP_VER}"`).length, 2, "APP_HTML の data-build が APP_VER でない（1 か所）");
const blank = APP_HTML.replace(`data-build="${APP_VER}"`, 'data-build=""');
assert.equal(createHash("sha256").update(blank + "\0" + BUILD, "utf8").digest("hex").slice(0, 12), APP_VER,
  "APP_VER が HTML とモジュール束の版から作り直した値と違う（tools/gen_pwa_modules.py の app_ver）");

// gloss_parity が読む Worker 側の関数と、実際に配信する関数の同期も守る。
const worker = readFileSync(new URL("../relay/src/worker.js", import.meta.url), "utf8");
const begin = worker.indexOf("// PWA_GLOSS_BEGIN");
const end = worker.indexOf("// PWA_GLOSS_END");
assert.ok(begin >= 0 && end > begin, "PWA_GLOSS markers not found in worker.js");
const gloss = runInNewContext(worker.slice(begin, end) +
  '\ntidyActivityPWA.toString() + "\\n" + activityGlossPWA.toString();', {}, { timeout: 1000 });
assert.ok(APP_HTML.includes(gloss), "配信HTMLとPWA_GLOSS関数が不一致");
console.log(`APP_HTML SHA-256 一致: ${actual} (${EXPECT_BYTES} bytes)`);

// R99-P4-6: 配信する閉包は部屋（ドット絵）だけ。3D（ui/iso・three.js）とテクスチャが戻ると、
// 開くときに取りに行く量が約 0.1MB → 約 1.1MB に戻る（本人裁定 A の理由そのもの）。
assert.ok(MODULES["/ui/cockpit/room.js"] && MODULES["/ui/core/pxroom.js"], "部屋（ドット絵）が同梱されていない");
assert.ok(MODULES["/ui/pwa/bootpx.js"], "起動部品 bootpx.js が同梱されていない");
assert.ok(!MODULES["/ui/pwa/boot3d.js"], "3D の起動部品 boot3d.js が同梱されている");
assert.deepEqual(Object.keys(ASSETS), [], "URL で読むアセット（3D のテクスチャ）が同梱されている");
assert.ok(Object.keys(MODULES).every((url) => !url.startsWith("/ui/iso") && !url.includes("/three")),
  "3D（ui/iso・three.js）が同梱されている");
// R122-P3: 部屋は 3D（boot3d.js・別の束）とドット絵（bootpx.js）を import(uiBoot(mode)) で読み分ける＝どちらも版つきの道（/u/<BUILD>/pwa/…）
assert.ok(APP_HTML.includes("import(uiBoot(mode))") && APP_HTML.includes('"/u/"+b+"/pwa/"+file'),
  "シェルが部屋の起動部品（bootpx.js／boot3d.js）を版つきの道で読んでいない");

// R102-10: 部屋は版つきの道（/u/<BUILD>/pwa/bootpx.js）で読み、部品どうしは相対 import＝全部品がその下で解決される。本人の iPhone は
// 8 月の 1 年キャッシュ（immutable）の頃の /ui/core/world.js・/ui/platform/clock.js を 2 か月後も手元から使い（cache:"reload" で
// 取り直しても置き換わらなかった）、新しい部品と結合できずに部屋が起動しなかった。版は同梱の BUILD・閉包は絶対の /ui/ を import しない
// （1 本でも /ui/... を import すると、その部品だけ版の無い道に戻り、古い写しに当たり得る）。
{
  const tag = "<script type=application/json id=uiparts>";
  const i = APP_HTML.indexOf(tag);
  assert.ok(i >= 0 && APP_HTML.indexOf(tag, i + 1) < 0, "uiparts が 1 個でない");
  const d = JSON.parse(APP_HTML.slice(i + tag.length, APP_HTML.indexOf("</script>", i)));
  assert.deepEqual(d, { b: BUILD, b3: BUILD3 }, "uiparts の版が同梱の BUILD（部屋）と BUILD3（3D の束）と違う");
  assert.ok(MODULES3["/ui/pwa/boot3d.js"] && MODULES3["/ui/iso/scene3d.js"], "3D の束に起動部品と 3D の部屋が無い");
  const seen = new Set(), stack = ["/ui/pwa/bootpx.js"];
  while (stack.length) {
    const url = stack.pop();
    if (seen.has(url)) continue;
    seen.add(url);
    assert.ok(MODULES[url], `${url} が同梱されていない`);
    assert.ok(!/from\s*"\/ui\//.test(MODULES[url]), `${url} が絶対の /ui/... を import している（版つきの道から外れる）`);
    for (const m of MODULES[url].matchAll(/from\s*"(\.\.?\/[^"]+)"/g)) stack.push(new URL(m[1], "https://x" + url).pathname);
  }
  assert.ok(seen.has("/ui/core/world.js") && seen.has("/ui/platform/clock.js") && seen.size >= 12, `部屋の閉包が足りない（${seen.size} 本）`);
}

// 部屋が作れない端末（2D の canvas も無い）では scene3d-failed を出す＝app.js が一覧へ退避する（R79 の永久白画面の教訓）。
// 作れた端末では出さない。import の後（const host）から最初の段の終わりまでを切り出し、room.js の init を差し替えた偽物で走らせる
//（import を正規表現で外すと、行末の注釈やセミコロンの無い行で次の文まで食べる＝赤の理由を取り違える）。
const boot = MODULES["/ui/pwa/bootpx.js"];
assert.ok(boot.indexOf("const host = ") > 0 && boot.indexOf("if (host && room)") > boot.indexOf("const host = "));
const init = boot.slice(boot.indexOf("const host = "), boot.indexOf("if (host && room)"));
for (const fail of [false, true]) {
  const events = [];
  let constructed = false;
  runInNewContext(init, {
    document: { getElementById: () => ({}), dispatchEvent: (event) => events.push(event.type) },
    CustomEvent: class { constructor(type) { this.type = type; } },
    matchMedia: () => ({ matches: false }),
    location: { search: "" },
    URLSearchParams,
    ROOM: { w: 184, h: 160, robot: { w: 12, h: 16 } },
    ROOM_SCALE_MIN: 2,
    SLOTS: { queue: [{ y: 132 }, { y: 134 }] },
    SESSION_COLORS: ["#000000"],
    tintHash: () => 0,
    clockFrozen: false,
    createRoomLighting: () => () => "day",
    initRoom: (opts) => {
      constructed = true;
      assert.equal(opts.scale, 2);
      assert.equal(typeof opts.frozen, "function");
      assert.equal(typeof opts.colorOf, "function", "個体色の衝突を解く colorOf を渡していない");
      assert.equal(opts.keepPrev, true);
      assert.equal(opts.life, true, "散歩（暮らし）が既定で入っていない（?life=0 の時だけ切る）");
      if (fail) throw new Error("canvas fixture failure");
      return {};
    },
  });
  assert.ok(constructed);
  assert.deepEqual(events, fail ? ["scene3d-failed"] : []);
}

// R87: 封書の入口は**部屋から独立して**配信されること。部屋に相乗りさせると、
// リスト表示を保存した端末では sceneShell3D() が呼ばれず永久に読み込まれない。
assert.ok(MODULES["/ui/pwa/dlg.js"], "dlg.js が同梱されていない");
assert.ok(MODULES["/ui/core/dialog_open.js"] && MODULES["/ui/core/dialog_kat.js"]);
assert.ok(!MODULES["/ui/pwa/bootpx.js"].includes("__dlg"),
  "封書の入口が bootpx（部屋）に紛れている＝リスト表示で詰む");

// 受信値だけが変わる更新、欠けた成長値、レシピ追加/削除を固定する。
const source = readFileSync(new URL("../ui/pwa/app.js", import.meta.url), "utf8");
const helpers = source.slice(source.indexOf("function hudData("), source.indexOf("function updateAttnCards("));
const runTab = {};
const office = { employees: [{ session: "s1", detail: "受信した詳細", vendor: "codex" }],
  growth: { byProject: { p1: { level: 0 } } }, actions: {} };
// B2: 表示の述語は絵文字を剥がす（plainPict）＝helpers の中から呼ぶので一緒に読む
const plain = source.slice(source.indexOf("function plainPict("), source.indexOf("function glossPlain("));
const hud = runInNewContext(gloss + "\n" + plain + "\n" + helpers +
  "\n({detailOf,vendorOf,levelOf,askSummary,syncRunTab})", {
    LANG: "ja", T: (ja) => ja, LAST_OFFICE: office, actionsView: () => office.actions,
    document: { getElementById: () => runTab },
  });
const agent = { session: "s1", projectId: "p1", approvalMin: 3,
  ask: { kind: "permission", tool: "Bash" } };
assert.equal(hud.askSummary(agent), "Bash の許可・3分");
// B1: 見出しの時間は右上の時間（waitText）と同じ語（「0分」と「いま」を並べない）。質問の分は ask.min → 無ければ質問を出してからの age
assert.equal(hud.askSummary({ ask: { kind: "question" } }), "質問への回答・いま");
assert.equal(hud.askSummary({ question: "q", ask: { kind: "question", min: 7 } }), "質問への回答・7分");
assert.equal(hud.askSummary({ question: "q", age: 400 }), "質問への回答・6分");
assert.equal(hud.askSummary({ session: "s9", question: "q", age: 5, sessions: [{ session: "s9", age: 125 }] }), "質問への回答・2分");
assert.equal(hud.vendorOf(agent), "codex");
assert.equal(hud.vendorOf({ vendor: "unrecognized" }), "other");
assert.equal(hud.detailOf(agent), "受信した詳細");
assert.equal(hud.detailOf({ ...agent, detail: "最新の詳細" }), "最新の詳細");
assert.equal(hud.levelOf(agent), 0);
assert.equal(hud.levelOf({ ...agent, level: 7 }), 7);
assert.equal(hud.levelOf({ session: "missing" }), null);
for (const recipes of [undefined, [], [{ id: "r1" }], []]) {
  office.actions.recipes = recipes;
  hud.syncRunTab();
  assert.equal(runTab.textContent, recipes?.length ? "Mac に登録した操作 1 件・実行結果もここから" : "Mac に登録した操作と実行結果を確認します");
}
console.log("PWA: 通常の束は部屋がドット絵だけ（3D は別の束・R122-P3）・作れない端末は退避の合図・detail/Lv/レシピ表示 OK");


// R103-2a → R122-P2: 固定のタブ（会話＝ホーム／オフィス／設定の 3 つ）と、登録操作・起動への入口を保持する。
const tabs = APP_HTML.match(/<nav class="tabbar hidden" id=tabbar[^>]*>(.*?)<\/nav>/s)[1];
assert.deepEqual([...tabs.matchAll(/<button id=(tb_\w+)/g)].map(m => m[1]),
  ["tb_wait", "tb_office", "tb_set"]);
assert.ok(APP_HTML.includes('id=st_run_btn onclick="openRun()"'));
assert.ok(APP_HTML.includes('id=st_ln_btn onclick="openLaunch()"'));
assert.ok(APP_HTML.includes('id=term_compose_lb for=termsay'));

// 帯とスワイプは同じ全員の順序を使い、端では相手を変えない。
const agents = [{ session: "s1" }, { session: "s2" }, { session: "s3" }, {}];
const hops = [];
const navigation = runInNewContext(source.slice(source.indexOf("function termSessions("), source.indexOf("var termSurface=")) +
  "\n({termNeighbor})", {
    TERM: { session: "s2" }, LAST_OFFICE: {}, officeAgents: () => agents,
    openTerm: e => hops.push(e.session),
  });
navigation.termNeighbor(-1);navigation.termNeighbor(1);
assert.deepEqual(hops, ["s1", "s3"]);
navigation.termNeighbor(2);
assert.deepEqual(hops, ["s1", "s3"], "端で別のセッションへ回り込んだ");

// 送信を待つ間の切替・status更新で、送信済み下書きを残したり別の入力を消したりしない。
const senderSource = source.slice(source.indexOf("function termSend("), source.indexOf("function termScrolled("));
for (const switched of [false, true]) {
  const shown = { session: "s1", ask: { id8: "01234567" } };
  const textarea = { value: "next step" }, drafts = { s1: "next step" };
  let resolveSend, target;
  const sender = runInNewContext(senderSource + "\n({termSend, update: e => { TERM.e=e;TERM.session=e.session; }})", {
    TERM: { session: "s1", e: shown }, TERM_DRAFTS: drafts,
    termEl: () => textarea, termSchedule: () => {}, termNote: () => {},
    T: ja => ja, termSubmit: (text, e) => { target=e;return new Promise(r => {resolveSend=r;}); },
  });
  sender.termSend();
  assert.equal(target, shown, "表示した要求の対象を差し替えた");
  sender.update({ session: switched ? "s2" : "s1" });
  if (switched)textarea.value="other draft";
  resolveSend(true);await Promise.resolve();
  assert.ok(!("s1" in drafts), "送信済み文を下書きに残した");
  assert.equal(textarea.value, switched ? "other draft" : "");
}
console.log("PWA R103-2a: 四タブ・実行入口・隣への切替・表示対象の送信と下書き OK");

// R103-2b: 候補の優先順と名前のあいまい検索を、配信元の関数で固定する（ブラウザ起動を要しない）。
const candidateSource = source.slice(source.indexOf("function nextNorm("), source.indexOf("function paintNext("));
const triageSource = source.slice(source.indexOf("function stateSort("), source.indexOf("function pushHintDismissed("));
const needSource = source.slice(source.indexOf("function needsAttn("), source.indexOf("function attnLine("));
const nextAgents = [
  { session: "s-work", disp: "Builder", projectId: "search-api", state: "working", age: 3 },
  { session: "s-idle", disp: "Writer", projectId: "blog-studio", state: "waiting" },
  { session: "s-plan", disp: "Planner", ask: { tool: "ExitPlanMode", kind: "permission" }, approvalMin: 2 },
  { session: "s-perm", disp: "Atlas", ask: { tool: "Bash", kind: "permission" }, approvalMin: 4 },
  { session: "s-question", disp: "Reviewer", question: "進めてよいですか？" },
];
const candidates = runInNewContext(gloss + "\n" + plain + "\n" + helpers + "\n" + triageSource + "\n" + needSource + "\n" +
  source.slice(source.indexOf("function officeNeed("), source.indexOf("function waitSummary(")) + "\n" + candidateSource +
  "\n({nextCandidates,nextMatch})", {
    LANG: "ja", T: ja => ja, LAST_OFFICE: {}, NEXT_RECENT: "s-work", TERM: null, MINI: null,
    officeAgents: () => nextAgents, sessionAgent: id => nextAgents.find(e => e.session === id),
    dispCrew: e => e.disp || e.session, waitSummary: e => e.question || e.ask?.tool || "",
    splitOffice: () => false,
  });
const suggestions = Array.from(candidates.nextCandidates(""));
// R134: 「テーマを変える」の候補はテーマの選択ごと撤去（いつも明るい）
assert.deepEqual(suggestions.map(r => r.kind), ["attn", "attn", "attn", "continue", "terminal", "notify", "resources"]);
assert.deepEqual(suggestions.slice(0, 3).map(r => r.verb).sort(), ["許可に答える", "質問に答える", "計画を読む"].sort());
assert.equal(suggestions.find(r => r.kind === "continue").e.session, "s-idle");
assert.equal(suggestions.find(r => r.kind === "terminal").e.session, "s-work");
for (const [query, session] of [["ＢＬＳＴ", "s-idle"], ["wrt", "s-idle"], ["swr", "s-work"], ["srch", "s-work"]]) {
  assert.ok(Array.from(candidates.nextCandidates(query)).some(r => r.e.session === session), `あいまい検索: ${query}`);
}
assert.equal(candidates.nextCandidates("no-such-project").length, 0);
assert.equal(candidates.nextMatch("tsla", "Atlas"), false, "文字の順序を無視した検索になった");

// 選んだだけ・取り消し・確認後の状態変化では送らず、確認した相手だけを既存 send に渡す。
const chooseSource = source.slice(source.indexOf("function nextChoose("), source.indexOf("var nextInput="));
for (const scenario of ["cancel", "changed", "send"]) {
  const shown = { session: "s-idle", disp: "Writer", state: "waiting" };
  let latest = shown;
  const sent = [], notes = [], input = { value: "", focus() {} };
  const chooser = runInNewContext(chooseSource + "\n({nextChoose,nextSend,cancel:()=>{NEXT_CONFIRM=null},confirmation:()=>NEXT_CONFIRM})", {
    NEXT_ROWS: [{ kind: "continue", e: shown }], NEXT_BUSY: false, NEXT_CONFIRM: null, SENDING: false,
    document: { getElementById: () => input }, T: ja => ja, paintNext() {},
    closeNext() {}, sessionAgent: () => latest, sessKind: e => e.state === "waiting" ? "idle" : "perm",
    attnSig: e => e.session + ":" + e.state, note: text => notes.push(text),
    send: (text, e) => { sent.push({ text, e }); return Promise.resolve(true); },
  });
  chooser.nextChoose(0);
  assert.equal(sent.length, 0, "選ぶだけで続けてを送った");
  assert.equal(chooser.confirmation().e, shown);
  if (scenario === "cancel") chooser.cancel();
  if (scenario === "changed") latest = { ...shown, state: "permission", ask: { id8: "abcdef12" } };
  chooser.nextSend();
  await Promise.resolve();
  assert.equal(sent.length, scenario === "send" ? 1 : 0, scenario);
  if (scenario === "send") { assert.equal(sent[0].text, "続けてください");assert.equal(sent[0].e, shown); }
  if (scenario === "changed") assert.equal(notes.length, 1);
}

// 先頭要求→0件→同じ要求の復帰でも、空のキャッシュを表示しない。
function fakeNode() {
  const attrs = new Map();
  return { children: [], hidden: false, classList: { toggle() {} },
    setAttribute: (k, v) => attrs.set(k, v), getAttribute: k => attrs.get(k), removeAttribute: k => attrs.delete(k),
    replaceChildren() { this.children = []; }, appendChild(n) { this.children.push(n); return n; } };
}
const compactWait = fakeNode();
const waitPainter = runInNewContext(source.slice(source.indexOf("function paintOfficeWait("), source.indexOf("function paintSplitWait(")) +
  "\n({paintOfficeWait})", {
    VIEW: "office", LANG: "ja", T: ja => ja,
    document: { getElementById: () => compactWait }, officeNeed: a => a,
    attnSig: e => e.session, dispCrew: e => e.disp, waitSummary: () => "要約",
    sessKind: () => "question", kindTag: fakeNode, waitAction: () => "答える",
    el: fakeNode, actBtn: fakeNode, apvIsPerm: () => false, APV_DONE: {},
  });
for (const list of [[nextAgents[4]], [], [nextAgents[4]]]) {
  waitPainter.paintOfficeWait(list);
  assert.equal(compactWait.hidden, list.length === 0);
  assert.equal(compactWait.children.length, list.length ? 4 : 0);
}
console.log("PWA R103-2b: 候補優先順・あいまい検索・続けての確認/取消/状態検算・先頭待ちの復帰 OK");

// R103-3: 配信元の状態計算と描画を同じ値で検査する。描画中に通信を始めると即座に失敗する。
function sourceBetween(start, end) {
  const first = source.indexOf(start), last = source.indexOf(end, first);
  assert.ok(first >= 0 && last > first, `設定の検査対象が読めない: ${start}`);
  return source.slice(first, last);
}
const settingsSource = [
  sourceBetween("function settingsState(", "var SETTINGS_PAINT_AT="),
  sourceBetween("function credExpiry(", "function credMd("),
  sourceBetween("function apvView(", "function canApproveFromPhone("),
  sourceBetween("function officeAgents(", "function attnId("),
  sourceBetween("function attnSig(", "function sentAt("),
  sourceBetween("function sessKind(", "function kindWord("),
].join("\n");
const settingsNow = 1800000000;
class SettingsDate extends Date { static now() { return settingsNow * 1000; } }
const settingsRowIds = ["st_sync_state", "st_approve_state", "st_notify_state", "st_cred_state"];
function settingsNode() {
  const classes = new Set(), attrs = new Map();
  return { textContent: "", hidden: true, disabled: false,
    classList: {
      toggle(name, on) { on ? classes.add(name) : classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    setAttribute(name, value) { attrs.set(name, value); },
    getAttribute(name) { return attrs.get(name); },
  };
}
const waitingPermission = { session: "settings-permission", state: "waiting",
  ask: { kind: "permission", tool: "Bash", id8: "01234567" } };
const waitingQuestion = { session: "settings-question", state: "waiting",
  ask: { kind: "question", id8: "01234567" } };
const settingsCases = [
  { name: "許可鍵なし・OFF（R118: 許可鍵は見ない）", approve: "OFF" },
  { name: "ON（期限なし・R118）", on: 1, untilAbs: 4102444800, approve: "ON（期限なし）" },
  { name: "登録済みOFF", mine: true, approve: "OFF" },
  { name: "許可1799秒", mine: true, on: 1, until: 1799, reasons: ["approve"] },
  { name: "許可1800秒", mine: true, on: 1, until: 1800 },
  // R112-P: until が過ぎた ON は「期限切れ（M/D HH:MM）」（Mac は期限後 until=0 で送る＝その形は OFF のまま・下の「登録済みOFF」）
  { name: "許可0秒", mine: true, on: 1, until: 0, expired: true },
  { name: "許可期限切れ", mine: true, on: 1, until: -1, expired: true },
  { name: "許可鍵なしでも ON（R118）", on: 1, until: 1799, reasons: ["approve"] },
  // R113-A: 控えの端末 id がいまのペアリングと違う（再ペアリング）・端末 id の無い古い控え＝照合コードが Mac にあっても自分の鍵とは言わない
  { name: "端末が変わった（R118: 許可鍵は見ない）", keyOf: "moved", approve: "OFF" },
  { name: "古い控え（R118: 許可鍵は見ない）", keyOf: "unknown", approve: "OFF" },
  { name: "登録259199秒", expiry: 259199, reasons: ["cred"], cred: "あと 3 日" },
  { name: "登録259200秒", expiry: 259200, cred: "あと 3 日" },
  { name: "登録1時間", expiry: 3600, reasons: ["cred"], cred: "あと 1 時間" },
  { name: "登録期限切れ", expiry: -1, reasons: ["cred"], cred: "期限切れ" },
  { name: "期限情報無し", cred: "期限情報なし" },
  { name: "登録無し", noCred: true, cred: "未登録" },
  { name: "通知OFFで許可待ち", notify: false, agents: [waitingPermission], reasons: ["notify"] },
  { name: "通知OFFで質問待ち", notify: false, agents: [waitingQuestion], reasons: ["notify"] },
  { name: "通知OFFで旧形式の質問", notify: false, agents: [{ session: "legacy-question", question: "Which one?" }], reasons: ["notify"] },
  { name: "通知OFFで旧形式の許可", notify: false, agents: [{ session: "legacy-permission", approvalMin: 1 }], reasons: ["notify"] },
  { name: "通知OFFで待ち無し", notify: false },
  { name: "通知OFFで回答済み", notify: false, agents: [waitingPermission], sent: true },
  { name: "通知OFFで古い回答印", notify: false, agents: [waitingPermission], sent: true, changed: true, reasons: ["notify"] },
  { name: "通知OFFでエラーだけ", notify: false, agents: [{ session: "failed", err: "failed" }] },
  { name: "通知非対応で待ち有り", available: false, notify: false, agents: [waitingPermission], notifyText: "この端末では使えない" },
  { name: "通知購読中で待ち有り", agents: [waitingPermission], notifyText: "購読中" },
  { name: "Mac同期経過", elapsed: 2, sync: "5 秒前" },
  { name: "Mac同期timestamp", fallback: true, ts: 7, sync: "7 秒前" },
  { name: "Mac同期不明", noSync: true, sync: "確認中" },
  { name: "Mac同期切れ", stale: true, ago: 243, sync: "4 分前から届いていません" },
  { name: "中継切れ", offline: true, ago: 243, sync: "4 分前から届いていません" },
  { name: "Mac同期切れの既存経過", stale: true, staleSec: 600, sync: "10 分前から届いていません" },
  { name: "3条件同時", mine: true, on: 1, until: 1799, expiry: 86400,
    notify: false, agents: [waitingQuestion], reasons: ["approve", "notify", "cred"] },
];
for (const c of settingsCases) {
  const nodes = Object.fromEntries([...settingsRowIds, "tb_set_dot", "tb_set", "st_nt_btn", "st_nt_hint", "st_pt_btn"].map(id => [id, settingsNode()]));
  const agents = (c.agents || []).map(e => ({ ...e, ...(e.ask ? { ask: { ...e.ask } } : {}) }));
  const context = {
    Date: SettingsDate, T: ja => ja,
    LAST_ST: c.noSync ? null : { ago: c.fallback ? null : (c.ago ?? 3), ts: (settingsNow - (c.ts || 3)) * 1000,
      at: (settingsNow - (c.elapsed || 0)) * 1000, on: !c.stale },
    LAST_OFFICE: { employees: agents, actions: { approve: {
      on: c.on || 0, until: c.untilAbs || settingsNow + (c.until || 0), fps: c.mine || c.keyOf ? ["settings-fp"] : [],
    } } },
    STALE: !!c.stale, STALE_SEC: c.staleSec || 0, OFFLINE: !!c.offline, NTOG_STATE: c.notify !== false,
    ATTN_SENT: {}, getCred: () => c.noCred ? null : ({ e: c.expiry == null ? 0 : settingsNow + c.expiry, d: "d_settings" }),
    apvStore: () => c.keyOf === "moved" ? { fp: "settings-fp", d: "d_old" } : c.keyOf === "unknown" ? { fp: "settings-fp" } : { fp: "settings-fp", d: "d_settings" },
    pushSupported: () => c.available !== false, errOf: e => e.err,
    document: { getElementById: id => nodes[id] || null },
    fetch() { assert.fail(`${c.name}: 状態の描画で通信を開始した`); },
    WebSocket() { assert.fail(`${c.name}: 状態の描画でWebSocketを開始した`); },
  };
  const settings = runInNewContext(settingsSource + "\n({settingsState,paintSettingsState,attnKey,attnSig,askMinOf})", context);
  if (c.sent) {
    const e = agents[0];
    context.ATTN_SENT[settings.attnKey(e)] = { t: SettingsDate.now(), sig: settings.attnSig(e), m: settings.askMinOf(e) };
    if (c.changed) e.ask.id8 = "89abcdef";
  }
  const state = settings.settingsState(), reasons = c.reasons || [];
  assert.deepEqual([state.approveSoon, state.notifyOff, state.credSoon],
    [reasons.includes("approve"), reasons.includes("notify"), reasons.includes("cred")], `${c.name}: 点の3条件`);
  assert.equal(state.mine, !c.noCred, `${c.name}: ペアリング済み（R118: 許可鍵は見ない）`);
  assert.equal(state.available, c.available !== false, `${c.name}: 通知対応`);
  assert.equal(state.sync, c.sync || "3 秒前", `${c.name}: Mac同期`);
  if (c.approve) assert.equal(state.approve, c.approve, c.name);
  if (c.cred) assert.equal(state.cred, c.cred, c.name);
  assert.equal(state.notify, c.notifyText || (c.notify === false ? "OFF" : "購読中"), c.name);
  const approveOn = c.on === 1 && (c.until > 0 || !!c.untilAbs);
  if (approveOn && !c.untilAbs) {
    const d = new Date((settingsNow + c.until) * 1000);
    const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    // R112-P: ON は終了時刻と残り（1799 秒・1800 秒＝あと 30 分）
    assert.equal(state.approve, `ON（${time} まで・あと ${Math.ceil(c.until / 60)} 分）`, `${c.name}: 終了時刻と残り`);
  }
  if (c.expired) {
    const d = new Date((settingsNow + c.until) * 1000);
    assert.equal(state.approve, `期限切れ（${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}）`, `${c.name}: 期限切れの日時`);
  }
  settings.paintSettingsState();
  assert.equal(nodes.tb_set_dot.hidden, reasons.length === 0, `${c.name}: 設定タブの点`);
  // R112-P（本人 2026-10-07・意図して変更）: スマホから許可の行は OFF・期限切れ・未登録でも朱（ここから許可できない状態）。設定タブの点は従来の 3 条件のまま
  assert.deepEqual(settingsRowIds.map(id => nodes[id].classList.contains("attn")),
    [!!c.stale || !!c.offline, reasons.includes("approve") || !approveOn, reasons.includes("notify"), reasons.includes("cred")], `${c.name}: 状態行の朱`);
  assert.deepEqual(settingsRowIds.map(id => nodes[id].textContent),
    [`Mac 最終同期: ${state.sync}`, `スマホから許可: ${state.approve}`, `通知: ${state.notify}`, `この端末の登録: ${state.cred}`], `${c.name}: 4行の値`);
  assert.equal(nodes.tb_set.getAttribute("aria-label"), "設定" + (reasons.length ? " — 確認が必要" : ""), `${c.name}: 設定タブの読み上げ`);
  assert.equal(nodes.st_nt_btn.disabled, c.available === false, `${c.name}: 非対応端末の通知操作`);
  assert.equal(nodes.st_nt_btn.textContent, c.available === false ? "使えません" : c.notify === false ? "ON にする" : "OFF にする", `${c.name}: 通知操作`);
  assert.equal(nodes.st_nt_hint.textContent, state.notify, `${c.name}: 通知行の状態`);
  assert.equal(nodes.st_nt_btn.getAttribute("aria-pressed"), c.available !== false && c.notify !== false ? "true" : "false", `${c.name}: 通知の読み上げ`);
  context.T = (ja, en) => en;
  settings.paintSettingsState();
  assert.ok(!/[\u3040-\u30ff\u3400-\u9fff]/u.test(settingsRowIds.map(id => nodes[id].textContent).join(" ")), `${c.name}: 英語の状態行`);
}
console.log(`PWA R103-3: 状態4行・点3条件/境界/送信済除外・状態行の朱との相関・英語 (${settingsCases.length}ケース) OK`);

// R116-S1（本人 2026-10-09「ごちゃごちゃ」「回りくどい」）→R118（本人裁定 2026-10-13「常に ON」）: 設定の ┤ 許可 ├ は 1 行（いまの状態）＋
// 主ボタン 1 つ（ON にする／OFF にする）。案内の文は出さず、止まったら失敗の 8 行の 1 つだけ（apvFailText）。許可鍵の登録の連鎖は撤去。
const enrollNodes = Object.fromEntries(["st_apv_hint", "st_apv_now", "st_apv_go"].map(id => [id, settingsNode()]));
const enrollRst = [], enrollSync = [];
const enrollContext = {
  Date: SettingsDate, T: ja => ja, APV: null, document: { getElementById: () => null },
  apvEl: id => enrollNodes[id], apvRemoteSet: (...a) => enrollRst.push(a), apvSync: () => enrollSync.push(1),
  apvNote() {}, playSE() {}, LAST_OFFICE: { actions: { approve: { on: 0 } } },
};
const enrollment = runInNewContext(sourceBetween("function apvEnrNote(", "function apvRemoteCanonical(") + "\n" +
  sourceBetween("function apvFailText(", "/* 危険・注意の理由は種類で言う") +
  "\n({apvEnrNote,apvEnrollState,apvFailNote,apvRstResult,set busy(v){APV_BUSY=v},set rst(v){APV_RST_REQ=v}})", enrollContext);
{
  const ctx = enrollContext, N = enrollNodes, sec = settingsNow;
  const paint = (on, until) => {
    enrollment.busy = null;
    ctx.LAST_OFFICE = { actions: { approve: { on, until: until ?? (on ? 4102444800 : 0) } } };
    enrollment.apvEnrNote("");
    return { now: N.st_apv_now.textContent, go: N.st_apv_go.textContent, act: N.st_apv_go.getAttribute("data-act"),
      hint: N.st_apv_hint.textContent, hintHidden: N.st_apv_hint.hidden };
  };
  // R118（本人裁定 2026-10-13「常に ON」）: 主ボタンは「ON にする／OFF にする」の 1 つ（登録・12 時間・1h・4h・登録し直すは撤去）・
  // ON は期限なし（期限つきの古い ON だけ「〜まで」）
  const machine = [
    { on: 0, go: "ON にする", act: "on", now: /^OFF$/ },
    { on: 1, go: "OFF にする", act: "off", now: /^ON（期限なし）$/ },
    { on: 1, until: sec + 3600, go: "OFF にする", act: "off", now: /^ON · \d\d:\d\d まで$/ },
  ];
  for (const c of machine) {
    const q = paint(c.on, c.until), name = `${c.on ? "ON" : "OFF"}${c.until ? "（期限つきの古い ON）" : ""}`;
    assert.equal(q.go, c.go, `${name}: 主ボタン`); assert.equal(q.act, c.act, `${name}: 主ボタンの操作`);
    assert.match(q.now, c.now, `${name}: いまの状態の 1 行`);
    assert.equal(q.hintHidden, true, `${name}: 平常時に案内の 1 行を出した: ${q.hint}`);
  }
  // 押した直後は主ボタンを止め、「送信中…」
  paint(0); enrollment.busy = { kind: "on" }; enrollment.apvEnrollState();
  assert.equal(N.st_apv_go.disabled, true); assert.equal(N.st_apv_go.textContent, "送信中…");
  enrollment.busy = null; enrollment.apvEnrollState(); assert.equal(N.st_apv_go.disabled, false);
  // ON の結果（granted）: 案内は出さない・主ボタンは「OFF にする」・カードを合わせ直す（apvSync）
  paint(0); enrollSync.length = 0;
  enrollment.rst = { reqId: "r-on", on: true, at: SettingsDate.now(), src: "main" };
  ctx.LAST_OFFICE = { actions: { approve: { on: 1, until: 4102444800 } } };
  enrollment.apvRstResult([{ reqId: "r-on", state: "running" }]);
  assert.equal(enrollSync.length, 0, "Mac の結果（granted）の前に終わった扱いにした");
  enrollment.apvRstResult([{ reqId: "r-on", state: "granted", output: "4102444800" }]);
  assert.equal(N.st_apv_hint.hidden, true); assert.equal(N.st_apv_go.textContent, "OFF にする"); assert.equal(N.st_apv_now.textContent, "ON（期限なし）");
  assert.equal(enrollSync.length, 1, "ON の後にカードを合わせ直さない");
  // ON の拒否（not-enrolled・rate）と返事なし（90 秒）
  ctx.LAST_OFFICE = { actions: { approve: { on: 0, until: 0 } } };
  enrollment.rst = { reqId: "r-ne", on: true, at: SettingsDate.now(), src: "main" };
  enrollment.apvRstResult([{ reqId: "r-ne", state: "refused", output: "not-enrolled" }]);
  assert.equal(N.st_apv_hint.textContent, "! このスマホの登録が無効です");
  enrollment.rst = { reqId: "r-rate", on: true, at: SettingsDate.now(), src: "main" };
  enrollment.apvRstResult([{ reqId: "r-rate", state: "refused", output: "rate" }]);
  assert.equal(N.st_apv_hint.textContent, "! 少し待ってからもう一度");
  enrollment.rst = { reqId: "r-late", on: true, at: SettingsDate.now() - 91000, src: "main" };
  enrollment.apvRstResult([]);
  assert.equal(N.st_apv_hint.textContent, "! Mac に届いていません（スリープ中かも）"); assert.equal(N.st_apv_go.textContent, "ON にする");
  // T(ja, en) の英語側に日本語が無い
  ctx.T = (ja, en) => en;
  for (const c of machine) { paint(c.on, c.until); assert.ok(!/[\u3040-\u30ff\u3400-\u9fff]/u.test(N.st_apv_now.textContent + N.st_apv_go.textContent + N.st_apv_hint.textContent), `英語の主の段に日本語: ${c.on}`); }
  ctx.T = ja => ja;
}
console.log("PWA R116-S1→R118: 設定の ┤ 許可 ├（1 行＋主ボタン「ON にする／OFF にする」1 つ・期限なし・処理中の停止）・ON の結果・失敗は 8 行の 1 つ OK");

// R112-P: 2 問以上・複数選択の質問への答えの文（配信元の関数）。日本語でも英語でも同じ形「1. 問い: 答え」（複数は「, 」）＋最後に「補足: 」・
// 問いの中身が無い（古い Mac）・1 問の単一選択は対象外（従来の 1 タップ）。
// R112-P2（別モデルレビュー）: 答えの文は Mac の受け手（daemon の short・hook の clip）の 2000 字（コードポイント）と 4000 字の封筒の両方に収める
// （問いを 60 字に切る→補足を削る・削ったら info.cut）。選択の鍵は答える相手の session＋askId（代表の object でも employees の object でも同じ）・
// 捨てるのはあなた待ちの全セッション（束の中も）から消えたときだけ。未送信の選択は sessionStorage に控える（30 分・送った箱と空の箱は控えない）。
{
  const store = new Map();
  const sb = {
    QPICK: {}, QPICK_SS: "aioffice.qpick", QPICK_TTL: 30 * 60000, QPICK_MAX: 2000, LAST_OFFICE: {},
    sessionStorage: { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) },
    officeAgents: o => ((o && Array.isArray(o.roster) && o.roster.length) ? o.roster : ((o && o.employees) || [])),
    needsAttn: e => !!(e && (e.question || (e.ask && e.ask.kind === "question"))),
    isPlanApproval: e => !!(e && e.ask && e.ask.tool === "ExitPlanMode"), sessKind: () => "question",
    questionMulti: e => (Number(e.ask.qn) || 1) > 1 || e.ask.multi === 1,
    askId8: e => (e && e.ask && /^[0-9a-f]{8}$/.test(e.ask.id8 || "") ? e.ask.id8 : ""),
  };
  const pick = runInNewContext(sourceBetween("function askQuestions(", "function qpickPaint(") +
    "\n({askQuestions,qpickOn,qpickKey,qpickText,qpickSel,qpickLeft,qpickLive,qpickPrune,qpickLoad,qpickSave,QPICK})", sb);
  const q3 = { session: "kat-q3", question: "どの方式?", ask: { kind: "question", qn: 3, multi: 1, id8: "a1b2c3d4", questions: [
    { q: "どの方式?", options: ["方式A (Recommended)", "方式B"], multi: 0 },
    { q: "どの機能を\n入れる?", options: ["検索", "通知", "履歴", "共有", "余分な5個目"], multi: 1 },
    { q: "いつ出す?", options: ["今日", "来週"], multi: 0 }] } };
  const qs = pick.qpickOn(q3);
  assert.equal(qs.length, 3);
  assert.deepEqual(qs.map(x => x.multi), [false, true, false]);
  assert.equal(qs[1].q, "どの機能を 入れる?", "問いの改行を 1 行へ");
  assert.equal(qs[1].o.length, 4, "選択肢は 4 個まで");
  assert.deepEqual([qs[0].o[0].label, qs[0].o[0].raw, qs[0].o[0].rec], ["方式A", "方式A (Recommended)", true]);
  assert.equal(pick.qpickText(q3, qs, [[0], [0, 2], [1]], ""),
    "1. どの方式?: 方式A (Recommended)\n2. どの機能を 入れる?: 検索, 履歴\n3. いつ出す?: 来週");
  const info0 = {};
  assert.equal(pick.qpickText(q3, qs, [[1], [3], [0]], "  急ぎで  ", info0), "1. どの方式?: 方式B\n2. どの機能を 入れる?: 共有\n3. いつ出す?: 今日\n補足: 急ぎで");
  assert.equal(info0.cut, false, "補足を削っていないのに cut");
  // 選んだ状態は答える相手の session＋askId で持つ（同じ相手の同じ要求なら再描画で残る・要求が変われば別の箱）。あなた待ちから消えたら捨てる
  const s0 = pick.qpickSel(q3, qs); s0.p[0].push(1);
  assert.equal(pick.qpickSel(q3, qs), s0); assert.equal(pick.qpickLeft(s0), 2);
  assert.notEqual(pick.qpickSel({ ...q3, ask: { ...q3.ask, id8: "deadbeef" } }, qs), s0, "別の要求の選択を引き継いだ");
  assert.notEqual(pick.qpickSel({ ...q3, session: "kat-q3-other" }, qs), s0, "同じ印でも別のセッションの選択を引き継いだ");
  assert.equal(pick.qpickSel({ ...q3, question: "（束ねた行の合成で違う文）", projectId: "kat-proj" }, qs), s0,
    "同じ相手（session＋askId）の選択が object の違い（代表／employees）で別の箱になった");
  delete pick.QPICK[pick.qpickKey({ ...q3, ask: { ...q3.ask, id8: "deadbeef" } })];
  delete pick.QPICK[pick.qpickKey({ ...q3, session: "kat-q3-other" })];
  pick.qpickPrune([q3]); assert.equal(Object.keys(pick.QPICK).length, 1, "生きている要求の選択を捨てた");
  pick.qpickPrune([]); assert.equal(Object.keys(pick.QPICK).length, 0, "あなた待ちから消えた要求の選択が残る");
  // 束ねたカード（avatarMode=project）: 代表が B に替わっても、束の中の A（employees）があなた待ちなら A の選択は残る・A が消えたら捨てる
  const qa = { ...q3, session: "kat-pa", ask: { ...q3.ask, id8: "aa11aa11" } }, qb = { ...q3, session: "kat-pb", ask: { ...q3.ask, id8: "bb22bb22" } };
  const sa = pick.qpickSel(qa, qs); sa.p[0] = [1];
  sb.LAST_OFFICE = { roster: [{ ...qb, projectId: "kat-proj", sessions: [{ session: "kat-pa" }, { session: "kat-pb" }] }], employees: [qa, qb] };
  pick.qpickPrune(pick.qpickLive());
  assert.equal(pick.qpickSel(qa, qs), sa, "代表が替わったら束の中の相手（非代表）の選択を捨てた");
  assert.equal(JSON.stringify(pick.qpickSel(qb, qs).p), "[[],[],[]]", "代表の選択が別の相手に付いた");
  sb.LAST_OFFICE = { roster: [{ ...qb, projectId: "kat-proj" }], employees: [qb] };
  pick.qpickPrune(pick.qpickLive());
  assert.ok(!Object.keys(pick.QPICK).includes(pick.qpickKey(qa)), "あなた待ちから消えた束の中の相手の選択が残る");
  // 控え（sessionStorage）: 選んだ箱だけ・送った箱と空の箱は控えない・30 分より古いものと形の違うものは戻さない・全部消えたら控えも消す
  for (const k of Object.keys(pick.QPICK)) delete pick.QPICK[k];
  const now = Date.now(), ka = pick.qpickKey(qa), kb = pick.qpickKey(qb), kc = pick.qpickKey({ ...qa, session: "kat-pc" });
  pick.QPICK[ka] = { p: [[1], [0, 2], []], at: now };
  pick.QPICK[kb] = { p: [[0], [], []], at: now, sent: 1 };
  pick.QPICK[kc] = { p: [[], [], []], at: now };
  pick.qpickSave();
  const saved = JSON.parse(store.get("aioffice.qpick"));
  assert.deepEqual(Object.keys(saved.m), [ka], "控えるのは未送信で何か選んだ箱だけ");
  assert.deepEqual(saved.m[ka], { p: [[1], [0, 2], []], at: now });
  assert.ok(!JSON.stringify(saved).includes("どの方式"), "控えに問いの本文が載った");
  assert.deepEqual(Object.keys(pick.qpickLoad()), [ka]);
  store.set("aioffice.qpick", JSON.stringify({ v: 1, m: { [ka]: { p: [[1], [], []], at: now - 31 * 60000 }, [kb]: { p: [[0], [], []], at: now - 60000 } } }));
  assert.deepEqual(Object.keys(pick.qpickLoad()), [kb], "30 分より古い控えを戻した／新しい控えを戻さない");
  store.set("aioffice.qpick", "{壊れた");
  assert.equal(JSON.stringify(pick.qpickLoad()), "{}", "壊れた控えで落ちる");
  pick.QPICK[ka] = { p: [[1, 0], [9], []], at: now };
  assert.equal(JSON.stringify(pick.qpickSel(qa, qs).p), "[[],[],[]]", "形の違う控え（単一選択に 2 個・範囲外の番号）を使った");
  for (const k of Object.keys(pick.QPICK)) delete pick.QPICK[k];
  pick.QPICK[ka] = { p: [[1], [], []], at: now };
  pick.qpickSave(); assert.ok(store.has("aioffice.qpick"));
  sb.LAST_OFFICE = { employees: [] };
  pick.qpickPrune(pick.qpickLive());
  assert.ok(!store.has("aioffice.qpick"), "要求が消えたのに控えが残る");
  // 長い問い×8・長い補足でも 2000 字（コードポイント）と 4000 字の封筒に収める（問いは 60 字・補足は削る＝info.cut）
  const long = { session: "kat-long", question: "x", ask: { kind: "question", qn: 8, id8: "0badc0de", questions: Array.from({ length: 9 }, (_, i) => ({
    q: `問い${i}`.padEnd(200, "長"), options: ["選".repeat(40), "択".repeat(40), "肢".repeat(40), "候".repeat(40)], multi: 1 })) } };
  const lq = pick.qpickOn(long);
  assert.equal(lq.length, 8, "問いは 8 個まで");
  const linfo = {};
  const lt = pick.qpickText(long, lq, lq.map(() => [0, 1, 2, 3]), "補".repeat(3900), linfo);
  assert.ok(lt && Array.from(lt).length <= 2000, "答えの文が 2000 字（Mac の受け手の上限）を超えた");
  assert.ok(Array.from(JSON.stringify({ aioffice: 1, kind: "answer", ask: "0badc0de", text: lt })).length <= 4000, "4000 字の封筒を超えた");
  assert.ok(lt.split("\n").slice(0, 8).every((l, i) => l.startsWith(`${i + 1}. `) && Array.from(l.split(": ")[0]).length <= 60 + 3), "問いを 60 字に切っていない");
  assert.match(lt, /\n補足: 補+$/, "補足を削って残していない");
  assert.equal(linfo.cut, true, "補足を削ったのに cut が立たない");
  // 3 問＋2100 字の補足（シートの返信欄）: 問いは切らずに補足だけを削って 2000 字ちょうどに収める・絵文字（サロゲート）もコードポイントで数える
  const minfo = {};
  const mt = pick.qpickText(q3, qs, [[0], [0, 2], [1]], "😀".repeat(2100), minfo);
  assert.equal(Array.from(mt).length, 2000, "2000 字ちょうどまで補足を残していない");
  assert.ok(mt.startsWith("1. どの方式?: 方式A (Recommended)\n2. どの機能を 入れる?: 検索, 履歴\n3. いつ出す?: 来週\n補足: 😀"), "問い・答えの前半が崩れた");
  assert.equal(minfo.cut, true);
  // 問いを 60 字に切るだけで収まるなら補足は削らない（cut は立たない）
  const cinfo = {};
  const lt2 = pick.qpickText(long, lq, lq.map(() => [0, 1]), "短い補足", cinfo);
  assert.ok(lt2 && Array.from(lt2.split("\n")[0].split(": ")[0]).length === 60 + 3, "問いを 60 字に切っていない（切らないと 2000 字を超える形）");
  assert.ok(lt2.endsWith("\n補足: 短い補足") && cinfo.cut === false, "補足を削らずに収まるのに cut");
  // 対象外: 問いの中身が無い（古い Mac）・1 問の単一選択・壊れた問い（選択肢が無い）
  assert.equal(pick.qpickOn({ ...q3, ask: { ...q3.ask, questions: undefined } }), null);
  assert.equal(pick.qpickOn({ question: "q", ask: { kind: "question", qn: 1, questions: [{ q: "q", options: ["a", "b"], multi: 0 }] } }), null);
  assert.equal(pick.qpickOn({ question: "q", ask: { kind: "question", qn: 2, questions: [{ q: "q", options: ["a"], multi: 0 }, { q: "r", options: [], multi: 0 }] } }), null);
  assert.ok(pick.qpickOn({ question: "q", ask: { kind: "question", qn: 1, multi: 1, questions: [{ q: "q", options: ["a", "b"], multi: 1 }] } }), "1 問の複数選択も問いごとに選ぶ");
}
console.log("PWA R112-P/P2: 複数の問い・複数選択の答えの文（形・補足・2000 字と 4000 字・選択の鍵と保持と破棄・控え・対象外）OK");

// R116-S1（D3「状態は apvState の 1 本で決める」）: 判定表＝パスキー非対応・読み切れない形（remote≠1）→ Mac だけ／いまの端末 id の鍵が無い
// → このスマホで許可できるようにする／OFF・期限切れ → ON にして確認（caps.approve は「封じられる ∧ ON ∧ 有効な鍵」＝ON を caps より先に見る）／
// ON なのに Mac が封じられない → Mac だけ／封書待ち → loading／封書の段 remote・review → タップ・mac（全文あり）→ Face ID・mac（全文なし）→ Mac だけ。
// 押せるかの判定は R118（本人裁定 2026-10-13）で段を問わず 1 タップ（apvTapAllowed は mac も・許可鍵は要らない）。
{
  const stateSource = sourceBetween("function apvIsPerm(", "function apvFocusItem(") + "\n" + sourceBetween("function apvLevelOk(", "function apvTapPayload(");
  const perm = (remote = 1, tool = "Bash") => ({ session: "s", ask: { kind: "permission", tool, remote, id8: "01234567" } });
  const P = (level, extra = {}) => ({ level, macOnly: 0, inputCanon: "{}", inputSha: "a".repeat(64), ...extra });
  const cases = [
    { name: "読み切れない形（remote=0）", e: perm(0), want: "mac-only" },
    { name: "OFF（caps も 0）", on: false, caps: false, want: "need-on" },
    { name: "ON・Mac が封じられない", on: true, caps: false, want: "mac-only" },
    { name: "ON・封書待ち", on: true, caps: true, want: "loading" },
    { name: "ON・remote", on: true, caps: true, page: P("remote"), want: "ready" },
    { name: "ON・review", on: true, caps: true, page: P("review"), want: "ready" },
    { name: "ON・mac（全文あり）＝R118 からタップ", on: true, caps: true, page: P("mac"), want: "ready" },
    { name: "ON・mac（Mac だけ）", on: true, caps: true, page: P("mac", { macOnly: 1 }), want: "mac-only" },
    { name: "ON・mac（全文なし）", on: true, caps: true, page: P("mac", { inputCanon: "" }), want: "mac-only" },
    { name: "ON・判定できない段", on: true, caps: true, page: P("unjudged"), want: "mac-only" },
    { name: "別の件の封書", on: true, caps: true, page: P("remote"), other: true, want: "loading" },
    { name: "計画の承認", e: perm(1, "ExitPlanMode"), want: "" },
    { name: "質問", e: { session: "s", ask: { kind: "question", tool: "AskUserQuestion" } }, want: "" },
  ];
  for (const c of cases) {
    const e = c.e || perm();
    const ctx = { APV_DONE: {}, attnSig: x => x.session + ":" + ((x.ask && x.ask.id8) || ""), isPlanApproval: x => !!(x && x.ask && x.ask.tool === "ExitPlanMode"),
      apvOnNow: () => !!c.on, apvView: () => ({ caps: !!c.caps }),
      APV: c.page ? { session: e.session, e: c.other ? { ...e, ask: { ...e.ask, id8: "89abcdef" } } : e, page: c.page } : null };
    const got = runInNewContext(stateSource + "\n({apvState})", ctx).apvState(e);
    assert.equal(got, c.want, `${c.name}: apvState`);
  }
  // R118: 押せるか（apvPressable）・送る直前の検算（seal）・状態（apvState）は端末のペアリングだけで決まる＝許可鍵とパスキーの経路は配信元に無い
  for (const w of ["apvMine", "apvKeyState", "apvSupported", "apvEnroll(", "apvEnrollFromBox", "apvStore", "navigator.credentials"]) {
    assert.ok(!source.includes(w), `R118: app.js に ${w} が残っている`);
  }
  assert.match(source, /function apvPressable\(P\)\{return apvTapAllowed\(P\)&&!!apvView\(\)\.caps\}/, "R118: 押せるかはタップの条件＋Mac の caps");
}
console.log("PWA R116-S1→R118: カードの状態は apvState 1 本（読み切れない形→Mac だけ／OFF を caps より先に／封書待ち／段を問わずタップ・登録と Face ID は無い）OK");

// R103-4: 通信と時計だけを差し替え、先読みの一回制限と破棄を配信元で検査する。
const answerIdSource = sourceBetween("function attnSig(", "function sentAt(");
const prefetchSource = [
  sourceBetween("function apvPrefetchKey(", "function apvDenyCurrent("),
  sourceBetween("function apvIsPerm(", "function apvState("),
  sourceBetween("function apvFocusItem(", "function apvSheetOpen("),
  sourceBetween("function canApproveFromPhone(", "function apvB64u("),
  sourceBetween("function apvView(", "function canApproveFromPhone("),
  answerIdSource,
].join("\n");
async function flushPwaTasks() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
const prefetchPermission = { session: "prefetch-permission", ask: { kind: "permission", remote: 1, tool: "Bash", id8: "1234abcd" } };
function prefetchHarness(options = {}) {
  const signed = [], requests = [], timers = [], pending = {};
  let sequence = 0, resolveSign;
  const agents = options.agents || [{ ...prefetchPermission, ask: { ...prefetchPermission.ask } }];
  const context = {
    Date: options.Date || SettingsDate, T: ja => ja, APV: null, APV_PREFETCH: {}, APV_RST_REQ: options.turningOff ? { on: false } : null,
    APV_DONE: {}, APV_FOCUS: null, isPlanApproval: () => false, sessKind: e => (e && e.ask && e.ask.kind === "permission") ? "perm" : "question",
    fail: !!options.fail,
    LAST_OFFICE: { employees: agents, actions: { caps: { approve: options.caps === false ? 0 : 1 },
      approve: { on: options.on === false ? 0 : 1, until: settingsNow + (options.until ?? 3600), fps: ["prefetch-fixture"] } } },
    officeAgents: o => o.employees, officeNeed: a => a, sessionAgent: id => context.LAST_OFFICE.employees.find(e => e.session === id),
    actionsView: () => context.LAST_OFFICE.actions, credExpired: () => !!options.expiredCred,
    getCred: () => options.noCred ? null : { t: "fixture", d: "d_fixture" }, apvRand: () => `prefetch-${++sequence}`,
    dlgPending: () => pending, dlgSavePending() {}, poll() {}, apvNote() {},
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    sign: (cred, session, text) => {
      signed.push({ session, text });
      return options.delaySign ? new Promise(resolve => { resolveSign = resolve; }) : Promise.resolve({ text });
    },
    fetch: (url, init) => { requests.push({ url, init }); return Promise.resolve({ json: () => Promise.resolve({ ok: !context.fail }) }); },
  };
  const api = runInNewContext(prefetchSource + "\n({apvPrefetch,apvPrefetchCurrent,apvPrefetchClear})", context);
  return { context, api, signed, requests, pending, timers, resolveSign: () => resolveSign({ text: signed[0].text }) };
}
// R118（本人裁定 2026-10-13）: 許可はペアリングだけで通る＝ON・caps・ペアリングがあれば先読みする（許可鍵の控えは無い）
{
  const h = prefetchHarness();
  h.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(h.signed.length, 1, "ON・caps・ペアリングがあるのに先読みしない（R118）");
}
for (const options of [
  { on: false }, { caps: false }, { until: 0 }, { until: -1 },
  { expiredCred: true }, { noCred: true }, { turningOff: true },
  { agents: [{ ...prefetchPermission, ask: { ...prefetchPermission.ask, remote: 0 } }] },
  { agents: [{ ...prefetchPermission, ask: { ...prefetchPermission.ask, id8: "" } }] },
]) {
  const h = prefetchHarness(options);
  h.api.apvPrefetch(); h.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(h.signed.length, 0, `先読みしてはいけない条件で署名した: ${JSON.stringify(options)}`);
  assert.equal(h.requests.length, 0, "先読みしてはいけない条件で中継へ送った");
}
{
  const h = prefetchHarness({ agents: [prefetchPermission, { ...prefetchPermission, session: "second-permission" }] });
  h.api.apvPrefetch(); h.api.apvPrefetch(); await flushPwaTasks(); h.api.apvPrefetch();
  assert.equal(h.requests.length, 1, "同じ askId を複数回先読みした");
  assert.equal(h.signed.length, 1, "先頭以外の許可も先読みした");
  const request = JSON.parse(h.signed[0].text), key = "prefetch-permission\n1234abcd", rec = h.context.APV_PREFETCH[key];
  assert.deepEqual(request, { aioffice: 1, kind: "ask", reqId: rec.req, session: prefetchPermission.session });
  assert.equal(h.signed[0].session, `dlg-${rec.req}`, "先読みを封書の dlg- 要求として送っていない");
  assert.equal(h.pending[rec.req].prefetch, 1);
  rec.page = { kind: "ask", fixture: "sealed input" };
  const req = rec.req;
  h.context.LAST_OFFICE.actions.approve.on = 0; h.api.apvPrefetch();
  assert.equal(rec.page, null, "OFF にした後も許可の全文を保持した");
  assert.equal(rec.req, null); assert.ok(!(req in h.pending), "OFF にした要求の受け口が残った");
  h.context.LAST_OFFICE.actions.approve.on = 1; h.api.apvPrefetch(); h.api.apvPrefetch(); await flushPwaTasks();
  // R116-S0（意図した反転）: 以前は捨てた控えが残り、ON に戻しても同じ askId を二度と先読みしなかった＝開くたびに Mac への往復を待った。
  // いまは OFF で控えごと消し、ON に戻った次の機会に 1 回だけ取り直す（2 回呼んでも 1 通）
  assert.equal(h.requests.length, 2, "OFF→ON で捨てた先読みを取り直していない／二重に取り直した");
  h.context.LAST_OFFICE.employees = []; h.api.apvPrefetch();
  assert.equal(Object.keys(h.context.APV_PREFETCH).length, 0, "終了した要求の控えを捨てていない");
}
// R116-S1（D3「先読みは、いまの 1 件と次の 1 件」）: 先頭が質問でも、その後ろの許可を 1 通だけ先読みする（以前は先頭だけを見て 0 通）。
// Mac の封書の箱は端末ごとに 1 通＝同時に 2 件取りに行かない: 今の件の全文が届いてから次の件を取りに行く・2 件より先は取らない
{
  const h = prefetchHarness({ agents: [{ session: "first-question", ask: { kind: "question", id8: "abcd1234" } }, prefetchPermission] });
  h.api.apvPrefetch(); h.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(h.requests.length, 1, "質問の後ろの許可を先読みしない／二重に先読みした");
  assert.equal(JSON.parse(h.signed[0].text).session, prefetchPermission.session);
}
{
  const third = { ...prefetchPermission, session: "third-permission", ask: { ...prefetchPermission.ask, id8: "3333cccc" } };
  const second = { ...prefetchPermission, session: "second-permission", ask: { ...prefetchPermission.ask, id8: "2222bbbb" } };
  const h = prefetchHarness({ agents: [prefetchPermission, second, third] });
  h.api.apvPrefetch(); await flushPwaTasks(); h.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(h.requests.length, 1, "今の件の全文が届く前に次の件を取りに行った（Mac の箱で上書きされる）");
  h.context.APV_PREFETCH["prefetch-permission\n1234abcd"].page = { kind: "ask" };
  h.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(h.requests.length, 2, "今の件の全文が届いた後に次の件を取りに行かない");
  assert.equal(JSON.parse(h.signed[1].text).session, "second-permission");
  h.context.APV_PREFETCH["second-permission\n2222bbbb"].page = { kind: "ask" };
  h.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(h.requests.length, 2, "3 件目まで先読みした（焦点の件と次の件だけ）");
  // カードが結んでいる件（APV）は先読みしない（カード自身が取りに行く）＝次の 2 件目まで
  const h2 = prefetchHarness({ agents: [prefetchPermission, second] });
  h2.context.APV = { session: prefetchPermission.session, e: prefetchPermission, req: null, page: { kind: "ask" } };
  h2.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(h2.requests.length, 1); assert.equal(JSON.parse(h2.signed[0].text).session, "second-permission", "カードが結んだ件を先読みした");
  // カードが自分の封書を取りに行っている間は先読みしない（同時に取らない）。黙った取り直し（page を残したまま・inflight）も同じ（別モデルレビュー）
  const h3 = prefetchHarness({ agents: [prefetchPermission, second] });
  h3.context.APV = { session: "other", e: { session: "other" }, req: "card-req", page: null, inflight: "card-req" };
  h3.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(h3.requests.length, 0, "カードの封書の要求と同時に先読みした");
  const h4 = prefetchHarness({ agents: [prefetchPermission, second] });
  h4.context.APV = { session: "other", e: { session: "other" }, req: "card-refresh", page: { kind: "ask" }, inflight: "card-refresh" };
  h4.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(h4.requests.length, 0, "カードの黙った取り直し（全文は残したまま）と同時に先読みした＝Mac の箱で上書き");
}
console.log("PWA R116-S1: 先読みは焦点の件と次の件（質問の後ろも）・順に 1 件ずつ（全文が届いてから次）・カードの件と同時に取らない OK");
for (const change of ["ended", "off", "changed"]) {
  const h = prefetchHarness({ delaySign: true });
  h.api.apvPrefetch();
  const rec = Object.values(h.context.APV_PREFETCH)[0];
  if (change === "ended") h.context.LAST_OFFICE.employees = [];
  else if (change === "off") h.context.LAST_OFFICE.actions.approve.on = 0;
  else h.context.LAST_OFFICE.employees[0].ask.id8 = "abcd5678";
  assert.equal(h.api.apvPrefetchCurrent(rec), false, `${change}: 終了・OFF・別要求を生きた先読みとして扱った`);
  h.resolveSign(); await flushPwaTasks();
  assert.equal(h.requests.length, 0, `${change}: 署名待ちの間に無効になった要求を送った`);
}
for (const change of ["ended", "changed", "off"]) {
  const h = prefetchHarness(); h.api.apvPrefetch(); await flushPwaTasks();
  const rec = Object.values(h.context.APV_PREFETCH)[0], req = rec.req;
  rec.page = { kind: "ask", fixture: "sealed input" };
  if (change === "ended") h.context.LAST_OFFICE.employees = [];
  else if (change === "changed") h.context.LAST_OFFICE.employees[0].ask.id8 = "ffff5678";
  else h.context.LAST_OFFICE.actions.approve.on = 0;              // R118: 許可鍵の失効は効かない＝代わりに OFF
  h.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(rec.page, null, `${change}: 終了・別要求・OFF の全文を捨てていない`);
  assert.equal(rec.req, null); assert.ok(!(req in h.pending));
  if (change !== "off") assert.ok(!Object.values(h.context.APV_PREFETCH).includes(rec), `${change}: 古い要求の控えが残った`);
}
{
  const h = prefetchHarness(); h.api.apvPrefetch(); await flushPwaTasks();
  const rec = Object.values(h.context.APV_PREFETCH)[0], req = rec.req;
  rec.page = { kind: "ask" }; rec.expires = SettingsDate.now() - 1;
  h.api.apvPrefetch(); h.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(rec.page, null); assert.ok(!(req in h.pending));
  // R116-S0（意図した反転）: 150 秒の鮮度が切れた全文は捨てて取り直す（以前は捨てた控えが残って二度と取り直さなかった）。2 回呼んでも 1 通
  assert.equal(h.requests.length, 2, "150 秒の鮮度が切れた先読みを取り直していない／二重に取り直した");
  const again = Object.values(h.context.APV_PREFETCH)[0];
  assert.ok(again && again !== rec && again.req && again.req !== req, "取り直しが新しい要求になっていない");
  h.api.apvPrefetchClear(); assert.equal(Object.keys(h.context.APV_PREFETCH).length, 0);
}
// R116-S0: 署名中に OFF で捨てた先読みは控えごと消える＝ON に戻った次の機会に取り直す
{
  const h = prefetchHarness({ delaySign: true });
  h.api.apvPrefetch();
  h.context.LAST_OFFICE.actions.approve.on = 0;
  h.resolveSign(); await flushPwaTasks();
  assert.equal(h.requests.length, 0, "署名中に OFF にした先読みを送った");
  assert.equal(Object.keys(h.context.APV_PREFETCH).length, 0, "署名中に OFF で捨てた控えが残った（ON に戻しても取り直さない）");
  h.context.LAST_OFFICE.actions.approve.on = 1; h.api.apvPrefetch(); h.resolveSign(); await flushPwaTasks();
  assert.equal(h.requests.length, 1, "OFF で捨てた先読みを ON に戻した次の機会に取り直さない");
}
// R116-S0: 失敗（中継の拒否・60 秒届かない）は 150 秒の印＝status のたびに同じ失敗を繰り返さず、150 秒を過ぎたら取り直す
for (const how of ["relay", "silent"]) {
  const clock = { t: settingsNow * 1000 };
  class ClockDate extends Date { static now() { return clock.t; } }
  const h = prefetchHarness({ Date: ClockDate, fail: how === "relay", until: 7200 });
  h.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(h.requests.length, 1);
  if (how === "silent") {
    const wait = h.timers.find(t => t.ms === 60000);
    assert.ok(wait, "60 秒の待ちが無い"); clock.t += 60000; wait.fn();
  }
  const rec = Object.values(h.context.APV_PREFETCH)[0], t0 = clock.t;
  assert.ok(rec && rec.req === null && rec.page === null && rec.retryAt === t0 + 150000, `${how}: 失敗した先読みに 150 秒の印が無い`);
  for (const dt of [0, 1000, 60000, 149999]) { clock.t = t0 + dt; h.api.apvPrefetch(); await flushPwaTasks(); }
  assert.equal(h.requests.length, 1, `${how}: 失敗した先読みを 150 秒より前に取り直した（status のたびに同じ失敗を繰り返す）`);
  h.context.fail = false; clock.t = t0 + 150000; h.api.apvPrefetch(); h.api.apvPrefetch(); await flushPwaTasks();
  assert.equal(h.requests.length, 2, `${how}: 失敗から 150 秒たっても取り直さない／二重に取り直した`);
}
console.log("PWA R103-4/R116-S0: 許可先読み1回・OFF/鍵なし/期限/先頭限定・終了と署名中の変更で破棄・捨てたら次の機会に取り直す・失敗は 150 秒の印 OK");

// R116-S0b（反証 low 2 件）: 先読みの鮮度と取り直しはスマホの時計の上で要求（reqAt＝Mac が封じるより前）から測る＝Mac の時計とのずれに依らない。
// 全文を使えるのは封書の寿命（e−iat＝既定 90 秒）の内、取り直しは要求から 150 秒（設計の「150 秒で取り直し」）。以前は Mac の時計の iat+90 を
// スマホの時計で比べた＝ずれが無くても 90 秒ごと、スマホが進んでいると status ごとに取り直した（周期に下限が無かった）。届く処理は本物の onApvItem。
{
  // R116-S1b: 封書を開く 3 か所は openSealed（今の秘密で開けなければ延長の直前の秘密でも試す）を通る＝本物を一緒に動かす
  const onApvSource = sourceBetween("function openSealed(", "var REKEY=") + "\n"
    + sourceBetween("function onApvItem(", "function openApprove(");
  function arrivalHarness(clock) {
    class ClockDate extends Date { static now() { return clock.t; } }
    const h = prefetchHarness({ Date: ClockDate, until: 7200 }), box = { page: null };
    Object.assign(h.context, { apvDiag() {}, apvErrText: (x) => x, apvRender() {}, apvPageNote() {}, ensureDlg: () => Promise.resolve(true),
      window: { __dlg: { openBlob: () => Promise.resolve(box.page) } } });
    const api = runInNewContext(onApvSource + "\n({onApvItem,apvPrefetchUsable})", h.context);
    return { h, api, box };
  }
  async function arrive(t, rec, clock, skew) {
    const iat = Math.floor(clock.t / 1000) - skew;           // Mac の時計（スマホより skew 秒遅れている）で封じた時刻
    t.box.page = { kind: "ask", session: prefetchPermission.session, askId: "1234abcd" + "0".repeat(24), iat };
    t.api.onApvItem({ id: rec.req, e: iat + 90 }, { session: prefetchPermission.session }, { s: "", d: "" }, Math.floor(clock.t / 1000));
    await flushPwaTasks();
  }
  for (const skew of [0, 85, -85]) {
    const clock = { t: settingsNow * 1000 }, t = arrivalHarness(clock), t0 = clock.t;
    t.h.api.apvPrefetch(); await flushPwaTasks();
    assert.equal(t.h.requests.length, 1);
    const rec = Object.values(t.h.context.APV_PREFETCH)[0];
    clock.t = t0 + 1000; await arrive(t, rec, clock, skew);
    assert.ok(rec.page && rec.fresh === t0 + 90000 && rec.expires === t0 + 150000 && rec.at === t0,
      `ずれ ${skew} 秒: 鮮度と取り直しが要求から 90／150 秒でない: ${JSON.stringify({ page: !!rec.page, fresh: rec.fresh - t0, expires: rec.expires - t0, at: rec.at - t0 })}`);
    for (const [dt, usable] of [[1000, true], [60000, true], [89999, true], [90000, false], [120000, false], [149999, false]]) {
      clock.t = t0 + dt; t.h.api.apvPrefetch(); await flushPwaTasks();
      assert.equal(t.api.apvPrefetchUsable(rec), usable, `ずれ ${skew} 秒: 要求から ${dt} ms で全文を${usable ? "使えない" : "まだ使う"}`);
    }
    assert.equal(t.h.requests.length, 1, `ずれ ${skew} 秒: 要求から 150 秒より前に取り直した（周期に下限が無い）`);
    assert.equal(rec.page, null, `ずれ ${skew} 秒: 鮮度の切れた全文を持ち続けた`);
    clock.t = t0 + 150000; t.h.api.apvPrefetch(); t.h.api.apvPrefetch(); await flushPwaTasks();
    assert.equal(t.h.requests.length, 2, `ずれ ${skew} 秒: 要求から 150 秒で取り直さない／二重に取り直した`);
  }
  // 封書の寿命を過ぎてから届いた（要求から 91 秒）＝全文は使わず失敗の 150 秒の印（status のたびに取り直さない）
  {
    const clock = { t: settingsNow * 1000 }, t = arrivalHarness(clock), t0 = clock.t;
    t.h.api.apvPrefetch(); await flushPwaTasks();
    const rec = Object.values(t.h.context.APV_PREFETCH)[0];
    clock.t = t0 + 91000; await arrive(t, rec, clock, 0);
    assert.ok(rec.page === null && rec.retryAt === t0 + 91000 + 150000 && !t.api.apvPrefetchUsable(rec),
      `封書の寿命を過ぎて届いた全文を使った／失敗の印が無い: ${JSON.stringify({ page: !!rec.page, retry: rec.retryAt - t0 })}`);
    for (const dt of [92000, 150000, 240999]) { clock.t = t0 + dt; t.h.api.apvPrefetch(); await flushPwaTasks(); }
    assert.equal(t.h.requests.length, 1, "寿命を過ぎて届いた先読みを 150 秒の印より前に取り直した");
  }
}
console.log("PWA R116-S0b: 先読みの鮮度（封書の寿命 90 秒）と取り直し（150 秒）は要求からスマホの時計で測る＝Mac との時計のずれ 0・±85 秒で同じ周期 OK");

// 中継の受付成功だけでは「Mac が受け付けた」に進めず、対象の新しい status で三段を進める。
const answerFitSource = sourceBetween("function answerTextFits(", "// R103-4: 送信行もメモリだけ。");
function outboxNode(className = "") {
  const attrs = new Map(), events = new Map();
  return { className, children: [], textContent: "", hidden: false, scrollTop: 0, scrollHeight: 200, clientHeight: 200,
    setAttribute: (name, value) => attrs.set(name, value), getAttribute: name => attrs.get(name),
    addEventListener: (name, fn) => events.set(name, fn), trigger: name => events.get(name)?.(),
    appendChild(node) { this.children.push(node); return node; },
    contains(node) { return this === node || this.children.some(child => child.contains(node)); },
    querySelector(selector) {
      for (const child of this.children) {
        if (child.className.split(" ").includes(selector.slice(1))) return child;
        const found = child.querySelector(selector); if (found) return found;
      }
      return null;
    },
    // 検収: termRestoreSends は描き直した本文の ❯ 行（.tl.u:not(.tsend) .tb）を数える＝この簡易の木には該当が無い（空）
    querySelectorAll() { return []; },
  };
}
function outboxHarness(options = {}) {
  const original = { session: "outbox-target", pending: !!options.pending,
    ...(options.ask ? { ask: { kind: "permission", id8: "abcd1234" } } : {}) };
  const body = outboxNode(), sent = [], timers = [], nodes = [];
  const context = {
    TERM: { session: original.session, e: original, sends: [] }, SENDING: false, STALE: false, OFFLINE: false,
    LAST_ST: { ts: 100 }, LAST_OFFICE: { employees: [original, { session: "outbox-other", pending: false }] },
    Date: SettingsDate, T: ja => ja, officeAgents: o => o.employees,
    sessionAgent: id => context.LAST_OFFICE.employees.find(e => e.session === id),
    newReqId: () => `outbox-${nodes.length}`, termEl: () => body, termAtBottom: () => true, termSchedule() {},
    el: (tag, cls, text) => { const n = outboxNode(cls || ""); n.textContent = text || ""; nodes.push(n); return n; },
    termLine: (cls, mark, contents) => { const n = outboxNode(`tl ${cls}`); n.appendChild(contents); return n; },
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    send: (text, e, signed) => { sent.push({ text, e }); signed({ nonce: "fixture-nonce" }); return Promise.resolve(options.failed !== true); },
  };
  const api = runInNewContext(answerIdSource + "\n" + answerFitSource + "\n" + sourceBetween("function termSendState(", "// 生きている最後の q 行") +
    "\n({termSubmit,termOutboxStatus})", context);
  function frame(ts, pending) { context.LAST_ST = { ts }; context.LAST_OFFICE.employees[0] = { ...original, pending }; api.termOutboxStatus(); }
  return { context, api, sent, timers, original, frame };
}
{
  const h = outboxHarness(); await h.api.termSubmit("fixture instruction", h.original);
  const tx = h.context.TERM.sends[0];
  assert.equal(tx.relayOk, true); assert.equal(tx.state, "sending", "d.ok だけで Mac 受付を表示した");
  assert.equal(tx.row.querySelector(".tsendstate").textContent, "送信中");
  h.frame(100, true); assert.equal(tx.state, "sending", "送信前と同じ status を受付証拠にした");
  h.frame(101, true); assert.equal(tx.state, "accepted"); assert.equal(tx.row.querySelector(".tsendstate").textContent, "Mac が受け付けた");
  h.frame(102, false); assert.equal(tx.state, "accepted", "届いた段へ入る前の受付表示が無い");
  assert.equal(h.timers[0].ms, 150); h.timers[0].fn();
  assert.equal(tx.state, "delivered"); assert.equal(tx.row.querySelector(".tsendstate").textContent, "届いた ✓");
  assert.equal(tx.row.querySelector(".tsendretry").hidden, true);
}
{
  const h = outboxHarness({ pending: true }); await h.api.termSubmit("new instruction", h.original);
  const tx = h.context.TERM.sends[0];
  h.frame(101, true); assert.equal(tx.state, "sending", "送信前の pending を新しい送信の受付にした");
  h.frame(102, false); assert.equal(tx.state, "sending", "前の pending が消えただけで配達済みにした");
  assert.equal(tx.baselinePending, false); assert.equal(h.timers.length, 0);
  h.frame(103, true); assert.equal(tx.state, "accepted");
  h.frame(104, false); h.timers[0].fn(); assert.equal(tx.state, "delivered");
}
{
  const h = outboxHarness(); await h.api.termSubmit("target only", h.original);
  const tx = h.context.TERM.sends[0];
  h.context.LAST_ST.ts = 101; h.context.LAST_OFFICE.employees[1].pending = true; h.api.termOutboxStatus();
  h.context.LAST_ST.ts = 102; h.context.LAST_OFFICE.employees[1].pending = false; h.api.termOutboxStatus();
  assert.equal(tx.state, "sending", "別セッションの pending で受付・配達を表示した");
  for (const flag of ["STALE", "OFFLINE"]) {
    h.context[flag] = true; h.frame(103, true); assert.equal(tx.state, "sending", `${flag}: 新鮮でない状態で受付を表示した`); h.context[flag] = false;
  }
  h.context.LAST_OFFICE.employees = [h.context.LAST_OFFICE.employees[1]];
  h.api.termOutboxStatus(); assert.equal(tx.state, "sending", "対象が消えた状態で配達を推測した");
}
{
  const h = outboxHarness({ ask: true }); await h.api.termSubmit("refusal words", h.original);
  const tx = h.context.TERM.sends[0];
  h.original.ask.id8 = "ffff1234";
  assert.equal(h.sent[0].e.ask.id8, "abcd1234", "送信開始後の要求変更で答えの対象が差し替わった");
  h.api.termOutboxStatus(); assert.equal(tx.state, "sending", "古い status で要求終了を確定した");
  // 別モデルレビュー: 要求の印が変わったのは Mac で先に答えた可能性もある＝「届いた ✓」とは言わない
  h.context.LAST_ST.ts = 101; h.api.termOutboxStatus(); assert.equal(tx.state, "ended");
  assert.equal(tx.row.querySelector(".tsendstate").textContent, "要求は終わりました（Mac かこの答えで）");
  assert.equal(h.timers.length, 0, "要求の終了から届いた ✓ へ進めた");
}
{
  // 別モデルレビュー: 受信箱の印は 3 時間で期限切れでも消える＝送ってから 2.5 時間を過ぎて消えたら「期限切れ」（届いたと言わない）
  const h = outboxHarness(); await h.api.termSubmit("late instruction", h.original);
  const tx = h.context.TERM.sends[0];
  h.frame(101, true); assert.equal(tx.state, "accepted");
  tx.sentSec -= 9001; h.frame(102, false);
  assert.equal(tx.state, "expired"); assert.equal(h.timers.length, 0);
  assert.equal(tx.row.querySelector(".tsendstate").textContent, "! 期限切れ（届いていない可能性）");
}
{
  const h = outboxHarness({ failed: true }); await h.api.termSubmit("keep for retry", h.original);
  const tx = h.context.TERM.sends[0];
  assert.equal(tx.state, "failed"); assert.equal(tx.row.querySelector(".tsendretry").hidden, false);
  assert.equal(tx.row.querySelector(".tsendstate").textContent, "! 送れませんでした");
  h.frame(101, true); assert.equal(tx.state, "failed", "失敗した送信を status だけで復活させた");
  h.context.send = (text, e, signed) => { signed({ nonce: "retry-fixture" }); return Promise.resolve(true); };
  await h.api.termSubmit(tx.text, tx.e, tx);
  assert.equal(h.context.TERM.sends.length, 1, "再送で同じ送信の行を増やした");
  assert.equal(tx.state, "sending"); assert.equal(tx.row.querySelector(".tsendretry").hidden, true);
}
console.log("PWA R103-4: 中継成功とMac受付を区別・新しいpendingの三段・既存pending/別人/古いstatus除外・失敗と再送 OK");

// 拒否は一回のタップ後の二秒だけ待つ。取り消し・非表示・要求変更では言葉を送らない。
const denySource = answerIdSource + "\n" + answerFitSource + "\n" + sourceBetween("function apvDenyCurrent(", "function apvView(") + "\n" +
  sourceBetween("function apvDenyText(", "function apvMainPaint(") + "\n" + sourceBetween("function apvFailText(", "/* 危険・注意の理由は種類で言う");
for (const scenario of ["deny", "write", "cancel", "hidden", "changed", "failed"]) {
  const shown = { session: "deny-fixture", ask: { kind: "permission", tool: "Bash", id8: "abcd1234" } };
  let latest = shown, sequence = 0, focused = false;
  const sent = [], intervals = new Map(), events = new Map(), decided = [];
  const nodes = Object.fromEntries(["apvdeny", "apvdenywrite", "apvdenysend", "apvdenyundo", "apvdenycount", "apvdenybox", "apvdenytext", "apv_allow"].map(id => [id, settingsNode()]));
  nodes.apvdenytext.value = ""; nodes.apvdenytext.focus = () => { focused = true; };
  const context = {
    APV: { session: shown.session, e: shown, page: { level: "remote" }, readyAt: SettingsDate.now() - 1 },
    Date: SettingsDate, T: ja => ja, sessKind: e => e.ask?.kind === "permission" ? "perm" : "idle",
    sessionAgent: () => latest, apvEl: id => nodes[id], apvAckRefresh() { nodes.apv_allow.disabled = false; }, apvNote() {}, playSE() {},
    apvDecided: (A, kind) => decided.push(kind), APV_DRAFT: {}, apvDraftDrop() {},
    document: { hidden: false, addEventListener: (name, fn) => events.set(name, fn) },
    setInterval: (fn, ms) => { assert.equal(ms, 1000); const id = ++sequence; intervals.set(id, fn); return id; },
    clearInterval: id => intervals.delete(id),
    send: (text, e) => { sent.push({ text, e }); return Promise.resolve(scenario !== "failed"); },
  };
  const api = runInNewContext(denySource + "\n({approveDeny,approveDenyWrite,approveDenySend,cancelApproveDeny})", context);
  const tick = () => { for (const fn of [...intervals.values()]) fn(); };
  if (scenario === "write") {
    api.approveDenyWrite(); assert.equal(nodes.apvdenybox.hidden, false); assert.equal(focused, true);
    nodes.apvdenytext.value = "  read the fixture first  "; api.approveDenySend();
  } else api.approveDeny();
  assert.equal(sent.length, 0); assert.equal(nodes.apvdenyundo.hidden, false); assert.equal(nodes.apvdenycount.textContent, "送信まで 2…");
  tick(); assert.equal(sent.length, 0, `${scenario}: 二秒より前に拒否を送った`); assert.equal(nodes.apvdenycount.textContent, "送信まで 1…");
  if (scenario === "cancel") api.cancelApproveDeny();
  else if (scenario === "hidden") { context.document.hidden = true; events.get("visibilitychange")(); }
  else if (scenario === "changed") latest = { ...shown, ask: { ...shown.ask, id8: "ffff1234" } };
  tick(); await flushPwaTasks();
  const shouldSend = ["deny", "write", "failed"].includes(scenario);
  assert.equal(sent.length, shouldSend ? 1 : 0, `${scenario}: 取り消し・非表示・別要求に拒否を送った`);
  assert.equal(intervals.size, 0); assert.equal(nodes.apvdenyundo.hidden, true);
  if (shouldSend) {
    assert.equal(sent[0].e, shown, "拒否を表示していた要求以外へ送った");
    assert.equal(sent[0].text, scenario === "write" ? "read the fixture first" : "この操作は許可しません。別の方法で進めてください。");
    assert.equal(!!context.APV.denied, scenario !== "failed");
    assert.deepEqual(decided, scenario === "failed" ? [] : ["deny"], `${scenario}: 送れた拒否で次の件へ進まない／失敗で進んだ`);
  }
  if (scenario === "write") { assert.equal(nodes.apvdenybox.hidden, true); assert.equal(nodes.apvdenytext.value, ""); }
  if (scenario === "cancel" || scenario === "failed") assert.equal(nodes.apv_allow.disabled, false, "取り消し・失敗の後に既存の許可操作を戻していない");
}
console.log("PWA R103-4→R116-S1: 拒否二秒・届く文は 1 通り・書いた指示・取り消し・非表示・要求変更・失敗復帰・送れたら次の件へ OK");

// R104-1: ブラウザを起動せず、長時間の更新・再試行の境界・別セッションの文脈漏れを検査する。
{
  let now = 1000000, sequence = 0, requests = 0;
  const timers = new Map(), pending = { old: { session: "term-fixture" } };
  const note = outboxNode();
  note.replaceChildren = () => { note.children = []; };
  const context = {
    TERM: { session: "term-fixture", e: { state: "working", mtime: 100, age: 5 },
      opAt: now, req: null, timer: null, rows: [] },
    Date: { now: () => now }, T: ja => ja, sessKind: e => e.state,
    termRequest: () => { requests++; },
    termEl: id => id === "termnote" ? note : note.children.find(n => n.id === id),
    el: (tag, cls, text) => { const n = outboxNode(cls || ""); n.textContent = text || ""; return n; },
    dlgPending: () => pending, dlgSavePending() {},
    setTimeout: (fn, ms) => { const id = ++sequence; timers.set(id, { fn, ms }); return id; },
    clearTimeout: id => timers.delete(id),
  };
  const api = runInNewContext([
    sourceBetween("function termDelay(", "function termThrottled("),
    sourceBetween("function termDone(", "function onTermItem("),
    sourceBetween("function termSig(", "// status フレームごと:"),
    "({termDelay,termSchedule,termWaitTick,termRetry,termDone,termSig})",
  ].join("\n"), context);
  now += 360000;
  api.termSchedule();
  assert.equal(api.termDelay(), 5000);
  assert.equal(timers.get(context.TERM.timer).ms, 5000, "六分後に作業中の更新が減速した");
  context.TERM.e.state = "idle";
  context.TERM.requestSig = api.termSig(context.TERM.e);
  api.termSchedule();
  assert.equal(context.TERM.timer, null, "変化のない指示待ちで要求を続けた");
  context.TERM.e.mtime++;
  api.termSchedule();
  assert.equal(timers.get(context.TERM.timer).ms, 5000, "mtime の更新を取りこぼした");
  context.TERM.req = "old"; context.TERM.reqAt = now;
  api.termWaitTick("old");
  const retry = context.termEl("termretry"), initialTick = timers.get(context.TERM.noteTimer).fn;
  assert.equal(retry.hidden, true);
  now += 19000; api.termWaitTick("old"); api.termRetry();
  assert.equal(requests, 0, "二十秒より前に再試行した");
  assert.equal(context.termEl("termnotetext").textContent, "Mac に要求中… 19 秒");
  now += 1000; api.termWaitTick("old");
  assert.equal(context.termEl("termretry"), retry, "秒の更新でボタンのフォーカスを失う");
  assert.equal(retry.hidden, false);
  api.termRetry();
  assert.equal(requests, 1); assert.equal(context.TERM.req, null);
  assert.equal(pending.old, undefined, "再試行した古い要求が台帳に残った");
  assert.equal(context.TERM.noteTimer, null);
  context.TERM.req = "new";
  const before = context.termEl("termnotetext").textContent;
  initialTick();
  assert.equal(context.termEl("termnotetext").textContent, before, "古い時計が新しい要求を書き換えた");
}
console.log("PWA R104-1: 六分後も5秒・mtime/idle更新・経過秒/20秒再試行・古い時計破棄 OK（許可の文脈の行は R116-S1 で撤去）");
