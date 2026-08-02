#!/bin/bash
# 受信フックの配布: リポジトリ正本 → ~/.claude/hooks/（グローバルStop hookが参照する場所）
# 使い方:  bash hooks/install.sh              # 配布＋配線チェック（未配線ならスニペット表示）
#          bash hooks/install.sh --wire       # ~/.claude/settings.json へ Stop hook を自動配線
#          bash hooks/install.sh --statusline # statusLine を capture ラッパーへ配線（R61=
#                                             # Claude枠%の実測記録。既存コマンドは
#                                             # office_usage/passthrough.cmd へ退避し表示維持）
#（いずれもバックアップ作成・冪等・既存キー温存）
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.claude/hooks"
for f in office-inbox-wait.sh office-statusline-capture.sh; do
  cp "$HERE/$f" "$HOME/.claude/hooks/$f" && chmod +x "$HOME/.claude/hooks/$f"
  echo "✓ 配布: $HOME/.claude/hooks/$f"
done

# 配線の検査と自動追記。jq 依存をやめ python3 stdlib で完結
#（クリーンMacに jq は無い＝検出が常に「未配線」になる偽警告の根絶）。
python3 - "$HOME/.claude/settings.json" "${1:-}" <<'PYEOF'
import json
import os
import shutil
import sys
import time
from pathlib import Path

p = Path(sys.argv[1])
mode = sys.argv[2]
try:
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
except json.JSONDecodeError:
    print(f"✗ {p} がJSONとして壊れています。手で直してから再実行してください")
    sys.exit(1)
if not isinstance(data, dict):
    print(f"✗ {p} のトップレベルがオブジェクトではありません")
    sys.exit(1)


def save(d):
    """バックアップ→tmp+rename の原子書込（--wire/--statusline 共通）。"""
    if p.exists():
        backup = p.with_name(p.name + ".bak-" + time.strftime("%Y%m%d%H%M%S"))
        shutil.copy2(p, backup)
        print(f"  バックアップ: {backup}")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


HOOK = {"hooks": [{"type": "command",
                   "command": 'bash "$HOME/.claude/hooks/office-inbox-wait.sh"',
                   "timeout": 7300,
                   "statusMessage": "AI Office inbox…",
                   "asyncRewake": True}]}
SL_CMD = 'bash "$HOME/.claude/hooks/office-statusline-capture.sh"'

if mode == "--statusline":
    # R61: statusLine を capture ラッパーへ。既存コマンドはファイル退避（ネスト引用でも安全）
    sl = data.get("statusLine")
    cur = sl.get("command", "") if isinstance(sl, dict) else ""
    if "office-statusline-capture" in cur:
        print("✓ statusLine は配線済み（office-statusline-capture）")
    else:
        usage_dir = p.parent / "office_usage"
        if cur.strip():
            usage_dir.mkdir(parents=True, exist_ok=True)
            pt = usage_dir / "passthrough.cmd"
            pt.write_text(cur, encoding="utf-8")
            try:
                os.chmod(pt, 0o600)
            except OSError:
                pass
            print(f"  既存の statusLine コマンドを退避: {pt}（表示はそのまま維持）")
        data["statusLine"] = {"type": "command", "command": SL_CMD}
        save(data)
        print("✓ statusLine を配線しました（次のセッションから枠%を記録・"
              "ゲージが実測表示になります）")
    sys.exit(0)

stops = data.setdefault("hooks", {}).setdefault("Stop", [])
wired = any("office-inbox-wait" in h.get("command", "")
            for grp in stops if isinstance(grp, dict)
            for h in grp.get("hooks", []) if isinstance(h, dict))
if wired:
    print("✓ ~/.claude/settings.json の Stop hook 配線を確認")
elif mode == "--wire":
    stops.append(HOOK)
    save(data)
    print("✓ Stop hook を配線しました（新しいセッションから有効）")
else:
    print("⚠ 指示配達が未配線です。自動配線: bash hooks/install.sh --wire")
    print("  （手動なら ~/.claude/settings.json の hooks.Stop に次の要素を追加）:")
    print(json.dumps(HOOK, ensure_ascii=False, indent=2))
if "office-statusline-capture" not in str((data.get("statusLine") or {}).get("command", "")):
    print("ℹ Claude枠%の実測ゲージ: bash hooks/install.sh --statusline で配線できます")
PYEOF
