#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R100-1b: スマホから許可の端から端まで（relay_e2e.sh から呼ぶ・wrangler dev の中継が要る）。

実 daemon（一時 HOME・:4794）× 実 hook（hooks/office-approval-wait.sh）× 中継 × relay_agent --once × パスキー役
（tests/fake_authn.py＝実機の navigator.credentials と同じ形の値を P-256 で作る）:
  1. ON（Origin 必須）→ 許可鍵の登録チケット（Mac の受付）→ enr- 封筒 → 台帳に公開鍵・fp が office_json に出る
  2. hook が掲示 → スマホ役が kind:"ask" の封書を要求 → 端末秘密で開ける（inputCanon・inputSha）
  3. 画面の文字列から SHA-256 を作り直して署名 → apv- 封筒 → daemon の 14 段 → hook の標準出力が {"behavior":"allow"}
  4. R103-1: rm -rf（level mac・判定済みの理由）は全文を封じて届き、正しい署名で daemon が mac のまま受理 → hook が allow。
     判定できない形（eval＝UNJUDGED）は全文なし・署名しても拒否
  R114→R118（本人裁定 2026-10-13「Claude のスマホアプリと同じく、サクッと許可」）: mode:"tap"＝WebAuthn の 4 項目が無く、端末の
     ペアリング鍵の HMAC 封筒だけの許可を、段（remote・review・mac）を問わず daemon が受理 → 本物の hook が allow（監査に mode=tap）／
     掲示の時点で mac なら決定の瞬間に remote へ下がっていても通る（段は上がっていない）／掲示の後に危険な段へ上がった 1 件は
     daemon が仮に通しても hook が自分の再計算で deny（dangerous＝多重防御）／OFF の間は off（1 件を焼かない）／
     許可鍵の無い端末（ペアリングだけ）も、その端末へ封じた 1 件なら通る（別の端末へ封じた 1 件は not-sealed）
  5. R103-1→R118: スマホの OFF（rst-・封筒だけ）→ 封じない（off）→ スマホの ON（rst-・封筒だけ）→ 期限なし
  6. apv-/enr-/rst- の孤児 inbox は生えない
使い方: python3 tests/apv_e2e.py <relay_base> <relay_token> <relay_port>
"""
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tests"))
B, TOKEN, RPORT = sys.argv[1], sys.argv[2], sys.argv[3]
PORT = 4794
HOME = Path(tempfile.mkdtemp(prefix="apv-e2e-"))
os.environ["OFFICE_HOME"] = str(HOME)
(HOME / ".claude").mkdir(parents=True)
(HOME / "office_config.json").write_text('{"projects": {}}\n')
# rpId と origin は Mac の office_relay.json の url から（WebAuthn は IP を rpId にできない＝localhost で）
(HOME / ".claude" / "office_relay.json").write_text(json.dumps({"url": f"http://localhost:{RPORT}", "token": TOKEN}))
import dialog_seal  # noqa: E402
import office_approve  # noqa: E402
import office_server as o  # noqa: E402
from fake_authn import FakeAuthenticator  # noqa: E402

NG = 0
dev = o.new_device("e2e-apv")
DID, SEC = dev["device_id"], dev["secret"]
RP = ("localhost", f"http://localhost:{RPORT}")
auth = FakeAuthenticator(*RP)
ENV = dict(os.environ, OFFICE_HOME=str(HOME), OFFICE_CONFIG=str(HOME / "office_config.json"),
           OFFICE_FAKE_CONFIRM="ok", OFFICE_FAKE_NOTIFY=str(HOME / "notify.txt"))
daemon = subprocess.Popen([sys.executable, str(ROOT / "server/office_server.py"), "--port", str(PORT)],
                          env=ENV, stdout=open(HOME / "daemon.log", "w"), stderr=subprocess.STDOUT)
hooks = []


def ok(msg):
    print(f"  ✓ {msg}")


def ng(msg):
    global NG
    NG += 1
    print(f"  ✗ {msg}")


def local(path, body=None, origin=True):
    h = {"X-Office-Local": "1", "Content-Type": "application/json"}
    if origin:
        h["Origin"] = f"http://127.0.0.1:{PORT}"
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers=h, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def instruct(session, payload, ts=None, did=None, sec=None):
    env = o.sign_envelope(sec or SEC, did or DID, session, json.dumps(payload, separators=(",", ":")),
                          int(ts or time.time()), secrets.token_hex(16))
    req = urllib.request.Request(f"{B}/instruct", data=json.dumps(env).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def relay_once():
    subprocess.run([sys.executable, str(ROOT / "server/relay_agent.py"), "--once"], timeout=60,
                   env=dict(ENV, RELAY_URL=B, RELAY_TOKEN=TOKEN, OFFICE_PORT=str(PORT)),
                   stdout=open(HOME / "relay.log", "a"), stderr=subprocess.STDOUT)


def status_office():
    req = urllib.request.Request(f"{B}/status", headers={"Authorization": f"Bearer {TOKEN}"})
    d = json.loads(urllib.request.urlopen(req, timeout=10).read())
    return (json.loads(d["json"]) if isinstance(d.get("json"), str) else d), d


def start_hook(session, command, cwd):
    payload = {"session_id": session, "cwd": cwd, "hook_event_name": "PermissionRequest", "tool_name": "Bash",
               "tool_input": {"command": command, "description": "E2E"}, "transcript_path": "/nonexistent.jsonl"}
    p = subprocess.Popen(["bash", str(ROOT / "hooks/office-approval-wait.sh")], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                         env=dict(ENV, OFFICE_APPROVAL_WAIT="90", OFFICE_APPROVAL_POLL="0.2"))
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    p.stdin = None
    hooks.append(p)
    appr = HOME / ".claude" / "office_approvals"
    for _ in range(80):
        found = [f for f in appr.glob(f"{session}.*.json") if not f.name.endswith(".reply.json")] if appr.is_dir() else []
        if found:
            return p, json.loads(found[0].read_text())
        time.sleep(0.1)
    raise AssertionError("掲示が出ない")


def sealed_ask(session, did=None, sec=None):
    rid = secrets.token_hex(16)
    instruct(f"dlg-{rid}", {"aioffice": 1, "kind": "ask", "reqId": rid, "session": session}, did=did, sec=sec)
    for _ in range(4):
        relay_once()
        office, raw = status_office()
        for it in (json.loads(raw.get("dlg") or "{}").get("items") if isinstance(raw.get("dlg"), str) else
                   (raw.get("dlg") or {}).get("items", [])) or []:
            if it.get("id") == rid:
                if it.get("err"):
                    return rid, {"err": it["err"]}
                return rid, dialog_seal.unseal(sec or SEC, did or DID, session, it)
        time.sleep(0.5)
    return rid, None


def approve(page, session):
    rid = secrets.token_hex(16)
    sha = office_approve.approval_policy.input_sha(page["inputCanon"])
    chal = office_approve.approve_challenge(DID, session, page["askId"], page["tool"], sha, page["iat"], "allow", rid)
    got = auth.get(chal)
    instruct(f"apv-{rid}", {"aioffice": 1, "kind": "approve", "reqId": rid, "session": session, "askId": page["askId"],
                            "tool": page["tool"], "inputSha": sha, "iat": page["iat"], "decision": "allow", **got})
    relay_once()
    return result_of(rid)


def result_of(rid):
    for _ in range(30):                          # office_json は数秒キャッシュされる＝結果が出るまで待つ
        local_rs = [r for r in local("/api/office")[1]["actions"]["results"] if r.get("reqId") == rid]
        if local_rs:
            return local_rs[0]
        time.sleep(0.5)
    return {}


def tap(page, session, did=None, sec=None):
    """R114: ON の間のタップ（PWA の R114-P が送る形）。WebAuthn の 4 項目（credId・authData・clientDataJSON・signature）は
    無く、真正性は封筒（端末のペアリング鍵の HMAC）だけ。(公開の結果, daemon の結果台帳の 1 行＝state・reason・mode)。"""
    rid = secrets.token_hex(16)
    sha = office_approve.approval_policy.input_sha(page["inputCanon"])
    instruct(f"apv-{rid}", {"aioffice": 1, "kind": "approve", "mode": "tap", "reqId": rid, "session": session,
                            "askId": page["askId"], "tool": page["tool"], "inputSha": sha, "iat": page["iat"],
                            "decision": "allow"}, did=did, sec=sec)
    relay_once()
    res = result_of(rid)
    try:
        row = json.loads(office_approve.state_file().read_text()).get("results", {}).get(rid) or {}
    except (OSError, ValueError):
        row = {}
    return res, row


def hook_out(p, timeout=30):
    """hook の標準出力の決定（{"behavior": ...}）。出力が無ければ None。"""
    try:
        out, _err = p.communicate(timeout=timeout)
        return json.loads(out)["hookSpecificOutput"]["decision"] if out.strip() else None
    except Exception as e:  # noqa: BLE001
        return f"hook error {e}"


def hook_decides(ask_id):
    """hook の監査（office_approvals_hook.jsonl）の decide 行のうち、その 1 件のもの。"""
    p = office_approve.hook_audit_file()
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    return [r for r in rows if r.get("ev") == "decide" and r.get("askId") == ask_id]


def daemon_audit(req_id):
    """daemon の監査（office_approvals_audit.jsonl）の approve 行のうち、その reqId のもの。"""
    p = office_approve.audit_file()
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    return [r for r in rows if r.get("kind") == "approve" and req_id and r.get("reqId") == req_id]


def still_waiting(p, session, ask_id):
    """hook に allow が渡っていない: 回答のファイルが無く・hook は生きて待っていて・決定の監査も無い。"""
    time.sleep(1.0)                              # hook は 0.2 秒ごとに回答を見る（OFFICE_APPROVAL_POLL）
    reply = HOME / ".claude" / "office_approvals" / f"{session}.{ask_id}.reply.json"
    return p.poll() is None and not reply.exists() and not hook_decides(ask_id)


def stop_hook(p):
    try:
        os.killpg(os.getpgid(p.pid), 15)
    except (ProcessLookupError, PermissionError):
        pass


try:
    for _ in range(40):
        try:
            if local("/api/office")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.3)
    # 1) ON（Origin 無しは 403・R118 から期限なし＝古い画面が時間を送っても読まない）
    c1, _ = local("/api/config/remote_approve", {"on": True}, origin=False)
    c3, d3 = local("/api/config/remote_approve", {"on": True, "hours": 2})
    if (c1, c3) == (403, 200) and d3.get("until") == office_approve.REMOTE_ALWAYS:
        ok("R100-1b→R118 ON は画面の操作だけ（Origin 無し 403）・期限なし（時間は読まない）")
    else:
        ng(f"R118 ON の門が違う: {(c1, c3, d3)}")
    # 2) 許可鍵の登録
    c, t = local("/api/approve/enroll_ticket", {"device": DID, "projects": "*"})
    ticket = t.get("ticket", "")
    got = auth.create(office_approve.enroll_challenge(DID, ticket))
    instruct(f"enr-{ticket}", {"aioffice": 1, "kind": "enroll", "ticket": ticket, **got})
    relay_once()
    relay_once()
    office, _raw = status_office()
    fps = ((office.get("actions") or {}).get("approve") or {}).get("fps", [])
    caps = ((office.get("actions") or {}).get("caps") or {}).get("approve")
    if c == 200 and auth.fp() in fps and caps == 1:
        ok(f"R100-1b 許可鍵の登録（Mac の受付 → enr- → 台帳・R103-1 で確認ダイアログは無し）・照合コード {auth.fp()} と caps.approve=1 が中継に出る")
    else:
        ng(f"R100-1b 登録が通らない: code={c} fps={fps} caps={caps} log={(HOME / 'daemon.log').read_text()[-300:]}")
    keys_raw = (HOME / ".claude" / "office_approve_keys.json").read_text()
    if oct(os.stat(HOME / ".claude" / "office_approve_keys.json").st_mode & 0o777) == "0o600" and "secret" not in keys_raw:
        ok("R100-1b 許可鍵の台帳は 0600・公開鍵だけ")
    else:
        ng("R100-1b 許可鍵の台帳が 0600 でない／秘密を持っている")
    # 3) 実 hook の掲示 → 封書 → 許可 → hook が allow
    cwd = tempfile.mkdtemp(prefix="apv-proj-")
    session = "e2e-apv-" + secrets.token_hex(4)
    hook, rec = start_hook(session, "npm test", cwd)
    office, _raw = status_office() if False else (None, None)
    rid, page = sealed_ask(session)
    if page and page.get("inputCanon") == rec.get("inputCanon") and page.get("level") == "remote":
        ok("R100-1b 許可の全文は端末の鍵で封じて届く（開けた inputCanon が掲示と同じ・level=remote）")
    else:
        ng(f"R100-1b 封書が開けない／中身が違う: {page}")
    res = approve(page, session) if page and "inputCanon" in page else {}
    try:
        out, _err = hook.communicate(timeout=30)
        dec = json.loads(out)["hookSpecificOutput"]["decision"] if out.strip() else None
    except Exception as e:  # noqa: BLE001
        dec = f"hook error {e}"
    if res.get("state") == "granted" and dec == {"behavior": "allow"}:
        ok("R100-1b スマホ→中継→relay_agent→daemon の 14 段→**hook が allow**（message も updatedInput も無し）")
    else:
        ng(f"R100-1b 許可が通らない: result={res} hook={dec} daemon={(HOME / 'daemon.log').read_text()[-300:]}")
    # 4) R103-1（本人裁定「Mac だけの操作も全文確認＋Face ID」）: rm -rf は level mac のまま全文を封じて届き（macOnly=0）、
    #    画面の文字列から作った署名で daemon の 14 段が mac のまま受理 → hook が allow
    time.sleep(office_approve.RATE_GAP + 0.5)           # 端末ごと 5 秒に 1 件
    s2 = "e2e-apv-" + secrets.token_hex(4)
    hook2, rec2 = start_hook(s2, "rm -rf build", cwd)
    _rid, page2 = sealed_ask(s2)
    res2 = approve(page2, s2) if page2 and page2.get("inputCanon") else {"output": f"sealed={page2}"}
    try:
        out2, _err = hook2.communicate(timeout=30)
        dec2 = json.loads(out2)["hookSpecificOutput"]["decision"] if out2.strip() else None
    except Exception as e:  # noqa: BLE001
        dec2 = f"hook error {e}"
    if (rec2.get("level") == "mac" and rec2.get("inputCanon") and (page2 or {}).get("level") == "mac"
            and (page2 or {}).get("macOnly") == 0 and (page2 or {}).get("inputCanon") == rec2.get("inputCanon")
            and res2.get("state") == "granted" and dec2 == {"behavior": "allow"}):
        ok("R103-1 rm -rf（Mac だけだった段）も全文を封じて届き、スマホの署名で daemon が mac のまま受理 → hook が allow")
    else:
        ng(f"R103-1 level mac がスマホから通らない: level={rec2.get('level')} page={page2} res={res2} hook={dec2}")
    # 4b) 判定できない形（eval＝UNJUDGED）は全文を作らない（macOnly=1）・入力から作った正しい署名を送っても Mac が拒否する
    time.sleep(office_approve.RATE_GAP + 0.5)
    s4 = "e2e-apv-" + secrets.token_hex(4)
    hook4, rec4 = start_hook(s4, 'eval "$X"', cwd)
    _rid, page4 = sealed_ask(s4)
    forged = dict(page4 or {}, inputCanon=office_approve.approval_policy.input_canon(
        "Bash", {"command": 'eval "$X"', "description": "E2E"}), tool="Bash")
    res4 = approve(forged, s4) if page4 and page4.get("askId") else {"output": f"sealed={page4}"}
    if (rec4.get("level") == "mac" and "inputCanon" not in rec4 and (page4 or {}).get("macOnly") == 1
            and not (page4 or {}).get("inputCanon") and res4.get("state") == "refused"
            and res4.get("output") in ("ask-mismatch", "dangerous")):
        ok(f"R103-1 判定できない形（eval）は Mac だけ（全文なし・正しく署名しても {res4.get('output')} で拒否）")
    else:
        ng(f"R103-1 判定できない形が通った／理由が違う: level={rec4.get('level')} page={page4} res={res4}")
    # 4c) R114→R118: mode:"tap"＝WebAuthn の 4 項目が無く、端末のペアリング鍵の HMAC 封筒だけの許可。daemon は WebAuthn の段だけ
    #    省き、他の段は全部掛ける・段（remote・review・mac）は問わない。hook は自分の再計算（段が上がっていない・判定できる形）で通す
    time.sleep(office_approve.RATE_GAP + 0.5)           # 直前の許可（4）から端末ごと 5 秒
    for cmd, want in (("npm test", "remote"), ("bash verify.sh", "review")):
        st = "e2e-apv-" + secrets.token_hex(4)
        hk, rc = start_hook(st, cmd, cwd)
        _rid, pg = sealed_ask(st)
        res, row = tap(pg, st) if pg and pg.get("inputCanon") else ({"output": f"sealed={pg}"}, {})
        dec = hook_out(hk)
        aud = (hook_decides(rc.get("askId")) or [{}])[-1]
        dau = (daemon_audit(res.get("reqId")) or [{}])[-1]
        if (rc.get("level") == want and (pg or {}).get("level") == want and res.get("state") == "granted"
                and (row.get("state"), row.get("mode")) == ("granted", "tap")
                and (dau.get("state"), dau.get("mode"), dau.get("level")) == ("granted", "tap", want)
                and dec == {"behavior": "allow"}
                and (aud.get("src"), aud.get("decision"), aud.get("mode")) == ("remote", "allow", "tap")):
            ok(f"R114 タップ（WebAuthn 無し・封筒の HMAC だけ）で {want} の段（{cmd}）: 中継→relay_agent→daemon が受理"
               "（結果台帳・daemon の監査に mode=tap）→**hook が allow**（監査 decide に src=remote・mode=tap）")
        else:
            ng(f"R114 タップで {want} が通らない: level={rc.get('level')} page={pg} res={res} row={row} daemon={dau} "
               f"hook={dec} audit={aud}")
        time.sleep(office_approve.RATE_GAP + 0.5)
    # mac（rm -rf・判定済みの理由）: R118 からタップで daemon が受理 → 本物の hook が allow（Face ID 無し）
    sm = "e2e-apv-" + secrets.token_hex(4)
    hm, rm_ = start_hook(sm, "rm -rf build", cwd)
    _rid, pm = sealed_ask(sm)
    res, row = tap(pm, sm) if pm and pm.get("inputCanon") else ({"output": f"sealed={pm}"}, {})
    dec = hook_out(hm)
    aud = (hook_decides(rm_.get("askId")) or [{}])[-1]
    dau = (daemon_audit(res.get("reqId")) or [{}])[-1]
    if (rm_.get("level") == "mac" and (pm or {}).get("level") == "mac" and (pm or {}).get("macOnly") == 0
            and res.get("state") == "granted" and (row.get("state"), row.get("mode")) == ("granted", "tap")
            and (dau.get("state"), dau.get("mode"), dau.get("level")) == ("granted", "tap", "mac")
            and dec == {"behavior": "allow"}
            and (aud.get("src"), aud.get("decision"), aud.get("mode")) == ("remote", "allow", "tap")):
        ok("R118 mac の段（rm -rf）もタップ 1 回で daemon が受理 → **本物の hook が allow**（監査に mode=tap・Face ID 無し）")
    else:
        ng(f"R118 mac の段がタップで通らない: level={rm_.get('level')} page={pm} res={res} row={row} daemon={dau} "
           f"hook={dec} audit={aud}")
    # 掲示の時点で mac（秘密へのリンク）と見せた 1 件が、決定の瞬間に remote へ下がった＝段は上がっていない＝タップで通る
    time.sleep(office_approve.RATE_GAP + 0.5)           # 直前の許可から 5 秒
    cwd2 = tempfile.mkdtemp(prefix="apv-proj-")
    secret = HOME / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("k")
    note = Path(cwd2) / "notes.txt"
    os.symlink(secret, note)
    sl = "e2e-apv-" + secrets.token_hex(4)
    hl, rl = start_hook(sl, "cat notes.txt", cwd2)
    _rid, pl = sealed_ask(sl)
    note.unlink()
    note.write_text("x")                                # 決定の瞬間の再判定は remote
    now_lv = office_approve.approval_policy.classify("Bash", {"command": "cat notes.txt", "description": "E2E"},
                                                     cwd2, str(HOME)).get("level")
    res, row = tap(pl, sl) if pl and pl.get("inputCanon") else ({"output": f"sealed={pl}"}, {})
    dec = hook_out(hl)
    if (rl.get("level") == "mac" and (pl or {}).get("level") == "mac" and now_lv == "remote"
            and res.get("state") == "granted" and row.get("mode") == "tap" and dec == {"behavior": "allow"}):
        ok("R118 掲示の時点で mac（秘密へのリンク）→ 決定の瞬間に remote へ下がった 1 件はタップで通る（段は上がっていない・hook が allow）")
    else:
        ng(f"R118 掲示の段が mac で下がった 1 件がタップで通らない: level={rl.get('level')} page={pl} now={now_lv} res={res} "
           f"row={row} hook={dec}")
    # 多重防御: daemon が仮に通しても（daemon と同じ write_approval_reply で mode:"tap" の回答を書く）、掲示の後に危険な段へ
    # 上がった 1 件（ふつうのファイル → 秘密へのリンクにすり替え）は hook が自分の再計算で deny（dangerous）
    cwd3 = tempfile.mkdtemp(prefix="apv-proj-")
    note3 = Path(cwd3) / "notes.txt"
    note3.write_text("x")
    sx = "e2e-apv-" + secrets.token_hex(4)
    hx, rx = start_hook(sx, "cat notes.txt", cwd3)
    note3.unlink()
    os.symlink(secret, note3)                           # 掲示の後に秘密へのリンクへすり替え＝決定の瞬間は mac
    o.write_approval_reply(sx, "allow", src="remote", ask_id=rx.get("askId"),
                           extra={"inputSha": rx.get("inputSha"), "device": DID, "reqId": secrets.token_hex(16),
                                  "mode": "tap"})
    dec = hook_out(hx)
    aud = (hook_decides(rx.get("askId")) or [{}])[-1]
    if (rx.get("level") == "remote" and isinstance(dec, dict) and dec.get("behavior") == "deny"
            and "dangerous" in str(dec.get("message")) and (aud.get("reason"), aud.get("mode")) == ("dangerous", "tap")):
        ok("R118 掲示の後に危険な段へ上がった 1 件は、daemon が仮にタップで通しても hook が自分の再計算で deny（dangerous・多重防御）")
    else:
        ng(f"R118 hook がすり替えた 1 件を通した: level={rx.get('level')} hook={dec} audit={aud}")
    # OFF の間のタップは off（1 件を焼かない）・許可鍵の無い端末（ペアリングだけ）も、その端末へ封じた 1 件なら通る
    sf = "e2e-apv-" + secrets.token_hex(4)
    hf, rf = start_hook(sf, "git status", cwd)
    _rid, pf = sealed_ask(sf)                           # ON の間に封じた 1 件
    c_off, _ = local("/api/config/remote_approve", {"on": False})
    res_off, row_off = tap(pf, sf) if pf and pf.get("inputCanon") else ({"output": f"sealed={pf}"}, {})
    c_on, _ = local("/api/config/remote_approve", {"on": True})
    if (c_off, c_on) == (200, 200) and (res_off.get("state"), res_off.get("output")) == ("refused", "off") \
            and (row_off.get("reason"), row_off.get("mode")) == ("off", "tap"):
        ok("R114 ON が切れている間のタップは off（封じた 1 件・封筒も正しくても）")
    else:
        ng(f"R114 OFF の間のタップが off にならない: codes={(c_off, c_on)} res={res_off} row={row_off}")
    dev2 = o.new_device("e2e-apv-nokey")
    res_ne, row_ne = tap(pf, sf, did=dev2["device_id"], sec=dev2["secret"]) if pf and pf.get("inputCanon") else ({}, {})
    waiting = still_waiting(hf, sf, rf.get("askId"))
    if (res_ne.get("state"), res_ne.get("output")) == ("refused", "not-sealed") \
            and (row_ne.get("reason"), row_ne.get("mode")) == ("not-sealed", "tap") and waiting:
        ok("R118 ペアリングだけの端末でも、その端末へ封じていない 1 件のタップは not-sealed（封じた 1 回に縛る）・hook に allow は渡らない")
    else:
        ng(f"R118 別の端末へ封じた 1 件のタップが not-sealed にならない: res={res_ne} row={row_ne} waiting={waiting}")
    time.sleep(office_approve.RATE_GAP + 0.5)
    _rid, pf2 = sealed_ask(sf, did=dev2["device_id"], sec=dev2["secret"])
    res_ok, row_ok = (tap(pf2, sf, did=dev2["device_id"], sec=dev2["secret"]) if pf2 and pf2.get("inputCanon")
                      else ({"output": f"sealed={pf2}"}, {}))
    dec = hook_out(hf)
    if res_ok.get("state") == "granted" and row_ok.get("mode") == "tap" and dec == {"behavior": "allow"}:
        ok("R118 許可鍵の無い端末（ペアリングだけ）も、その端末へ封じた 1 件はタップで通る（hook が allow）・off で断られた 1 件も ON に戻せば通る")
    else:
        ng(f"R118 ペアリングだけの端末のタップが通らない: page={pf2} res={res_ok} row={row_ok} hook={dec}")
    # 5) R103-1→R118: スマホの OFF（rst-・封筒の署名だけ）→ 中継 → daemon で OFF → 全文を封じない（err=off）→
    #    スマホの ON（rst-・封筒だけ・パスキー不要）→ 期限なし
    rq = secrets.token_hex(16)
    instruct(f"rst-{rq}", {"aioffice": 1, "kind": "remote_set", "reqId": rq, "on": False})
    relay_once()
    off_until = office_approve.remote_until()
    s3 = "e2e-apv-" + secrets.token_hex(4)
    hook3, _rec3 = start_hook(s3, "git status", cwd)
    _rid, page3 = sealed_ask(s3)
    if off_until <= time.time() and page3 == {"err": "off"}:
        ok("R103-1 スマホの OFF（rst-）→ 中継 → daemon で OFF・その間は全文も封じない（err=off）")
    else:
        ng(f"R103-1 スマホの OFF が効かない: until={off_until} page={page3} relay={(HOME / 'relay.log').read_text()[-300:]}")
    time.sleep(office_approve.RST_GAP + 0.5)           # ON/OFF は端末ごと 10 秒に 1 回
    rq2 = secrets.token_hex(16)
    instruct(f"rst-{rq2}", {"aioffice": 1, "kind": "remote_set", "reqId": rq2, "on": True})
    relay_once()
    on_until = office_approve.remote_until()
    rs = [r for r in local("/api/office")[1]["actions"]["results"] if r.get("reqId") == rq2]
    if on_until == office_approve.REMOTE_ALWAYS and rs and rs[0].get("state") == "granted":
        ok("R118 スマホの ON（rst-・封筒だけ・パスキー不要）→ 中継 → daemon で ON（期限なし）")
    else:
        ng(f"R103-1 スマホの ON が効かない: until={on_until} results={rs} relay={(HOME / 'relay.log').read_text()[-300:]}")
    # 6) 孤児 inbox は生えない
    inbox = HOME / ".claude" / "office_inbox"
    orphans = [f.name for f in inbox.glob("*.json") if f.name.startswith(("apv-", "enr-", "dlg-", "rst-"))] if inbox.is_dir() else []
    if not orphans:
        ok("R100-1b apv-/enr-/dlg-/rst- は office_inbox に書かない（孤児 0）")
    else:
        ng(f"R100-1b 孤児 inbox: {orphans}")
finally:
    for h in hooks:
        try:
            os.killpg(os.getpgid(h.pid), 15)
        except (ProcessLookupError, PermissionError):
            pass
    daemon.terminate()
    try:
        daemon.wait(timeout=10)
    except subprocess.TimeoutExpired:
        daemon.kill()
    if not NG:
        import shutil
        shutil.rmtree(HOME, ignore_errors=True)        # 赤のときはログ（daemon.log・relay.log）を残す
sys.exit(1 if NG else 0)
