// R129 今日の 1 枚（2026-11-14）: 部屋のドット絵と今日の数を 1200×675（16:9＝SNS と動画に収まる）の 1 枚に描く。
// 名前は描かない: 数は ui/core/today.js の todaySummary（名前の鍵を持たない）・部屋は office.snapshot()（canvas は文字を描かない）。
// 色と字は明るい画面のトークン（ui/app/style.css の --a-*）を読む＝画面と同じ配色。飾りは罫線と背景の明度差だけ（左の帯・影・グラデーションは使わない）。

export const TODAY_CARD = Object.freeze({ w: 1200, h: 675 });

const FALLBACK = { bg: "#F7F6F3", surface: "#FFFFFF", line: "#E4E0D8", ink: "#1C1B19", soft: "#57534E", faint: "#6B665E",
  accent: "#1F6B52", font: '-apple-system, "Hiragino Sans", "Hiragino Kaku Gothic ProN", sans-serif' };

function tokens(from) {
  try {
    const css = from.ownerDocument.defaultView.getComputedStyle(from);
    const v = (name, fb) => css.getPropertyValue(name).trim() || fb;
    return { bg: v("--a-bg", FALLBACK.bg), surface: v("--a-surface", FALLBACK.surface), line: v("--a-line", FALLBACK.line),
      ink: v("--a-ink", FALLBACK.ink), soft: v("--a-ink-soft", FALLBACK.soft), faint: v("--a-ink-faint", FALLBACK.faint),
      accent: v("--a-accent", FALLBACK.accent), font: v("--font-ui", FALLBACK.font) };
  } catch {
    return FALLBACK;
  }
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** 日付の見出し（その日の YYYY-MM-DD から・時計は読まない＝UTC の暦で曜日を出す） */
export function dayLabel(day, lang) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day || ""));
  if (!m) return "";
  const date = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return new Intl.DateTimeFormat(lang === "en" ? "en-US" : "ja-JP",
    { year: "numeric", month: "long", day: "numeric", weekday: "short", timeZone: "UTC" }).format(date);
}

/**
 * canvas に今日の 1 枚を描く（大きさも整える）。
 * @param {HTMLCanvasElement} canvas
 * @param {{ summary: ReturnType<import("../core/today.js").todaySummary>, room: HTMLCanvasElement|null, T: Function, lang: string,
 *   tokensFrom?: Element }} ctx  tokensFrom＝色と字のトークンを読む要素（様式の根・既定は文書の根）
 */
export function drawTodayCard(canvas, { summary, room, T, lang, tokensFrom = null }) {
  const { w: W, h: H } = TODAY_CARD;
  canvas.width = W;
  canvas.height = H;
  const c = tokens(tokensFrom || canvas.ownerDocument.documentElement);
  const ctx = canvas.getContext("2d");
  const num = (n) => Number(n || 0).toLocaleString(lang === "en" ? "en-US" : "ja-JP");
  const font = (weight, px) => `${weight} ${px}px ${c.font}`;
  ctx.fillStyle = c.bg;
  ctx.fillRect(0, 0, W, H);

  // 左: 部屋（白い面＋罫線・ドット絵は整数倍のまま中央へ）
  const box = { x: 40, y: 40, w: 600, h: H - 80 };
  roundRect(ctx, box.x, box.y, box.w, box.h, 16);
  ctx.fillStyle = c.surface;
  ctx.fill();
  ctx.strokeStyle = c.line;
  ctx.lineWidth = 1;
  ctx.stroke();
  if (room && room.width && room.height) {
    const k = Math.max(1, Math.min(Math.floor((box.w - 24) / room.width), Math.floor((box.h - 24) / room.height))) || 1;
    const rw = room.width * k, rh = room.height * k;
    const scaled = rw <= box.w - 24 && rh <= box.h - 24 ? k : 1;
    const dw = room.width * scaled, dh = room.height * scaled;
    ctx.save();
    roundRect(ctx, box.x + 1, box.y + 1, box.w - 2, box.h - 2, 15);
    ctx.clip();
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(room, Math.round(box.x + (box.w - dw) / 2), Math.round(box.y + (box.h - dh) / 2), dw, dh);
    ctx.restore();
  } else {
    ctx.fillStyle = c.faint;
    ctx.font = font(400, 16);
    ctx.textAlign = "center";
    ctx.fillText(T("today_no_room"), box.x + box.w / 2, box.y + box.h / 2);
    ctx.textAlign = "left";
  }

  // 右: 名乗り・題・日付・数
  const x = 684, right = W - 48;
  ctx.textBaseline = "alphabetic";
  roundRect(ctx, x, 62, 16, 16, 4);
  ctx.fillStyle = c.accent;
  ctx.fill();
  ctx.fillStyle = c.soft;
  ctx.font = font(600, 16);
  ctx.fillText(T("today_brand"), x + 26, 76);
  ctx.fillStyle = c.ink;
  ctx.font = font(700, 36);
  ctx.fillText(T("today_title"), x, 132);
  ctx.fillStyle = c.soft;
  ctx.font = font(400, 18);
  ctx.fillText(dayLabel(summary.day, lang), x, 166);
  ctx.fillStyle = c.line;
  ctx.fillRect(x, 192, right - x, 1);

  const cells = [
    [num(summary.sessions), T("today_sessions")],
    [num(summary.answered), T("today_answered")],
    [num(summary.commits), T("today_commits")],
    [T("today_hours_value", num(summary.activeHours)), T("today_hours")],
    [num(summary.tools), T("today_tools")],
    ...(summary.bots > 0 ? [[num(summary.bots), T("today_bots")]] : []),   // Bot が居ない日は枠ごと出さない（0 を見せない）
  ];
  const colW = (right - x) / 2;
  cells.forEach(([value, label], i) => {
    const cx = x + (i % 2) * colW, cy = 252 + Math.floor(i / 2) * 104;
    ctx.fillStyle = c.ink;
    ctx.font = font(700, 44);
    ctx.fillText(String(value), cx, cy);
    ctx.fillStyle = c.soft;
    ctx.font = font(400, 16);
    ctx.fillText(label, cx, cy + 28);
  });

  const v = summary.vendors || {};
  const split = [["Claude Code", v.claude], ["Codex", v.codex], ["OpenClaw", v.openclaw]]
    .filter(([, n]) => n > 0).map(([name, n]) => `${name} ${num(n)}`).join(" · ");
  ctx.fillStyle = c.line;
  ctx.fillRect(x, 568, right - x, 1);
  ctx.fillStyle = c.soft;
  ctx.font = font(400, 16);
  if (split) ctx.fillText(split, x, 598);
  ctx.fillStyle = c.faint;
  ctx.font = font(400, 13);
  ctx.fillText(T(summary.available ? "today_private" : "today_unavailable"), x, 630);
  return canvas;
}
