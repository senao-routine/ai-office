// R99 Phase 2: 端末への入力の送り口（ui/platform/api.js の termInput）の番人。
// fetch を差し替えて、**順番・分割・空振り・失敗の通知**を実際の POST の並びで確かめる。
// どれも E2E で踏んだか、別モデルレビューが実測した壊れ方:
//   - 空振りの flush が自分自身を返して Promise が循環 → 最初の塊だけ届き、以降は永久に消える
//   - 64 KiB を超える貼り付けを 1 回で送り、上限で拒否されて黙って消える
import test from "node:test";

// ★各テストに上限を付ける。鎖が循環すると失敗ではなく**永久に待つ**ので、上限が無いと
//   回帰した瞬間に verify ごと止まる（壊して確かめたら実際に固まった）。
const LIMIT = { timeout: 5000 };
import assert from "node:assert/strict";

const posts = [];
let failAt = -1;
globalThis.fetch = async (path, opts) => {
  const body = JSON.parse(opts.body);
  posts.push(Buffer.from(body.data, "base64"));
  const ok = posts.length - 1 !== failAt;
  return { ok: true, status: 200, text: async () => JSON.stringify({ ok }) };
};
const { termInput, TERM_INPUT_CHUNK } = await import("../ui/platform/api.js");

const bytes = (s) => new TextEncoder().encode(s);
const reset = () => { posts.length = 0; failAt = -1; };

test("termInput: 何度打っても全部届く（Promise の鎖が途中で死なない）", LIMIT, async () => {
  reset();
  const send = termInput("t1");
  for (const ch of "echo one\r") await send(bytes(ch));
  for (const ch of "echo two\r") await send(bytes(ch));
  const all = Buffer.concat(posts).toString();
  assert.equal(all, "echo one\recho two\r");
});

test("termInput: 並べて投げても打った順に届く", LIMIT, async () => {
  reset();
  const send = termInput("t1");
  const waits = [...Array(50).keys()].map((i) => send(bytes(`${i},`)));
  await Promise.all(waits);
  assert.equal(Buffer.concat(posts).toString(), [...Array(50).keys()].map((i) => `${i},`).join(""));
});

test("termInput: 長い貼り付けは上限以下に割って、順番どおり全部送る", LIMIT, async () => {
  reset();
  const send = termInput("t1");
  const big = new Uint8Array(70 * 1024).map((_, i) => 65 + (i % 26));
  await send(big);
  assert.ok(posts.length >= 5, `分割されていない（${posts.length} 回）`);
  assert.ok(posts.every((p) => p.length <= TERM_INPUT_CHUNK), "上限を超える POST がある");
  assert.deepEqual(new Uint8Array(Buffer.concat(posts)), big);
});

test("termInput: 途中で拒否されたら知らせる・残りは送らない・次の入力は届く", LIMIT, async () => {
  reset();
  failAt = 1;
  const errors = [];
  const send = termInput("t1", { onError: (err, left) => errors.push(left) });
  await send(new Uint8Array(TERM_INPUT_CHUNK * 3));
  assert.equal(errors.length, 1, "拒否を黙って捨てている");
  assert.equal(posts.length, 2, "拒否のあとも残りを送り続けた（順番の崩れた入力がシェルに流れる）");
  failAt = -1;
  await send(bytes("x"));
  assert.equal(posts.at(-1).toString(), "x", "1 回の失敗で送り口ごと死んだ");
});
