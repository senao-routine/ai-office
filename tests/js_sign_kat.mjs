// JS↔Python 署名パリティKAT（node:crypto で APP_HTML と同じ canonical を再現）。
// WebCrypto(subtle) と node:crypto の HMAC-SHA256 は同一値なので、これが Python の
// tests/test_crypto.py の KAT_SIG と一致すれば「canonical 文字列の作り方が JS/Python で
// 揃っている」ことの機械的証明になる（相互運用の回帰網）。relay_e2e.sh から node で実行。
//
// さらに（重要）: 本KATが二重実装の自己複製に堕ちないよう、**本番PWA署名器**
// （worker.js の APP_HTML 内 canonical 構築リテラル）を実ファイルから読んで、下の
// 期待リテラルとバイト一致することも検証する。これで worker.js:APP_HTML の
// フィールド順/区切りを変えたら（＝実機スマホのsigがズレる変更）この KAT が破れる。
import { createHash, createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const SEC = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff";
const DID = "d_0123456789ab";
const SESS = "sess-kat-00000001";
const TEXT = "こんにちは、世界";
const TS = 1720400000;
const NONCE = "0123456789abcdef0123456789abcdef";
const KAT = "6ec594fdd36b8eeca7ef8a2c96676849cdbc51560fac6449140a71a4ffd54773";

const th = createHash("sha256").update(Buffer.from(TEXT, "utf8")).digest("hex");
const canonical = ["aioffice-instruct", "v1", DID, SESS, String(TS), NONCE, th].join("\n");
const sig = createHmac("sha256", Buffer.from(SEC, "hex"))
  .update(Buffer.from(canonical, "utf8")).digest("hex");

if (sig !== KAT) {
  console.error("JS署名KAT不一致: " + sig + " != " + KAT + " （canonical が JS/Python でズレている）");
  process.exit(1);
}

// 本番PWA署名器（worker.js APP_HTML）の canonical 構築リテラルが上と同型かをバイト一致で検査
const HERE = dirname(fileURLToPath(import.meta.url));
const workerSrc = readFileSync(join(HERE, "..", "relay", "src", "worker.js"), "utf8");
const EXPECT_APPHTML_CANON = String.raw`["aioffice-instruct","v1",cred.d,session,String(ts),nonce,th].join("\\n")`;
if (!workerSrc.includes(EXPECT_APPHTML_CANON)) {
  console.error("worker.js APP_HTML の canonical 構築が期待リテラルと不一致＝本番スマホ署名器がズレた");
  console.error("期待: " + EXPECT_APPHTML_CANON);
  process.exit(1);
}
console.log("JS署名KAT一致＋APP_HTML canonical 検査OK（本番スマホ署名器の相互運用ガード）");
