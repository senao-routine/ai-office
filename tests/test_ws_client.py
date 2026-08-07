#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R79-8: server/ws_client.py の検証。

1) RFC 6455 の**公式ベクタKAT**（§1.3 Accept-Key・§5.7 フレーム例）＝仕様書の値と
   バイト一致で固定する（webpush_kat.mjs=RFC8291 / test_qr.py=segno golden と同じ型）。
2) ループバック実往復＝スレッドの最小RFC6455サーバー相手に、握手・エコー・
   PING自動PONG・断片化（制御フレーム割込つき）・CLOSE握手を実測する。
"""
import base64
import hashlib
import socket
import struct
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import ws_client as wsc  # noqa: E402


class TestKAT(unittest.TestCase):
    def test_accept_key_rfc6455_1_3(self):
        # §1.3 の実例そのまま（handshake KAT）
        self.assertEqual(wsc.accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
                         "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
        # 導出の自己検証（GUID連結→SHA1→base64）
        want = base64.b64encode(hashlib.sha1(
            ("x" + wsc.GUID).encode()).digest()).decode()
        self.assertEqual(wsc.accept_key("x"), want)

    def test_frame_kat_unmasked_hello(self):
        # §5.7: A single-frame unmasked text message ("Hello")
        self.assertEqual(wsc.encode_frame(wsc.OP_TEXT, b"Hello"),
                         bytes([0x81, 0x05, 0x48, 0x65, 0x6C, 0x6C, 0x6F]))

    def test_frame_kat_masked_hello(self):
        # §5.7: A single-frame masked text message ("Hello")
        self.assertEqual(
            wsc.encode_frame(wsc.OP_TEXT, b"Hello", mask_key=bytes([0x37, 0xFA, 0x21, 0x3D])),
            bytes([0x81, 0x85, 0x37, 0xFA, 0x21, 0x3D, 0x7F, 0x9F, 0x4D, 0x51, 0x58]))

    def test_frame_kat_fragmented(self):
        # §5.7: A fragmented unmasked text message ("Hel" + "lo")
        self.assertEqual(wsc.encode_frame(wsc.OP_TEXT, b"Hel", fin=False),
                         bytes([0x01, 0x03, 0x48, 0x65, 0x6C]))
        self.assertEqual(wsc.encode_frame(wsc.OP_CONT, b"lo"),
                         bytes([0x80, 0x02, 0x6C, 0x6F]))

    def test_frame_kat_ping_pong(self):
        # §5.7: Unmasked Ping / masked Pong（本文 "Hello"）
        self.assertEqual(wsc.encode_frame(wsc.OP_PING, b"Hello"),
                         bytes([0x89, 0x05, 0x48, 0x65, 0x6C, 0x6C, 0x6F]))
        self.assertEqual(
            wsc.encode_frame(wsc.OP_PONG, b"Hello", mask_key=bytes([0x37, 0xFA, 0x21, 0x3D])),
            bytes([0x8A, 0x85, 0x37, 0xFA, 0x21, 0x3D, 0x7F, 0x9F, 0x4D, 0x51, 0x58]))

    def test_frame_kat_extended_lengths(self):
        # §5.7: 256 bytes → 16bit拡張長 / 64KiB → 64bit拡張長
        f256 = wsc.encode_frame(wsc.OP_BIN, bytes(256))
        self.assertEqual(f256[:4], bytes([0x82, 0x7E, 0x01, 0x00]))
        self.assertEqual(len(f256), 4 + 256)
        f64k = wsc.encode_frame(wsc.OP_BIN, bytes(65536))
        self.assertEqual(f64k[:10],
                         bytes([0x82, 0x7F, 0, 0, 0, 0, 0, 1, 0, 0]))
        self.assertEqual(len(f64k), 10 + 65536)


class TestParser(unittest.TestCase):
    def test_unmasked_hello_bytewise(self):
        # 1バイトずつ流しても正しく組み上がる（TCP断片への耐性）
        p = wsc.FrameParser()
        frames = []
        for b in bytes([0x81, 0x05, 0x48, 0x65, 0x6C, 0x6C, 0x6F]):
            frames += p.feed(bytes([b]))
        self.assertEqual(frames, [(True, wsc.OP_TEXT, b"Hello")])

    def test_masked_roundtrip(self):
        p = wsc.FrameParser()
        raw = wsc.encode_frame(wsc.OP_TEXT, "日本語テキスト".encode("utf-8"),
                               mask_key=b"\x01\x02\x03\x04")
        frames = p.feed(raw)
        self.assertEqual(frames, [(True, wsc.OP_TEXT, "日本語テキスト".encode("utf-8"))])

    def test_extended16_roundtrip(self):
        p = wsc.FrameParser()
        payload = bytes(range(256)) * 2   # 512B → 16bit長
        frames = p.feed(wsc.encode_frame(wsc.OP_BIN, payload))
        self.assertEqual(frames, [(True, wsc.OP_BIN, payload)])

    def test_two_frames_one_feed(self):
        p = wsc.FrameParser()
        raw = wsc.encode_frame(wsc.OP_TEXT, b"a") + wsc.encode_frame(wsc.OP_TEXT, b"b")
        self.assertEqual(p.feed(raw),
                         [(True, wsc.OP_TEXT, b"a"), (True, wsc.OP_TEXT, b"b")])


class _LoopbackServer(threading.Thread):
    """最小RFC6455サーバー（テスト専用）。台本:
    - テキストはエコー
    - "do-ping" → PING("Hello") を送り、PONG("Hello") を受けたら "pong-ok"
    - "do-frag" → "He"(fin=0) → PING割込 → "llo"(CONT fin=1)
    - "do-close" → CLOSE(1000) を送り、echoのCLOSEを待って終える
    """

    def __init__(self):
        super().__init__(daemon=True)
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.errors = []
        self.got_pong = threading.Event()
        self.got_close_echo = threading.Event()

    def run(self):
        conn = None
        try:
            conn, _ = self.srv.accept()
            conn.settimeout(5)
            head = b""
            while b"\r\n\r\n" not in head:
                head += conn.recv(4096)
            key = ""
            for line in head.decode("latin-1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {wsc.accept_key(key)}\r\n\r\n").encode())
            parser = wsc.FrameParser()
            frag = bytearray()
            while True:
                data = conn.recv(65536)
                if not data:
                    return
                for fin, op, payload in parser.feed(data):
                    if op == wsc.OP_PONG:
                        if payload == b"Hello":
                            self.got_pong.set()
                            conn.sendall(wsc.encode_frame(wsc.OP_TEXT, b"pong-ok"))
                        continue
                    if op == wsc.OP_CLOSE:
                        self.got_close_echo.set()
                        return
                    if op in (wsc.OP_TEXT, wsc.OP_CONT):
                        frag += payload
                        if not fin:
                            continue
                        msg = bytes(frag).decode("utf-8")
                        frag = bytearray()
                        if msg == "do-ping":
                            conn.sendall(wsc.encode_frame(wsc.OP_PING, b"Hello"))
                        elif msg == "do-frag":
                            conn.sendall(wsc.encode_frame(wsc.OP_TEXT, b"He", fin=False))
                            conn.sendall(wsc.encode_frame(wsc.OP_PING, b"Hello"))
                            conn.sendall(wsc.encode_frame(wsc.OP_CONT, b"llo"))
                        elif msg == "do-close":
                            conn.sendall(wsc.encode_frame(
                                wsc.OP_CLOSE, struct.pack(">H", 1000)))
                        else:
                            conn.sendall(wsc.encode_frame(
                                wsc.OP_TEXT, ("echo:" + msg).encode("utf-8")))
        except Exception as e:   # noqa: BLE001 - テスト側でまとめて報告
            self.errors.append(repr(e))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass


class TestLoopback(unittest.TestCase):
    def setUp(self):
        self.server = _LoopbackServer()
        self.server.start()
        self.ws = wsc.WSClient(f"ws://127.0.0.1:{self.server.port}/ws",
                               token="t0", timeout=5).connect()

    def tearDown(self):
        self.ws.close()
        self.server.srv.close()

    def test_echo(self):
        self.ws.send_text("こんにちは")
        self.assertEqual(self.ws.recv(timeout=5), "echo:こんにちは")

    def test_ping_auto_pong(self):
        self.ws.send_text("do-ping")
        # PINGはrecv内で自動PONG＝ユーザーには "pong-ok" だけが見える
        self.assertEqual(self.ws.recv(timeout=5), "pong-ok")
        self.assertTrue(self.server.got_pong.wait(5), "サーバーがPONGを受け取れていない")

    def test_fragmented_with_control_interleave(self):
        self.ws.send_text("do-frag")
        # 断片の間にPINGが割り込んでも "Hello" が1メッセージに組み上がる（§5.4）
        self.assertEqual(self.ws.recv(timeout=5), "Hello")
        self.assertTrue(self.server.got_pong.wait(5))

    def test_close_handshake(self):
        self.ws.send_text("do-close")
        with self.assertRaises(wsc.WSClosed) as ctx:
            self.ws.recv(timeout=5)
        self.assertEqual(ctx.exception.code, 1000)
        self.assertTrue(self.server.got_close_echo.wait(5), "CLOSEのechoが返っていない")

    def test_timeout_returns_none(self):
        self.assertIsNone(self.ws.recv(timeout=0.2))

    def test_no_server_errors(self):
        self.ws.send_text("x")
        self.assertEqual(self.ws.recv(timeout=5), "echo:x")
        self.assertEqual(self.server.errors, [])


class TestHandshakeGuards(unittest.TestCase):
    def test_bad_accept_rejected(self):
        srv = socket.create_server(("127.0.0.1", 0))
        port = srv.getsockname()[1]

        def bad_server():
            conn, _ = srv.accept()
            head = b""
            while b"\r\n\r\n" not in head:
                head += conn.recv(4096)
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\n"
                         b"Sec-WebSocket-Accept: wrong==\r\n\r\n")

        threading.Thread(target=bad_server, daemon=True).start()
        with self.assertRaises(wsc.WSError):
            wsc.WSClient(f"ws://127.0.0.1:{port}/", timeout=5).connect()
        srv.close()

    def test_non_101_rejected(self):
        srv = socket.create_server(("127.0.0.1", 0))
        port = srv.getsockname()[1]

        def deny_server():
            conn, _ = srv.accept()
            head = b""
            while b"\r\n\r\n" not in head:
                head += conn.recv(4096)
            conn.sendall(b"HTTP/1.1 401 Unauthorized\r\n\r\n")

        threading.Thread(target=deny_server, daemon=True).start()
        with self.assertRaises(wsc.WSError):
            wsc.WSClient(f"ws://127.0.0.1:{port}/", timeout=5).connect()
        srv.close()


if __name__ == "__main__":
    unittest.main()
