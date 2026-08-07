#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RFC 6455 WebSocket クライアント（標準ライブラリのみ・R79-8）。

relay_agent が Cloudflare Worker の /ws へ常時接続するための最小実装。
サーバー実装ではない（クライアント専用）。設計の掟:

- 純関数（accept_key / encode_frame / FrameParser）とソケット層（WSClient)を分離し、
  純関数は tests/test_ws_client.py が **RFC 6455 §1.3/§5.7 の公式ベクタでKAT固定**する。
  このリポジトリの「公式ベクタで固める」型（webpush_kat=RFC8291 / test_qr=segno golden）に従う。
- クライアント→サーバーのフレームは必ずマスクする（§5.1。裸で送るとサーバーは1002で切る）。
- PING には同一ペイロードの PONG を即返す（recv 中に自動処理・呼び出し側へは出さない）。
- CLOSE は echo してから WSClosed を上げる（握手・以後の send は WSError）。
- User-Agent 必須: 既定UAは Cloudflare のBot対策に弾かれる（relay_agent._req と同じ罠）。
"""
import base64
import hashlib
import os
import select
import socket
import ssl
import struct
import urllib.parse
from collections import deque

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"   # RFC 6455 §1.3 固定値
OP_CONT, OP_TEXT, OP_BIN = 0x0, 0x1, 0x2
OP_CLOSE, OP_PING, OP_PONG = 0x8, 0x9, 0xA
_MAX_FRAME = 16 * 1024 * 1024   # 受信フレーム上限（暴走ヘッダで無限確保しない）


class WSError(Exception):
    """接続不能・プロトコル違反・切断後の送受信。"""


class WSClosed(WSError):
    """相手が CLOSE を送ってきた／TCPが閉じた。code は 1006（異常切断）がデフォルト。"""

    def __init__(self, code=1006, reason=""):
        super().__init__(f"closed code={code} reason={reason!r}")
        self.code = code
        self.reason = reason


def accept_key(sec_key):
    """§1.3: Sec-WebSocket-Accept = base64(sha1(key + GUID))。KATベクタで固定。"""
    digest = hashlib.sha1((sec_key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(opcode, payload, fin=True, mask_key=None):
    """1フレームをバイト列へ（§5.2）。クライアント送信は mask_key(4B) 必須。"""
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")
    head = bytearray()
    head.append((0x80 if fin else 0x00) | (opcode & 0x0F))
    mask_bit = 0x80 if mask_key else 0x00
    n = len(payload)
    if n < 126:
        head.append(mask_bit | n)
    elif n < 65536:
        head.append(mask_bit | 126)
        head += struct.pack(">H", n)
    else:
        head.append(mask_bit | 127)
        head += struct.pack(">Q", n)
    if not mask_key:
        return bytes(head) + bytes(payload)
    if len(mask_key) != 4:
        raise ValueError("mask_key must be 4 bytes")
    head += mask_key
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return bytes(head) + masked


class FrameParser:
    """逐次フレームデコーダ。feed(data) が完成フレーム [(fin, opcode, payload)] を返す。
    マスク有無どちらも読める（サーバー→クライアントは非マスクが正・§5.1）。"""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            frame = self._next()
            if frame is None:
                return out
            out.append(frame)

    def _next(self):
        buf = self.buf
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        pos = 2
        if length == 126:
            if len(buf) < pos + 2:
                return None
            length = struct.unpack(">H", bytes(buf[pos:pos + 2]))[0]
            pos += 2
        elif length == 127:
            if len(buf) < pos + 8:
                return None
            length = struct.unpack(">Q", bytes(buf[pos:pos + 8]))[0]
            pos += 8
        if length > _MAX_FRAME:
            raise WSError(f"frame too large: {length}")
        mask = b""
        if masked:
            if len(buf) < pos + 4:
                return None
            mask = bytes(buf[pos:pos + 4])
            pos += 4
        if len(buf) < pos + length:
            return None
        payload = bytes(buf[pos:pos + length])
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        del buf[:pos + length]
        return (fin, opcode, payload)


class WSClient:
    """テキストWebSocketクライアント。使い方:
        ws = WSClient("wss://host/ws?role=agent", token="...")
        ws.connect()
        ws.send_text('{"t":"sync"}')
        msg = ws.recv(timeout=30)   # None=タイムアウト / str=TEXTメッセージ / WSClosed=切断
        ws.close()
    """

    def __init__(self, url, token=None, protocols=None, timeout=10.0,
                 user_agent="aioffice-relay/1.0"):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("ws", "wss"):
            raise WSError(f"bad scheme: {u.scheme}")
        self.tls = (u.scheme == "wss")
        self.host = u.hostname or ""
        self.port = u.port or (443 if self.tls else 80)
        self.path = (u.path or "/") + (("?" + u.query) if u.query else "")
        self.token = token
        self.protocols = list(protocols or [])
        self.timeout = timeout
        self.user_agent = user_agent
        self.sock = None
        self.parser = FrameParser()
        self._msgs = deque()
        self._frag_op = None
        self._frag = bytearray()
        self._closed = False
        self._close_sent = False

    # ---- 接続（HTTP/1.1 Upgrade 握手） ----
    def connect(self):
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        if self.tls:
            ctx = ssl.create_default_context()
            self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
        else:
            self.sock = raw
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        lines = [
            f"GET {self.path} HTTP/1.1",
            f"Host: {self.host}:{self.port}" if self.port not in (80, 443)
            else f"Host: {self.host}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            f"User-Agent: {self.user_agent}",
        ]
        if self.token:
            lines.append(f"Authorization: Bearer {self.token}")
        if self.protocols:
            lines.append("Sec-WebSocket-Protocol: " + ", ".join(self.protocols))
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSError("connection closed during handshake")
            head += chunk
            if len(head) > 65536:
                raise WSError("handshake response too large")
        head, _, rest = head.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 101" not in status.split("\r\n")[0]:
            raise WSError(f"handshake rejected: {status.strip()}")
        headers = {}
        for line in head.decode("latin-1", "replace").split("\r\n")[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        if headers.get("sec-websocket-accept") != accept_key(key):
            raise WSError("bad Sec-WebSocket-Accept (§1.3 KAT mismatch)")
        if rest:
            self._pump(self.parser.feed(rest))
        return self

    # ---- 送受信 ----
    def send_text(self, text):
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def _send_frame(self, opcode, payload):
        if self._closed or self.sock is None:
            raise WSError("send on closed socket")
        frame = encode_frame(opcode, payload, mask_key=os.urandom(4))
        try:
            self.sock.sendall(frame)
        except OSError as e:
            raise WSError(f"send failed: {e}") from e

    def recv(self, timeout=None):
        """次の TEXT メッセージ（str）。timeout 秒で無ければ None。
        PING は自動 PONG・CLOSE は echo して WSClosed。断片化は結合してから返す。"""
        import time as _time
        deadline = None if timeout is None else _time.monotonic() + timeout
        while True:
            if self._msgs:
                return self._msgs.popleft()
            if self._closed:
                raise WSClosed()
            remain = None if deadline is None else max(0.0, deadline - _time.monotonic())
            if not self._readable(remain):
                return None
            try:
                data = self.sock.recv(65536)
            except (ssl.SSLWantReadError, BlockingIOError):
                continue
            except OSError as e:
                self._closed = True
                raise WSClosed(1006, f"recv failed: {e}") from e
            if not data:
                self._closed = True
                raise WSClosed(1006, "tcp closed")
            self._pump(self.parser.feed(data))

    def _readable(self, remain):
        """TLSはレコードがユーザー空間に残る（selectに映らない）ので pending を先に見る。"""
        if isinstance(self.sock, ssl.SSLSocket) and self.sock.pending():
            return True
        if remain is not None and remain <= 0:
            return False
        r, _, _ = select.select([self.sock], [], [], remain)
        return bool(r)

    def _pump(self, frames):
        for fin, opcode, payload in frames:
            if opcode == OP_PING:
                # 制御フレームはデータの断片列に割り込める（§5.4）＝断片状態に触れず即応
                try:
                    self._send_frame(OP_PONG, payload)
                except WSError:
                    pass
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 1005
                if not self._close_sent:
                    try:
                        self._send_frame(OP_CLOSE, payload[:2])
                    except WSError:
                        pass
                    self._close_sent = True
                self._closed = True
                raise WSClosed(code, payload[2:].decode("utf-8", "replace"))
            if opcode in (OP_TEXT, OP_BIN):
                if fin and opcode == OP_TEXT:
                    self._msgs.append(payload.decode("utf-8", "replace"))
                elif fin:
                    continue   # BINARYは本製品のプロトコル外＝黙って捨てる
                else:
                    self._frag_op = opcode
                    self._frag = bytearray(payload)
            elif opcode == OP_CONT:
                self._frag += payload
                if fin:
                    if self._frag_op == OP_TEXT:
                        self._msgs.append(bytes(self._frag).decode("utf-8", "replace"))
                    self._frag_op = None
                    self._frag = bytearray()

    def close(self, code=1000):
        """CLOSE握手（ベストエフォート）→ ソケット破棄。二重closeは無害。"""
        if self.sock is None:
            return
        if not self._close_sent and not self._closed:
            try:
                self._send_frame(OP_CLOSE, struct.pack(">H", code))
                self._close_sent = True
                self._readable(1.0)   # echoを少しだけ待つ（来なくても畳む）
            except WSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = None
        self._closed = True
