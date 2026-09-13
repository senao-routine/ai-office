// R87-S6: E2E の「端末役」。stdin の {bundle, secretHex, deviceId, targetSession} を
// **PWA 本番と同じ ui/core/dialog_open.js** で開き、page JSON を stdout へ。開けなければ exit 2。
// relay_e2e.sh が「中継から取った封書を端末秘密で本当に開ける／別の秘密では開けない」を確かめるのに使う。
import { openBlob } from "../ui/core/dialog_open.js";

let raw = "";
for await (const chunk of process.stdin) raw += chunk;
let input;
try {
  input = JSON.parse(raw);
} catch (e) {
  console.error(`bad input: ${e.message}`);
  process.exit(3);
}
try {
  const page = await openBlob(input.bundle, input.secretHex, input.deviceId, input.targetSession);
  process.stdout.write(JSON.stringify(page));
} catch (e) {
  console.error(`open failed: ${e && e.message ? e.message : e}`);
  process.exit(2);
}
