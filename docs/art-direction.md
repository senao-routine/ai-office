# AI Office 2.0 アートディレクション（正本・R90-AD2）

> **状態: 確定（2026-09-07・方向 C「写実寄り北欧オフィス」を本人裁定）**
> この文書が見た目の SSOT。委譲プロンプト・コード・golden が矛盾したら**この文書が勝つ**。変えるときはここを先に直す。
> 数値の根拠は §9 の参照画像を PIL で実測した値（採色点つき）と、現行 `ui/iso/*` の棚卸し。
> **期待値の合意（本人と共有済み）**: リアルタイム three.js で届くのは参照画像の **7〜8割**。
> 届かない部分は §11 に正直に書いてある。**V2 の実レンダを見て続行可否を再判断する**取り決め。

## 1. コンセプト（1文）

**白い台の上に置かれた、明るい北欧オフィスの切断模型。** 大きな窓から回り込む昼光、白木のオーク床、白いマット壁、
たくさんの植物、リネンのソファ。そこに 2 頭身の陶器のロボが座って働いている。
色は「オーク・暖白・セージ緑1色」。硬い影も、光る線も、夜も無い。

## 2. パレット（役割つき・hex・採色元）

| 役割 | hex | 採色元 / 用途 |
|---|---|---|
| paper | `#f6f2ec` | 壁の内側面。h30 の暖色ニュートラル。**青みゼロ**（ボード実測は `#eae8e6` だが、俯瞰の平行投影では壁の面積が小さく画面が眠くなるので一段明るく振る＝2026-09-08 実測で決定） |
| paper-2 | `#e4dcd2` | 壁の陰面・受付天板・白キャビネット（陰では `#bbaa94` まで落ちる） |
| paper-cap | `#fbfbfb` | 壁の天端（切断キャップ）・背景。**純白はここだけ** |
| shell-out | `#bcbcbc` | 壁の外側面。**画面で唯一の無彩色グレー**＝「白い台に置いた模型」の記号 |
| wood | `#c4a47c` | オーク床（板の継ぎ目 `#ad8c61`・最暗の目地 `#6e5734`） |
| wood-2 | `#d3b48a` | 机天板・ローテーブル・受付天板（床より 1 段明るく黄み寄り） |
| wood-deep | `#ac8356` | サイドボード・本棚の骨・受付のフルート前板 |
| jute | `#c7a87e` | ラグ（織り目 ±6 L・縁は `#d3b48a`） |
| ink | `#2e2d2c` | 顔スクリーン・ガラスの黒枠・HUD 本文。**純黒ではなく L18〜20** |
| ink-2 | `#5e5a55` | ロボの関節・手・HUD の補助文字 |
| **accent（唯一のアクセント）** | 3D 布 `#7a9469`（h96）／濃い面 `#5f7d59`（h110）／HUD チップ地 `#e3ebdd` | 椅子の座・机のフェルト仕切り・クッション・玄関マット・HUD の主操作 |
| warn | `#c28a3a` | 待機（指示待ち）・保留。意味色 |
| alert | `#c0483a` | ❗（承認・質問）・危険操作。意味色 |
| rest | `#b4aca0` | 休憩・非活性 |
| plant | `#4f5a2a` | 葉の基調（明面 `#7a8a4c`・鉢の土 `#3c3229`） |
| plant-out | `#b2b290` | 窓の外の樹冠（内壁より 1〜1.5EV 明るい・飛ばさない） |
| sofa | `#ded5ca` | クリームリネン（陰 `#b9ae9c`・脚は濃ウォルナット `#402e18`） |
| glass | `#dfe3da` @ opacity .10 | 会議室のガラス（暖グレー・**枠が主役**） |
| metal | `#b3b0aa` | 椅子の 5 本星ベース・ガスシリンダ・ランプ支柱 |
| copper | `#b8895a` | ペンダント照明のドーム（内側は `lamp`） |
| lamp | `#ffd9a0` | 暖色ランプの発光（夕プリセットで強くする） |
| shell-claude | `#e9e0d5` | Claude ロボの殻（クリーム） |
| shell-codex | `#787676` | Codex ロボの殻（グラファイト） |
| shell-openclaw | `#bb4838` | OpenClaw ロボの殻（ブリックレッド・既存ロブスター） |
| face | `#262626` | 顔スクリーン（艶あり黒ガラス・上→下に L25→L11） |
| fog | `#f4efe7` | 台座の外を溶かす紙色のフォグ |

**掟**: 青系の画素を作らない（実測でも参照 C に青は 1 画素も無い）。緑は彩度を上げない（HSV s ≤ 0.32）。
白飛びしてよいのは壁の天端と背景だけ。**アクセントは面積が小さいのが正しい**（参照実測 0.6%）。

## 3. マテリアル表（`makeMaterials()` のキー 1:1）

現行 58 キー＋実行時 4 キー＝62。C ではネオン/ホログラム/擬似映り込み系を退役して **≈45 キー**にする（ゲート ≤64）。

**§2 の hex は「画面に出る最終的な色」**。テクスチャが色を持つ材質（`oak_floor` `jute` `rattan` `rugart`）は、`color × map` で**二重に暗くなる**ので `color` をほぼ白にする。無彩色のマップ（`linen` `felt`）は「マップの平均輝度で割った色」を入れる。実測の補正値（テクスチャ平均をリニア空間で割って算出・R90-V2 で1回踏んだ）:

| 材質 | マップ平均 | §2 の目標 | `color` に入れる値 |
|---|---|---|---|
| floor / wood / wood2 / woodFloor | `#cda674` | `#c4a47c` `#d3b48a` | **`#ffffff` 前後**（`floor` は `#f4f0ea` まで） |
| rug / rugB（ジュート） | `#bfa072` | `#c7a87e` | **`#ffffff`** |
| rattan | `#b88146` | `#c8a878` | **`#ffffff`** |
| sofa / sofaB / sofaC（リネン） | `#c1bcb5` | `#ded5ca` | **`#ffffff`** |
| seat / panelA / felt（セージ） | `#afadab` | `#7a9469` | **`#b2da9c`** |
| seatB / panelB | `#afadab` | `#6f8f63` | **`#a2d393`** |
| seatC / cushionC | `#afadab` | `#84a074` | **`#c0e6ab`** |

`r`=roughness `m`=metalness `cc`=clearcoat `ccR`=clearcoatRoughness。型: std=MeshStandard・phys=MeshPhysical・basic=MeshBasic。

### 3.1 継続（値だけ差し替え）

| キー | 型 | C の色 | r | m | その他 | 備考 |
|---|---|---|---|---|---|---|
| base | std | `#e6ded2` | .85 | 0 | – | 台座。木口は wood-deep（側面別マテリアルは V5 で検討） |
| floor | std | `#ffffff`（テクスチャで着色） | .55 | 0 | `map oak_floor.webp`(repeat 1/1.6m・aniso 4)・`normalMap` 手続き目地(.35)・`lightMap` 手続き 512² | **専用メッシュ 1 枚に分離**。金属味を消す（現行 m.40 は envMap を映すための値） |
| floor2 | std | `#f0eae1` | .80 | 0 | – | ゾーン台座（会議室・ラウンジ）。床より僅かに白い |
| woodFloor | std | `#d8bd97` | .60 | 0 | `map oak_floor.webp`（repeat 90°回転） | ソファ/ラウンジ台座 |
| darkFloor | std | `#8e8880` | .85 | 0 | – | 外部接続ベイ。**暗紺 `#2e3352` は退役**（暖グレーへ） |
| white | phys | `#f1ede7` | .80 | 0 | – | **什器専用**（机の側板・枠・カウンター・棚）。艶なし紙質。ロボからは切り離す（→ shell） |
| shell | phys | `#e9e0d5` | .32 | 0 | cc .70 / ccR .18 / envMapIntensity 1.0 | **ロボの殻専用**（陶器）。色は instanceColor（cream/graphite/brick） |
| dark | std | `#2e2d2c` | .55 | .10 | – | ガラスの黒枠・椅子の肘掛と脚・小物 |
| darker | std | `#232120` | .60 | .10 | – | サーバーラック筐体・モニタ背面 |
| steel | std | `#b3b0aa` | .35 | .85 | – | 椅子の 5 本星ベース・ガスシリンダ・支柱 |
| wood | std | `#ffffff`（map で着色） | .45 | 0 | `map oak_floor.webp`(repeat 1/1.2)・cc .12 / ccR .5 | 机天板（奇数島）。天板だけ僅かにツヤ＝窓が薄く映る |
| wood2 | std | `#f0e2cc`（map 併用） | .48 | 0 | 同上 | 机天板（偶数島）・会議卓・コーヒーバー |
| seat / seatB / seatC | std | `#7a9469` / `#6f8f63` / `#84a074` | .90 | 0 | `map felt.webp` | 椅子の座と背。**3 色は同じセージの明度違い**（青・紫・青緑は退役） |
| sofa / sofaB | phys→`linen` | `#ded5ca` / `#e5dccf` | .95 | 0 | `map linen.webp`・sheen .5 / sheenColor `#fff6e8` / sheenRoughness .85 | mobile は std へ差し替え（同 map・sheen 無し） |
| sofaC | std | `#c9bfae` | .92 | 0 | `map linen.webp` | プーフ・スツール |
| cushionA / B / C | std | `#7a9469` / `#c9bda9` / `#8ba57c` | .95 | 0 | `map linen.webp` | クッション。**B は無彩色寄り**（アクセントを増やしすぎない） |
| rug / rugB | std | `#c7a87e` / `#bda07a` | 1.0 | 0 | `map jute.webp` | 机島・会議室のラグ |
| rugArt | std | `#ffffff` | .95 | 0 | `map rugart.webp`（セージのフェルト柄で再生成） | ラウンジのラグ |
| panelA / panelB | std | `#7a9469` / `#6f8f63` | .95 | 0 | `map felt.webp` | 机のフェルト仕切り（高さ 0.35m）。**幕板ではなく仕切りとして使う** |
| paper（材質） | std | `#f7f4ea` | .92 | 0 | – | 書類 |
| mugA / mugB / mugC | std | `#d8cfc2` / `#7a9469` / `#c28a3a` | .40 | 0 | cc .5 | マグ。**ショッキングピンク/シアンは退役** |
| bookA〜E | std | `#a8574a` `#5f7d59` `#c9a86a` `#7d766c` `#8a6a45` | .85 | 0 | – | 本棚。**紫 `#8a5cff` は退役** |
| leaf / leaf2 | → `leafCard` へ統合 | – | – | – | – | §3.3 |
| pot | std | `#e0d6c6` | .35 | 0 | cc .25 | 白陶器の鉢（テラコッタは `potTerra` 新設） |
| kbd | std | `#e8e3db` | .60 | 0 | `map keyboardTexture()` | – |
| sky | std | `#ffffff` | .20 | 0 | `map/emissiveMap window_day.webp`・emissiveIntensity **1.15** | 窓の外。**bloom しきい値 1.0 をわずかに超える＝窓だけがにじむ** |
| accent（材質） | basic | `#ffffff`・toneMapped false | – | – | instanceColor＝状態色 | ロボの胸リング・アンテナ球。状態色は §6.3 |
| visor | phys | `#262626` | .12 | 0 | cc 1.0 / ccR .08 | 顔スクリーン（黒ガラス）。**発光させない**（青 emissive は退役） |
| glass | std | `#ffffff` | .04 | 0 | transparent / opacity **.10** / envMapIntensity 1.4 / depthWrite false / DoubleSide / renderOrder 10 | **transmission は使わない**（追加パスが乗る・SwiftShader で不安定） |
| shadow | basic | `#3a2e20` | – | – | `map softShadowTexture()` / opacity .55 | 接地 AO。紫 `#2c2647` から暖色へ |
| islandShadow | basic | `#4a3d2c` | – | – | opacity .22 | 台座の落ち影 |
| crown | std | `#d8b45c` | .30 | .70 | – | 王冠（ボス席）。emissive を外す |
| lampWarm | std | `#ffe3b0` | .40 | 0 | emissive `#ffc87a` **1.3**・toneMapped false | ペンダント/デスクランプの発光部。脈動は残す（振幅 ±.06） |
| glowW | basic | `#ffd9a0` | – | – | radial・Additive・opacity .18 | ランプ直下の床グローだけ残す |
| screen0..3（実行時） | basic | – | – | – | `screenTexture()`→`screen_*.webp` | 紺地に紫バー → **白地に薄いセージと墨のコード/グラフ**で再生成 |

### 3.2 退役（キーごと削除）

`neon` `neonC` `stage`（孤児）`holo` `screenGlow` `eye` `reflP` `reflC` `glowP` `glowC`。
これに伴い `office.js` の `neonRing()` 呼び出し 9 箇所・サーバー LED 48 本・足元ライン・`HOLO_PANELS`・
擬似映り込み板 4 枚・フェイクブルーム板 6 枚も撤去する（`scene3d.js` の update の脈動・自転も同時に）。
**目の emissive も退役**＝目は顔テクスチャの白い形に統合（§6.4）。

### 3.3 新設

| キー | 型 | 色 | r | m | その他 | 用途 |
|---|---|---|---|---|---|---|
| linen | phys | `#ded5ca` | .95 | 0 | sheen .5 / sheenColor `#fff6e8` / sheenRoughness .85 / `map linen.webp` | ソファ・クッション（mobile は std へ） |
| felt | std | `#7a9469` | .95 | 0 | `map felt.webp` | 机の仕切り・椅子の座 |
| leafCard | std | `#ffffff` | .65 | 0 | `map leaf_atlas.webp` / alphaTest .5 / DoubleSide / vertexColors | 葉カード。**全植物の葉を 1 メッシュに merge＝1 draw** |
| potTerra | std | `#8a6a45` | .80 | 0 | – | テラコッタの鉢 |
| glassPane | std | `#ffffff` | .04 | 0 | opacity .08 / envMapIntensity 1.4 / depthWrite false | 窓ガラス面（白い反射筋用に 1 枚重ねる） |
| joint | std | `#5e5a55` | .70 | 0 | – | ロボの関節・手・足（黒ゴム）。instanceColor で殻色×0.3 |
| rattan | std | `#c8a878` | .75 | 0 | `map rattan.webp` | ペンダントの外側 |
| wallart | basic | `#ffffff` | – | – | `map wallart_a/b.webp` | 額装プリント 2 枚（額縁は slab で作る） |
| signWood | std | `#c9a86a` | .70 | 0 | – | 島の木の看板（紫ネオンサインの置き換え） |

**マテリアル数の見込み**: 62 − 10（退役）+ 9（新設）= **61**（ゲート ≤64）。`stats()` に `materials` を足してゲートを発効させる（設計時点では未発効、V1 で実装済み）。

## 4. ライティング

| 光 | 色 | 強度 | 位置・設定 |
|---|---|---|---|
| Hemisphere | sky `#f0f4fa` / ground `#d9c9b0` | **0.60** | 日陰を僅かに寒色・床際を暖色＝色温度差の土台 |
| Key（DirectionalLight） | `#fff3e0` | **1.60** | `(-15, 17, -19)`（窓側の左奥・仰角 ≈35°）。castShadow |
| Fill | `#e9efe6` | **0.45** | `(14, 10, 15)` |
| Rim | `#ffe9cf` | **0.35** | `(-8, 5, -13)`。**紫リム `#7c5cff 1.10` は退役** |
| Environment | §4.2 の PMREM 小部屋 | `environmentIntensity` **0.60** | MeshStandard の拡散にも効く＝擬似 GI |
| Fog | `#f4efe7` | near 70 / far 150 | 台座の外だけ溶ける |

### 4.1 影
- `shadowMap.type = THREE.PCFShadowMap` を**明示**（`PCFSoftShadowMap` は r185 で deprecated＝内部で PCF に落ちて警告だけ出る）。
- 解像度 **2048²**・shadow camera は `contentBox` を光空間へ投影した AABB にフィット（現行は ±33 の固定値で L レイアウトが欠ける）。
- **`shadow.intensity = 0.55`**（r185 の機能）＝影の中に環境光が残る。参照 C の落影は床の ×0.88〜0.92 しかない。
- 半影の狙い **12cm** → `radius ≈ 0.12 / テクセル寸法`（2048² で ≈5）。`bias -0.0004` / `normalBias 0.02`。
- **窓（sky）のバッチは `castShadow=false`**。忘れると窓が塞がり日差しが一切入らない。
- 葉カードは `castShadow=true` のまま（depth material が `map`+`alphaTest` を写すので切り抜き影になる）。
- VSM（本当に溶ける半影）は `?shadow=vsm` の試験口だけ残す。**golden は PCF で撮る**。

### 4.2 環境マップ（`ui/iso/env.js`・`envTexture()` を置換）
`PMREMGenerator.fromScene`（core にある）で**部屋の形をした env** を作る。equirect のグラデでは方向性が出ず、陶器の艶が死ぬ。
1. `envScene` の全メッシュは `MeshBasicMaterial`（unlit・`toneMapped:false`）＝色をそのまま放射輝度に。
2. 部屋 `BoxGeometry(6,3,6)` `side:BackSide`。天井 `#f6f2ec`・壁 `#efebe4`・床 `#d9c5a5`（床を暖色に＝ロボの下側が暖かくなる）。
3. 窓: −X 壁に 3×2m の Plane を `setRGB(6,6,6)`（HDR・PMREM は half float）。奥壁 −Z にも 2×1.6m を `(4.0,4.4,4.8)`。
   **この 2 枚がロボの頭の白いハイライトになる**（参照 C_robots の見えの本体）。
4. 暖色ランプ: +X 天井付近に r0.25 の Sphere を `(3.0,2.3,1.5)`。
5. `pmrem.fromScene(envScene, 0.04, 0.1, 100)`（sigma 0.04＝窓の縁を少し溶かす。剃刀のハイライトは安っぽい）→
   `scene.environment` / `environmentRotation.y` で窓の向きを実際の窓壁へ合わせる → `pmrem.dispose()`。
6. constructor で**同期に 1 回だけ**生成（`assetsPending` の流儀を汚さない）。夕用にもう 1 枚（窓 `(3.5,2.6,2.0)`・ランプ ×6）。

### 4.3 トーンマッピングと時間帯
- **`NeutralToneMapping`・exposure 1.0**（探索 0.95〜1.10）。ACES で 1.06 が上限だったのは白壁が早く飛び彩度が抜けるから。
  Neutral は 0.8 以上だけを圧縮するので `#ebe7e2` の壁を白のまま保てる。**「exposure ≤1.06」の旧掟は ACES 前提**＝
  Neutral 移行後の実質的な歯止めは `style_score luma_mean ≤ 0.80`。
- ポスト有効時はシーンを `NoToneMapping` で RT に描き、合成パスで `#include <tonemapping_fragment>` → `<colorspace_fragment>`。
  `quality:"mobile"/"off"` は直描きで `renderer.toneMapping = Neutral`＝**どの品質でも同じトーンカーブ**。
- 時間帯 3 プリセット（`setHour`・smoothstep）: 朝＝Key `#ffe6c8` 1.3・仰角 28° ／ 昼＝上表 ／ 夕＝Key `#ffd2a6` 1.2＋`lampWarm` 増＋夕用 env。**夜は作らない**。

## 5. カメラと後処理

- **OrthographicCamera を継続**（az 45° / el 40° / DIST 52 / MARGIN 0.86）。参照ボードは透視 fov≈28・el≈50 だが、
  `resize()`・`focusOn`・ピンチ/パン・名札の `labelAnchor` が全部 ortho の数学で書かれており、透視化は
  **HUD とスマホの操作系まで巻き込む**。C の「らしさ」は角度ではなく光と材質と切断模型フレーミングで作る（§10 の実測でも
  構図要素は再現できている）。el を 44° まで起こす案は V5 で間取りごと再検討（golden 撮り直しが要る）。
- 壁は **厚み 0.25m の切断模型**（内側 paper・天端 paper-cap・外側 shell-out）。高さ 2.5m。**天井構造物は置かない**（俯瞰が塞がる）。
- ポスト処理（`ui/iso/post.js`・自前・addons は vendor しない）:
  `scene → RT(MSAA4・HalfFloat・toneMapping=None) → 輝度しきい値 1.0 → 1/4 解像度で分離ガウス → 合成`。
  合成 = `RT + bloom*0.10` → トーンマップ → ビネット −8% → 整数ハッシュのグレイン ±1% → sRGB。`alpha` は保持（`?stream=1` 用）。
- **ティルトシフトは C では切る（既定 `tilt:0`）**。理由: ①ミニチュア（方向 A）の記号で写実と矛盾 ②画面上下のロボの
  ❗や表情が読めなくなる ③HTML 名札はシャープなままなので「ぼけた絵に鮮明な字」になる ④iPhone で最も重い。
  コードは残し `?tilt=1` の opt-in（上下端 8%・2px 相当）。
- `renderer.info.autoReset=false` ＋ フレーム頭で `info.reset()`。**これを忘れると drawCalls ゲートが嘘をつく**（合成パスの 1 だけ数える）。
- グレインは整数ハッシュ（`sin()` ベースは GPU で値がぶれる）・**時刻を混ぜない**＝静止グレイン・決定論。
- quality: `high`（MSAA RT＋bloom・dpr≤2）／`mobile`（直描き・bloom 無し・dpr≤1.5・sheen 無し）／`off`（直描き・透過保持）。

## 6. ロボの造形言語

### 6.1 プロポーション（`robot_C_turnaround.jpg` 実測）
- **2 頭身**（頭高 / 全高 = 0.49）。立位全高 1.0m 換算で頭径 0.61m・顔幅 0.41m・目径 5cm。
- 顔スクリーンは **頭幅の 67%**・顔の縦位置は頭高の 25〜90%（低め）。目は顔幅の 12% 径・間隔 54%・顔高の 49% に配置。
  口は幅 20% の弧。**眉・鼻・頬・光彩は無い**。
- 首は頭幅の 0.52・胴は頭幅の 0.78 の洋梨型。腕 2 節（肩に黒球・肘は継ぎ目のみ）。脚は短く足ポッド 2 個。
- 殻はサテン陶器（ハイライトが頭全幅で L85→95→93 の**10 L 幅＝非常に広く鈍い**）。下面は L66（接触 AO）。
- 部品ジオメトリに**頂点色 AO** を焼く（頭の下面・首・腋を 0.88）。`merge.js` の `ATTRS` に `color` を足す（無い側は 1,1,1 で埋める）。
  `instanceColor` は頂点色と乗算されるのでロブスター/職業 tint と共存する。

### 6.2 ベンダー別シルエット（60px で見分く）
| | 頭 | アンテナ | 手 | 殻色 |
|---|---|---|---|---|
| Claude | 球（下面やや平） | **1 本**・棒は頭高の 0.38・先に球 | 黒ミトン | `#e9e0d5` クリーム |
| Codex | **角丸四角**（角 R ≈ 頭高の 0.35・横長 W:H 1.37） | **2 本**・外へ ±15° | 黒ミトン | `#787676` グラファイト |
| OpenClaw | 横長楕円ヘルメット（バイザー縁） | **2 本の弧**（頭高の 0.85・外へ湾曲） | **2 枚爪のハサミ** | `#bb4838` ブリック |
差異は**シルエットと instanceColor だけ**で作る（骨格・InstancedMesh 構成は共通）。頭 3 種・アンテナ 3 種・手 2 種の部品差し替えで draw は +6 程度。

### 6.3 状態の可視化（光ではなく姿勢・表情・胸リングで）
| 状態 | 胸リング/アンテナ球（`accent` の instanceColor） | 姿勢 | 表情 |
|---|---|---|---|
| working | `#7a9469` | 前傾・タイプ | focus |
| waiting | `#c28a3a` | 背もたれ・小さく揺れ | idle / blink |
| attention（❗） | `#c0483a` | 手を挙げる | question |
| resting | `#b4aca0` | ソファ・脱力 | sleepy / happy |
| external | `#8a6a45` | – | idle |
**光らせない**（emissive を上げない）。色は小さな面に置き、形（挙手・💭）と HUD の❗で伝える。

### 6.4 表情アトラス（4×2・手続き canvas・白い形だけ）
`idle / blink / focus / think / question / happy / sleepy / alert`。黒ガラスの上に白い形だけを描く（`robot_C_faces.jpg` 参照）。
`InstancedBufferAttribute("faceCell")` ＋ `onBeforeCompile` で UV をずらす。フォールバックは表情別 InstancedMesh（+8 draw）。
**目は発光させない**（bloom を通すと C から外れる）＝旧 `eye` マテリアルは退役。

### 6.5 職業アクセサリ（既存 9 種を継続）
`phones/cap/beret/pencil/bowtie/mortar/headset/hardhat/eyeshade`。色は `arch.accC` のまま。ただし**彩度を落として**
セージ・オーク・墨・テラコッタの範囲に収める（原色は使わない）。該当なしのロボには何も付けない（嘘の帽子を作らない）。

## 7. HUD トークン（`ui/iso/style.css` の `:root`・PWA `app.css` と同値）

| トークン | 値 |
|---|---|
| 面 | `--paper #f7f4ef` / `--paper-2 #efe9e1` / `--paper-3 #e6dfd5`（**不透明**・ガラス blur は廃止） |
| 線 | `--line rgba(46,45,44,.14)`（hairline 1px）／区切りは線 1 本か背景の明度差だけ |
| 文字 | `--ink #2e2d2c` / `--ink-2 #5e5a55` / `--ink-3 #8a8279` |
| アクセント | `--accent #5f7d59`（文字・主ボタン）／`--accent-chip #e3ebdd`（チップ地）／`--accent-3d #7a9469` |
| 意味色 | `--warn #c28a3a` / `--alert #c0483a` / `--rest #b4aca0` |
| 型 | 11 / 12.5 / 14 / 18 / 24px（5 段）。行間 1.45。等幅は状態値のみ |
| 余白 | 4 の倍数（4/8/12/16/24）。角丸 10（小）/ 14（面） |
| 影 | 1 段だけ `0 1px 2px rgba(46,45,44,.06)`。多重影・blur・グラデは使わない |
| 動き | transition 120ms ease-out。keyframe アニメは禁止（golden がフレークする） |

## 8. 禁止事項

1. **紫・青紫・ネオンシアン・彩度の高い青**（旧 UI の `#7c5cff / #4f8dff / #53e0c4`）。機械ゲート= `style_score --profile c` の「禁止色（シアン〜青〜紫）の面積 ≤ 0.02」＝色相 160〜300°・彩度 55 以上の画素。
2. **発光で状態を伝えない**（emissive を上げる・光る線・ホログラム・グロー板）。残す発光はランプと窓だけ。
3. **夜プリセット・暗い背景**。基準は常に明るい昼光。
4. **絵文字箇条書き・border-left のアクセントバー・紫グラデのボタン**（全プロジェクト共通のデザイン方針）。
5. **ガラスの transmission**・SSAO・TAA・three addons の vendor・`ctx.filter` blur・`Math.random` / `Date.now`（決定論が壊れる）。
6. **天井構造物**（俯瞰が塞がる）。壁は 2.5m まで。
7. **読める文字をテクスチャに焼かない**（i18n が破れる・画面/看板はダミーのバーと図形で）。
8. **ティルトシフトの常用**（C では既定 0）。
9. アクセントを 2 色以上に増やさない。意味色（warn/alert/rest）はアクセントとは別枠で、面積を小さく保つ。

## 9. 参照画像（`docs/art/`・各 ≤400KB）

| ファイル | 中身 |
|---|---|
| `board_C_overview.jpg` | **採用**の全景（俯瞰・パレットと構図の正本） |
| `board_C_overview_alt.jpg` | 別案の全景（1 段暖く暗い・比較用） |
| `board_C_robots.jpg` | ロボ 3 体寄り（ベンダー別シルエットの正本） |
| `board_C_hud.jpg` | デスクトップ HUD モック（1440×900・情報設計は §7 と U2 が正本） |
| `board_C_phone.jpg` | スマホ縦モック（390×844） |
| `robot_C_turnaround.jpg` | ロボの 4 面図＋着席/挙手/歩行（プロポーションの正本） |
| `robot_C_faces.jpg` | 表情 8 種（顔アトラスの正本） |
| `tex_C_set.jpg` | 素材 4 種（オーク床・リネン・セージフェルト・コンクリート） |
| `tex_C_window.jpg` | 窓外の樹冠（`window_day.webp` の元。**方立ごと写っているので中央を切り出す**） |

### 9.1 テクスチャ（`ui/iso/tex/`・**生成済み**・1 枚 ≤80KB・合計 ≤600KB）

GPT Image 2.0 で生成し、PIL で「周期検出→切り出し→継ぎ目処理→webp」まで済ませた実物。合計 **225KB**（`city.webp` 35KB は撤去）。

| ファイル | 寸法 | KB | 用途 | 繰り返し |
|---|---|---|---|---|
| `oak_floor.webp` | 512² | 31 | 床（repeat 1/1.6m）・机天板（repeat 1/1.2m・色で 1 段明るく） | `wrapS=Repeat` / **`wrapT=MirroredRepeat`** |
| `linen.webp` | 256² | 16 | ソファ・クッション（無彩色＝材質の color で着色） | Mirrored 両軸 |
| `felt.webp` | 256² | 14 | 机の仕切り・椅子の座（無彩色） | Mirrored 両軸 |
| `jute.webp` | 256² | 18 | ラグ | Mirrored 両軸 |
| `rattan.webp` | 256² | 11 | ペンダント照明の外側 | Mirrored 両軸 |
| `leaf_atlas.webp` | 512² RGBA | 25 | 葉カード（モンステラ/ストレリチア/ポトス/サンスベリアの 2×2・**白キーで alpha 化済み**・不透明 23%） | – |
| `window_day.webp` | 1280×320 | 66 | 窓の外（樹冠と白い空・**枠は含まない**・壁 1 枚で連続 UV） | `wrapS=Repeat` |
| `wallart_a.webp` `wallart_b.webp` | 256×320 | 8 / 3 | 額装プリント（シダ・ユーカリの線画） | – |
| `rugart.webp` | 640×392 | 15 | ラウンジのラグ（セージのフェルト柄） | – |
| `screen_code.webp` `screen_dash.webp` | 640×400 | 4 / 7 | モニタ（**読める文字は無い**・墨のバーとセージのアクセント） | `wrapT=Repeat`（UV スクロール） |

**繰り返しの掟**: タイル素材は `MirroredRepeatWrapping` を既定にする。オフセット法で継ぎ目を溶かしても
「板ごとに色が違う木目」は上下方向で必ず段差が出る（実測 92/255）。鏡で折り返せば継ぎ目は**構造的に発生しない**。
織物（リネン・フェルト・ジュート・ラタン）は鏡像が視認できないので両軸 Mirrored でよい。
**オークは彩度がやや高い**（生成物 h33 s0.37）ので、材質の `color` で `#f0e6d8` 程度を掛けて参照の淡いオークへ寄せる。

## 10. 較正値と機械ゲート

`board_C_overview.jpg` の実測（`python3 tools/style_score.py --profile c --calibrate docs/art/board_C_overview.jpg`）:

| 指標 | 参照の実測 | ゲート（`--profile c`） |
|---|---|---|
| 明るい一様面の割合 | 0.191 | ≤ 0.22 |
| 色の種類数 | 92 | 60〜400 |
| 禁止色（シアン〜青〜紫）の面積 | 0.000 | ≤ 0.02 |
| アクセント（セージ）の面積 | 0.006 | 0.001〜0.08 |
| 明度の標準偏差 | 0.221（**室内だけ切ると 0.189**） | **0.15**〜0.40（実測 0.165） |
| 平均輝度 | 0.665 | 0.58〜0.80 |

- アクセント判定は **色相 105±30・彩度の床 26**（既定の 55 では低彩度のセージが 1 画素も拾えず「測っているつもりで常に 0」になる。実測で踏んだ）。
- ほかの常設ゲート: drawCalls ≤ 300（見込み 150〜160）・マテリアル ≤ 64（見込み 61）・tex 1 枚 ≤80KB / 合計 ≤600KB・
  golden は SwiftShader で 2 回撮ってビット一致・`ui/core` は DOM/通信/時刻/乱数に触らない。

## 11. リアルタイム再現の期待値（正直に）

| 要素 | 見込み | 補足 |
|---|---|---|
| 床・壁・机・ラグ・ガラス箱・窓の外 | **高** | テクスチャ＋lightMap＋黒フレームで参照と並べても違和感が出ない見込み |
| 陶器のロボ | **高** | 艶の本体は PMREM の窓ハイライト。参照ロボ自体が単純形状＋反射で成立している |
| 植物 | 中〜高 | 引きでは本物・寄ると板（葉カード）。葉の透けは作らない |
| リネンのソファ | 中 | `puff()` の膨らみ＋sheen＋縁の AO で「柔らかい塊」まで。しわ・縫い目は作らない |
| 影・接地 | 中〜高 | 半影は 12cm 固定（距離で広がる PCSS は無理）。4 層（影マップ・**ゾーン天面に焼いた頂点AO**・頂点 AO・接地カード）で寄せる。※床の `lightMap` は**ゾーン台座に覆われて 0.4% しか見えない**ことが実測で判明（R90-V5）＝接地AOは頂点色側が本体 |
| GI の色滲み（植物の緑が白壁に乗る等） | **低** | hemisphere ground と env の床色で「全体的な暖色の回り込み」だけ。局所的な色移りはしない |
| 床の実反射 | **低** | SSR は入れない。PMREM の窓が roughness .5 の床にぼんやり乗る程度（参照 C も実反射はほぼ無い） |

**総じて参照画像の 7〜8 割**。ゲーム画面としては上等、静止画レンダの代わりにはならない。
V2 の実レンダを見て、届かないと判断したら A 案のマット質感（ティルトシフト＋クレイ）へ寄せる「C-lite」に舵を切れる
（部品は同じで、材質値とポストの設定だけが変わる）。

## 12. 実装順序（V1 / V2 の分担）

**V1 レンダ基盤**（`Codex可(ブロック)`）: `post.js`（§5）→ `env.js`（§4.2）→ ライティング書換（§4）→ 窓バッチ `castShadow=false` →
`merge.js` に `color` 属性＋`bake.js`（頂点 AO・床 lightMap）→ `stats().materials` 追加・quality 3 段。
着手前に 20 行スパイクで 3 点だけ確かめる: ①SwiftShader で「MSAA4＋HalfFloat RT→合成」が 2 回撮ってビット一致するか
②`PMREMGenerator.fromScene` が SwiftShader で決定論か ③`alphaTest` の葉が影マップに出るか（出なければ `customDepthMaterial`）。

**V2 材質・デカール**（`Codex可`・画像は Claude が先に生成して参照パスを渡す）: `makeMaterials()` を §3 へ全面書換 →
テクスチャ 9 枚 → 床（オーク＋目地 normal＋lightMap）→ 植物（`plants.js`）→ ガラス/窓/ペンダント/額装/ラグ/フェルト/リネン →
ロボの殻・joint・visor と頂点 AO → `style_score --profile c` を verify ▶7 に配線 → golden 撮り直し。

壁の厚み 0.25m と `puff()`（ソファの膨らみ）は本来 V4/V5（kit.js）の仕事だが、**「7〜8 割」を早く見たいなら V2 に前倒し**してよい
（どちらも 30 行以内・golden 更新が 1 回で済む）。
