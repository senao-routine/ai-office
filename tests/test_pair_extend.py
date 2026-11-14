# -*- coding: utf-8 -*-
"""R116-S1b つながりの延長（本人裁定 2026-10-26・計画 ~/.claude/plans/r116-s1b-pair-extend.md）。

Mac の［延長］（Origin・物理確認・通知）→ スマホが今の秘密で求める（rky- req）→ daemon が新しい秘密を今の秘密で封じる
→ スマホが新しい秘密で受領（rky- ack）→ 昇格（古い秘密は PREV_GRACE だけ受ける）。新しい秘密は URL に一度も載らない。"""
import io
import json
import os
import secrets
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))


def _reload(home):
    os.environ["OFFICE_HOME"] = str(home)
    for m in ("office_server", "relay_agent", "dialog_seal"):
        sys.modules.pop(m, None)
    import office_server
    return office_server


class PairExtendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.notify = Path(self.home) / "notify.txt"
        env = patch.dict(os.environ, {"OFFICE_FAKE_NOTIFY": str(self.notify), "OFFICE_FAKE_CONFIRM": "ok"})
        env.start()
        self.addCleanup(env.stop)
        self.office = _reload(self.home)
        self.ds = self.office.dialog_seal
        if not self.ds.AVAILABLE:
            self.skipTest("封緘バックエンドが無い環境")
        dev = self.office.new_device("テスト端末")
        self.did, self.s = dev["device_id"], dev["secret"]

    def tearDown(self):
        os.environ.pop("OFFICE_HOME", None)
        self.tmp.cleanup()

    # ── 道具 ──────────────────────────────────────────────────────────
    def ledger(self):
        return self.office.load_devices()["devices"][self.did]

    def edit(self, **kw):
        d = self.office.load_devices()
        d["devices"][self.did].update(kw)
        self.office.save_devices(d)

    def env(self, secret, step, ts=None, req_id=None):
        req_id = req_id or secrets.token_hex(16)
        text = json.dumps({"aioffice": 1, "kind": "rekey", "step": step, "reqId": req_id})
        return req_id, self.office.sign_envelope(secret, self.did, f"rky-{req_id}", text,
                                                 int(ts or time.time()), secrets.token_hex(16))

    def instruct(self, secret, ts=None):
        return self.office.sign_envelope(secret, self.did, "sess-aaaa1111", "続けて", int(ts or time.time()),
                                         secrets.token_hex(16))

    def accepts(self, env, now=None):
        return self.office.verify_envelope(env, self.office.load_devices(), int(now or time.time()), 300)[0]

    def post(self, path, body, origin=True, client="127.0.0.1"):
        raw = json.dumps(body).encode()
        h = self.office.Handler.__new__(self.office.Handler)
        h.path = path
        h.headers = {"Host": "127.0.0.1:4780", "X-Office-Local": "1", "Content-Length": str(len(raw)),
                     **({"Origin": "http://127.0.0.1:4780"} if origin else {})}
        h.client_address = (client, 12345)
        h.rfile = io.BytesIO(raw)
        h._send = Mock()
        h.do_POST()
        code, out, _ = h._send.call_args.args
        return code, json.loads(out)

    def arm(self):
        code, out = self.post("/api/pair/extend", {"device_id": self.did})
        self.assertEqual(code, 200, out)

    def request_next(self):
        """req を送り、封書を今の秘密で開いて新しい秘密 {s, e} を返す。"""
        req_id, env = self.env(self.s, "req")
        ok, msg, extra = self.office._pair_rekey({"envelope": env})
        self.assertTrue(ok, msg)
        self.assertEqual(extra["state"], "sealed")
        bundle = self.ds.live_bundle(req_id)
        self.assertIsNotNone(bundle, "封書が置き場に無い")
        return self.ds.unseal(self.s, self.did, self.office.REKEY_TARGET, bundle)

    # ── ［延長］の受け口 ─────────────────────────────────────────────
    def test_extend_needs_origin_a_physical_confirm_and_notifies(self):
        code, _ = self.post("/api/pair/extend", {"device_id": self.did}, origin=False)
        self.assertEqual(code, 403, "Origin 無しで延長の窓が開く")
        with patch.dict(os.environ, {"OFFICE_FAKE_CONFIRM": "cancel"}):
            code, _ = self.post("/api/pair/extend", {"device_id": self.did})
        self.assertEqual(code, 400)
        self.assertNotIn("rekey_until", self.ledger(), "Mac の確認でキャンセルしたのに窓が開いた")
        self.arm()
        until = self.ledger()["rekey_until"]
        self.assertAlmostEqual(until, time.time() + self.office.REKEY_WINDOW, delta=5)
        self.assertNotIn("next", self.ledger(), "押しただけで秘密を作った（作るのはスマホが求めたとき）")
        self.assertEqual(self.office.rekey_pending(), [self.did])
        self.assertIn("テスト端末", self.notify.read_text(encoding="utf-8"))

    def test_revoked_expired_or_unknown_phones_cannot_be_extended(self):
        code, _ = self.post("/api/pair/extend", {"device_id": "d_000000000000"})
        self.assertEqual(code, 400)
        self.edit(expires=int(time.time()) - 1)
        code, _ = self.post("/api/pair/extend", {"device_id": self.did})
        self.assertEqual(code, 400, "期限切れの端末を延長した")
        self.edit(expires=int(time.time()) + 3600)
        self.office.revoke_device(self.did)
        code, _ = self.post("/api/pair/extend", {"device_id": self.did})
        self.assertEqual(code, 400, "失効した端末を延長した")
        self.assertEqual(self.office.rekey_pending(), [])

    def test_extend_appears_in_office_json_for_the_phone(self):
        self.arm()
        approve = self.office.scan_office()["actions"]["approve"]
        self.assertEqual(approve.get("rk"), [self.did])
        for k in ("on", "until", "fps", "enr"):
            self.assertIn(k, approve, "approve の今の形を壊した")

    # ── 求め（req） ─────────────────────────────────────────────────
    def test_request_seals_a_fresh_secret_under_the_current_one(self):
        self.arm()
        got = self.request_next()
        self.assertRegex(got["s"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(got["s"], self.s)
        self.assertAlmostEqual(got["e"], time.time() + self.office.DEVICE_TTL, delta=5)
        rec = self.ledger()
        self.assertEqual(rec["secret"], self.s, "求めただけで今の秘密を替えた")
        self.assertEqual(rec["next"]["secret"], got["s"])

    def test_request_is_idempotent_while_the_window_is_open(self):
        self.arm()
        first = self.request_next()
        second = self.request_next()
        self.assertEqual(first["s"], second["s"], "取り直すたびに別の秘密を作った（届いた方と食い違う）")

    def test_request_without_an_open_window_is_refused(self):
        _req, env = self.env(self.s, "req")
        ok, _msg, extra = self.office._pair_rekey({"envelope": env})
        self.assertFalse(ok)
        self.assertNotIn("next", self.ledger())
        self.arm()
        self.edit(rekey_until=int(time.time()) - 1)
        _req, env = self.env(self.s, "req")
        self.assertFalse(self.office._pair_rekey({"envelope": env})[0], "窓が閉じた後に封じた")

    def test_request_signed_with_an_old_or_unknown_secret_is_refused(self):
        """求めは今の秘密で署名されたものだけ封じる（昇格の後の猶予の間の古い秘密・無関係の秘密は断る＝新しい next も作らない）。"""
        self.arm()
        nxt = self.request_next()
        _req, ack = self.env(nxt["s"], "ack")
        self.assertEqual(self.office._pair_rekey({"envelope": ack})[2]["state"], "extended")
        self.arm()                                            # もう一度［延長］＝窓は開いている
        _req, old = self.env(self.s, "req")                   # 古い秘密（prev・猶予の間）
        ok, msg, _ = self.office._pair_rekey({"envelope": old})
        self.assertFalse(ok, "古い秘密の求めで封じた")
        self.assertNotIn("next", self.ledger(), "古い秘密の求めで新しい next を作った")
        _req, bad = self.env(secrets.token_hex(32), "req")
        self.assertFalse(self.office._pair_rekey({"envelope": bad})[0])

    def test_request_signed_with_the_new_secret_promotes(self):
        """別モデルレビュー high 2: 新しい秘密を持っているのに受領が届かなかった（Mac が 300 秒より後に拾って stale-ts で捨てた）スマホは、
        新しい秘密で求めを出す＝それは新しい秘密を持つ証明なので昇格する（行き止まりにしない）。"""
        self.arm()
        nxt = self.request_next()
        late = int(time.time()) + 400                          # 受領は 300 秒の窓の外で捨てられた
        _req, stale_ack = self.env(nxt["s"], "ack", ts=late - 400)
        self.assertFalse(self.office._pair_rekey({"envelope": stale_ack}, now=late)[0], "古い受領を受けた（前提が違う）")
        self.assertIn("next", self.ledger())
        _req, req = self.env(nxt["s"], "req", ts=late)
        ok, msg, extra = self.office._pair_rekey({"envelope": req}, now=late)
        self.assertTrue(ok, msg)
        self.assertEqual(extra["state"], "extended", "新しい秘密の求めで昇格しない＝受領を失ったスマホが戻れない")
        self.assertEqual(self.ledger()["secret"], nxt["s"])

    def test_ack_is_answered_with_a_sealed_done_under_the_new_secret(self):
        """別モデルレビュー 3 巡目: スマホは状態や時刻から完了を推さない＝Mac が受領に「済み」を新しい秘密で封じて返す
        （受領の reqId に縛る・新しい期限つき）。昇格の後の二度目の受領にも「済み」・古い秘密の受領には何も返さない。"""
        self.arm()
        nxt = self.request_next()
        rid, ack = self.env(nxt["s"], "ack")
        self.assertEqual(self.office._pair_rekey({"envelope": ack})[2]["state"], "extended")
        done = self.ds.live_bundle(rid)
        self.assertIsNotNone(done, "受領に「済み」の封書を返さない（スマホが受領待ちを終えられない）")
        self.assertEqual(self.ds.unseal(nxt["s"], self.did, self.office.REKEY_TARGET, done), {"done": 1, "e": nxt["e"], "rk": 0})
        with self.assertRaises(Exception):
            self.ds.unseal(self.s, self.did, self.office.REKEY_TARGET, done)      # 古い秘密では開けない
        rid2, again = self.env(nxt["s"], "ack")
        self.assertEqual(self.office._pair_rekey({"envelope": again})[2]["state"], "done")
        self.assertIsNotNone(self.ds.live_bundle(rid2), "「済み」を受け取り損ねたスマホの二度目の受領に答えない")
        self.arm()                                                                # もう一度［延長］（前回の受領待ちが残ったスマホ）
        rid4, again2 = self.env(nxt["s"], "ack")
        self.assertEqual(self.office._pair_rekey({"envelope": again2})[2]["state"], "done")
        self.assertEqual(self.ds.unseal(nxt["s"], self.did, self.office.REKEY_TARGET, self.ds.live_bundle(rid4))["rk"], 1,
                         "新しい窓があるのに「済み」に印が無い（スマホが 5 分待ってから求める）")
        rid3, old = self.env(self.s, "ack")                                       # 古い秘密（猶予の間）の受領
        self.assertEqual(self.office._pair_rekey({"envelope": old})[2]["state"], "noop")
        self.assertIsNone(self.ds.live_bundle(rid3), "古い秘密の受領に「済み」を返した")

    def test_scope_is_kept_when_the_ack_arrives_after_the_old_expiry(self):
        """別モデルレビュー high 1: 許可鍵の期限は端末の元の期限と同じ。受領が元の期限の後に届いても、範囲つきの許可（p1 だけ）が
        全プロジェクトへ広がらない（鍵は求めの時点＝まだ有効なうちに新しい期限へ延ばす）。実時刻で元の期限を過ぎた形で見る
        （昇格の側は実時刻で鍵の期限を見る＝時刻を差し込むだけでは再現しない）。"""
        oa = self.office.office_approve
        real = int(time.time())
        exp = real - 30                                        # 端末と鍵の元の期限（いまはもう過ぎている）
        t_req = exp - 100                                      # 求めは元の期限の前に出た
        self.edit(expires=exp)
        oa._write_json(oa.keys_file(), {"version": 1, "keys": {
            self.did: {"pub": "x", "credId": "c", "fp": "abc", "projects": ["p1"], "expires": exp}}})
        self.assertTrue(self.office.rekey_allow(self.did, now=t_req)[0])
        req_id, req = self.env(self.s, "req", ts=t_req)
        self.assertEqual(self.office._pair_rekey({"envelope": req}, now=t_req)[2]["state"], "sealed")
        nxt = self.ledger()["next"]
        _req, ack = self.env(nxt["secret"], "ack", ts=real)    # 受領は元の期限の後（実時刻の今）に届いた
        self.assertEqual(self.office._pair_rekey({"envelope": ack}, now=real)[2]["state"], "extended")
        got = oa.approver(self.did, self.office.load_devices(), now=real + 1)
        self.assertEqual(got.get("projects"), ["p1"], f"延長の後に許可の範囲が広がった: {got}")

    def test_prune_keeps_a_phone_whose_new_secret_is_still_valid(self):
        """別モデルレビュー medium: 受領を送れないまま元の期限から 7 日を過ぎても、新しい秘密が生きている端末は台帳から消さない。"""
        now = int(time.time())
        self.edit(expires=now - self.office.DEVICE_KEEP_DEAD - 3600,
                  next={"secret": "ab" * 32, "expires": now + 86400, "created": now - 9 * 86400})
        self.office.prune_devices()
        self.assertIn(self.did, self.office.load_devices()["devices"], "新しい秘密が生きている端末を消した")
        self.edit(next={"secret": "ab" * 32, "expires": now - self.office.DEVICE_KEEP_DEAD - 60, "created": now - 40 * 86400})
        self.office.prune_devices()
        self.assertNotIn(self.did, self.office.load_devices()["devices"], "両方切れて 7 日たった端末が残った")

    def test_malformed_rekey_bodies_are_refused(self):
        self.arm()
        req_id = secrets.token_hex(16)
        for body in ({"aioffice": 1, "kind": "rekey", "step": "req", "reqId": "0" * 32},   # reqId と宛先の不一致
                     {"aioffice": 1, "kind": "approve", "step": "req", "reqId": req_id},
                     {"aioffice": 1, "kind": "rekey", "step": "go", "reqId": req_id}):
            env = self.office.sign_envelope(self.s, self.did, f"rky-{req_id}", json.dumps(body), int(time.time()),
                                            secrets.token_hex(16))
            self.assertFalse(self.office._pair_rekey({"envelope": env})[0], body)
        env = self.office.sign_envelope(self.s, self.did, f"dlg-{req_id}",
                                        json.dumps({"aioffice": 1, "kind": "rekey", "step": "req", "reqId": req_id}),
                                        int(time.time()), secrets.token_hex(16))
        self.assertFalse(self.office._pair_rekey({"envelope": env})[0], "rky- 以外の宛先を受けた")
        self.assertNotIn("next", self.ledger())

    # ── 受領（ack）と昇格 ─────────────────────────────────────────────
    def test_ack_with_the_new_secret_promotes_and_keeps_the_old_one_briefly(self):
        self.arm()
        nxt = self.request_next()
        old_instruct = self.instruct(self.s)
        _req, ack = self.env(nxt["s"], "ack")
        ok, msg, extra = self.office._pair_rekey({"envelope": ack})
        self.assertTrue(ok, msg)
        self.assertEqual(extra["state"], "extended")
        rec = self.ledger()
        self.assertEqual(rec["secret"], nxt["s"])
        self.assertEqual(rec["expires"], nxt["e"])
        self.assertEqual(rec["prev"]["secret"], self.s)
        self.assertNotIn("next", rec)
        self.assertNotIn("rekey_until", rec)
        self.assertEqual(self.office.rekey_pending(), [])
        self.assertIn("延長", self.notify.read_text(encoding="utf-8"))
        # 昇格の前に古い秘密で出た封筒は猶予の間だけ受ける・猶予を過ぎたら受けない
        self.assertTrue(self.accepts(old_instruct), "昇格の直前に出た指示を取りこぼした")
        late = int(time.time()) + self.office.PREV_GRACE + 1
        self.assertFalse(self.accepts(self.instruct(self.s, ts=late), now=late), "古い秘密を猶予の後も受けた")
        self.assertTrue(self.accepts(self.instruct(nxt["s"], ts=late), now=late))
        # 二度目の受領は「済み」だけ返す（台帳は替えない＝冪等）
        before = self.ledger()
        _req, again = self.env(nxt["s"], "ack")
        self.assertEqual(self.office._pair_rekey({"envelope": again})[2]["state"], "done")
        self.assertEqual(self.ledger(), before)

    def test_ack_signed_with_the_current_secret_does_not_promote(self):
        self.arm()
        nxt = self.request_next()
        _req, ack = self.env(self.s, "ack")
        ok, _msg, extra = self.office._pair_rekey({"envelope": ack})
        self.assertEqual(extra["state"], "noop", "今の秘密の受領で昇格した（新しい秘密を持つ証明になっていない）")
        self.assertIsNone(self.ds.live_bundle(_req), "受け渡し中なのに今の秘密の受領へ「済み」を返した")
        rec = self.ledger()
        self.assertEqual(rec["secret"], self.s)
        self.assertEqual(rec["next"]["secret"], nxt["s"])

    def test_envelopes_signed_with_the_new_secret_are_accepted_before_the_ack(self):
        """スマホは受領を中継に預けたら新しい秘密へ替える＝受領の処理より先に届いた指示も落とさない。期限は next の期限。"""
        self.arm()
        nxt = self.request_next()
        self.assertTrue(self.accepts(self.instruct(nxt["s"])))
        far = nxt["e"] + 1
        self.assertFalse(self.accepts(self.instruct(nxt["s"], ts=far), now=far))
        # 今の秘密が切れた後でも、受け取っていた新しい秘密の受領で昇格できる（遅れて届いた受領）
        self.edit(expires=int(time.time()) - 1)
        _req, ack = self.env(nxt["s"], "ack")
        self.assertEqual(self.office._pair_rekey({"envelope": ack})[2]["state"], "extended")

    def test_revoke_drops_the_pending_secrets_and_blocks_every_key(self):
        self.arm()
        nxt = self.request_next()
        self.office.revoke_device(self.did)
        rec = self.ledger()
        for slot in ("next", "prev", "rekey_until"):
            self.assertNotIn(slot, rec)
        self.assertFalse(self.accepts(self.instruct(self.s)))
        self.assertFalse(self.accepts(self.instruct(nxt["s"])))
        _req, ack = self.env(nxt["s"], "ack")
        self.assertFalse(self.office._pair_rekey({"envelope": ack})[0])

    def test_prune_drops_stale_slots_and_keeps_live_ones(self):
        now = int(time.time())
        self.edit(rekey_until=now - 1, next={"secret": "ab" * 32, "expires": now - 1, "created": now - 99},
                  prev={"secret": "cd" * 32, "until": now - 1})
        self.office.prune_devices()
        rec = self.ledger()
        for slot in ("rekey_until", "next", "prev"):
            self.assertNotIn(slot, rec, f"切れた {slot} が残った（秘密を必要以上に持つ）")
        self.edit(rekey_until=now + 60, next={"secret": "ab" * 32, "expires": now + 60, "created": now},
                  prev={"secret": "cd" * 32, "until": now + 60})
        self.office.prune_devices()
        rec = self.ledger()
        for slot in ("rekey_until", "next", "prev"):
            self.assertIn(slot, rec, f"生きている {slot} を消した")
        self.edit(next="壊れた")
        self.office.prune_devices()
        self.assertNotIn("next", self.ledger())
        self.assertTrue(self.accepts(self.instruct(self.s)), "壊れた next が今の秘密の検証を巻き込んだ")

    def test_list_devices_shows_the_progress(self):
        state = lambda: next(x for x in self.office.list_devices() if x["device_id"] == self.did)["rekey"]
        self.assertEqual(state(), "")
        self.arm()
        self.assertEqual(state(), "wait")
        nxt = self.request_next()
        self.assertEqual(state(), "pending")
        _req, ack = self.env(nxt["s"], "ack")
        self.office._pair_rekey({"envelope": ack})
        self.assertEqual(state(), "")
        listed = json.dumps(self.office.list_devices())
        self.assertNotIn(nxt["s"], listed, "一覧に秘密が出た")
        self.assertNotIn(self.s, listed)

    def test_the_approve_key_follows_the_extension(self):
        oa = self.office.office_approve
        now = int(time.time())
        oa._write_json(oa.keys_file(), {"version": 1, "keys": {
            self.did: {"pub": "x", "credId": "c", "fp": "abc", "projects": ["p1"], "expires": now + 3600}}})
        self.arm()
        nxt = self.request_next()
        _req, ack = self.env(nxt["s"], "ack")
        self.office._pair_rekey({"envelope": ack})
        self.assertEqual(oa.load_keys()["keys"][self.did]["expires"], nxt["e"],
                         "許可鍵が先に切れる＝範囲つきの許可が全プロジェクトへ黙って広がる")
        # 失効した鍵は延長で生き返らせない
        oa.revoke_key(self.did)
        self.assertFalse(oa.extend_key(self.did, now + 99999))
        self.assertTrue(oa.load_keys()["keys"][self.did]["revoked"])

    def test_a_phone_holding_a_live_new_secret_stays_active_and_revocable(self):
        """別モデルレビュー 2 巡目 medium: 受領が届かないまま元の期限を過ぎても、新しい秘密（next）が生きている間はその署名で
        指示を送れる＝一覧は有効（受け取り待ち）のまま・画面から失効できる・［延長］で窓を開けば新しい秘密の求めで昇格できる。"""
        self.arm()
        nxt = self.request_next()
        self.edit(expires=int(time.time()) - 10, rekey_until=int(time.time()) - 5)   # 元の期限も窓も過ぎた
        row = next(x for x in self.office.list_devices() if x["device_id"] == self.did)
        self.assertEqual((row["state"], row["rekey"]), ("active", "pending"), f"まだ指示を送れる端末が有効に見えない: {row}")
        self.assertGreater(row["daysLeft"], 25)
        code, out = self.post("/api/pair/extend", {"device_id": self.did})
        self.assertEqual(code, 200, f"新しい秘密が生きている端末を延長できない（行き止まり）: {out}")
        _req, req = self.env(nxt["s"], "req")
        self.assertEqual(self.office._pair_rekey({"envelope": req})[2]["state"], "extended")
        self.office.revoke_device(self.did)
        self.assertEqual(next(x for x in self.office.list_devices() if x["device_id"] == self.did)["state"], "revoked")

    # ── relay_agent から回る受け口 ───────────────────────────────────
    def test_rekey_endpoint_is_loopback_only_and_takes_the_envelope(self):
        self.arm()
        req_id, env = self.env(self.s, "req")
        code, _ = self.post("/api/pair/rekey", {"envelope": env}, origin=False, client="10.0.0.2")
        self.assertEqual(code, 403)
        code, out = self.post("/api/pair/rekey", {"envelope": env}, origin=False)
        self.assertEqual(code, 200, out)
        self.assertIsNotNone(self.ds.live_bundle(req_id))
        self.assertNotIn(self.ledger()["next"]["secret"], json.dumps(out), "応答に新しい秘密が出た（中継へ平文で載る）")


if __name__ == "__main__":
    unittest.main()
