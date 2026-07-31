#!/bin/bash
# 受信フックの配布: リポジトリ正本 → ~/.claude/hooks/（グローバルStop hookが参照する場所）
# 使い方:  bash hooks/install.sh          # 配布＋配線チェック（未配線ならスニペット表示）
#          bash hooks/install.sh --wire   # ~/.claude/settings.json へ Stop hook を自動配線
#                                         #（バックアップ作成・冪等・既存hooksは温存）
set -u
SRC="$(cd "$(dirname "$0")" && pwd)/office-inbox-wait.sh"
DST="$HOME/.claude/hooks/office-inbox-wait.sh"
mkdir -p "$HOME/.claude/hooks"
cp "$SRC" "$DST" && chmod +x "$DST"
echo "✓ 配布: $DST"

# 配線の検査と（--wire時のみ）自動追記。jq 依存をやめ python3 stdlib で完結
#（クリーンMacに jq は無い＝検出が常に「未配線」になる偽警告の根絶）。
python3 - "$HOME/.claude/settings.json" "${1:-}" <<'PYEOF'
import json
import shutil
import sys
import time
from pathlib import Path

p = Path(sys.argv[1])
wire = sys.argv[2] == "--wire"
try:
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
except json.JSONDecodeError:
    print(f"✗ {p} がJSONとして壊れています。手で直してから再実行してください")
    sys.exit(1)
if not isinstance(data, dict):
    print(f"✗ {p} のトップレベルがオブジェクトではありません")
    sys.exit(1)
stops = data.setdefault("hooks", {}).setdefault("Stop", [])
wired = any("office-inbox-wait" in h.get("command", "")
            for grp in stops if isinstance(grp, dict)
            for h in grp.get("hooks", []) if isinstance(h, dict))
HOOK = {"hooks": [{"type": "command",
                   "command": 'bash "$HOME/.claude/hooks/office-inbox-wait.sh"',
                   "timeout": 7300,
                   "statusMessage": "AI Office inbox…",
                   "asyncRewake": True}]}
if wired:
    print("✓ ~/.claude/settings.json の Stop hook 配線を確認")
elif wire:
    if p.exists():
        backup = p.with_name(p.name + ".bak-" + time.strftime("%Y%m%d%H%M%S"))
        shutil.copy2(p, backup)
        print(f"  バックアップ: {backup}")
    stops.append(HOOK)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    print("✓ Stop hook を配線しました（新しいセッションから有効）")
else:
    print("⚠ 指示配達が未配線です。自動配線: bash hooks/install.sh --wire")
    print("  （手動なら ~/.claude/settings.json の hooks.Stop に次の要素を追加）:")
    print(json.dumps(HOOK, ensure_ascii=False, indent=2))
PYEOF
