// R80.7: ロボットの職業アーキタイプ（純ロジック・DOM/THREE/乱数なし）。
// ユーザーFB「ロボットに特徴がない。動画編集なら動画編集者みたいなデザインが欲しい」。
// プロジェクト名/role のキーワードで職業を決め、該当なしは projectId ハッシュで
// パステル色だけ変える（無関係なプロジェクトに嘘の職業帽子を被せない）。
//
// tint = 殻のインスタンスカラー（白1,1,1からの淡い色差し＝白ロボの人格は保つ）
// acc  = アクセサリのインスタンスカラー

// 順序=先勝ち。具体的な職種を先に・広すぎる dev を最後に置く（「アプリ」等が全部devに
// 吸われるのを防ぐ）。R80.8: 5種→9種へ拡充（ユーザーFB「種類を豊富に」）。
export const ARCHETYPE_RULES = [
  ["video", /動画|映像|ムービー|video|movie|premiere|カット|字幕|vlog|shorts/i],
  ["audio", /音声|音楽|music|audio|podcast|ポッドキャスト|ラジオ|ナレーション|bgm/i],
  ["research", /リサーチ|調査|研究|分析|research|analytics|データ分析|検証|実験/i],
  ["finance", /経理|会計|請求|финанс|finance|予算|収益|売上|決算|invoice|マネタイズ/i],
  ["writer", /ブログ|blog|記事|執筆|ライタ|note|メルマガ|newsletter|原稿|台本|書/i],
  ["design", /デザイン|design|figma|イラスト|絵|banner|サムネ|lp|ランディング|ロゴ/i],
  ["support", /広報|マーケ|sns|サポート|受付|問い合わせ|cs|コミュニティ|発信|宣伝/i],
  ["ops", /申請|運用|自動化|bot|cron|watchdog|監視|ops|台帳|集計|バックアップ/i],
  ["infra", /インフラ|infra|サーバー構築|deploy|デプロイ|ci|ビルド基盤|移行|migration/i],
  ["dev", /開発|コード|アプリ|エンジニア|dev|code|app|api|server|サーバ|基盤|ツール|edit/i],
];

export const ACCESSORIES = Object.freeze({
  phones: "video", cap: "dev", beret: "design", pencil: "writer", bowtie: "ops",
  mortar: "research", headset: "support", hardhat: "infra", eyeshade: "finance",
});

// R91: 殻の色（アクセサリと直交する個体差）。tint=instanceColor の乗算値・
// swatch=カスタマイズ画面のチップ色（材質のクリームを掛けた見え方の近似）。
// サーバー（office_server.PROJECT_COLORS）はキー名しか知らない＝配色の正本はここ。
// 本人が選んだ色は「選んだと分かる」濃さにする（淡すぎると R91 以前の
// 職業パステルと見分けが付かず、カスタマイズした手応えが無い＝実レンダで測って調整）。
// 彩度は陶器の範囲に留める（禁止色の青紫シアンに入らない・style_score の neon_cool 圏外）。
export const SHELL_COLORS = Object.freeze({
  cream: { tint: [1.00, 0.98, 0.94], swatch: "#e9dcc8" },
  oak: { tint: [1.00, 0.84, 0.62], swatch: "#e9bc84" },
  sage: { tint: [0.78, 0.94, 0.76], swatch: "#b6d3a2" },
  clay: { tint: [1.00, 0.74, 0.64], swatch: "#e9a688" },
  slate: { tint: [0.74, 0.82, 0.95], swatch: "#acb8ca" },
  sand: { tint: [1.00, 0.92, 0.68], swatch: "#e9ce91" },
  moss: { tint: [0.80, 0.90, 0.62], swatch: "#baca84" },
  rose: { tint: [1.00, 0.78, 0.85], swatch: "#e9afb5" },
});

const STYLE = {
  video: { tint: [0.87, 0.92, 1.0], acc: [0.22, 0.24, 0.32] },     // 🎧=ダークグレー
  audio: { tint: [0.88, 0.97, 0.97], acc: [0.16, 0.45, 0.48] },    // 🎧=ティール（部品共有）
  research: { tint: [0.93, 0.91, 1.0], acc: [0.24, 0.20, 0.40] },  // 🎓角帽=濃紫
  finance: { tint: [0.90, 0.98, 0.92], acc: [0.20, 0.50, 0.34] },  // 👓アイシェード=グリーン
  ops: { tint: [1.0, 0.96, 0.86], acc: [0.30, 0.32, 0.42] },       // 🎀蝶ネクタイ=ネイビー
  support: { tint: [1.0, 0.93, 0.88], acc: [0.85, 0.45, 0.20] },   // 🎤ヘッドセット=オレンジ
  infra: { tint: [1.0, 0.95, 0.82], acc: [0.95, 0.75, 0.10] },     // ⛑ヘルメット=イエロー
  writer: { tint: [0.88, 0.98, 0.90], acc: [0.98, 0.80, 0.25] },   // ✏️鉛筆=イエロー
  design: { tint: [1.0, 0.90, 0.94], acc: [0.88, 0.36, 0.52] },    // 🎨ベレー=ローズ
  dev: { tint: [0.90, 0.93, 1.0], acc: [0.32, 0.40, 0.85] },       // 🧢キャップ=ブルー
};

// 該当なしプロジェクトの淡色パレット（識別はモノグラム＋この色差し）
const PASTELS = [
  [1.0, 1.0, 1.0], [0.92, 0.95, 1.0], [0.93, 1.0, 0.95],
  [1.0, 0.94, 0.92], [0.97, 0.93, 1.0], [1.0, 0.98, 0.90],
  [0.90, 0.98, 1.0], [1.0, 0.92, 0.97], [0.95, 1.0, 0.90],
];

function hashStr(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** agent（buildWorldの1件）→ {kind, tint, acc}。kind="generic" はアクセサリなし。 */
export function archetypeFor(agent) {
  const base = baseArchetype(agent);
  // 殻の色を明示していれば、職業判定より色だけを上書きする（帽子は帽子のまま）。
  // 索引は own-prop 限定（"toString" 等が来ると継承プロパティが真になって崩れる）
  const name = agent?.color;
  const shell = typeof name === "string" && Object.hasOwn(SHELL_COLORS, name)
    ? SHELL_COLORS[name] : null;
  return shell ? { ...base, tint: shell.tint, color: name } : base;
}

function baseArchetype(agent) {
  if (agent && Object.hasOwn(agent, "arch")) {
    const kind = typeof agent.arch === "string" && Object.hasOwn(ACCESSORIES, agent.arch)
      ? ACCESSORIES[agent.arch] : null;
    if (kind) return { kind, ...STYLE[kind] };
    if (agent.arch === null) return { kind: "generic", tint: [1, 1, 1], acc: null };
  }
  const text = [agent?.name, agent?.role, agent?.dept].filter(Boolean).join(" ");
  for (const [kind, re] of ARCHETYPE_RULES) {
    if (re.test(text)) return { kind, tint: STYLE[kind].tint, acc: STYLE[kind].acc };
  }
  const h = hashStr(String(agent?.id || agent?.session || ""));
  return { kind: "generic", tint: PASTELS[h % PASTELS.length], acc: null };
}

/** Shared verb/target classification for activityGloss and actFor (ja/en). */
export function activityKind(a) {
  if (!a) return null;
  if (a.state === "resting") return "resting";
  const verb = String(a.verb || "").trim();
  const target = String(a.target || "");
  if (a.kind === "think" || /考え中|Thinking/i.test(verb)) return "think";
  if (/指示待ち|Waiting/i.test(verb)) return "waiting";
  if (/報告中|Reporting|Replying|応答中/i.test(verb)) return "report";
  if (/調査中|Reading|Searching|検索中/i.test(verb)) return "research";
  if (/実行中|Running/i.test(verb)) {
    if (/verify|pytest|unittest|node --test|\btest\b|spec|smoke/i.test(target)) return "test";
    if (/git |commit|push|merge|rebase|deploy/i.test(target)) return "ship";
    if (/npm|pip|install|build|make|brew/i.test(target)) return "build";
    return "run";
  }
  if (/編集中|Editing/i.test(verb)) return /\.md\b|readme|docs?\//i.test(target) ? "docs" : "code";
  if (/執筆中|Writing/i.test(verb)) return /\.md\b|readme/i.test(target) ? "docs" : "write";
  return null;
}
