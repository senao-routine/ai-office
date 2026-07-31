# OpenClaw status 契約スキーマ v1（mini→mac・R42.3）

Mac mini 側の `tools/openclaw_push.py`（R42.4・spike後に実装）が生成し、
中継 `POST /status?site=macmini` 経由でメインMacの relay_agent が取得して
`~/.claude/openclaw_status.json` へ保存する **こちら定義の契約**。
OpenClaw本体の非公開フォーマットはmini側のpushスクリプトだけが読む＝
形式変動の影響面をmini側1ファイルに封じ込める（フィクスチャ第一則）。

```json
{
  "v": 1,
  "site": "macmini",
  "generatedAt": 1753500000.0,
  "agents": [
    {
      "id": "main",              // [a-z0-9-]{1,32}。session は "oc-<id>" になる
      "name": "OpenClaw",        // 表示名の素材（disp番号はmac側で採番）
      "state": "working",        // working | waiting | resting
      "verb": "replying on WhatsApp",  // 現在の動作（本文は入れない）
      "channel": "whatsapp",     // 任意: whatsapp|telegram|discord|... （UIバッジ用）
      "age": 12,                 // 最終活動からの秒
      "minions": 0               // 併走サブエージェント数（任意）
    }
  ]
}
```

## mac側の取り込み規則（server/openclaw_source.py）

- 供給源の優先順: env `OFFICE_OPENCLAW_FIXTURE`（テスト注入口）→ `~/.claude/openclaw_status.json` → なし
- `generatedAt` が **600秒より古い場合は切断扱い**（employees空・connected:false・reason=stale）
  ＝mini停止時は自然に「未接続」へフォールバック
- employee変換: `session="oc-<id>"`・`external="openclaw"`・`dept=name`・
  `disp="<name>"`（同名2体目から「 N号」/enは「 #N」）・`sprite=/assets/agent_bot.png`
- **プライバシー掟をソースで満たす**: `cwd/branch/lastSaid/lastOrder/target` は常に空・
  `feed` は verb 行のみ（本文なし）＝ relay redaction に頼らず最初から載せない
- 不正行（id不正・state不正・dict以外）は黙って捨てる（1行の毒で全滅させない）

## 検証

- 単体: `tests/test_openclaw_source.py`（golden・不正行・stale・マージ・claude版非表示）
- fixture: `tests/fixtures/openclaw_status.json`
- verify ▶5: `OFFICE_OPENCLAW_FIXTURE` 注入で `/api/office` に oc- 社員、
  `/api/external/openclaw` が connected:true を返す
