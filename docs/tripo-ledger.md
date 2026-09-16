# Tripo 生成台帳（R96-D）

`tools/tripo_gen.py` が 1 行ずつ追記する。クレジットは API 残高（Web プランの枠とは別）。
GLB そのものはリポに置かない（`tools/tripo_out/` は gitignore）＝リポに入るのは `ui/iso/gen/<name>.js` だけ。

| 日付 | name | model | 設定 | seed | credits | GLB | task | prompt（先頭） |
|---|---|---|---|---|---|---|---|---|
| 2026-09-15 | chair（サンプル・未採用） | P1-20260311 | faces 3000 texture | seed 11 | 40 | 892KB | 29d1946b | A modern ergonomic office task chair（tripo CLI で生成・品質確認用） |
| 2026-09-15 | chair（v3.1 比較・未採用） | v3.1-20260211 | faces 3000 lowpoly no-texture | seed 11 | 20 | 226KB | 2b3b3a51 | 同上（4,434 tris・色無し＝クレイ。焼ける色が無いので採用しない） |
| 2026-09-15 | desk（試作1・未採用） | P1-20260311 | faces 4000 texture | seed 11 | 40 | 540KB | 3589e5a3 | A modern minimalist Scandinavian office desk, 140cm（島 3.0×2.3m に合わない） |
| 2026-09-15 | desk_pod（試作2・未採用） | P1-20260311 | faces 5000 texture | seed 11 | 40 | 720KB | f1b1ae10 | two-person shared workstation island（十字の仕切り・4 席型になった） |
| 2026-09-15 | desk_long（試作2・未採用） | P1-20260311 | faces 4000 texture | seed 11 | 40 | 573KB | 6b4c1297 | long executive desk（背の高い衝立が付いた） |
| 2026-09-15 | **desk**（採用・島の机） | P1-20260311 | faces 5000 texture | seed 23 | 40 | 1.1MB | 7b5daf5b | two-person face-to-face workstation, single low lilac felt screen along the seam（negative: cross divider, four seats…） |
| 2026-09-15 | **desk_long**（採用・社長机） | P1-20260311 | faces 4000 texture | seed 23 | 40 | 0.9MB | 3d0246a2 | long low executive desk, nothing rises above the top（negative: privacy screen, hutch…） |

| 2026-09-15 | **chair**（採用） | P1-20260311 | faces 4000 texture | seed 23 | 40 | 0.9MB | 58432eac | ergonomic task chair, pale lilac seat, white mesh back, white frame |
| 2026-09-15 | **sofa**（採用） | P1-20260311 | faces 4500 texture | seed 23 | 40 | 1.0MB | f7da189d | three-seat lounge sofa, pale lavender linen, pale oak legs |
| 2026-09-15 | meeting_table（試作・未採用） | P1-20260311 | faces 3000 texture | seed 23 | 40 | 0.6MB | 3928fa32 | meeting table for six people（「for six people」で椅子 4 脚が付いた） |
| 2026-09-15 | **stool**（採用） | P1-20260311 | faces 2500 texture | seed 23 | 40 | 0.5MB | 72e57358 | cafe bar stool, pale blue linen seat, white frame |
| 2026-09-15 | **meeting_table**（採用） | P1-20260311 | faces 3000 texture | seed 23 | 40 | 0.6MB | 87fd9502 | single empty meeting table by itself, no chairs（negative: chairs, seats…） |
| 2026-09-15 | **floor_lamp**（採用） | P1-20260311 | faces 2500 texture | seed 23 | 40 | 0.5MB | daf5e965 | arc floor lamp, white stem, marble base, white drum shade |
| 2026-09-15 | plant（未採用） | P1-20260311 | faces 4000 texture | seed 23 | 40 | 0.9MB | a36e2b56 | monstera in white planter（葉が暗灰色に焼けた＝procedural の葉カードの方が良い） |
| 2026-09-15 | **shelf**（採用） | P1-20260311 | faces 3500 texture | seed 23 | 40 | 0.8MB | 6b4e1698 | open shelving unit with tidy objects |
| 2026-09-15 | phone_booth（未採用） | P1-20260311 | faces 4000 texture | seed 23 | 40 | 0.8MB | d3150529 | phone booth（ガラス扉が不透明の灰色に焼ける＝procedural のガラスの方が良い） |
| 2026-09-15 | **water_station**（採用） | P1-20260311 | faces 2500 texture | seed 23 | 40 | 0.5MB | 4768c331 | water dispenser + cup holder |
| 2026-09-15 | counter（試作・未採用） | P1-20260311 | faces 4000 texture | seed 23 | 40 | 0.9MB | cf024435 | reception counter with fluted front（縦溝がカーテン状の破面になった） |
| 2026-09-15 | cafe_counter（試作・未採用） | P1-20260311 | faces 4000 texture | seed 23 | 40 | 0.8MB | 4eb9164b | cafe counter with oak slats（白箱だけ・エスプレッソマシンが切れた） |
| 2026-09-15 | **pouf**（採用） | P1-20260311 | faces 2000 texture | seed 23 | 40 | 0.4MB | f5b30c1c | round pouf ottoman, pale sky-blue linen |
| 2026-09-15 | **counter**（採用・受付） | P1-20260311 | faces 3500 texture | seed 31 | 40 | 0.8MB | 5c624a00 | simple reception desk, one solid block, lavender-grey top slab（溝は諦めた） |
| 2026-09-15 | **cafe_counter**（採用） | P1-20260311 | faces 3500 texture | seed 31 | 40 | 0.8MB | 65d05527 | cafe bar counter, oak slats front, espresso machine + cups fully inside the outline |

合計 860cr（$8.6・試作 19 体＋採用 12 種）。API 残高は 2026-09-15 08:5x に一度尽き、同日夕方のチャージ後に残り 3 種（プフ・受付・カフェ）を追加した。教訓④: 「fluted/ribbed（縦溝）」は P1 で破面になる＝面の細かい繰り返し模様は頼まない。教訓: ①比率は prompt に cm で書いても緩くしか効かない＝`glb_to_geom.py` が**一番大きい上向きの面（天板）の高さ**を記録し、kit の fit:"box" が spec.h をそこへ合わせる ②「仕切り」「背板」は negative prompt で明示しないと勝手に足される ③texture:false の出力には焼ける色が無い（クレイ）＝色分けが要る物は texture あり（+20cr）で生成する。

## D2（リグ）の API 実測（2026-09-15）

| 日付 | 段 | 内容 | credits | task | メモ |
|---|---|---|---|---|---|
| 2026-09-15 | S0 | image-to-image `t_pose=true`（正面図 → T ポーズ） | 5 | bf2bf595 | 出力は 1024px PNG・キャラの再現度は高い |
| 2026-09-15 | S1 | image-to-multiview（T ポーズ → 4 面） | 10 | 4ad342c9 | 出力ファイル名に front/left/back/right が付く（URL は task.json に載らない＝CLI が落とす） |
| 2026-09-15 | S1 | multiview-to-model P1（texture・face_limit 5000・seed 23） | **50** | ba2c2506 | text-to-model の 40 より高い（4 面入力の分） |
| 2026-09-15 | S1 | rig-check | 0 | fd8164dc | riggable |
| 2026-09-15 | S1 | rig（CLI・spec mixamo・model 既定= v2.5-20260210） | 25 | fb5f6241 | **23 骨（Root＋mixamorig 22・指なし）**・3,298 頂点／4,792 三角形・IBM あり |
| 2026-09-15 | S1 | retarget（CLI・preset:biped:*／preset:*・in_place） | 0（返金） | 587cb602・53f7d5ee・10568539・eab1cf57 | 4 回とも error_code **1004**。原因= **`model` を省くとサーバ既定が `v2.5-20250123`（無効値）になる**（rig で `model` 無しを POST すると 400 で「allowed: v1.0-20240301, v2.5-20260210」と返る）。retarget も同じ既定を掴んで実行時に落ちる。ただし本当の原因は **spec=mixamo**（下 3 行）。rig は `model` 省略で 400（既定が無効値）なので明示は必要 |
| 2026-09-15 | S1 | rig（API・model v1.0・spec **mixamo**） | 25 | 724f8db4 | 23 骨・retarget は全変種（preset:biped:idle／preset:idle／animations 配列／fbx／out_format 無し／model 明示）で **1004**＝**mixamo spec のリグは preset retarget できない** |
| 2026-09-15 | S1 | rig（API・model v1.0・spec **tripo**） | 25 | 445b6fed | **retarget が通る**（tripo spec のリグだけ）。骨名は Tripo 命名 |
| 2026-09-15 | S1 | retarget `preset:biped:idle`（API・model 明示なし・in_place なし） | 10 | 6e06a709 | success・出力 `model_url` 1 本（GLB にスキン＋アニメ） |
| 2026-09-15 | S1 | retarget 5 本まとめ（walk/sit/look_around/wait/cheer・in_place・tripo spec rig） | 50 | a7d6a245 | 1 GLB（2.5MB）に 5 animations・各 42 ノード×TRS 126 channel・LINEAR・24fps。**in_place は Hip の絶対 translation が 0 付近**（rest は 0.137）＝絶対値で置くと腰の高さぶん沈む→ 変換器は先頭フレームからの差分にして rest に足す |
| 2026-09-15 | S2 | 変換 `tools/glb_rig.py`（body 132KB・clips 6 本 101KB・15fps・idle/look_around/cheer は 8 秒で切る） | 0 | – | 41 骨のうち回転が動くのは 15（Twist 系は定数で落ちる） |
| 2026-09-15 | S4 | `?rig=1` で 9 体を SkinnedMesh 化（drawCalls 153→155・materials 61 のまま） | 0 | – | 骨格 root は scene に居ない数学用なので、rig の group は scene に直接置いて root の行列を毎フレーム写す |

## 元 GLB の置き場（恒久退避・2026-09-16）

生成の**元データ**はリポに置かない（`tools/tripo_out/` は gitignore・Tripo の出力 URL は 5 分で失効＝再取得できない）。
`~/Documents/ai-office-assets/` に退避した: `rig/`（6 本・キャラの生・Tripo spec リグ・retarget したアニメ）と `furniture/`（22 点）。
同フォルダの README.md に各ファイルの役目を書いてある。clip を焼き直す作業はこの GLB が前提。
