# ライセンス運用ランブック（R42.2・開発者用）

買い切りライセンスの発行・失効・鍵管理の手順。**秘密鍵と購入者情報はリポジトリに置かない。**

## 仕組み（要約）

- 検証= `server/license.py`（RSA-2048 PKCS#1 v1.5 / SHA-256・純stdlib・公開鍵ハードコード）
- ライセンス実体= `~/.claude/office_license.json`（600・有効期限なし=買い切り恒久）
- 強制ポイントは全部Mac側: `relay_agent` 起動ゲート（relayPwa）／`/api/pair/new` 403／`/api/status_board*` 403。Workerは無強制
- features判定は `office_server.edition_features(edition, license_state)` の1関数（商売ロジックの単一集約点）
- hybridライセンスは①claude利用もカバー（アップグレード動線）。②openclaw版は無料＝ライセンス不要

## 鍵管理

- 秘密鍵= `~/.claude/office_license_signing.json`（600・`{"n":hex,"e":int,"d":hex}`・2026-07-26生成）
- **バックアップ必須**（消えると既発行ライセンスの再発行・追加発行が不能になる）。パスワードマネージャ等リポ外へ
- **バックアップ手順（2026-07-31確立・秘匿ポリシー: 秘密の運搬はAirDrop/USBのみ・クラウド/リポ禁止）**:
  ①鍵ファイルをフォルダごと AirDrop で別Mac（mini/Air）へ送り `~/.claude/` に置く（`chmod 600`）
  ②JSONの中身をパスワードマネージャにも保存 ③運搬用のデスクトップコピーは完了後に削除
- **復旧手順**: バックアップの JSON を `~/.claude/office_license_signing.json` に置く（600）→
  `python3 -m unittest tests.test_license` で本番鍵サニティ green を確認 → `issue` がそのまま使える
- ローテ手順: `python3 tools/license_sign.py gen-key --force` → 出力された `PUBKEY_N` を `server/license.py` に差し替え → `tests/test_license.py` の本番鍵サニティは自動追随 → **既発行の全ライセンスを再発行して買主へ再送**（旧鍵の署名は無効になる）

## 発行（手動運用・初期）

1. 購入通知（Gumroad/Lemon Squeezy）でメールアドレスを確認
2. `python3 tools/license_sign.py issue --edition claude --email buyer@example.com --out /tmp/office_license.json`
   （③混合の購入者は `--edition hybrid`。`--key-id` に注文IDを入れると突合しやすい）
3. `/tmp/office_license.json` をメール添付で送付。買主はオフィスUI「🔑 連携設定 → 🧾 ライセンス」へ中身を貼り付けて登録
4. 週5件を超えたら自動化（R42.2b: LS License API連携の極小アクティベーションWorker）へ移行

- 自Mac用: `python3 tools/license_sign.py install --edition hybrid --email <自分>`（直接 `~/.claude/office_license.json` へ）

## 失効・返金

- サーバー側に失効リストは無い（オフライン設計）。返金時は「以後のアップデートで無効化しない保証をしない」旨を販売ページに明記する運用
- holder= 購入者メールsha256先頭12hex。問い合わせ時は `issue` を同じメールで再実行するとholder一致で本人確認できる

## 検証

- 単体: `python3 -m unittest tests.test_license`（KAT・改竄・無料tier・600保存）
- E2E: `bash verify.sh` ▶5（無ライセンス403→license/set復帰→不正拒否）
- 既存の署名封筒KAT（`test_crypto.KAT_SIG`）とは別系統＝ライセンス変更でP3署名に触れないこと
