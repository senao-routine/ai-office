# AI Office アートディレクション v3（正本・R93-AD2'）

> **状態: 確定（2026-09-14・本人裁定「旧の雰囲気 × 今の描画力」→ M0' で G1「ラベンダー・グラスロフト」を採用）**
> この文書が見た目の SSOT。委譲プロンプト・コード・golden が矛盾したら**この文書が勝つ**。変えるときはここを先に直す。
> v2（方向 C・2026-09-07）からの差分は**色・光・HUD・規模感**だけ。**ロボ（§6）は v2 のまま**＝「今の描画力」の中核。
> 数値の根拠は §9 の採用ボードを PIL で実測した値（§10）と、旧 R85 の実装（`f671101:ui/iso/*`）の棚卸し。
> **期待値の合意**: リアルタイム three.js で届くのはボードの **7〜8 割**（§11）。V4' の実レンダで `screen_glow` の下限を確定する。

## 0. v2 → v3 で変わること・変わらないこと（一覧）

| 変わる | 変わらない |
|---|---|
| パレット: オーク×クリーム×セージ → **白×薄ラベンダー×ガラス、紫 `#7c5cff`＋青 `#4f8dff` のアクセント** | ロボの造形・顔スクリーン・ベンダー別シルエット・殻の色8種・職業アクセサリ（§6） |
| 光: 暖色の昼光 → **寒色の昼光**（key 白・fill 青白・rim 紫弱・fog `#dfe8ff`） | カメラ（ortho az45/el40/DIST52/MARGIN .86）・影の方式・PMREM 小部屋の**仕組み**・Neutral 1.0 |
| 発光: 窓とランプだけ → **黒モニタが青白く光る（emissive＋post.js bloom）** | 発光は post.js の実 bloom **だけ**（fake glow 板は戻さない・構造ピン） |
| HUD: 不透明の紙 → **ガラス（blur）・角丸 18・影 2 段・ステージ背景の紫青グラデ** | 情報設計・DOM・操作系（`ui/hud/*`）・PWA の骨格 |
| 規模感: L/XL へ育つ → **コンパクト規則**（M を見せ方の上限にするプリセット・L/XL のピッチを詰める） | tier の記憶（maxSeen）・席の割当ロジック |
| 材質表: 全キーの**値**（テクスチャ 8 枚差替） | `makeMaterials()` のキー集合・材質数（59）・drawCalls |

## 1. コンセプト（1文）

**白いガラスの箱に入った、薄ラベンダーの光が満ちる小さなオフィスの模型。** 縁の見えるガラス仕切り、白いラミネートの机、
紫と青の椅子とラグ、黒いモニタがそれぞれ青白く灯っている。そこに 2 頭身の陶器のロボが座って働いている。
色は「白・薄ラベンダー・紫と青の2アクセント」。暖色は無い。硬い影も、夜も無い。**光るのは画面だけ**。

## 2. パレット（役割つき・hex・採色元）

| 役割 | hex | 採色元 / 用途 |
|---|---|---|
| paper | `#f4f4fb` | 壁の内側面・HUD の地。旧 `--iso-bg`。h240 の寒色白（**青みが 4〜6 あるのが正**） |
| paper-2 | `#e8e8f4` | 壁の陰面・キャビネット・受付天板 |
| paper-cap | `#fbfbfe` | 壁の天端（切断キャップ）・背景。純白に最も近い |
| shell-out | `#c9c9d8` | 壁の外側面＝「白い台に置いた模型」の記号（v2 と同じ役割・寒色へ） |
| floor | `#e7ecfb` | 床（旧 `floor`）。淡ラベンダー白の大判タイル。目地 `#d3d8ee`（板差 ±3 L） |
| floor-2 | `#dfe3f7` | ゾーン台座（会議室・ラウンジ）。床より僅かに濃い |
| dark-floor | `#3a3f66` | 外部接続ベイ（小面積の紺）。旧 `#2e3352` より 1 段明るく |
| desk | `#f6f7fd` | 机天板（白ラミネート・マップ無し）。木目は無い |
| desk-edge | `#dcdff0` | 天板の小口・脚 |
| ink | `#23213a` | ガラスの枠・小物の墨・HUD 本文（旧 `--iso-ink`）。ロボの関節は v2 のまま |
| ink-2 | `#6c6890` | HUD の補助文字（旧 `--iso-ink-soft`） |
| **accent** | `#7c5cff` | 紫。HUD の主操作・チップ・3D の椅子（seat）・クッション |
| **accent-2** | `#4f8dff` | 青。HUD の第 2 操作・3D の椅子（seatB）・ソファの一部 |
| accent-chip | `#eee9ff` | HUD チップの地 |
| seat | `#8f86e0` / `#7a9cf0` / `#a99cf5` | 椅子 3 色（紫・青・淡紫）＝ボードの椅子 |
| sofa | `#8a8ee6` / `#a3a8f0` / `#6f9bf0` | ソファ（ペリウィンクル）・プーフ・アームチェア（青） |
| rug | `#c8cdf2` / `#b9c0ea` | ラグ（ボードの薄紫のマット） |
| glass | `#a9c8ff` @ opacity .16 | ガラス仕切り。**縁 `#b8b4ff` の枠（0.03m）が主役**＝縁の見えるガラス |
| glass-rail | `#9a9ab4` | ガラスのレール・椅子の脚・支柱（旧 `steel`） |
| monitor | 筐体 `#1d1b30` ／ 画面 emissive `#dfe9ff` ×1.5 | **黒い筐体に青白い画面**。bloom で滲む（唯一の発光） |
| warn / alert / rest | `#f5a524` / `#e0538a` / `#aab2d8` | 意味色（旧 R85 と同値）。面積は小さく |
| working / external | `#4fb3a3` / `#5aa2ff` | 胸リング（sat を抑えたティール／青） |
| plant | `#4f7a4a`（明面 `#7aa070`・鉢 `#f0f0f8` 白陶器） | 葉は緑のまま（ボードにも緑の植物がある） |
| plant-out | `#b8c8b0` | 窓の外の樹冠（寒色に寄せる） |
| lamp | `#ffd9a0` | ペンダントの発光（唯一の暖色・弱く） |
| shell-claude / codex / openclaw | `#e9e0d5` / `#787676` / `#bb4838` | **v2 のまま** |
| face | `#262626` | 顔スクリーン（v2 のまま） |
| fog | `#dfe8ff` | 台座の外を溶かす寒色フォグ（旧値） |
| stage-bg | radial `#e9e4ff`→透明（左上）＋ radial `#e2ecff`→透明（右下）＋ linear `#f6f5fc`→`#eeeefb` | ステージ／ページの背景（旧 `.ui-iso` の型） |

**掟**: 暖色の面を作らない（ランプの発光と木の看板だけ例外）。**紫と青は 2 色で 1 組**＝どちらか一方にしない。
アクセントは面積が小さいのが正（ボード室内の実測 4.7%・§10）。白飛びしてよいのは壁の天端と背景と**画面の中心**だけ。

## 3. マテリアル表（`makeMaterials()` のキー 1:1・**キー名と数は v2 のまま（現行コードの集合が正）、値だけ差替**）

`color × map` は線形空間の乗算＝色を持つマップに hex を掛けると二重に暗くなる（v2 で踏んだ）。v3 の補正表:

| 材質 | マップ | 目標 | `color` に入れる値 |
|---|---|---|---|
| floor / floor2 | `floor_cool.webp`（淡ラベンダー白タイル・平均 `#e4e8f6`） | `#e7ecfb` / `#dfe3f7` | **`#ffffff`** / `#f6f8ff` |
| wood / wood2（机天板） | **マップ無し**（白ラミネート） | `#f6f7fd` / `#f2f3fb` | そのまま |
| seat / seatB / seatC | `felt.webp`（無彩・平均 `#afadab`） | `#8f86e0` / `#7a9cf0` / `#a99cf5` | **`#d0c3ff`** / **`#b3e0ff`** / **`#f0e3ff`** |
| panelA / panelB | `felt.webp` | `#a9a2ea` / `#98b4f2` | **`#f2ebff`** / **`#dcf0ff`** |
| sofa / sofaC | `linen.webp`（平均 `#c1bcb5`） | `#8a8ee6` / `#6f9bf0` | **`#b6bcff`** / **`#93ccff`** |
| rug / rugB | `jute.webp`（平均 `#bfa072`）→ **`jute` は使わない**。`rug_cool.webp`（無彩の織り・平均 `#c4c4c4`） | `#c8cdf2` / `#b9c0ea` | **`#ffffff`** / **`#f0f4ff`** |
| rugArt | `rugart.webp`（寒色版で再生成） | – | `#ffffff` |

### 3.1 継続（値だけ差し替え）

| キー | 型 | v3 の色 | r | m | その他 | 備考 |
|---|---|---|---|---|---|---|
| base | std | `#e8e8f4` | .85 | 0 | – | 台座 |
| floor | std | `#ffffff`（テクスチャで着色） | .35 | 0 | `map floor_cool.webp`(repeat 1/1.2m・aniso 4)・`normalMap` 手続き目地(.25)・`lightMap` 手続き 512² | 大判タイル。**m は 0 のまま**（映り込みは PMREM の寒色小部屋で） |
| floor2 | std | `#f6f8ff`（map 併用） | .40 | 0 | `map floor_cool.webp` | ゾーン台座 |
| woodFloor | std | `#eef0fa` | .45 | 0 | `map floor_cool.webp`（90°回転） | ラウンジ台座 |
| darkFloor | std | `#3a3f66` | .85 | 0 | – | 外部ベイ |
| white | phys | `#f6f7fd` | .70 | 0 | – | 什器（机側板・枠・カウンター・棚）＝白ラミネート |
| shell | phys | `#e9e0d5` | .32 | 0 | cc .70 / ccR .18 / envMapIntensity 1.0 | **v2 のまま**（ロボの殻） |
| dark | std | `#23213a` | .55 | .10 | – | ガラスの枠・椅子の肘掛・小物（寒色の墨） |
| darker | std | `#1d1b30` | .60 | .10 | – | モニタ筐体・サーバーラック（紺黒） |
| steel | std | `#9a9ab4` | .35 | .85 | – | ガラスのレール・椅子の脚・支柱 |
| wood | std | `#f6f7fd` | .30 | 0 | **map 無し**・cc .15 / ccR .4 | 机天板（白ラミネート・天板だけ僅かにツヤ） |
| wood2 | std | `#f2f3fb` | .32 | 0 | 同上 | 会議卓・コーヒーバー |
| seat / seatB / seatC | std | `#d0c3ff` / `#b3e0ff` / `#f0e3ff`（felt 補正済み） | .90 | 0 | `map felt.webp` | 椅子。**紫・青・淡紫の 3 色** |
| sofa | phys | `#b6bcff`（linen 補正済み） | .95 | 0 | sheen .4 / sheenColor `#f0f2ff` / `map linen.webp` | ソファ（ペリウィンクル）。mobile は std |
| sofaC | std | `#93ccff` | .92 | 0 | `map linen.webp` | 青いアームチェア・プーフ |
| cushionA / B / C | std | `#d0c3ff` / `#f0f0f8` / `#b3e0ff` | .95 | 0 | `map linen.webp` | クッション（B は白） |
| rug / rugB | std | `#ffffff` / `#f0f4ff` | 1.0 | 0 | `map rug_cool.webp` | ラグ（薄紫のマット） |
| rugArt | std | `#ffffff` | .95 | 0 | `map rugart.webp`（寒色の幾何柄で再生成） | ラウンジのラグ |
| panelA / panelB | std | `#f2ebff` / `#dcf0ff` | .95 | 0 | `map felt.webp` | 机のフェルト仕切り（薄紫・薄青） |
| paper（材質） | std | `#f7f7fc` | .92 | 0 | – | 書類 |
| mugA / mugB / mugC | std | `#e0538a` / `#4fc9ff` / `#7c5cff` | .40 | 0 | cc .5 | マグ（旧の色を復帰・面積は極小） |
| bookA〜E | std | `#7c5cff` `#4f8dff` `#e0538a` `#5c5a7a` `#f5a524` | .85 | 0 | – | 本棚（紫を復帰） |
| pot | std | `#f0f0f8` | .35 | 0 | cc .25 | 白陶器の鉢 |
| kbd | std | `#eef0fa` | .60 | 0 | `map keyboardTexture()` | – |
| sky | std | `#ffffff` | .20 | 0 | `map/emissiveMap window_cool.webp`・emissiveIntensity **1.10** | 窓の外（寒色の空と街）。**モニタより弱く**（`screen_glow` はモニタ優先） |
| accent（材質） | basic | `#ffffff`・toneMapped false | – | – | instanceColor＝状態色（§6.3） | 胸リング・アンテナ球 |
| visor | phys | `#262626` | .12 | 0 | cc 1.0 / ccR .08 | 顔スクリーン。**v2 のまま・発光させない** |
| glass | std | `#a9c8ff` | .04 | 0 | transparent / opacity **.16** / envMapIntensity 1.4 / depthWrite false / DoubleSide / renderOrder 10 | ガラス仕切り。**transmission は使わない**。枠は `dark` の細い slab で縁を見せる |
| shadow | basic | `#2c2647` | – | – | `map softShadowTexture()` / opacity .50 | 接地 AO（旧の青灰へ） |
| islandShadow | basic | `#4a4386` | – | – | opacity .20 | 台座の落ち影 |
| crown | std | `#d8b45c` | .30 | .70 | – | 王冠（v2 のまま） |
| lampWarm | std | `#ffe3b0` | .40 | 0 | emissive `#ffc87a`・強度は `HOUR_PRESETS.lamp` が決める（**現行の 0.8 倍**＝昼 2.2・夕 3.4）・toneMapped false | ランプ（v2 より弱く＝唯一の暖色） |
| glowW | basic | `#ffd9a0` | – | – | radial・Additive・opacity .12 | ランプ直下の床グロー（**唯一の Additive**・構造ピン） |
| screen0..3（実行時） | **std** | `#000000`・emissive `#ffffff`・`emissiveMap screen_*.webp`・emissiveIntensity **1.5**・toneMapped false | – | – | `screens.js` の共有材質（v2 は `MeshBasicMaterial`＝輝度 ≤1.0 で bloom しきい値に届かなかった） | **紺地 `#101a3a` に紫 `#7c5cff`／青 `#4f8dff` のバー**で再生成。文字は焼かない |
| felt / leaf / board / potTerra / glassPane / rattan / wallart / signWood | v2 の型のまま | felt `#ffffff`(map) / leaf **v2 のまま**（葉は緑） / board **v2 のまま**（日報ボード） / potTerra→**`#e8e8f4`（白陶器へ）** / glassPane `#dfe9ff` opacity .10 / rattan→`#c9c9d8`（寒色） / wallart `#ffffff`(map 寒色版) / signWood `#c9c9d8`（白い看板・文字は焼かない） | | | | キーを増減しない |
| shell / visor / accent / crown / joint | **v2 のまま** | ロボの材質（§6）。R93 では 1 値も触らない | | | | |

### 3.2 退役（v2 で退役済み・**復活禁止**）

`neon` `neonC` `stage` `holo` `screenGlow` `eye` `reflP` `reflC` `glowP` `glowC`。`neonRing()`・`HOLO_PANELS`・擬似映り込み板・
フェイクブルーム板は戻さない。**発光は `screen*`・`sky`・`lampWarm` の emissive を post.js の bloom が滲ませる経路だけ**
（`tests/iso_no_fake_glow.test.mjs` と verify ▶3b の grep ピンが機械で止める）。

### 3.3 テクスチャ（`ui/iso/tex/`・同名上書き＝枚数 14 のまま・1 枚 ≤80KB・合計 ≤600KB）

| ファイル | v3 の中身 | 生成 |
|---|---|---|
| `oak_floor.webp` → 名前はそのまま・中身を **淡ラベンダー白の大判タイル**（`floor_cool`）に差替 | 512²・目地は薄い `#d3d8ee`・板差 ±3 L・MirroredRepeat | Claude（GPT Image → PIL で周期化） |
| `window_day.webp` → 中身を **寒色の空と街**（`window_cool`）に | 1280×320・明るい青白の空・遠景の白い街 | Claude |
| `screen_code.webp` / `screen_dash.webp` | 紺地 `#101a3a`・紫/青のバーとグラフ・**文字なし** | Claude |
| `wallart_a/b.webp` | 寒色の抽象（ラベンダー／青灰） | Claude |
| `rugart.webp` | 寒色の幾何柄 | Claude |
| `jute.webp` → 中身を **無彩の織り**（`rug_cool`）に | 256²・平均 `#c4c4c4` | Claude |
| `linen.webp` `felt.webp` `rattan.webp` `leaf_atlas.webp` `board.webp` `sign.webp` | v2 のまま | – |

## 4. ライティング

| 光 | 色 | 強度 | 位置・設定 |
|---|---|---|---|
| Hemisphere | sky `#e8f2ff` / ground `#b9bfe0` | **0.60** | 日陰を寒色・床際を薄紫（旧 ground `#8a97cc` は紫が強すぎた） |
| Key（DirectionalLight） | `#ffffff` | **1.70** | `(-15, 17, -19)`（v2 と同位置）。castShadow |
| Fill | `#cfe0ff` | **0.45** | `(14, 10, 15)`（旧値） |
| Rim | `#7c5cff` | **0.55** | `(-8, 5, -13)`。旧 1.10 は bloom と重なって飛ぶ＝半分に |
| Environment | §4.2 の寒色 PMREM 小部屋 | `environmentIntensity` **0.55** | 陶器の殻に青白い窓が映る |
| Fog | `#dfe8ff` | near 70 / far 150 | 台座の外だけ溶ける（旧値） |

### 4.1 影
v2 のまま（PCF・2048²・`shadow.intensity .55`・contentBox フィット・半影 12cm・`bias -0.0004`・窓は `castShadow=false`）。
影の色は `shadow`/`islandShadow` 材質の青灰で作る（光の色を暗くして作らない）。

### 4.2 環境マップ（`ui/iso/env.js`・**仕組みは v2 のまま・色だけ寒色**）
天井 `#f6f6fd`・壁 `#eef0fa`・床 `#d6dbf2`。窓 −X 3×2m を `setRGB(5.5, 6.0, 7.0)`（青白）・奥壁 −Z 2×1.6m を `(4.0, 4.6, 5.6)`。
暖色ランプ球は `(2.0, 1.6, 1.2)` へ弱める（唯一の暖色）。sigma 0.04・夕用は窓 `(3.2, 3.6, 4.8)`＋ランプ ×4。

### 4.3 トーンマッピングと時間帯
**`NeutralToneMapping`・exposure 1.0 を維持**（ACES は白壁が飛ぶ・v2 §4.3 の記録どおり）。歯止めは `style_score luma_mean ≤ 0.84`（§10）。
時間帯 3 プリセット（夜は作らない）: 朝＝Key `#f4ecff`（桜灰）1.35・仰角 28° ／ 昼＝上表 ／ 夕＝Key `#d9d2ff`（ラベンダー琥珀）1.2＋`lampWarm` 増＋夕用 env。

## 5. カメラと後処理

- **OrthographicCamera を継続**（az 45° / el 40° / DIST 52 / MARGIN 0.86・v2 と同じ理由）。壁は厚み 0.25m の切断模型・高さ 2.5m・天井構造物なし。
- BOT_PAD: desktop **0.10** / PWA 0.17 / 配信 0（R95・HUD の下段カードはステージ外の行。0.04 と 0.10 の A/B で本人が 0.10 を選択・2026-09-14）
- ポスト処理（`ui/iso/post.js`）: `scene → RT(MSAA4・HalfFloat) → 輝度しきい値 1.0 → 1/4 解像度ガウス → 合成`。
  **合成係数 0.10 を uniform `bloomStrength` にし、既定 0.22**（constructor option `bloom`）。しきい値 1.0 据置。ビネット −8%・静止グレイン ±1% は v2 のまま。
  `quality:"mobile"/"off"` は bloom 無し＝**PWA と配信では発光は emissive の明るさだけ**（README に正直に書く）。
- **コンパクト規則（V6'）**: M spec は無改変。`layout_specs.js` の L/XL の机ピッチを詰める（XL 34.25×24.4 → 約 31×22）。
  `ui/hud/customize.js` の配置プリセットに「コンパクト」＝`tierFor` の結果を M に上限し、あふれは `assignOverflow`（受付列/ラウンジ）へ。
  `growth.js` の maxSeen ラチェットは触らない（席の記憶は保持・見せ方だけ縮む）。
- ティルトシフトは使わない（`?tilt=1` の opt-in は残す）。`renderer.info.autoReset=false`＋`info.reset()` は v2 のまま。

## 6. ロボの造形言語（**v2＝方向 C から無改変**・R93 でも触らない）

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
| working | `#4fb3a3`（ティール・sat を抑えた） | 前傾・タイプ | focus |
| waiting | `#f5a524` | 背もたれ・小さく揺れ | idle / blink |
| attention（❗） | `#e0538a` | 手を挙げる | question |
| resting | `#aab2d8` | ソファ・脱力 | sleepy / happy |
| external | `#5aa2ff` | – | idle |
**光らせない**（emissive を上げない）。色は小さな面に置き、形（挙手・💭）と HUD の❗で伝える。

### 6.4 表情アトラス（4×2・手続き canvas・白い形だけ）
`idle / blink / focus / think / question / happy / sleepy / alert`。黒ガラスの上に白い形だけを描く（`robot_C_faces.jpg` 参照）。
`InstancedBufferAttribute("faceCell")` ＋ `onBeforeCompile` で UV をずらす。フォールバックは表情別 InstancedMesh（+8 draw）。
**目は発光させない**（bloom を通すと C から外れる）＝旧 `eye` マテリアルは退役。

### 6.5 職業アクセサリ（既存 9 種を継続）
`phones/cap/beret/pencil/bowtie/mortar/headset/hardhat/eyeshade`。色は `arch.accC` のまま。ただし**彩度を落として**
セージ・オーク・墨・テラコッタの範囲に収める（原色は使わない）。該当なしのロボには何も付けない（嘘の帽子を作らない）。


## 7. HUD トークン（`ui/hud-tokens.css` が正本 → `ui/iso/style.css` と `ui/pwa/app.css` に展開）

| トークン | 値 |
|---|---|
| 面（不透明） | `--paper #f4f4fb` / `--paper-2 #ececf7` / `--paper-3 #e3e3f2` |
| ガラス（デスクトップ） | `--glass rgba(255,255,255,.72)` ＋ `backdrop-filter: blur(14px) saturate(150%)`／`--glass-solid #ffffff`。**PWA は blur を付けない**（iOS の合成コスト＝`--paper` のまま） |
| 線 | `--line rgba(96,82,170,.14)`（hairline 1px・薄紫） |
| 文字 | `--ink #23213a` / `--ink-2 #6c6890` / `--ink-3 #9793b8` |
| アクセント | `--accent #7c5cff`（主操作・文字）／`--accent-2 #4f8dff`（第 2 操作）／`--accent-chip #eee9ff`（チップ地）／`--accent-3d #8f86e0` |
| 意味色 | `--warn #f5a524` / `--alert #e0538a` / `--rest #aab2d8` |
| 型 | 11 / 12.5 / 14 / 18 / 24px。行間 1.45 |
| 余白 | 4 の倍数。角丸 **18（面）/ 12（小）** |
| 影 | **2 段** `0 12px 34px rgba(64,52,140,.12), 0 2px 6px rgba(64,52,140,.06)` |
| ステージ／ページ背景 | `--stage-bg`＝§2 stage-bg（radial 紫・radial 青・linear）。**装飾面だけ**＝ボタン・文字にグラデは使わない |
| ダーク | `.th-dark`＝寒色の「夜の紙」: bg `#12111e`・glass `rgba(28,26,46,.78)`・ink `#e8e6f6`・accent `#9b82ff`／`#6ea4ff`（旧 R85 の値） |
| 動き | transition 120ms ease-out。keyframe アニメは禁止（golden がフレークする） |

デスクトップの `--iso-stage-bg` は意図して `--stage-bg` より濃い（R95）。

## 8. 禁止事項

> **本人 override（2026-09-14・R93）**: v2 §8-1（紫・青紫の禁止）・§8-2（発光で状態を伝えない、のうち「モニタ・窓の発光」）・
> §7 の「ガラス blur は廃止」を撤回する。全プロジェクト共通のデザイン方針（**紫グラデのボタン/文字・border-left アクセントバー・
> 絵文字箇条書きの禁止**）は据え置き。

1. **発光は post.js の bloom（emissive な画面材質）だけ**。グロー板・Additive メッシュ（`glowW` を除く）・`neonRing`・`HOLO_PANELS`・
   擬似映り込み板の復活は禁止。機械ゲート= `tests/iso_no_fake_glow.test.mjs`＋verify ▶3b の grep ピン。
2. **発光で状態を伝えない**（胸リングは非発光の instanceColor・挙手・表情・HUD の❗で伝える）。光るのは画面と窓とランプだけ。
3. **夜プリセット・暗い背景**。基準は常に明るい寒色の昼光（`luma_mean ≥ 0.58`）。
4. **紫グラデのボタン・文字**・**border-left のアクセントバー**・**絵文字箇条書き**（全プロジェクト共通）。
   紫 `#7c5cff`／青 `#4f8dff` は**ソリッド**で、面積の小さいアクセントとしてだけ使う（§10 `accent_area ≤ 0.12`・`neon_cool ≤ 0.12`）。
5. **ガラスの transmission**・SSAO・TAA・three addons の vendor・`ctx.filter` blur・`Math.random` / `Date.now`（決定論が壊れる）。
6. **天井構造物**（俯瞰が塞がる）。壁は 2.5m まで。
7. **読める文字をテクスチャに焼かない**（画面・看板・本の背はバーと図形で）。
8. **ティルトシフトの常用**（既定 0）。
9. アクセントは**紫と青の 2 色で 1 組**。3 色目を足さない（意味色 warn/alert/rest は別枠・面積を小さく）。
10. `backdrop-filter` はデスクトップの `.card` `.side` `.sheet` `.tray` `.bottom` 系 **≤6 要素**まで。PWA は不透明のまま。

## 9. 参照画像（`docs/art/`・各 ≤400KB）

| ファイル | 中身 |
|---|---|
| `board_G_overview.jpg` | **採用**の全景（G1 ラベンダー・グラスロフト・パレットと構図の正本・2026-09-14 本人裁定） |
| `board_G_overview_alt.jpg` | 同案の別角度（ラウンジが手前・比較用） |
| `board_G_hud.jpg` | デスクトップ HUD モック（1440×900・**情報設計は §7 と現行 `ui/hud/*` が正本**・色とガラス感だけ参照） |
| `board_G_phone.jpg` | スマホ縦モック（390×844） |
| `robot_C_turnaround.jpg` / `robot_C_faces.jpg` | **v2 のまま**（ロボの正本） |
| `board_C_*.jpg` | 方向 C（v2）の記録。参照しない |

### 9.1 テクスチャ
§3.3 の表のとおり（同名上書き・14 枚・1 枚 ≤80KB・合計 ≤600KB）。

## 10. 較正値と機械ゲート（`style_score --profile glass`）

採用ボードの**室内だけ**を切って実測（白い周囲を含む全景は `empty_floor 0.454`／`luma_mean 0.831` と全部ずれる＝v2 と同じ罠）。
**M1' の実レンダ（V6' 後・2026-09-14・M golden）**: empty_floor 0.209 / color_count 51 / neon_cool 0.033 / accent_area 0.018 / screen_glow 0.004 / luma_std 0.191 / luma_mean 0.762＝**全項目クリア**。
ボードより luma_mean が低く accent が小さいのは、ボードが白い周囲を含むのと椅子が机の陰に入るため（想定内・ゲートは広げない）:

| 指標 | ボード室内の実測 | ゲート（glass） | 根拠 |
|---|---|---|---|
| 明るい一様面の割合 | （周囲込みで測れず） | ≤ 0.22 | ラチェット目標を維持。V1'〜V3' の実レンダで見直す |
| 色の種類数 | 46 | 40〜400 | ラベンダー単色系は色数が少ない（legacy の下限 42 と同じ線） |
| 高彩度の寒色の面積（`neon_cool`・色相 160〜300・sat≥55） | 0.078 | **≤ 0.12**（上限だけ） | ソファ・椅子の紫青。ネオン漬け（旧 legacy `glow_area` 0.164 相当）は弾く |
| アクセント（青紫・205〜265°・**sat≥64**）の面積 | 0.047 | 0.01〜0.12 | sat≥16 だと壁ごと拾って 0.70・sat≥32 で 0.34・sat≥48 で 0.15・**sat≥64 で椅子とラグだけ** |
| 画面の発光面積（`screen_glow`・luma≥.85・**sat≥40**・色相 190〜265°） | 0.000（GPT はモニタを光らせていない）／**実レンダ 0.004** | 0.001〜0.03（**確定 2026-09-14**） | 下限はボードでは決められず、V4' の初回実レンダ（emissive 1.5＋bloom .22）で 0.004 を実測して確定 |
| 明度の標準偏差 | 0.166 | 0.15〜0.40 | v2 の実測 0.165 と同水準 |
| 平均輝度 | 0.806 | 0.58〜**0.84** | 白基調。C は「ボード 0.665 → 実レンダ 0.675」だった |

ほかの常設ゲート: drawCalls ≤300・材質 ≤64（59 のまま）・tex 1 枚 ≤80KB / 合計 ≤600KB・golden は SwiftShader で 2 回撮ってビット一致・
`ui/core` は DOM/通信/時刻/乱数に触らない・**`tests/iso_no_fake_glow.test.mjs`**（fake glow 非復活）。

## 11. リアルタイム再現の期待値（正直に）

- ボードは GPT のレイトレ。届くのは **7〜8 割**: ガラスの多重反射と屈折は出ない（opacity と PMREM の映り込みで近似）・GI は無い（PMREM＋頂点 AO）・
  ラグの起毛やフェルトの毛羽は無い。
- **モニタの発光**はデスクトップの `quality:"high"` だけ bloom が滲む。PWA（mobile）と配信（off）は emissive の明るさだけ＝「光っている画面」には見えるが滲まない。
- ボードの黒モニタは GPT が光らせていないので、**V4' の実レンダはボードより「光る」**。これは意図（本人裁定②「黒モニタが光る」）。
- ロボは v2 のまま＝ボードのロボと同一。

## 12. 実装順序（R93 V レーン・正本= `~/.claude/plans/cheerful-drifting-penguin.md` §3.3）

V1' トークン＋HUD ガラス＋ステージ背景＋ダーク → V2' 光・フォグ・環境 → V3' 材質表 v3（テクスチャは Claude が先に生成） →
V4' 発光モニタ＋bloom 再調律 → V5' ガラス壁 → V6' コンパクト規則 → ★M1'（golden 3 枚更新＋`verify.sh` を `--profile glass` へ＋§10 の確定値を写す・同一コミット）。
各委譲は `--update` 禁止・差分%を報告。この文書と矛盾したらこの文書が勝つ。
