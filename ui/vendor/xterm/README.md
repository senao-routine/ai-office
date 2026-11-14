# xterm.js（vendored）

- 版: **@xterm/xterm 6.0.0** ／ **@xterm/addon-fit 0.11.0**
- 取得元: `npm pack @xterm/xterm@6.0.0 @xterm/addon-fit@0.11.0`（`lib/*.mjs` と `css/xterm.css`）
- ライセンス: **MIT**（`LICENSE` を同梱・著作権表示を保持すること。addon-fit も同じ著作者・同じ MIT）

## なぜ vendor するか

three と同じ。「clone 即動作（npm install 不要）」がこのプロダクトの価値なので、バンドラも
node_modules も入れない。ESM 版（`.mjs`）は外部 import を持たない自己完結なので、そのまま import できる。

## 何に使うか

R99 Phase 2 のコックピットの端末（`ui/cockpit/term.js`）。**シェルは自作しない**＝画面は xterm.js、
中身は本物の pty（`server/office_pty.py`）。

## 更新のしかた

```bash
cd "$(mktemp -d)" && npm pack @xterm/xterm@<版> @xterm/addon-fit@<版> && for f in *.tgz; do tar xzf "$f"; mv package "${f%.tgz}"; done
# lib/xterm.mjs・lib/addon-fit.mjs は末尾の sourceMappingURL 行を落として置く（.map は同梱しない）
python3 tests/cockpit_smoke.py     # 端末が開いて打てることを確認
```

**6.x は ESM 版の export 名が `Terminal` / `FitAddon`**。5.x までの UMD（`window.Terminal`）とは読み方が違う。
