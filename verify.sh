#!/bin/bash
# AI Office 検証ハーネス一括実行（コミット前に必ず green にする）
# 使い方: bash "AI Office/verify.sh"   （SKIP_UI=1 でUIスモーク省略）
set -u
cd "$(dirname "$0")"
# 呼び出しシェルの注入envを除染（P4デバッグ中のシェルから実行しても検証対象がすり替わらない）
unset OFFICE_DATA OFFICE_CONFIG OFFICE_HOME OFFICE_PICK_DIR OFFICE_FAKE_LAUNCH OFFICE_FAKE_GEN 2>/dev/null || true
NG=0
ng(){ echo "  ✗ $1"; NG=$((NG+1)); }
ok(){ echo "  ✓ $1"; }
# 中断・失敗時も一時サーバー/一時dirを必ず片付ける（孤児:4797が次回の嘘greenを生むのを防ぐ）
SPID="" DPID="" VHOME="" HDIR="" DTMP="" DHOME="" MHOME=""
trap '[ -n "$SPID" ] && kill $SPID 2>/dev/null; [ -n "$DPID" ] && kill $DPID 2>/dev/null;
      rm -rf "$VHOME" "$HDIR" "$DTMP" "$DHOME" "$MHOME" 2>/dev/null' EXIT

echo "▶ 1/8 構文チェック (py_compile)"
python3 -m py_compile server/*.py tools/*.py tests/*.py 2>&1 && ok "全ファイル構文OK" || ng "構文エラー"

echo "▶ 2/8 stdlib番人 (server/ は標準ライブラリのみ)"
python3 tools/check_stdlib.py server/*.py || ng "server/ に外部依存が混入"
# tools/office_send.py も stdlib のみ（office_server は同梱の許可モジュール）
python3 tools/check_stdlib.py --allow office_server tools/office_send.py || ng "office_send.py に外部依存が混入"

# R80: シェルスクリプトの「$VAR の直後に全角文字」は bash が全角まで変数名と解釈して
# unbound variable で落ちる（setup.sh 作成時に実際に踏んだ）。日本語UIのスクリプトでは
# 再発しやすいので機械で止める。
python3 - <<'SHVAR' || ng "シェル変数展開の罠（\$VAR の直後に全角文字）"
import re, sys
from pathlib import Path
pat = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7f])")
bad = []
for f in Path(".").glob("**/*.sh"):
    if "node_modules" in str(f) or "_archive" in str(f):
        continue
    for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        for m in pat.finditer(line):
            bad.append(f"{f}:{i}: ${m.group(1)} の直後が全角（${{{m.group(1)}}} と書く）")
if bad:
    print("\n".join("  " + b for b in bad[:10]))
    sys.exit(1)
print("  ✓ シェル変数展開の罠なし（$VAR+全角）")
SHVAR

echo "▶ 2b 新UI(R50) の層と core ユニット"
# 層lint: ui/core が DOM/通信/時刻/乱数に触っていないこと＝core を node だけでテストできる前提を守る番人
python3 tools/js_layer_lint.py || ng "R50 層lint 違反（core の逆流）"
if command -v node >/dev/null 2>&1; then
  UIJS_NG=0
  for F in $(find ui -name '*.js' -not -path 'ui/vendor/*' 2>/dev/null); do
    node --check "$F" >/dev/null 2>&1 || { echo "    構文エラー: $F"; UIJS_NG=1; }
  done
  [ "$UIJS_NG" = "0" ] && ok "新UI JS 構文" || ng "新UI JS 構文エラー"
  if ls ui/core/*.test.js >/dev/null 2>&1; then
    UT=$(node --test ui/core/*.test.js 2>&1)
    if echo "$UT" | grep -qE '^ℹ fail 0$'; then
      ok "core ユニット ($(echo "$UT" | grep -oE '^ℹ pass [0-9]+' | grep -oE '[0-9]+')件)"
    else
      ng "core ユニット失敗"; echo "$UT" | tail -20
    fi
  fi
else
  echo "  - node なし → 新UIのJS検査を省略"
fi

echo "▶ 3/8 office_config.json 検証"
# R80 Phase4: アセット検査（sprite実在/PNGマジック/寸法）は退役＝assets/ ごと撤去した。
# 3D UIは ui/iso/tex/*.webp（▶3bのmodules番人が守る）だけを読む。ここはconfigの構造のみ。
python3 - <<'EOF' || ng "config検証失敗"
import json, sys
from pathlib import Path
_cfg_p = Path("office_config.json")
cfg = json.loads(_cfg_p.read_text()) if _cfg_p.exists() else {}
projects = cfg.get("projects", {})
if not isinstance(projects, dict):
    print("  ✗ projects が dict ではありません"); sys.exit(1)
bad = [k for k, v in projects.items() if not isinstance(v, dict)]
if bad:
    print(f"  ✗ 不正なプロジェクト項目: {bad}"); sys.exit(1)
print(f"  ✓ config OK ({len(projects)}プロジェクト)")
EOF

echo "▶ 3b PWA同梱物 (modules_data.js が ui/ と一致・git追跡・worker構文)"
# R79: スプライト同梱(sprites_data.js)は全廃＝アバターはモノグラム・シーンは3D ESM。
# 生成物 relay/src/modules_data.js は worker.js が static import する＝未生成/未追跡だと
# クリーンclone/CIで wrangler deploy が丸ごと失敗し既存relay全ルートが落ちる。
# R77: PWAの3Dシーン用ESM同梱物も同じ掟（未生成/未追跡ならクリーンcloneのdeployが死ぬ）
if python3 tools/gen_pwa_modules.py --check >/dev/null 2>&1; then
  ok "modules_data.js が ui/ と一致 (PWA 3Dシーン)"
else
  ng "modules_data.js ドリフト/未生成 → python3 tools/gen_pwa_modules.py で再生成しコミット"
fi
git ls-files --error-unmatch relay/src/modules_data.js >/dev/null 2>&1 \
  && ok "modules_data.js git追跡済み" \
  || ng "modules_data.js が未追跡 → git add relay/src/modules_data.js"
# R79: 撤去の恒久ピン＝スプライト同梱が「復活していない」ことを機械で守る
if [ -f relay/src/sprites_data.js ] || [ -f tools/gen_pwa_sprites.py ]; then
  ng "スプライト同梱が復活している（R79で全廃＝アバターはモノグラム）"
else
  ok "スプライト同梱なし (R79全廃・アバター=モノグラム)"
fi
if command -v node >/dev/null 2>&1; then
  node --check relay/src/worker.js >/dev/null 2>&1 && ok "worker.js 構文OK" || ng "worker.js 構文エラー"
else
  echo "  - node無し → worker.js構文チェック省略"
fi


# ▶3d scene_sync --check は R52 旧UI削除で退役。office_scene.json＋/api/layout＋
# test_scene_geometry.py も R80 Phase4 で撤去済み（3Dの間取り正本= ui/core/nav.js）

echo "▶ 4/8 ユニットテスト (状態推定ゴールデン他)"
python3 -m unittest discover -s tests -q 2>&1 | tail -2 | sed 's/^/  /'
python3 -m unittest discover -s tests -q >/dev/null 2>&1 || ng "unittest失敗"

# テストポート: 既定4797（台帳）。dev.sh の fixtureサーバー等が使用中なら空きポートへ退避する。
# 先客のまま固定ポートへ撃つと、テストenv注入なしの実サーバーに /api/project/pick が刺さり
# 本物の「フォルダを選択」ダイアログが連発する（実際に起きた）。退避すれば dev と verify は共存できる。
TPORT=4797
SQUATTER=$(lsof -ti tcp:$TPORT 2>/dev/null | head -1)
if [ -n "$SQUATTER" ]; then
  TPORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
  echo "  ℹ :4797 は使用中 (PID $SQUATTER: $(ps -o command= -p "$SQUATTER" | head -c 90)) → :$TPORT で検査"
fi
echo "▶ 5/8 起動スモーク (フィクスチャHOME・:$TPORT)"
VHOME=$(python3 tests/make_home.py)
LAUNCH_MARKER="$VHOME/claude_launch.marker"
mkdir -p "$VHOME/data"
# 2026-08-10 ライセンス廃止: 機能ゲートが無いので鍵無しで全機能が使える（誰でも即開始）。
OFFICE_HOME="$VHOME" OFFICE_CONFIG="$VHOME/office_config.json" OFFICE_PICK_DIR="$VHOME/pickme" \
  OFFICE_DATA="$VHOME/data" OFFICE_FAKE_LAUNCH="$LAUNCH_MARKER" \
  python3 server/office_server.py --port $TPORT >/dev/null 2>&1 &
SPID=$!
sleep 1.2
API=$(curl -s -H "X-Office-Local: 1" http://127.0.0.1:$TPORT/api/office)
python3 - "$API" <<'EOF' || ng "APIスキーマ検証失敗"
import json, sys
d = json.loads(sys.argv[1])
emps = d["employees"]
assert len(emps) == 2, f"社員数 {len(emps)} != 2"
for e in emps:
    for k in ("session", "state", "verb", "disp", "pending"):
        assert k in e, f"キー欠落: {k}"
print(f"  ✓ /api/office スキーマOK (社員{len(emps)})")
EOF
H_LOCAL='-H X-Office-Local:1'
# R3: ローカルパス一覧はCSRF必須。6時間窓外の古いプロジェクトも返す。
CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$TPORT/api/projects)
[ "$CODE" = "403" ] && ok "R3 projects CSRFガード (ヘッダ無403)" || ng "R3 projects CSRF失敗 (code=$CODE)"
PROJECT_LIST=$(curl -s $H_LOCAL http://127.0.0.1:$TPORT/api/projects)
echo "$PROJECT_LIST" | grep -q -- "-Users-test-old-project" \
  && ok "R3 全プロジェクト一覧 (8日前を含む)" || ng "R3 古いプロジェクト欠落: $(echo "$PROJECT_LIST" | head -c 160)"
# 既存 launch_claude のテスト注入口で、課金なしにTerminal起動経路まで確認。
rm -f "$LAUNCH_MARKER"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$TPORT/api/projects/launch $H_LOCAL \
  -H "Content-Type: application/json" -d "{\"path\":\"$VHOME/pickme\"}")
[ "$CODE" = "200" ] && [ -f "$LAUNCH_MARKER" ] \
  && ok "R3 project launch (200+マーカー生成)" || ng "R3 project launch失敗 (code=$CODE)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$TPORT/api/instruct $H_LOCAL \
  -H "Content-Type: application/json" -d '{"session":"sess-verify0001","text":"verify指示"}')
[ "$CODE" = "200" ] && [ -f "$VHOME/.claude/office_inbox/sess-verify0001.json" ] \
  && ok "投函API正常系 (200+ファイル生成)" || ng "投函API正常系失敗 (code=$CODE)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$TPORT/api/instruct $H_LOCAL \
  -H "Content-Type: application/json" -d '{"session":"../evil","text":"x"}')
[ "$CODE" = "400" ] && ok "投函API不正session拒否 (400)" || ng "不正session拒否失敗 (code=$CODE)"
# CSRFガード: X-Office-Local ヘッダ無しのPOSTは403（別オリジンWebページ由来を弾く）
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$TPORT/api/project/new \
  -H "Content-Type: application/json" -d "{\"path\":\"$VHOME/curltest\",\"name\":\"x\"}")
[ "$CODE" = "403" ] && ok "CSRFガード: ヘッダ無しPOST拒否 (403)" || ng "CSRFガード失敗 (code=$CODE)"
# DNSリバインディングガード: ループバック以外のHostは403
CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: evil.example" http://127.0.0.1:$TPORT/api/office)
[ "$CODE" = "403" ] && ok "Hostガード: 非ループバックHost拒否 (403)" || ng "Hostガード失敗 (code=$CODE)"
# M4: GET /api/office もCSRFゲート（ヘッダ無し＝別オリジンのdrive-byを拒否・res_summaryの外部HTTP副作用を防ぐ）
CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$TPORT/api/office)
[ "$CODE" = "403" ] && ok "M4 GET /api/office CSRFガード (ヘッダ無403)" || ng "M4 /api/office CSRF失敗 (code=$CODE)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-Office-Local: 1" http://127.0.0.1:$TPORT/api/office)
[ "$CODE" = "200" ] && ok "M4 GET /api/office ヘッダ有り200" || ng "M4 /api/office 正常系失敗 (code=$CODE)"
# R86-B: 会話ビューア＝本文を返す唯一の経路（office_json非搭載・CSRF配下・未知=200空・不正=400）
DLG=$(curl -s -H "X-Office-Local: 1" "http://127.0.0.1:$TPORT/api/session/dialog?session=sess-verify0001")
echo "$DLG" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d["ok"] and len(d["messages"])>=1 and d["messages"][0]["role"] in ("user","ai"), d' \
  && ok "R86-B dialog 正常200+messages" || ng "R86-B dialog 取得失敗: $(echo "$DLG" | head -c 80)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$TPORT/api/session/dialog?session=sess-verify0001")
[ "$CODE" = "403" ] && ok "R86-B dialog CSRFガード (ヘッダ無403)" || ng "R86-B dialog CSRF失敗 (code=$CODE)"
DLG=$(curl -s -H "X-Office-Local: 1" "http://127.0.0.1:$TPORT/api/session/dialog?session=sess-nothere99")
echo "$DLG" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d["ok"] and d["messages"]==[], d' \
  && ok "R86-B dialog 未知session=200+空 (console error回避)" || ng "R86-B dialog 未知sessionの応答が不正"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-Office-Local: 1" "http://127.0.0.1:$TPORT/api/session/dialog?session=..%2Fevil")
[ "$CODE" = "400" ] && ok "R86-B dialog 形式不正=400 (トラバーサル拒否)" || ng "R86-B dialog 不正session拒否失敗 (code=$CODE)"
# R86-C: depth は**列挙**（bytes/limitの直接指定を受けない）＝任意サイズ読みが構造的に不可能
D2=$(curl -s -H "X-Office-Local: 1" "http://127.0.0.1:$TPORT/api/session/dialog?session=sess-verify0001&depth=2")
echo "$D2" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d["depth"]==2 and isinstance(d["hasMore"],bool) and d["maxDepth"]>=2, d' \
  && ok "R86-C dialog depth=2 (depth/maxDepth/hasMore を返す)" || ng "R86-C dialog depth応答が不正: $(echo "$D2" | head -c 80)"
for BAD in 9 abc 1.5 -1; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-Office-Local: 1" "http://127.0.0.1:$TPORT/api/session/dialog?session=sess-verify0001&depth=$BAD")
  [ "$CODE" = "400" ] || ng "R86-C dialog depth=$BAD を拒否しない (code=$CODE)"
done
ok "R86-C dialog 列挙外depth(9/abc/1.5/-1)=400"
D0=$(curl -s -H "X-Office-Local: 1" "http://127.0.0.1:$TPORT/api/session/dialog?session=sess-verify0001&depth=0")
python3 -c '
import json,sys
d0=json.loads(sys.argv[1]); d2=json.loads(sys.argv[2])
a,b=d0["messages"],d2["messages"]
assert len(b)>=len(a) and (not a or a==b[-len(a):]), "深いdepthが浅いdepthのsuffixでない"
' "$D0" "$D2" && ok "R86-C dialog 深いdepthは浅いdepthのsuffix (置換で会話が飛ばない)" \
  || ng "R86-C dialog suffix性が破れている"
curl -s http://127.0.0.1:$TPORT/ | grep -q "AI Office" && ok "GET / 200+HTMLマーカー" || ng "トップページ異常"
# R52: 旧UIは削除済み＝?ui=legacy でも新UI(boot.html)が返ること（旧ページの残骸を配らない）
curl -s http://127.0.0.1:$TPORT/ | grep -q 'id="bootstate"' \
  && ok "R50 / は新UI(boot.html)" || ng "R50 / が新UIを返していない"
curl -s "http://127.0.0.1:$TPORT/?ui=legacy" | grep -q 'id="bootstate"' \
  && ok "R52 ?ui=legacy も新UI（旧UI削除済み）" || ng "R52 ?ui=legacy が旧UI残骸を返した"
# /ui/ 静的ルート: 配れるもの / 配ってはいけないもの
UI_JS=$(curl -s -o /dev/null -w "%{http_code} %{content_type}" http://127.0.0.1:$TPORT/ui/core/world.js)
[ "$UI_JS" = "200 text/javascript; charset=utf-8" ] \
  && ok "R50 /ui 静的配信 (200+MIME)" || ng "R50 /ui 静的配信 異常 ($UI_JS)"
UI_ESC=0
for BADPATH in "/ui/%2e%2e/server/office_server.py" "/ui/%2e%2e/%2e%2e/etc/passwd" "/ui/office_page.html.bak" "/ui/"; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$TPORT$BADPATH")
  [ "$CODE" = "404" ] || { UI_ESC=1; echo "    ! $BADPATH → $CODE"; }
done
[ "$UI_ESC" = "0" ] && ok "R50 /ui パストラバーサル・未知拡張子を拒否" || ng "R50 /ui の閉じ込めが破れている"
# P1: ➕新プロジェクトAPI（picker/config/launchは全てモック注入）
R=$(curl -s -X POST http://127.0.0.1:$TPORT/api/project/pick $H_LOCAL)
echo "$R" | grep -q '"ok": true' && echo "$R" | grep -q "pickme" \
  && ok "P1 pick API (OFFICE_PICK_DIRモック)" || ng "P1 pick API失敗: $R"
# curl登録は curltest（UIフロー用 pickme とは別フォルダ＝UIが確実に新規入社になる）
R=$(curl -s -X POST http://127.0.0.1:$TPORT/api/project/new $H_LOCAL -H "Content-Type: application/json" \
  -d "{\"path\":\"$VHOME/curltest\",\"name\":\"検証部\",\"role\":\"P1テスト\"}")
echo "$R" | grep -q '"ok": true' && grep -q "検証部" "$VHOME/office_config.json" \
  && ok "P1 新プロジェクト登録 (configにエントリ増)" || ng "P1 登録失敗: $R"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$TPORT/api/project/new $H_LOCAL \
  -H "Content-Type: application/json" -d '{"path":"/no/such/dir9","name":"x"}')
[ "$CODE" = "400" ] && ok "P1 不正パス拒否 (400)" || ng "P1 不正パス拒否失敗 (code=$CODE)"
# 型不正パス（数値）も400を返す（接続断にしない）
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$TPORT/api/project/new $H_LOCAL \
  -H "Content-Type: application/json" -d '{"path":123,"name":"x"}')
[ "$CODE" = "400" ] && ok "P1 型不正パス拒否 (400)" || ng "P1 型不正パス拒否失敗 (code=$CODE)"
# P3: スマホ連携ペアリングAPI（new→list secret無→revoke／台帳600／CSRF）
R=$(curl -s -X POST http://127.0.0.1:$TPORT/api/pair/new $H_LOCAL -H "Content-Type: application/json" -d '{"label":"iPhoneテスト"}')
DID=$(echo "$R" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("device_id",""))' 2>/dev/null)
echo "$R" | grep -q '"secret"' && echo "$R" | grep -q "/app#" && [ -n "$DID" ] \
  && ok "P3 pair/new (device+secret+pairUrl返却)" || ng "P3 pair/new失敗: $R"
# P6: 中継設定済み(make_homeがoffice_relay.jsonをseed)なので pair/new は QR SVG も返す
echo "$R" | python3 -c 'import sys,json; s=json.load(sys.stdin).get("qrSvg",""); sys.exit(0 if s.startswith("<svg") and "<path" in s else 1)' 2>/dev/null \
  && ok "P6 pair/new QR SVG生成 (vendored segno)" || ng "P6 QR SVG欠落: $(echo "$R" | head -c 120)"
PERM=$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[-3:])' "$VHOME/.claude/office_devices.json" 2>/dev/null)
[ "$PERM" = "600" ] && ok "P3 台帳 chmod 600" || ng "P3 台帳権限が600でない (perm=$PERM)"
L=$(curl -s $H_LOCAL http://127.0.0.1:$TPORT/api/pair/list)
echo "$L" | grep -q "$DID" && ! echo "$L" | grep -q '"secret"' \
  && ok "P3 pair/list (secret伏字)" || ng "P3 pair/list secret露出orDevice欠落: $L"
CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$TPORT/api/pair/list)
[ "$CODE" = "403" ] && ok "P3 pair/list CSRFガード (ヘッダ無403)" || ng "P3 pair/list CSRF失敗 (code=$CODE)"
curl -s -X POST http://127.0.0.1:$TPORT/api/pair/revoke $H_LOCAL -H "Content-Type: application/json" -d "{\"device_id\":\"$DID\"}" >/dev/null
curl -s $H_LOCAL http://127.0.0.1:$TPORT/api/pair/list | python3 -c 'import sys,json; ds=json.load(sys.stdin)["devices"]; sys.exit(0 if ds and ds[0]["revoked"] else 1)' \
  && ok "P3 pair/revoke (revoked=true)" || ng "P3 pair/revoke 効かず"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$TPORT/api/pair/new \
  -H "Content-Type: application/json" -d '{"label":"x"}')
[ "$CODE" = "403" ] && ok "P3 pair CSRFガード (ヘッダ無403)" || ng "P3 pair CSRF失敗 (code=$CODE)"

# リソースモニター: /api/status_board（CSRF必須GET）が providers を返し、codex fixtureの usedPercent と 台帳エントリが載る
SB=$(curl -s $H_LOCAL http://127.0.0.1:$TPORT/api/status_board)
echo "$SB" | python3 -c 'import sys,json; d=json.load(sys.stdin); ids=[p["id"] for p in d["providers"]]; assert "codex" in ids and "claude" in ids and "higgsfield" in ids, ids; cx=[p for p in d["providers"] if p["id"]=="codex"][0]; assert abs(cx["usedPercent"]-42.5)<0.01, cx; print("  ✓ status_board (codex 42.5%・台帳・claude)")' && ok "リソースモニターAPI" || ng "status_board API異常: $(echo "$SB" | head -c 160)"
# ヘッダ無しGETは403（CSRFガード）
CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$TPORT/api/status_board)
[ "$CODE" = "403" ] && ok "status_board CSRFガード (403)" || ng "status_board CSRF失敗 (code=$CODE)"
# 台帳POST（upsert）→ 反映
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$TPORT/api/status_board/ledger $H_LOCAL -H "Content-Type: application/json" -d '{"op":"upsert","entry":{"id":"xapi","label":"X API","plan":"従量","remaining":0,"total":100,"unit":"cr"}}')
[ "$CODE" = "200" ] && ok "台帳 upsert (200)" || ng "台帳 upsert失敗 (code=$CODE)"
# 連携設定: status GETはCSRF必須、キー保存は値を応答・ログへ出さずファイル生成だけ確認
CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$TPORT/api/keys/status)
[ "$CODE" = "403" ] && ok "keys/status CSRFガード (ヘッダ無403)" || ng "keys/status CSRF失敗 (code=$CODE)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$TPORT/api/keys/set $H_LOCAL \
  -H "Content-Type: application/json" -d '{"name":"OPENAI_API_KEY","value":"sk-verify-keys-smoke-123456"}')
[ "$CODE" = "200" ] && grep -q '^OPENAI_API_KEY=' "$VHOME/.claude/office_secrets" \
  && ok "keys/set (200+office_secrets行生成)" || ng "keys/set失敗 (code=$CODE)"

# R42.1 エディション: config差し替えで同一サーバーのまま検査（editionは毎スキャンでconfig再読込・cache 2秒）。
# 検査後は必ず元configへ復元＋cache失効待ち（▶7 UIスモークは既定hybrid前提のため）。
API=$(curl -s -H "X-Office-Local: 1" http://127.0.0.1:$TPORT/api/office)
echo "$API" | python3 -c 'import sys,json; d=json.load(sys.stdin); e=d["edition"]; f=e["features"]; assert e["id"]=="hybrid" and f["claudeSessions"] and f["openclaw"] and f["costDash"], e; print("  ✓ R42.1+R84 edition既定=hybrid (鍵なしでfeatures全開)")' \
  || ng "R42.1 edition既定がhybridでない: $(echo "$API" | head -c 160)"
CFG_ORIG=$(cat "$VHOME/office_config.json")
python3 - "$VHOME/office_config.json" claude <<'EOF'
import json, sys
p, ed = sys.argv[1], sys.argv[2]
d = json.loads(open(p, encoding="utf-8").read()); d["edition"] = ed
open(p, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False))
EOF
sleep 2.2
API=$(curl -s -H "X-Office-Local: 1" http://127.0.0.1:$TPORT/api/office)
echo "$API" | python3 -c 'import sys,json; d=json.load(sys.stdin); e=d["edition"]; assert e["id"]=="claude" and e["features"]["openclaw"] is False, e; assert len(d["employees"])==2, len(d["employees"]); print("  ✓ R42.1 edition=claude (openclaw閉・claude社員は出る)")' \
  || ng "R42.1 edition=claude 検査失敗: $(echo "$API" | head -c 160)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" $H_LOCAL http://127.0.0.1:$TPORT/api/external/openclaw)
[ "$CODE" = "403" ] && ok "R42.1 claude版で /api/external/openclaw 403" || ng "R42.1 openclaw APIが閉じない (code=$CODE)"
python3 - "$VHOME/office_config.json" openclaw <<'EOF'
import json, sys
p, ed = sys.argv[1], sys.argv[2]
d = json.loads(open(p, encoding="utf-8").read()); d["edition"] = ed
open(p, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False))
EOF
sleep 2.2
API=$(curl -s -H "X-Office-Local: 1" http://127.0.0.1:$TPORT/api/office)
echo "$API" | python3 -c 'import sys,json; d=json.load(sys.stdin); e=d["edition"]; assert e["id"]=="openclaw" and e["features"]["claudeSessions"] is False, e; assert d["employees"]==[], "openclaw版でclaude社員が出ている"; print("  ✓ R42.1 edition=openclaw (transcriptスキャン停止=社員0)")' \
  || ng "R42.1 edition=openclaw 検査失敗: $(echo "$API" | head -c 160)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" $H_LOCAL http://127.0.0.1:$TPORT/api/external/openclaw)
[ "$CODE" = "200" ] && ok "R42.1 openclaw版で /api/external/openclaw 200" || ng "R42.1 openclaw APIが開かない (code=$CODE)"
printf '%s' "$CFG_ORIG" > "$VHOME/office_config.json"
sleep 2.2

# 2026-08-10 ライセンス廃止: 鍵が無くても有料だった機能（中継/pair/コスト）が全員に開く。
CODE=$(curl -s -o /dev/null -w "%{http_code}" $H_LOCAL http://127.0.0.1:$TPORT/api/status_board)
[ "$CODE" = "200" ] && ok "ライセンス廃止: 鍵無しで status_board 200" || ng "status_boardが開かない (code=$CODE)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:$TPORT/api/pair/new $H_LOCAL \
  -H "Content-Type: application/json" -d '{"label":"nolic"}')
[ "$CODE" = "200" ] && ok "ライセンス廃止: 鍵無しで pair/new 200" || ng "pair/newが開かない (code=$CODE)"
R=$(curl -s $H_LOCAL http://127.0.0.1:$TPORT/api/office)
echo "$R" | python3 -c 'import sys,json; d=json.load(sys.stdin); f=d["edition"]["features"]; assert f["relayPwa"] and f["push"] and f["costDash"], f; print("  \u2713 ライセンス廃止: edition.features 全ON")' \
  || ng "features が全ONでない: $(echo "$R" | head -c 160)"

# R42.3: OpenClaw契約statusを既定パス($VHOME/.claude/openclaw_status.json)へ設置→oc-社員がマージされる
# （external viewは60秒キャッシュがありレースするため、office_json側の2秒キャッシュだけで検査）
python3 - "$VHOME" <<'EOF'
import json, sys, time
from pathlib import Path
raw = json.loads(Path("tests/fixtures/openclaw_status.json").read_text(encoding="utf-8"))
raw["generatedAt"] = time.time()
p = Path(sys.argv[1]) / ".claude" / "openclaw_status.json"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(raw), encoding="utf-8")
EOF
sleep 2.2
API=$(curl -s -H "X-Office-Local: 1" http://127.0.0.1:$TPORT/api/office)
echo "$API" | python3 -c 'import sys,json; d=json.load(sys.stdin); oc=[e for e in d["employees"] if e.get("external")=="openclaw"]; assert len(oc)==3, [e.get("session") for e in d["employees"]]; assert all(e["session"].startswith("oc-") and e["lastSaid"]=="" and e["cwd"]=="" for e in oc), oc; print("  ✓ R42.3 oc-社員3体がoffice_jsonへマージ (本文フィールドは空)")' \
  || ng "R42.3 oc-社員マージ失敗: $(echo "$API" | head -c 200)"
rm -f "$VHOME/.claude/openclaw_status.json"
sleep 2.2

echo "▶ 6/8 受信フックのパイプテスト"
HDIR=$(mktemp -d)
mkdir -p "$HDIR/.claude/office_inbox"
printf '{"text":"hookテスト","ts":1}' > "$HDIR/.claude/office_inbox/hooktest-session-01.json"
OUT=$(echo '{"session_id":"hooktest-session-01"}' | OFFICE_HOME="$HDIR" bash hooks/office-inbox-wait.sh)
RC=$?
[ "$RC" = "2" ] && echo "$OUT" | grep -q "📨" && ok "配達 → exit 2 + 📨" || ng "配達テスト失敗 (rc=$RC)"
OUT=$(echo '{"session_id":"hooktest-session-02"}' | OFFICE_HOME="$HDIR" OFFICE_WAIT_LOOPS=2 OFFICE_WAIT_INTERVAL=0 bash hooks/office-inbox-wait.sh)
[ "$?" = "0" ] && ok "空inboxタイムアウト → exit 0" || ng "タイムアウト系失敗"
rm -rf "$HDIR"

echo "▶ 7/8 UIスモーク (Playwright)"
# UIスモーク用Playwright Python: env(VENV_PY) > verify.local(gitignore・ローカル環境固有) > リポ内venv > playwright入りシステムpython
[ -f verify.local ] && . ./verify.local
VENV_PY="${VENV_PY:-}"
if [ ! -x "$VENV_PY" ]; then
  VENV_PY=""
  for candidate in ./.venv/bin/python ./venv/bin/python; do
    if [ -x "$candidate" ] && "$candidate" -c 'import playwright' >/dev/null 2>&1; then
      VENV_PY="$candidate"
      break
    fi
  done
fi
if [ -z "$VENV_PY" ]; then
  for command_name in python3 python; do
    candidate=$(command -v "$command_name" 2>/dev/null || true)
    if [ -n "$candidate" ] && [ -x "$candidate" ] \
      && "$candidate" -c 'import playwright' >/dev/null 2>&1; then
      VENV_PY="$candidate"
      break
    fi
  done
fi
export VENV_PY
if [ "${SKIP_UI:-}" = "1" ]; then
  echo "  - SKIP_UI=1 で省略"
elif [ -x "$VENV_PY" ] && "$VENV_PY" -c 'import playwright' >/dev/null 2>&1 \
  && "$VENV_PY" -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(); b.close(); p.stop()' >/dev/null 2>&1; then
  mkdir -p tests/artifacts
  # UIスモークの実行器: 失敗時1回だけリトライ（SwiftShader稀死・▶7が8本連続になった
  # フル実行の負荷で毎回別のスモークが落ちる実測フレークへの対策。ui_shot の3回リトライと
  # 同じ思想＝実回帰は2回連続で落ちるので赤のまま）。PIPESTATUSで判定＝sedのexit0に握り潰させない
  run_ui() {
    local label="$1"; shift
    "$@" | sed 's/^/  /'
    local rc=${PIPESTATUS[0]}
    if [ "$rc" != "0" ]; then
      echo "  ↻ ${label}: リトライ（フレーク対策・1回だけ）"
      "$@" | sed 's/^/  /'
      rc=${PIPESTATUS[0]}
    fi
    [ "$rc" = "0" ] || ng "${label}失敗 (exit $rc)"
  }
  # R52: 旧UI(?ui=legacy)削除に伴い legacy 3本（ui_smoke/edition_smoke/i18n_smoke）は退役。
  # 後継カバレッジ= P1入社フロー→ui_admin_smoke／edition表示ゲート→test_edition+▶5 curl／
  # 日本語カナリア→i18n_iso_smoke（新UI・下で常設）
  # R50: 両スタイルが同じ意味を返すか（見た目は別物・情報は同一）＋ inject が本当に効いているか
  run_ui "R50 UI契約テスト" "$VENV_PY" tests/ui_contract.py
  # R50 P6: 操作系（❗回答/コンポーズが office_inbox へ実投函されるか＝配達経路入口の機械証明）
  run_ui "R50 操作系スモーク" "$VENV_PY" tests/ui_ops_smoke.py
  # R50: 管理フロー（➕新プロジェクト=config反映+起動マーカー / 📱ペアリング=台帳発行+失効）
  run_ui "R50 管理フロースモーク" "$VENV_PY" tests/ui_admin_smoke.py
  # R50提案2b: 初回体験（空オフィス導線・hook未設定バナー・?demo=1同梱world+投函ブロック）
  run_ui "R50 初回体験スモーク" "$VENV_PY" tests/ui_onboard_smoke.py
  # R80-B6: WebGL不可の環境（古いGPU/VM/リモートデスクトップ）でも仕事ができるか。
  # 配布すると必ず一定数いる環境で、以前は白画面＋英語の行き止まりだった
  run_ui "R80 WebGL退避スモーク" "$VENV_PY" tests/ui_webgl_fallback_smoke.py \
    "http://127.0.0.1:$TPORT" tests/artifacts/ui_webgl_fallback.png
  # R50提案2c: 新UIの日本語文字カナリア（lang=en で日本語0・旧i18n_smokeの新UI版）
  run_ui "R50 新UI i18nカナリア" "$VENV_PY" tests/i18n_iso_smoke.py
  # R42.6骨格: エディション別表示（openclawダーク/バッジ/Claude面ゲート/②→③導線・claude無退行）
  run_ui "R42.6 エディションUI(新)スモーク" "$VENV_PY" tests/edition_iso_smoke.py
  # R50: 新UIのビジュアル回帰。自前でサーバーを立て /api/office を fixture で差し替えるので
  # 実セッションの状態に左右されない。バックエンドは SwiftShader 固定（実測でビット一致）。
  "$VENV_PY" tools/ui_shot.py --check | sed 's/^/  /'
  [ "${PIPESTATUS[0]}" = "0" ] || ng "R50 新UIビジュアル回帰失敗 (exit ${PIPESTATUS[0]})"
  # R50-P3: 3Dシーンの作り込みを機械採点（空き床・色数・発光・明度）。
  # 閾値は参考画像2を実測して較正済み＝参考画像自身が全項目通る範囲にしてある。
  "$VENV_PY" tools/style_score.py tests/artifacts/ui_iso_scene.png | sed 's/^/  /'
  [ "${PIPESTATUS[0]}" = "0" ] || ng "R50 3D品質ゲート未達 (exit ${PIPESTATUS[0]})"
else
  echo "  - Playwright/Chromium venvなしまたは起動不可 → 省略（検収側で要実行）"
fi
# R52: 旧UIのスクショ差分ゲート(scene_diff.py)は退役（新UIのビジュアル回帰は
# ▶7 の ui_shot --check + style_score が常設で担う）
kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
rm -rf "$VHOME"

echo "▶ 8/8 実データカナリア (トランスクリプト形式の変更検知)"
python3 - <<'EOF' || ng "カナリア失敗＝形式変更の疑い (docs/transcript-format.md 参照)"
import json, os, subprocess, sys, time
from pathlib import Path
proj = Path.home() / ".claude" / "projects"
recent = [f for d in proj.iterdir() if d.is_dir() for f in d.glob("*.jsonl")
          if time.time() - f.stat().st_mtime < 3 * 3600] if proj.is_dir() else []  # SHOW_WINDOW(3h)と同期
if not recent:
    print("  - 直近3時間のセッションなし → カナリア省略"); sys.exit(0)
out = subprocess.run(["python3", "server/office_server.py", "--dump"],
                     capture_output=True, text=True, timeout=60)
n = len(json.loads(out.stdout)["employees"])
assert n > 0, "実データで社員0人"
print(f"  ✓ 実データカナリア OK (社員{n}人検出)")
EOF

echo "▶ 9 中継E2E (P2・任意=RUN_RELAY=1のとき。通常は 4/8 の relay_agent 単体テストがロジックを担保)"
if [ "${RUN_RELAY:-}" = "1" ]; then
  if [ ! -d relay/node_modules ]; then
    ng "中継E2E要求(RUN_RELAY=1)だが relay/node_modules 無し（cd relay && npm install）"
  else
    bash tests/relay_e2e.sh | sed 's/^/  /'
    [ "${PIPESTATUS[0]}" = "0" ] || ng "中継E2E失敗"
  fi
else
  echo "  - RUN_RELAY=1 で wrangler dev を使うE2Eを実行（例: RUN_RELAY=1 bash \"AI Office/verify.sh\"）"
fi

echo "▶ 10 P4常駐の配線検査 (temp-deploy・実launchctlはしない)"
if lsof -ti tcp:$TPORT -sTCP:LISTEN >/dev/null 2>&1; then
  ng "P4 :$TPORT に先客（孤児サーバー）が居る — 停止してから再実行（嘘green防止のため▶10中断）"
else
DTMP=$(mktemp -d)
DHOME=$(python3 tests/make_home.py)
# (a) temp-deploy: exit code＋コード/データ配置＋余計なもの(tests/relay/キャッシュ)が入らない
AIOFFICE_DEST="$DTMP" bash macapp/install.sh >/dev/null 2>&1 || ng "P4 install(1回目) exit≠0"
[ -f "$DTMP/app/server/office_server.py" ] \
  && [ -f "$DTMP/app/server/mcp_office.py" ] && [ -f "$DTMP/app/tools/qr_gen.py" ] \
  && [ -f "$DTMP/app/tools/vendor/segno/encoder.py" ] && [ -f "$DTMP/data/office_config.json" ] \
  && ok "P4 deploy-copy (app/+data/・mcp/qr_gen/vendored segno同梱)" || ng "P4 deploy-copy失敗"
find "$DTMP/app" \( -name '__pycache__' -o -name 'tests' -o -name 'relay' -o -name 'node_modules' \) | grep -q . \
  && ng "P4 app/に除外物が混入" || ok "P4 除外 (キャッシュ/tests/relay無し)"
# (b) コピー先起動＝自己完結証明（:4797・応答者が本当にこのプロセスであることも確認）
OFFICE_HOME="$DHOME" OFFICE_DATA="$DTMP/data" python3 "$DTMP/app/server/office_server.py" --port $TPORT >/dev/null 2>&1 &
DPID=$!
sleep 1.2
kill -0 $DPID 2>/dev/null || ng "P4 コピー先サーバー即死 (${TPORT}先客/EADDRINUSE?)"
D_API=$(curl -s -H "X-Office-Local: 1" http://127.0.0.1:$TPORT/api/office)
echo "$D_API" | grep -q '"employees"' && ok "P4 コピー先サーバー起動+API応答" || ng "P4 コピー先起動失敗: $(echo "$D_API" | head -c 120)"
kill $DPID 2>/dev/null; wait $DPID 2>/dev/null; DPID=""
# (d) plist 妥当性（lint+中身+実行子の存在）
bash macapp/install.sh --print-plist > "$DTMP/p.plist"
plutil -lint "$DTMP/p.plist" >/dev/null && ok "P4 plist lint合格" || ng "P4 plist lint失敗"
grep -q -- "--port" "$DTMP/p.plist" && grep -q "4780" "$DTMP/p.plist" && grep -q "OFFICE_DATA" "$DTMP/p.plist" \
  && grep -q "SuccessfulExit" "$DTMP/p.plist" && grep -q "PYTHONUNBUFFERED" "$DTMP/p.plist" \
  && ok "P4 plist 内容 (port/OFFICE_DATA/KeepAlive/unbuffered)" || ng "P4 plist 内容不備"
PYARG=$(python3 -c "import plistlib,sys; print(plistlib.load(open('$DTMP/p.plist','rb'))['ProgramArguments'][0])")
[ -x "$PYARG" ] && ok "P4 plist の python3 実行可能 ($PYARG)" || ng "P4 plist の python3 不在: $PYARG"
# P4.5: relay 常駐 plist（--port無し・relay_agent.py・PathStateでconfig在時のみ起動・TCC回避）
bash macapp/install.sh --print-relay-plist > "$DTMP/rp.plist"
plutil -lint "$DTMP/rp.plist" >/dev/null \
  && grep -q "server/relay_agent.py" "$DTMP/rp.plist" && ! grep -q -- "--port" "$DTMP/rp.plist" \
  && grep -q "PathState" "$DTMP/rp.plist" && grep -q "office_relay.json" "$DTMP/rp.plist" \
  && grep -q "com.senao.aioffice.relay" "$DTMP/rp.plist" && ! grep -q "/Downloads/" "$DTMP/rp.plist" \
  && ok "P4.5 relay plist (relay_agent/PathState/no-port/TCC回避)" || ng "P4.5 relay plist 不備"
[ -f "$DTMP/com.senao.aioffice.relay.plist" ] && ok "P4.5 relay plist もTESTMODE deployで生成" || ng "P4.5 relay plist未生成"
# TESTMODE deploy が書いた plist（$DTMP/app を指す）で target 実在を確認（rp.plistは実インストール先パス）
RTARGET=$(python3 -c "import plistlib; print(plistlib.load(open('$DTMP/com.senao.aioffice.relay.plist','rb'))['ProgramArguments'][1])" 2>/dev/null)
[ -f "$RTARGET" ] && ok "P4.5 relay plist target実在 (deployのrelay_agent.py)" || ng "P4.5 relay plist target欠落: $RTARGET"
# (e) TCC主張の機械固定: plist にも 既定DEST にも Downloads を書かない
grep -q "/Downloads/" "$DTMP/p.plist" && ng "P4 plist がDownloads参照 (TCC違反)" || ok "P4 plist にDownloads無し (TCC回避)"
grep -q 'Application Support/AIOffice' macapp/aioffice.env.sh && ok "P4 既定DEST=Application Support" || ng "P4 既定DEST不正"
# (f) 冪等＆非破壊: 2回目installで plist不変・data直下の追加物/config追記が残る・トップレベルstaleは剪定
touch "$DTMP/data/zzz_keep.marker"
python3 - "$DTMP/data/office_config.json" <<'EOF'
import json, sys
p = sys.argv[1]
cfg = json.load(open(p))
cfg["projects"]["P4マーカー部署"] = {"name": "P4マーカー"}
json.dump(cfg, open(p, "w"), ensure_ascii=False)
EOF
touch "$DTMP/app/stale_top.sh"
cp "$DTMP/com.senao.aioffice.plist" "$DTMP/p1.plist"
AIOFFICE_DEST="$DTMP" bash macapp/install.sh >/dev/null 2>&1 || ng "P4 install(2回目) exit≠0"
diff -q "$DTMP/p1.plist" "$DTMP/com.senao.aioffice.plist" >/dev/null && [ -f "$DTMP/data/zzz_keep.marker" ] \
  && grep -q "P4マーカー部署" "$DTMP/data/office_config.json" && [ ! -e "$DTMP/app/stale_top.sh" ] \
  && ok "P4 冪等+非破壊 (plist不変・data温存・staleトップレベル剪定)" || ng "P4 再installで破壊/剪定漏れ/plist差分"
# (g) uninstall TESTMODE: app削除・data温存・実plist/実SwiftBarに非接触（テスト後片付けが安全）
AIOFFICE_DEST="$DTMP" bash macapp/uninstall.sh >/dev/null 2>&1 \
  && [ ! -d "$DTMP/app" ] && [ -f "$DTMP/data/office_config.json" ] \
  && [ ! -f "$DTMP/com.senao.aioffice.plist" ] && [ ! -f "$DTMP/com.senao.aioffice.relay.plist" ] \
  && ok "P4 uninstall TESTMODE (app削除・data温存・両plist削除)" || ng "P4 uninstall 動作不正"
# (h) SwiftBar プラグイン構文
bash -n macapp/swiftbar/aioffice.5s.sh && [ -x macapp/swiftbar/aioffice.5s.sh ] \
  && ok "P4 SwiftBarプラグイン (構文+実行ビット)" || ng "P4 SwiftBarプラグイン不備"
rm -rf "$DTMP" "$DHOME"; DTMP=""; DHOME=""
fi
echo "  ℹ 本checkが保証しないもの: 実launchctl load/ログイン自動起動/KeepAlive実挙動/"
echo "    Automation TCC同意/再起動後の復帰。これらは macapp/install.sh 末尾のユーザー手順で確認する"

echo "▶ 11 MCPサーバー stdioスモーク (mcp_office.py)"
MHOME=$(python3 tests/make_home.py)
MOUT=$(printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"office_instruct","arguments":{"session":"sess-verify0001","text":"verify経由"}}}' \
  | OFFICE_HOME="$MHOME" OFFICE_CONFIG="$MHOME/office_config.json" OFFICE_DATA="$MHOME" \
    python3 server/mcp_office.py 2>/dev/null)
MRC=$?
# stdout純度: 将来 office_server の import〜office_json/post_instruction 経路に print が混入した瞬間 fail する番人
echo "$MOUT" | python3 -c 'import sys,json; [json.loads(l) for l in sys.stdin if l.strip()]' 2>/dev/null \
  && ok "MCP stdout純度 (全行が正JSON)" || ng "MCP stdoutにJSON以外が混入"
# 応答数=id付き3リクエスト（通知は無応答）＝空出力の空虚green・過剰応答の両退行を検知
[ "$(printf '%s\n' "$MOUT" | grep -c '"jsonrpc"')" = "3" ] \
  && ok "MCP応答数 (3リクエスト=3応答・通知は無応答)" || ng "MCP応答数が3でない"
[ -f "$MHOME/.claude/office_inbox/sess-verify0001.json" ] && ok "MCP経由の投函 (在席セッションへinbox生成)" || ng "MCP投函失敗"
[ "$MRC" = "0" ] && ok "MCP EOF正常終了 (exit 0)" || ng "MCP exit=$MRC"
rm -rf "$MHOME"; MHOME=""
# 登録は常駐デプロイ済みなら app/ 側を推奨（コード更新は install.sh 再実行で反映）・未導入なら repo 正本
MCP_REG="$(pwd)/server/mcp_office.py"
[ -f "$HOME/Library/Application Support/AIOffice/app/server/mcp_office.py" ] \
  && MCP_REG="$HOME/Library/Application Support/AIOffice/app/server/mcp_office.py"
echo "  ℹ 実Claude Code登録: claude mcp add --scope user aioffice -- \$(python3 -c 'import sys;print(sys.executable)') \"$MCP_REG\""

echo
if [ $NG -eq 0 ]; then echo "✅ verify: 全チェック合格"; else echo "❌ verify: ${NG}件失敗"; exit 1; fi
