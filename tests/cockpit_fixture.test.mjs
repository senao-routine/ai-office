// R99 Phase 3: コックピット用の固定ワールド 2 本の番人。
//   tests/fixtures/world/cockpit.json       golden 用（語彙が全部そろい、部屋に全員が入る）
//   tests/fixtures/world/cockpit_peak.json  ピーク用（部屋の定員を超える＝あふれを roomLayout で証明する）
// 正本は tools/gen_cockpit_fixture.py（JSON を手で直さない・作り直し= `python3 tools/gen_cockpit_fixture.py`）。
//
// なぜ要るか: basic.json / xl22.json にはサーバーが実際に出す語彙（kind の said/tool/order/think/idle、
// ask、報告して待機、読込中…）がほとんど無く、どの fixture も部屋の定員（26）に届かない。その fixture の
// ままで読書・⚠・✓・会議のポーズやフッターの人数を入れても、golden は「その人が居ない絵」を撮るだけで
// 何も証明しない。ここでは (1) 語彙が 1 つも欠けていないこと (2) roster・feed がサーバーの形そのもの
// （手で作った嘘の形ではない）こと (3) ピークでは机・ソファ・会議室・ラック前・受付がそれぞれあふれる
// ことを機械で確かめる。欠けたものは名指しで赤にする。
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
// ui/hud は「/ui/...」の絶対指定子で import する（ブラウザと同じ）＝node ではリポジトリ根へ読み替える
registerHooks({ resolve(specifier, context, next) {
  return next(specifier.startsWith("/ui/") ? new URL(specifier.slice(1), root).href : specifier, context);
} });
const { buildWorld } = await import("../ui/core/world.js");
const { SEATS, SLOTS, roomLayout, roomZoneOf } = await import("../ui/core/pxroom.js");
const { roomMark } = await import("../ui/core/pxtop.js");
const { departmentBoard } = await import("../ui/hud/board.js");

const REGEN = "python3 tools/gen_cockpit_fixture.py";
const T = (key, ...args) => `${key}${args.length ? ":" + args.join(",") : ""}`;
/** fixture を読み、コックピットと同じ経路（world → 台帳の 2 階層＝ui/cockpit/index.js）で組む。 */
function load(name) {
  const fx = JSON.parse(readFileSync(new URL(`fixtures/world/${name}.json`, import.meta.url), "utf8"));
  const world = buildWorld(fx);
  const board = departmentBoard(fx, world, T);
  return { name, fx, world, agents: world.agents, board, sessions: board.flatMap((g) => g.sessions) };
}
const CP = load("cockpit");
const PK = load("cockpit_peak");
const { agents, board, sessions } = CP;

/** 条件を満たす人が 1 人以上居ること。居なければ「どの語彙が欠けたか」を名指しで落とす。 */
const some = (label, list, pred, where = "cockpit.json") => {
  const hit = list.filter(pred);
  assert.ok(hit.length > 0, `語彙が欠けている: ${label}（${where} に該当する人が 0 人）`);
  return hit;
};

// サーバーに聞く（1 回だけ）: employees から roster を作らせ、feed に出る動詞の一覧をもらう
let serverView = null;
function server() {
  if (serverView) return serverView;
  const script = `
import importlib.util, json, os, shutil, sys, tempfile
home = tempfile.mkdtemp(prefix="cp_fixture_")
os.environ["OFFICE_HOME"] = home
try:
    spec = importlib.util.spec_from_file_location("office_server_fx", sys.argv[1])
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    out = {"rosters": {}, "askVerb": srv.TOOL_VERB["AskUserQuestion"], "verbs": sorted(set(srv.TOOL_VERB.values())
           | {srv.describe_tool("", {})[0], srv.describe_tool("mcp__fx__probe", {})[0]})}
    for name, emps in json.load(sys.stdin).items():
        for e in emps:
            e.pop("evidence", None)   # scan_office は roster を作った後に employees へだけ付ける
        out["rosters"][name] = srv.group_by_project(emps, "ja", mode="session")
    print(json.dumps(out, ensure_ascii=False))
finally:
    shutil.rmtree(home, ignore_errors=True)
`;
  const input = JSON.stringify({ cockpit: CP.fx.employees, cockpit_peak: PK.fx.employees });
  serverView = JSON.parse(execFileSync("python3", ["-c", script, fileURLToPath(new URL("server/office_server.py", root))],
    { input, encoding: "utf8" }));
  return serverView;
}

// ── 生成器と一致 ─────────────────────────────────────────────────────
test("2 本とも生成器（tools/gen_cockpit_fixture.py）の出力そのもの＝手で直していない", () => {
  let out = "";
  try {
    out = execFileSync("python3", ["tools/gen_cockpit_fixture.py", "--check"],
      { cwd: fileURLToPath(root), encoding: "utf8", stdio: "pipe" });
  } catch (e) {
    assert.fail(`${e.stdout || ""}${e.stderr || ""}\n→ ${REGEN} で作り直す（JSON を手で直さない）`);
  }
  // 比べた本数が減っても ✓ になる形を塞ぐ（BUILDERS から 1 本抜けると、その fixture は比べられない）
  for (const fx of [CP, PK]) {
    assert.ok(out.includes(`${fx.name}.json`), `--check が ${fx.name}.json を比べていない（出力: ${out.trim()}）`);
  }
});

// ── golden 用（cockpit.json）の語彙 ────────────────────────────────────
test("state: working / waiting / resting が全部居る", () => {
  for (const s of ["working", "waiting", "resting"]) some(`state=${s}`, agents, (a) => a.state === s);
});

test("zone: 机・会議・受付・ラウンジ・外部の 5 区画が全部埋まる（world の zoneOf）", () => {
  for (const z of ["desk", "meeting", "queue", "lounge", "external"]) some(`zone=${z}`, agents, (a) => a.zone === z);
});

test("kind: サーバーの 5 分類 said / tool / order / think / idle が全部居る", () => {
  for (const k of ["said", "tool", "order", "think", "idle"]) some(`kind=${k}`, agents, (a) => a.kind === k);
});

test("vendor: claude / codex / openclaw / other が全部居て、部屋へ渡っても other が claude に潰れない", () => {
  for (const v of ["claude", "codex", "openclaw", "other"]) some(`vendor=${v}`, agents, (a) => a.vendor === v);
  const ext = some("external(openclaw)", agents, (a) => a.external === "openclaw");
  for (const a of ext) assert.equal(a.zone, "external", `${a.session}: 外部社員は専用区画`);
  // 未知のベンダー（今のサーバーに無い将来のソース）は台帳でも部屋でも other のまま（B3）
  const others = some("台帳の vendor=other", sessions, (s) => s.vendor === "other");
  const room = roomLayout(CP.world, board, null);
  for (const s of others) {
    const actor = room.actors.find((a) => a.session === s.session);
    assert.ok(actor, `${s.session}: 未知のベンダーの人が部屋に描かれていない`);
    assert.equal(actor.vendor, "other", `${s.session}: 部屋で vendor が ${actor.vendor} に化けた（other を潰した）`);
  }
});

test("確認待ち（attention）はちょうど 2 件＝承認まち（permission）と質問まち（question）", () => {
  const attn = agents.filter((a) => a.attention);
  assert.equal(attn.length, 2, `attention の人数: ${attn.map((a) => a.session).join(",")}`);
  for (const a of attn) assert.equal(a.zone, "queue", `${a.session}: 確認待ちは受付に並ぶ`);
  some("ask.kind=permission", attn, (a) => a.ask?.kind === "permission" && a.approvalMin > 0);
  some("ask.kind=question", attn, (a) => a.ask?.kind === "question" && a.question && a.questionOptions.length > 0);
});

test("stuckTool: 承認まちの人が止まっているツールを持つ（サーバーは approvalMin>0 のときだけ出す）", () => {
  const stuck = some("stuckTool", agents, (a) => a.stuckTool);
  for (const a of stuck) {
    assert.ok(a.approvalMin > 0 && a.attention, `${a.session}: stuckTool があるのに承認まちでない＝サーバーが出さない形`);
  }
});

test("❗ではないがツールが長く止まっている人（kind=tool・waiting・attention なし）が居る＝⚠ の材料", () => {
  some("kind=tool かつ waiting かつ attention なし", agents,
    (a) => a.kind === "tool" && a.state === "waiting" && !a.attention && a.age >= 240);
});

test("サブエージェント稼働中（minions>0）は会議室、思考中（think）は机のまま", () => {
  const meet = some("minions>0", agents, (a) => a.minions > 0);
  for (const a of meet) assert.equal(a.zone, "meeting", `${a.session}: 会議室はサブエージェント稼働中の場所`);
  // 裁定: 思考は会議室へ歩かせない＝この fixture の思考中の人は部下を持たない（world でも部屋でも机）
  const think = some("kind=think", agents, (a) => a.kind === "think");
  for (const a of think) {
    assert.equal(a.zone, "desk", `${a.session}: 思考中の人が机以外に居る`);
    assert.equal(roomZoneOf(a), "desk", `${a.session}: 部屋で思考中の人が机以外へ行く`);
  }
});

test("報告して待機（kind=said・waiting）と、読書の動詞（読込中・リサーチ中）が居る", () => {
  some("kind=said かつ waiting", agents, (a) => a.kind === "said" && a.state === "waiting");
  some("verb=読込中", agents, (a) => a.verb === "読込中");
  some("verb=リサーチ中", agents, (a) => a.verb === "リサーチ中");
});

// 💬 を出す報告からの秒数の上限（統括の既定・本人が覆せる）。実装の定数（pxtop の MARK_WAIT_MAX_AGE）は読まない
//＝上限を動かす変異が期待値ごと動いて緑のまま、を塞ぐ（tests/cockpit_smoke.py の WAIT_MAX_AGE と同じ独立の数字）
const WAIT_MAX_AGE = 600;
test("💬 の 600 秒の境: 報告して 600 秒以上待っている人は部屋に居ても 💬 が出ない（同じ人を 600 秒未満にすると出る）", () => {
  const room = roomLayout(CP.world, board, null);
  // ❗・⚠ が勝つ人（attention・stuckTool）は除く＝その人に 💬 が無いのは 600 秒の境のせいではない
  const reported = room.actors.filter((a) => a.state === "waiting" && a.kind === "said" && !a.attention
    && !String(a.stuckTool ?? "").trim() && Number.isFinite(a.age));
  const over = reported.filter((a) => a.age >= WAIT_MAX_AGE);
  const under = reported.filter((a) => a.age >= 0 && a.age < WAIT_MAX_AGE);
  assert.ok(over.length > 0,
    `語彙が欠けている: 報告して ${WAIT_MAX_AGE} 秒以上待っている人が部屋に居ない（cockpit.json）→ ${REGEN} 側に足す`);
  assert.ok(under.length > 0, `語彙が欠けている: 報告して ${WAIT_MAX_AGE} 秒未満の人が部屋に居ない（cockpit.json）`);
  const mark = (a) => roomMark(a, 0, undefined, {}).mark;
  for (const a of over) {
    assert.equal(mark(a), null, `${a.session}: 報告から ${a.age} 秒なのに 💬（${mark(a)}）＝上限 ${WAIT_MAX_AGE} 秒の外`);
    // 同じ人・同じ場所（同じ区分）で age だけ変える＝出ないのは 600 秒の境のせい（区分や状態のせいではない）。境ちょうどは出ない
    assert.equal(mark({ ...a, age: WAIT_MAX_AGE - 1 }), "wait", `${a.session}: 同じ人を ${WAIT_MAX_AGE - 1} 秒にしても 💬 が出ない`);
    assert.equal(mark({ ...a, age: WAIT_MAX_AGE }), null, `${a.session}: ちょうど ${WAIT_MAX_AGE} 秒で 💬（境は「未満」）`);
  }
  for (const a of under) assert.equal(mark(a), "wait", `${a.session}: 報告から ${a.age} 秒なのに 💬 が出ない`);
});

test("evidence: tested / committed / failed が台帳の行（部屋へ渡る 2 階層）に載る", () => {
  // サーバーは roster を作った後に employees へだけ evidence を付ける（scan_office）。
  // 部屋と台帳は departmentBoard で employees を重ねた行を使うので、そこに現れれば届いている。
  for (const k of ["tested", "committed", "failed"]) {
    some(`evidence=${k}`, sessions, (s) => s.evidence?.kind === k);
  }
  assert.ok(agents.every((a) => a.evidence === null),
    "roster に evidence が載っている＝サーバーが出さない形（scan_office は employees にだけ付ける）");
});

test("規模: 机のあふれは起きるが、部屋の外（overflow）は出ない＝golden は全員を描く", () => {
  assert.ok(board.length >= 8 && board.length <= 10, `プロジェクト数 ${board.length}`);
  assert.equal(sessions.length, agents.length, "台帳のセッション数と world の人数が一致しない");
  // 裁定（思考は机）のもとで、机に居たい人（思考中を含む）は 10 人 > 9 席
  const wantDesk = sessions.filter((s) => roomZoneOf(s) === "desk").length;
  assert.ok(wantDesk > SEATS, `机に居たい人 ${wantDesk} 人 ≤ ${SEATS} 席＝机のあふれを証明できない`);
  const room = roomLayout(CP.world, board, null);
  assert.equal(room.counts.desk, SEATS, `机 ${room.counts.desk}/${SEATS}（机に居たい人が余っているのに空席がある）`);
  assert.ok(room.counts.standby > 0, `机からあふれた人が受付の standby に居ない（counts ${JSON.stringify(room.counts)}）`);
  assert.equal(room.counts.lounge, SLOTS.lounge.length, "ソファが満席でない（golden でソファ 2 席を描く材料）");
  assert.equal(room.overflow, 0, `golden 用なのに部屋の外が ${room.overflow} 人（あふれの証明は cockpit_peak.json の役）`);
  // 同じフォルダを複数ベンダーで並走しているプロジェクトが在る（ベンダー差の描き分けの材料）
  some("1 プロジェクトに複数ベンダー", board, (g) => new Set(g.sessions.map((s) => s.vendor)).size > 1);
});

// ── 2 本共通: サーバーの形 ──────────────────────────────────────────────
test("roster は 2 本ともサーバーの group_by_project(mode=session) の出力そのもの（手で作った形ではない）", () => {
  const { rosters } = server();
  for (const fx of [CP, PK]) {
    assert.deepEqual(fx.fx.roster, rosters[fx.name],
      `${fx.name}.json の roster がサーバーの集約と違う → ${REGEN} で作り直す`);
    assert.equal(fx.fx.avatarMode, "session", `${fx.name}: サーバーの既定（session）で作った fixture`);
  }
});

test("Claude の feed はサーバーが出す形だけ（「動詞 対象」か「💬 本文」・thinking は feed に入らない）", () => {
  const verbs = server().verbs;
  for (const fx of [CP, PK]) {
    for (const e of fx.fx.employees.filter((x) => x.vendor === "claude")) {
      for (const line of e.feed) {
        const ok = line.startsWith("💬 ") || verbs.some((v) => line === v || line.startsWith(`${v} `));
        assert.ok(ok, `${fx.name}: ${e.session} の feed「${line}」は parse_session が出さない形 → ${REGEN} 側を直す`);
      }
    }
  }
});

test("質問まち（AskUserQuestion）: target・question は「質問文（ラベル / ラベル）」、feed は質問文だけ（サーバーの形）", () => {
  const { askVerb } = server();
  for (const fx of [CP, PK]) {
    const asking = fx.fx.employees.filter((e) => e.kind === "tool" && e.feed[0]?.startsWith(`${askVerb} `));
    assert.ok(asking.length > 0, `${fx.name}: 質問まち（transcript の AskUserQuestion）が居ない`);
    for (const e of asking) {
      const suffix = `（${(e.questionOptions || []).map((o) => o.label).join(" / ")}）`;
      assert.ok(e.questionOptions?.length > 0 && e.question.endsWith(suffix),
        `${fx.name}: ${e.session} の question「${e.question}」に選択肢の接尾辞「${suffix}」が無い → ${REGEN} 側を直す`);
      if (e.state !== "resting") assert.equal(e.target, e.question, `${fx.name}: ${e.session} の target と question が違う`);
      assert.ok(!e.feed[0].endsWith(suffix), `${fx.name}: ${e.session} の feed に接尾辞（describe_tool は質問文だけ）`);
    }
  }
});

test("disp は scan_office の採番どおり（Claude は新しい順・Codex は source_codex の ORDER BY id の順で並べて採番）", () => {
  for (const fx of [CP, PK]) {
    for (const [vendor, cmp] of [["claude", (a, b) => b.mtime - a.mtime],
                                 ["codex", (a, b) => (a.session < b.session ? -1 : a.session > b.session ? 1 : 0)]]) {
      const rows = fx.fx.employees.filter((e) => e.vendor === vendor);
      assert.deepEqual(rows.map((e) => e.session), [...rows].sort(cmp).map((e) => e.session),
        `${fx.name}: ${vendor} の employees の並びがサーバーと違う → ${REGEN} 側を直す`);
      const seen = {};
      for (const e of rows) {
        seen[e.dept] = (seen[e.dept] || 0) + 1;
        assert.equal(e.disp, seen[e.dept] === 1 ? e.dept : `${e.dept} ${seen[e.dept]}号`, `${fx.name}: ${e.session} の採番`);
      }
    }
  }
});

// ── ピーク（cockpit_peak.json）: 部屋の定員を超える ───────────────────────
const PEAK_ROOM = roomLayout(PK.world, PK.board, null);
const wantOf = (z) => PK.sessions.filter((s) => roomZoneOf(s) === z);

test("ピークの規模: 36〜48 セッション・12〜16 プロジェクト・4 ベンダー・外部 4 体以上・❗4・休憩 3・会議 4 以上", () => {
  const a = PK.agents;
  assert.ok(a.length >= 36 && a.length <= 48, `セッション ${a.length}`);
  assert.equal(PK.sessions.length, a.length, "台帳のセッション数と world の人数が一致しない");
  const projects = PK.board.filter((g) => !g.sessions.some((s) => s.external));
  assert.ok(projects.length >= 12 && projects.length <= 16, `プロジェクト（外部社員を除く）${projects.length}`);
  for (const v of ["claude", "codex", "openclaw", "other"]) some(`vendor=${v}`, a, (x) => x.vendor === v, "cockpit_peak.json");
  assert.ok(a.filter((x) => x.external).length >= 4, "外部社員が 4 体未満（ラック前 3 枠をあふれない）");
  assert.ok(a.filter((x) => x.attention && !x.external).length >= 4, "確認待ちが 4 件未満");
  assert.ok(a.filter((x) => x.state === "resting" && !x.external).length >= 3, "休憩が 3 件未満");
  assert.ok(a.filter((x) => x.minions > 0 && !x.external).length >= 4, "サブエージェント稼働中が 4 件未満");
  let total = 0;
  for (const z of Object.keys(SLOTS)) total += SLOTS[z].length;
  assert.ok(a.length > total, `セッション ${a.length} が部屋の定員 ${total} 以下＝部屋の外を証明できない`);
});

test("ピーク: 部屋の外（overflow）が出て、その人数は描けなかった人数と一致する", () => {
  const r = PEAK_ROOM;
  assert.ok(r.overflow > 0, `overflow=${r.overflow}（定員を超えているのに全員が部屋に居る）`);
  assert.equal(r.overflow, PK.sessions.length - r.actors.length, "overflow と描けなかった人数が食い違う");
  const feet = new Set(r.actors.map((x) => `${x.x},${x.y}`));
  assert.equal(feet.size, r.actors.length, "部屋に描いた人の立ち位置が重なっている");
  assert.equal(r.counts.desk, SEATS, `机 ${r.counts.desk}/${SEATS}（あふれているのに空席がある）`);
});

// sublet: あふれた人が仮住まい（机・standby）として部屋に描かれることまで要るか。ソファと会議室は
// 台帳の先頭のプロジェクトに寄せてあるので、机が埋まる前にあふれて机で仮住まいする（空振りさせない）。
// 外部社員は台帳の最後（トリアージ順）＝机からあふれた人が先に standby を埋めるので、部屋の外へ出る。
for (const [zone, label, allowed, sublet] of [
  ["lounge", "ソファ（休憩）", ["desk", "standby"], true],
  ["meeting", "会議室（サブエージェント稼働中）", ["desk", "standby"], true],
  // サーバーは外部社員に❗を出さない（openclaw_source は approvalMin=0・question="" 固定）＝あふれは standby だけ
  ["external", "ラック前（外部社員）", ["standby"], false],
]) {
  test(`ピーク: ${label}があふれる（満席＋あふれた人は仮住まいか部屋の外）`, () => {
    const want = wantOf(zone);
    const cap = SLOTS[zone].length;
    assert.ok(want.length > cap, `${label}に居たい人 ${want.length} ≤ 定員 ${cap}＝あふれを証明できない`);
    assert.equal(PEAK_ROOM.counts[zone], cap, `${label}が満席でない（${PEAK_ROOM.counts[zone]}/${cap}）`);
    assert.ok(want.length - PEAK_ROOM.counts[zone] > 0, `${label}からあふれた人が居ない`);
    const moved = PEAK_ROOM.actors.filter((x) => x.want === zone && x.zone !== zone);
    for (const x of moved) {
      assert.ok(allowed.includes(x.zone), `${x.session}: ${label}からあふれて ${x.zone} に居る（許すのは ${allowed}）`);
      assert.equal(x.attention, false, `${x.session}: あふれた人が挙手の絵になる`);
    }
    if (sublet) {
      // standby 行きは「受付で待つ」絵＝机での仮住まい（want≠zone の机）が 1 人は要る
      assert.ok(moved.some((x) => x.zone === "desk"),
        `${label}からあふれて机で仮住まいする人が居ない（あふれた人: ${moved.map((x) => `${x.session}→${x.zone}`).join(", ") || "なし"}・fixture の並びを見直す）`);
    }
    // 描けた人（自分の区分＋仮住まい）は居たい人以下＝残りは部屋の外（台帳とフッターが持つ）
    const drawn = PEAK_ROOM.actors.filter((x) => x.want === zone).length;
    assert.ok(drawn <= want.length, `${label}: 描いた人数 ${drawn} が居たい人数 ${want.length} を超える`);
  });
}

test("ピーク: 受付は確認待ちが全員並び、standby も居て、両端から埋まって満員になる", () => {
  const r = PEAK_ROOM;
  const callers = PK.sessions.filter((s) => roomZoneOf(s) === "queue");
  assert.ok(callers.length >= 4 && callers.length < SLOTS.queue.length,
    `確認待ち ${callers.length}（4 以上・受付 ${SLOTS.queue.length} 枠未満でないと standby と出会えない）`);
  assert.equal(r.counts.queue, callers.length, `確認待ち ${callers.length} 人のうち受付に並んだのは ${r.counts.queue} 人`);
  assert.ok(r.counts.standby > 0, `standby が居ない（counts ${JSON.stringify(r.counts)}）`);
  for (const x of r.actors.filter((a) => a.zone === "queue")) assert.ok(x.attention, `${x.session}: 受付の列に確認待ちでない人`);
  for (const x of r.actors.filter((a) => a.zone === "standby")) assert.equal(x.attention, false, `${x.session}: standby が挙手する`);
  assert.equal(r.counts.queue + r.counts.standby, SLOTS.queue.length,
    "部屋の外に人が居るのに受付に空き枠が残っている（両端から埋めて出会うまで詰める）");
});

// R123: 一覧の名前（新画面・コックピットの台帳で共有）。/rename → 自動タイトル → これまでの名前の順・
// 組の名前は同じフォルダの Codex（「Codex」）が先に来ても Claude のプロジェクト名を使う。
test("R123: 行の名前は title → aiTitle → プロジェクト名・組の名前は Claude のプロジェクト名が勝つ", () => {
  const cwd = "/Users/x/works";
  const office = {
    avatarMode: "session",
    employees: [
      { session: "cx-1", vendor: "codex", disp: "Codex", dept: "Codex", cwd, state: "working", age: 3 },
      { session: "s-a", vendor: "claude", disp: "制作本部", dept: "制作本部", cwd, state: "working", age: 5, aiTitle: "請求書の住所の直し" },
      { session: "s-b", vendor: "claude", disp: "制作本部 2号", dept: "制作本部", cwd, state: "resting", age: 9, title: "LINE返事", aiTitle: "LINEメッセージ読み取り" },
      { session: "s-c", vendor: "claude", disp: "制作本部 3号", dept: "制作本部", cwd, state: "resting", age: 20 },
    ],
  };
  office.roster = office.employees.map((e) => ({ ...e, projectId: `p-${e.session}`, name: e.dept,
    sessions: [{ session: e.session, vendor: e.vendor, state: e.state, age: e.age }] }));
  const groups = departmentBoard(office, buildWorld(office), T);
  const works = groups.filter((g) => g.key === cwd);
  assert.equal(works.length, 1, "同じフォルダは 1 つの組");
  assert.equal(works[0].name, "制作本部", "組の名前が Codex に取られた");
  const names = Object.fromEntries(works[0].sessions.map((m) => [m.session, m.name]));
  assert.equal(names["s-a"], "請求書の住所の直し", "名前の無いセッションは自動タイトル");
  assert.equal(names["s-b"], "LINE返事", "/rename の名前が自動タイトルに勝つ");
  assert.ok(names["s-c"] && names["s-c"] !== "", "どちらも無ければ従来の名前");
  assert.ok(works[0].sessions.every((m) => !("fromClaude" in m)) && !("fromClaude" in works[0]), "内部の印を外へ出さない");
});
