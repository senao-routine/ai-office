# -*- coding: utf-8 -*-
"""R100-1b: server/office_approve.py（スマホから許可・許可鍵の登録・期限つき ON）の全部の枝。

- ON は期限なし（R118・bool 以外は拒否）・OFF にするまで ON（期限つきの古い形は期限を過ぎたら OFF）
- 登録（enr-）: チケットは一度きり・端末一致・challenge/origin/type・rpIdHash・UP と UV・credId・物理確認
- 許可（apv-）: 14 の検査を 1 つずつ落として、それぞれの reason で拒否される／全部通ったときだけ回答を書く
- results（reqId → 結果・冪等）と used（askId・二度目の許可を止める）は別のキー
- 台帳・状態・ON のファイルは最初から 0600
署名の外側（HMAC の封筒）は office_server.verify_envelope の実物で確かめる。
"""
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TMP_HOME = tempfile.mkdtemp(prefix="apv-home-")
os.environ["OFFICE_HOME"] = TMP_HOME
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tests"))
import office_approve as oa  # noqa: E402
from fake_authn import FakeAuthenticator  # noqa: E402

try:
    import office_server as osv  # noqa: E402
    VERIFY, SIGN = osv.verify_envelope, osv.sign_envelope
except Exception:  # pragma: no cover  scratch で office_server が無いとき
    import hmac

    def SIGN(sec, did, sess, text, ts, nonce):
        th = hashlib.sha256(text.encode()).hexdigest()
        c = "\n".join(["aioffice-instruct", "v1", did, sess, str(ts), nonce, th]).encode()
        return {"v": 1, "device_id": did, "session": sess, "text": text, "ts": ts, "nonce": nonce, "alg": "HS256",
                "sig": hmac.new(bytes.fromhex(sec), c, hashlib.sha256).hexdigest()}

    def VERIFY(env, devices, now, window):
        rec = devices["devices"].get(env.get("device_id"))
        if not rec or rec.get("revoked") or abs(now - env["ts"]) > window:
            return False, "x", "", ""
        good = SIGN(rec["secret"], env["device_id"], env["session"], env["text"], env["ts"], env["nonce"])
        return (good["sig"] == env["sig"], "ok", env["session"], env["text"])

RP = ("relay.example.dev", "https://relay.example.dev")
DID = "d_0123456789ab"
SEC = "11" * 32
SESSION = "3f2a9c1e-7b4d-4e21-9a0c-5d6e7f809102"
PROJECT = "0123456789ab"


def devices(now=None, **over):
    now = int(now or time.time())
    rec = {"secret": SEC, "label": "テスト端末", "created": now, "expires": now + 86400, "revoked": False}
    rec.update(over)
    return {"version": 1, "devices": {DID: rec}}


def envelope(session, payload, ts=None, nonce=None):
    return SIGN(SEC, DID, session, json.dumps(payload, separators=(",", ":")), int(ts or time.time()),
                nonce or secrets.token_hex(16))


class Base(unittest.TestCase):
    def setUp(self):
        # discover では別のテストが OFFICE_HOME を差し替え／外す＝テストの間だけ一時 HOME に固定する（置き場所は呼び出し時に決まる）
        env = mock.patch.dict(os.environ, {"OFFICE_HOME": TMP_HOME})
        env.start()
        self.addCleanup(env.stop)
        for f in (oa.remote_file(), oa.keys_file(), oa.state_file(), oa.audit_file()):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        self.assertTrue(str(oa.state_file()).startswith(TMP_HOME))   # 実 HOME を触らない安全弁
        self.confirm_answer = True
        self.notes = []

    def confirm(self, msg, ok):
        self.confirm_msg = msg
        return self.confirm_answer

    def enroll(self, auth=None, projects="*", **kw):
        auth = auth or FakeAuthenticator(*RP)
        ok, _m, ticket = oa.new_ticket(DID, projects, devices())
        self.assertTrue(ok)
        got = auth.create(oa.enroll_challenge(DID, ticket), **kw)
        env = envelope("enr-" + ticket, {"aioffice": 1, "kind": "enroll", "ticket": ticket, **got})
        return oa.enroll(env, devices=devices(), verify=VERIFY, rp=RP, confirm=self.confirm,
                         notify=lambda t, b: self.notes.append((t, b))), auth


class RemoteTest(Base):
    def test_on_has_no_time_limit(self):
        """R118（本人裁定 2026-10-13「常に ON」）: ON は期限なし（until＝REMOTE_ALWAYS）。時間を渡しても読まない・bool 以外は拒否。"""
        for h in (None, 1, 4, 12, 24, "4"):
            ok, _m, until = oa.set_remote(True, h, now=1000)
            self.assertTrue(ok, h)
            self.assertEqual(until, oa.REMOTE_ALWAYS, h)
        self.assertFalse(oa.set_remote("yes", 1)[0])
        self.assertFalse(oa.set_remote(1, 1)[0])

    def test_on_stays_until_off(self):
        oa.set_remote(True)
        self.assertEqual(oa.remote_until(), oa.REMOTE_ALWAYS)
        self.assertEqual(oa.remote_until(now=time.time() + 400 * 86400), oa.REMOTE_ALWAYS)   # 1 年たっても ON のまま
        oa.remote_file().write_text(json.dumps({"v": 1, "until": int(time.time()) + 3600}))   # 期限つきの古い形
        self.assertEqual(oa.remote_until(now=time.time() + 3601), 0)                         # は期限を過ぎたら OFF
        oa.set_remote(False)
        self.assertEqual(oa.remote_until(), 0)
        self.assertEqual(stat.S_IMODE(oa.remote_file().stat().st_mode), 0o600)

    def test_broken_or_bool_until_is_off(self):
        for body in ('{"v":1,"until":true}', '{"v":1,"until":"9999999999"}', '{"v":2,"until":9999999999}', "[]", "{"):
            oa.remote_file().write_text(body)
            self.assertEqual(oa.remote_until(), 0, body)


class EnrollTest(Base):
    def phone_enroll(self, auth=None, nonce=None, devs=None, **kw):
        """R103-1: スマホだけで登録（Mac の受付も確認も無し）。スマホが選んだ 32 桁をチャレンジに結ぶ。"""
        auth = auth or FakeAuthenticator(*RP)
        nonce = nonce or secrets.token_hex(16)
        got = auth.create(oa.enroll_challenge(DID, nonce), **kw)
        env = envelope("enr-" + nonce, {"aioffice": 1, "kind": "enroll", "ticket": nonce, **got})
        return oa.enroll(env, devices=devs or devices(), verify=VERIFY, rp=RP, confirm=self.confirm,
                         notify=lambda t, b: self.notes.append((t, b))), auth, env

    def test_enroll_happy_path(self):
        (state, reason, fp), auth = self.enroll()
        self.assertEqual((state, reason), ("granted", ""))
        self.assertEqual(fp, auth.fp())
        self.assertFalse(hasattr(self, "confirm_msg"), "Mac の確認ダイアログを出した（R103-1 で無くした）")
        k = oa.valid_key(DID, devices())
        self.assertEqual(k["fp"], fp)
        self.assertEqual(stat.S_IMODE(oa.keys_file().stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(oa.state_file().stat().st_mode), 0o600)
        self.assertTrue(self.notes)

    def test_phone_only_enroll_needs_no_mac_window_and_no_confirmation(self):
        """R103-1（本人裁定 2026-10-02「登録も ON もスマホだけで完結」「登録した瞬間から有効」）: Mac の受付が無くても、
        ペアリング済みの端末＋パスキー（UP と UV）だけで登録でき、その場で有効。Mac には通知（心当たりが無ければ失効）・監査。"""
        self.assertEqual(oa.approve_public(devices())["enr"], [])
        (state, reason, fp), auth, _env = self.phone_enroll()
        self.assertEqual((state, reason), ("granted", ""))
        self.assertFalse(hasattr(self, "confirm_msg"))
        self.assertEqual(oa.valid_key(DID, devices())["fp"], fp)
        self.assertEqual(oa.valid_key(DID, devices())["projects"], "*")
        self.assertIn("スマホだけ", self.notes[-1][0])
        rows = [json.loads(x) for x in oa.audit_file().read_text().splitlines()]
        self.assertEqual((rows[-1]["kind"], rows[-1]["state"]), ("enroll", "granted"))

    def test_phone_only_enroll_still_needs_a_live_pairing_and_a_real_passkey(self):
        (st, got, _fp), _a, _e = self.phone_enroll(devs=devices(revoked=True))
        self.assertEqual((st, got), ("refused", "bad-sig"))                        # 失効した端末の封筒は通らない
        (st, got, _fp), _a, _e = self.phone_enroll(auth=FakeAuthenticator(*RP, uv=False))
        self.assertEqual(got, "bad-auth-data")                                     # 生体・PIN（UV）が要る
        (st, got, _fp), _a, _e = self.phone_enroll(origin="https://evil.example")
        self.assertEqual(got, "bad-client-data")
        (st, got, _fp), _a, env = self.phone_enroll()
        self.assertEqual(st, "granted")
        again = oa.enroll(env, devices=devices(), verify=VERIFY, rp=RP, confirm=self.confirm)
        self.assertEqual(again[:2], ("refused", "replay"))                         # 同じ封筒の再送は止まる

    def test_old_enrollment_data_cannot_revive_a_revoked_key(self):
        """別モデルレビュー（high）: ペアリング秘密があれば、過去の登録データを外の封筒だけ替えて出し直せた＝失効した鍵が
        Face ID 無しで復活し、カウンタが 0・範囲が * に戻った。使った登録のチャレンジと失効した資格情報は二度と通さない。"""
        nonce = secrets.token_hex(16)
        auth = FakeAuthenticator(*RP)
        got = auth.create(oa.enroll_challenge(DID, nonce))
        p = {"aioffice": 1, "kind": "enroll", "ticket": nonce, **got}
        st, _r, _fp = oa.enroll(envelope("enr-" + nonce, p), devices=devices(), verify=VERIFY, rp=RP, confirm=None)
        self.assertEqual(st, "granted")
        again = oa.enroll(envelope("enr-" + nonce, p), devices=devices(), verify=VERIFY, rp=RP, confirm=None)
        self.assertEqual(again[:2], ("refused", "bad-ticket"))                    # 同じ登録データの出し直し
        self.assertTrue(oa.revoke_key(DID))
        n2 = secrets.token_hex(16)
        got2 = auth.create(oa.enroll_challenge(DID, n2))                           # 同じ資格情報で新しいチャレンジ
        r = oa.enroll(envelope("enr-" + n2, {"aioffice": 1, "kind": "enroll", "ticket": n2, **got2}),
                      devices=devices(), verify=VERIFY, rp=RP, confirm=None)
        self.assertEqual(r[:2], ("refused", "bad-ticket"))                        # 失効した資格情報は復活しない
        self.assertIsNone(oa.valid_key(DID, devices()))
        (st3, _r3, _fp3), _a3, _e3 = self.phone_enroll()                            # 新しいパスキーなら登録できる
        self.assertEqual(st3, "granted")

    def test_mac_window_scope_still_applies(self):
        """Mac の受付（📱 の「許可鍵を登録」）を範囲つきで開いたときは、その範囲で登録される（スマホだけの登録は全プロジェクト）。"""
        auth = FakeAuthenticator(*RP)
        ok, _m, ticket = oa.new_ticket(DID, [PROJECT], devices())
        got = auth.create(oa.enroll_challenge(DID, ticket))
        env = envelope("enr-" + ticket, {"aioffice": 1, "kind": "enroll", "ticket": ticket, **got})
        st, _r, _fp = oa.enroll(env, devices=devices(), verify=VERIFY, rp=RP, confirm=self.confirm)
        self.assertEqual(st, "granted")
        self.assertEqual(oa.valid_key(DID, devices())["projects"], [PROJECT])

    def test_wrong_challenge_and_cred_mismatch(self):
        auth = FakeAuthenticator(*RP)
        ok, _m, ticket = oa.new_ticket(DID, "*", devices())
        got = auth.create(oa.enroll_challenge(DID, "0" * 32))                    # 別のチケットの challenge
        r = oa.enroll(envelope("enr-" + ticket, {"aioffice": 1, "kind": "enroll", "ticket": ticket, **got}),
                      devices=devices(), verify=VERIFY, rp=RP, confirm=self.confirm)
        self.assertEqual(r[1], "bad-client-data")
        ok, _m, ticket = oa.new_ticket(DID, "*", devices())
        got = auth.create(oa.enroll_challenge(DID, ticket))
        got["credId"] = oa.b64u(b"x" * 16)                                        # authData の credId と違う
        r = oa.enroll(envelope("enr-" + ticket, {"aioffice": 1, "kind": "enroll", "ticket": ticket, **got}),
                      devices=devices(), verify=VERIFY, rp=RP, confirm=self.confirm)
        self.assertEqual(r[1], "bad-auth-data")

    def test_no_relay_and_bad_envelope(self):
        ok, _m, ticket = oa.new_ticket(DID, "*", devices())
        auth = FakeAuthenticator(*RP)
        got = auth.create(oa.enroll_challenge(DID, ticket))
        env = envelope("enr-" + ticket, {"aioffice": 1, "kind": "enroll", "ticket": ticket, **got})
        self.assertEqual(oa.enroll(env, devices=devices(), verify=VERIFY, rp=None, confirm=self.confirm)[1], "no-relay")
        env2 = dict(env, sig="0" * 64, nonce=secrets.token_hex(16))
        self.assertEqual(oa.enroll(env2, devices=devices(), verify=VERIFY, rp=RP, confirm=self.confirm)[1], "bad-sig")

    def test_ticket_validation(self):
        self.assertFalse(oa.new_ticket("d_ffffffffffff", "*", devices())[0])
        self.assertFalse(oa.new_ticket(DID, ["../x"], devices())[0])
        self.assertFalse(oa.new_ticket(DID, "all", devices())[0])
        self.assertFalse(oa.new_ticket(DID, "*", devices(revoked=True))[0])
        self.assertTrue(oa.new_ticket(DID, [PROJECT], devices())[0])


class ApproveTest(Base):
    TOOL = "Bash"
    INPUT = {"command": "npm test", "description": "テストを実行"}

    def setUp(self):
        Base.setUp(self)
        (st, _r, _fp), self.auth = self.enroll()
        self.assertEqual(st, "granted")
        oa.set_remote(True, 1)
        self.ask_id = secrets.token_hex(16)
        self.canon = oa.approval_policy.input_canon(self.TOOL, self.INPUT)
        self.sha = oa.approval_policy.input_sha(self.canon)
        self.cwd = tempfile.mkdtemp(prefix="proj-", dir=TMP_HOME)
        self.ask = {"askId": self.ask_id, "tool": self.TOOL, "inputCanon": self.canon, "inputSha": self.sha,
                    "cwd": self.cwd, "session": SESSION, "level": "remote"}
        self.replies = []
        self.iat = int(time.time())
        oa.mark_seen(DID, self.ask_id, self.iat)

    def payload(self, req_id=None, **over):
        req_id = req_id or secrets.token_hex(16)
        fields = dict(device=DID, session=SESSION, ask_id=over.pop("ask_id", self.ask_id),
                      tool=over.pop("tool", self.TOOL), sha=over.pop("sha", self.sha), iat=over.pop("iat", self.iat))
        chal = over.pop("challenge", None) or oa.approve_challenge(fields["device"], fields["session"], fields["ask_id"],
                                                                  fields["tool"], fields["sha"], fields["iat"], "allow",
                                                                  req_id)
        got = (over.pop("auth", None) or self.auth).get(chal, **over.pop("get", {}))
        p = {"aioffice": 1, "kind": "approve", "reqId": req_id, "session": SESSION, "askId": fields["ask_id"],
             "tool": fields["tool"], "inputSha": fields["sha"], "iat": fields["iat"], "decision": "allow", **got}
        p.update(over)
        return req_id, p

    def run_approve(self, p, req_id, **kw):
        env = envelope("apv-" + req_id, p)
        args = dict(devices=devices(), verify=VERIFY, rp=RP,
                    read_ask=lambda s, a: self.ask if (s == SESSION and a == self.ask.get("askId")) else None,
                    write_reply=lambda s, a, extra: self.replies.append((s, a, extra)),
                    project_of=lambda s, ask: PROJECT, home=TMP_HOME, available=True,
                    notify=lambda t, b: self.notes.append((t, b)))
        args.update(kw)
        return oa.approve(env, **args), env

    def test_happy_path_writes_one_remote_reply(self):
        rid, p = self.payload()
        (state, reason, info), _env = self.run_approve(p, rid)
        self.assertEqual((state, reason), ("granted", ""))
        self.assertEqual(self.replies, [(SESSION, self.ask_id, {"inputSha": self.sha, "device": DID, "reqId": rid})])
        self.assertIn("npm test", self.notes[-1][1])
        rows = [json.loads(x) for x in oa.audit_file().read_text().splitlines()]
        self.assertEqual(rows[-1]["state"], "granted")
        self.assertEqual(rows[-1]["askId"], self.ask_id)

    def test_results_and_used_are_separate(self):
        rid, p = self.payload()
        self.assertEqual(self.run_approve(p, rid)[0][0], "granted")
        # 同じ reqId の再送（別の nonce）→ 同じ結果（冪等）・回答は 1 通のまま
        (st, reason, _i), _e = self.run_approve(p, rid)
        self.assertEqual((st, reason), ("granted", ""))
        self.assertEqual(len(self.replies), 1)
        # 別の reqId で同じ askId → used
        time.sleep(0)
        with mock.patch.object(oa, "RATE_GAP", 0):
            rid2, p2 = self.payload()
            (st, reason, _i), _e = self.run_approve(p2, rid2)
        self.assertEqual((st, reason), ("refused", "used"))
        self.assertEqual(len(self.replies), 1)

    def test_replay_of_the_same_envelope(self):
        rid, p = self.payload()
        (_r, env) = self.run_approve(p, rid)
        r2 = oa.approve(env, devices=devices(), verify=VERIFY, rp=RP, read_ask=lambda s, a: self.ask,
                        write_reply=lambda *a: self.replies.append(a), project_of=lambda s, a: PROJECT,
                        home=TMP_HOME, available=True)
        self.assertEqual(r2[:2], ("refused", "replay"))

    def refused(self, reason, rid, p, **kw):
        n = len(self.replies)
        (st, got, _i), _e = self.run_approve(p, rid, **kw)
        self.assertEqual((st, got), ("refused", reason))
        self.assertEqual(len(self.replies), n, reason)            # 拒否では回答を 1 通も書かない

    def test_each_check_refuses(self):
        rid, p = self.payload()
        self.refused("bad-format", rid, dict(p, decision="deny"))
        rid, p = self.payload()
        self.refused("bad-format", rid, dict(p, kind="enroll"))
        rid, p = self.payload()
        self.refused("unavailable", rid, p, available=False)
        rid, p = self.payload()
        self.refused("no-ask", rid, p, read_ask=lambda s, a: None)
        rid, p = self.payload(tool="Write")
        self.refused("ask-mismatch", rid, p)
        rid, p = self.payload(sha="0" * 64)
        self.refused("input-mismatch", rid, p)
        rid, p = self.payload(get={"origin": "https://evil.example"})
        self.refused("bad-client-data", rid, p)
        rid, p = self.payload(get={"typ": "webauthn.create"})
        self.refused("bad-client-data", rid, p)
        rid, p = self.payload(challenge=b"\x00" * 32)
        self.refused("bad-client-data", rid, p)
        rid, p = self.payload(get={"rp_id": "evil.example"})
        self.refused("bad-auth-data", rid, p)
        other = FakeAuthenticator(*RP, cred_id=self.auth.cred_id)                     # 同じ credId・別の鍵
        rid, p = self.payload(auth=other)
        self.refused("bad-sig-inner", rid, p)
        rid, p = self.payload(auth=FakeAuthenticator(*RP))                             # 登録していない credId
        self.refused("not-enrolled", rid, p)
        uv_off = FakeAuthenticator(*RP, d=self.auth.d, cred_id=self.auth.cred_id, uv=False)
        rid, p = self.payload(auth=uv_off)
        self.refused("bad-auth-data", rid, p)

    def test_scope_and_enrollment(self):
        Base.setUp(self)
        (st, _r, _fp), self.auth = self.enroll(projects=["ffffffffffff"])
        oa.set_remote(True, 1)
        oa.mark_seen(DID, self.ask_id, self.iat)
        rid, p = self.payload()
        self.refused("scope", rid, p)
        oa.revoke_key(DID)
        rid, p = self.payload()
        self.refused("not-enrolled", rid, p)

    def test_off_and_expired_and_not_sealed(self):
        rid, p = self.payload()
        oa.set_remote(False)
        self.refused("off", rid, p)
        oa.set_remote(True, 1)
        other_ask = secrets.token_hex(16)
        self.ask = dict(self.ask, askId=other_ask)
        rid, p = self.payload(ask_id=other_ask)
        self.refused("not-sealed", rid, p)
        old = int(time.time()) - oa.SEEN_FRESH - 1
        oa.mark_seen(DID, other_ask, old)
        rid, p = self.payload(ask_id=other_ask, iat=old)
        self.refused("expired", rid, p)

    def test_old_signature_cannot_ride_a_new_seal(self):
        """署名は封じた時刻（iat）に結ばれる: 古い封書の iat で作った署名は、同じ掲示を封じ直した新しい seen では通らない。"""
        old = int(time.time()) - 900
        rid, p = self.payload(iat=old)                   # 900 秒前の封書に対する（届かなかった）署名
        oa.mark_seen(DID, self.ask_id, int(time.time()))  # 攻撃者が封じ直して鮮度だけ新しくする
        self.refused("not-sealed", rid, p)
        rid, p = self.payload(iat=self.iat - 7)          # 封じていない時刻
        self.refused("not-sealed", rid, p)

    def test_clone_detection(self):
        rid, p = self.payload(get={"count_step": 5})
        self.assertEqual(self.run_approve(p, rid)[0][0], "granted")
        self.assertEqual(oa.load_keys()["keys"][DID]["signCount"], 5)
        ask2 = secrets.token_hex(16)
        self.ask = dict(self.ask, askId=ask2)
        oa.mark_seen(DID, ask2, self.iat)
        with mock.patch.object(oa, "RATE_GAP", 0):
            rid, p = self.payload(ask_id=ask2, get={"count_step": 0})           # 5 のまま＝逆行（複製）
            self.refused("clone", rid, p)

    def test_rate(self):
        rid, p = self.payload()
        self.assertEqual(self.run_approve(p, rid)[0][0], "granted")
        ask2 = secrets.token_hex(16)
        self.ask = dict(self.ask, askId=ask2)
        oa.mark_seen(DID, ask2, self.iat)
        rid, p = self.payload(ask_id=ask2)
        self.refused("rate", rid, p)

    def test_mac_level_is_granted_from_the_phone_and_marked_dangerous(self):
        """R103-1（本人裁定 2026-10-02「Mac だけの操作も全文確認＋Face ID でスマホから」）: 拒否リストに当たる形（mac）も、
        14 段が揃えば許可する（スマホは全文を最後まで読む→「内容を確認した」→Face ID の 2 段）。Mac の通知の題で危険な操作だったと分かる。"""
        inp = {"command": "rm -rf build"}
        canon = oa.approval_policy.input_canon("Bash", inp)
        sha = oa.approval_policy.input_sha(canon)
        self.ask = dict(self.ask, inputCanon=canon, inputSha=sha, level="mac")
        rid, p = self.payload(sha=sha)
        (state, reason, info), _env = self.run_approve(p, rid)
        self.assertEqual((state, reason, info.get("level")), ("granted", "", "mac"))
        self.assertIn("危険な操作", self.notes[-1][0])

    def test_unjudged_or_escalated_forms_are_refused_by_the_daemon(self):
        """別モデルレビュー: daemon の再判定も hook と同じ phone_approvable ＋「掲示の時点の段より上がっていない」を見る。
        判定できない構文（case 文＝unreadable-syntax）と、1 段として掲示したのに今は mac（すり替え）は許可しない（askId も使い切らない）。"""
        for inp, posted in (({"command": "case x in x) echo ok;; esac"}, "mac"), ({"command": "rm -rf build"}, "remote")):
            canon = oa.approval_policy.input_canon("Bash", inp)
            sha = oa.approval_policy.input_sha(canon)
            self.ask = dict(self.ask, inputCanon=canon, inputSha=sha, level=posted, askId=self.ask_id)
            rid, p = self.payload(sha=sha)
            self.refused("dangerous", rid, p)
        self.assertEqual(self.replies, [])

    def test_no_full_text_is_never_granted(self):
        """全文が読めない形（掲示に inputCanon が無い＝大きすぎる・読めない）はスマホから許可できない（11 で止まる）。"""
        self.ask = {k: v for k, v in self.ask.items() if k != "inputCanon"}
        rid, p = self.payload()
        self.refused("ask-mismatch", rid, p)

    def test_review_level_is_granted_and_marked_in_the_notice(self):
        """R102-2: 拒否リストに当たらないが 1 段の許可リストに無い形（review）も、14 段が揃えば許可する。
        Mac への通知の題で「確認つきの操作」の許可だったと分かる・監査に段が残る（R116-S0: 題を今の境界に＝R114 で全文を読む義務は廃止）。"""
        inp = {"command": "bash verify.sh"}
        canon = oa.approval_policy.input_canon("Bash", inp)
        sha = oa.approval_policy.input_sha(canon)
        self.ask = dict(self.ask, inputCanon=canon, inputSha=sha, level="review")
        rid, p = self.payload(sha=sha)
        (state, reason, info), _env = self.run_approve(p, rid)
        self.assertEqual((state, reason, info.get("level")), ("granted", "", "review"))
        self.assertEqual(self.notes[-1][0], "スマホから許可（確認つきの操作・Face ID）")
        rows = [json.loads(x) for x in oa.audit_file().read_text().splitlines()]
        self.assertEqual((rows[-1]["state"], rows[-1]["level"]), ("granted", "review"))

    def test_write_failure_is_refused(self):
        def boom(*a):
            raise OSError("disk")
        rid, p = self.payload()
        (st, got, _i), _e = self.run_approve(p, rid, write_reply=boom)
        self.assertEqual((st, got), ("refused", "write-failed"))

    def test_public_views(self):
        pub = oa.approve_public(devices())
        # R102-9: enr＝登録の受付が開いている端末・R103-1: until＝ON の終了時刻（意図した更新）
        self.assertEqual(pub, {"on": 1, "until": oa.remote_until(), "fps": [self.auth.fp()], "enr": []})
        self.assertGreater(pub["until"], time.time())
        self.assertEqual(oa.caps_approve(devices(), True), 1)
        self.assertEqual(oa.caps_approve(devices(), False), 0)
        self.assertEqual(oa.caps_approve(devices(revoked=True), True), 0)
        oa.set_remote(False)
        self.assertEqual(oa.caps_approve(devices(), True), 0)
        self.assertEqual(oa.approve_public(devices())["on"], 0)
        local = oa.keys_local(devices())
        self.assertTrue(local[DID]["active"])
        self.assertNotIn("pub", local[DID])
        self.assertNotIn("credId", local[DID])

    def test_seal_gate(self):
        self.assertIsNone(oa.seal_gate(DID, SESSION, self.ask, devices(), lambda s, a: PROJECT))
        self.assertEqual(oa.seal_gate(DID, SESSION, None, devices(), lambda s, a: PROJECT), "no-ask")
        self.assertEqual(oa.seal_gate("d_ffffffffffff", SESSION, self.ask, devices(), lambda s, a: PROJECT), "not-enrolled")
        oa.set_remote(False)
        self.assertEqual(oa.seal_gate(DID, SESSION, self.ask, devices(), lambda s, a: PROJECT), "off")


class TapModeTest(Base):
    """R114（本人裁定 2026-10-08「ON の間はタップだけ」）→R118（2026-10-13「Claude のスマホアプリと同じく、サクッと許可」）:
    本文の mode:"tap" は WebAuthn（Face ID）を求めず、段（remote・review・mac）を問わず通す。ただし封筒の HMAC・nonce・形式・冪等・ON・
    dialog_seal・ペアリングが有効な端末・掲示・範囲・seen・鮮度・ask/input・used・rate・危険判定（掲示の時点より上がっていない・
    判定できない形）は 1 つも省かない。"""

    # ApproveTest の準備と道具をそのまま使う（継承すると ApproveTest のテストが二重に走る）
    TOOL, INPUT = ApproveTest.TOOL, ApproveTest.INPUT
    setUp = ApproveTest.setUp
    payload = ApproveTest.payload
    run_approve = ApproveTest.run_approve
    refused = ApproveTest.refused

    def tap(self, req_id=None, **over):
        """PWA（R114-P）が送る形: WebAuthn の 4 項目が無い・mode:"tap"。"""
        req_id = req_id or secrets.token_hex(16)
        p = {"aioffice": 1, "kind": "approve", "mode": "tap", "reqId": req_id, "session": SESSION,
             "askId": over.pop("ask_id", self.ask_id), "tool": over.pop("tool", self.TOOL),
             "inputSha": over.pop("sha", self.sha), "iat": over.pop("iat", self.iat), "decision": "allow"}
        p.update(over)
        return req_id, p

    def new_ask(self, inp=None, level="remote"):
        """別の 1 件を掲示して封じる（askId を使い切った後の続きの検査用）。"""
        inp = inp or self.INPUT
        canon = oa.approval_policy.input_canon(self.TOOL, inp)
        sha = oa.approval_policy.input_sha(canon)
        ask_id = secrets.token_hex(16)
        self.ask = dict(self.ask, askId=ask_id, inputCanon=canon, inputSha=sha, level=level)
        oa.mark_seen(DID, ask_id, self.iat)
        return ask_id, sha

    def state_results(self):
        return json.loads(oa.state_file().read_text())["results"]

    def test_tap_mode_skips_webauthn_but_keeps_the_rest(self):
        # --- WebAuthn 以外の段は全部残っている（拒否は回答を書かず・askId も rate も使わない）
        rid, p = self.tap()
        self.assertNotIn("signature", p)
        self.refused("bad-format", rid, dict(p, mode="TAP"))                       # 方式は sig/tap だけ
        rid, p = self.tap()
        self.refused("bad-format", rid, dict(p, mode=None))
        rid, p = self.tap()
        self.refused("bad-format", rid, dict(p, decision="deny"))
        rid, p = self.tap()
        self.refused("bad-format", rid, dict(p, iat="1"))
        rid, p = self.tap()
        self.refused("unavailable", rid, p, available=False)                      # dialog_seal が使えない
        rid, p = self.tap()
        self.refused("no-ask", rid, p, read_ask=lambda s, a: None)                # 掲示が無い（心拍が止まった）
        rid, p = self.tap(tool="Write")
        self.refused("ask-mismatch", rid, p)
        rid, p = self.tap(sha="0" * 64)
        self.refused("input-mismatch", rid, p)                                    # 表示した入力と違う
        other = secrets.token_hex(16)
        self.ask = dict(self.ask, askId=other)
        rid, p = self.tap(ask_id=other)
        self.refused("not-sealed", rid, p)                                        # その端末へ封じていない 1 件
        rid, p = self.tap(ask_id=other, iat=self.iat - 7)
        oa.mark_seen(DID, other, self.iat)
        self.refused("not-sealed", rid, p)                                        # seen の iat が違う
        old = int(time.time()) - oa.SEEN_FRESH - 1
        oa.mark_seen(DID, other, old)
        rid, p = self.tap(ask_id=other, iat=old)
        self.refused("expired", rid, p)                                           # 封じてから 180 秒より後
        self.ask = dict(self.ask, askId=self.ask_id)
        rid, p = self.tap()
        oa.set_remote(False)
        self.refused("off", rid, p)                                               # ON の窓の外
        oa.set_remote(True, 1)
        rid, p = self.tap()
        env = envelope("apv-" + rid, p)
        bad = dict(env, sig="0" * 64)
        r = oa.approve(bad, devices=devices(), verify=VERIFY, rp=RP, read_ask=lambda s, a: self.ask,
                       write_reply=lambda *a: self.replies.append(a), project_of=lambda s, a: PROJECT,
                       home=TMP_HOME, available=True)
        self.assertEqual(r[:2], ("refused", "bad-sig"))                            # ペアリング鍵の HMAC が要る
        self.assertEqual(self.replies, [])
        # --- 全部そろえば WebAuthn 無しで通る（回答に mode:"tap"・署名カウンタは動かさない・通知と監査に方式）
        before = oa.load_keys()["keys"][DID]["signCount"]
        (state, reason, info), env = self.run_approve(p, rid)
        self.assertEqual((state, reason, info["mode"], info["level"]), ("granted", "", "tap", "remote"))
        self.assertEqual(self.replies, [(SESSION, self.ask_id,
                                         {"inputSha": self.sha, "device": DID, "reqId": rid, "mode": "tap"})])
        self.assertEqual(oa.load_keys()["keys"][DID]["signCount"], before)
        self.assertGreater(oa.load_keys()["keys"][DID]["last_used"], 0)
        self.assertEqual(self.notes[-1][0], "スマホから許可（タップ）")
        rows = [json.loads(x) for x in oa.audit_file().read_text().splitlines()]
        self.assertEqual((rows[-1]["state"], rows[-1]["mode"]), ("granted", "tap"))
        self.assertEqual(self.state_results()[rid]["mode"], "tap")
        # 同じ封筒の再送は replay（nonce）
        r = oa.approve(env, devices=devices(), verify=VERIFY, rp=RP, read_ask=lambda s, a: self.ask,
                       write_reply=lambda *a: self.replies.append(a), project_of=lambda s, a: PROJECT,
                       home=TMP_HOME, available=True)
        self.assertEqual(r[:2], ("refused", "replay"))
        # 別の reqId で同じ askId → used（二度目の許可は通らない）
        with mock.patch.object(oa, "RATE_GAP", 0):
            rid2, p2 = self.tap()
            self.refused("used", rid2, p2)
        # 次の 1 件はすぐには通らない（端末ごと 5 秒に 1 件）
        ask2, sha2 = self.new_ask()
        rid3, p3 = self.tap(ask_id=ask2, sha=sha2)
        self.refused("rate", rid3, p3)
        # WebAuthn の項目が付いていても見ない（壊れた値でも tap の段で判定する）
        with mock.patch.object(oa, "RATE_GAP", 0):
            rid4, p4 = self.tap(ask_id=ask2, sha=sha2, credId="AAAA", authData="AAAA", clientDataJSON="AAAA",
                                signature="AAAA")
            (st4, why4, _i), _e = self.run_approve(p4, rid4)
        self.assertEqual((st4, why4), ("granted", ""))

    def test_ux_is_audited_but_never_decides(self):
        """R116-S1: allow の本文の ux（入口・状態・所要 ms）は形が正しいときだけ監査の行へ。形が違っても許可の可否は変わらない（捨てるだけ）。"""
        good = {"e": "notif", "s": "ready", "ms": 4200, "q": 0}
        rid, p = self.tap(ux=good)
        (state, reason, info), _env = self.run_approve(p, rid)
        self.assertEqual((state, reason, info.get("ux")), ("granted", "", good))
        rows = [json.loads(x) for x in oa.audit_file().read_text().splitlines()]
        self.assertEqual(rows[-1]["ux"], good)
        self.assertNotIn("ux", self.replies[-1][2])                                  # hook への回答には載せない
        bad_shapes = [{"e": "notif", "s": "ready", "ms": 1, "q": 0, "text": "rm -rf /"},   # 余計な鍵（本文の紛れ込み）
                      {"e": "elsewhere", "s": "ready", "ms": 1, "q": 0}, {"e": "tab", "s": "need-on", "ms": 1, "q": 0},
                      {"e": "tab", "s": "ready", "ms": -1, "q": 0}, {"e": "tab", "s": "ready", "ms": True, "q": 0},
                      {"e": "tab", "s": "ready", "ms": 1, "q": oa.UX_MS_MAX + 1}, "notif", ["tab"]]
        for bad in bad_shapes:
            aid, sha = self.new_ask()
            rid, p = self.tap(ask_id=aid, sha=sha, ux=bad)
            st = json.loads(oa.state_file().read_text())
            st["rate"] = {}
            oa.state_file().write_text(json.dumps(st))
            (state, reason, info), _env = self.run_approve(p, rid)
            self.assertEqual((state, reason), ("granted", ""), bad)
            self.assertNotIn("ux", info, bad)
            rows = [json.loads(x) for x in oa.audit_file().read_text().splitlines()]
            self.assertNotIn("ux", rows[-1], bad)

    def test_tap_mode_allows_every_level(self):
        """R118（本人裁定 2026-10-13）: tap は段を問わず通す＝mac の段（削除）も 1 タップで allow（回答に mode:"tap"・通知の題は危険な操作）。
        掲示の時点で mac と見せた 1 件がいまの再判定で remote に下がっていても通る（すり替えの拒否は段が上がったときだけ＝
        test_tap_mode_refuses_review_escalated_to_mac）。監査に "tap-mac" の拒否は出ない。"""
        ask_mac, sha_mac = self.new_ask({"command": "rm -rf build"}, level="mac")
        rid, p = self.tap(ask_id=ask_mac, sha=sha_mac)
        (state, reason, info), _e = self.run_approve(p, rid)
        self.assertEqual((state, reason, info["mode"], info["level"]), ("granted", "", "tap", "mac"))
        self.assertEqual(self.replies[-1], (SESSION, ask_mac, {"inputSha": sha_mac, "device": DID, "reqId": rid, "mode": "tap"}))
        self.assertEqual(self.notes[-1][0], "スマホから許可（危険な操作・タップ）")
        with mock.patch.object(oa, "RATE_GAP", 0):
            ask_shown_mac, sha_shown = self.new_ask(level="mac")
            rid, p = self.tap(ask_id=ask_shown_mac, sha=sha_shown)
            (state, reason, info), _e = self.run_approve(p, rid)
            self.assertEqual((state, reason, info["level"]), ("granted", "", "remote"))
            for inp, lv in (({"command": "bash verify.sh"}, "review"), (self.INPUT, "remote")):
                ask_id, sha = self.new_ask(inp, level=lv)
                rid, p = self.tap(ask_id=ask_id, sha=sha)
                (state, reason, info), _e = self.run_approve(p, rid)
                self.assertEqual((state, reason, info["level"], info["mode"]), ("granted", "", lv, "tap"), lv)
                self.assertEqual(self.replies[-1][2]["mode"], "tap")
        self.assertEqual(self.notes[-2][0], "スマホから許可（確認つきの操作・タップ）")
        rows = [json.loads(x) for x in oa.audit_file().read_text().splitlines()]
        self.assertNotIn("tap-mac", [r.get("reason") for r in rows])

    def test_tap_mode_refuses_review_escalated_to_mac(self):
        """review（2 段）・remote として掲示したのに、決定の瞬間の再判定が mac（すり替え）＝tap でも通さない
        （sig の同じ拒否は test_unjudged_or_escalated_forms_are_refused_by_the_daemon が固定）。"""
        for posted in ("review", "remote"):
            ask_id, sha = self.new_ask({"command": "rm -rf build"}, level=posted)
            rid, p = self.tap(ask_id=ask_id, sha=sha)
            self.refused("dangerous", rid, p)
        # 判定できない構文（全文はあるが unreadable-syntax）を mac として掲示した形も tap では通らない
        ask_id, sha = self.new_ask({"command": "case x in x) echo ok;; esac"}, level="mac")
        rid, p = self.tap(ask_id=ask_id, sha=sha)
        (st, got, _i), _e = self.run_approve(p, rid)
        self.assertEqual(st, "refused")
        self.assertEqual(got, "dangerous")
        self.assertEqual(self.replies, [])

    def test_tap_mode_needs_a_valid_pairing_not_a_key(self):
        """R118（本人裁定 2026-10-13）: tap はペアリングが有効な端末なら許可鍵（パスキー）の登録が無くても通る（全プロジェクト）。
        ペアリングの失効は通らない。登録済みの許可鍵に範囲（projects）があれば、その範囲は今までどおり効く。"""
        oa.keys_file().unlink()
        rid, p = self.tap()
        (state, reason, info), _e = self.run_approve(p, rid)
        self.assertEqual((state, reason, info["mode"]), ("granted", "", "tap"))
        with mock.patch.object(oa, "RATE_GAP", 0):
            ask_id, sha = self.new_ask()
            rid, p = self.tap(ask_id=ask_id, sha=sha)
            self.refused("bad-sig", rid, p, devices=devices(revoked=True))        # ペアリングを失効した端末
        Base.setUp(self)
        (st, _r, _fp), self.auth = self.enroll(projects=["ffffffffffff"])
        oa.set_remote(True)
        ask_id, sha = self.new_ask()
        rid, p = self.tap(ask_id=ask_id, sha=sha)
        self.refused("scope", rid, p)                                             # 登録済みの鍵の範囲の外

    def test_tap_and_sig_idempotent_results(self):
        """同じ reqId の再送（別の nonce）は最初と同じ結果・同じ方式（回答は 1 通のまま）。"""
        rid, p = self.tap()
        self.assertEqual(self.run_approve(p, rid)[0][:2], ("granted", ""))
        (st, why, info), _e = self.run_approve(p, rid)
        self.assertEqual((st, why, info["mode"]), ("granted", "", "tap"))
        _sid, sig_p = self.payload(req_id=rid)                                    # 同じ reqId を sig で出し直しても
        (st, why, info), _e = self.run_approve(sig_p, rid)
        self.assertEqual((st, why, info["mode"]), ("granted", "", "tap"))        # 最初の結果と方式
        self.assertEqual(len(self.replies), 1)
        with mock.patch.object(oa, "RATE_GAP", 0):
            ask_id, sha = self.new_ask()
            rid2, p2 = self.payload(ask_id=ask_id, sha=sha)
            self.assertEqual(self.run_approve(p2, rid2)[0][:2], ("granted", ""))
            (st, why, info), _e = self.run_approve(p2, rid2)
            self.assertEqual((st, why, info["mode"]), ("granted", "", "sig"))
            ask_mac, sha_mac = self.new_ask({"command": "rm -rf build"}, level="mac")
            rid3, p3 = self.tap(ask_id=ask_mac, sha=sha_mac)
            self.assertEqual(self.run_approve(p3, rid3)[0][:2], ("granted", ""))     # R118: mac の段も tap で通る
            (st, why, info), _e = self.run_approve(p3, rid3)
            self.assertEqual((st, why, info["mode"]), ("granted", "", "tap"))
        res = self.state_results()
        self.assertEqual({k: res[k]["mode"] for k in (rid, rid2, rid3)}, {rid: "tap", rid2: "sig", rid3: "tap"})
        self.assertEqual(len(self.replies), 3)

    def test_idempotent_reply_keeps_the_stored_mode(self):
        """R114-S2（レビュー low）: 同じ reqId の再送の応答は results に残った mode をそのまま返す（上書きしない）。
        未知の方式で拒否した 1 件（"?"）が、再送の応答で "sig"（Face ID を使った）に化けない。mode の無い行（R114 より前）は sig。"""
        # 未知の方式（"TAP"）→ bad-format・results に "?"。同じ reqId を正しい tap で出し直しても最初の結果と方式のまま
        rid, p = self.tap()
        self.refused("bad-format", rid, dict(p, mode="TAP"))
        self.assertEqual(self.state_results()[rid]["mode"], "?")
        (st, why, info), _e = self.run_approve(p, rid)
        self.assertEqual((st, why, info["mode"]), ("refused", "bad-format", "?"))
        _sid, sig_p = self.payload(req_id=rid)
        (st, why, info), _e = self.run_approve(sig_p, rid)
        self.assertEqual((st, why, info["mode"]), ("refused", "bad-format", "?"))
        # results に直接残った行: この版の知らない方式はそのまま・mode の無い行は sig
        now = int(time.time())
        state = json.loads(oa.state_file().read_text())
        fut, old = secrets.token_hex(16), secrets.token_hex(16)
        state["results"][fut] = {"state": "refused", "reason": "off", "ts": now, "mode": "later-mode"}
        state["results"][old] = {"state": "granted", "reason": "", "ts": now}
        oa.state_file().write_text(json.dumps(state))
        for r, want in ((fut, ("refused", "off", "later-mode")), (old, ("granted", "", "sig"))):
            _r, p = self.tap(r)
            (st, why, info), _e = self.run_approve(p, r)
            self.assertEqual((st, why, info["mode"]), want, r)
        self.assertEqual(self.state_results()[fut]["mode"], "later-mode")    # 保存した値も書き換えない
        self.assertEqual(self.replies, [])

    def test_sig_mode_unchanged(self):
        """mode 省略と "sig" は R100-1b のまま: WebAuthn の 4 項目が必須・回答の extra に mode は付かない・署名カウンタを進める。
        canonical の項目（KAT は tests/test_approve_kat.py と tests/approve_kat.mjs が固定）に mode は入らない。"""
        import inspect
        self.assertEqual(list(inspect.signature(oa.canonical).parameters),
                         ["device", "session", "ask_id", "tool", "input_sha", "iat", "decision", "req_id"])
        rid, p = self.payload()
        self.refused("bad-format", rid, {k: v for k, v in p.items() if k != "signature"})
        rid, p = self.payload()
        self.refused("bad-format", rid, dict({k: v for k, v in p.items() if k not in oa.WEBAUTHN_FIELDS}, mode="sig"))
        rid, p = self.payload(get={"count_step": 3})
        (state, reason, info), _e = self.run_approve(dict(p, mode="sig"), rid)
        self.assertEqual((state, reason, info["mode"]), ("granted", "", "sig"))
        self.assertEqual(self.replies, [(SESSION, self.ask_id, {"inputSha": self.sha, "device": DID, "reqId": rid})])
        self.assertEqual(oa.load_keys()["keys"][DID]["signCount"], 3)
        self.assertEqual(self.notes[-1][0], "スマホから許可")


class RemoteSetTest(Base):
    """R103-1→R118（本人裁定 2026-10-13「常に ON」）: スマホから「スマホから許可」を ON/OFF（rst-）。ON も OFF も封筒（ペアリング鍵の
    HMAC）だけ・ON は期限なし（hours やパスキーの項目が付いていても読まない）・結果は reqId で冪等・回数は別の枠・Mac には毎回通知。"""

    def setUp(self):
        Base.setUp(self)
        oa.set_remote(False)

    def rst(self, on=True, ts=None, req_id=None, devs=None, **over):
        req_id = req_id or secrets.token_hex(16)
        ts = int(ts or time.time())
        p = {"aioffice": 1, "kind": "remote_set", "reqId": req_id, "on": on}
        p.update(over)
        env = envelope("rst-" + req_id, p, ts=ts)
        return oa.remote_set(env, devices=devs or devices(), verify=VERIFY, rp=RP,
                             notify=lambda t, b: self.notes.append((t, b))), env

    def test_on_from_the_phone_needs_only_the_envelope(self):
        (state, reason, info), _env = self.rst(on=True)
        self.assertEqual((state, reason), ("granted", ""))
        self.assertEqual(oa.remote_until(), oa.REMOTE_ALWAYS)
        self.assertEqual(info["until"], oa.REMOTE_ALWAYS)
        self.assertIn("ON（期限なし）", self.notes[-1][0])
        oa.set_remote(False)
        with mock.patch.object(oa, "RST_GAP", 0):                                     # 古い端末の時間・パスキーの項目は読まない
            (state, reason, _i), _env = self.rst(on=True, hours=1, credId="AAAA", authData="AAAA", clientDataJSON="AAAA",
                                                 signature="AAAA")
        self.assertEqual((state, reason), ("granted", ""))
        self.assertEqual(oa.remote_until(), oa.REMOTE_ALWAYS)

    def test_off_needs_only_the_envelope(self):
        oa.set_remote(True, 1)
        (state, reason, info), _env = self.rst(on=False)
        self.assertEqual((state, reason, info["until"]), ("granted", "", 0))
        self.assertEqual(oa.remote_until(), 0)

    def test_on_refusals(self):
        (st, got, _i), _e = self.rst(on="yes")
        self.assertEqual(got, "bad-format")                                          # on は true/false だけ
        (st, got, _i), _e = self.rst(devs=devices(revoked=True))
        self.assertEqual(got, "bad-sig")                                             # ペアリングを失効した端末
        self.assertEqual(oa.remote_until(), 0)

    def test_write_failure_does_not_leave_it_on(self):
        """別モデルレビュー（medium）→R118: ON の保存に失敗したら ON を残さない（拒否 write-failed）。"""
        real = oa._write_json

        def boom(path, data):
            if path == oa.remote_file():
                raise OSError("disk full")
            return real(path, data)
        with mock.patch.object(oa, "_write_json", side_effect=boom):
            (st, got, _i), _e = self.rst(on=True)
        self.assertEqual((st, got), ("refused", "write-failed"))
        self.assertEqual(oa.remote_until(), 0)

    def test_last_write_failure_restores_the_previous_state(self):
        """別モデルレビュー（medium・2 巡目）: ON の後の結果の控えの保存に失敗 → 拒否（write-failed）で終わるなら新しい ON を残さない
        （前の状態へ戻す）。OFF はもう効いている（安全側）＝受理として通知まで進む。"""
        real = oa._write_json
        calls = {"n": 0}

        def boom_last_state(path, data):
            if path == oa.state_file() and isinstance(data, dict) and any(
                    isinstance(v, dict) and v.get("state") == "granted" for v in (data.get("results") or {}).values()):
                calls["n"] += 1
                raise OSError("disk full")
            return real(path, data)
        with mock.patch.object(oa, "_write_json", side_effect=boom_last_state):
            (st, got, _i), _e = self.rst(on=True, hours=1)
        self.assertEqual((st, got, calls["n"]), ("refused", "write-failed", 1))
        self.assertEqual(oa.remote_until(), 0)                                       # 前の状態（OFF）へ戻した
        oa.set_remote(True, 4)
        before = oa.remote_until()
        with mock.patch.object(oa, "_write_json", side_effect=boom_last_state), mock.patch.object(oa, "RST_GAP", 0):
            (st, got, _i), _e = self.rst(on=True, hours=1)
        self.assertEqual((st, got), ("refused", "write-failed"))
        self.assertEqual(oa.remote_until(), before)                                  # 前の ON のまま
        n0 = len(self.notes)
        with mock.patch.object(oa, "_write_json", side_effect=boom_last_state), mock.patch.object(oa, "RST_GAP", 0):
            (st, got, _i), _e = self.rst(on=False)
        self.assertEqual((st, got), ("granted", ""))                                 # OFF は効いた＝受理・通知
        self.assertEqual(oa.remote_until(), 0)
        self.assertEqual(len(self.notes), n0 + 1)

    def test_idempotent_and_rate_limited(self):
        rid = secrets.token_hex(16)
        (st1, _r, _i), env = self.rst(req_id=rid)
        self.assertEqual(st1, "granted")
        again = oa.remote_set(env, devices=devices(), verify=VERIFY, rp=RP)
        self.assertEqual(again[:2], ("refused", "replay"))                           # 同じ封筒は二度通らない
        (st2, got, _i), _e = self.rst(on=False)
        self.assertEqual((st2, got), ("refused", "rate"))                            # 10 秒に 1 回


class SummaryTest(Base):
    def test_today_summary(self):
        now = time.time()
        rows = [{"v": 1, "ts": now, "ev": "ask", "level": "mac", "reasons": ["command"]},
                {"v": 1, "ts": now, "ev": "ask", "level": "mac", "reasons": ["command"]},
                {"v": 1, "ts": now, "ev": "ask", "level": "remote", "reasons": []},
                {"v": 1, "ts": now, "ev": "decide", "src": "remote", "decision": "allow"},
                {"v": 1, "ts": now - 3 * 86400, "ev": "ask", "level": "mac", "reasons": ["script"]},
                {"v": 1, "ts": now, "ev": "ask", "level": "mac", "reasons": ["<script>"]}]
        oa.hook_audit_file().parent.mkdir(parents=True, exist_ok=True)
        oa.hook_audit_file().write_text("\n".join(json.dumps(r) for r in rows) + "\nbroken\n")
        got = oa.today_summary(now)
        self.assertEqual(got, {"remote": 1, "asked": 4, "mac": {"command": 2}})


if __name__ == "__main__":
    unittest.main()
