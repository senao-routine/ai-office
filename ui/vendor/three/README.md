# three.js（vendored）

- 版: **0.185.1**
- 取得元: `https://unpkg.com/three@0.185.1/build/{three.module.min.js,three.core.min.js}`
- ライセンス: **MIT**（`LICENSE` を同梱・著作権表示を保持すること）

## なぜ vendor するか

「clone 即動作（npm install 不要）」がこのプロダクトの価値なので、バンドラも
node_modules も入れない。ブラウザのネイティブESMでそのまま import する。

## 更新のしかた

```bash
V=0.186.0
curl -sL "https://unpkg.com/three@$V/build/three.module.min.js" -o ui/vendor/three/three.module.min.js
curl -sL "https://unpkg.com/three@$V/build/three.core.min.js"   -o ui/vendor/three/three.core.min.js
curl -sL "https://unpkg.com/three@$V/LICENSE"                   -o ui/vendor/three/LICENSE
bash dev.sh --shot     # golden と比べて描画が変わっていないか確認
```

**`three.module.min.js` は `three.core.min.js` を import する2ファイル構成**。
片方だけ置くと実行時に 404 になる（実際に踏んだ）。
