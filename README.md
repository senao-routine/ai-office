# 🏢 AI Office

**Mission control for your AI agent fleet — a live 3D robot office on your Mac, with real controls and a phone app so your agents never stall while you're away.**

Every Claude Code session on your Mac walks into a glass-and-neon isometric office as a little white robot. Sessions in the same project fold into **one avatar** (`works ×9`), so twenty terminals read as a calm floor plan — who's typing, who's in a meeting with subagents, who's stuck waiting for you. Then you *act*: answer with number keys, tap a robot to open its status sheet, or approve from your phone on the train.

![AI Office demo](docs/demo.gif)

> No sessions running? See it immediately: <http://localhost:4780/?demo=1>

## Why

If you run several coding agents in parallel, the bottleneck isn't the AI — it's you. Sessions stall on permission prompts and questions the moment you step away.

AI Office closes that loop:

- ❗ When a session needs approval or asks a question, an exclamation mark pops over its robot's head — and your iPhone gets a **push notification**
- The question's **actual options become buttons** (mirrored from `AskUserQuestion`) — answer with one keystroke or one tap
- Your reply lands inside the real session via a Stop-hook inbox — **the agent keeps moving**

## How it's different

Plenty of tools *show* your agents. Almost none let you *run* them:

| | Watch | Act | Scope |
|---|---|---|---|
| Pixel-office viewers | ✅ cute | ❌ watch-only | one machine |
| Claude Code Agent View | table UI | peek & reply | one machine, Claude only |
| **AI Office** | ✅ live 3D office | ✅ answer / approve / instruct — from tray, list, 3D floor, or phone | Claude Code sessions on your Mac |

And privacy is structural, not a setting: message bodies are stripped **on your Mac** before anything reaches the relay — the relay carries states, activity verbs and questions, never your code or conversations. Instructions are HMAC-signed per device; the relay **cannot forge them**. Your own Cloudflare account, no vendor server, no account — and it's completely free.

## Features

- **🤖 Live 3D office** — 1 avatar = 1 project (sessions auto-aggregate, `×N` crew badge); robots type, stretch, grab coffee, hold meetings when they spawn subagents
- **❗ Answer queue** — the top blocked question sits in a tray; press `1`/`2`/`3` to answer with the real options
- **📇 Status sheets** — tap any robot or list row: a human-readable summary ("Waiting for your approval for 4 minutes…"), current/next/done tasks, recent activity, quick-reply chips, free-form compose
- **👑 Boss desk** — click the gold-crowned boss robot to pick any project and issue a directive
- **🎬 Demo mode** — `/?demo=1`, no sessions needed
- **🔔 Desktop notifications + daily report** — new ❗ pings you; at 18:00 a one-file daily summary of what your fleet shipped
- **📱 Phone PWA + Web Push** — QR pairing, per-device HMAC signing, one-tap approve / stop / free-form replies
- **💸 Cost gauges** — Claude / Codex and other providers' quota usage and monthly spend
- **🔌 MCP tools** — `office_status` / `office_instruct`, so agents can read and drive the office too
- **🔒 Private by design** — `127.0.0.1` bind only, transcripts read-only, bodies redacted at source
- **Zero dependencies** — server is Python standard library only. Clone and run

## Quick start

```bash
git clone https://github.com/senao-routine/ai-office.git && cd ai-office
bash setup.sh
```

That's it. `setup.sh` wires the Stop hook (so your replies reach the real session), installs the
resident app (starts at login), **runs the launchctl steps for you**, verifies the server responds,
and opens the office. Re-running it is safe.

```bash
bash setup.sh --check       # diagnose only — changes nothing
bash setup.sh --no-daemon   # just run it here, don't install as a resident app
```

Open <http://localhost:4780> — sessions on your Mac appear as robots.
No sessions yet? `/?demo=1` shows a populated office.

### What it touches

- `~/.claude/settings.json` — adds a Stop hook (a backup is kept). This is what lets your answers
  reach the running session.
- `~/Library/Application Support/AIOffice/` — the app and its data
- `~/Library/LaunchAgents/com.senao.aioffice.plist` — starts the office at login
- **macOS will ask for permission** the first time the office opens a Terminal for you
  ("Python wants to control Terminal") and when it posts a notification. Both are expected;
  the office only ever reads your transcripts and never sends their contents anywhere.

### MCP setup (optional)

```bash
claude mcp add --scope user aioffice -- "$(command -v python3)" "$PWD/server/mcp_office.py"
claude mcp list   # → aioffice: connected
```

## Phone setup (optional)

The mobile relay runs on **your own Cloudflare account** (the free tier is plenty — the office
keeps its own daily budget and slows itself down long before you could hit a limit).

```bash
bash relay/setup.sh
```

One command: it signs you in to Cloudflare if needed, generates the tokens and the push key,
deploys the Worker, writes `~/.claude/office_relay.json`, and checks the round trip.
Then, in the office UI: **📱 Phone pairing** → issue a device → open the link on your iPhone →
Add to Home Screen (iOS 16.4+) → tap 🔔 for push.

Requires Node.js (for `wrangler`) and a free Cloudflare account.

Security model: the relay carries a transport token only and **cannot forge instructions** — authenticity is verified on your Mac with a per-device HMAC key that never leaves it.

## Pricing

**Everything is free.** The full local office, phone PWA + push notifications, remote actions and
the cost dashboard are all included — no license, no subscription, no account, no telemetry.

Like it? The [product page](https://routinelabo-lp.routinelabo-senao.workers.dev) has updates and a
membership community (early builds, new tools, hands-on support).

## Requirements

- macOS (Apple Silicon or Intel), Python 3.9+ (the system `python3` works)
- Node.js — only for the optional phone relay (`wrangler` runs through `npx`)
- [Claude Code](https://claude.com/claude-code) — the office visualizes its local sessions
- UI language: English / Japanese (auto-detected; to pin, set `"lang"` in `office_config.json` — when installed as a resident app, that file lives at `~/Library/Application Support/AIOffice/data/office_config.json`)

## 日本語ドキュメント

日本語の詳細ドキュメントは [README.ja.md](README.ja.md) を参照してください。

---

Built by [senao](https://github.com/senao-routine) / Routine Labo.
