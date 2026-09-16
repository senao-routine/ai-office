#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Office の版の**正本**。ここ 1 箇所だけを人が書き換える。

なぜここに置くか:
  - `server/` は macapp/install.sh が丸ごと常駐先へ rsync する＝**動いているコードが自分の版を持ち歩く**。
  - Homebrew の libexec でも配布 zip でも、`import office_version` で同じように取れる。
  - シェルからは `python3 server/office_version.py` で 1 行取れる。

波及先（verify ▶2e が一致を機械で検査する。手で 2 箇所に書かない）:
  /api/office の app.version ／ MCP の serverInfo.version ／ README のバッジ ×2 ／ CHANGELOG の先頭見出し。

注意: office_json の `"v": 2` は**データ形式の版**で、これとは別の寿命を持つ別の数（docs/office-json.md）。
"""
VERSION = "2.1.0"

if __name__ == "__main__":
    print(VERSION)
