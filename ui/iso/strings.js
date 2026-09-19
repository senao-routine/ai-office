// R98-W1: 文言辞書は様式非依存なので ui/hud/strings.js へ移した（iso はここから再エクスポートするだけ）。
// hud/*.js は T を引数で受ける設計のまま（strings を直接 import しない）＝ 層の向きは変えない。
export { T, lang, setLang, dictKeys, dictStrings } from "/ui/hud/strings.js";
