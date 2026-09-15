# D2「リグ付きロボ試作」実装プラン（R96-D2・2026-09-15・設計= Fable 5.1）

> **置き場**: この文書はリポの `docs/tripo-d2-plan.md`（正本）。設計は Fable 5.1・調査 Workflow（3 調査＋統合＋批判的検証）・2026-09-15。実装は本人の Tripo 残高チャージ後に S0 から。

> 正本のプラン= `~/.claude/plans/cheerful-drifting-penguin.md` §4 D2（本人の追加裁定②「Mixamo 等でリギングが要るなら追加で設定する。動きの質がめちゃめちゃ良くなるはず」）。
> 本書は 3 調査（`tripo-rig`／`three-skinned`／`robot-fit`・全部読み取り専用）を統合した実装プラン。
> **凡例**: 文末の `[tripo-rig]` `[three]` `[fit]` は調査の出典、`[repo …]` はリポの実ファイル。**【推測】** と書いた行は S1 で実物を読むまで事実ではない。

## 0. 前提の現在地（事実）
- D0/D1 は done（`f3deb79`）: `tools/tripo_gen.py`（text-to-model のみ・`model_version` で P1・40cr 実測）・`tools/glb_to_geom.py`（POSITION/NORMAL/TEXCOORD_0/indices だけ・skins/animations/JOINTS_0/WEIGHTS_0 の出現ゼロ）・`kit.generated()`・`ui/iso/gen/` 9 点 ≈740KB（ls 合計 735,223B・2026-09-15）・manifest/index.js。**API 残高は 2026-09-15 08:5x に尽きた（403 code 2010）**＝D2 の生成は本人のチャージ待ち。[repo docs/tripo-ledger.md, docs/ROADMAP.md R96-D]
- 現行ロボ= メッシュ無し Object3D 骨格（`makeSkeleton`・hip/neck/shoulder/elbow/hipJoint/knee）＋部品 34 種の InstancedMesh（`RobotBatch`・capacity 40）＋顔アトラス（visor の `faceCell`・`USE_INSTANCING` 前提）＋instanceColor（殻 tint 13 部品・胸リング/antTip の accent）＋`ui/core/anim.js` の純関数ポーズ（13 DOF）。[repo ui/iso/robot.js:9-29,317-386,471-485,544-595]
- ゲート: drawCalls ≤300・materials ≤64（`tools/ui_shot.py:47,50`・materials は `stats()` が scene を traverse した材質の**集合サイズ** [repo scene3d.js:1525-1530]＝depth 材質の内部 variant は数えない）。実測 M= **155 / 61**（R96-D）・XL は R90-V7 時点 178。`ui/iso/gen/*.js` 1 点 ≤160KB・先頭ヘッダ固定・合計 ≤3.5MB（現在 9 点 ≈740KB）、modules_data raw ≤3.5MB / gzip ≤1.2MB（実測 2,074KB / 852KB＝gzip の余地 ≈348KB・2026-09-15 再計測）。[repo verify.sh:136-165, docs/ROADMAP.md]
- three 0.185.1 core に SkinnedMesh／Skeleton／Bone／AnimationClip／AnimationMixer（`setTime`）／KeyframeTrack 各種／PropertyBinding／DataTexture／Uint8BufferAttribute が全部ある（`three.core.min.js` の export 表で確認: qa=SkinnedMesh／Ga=Skeleton／Ha=Bone／od=AnimationMixer／_n=Uint8BufferAttribute・2026-09-15 grep）。**InstancedMesh のスキニングは無い**（`InstancedMesh` に skeleton/bindMatrix フィールドが無く、renderer は `isSkinnedMesh` の物にだけ骨テクスチャ・bindMatrix/bindMatrixInverse を渡す）。**※訂正（レビュー）**: program variant は instanced/skinned で**排他ではない**＝`getParameters` は `skinning:!0===S.isSkinnedMesh` と `instancing:G` を独立に持ち、depth 材質の variant も `isInstancedMesh&&(t+=2), isSkinnedMesh&&(t+=1)` で共存する（両フラグ duck-typing は shader 上は成立し得る＝§7 で「やらない」とした自作案の根拠は「未実測」であって「不可能」ではない）。[three: ui/vendor/three/three.module.min.js 実 grep]
- Tripo v3: `POST /animations/rig-check`（0cr）→ `/animations/rig`（25cr・`model: v1.0-20240301` が biped 専用で `preset:biped:*` 101 種）→ `/animations/retarget`（10cr/本・≤5 本/req・`animate_in_place`・`bake_animation`・`export_with_geometry`）。`POST /generation/multiview-to-model`（P1・front 必須 2 枚以上）。P1 は `generate_parts` 非対応。出力 URL は 5 分で失効・task は発行鍵でしか照会不可。[tripo-rig]

## 1. 結論（やる価値・代償）
**やる価値はある。ただし「全身 1 体 1 SkinnedMesh へ全面置換」ではなく、ハイブリッド（胴体だけスキン・頭/顔/胸リング/アクセサリ/小道具は今の InstancedMesh を骨のソケットへ載せる）で試作し、絵で決める。**

- **価値（事実に基づく）**: プロのモーション（Tripo プリセット= sit/idle/walk/look_around/wait/cheer/clap/wave_goodbye/agree/fold_arms 等）を「同じ t → 同じ姿勢」の決定論のまま再生できる（`mixer.setTime(t)` は t の純関数・または mixer 不使用の自前サンプラ）。core だけで組める＝addons を vendor しない不変条件を守れる。[three][tripo-rig]
- **代償（事実）**: ①1 体 1 draw＋影 1 draw（22 体＋ボス= +46 → M ≈200・XL ≈225・チビ 8 体も乗せると +16）＝「何体いても約 16 draw」の設計は失う。②instanceColor が使えない＝殻 tint は per-robot の頂点色に焼く（材質 clone は 61+22=83 で materials ゲート落ち）。③顔アトラスの shader hook は `USE_INSTANCING` 前提＝visor は InstancedMesh のまま頭骨に載せるしかない。④変換器の skin/anim 対応は実質新設（現行 `collect()` はノード変換を頂点に焼く＝スキン付きでは禁止・`weld()` は joints/weights を捨てる）。⑤サイズ: 胴体（geom+skin）は現行什器並み（chair 3,786 tris＝79.8KB の実測＋JOINTS/WEIGHTS 8B/頂点）で ≤160KB に収まる見込みだが、**clips は骨数×fps×秒数で膨らむ**（【推測・算術】Mixamo 規約の 65 骨（指込み）× 四元数 i16×4＝520B/フレーム × 30fps × 7 本 ≈20 秒 → raw ≈310KB・base64 ≈415KB＝160KB ゲート超過。指 30 骨を落として ≈24 骨なら ≈150KB・15fps なら ≈75KB）→ §2.2 で「指の骨を落とす・15fps・キー間引き」を既定にし、それでも超えるなら clips を用途別に分割。⑥golden 3 枚は採用時に撮り直し（1 回）。[fit][three]
- **やらない理由が無い限りハイブリッドが最小リスク**: 壊れるのは「骨格ノード名への直接書き込み（greet・applyPose）」と「高さ定数」だけに限定できる。faces.js／instanceColor／archetype／props／VENDORS（頭 3 種・アンテナ 3 種・ハサミ）は無改変で温存。ベンダー差は頭/アンテナ/手が担うので**生成する胴体は 1 体で済む**（3 体生成しない）。[fit]
- **保険（0cr・draw 不変）**: 同じ S1〜S3 の成果物で「clip の骨回転を既存 Object3D 骨格へ写す」経路も比較に並べる（皮膚変形は無いが動きの質の大半は得られる可能性【推測】）。本人が「動きは良いが造形は今のまま」と言えばこちらを採用する。

## 2. パイプライン（4 面図 → GLB → JS モジュール → SkinnedMesh）

```
[P0] 4 面図（¥0）        docs/art/robot_C_turnaround.jpg → codex_image.sh で T/A-pose の 4 面（front/left/back/right・白背景・腕を体から離す）
        │                  ※ リグの前提は腕が胴から離れた姿勢。turnaround は腕が下がっている【推測・要目視】
        │                  代替（4 枚の面が揃わないとき）: Tripo `image-to-multiview`（1 枚→4 面シート・出力 task_id をそのまま multiview-to-model の inputs に渡せる）
        │                  ／`text-to-image -p template=t_pose`（T-pose テンプレ）[tripo-cli 0.4.0 skill/commands/generate.md:59-74,101]。画像生成の同時実行枠は 1（common-errors.md:32）・価格は未確認
[P1] multiview→model     POST /generation/multiview-to-model  inputs=[{front:file_token},{left:…},{back:…},{right:…}] model=P1-20260311
        │                  texture=true（色を焼く）・face_limit ≈4,000〜6,000・model_seed 固定・generate_parts は付けない（P1 非対応）
[P2] rig-check（0cr）    POST /animations/rig-check {input:<P1 task_id>} → riggable / rig_type=biped を確認（落ちたら P0 をやり直す）
[P3] rig（25cr）         POST /animations/rig {input:<task_id>, model:"v1.0-20240301", rig_type:"biped", spec:"mixamo", out_format:"glb"}
        │                  ※ v1.0 固定（sit/wait/cheer 等 preset:biped:* はここだけ）・spec=mixamo は骨名を既知にするため（Hips/Spine/Neck/Head/…）【推測: 実名は S1 で読む】
[P4] retarget（10cr/本） POST /animations/retarget {input:<rig task_id>, animations:[…≤5], animate_in_place:true, bake_animation:true, export_with_geometry:false}
        │                  1 回目= **`preset:biped:idle` + `preset:biped:walk`**（20cr）→ animations[]/skins[] の構造を実測 → 2 回目以降で残り
        │                  ※ v1.0 rig の preset は**全部 `preset:biped:` 接頭**（basic: idle/walk/run/turn/sit/standing_relax/look_around/wait…・emotion: clap/agree/fold_arms/wave_goodbye_01…・daily: make_a_call_01/play_mobile_game・performance: cheer）。
        │                    無接頭の `preset:idle/walk/run/…` は v2.5 rig 用＝「preset 集合は上流 rig の model で決まる」[tripo-cli 0.4.0 skill/commands/process.md:72-81]。旧記述「両 rig 共通」は誤り
[P5] 取得                成功したら即ダウンロード（URL 5 分失効）→ tools/tripo_out/robot/{model,rig,anim_*}.glb + task.json（鍵は TRIPO_API_KEY 1 本）
[P6] 変換                tools/glb_to_geom.py --skin … → ui/iso/gen/robot_body.js（geom+skin+bones）／--clips … → ui/iso/gen/robot_clips.js
        │                  `--max-kb 160` を明示（変換器の自前上限は既定 120KB＝verify の 160KB より先に落ちる [repo tools/glb_to_geom.py:235,270]）
        │                  ヘッダ「// generated by tools/glb_to_geom.py」固定・manifest に kind:"rig"・pwa は採用時に true
[P7] 描画                ui/iso/rigbot.js: Bone 階層 → SkinnedMesh(geometry, materials.shell) → bind(skeleton, IBM)・ソケットに RobotBatch の部品
[P8] 駆動                ui/core/clip.js（純関数サンプラ）: (clip, time) → 骨ローカル TRS 配列。時刻は clock.js の t だけ。重みは smoothstep(0,.45,t-changedAt)
```

### 2.1 tripo_gen.py の拡張（Claude・stdlib）
- サブコマンド化: `text`（現行）／`multiview --front f.png --left … --back … --right …`（`POST /files` multipart を自前で組む・`file_token` 化）／`rigcheck <task>`／`rig <task> [--rig-model v1.0-20240301] [--spec mixamo]`／`retarget <rig_task> --anim preset:idle --anim preset:walk … [--in-place]`。`call()`・ポーリング・終了判定・「成功したら即 DL」・台帳追記は現行を流用。[tripo-rig]
- **鍵は 1 本**（CLI の login 鍵と混ぜない＝task_id が相互参照できない）。CLI（`tripo anim …`）は手動検証用に留める。[tripo-rig]
- 台帳 `docs/tripo-ledger.md` に rig/retarget の行（model・spec・preset 名・cr・骨数・GLB サイズ・task）を必ず記帳＝価格の食い違い（pricing 表 vs 実測 40cr）を実測で埋める。

### 2.2 glb_to_geom.py の skin/anim 拡張（Claude・stdlib・単体テスト先行）
| 追加 | 内容 | 出典 |
|---|---|---|
| `NCOMP["MAT4"]=16` | inverseBindMatrices（column-major・three の `Matrix4.fromArray` と同順） | [three] |
| `--skin` | `skins[0].joints`（＝JOINTS_0 の索引空間）・IBM（無ければ単位）・ノード階層（name/children/TRS）を出力。**スキン付き primitive にはノード変換を焼かない**（現行 `collect()` の `apply(m,v)` を skinned では素通し）。`weld()` のキーに joints/weights を含め、運ぶ。JOINTS_0 は u8（骨 ≤255）・WEIGHTS_0 は u8 normalized（4 本の和=255 に正規化） | [three][fit] |
| `--clips` | `animations[].channels/samplers` を読み、**CUBICSPLINE/STEP は線形へ再サンプル（既定 15fps・`--clip-fps 30` で上げる＝再生側は二分探索＋slerp なので疎でよい）**、四元数は i16×4、平行移動は root（Hips）だけ f32→i16（量子化 scale 付き）、**定数トラックは落とす**（Mixamo 風の全骨 scale/translation 対策）、**許容誤差でキー間引き**（隣接キーから線形補間して角度差 <0.5° のフレームは省く）、**指の骨（Hand の子孫）は落として重みを Hand へ畳む**（手は InstancedMesh の hand/claw が担う＝皮膚は要らない・Mixamo 規約なら 30 骨減＝clips が半分以下）。名前は骨索引に置換（`:` `.` の sanitize 問題を構造的に消す） | [three]・サイズ算術は §1 ⑤ |
| 正規化 | `--fit-height 1.484`（無スケール身長＝root scale 1.62 で世界 2.40m。現行の頭頂= hip .54＋neck .36＋HEAD_Y .2244＋HEAD_H/2 .36＝1.4844 [repo robot.js:36-38,317-326]・アンテナ除く）で **①geometry ②IBM の平行移動列 ③骨の rest translation（node TRS） ④clip の Hips 平行移動**の 4 つを同じ係数で一様スケール（③を忘れると IBM と骨がずれて崩れる）。`hipsY`（バインド時の Hips 高さ）を meta に記録＝SIT_DROP/pick/マーカー定数の再調整に使う | [fit][three] |
| 出力 | `robot_body.js`（≤160KB）と `robot_clips.js`（≤160KB）に分割。超えるなら clips を用途別 2 ファイルへ（stand 系／sit 系）。ゲートは変えない（骨数は S1 で実測してから §1 ⑤ の算術を置き換える） | [repo verify.sh:159-165] |
| テスト | `tests/test_glb_to_geom.py` に合成スキン GLB（2 骨・4 頂点・1 clip・CUBICSPLINE 1 本・定数トラック 1 本）→ 期待配列／IBM 往復／ノード変換を焼かないこと／weld 後も weights の和が 255 | [repo tests/test_glb_to_geom.py の型] |

### 2.3 three core での組み立て（`ui/iso/rigbot.js`・新規）
1. `Bone` を階層通りに作る（`position/quaternion/scale` は glTF と同順・glTF の四元数は x,y,z,w＝three と同じ）。根の Bone は robot root（Object3D）に add。[three]
2. geometry に `skinIndex`（Uint8BufferAttribute, 4, 非正規化）・`skinWeight`（Uint8BufferAttribute, 4, normalized）・`color`（AO × 殻 tint を焼いた頂点色・**robot ごとに geometry.clone()**）。[three][fit]
3. `mesh = new SkinnedMesh(geometry, materials.shell)`（`materials.shell` は既に `vertexColors:true` [repo scene3d.js:109-110]・PMREM は scene.environment で自動＝materials +0）→ `root.add(mesh)`（骨の根も root の子＝root の scale 1.62／位置は骨の matrixWorld 経由で効く）→ **`mesh.bind(new Skeleton(bones, ibms), new THREE.Matrix4())`＝bindMatrix は単位行列**（**レビューで訂正**: GLTFLoader r185 も `mesh.bind( skeleton, _identityMatrix )` [GLTFLoader.js:4232・jsdelivr 実読]。three の式は world = Σw·boneWorld·IBM·bindMatrix·p（bindMode "attached" では bindMatrixInverse が毎フレーム mesh.matrixWorld の逆で打ち消される）なので、`mesh.matrixWorld` を渡すと root の scale 1.62 と bind 時の位置が**頂点に二重に掛かる**。**第 2 引数を省くと `calculateInverses()` が GLB の IBM を骨の現在姿勢で上書きする** [three.core.min.js `bind(t,e)`]）→ `mesh.frustumCulled=false`（骨姿勢で bounding が動く）→ `castShadow=true`（depth 材質は variant 選択で自動的に skinning 付き・影を落とす光は key の DirectionalLight 1 灯 [repo scene3d.js:307-313]＝影パス +1 draw/体）。u8 の重みは変換器で和=255 に正規化するが、保険に `mesh.normalizeSkinWeights()`（core にある）を bind 前に 1 回。[three]
4. ソケット: Head 骨の子に Object3D を置き、現行の `nodes.head/visor/antStem/antTips/ears/acc` 相当を作る（オフセットは S1 の実寸から測る）。Spine2（胸）に `chest`・`bowtie`、RightHand に小道具。**RobotBatch.push は「胴体部品をスキップする」フィルタ引数を 1 つ足すだけ**（`put()` の対象を頭系＋胸＋手/ハサミ＋アクセサリ＋小道具に限定）。`tests/iso_acts.mjs` の robots スタブ形は不変。[fit][repo ui/iso/robot.js:544-595]
5. 駆動は **mixer を使わない**: `ui/core/clip.js` の `sampleClip(clip, time)`（二分探索＋lerp/slerp・約 40 行・three 非依存＝`node --test` 可）→ iso 側が Bone の TRS へ写して `root.updateMatrixWorld(true)`。`AnimationMixer` を使う場合でも `setTime(t)` のみ・`fadeIn/crossFadeTo/startAt` は禁止（絶対時刻スケジュールが setTime の「毎回 time=0」と噛み合わない）。**verify に grep ピン**（`ui/**` に `AnimationMixer|crossFadeTo|fadeIn\(|mixer.update` が出たら赤・置き場は ▶3b の fake-glow ピンと同じ [repo verify.sh:118-120]）。`Skeleton.update()`（骨行列→骨テクスチャ）は renderer が render パスごとに 1 回自動で呼ぶ（`WebGLObjects.update`・frame キー・shadow パスは projectObject の後）＝手で呼ばない。[three][fit]

## 3. 既存構造との接点と対処（ハイブリッド前提）
| # | 接点（現状・出典） | 壊れ方 | 対処 | 段 |
|---|---|---|---|---|
| 1 | `pickAgent/projectAgent`= root.y+1.1 の投影距離（半径 46/54px・レイキャスト無し）[fit scene3d.js:1334-1358] | 身長がずれると当たりがずれる | 変換器の `--fit-height 1.484` で身長を現行と一致させる＝定数無改変 | S2 |
| 2 | `labelAnchorFor`= 席アンカーの足元 [fit scene3d.js:1516-1523] | 壊れない | なし | – |
| 3 | 頭上マーカー ❗+2.55／💭+2.62／💬+2.45／✓+2.55・focus +0.9・王冠 y=2.42（root 相対定数）[repo scene3d.js:791,1239,1283,1309・office.js:343] | 身長次第で浮く/埋まる | 同上（身長一致）。ボス（root scale 1.85）は初回は procedural のまま | S2/S6 |
| 4 | instanceColor（殻 tint 13 部品・胸/antTip accent）[repo robot.js:26-29,556-561] | SkinnedMesh に無い | 殻 tint= per-robot の `color` 属性へ焼く（tint が変わった時だけ再焼き・hex でキャッシュ）。**geometry.clone() は使わない**＝position/normal/skinIndex/skinWeight/index の BufferAttribute は全個体で共有し（three は同じ BufferAttribute を複数 geometry から参照でき GPU バッファは 1 組）、`color` だけ個体別の BufferGeometry に載せる。胸/antTip は InstancedMesh のまま骨ソケット | S4 |
| 5 | 顔アトラス（`faceCell`・`#ifdef USE_INSTANCING`）[repo faces.js:53-75] | skinned 頭に共有すると atlas 全面 | visor InstancedMesh を Head 骨ソケットへ。頭も現行 InstancedMesh（＝顔の凹み faceDepth を保つ）。「頭も生成」は比較用の変種としてだけ試す | S4 |
| 6 | アクセサリ 9 種（neck+HEAD_Y・scale HEAD_H/.67・x はベンダー幅）・小道具 4 種（arms[1].hand・iso_acts がピン）[fit] | 骨格ノード名依存 | Head/RightHand ソケットに同じオフセットで再現。ベンダー幅（1/1.37/1.5）は頭系だけに掛ける（胴体は 1 体共有＝幅差は失う【設計判断・比較で本人に見せる】） | S4 |
| 7 | drawCalls（M 155／XL ≈178）[repo ROADMAP] | +2/体（本体 1＋key ライトの影 1） | 22 体→ +44（M ≈200・XL ≈222）。ボス・チビが procedural のままなので胴体部品の InstancedMesh は count>0 で描き続ける＝**削減は無い**（全員 rig 化して count=0 になれば `renderInstances` は 0 件で draw を発行しない [three.module.min.js]）。XL22 で実測してゲート 300 内を確認 | S4 |
| 8 | materials 61 [repo ROADMAP] | 材質 clone なら 83 | `materials.shell` 共有＝+0。**材質 clone 禁止**をレビュー観点に | S4 |
| 9 | 決定論（frozen で loop は 1 フレーム・遷移は初回 -∞）[repo clock.js:101-118, scene3d.js:1006-1013] | mixer の実時間駆動 | 自前サンプラ＋`smoothstep(0,.45,t-poseChangedAt)`（初回/frozen は -∞）・個体差は `seedOf` を clip 時刻オフセットに | S3 |
| 10 | 姿勢の直接書き込み（`greet`= shoulder/neck/hip を叩く・`upper()`・mug・celebrate hipY）[repo scene3d.js:1022-1036, 971-1003] | Bone 名が違う | S3 の対応表で「上半身マスク合成」（clip の骨集合に上半身/下半身の索引集合を持つ）＋greet は Head/RightArm 骨へ同じ式で上書き（サンプラの後・updateMatrixWorld の前） | S3/S4 |
| 11 | 座↔立（`mixPose` 0.45s・SIT_DROP 0.24 は root・RIG.sitHipY 0.44 は hip）[repo scene3d.js:1038-1044] | 座り高さの二重管理 | sit 系 clip の Hips Y を S1 で実測 → 「root は従来どおり SIT_DROP／Hips は clip」か「root で全部」かを決める（S1 の実測後に確定） | S1→S4 |
| 12 | 歩き（`walkPhaseFor(dist)`= 距離位相）[repo anim.js:304] | 時刻駆動だと足が滑る | walk clip は `animate_in_place:true` で取り、`clipTime = ((dist×cadence/2π) mod 1) × duration` | S3 |
| 13 | typing/console/present/relax/tablet 相当のプリセットが無い [tripo-rig] | 机で打つ姿勢が作れない | 下半身= sit clip・上半身= 現行 procedural（typingPose の腕角を骨へ上書き）。`play_mobile_game/make_a_call` は比較用に 1 本だけ試す | S3 |
| 14 | chibi（ノード scale で 2 頭身）・ボス（root 1.85・王冠は静的家具）[fit] | 首の重み境界が伸びる/王冠が刺さる | 初回は両方 procedural 据置＝同一画面に 2 系統が出る（比較画像で本人に見せて判断） | S6 |
| 15 | PWA 同梱（manifest pwa:false→null スタブ・gzip 余地 ≈330〜350KB）[repo gen_pwa_modules.py:65-77] | 未同梱なら Mac とスマホで別の絵（R77 原則と衝突） | 試作中は pwa:false。採用時に `robot_body+clips`（gz 見積 77〜165KB【推測】）を pwa:true にして modules_data を実測 | S6 |
| 16 | 変換器の `weld()`／`collect()` の変換焼き込み [repo glb_to_geom.py:102-157] | バインド姿勢とずれて崩れる | §2.2（skinned は焼かない・weld が joints/weights を運ぶ）を合成 GLB テストで先に固定 | S2 |
| 17 | verify ▶3b（gen 1 点 ≤160KB・ヘッダ固定）[repo verify.sh:159-165] | 1 ファイルだと超える公算 | 2 ファイル分割・ヘッダは同じ文字列 | S2 |
| 18 | `tests/iso_acts.mjs:44` の robots スタブ `{begin,push,end,faces:{render}}`・pwa3d_smoke の stats（robots≥1 のみ・drawCalls は表示だけ）・iso_growth 形状ハッシュ（buildOffice の静的バッチ＝ロボは対象外）[repo tests/] | API の形を変えると複数ピンが落ちる | RobotBatch の公開 API（begin/push/end/faces）は不変。rigbot は `?rig=1` フラグの裏＝フラグ OFF で全ピン不変 | S4 |
| 19 | **push/applyPose/greet が nodes のキーを直接叩く**: `RobotBatch.push()` は `setVendor(nodes, vendor)`（collar/torso/pelvis/acc の scale.x・ears/arms[].shoulder の position.x・antTips を書く [repo robot.js:388-400]）と `nodes.root.updateMatrixWorld(true)` を呼び、scene3d は毎フレーム `applyPose(actor.nodes, blended)`（hip/neck/legs[].hipJoint,knee/arms[].shoulder,elbow へ書く [repo scene3d.js:1015]）→ greet（1022-1036）→ root 配置（1048-1049）の順 | rigbot の nodes にキーが無いと push/applyPose が例外・分岐を足すと iso_acts のスタブ形も変わる | rigbot は **makeSkeleton と同じキー集合**を返す（骨に繋がるのは head 系ソケット・chest・arms[].hand だけ・残り（hip/neck/legs/arms[].shoulder,elbow/torso/pelvis/collar）はダミー Object3D）＝push()/setVendor/applyPose/greet に分岐を足さない。clip 適用のフックは **applyPose の直後・greet の前の 1 箇所**（`if (actor.rig) applyClip(actor, poseKind, blended, t)`・blended は上半身上書きの入力・poseKind は対応表のキー） | S4 |

## 4. 段階とゲート
各段は独立に止められる。**S4 まではフラグ `?rig=1`（scene3d.js:210 の `URLSearchParams`＝stream/quality/tilt/shadow/az/el/pad と同じ場所で読む・`ui/platform` は触らない）の裏で作り、golden 3 枚は 0.000% のまま**。採用（S6）で初めて golden を撮り直す（1 回）。

| 段 | 内容 | レーン | 受入条件（機械） | 受入条件（人） |
|---|---|---|---|---|
| **S0** 準備（0cr） | ①本人が API 残高チャージ ②4 面図（codex_image.sh・T/A-pose・4 枚・白背景・`docs/art/robot_C_turnaround.jpg` 参照。面が揃わなければ Tripo `image-to-multiview` へ切替＝§2 P0）③`tripo_gen.py` サブコマンド化＋multipart アップロード＋モック HTTP の単体テスト | ②Claude（画像）③Claude | `check_stdlib` 緑・`python3 -m unittest tests/test_tripo_gen.py`（モック）緑・ng_check 緑 | 4 面図を本人が一瞥（腕が離れている・2 頭身・顔幅 67% の印象） |
| **S1** 生成＋リグ（≈85〜105cr） | multiview→model（P1・texture）→ rig-check → rig（v1.0・mixamo）→ retarget 1 回目（`preset:biped:idle`＋`preset:biped:walk`・in place）→ GLB を `read_glb` で読み **骨数・骨名・skins/animations の構造・キーフレーム密度・Hips 高さ（idle と sit）・サイズ**を記録 | Claude | riggable=true・rig_type=biped・Draco 無し・台帳に 4 行・`tools/tripo_out/robot/` に task.json | 生成モデルの rendered_image を本人へ（造形の第一印象・ここで「造形は今のまま」なら保険経路へ） |
| **S1 失敗時** | riggable=false か biped 以外 → P0 を作り直して 1 回だけ再試行（+40cr）。2 回目も落ちたら **Mixamo 手動経路**（本人が Web UI で auto-rig・FBX → Blender で GLB・手順書を docs に）へ切替 | Claude/本人 | – | 本人が Mixamo 作業を引き受けるか判断 |
| **S2** 変換器（0cr） | §2.2。合成スキン GLB テスト先行 → S1 の実 GLB で `--dry` 統計 → `robot_body.js`/`robot_clips.js` を書く（manifest kind:"rig"・pwa:false） | Claude | `test_glb_to_geom` +6 件緑・各ファイル ≤160KB・ヘッダ固定・`gen_pwa_modules.py` が null スタブ化・`dev.sh --check` 緑 | – |
| **S3** サンプラ＋対応表（0cr） | `ui/core/clip.js`（sampleClip/blend/clipTimeFor・純関数）＋`ui/core/clip.test.js`（同じ t → 同じ配列のバイト一致・位相の周回・距離駆動 walk・上半身マスク）＋poseKind→clip の対応表（§3 #10-13） | Claude | `node --test` 緑・`js_layer_lint` 緑（core は three を import しない） | – |
| **S3b** 保険（0cr・任意） | clip の骨回転を既存 Object3D 骨格（hip/neck/shoulder/elbow/hipJoint/knee）へ写す（T-pose との rest 差を per-node で補正【推測】）。draw 不変・instanceColor/顔/アクセサリ無改変 | Claude | `?rig=clip` で 2 回撮ってビット一致・golden 不変 | 比較画像の 1 列に入れる |
| **S4** 試作 1〜22 体（0cr） | `ui/iso/rigbot.js`（§2.3）＋RobotBatch のフィルタ引数＋greet/upper の骨上書き＋殻 tint の頂点色焼き。`?rig=1` で全 actor を rigbot に切替（ボス・チビは据置） | Claude（骨組み・決定論）→ Astra（ソケット位置・見た目の詰め・**触らない: faces.js/anim.js/verify/golden**） | `ui_shot --query rig=1 --world xl22` で drawCalls ≤300・materials ≤64・**2 回撮ってビット一致**・`?rig=1&t=` を 0.4s 刻みで撮って**同一 poseKind 内の clip が連続**（frozen は遷移を -∞＝完了状態で描くので座↔立の補間はここでは見えない [repo scene3d.js:668,1009]）・grep ピン（AnimationMixer 等）緑・フラグ OFF で golden 3 枚 0.000%・iso_acts/pwa3d/screens スモーク緑 | Claude が実 GPU（`--gpu`）で目視: 顔が頭に載っている・胸リングの色が状態で変わる・小道具が手にある・影が落ちる。**座↔立の遷移と入場歩行は非 frozen の実 GPU headed 録画（R81-2 の型）で目視**（§6-2b） |
| **S5** 比較→本人判断（0cr） | §6 の比較セットを作って本人へ | Claude | – | 本人が「A/B/C/D のどれ」と言う。言わなければ D2 は S4 のフラグ付きで凍結（コードは残す・既定 OFF） |
| **S6** 採用時の展開 | 残り clip の retarget（≤5 本/req）・ボス/チビの扱い決定・PWA 同梱（pwa:true）・フラグ撤去（既定 ON）・golden 3 枚撮り直し（1 回・同一 commit）・`iso_growth` 形状ハッシュ・art-direction §6 を v4・ROADMAP/台帳/HISTORY・`.claude/rules/ui-3d.md` に「リグ付きロボの掟」（mixer 禁止・材質 clone 禁止・bind は単位行列・frustumCulled=false。同ファイルの「SIT_DROP=0.145」は現行 0.24 [repo scene3d.js:1038] と食い違う既存の死文＝同時に直す） | Astra（見た目）＋Claude（検収・配備） | full verify 緑（本体で）・modules_data raw ≤3.5MB / gz ≤1.2MB・style_score glass・golden 2 回 0.000%・別モデルレビュー pass | 本人の実機目視（Mac 実 GPU・iPhone） |

## 5. クレジット見積り
**Tripo（API 残高・$1=100cr）**【推測・P シリーズの multiview 価格は未確認＝S1 で実測して台帳に。rig 25／retarget 10 の単価も**ローカル資料（tripo-cli 0.4.0 同梱 docs）には無く未検証**（developers.tripo3d.ai の pricing は JS 描画で機械取得できず・2026-09-15）＝S1 の `credits_consumed` で確定。同時実行枠（アカウント既定）: P 系生成 5／アニメ（rig-check/rig/retarget）10／画像生成 1 [common-errors.md:26-33]】
| 項目 | cr | 根拠 |
|---|---|---|
| multiview→model P1（texture） | ≈40 | text-to-model P1 の実測 40cr と同額と仮定（pricing 表は 20/30 と食い違う） [tripo-rig] |
| rig-check | 0 | 公式 Free [tripo-rig]・CLI docs も「not riggable → stop before spending rig credits」[process.md:65] |
| rig v1.0 | 25 | pricing/models ページ [tripo-rig]（ローカル未検証） |
| retarget 1 回目（biped:idle／biped:walk） | 20 | 10cr×2 [tripo-rig]（「≤5 本・1 本ごとに課金」は CLI docs で確認 [process.md:67]・単価は未検証） |
| retarget 2 回目（biped:sit／look_around／wait／cheer／wave_goodbye_01＝5 本） | 50 | 10cr×5・≤5 本/req |
| 予備（biped:agree／clap／standing_relax／play_mobile_game／make_a_call_01・P0 やり直し 1 回） | ≈40〜80 | preset 名は process.md:79-88 の v1.0 表と一致させる（`wave_goodbye`→`wave_goodbye_01`・`make_a_call`→`make_a_call_01` のように番号付き） |
| **試作合計** | **≈175〜215cr（$1.75〜2.15）** | 4 面図は codex_image.sh＝¥0 |
| 採用時の追加（ボス用 scale 違いは不要・チビ用は別体型なら +115） | 0〜115 | チビをスキン化する場合だけ |

**作業（推測）**: S0 0.5 日／S1 0.5 日（API 待ち込み）／S2 1 日／S3 0.5 日／S3b 0.5 日／S4 1.5 日／S5 0.5 日＝**試作 ≈5 日**。S6 は +2 日。レーン: S0〜S3・S4 骨組み= Claude Opus 5（決定論・stdlib・ゲートに絡む）、S4 の見た目詰め・S6 の展開= Astra 委譲（¥0・`codex_lane.sh --net` 背景・worktree に `verify.local` を写す）。Fable 5.1 は本書で終わり（再入は S1 の実測が設計前提を覆したときだけ）。

## 6. 本人が絵で判断する比較の作り方
**同じ world・同じ t・同じカメラで 4 列を並べる**（判断材料は「造形」と「動き」の 2 軸なので静止画と動画を分ける）。

1. **静止画モンタージュ**（`tools/ui_shot.py --gpu --world xl22 --query "<flag>"` で 4 枚 → PIL で横並び・`tests/artifacts/d2_compare.png`・見出し文字は入れない＝絵だけ）
   - A= 現行 procedural（flag 無し）
   - B= 保険（`rig=clip`・造形は現行・動きは clip）
   - C= ハイブリッド（`rig=1`・生成胴体＋現行の頭/顔/アンテナ）
   - D= 頭も生成（`rig=full`・visor は生成頭の上に重ねる・S4 で 0.5 日だけ試す変種【推測: 板が浮く/めり込む可能性】）
   - 各列は t=3.2（座り・打鍵）と t=9.4（❗挙手・会議・ラウンジが出る fixture）の 2 行＝計 8 枚。**frozen（`?t=`）では歩行が出ない**（初回描画は全員が席・path は目的地 1 点・enteringAt=null [repo scene3d.js:883-890]）ので静止画に「歩き」の行は作れない＝歩きは §6-2(b) の録画で見せる。同じ 3 体（Claude/Codex/OpenClaw）を含む席へ `focusOn` で寄った拡大も 1 行足す（顔・胸リング・ハサミが残っているかを見せる）。
2. **動画**は 2 種（**レビューで訂正**: demo_gif の型は frozen のままなので遷移も歩行も写らない）:
   - (a) `tools/demo_gif.py` の型（frozen ロードのまま `clock.setTime` を 0.45s 刻みで 24 フレーム→ GIF・決定論）を A/B/C で 3 本＝**clip の連続性と個体差（seedOf オフセット）だけ**を見せる。frozen は `poseChangedAt=-∞`・`_track` は target 直返し・path は 1 点 [repo scene3d.js:668,883-890,1009] なので座↔立の補間も入場歩行も出ない。
   - (b) **入場歩行と座り→立ちの遷移**は非 frozen の実 GPU headed 録画（R81-2 の型＝probe inject で 2 PJ 出勤・`--use-angle=metal`）で A/B/C 各 1 本。ビット再現は保証しない（比較用であって golden ではない）。
   GIF/動画は `docs/` に置かず `tests/artifacts/.tmp/`（配布物に入れない）。
3. **数字を 1 行添える**: drawCalls（A/B/C/D）・materials・gen サイズ・gzip・Tripo 消費 cr。嘘の % は出さない（実測だけ）。
4. **送り方**: SendUserFile で PNG 1 枚＋GIF 3 本。質問は 1 つ「A/B/C/D のどれ（または止める）」。理由の説明は 3 行以内。
5. **判断後の扱い**: A= コードはフラグ付きで凍結（削除しない・ROADMAP に「凍結」）／B= S3b を既定 ON・golden 撮り直し／C or D= S6 へ。

## 7. やらないこと
- three の addons（GLTFLoader/DRACOLoader/SkeletonUtils）・Draco を vendor しない（不変条件・D0 と同じ自前変換器で読む）。
- **InstancedMesh のスキニング自作**（両フラグ duck-typing＋onBeforeCompile＋customDepthMaterial）はやらない【推測段階・golden/SwiftShader の実測が無い】。1 体 1 draw を受け入れ、ゲート 300 の内側で測る。
- `AnimationMixer` の実時間駆動・`fadeIn/crossFadeTo/startAt`・`mixer.update(dt)`。材質 clone による個体色。Tripo テクスチャ（PBR 3 枚）の同梱（頂点色に焼く D1 方式のまま）。
- rig `v2.5-20260210`（sit/wait/cheer 等が無い）・CLI 既定のまま流用（取り違えの罠）・鍵 2 本の混用。
- 3 ベンダー分の胴体生成（ハイブリッドで頭/アンテナ/手が差を担う）。チビ・ボスのスキン化（初回）。typing 専用 clip 探し（procedural 上書きで足りる）。
- 段階ごとの golden 撮り直し（採用時に 1 回だけ）。フラグ OFF の絵を 1 ピクセルでも変える変更（試作中は golden 0.000% が受入条件）。
- Mixamo を既定経路にしない（API 無し・FBX/DAE のみ＝Blender の手作業が要る・stdlib の tools/ で FBX は読めない）。Tripo で rig が通らないときの退避としてだけ手順書を書く。
- `generate_parts`（P1 非対応・リグ路線では不要）・`export_orientation`（下流に継承されない）。
- 本文・鍵・パスを台帳や生成物に書かない（Tripo task_id と cr だけ）。公開 snapshot に `tools/tripo_out/` は入れない（gitignore 済み）。

## 付録 A: 推測の一覧（S1 の実測で事実にする項目）
1. P1-20260311 の出力を rig に渡せるか（旧 docs の前提は Turbo-v1.0/v2.0 以上）→ rig-check 0cr で判定。
2. 骨数・骨名（spec=mixamo で Hips/Spine/…/Head/LeftHand になるか）・IBM の有無・アニメが CUBICSPLINE か LINEAR か・キーフレーム密度。
3. `bake_animation`/`export_with_geometry:false` の実出力（アニメだけの GLB に skins が含まれるか・複数 clip が 1 GLB に入るか）。
4. ~~v1.0 rig で `preset:idle/walk`（無接頭）が通るか~~ → **解決（ローカル docs）**: tripo-cli 0.4.0 `skill/commands/process.md:72-81` は「preset 集合は上流 rig の model で決まり、v1.0 は全部 `preset:biped:` 接頭（90+）」＝無接頭は v2.5 rig 用。設計は `preset:biped:*` に統一済み（API の実応答で最終確認）。
5. `preset:biped:sit` の Hips 高さ（床座りか椅子座りか）→ §3 #11 の設計。
6. multiview P1 の価格（並列数は解決: アカウント既定で P 系生成 5／アニメ 10／画像生成 1 [common-errors.md:26-33]）。
7. 2 頭身（頭が大きい体型）が biped として riggable と判定されるか・肩胸のウェイトが過剰に滑らかになるか（第三者記事の評価）。
8. リグ付きモジュールの実サイズ＝**骨数**（Tripo の mixamo spec が指 30 骨を含むか・rig GLB の `skins[0].joints` 長で分かる）と clip 尺。§1 ⑤ の算術（65 骨・30fps・7 本 ≈415KB base64／指を落とし 24 骨・15fps ≈75KB）を S1 の実測で置き換える。旧試算「raw 169〜272KB」は骨数を仮定していなかったので撤回。
9. 生成胴体と現行の頭（InstancedMesh）の継ぎ目（首）が 60px で不自然に見えないか＝比較画像でしか分からない。

## 付録 B: 出典
- 調査 `tripo-rig`: developers.tripo3d.ai（animations-rig-check／rig／retarget／models/rig／models/animation／pricing／rate-limits／files／generation-multiview-to-model/p／image-to-multiview／migration-v2-to-v3）・docs.tripo3d.ai（retarget／rig-v1-0／rig-v2-5／get-your-task-result／multiview-to-model-p1）・docs.comfy.org TripoRetargetNode・tripo-cli 0.4.0 同梱 docs（`~/.npm-global/lib/node_modules/tripo-cli`）・`~/.tripo/history.jsonl`・生成時の task.json（`tools/tripo_out/`）。
- 調査 `three-skinned`: `ui/vendor/three/three.core.min.js`（export 名・SkinnedMesh/Skeleton/Bone/AnimationMixer.setTime/PropertyBinding）・`three.module.min.js`（program params skinning/instancing・depth_vert）・r185 原典 InstancedMesh.js/SkinnedMesh.js/AnimationMixer.js。
- 調査 `robot-fit`: `ui/iso/robot.js`・`ui/core/anim.js`・`ui/iso/scene3d.js`・`ui/iso/faces.js`・`ui/iso/office.js`・`ui/pwa/boot3d.js`・`ui/platform/clock.js`・`tools/gen_pwa_modules.py`・`tools/ui_shot.py`・`tools/js_layer_lint.py`・`tests/iso_acts.mjs`・`docs/art-direction.md` §6。
- 本書で直接確認したリポ: `tools/glb_to_geom.py`（collect/weld/quantize/register・INDEX_HEAD）・`tools/tripo_gen.py`（call/ポーリング/台帳）・`verify.sh:136-165`・`ui/iso/gen/manifest.json`＋`index.js`・`ui/iso/kit.js:110-135`・`docs/tripo-ledger.md`・`docs/ROADMAP.md` R96-D 行・`.claude/rules/ui-3d.md`・`.claude/skills/codex-delegation/SKILL.md`・`tools/demo_gif.py`・`docs/art/robot_C_turnaround.jpg`（存在確認）・`~/.claude/plans/cheerful-drifting-penguin.md` §4。
- **批判的検証（2026-09-15・Fable 5.1・読み取り専用）で実読したもの**: `ui/vendor/three/three.core.min.js`／`three.module.min.js`（export 表・`bind(t,e)`・`calculateInverses`・`Skeleton` ctor/`computeBoneTexture`/`update`・`getParameters` の `skinning`/`instancing`・depth variant `a(e)`・`renderInstances` の 0 件早期 return・`normalizeSkinWeights`・`setTime`）・GLTFLoader r185（jsdelivr 実読: 4232 行 `mesh.bind( skeleton, _identityMatrix )`・3853 行 `normalizeSkinWeights`）・tripo-cli 0.4.0 `skill/commands/process.md`（rig/retarget/preset 表 56-88 行）・`skill/commands/generate.md`（multiview 59-74・P1 制約 19 行）・`skill/common-errors.md`（同時実行枠 26-33・URL 失効 51・2010）・`dist/knowledge/models.js:52-71`（rig wire version・server 既定 v1.0／CLI 既定 v2.5）・`ui/iso/robot.js` 全体（PARTS 34・TINT_PARTS 13・makeSkeleton/setVendor/applyPose/push/makeChibiSkeleton）・`ui/iso/scene3d.js`（210 URLSearchParams・307-313 影は key 1 灯・337 CAPACITY 40・883-892 frozen 時の path/enteringAt/scale 1.62・960-1049 ポーズ合成〜root 配置・1340-1357 pick・1525-1545 stats）・`ui/iso/faces.js:53-75`・`ui/iso/post.js:45,167`（info.autoReset）・`ui/platform/clock.js`・`ui/core/anim.js`（RIG・mixPose・seedOf・smoothstep）・`tests/iso_acts.mjs:44`・`tests/iso_growth.test.mjs`（形状ハッシュは buildOffice のみ）・`tests/pwa3d_smoke.py:258-270`・`tools/ui_shot.py`（引数 202-214・上限 47/50）・`tools/gen_pwa_modules.py:62-77`・`relay/src/modules_data.js`（raw 2,074KB／gzip 852KB 実測）。

## 付録 C: 批判的検証で修正した点（2026-09-15・Fable 5.1）
| 重さ | 箇所 | 何が違ったか | 直した内容 |
|---|---|---|---|
| **重大** | §2.3-3 | `mesh.bind(skeleton, mesh.matrixWorld)` は root の scale 1.62／位置を頂点に二重に掛ける（three の式は Σw·boneWorld·IBM·bindMatrix·p） | bindMatrix は**単位行列**（GLTFLoader r185:4232 と同じ）。「第 2 引数省略で IBM が捨てられる」は正しいので残した |
| **重大** | §6・S4 受入 | frozen（`?t=`）では歩行も座↔立の補間も出ない（path 1 点・poseChangedAt=-∞・`_track` 直返し）のに「t=9.4（歩き）」「demo_gif で遷移が写る」としていた | 静止画は❗/会議/ラウンジの行に、動画は (a) frozen＝clip 連続性 (b) 非 frozen 実 GPU 録画＝遷移・歩行 の 2 種に分離。S4 の受入条件も同様に修正 |
| **重大** | §2 P4・S1・§5・付録 A#4 | v1.0 rig の preset は全部 `preset:biped:` 接頭（CLI docs process.md:72-81）。無接頭 `preset:idle/walk` を「両 rig 共通」としていた | `preset:biped:idle/walk/sit/…` に統一・番号付き名（wave_goodbye_01・make_a_call_01）に修正 |
| 中 | §0 | 「program variant は instanced/skinned 排他」は事実誤認（getParameters は独立・depth variant も共存） | 正しい根拠（InstancedMesh に skeleton 無し・renderer は isSkinnedMesh にだけ骨を渡す）に書き換え。§7 の判断は据置 |
| 中 | §1 ⑤・§2.2・付録 A#8 | clips のサイズ試算に骨数の仮定が無く、Mixamo 65 骨×30fps×7 本なら ≈415KB で 160KB ゲート超過 | 指の骨を落とす・15fps 既定・キー間引きを変換器の仕様に追加・算術を明記（推測と明記） |
| 中 | §2.2 正規化 | `--fit-height` のスケール対象に骨の rest translation（node TRS）が無かった | ①geometry ②IBM ③骨 TRS ④clip Hips の 4 点を同係数でと明記 |
| 中 | §3 #19（新設） | push()→setVendor／applyPose／greet が nodes のキーを直接叩く接点が抜けていた | rigbot は makeSkeleton と同じキー集合（ダミー含む）を返す・clip 適用フックは applyPose 直後の 1 箇所 |
| 中 | §5・付録 A#6 | rig 25／retarget 10 の単価はローカル資料に無い・並列数は CLI docs に既定値がある | 単価は「未検証・S1 で確定」と明記・並列数（P 系 5／アニメ 10／画像 1）を事実に |
| 軽 | §2 P0・S0 | 4 枚別生成は面が揃わない危険 | Tripo `image-to-multiview`／`template=t_pose` を代替として追記 |
| 軽 | §2 P6 | 変換器の自前上限（既定 120KB）が verify の 160KB より先に落ちる | `--max-kb 160` を明示 |
| 軽 | §3 #4 | `geometry.clone()` は全属性を複製 | 共有 BufferAttribute＋`color` だけ個体別に |
| 軽 | §3 #7 | ボス・チビが procedural の間は InstancedMesh の削減が無いことが未記載 | 明記（count=0 なら draw は出ないことも） |
| 軽 | §4 冒頭 | `?rig=1` を「ui/platform で読む」としていたが実物は scene3d.js:210 が直接読む | 修正 |
| 軽 | 行番号 | verify.sh の gen ゲート 151-156→159-165・ui_shot 45-48→47,50・gen 合計 744→≈740KB・王冠 2.42/2.55→2.42 | 修正 |
| 軽 | S6 | `.claude/rules/ui-3d.md` の SIT_DROP=0.145 は現行 0.24 と食い違う死文 | S6 で同時に直すと追記 |
