#!/bin/bash
# <bitbar.title>AI Office</bitbar.title>
# <bitbar.desc>メニューバーに AI社員の稼働数(🟢)と要対応(❗=承認/質問待ち)を表示</bitbar.desc>
# <bitbar.dependencies>bash,curl,python3</bitbar.dependencies>
# SwiftBar プラグイン（.5s = 5秒更新。サーバー側 /api/office は2秒キャッシュなので過負荷なし）
# 依存: bash + curl + python3(標準ライブラリのみ)。127.0.0.1固定（不変条件）。
PY="$(command -v python3 || echo /usr/bin/python3)"
JSON="$(curl -s --max-time 2 -H "X-Office-Local: 1" http://127.0.0.1:4780/api/office 2>/dev/null)"
if [ -z "$JSON" ]; then
  echo "🏢 — | color=gray"
  echo "---"
  echo "AI Office は起動していません"
  exit 0
fi
printf '%s' "$JSON" | "$PY" -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("🏢 ? | color=gray"); sys.exit(0)
emps = d.get("employees", [])
c = d.get("counts", {})
alerts = [e for e in emps if e.get("question") or e.get("approvalMin", 0) > 0]
bar = "🏢 🟢%d" % c.get("working", 0)
if alerts:
    bar += " ❗%d" % len(alerts)
print(bar)
print("---")
def s(x):
    # トランスクリプト由来テキストの "|" は SwiftBar のパラメータ区切り＝注入経路になるので潰す
    return str(x or "").replace("|", "¦")
for e in alerts:
    why = s(e.get("question")) or ("承認待ち %d分" % e.get("approvalMin", 0))
    print(("❗ %s — %s" % (s(e.get("disp", "?")), why))[:60] + " | href=http://localhost:4780")
for e in emps[:12]:
    icon = {"working": "🟢", "waiting": "🟡", "resting": "💤"}.get(e.get("state"), "・")
    line = ("%s %s — %s %s" % (icon, s(e.get("disp", "?")), s(e.get("verb", "")), s(e.get("target", "")))).strip()
    print(line[:60] + " | href=http://localhost:4780")
print("---")
print("オフィスを開く | href=http://localhost:4780")
'
