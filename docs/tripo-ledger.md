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
| 2026-09-15 | counter / cafe_counter / pouf（未生成） | – | – | – | 0 | – | – | **API クレジット切れ（403 code 2010）**＝残高チャージ後に同じ型で生成する |

合計 660cr（$6.6・試作 14 体＋採用 9 種）。API 残高は 2026-09-15 08:5x に尽きた（受付カウンター・カフェカウンター・プフは未生成）。教訓: ①比率は prompt に cm で書いても緩くしか効かない＝`glb_to_geom.py` が**一番大きい上向きの面（天板）の高さ**を記録し、kit の fit:"box" が spec.h をそこへ合わせる ②「仕切り」「背板」は negative prompt で明示しないと勝手に足される ③texture:false の出力には焼ける色が無い（クレイ）＝色分けが要る物は texture あり（+20cr）で生成する。
