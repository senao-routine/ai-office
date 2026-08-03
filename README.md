# 🏢 AI Office

**Mission control for your AI agent fleet — a live 3D robot office on your Mac, with real controls and a phone app so your agents never stall while you're away.**

Every Claude Code session on your Mac walks into a glass-and-neon isometric office as a little white robot. Sessions in the same project fold into **one avatar** (`works ×9`), so twenty terminals read as a calm floor plan — who's typing, who's in a meeting with subagents, who's stuck waiting for you. Then you *act*: answer with number keys, tap a robot to open its status sheet, or approve from your phone on the train.

![AI Office demo](docs/demo.gif)

> No sessions running? See it immediately: <http://localhost:4780/?demo=1>

## Why

If you run several coding agents in parallel, the bottleneck isn't the AI — it's you. Sessions stall on permission prompts and questions the moment you step away.

AI Office closes that loop:

- ❗ When a session needs approval or asks a question, an exclamation mark pops over its robot's head — and (Pro) your iPhone gets a **push notification**
- The question's **actual options become buttons** (mirrored from `AskUserQuestion`) — answer with one keystroke or one tap
- Your reply lands inside the real session via a Stop-hook inbox — **the agent keeps moving**

## How it's different

Plenty of tools *show* your agents. Almost none let you *run* them:

| | Watch | Act | Scope |
|---|---|---|---|
| Pixel-office viewers | ✅ cute | ❌ watch-only | one machine |
| Claude Code Agent View | table UI | peek & reply | one machine, Claude only |
| **AI Office** | ✅ live 3D office | ✅ answer / approve / instruct — from tray, list, 3D floor, or phone | Claude Code today, external agents (OpenClaw) in the same office, multi-Mac aggregation in progress |

And privacy is structural, not a setting: message bodies are stripped **on your Mac** before anything reaches the relay — the relay carries states, activity verbs and questions, never your code or conversations. Instructions are HMAC-signed per device; the relay **cannot forge them**. Your own Cloudflare account, no vendor server, no account, one-time license.

## Features

- **🤖 Live 3D office** — 1 avatar = 1 project (sessions auto-aggregate, `×N` crew badge); robots type, stretch, grab coffee, hold meetings when they spawn subagents
- **❗ Answer queue** — the top blocked question sits in a tray; press `1`/`2`/`3` to answer with the real options
- **📇 Status sheets** — tap any robot or list row: a human-readable summary ("Waiting for your approval for 4 minutes…"), current/next/done tasks, recent activity, quick-reply chips, free-form compose
- **👑 Boss desk** — click the gold-crowned boss robot to pick any project and issue a directive
- **🎬 Demo mode** — `/?demo=1`, no sessions needed
- **🔔 Desktop notifications + daily report** — new ❗ pings you; at 18:00 a one-file daily summary of what your fleet shipped
- **📱 Phone PWA + Web Push** (Pro) — QR pairing, per-device HMAC signing, one-tap approve / stop / free-form replies
- **💸 Cost gauges** (Pro) — Claude / Codex / Gemini credit usage and monthly spend
- **🔌 MCP tools** — `office_status` / `office_instruct`, so agents can read and drive the office too
- **🔒 Private by design** — `127.0.0.1` bind only, transcripts read-only, bodies redacted at source
- **Zero dependencies** — server is Python standard library only. Clone and run

## Quick start

```bash
git clone https://github.com/senao-routine/ai-office.git && cd ai-office
python3 server/office_server.py
```

Open <http://localhost:4780> — sessions on your Mac appear as robots. Try the demo at `/?demo=1`.

**Enable instruction delivery** (1 minute — this is the half you'll love):

```bash
bash hooks/install.sh --wire   # wires a Stop hook into ~/.claude/settings.json (backup kept)
```

Run it as a resident app (starts at login, survives restarts):

```bash
bash macapp/install.sh
```

### MCP setup (optional)

```bash
claude mcp add --scope user aioffice -- "$(command -v python3)" "$PWD/server/mcp_office.py"
claude mcp list   # → aioffice: connected
```

## Phone setup (Pro)

The mobile relay runs on **your own Cloudflare account** (free tier is plenty):

1. `bash relay/deploy.sh` — deploys the Worker + Durable Object
2. Office UI → **📱 Phone pairing** → issue a device → scan the QR with your iPhone
3. Add to Home Screen (iOS 16.4+) → toggle 🔔 to enable push

Security model: the relay carries a transport token only and **cannot forge instructions** — authenticity is verified on your Mac with a per-device HMAC key that never leaves it.

## Editions

| | Price | What you get |
|---|---|---|
| **Free** | $0 | The full local office — live 3D visualization, instruct from the desktop UI, demo mode, MCP |
| **Pro** | $12.99 one-time | Phone PWA delivery, push notifications, cost dashboard |
| **OpenClaw** | $0 | For OpenClaw users: your agents work in a dark-themed office as red lobster bots, phone PWA and push included |
| **Hybrid** | $29 one-time | Pro + external agents (OpenClaw nodes) working in the same office |

Offline license file. No subscription, no account, no telemetry. Purchase links are coming at launch — **Watch this repo** for the release.

The OpenClaw edition is **not a trial and does not expire** — start it with
`bash macapp/install.sh --edition openclaw` (or set `"edition": "openclaw"` in
`office_config.json`). It runs without Claude Code.

## Requirements

- macOS (Apple Silicon or Intel), Python 3.9+ (the system `python3` works)
- [Claude Code](https://claude.com/claude-code) — the office visualizes its local sessions
  (not required for the OpenClaw edition, which shows agents from your OpenClaw machine instead)
- UI language: English / Japanese (auto-detected from your locale; set `"lang"` in `office_config.json` to pin)

## 日本語ドキュメント

日本語の詳細ドキュメントは [README.ja.md](README.ja.md) を参照してください。

---

Built by [senao](https://github.com/senao-routine) / Routine Labo.
