# R96-D3 実装プラン（R96-D2 リグ化の後始末・次のループ）

対象: AI Office `（このリポジトリ）`（HEAD `966b59b`）
前提: R96-D2（`9a7a4e0` / `4559e7c` / `966b59b`）で 3D のロボが Tripo 生成の SkinnedMesh になった。
本プランは「生成体に移した結果、procedural 時代に在った表現が既定経路（`?rig` 無し＝`rigMode=1`）から落ちた」分の回収と、
それを二度と黙って落とさないための門の設置。4 観点の監査＋敵対的検証で **実コード・実レンダ・実測値まで裏が取れた所見だけ** を採った。

不変条件（この 1 本も壊さない）: 決定論（`?t=` 固定で golden ビット一致）・drawCalls ≤300・materials ≤64・
`modules_data.js` raw ≤3.5MB / gzip ≤1.2MB・`server/` は stdlib のみ・`ui/core` は DOM/通信/時刻/乱数に触らない。

プラン必須ステップの宣言（CLAUDE.md「プラン立案時の必須ルール」）:

- ハーネス整備フェーズ: **項目 1 が実装より前**（門を先に作り、項目 2〜5 の修正前に赤くなることを確認してから直す）。
- ループ設計フェーズ: 既存の `bash verify.sh` / `bash dev.sh --check|--shot|--golden` / `.claude/skills/deploy` に乗る。新規の常駐は作らない。
- グラフ接続フェーズ: 正本は `docs/ROADMAP.md`（R96-D3 の行を新設）と `.claude/rules/ui-3d.md`（「生成体に重ねる部品の位置は実測から導く」を追記）。公開面レジストリは該当なし（内部実装のみ）。
- レーン割当フェーズ: 各項目に `Claude` / `Codex可` を明記（下表）。

---

## 1) 結論 — 次のループでやる 6 項目

| # | 項目 | 規模 | レーン | 直す理由の一言 |
|---|---|---|---|---|
| 1 | **門を先に置く**（`clipFor` の単体テスト・`rigDebug` の嘘を直す・PWA へリグが同梱されているかの番人・`stats().rig`） | 半日 | Claude | 項目 2〜5 の欠陥は **全ゲート緑のまま本番へ出た**。同じ穴を先に塞ぐ |
| 2 | **頭と胸に付く物の位置を実測から決め直す**（職業アクセサリ 9 種・胸の状態リング・蝶ネクタイ・アンテナ先端ランプ） | 1 日 | Codex可 | いま golden に「頭の横に浮いた帽子」「体の 28cm 前に浮く金の円盤」が写っている＝本人が最初に気づく |
| 3 | **安い退行 4 件**（手の小道具が 0 個・greet の位相と補間・celebrate の位相・Skeleton の未解放） | 半日 | Claude | どれも 1〜数行。マグ 3・タブレット 1 が golden から消えたまま |
| 4 | **姿勢オーバーレイ層**（挙手・頷き・打鍵・コンソール・会議チビの所作を clip の上に加算で戻す） | 1 日 | Claude | 20 種類以上の姿勢が 6 clip、うち 16 種が `sit`/`idle` に潰れた。❗の挙手と承認の頷きは **完全に消えている** |
| 5 | **座りの沈下と、着席中に立ちクリップが出る件** | 半日 | Codex可 | 座っているロボの腰が座面の 31cm 下＝椅子が体を貫く。足は床下 23cm |
| 6 | **`/ui/**` の ETag をファイル単位にする** | 半日 | Codex可 | いま 1 行直すとスマホが毎回 **約 1.07MiB** を再取得する。リグで 12% 増えた分がそのまま乗る |

順序は 1 → 3 → 2 → 5 → 4 → 6 を推奨（安い実害から潰し、見た目を変える 2・4・5 は golden 撮り直しが要るので固める）。
**golden を撮り直すのは項目 2・4・5 のコミットだけ。1 コミットにつき 1 回**（CLAUDE.md の掟）。項目 1・3・6 は golden 不変が受入条件。

---

## 2) 各項目

### 項目 1 — 門を先に置く（ハーネス整備・半日・Claude）

**なぜ**: `grep -rln "rigbot\|clipFor\|rigDebug" tests ui/core` が **0 件**（実行確認）。既定経路のリグを実体で動かすテストは 1 本も無い。
`tests/iso_acts.mjs` は描画側を no-op に差し替え、`scene()` が `rigKit` を組まないので rig 分岐がそもそも実行されない。
golden 3 枚は rig=1 で撮られているが、`?t=` が frozen を立てるため **6 clip 中 walk と cheer は 1 枚も描かれていない**し、
R96-D2 と同じコミットで撮り直されているので「同時に入った取り違え」は原理的に捕まらない。
さらに PWA 側は `ui/iso/gen/manifest.json` の `rig.pwa` から 2 本を外して再生成すれば、**通常の手順どおり進めた場合に全ゲート緑のまま**
スマホだけ procedural へ静かに退避する（`rigbot.js` は kit を null で返し、scene3d の rig 分岐は全てガード済み＝例外もコンソールエラーも出ない）。
R90-P2 で踏んだ「boot3d の import 先が旧パスでスマホだけ旧デザイン」と同じ形の穴が、いちばん重い資産に開いている。

**触るファイル**
- 新規 `tests/rigbot.test.mjs`（node で three を import する形は `tests/iso_acts.mjs` が既に実証）
- `ui/core/clip.js`（`clipFor` の移設先。three 非依存にする＝ユニットテストが書けるようになる）
- `ui/iso/rigbot.js:16-26`（`clipFor` を re-export に）
- `ui/iso/scene3d.js:1627-1661`（`rigDebug().fit`）・`:1665-1682`（`stats()`）
- `ui/pwa/boot3d.js`（`window.__scene3d` の façade に 1 行）
- `verify.sh` ▶3b（99-118 行の近辺）・`tests/pwa3d_smoke.py`

**実装の勘所**
- `clipFor` は `ui/core` へ。`ui/core` は DOM・通信・時刻・乱数に触らない層なので、純関数の写像表はここが正しい置き場（`tools/js_layer_lint.py` が層の依存を機械強制している）。
- `rigDebug().fit.acc` は `Object.entries(actor.nodes.acc)[0]` 固定で、**常に phones を返す**（実行確認）。頭部アクセサリ 9 種はノード transform が同一なので座標は同じでも**ラベルが嘘**になる。「実際に装着中の part を引数で受け、ノード原点ではなくアクセサリ実メッシュのワールド AABB を返す」へ直す。これが項目 2 の受入条件の測定器になる。
- PWA の番人は 2 段。(a) `verify.sh` ▶3b に「`relay/src/modules_data.js` の `/ui/iso/gen/robot_body.js` と `robot_clips.js` が **スタブ（`export default null`）ではなく本体**である」ことの grep。これが `RUN_RELAY` 無しで常時効く唯一の門。(b) `stats()` に `rig: this.rigMode` と `rigged`（`actor.rig` を持つアクター数）を足し、`tests/pwa3d_smoke.py` に `stats().rig === 1 && stats().rigged === stats().robots` のピン 1 行。
- **素材の退避もここでやる**: リグの元 GLB は （作業用の一時領域）（計約 5MB・実在確認）＝**ジョブの一時領域**にしか無い。什器 20 種も `…/tmp/tripo/api/*/model.glb`（18MB）。リポには置かない方針（`docs/tripo-ledger.md`・`tools/tripo_out/` は gitignore）なので、`~/Documents` などリポ外の恒久フォルダへコピーし、置き場を `docs/tripo-ledger.md` に 1 行書く。clip を焼き直す将来の作業（項目 4 の次）はこの GLB が前提。

**受入条件（機械）**
- `node --test tests/rigbot.test.mjs` が緑。内容: `clipFor` の対応表を scene3d が実際に組む poseKind 一式（`desk:` / `meeting:` / `meeting:present` / `meeting:stand` / `queue:` / `external:` / `lounge:` / `chat` / `chat:mug` / `relax` / `loungeTab` / `think` / `question` / `walk:question` / `celebrate` / `walk:celebrate` / `approval` / `walk:approval` / `read:*` / `enter` / `run` / `exit` / `greet` / `chibi` / `""`）× `seated` 両値で固定。
- 同テストで `createRigKit(...)` → `attach(makeSkeleton())` → `apply(...)` を実体で回し、`nodes.visor` / `nodes.chest` / 装着中 `nodes.acc[part]` のワールド位置を数値で固定（**項目 2・4・5 の修正前には落ちる**ことを先に確認してから直す）。
- `bash verify.sh`（SKIP_UI 無し・本体 ROOT で）が緑。▶3b が modules_data のスタブを検出して赤くなることを、manifest を一時的に書き換えて 1 回だけ実測（確認後に戻す）。
- `RUN_RELAY=1` で `tests/pwa3d_smoke.py` の rig ピンが緑。
- golden 3 枚ビット一致（絵は変えない）。

**想定リスク**
- `clipFor` の移設で import が循環しないこと（`ui/core/clip.js` は three を import しない・`rigbot.js` が下向きに読むだけ）。`tools/js_layer_lint.py` が検査する。
- `stats()` にキーを足すと `tools/ui_shot.py` の性能ゲートが読む dict が変わる。上限検査はキー名で引いているので追加は安全（`DRAW_CALL_LIMIT` / `MATERIAL_LIMIT` の 2 キーのみ参照・確認済み）。

---

### 項目 2 — 頭と胸に付く物の位置を実測から決め直す（1 日・Codex可）

**なぜ**（4 件まとめて。原因が全部「procedural の頭・胴を前提にした固定値が生成体に合っていない」ため）

1. **職業アクセサリ 8〜9 種が頭に載っていない**。コミット済みの `tests/visual/golden/iso.png` に写っている＝いま本番がこの絵。golden 9 体のうち 6 体が該当（🧢cap 3・✏️pencil・🎤headset・🎧phones）。実測: acc ソケットは `HEAD_LIFT_D 0.18` + `HEAD_Y 0.2244` で Head 骨 +0.4044＝体ローカル y 1.0495 に固定されるが、生成体の頭ドームの中心は y 0.902（**ソケットが 0.148 高い**）。部品側は `node.scale.setScalar(HEAD_H/.67)`＝1.0746 で半径 0.387 の頭を想定するのに、ソケット高での生成体の頭半径は 0.326（最大幅でも 0.371）。headset のバンド（Torus 0.36→0.387）はドーム頂点 1.197 の 0.24 上＝アンテナの高さで宙を回る。phones のカップは ±0.403 で頭より 0.077 外側。
2. **胸の状態リングが体の前に浮く**。`CHEST_FWD = 0.40`（`rigbot.js:61`）は**軸の当て方は正しい**（`follow()` が root ローカル＝前＝+z へ出す。横ずれは実測 0）。壊れているのは長さだけで、rest の Spine02 から同じ高さの体表までは **0.229** しかない＝リング中心が体表の 0.172 モデル単位前（実スケールで actor 0.278m・ボス 0.318m・チビ 0.163m）。0.40 の出どころは「胸の前面」ではなく**肩の左右半幅**（胸の高さ帯の z ヒストグラムは 0.375–0.475 が空白）。golden のボスで目視確認済み＝机の上に金の円盤が落ちて見える。
3. **蝶ネクタイ**は `CHEST_FWD - 0.02` 連動。リングを詰めるとこちらが 0.04 めり込む（蝶ネクタイの高さ y0.586 では体表が z 0.2819 まで出ており、すき間は 0.117 で別値）。
4. **アンテナ先端の状態ランプ `antTip` が 0 個**（実測: rig=0 で count 15・rig=1 で **0**・rig=2 で 15）。`D_PARTS` に無いため。生成体にアンテナ自体は在るが色は頭の殻と同色 (219,202,184) の焼き込み＝状態を表さない。結果、3D で状態を示す面は胸リングと表情バイザーの 2 つに減り、**ロボが背を向けている場面で状態が読めない**。ベンダー別の本数差（claude 1 本 / 他 2 本）も消えた。

**触るファイル**: `ui/iso/rigbot.js`（`D_PARTS` 54・`HEAD_LIFT_D` 59・`CHEST_FWD`/`CHEST_LIFT` 61-62・`followAll()` 205-209・`attach()` の acc 付け替え）/ `ui/iso/robot.js:352-370`（acc ノードの y と scale）・`:415`（antTip の配置）・`:592`（「claude 以外は 2 本」分岐）/ `ui/iso/scene3d.js`（`rigDebug().fit` は項目 1 で拡張済み）/ `tests/rigbot.test.mjs` / `tests/visual/golden/*.png`

**実装の勘所**
- 値をベタ書きで詰め直さない。**attach 時に生成体の頭の AABB を 1 回だけ測って導出する**（`rigDebug().fit` が既に `headTop` / `headFront` / `headBackZ` を出している計算の一般化）。頭のスケール（`VENDOR_HEAD` codex 1.37 / openclaw 1.5×0.90）は `setPose` で Head 骨の `b.scale` にしか入っておらず、`follow()` は position と quaternion しか写さないので **アクセサリには伝わらない**（golden 6 体は全員 claude＝倍率 1 なので今は表面化していないが、codex/openclaw の席では効く）。導出式に頭スケールを掛ける。
- `CHEST_FWD` は 0.40 → **≈0.24**（リング後面 0.013 が体表に接する値 = 0.40 − 0.172 + 0.013）。蝶ネクタイは連動をやめて独自値 **≈0.30**。コメントの軸名（「z≈0.40」）も実際に測った軸へ直す。
- `antTip` は `D_PARTS` / `D_PARTS_CLAW` に足す。ソケットは既に生きている（`nodes.antTips` は neck の子で、neck は root 直下へ移して Head 骨へ追従済み）。生成体のアンテナ球は体ローカル y≈1.443 なので `HEAD_LIFT_D` 基準のオフセットを rig 用に切り替える。**本数は生成体が 1 本なので rig モードでは 1 本に固定**（`robot.js:592` の分岐を rig で分ける）。ベンダー識別はハサミ・頭幅・個体色が残っているので、ここで無理に 2 本にしない。
- `?rig=0` の見た目を 1px も変えないこと（procedural 側の定数は触らず、rig 経路だけ上書きする）。

**受入条件（機械）**
- 項目 1 で拡張した `rigDebug().fit` を使い、新テストで **9 種のアクセサリ × 3 ベンダー**について「装着中アクセサリのワールド AABB が頭の AABB から 5cm 以上はみ出さない」「中心距離が頭半径以下」。
- 胸リング: 「リングの後面と体表の距離が 0〜3cm」、蝶ネクタイ: 「めり込み 0・すき間 3cm 以内」を rest / idle / walk の 3 位相で。
- `antTip` の InstancedMesh count が rig=1 で `robots` と一致（1 体 1 個）。
- `drawCalls ≤300`・`materials ≤64`（現状 148(M)/184(XL)・materials 62＝**余裕 2**。材質キーを増やさないこと）。
- golden 3 枚を同一コミットで 1 回だけ撮り直し、**2 回撮って 0.000%**。`tools/style_score.py --profile glass` 全項目クリア。

**想定リスク**
- materials が 62/64 まで来ている。アクセサリ用に新しい材質キーを作ると 64 に当たる＝**既存材質を使い回す**（`antTip` は accent の `MeshBasicMaterial` が既にある）。
- 頭の AABB を毎フレーム測ると決定論と性能に響く。**attach 時 1 回・kit スコープでキャッシュ**（rest 姿勢の形は個体で変わらない）。
- golden が動く＝項目 4・5 と同じコミットに混ぜない（混ぜると「どの変更でどこが変わったか」が読めなくなる）。

---

### 項目 3 — 安い退行 4 件（半日・Claude）

**なぜ**

1. **手の小道具（mug / tablet / wrench / book）が既定で 1 個も描かれない**。二重の理由: (a) `ui/iso/scene3d.js:1157-1158` の rig 分岐が `prop` に `null` を固定で渡す（実コードで確認）(b) 渡しても `only=D_PARTS` に prop 名が無く `robot.js:611` の `put()` で落ちる。`ACTIONS`（`ui/core/act.js`）は 11 種のうち 7 種が prop を持つ。**golden が実際に失ったのは mug 3 + tablet 1**（rig=0 で `robot.js:611` を消したレンダとの差分＝138〜149px・bbox 297,269–429,341 を拡大して確認。ラウンジのソファのマグ 2・コンソールのロブスターの黒いタブレット。旧 golden に wrench は存在しない）。**全画面の約 0.011% なので `DIFF_LIMIT 0.5%` では構造的に検出できない**。
2. **greet（タップの挨拶）の遷移補間が死んでいる**。`scene3d.js:1040` の `poseKind = "greet"` が変化検出ブロック（1018-1022）より**後ろ**にあるため `actor.poseChangedAt` が更新されず、`rigbot.js` の混合重み `w` が 1 のまま＝0.45 秒ブレンドが効かない。実測で 1 フレームに骨が最大 0.49〜0.63（体高 1.484 の 33〜42%）動く（補間が効いていれば 0.04 台）。※ `question`（996-997 行）と `celebrate`（1000 行）は検出ブロックより前なので**補間は無傷**。
3. **一発芸の位相**。`rigbot.js:202` の `timeFor` が one-shot でも `t + seed` を返すので、cheer(8.0s) の**任意位相**から greet は 0.9 秒（11.25%）、celebrate は 1.2 秒（15%）を切り取る。実測: 0.9 秒窓での手の上下移動量が 0.10 未満になる開始位相が 80 分の 36、0.05 未満（ほぼ静止）が 80 分の 15＝**タップの約半分は「腕が上がった姿勢へ一瞬で跳び、静止して、戻る」**＝手を振ったように見えない。
4. **Skeleton が解放されない**。`rigbot.js:246` の個体 `dispose()` が `scene.remove(group); pool.push(geo);` だけで `skeleton.dispose()` を呼ばない。実測（Chromium/SwiftShader・実 `rigbot.js` を直接 import）: attach×20 → dispose×20 → 2 巡目 attach で `info.memory.textures` が 21 → 41 → 61 と単調増加し、`skeleton.dispose()` を足すと **ちょうど 20 枚戻る**。41 骨で 16×16 RGBA Float ＝ 4,096B/体。強制 GC では 20 枚中 19 枚が回収されたので**恒久リークではない**が、(a) `info.memory.textures` が永久にずれる＝`scene3d.stats().textures` としてアプリが公開している計測値が嘘になる (b) 解放が GC 任せになる。churn するのは主役アクターだけ（boss は起動時 1 回・チビはプールで visible=false）。

**触るファイル**: `ui/iso/scene3d.js:1157-1158`（prop を渡す）・`:1018-1022` と `:1039-1040`（greet の順序）/ `ui/iso/rigbot.js:54`（`partsFor` に prop 名を合流）・`:200-204`（`timeFor`）・`:246`（`dispose`）/ `ui/iso/robot.js`（`PROP_MATERIALS` のキー集合を export するなら）

**実装の勘所**
- prop の配線は 2 点だけ。`push(..., actor.prop, ...)` に戻し、`partsFor(vendor)` の集合に `Object.keys(PROP_MATERIALS)` を合流。**位置合わせの追加実装は不要**＝prop が載る `nodes.arms[1].hand` は attach で root 直下へ移し R_Hand 骨へ follow 済み（OpenClaw のハサミが rig 経路で今も描けているのと同じ仕組み・`rigbot.js:176-179`）。4 種の当たり具合は実レンダで目視。
- greet は「`poseKind` 決定を greet 判定の後ろへ移す」か「greet で上書きしたときに `poseChangedAt` も書く」。**frozen（golden）では greet はタップ起点のみ・`levelUps` は破棄されるので golden は不変の見込み**（要確認）。
- one-shot（greet / celebrate）の `timeFor` は「**イベント開始時刻からの経過**」で引き、`sampleClip` の loop=false を使う。`question` は ❗ が消えるまで続く**持続状態**なので任意位相のままが正しい（意図どおり＝触らない）。
- `dispose()` は `mesh.skeleton.dispose()` の 1 語追加で足りる（実測でちょうど回収）。余力があれば `pool` に `{group, mesh, skeleton, bones}` 一式を積んで再バインドする。

**受入条件（機械）**
- 新テスト or スモークで「rig=1 で `prop` の InstancedMesh count > 0」。アプリ経路（`only` を渡した push）を通すこと＝`tests/iso_acts.mjs` が `only` 無しで push しているのが今の緑の正体。
- 「attach×N → dispose×N → render で `info.memory.textures` が元に戻る」を 1 本（上の計測をそのままテスト化）。
- greet: `poseChangedAt` が greet で更新されることを単体で固定＋「1 フレームあたりの骨の最大移動量が 0.1 未満」。
- golden: **mug 3 + tablet 1 が戻るので絵は変わる**。項目 2 と同じコミットに入れるなら撮り直しは 1 回で済む（推奨）。単独コミットにするなら 1 回撮り直し、2 回撮って 0.000%。

**想定リスク**
- prop を戻すと `drawCalls` が prop の種類数だけ増える（InstancedMesh は種類ごとに 1）。現状 184(XL) / 上限 300 なので余裕はあるが実測すること。
- `approval` 中は `actor.prop = null` にする既存ロジック（`scene3d.js:1014`）が rig でも効き始める＝承認の瞬間にマグが消える。これは procedural と同じ挙動なので**仕様どおり**。

---

### 項目 4 — 姿勢オーバーレイ層（1 日・Claude）

**なぜ**: `clipFor` を全語彙に通した実測で、scene3d が作り分けている 20 種類以上の poseKind が 6 clip に写像され、**うち 16 種が `sit` と `idle` の 2 本に集まる**。

| clip | 吸い込む poseKind |
|---|---|
| `sit` | desk（打鍵）, **approval**, meeting, lounge, lounge:tablet, relax, loungeTab, chat, chat:mug, read:book, read:tablet |
| `idle` | meeting:present, meeting:stand, queue, **external（コンソール）**, external:stand, leave |
| `walk` | walk, walk:question, walk:approval, walk:celebrate, enter, **run**, exit |
| `wait` | think, **chibi** |
| `cheer` | celebrate, greet |
| `look_around` | question |

実害が大きい 4 つ:

1. **❗の挙手が消えた**。FK で骨のワールド Y を実測すると `look_around`（＝question）の手の最高 Y は (手−腰)/(頭−腰) = **0.245**＝腕は体側に垂れたまま。procedural（rig=0）は **0.861**＝頭の高さまで上がっていた。しかも rig では procedural の腕は `only` フィルタで描かれないので、挙手が出る経路が存在しない。
2. **承認の頷きが消えた**。`clipFor` に `approval` の枝が無く、seated なら `sit`・立位なら `idle`＝**置き換わる前と同じ clip**なので `blendPoses` も恒等。加えて `approvalPose` は頭だけを動かすが、`followAll()` が `nodes.neck` を Head 骨から毎フレーム上書きするので構造的に出ない（実測: rig=0 では `neck.rotation.x` が 0.08rad 振れる / rig=1 では頭の前方ベクトルが 3 時点すべて完全一致）。※顔の表情（0.9 秒 happy）・頭上の ✓ スプライト・胸リングの色戻しは**生きている**＝無反応なのは体と首だけ。
3. **会議チビ（サブエージェント）の頷き・ピョコン跳ね・挙手が全部死んでいる**。`scene3d.js:1267` / `1289` の `applyPose(ch, chibiPose(t, seed))` は既定では**完全な no-op**（`chibi`→`wait` clip・neck/chest は骨が上書き・hip/legs/arms は push されない）。`wait` には Hip/脚/腰/Spine/NeckTwist01/腕のトラックがあるので微揺れはするが、Head 骨のトラックは 1 本も無く上下動は visor ワールド位置で 8mm（rig=0 の跳ねは 159mm）。**「頷き＋たまにピョコン跳ね・挙手」というコメント付きの死んだコードが残っている**のも実害（読んだ人が演出が生きていると誤解する）。
4. **外部社員（OpenClaw）のコンソール操作**。`consolePose` の手は y=0.93 で天面 0.96 のコンソールにほぼ載っていたが、`idle` clip では 0.39〜0.50m＝約 0.5m 下。ハサミは手の骨に追従するので、**R75 が「ハサミが見えない」と言って直した状態が戻る**。

**触るファイル**: 新規 `ui/core/overlay.js`（純関数）/ `ui/iso/rigbot.js`（`apply` の `setPose` 直後・`followAll` の前に加算）/ `ui/iso/scene3d.js:1244,1267,1289`（チビ）/ `ui/core/anim.js`（角度の出どころを再利用）/ `tests/rigbot.test.mjs`

**実装の勘所**
- **clip を増やさない**。`overlayFor(poseKind, t, seed)` を `ui/core` に新設し、骨名 `R_Upperarm` / `R_Forearm` / `L_Forearm` / `Head` / `NeckTwist01` / `Spine02`（`robot_clips.js` の joints 41 本に実在を確認済み）へ **rest からの差分回転を加算する純関数**として書く。`t` と `seed` だけの関数＝決定論は保たれる。
- 適用点は `rigbot.apply` の `setPose(sample)` の直後・`followAll()` の前。`followAll()` より後ろに書くと neck が上書きされて消える（これが頷きが死んでいる理由そのもの）。
- 戻す所作は 5 つに絞る: ①question＝右腕を頭上へ（挙手）②approval＝`NeckTwist01` に pitch 加算（頷き）③desk working＝手の小刻みな振動 ④external＝手をカウンター天面の高さへ ⑤chibi＝頷き・挙手。
- **チビのピョコン跳ね（`hipY` 23 秒周期 0.15m）は `ch.root.position.y` へ移すだけで生成体ごと跳ねる**（rigbot 無改造）。
- 死んだ `applyPose(ch, chibiPose(...))` はコメントごと整理する（「rig 経路では効かない」を残すか、overlay に置き換える）。
- 骨は Y 軸方向のチェーンなのでリターゲット式が要る（procedural の shoulder/elbow 角をそのまま骨のローカル回転に入れない）。まず rest 姿勢で 1 回測り、差分回転として持つ。

**受入条件（機械）**
- 新テストで「working / waiting / attention（question）/ approval の 4 状態が**互いに異なる右手首ローカル座標**を返す」。
- 「attention では右手首の y が頭頂 y を上回る」。
- 「approval で `Head` のワールド前方ベクトルが 0.3 秒の間に 0.05rad 以上振れる」。
- チビ: 「`chibi` の跳ね局面で root.y が 0.1 以上上がる」。
- 決定論: 同じ `?t=` を 2 回撮って **0.000%**。`drawCalls` / `materials` 不変。
- golden: frozen では approval / greet は出ないが **desk working の手の小刻みは frozen でも位相 t で決まる**＝golden が動く。1 回だけ撮り直す。

**想定リスク**
- 加算レイヤーが clip の腕トラックと喧嘩して肘が裏返る可能性。**加算は「rest からの差分を clip サンプルの後に合成」で、上書きではなく合成**にする。破綻したら該当骨だけ上書きへ落とす。
- `ui/core` に置く以上、three を import してはいけない（クォータニオンは配列で扱う。`ui/core/clip.js` の `slerp` が既に配列でやっている＝同じ流儀に合わせる）。
- 手の高さを「什器の天板に載せる」のは難しい。ロボが什器に対して大きく（Head 骨 0.93m が机の天板 0.89m とほぼ同高）、**procedural 時代も手は天板の 0.32m 下だった**。狙いは「手が動いている」という情報の回復であって、天板に吸着させることではない。

---

### 項目 5 — 座りの沈下と、着席中の立ちクリップ（半日・Codex可）

**なぜ**
- `robot_clips.js` の `sit` は **腰を一切落とさない**（Hip のワールド y が 109 フレーム全部 0.330＝rest と同値）うえ、足は床に接地したまま（スキニング後の最低頂点 +0.011）。つまり「床に立ったまま脚だけ前へ出した in-place クリップ」なので、`scene3d.js:1051-1057` の `SIT_DROP = 0.24` がまるまる沈下量になる。
- 結果、着席時の Hip はゾーン床 +0.09。椅子の座面は 0.40 なので **腰が座面より 0.31 下＝椅子が体を貫く**。procedural は腰 0.473（座面 +0.07）でちゃんと載っていた。床下侵入も procedural −0.097 に対し生成体 **−0.229**（desk/lounge は lift 0.12 なので本物の床より 0.109 下にも入り、メッシュが床に切られて足が消える）。
- **同根の別件（こちらの方が深い）**: `clipFor` は `question`→`look_around`、`think`→`wait` を **seated 判定より先に**返すので、着席中に ❗ が出た／🤔思考中のエージェントは「立ちクリップ＋0.24 沈下」になり、最低点が着座面から 0.326（look_around）〜0.385（wait）下まで入る。procedural は `questionPose` / `thinkingPose` が着席の脚と hipY を保っていた＝R96-D2 で入った退行。
- ボスは既に例外扱い（`scene3d.js:1200` の `this.bossRig ? 0 : .35`）＝同じ問題を 1 箇所だけ場当たりで避けている。

**触るファイル**: `ui/iso/scene3d.js:1051-1057`（`SIT_DROP`）・`:1200`（ボスの例外を一般化）/ `ui/iso/rigbot.js:16-26`（`clipFor` の判定順）/ `tests/rigbot.test.mjs` / golden 3 枚

**実装の勘所**
- **最小の修正は rig 経路で `SIT_DROP` を 0 にすること**。実測でそれだけで つま先 +0.011（接地）・腰はゾーン床 +0.33（座面 0.40 の 7cm 下＝クッションに沈んだ見え方）になり、沈下は解消する。「ちゃんと座って見える」まで詰めるなら sit クリップ側で腰を落とす焼き直しが要るが、**それは今回の範囲外**（項目 4 の次）。
- `clipFor` の `question` / `think` を **seated 判定より後ろへ**移す（着席中の ❗ は `sit` ベース＋項目 4 の挙手オーバーレイで出す）。ここは項目 4 と同時にやると噛み合いが良い。
- ボスの `bossRig ? 0 : .35` は「rig 経路では sit の沈下を掛けない」という一般則へ畳む。

**受入条件（機械）**
- 新テストで「着席時のスキニング後メッシュ最低点が着座面 −0.02 以上」「腰（Hip）のワールド y が座面 ±0.10」。
- 「着席中の `question` / `think` が `sit` 系 clip を選ぶ」ことを `clipFor` の表で固定。
- golden 3 枚を 1 回撮り直し（座っている全員が 0.24 上がる＝絵は確実に変わる）。2 回撮って 0.000%・`style_score --profile glass` 全項目クリア。

**想定リスク**
- 座面から 7cm 下のままなので「浮いている」ではなく「沈んでいる」に変わるだけ。**本人の目で見て許容できるか**が最終判断＝実レンダの目視を検収に含める（機械では測れない）。
- `SIT_DROP` は procedural でも使われている。**rig 経路だけ 0** にすること（`?rig=0` の絵を変えない）。

---

### 項目 6 — `/ui/**` の ETag をファイル単位にする（半日・Codex可）

**なぜ**: `relay/src/worker.js:622-646` の `/ui/` 分岐は、50 本のモジュールと 14 枚のテクスチャ（計 64 URL）**すべてに同じ** `W/"ui-" + UI_BUILD` を返す。`UI_BUILD` は
`tools/gen_pwa_modules.py:build_id()`＝全モジュール本文＋全アセット base64 の sha256 なので、**1 ファイルの 1 行でも変われば必ず別値**になる。
実測（実物の worker.js を node で読み込んで 64 URL を叩いた）: 現行 ETag 提示で **304 × 64**、古い ETag 提示で **200 × 64 / raw 2,474,573B**。
エッジ圧縮後で **gzip 約 1,124,839B（1.07MiB）/ brotli 約 1,013,975B**。うちリグ 2 本が 134,228B＝**11.9%**。
`relay/src/modules_data.js` は 2026-08-04〜09-16 の 17 日間に **64 コミット**で変更されている（生成物なので変更＝必ず版が飛ぶ）。
Service Worker の fetch ハンドラは `respondWith` を呼ばない空実装＝**ブラウザの HTTP キャッシュだけが層**。
R80 Phase0 で「PWA 再接続バックオフが一度も進まず 1 台 86,400req/日」を踏んでいる系統の、転送量側の穴。

**触るファイル**: `tools/gen_pwa_modules.py`（`ETAGS` の出力を追加）/ `relay/src/worker.js:622-646` / 新規テスト 1 本

**実装の勘所**
- `gen_pwa_modules.py` は既に per-entry で sha256 を回しているので、`export const ETAGS = {"/ui/...": "<12hex>"}` を併せて吐くだけ。
- `worker.js` 側は **ETag の算出をパス解決の後ろへ移す**（今は MODULES 参照の前に作っている）。`ETAGS[path] || UI_BUILD` で比較・返却。**404 に ETag を付けない**点だけ注意。
- `immutable` を付けるのではなく per-file ETag が正解（R79 で immutable をやめた判断はそのまま活きる＝生きたコードが最大 1 年届かない問題）。
- `modules_data.js` は 64 行 × 約 30B 増えるだけ。gzip 予算への影響は無視できる。

**受入条件（機械）**
- 新テストで「1 モジュールだけ差し替えて、そのファイルだけ 200・残り 63 本は 304」。
- `bash verify.sh` ▶3b（modules_data の一致・git 追跡・worker 構文）が緑。
- `gzip(modules_data.js) ≤ 1,200,000B`（**現状 1,124,312B ＝ 余裕 75,688B / 73.9KiB**）。
- `RUN_RELAY=1 bash verify.sh` の中継 E2E が緑 → `relay/deploy.sh` → 実機で「1 行直して 1 本だけ 200」を確認（デプロイ直後は新旧がエッジで数分混在＝特徴文字列の伝播を待ってから検証する）。

**想定リスク**
- ETag をパス解決の後ろへ移すと、現在「MODULES にも ASSETS にも無いパス」に ETag を返している挙動が変わる。404 分岐の前で `ETAGS` を引かないよう順序に注意。
- 既存端末は初回だけ全部 200（ETag の形が変わるため）。1 回だけの支払いなので許容。

---

## 3) やらないこと（理由つき）

1. **歩行の位相を `walkTime` へ配線する（足の滑り）**。`rigbot.js:202` の `dist / ANIM_RIG.speed` はアクターの実移動も同じ `speed` なので**経過秒と恒等**＝clip は常に等倍再生。生成体の walk の自然な歩幅は 1 周期（1.2 秒）あたり 0.198〜0.211 なのに体は 1.50 進む＝**約 7.1〜7.6 倍の過走**（コメントの「距離駆動＝足が滑らない」は成立していない）。ただし **procedural でも実測 3.3 倍滑っていた**＝R96-D2 の新規退行ではなく「2.3 倍悪化させた上で、滑らないと書いた」。`ui/core/clip.js:84` の `walkTime` は用意されているが `tools/glb_rig.py` が clip の `stride` を書き出していないので**生成側の変更が要る**＝半日を超える。項目 4 の次のループへ。ボスだけ `pathTravel(...,1.15)` なのに除数が 1.25 固定な点も同時に直す。
2. **clip を新しく焼く（typing / present / nod / sit-deep）**。Tripo の retarget は `preset:biped:*` で通ることが実証済み（10cr/本）だが、生成 → `tools/glb_rig.py` → `modules_data.js` 再生成 → golden 撮り直しまでで **1 日を超える**。容量の天井も先に確認が要る: `robot_clips.js` は raw 101KB で**1 ファイル上限 160KB**（8 秒 15fps の clip が約 20KB ＝ raw では 3 本が上限）、gzip 側は **余裕 73.9KiB しかない**。項目 4 の加算オーバーレイで足りるかを先に見てから判断する。
3. **PWA の初回転送 −131KB（リグの dynamic import 化）**。`scene3d.js:33` → `rigbot.js:8-10` が全て static import で、`?rig=0` でも WebGL 不可端末でも払う（実ブラウザで rig=0/1/無指定の 3 条件が **バイト単位に同一**＝JS 47 本 837.3KiB を実測）。2Mbps では 4 秒退避ウォッチドッグの発火が新規に起きる（ready 4.11s → 4.52s）。ただし `createRigKit` は `scene3d` のコンストラクタ内の**同期呼び出し**で、非同期化は **golden の `t` 固定ビット一致（決定論の掟）に触れる**。既存の `assetsPending` に相乗りさせるのが素直だが設計判断を伴う＝M 以上。R79-9 の一時退避（scene3d-ready で復帰）が入っているので恒久故障にはならない。別ループで。なお `robot_body_h.js`（98KB・`?rig=2` 専用）は**デスクトップでは rig モードに関係なく毎回読まれる**完全な死に荷重＝ここだけは項目 6 のついでに切れるなら切ってよい（PWA ではスタブ済み）。
4. **配信（stream）と xl22 を `style_score` の常設ゲートに足す（R50-P3）**。`verify.sh:561` は 3 枚のうち `ui_iso_scene.png`（M）1 枚にしか採点を掛けていないのは事実だが、**先に計測の穴を塞がないと誤ったものを固定する**: 配信の「明るい一様面 0.222 超過」の正体は採点用スクショに写り込んだ**白い字幕帯 `#stream-subtitle`**（除外して測ると 0.153＝M 0.134 / XL22 0.142 と同水準）で、「画面の発光 0.001 未達」は配信が `quality="off"` で bloom を作らない**設計どおりの帰結**（`art-direction.md` §10）。つまり必要なのは (a) `tools/ui_shot.py:167/178` の非表示リストに配信専用要素を足す (b) 配信用プロファイルを切って `screen_glow` の下限を実測から引き直す、という**較正の設計判断**。ゲートを広げる前にそれを決めるループが要る。
5. **`?rig=0`（従来経路）のテスト・`quality:"mobile"` に影の段を入れる・materials の圧縮**。いずれも low・未検証。`materials 62/64` は今は当たっていないので、当たったときに測って直す。

---

## 4) 確認できなかった / 推測のまま残る点

1. **iOS Safari の実機**。Skeleton の未解放（項目 3-4）について「メモリ圧で WebGL コンテキストが落ちる」は**推測**であり根拠が出せていない。1 体 4KB で入退場 1,000 回でも 4MB 相当。確実な実害は「`info.memory.textures` が単調増加して `stats().textures` が嘘になる」ことと「解放が GC 任せになる」ことの 2 点のみ。
2. **2Mbps 帯のちらつき増**（やらないこと 3）はローカルの帯域制限での実測であって、**実機の 4G/3G では未検証**。`ui/pwa/app.js` の 4000ms 退避しきい値が妥当かも実機待ち。
3. **項目 2・4・5 の「直したあと本人の目にどう映るか」は機械では測れない**。AABB・座面との距離・手首の高さは固定できるが、「座って見えるか」「帽子が似合っているか」は実レンダの目視が最終判断。検収に `dev.sh --shot` の実レンダ目視を必ず入れる。
4. **CLAUDE.md の数値が古い**。「`modules_data.js` の gzip 1.2MB 以下（実測 1,097KB＝余裕 103KB）」は**単位の取り違え**で、ゲートは `gz > 1_200_000` の**バイト**比較（`verify.sh:150`）＝実測 1,124,312B に対して **余裕は 75,688B（73.9KiB）**。次のループで CLAUDE.md と `.claude/rules/ui-3d.md` の記述を直す（今回は読み取り専用のため未修正）。
5. **リグの元 GLB の置き場**。（作業用の一時領域）（約 5MB）と什器 20 種 `…/tmp/tripo/api/*/model.glb`（18MB）に**実在を確認**した（`tools/glb_rig.py` で焼き直すとコミット済みの `ui/iso/gen/*.js` とバイト一致することも別途実証済み）。ただしここは**ジョブの一時領域**で、掃除されると clip の焼き直しが不可能になる（`tools/tripo_gen.py` に task_id から再取得するサブコマンドは無い）。項目 1 でリポ外の恒久フォルダへ退避し `docs/tripo-ledger.md` に置き場を 1 行書く。`tripo` CLI（`task get`）での再取得可否は鍵が手元に無く**未検証**。
6. **表情プレートが細いスリットになった**（`rigbot.js:67-104` の `facePlateFromBody`）・**drawCalls が在席人数に比例するようになった**（`CAPACITY=40` で上限 300 に当たるか）は low で**未検証**。後者は 22 体 cap=M で 165 draw の実測があるので当面は安全という**推測**。
7. **`?rig=2`（ハイブリッド C）の扱い**。本人裁定 D で既定は rig=1＝比較用にしか使われていないが、項目 2・4・5 の修正を rig=2 側にも通すのか（`HYBRID_PARTS` は別集合）は**未決**。最小は「rig=1 だけ直し、rig=2 は現状維持」だが、死に荷重（`robot_body_h.js` 98KB）ごと退役させる選択肢もある＝本人の判断が要る。
