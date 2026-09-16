# 🏢 AI Office

<!--version-->v2.1.0<!--/version--> · macOS · MIT · no account, no telemetry, no server of mine

**A live 3D office on your Mac for Claude Code, Codex CLI and OpenClaw: answer sessions, catch up on their work, and keep a calm screen beside your editor or on stream.**

One session, one robot. Claude Code's `/rename` becomes its displayed name, so parallel sessions in the same folder stay distinguishable; project aggregation remains optional. Round heads with one antenna identify Claude, rounded-square heads with two antennas identify Codex, and lobster silhouettes identify OpenClaw.

The office is a daylight Scandinavian interior: oak floors, white walls, large windows, plants, linen and ceramic robots. Its visual source of truth is [the art direction](docs/art-direction.md).

![AI Office demo](docs/demo.gif)

> Try it with no sessions running: <http://localhost:4780/?demo=1>

## Three pillars

- **Answer across vendors.** See Claude Code (interactive and background), Codex CLI and connected OpenClaw sessions together. Use the ❗ tray's number keys, click a robot for its sheet, or reply from your phone. Delivery follows each vendor's supported route; Claude permission decisions use the local PermissionRequest hook.
- **Measure the human side, with a timeline.** See what waited for your answer and what finished while you were away. An away digest, a 20-second replay, and XP/levels turn recorded activity into a history you can inspect. XP excludes token volume; the Mac view exposes its breakdown.
- **A 3D screen worth leaving open.** A white × pale-lavender glass loft in cool daylight, ceramic robots, and black monitors that glow blue-white (real bloom on emissive screens, plus vignette and static grain, a PMREM room environment). It grows from S through M and L to XL (24 seats) with peak concurrent activity; levels unlock decorations. Stream mode makes it useful beside your editor or in OBS.

## Working with sessions

- **Answer queue and status sheets** — actual question options, number-key replies, current/next/completed tasks, recent activity, reply chips and free-form instructions.
- **Codex CLI** — session metadata comes from read-only SQLite databases under `~/.codex`; instructions go through `codex queue`. The session adapter does not read conversation-body columns. Codex CLI must support `queue` for delivery to work; failures are reported.
- **Hire** — choose a registered project, enter a task and toggle separate-branch isolation. `POST /api/hire` starts `claude --bg`, with worktree isolation when selected. The Codex choice copies `open -a Terminal`; you run it and start Codex yourself.
- **Away digest and replay** — review sessions waiting for you, finished work and ongoing work, then replay the recorded timeline in 20 seconds. This is a reconstruction from events, not a recording of terminal contents.
- **XP and levels** — completed tasks, commits, answers, timely-answer bonuses, active minutes, hires and Codex turns contribute. Token usage does not. The Mac view shows every contribution; the relay carries totals and levels without the detailed breakdown.
- **Phone PWA and Web Push** — the same 3D renderer at `quality:"mobile"` (no bloom pass: screens glow by emissive brightness only), QR pairing and signed replies. Push notifications bring questions to your attention.
- **Cost gauges, notifications and reports** — quota/spend views remain available alongside desktop notifications and daily summaries.
- **MCP** — `office_status`, `office_instruct` and `office_digest` let agents inspect the office, send instructions and read a digest.

### How answers arrive

Claude Code instructions use the Stop-hook inbox at the end of a turn. A session stopped on a permission prompt cannot reach that hook, so the **PermissionRequest hook** publishes the pending request and returns your answer as its decision.

**Execution approval is available from the Mac's local screen (127.0.0.1). Phone replies carry words, not a grant to execute a pending permission request.** You can still answer in the terminal; a terminal answer takes precedence while the hook waits. Codex uses `codex queue`; OpenClaw uses its configured inbox/relay integration.

## Stream mode

Open <http://localhost:4780/?stream=1>. The normal HUD is hidden, leaving the office, an activity subtitle and an ❗ count. The automatic camera follows activity and stops when you take manual control.

- OBS landscape: <http://localhost:4780/?stream=1&aspect=16:9>
- Vertical video: <http://localhost:4780/?stream=1&aspect=9:16>
- Hide project names and use a one-character badge with status: add `&privacy=1`.
- Larger nameplates: add `&plates=large`.

Settings can copy URLs for OBS, vertical video or a second monitor. In browsers supporting canvas capture and MediaRecorder, settings also offer a **20-second WebM replay export**. Privacy mode changes the visible labels and subtitles; it does not change the relay's data contract.

## Quick start

```bash
git clone https://github.com/senao-routine/ai-office.git && cd ai-office
bash setup.sh
```

`setup.sh` installs the hooks, registers the resident app and starts it with `launchctl`, checks that the server responds, and opens the office. Re-running it is safe. Its optional first-session walkthrough defaults to No.

```bash
bash setup.sh --check       # diagnose only
bash setup.sh --no-daemon   # run here without installing the resident app
```

Open <http://localhost:4780>. Local sessions appear as robots; use `/?demo=1` to explore without sessions.

### What it touches

- `~/.claude/settings.json` — hook wiring, with a backup: **Stop** for instruction delivery, **PermissionRequest** for pending approvals/questions, and asynchronous recording across **17 event types**. Existing unrelated hooks are kept.
- `hooks/office-event.sh` — the recording hook's repository source, copied to `~/.claude/hooks/office-event.sh`. It records event types, identifiers, counts and limited metadata such as basenames; **it never writes prompt, command or response bodies**. Records go to `~/.claude/office_events/<YYYY-MM-DD>.jsonl`.
- `~/.claude/office_timeline.sqlite` — the local timeline database used for the digest, replay and XP. It stores events and state/wait intervals, with no conversation-body columns. `~/.claude/office_seen.json` records when you last acknowledged the digest.
- `~/.claude/office_inbox/` and `~/.claude/office_approvals/` — instruction delivery and pending permission requests/answers; these can contain the text needed to answer a session and are separate from the body-free event log.
- `~/.claude/office_recipes.json`, `~/.claude/office_action_results.json` and `~/.claude/office_actions_audit.jsonl` (all `0600`) — the allow-list of actions you can run from your phone, their most recent outcomes, and an audit trail. Results are shared through a file because the office and the phone relay run as two separate processes. The stored outcome carries the label, state and scrubbed output only — never the command line, working directory or environment.
- `~/.claude/projects/` — Claude transcripts are read-only. The Codex session adapter reads metadata from `~/.codex/state_5.sqlite` and `~/.codex/thread_history_1.sqlite` read-only; it does not modify those databases.
- `~/Library/Application Support/AIOffice/` — the installed app and its data.
- `~/Library/LaunchAgents/com.senao.aioffice.plist` — starts the office at login.
- macOS may ask for Automation permission when the office opens Terminal, and for notification permission.

### What leaves your Mac

Nothing is sent to me. There is no account, no telemetry, and no server of mine: the repository
contains no endpoint I control, and the app does not even check for updates on start-up. The HTTP
server binds `127.0.0.1` only, so it is not reachable from your LAN.

Exactly two kinds of outbound request exist, and you switch both on yourself:

| Destination | When | What is sent |
|---|---|---|
| **The Cloudflare Worker you deploy** (`relay/`) | Only if you set up the phone relay | The office view on an allowlist — no message bodies, no local paths. New fields are excluded by default. |
| **api.x.com · api.openai.com · openrouter.ai · api.moonshot.ai · api.deepseek.com** | Only for a provider whose API key you entered | A `GET` with your key, to read your own balance or quota. Nothing about your office is included, and with no key stored not a single byte is sent. Answers are cached for 15 minutes. |

The browser side loads nothing from the internet: no CDN, no web fonts, no analytics. three.js is
vendored in this repository.

### MCP setup (optional)

```bash
claude mcp add --scope user aioffice -- "$(command -v python3)" "$PWD/server/mcp_office.py"
claude mcp list
```

## Phone setup (optional)

The relay runs on **your own Cloudflare account**. The Mac connects outbound; no inbound port forwarding is needed.

```bash
bash relay/setup.sh
```

The setup script handles Cloudflare login, token/push-key generation, deployment, local relay configuration and connectivity checks. Then open **Phone pairing** in the office, issue a device and open the link on your phone. On iPhone, add it to the Home Screen and enable push. Node.js is needed for the relay setup; see [the relay guide](relay/README.md).

The relay uses an allowlist. It carries status, questions/options, chosen session titles, background summaries, templates and scrubbed action results; transcript bodies and local path fields are removed on the Mac. Titles and background summaries can be disabled separately. See [the exact data contract](docs/office-json.md). Instructions are signed per device and verified on the Mac; the relay's transport token cannot forge them.

**Reading a conversation on the phone (optional, off by default).** When you turn on *Show conversations on the phone* in the Phone pairing sheet, tapping **💬 Show conversation** asks the Mac for that session's recent messages. The Mac seals them with **AES-256-GCM using a key derived for that one device** and hands the ciphertext to the relay; only that phone can open it. What this protects: the relay (Cloudflare) and anyone holding only the transport token cannot read the conversation. What it does not protect: the relay still sees *when* a device asked for *which* session and roughly how large the reply was; there is no forward secrecy (the pairing secret is fixed for 30 days); the ciphertext stays on the relay until it expires (90 seconds); the Mac itself can of course read everything; and **if you lose the phone, whoever has it can read conversations as well as send instructions** — revoke the device from the Mac and no new envelopes are sealed for it. This is not "zero-knowledge". Macs where the system crypto library is unavailable simply do not offer the button.

## Pricing

**All features are free.** The local office, phone PWA, push, configured remote actions, cost dashboard and stream mode are included. There are no editions, licenses or AI Office subscriptions. AI-provider and Cloudflare usage follow your own accounts' plans.

Updates and the Routine Labo community are on the [product page](https://routinelabo-lp.routinelabo-senao.workers.dev).

## Uninstalling

Stop the daemons first — `uninstall.sh` deliberately does not run `launchctl` for you
(unregistering a login item is your call), so it stops and tells you what to run if either
label is still registered:

```bash
launchctl bootout gui/$(id -u)/com.senao.aioffice
launchctl bootout gui/$(id -u)/com.senao.aioffice.relay   # if you enabled phone delivery

bash macapp/uninstall.sh              # remove the plists and code; keep data/config
bash macapp/uninstall.sh --purge-data # also remove app data/config
```

The hook wiring in `~/.claude/settings.json` is not removed automatically. Remove the AI Office entries referencing `office-inbox-wait`, `office-approval-wait` and `office-event`, or restore the settings backup. The event-recording entries span multiple event types. Local event/timeline files under `~/.claude` are separate from the app's data directory.

## Requirements

| | |
|---|---|
| **Mac** | Apple Silicon or Intel. macOS 13 Ventura or newer (tested on 15 Sequoia). |
| **Python** | The stock `/usr/bin/python3` (3.9.6) is enough — the server uses the standard library only. Xcode Command Line Tools provide it: `xcode-select --install`. |
| **Agents** | Claude Code (interactive and background), Codex CLI, or a configured OpenClaw connection. Hiring needs Claude Code with background sessions; delivery to Codex needs `codex queue`. Answering permission prompts from the office needs a Claude Code build with `PermissionRequest` hooks. |
| **Browser** | Any WebGL-capable browser for the 3D view; without it the office falls back to a list. |
| **Node.js** | Only for development checks and the optional phone relay (wrangler needs Node 22+). Running the local office needs no package install. |
| **Language** | English/Japanese, auto-detected. Pin `"lang"` in `office_config.json`. |

**Not supported:** Windows and Linux. The resident app, the Terminal integration and the sealed-dialog
backend are macOS-specific.

## Documentation

[日本語](README.ja.md) · [Changelog](CHANGELOG.md) · [Art direction](docs/art-direction.md) · [Office JSON](docs/office-json.md)

## Support

Run `bash setup.sh --check` first — it diagnoses the things that break most often and its output
is what a bug report needs. Then open an [issue](https://github.com/senao-routine/ai-office/issues)
(English or Japanese). For anything security-related, use GitHub's private vulnerability reporting
instead of a public issue; see [SECURITY.md](.github/SECURITY.md).

This is a one-person project, so I cannot promise a response time — but I read everything.

Built by [senao](https://github.com/senao-routine) / Routine Labo.
