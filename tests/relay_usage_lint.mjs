// R97-B: 中継 DO の「行を書く経路」が、必ず使用量（_bump）に計上されることを機械で固定する。
//
// なぜ要るか（2026-09-17 の監査）: `_bump` の呼び出しは `sync()` の 1 箇所だけで、
// enqueue / ack / statusForApp / putStatus / push 購読 / WS 起源の 7 経路が数えられていなかった。
// PWA の HTTP 退避は 20 秒ポーリング＝**1 端末 4,320 行/日**が丸ごと不可視で、無料枠 100,000 行/日の
// 4.3% を食っていた。それでいて PWA の設定画面は「80% を超えると自動で通信を減らします」と断言していた。
// ＝ ユーザーには「スマホが繋がらない」としか見えない形で枠が尽きる。
//
// この番人は wrangler を要らない（worker.js を**文字列として**読むだけ）＝既定の verify で毎回走る。
// 新しい書込経路を足した人は、下の表に「誰が数えるか」を書かないと通れない。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(ROOT, "relay/src/worker.js"), "utf8");

/** 行を書くメソッド → 誰が使用量に計上するか。"self"= そのメソッドが _bump を呼ぶ。 */
const WRITERS = {
  _bump: "meter",                   // 使用量そのもの（自分の 1 行を +1 して数える＝_bump は呼ばない）
  _kvPut: "caller",                 // 低水準（dlg は sync が、条件付きは _kvPutIfChanged が数える）
  _kvPutIfChanged: "self",
  _touchSeen: "caller",             // sync（agentseen）と statusForApp（appseen・60 秒間引き）
  enqueue: "self",
  ack: "caller",                    // sync 内は sync が、/ack ルートは ackRemote が数える
  ackRemote: "self",
  putStatus: "caller",              // sync 内は sync が、POST /status は putStatusRemote が数える
  putStatusRemote: "self",
  putAttnState: "caller",           // sync が attnNow の 1 行として数える
  putSub: "self",
  delSub: "self",
  sync: "self",
  statusForApp: "self",             // appseen を書いた回だけ（60 秒に 1 回へ間引き済み）
  fetch: "caller",                  // WS upgrade の origin/site は _kvPutIfChanged が数える
};

// DO クラスの本体を切り出す（ルート側の fetch ハンドラは SQL を書かない）
const clsAt = src.indexOf("export class Room");
assert.ok(clsAt > 0, "Room クラスが見つからない");
const body = src.slice(clsAt);

// メソッド定義（インデント 2 のもの）を拾い、それぞれの本文を次の定義までで区切る
const defRe = /^ {2}(?:async )?([A-Za-z_][A-Za-z0-9_]*)\(/gm;
const defs = [...body.matchAll(defRe)].map((m) => ({ name: m[1], at: m.index }));
assert.ok(defs.length > 10, `メソッドが ${defs.length} 個しか取れていない（切り出しが壊れている）`);

const WRITE_SQL = /sql\.exec\(\s*(?:`|")(?:\s*)(INSERT|DELETE|UPDATE)/i;
const unaccounted = [];
const lostBump = [];
for (let i = 0; i < defs.length; i++) {
  const { name, at } = defs[i];
  const text = body.slice(at, i + 1 < defs.length ? defs[i + 1].at : body.length);
  const writesSql = WRITE_SQL.test(text);
  const callsWriter = /this\.(?:_kvPut|_kvPutIfChanged|_touchSeen|putStatus|putAttnState|ack|enqueue|putSub|delSub)\(/.test(text);
  if (!writesSql && !callsWriter) continue;
  const rule = WRITERS[name];
  if (!rule) {
    unaccounted.push(`${name}()`);
    continue;
  }
  if (rule === "self" && !/this\._bump\(/.test(text)) lostBump.push(`${name}()`);
  if (rule === "meter" && !/d\.rows \+=/.test(text)) lostBump.push(`${name}()（計器自身が数えていない）`);
}

assert.deepEqual(unaccounted, [],
  `行を書く経路が使用量の表に無い: ${unaccounted.join(" ")} → tests/relay_usage_lint.mjs の WRITERS に「誰が数えるか」を書く`);
assert.deepEqual(lostBump, [],
  `自分で数えるはずのメソッドから _bump が消えている: ${lostBump.join(" ")}`);

// 具体的な回帰 3 件（監査で実際に漏れていたもの）を名指しで固定する
for (const [name, must] of [["enqueue", true], ["ackRemote", true], ["putStatusRemote", true], ["statusForApp", true]]) {
  const d = defs.find((x) => x.name === name);
  assert.ok(d, `${name}() が無い`);
  const text = body.slice(d.at, body.indexOf("\n  }", d.at));
  assert.equal(/this\._bump\(/.test(text), must, `${name}() の計上が失われている`);
}
// 20 秒ポーリングのたびに appseen を書かない（間引きの実装が消えたら落ちる）
const sfa = defs.find((x) => x.name === "statusForApp");
assert.ok(/60000/.test(body.slice(sfa.at, body.indexOf("\n  }", sfa.at))),
  "statusForApp の appseen 間引き（60 秒）が消えている＝1 端末 4,320 行/日が戻る");

console.log(`✓ 中継の使用量: 書込経路 ${Object.keys(WRITERS).length} 本すべてが計上される（wrangler 不要）`);
