# Codex rollout 形式の観測台帳

> この JSONL は Codex CLI の非公式な内部形式であり、公開 API ではない。フィールドや階層は予告なく変わりうる。
> リソースモニターで Codex カードだけが `unavailable` になった場合は、まず rollout 形式の変更を疑う。

## 2026-07-14 に観測した形

ローカルの `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` 1本を読み、値・会話本文・識別子・実パスを
記録せず、次のフィールド名と入れ子だけを確認した。

- 1行は `timestamp`、`type`、`payload` を持つ JSON オブジェクト。
- セッション情報行はトップレベル `type: "session_meta"` で、`payload.cli_version` などを持つ。
- 使用量行はトップレベル `type: "event_msg"`、`payload.type: "token_count"` の形を取る。
- レート制限は `payload.rate_limits` にあり、観測キーは次のとおり。
  - `limit_id`、`limit_name`、`plan_type`
  - `primary`: `used_percent`、`window_minutes`、`resets_at`
  - `secondary`: `null` または primary と同じ3フィールドを持つオブジェクト。使用中でも null のことがあるため、表示時は走査した直近の非null値を採用する。
  - `credits`、`individual_limit`、`rate_limit_reached_type`
- セッション累積トークンは `payload.info.total_token_usage` にあり、
  `input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_output_tokens`、`total_tokens` を持つ。
- 同じ token-count 行に `payload.rate_limits` と `payload.info.total_token_usage` が共存しうる。

## 合成 fixture

`tests/fixtures/codex_rollout*.jsonl` は 2026-07-14 に、Codex CLI `0.144.1` 相当の上記観測形を基に
手書きした完全な合成データである。実セッションの値・文字列・ID・パスは含めていない。
