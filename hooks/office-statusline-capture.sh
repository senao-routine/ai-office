#!/bin/bash
# R61: Claude Code statusLine の公式 payload から rate_limits（Pro/Maxサブスク枠の
# 実測%・5h/週間窓）を横取りして ~/.claude/office_usage/ へ落とす。
# status_board がこれを読み、Claudeゲージを「推定ペース」→「実測枠%」へ格上げする。
# OAuthトークンには一切触れない（規約クリーン。出典= code.claude.com/docs/en/statusline）。
#
# 掟（office-inbox-wait.sh と同じ）:
#   - どんな失敗でも exit 0・stderr沈黙＝Claude Code の表示を絶対に壊さない
#   - 処理は python3 1回起動のみ（statusLine はターン毎に走るため軽く保つ）
#   - rate_limits が無い payload（サブスク外/初回API応答前）では既存記録を消さない
#
# パススルー: ~/.claude/office_usage/passthrough.cmd があれば、その中身へ同じ stdin を
# 渡して stdout をそのまま返す（既存の statusLine 表示を維持）。無ければ自前の1行を出す。
# OFFICE_HOME はテスト注入口（既定 ${HOME}）。
set +e
H="${OFFICE_HOME:-$HOME}"
INPUT="$(cat 2>/dev/null)"
# 注意: `python3 - <<HEREDOC` はプログラム自体をstdinから読む＝payloadをパイプで
# 渡すとheredocに上書きされて届かない（実測1敗）。payloadは環境変数で渡す。
LINE="$(SL_JSON="$INPUT" OFFICE_HOME="$H" python3 - 2>/dev/null <<'PYEOF'
import json
import os
import time

home = os.environ.get("OFFICE_HOME") or os.path.expanduser("~")
usage_dir = os.path.join(home, ".claude", "office_usage")
bits = []
try:
    payload = json.loads(os.environ.get("SL_JSON", ""))
except Exception:
    payload = None
if isinstance(payload, dict):
    model = ""
    if isinstance(payload.get("model"), dict):
        model = payload["model"].get("display_name") or ""
    rl = payload.get("rate_limits")

    def pct(win):
        w = rl.get(win) if isinstance(rl, dict) else None
        v = w.get("used_percentage") if isinstance(w, dict) else None
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    five, week = pct("five_hour"), pct("seven_day")
    if five is not None or week is not None:
        account = None
        try:
            oa = json.load(open(os.path.join(home, ".claude.json"),
                                encoding="utf-8")).get("oauthAccount") or {}
            if oa.get("accountUuid"):
                account = {"id": oa["accountUuid"],
                           "email": oa.get("emailAddress") or ""}
        except Exception:
            account = None
        rec = {"capturedAt": int(time.time()), "rateLimits": rl,
               "model": model, "account": account}
        names = ["current.json"]
        if account:
            names.append("acct-%s.json" % account["id"][:12])
        os.makedirs(usage_dir, exist_ok=True)
        try:
            os.chmod(usage_dir, 0o700)
        except OSError:
            pass
        for name in names:
            p = os.path.join(usage_dir, name)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            os.replace(tmp, p)
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
    if model:
        bits.append(model)
    if five is not None:
        bits.append("5h %d%%" % round(five))
    if week is not None:
        bits.append("wk %d%%" % round(week))
print(" | ".join(bits) if bits else "AI Office")
PYEOF
)"
PASS="$H/.claude/office_usage/passthrough.cmd"
if [ -f "$PASS" ]; then
  printf '%s' "$INPUT" | bash -c "$(cat "$PASS" 2>/dev/null)" 2>/dev/null && exit 0
fi
printf '%s\n' "${LINE:-AI Office}"
exit 0
