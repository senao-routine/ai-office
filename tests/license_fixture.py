# -*- coding: utf-8 -*-
"""テスト/verify用ライセンス発行ヘルパー（fixtures/license_test_key.json で署名）。

Pro機能ゲート（status_board・pair/new・relayPwa）配下のAPIを従来どおり検証するテストは、
これで一時HOMEへライセンスを敷いてから叩く。本番鍵とは別のテスト鍵＝製品側では無効。"""
import importlib.util
import json
import os
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

_KEY = json.loads((TESTS / "fixtures" / "license_test_key.json").read_text(encoding="utf-8"))
N = int(str(_KEY["n"]), 16)
E = int(_KEY["e"])
D = int(str(_KEY["d"]), 16)

_spec = importlib.util.spec_from_file_location("license_sign_fx", ROOT / "tools" / "license_sign.py")
_signer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_signer)


def issue(edition="hybrid", email="fixture@test"):
    return _signer.build_license(edition, email, N, E, D)


def install(path, edition="hybrid"):
    """ライセンスをpathへ書き、検証が通るよう環境（パス+テスト公開鍵）を注入する。"""
    p = Path(path)
    p.write_text(json.dumps(issue(edition), ensure_ascii=False), encoding="utf-8")
    os.environ["OFFICE_LICENSE"] = str(p)
    os.environ["OFFICE_LICENSE_PUBKEY_N"] = format(N, "x")
    return p
