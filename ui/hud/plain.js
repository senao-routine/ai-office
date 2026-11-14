// 文言から絵文字を落とす（b4-mac・コックピットの「端末×ドット絵」に色付きの絵文字を混ぜない＝本人 9/29「前のモデルの名残」）。
// hud の文言辞書（ui/hud/strings.js）は iso と共用＝辞書は変えず、cockpit が hud の部品へ渡す T をこれで包む
//（iso / pixel は従来の T のまま＝iso の golden は変わらない）。スモークも同じ関数で「期待する文言」を作る。
//   ❗ → 「!」・✅ → 「✓」・ほかの絵文字（📋 📱 ▶ ⏭ ⚠ …）と異体字セレクタは消す・先頭の空白を詰める。
export function plainText(v) {
  if (typeof v !== "string") return v;
  return v.replace(/❗/gu, "!").replace(/✅/gu, "✓").replace(/️/gu, "")
    .replace(/\p{Extended_Pictographic}\s?/gu, "").replace(/^\s+/u, "");
}

/** T（文言を引く関数）を絵文字なしの T にする。hud の部品は T.plain を見て、文言の外に直書きした絵文字の印（配達の
 *  📴/📨・定型の ▶👍🧪⏸・シートの 🟢❓ など）も出さない */
export const plainT = (T) => Object.assign((key, ...args) => plainText(T(key, ...args)), { plain: true });
