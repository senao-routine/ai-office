# tools/vendor/ — 同梱third-party（tools/ はstdlib縛りの外・deps可の領域）

- **segno/** = QRコード生成の信頼ライブラリ segno 1.6.6（MIT・pure-python・依存ゼロ）から
  `encoder.py`＋`consts.py`のみ抜粋（matrix生成に必要な最小）。pip不要＝clone即動作を維持しつつ
  手書きQRエンコーダの「壊れQR」リスクを回避するため vendored。使用は tools/qr_gen.py（subprocess）から。
  更新: `pip install segno==<新> && cp .../segno/{encoder,consts}.py tools/vendor/segno/` → `bash verify.sh`（QR KAT）。

## QR KAT golden の再導出（独立オラクルでの相互検証）

`tests/test_qr.py` の `KAT_MATRIX_SHA` は vendoring 時に pip版 segno から生成した固定値。
別マシン/更新時に独立に再確認するには（pip版 segno を一時venvに入れて突合）:

```bash
python3 -m venv /tmp/qrv && /tmp/qrv/bin/pip install segno==1.6.6
/tmp/qrv/bin/python -c "import segno,hashlib; \
c=segno.encoder.encode('https://ex.workers.dev/app#v=1&d=d_0123456789ab&s='+'ab'*32+'&t='+'cd'*32+'&e=1900000000',error='m'); \
print(hashlib.sha256(''.join(str(x) for r in c.matrix for x in r).encode()).hexdigest())"
# → 73d60acaa1dbbc5e4c6a72d94e2cf1807d4fea982679385811a05d3333f7b859（test_qr.py と一致すること）
```
