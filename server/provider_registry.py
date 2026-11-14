"""R103-5: AI サービス・API の接続の正本（本人 2026-10-01「リソースは様々な AI サービスや API がきちんと接続できる仕様に」）。

1 プロバイダ＝この表の 1 行（＋新しい取得の形なら下の関数 1 本）。status_board（消費・残高・上限）・office_server（🔑 の行・接続テスト・
保存できる鍵の名前）・UI（🔑 の行と「＋ つなぐ」）はここを読むだけ＝4 か所の手書きの同期をやめる。

掟（R63 から引き継ぐ）:
- 取れない値は None（推測のレート・既定の上限・丸めた安全幅を作らない）。上限が取れず手動の予算も無いなら % を出さない。
- 鍵は応答・ログ・office_json（中継）に出さない。取得の関数は鍵を URL に入れない（ヘッダーだけ）。
- 公開されていない API を推測で叩かない（Mistral は消費の API が無い＝鍵が使えるかの確認と手動予算だけ）。

出典（2026-10-02 に公式ドキュメントで確認）:
- Anthropic: GET /v1/organizations/cost_report（Admin キー・x-api-key・anthropic-version）。amount は最小単位（セント）の 10 進文字列・
  currency は USD・bucket_width=1d・limit 最大 31・has_more/next_page。組織の月の上限を読む API は無い（Spend Limits API は Enterprise の
  メンバーごとの上限＝別物）。platform.claude.com/docs/en/api/admin-api/usage-cost/get-cost-report
- Mistral: 消費・残高・上限を読む公開 API は見つからない（console の Usage/Limits だけ）。鍵の確認は GET /v1/models。
- OpenAI: GET /v1/organization/costs（管理キー）＝既存の status_board.collect_openai_cost。Groq: GET /openai/v1/models（鍵の確認だけ）。
"""
import math
from datetime import datetime, timezone
from urllib.parse import urlencode

KINDS = ("subscription", "payg", "local")
MEASURES = ("spend", "limit", "balance", "connect")

# label= ゲージ・status_board の名前（ブランド名）。panel= 🔑 の行の見出し（ja, en）。hint= 何が取れるか（ja, en）。
# measures= この接続で測れるもの（UI はここから「残高のみ」等を言う）。test= 接続テストの方法（None＝取得の関数を 1 回走らせる）。
PROVIDERS = (
    {"id": "anthropic", "label": "Anthropic", "kind": "payg", "key": "ANTHROPIC_ADMIN_KEY", "measures": ("spend",),
     "panel": ("Anthropic 使用金額（Admin キー）", "Anthropic spend (Admin key)"),
     "hint": ("組織の今月の実請求額（USD）を取得します。上限は API で読めないので ⚡ の予算で手動設定します",
              "Fetches your organization's spend this month (USD). The cap is not readable by API — set a budget in ⚡"),
     "ph": "sk-ant-admin01-…", "getFrom": "console.anthropic.com/settings/admin-keys", "test": None},
    {"id": "openai_usage", "label": "OpenAI", "kind": "payg", "key": "OPENAI_ADMIN_KEY", "measures": ("spend",),
     "panel": ("OpenAI 使用金額（管理キー）", "OpenAI spend (admin key)"),
     "hint": ("organization の実請求額を取得します", "Fetches your organization's actual spend"),
     "ph": "sk-admin-…", "getFrom": "platform.openai.com/settings/organization/admin-keys", "test": "openai_costs"},
    {"id": "mistral", "label": "Mistral", "kind": "payg", "key": "MISTRAL_API_KEY", "measures": ("connect",),
     "panel": ("Mistral（接続と予算）", "Mistral (connection & budget)"),
     "hint": ("鍵が使えるかを確かめます。消費・残高の API は公開されていないので ⚡ の予算で上限を手動設定します",
              "Checks that the key works. There is no public spend/balance API — set a budget in ⚡"),
     "ph": "…（32 文字）", "getFrom": "console.mistral.ai/api-keys", "test": None},
    {"id": "openrouter", "label": "OpenRouter", "kind": "payg", "key": "OPENROUTER_API_KEY", "measures": ("spend", "limit"),
     "panel": ("OpenRouter（消費・上限）", "OpenRouter (spend & limit)"),
     "hint": ("当月消費とキー上限を自動取得します", "Fetches this month's spend and the key's limit"),
     "ph": "sk-or-v1-…", "getFrom": "openrouter.ai/settings/keys", "test": None},
    {"id": "moonshot", "label": "Kimi (Moonshot)", "kind": "payg", "key": "MOONSHOT_API_KEY", "measures": ("balance",),
     "panel": ("Kimi / Moonshot（残高）", "Kimi / Moonshot (balance)"),
     "hint": ("残高を自動取得します（国際版 api.moonshot.ai のキー）", "Fetches balance (international api.moonshot.ai key)"),
     "ph": "sk-…", "getFrom": "platform.kimi.ai", "test": None},
    {"id": "deepseek", "label": "DeepSeek", "kind": "payg", "key": "DEEPSEEK_API_KEY", "measures": ("balance",),
     "panel": ("DeepSeek（残高）", "DeepSeek (balance)"),
     "hint": ("残高を自動取得します", "Fetches balance"),
     "ph": "sk-…", "getFrom": "platform.deepseek.com/api_keys", "test": None},
    {"id": "groq", "label": "Groq", "kind": "payg", "key": "GROQ_API_KEY", "measures": ("connect",),
     "panel": ("Groq（予算のみ）", "Groq (budget only)"),
     "hint": ("消費APIが無いため、⚡の予算欄で上限を手動設定して使います", "No spend API — set a manual budget in ⚡ instead"),
     "ph": "gsk_…", "getFrom": "console.groq.com/keys", "test": "groq_models"},
)
BY_ID = {p["id"]: p for p in PROVIDERS}
KEY_NAMES = frozenset(p["key"] for p in PROVIDERS)


def spec(provider_id):
    return BY_ID.get(provider_id)


# ------------------------------------------------------------------ 取得（新しい形のものだけ・get_json は status_board._fetch_json）

def _month_start(now):
    d = datetime.fromtimestamp(now, tz=timezone.utc)
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _cents(value):
    """Anthropic の amount＝最小単位（セント）の 10 進文字列（"123.45" は $1.23）。負・非数は ValueError。"""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("amount")
    v = float(value)
    if not math.isfinite(v) or v < 0:
        raise ValueError("amount")
    return v


def fetch_anthropic(secret, get_json, now):
    """GET /v1/organizations/cost_report（Admin キー）。今月 1 日（UTC）からの日次の合計＝今月の請求額（USD）。上限は読めない＝None。"""
    start = _month_start(now).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"x-api-key": secret, "anthropic-version": "2023-06-01"}
    total, page = 0.0, None
    for _ in range(4):                                  # 31 日分＝1 ページ。続きがあっても 4 ページで打ち切る（暴走しない）
        q = {"starting_at": start, "bucket_width": "1d", "limit": 31}
        if page:
            q["page"] = page
        payload = get_json("https://api.anthropic.com/v1/organizations/cost_report?" + urlencode(q), headers)
        if not isinstance(payload, dict) or payload.get("error"):
            err = payload.get("error") if isinstance(payload, dict) else None
            return {"error": err if isinstance(err, str) else "invalid response"}
        data = payload.get("data")
        if not isinstance(data, list):
            return {"error": "invalid response"}
        try:
            for bucket in data:
                # 別モデルレビュー: 欠けた・壊れたバケットを空として数えると「今月 $0」と嘘をつく＝形が違えば取得失敗
                if not isinstance(bucket, dict) or not isinstance(bucket.get("results"), list):
                    raise ValueError("bucket")
                for r in bucket["results"]:
                    if not isinstance(r, dict):
                        raise ValueError("result")
                    if r.get("currency", "USD") != "USD":
                        return {"error": "unexpected currency"}
                    total += _cents(r.get("amount"))
        except (TypeError, ValueError, AttributeError):
            return {"error": "invalid response"}
        if payload.get("has_more") is not True:
            return {"currency": "USD", "spentMonth": round(total / 100.0, 2), "limit": None}
        page = payload.get("next_page")
        if not isinstance(page, str) or not page:
            return {"error": "invalid response"}         # 続きがあるのに次の印が無い＝一部の額を月の合計にしない
    return {"error": "invalid response"}                 # 4 ページで終わらない（月は 31 日＝1 ページのはず）＝部分の額を出さない


def fetch_mistral(secret, get_json, now):
    """消費・残高の API は公開されていない（2026-10 時点の公式ドキュメント）＝鍵が使えるかだけ（GET /v1/models）。手動予算で % を出す。"""
    payload = get_json("https://api.mistral.ai/v1/models", {"Authorization": "Bearer " + secret})
    if not isinstance(payload, dict) or payload.get("error"):
        err = payload.get("error") if isinstance(payload, dict) else None
        return {"error": err if isinstance(err, str) else "invalid response"}
    if not isinstance(payload.get("data"), list):
        return {"error": "invalid response"}
    return {"currency": "USD", "spentMonth": None, "balance": None, "limit": None, "noApi": True}


FETCHERS = {"anthropic": fetch_anthropic, "mistral": fetch_mistral}


# ------------------------------------------------------------------ 接続テスト（保存の前に 1 回・鍵は返さない）

def _probe(kind, secret, get_json, now):
    if kind == "groq_models":
        return get_json("https://api.groq.com/openai/v1/models", {"Authorization": "Bearer " + secret})
    if kind == "openai_costs":
        q = urlencode({"start_time": int(_month_start(now).timestamp()), "limit": 1})
        return get_json("https://api.openai.com/v1/organization/costs?" + q, {"Authorization": "Bearer " + secret})
    return {"error": "invalid response"}


def error_text(err, lang_ja=True):
    """取得の失敗の言葉（推測で言い換えない＝HTTP の番号はそのまま添える）。"""
    e = str(err or "")
    if e in ("HTTP 401", "HTTP 403"):
        return ("鍵が無効か、権限が足りません（%s）" % e) if lang_ja else ("The key is invalid or lacks permission (%s)" % e)
    if e == "HTTP 404":
        return "この鍵では使えない窓口です（HTTP 404）" if lang_ja else "This endpoint is not available for this key (HTTP 404)"
    if e == "HTTP 429":
        return "回数の上限に当たりました（HTTP 429）。少し待ってから" if lang_ja else "Rate limited (HTTP 429). Try again shortly"
    if e.startswith("HTTP 5"):
        return ("相手の側の障害です（%s）" % e) if lang_ja else ("The provider is having trouble (%s)" % e)
    if e == "timeout":
        return "応答がありません（時間切れ）" if lang_ja else "No response (timed out)"
    if e == "network error":
        return "つながりません（ネットワーク）" if lang_ja else "Cannot connect (network)"
    if e == "unexpected currency":
        return "想定外の通貨で返りました" if lang_ja else "Unexpected currency in the response"
    return "思っていた形の応答ではありません" if lang_ja else "The response was not in the expected shape"


def test(provider_id, secret, get_json, fetch, now, lang_ja=True):
    """(ok, message)。fetch＝その provider の取得の関数（status_board が渡す）。鍵は message に入れない。"""
    p = spec(provider_id)
    if not p:
        return False, ("知らないプロバイダです" if lang_ja else "Unknown provider")
    if p.get("test"):
        payload = _probe(p["test"], secret, get_json, now)
        if not isinstance(payload, dict) or payload.get("error"):
            return False, error_text(payload.get("error") if isinstance(payload, dict) else "", lang_ja)
        return True, ("つながりました（鍵は使えます）" if lang_ja else "Connected (the key works)")
    payload = fetch(secret) if fetch else {"error": "invalid response"}
    if not isinstance(payload, dict) or payload.get("error"):
        return False, error_text(payload.get("error") if isinstance(payload, dict) else "", lang_ja)
    parts = []
    if isinstance(payload.get("spentMonth"), (int, float)):
        parts.append(("今月 %.2f %s" if lang_ja else "This month %.2f %s") % (payload["spentMonth"], payload.get("currency") or "USD"))
    if isinstance(payload.get("balance"), (int, float)):
        parts.append(("残高 %.2f %s" if lang_ja else "Balance %.2f %s") % (payload["balance"], payload.get("currency") or "USD"))
    if isinstance(payload.get("limit"), (int, float)):
        parts.append(("上限 %.2f" if lang_ja else "Limit %.2f") % payload["limit"])
    head = "つながりました" if lang_ja else "Connected"
    return True, head + ("（" + "・".join(parts) + "）" if parts and lang_ja else (" (" + ", ".join(parts) + ")" if parts else ""))
